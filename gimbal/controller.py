"""
Silver Dome — Gimbal Controller
Pan (stepper via A4988), tilt (servo via PWM), and laser control.
Supports simulation mode when RPi.GPIO is unavailable or config.SIMULATE is set.
"""

import logging
import threading
import time

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GPIO abstraction — try to import RPi.GPIO once, but defer simulate decision
# to __init__ time so that --simulate flag is respected.
# ---------------------------------------------------------------------------
_GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO
    # Pi 5 imports and setmode() work, but setup() crashes with
    # "Cannot determine SOC peripheral base address" — probe with a real pin
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(config.LASER_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.cleanup(config.LASER_PIN)
    _GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO = None


class GimbalController:
    """Thread-safe pan/tilt/laser controller for the Silver Dome gimbal."""

    def __init__(self):
        # Determine simulate mode at construction time (after argparse has run)
        self._sim = config.SIMULATE or not _GPIO_AVAILABLE
        if not _GPIO_AVAILABLE and not config.SIMULATE:
            logger.info("RPi.GPIO unavailable — GPIO calls will be logged only")

        self._setup_gpio()

        # Stepper state
        self._step_pos = 0  # current position in steps (0 = home)
        self._steps_per_rev = config.STEPPER_STEPS_PER_REV
        self._deg_per_step = config.STEPPER_DEGREES_PER_STEP
        self._step_delay = config.STEPPER_STEP_DELAY
        self._lock = threading.Lock()

        # Servo PWM handle
        self._tilt_pwm = None
        self._tilt_angle = 90.0
        if not self._sim:
            self._tilt_pwm = GPIO.PWM(config.SERVO_TILT_PIN, config.SERVO_FREQ)
            self._tilt_pwm.start(self._angle_to_duty(self._tilt_angle))

        self._laser_active = False

        logger.info("GimbalController initialised (simulate=%s)", self._sim)

    def _setup_gpio(self):
        """Initialise BCM GPIO pins. No-op in simulation mode."""
        if self._sim:
            logger.info("[SIM] GPIO.setmode(BCM) + pin setup skipped")
            return
        GPIO.setup(config.STEPPER_STEP_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(config.STEPPER_DIR_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(config.STEPPER_ENABLE_PIN, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(config.SERVO_TILT_PIN, GPIO.OUT)
        GPIO.setup(config.LASER_PIN, GPIO.OUT, initial=GPIO.LOW)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _angle_to_duty(angle: float) -> float:
        """Map 0-180 degrees to servo duty cycle range."""
        angle = max(0.0, min(180.0, angle))
        return config.SERVO_MIN_DUTY + (angle / 180.0) * (config.SERVO_MAX_DUTY - config.SERVO_MIN_DUTY)

    def _enable_stepper(self):
        if not self._sim:
            GPIO.output(config.STEPPER_ENABLE_PIN, GPIO.LOW)  # active-low enable

    def _disable_stepper(self):
        if not self._sim:
            GPIO.output(config.STEPPER_ENABLE_PIN, GPIO.HIGH)

    def _step_once(self):
        """Pulse the STEP pin once."""
        if self._sim:
            return
        GPIO.output(config.STEPPER_STEP_PIN, GPIO.HIGH)
        time.sleep(self._step_delay)
        GPIO.output(config.STEPPER_STEP_PIN, GPIO.LOW)
        time.sleep(self._step_delay)

    @property
    def current_bearing(self) -> float:
        """Current pan position in degrees (0-360)."""
        with self._lock:
            return (self._step_pos * self._deg_per_step) % 360.0

    @property
    def current_tilt(self) -> float:
        """Current tilt position in degrees (0-180)."""
        return self._tilt_angle

    # ------------------------------------------------------------------
    # Pan (stepper)
    # ------------------------------------------------------------------
    def pan_to(self, degrees: float):
        """Move to an absolute bearing (0-360 degrees) using the shortest path."""
        degrees = degrees % 360.0
        with self._lock:
            current_deg = (self._step_pos * self._deg_per_step) % 360.0
            delta = degrees - current_deg

            # Shortest-path: keep delta within (-180, 180]
            if delta > 180.0:
                delta -= 360.0
            elif delta <= -180.0:
                delta += 360.0

            steps = int(round(delta / self._deg_per_step))
            if steps == 0:
                logger.debug("pan_to(%.1f): already at target", degrees)
                return

            direction = 1 if steps > 0 else -1
            abs_steps = abs(steps)

            logger.info(
                "pan_to(%.1f): from %.1f, delta=%.1f, steps=%+d",
                degrees, current_deg, delta, steps,
            )

            try:
                if not self._sim:
                    GPIO.output(config.STEPPER_DIR_PIN, GPIO.HIGH if direction > 0 else GPIO.LOW)
                else:
                    logger.info("[SIM] DIR=%s", "CW" if direction > 0 else "CCW")

                self._enable_stepper()
                deadline = time.time() + config.GIMBAL_PAN_TIMEOUT_SEC
                completed = 0
                try:
                    for _ in range(abs_steps):
                        if time.time() > deadline:
                            logger.error(
                                "pan_to TIMEOUT after %d/%d steps (%.1fs limit)",
                                completed, abs_steps, config.GIMBAL_PAN_TIMEOUT_SEC,
                            )
                            break
                        self._step_once()
                        completed += 1
                finally:
                    self._disable_stepper()
                    self._step_pos += direction * completed
            except Exception:
                logger.exception("GPIO error during pan")

        logger.info("pan complete — bearing now %.1f", self.current_bearing)

    def home(self):
        """Return pan to 0 degrees."""
        logger.info("Homing gimbal to 0 degrees")
        self.pan_to(0.0)
        with self._lock:
            self._step_pos = 0  # reset absolute reference
        logger.info("Home position set")

    # ------------------------------------------------------------------
    # Tilt (servo)
    # ------------------------------------------------------------------
    def tilt_to(self, degrees: float):
        """Set the tilt servo to an angle between 0 and 180 degrees."""
        degrees = max(0.0, min(180.0, degrees))
        duty = self._angle_to_duty(degrees)
        try:
            if self._sim:
                logger.info("[SIM] tilt_to(%.1f) duty=%.2f%%", degrees, duty)
            else:
                self._tilt_pwm.ChangeDutyCycle(duty)
        except Exception:
            logger.exception("GPIO error during tilt")
        self._tilt_angle = degrees
        logger.info("tilt set to %.1f degrees", degrees)

    # ------------------------------------------------------------------
    # Laser
    # ------------------------------------------------------------------
    def laser_on(self):
        """Activate the laser designator."""
        try:
            if self._sim:
                logger.info("[SIM] LASER ON")
            else:
                GPIO.output(config.LASER_PIN, GPIO.HIGH)
        except Exception:
            logger.exception("GPIO error activating laser")
        self._laser_active = True

    def laser_off(self):
        """Deactivate the laser designator."""
        try:
            if self._sim:
                logger.info("[SIM] LASER OFF")
            else:
                GPIO.output(config.LASER_PIN, GPIO.LOW)
        except Exception:
            logger.exception("GPIO error deactivating laser")
        self._laser_active = False

    # ------------------------------------------------------------------
    # Combined actions
    # ------------------------------------------------------------------
    def lock_on(self, bearing_deg: float, tilt_deg: float = 90.0):
        """Pan to bearing, tilt to angle, and activate laser."""
        logger.info("LOCK ON — bearing=%.1f tilt=%.1f", bearing_deg, tilt_deg)
        self.pan_to(bearing_deg)
        self.tilt_to(tilt_deg)
        self.laser_on()

    def stand_down(self):
        """Laser off, tilt to neutral (90 degrees)."""
        logger.info("STAND DOWN")
        self.laser_off()
        self.tilt_to(90.0)

    # ------------------------------------------------------------------
    # Health / status
    # ------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "name": "gimbal",
            "simulate": self._sim,
            "bearing": round(self.current_bearing, 1),
            "tilt": round(self._tilt_angle, 1),
            "laser_active": self._laser_active,
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self):
        """Release GPIO resources."""
        try:
            self.laser_off()
            if self._tilt_pwm is not None:
                self._tilt_pwm.stop()
            if not self._sim:
                GPIO.cleanup()
        except Exception:
            logger.exception("Error during GPIO cleanup")
        logger.info("GimbalController cleaned up")


# -----------------------------------------------------------------------
# Demo / self-test
# -----------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    gimbal = GimbalController()

    try:
        # Pan sweep: 0 -> 90 -> 180 -> 270 -> 0
        for bearing in (90, 180, 270, 0):
            gimbal.pan_to(bearing)
            print(f"  bearing: {gimbal.current_bearing:.1f}")
            time.sleep(0.3)

        # Tilt sweep
        for angle in (0, 45, 90, 135, 180, 90):
            gimbal.tilt_to(angle)
            time.sleep(0.2)

        # Lock-on demo
        gimbal.lock_on(bearing_deg=45.0, tilt_deg=60.0)
        print(f"  locked on — bearing={gimbal.current_bearing:.1f}, laser={gimbal._laser_active}")
        time.sleep(1)

        # Stand down
        gimbal.stand_down()
        print(f"  stood down — laser={gimbal._laser_active}")

        # Home
        gimbal.home()
        print(f"  home — bearing={gimbal.current_bearing:.1f}")

    finally:
        gimbal.cleanup()
