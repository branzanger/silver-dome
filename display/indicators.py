"""
Silver Dome -- LED Status Indicators
Controls three LEDs (green / amber / red) to reflect system threat state.
Falls back to coloured console output when running in simulation mode.
"""

import threading
import time
import sys
import os

# Allow running as a standalone script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config  # noqa: E402

# ---------------------------------------------------------------------------
# GPIO abstraction — try to import RPi.GPIO once, defer simulate decision
# to __init__ so --simulate flag is respected.
# ---------------------------------------------------------------------------
_GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO  # type: ignore
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(config.LED_GREEN_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.cleanup(config.LED_GREEN_PIN)
    _GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO = None  # type: ignore


class StatusIndicator:
    """Thread-safe LED indicator controller with simulation fallback."""

    # Confidence thresholds (mirrors config.py logic)
    _THRESH_DETECTING = 0.60
    _THRESH_TRACKING = 0.85

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._blink_event = threading.Event()  # set = stop blinking
        self._blink_thread: threading.Thread | None = None
        self._state: str = "off"
        self._sim = config.SIMULATE or not _GPIO_AVAILABLE

        self._green = config.LED_GREEN_PIN
        self._amber = config.LED_AMBER_PIN
        self._red = config.LED_RED_PIN

        if not self._sim:
            for pin in (self._green, self._amber, self._red):
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _stop_blink(self) -> None:
        """Signal the blink thread to stop and wait for it."""
        if self._blink_thread is not None and self._blink_thread.is_alive():
            self._blink_event.set()
            self._blink_thread.join(timeout=2.0)
        self._blink_thread = None
        self._blink_event.clear()

    def _write_pin(self, pin: int, state: bool) -> None:
        if not self._sim:
            GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)

    def _all_off(self) -> None:
        for pin in (self._green, self._amber, self._red):
            self._write_pin(pin, False)

    def _log_sim(self, label: str, colour: str) -> None:
        if self._sim:
            print(f"[{colour}] {label}")

    def _blink_loop(self) -> None:
        """Background loop: toggles the red LED every 0.5 s."""
        on = False
        while not self._blink_event.is_set():
            on = not on
            self._write_pin(self._red, on)
            if self._sim:
                tag = "RED   " if on else "      "
                print(f"[{tag}] ALERT {'*' if on else ' '}", flush=True)
            self._blink_event.wait(timeout=0.5)
        # Ensure LED is off when we exit
        self._write_pin(self._red, False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_all_clear(self) -> None:
        with self._lock:
            self._stop_blink()
            self._all_off()
            self._write_pin(self._green, True)
            self._state = "all_clear"
            self._log_sim("ALL CLEAR", "GREEN ")

    def set_detecting(self) -> None:
        with self._lock:
            self._stop_blink()
            self._all_off()
            self._write_pin(self._amber, True)
            self._state = "detecting"
            self._log_sim("DETECTING", "AMBER ")

    def set_tracking(self) -> None:
        with self._lock:
            self._stop_blink()
            self._all_off()
            self._write_pin(self._red, True)
            self._state = "tracking"
            self._log_sim("TRACKING", "RED   ")

    def set_alert(self) -> None:
        with self._lock:
            self._stop_blink()
            self._all_off()
            self._state = "alert"
            self._blink_event.clear()
            self._blink_thread = threading.Thread(
                target=self._blink_loop, daemon=True, name="led-blink"
            )
            self._blink_thread.start()

    def update_from_confidence(self, confidence: float) -> None:
        """Automatically choose state based on confidence score. Skips if already in target state."""
        if confidence >= self._THRESH_TRACKING:
            if self._state != "tracking":
                self.set_tracking()
        elif confidence >= self._THRESH_DETECTING:
            if self._state != "detecting":
                self.set_detecting()
        else:
            if self._state != "all_clear":
                self.set_all_clear()

    @property
    def state(self) -> str:
        return self._state

    def status(self) -> dict:
        return {
            "name": "indicators",
            "simulate": self._sim,
            "state": self._state,
        }

    def cleanup(self) -> None:
        """Turn off all LEDs and release GPIO resources."""
        with self._lock:
            self._stop_blink()
            self._all_off()
            self._state = "off"
        if not self._sim:
            GPIO.cleanup([self._green, self._amber, self._red])


# ----------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------
if __name__ == "__main__":
    indicator = StatusIndicator()

    try:
        print("=== Silver Dome LED Demo ===\n")

        print(">> ALL CLEAR")
        indicator.set_all_clear()
        time.sleep(2)

        print("\n>> DETECTING")
        indicator.set_detecting()
        time.sleep(2)

        print("\n>> TRACKING")
        indicator.set_tracking()
        time.sleep(2)

        print("\n>> ALERT (blinking for 3 s)")
        indicator.set_alert()
        time.sleep(3)

        print("\n>> Confidence sweep")
        for conf in (0.10, 0.40, 0.60, 0.75, 0.85, 0.95):
            print(f"   confidence={conf:.2f}")
            indicator.update_from_confidence(conf)
            time.sleep(1.5)

        print("\n>> Cleanup")
        indicator.cleanup()
        print("Done.")

    except KeyboardInterrupt:
        indicator.cleanup()
        print("\nInterrupted — cleaned up.")
