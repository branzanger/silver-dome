"""
Silver Dome -- RF Sensor Module (RTL-SDR)
Captures IQ samples, runs FFT, detects power spikes above calibrated noise floor.
"""
import logging
import queue
import random
import threading
import time

import numpy as np

import config
from models import SensorEvent

logger = logging.getLogger("silver_dome.rf_sensor")

# ---------------------------------------------------------------------------
# Noise floor calibration
# ---------------------------------------------------------------------------
_CALIBRATION_SCANS = 10  # number of initial scans used to establish baseline


class RFSensor:
    """Threaded RTL-SDR RF detection sensor."""

    def __init__(self):
        self._running = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._sdr = None
        self._simulate = config.SIMULATE
        self._noise_floor_db: float | None = None
        self._last_event_time: float = 0.0

        # Try to open the SDR device eagerly so we know up-front if hardware
        # is present.  If not, fall back to simulation.
        if not self._simulate:
            try:
                from rtlsdr import RtlSdr  # type: ignore

                sdr = RtlSdr()
                sdr.sample_rate = config.RF_SAMPLE_RATE
                sdr.center_freq = config.RF_CENTER_FREQ
                sdr.gain = config.RF_GAIN
                self._sdr = sdr
                logger.info(
                    "RTL-SDR opened  center=%.3f MHz  rate=%.1f MS/s  gain=%d dB",
                    config.RF_CENTER_FREQ / 1e6,
                    config.RF_SAMPLE_RATE / 1e6,
                    config.RF_GAIN,
                )
            except Exception as exc:
                logger.warning(
                    "RTL-SDR unavailable (%s) -- falling back to SIMULATE mode", exc
                )
                self._simulate = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, event_queue: queue.Queue) -> None:
        """Main loop -- call from a dedicated thread."""
        with self._lock:
            self._running = True

        logger.info(
            "RF sensor starting  (simulate=%s)", self._simulate
        )

        try:
            if self._simulate:
                self._run_simulate(event_queue)
            else:
                self._run_hardware(event_queue)
        except Exception:
            logger.exception("RF sensor fatal error")
        finally:
            self._cleanup()
            logger.info("RF sensor stopped")

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

    def _capture_psd(self) -> np.ndarray:
        """Capture IQ samples and return power spectral density in dB."""
        samples = self._sdr.read_samples(config.RF_NUM_SAMPLES)
        # Windowed FFT
        window = np.hanning(len(samples))
        fft_vals = np.fft.fftshift(np.fft.fft(samples * window))
        psd = 10.0 * np.log10(np.abs(fft_vals) ** 2 + 1e-20)
        return psd

    def _calibrate_noise_floor(self) -> float:
        """Average several PSD scans to establish baseline noise floor."""
        logger.info("Calibrating RF noise floor (%d scans)...", _CALIBRATION_SCANS)
        accumulator = np.zeros(config.RF_NUM_SAMPLES)
        for i in range(_CALIBRATION_SCANS):
            psd = self._capture_psd()
            accumulator += psd
            time.sleep(0.05)
        avg_psd = accumulator / _CALIBRATION_SCANS
        noise_floor = float(np.median(avg_psd))
        logger.info("Noise floor calibrated: %.2f dB", noise_floor)
        return noise_floor

    def _run_hardware(self, event_queue: queue.Queue) -> None:
        self._noise_floor_db = self._calibrate_noise_floor()

        while self.running:
            try:
                psd = self._capture_psd()
                peak_db = float(np.max(psd))
                spike = peak_db - self._noise_floor_db

                if spike >= config.RF_THRESHOLD_DB:
                    peak_idx = int(np.argmax(psd))
                    freq_offset = (
                        (peak_idx - len(psd) / 2)
                        / len(psd)
                        * config.RF_SAMPLE_RATE
                    )
                    detected_freq = config.RF_CENTER_FREQ + freq_offset

                    evt = SensorEvent(
                        sensor_type="rf",
                        timestamp=time.time(),
                        confidence=min(spike / (config.RF_THRESHOLD_DB * 3), 1.0),
                        metadata={
                            "signal_strength_db": round(spike, 2),
                            "frequency_mhz": round(detected_freq / 1e6, 3),
                            "peak_db": round(peak_db, 2),
                            "noise_floor_db": round(self._noise_floor_db, 2),
                        },
                    )
                    event_queue.put(evt)
                    self._last_event_time = time.time()
                    logger.info(
                        "RF detection  +%.1f dB @ %.3f MHz  conf=%.2f",
                        spike,
                        detected_freq / 1e6,
                        evt.confidence,
                    )

            except Exception:
                logger.exception("Error during RF scan")

            if self._stop_event.wait(timeout=config.RF_SCAN_INTERVAL):
                break

    # ------------------------------------------------------------------
    # Simulation path
    # ------------------------------------------------------------------

    def _run_simulate(self, event_queue: queue.Queue) -> None:
        self._noise_floor_db = -40.0
        logger.info("RF sensor running in SIMULATE mode (noise floor=%.1f dB)", self._noise_floor_db)

        while self.running:
            try:
                # Random detection every 3-8 seconds — use Event for fast stop
                if self._stop_event.wait(timeout=random.uniform(3.0, 8.0)):
                    break

                spike = random.uniform(
                    config.RF_THRESHOLD_DB, config.RF_THRESHOLD_DB * 2.5
                )
                freq = random.choice(config.RF_FREQUENCIES)

                evt = SensorEvent(
                    sensor_type="rf",
                    timestamp=time.time(),
                    confidence=min(spike / (config.RF_THRESHOLD_DB * 3), 1.0),
                    metadata={
                        "signal_strength_db": round(spike, 2),
                        "frequency_mhz": round(freq / 1e6, 3),
                        "peak_db": round(self._noise_floor_db + spike, 2),
                        "noise_floor_db": round(self._noise_floor_db, 2),
                        "simulated": True,
                    },
                )
                event_queue.put(evt)
                self._last_event_time = time.time()
                logger.info(
                    "[SIM] RF detection  +%.1f dB @ %.3f MHz  conf=%.2f",
                    spike,
                    freq / 1e6,
                    evt.confidence,
                )
            except Exception:
                logger.exception("Error in RF simulate loop")

    # ------------------------------------------------------------------
    # Health / status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "name": "rf",
            "running": self.running,
            "simulate": self._simulate,
            "last_event": self._last_event_time if self._last_event_time > 0 else None,
            "noise_floor_db": self._noise_floor_db,
            "hardware": "rtl-sdr" if self._sdr is not None else "none",
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup(self) -> None:
        if self._sdr is not None:
            try:
                self._sdr.close()
            except Exception:
                pass
            self._sdr = None


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys

    # Force simulate if no hardware
    os.environ.setdefault("SILVER_DOME_SIMULATE", "true")
    # Re-import config to pick up env change
    import importlib
    importlib.reload(config)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    q: queue.Queue = queue.Queue()
    sensor = RFSensor()

    t = threading.Thread(target=sensor.run, args=(q,), daemon=True)
    t.start()

    print("RF sensor test running -- press Ctrl+C to stop\n")
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
