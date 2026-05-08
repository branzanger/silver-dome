"""
Silver Dome -- PIR Motion Sensor Module (GPIO)
Reads a PIR sensor on a GPIO pin with debouncing.
"""
import logging
import queue
import random
import threading
import time

import config
from models import SensorEvent

logger = logging.getLogger("silver_dome.pir_sensor")


class PIRSensor:
    """Threaded PIR motion-detection sensor."""

    def __init__(self):
        self._running = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._simulate = config.SIMULATE
        self._gpio = None
        self._last_event_time: float = 0.0

        if not self._simulate:
            try:
                import RPi.GPIO as GPIO  # type: ignore

                GPIO.setmode(GPIO.BCM)
                GPIO.setup(config.PIR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
                self._gpio = GPIO
                logger.info("PIR sensor initialized on GPIO %d (BCM)", config.PIR_PIN)
            except Exception as exc:
                logger.warning(
                    "RPi.GPIO unavailable (%s) -- falling back to SIMULATE mode", exc
                )
                self._simulate = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, event_queue: queue.Queue) -> None:
        """Main loop -- call from a dedicated thread."""
        with self._lock:
            self._running = True

        logger.info("PIR sensor starting  (simulate=%s)", self._simulate)

        try:
            if self._simulate:
                self._run_simulate(event_queue)
            else:
                self._run_hardware(event_queue)
        except Exception:
            logger.exception("PIR sensor fatal error")
        finally:
            self._cleanup()
            logger.info("PIR sensor stopped")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._stop_event.set()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    # ------------------------------------------------------------------
    # Hardware path
    # ------------------------------------------------------------------

    def _run_hardware(self, event_queue: queue.Queue) -> None:
        last_trigger_time = 0.0
        debounce_sec = config.PIR_DEBOUNCE_MS / 1000.0

        while self.running:
            try:
                pin_state = self._gpio.input(config.PIR_PIN)

                if pin_state == 1:
                    now = time.time()
                    if (now - last_trigger_time) >= debounce_sec:
                        last_trigger_time = now
                        evt = SensorEvent(
                            sensor_type="pir",
                            timestamp=now,
                            confidence=1.0,
                            metadata={"pin_state": 1},
                        )
                        event_queue.put(evt)
                        self._last_event_time = time.time()
                        logger.info("PIR triggered on GPIO %d", config.PIR_PIN)

            except Exception:
                logger.exception("Error reading PIR GPIO")

            time.sleep(config.PIR_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Simulation path
    # ------------------------------------------------------------------

    def _run_simulate(self, event_queue: queue.Queue) -> None:
        logger.info("PIR sensor in SIMULATE mode -- no fake events (real-only mode)")
        # Do nothing: wait until stopped so only real hardware produces events
        self._stop_event.wait()

    # ------------------------------------------------------------------
    # Health / status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "name": "pir",
            "running": self.running,
            "simulate": self._simulate,
            "last_event": self._last_event_time if self._last_event_time > 0 else None,
            "hardware": "gpio" if self._gpio is not None else "none",
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup(self) -> None:
        if self._gpio is not None:
            try:
                self._gpio.cleanup(config.PIR_PIN)
            except Exception:
                pass
            self._gpio = None


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

    q: queue.Queue = queue.Queue()
    sensor = PIRSensor()

    t = threading.Thread(target=sensor.run, args=(q,), daemon=True)
    t.start()

    print("PIR sensor test running -- press Ctrl+C to stop\n")
    try:
        while True:
            try:
                evt = q.get(timeout=1.0)
                print(f"  EVENT: {evt}")
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        sensor.stop()
        t.join(timeout=3)
        print("\nStopped.")
