"""
Silver Dome -- Visual Sensor Module (Camera + YOLOv8)
On-demand scanning: call scan() from the fusion engine when corroboration is needed.
"""
import logging
import random
import time
from typing import Optional

import config
from models import SensorEvent

logger = logging.getLogger("silver_dome.visual_sensor")


class VisualSensor:
    """Camera + YOLOv8 inference -- on-demand, not a continuous thread."""

    def __init__(self):
        self._simulate = config.SIMULATE
        self._cap = None
        self._model = None
        self._use_drone_model = config.YOLO_USE_DRONE_MODEL

        if self._use_drone_model:
            logger.info("Visual sensor: using DRONE detection model")
        else:
            logger.info("Visual sensor: using COCO model (demo mode)")

        if not self._simulate:
            self._init_hardware()

    # ------------------------------------------------------------------
    # Hardware initialisation
    # ------------------------------------------------------------------

    def _init_hardware(self) -> None:
        # Camera
        try:
            import cv2  # type: ignore

            cap = cv2.VideoCapture(config.CAMERA_INDEX)
            if not cap.isOpened():
                raise RuntimeError(f"Camera index {config.CAMERA_INDEX} not available")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_RES_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_RES_H)
            self._cap = cap
            self._cv2 = cv2
            logger.info(
                "Camera opened  index=%d  res=%dx%d",
                config.CAMERA_INDEX,
                config.CAMERA_RES_W,
                config.CAMERA_RES_H,
            )
        except Exception as exc:
            logger.warning("Camera unavailable (%s) -- falling back to SIMULATE", exc)
            self._simulate = True
            return

        # YOLO model
        try:
            from ultralytics import YOLO  # type: ignore

            model_path = config.YOLO_DRONE_MODEL if self._use_drone_model else config.YOLO_MODEL
            self._model = YOLO(model_path)
            logger.info("YOLOv8 model loaded: %s", model_path)
        except Exception as exc:
            logger.warning("YOLO unavailable (%s) -- falling back to SIMULATE", exc)
            self._simulate = True
            self._release_camera()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> Optional[SensorEvent]:
        """
        Capture a frame, run YOLO, return a SensorEvent if a target class
        is detected -- otherwise return None.
        """
        try:
            if self._simulate:
                return self._scan_simulate()
            return self._scan_hardware()
        except Exception:
            logger.exception("Error during visual scan")
            return None

    def release(self) -> None:
        """Release camera resources."""
        self._release_camera()

    # ------------------------------------------------------------------
    # Hardware scan
    # ------------------------------------------------------------------

    def _scan_hardware(self) -> Optional[SensorEvent]:
        ret, frame = self._cap.read()
        if not ret or frame is None:
            logger.warning("Failed to capture frame from camera")
            return None

        frame_h, frame_w = frame.shape[:2]
        frame_center_x = frame_w / 2.0

        results = self._model(frame, conf=config.YOLO_CONFIDENCE, verbose=False)

        best_event: Optional[SensorEvent] = None
        best_conf = 0.0

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                target_classes = config.YOLO_DRONE_CLASSES if self._use_drone_model else config.YOLO_TARGET_CLASSES
                if cls_id not in target_classes:
                    continue

                conf = float(box.conf[0])
                if conf <= best_conf:
                    continue

                # Bounding box: xyxy format
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                det_center_x = (x1 + x2) / 2.0
                bbox_w = x2 - x1
                bbox_h = y2 - y1

                # Bearing offset from frame center
                bearing_offset = (
                    (det_center_x - frame_center_x) / frame_w
                ) * config.CAMERA_FOV_H

                # Map COCO class id to name
                cls_name = result.names.get(cls_id, str(cls_id))

                best_conf = conf
                best_event = SensorEvent(
                    sensor_type="visual",
                    timestamp=time.time(),
                    confidence=conf,
                    bearing_hint=round(bearing_offset, 2),
                    metadata={
                        "yolo_class": cls_name,
                        "yolo_class_id": cls_id,
                        "bbox": [
                            round(x1, 1),
                            round(y1, 1),
                            round(bbox_w, 1),
                            round(bbox_h, 1),
                        ],
                        "frame_x_center": round(det_center_x, 1),
                        "frame_width": frame_w,
                    },
                )

        if best_event:
            logger.info(
                "Visual detection: %s  conf=%.2f  bearing=%.1f deg",
                best_event.metadata.get("yolo_class"),
                best_event.confidence,
                best_event.bearing_hint,
            )
        return best_event

    # ------------------------------------------------------------------
    # Simulation scan
    # ------------------------------------------------------------------

    def _scan_simulate(self) -> Optional[SensorEvent]:
        # No fake detections — only real camera/YOLO produces events
        logger.debug("[SIM] Visual scan skipped -- no hardware, no fake events")
        return None

    def _scan_simulate_DISABLED(self) -> Optional[SensorEvent]:
        # Short delay to mimic inference time
        time.sleep(random.uniform(0.05, 0.15))

        # ~70% chance of "detecting" something during a sim scan
        if random.random() > 0.7:
            logger.debug("[SIM] Visual scan -- no detection")
            return None

        frame_w = config.CAMERA_RES_W
        frame_center_x = frame_w / 2.0
        det_center_x = random.uniform(frame_w * 0.1, frame_w * 0.9)
        bearing_offset = (
            (det_center_x - frame_center_x) / frame_w
        ) * config.CAMERA_FOV_H

        if self._use_drone_model:
            cls_name = "drone"
        else:
            cls_name = random.choice(["person", "cell phone", "laptop"])
        conf = random.uniform(config.YOLO_CONFIDENCE, 0.95)

        evt = SensorEvent(
            sensor_type="visual",
            timestamp=time.time(),
            confidence=round(conf, 3),
            bearing_hint=round(bearing_offset, 2),
            metadata={
                "yolo_class": cls_name,
                "bbox": [
                    round(det_center_x - 40, 1),
                    round(random.uniform(100, 300), 1),
                    80.0,
                    120.0,
                ],
                "frame_x_center": round(det_center_x, 1),
                "frame_width": frame_w,
                "simulated": True,
            },
        )
        logger.info(
            "[SIM] Visual detection: %s  conf=%.2f  bearing=%.1f deg",
            cls_name,
            conf,
            bearing_offset,
        )
        return evt

    # ------------------------------------------------------------------
    # Health / status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "name": "visual",
            "simulate": self._simulate,
            "hardware": "camera" if self._cap is not None else "none",
            "model": "drone" if self._use_drone_model else "coco",
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _release_camera(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def __del__(self):
        self._release_camera()


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

    sensor = VisualSensor()

    print("Visual sensor test -- running 5 scans\n")
    for i in range(5):
        result = sensor.scan()
        if result:
            print(f"  Scan {i+1}: {result}")
        else:
            print(f"  Scan {i+1}: no detection")

    sensor.release()
    print("\nDone.")
