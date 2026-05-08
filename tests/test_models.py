"""Tests for Silver Dome data models."""
import time
from models import SensorEvent, ThreatObject


def test_sensor_event_defaults():
    evt = SensorEvent(sensor_type="rf", timestamp=time.time(), confidence=0.5)
    assert evt.bearing_hint == -1
    assert evt.metadata == {}


def test_sensor_event_with_metadata():
    evt = SensorEvent(
        sensor_type="rf",
        timestamp=time.time(),
        confidence=0.7,
        bearing_hint=45.0,
        metadata={"signal_strength_db": 15.0},
    )
    assert evt.bearing_hint == 45.0
    assert evt.metadata["signal_strength_db"] == 15.0


def test_threat_object_to_dict():
    t = ThreatObject(
        confidence=0.85,
        bearing_deg=120.0,
        active_sensors=["rf", "pir"],
        threat_level="HIGH",
        site_id="FOB-ALPHA",
        status="TRACKING",
    )
    d = t.to_dict()
    assert d["confidence"] == 0.85
    assert d["bearingDeg"] == 120.0
    assert d["activeSensors"] == ["rf", "pir"]
    assert d["threatLevel"] == "HIGH"
    assert d["siteId"] == "FOB-ALPHA"
    assert "threatId" in d
    assert "timestamp" in d


def test_threat_level_from_confidence():
    assert ThreatObject.level_from_confidence(0.95) == "HIGH"
    assert ThreatObject.level_from_confidence(0.85) == "HIGH"
    assert ThreatObject.level_from_confidence(0.70) == "MEDIUM"
    assert ThreatObject.level_from_confidence(0.60) == "MEDIUM"
    assert ThreatObject.level_from_confidence(0.40) == "LOW"
    assert ThreatObject.level_from_confidence(0.0) == "LOW"
