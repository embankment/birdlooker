# birdlooker — status

Pan/tilt servo control for the bird camera. Last updated 2026-10-05.

## Hardware

- Raspberry Pi 4 (a Pi 5 was tried first; see notes below)
- Raspberry Pi Camera Module 2 (IMX219)
- Two servos: pan on **GPIO12**, tilt on **GPIO13**
- OS: Bookworm or later (pip is externally-managed)

## PSC-USB (#28823) — WORKING as of 2026-10-08

Pivoted from direct GPIO to the Parallax Servo Controller USB, to get
jitter-free pulses, simpler wiring, and the hat connector back.
`psc.py` + `psc_test.py`. Pan on channel 0, tilt on channel 1.

Two separate faults had to be fixed before anything moved:

1. **The addressing jumper has to be off.** With it fitted the board
   answers on channels 16-31, so commands to 0-15 are ignored. This was
   the one that finally made it go. Removing it mid-session did not
   appear to help at the time — fault 2 was still masking it.

2. **The serial line echoes.** The PSC's interface is single-wire
   bidirectional, so the USB board's FTDI chip reads back every byte
   transmitted before any reply arrives. The echo is an electrical
   loopback: it returns cleanly at whatever baud the FTDI is set to,
   whether or not the controller understood anything. Reading 3 bytes
   straight after a write returned `!SC` -- the first echoed bytes --
   which passed as a version reply and made every baud rate look
   correct. The board sat at 2400 while we transmitted at 38400.
   Every command must drain its own echo first.

### Other PSC facts worth keeping

- Position range on the wire is **250-1250 in 2 us steps**, which is
  HALF the microsecond numbers the Windows PSCI GUI displays.
  750 = 1500 us = center.
- Command is exactly 8 bytes: `!SC` + chan + ramp + pw_low + pw_high + CR.
  Verified byte-identical to the datasheet's PBASIC example:
  `21 53 43 0F 07 E2 04 0D` for ch=15 ra=7 pw=1250.
- **The datasheet contradicts itself on stop bits.** Prose says
  "2400 N 8 2"; its own PBASIC example uses baudmode 396 = 2400 8N1.
  One stop bit is what works.
- Ramp 0-63 is the controller's own acceleration curve, done in
  firmware. 0 = immediate; 63 ~ 60 s for a full excursion. This
  replaces the Python easing loop from the GPIO version.
- Baud defaults to 2400 and persists until a hardware reset, so the
  host and board can disagree after a restart. `connect()` probes both.
- Servos need their own supply on the screw terminals; USB powers only
  the logic. So VER? answers even with the servo switch off.
- `psc_probe.py` is the diagnostic: raw hex of echo and reply, both RSP
  byte orders, and a sweep of baud and stop-bit combinations.

## Camera field of view — MEASURED 2026-10-09

    HFOV = 66.7 deg
    VFOV = 50.0 deg   (4:3 sensor, derived as HFOV * 3/4)

Measured with `pan_demo.py calibrate`: pan a known angle, see how far a
centred target shifts as a fraction of frame width, then
`HFOV = angle / shift`. This is for the current camera and ribbon (both
swapped on 2026-10-09) at 1600x1200. Re-measure if either changes.

Step 2's click-to-look mapping uses these:

    pan_offset  = (click_x - 0.5) * HFOV
    tilt_offset = (0.5 - click_y) * VFOV

Slightly wider than the ~62 deg a stock IMX219 lens is quoted at, which
is within eyeball-the-fraction error.

### Still open

- Does the firmware ramp look better or worse than the old Python
  ease-out on camera? Linear-rate vs ease-out. `--ramp N` to taste.
- RSP byte order is decoded low-byte-first to match the command, but
  that has not been confirmed against a known position.

## Earlier: direct GPIO approach (superseded)

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
