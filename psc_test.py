#!/usr/bin/env python3
"""Pan/tilt test via the Parallax Servo Controller USB (#28823).

Same two phases as servo_test.py, but the PSC's firmware ramp does the
acceleration instead of a Python loop:

  Phase 1: slow sweep +/- SWEEP_DEG around center, 5 times.
  Phase 2: 5 random targets within +/- RANDOM_DEG of center, 3s dwell.

Usage:
    python3 psc_test.py [--port /dev/ttyUSB0] [--ramp N]
"""

import argparse
import random
import time

from psc import PSC, angle_to_position, position_to_angle

PAN_CHANNEL = 0
TILT_CHANNEL = 1

SWEEP_DEG = 20
SWEEP_CYCLES = 5
SWEEP_RAMP = 20      # slower ramp for the sweep

RANDOM_DEG = 30
RANDOM_TARGETS = 5
DWELL = 3.0
MOVE_RAMP = 10       # default ramp for random moves

CENTER = 0.0


def wait_until_settled(psc, channels, timeout=10.0, poll=0.1, stable=3):
    """Poll positions until they stop changing.

    The PSC reports live position during a ramp, so this is how we know
    a move finished without hardcoding a duration. Returns elapsed time.
    """
    start = time.time()
    last = None
    unchanged = 0

    while time.time() - start < timeout:
        time.sleep(poll)
        current = tuple(psc.get_position(c) for c in channels)
        if None in current:
            # No reply: fall back to a fixed wait rather than spinning.
            time.sleep(1.0)
            return time.time() - start
        if current == last:
            unchanged += 1
            if unchanged >= stable:
                return time.time() - start
        else:
            unchanged = 0
            last = current

    return time.time() - start


def goto(psc, pan_deg, tilt_deg, ramp):
    psc.set_position(PAN_CHANNEL, angle_to_position(pan_deg), ramp=ramp)
    psc.set_position(TILT_CHANNEL, angle_to_position(tilt_deg), ramp=ramp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--ramp", type=int, default=MOVE_RAMP,
                    help="ramp rate 0-63 for phase 2 (0 = full speed)")
    ap.add_argument("--jumper", action="store_true",
                    help="channel jumper fitted (channels become 16-31)")
    ap.add_argument("--debug", action="store_true",
                    help="show the baud probe and other protocol detail")
    args = ap.parse_args()

    with PSC(args.port, jumper=args.jumper, debug=args.debug) as psc:
        print(f"Connected to PSC on {args.port} at {psc.baudrate} baud, "
              f"firmware {psc.version}")
        print(f"Phase 2 ramp rate: {args.ramp}")

        print(f"\nCentering")
        goto(psc, CENTER, CENTER, ramp=0)
        time.sleep(1.0)

        # ---- phase 1: sweep --------------------------------------------
        print(f"\nSweeping +/-{SWEEP_DEG} deg, {SWEEP_CYCLES} cycles, "
              f"ramp {SWEEP_RAMP}")
        for i in range(SWEEP_CYCLES):
            print(f"  cycle {i + 1}/{SWEEP_CYCLES}")
            for end in (CENTER + SWEEP_DEG, CENTER - SWEEP_DEG, CENTER):
                goto(psc, end, end, ramp=SWEEP_RAMP)
                wait_until_settled(psc, (PAN_CHANNEL, TILT_CHANNEL))

        # ---- phase 2: random targets -----------------------------------
        print(f"\n{RANDOM_TARGETS} random targets within +/-{RANDOM_DEG} deg")
        for i in range(RANDOM_TARGETS):
            tx = CENTER + random.uniform(-RANDOM_DEG, RANDOM_DEG)
            ty = CENTER + random.uniform(-RANDOM_DEG, RANDOM_DEG)

            here = [psc.get_position(c)
                    for c in (PAN_CHANNEL, TILT_CHANNEL)]
            here_deg = [position_to_angle(p) if p is not None else float("nan")
                        for p in here]

            print(f"  [{i + 1}/{RANDOM_TARGETS}] "
                  f"from ({here_deg[0]:+6.1f}, {here_deg[1]:+6.1f}) "
                  f"-> ({tx:+6.1f}, {ty:+6.1f})  "
                  f"[pw {angle_to_position(tx)}, {angle_to_position(ty)}]")

            goto(psc, tx, ty, ramp=args.ramp)
            elapsed = wait_until_settled(psc, (PAN_CHANNEL, TILT_CHANNEL))
            print(f"        settled in {elapsed:.2f}s, dwelling {DWELL:.0f}s")
            time.sleep(DWELL)

        print("\nReturning to center")
        goto(psc, CENTER, CENTER, ramp=args.ramp)
        wait_until_settled(psc, (PAN_CHANNEL, TILT_CHANNEL))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted")
