# birdlooker — status

Click-to-look bird camera: a live stream you can click to aim. Last
updated 2026-10-10.

## Working end to end

Open `http://<pi-ip>:8081/`, click anywhere in the picture, and the
camera centres on that point. Runs on boot; survives reboots.

Tested with three simultaneous viewers tapping freely: 89% peak CPU
across four cores, 71.5 C, no throttling.

    ./install_services.sh      # once, as root -- installs systemd units
    http://<pi-ip>:8081/       # that's it

## Hardware

- Raspberry Pi 4 (a Pi 5 was tried first; see notes below)
- Raspberry Pi Camera Module 2 (IMX219) — camera and ribbon swapped
  2026-10-09, still needs `--rotation=180`
- Parallax Servo Controller USB (#28823) on `/dev/ttyUSB0`
- Two servos: pan on PSC **channel 0**, tilt on **channel 1**
- OS: Bookworm or later (pip is externally-managed)

## Running as services

`install_services.sh` writes two independent systemd units:

    birdlooker-stream    pi-webrtc, video on 8080
    birdlooker-control   click server and viewer page on 8081

Deliberately not ordered against each other — either can restart
without disturbing the other, and the page reports "no stream" by
itself if pi-webrtc is down. `Restart=always` with `RestartSec=5`
covers the USB serial adapter not being enumerated yet at boot, which
is the common cold-start failure. `StartLimitIntervalSec=0` stops
systemd giving up permanently; an unattended camera sitting in failed
state is worse than one still retrying.

    systemctl status birdlooker-stream birdlooker-control
    journalctl -u birdlooker-control -f
    sudo systemctl restart birdlooker-control   # after editing config.py

The run user needs to be in `dialout` (serial) and `video` (camera);
the installer checks and tells you if not.

## Click-to-look

`control_server.py` serves the viewer page and takes clicks;
`static/index.html` is the page.

**Why a separate server at all:** WHEP carries no DataChannel.
pi-webrtc supports two-way messaging only under `--use-mqtt` or
`--use-livekit`, so clicks cannot ride the video connection. Moving to
LiveKit later would remove this whole second channel.

Serving our own page from the Pi also killed the mixed-content prompt —
page and stream are now the same scheme and host, so it works in any
browser rather than Chrome-with-permission.

Click mapping uses `atan`, not linear interpolation of the FOV: a
camera is a pinhole projection, so linear undershoots by about 1.5 deg
at a quarter-frame out.

Motion is owned by one thread, since the serial port is not
thread-safe and HTTP handlers are concurrent. A new target supersedes
the one in flight rather than queueing — click twice and the camera
abandons the first move for the second.

Rate limiting is a token bucket per IP, charged only after validation.
**Currently set generously for testing** (20 clicks, one back every
0.3 s). Public-facing values from the June design are much stricter.
Note buckets are keyed by IP, so several browsers on one laptop share
one bucket.

### Calibration lives in config.py

FOV, dead bands, channels, limits, axis inversion, rate limits. When
servos or geometry change, that file is the only one to edit.

Axis inversion was determined empirically — **both axes are inverted**.
Not predictable from first principles with the camera mounted upside
down and corrected in software.

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

## Servo dead bands — MEASURED 2026-10-09

    channel 0 (pan):  4 units = 8 us  -> 0.73 deg granularity   WORN
    channel 1 (tilt): 1 unit  = 2 us  -> 0.18 deg (hardware floor)  OK

Measured with `deadband.py`. The asymmetry was the useful part: tilt is
as good as the hardware allows, so the controller, wiring and protocol
are all fine and exactly one servo is tired.

**After swapping a servo, re-run `deadband.py` and update `DEADBAND` in
`config.py`.** That is the only change needed; a new servo measuring 1
or 2 just makes the motion finer. Beefier servos are planned for
channel 0.

### Why the controller's ramp was abandoned

The PSC's own ramp updates on the firmware's slow tick, visible as
stepping during slow moves, and each channel ramps independently so
diagonal moves dogleg. Ramp is also the ONLY motion parameter available
over serial — the GUI's Offset and Delay are host-side, not commands —
so there was nothing else to tune.

`motion.Mover` sends ramp=0 and interpolates here at 50 Hz, advancing
both axes against one eased progress value so the path is straight and
they arrive together.

### Dead-band quantization

A servo ignores any step below its dead band; the error then accumulates
silently and the servo lurches at intervals we do not control. So Mover
only transmits when the position differs from the last sent by at least
that channel's dead band. Same number of physical movements, evenly
spaced, on our schedule.

This also removes the "minimum useful speed" the probe warns about. At
10 deg/sec a pan move sends 28 commands and skips 167, every step
exactly 4 units. Slow panning works fine, just at 0.73 deg granularity.

`smooth_test.py --deadband off` disables it for comparison;
`--deadband 0:2,1:1` overrides without editing the file.

### Still open

- **RSP byte order is unverified.** Decoded low-byte-first to match the
  command, but never confirmed against a known position. Nothing in the
  current motion path reads it, so it is harmless today — and a trap for
  whoever next reaches for position readback.
- Easing is largely wasted on pan right now. `smoothstep` computes a
  gentle acceleration curve and 4-unit quantization rounds most of it
  away; linear would look near-identical on that axis today. Left in
  because it matters again with better servos.

## Next steps

Ordered by what unblocks what. Decided 2026-10-10.

1. ~~Reboot survivability~~ — done, see "Running as services".
2. **Pi 5 move.** Deferred but likely. Note it has NO hardware H.264
   encoder, so encoding stays in software; its faster CPU probably wins
   but that deserves a `monitor.sh` run rather than an assumption. A fan
   is planned, which addresses the thermal headroom question below.
3. **LiveKit SFU.** The Pi publishes once and the SFU fans out, so Pi
   load stops scaling with viewers. Clicks move onto the LiveKit
   DataChannel, deleting the separate HTTP control path.
4. **Aggregator on the VPS.** Vote accumulation, rate limiting and
   viewer verification move off the Pi, which then needs no inbound
   exposure at all. With an SFU the June design's heartbeat check gets
   better: the SFU knows its participants, so a voter can be verified
   against real subscription state rather than an inferred heartbeat.
5. **Vote accumulator.** Last, because tuning its constants needs
   several simultaneous real strangers.

`Camera.look_at()` is the seam for 4 and 5 — that is where "aggregator
says go here" replaces "HTTP handler says go here". Everything
downstream (motion, quantization, FOV mapping, inversion) is unaffected.

Independent of all the above: the beefier servos, whenever they arrive.

### Considered and set aside

**Twitch / YouTube Live.** Feasible for watching, not for clicking.
Low-latency modes still run 2-5 seconds, so you would be clicking where
a bird was several seconds ago. Not mutually exclusive with the SFU
though: LiveKit egress can push the same room to RTMP, giving WebRTC to
people who want to steer and a CDN to people who just want a bird
channel — on one encode. Worth revisiting after step 3.

## Measurements

Peak with three simultaneous viewers, all clicking, 1600x1200 @ 30fps:

    total CPU   89%  (of 400% across 4 cores)
    temperature 71.5 C      throttling: none

Roughly 3.5 of 4 cores. A fourth viewer would plausibly tip it; the
failure mode is WebRTC quietly scaling resolution down for everyone
rather than anything crashing. Thermal headroom is ~10 C before the Pi
4 throttles around 80-85 C — fine indoors in October, less certain in
an enclosure in July. `monitor.sh` reports throttling explicitly
because the symptom otherwise (frames dropping while CPU looks healthy)
is baffling.

Headroom options if needed before the SFU lands: 1280x960 is ~40% fewer
pixels at full FOV and still a multiple of 64.

## Files

Current:

- `config.py` — all calibration. The file to edit when hardware changes.
- `psc.py` — Parallax PSC-USB serial driver.
- `motion.py` — coordinated, dead-band-quantized motion.
- `control_server.py` + `static/index.html` — click-to-look.
- `install_services.sh` — systemd units.
- `monitor.sh` — CPU, temperature and throttling with peak tracking.
- `deadband.py`, `pan_demo.py`, `smooth_test.py`, `psc_probe.py`,
  `psc_test.py` — calibration and diagnostics.

Superseded, kept for reference:

- `servo_test.py`, `hwservo.py` — the direct-GPIO era, before the PSC.

## Earlier: direct GPIO approach (superseded by the PSC)

Servos driven straight from GPIO12/13, first with gpiozero/lgpio
software PWM and then with `rpi-hardware-pwm` via `dtoverlay=pwm-2chan`.
Abandoned for the PSC-USB: jitter-free pulses, simpler wiring, and it
frees the hat connector.

Its motion model — step a fraction of the remaining distance each tick,
clamped to a speed cap — is the ancestor of what `motion.py` does now,
and the gotchas below are still worth keeping.

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

`start_stream.sh` runs exactly this; the systemd unit calls that script,
so the arguments have one home.

For checking the stream alone, without the control server:
https://tzuhuantai.github.io/webrtc-player/demo/ , adapter on WHEP, URL
`http://<pi-ip>:8080` (bare, no path). That page is https reaching into
an http device, so Chrome prompts for local network access and other
browsers refuse — which is exactly why the viewer page is served from
the Pi instead.

- At 1600x1200 / 30 fps on the Pi 4, one viewer is ~94% of a core-hour
  budget spread over 4 cores; see Measurements for the three-viewer
  figure.
- **Width must be a multiple of 64** or libcamera's stride padding
  trips "Stride is not equal to width". 1640 fails; 1600 works.
- 1640x1232 and 1600x1200 use the full sensor FOV; 1920x1080 is a
  cropped mode with narrower FOV.
- The Pi 5 has no hardware H.264 encoder — do not pass `--hw-accel`
  there. The Pi 4 does.

## Odds and ends worth keeping

- The jitter question is settled: it was the firmware ramp's tick rate
  plus one worn servo, not the pulse source. See "Servo dead bands".
- Servos must have their own supply with ground tied to the Pi. Running
  them off the Pi's 5V rail gets worse once the camera streams.
- A PCA9685 I2C breakout is the fallback if the PSC ever disappoints,
  and the answer if the servo count grows past two.
- Mirroring: `--rotation` handles 0/90/180/270, but there is no hflip in
  pi-webrtc. Viewer-side CSS `scaleX(-1)` or a v4l2loopback virtual
  camera are the options if a true mirror is ever needed.
- `rpicam-jpeg -o test.jpg -t 2000 --rotation 180` is the quickest
  check that the camera itself works, independent of the streaming
  stack. A stream that comes up black looks the same whether the fault
  is the camera or the encoder.
