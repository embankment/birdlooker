"""Calibration and tuning for the pan/tilt camera.

Everything that depends on the physical rig lives here. When the servos,
the lens, or the mounting geometry change, this is the file to edit --
nothing else should hold a hardware-specific number.
"""

# ---------------------------------------------------------------------
# Camera field of view, in degrees.
#
# Measured 2026-10-09 with `pan_demo.py calibrate` on the camera and
# ribbon fitted that day, at 1600x1200. Re-measure after a lens or
# sensor change.
# ---------------------------------------------------------------------
HFOV = 66.7
VFOV = 50.0

# ---------------------------------------------------------------------
# Servo dead bands, in PSC units (1 unit = 2 us of pulse width).
#
# Measured 2026-10-09 with `deadband.py`. A servo ignores any step
# smaller than this, so motion is quantized to it. 1 is the hardware
# floor; higher means a worn servo.
#
# Re-run deadband.py after swapping a servo and update these.
# ---------------------------------------------------------------------
DEADBAND = {
    0: 4,   # pan  -- worn, 0.73 deg granularity
    1: 1,   # tilt -- healthy, 0.18 deg
}

# ---------------------------------------------------------------------
# Channels and travel limits.
# ---------------------------------------------------------------------
PAN_CHANNEL = 0
TILT_CHANNEL = 1

PAN_LIMIT = 60.0     # degrees either side of centre
TILT_LIMIT = 45.0

# ---------------------------------------------------------------------
# Axis direction.
#
# The camera is mounted upside down and corrected with --rotation=180,
# so whether a click to the right should drive the pan servo positive or
# negative is not predictable from first principles. Flip these if the
# camera moves the wrong way; it is a thirty-second empirical test.
# ---------------------------------------------------------------------
INVERT_PAN = True     # verified on hardware 2026-10-10
INVERT_TILT = True    # verified on hardware 2026-10-10

# ---------------------------------------------------------------------
# Motion.
# ---------------------------------------------------------------------
MOVE_SPEED = 25.0    # degrees per second, average over a move
UPDATE_RATE = 50.0   # servo updates per second

# ---------------------------------------------------------------------
# Network.
# ---------------------------------------------------------------------
CONTROL_PORT = 8081   # this server
STREAM_PORT = 8080    # pi-webrtc's WHEP endpoint
SERIAL_PORT = "/dev/ttyUSB0"

# ---------------------------------------------------------------------
# Vote accumulation.
#
# Several viewers clicking at once should blend into one target rather
# than fighting, with each click superseding the last. See votes.py.
#
# With a single viewer this behaves identically to direct control, just
# resolved on the next tick -- so there is no separate single-viewer
# code path to maintain.
# ---------------------------------------------------------------------
VOTING_ENABLED = True

VOTE_DECAY_SECONDS = 4.0      # weight falls to 1/e after this
VOTE_MAX_AGE_SECONDS = 12.0   # votes older than this are dropped
VOTE_DEAD_ZONE_DEGREES = 1.5  # ignore consensus shifts smaller than this
VOTE_RESOLVE_INTERVAL = 0.2   # seconds between consensus recalculations

# ---------------------------------------------------------------------
# Rate limiting -- a token bucket per client IP.
#
# Tuned for LAN use with a few viewers. The June design's public-facing
# numbers (3 tokens, one per 10 s, escalating timeouts) are stricter;
# tighten towards those before exposing this to the internet.
# ---------------------------------------------------------------------
# Currently set generously for multi-device testing: tap freely from a
# lapful of phones without hitting a limit. These are NOT sensible
# public-facing values -- the June design's numbers for that are 3
# tokens refilling one per 10 s, with escalating timeouts.
BUCKET_CAPACITY = 20        # clicks available in a burst
BUCKET_REFILL_SECONDS = 0.3  # seconds to earn one more click

# Note on testing from several browsers on ONE machine: buckets are
# keyed by client IP, so every browser and incognito window on the same
# laptop shares a single bucket. Separate devices get separate buckets.
