"""Tests for the Silver Dome fusion engine."""
import time
from queue import Queue

import config
from models import SensorEvent
from fusion.engine import FusionEngine, _BearingSmoother, _CONFIDENCE_TABLE


class TestConfidenceTable:
    def test_rf_only(self):
        assert _CONFIDENCE_TABLE[frozenset({"rf"})] == config.CONFIDENCE_RF_ONLY

    def test_pir_only(self):
        assert _CONFIDENCE_TABLE[frozenset({"pir"})] == config.CONFIDENCE_PIR_ONLY

    def test_rf_pir(self):
        assert _CONFIDENCE_TABLE[frozenset({"rf", "pir"})] == config.CONFIDENCE_RF_PIR

    def test_rf_pir_visual(self):
        assert _CONFIDENCE_TABLE[frozenset({"rf", "pir", "visual"})] == config.CONFIDENCE_RF_PIR_VISUAL

    def test_empty(self):
        assert _CONFIDENCE_TABLE[frozenset()] == 0.0


class TestBearingSmoother:
    def test_first_value_passes_through(self):
        s = _BearingSmoother(alpha=0.3)
        assert s.update(90.0) == 90.0

    def test_smoothing_moves_toward_target(self):
        s = _BearingSmoother(alpha=0.5)
        s.update(0.0)
        val = s.update(100.0)
        assert 40.0 < val < 60.0  # should be ~50

    def test_wraparound_359_to_1(self):
        s = _BearingSmoother(alpha=0.5)
        s.update(359.0)
        val = s.update(1.0)
        # Should go 359 -> ~0 (shortest path across 360)
        assert val > 350.0 or val < 10.0

    def test_reset(self):
        s = _BearingSmoother(alpha=0.3)
        s.update(180.0)
        s.reset()
        assert s.update(45.0) == 45.0  # first value after reset passes through


class TestFusionEngine:
    def _make_engine(self, stub_gimbal, stub_visual, stub_indicators, stub_foundry):
        eq = Queue()
        engine = FusionEngine(
            event_queue=eq,
            gimbal_controller=stub_gimbal,
            visual_sensor=stub_visual,
            status_indicator=stub_indicators,
            foundry_client=stub_foundry,
        )
        return engine, eq

    def test_drain_queue(self, stub_gimbal, stub_visual, stub_indicators, stub_foundry):
        engine, eq = self._make_engine(stub_gimbal, stub_visual, stub_indicators, stub_foundry)
        eq.put(SensorEvent(sensor_type="rf", timestamp=time.time(), confidence=0.5))
        eq.put(SensorEvent(sensor_type="pir", timestamp=time.time(), confidence=1.0))
        engine._drain_queue()
        assert len(engine._window) == 2

    def test_expire_window(self, stub_gimbal, stub_visual, stub_indicators, stub_foundry):
        engine, eq = self._make_engine(stub_gimbal, stub_visual, stub_indicators, stub_foundry)
        old_event = SensorEvent(sensor_type="rf", timestamp=time.time() - 10, confidence=0.5)
        new_event = SensorEvent(sensor_type="rf", timestamp=time.time(), confidence=0.5)
        engine._window = [old_event, new_event]
        engine._expire_window()
        assert len(engine._window) == 1

    def test_active_sensor_types(self, stub_gimbal, stub_visual, stub_indicators, stub_foundry):
        engine, eq = self._make_engine(stub_gimbal, stub_visual, stub_indicators, stub_foundry)
        engine._window = [
            SensorEvent(sensor_type="rf", timestamp=time.time(), confidence=0.5),
            SensorEvent(sensor_type="pir", timestamp=time.time(), confidence=1.0),
        ]
        assert engine._active_sensor_types() == {"rf", "pir"}

    def test_rf_signal_history_tracking(self, stub_gimbal, stub_visual, stub_indicators, stub_foundry):
        engine, eq = self._make_engine(stub_gimbal, stub_visual, stub_indicators, stub_foundry)
        for i in range(5):
            eq.put(SensorEvent(
                sensor_type="rf", timestamp=time.time(), confidence=0.5,
                metadata={"signal_strength_db": 10.0 + i},
            ))
        engine._drain_queue()
        assert len(engine._rf_signal_history) == 5
        assert engine._rf_signal_history[-1] == 14.0

    def test_sensor_confidence_extraction(self, stub_gimbal, stub_visual, stub_indicators, stub_foundry):
        engine, eq = self._make_engine(stub_gimbal, stub_visual, stub_indicators, stub_foundry)
        engine._window = [
            SensorEvent(sensor_type="rf", timestamp=time.time(), confidence=0.65),
            SensorEvent(sensor_type="pir", timestamp=time.time(), confidence=1.0),
        ]
        sc = engine._get_sensor_confidence()
        assert sc["rf"] == 0.65
        assert sc["pir"] == 1.0

    def test_status_method(self, stub_gimbal, stub_visual, stub_indicators, stub_foundry):
        engine, eq = self._make_engine(stub_gimbal, stub_visual, stub_indicators, stub_foundry)
        st = engine.status()
        assert st["name"] == "fusion"
        assert st["running"] is False
        assert st["confidence"] == 0.0
