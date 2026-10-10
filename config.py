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
INVERT_PAN = False
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
# Rate limiting -- a token bucket per client IP.
#
# Tuned for LAN use with a few viewers. The June design's public-facing
# numbers (3 tokens, one per 10 s, escalating timeouts) are stricter;
# tighten towards those before exposing this to the internet.
# ---------------------------------------------------------------------
BUCKET_CAPACITY = 3        # clicks available in a burst
BUCKET_REFILL_SECONDS = 2.0  # seconds to earn one more click
