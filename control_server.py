#!/usr/bin/env python3
"""Click-to-look control server for the bird camera.

Runs alongside pi-webrtc. pi-webrtc serves video over WHEP on port 8080;
this serves the viewer page and takes click coordinates on 8081.

Why a separate channel: WHEP carries no DataChannel. pi-webrtc supports
two-way messaging only under --use-mqtt or --use-livekit, so clicks
cannot ride the video connection.

A side benefit of serving our own page from the Pi: the page and the
stream are both plain http on the same host, so there is no mixed-content
prompt. The hosted demo player is https reaching into an http device,
which is why Chrome asks and other browsers refuse.

    python3 control_server.py

Then open http://<pi-ip>:8081/ in any browser on the LAN.
"""

import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import motion
import votes
from psc import PSC

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")


# ---------------------------------------------------------------------
# Click geometry
# ---------------------------------------------------------------------

def click_to_offset(norm, fov_degrees):
    """Angle from frame centre to a point at normalised offset `norm`.

    `norm` is -0.5 at one edge, 0 at centre, +0.5 at the other.

    A camera is a pinhole projection, not a linear map from position to
    angle, so interpolating the FOV linearly undershoots. For a 66.7 deg
    lens a click a quarter-frame out is really 18.2 deg off axis, where
    linear gives 16.7. Converging after two clicks would hide it, but
    the trig costs one line and lands it first time.
    """
    half = math.radians(fov_degrees) / 2.0
    return math.degrees(math.atan(2.0 * norm * math.tan(half)))


# ---------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------

class TokenBucket:
    """One bucket per client IP.

    Capacity sets the burst a viewer gets; refill sets the sustained
    rate. Deliberately permissive for LAN use -- tighten before this
    faces the internet.
    """

    def __init__(self, capacity, refill_seconds):
        self.capacity = capacity
        self.refill_seconds = refill_seconds
        self._buckets = {}
        self._lock = threading.Lock()

    def take(self, key):
        """Spend a token. Returns (allowed, seconds_until_next)."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity,
                         tokens + (now - last) / self.refill_seconds)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True, 0.0
            self._buckets[key] = (tokens, now)
            return False, (1.0 - tokens) * self.refill_seconds


# ---------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------

class Camera:
    """Owns the serial link and the motion thread.

    The serial port is not thread-safe and HTTP handlers are concurrent,
    so exactly one thread talks to the PSC. Request handlers only set a
    target and return; they never block on movement.

    A new target supersedes the one in flight rather than queueing
    behind it -- click twice and the camera abandons the first move for
    the second, which is what a viewer expects.
    """

    def __init__(self, psc):
        self.mover = motion.Mover(
            psc,
            pan_channel=config.PAN_CHANNEL,
            tilt_channel=config.TILT_CHANNEL,
            pan_limit=config.PAN_LIMIT,
            tilt_limit=config.TILT_LIMIT,
            speed=config.MOVE_SPEED,
        )
        self._target = (0.0, 0.0)
        self._generation = 0      # bumped on every new target
        self._moving = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._running = True

        self.mover.jump(0.0, 0.0)

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def look_at(self, pan, tilt):
        """Set a new target. Returns the clamped target."""
        pan, tilt = self.mover.clamp(pan, tilt)
        with self._lock:
            self._target = (pan, tilt)
            self._generation += 1
        self._wake.set()
        return pan, tilt

    def nudge(self, dpan, dtilt):
        with self._lock:
            pan, tilt = self.mover.pan, self.mover.tilt
        return self.look_at(pan + dpan, tilt + dtilt)

    def absolute_from_offset(self, dpan, dtilt):
        """Where a click points, as an absolute direction.

        A vote has to name a direction in the world, not a nudge: by the
        time the consensus resolves, the camera may have moved, and
        "20 degrees right of wherever you end up" is not what anyone
        clicked on.

        Measured against where the camera is NOW, which is approximate
        while it is moving -- the frame the viewer clicked was captured
        a couple of hundred milliseconds earlier. Small for ordinary
        clicks; worth revisiting with frame timestamps if it ever
        matters.
        """
        with self._lock:
            return self.mover.clamp(self.mover.pan + dpan,
                                    self.mover.tilt + dtilt)

    def state(self):
        with self._lock:
            return {
                "pan": round(self.mover.pan, 2),
                "tilt": round(self.mover.tilt, 2),
                "target_pan": round(self._target[0], 2),
                "target_tilt": round(self._target[1], 2),
                "moving": self._moving,
                "pan_limit": config.PAN_LIMIT,
                "tilt_limit": config.TILT_LIMIT,
            }

    def _run(self):
        while self._running:
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            if not self._running:
                break

            with self._lock:
                target = self._target
                generation = self._generation

            if (abs(target[0] - self.mover.pan) < 0.05
                    and abs(target[1] - self.mover.tilt) < 0.05):
                continue

            with self._lock:
                self._moving = True
            try:
                self.mover.move_to(
                    *target,
                    should_abort=lambda: self._generation != generation,
                )
            except Exception as e:
                print(f"[camera] move failed: {e}")
            finally:
                with self._lock:
                    # Only clear `moving` if nothing newer arrived while
                    # we were travelling; otherwise the next pass picks
                    # it up and the viewer should keep seeing "moving".
                    if self._generation == generation:
                        self._moving = False
                    else:
                        self._wake.set()

    def stop(self):
        self._running = False
        self._wake.set()
        self._thread.join(timeout=2.0)


# ---------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------

class Resolver:
    """Turns accumulated votes into camera targets on a fixed tick.

    Separate from the motion thread on purpose. Motion is about getting
    somewhere smoothly; this is about deciding where. Keeping them apart
    means the consensus can shift mid-move and the camera just
    retargets, which is the behaviour you want when a second viewer
    clicks while the first one's move is still running.
    """

    def __init__(self, camera, accumulator, interval):
        self.camera = camera
        self.accumulator = accumulator
        self.interval = interval
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while self._running:
            time.sleep(self.interval)
            try:
                result = self.accumulator.resolve()
                if result is not None:
                    self.camera.look_at(*result)
            except Exception as e:
                print(f"[resolver] {e}")

    def stop(self):
        self._running = False
        self._thread.join(timeout=2.0)


class Handler(BaseHTTPRequestHandler):
    server_version = "birdlooker/1.0"
    camera = None
    limiter = None
    accumulator = None

    def log_message(self, fmt, *args):
        # The default logs every request; too noisy with /state polling.
        if "/state" not in (self.path or ""):
            print(f"[http] {self.address_string()} {fmt % args}")

    # -- helpers -------------------------------------------------------

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, content_type):
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes --------------------------------------------------------

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._file(os.path.join(STATIC, "index.html"), "text/html")
        elif self.path == "/state":
            state = self.camera.state()
            state.update(self.accumulator.stats())
            state["voting"] = config.VOTING_ENABLED
            self._json(state)
        elif self.path == "/config":
            self._json({
                "stream_port": config.STREAM_PORT,
                "hfov": config.HFOV,
                "vfov": config.VFOV,
                "pan_limit": config.PAN_LIMIT,
                "tilt_limit": config.TILT_LIMIT,
            })
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/look":
            self._json({"error": "not found"}, 404)
            return

        # Validate before charging a token. The rate limit protects the
        # servos, and a malformed request moves nothing -- making a
        # client bug eat someone's click budget would be its own fault
        # mode, and a confusing one to diagnose.
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            nx = float(payload["x"])
            ny = float(payload["y"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            self._json({"error": "expected JSON {x, y} in 0..1"}, 400)
            return

        if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
            self._json({"error": "x and y must be within 0..1"}, 400)
            return

        allowed, wait = self.limiter.take(self.client_address[0])
        if not allowed:
            self._json({"error": "rate limited",
                        "retry_after": round(wait, 1)}, 429)
            return

        # Centre the clicked point: rotate by its angular offset from the
        # middle of the frame. Screen y grows downward, so a click above
        # centre is a positive tilt.
        dpan = click_to_offset(nx - 0.5, config.HFOV)
        dtilt = click_to_offset(0.5 - ny, config.VFOV)
        if config.INVERT_PAN:
            dpan = -dpan
        if config.INVERT_TILT:
            dtilt = -dtilt

        # Record a vote for an absolute direction rather than moving
        # directly, so simultaneous viewers blend instead of each click
        # cancelling the last.
        pan, tilt = self.camera.absolute_from_offset(dpan, dtilt)
        self.accumulator.add(self.client_address[0], pan, tilt)

        if not config.VOTING_ENABLED:
            # Same path, resolved immediately -- no second code path to
            # keep in step, and a direct A/B against voting.
            result = self.accumulator.resolve()
            if result is not None:
                self.camera.look_at(*result)

        self._json({"ok": True,
                    "pan": round(pan, 2), "tilt": round(tilt, 2),
                    "dpan": round(dpan, 2), "dtilt": round(dtilt, 2),
                    **self.accumulator.stats()})


def main():
    print(f"Opening PSC on {config.SERIAL_PORT}")
    with PSC(config.SERIAL_PORT) as psc:
        print(f"  firmware {psc.version} at {psc.baudrate} baud")

        Handler.camera = Camera(psc)
        Handler.limiter = TokenBucket(config.BUCKET_CAPACITY,
                                      config.BUCKET_REFILL_SECONDS)
        Handler.accumulator = votes.VoteAccumulator(
            decay_seconds=config.VOTE_DECAY_SECONDS,
            max_age_seconds=config.VOTE_MAX_AGE_SECONDS,
            dead_zone_degrees=config.VOTE_DEAD_ZONE_DEGREES,
        )

        resolver = None
        if config.VOTING_ENABLED:
            resolver = Resolver(Handler.camera, Handler.accumulator,
                                config.VOTE_RESOLVE_INTERVAL)

        server = ThreadingHTTPServer(("0.0.0.0", config.CONTROL_PORT),
                                     Handler)
        try:
            import socket
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "<pi-ip>"

        if config.VOTING_ENABLED:
            print(f"  voting on: {config.VOTE_DECAY_SECONDS:.0f}s decay, "
                  f"{config.VOTE_DEAD_ZONE_DEGREES} deg dead zone")
        else:
            print(f"  voting off: each click moves the camera directly")

        print(f"\n  Viewer:  http://{ip}:{config.CONTROL_PORT}/")
        print(f"  Stream:  http://{ip}:{config.STREAM_PORT} "
              f"(pi-webrtc must be running)")
        print(f"\n  Same scheme and host as the stream, so no mixed-content")
        print(f"  prompt -- works in any browser on the LAN.\n")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down")
        finally:
            if resolver:
                resolver.stop()
            Handler.camera.stop()
            server.server_close()


if __name__ == "__main__":
    main()
