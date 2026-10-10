#!/usr/bin/env python3
"""Measure a servo's dead band -- the smallest step it actually responds to.

Why this matters: interpolating smoothly in software only helps if the
servo can resolve the steps we send. A worn servo has a dead band, a
range of pulse width where it does not move at all. Steps smaller than
that produce nothing, then a lurch once the accumulated error finally
crosses it. That looks identical to the controller's firmware ramp
stepping, but no amount of software smoothing will fix it.

The PSC's resolution is 2 us per step. A healthy hobby servo responds to
roughly 2-5 us (1-3 steps); a tired one can need 10-20 us (5-10 steps).

Method: oscillate the servo by a given step size several times and ask
whether any movement is visible. Work down until it stops responding.
Watch the servo ARM, not the video -- and put a long pointer on it if
you have one, since a small angular change is easier to see at radius.

    python3 deadband.py --channel 0
"""

import argparse
import sys
import time

from psc import PSC, PW_MID

# Step sizes to try, in PSC units (1 unit = 2 us of pulse width).
LADDER = (20, 10, 6, 4, 3, 2, 1)

OSCILLATIONS = 6
DWELL = 0.45


def oscillate(psc, channel, centre, step):
    """Rock the servo +/- step around centre a few times."""
    for _ in range(OSCILLATIONS):
        psc.set_position(channel, centre + step, ramp=0)
        time.sleep(DWELL)
        psc.set_position(channel, centre - step, ramp=0)
        time.sleep(DWELL)
    psc.set_position(channel, centre, ramp=0)
    time.sleep(DWELL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--centre", type=int, default=PW_MID,
                    help=f"position to rock around (default {PW_MID})")
    args = ap.parse_args()

    print(__doc__.split("    python3")[0].strip())
    print("\n" + "=" * 62)

    with PSC(args.port) as psc:
        print(f"PSC firmware {psc.version} at {psc.baudrate} baud")
        print(f"Testing channel {args.channel} around position {args.centre}")
        print("\nWatch the servo arm. Answer y if you see ANY movement.\n")

        psc.set_position(args.channel, args.centre, ramp=0)
        time.sleep(1.0)

        smallest = None
        for step in LADDER:
            print(f"--- step +/-{step} units ({step * 2} us) ---")
            oscillate(psc, args.channel, args.centre, step)
            ans = input("    movement? [y/n/q] ").strip().lower()
            if ans.startswith("q"):
                break
            if ans.startswith("y"):
                smallest = step
            else:
                print(f"    -> no response below {smallest or '?'} units")
                break

        print("\n" + "=" * 62)
        if smallest is None:
            print("No movement at any step size. Check the servo is")
            print("connected to this channel and has power.")
            return 1

        print(f"Smallest step that moved the servo: "
              f"{smallest} units = {smallest * 2} us")

        # Translate into what it means for smooth motion.
        # angle_to_position spans SAFE_MIN..SAFE_MAX over 180 degrees.
        deg_per_unit = 180.0 / (1240 - 260)
        resolution = smallest * deg_per_unit
        print(f"Angular resolution: about {resolution:.2f} deg per "
              f"usable step")

        if smallest <= 3:
            print("\nHealthy. The servo can resolve fine steps, so smooth"
                  "\ninterpolation will look smooth.")
        elif smallest <= 6:
            print("\nMarginal. Workable, but slow moves will show some"
                  "\nstepping no matter how finely we interpolate.")
        else:
            print("\nWorn. This dead band is large enough that the servo"
                  "\nitself is the limit, not the software. Slow moves will"
                  "\nalways look coarse. New servos are the fix.")

        print(f"\nFor smooth motion, keep each interpolation step at or")
        print(f"above {smallest} units. At 50 Hz that means a minimum")
        print(f"useful speed of about {resolution * 50:.0f} deg/sec --")
        print(f"below that, steps fall inside the dead band.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)
