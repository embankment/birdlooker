#!/usr/bin/env python3
"""Compare host-interpolated motion against the controller's own ramp.

Same two phases as psc_test.py, but motion goes through motion.Mover:
ramp=0 on every command, 50 Hz updates from here, both axes advancing
against one eased progress value so they arrive together.

Run psc_test.py and this back to back on the same moves. If the firmware
ramp was the cause of the stepping, the difference is obvious. If this
still looks coarse, the servos' dead band is the limit -- deadband.py
measures that.

    python3 smooth_test.py                 # both phases
    python3 smooth_test.py --speed 15      # slower, to exaggerate stepping
    python3 smooth_test.py --linear        # no easing, for comparison
    python3 smooth_test.py --diagonal      # just the dogleg test
"""

import argparse
import random
import sys
import time

import motion
from psc import PSC

PAN_CHANNEL = 0
TILT_CHANNEL = 1

SWEEP_DEG = 20
SWEEP_CYCLES = 5
RANDOM_DEG = 30
RANDOM_TARGETS = 5
DWELL = 3.0


def diagonal_test(mover):
    """Move where the axes travel very different distances.

    This is where independent per-axis ramping shows worst: with the
    firmware ramp, the short axis finishes first and the camera visibly
    turns a corner. Coordinated interpolation should trace a straight
    line instead.
    """
    print("\nDiagonal test -- watch whether the camera travels in a")
    print("straight line or turns a corner partway.\n")
    pairs = [(30, 5), (-30, -5), (5, 30), (-5, -30), (0, 0)]
    for pan, tilt in pairs:
        print(f"  -> ({pan:+3}, {tilt:+3})  "
              f"[pan moves {abs(pan - mover.pan):.0f} deg, "
              f"tilt {abs(tilt - mover.tilt):.0f} deg]")
        elapsed = mover.move_to(pan, tilt)
        print(f"     arrived in {elapsed:.2f}s")
        time.sleep(1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--speed", type=float, default=motion.DEFAULT_SPEED,
                    help=f"degrees/sec (default {motion.DEFAULT_SPEED})")
    ap.add_argument("--linear", action="store_true",
                    help="no easing, constant rate")
    ap.add_argument("--diagonal", action="store_true",
                    help="only run the dogleg test")
    ap.add_argument("--deadband", type=str, default=None,
                    help="override measured dead bands, e.g. --deadband 0:4,1:1 "
                         "or --deadband off to disable quantizing")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    ease = motion.linear if args.linear else motion.smoothstep

    deadband = None
    if args.deadband == "off":
        deadband = {}
    elif args.deadband:
        deadband = {}
        for pair in args.deadband.split(","):
            ch, val = pair.split(":")
            deadband[int(ch)] = int(val)

    with PSC(args.port, debug=args.debug) as psc:
        print(f"PSC firmware {psc.version} at {psc.baudrate} baud")
        print(f"Host interpolation at {motion.RATE:.0f} Hz, "
              f"{args.speed:.0f} deg/sec, "
              f"{'linear' if args.linear else 'eased'}")

        mover = motion.Mover(psc, PAN_CHANNEL, TILT_CHANNEL,
                             speed=args.speed, ease=ease, deadband=deadband)
        print(f"Dead bands: pan {mover.pan_deadband} units "
              f"({mover.pan_deadband * 0.184:.2f} deg), "
              f"tilt {mover.tilt_deadband} units "
              f"({mover.tilt_deadband * 0.184:.2f} deg)")

        print("\nCentering")
        mover.jump(0, 0)
        time.sleep(1.0)

        if args.diagonal:
            diagonal_test(mover)
            return 0

        # ---- phase 1: sweep -------------------------------------------
        print(f"\nSweeping +/-{SWEEP_DEG} deg, {SWEEP_CYCLES} cycles")
        for i in range(SWEEP_CYCLES):
            print(f"  cycle {i + 1}/{SWEEP_CYCLES}")
            for end in (SWEEP_DEG, -SWEEP_DEG, 0):
                mover.move_to(end, end)

        # ---- phase 2: random targets ----------------------------------
        print(f"\n{RANDOM_TARGETS} random targets within +/-{RANDOM_DEG} deg")
        for i in range(RANDOM_TARGETS):
            tx = random.uniform(-RANDOM_DEG, RANDOM_DEG)
            ty = random.uniform(-RANDOM_DEG, RANDOM_DEG)
            print(f"  [{i + 1}/{RANDOM_TARGETS}] "
                  f"from ({mover.pan:+6.1f}, {mover.tilt:+6.1f}) "
                  f"-> ({tx:+6.1f}, {ty:+6.1f})")
            elapsed = mover.move_to(tx, ty)
            print(f"        arrived in {elapsed:.2f}s, "
                  f"dwelling {DWELL:.0f}s")
            time.sleep(DWELL)

        print("\nReturning to center")
        mover.move_to(0, 0)

        if mover.skipped:
            print(f"\nSkipped {mover.skipped} commands that fell inside a")
            print("dead band -- those would have moved nothing.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)
