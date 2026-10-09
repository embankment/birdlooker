#!/usr/bin/env python3
"""Low-level diagnostics for the Parallax PSC-USB (#28823).

Written for the case where VER? succeeds (so the serial link works in
both directions) but position commands appear to do nothing.

The key question this answers: does the PSC ACCEPT the position command?
RSP reports the controller's own idea of a channel's position. So:

  * RSP value changes after a position command
      -> the PSC accepted it; the problem is downstream (servo wiring,
         servo power, the wrong channel header, a dead servo).

  * RSP value does not change
      -> the PSC rejected or never parsed the command; the problem is
         the command bytes or the serial framing.

Usage:
    python3 psc_probe.py                  # full sweep of tests
    python3 psc_probe.py --stopbits 1     # compare framing
    python3 psc_probe.py --channel 4
"""

import argparse
import sys
import time

import serial

PW_MIN, PW_MID, PW_MAX = 250, 750, 1250
REPLY_DELAY = 0.0015


def hexs(b):
    return " ".join(f"{x:02X}" for x in b) if b else "(nothing)"


def send(ser, packet, reply_bytes=0, label=""):
    """Send a command and separate the echo from the real reply.

    The PSC-USB loops transmitted bytes back through the FTDI chip. That
    echo returns cleanly at any baud rate, so it tells you nothing about
    whether the controller understood the command -- but mistaking it for
    a reply makes every baud rate look like it works.
    """
    ser.reset_input_buffer()
    ser.write(packet)
    ser.flush()
    echo = ser.read(len(packet))
    ok = echo == packet
    print(f"   sent  {hexs(packet)}")
    print(f"   echo  {hexs(echo)}  {'(matches)' if ok else '*** MISMATCH ***'}")
    if reply_bytes == 0:
        return b"", ok
    time.sleep(REPLY_DELAY)
    reply = ser.read(reply_bytes)
    print(f"   reply {hexs(reply)}  {reply!r}")
    return reply, ok


def ver(ser):
    return send(ser, b"!SCVER?\r", reply_bytes=3)[0]


def rsp(ser, channel):
    return send(ser, b"!SCRSP" + bytes([channel]) + b"\r", reply_bytes=3)[0]


def set_pos(ser, channel, position, ramp=0):
    pkt = (b"!SC"
           + bytes([channel, ramp, position & 0xFF, (position >> 8) & 0xFF])
           + b"\r")
    send(ser, pkt, reply_bytes=0)
    return pkt


def decode_rsp(reply):
    """Return (low_first, high_first) so we can see which looks sane."""
    if len(reply) != 3:
        return None, None
    return (reply[2] << 8) | reply[1], (reply[1] << 8) | reply[2]


def run(port, baud, stopbits, channel):
    print(f"\n{'=' * 62}")
    print(f"port={port} baud={baud} stopbits={stopbits} channel={channel}")
    print("=" * 62)

    try:
        ser = serial.Serial(port, baud, timeout=0.5, stopbits=stopbits)
    except serial.SerialException as e:
        print(f"  could not open port: {e}")
        return False

    with ser:
        time.sleep(0.1)

        # --- 1. is anybody home? ---------------------------------------
        print("\n1. VER?")
        reply = ver(ser)
        valid = (len(reply) == 3 and reply[0:1].isdigit()
                 and reply[1:2] == b"." and reply[2:3].isdigit())
        if not valid:
            print("   Not a version string (expect something like '1.4').")
            print("   The controller is not answering at this baud rate.")
            return False
        print(f"   firmware {reply.decode('ascii')} -- this baud is correct")

        # --- 2. can we read a position? --------------------------------
        print(f"\n2. RSP ch{channel}")
        before = rsp(ser, channel)
        lo_first, hi_first = decode_rsp(before)
        if lo_first is None:
            print("   No RSP reply. The PSC answers VER? but not RSP.")
        else:
            print(f"   decoded: low-byte-first={lo_first}  "
                  f"high-byte-first={hi_first}")
            print(f"   (valid positions are {PW_MIN}-{PW_MAX}; "
                  "whichever is in range is the right decoding)")

        # --- 3. does a position command change anything? ---------------
        # Pick a target far from wherever it is now.
        target = PW_MIN + 150 if (lo_first or PW_MID) > PW_MID else PW_MAX - 150
        print(f"\n3. Position ch{channel} -> {target} ({target * 2} us)")
        set_pos(ser, channel, target, ramp=0)
        time.sleep(1.5)

        print("   reading back:")
        after = rsp(ser, channel)
        a_lo, a_hi = decode_rsp(after)
        if a_lo is not None:
            print(f"   decoded: low-byte-first={a_lo}  high-byte-first={a_hi}")

        # --- verdict ----------------------------------------------------
        print("\n   VERDICT:")
        if before == after and before is not None and len(before) == 3:
            print("   RSP value did NOT change -> the PSC is not acting on")
            print("   the position command. Suspect framing or command bytes.")
            moved = False
        elif a_lo is not None:
            print("   RSP value CHANGED -> the PSC accepted the command.")
            print("   If the servo still did not move, the problem is after")
            print("   the controller: servo power, the channel header, or")
            print("   the servo itself.")
            moved = True
        else:
            print("   Inconclusive -- no usable RSP reply.")
            moved = False

        # --- 4. visible wiggle -----------------------------------------
        print("\n4. Wiggling ch{} between extremes, ramp=0 -- WATCH THE SERVO"
              .format(channel))
        for pw in (PW_MID, PW_MID + 250, PW_MID - 250, PW_MID):
            set_pos(ser, channel, pw, ramp=0)
            print(f"   -> {pw} ({pw * 2} us)")
            time.sleep(1.2)

        return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--baud", type=int, default=None,
                    help="default: try 38400 then 2400")
    ap.add_argument("--stopbits", type=int, default=None, choices=(1, 2),
                    help="default: try 2 then 1")
    args = ap.parse_args()

    bauds = [args.baud] if args.baud else [38400, 2400]
    stops = [args.stopbits] if args.stopbits else [2, 1]

    for sb in stops:
        for baud in bauds:
            if run(args.port, baud, sb, args.channel):
                print(f"\n>>> Working combination: baud={baud} stopbits={sb}")
                return 0

    print("\nNo combination produced a confirmed position change.")
    print("Next things to check:")
    print("  - the servo power switch on the PSC is ON")
    print("  - the separate servo supply is connected to the screw terminals")
    print("  - the servo plug orientation on the channel header "
          "(white/signal toward the 'S' label)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
