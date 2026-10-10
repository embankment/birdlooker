"""Coordinated, host-interpolated motion for the PSC.

Why not the controller's own ramp:

  * It updates on the firmware's own tick, which is slow enough to see as
    stepping during a slow move, and there is no way to change it.
  * Each channel ramps independently toward its own target, so a
    diagonal move doglegs -- the axes do not arrive together.

Sending ramp=0 and interpolating here fixes both. We pick the update
rate (50 Hz), and both axes are stepped inside the same tick against a
single eased progress value, so the camera travels in a straight line
and arrives on both axes at once.

Cost: a position command is 8 bytes out plus 8 echoed back. At 38400
baud that is about 4 ms per channel, so two channels per tick is ~8 ms
of the 20 ms budget. Comfortable, but it is why 50 Hz rather than 100.
"""

import time

from psc import angle_to_position

RATE = 50.0              # updates per second
TICK = 1.0 / RATE
RAMP_IMMEDIATE = 0

DEFAULT_SPEED = 25.0     # degrees per second at the fastest part of a move


def smoothstep(t):
    """Ease in and out. t in [0,1].

    3t^2 - 2t^3: zero velocity at both ends, so the camera accelerates
    away and decelerates in rather than jerking at each end.
    """
    return t * t * (3.0 - 2.0 * t)


def linear(t):
    return t


class Mover:
    """Drives two PSC channels as one coordinated pan/tilt head.

    Tracks its own commanded position, because RSP reports what the
    controller believes rather than where the servo physically is, and
    its byte order is still unconfirmed.
    """

    def __init__(self, psc, pan_channel=0, tilt_channel=1,
                 pan_limit=60.0, tilt_limit=45.0,
                 speed=DEFAULT_SPEED, ease=smoothstep):
        self.psc = psc
        self.pan_channel = pan_channel
        self.tilt_channel = tilt_channel
        self.pan_limit = pan_limit
        self.tilt_limit = tilt_limit
        self.speed = speed
        self.ease = ease
        self.pan = 0.0
        self.tilt = 0.0

    def clamp(self, pan, tilt):
        return (max(-self.pan_limit, min(self.pan_limit, pan)),
                max(-self.tilt_limit, min(self.tilt_limit, tilt)))

    def _write(self, pan, tilt):
        self.psc.set_position(self.pan_channel, angle_to_position(pan),
                              ramp=RAMP_IMMEDIATE)
        self.psc.set_position(self.tilt_channel, angle_to_position(tilt),
                              ramp=RAMP_IMMEDIATE)
        self.pan, self.tilt = pan, tilt

    def jump(self, pan, tilt):
        """Go immediately, no interpolation."""
        self._write(*self.clamp(pan, tilt))

    def move_to(self, pan, tilt, speed=None, on_step=None):
        """Travel to (pan, tilt) in a straight line, eased at both ends.

        Duration comes from the LARGER of the two axis distances, and
        both axes advance against the same eased fraction. That is what
        makes them arrive together instead of doglegging.

        Returns the elapsed time.
        """
        speed = speed or self.speed
        target_pan, target_tilt = self.clamp(pan, tilt)

        start_pan, start_tilt = self.pan, self.tilt
        dpan = target_pan - start_pan
        dtilt = target_tilt - start_tilt

        distance = max(abs(dpan), abs(dtilt))
        if distance < 0.05:
            return 0.0

        # Eased motion averages half the peak rate over the move, so for
        # a given average speed the move takes about twice as long as a
        # constant-rate one. Scale the duration so `speed` stays an
        # honest average rather than a peak nobody reaches.
        duration = (distance / speed) * 1.5
        steps = max(1, int(round(duration * RATE)))

        t0 = time.monotonic()
        for i in range(1, steps + 1):
            frac = self.ease(i / steps)
            self._write(start_pan + dpan * frac,
                        start_tilt + dtilt * frac)
            if on_step:
                on_step(self.pan, self.tilt, i / steps)
            # Sleep against the wall clock rather than a fixed TICK, so
            # serial latency does not stretch the move.
            next_due = t0 + (i * duration / steps)
            lag = next_due - time.monotonic()
            if lag > 0:
                time.sleep(lag)

        self._write(target_pan, target_tilt)
        return time.monotonic() - t0

    def nudge(self, dpan, dtilt, speed=None):
        """Move by a relative amount."""
        return self.move_to(self.pan + dpan, self.tilt + dtilt, speed=speed)
