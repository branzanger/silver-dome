"""
Silver Dome -- Distance Sensor Module
Measures range using HC-SR04 (ultrasonic / GPIO) or VL53L0X (ToF / I2C).
Mounted on the stepper gimbal to produce (bearing, range) polar coordinates.
"""
import logging
import math
import random
import threading
import time

import config

logger = logging.getLogger("silver_dome.distance_sensor")


class DistanceSensor:
    """Distance measurement sensor with automatic hardware detection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sensor_type: str = "simulate"
        self._simulate = config.SIMULATE

        # Hardware handles
        self._gpio = None
        self._tof = None

        # Simulation state
        self._sim_target_m = random.uniform(1.0, 3.0)
        self._sim_drift_dir = 1

        if not self._simulate:
            # Try VL53L0X first (cleaner, more precise)
            if self._init_vl53l0x():
                self._sensor_type = "vl53l0x"
            elif self._init_hcsr04():
                self._sensor_type = "hc-sr04"
            else:
                logger.warning(
                    "No distance sensor hardware found -- falling back to SIMULATE"
                )
                self._simulate = True

        if self._simulate:
            self._sensor_type = "simulate"
            logger.info("Distance sensor running in SIMULATE mode")
        else:
            logger.info("Distance sensor initialized  type=%s", self._sensor_type)

    # ------------------------------------------------------------------
    # Hardware init helpers
    # ------------------------------------------------------------------

    def _init_vl53l0x(self) -> bool:
        """Try to initialise VL53L0X Time-of-Flight sensor on I2C."""
        try:
            import vl53l0x  # type: ignore

            tof = vl53l0x.VL53L0X(i2c_address=0x29)
            tof.open()
            tof.start_ranging(vl53l0x.Vl53l0xAccuracyMode.BETTER)
            self._tof = tof
            logger.info("VL53L0X initialised on I2C addr 0x29")
            return True
        except Exception as exc:
            logger.debug("VL53L0X unavailable: %s", exc)
            return False

    def _init_hcsr04(self) -> bool:
        """Try to initialise HC-SR04 ultrasonic sensor on GPIO."""
        try:
            import RPi.GPIO as GPIO  # type: ignore

            GPIO.setmode(GPIO.BCM)
            GPIO.setup(config.DISTANCE_TRIG_PIN, GPIO.OUT, initial=GPIO.LOW)
            GPIO.setup(config.DISTANCE_ECHO_PIN, GPIO.IN)
            self._gpio = GPIO
            logger.info(
                "HC-SR04 initialised  TRIG=GPIO%d  ECHO=GPIO%d",
                config.DISTANCE_TRIG_PIN,
                config.DISTANCE_ECHO_PIN,
            )
            return True
        except Exception as exc:
            logger.debug("HC-SR04 / RPi.GPIO unavailable: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def measure(self) -> float:
        """Return distance in metres.  Returns -1 on failure."""
        with self._lock:
            try:
                if self._simulate:
                    return self._measure_simulate()
                if self._sensor_type == "vl53l0x":
                    return self._measure_vl53l0x()
                if self._sensor_type == "hc-sr04":
                    return self._measure_hcsr04()
            except Exception:
                logger.exception("Distance measurement error")
            return -1.0

    def is_available(self) -> bool:
        """True when a real hardware sensor was detected."""
        return self._sensor_type in ("hc-sr04", "vl53l0x")

    def get_type(self) -> str:
        """Return sensor type: 'hc-sr04', 'vl53l0x', or 'simulate'."""
        return self._sensor_type

    def status(self) -> dict:
        return {
            "name": "distance",
            "simulate": self._simulate,
            "hardware": self._sensor_type,
        }

    def cleanup(self) -> None:
        """Release hardware resources."""
        if self._tof is not None:
            try:
                self._tof.stop_ranging()
                self._tof.close()
            except Exception:
                pass
            self._tof = None

        if self._gpio is not None:
            try:
                self._gpio.cleanup(config.DISTANCE_TRIG_PIN)
                self._gpio.cleanup(config.DISTANCE_ECHO_PIN)
            except Exception:
                pass
            self._gpio = None

        logger.info("Distance sensor cleaned up")

    # ------------------------------------------------------------------
    # HC-SR04 measurement
    # ------------------------------------------------------------------

    def _measure_hcsr04(self) -> float:
        """Send ultrasonic pulse and time the echo."""
        gpio = self._gpio
        trig = config.DISTANCE_TRIG_PIN
        echo = config.DISTANCE_ECHO_PIN
        timeout = config.DISTANCE_TIMEOUT_S

        # Ensure TRIG is low
        gpio.output(trig, gpio.LOW)
        time.sleep(0.000002)  # 2us settle

        # Send 10us trigger pulse
        gpio.output(trig, gpio.HIGH)
        time.sleep(0.00001)  # 10us
        gpio.output(trig, gpio.LOW)

        # Wait for ECHO to go high (pulse start)
        deadline = time.time() + timeout
        while gpio.input(echo) == 0:
            pulse_start = time.time()
            if pulse_start > deadline:
                logger.debug("HC-SR04 timeout waiting for echo start")
                return -1.0

        # Wait for ECHO to go low (pulse end)
        deadline = time.time() + timeout
        while gpio.input(echo) == 1:
            pulse_end = time.time()
            if pulse_end > deadline:
                logger.debug("HC-SR04 timeout waiting for echo end")
                return -1.0

        pulse_duration = pulse_end - pulse_start  # type: ignore[possibly-undefined]
        distance_m = (pulse_duration * 343.0) / 2.0

        # Clamp to sensor range (2cm - 4m)
        if distance_m < 0.02 or distance_m > config.DISTANCE_MAX_RANGE_M:
            logger.debug("HC-SR04 reading out of range: %.3f m", distance_m)
            return -1.0

        return round(distance_m, 3)

    # ------------------------------------------------------------------
    # VL53L0X measurement
    # ------------------------------------------------------------------

    def _measure_vl53l0x(self) -> float:
        """Read ToF sensor range in mm and convert to metres."""
        range_mm = self._tof.get_distance()
        if range_mm < 0:
            logger.debug("VL53L0X returned negative range")
            return -1.0

        distance_m = range_mm / 1000.0

        if distance_m > 2.0:
            logger.debug("VL53L0X reading out of range: %.3f m", distance_m)
            return -1.0

        return round(distance_m, 3)

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def _measure_simulate(self) -> float:
        """No fake distance readings -- only real hardware produces data."""
        return -1.0


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import importlib

    os.environ.setdefault("SILVER_DOME_SIMULATE", "true")
    importlib.reload(config)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    sensor = DistanceSensor()
    print(f"Sensor type : {sensor.get_type()}")
    print(f"Available   : {sensor.is_available()}")
    print(f"\nTaking 10 readings:\n")

    for i in range(1, 11):
        d = sensor.measure()
        bar = "#" * int(d * 10) if d > 0 else "---"
        print(f"  [{i:>2}]  {d:>6.3f} m  {bar}")
        time.sleep(0.3)

    sensor.cleanup()
    print("\nDone.")
