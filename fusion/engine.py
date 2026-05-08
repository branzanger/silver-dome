"""
Silver Dome -- Sensor Fusion Engine
Consumes SensorEvents from a shared queue, correlates across modalities
(RF, PIR, Visual), computes fused confidence, and triggers gimbal/laser/Foundry actions.
"""

import logging
import queue
import threading
import time
import config
from models import SensorEvent, ThreatObject, ThreatTrack

logger = logging.getLogger("silver_dome.fusion")

# ---------------------------------------------------------------------------
# Confidence lookup for sensor combinations
# Keys are frozensets of active sensor types.
# ---------------------------------------------------------------------------
_CONFIDENCE_TABLE: dict[frozenset[str], float] = {
    frozenset():                                    0.0,
    frozenset({"rf"}):                              config.CONFIDENCE_RF_ONLY,
    frozenset({"pir"}):                             config.CONFIDENCE_PIR_ONLY,
    frozenset({"acoustic"}):                        config.CONFIDENCE_ACOUSTIC_ONLY,
    frozenset({"rf", "pir"}):                       config.CONFIDENCE_RF_PIR,
    frozenset({"rf", "pir", "visual"}):             config.CONFIDENCE_RF_PIR_VISUAL,
    frozenset({"rf", "visual"}):                    config.CONFIDENCE_RF_VISUAL,
    frozenset({"pir", "visual"}):                   config.CONFIDENCE_PIR_VISUAL,
    frozenset({"visual"}):                          0.85,
    frozenset({"acoustic", "visual"}):              config.CONFIDENCE_ACOUSTIC_VISUAL,
    frozenset({"acoustic", "rf"}):                  config.CONFIDENCE_ACOUSTIC_RF,
    frozenset({"acoustic", "rf", "visual"}):        config.CONFIDENCE_ACOUSTIC_RF_VISUAL,
    frozenset({"acoustic", "pir", "visual"}):       config.CONFIDENCE_ACOUSTIC_PIR_VISUAL,
    frozenset({"acoustic", "rf", "pir", "visual"}): config.CONFIDENCE_ACOUSTIC_RF_PIR_VISUAL,
}

# Deduplication window for Foundry writes (seconds)
_DEDUP_WINDOW_SEC = 10.0


class _BearingSmoother:
    """Exponential smoothing for bearing values with 360-degree wraparound."""

    def __init__(self, alpha: float = 0.3):
        self._alpha = alpha
        self._value: float | None = None

    def update(self, raw: float) -> float:
        if self._value is None:
            self._value = raw
            return raw
        delta = raw - self._value
        if delta > 180.0:
            delta -= 360.0
        elif delta < -180.0:
            delta += 360.0
        self._value = (self._value + self._alpha * delta) % 360.0
        return self._value

    def reset(self):
        self._value = None


class FusionEngine:
    """
    Sensor-fusion brain for Silver Dome.

    Runs in its own thread.  Drains a shared event queue, maintains a sliding
    time window of recent SensorEvents, computes a fused confidence score, and
    drives the gimbal, laser, LEDs, and Foundry writes.
    """

    def __init__(
        self,
        event_queue: queue.Queue,
        gimbal_controller,
        visual_sensor,
        distance_sensor=None,
        status_indicator=None,
        foundry_client=None,
        dashboard=None,
    ) -> None:
        self._event_queue = event_queue
        self._gimbal = gimbal_controller
        self._visual = visual_sensor
        self._distance = distance_sensor
        self._status = status_indicator
        self._foundry = foundry_client
        self._dashboard = dashboard

        # Latest distance reading (meters), updated during tracking
        self._current_range_m: float = -1.0

        # Sliding window of recent events
        self._window: list[SensorEvent] = []
        self._window_lock = threading.Lock()

        # Thread control
        self._running = False
        self._stop_event = threading.Event()

        # Deduplication: timestamp of last Foundry write
        self._last_foundry_write: float = 0.0

        # Timestamp of most recent event (any type) -- used for ALL CLEAR
        self._last_event_time: float = 0.0

        # Previous fused confidence (for state-change logging)
        self._prev_confidence: float = 0.0

        # Track whether we've already issued ALL CLEAR to avoid spam
        self._is_clear: bool = True

        # Track whether laser is currently locked to avoid repeated lock_on calls
        self._laser_locked: bool = False

        # Track last commanded bearing to avoid redundant pan commands
        self._last_bearing: float = -1.0
        self._bearing_smoother = _BearingSmoother(alpha=config.BEARING_FILTER_ALPHA)

        # Recent threat log for dashboard (max 20 entries)
        self._threat_log: list[dict] = []

        # Radar sweep history: list of (bearing, range, timestamp) tuples, max 50
        self._radar_history: list[tuple[float, float, float]] = []

        # Rolling RF signal strength history for sparkline (max 30 entries)
        self._rf_signal_history: list[float] = []

        # Active threat tracks (clusters of events by bearing)
        self._tracks: list[ThreatTrack] = []

    # ------------------------------------------------------------------
    # Health / status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        with self._window_lock:
            window_size = len(self._window)
        return {
            "name": "fusion",
            "running": self._running,
            "confidence": round(self._prev_confidence, 3),
            "active_sensors": sorted(self._active_sensor_types()),
            "window_events": window_size,
            "laser_locked": self._laser_locked,
            "is_clear": self._is_clear,
        }

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main loop -- designed to be the target of a threading.Thread."""
        self._running = True
        self._stop_event.clear()
        logger.info("Fusion engine started  (window=%.1fs  interval=%.2fs)",
                     config.FUSION_WINDOW_SEC, config.FUSION_PROCESS_INTERVAL)

        try:
            while not self._stop_event.is_set():
                try:
                    self._tick()
                except Exception:
                    logger.exception("Error in fusion tick -- continuing")
                self._stop_event.wait(timeout=config.FUSION_PROCESS_INTERVAL)
        finally:
            self._running = False
            logger.info("Fusion engine stopped")

    def stop(self) -> None:
        """Signal the engine to shut down (thread-safe)."""
        self._stop_event.set()
        logger.info("Fusion engine stop requested")

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """One iteration of the fusion loop."""

        # 1. Drain new events from the queue into the window
        self._drain_queue()

        # 2. Expire old events
        self._expire_window()

        # 2b. Cluster into threat tracks
        self._tracks = self._cluster_events()

        # 3. Determine active sensor modalities
        active_types = self._active_sensor_types()

        # 4. Compute fused confidence
        confidence = _CONFIDENCE_TABLE.get(frozenset(active_types), 0.0)
        logger.debug("Active sensors: %s  -> confidence=%.2f", active_types, confidence)

        # Log state transitions
        if confidence != self._prev_confidence:
            logger.info("Fused confidence changed: %.2f -> %.2f  sensors=%s",
                        self._prev_confidence, confidence, active_types)
            self._prev_confidence = confidence

        # 5. Actions based on confidence
        self._act(confidence, active_types)

        # 6. Push state to dashboard
        self._push_dashboard(confidence, active_types)

    # ------------------------------------------------------------------
    # Queue / window management
    # ------------------------------------------------------------------

    def _drain_queue(self) -> None:
        """Move all pending events from the queue into the sliding window."""
        count = 0
        while True:
            try:
                evt: SensorEvent = self._event_queue.get_nowait()
                with self._window_lock:
                    self._window.append(evt)
                self._last_event_time = max(self._last_event_time, evt.timestamp)
                if evt.sensor_type == "rf" and "signal_strength_db" in evt.metadata:
                    self._rf_signal_history.append(evt.metadata["signal_strength_db"])
                    self._rf_signal_history = self._rf_signal_history[-30:]
                count += 1
                logger.debug("Ingested %s event  ts=%.3f  conf=%.2f",
                             evt.sensor_type, evt.timestamp, evt.confidence)
            except queue.Empty:
                break
        if count:
            logger.debug("Drained %d events from queue", count)

    def _expire_window(self) -> None:
        """Remove events older than FUSION_WINDOW_SEC."""
        cutoff = time.time() - config.FUSION_WINDOW_SEC
        with self._window_lock:
            before = len(self._window)
            self._window = [e for e in self._window if e.timestamp >= cutoff]
            expired = before - len(self._window)
        if expired:
            logger.debug("Expired %d events from window", expired)

    def _active_sensor_types(self) -> set[str]:
        """Return set of sensor types present in current window."""
        with self._window_lock:
            return {e.sensor_type for e in self._window}

    def _cluster_events(self) -> list[ThreatTrack]:
        """Cluster current window events into bearing-based threat tracks."""
        tracks: list[ThreatTrack] = []
        bearingless_events: list[SensorEvent] = []

        with self._window_lock:
            for evt in self._window:
                bearing = evt.bearing_hint
                if bearing < 0:
                    # Events without bearing (e.g. PIR) — assign later
                    bearingless_events.append(evt)
                    continue

                # Find existing track within ±15 degrees
                matched = None
                for track in tracks:
                    delta = abs(bearing - track.bearing)
                    angular_diff = min(delta, 360.0 - delta)
                    if angular_diff <= 15.0:
                        matched = track
                        break

                if matched:
                    matched.events.append(evt)
                    matched.active_sensors.add(evt.sensor_type)
                    matched.last_update = max(matched.last_update, evt.timestamp)
                    # Update bearing as weighted average toward latest
                    matched.bearing = bearing
                else:
                    tracks.append(ThreatTrack(
                        bearing=bearing,
                        events=[evt],
                        active_sensors={evt.sensor_type},
                        last_update=evt.timestamp,
                    ))

        # Assign bearingless events to the most recent track, or create a standalone
        if bearingless_events:
            if tracks:
                # Assign to the track with the closest last_update time
                best = max(tracks, key=lambda t: t.last_update)
                for evt in bearingless_events:
                    best.events.append(evt)
                    best.active_sensors.add(evt.sensor_type)
                    best.last_update = max(best.last_update, evt.timestamp)
            else:
                # No bearing-based tracks exist; create one at bearing 0
                t = ThreatTrack(
                    bearing=0.0,
                    events=bearingless_events,
                    active_sensors={e.sensor_type for e in bearingless_events},
                    last_update=max(e.timestamp for e in bearingless_events),
                )
                tracks.append(t)

        # Compute confidence per track
        for track in tracks:
            sensor_types = frozenset(track.active_sensors)
            track.confidence = _CONFIDENCE_TABLE.get(sensor_types, 0.0)

        # Sort by confidence descending (primary = highest)
        tracks.sort(key=lambda t: t.confidence, reverse=True)
        return tracks

    # ------------------------------------------------------------------
    # Action logic
    # ------------------------------------------------------------------

    def _act(self, confidence: float, active_types: set[str]) -> None:
        """Execute actions based on the current fused confidence."""

        # a. Always update LED status
        try:
            self._status.update_from_confidence(confidence)
        except Exception:
            logger.exception("Failed to update status indicator")

        # b. If confidence >= GIMBAL_SCAN_THRESHOLD and no visual yet, trigger scan
        if confidence >= config.GIMBAL_SCAN_THRESHOLD and "visual" not in active_types:
            self._trigger_visual_scan()
            # Re-evaluate after potential visual addition
            active_types = self._active_sensor_types()
            confidence = _CONFIDENCE_TABLE.get(frozenset(active_types), confidence)
            if confidence != self._prev_confidence:
                logger.info("Post-visual re-evaluation: confidence=%.2f  sensors=%s",
                            confidence, active_types)
                self._prev_confidence = confidence
                # Update LEDs with new confidence
                try:
                    self._status.update_from_confidence(confidence)
                except Exception:
                    logger.exception("Failed to update status indicator after visual")

        # c. Gimbal pan to bearing (only when bearing changes significantly)
        if confidence >= config.GIMBAL_SCAN_THRESHOLD:
            bearing = self._bearing_smoother.update(self._compute_bearing())
            # Only pan if bearing changed by more than stepper resolution (1.8 deg)
            # Use angular difference that handles 360° wrap-around
            delta = abs(bearing - self._last_bearing)
            angular_diff = min(delta, 360.0 - delta)
            if angular_diff > 2.0 or self._last_bearing < 0:
                logger.debug("Commanding gimbal to bearing=%.1f deg", bearing)
                try:
                    self._gimbal.pan_to(bearing)
                    self._last_bearing = bearing
                except Exception:
                    logger.exception("Failed to command gimbal pan")

        # c2. Measure distance when tracking
        if confidence >= config.GIMBAL_SCAN_THRESHOLD and self._distance is not None:
            try:
                range_m = self._distance.measure()
                if range_m > 0:
                    self._current_range_m = range_m
                    logger.info("Distance: %.2f m at bearing %.1f",
                                range_m, self._last_bearing)
                    # Record to radar history
                    now = time.time()
                    self._radar_history.append((self._last_bearing, range_m, now))
                    # Cap at 50 entries and expire older than 30s
                    self._radar_history = [
                        (b, r, t) for b, r, t in self._radar_history
                        if now - t < 30
                    ][-50:]
            except Exception:
                logger.exception("Distance measurement failed")

        # d. Laser lock (only fire once per engagement)
        if confidence >= config.LASER_LOCK_THRESHOLD:
            if not self._laser_locked:
                logger.info("LASER LOCK -- confidence=%.2f", confidence)
                try:
                    bearing = self._bearing_smoother.update(self._compute_bearing())
                    self._gimbal.lock_on(bearing_deg=bearing)
                    self._laser_locked = True
                except Exception:
                    logger.exception("Failed to engage laser lock")
        elif self._laser_locked:
            # Confidence dropped below laser threshold — disengage
            logger.info("LASER DISENGAGE -- confidence=%.2f", confidence)
            try:
                self._gimbal.laser_off()
            except Exception:
                logger.exception("Failed to disengage laser")
            self._laser_locked = False

        # e. Foundry write (with deduplication)
        if confidence >= config.FOUNDRY_WRITE_THRESHOLD:
            self._maybe_write_foundry(confidence, active_types)

        # f. ALL CLEAR when no events and enough time elapsed (only transition once)
        if not active_types and confidence == 0.0:
            if not self._is_clear:
                elapsed = time.time() - self._last_event_time if self._last_event_time else float("inf")
                if elapsed >= config.CLEAR_TIMEOUT_SEC:
                    logger.info("No events for %.1fs -- ALL CLEAR", elapsed)
                    try:
                        self._status.set_all_clear()
                        self._gimbal.stand_down()
                    except Exception:
                        logger.exception("Failed to set ALL CLEAR / stand_down")
                    self._is_clear = True
                    self._last_bearing = -1.0
                    self._bearing_smoother.reset()
                    self._current_range_m = -1.0
        elif confidence > 0.0:
            self._is_clear = False

    # ------------------------------------------------------------------
    # Visual scan trigger
    # ------------------------------------------------------------------

    def _trigger_visual_scan(self) -> None:
        """Ask the visual sensor to do a scan; if it detects, inject event."""
        logger.debug("Triggering visual sensor scan")
        try:
            result = self._visual.scan()
            if result is not None:
                # result is expected to be a SensorEvent or similar
                if isinstance(result, SensorEvent):
                    evt = result
                else:
                    # Adapt from dict / generic return
                    evt = SensorEvent(
                        sensor_type="visual",
                        timestamp=time.time(),
                        confidence=result.get("confidence", 0.5) if isinstance(result, dict) else 0.5,
                        bearing_hint=result.get("bearing_hint", -1) if isinstance(result, dict) else -1,
                        metadata=result if isinstance(result, dict) else {},
                    )
                with self._window_lock:
                    self._window.append(evt)
                self._last_event_time = max(self._last_event_time, evt.timestamp)
                logger.info("Visual scan returned detection: conf=%.2f bearing=%.1f",
                            evt.confidence, evt.bearing_hint)
        except Exception:
            logger.exception("Visual scan failed")

    # ------------------------------------------------------------------
    # Bearing computation
    # ------------------------------------------------------------------

    def _compute_bearing(self) -> float:
        """
        Determine the best bearing estimate from available sensor hints.

        Visual sensor returns a camera-relative offset (degrees from frame center).
        We convert to absolute bearing ONCE using the gimbal position at scan time,
        then cache it on the event to avoid re-applying the offset on subsequent ticks.

        Priority:
          1. Visual bearing (most accurate, pixel-derived)
          2. RF bearing_hint
          3. Current gimbal position (hold position / scan mode)
        """
        visual_bearing = None
        rf_bearing = None

        with self._window_lock:
            for evt in reversed(self._window):  # most recent first
                if evt.sensor_type == "visual" and evt.bearing_hint != -1:
                    # Check if we already computed absolute bearing for this event
                    if "absolute_bearing" in evt.metadata:
                        visual_bearing = evt.metadata["absolute_bearing"]
                    else:
                        # First time seeing this visual event — compute absolute bearing
                        # bearing_hint is offset from camera center at time of scan
                        current_pan = self._gimbal.current_bearing
                        visual_bearing = (current_pan + evt.bearing_hint) % 360.0
                        evt.metadata["absolute_bearing"] = visual_bearing
                        logger.debug("Computed absolute bearing: offset %.1f + gimbal %.1f = %.1f",
                                     evt.bearing_hint, current_pan, visual_bearing)
                    break
            for evt in reversed(self._window):
                if evt.sensor_type == "rf" and evt.bearing_hint != -1:
                    rf_bearing = evt.bearing_hint
                    break

        if visual_bearing is not None:
            logger.debug("Bearing source: visual (%.1f deg)", visual_bearing)
            return visual_bearing
        if rf_bearing is not None:
            logger.debug("Bearing source: RF (%.1f deg)", rf_bearing)
            return rf_bearing

        # Hold current position
        current = self._gimbal.current_bearing
        logger.debug("Bearing source: current gimbal position (%.1f deg)", current)
        return current

    # ------------------------------------------------------------------
    # Foundry write (deduplicated)
    # ------------------------------------------------------------------

    def _maybe_write_foundry(self, confidence: float, active_types: set[str]) -> None:
        """Build a ThreatObject and write to Foundry if dedup window has passed."""
        now = time.time()
        if (now - self._last_foundry_write) < _DEDUP_WINDOW_SEC:
            logger.debug("Foundry write skipped -- dedup window (%.1fs remaining)",
                         _DEDUP_WINDOW_SEC - (now - self._last_foundry_write))
            return

        bearing = self._compute_bearing()
        threat = ThreatObject(
            confidence=confidence,
            bearing_deg=bearing,
            range_m=self._current_range_m,
            active_sensors=sorted(active_types),
            threat_level=ThreatObject.level_from_confidence(confidence),
            site_id=config.SITE_ID,
            status="TRACKING" if confidence >= config.LASER_LOCK_THRESHOLD else "ACTIVE",
            metadata=self._collect_metadata(),
        )

        try:
            self._foundry.write_threat(threat)
            self._last_foundry_write = now
            logger.info("Foundry write: %s  conf=%.2f  level=%s  bearing=%.1f",
                        threat.threat_id, confidence, threat.threat_level, bearing)
            # Add to dashboard threat log
            self._threat_log.insert(0, {
                "id": threat.threat_id[:8],
                "timestamp": threat.timestamp,
                "confidence": confidence,
                "bearing": bearing,
                "range": self._current_range_m,
                "sensors": sorted(active_types),
                "level": threat.threat_level,
            })
            self._threat_log = self._threat_log[:20]  # keep last 20
        except Exception:
            logger.exception("Foundry write failed for threat %s", threat.threat_id)

    def _collect_metadata(self) -> dict:
        """Gather representative metadata from the current window for the threat."""
        meta: dict = {}
        with self._window_lock:
            for evt in self._window:
                key = f"{evt.sensor_type}_metadata"
                if key not in meta:
                    meta[key] = evt.metadata
        return meta

    # ------------------------------------------------------------------
    # Dashboard push
    # ------------------------------------------------------------------

    def _get_sensor_confidence(self) -> dict:
        """Extract the latest confidence value per sensor type from the window."""
        result = {}
        with self._window_lock:
            for evt in reversed(self._window):
                if evt.sensor_type not in result:
                    result[evt.sensor_type] = round(evt.confidence, 3)
        return result

    def _push_dashboard(self, confidence: float, active_types: set[str]) -> None:
        """Send current state to the web dashboard."""
        if self._dashboard is None:
            return
        try:
            if confidence >= config.LASER_LOCK_THRESHOLD:
                status = "TRACKING"
            elif confidence >= config.GIMBAL_SCAN_THRESHOLD:
                status = "DETECTING"
            else:
                status = "ALL_CLEAR"

            state = {
                "threat_level": ThreatObject.level_from_confidence(confidence),
                "confidence": round(confidence, 3),
                "bearing_deg": round(self._last_bearing if self._last_bearing >= 0 else 0, 1),
                "range_m": round(self._current_range_m, 2) if self._current_range_m > 0 else None,
                "active_sensors": sorted(active_types),
                "status": status,
                "gimbal_bearing": round(self._gimbal.current_bearing, 1),
                "laser_active": self._laser_locked,
                "site_id": config.SITE_ID,
                "threats": self._threat_log,
                "radar_points": [
                    {"bearing": b, "range": r, "type": "scan"}
                    for b, r, t in self._radar_history
                    if time.time() - t < 30
                ] + ([{"bearing": self._last_bearing, "range": self._current_range_m, "type": "threat"}]
                     if self._current_range_m > 0 and self._last_bearing >= 0 else []),
                "sensor_confidence": self._get_sensor_confidence(),
                "rf_signal_history": list(self._rf_signal_history),
                "gimbal_tilt": round(self._gimbal.current_tilt, 1),
                "tracks": [
                    {
                        "id": t.track_id,
                        "bearing": round(t.bearing, 1),
                        "confidence": round(t.confidence, 3),
                        "sensors": sorted(t.active_sensors),
                    }
                    for t in self._tracks[:5]
                ],
            }
            self._dashboard.push_state(state)
        except Exception:
            pass  # never let dashboard errors break fusion


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys

    os.environ.setdefault("SILVER_DOME_SIMULATE", "true")
    import importlib
    importlib.reload(config)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # ------------------------------------------------------------------
    # Minimal stub dependencies for testing
    # ------------------------------------------------------------------

    class _StubGimbal:
        _bearing = 0.0

        @property
        def current_bearing(self) -> float:
            return self._bearing

        def pan_to(self, deg: float) -> None:
            self._bearing = deg % 360.0
            logger.info("[STUB GIMBAL] pan_to(%.1f)", deg)

        def laser_on(self) -> None:
            logger.info("[STUB GIMBAL] laser_on()")

        def laser_off(self) -> None:
            logger.info("[STUB GIMBAL] laser_off()")

        def lock_on(self, bearing_deg: float = 0.0, tilt_deg: float = 90.0) -> None:
            self._bearing = bearing_deg % 360.0
            logger.info("[STUB GIMBAL] lock_on(bearing=%.1f, tilt=%.1f)", bearing_deg, tilt_deg)

        def stand_down(self) -> None:
            logger.info("[STUB GIMBAL] stand_down()")

    class _StubVisual:
        """Returns a detection every other call."""
        _call_count = 0

        def scan(self):
            self._call_count += 1
            if self._call_count % 2 == 0:
                return SensorEvent(
                    sensor_type="visual",
                    timestamp=time.time(),
                    confidence=0.75,
                    bearing_hint=15.0,
                    metadata={"yolo_class": "person", "simulated": True},
                )
            return None

    class _StubFoundry:
        def write_threat(self, threat: ThreatObject) -> None:
            logger.info("[STUB FOUNDRY] write_threat(%s)", threat.to_dict())

    # ------------------------------------------------------------------
    # Build and feed the engine
    # ------------------------------------------------------------------

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from display.indicators import StatusIndicator

    eq: queue.Queue = queue.Queue()
    status = StatusIndicator()
    engine = FusionEngine(
        event_queue=eq,
        gimbal_controller=_StubGimbal(),
        visual_sensor=_StubVisual(),
        status_indicator=status,
        foundry_client=_StubFoundry(),
    )

    engine_thread = threading.Thread(target=engine.run, daemon=True, name="fusion-engine")
    engine_thread.start()

    print("=== Fusion Engine Test ===")
    print("Pushing fake events -- watch the logs.\n")

    try:
        # Phase 1: RF only -> low confidence
        print(">> Phase 1: RF only")
        eq.put(SensorEvent(sensor_type="rf", timestamp=time.time(), confidence=0.6,
                           metadata={"signal_strength_db": 15.0, "frequency_mhz": 2420.0}))
        time.sleep(2)

        # Phase 2: RF + PIR -> medium confidence, should trigger visual scan
        print("\n>> Phase 2: RF + PIR")
        eq.put(SensorEvent(sensor_type="rf", timestamp=time.time(), confidence=0.7,
                           bearing_hint=90.0,
                           metadata={"signal_strength_db": 18.0, "frequency_mhz": 2420.0}))
        eq.put(SensorEvent(sensor_type="pir", timestamp=time.time(), confidence=1.0,
                           metadata={"pin_state": 1}))
        time.sleep(3)

        # Phase 3: All three -> high confidence, laser lock
        print("\n>> Phase 3: RF + PIR + Visual (manual inject)")
        eq.put(SensorEvent(sensor_type="rf", timestamp=time.time(), confidence=0.8,
                           bearing_hint=120.0,
                           metadata={"signal_strength_db": 22.0, "frequency_mhz": 2420.0}))
        eq.put(SensorEvent(sensor_type="pir", timestamp=time.time(), confidence=1.0,
                           metadata={"pin_state": 1}))
        eq.put(SensorEvent(sensor_type="visual", timestamp=time.time(), confidence=0.85,
                           bearing_hint=118.0,
                           metadata={"yolo_class": "person", "bbox": [100, 50, 200, 300]}))
        time.sleep(3)

        # Phase 4: Let events expire -> ALL CLEAR
        print("\n>> Phase 4: Waiting for ALL CLEAR (events expire + timeout)...")
        time.sleep(config.FUSION_WINDOW_SEC + config.CLEAR_TIMEOUT_SEC + 2)

        print("\n>> Done.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        engine.stop()
        engine_thread.join(timeout=5)
        status.cleanup()
        print("Cleaned up.")
