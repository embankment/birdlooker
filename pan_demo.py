#!/usr/bin/env python3
"""Step 1: pan the camera while pi-webrtc is streaming.

Two jobs:

  1. Confirm the servo control and the video encoder coexist -- that the
     serial link stays reliable under encoder load, that CPU headroom
     holds, and that servo motion does not shake the picture.

  2. Measure the camera's real horizontal field of view, which step 2
     needs. The click-to-look mapping is

         angle_offset = (click_position - center) x FOV

     so a guessed FOV means every click lands slightly off. Measuring it
     against the actual lens and resolution costs one minute here and
     makes step 2 correct on the first try.

Run pi-webrtc first, open the stream, then run this.

    python3 pan_demo.py calibrate   # measure the FOV
    python3 pan_demo.py sweep       # continuous pan, watch for shake
    python3 pan_demo.py load        # rapid moves, stress the serial link
"""

import argparse
import sys
import time

from psc import PSC, angle_to_position

PAN_CHANNEL = 0
TILT_CHANNEL = 1

RAMP_SMOOTH = 10
RAMP_FAST = 3

PAN_LIMIT = 60.0    # soft limit, degrees either side of center
TILT_LIMIT = 45.0


def goto(psc, pan, tilt, ramp=RAMP_SMOOTH):
    pan = max(-PAN_LIMIT, min(PAN_LIMIT, pan))
    tilt = max(-TILT_LIMIT, min(TILT_LIMIT, tilt))
    psc.set_position(PAN_CHANNEL, angle_to_position(pan), ramp=ramp)
    psc.set_position(TILT_CHANNEL, angle_to_position(tilt), ramp=ramp)
    return pan, tilt


def calibrate(psc, step, settle):
    """Pan in known steps so the FOV can be read off the stream.

    Method: put something small and distinctive at the centre of frame,
    then pan by a known angle and see how far across the frame it moved.

        HFOV = step_angle / fraction_of_frame_width_it_moved

    Two readings at different step sizes should agree; if they don't,
    the servo is not actually travelling the angle it is commanded and
    the servo calibration needs looking at before anything else.
    """
    print(__doc__.split("Run pi-webrtc")[0].strip())
    print("\n" + "=" * 64)
    print("FOV CALIBRATION")
    print("=" * 64)
    print("""
Before starting:
  - Have the stream open and visible.
  - Put a small distinctive object (a mug, a bit of tape) at the CENTRE
    of the frame. The centre matters: lens distortion is worst at the
    edges, so a target that starts centred and ends mid-frame gives a
    better number than one that sweeps edge to edge.

At each step, note roughly where the object sits as a fraction of the
frame width: 0.0 is the left edge, 0.5 the centre, 1.0 the right edge.
""")
    input("Press Enter when the object is centred and the stream is up...")

    print(f"\nCentring (pan 0)")
    goto(psc, 0.0, 0.0, ramp=RAMP_SMOOTH)
    time.sleep(settle)

    readings = []
    for angle in (step, step * 2):
        print(f"\n--> panning to {angle:+.1f} deg")
        goto(psc, angle, 0.0, ramp=RAMP_SMOOTH)
        time.sleep(settle)
        raw = input(f"    object is now at what fraction of frame width? "
                    f"[0.0-1.0, or 'off' if it left the frame]: ").strip()
        if raw.lower().startswith("off"):
            print("    (left the frame -- ignoring this step)")
            continue
        try:
            frac = float(raw)
        except ValueError:
            print("    (not a number -- ignoring)")
            continue
        shift = abs(frac - 0.5)
        if shift < 0.02:
            print("    (barely moved -- check the servo is actually turning)")
            continue
        hfov = abs(angle) / shift
        readings.append(hfov)
        print(f"    moved {shift:.3f} of frame width "
              f"-> HFOV approx {hfov:.1f} deg")

    print("\nReturning to centre")
    goto(psc, 0.0, 0.0, ramp=RAMP_SMOOTH)
    time.sleep(settle)

    print("\n" + "=" * 64)
    if not readings:
        print("No usable readings.")
        return
    mean = sum(readings) / len(readings)
    print(f"Readings: {', '.join(f'{r:.1f}' for r in readings)}")
    print(f"Estimated HFOV: {mean:.1f} deg")
    if len(readings) > 1:
        spread = max(readings) - min(readings)
        if spread > mean * 0.15:
            print(f"\nNOTE: readings differ by {spread:.1f} deg. Either the")
            print("eyeballed fractions are rough, or the servo is not")
            print("travelling the angle it is told -- worth checking before")
            print("relying on this number.")
    print(f"\nFor reference, a stock IMX219 lens is about 62 deg horizontal.")
    print(f"A number far from that suggests the servo scaling is off, not")
    print(f"the lens.")
    print(f"\nPut this in STATUS.md and step 2 will use it:")
    print(f"    HFOV = {mean:.1f}")
    print(f"    VFOV = {mean * 3 / 4:.1f}   (4:3 sensor, approximate)")
    print("=" * 64)


def sweep(psc, span, settle):
    """Slow continuous pan. Watch the stream for shake or dropped frames."""
    print(f"Sweeping +/-{span} deg until Ctrl-C. Watch the stream for:")
    print("  - picture shake while the servo moves (mounting rigidity)")
    print("  - frames dropping or stuttering (CPU contention)")
    print("  - the motion looking smooth or mechanical (ramp tuning)\n")
    n = 0
    while True:
        for target in (span, -span):
            n += 1
            print(f"  [{n}] -> pan {target:+.0f}")
            goto(psc, target, 0.0, ramp=RAMP_SMOOTH)
            time.sleep(settle)


def load(psc, span, count):
    """Rapid commands, to stress the serial link while the encoder runs."""
    print(f"Sending {count} rapid position commands...")
    t0 = time.time()
    errors = 0
    for i in range(count):
        angle = span if i % 2 else -span
        try:
            goto(psc, angle, 0.0, ramp=RAMP_FAST)
        except Exception as e:
            errors += 1
            print(f"  command {i} failed: {e}")
        time.sleep(0.05)
    elapsed = time.time() - t0
    print(f"\n{count} commands in {elapsed:.1f}s "
          f"({count / elapsed:.1f}/sec), {errors} errors")

    ver = psc._try_version()
    print(f"Link still alive afterwards: "
          f"{'yes, firmware ' + ver if ver else 'NO -- link died'}")
    goto(psc, 0.0, 0.0, ramp=RAMP_SMOOTH)


def main():
    ap = argparse.ArgumentParser(
        description="Pan the camera while pi-webrtc streams.")
    ap.add_argument("mode", choices=("calibrate", "sweep", "load"))
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--step", type=float, default=10.0,
                    help="calibrate: degrees per step (default 10)")
    ap.add_argument("--span", type=float, default=20.0,
                    help="sweep/load: degrees either side of centre")
    ap.add_argument("--settle", type=float, default=2.5,
                    help="seconds to wait after each move")
    ap.add_argument("--count", type=int, default=100,
                    help="load: number of commands")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    with PSC(args.port, debug=args.debug) as psc:
        print(f"PSC on {args.port} at {psc.baudrate} baud, "
              f"firmware {psc.version}\n")
        if args.mode == "calibrate":
            calibrate(psc, args.step, args.settle)
        elif args.mode == "sweep":
            sweep(psc, args.span, args.settle)
        else:
            load(psc, args.span, args.count)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)
