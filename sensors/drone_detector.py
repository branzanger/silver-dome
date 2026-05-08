"""
Silver Dome -- Drone Vision Detector
Continuous webcam-based Shahed drone detection using YOLOv8.
Runs as a thread, pushes SensorEvent objects to the shared queue.

Architecture: two internal loops —
  1. Fast capture loop (~30fps): grabs frames, encodes JPEG for MJPEG stream
  2. Slow inference loop (~1fps): runs YOLO on latest frame, emits SensorEvents
"""
import logging
import os
import random
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Optional

import config
from models import SensorEvent

logger = logging.getLogger("silver_dome.drone_detector")

# Model paths
_MODELS_DIR = Path(__file__).parent.parent / "models"
_SHAHED_MODEL = _MODELS_DIR / "shahed.pt"

# Intervals
_CAPTURE_INTERVAL = 0.033  # ~30fps camera capture for smooth stream
_INFERENCE_INTERVAL = 1.5  # seconds between YOLO runs (inference is ~0.5-1s)

# Simulated sensor delay range (seconds) after a visual detection
_SIM_DELAY_MIN = 0.05
_SIM_DELAY_MAX = 0.4


class DroneDetector:
    """Continuous webcam drone detector with YOLOv8."""

    def __init__(self):
        self._cap = None
        self._model = None
        self._model_name = "none"
        self._target_classes: list[int] = []
        self._class_names: dict[int, str] = {}
        self._running = False
        self._stop_event = threading.Event()
        self._last_detection_time: float = 0
        self._detection_count: int = 0

        # Shared frame buffers
        self._frame_lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._latest_raw_frame = None  # raw numpy frame for YOLO

        # Latest detection overlay info (drawn on stream frames)
        self._overlay_lock = threading.Lock()
        self._overlay_boxes: list = []  # [(x1,y1,x2,y2,conf,cls_name), ...]
        self._overlay_expire: float = 0  # timestamp when overlay should clear

        self._init_camera()
        self._init_model()

    def _init_camera(self):
        try:
            import cv2
            self._cv2 = cv2
            cap = cv2.VideoCapture(config.CAMERA_INDEX)
            if not cap.isOpened():
                raise RuntimeError(f"Camera index {config.CAMERA_INDEX} not available")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_RES_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_RES_H)
            self._cap = cap
            logger.info("Camera opened: index=%d res=%dx%d",
                        config.CAMERA_INDEX, config.CAMERA_RES_W, config.CAMERA_RES_H)
        except Exception as exc:
            logger.error("Failed to open camera: %s", exc)
            self._cap = None

    def _init_model(self):
        try:
            from ultralytics import YOLO
        except ImportError:
            logger.error("ultralytics not installed -- cannot load YOLO model")
            return

        # Priority 1: Custom Shahed model
        if _SHAHED_MODEL.exists():
            self._model = YOLO(str(_SHAHED_MODEL))
            self._model_name = "shahed-custom"
            self._target_classes = [0]
            self._class_names = {0: "shahed-136"}
            logger.info("Loaded custom Shahed model: %s", _SHAHED_MODEL)
            return

        # Priority 2: Roboflow model
        api_key = os.getenv("ROBOFLOW_API_KEY", "")
        if api_key:
            try:
                from roboflow import Roboflow
                rf = Roboflow(api_key=api_key)
                project = rf.workspace("myws-tckfd").project("shahed-136-bvs8o")
                version = project.version(1)
                model_path = version.download("yolov8", location=str(_MODELS_DIR / "roboflow_shahed"))
                self._model = YOLO(str(Path(model_path) / "best.pt"))
                self._model_name = "shahed-roboflow"
                self._target_classes = [0]
                self._class_names = {0: "shahed-136"}
                logger.info("Loaded Roboflow Shahed-136 model")
                return
            except Exception as exc:
                logger.warning("Roboflow model download failed: %s -- falling back", exc)

        # Priority 3: Standard YOLOv8 with airplane class as proxy
        self._model = YOLO("yolov8n.pt")
        self._model_name = "yolov8n-coco"
        self._target_classes = [4]  # COCO class 4 = airplane
        self._class_names = {4: "shahed-136"}
        logger.info("Loaded YOLOv8n COCO model (airplane class as Shahed proxy)")

    def run(self, event_queue: Queue) -> None:
        """Main loop -- run as thread target. Spawns capture + inference threads."""
        if self._cap is None or self._model is None:
            logger.error("DroneDetector cannot run: camera=%s model=%s",
                         self._cap is not None, self._model is not None)
            return

        self._running = True
        self._stop_event.clear()
        logger.info("DroneDetector started (model=%s)", self._model_name)

        # Start inference in a separate thread
        inference_thread = threading.Thread(
            target=self._inference_loop, args=(event_queue,),
            name="drone-inference", daemon=True
        )
        inference_thread.start()

        # Capture loop runs in this thread (fast, for MJPEG stream)
        try:
            self._capture_loop()
        finally:
            self._running = False
            inference_thread.join(timeout=3)
            logger.info("DroneDetector stopped (total detections: %d)", self._detection_count)

    def _capture_loop(self):
        """Fast loop: grab frames, draw overlays, encode JPEG for stream."""
        cv2 = self._cv2
        while not self._stop_event.is_set():
            ret, frame = self._cap.read()
            if not ret or frame is None:
                self._stop_event.wait(timeout=0.1)
                continue

            # Store raw frame for inference thread
            with self._frame_lock:
                self._latest_raw_frame = frame.copy()

            # Draw detection overlays from last inference
            with self._overlay_lock:
                boxes = self._overlay_boxes if time.time() < self._overlay_expire else []

            frame_h, frame_w = frame.shape[:2]
            for (x1, y1, x2, y2, conf, cls_name) in boxes:
                color = (0, 0, 255)
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                label = f"SHAHED-136 {conf:.0%}"
                label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(frame, (int(x1), int(y1) - label_size[1] - 8),
                              (int(x1) + label_size[0] + 4, int(y1)), color, -1)
                cv2.putText(frame, label, (int(x1) + 2, int(y1) - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            # Status overlay
            status_text = "SCANNING..." if not boxes else f"THREAT DETECTED ({len(boxes)})"
            status_color = (0, 180, 0) if not boxes else (0, 0, 255)
            cv2.putText(frame, status_text, (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)

            # Encode JPEG
            _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
            with self._frame_lock:
                self._latest_jpeg = jpeg.tobytes()

            self._stop_event.wait(timeout=_CAPTURE_INTERVAL)

    def _inference_loop(self, event_queue: Queue):
        """Slow loop: run YOLO on latest frame, emit events."""
        while not self._stop_event.is_set():
            # Grab latest frame
            with self._frame_lock:
                frame = self._latest_raw_frame
            if frame is None:
                self._stop_event.wait(timeout=0.2)
                continue

            try:
                detection = self._run_inference(frame)
                if detection is not None:
                    event_queue.put(detection)
                    self._detection_count += 1
                    self._last_detection_time = time.time()
                    self._generate_sim_rf(event_queue, detection)
                    self._generate_sim_pir(event_queue)
            except Exception:
                logger.exception("Error in drone inference")

            self._stop_event.wait(timeout=_INFERENCE_INTERVAL)

    def _run_inference(self, frame) -> Optional[SensorEvent]:
        """Run YOLO on a frame, update overlay, return SensorEvent or None."""
        frame_h, frame_w = frame.shape[:2]
        frame_center_x = frame_w / 2.0

        results = self._model(frame, conf=config.YOLO_CONFIDENCE, verbose=False)

        best_event = None
        best_conf = 0.0
        detections = []

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in self._target_classes:
                    continue

                conf = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                det_center_x = (x1 + x2) / 2.0
                bbox_w = x2 - x1
                bbox_h = y2 - y1
                cls_name = self._class_names.get(cls_id, result.names.get(cls_id, str(cls_id)))

                detections.append((x1, y1, x2, y2, conf, cls_name))

                if conf <= best_conf:
                    continue

                bearing_offset = (
                    (det_center_x - frame_center_x) / frame_w
                ) * config.CAMERA_FOV_H

                best_conf = conf
                best_event = SensorEvent(
                    sensor_type="visual",
                    timestamp=time.time(),
                    confidence=conf,
                    bearing_hint=round(bearing_offset, 2),
                    metadata={
                        "yolo_class": cls_name,
                        "yolo_class_id": cls_id,
                        "bbox": [round(x1, 1), round(y1, 1), round(bbox_w, 1), round(bbox_h, 1)],
                        "frame_x_center": round(det_center_x, 1),
                        "frame_width": frame_w,
                        "drone_type": "Shahed-136",
                        "model": self._model_name,
                    },
                )

        # Update overlay for capture loop to draw
        with self._overlay_lock:
            self._overlay_boxes = detections
            self._overlay_expire = time.time() + _INFERENCE_INTERVAL + 0.5

        if best_event:
            logger.info(
                "DRONE DETECTED: %s  conf=%.2f  bearing=%.1f deg  model=%s",
                best_event.metadata.get("yolo_class"),
                best_event.confidence,
                best_event.bearing_hint,
                self._model_name,
            )
        return best_event

    def stop(self):
        self._stop_event.set()

    def release(self):
        self.stop()
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def get_latest_jpeg(self) -> bytes | None:
        """Return the most recent annotated frame as JPEG bytes (for MJPEG streaming)."""
        with self._frame_lock:
            return self._latest_jpeg

    def _generate_sim_rf(self, queue: Queue, visual_event: SensorEvent) -> None:
        """Generate a simulated RF event as if the drone is emitting signals."""
        signal_strength = random.uniform(12.0, 25.0)
        freq = random.choice([433.0, 915.0, 2420.0])
        evt = SensorEvent(
            sensor_type="rf",
            timestamp=time.time(),
            confidence=round(random.uniform(0.5, 0.85), 3),
            bearing_hint=visual_event.bearing_hint + random.uniform(-5, 5),
            metadata={
                "signal_strength_db": round(signal_strength, 1),
                "frequency_mhz": freq,
                "simulated": True,
                "source": "drone_rf_emission",
            },
        )
        queue.put(evt)

    def _generate_sim_pir(self, queue: Queue) -> None:
        """Generate a simulated PIR event (heat signature from drone engine)."""
        evt = SensorEvent(
            sensor_type="pir",
            timestamp=time.time(),
            confidence=1.0,
            bearing_hint=-1,
            metadata={
                "pin_state": 1,
                "simulated": True,
                "source": "drone_heat_signature",
            },
        )
        queue.put(evt)

    def status(self) -> dict:
        return {
            "name": "drone_detector",
            "running": self._running,
            "camera": self._cap is not None,
            "model": self._model_name,
            "model_loaded": self._model is not None,
            "detection_count": self._detection_count,
            "last_detection": self._last_detection_time,
        }
