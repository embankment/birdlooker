#!/usr/bin/env python3
"""Find the mount's real travel limits, and a home position.

The limits in config.py started as guesses. If the mount cannot reach
them, the servo drives into a hard stop and sits there stalled --
drawing current, heating, and grinding the gear train. A worn servo
does not need the help.

This walks each axis outward in small steps and waits for you to say
when it has gone far enough. Nothing moves faster than one step at a
time, so there is always a chance to stop before anything strains.

    python3 find_limits.py

Stop a sweep the moment you see or hear ANY of:
  - the mount reaching its mechanical end
  - the camera ribbon or servo wires pulling taut
  - the servo buzzing or straining rather than holding quietly
  - the view running past anything you would ever want to see

Then back off one step from wherever you stopped; the suggested limits
below already include that margin.

Stop the control server first -- it owns the serial port:

    sudo systemctl stop birdlooker-control
"""

import argparse
import sys
import time

import config
import motion
from psc import PSC

STEP = 5.0          # degrees per press
SETTLE = 0.35       # let it arrive before asking again
MARGIN = 3.0        # degrees held back from whatever you accept


def walk(mover, axis, direction, ceiling):
    """Step one axis outward until the operator says stop.

    Returns the last angle accepted as safe.
    """
    label = "pan" if axis == "pan" else "tilt"
    way = "positive" if direction > 0 else "negative"
    print(f"\n--- {label}, {way} direction ---")
    print("    Enter to step further, 's' to stop here, 'q' to quit")

    angle = 0.0
    last_ok = 0.0

    while True:
        nxt = angle + STEP * direction
        if abs(nxt) > ceiling:
            print(f"    reached the configured ceiling of {ceiling:.0f} deg")
            return last_ok

        if axis == "pan":
            mover.move_to(nxt, mover.tilt)
        else:
            mover.move_to(mover.pan, nxt)
        time.sleep(SETTLE)
        angle = nxt

        answer = input(f"    at {angle:+6.1f} deg > ").strip().lower()
        if answer.startswith("q"):
            raise KeyboardInterrupt
        if answer.startswith("s"):
            # The angle they stopped AT is already too far by one step,
            # or they would have carried on. Back off.
            return angle - STEP * direction
        last_ok = angle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=config.SERIAL_PORT)
    ap.add_argument("--ceiling", type=float, default=80.0,
                    help="never travel beyond this, whatever you press")
    args = ap.parse_args()

    print(__doc__.split("Stop the control server")[0].strip())
    print("\n" + "=" * 62)
    input("Enter to centre the camera and begin...")

    with PSC(args.port) as psc:
        print(f"PSC firmware {psc.version} at {psc.baudrate} baud")

        # Open the limits right up for the duration, or we would only
        # ever measure the guesses we are trying to replace.
        mover = motion.Mover(
            psc,
            pan_channel=config.PAN_CHANNEL,
            tilt_channel=config.TILT_CHANNEL,
            pan_limit=args.ceiling,
            tilt_limit=args.ceiling,
            speed=15.0,
        )

        results = {}
        try:
            mover.jump(0.0, 0.0)
            time.sleep(1.0)

            results["pan+"] = walk(mover, "pan", +1, args.ceiling)
            mover.move_to(0.0, 0.0); time.sleep(0.6)

            results["pan-"] = walk(mover, "pan", -1, args.ceiling)
            mover.move_to(0.0, 0.0); time.sleep(0.6)

            results["tilt+"] = walk(mover, "tilt", +1, args.ceiling)
            mover.move_to(0.0, 0.0); time.sleep(0.6)

            results["tilt-"] = walk(mover, "tilt", -1, args.ceiling)
            mover.move_to(0.0, 0.0); time.sleep(0.6)

        except KeyboardInterrupt:
            print("\n\nStopped. Returning to centre.")
            try:
                mover.move_to(0.0, 0.0)
            except Exception:
                pass
            if not results:
                return 1

        # --- report ---------------------------------------------------
        print("\n" + "=" * 62)
        print("MEASURED TRAVEL")
        print("=" * 62)
        for key in ("pan+", "pan-", "tilt+", "tilt-"):
            if key in results:
                print(f"  {key:6} {results[key]:+6.1f} deg")

        pan_pos = results.get("pan+", config.PAN_LIMIT)
        pan_neg = abs(results.get("pan-", config.PAN_LIMIT))
        tilt_pos = results.get("tilt+", config.TILT_LIMIT)
        tilt_neg = abs(results.get("tilt-", config.TILT_LIMIT))

        # The limits are symmetric about centre, so the usable range is
        # the SMALLER side of each axis. Taking the larger would let the
        # camera drive into the stop on the tighter side.
        pan_limit = max(5.0, min(pan_pos, pan_neg) - MARGIN)
        tilt_limit = max(5.0, min(tilt_pos, tilt_neg) - MARGIN)

        print(f"\n  Usable range is the smaller side of each axis, less")
        print(f"  a {MARGIN:.0f} deg margin:")
        print(f"\n    PAN_LIMIT = {pan_limit:.0f}")
        print(f"    TILT_LIMIT = {tilt_limit:.0f}")

        if abs(pan_pos - pan_neg) > 10 or abs(tilt_pos - tilt_neg) > 10:
            print(f"\n  NOTE: the two sides differ by more than 10 deg, so")
            print(f"  the mount's mechanical centre is not where the servo")
            print(f"  thinks centre is. Symmetric limits throw away the")
            print(f"  extra travel on the roomier side. Worth re-seating")
            print(f"  the servo horn if you want it back.")

        print(f"\n  Put these in config.py, then:")
        print(f"      sudo systemctl start birdlooker-control")
        print("=" * 62)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(1)
