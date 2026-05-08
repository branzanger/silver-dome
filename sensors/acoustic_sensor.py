"""
Silver Dome -- Acoustic Drone Detection Sensor
Listens on laptop microphone for drone propeller signatures using FFT.
Drone propellers produce harmonics in the 80-400Hz range with a distinctive
comb-like spectral pattern.

Runs as a thread, pushes SensorEvent objects to the shared queue.
"""
import logging
import math
import threading
import time
from queue import Queue

import numpy as np

import config
from models import SensorEvent

logger = logging.getLogger("silver_dome.acoustic")

# Audio parameters
RATE = 44100          # Sample rate (Hz)
CHUNK = 4096          # Samples per FFT window (~93ms at 44.1kHz)
FORMAT_WIDTH = 2      # 16-bit audio

# Drone detection parameters
DRONE_FREQ_LOW = 100    # Hz — lower bound of drone propeller fundamentals
DRONE_FREQ_HIGH = 350   # Hz — upper bound of drone harmonics
HARMONIC_COUNT = 3      # Number of harmonic peaks to look for
ENERGY_THRESHOLD = 10.0 # Minimum normalized energy in drone band to consider
PEAK_RATIO_THRESHOLD = 8.0  # Drone band energy must be this × ambient energy
DETECTION_COOLDOWN = 2.0    # Min seconds between detection events
SCAN_INTERVAL = 0.1         # Seconds between analysis windows


class AcousticSensor:
    """Microphone-based drone acoustic detection using FFT spectral analysis."""

    def __init__(self):
        self._stream = None
        self._pa = None
        self._running = False
        self._stop_event = threading.Event()
        self._detection_count: int = 0
        self._last_detection_time: float = 0
        self._last_energy: float = 0.0
        self._last_drone_score: float = 0.0

        # Frequency analysis helpers
        self._freqs = np.fft.rfftfreq(CHUNK, d=1.0 / RATE)
        self._drone_mask = (self._freqs >= DRONE_FREQ_LOW) & (self._freqs <= DRONE_FREQ_HIGH)
        self._ambient_mask = (self._freqs >= 500) & (self._freqs <= 2000)

        self._init_audio()

    def _init_audio(self):
        try:
            import pyaudio
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )
            logger.info("Microphone opened: rate=%d chunk=%d", RATE, CHUNK)
        except Exception as exc:
            logger.error("Failed to open microphone: %s", exc)
            self._stream = None

    def run(self, event_queue: Queue) -> None:
        """Main loop -- run as thread target."""
        if self._stream is None:
            logger.error("AcousticSensor cannot run: no microphone")
            return

        self._running = True
        self._stop_event.clear()
        logger.info("AcousticSensor started (drone band: %d-%dHz, threshold: %.2f)",
                     DRONE_FREQ_LOW, DRONE_FREQ_HIGH, PEAK_RATIO_THRESHOLD)

        try:
            while not self._stop_event.is_set():
                try:
                    event = self._analyze_audio()
                    if event is not None:
                        event_queue.put(event)
                        self._detection_count += 1
                        self._last_detection_time = time.time()
                except Exception:
                    logger.exception("Error in acoustic analysis")
                self._stop_event.wait(timeout=SCAN_INTERVAL)
        finally:
            self._running = False
            logger.info("AcousticSensor stopped (total detections: %d)", self._detection_count)

    def _analyze_audio(self) -> SensorEvent | None:
        """Read audio chunk, run FFT, detect drone-like spectral pattern."""
        try:
            raw = self._stream.read(CHUNK, exception_on_overflow=False)
        except Exception:
            return None

        # Convert to numpy
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)

        # Apply Hanning window to reduce spectral leakage
        window = np.hanning(len(samples))
        samples = samples * window

        # FFT
        spectrum = np.abs(np.fft.rfft(samples))
        spectrum = spectrum / (CHUNK / 2)  # Normalize

        # Energy in drone band vs ambient
        drone_energy = np.mean(spectrum[self._drone_mask]) if np.any(self._drone_mask) else 0
        ambient_energy = np.mean(spectrum[self._ambient_mask]) if np.any(self._ambient_mask) else 1e-10

        self._last_energy = float(drone_energy)

        # Peak ratio: how much louder is the drone band vs ambient?
        peak_ratio = drone_energy / max(ambient_energy, 1e-10)

        # Find dominant peaks in drone band for harmonic analysis
        drone_spectrum = spectrum[self._drone_mask]
        drone_freqs = self._freqs[self._drone_mask]

        harmonic_score = 0.0
        if len(drone_spectrum) > 0 and drone_energy > ENERGY_THRESHOLD:
            # Find the top peaks
            peak_indices = np.argsort(drone_spectrum)[-HARMONIC_COUNT:]
            peak_freqs = drone_freqs[peak_indices]
            peak_amps = drone_spectrum[peak_indices]

            if len(peak_freqs) >= 2:
                # Check for harmonic relationship (peaks at f, 2f, 3f)
                fundamental = np.min(peak_freqs[peak_freqs > 0])
                if fundamental > 0:
                    harmonic_hits = 0
                    for pf in peak_freqs:
                        ratio = pf / fundamental
                        # Check if ratio is close to an integer (harmonic)
                        if abs(ratio - round(ratio)) < 0.15:
                            harmonic_hits += 1
                    harmonic_score = harmonic_hits / HARMONIC_COUNT

        # Combined drone score
        drone_score = 0.0
        if peak_ratio >= PEAK_RATIO_THRESHOLD and drone_energy > ENERGY_THRESHOLD:
            # Base score from peak ratio (capped at 1.0)
            ratio_score = min(peak_ratio / (PEAK_RATIO_THRESHOLD * 3), 1.0)
            # Boost for harmonic pattern
            drone_score = ratio_score * 0.7 + harmonic_score * 0.3

        self._last_drone_score = float(drone_score)

        # Emit detection if score is high enough and cooldown elapsed
        if drone_score >= 0.4:
            now = time.time()
            if (now - self._last_detection_time) < DETECTION_COOLDOWN:
                return None

            confidence = min(drone_score, 0.95)

            logger.info(
                "ACOUSTIC DETECTION: score=%.2f  energy=%.3f  ratio=%.1f  harmonic=%.2f",
                drone_score, drone_energy, peak_ratio, harmonic_score,
            )

            return SensorEvent(
                sensor_type="acoustic",
                timestamp=now,
                confidence=round(confidence, 3),
                bearing_hint=-1,  # Mic can't determine direction
                metadata={
                    "drone_score": round(drone_score, 3),
                    "drone_energy": round(drone_energy, 4),
                    "peak_ratio": round(peak_ratio, 2),
                    "harmonic_score": round(harmonic_score, 3),
                    "dominant_freq_hz": round(float(drone_freqs[np.argmax(drone_spectrum)])) if len(drone_spectrum) > 0 else 0,
                },
            )

        return None

    def stop(self):
        self._stop_event.set()

    def cleanup(self):
        self.stop()
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    def status(self) -> dict:
        return {
            "name": "acoustic",
            "running": self._running,
            "microphone": self._stream is not None,
            "detection_count": self._detection_count,
            "last_energy": round(self._last_energy, 4),
            "last_drone_score": round(self._last_drone_score, 3),
        }
