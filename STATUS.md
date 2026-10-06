# birdlooker — status

Pan/tilt servo control for the bird camera. Last updated 2026-10-05.

## Hardware

- Raspberry Pi 4 (a Pi 5 was tried first; see notes below)
- Raspberry Pi Camera Module 2 (IMX219)
- Two servos: pan on **GPIO12**, tilt on **GPIO13**
- OS: Bookworm or later (pip is externally-managed)

## Where things stand

**Working:** servos move under `servo_test.py` using gpiozero/lgpio
software PWM. Motion confirmed, but with visible jitter.

**In progress:** switching to hardware PWM to reduce jitter.

### Exact next step

```bash
cd ~/birdlooker
python3 -m venv --system-site-packages venv
venv/bin/pip install rpi-hardware-pwm
venv/bin/python servo_test.py --hw
```

`sudo venv/bin/python ...` if the sysfs PWM nodes refuse permission.

Not yet verified: whether hardware PWM actually reduces the jitter.
If it doesn't, suspect the servos themselves or the power supply
rather than the pulse source — see "Open questions".

## Files

- `servo_test.py` — sweep phase, then eased random targets.
  `--hw` selects the hardware PWM backend; default is gpiozero.
- `hwservo.py` — `HardwareServo`, a drop-in `AngularServo` replacement
  built on `rpi-hardware-pwm`.

## Motion model

Each tick (20 ms), step `EASE` (0.08) of the remaining distance toward
the target, clamped to `SPEED_CAP` (40 deg/sec). The fraction gives
deceleration into the target; the cap protects the servo when a target
jumps a long way. Both are in `servo_test.py`.

This is the model intended for the multi-viewer click-voting layer
later: the vote accumulator sets a target, this moves toward it.

## Gotchas found the hard way

- **The PWM overlay takes the pins away from gpiozero.** With
  `dtoverlay=pwm-2chan` active on GPIO12/13, gpiozero raises `KeyError`
  on those pins. Software and hardware PWM are mutually exclusive
  there — you cannot A/B them without editing config.txt and rebooting.

- **The overlay line differs by model.** In `/boot/firmware/config.txt`:
  - Pi 4: `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4`
    (plain `pwm-2chan` maps GPIO18/19 on a Pi 4, not 12/13)
  - Pi 5: `dtoverlay=pwm-2chan` (already maps 12/13)
  - Verify with `cat /sys/class/pwm/pwmchip0/npwm` → `2`

- **pigpio and RPi.GPIO do not work on the Pi 5.** Only lgpio does.
  gpiozero defaults to lgpio, so it works on both models.

- **gpiozero's default pulse widths are 1-2 ms**, which makes a typical
  servo travel about half its range and then jitter. The code sets
  0.5-2.5 ms explicitly. Tune to the servo's datasheet.

- **config.txt lives on a FAT partition** — chmod does nothing there.
  Edit with sudo.

## Camera / streaming (separate but same rig)

pi-webrtc with WHEP signaling, verified working:

```
pi-webrtc --camera=libcamera:0 --uid=home-pi-5 --fps=30 \
  --width=1600 --height=1200 --use-whep --whep-port=8080 \
  --no-audio --rotation=180
```

Viewer: https://tzuhuantai.github.io/webrtc-player/demo/ , adapter on
WHEP, URL `http://<pi-ip>:8080` (bare, no path). Chrome must be allowed
local network access; other browsers block it as mixed content.

- At 1600x1200 / 30 fps on the Pi 4, load is ~94% spread over 4 cores.
- **Width must be a multiple of 64** or libcamera's stride padding
  trips "Stride is not equal to width". 1640 fails; 1600 works.
- 1640x1232 and 1600x1200 use the full sensor FOV; 1920x1080 is a
  cropped mode with narrower FOV.
- The Pi 5 has no hardware H.264 encoder — do not pass `--hw-accel`
  there. The Pi 4 does.

## Open questions

- Does hardware PWM actually fix the jitter? If jitter persists while a
  servo is *parked* at a target, the pulse source isn't the cause —
  look at the servos or the supply. Jitter only during motion points
  back at the pulse train.
- Are the servos on their own supply with ground tied to the Pi?
  Running them off the Pi's 5V rail gets worse once the camera streams.
- A PCA9685 I2C breakout is the fallback if neither PWM path is clean
  enough, and the answer if the servo count grows past two.
- Mirroring: `--rotation` handles 0/90/180/270, but there is no hflip
  in pi-webrtc. Viewer-side CSS `scaleX(-1)` or a v4l2loopback virtual
  camera are the options if a true mirror is ever needed.
