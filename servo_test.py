#!/usr/bin/env python3
"""
Pan/tilt servo test — GPIO12 (X/pan) and GPIO13 (Y/tilt).

Phase 1: slow sweep +/- SWEEP_DEG around midrange, 5 times.
Phase 2: 5 random targets within +/- RANDOM_DEG of midrange, using
         ease-out motion with a hard speed cap, 3s dwell at each.
"""

import random
import sys
import time

# --hw selects hardware PWM (needs the dtoverlay - see hwservo.py).
# Default is gpiozero/lgpio software PWM.
USE_HARDWARE_PWM = "--hw" in sys.argv

if USE_HARDWARE_PWM:
    from hwservo import HardwareServo as ServoClass
else:
    from gpiozero import AngularServo as ServoClass

# ---- servo calibration -------------------------------------------------
PAN_PIN = 12
TILT_PIN = 13
MIN_ANGLE = -90
MAX_ANGLE = 90
MIN_PULSE = 0.0005   # 0.5 ms - check your servo datasheet
MAX_PULSE = 0.0025   # 2.5 ms

# ---- motion parameters -------------------------------------------------
SWEEP_DEG = 20       # phase 1: +/- this many degrees around midrange
SWEEP_CYCLES = 5
SWEEP_SPEED = 25     # deg/sec during the sweep

RANDOM_DEG = 30      # phase 2: random targets within +/- this of midrange
RANDOM_TARGETS = 5
DWELL = 3.0          # seconds to hold at each random target

TICK = 0.02          # 50 Hz update rate
EASE = 0.08          # fraction of remaining distance per tick
SPEED_CAP = 40       # deg/sec ceiling - mechanical protection
TOLERANCE = 0.5      # degrees; close enough to call it arrived

MID = (MIN_ANGLE + MAX_ANGLE) / 2


def make_servo(pin):
    return ServoClass(
        pin,
        min_angle=MIN_ANGLE,
        max_angle=MAX_ANGLE,
        min_pulse_width=MIN_PULSE,
        max_pulse_width=MAX_PULSE,
    )


def move_to(pan, tilt, pos, target_x, target_y, speed_cap=SPEED_CAP):
    """Ease toward (target_x, target_y), capped at speed_cap deg/sec.

    pos is a mutable [x, y] holding current commanded position.
    Returns when both axes are within TOLERANCE.
    """
    max_step = speed_cap * TICK
    while True:
        dx = target_x - pos[0]
        dy = target_y - pos[1]
        if abs(dx) < TOLERANCE and abs(dy) < TOLERANCE:
            pos[0], pos[1] = target_x, target_y
            pan.angle, tilt.angle = pos
            return

        # ease-out: a fraction of what's left, then clamp to the speed cap
        step_x = max(-max_step, min(max_step, dx * EASE))
        step_y = max(-max_step, min(max_step, dy * EASE))

        pos[0] += step_x
        pos[1] += step_y
        pan.angle = pos[0]
        tilt.angle = pos[1]
        time.sleep(TICK)


def main():
    pan = make_servo(PAN_PIN)
    tilt = make_servo(TILT_PIN)
    pos = [MID, MID]

    try:
        backend = "hardware PWM" if USE_HARDWARE_PWM else "software PWM (lgpio)"
        print(f"Backend: {backend}")
        print(f"Centering at ({MID:.1f}, {MID:.1f})")
        pan.angle = tilt.angle = MID
        time.sleep(1.0)

        # ---- phase 1: slow sweep ---------------------------------------
        print(f"\nSweeping +/-{SWEEP_DEG} deg, {SWEEP_CYCLES} cycles")
        for i in range(SWEEP_CYCLES):
            print(f"  cycle {i + 1}/{SWEEP_CYCLES}")
            for end in (MID + SWEEP_DEG, MID - SWEEP_DEG, MID):
                move_to(pan, tilt, pos, end, end, speed_cap=SWEEP_SPEED)

        # ---- phase 2: random targets -----------------------------------
        print(f"\n{RANDOM_TARGETS} random targets within +/-{RANDOM_DEG} deg")
        for i in range(RANDOM_TARGETS):
            tx = MID + random.uniform(-RANDOM_DEG, RANDOM_DEG)
            ty = MID + random.uniform(-RANDOM_DEG, RANDOM_DEG)
            print(f"  [{i + 1}/{RANDOM_TARGETS}] "
                  f"from ({pos[0]:+6.1f}, {pos[1]:+6.1f}) "
                  f"-> ({tx:+6.1f}, {ty:+6.1f})")
            t0 = time.time()
            move_to(pan, tilt, pos, tx, ty)
            print(f"        arrived in {time.time() - t0:.2f}s, "
                  f"dwelling {DWELL:.0f}s")
            time.sleep(DWELL)

        print("\nReturning to center")
        move_to(pan, tilt, pos, MID, MID)
        time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        pan.detach()
        tilt.detach()
        pan.close()
        tilt.close()
        print("Servos detached")


if __name__ == "__main__":
    main()
