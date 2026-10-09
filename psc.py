"""Driver for the Parallax Servo Controller USB (#28823).

Protocol, from the #28823 datasheet (v1.2, 6/20/2008):

    Position:  "!SC" <chan> <ramp> <pw_low> <pw_high> 0x0D   - no reply
    Version:   "!SCVER?" 0x0D                                 - 3-byte reply
    Baud rate: "!SCSBR" <0|1> 0x0D                            - 3-byte reply
    Report:    "!SCRSP" <chan> 0x0D                            - 3-byte reply

Position is a 16-bit little-endian word in the range 250-1250, where one
step is 2 us of pulse width. So 250 = 500 us, 750 = 1500 us (center),
1250 = 2500 us. Note this is HALF the microsecond numbers the Windows
PSCI GUI displays -- the GUI shows microseconds, the wire wants steps.

The ramp parameter (0-63) is the controller's own acceleration curve:
0 moves immediately, 1-63 span roughly 0.75 s to 60 s for a full
500-2500 us excursion. Non-zero ramp means the PSC interpolates in
firmware, so the host sends one command per target instead of a stream
of position updates.

The board powers up at 2400 baud and can be switched to 38400.
If the host restarts without the PSC resetting (or the reverse), the
two ends disagree about baud; connect() probes both to recover.
"""

import time

import serial

# Wire-level position limits, in 2 us steps.
PW_MIN = 250    # 500 us
PW_MAX = 1250   # 2500 us
PW_MID = 750    # 1500 us

# The datasheet suggests backing off the extremes if a servo strains
# at them (p. 11). Widen only after checking your own servos.
SAFE_MIN = 260
SAFE_MAX = 1240

RAMP_IMMEDIATE = 0
RAMP_MAX = 63

BAUD_LOW = 2400
BAUD_HIGH = 38400

REPLY_DELAY = 0.0015  # datasheet: replies come 1.5 ms after the command


class PSCError(Exception):
    pass


class PSC:
    """Parallax Servo Controller over USB serial.

    Typical use:

        with PSC("/dev/ttyUSB0") as psc:
            psc.set_position(0, 750, ramp=10)
    """

    def __init__(self, port="/dev/ttyUSB0", baud=BAUD_HIGH, timeout=0.5,
                 jumper=False, debug=False):
        self.port = port
        self.target_baud = baud
        self.timeout = timeout
        # With the channel jumper fitted, channels shift 0-15 -> 16-31.
        self.channel_offset = 16 if jumper else 0
        self.ser = None
        self.version = None
        self.debug = debug

    def _log(self, msg):
        if self.debug:
            print(f"[psc] {msg}")

    @property
    def baudrate(self):
        """The rate currently in use, or None when not connected."""
        return self.ser.baudrate if self.ser else None

    # ---- connection ----------------------------------------------------

    def connect(self):
        """Open the port and get the PSC to the target baud rate.

        Probes both rates, because the board keeps whatever rate it was
        last set to until it is reset.
        """
        self.ser = serial.Serial(self.port, BAUD_LOW, timeout=self.timeout)
        self._log(f"opened {self.port}, target baud {self.target_baud}")

        tried = []
        for baud in (self.target_baud, BAUD_LOW, BAUD_HIGH):
            if baud in tried:
                continue
            tried.append(baud)
            self.ser.baudrate = baud
            self.ser.reset_input_buffer()
            self._log(f"probing at {baud}...")
            version = self._try_version()
            if version:
                self.version = version
                self._log(f"found PSC at {baud}, firmware {version!r}")
                if baud != self.target_baud:
                    self._log(f"switching {baud} -> {self.target_baud}")
                    self._set_baud(self.target_baud)
                self._log(f"negotiated baud: {self.ser.baudrate}")
                return self
            self._log(f"  no reply at {baud}")

        self._log(f"no response at any of {tried}")
        self.close()
        raise PSCError(
            f"No response from PSC on {self.port} at 2400 or 38400 baud. "
            "Check the USB cable, that the board has power, and that you "
            "are in the dialout group. The reset button restores 2400."
        )

    def _try_version(self):
        """Send VER? and return the firmware string, or None."""
        try:
            self.ser.write(b"!SCVER?\r")
            self.ser.flush()
            time.sleep(REPLY_DELAY)
            reply = self.ser.read(3)
        except serial.SerialException:
            return None
        if len(reply) == 3:
            try:
                return reply.decode("ascii")
            except UnicodeDecodeError:
                self._log(f"  non-ASCII reply {reply!r} (baud mismatch?)")
                return None
        if reply:
            self._log(f"  short reply {reply!r} ({len(reply)} of 3 bytes)")
        return None

    def _set_baud(self, baud):
        """Switch the PSC's baud rate, then follow it."""
        flag = 1 if baud == BAUD_HIGH else 0
        self.ser.write(b"!SCSBR" + bytes([flag]) + b"\r")
        self.ser.flush()
        # The PSC replies at the NEW rate, so switch before reading.
        time.sleep(0.05)
        self.ser.baudrate = baud
        time.sleep(0.05)
        self.ser.reset_input_buffer()
        if not self._try_version():
            raise PSCError(f"PSC did not respond after switching to {baud}")

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # ---- commands ------------------------------------------------------

    def set_position(self, channel, position, ramp=RAMP_IMMEDIATE):
        """Move a servo. position is 250-1250 in 2 us steps.

        ramp 0 moves at full speed; 1-63 ramp progressively slower.
        This command gets no reply.
        """
        if self.ser is None:
            raise PSCError("not connected")
        if not 0 <= channel <= 15:
            raise ValueError(f"channel {channel} outside 0-15")
        if not RAMP_IMMEDIATE <= ramp <= RAMP_MAX:
            raise ValueError(f"ramp {ramp} outside 0-63")

        position = int(round(position))
        if not PW_MIN <= position <= PW_MAX:
            raise ValueError(
                f"position {position} outside {PW_MIN}-{PW_MAX} "
                "(these are 2 us steps, not microseconds)"
            )

        chan = channel + self.channel_offset
        packet = (
            b"!SC"
            + bytes([chan, ramp, position & 0xFF, (position >> 8) & 0xFF])
            + b"\r"
        )
        self.ser.write(packet)
        self.ser.flush()

    def get_position(self, channel):
        """Read a channel's current position, or None if no reply.

        Reports the live position, so it can be polled during a ramp.
        """
        if self.ser is None:
            raise PSCError("not connected")
        chan = channel + self.channel_offset
        self.ser.reset_input_buffer()
        self.ser.write(b"!SCRSP" + bytes([chan]) + b"\r")
        self.ser.flush()
        time.sleep(REPLY_DELAY)
        reply = self.ser.read(3)
        if len(reply) != 3:
            return None
        # Reply is: channel, then the position as high byte, low byte.
        return (reply[1] << 8) | reply[2]


# ---- angle helpers -----------------------------------------------------
#
# The datasheet maps 250-1250 to 0-180 degrees. These convert to the
# signed -90..+90 convention the rest of the project uses.

def angle_to_position(angle, min_angle=-90, max_angle=90,
                      pw_min=SAFE_MIN, pw_max=SAFE_MAX):
    angle = max(min_angle, min(max_angle, angle))
    frac = (angle - min_angle) / (max_angle - min_angle)
    return int(round(pw_min + frac * (pw_max - pw_min)))


def position_to_angle(position, min_angle=-90, max_angle=90,
                      pw_min=SAFE_MIN, pw_max=SAFE_MAX):
    frac = (position - pw_min) / (pw_max - pw_min)
    return min_angle + frac * (max_angle - min_angle)
