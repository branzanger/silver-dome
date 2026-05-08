"""
Silver Dome -- Palantir Foundry client.

Writes fused ThreatObjects to Foundry's Ontology via its REST API.
Queues locally when the network is unavailable or credentials are missing,
and flushes the queue on reconnection.
"""

import json
import logging
import threading
from pathlib import Path
from typing import List

import requests

import config
from models import ThreatObject

logger = logging.getLogger(__name__)

QUEUE_PATH = Path(__file__).resolve().parent.parent / "threat_queue.json"


class FoundryClient:
    """Thread-safe Foundry writer with offline queueing and simulation mode."""

    def __init__(
        self,
        base_url: str = config.FOUNDRY_BASE_URL,
        token: str = config.FOUNDRY_TOKEN,
        ontology_id: str = config.FOUNDRY_ONTOLOGY_ID,
        object_type: str = config.FOUNDRY_OBJECT_TYPE,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ontology_id = ontology_id
        self.object_type = object_type
        self._lock = threading.Lock()

        # Determine if we're running in simulation mode
        self.simulate = config.SIMULATE or not self.token
        if self.simulate:
            reason = "SIMULATE flag" if config.SIMULATE else "empty credentials"
            logger.warning("FoundryClient running in SIMULATION mode (%s)", reason)

        if not self.token:
            logger.warning("FOUNDRY_TOKEN is empty -- writes will be queued locally")
        if not self.ontology_id:
            logger.warning("FOUNDRY_ONTOLOGY_ID is empty")

    def status(self) -> dict:
        return {
            "name": "foundry",
            "simulate": self.simulate,
            "has_token": bool(self.token),
            "has_ontology_id": bool(self.ontology_id),
            "base_url": self.base_url,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_threat(self, threat: ThreatObject) -> bool:
        """Write a ThreatObject to Foundry. Returns True on success."""
        if self.simulate:
            logger.info(
                "[SIM] Would write ThreatObject %s: %s",
                threat.threat_id,
                json.dumps(threat.to_dict(), default=str),
            )
            self._queue_locally(threat)
            return False

        url = (
            f"{self.base_url}/api/v2/ontologies/"
            f"{self.ontology_id}/actions/create-threat-object/apply"
        )
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        # Only send parameters the Foundry action type accepts
        full = threat.to_dict()
        accepted_keys = {"timestamp", "confidence", "bearingDeg", "rangeM",
                         "threatLevel", "siteId", "status"}
        body = {"parameters": {k: v for k, v in full.items() if k in accepted_keys}}

        try:
            logger.debug("POST %s  body=%s", url, json.dumps(body, default=str))
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            logger.debug(
                "Response %s: %s", resp.status_code, resp.text[:500]
            )

            if resp.ok:
                logger.info(
                    "Wrote ThreatObject %s to Foundry (HTTP %s)",
                    threat.threat_id,
                    resp.status_code,
                )
                return True

            logger.error(
                "Foundry rejected ThreatObject %s: HTTP %s -- %s",
                threat.threat_id,
                resp.status_code,
                resp.text[:500],
            )
            self._queue_locally(threat)
            return False

        except requests.RequestException as exc:
            logger.error(
                "Network error writing ThreatObject %s: %s", threat.threat_id, exc
            )
            self._queue_locally(threat)
            return False
        except Exception as exc:
            logger.error(
                "Unexpected error writing ThreatObject %s: %s", threat.threat_id, exc
            )
            self._queue_locally(threat)
            return False

    def flush_queue(self) -> int:
        """Attempt to write all queued threats. Returns count of successes."""
        queued = self._load_queue()
        if not queued:
            logger.debug("No queued threats to flush")
            return 0

        logger.info("Flushing %d queued threat(s)", len(queued))
        successes = 0
        remaining: List[dict] = []

        for entry in queued:
            try:
                threat = _dict_to_threat(entry)
            except Exception as exc:
                logger.error("Skipping malformed queue entry: %s", exc)
                continue

            if self.write_threat(threat):
                successes += 1
            else:
                # write_threat already re-queues on failure, but we handle
                # it ourselves here to avoid double-queueing.
                remaining.append(entry)

        # Overwrite the queue with only the failures (write_threat will have
        # appended them again, so we replace the file with the canonical list).
        self._save_queue(remaining)
        logger.info("Flush complete: %d/%d succeeded", successes, len(queued))
        return successes

    # ------------------------------------------------------------------
    # Offline queue (thread-safe)
    # ------------------------------------------------------------------

    def _queue_locally(self, threat: ThreatObject) -> None:
        """Append a ThreatObject to the local JSON queue file."""
        with self._lock:
            try:
                queue = self._load_queue_unlocked()
                queue.append(threat.to_dict())
                self._save_queue_unlocked(queue)
                logger.info(
                    "Queued ThreatObject %s locally (%d total)",
                    threat.threat_id,
                    len(queue),
                )
            except Exception as exc:
                logger.error("Failed to queue threat locally: %s", exc)

    def _load_queue(self) -> List[dict]:
        with self._lock:
            return self._load_queue_unlocked()

    def _save_queue(self, data: List[dict]) -> None:
        with self._lock:
            self._save_queue_unlocked(data)

    # Unlocked helpers (caller must hold self._lock)

    def _load_queue_unlocked(self) -> List[dict]:
        if not QUEUE_PATH.exists():
            return []
        try:
            text = QUEUE_PATH.read_text()
            return json.loads(text) if text.strip() else []
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Corrupt queue file, resetting: %s", exc)
            return []

    def _save_queue_unlocked(self, data: List[dict]) -> None:
        QUEUE_PATH.write_text(json.dumps(data, indent=2, default=str))


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _dict_to_threat(d: dict) -> ThreatObject:
    """Reconstruct a ThreatObject from its to_dict() output."""
    return ThreatObject(
        threat_id=d["threatId"],
        timestamp=d["timestamp"],
        confidence=d["confidence"],
        bearing_deg=d["bearingDeg"],
        range_m=d.get("rangeM", -1.0),
        tilt_deg=d["tiltDeg"],
        active_sensors=d["activeSensors"],
        threat_level=d["threatLevel"],
        site_id=d["siteId"],
        status=d["status"],
        metadata=d.get("metadata", {}),
    )


# ------------------------------------------------------------------
# Self-test
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    sample = ThreatObject(
        confidence=0.78,
        bearing_deg=135.0,
        tilt_deg=12.5,
        active_sensors=["rf", "pir"],
        threat_level=ThreatObject.level_from_confidence(0.78),
        site_id=config.SITE_ID,
        status="TRACKING",
        metadata={"signal_strength_db": 14.3, "frequency_mhz": 2420},
    )

    client = FoundryClient()
    print(f"Simulate mode: {client.simulate}")
    print(f"Queue path:    {QUEUE_PATH}")
    print()

    success = client.write_threat(sample)
    print(f"write_threat returned: {success}")
    print()

    count = client.flush_queue()
    print(f"flush_queue returned:  {count} successes")
