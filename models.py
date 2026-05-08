"""
Silver Dome — Shared data models.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid


@dataclass
class SensorEvent:
    """A single detection event from one sensor modality."""
    sensor_type: str          # "rf" | "pir" | "visual"
    timestamp: float          # time.time()
    confidence: float         # per-sensor confidence 0-1
    bearing_hint: float = -1  # degrees, -1 if unknown
    metadata: dict = field(default_factory=dict)
    # metadata examples:
    #   rf:     {"signal_strength_db": 15.2, "frequency_mhz": 2420}
    #   pir:    {"pin_state": 1}
    #   visual: {"yolo_class": "person", "bbox": [x,y,w,h], "frame_x_center": 320}


@dataclass
class ThreatObject:
    """A fused threat event written to Foundry."""
    threat_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confidence: float = 0.0
    bearing_deg: float = 0.0
    range_m: float = -1.0         # distance in meters, -1 if unknown
    tilt_deg: float = 0.0
    active_sensors: list = field(default_factory=list)
    threat_level: str = "LOW"  # LOW | MEDIUM | HIGH
    site_id: str = "FOB-ALPHA"
    status: str = "ACTIVE"     # ACTIVE | TRACKING | CLEARED
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "threatId": self.threat_id,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "bearingDeg": self.bearing_deg,
            "rangeM": self.range_m,
            "tiltDeg": self.tilt_deg,
            "activeSensors": self.active_sensors,
            "threatLevel": self.threat_level,
            "siteId": self.site_id,
            "status": self.status,
            "metadata": self.metadata,
        }

    @staticmethod
    def level_from_confidence(confidence: float) -> str:
        if confidence >= 0.85:
            return "HIGH"
        elif confidence >= 0.60:
            return "MEDIUM"
        return "LOW"


@dataclass
class ThreatTrack:
    """A cluster of correlated sensor events around a common bearing."""
    track_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    bearing: float = 0.0
    events: list = field(default_factory=list)
    confidence: float = 0.0
    last_update: float = 0.0
    active_sensors: set = field(default_factory=set)
    range_m: float = -1.0
