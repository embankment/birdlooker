"""Hardware-PWM servo driver, API-compatible with gpiozero's AngularServo.

Requires in /boot/firmware/config.txt, then a reboot:

    Pi 4:  dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
    Pi 5:  dtoverlay=pwm-2chan

Both map GPIO12 -> PWM channel 0 and GPIO13 -> PWM channel 1.

    sudo pip3 install rpi-hardware-pwm --break-system-packages
"""

from rpi_hardware_pwm import HardwarePWM

# GPIO -> hardware PWM channel. Same on Pi 4 (with the pin= overlay
# parameters above) and Pi 5.
PIN_TO_CHANNEL = {12: 0, 13: 1}

SERVO_HZ = 50  # 20 ms frame - standard analog servo


class HardwareServo:
    """Drop-in replacement for gpiozero.AngularServo on GPIO12/13.

    Exposes .angle, .detach() and .close() so calling code does not
    need to care which backend is driving the servo.
    """

    def __init__(self, pin, min_angle=-90, max_angle=90,
                 min_pulse_width=0.0005, max_pulse_width=0.0025,
                 chip=0, hz=SERVO_HZ):
        if pin not in PIN_TO_CHANNEL:
            raise ValueError(
                f"GPIO{pin} has no hardware PWM channel; "
                f"use one of {sorted(PIN_TO_CHANNEL)}"
            )
        self.pin = pin
        self.min_angle = min_angle
        self.max_angle = max_angle
        self.min_pulse = min_pulse_width
        self.max_pulse = max_pulse_width
        self.hz = hz

        self._pwm = HardwarePWM(
            pwm_channel=PIN_TO_CHANNEL[pin], hz=hz, chip=chip
        )
        self._angle = None
        self._running = False

    def _duty_for_angle(self, angle):
        """Map an angle to a duty cycle percentage."""
        angle = max(self.min_angle, min(self.max_angle, angle))
        frac = (angle - self.min_angle) / (self.max_angle - self.min_angle)
        pulse = self.min_pulse + frac * (self.max_pulse - self.min_pulse)
        return pulse * self.hz * 100.0

    @property
    def angle(self):
        return self._angle

    @angle.setter
    def angle(self, value):
        if value is None:
            self.detach()
            return
        duty = self._duty_for_angle(value)
        if not self._running:
            self._pwm.start(duty)
            self._running = True
        else:
            self._pwm.change_duty_cycle(duty)
        self._angle = value

    def detach(self):
        """Stop pulsing. The servo goes limp and stops buzzing."""
        if self._running:
            self._pwm.stop()
            self._running = False
        self._angle = None

    def close(self):
        self.detach()
