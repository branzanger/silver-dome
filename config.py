"""
Silver Dome — Configuration
All constants, GPIO pins, thresholds, and credentials in one place.
"""
import os

# ---------------------------------------------------------------------------
# Operating mode
# ---------------------------------------------------------------------------
SIMULATE = os.getenv("SILVER_DOME_SIMULATE", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Site identity
# ---------------------------------------------------------------------------
SITE_ID = os.getenv("SILVER_DOME_SITE_ID", "FOB-ALPHA")

# ---------------------------------------------------------------------------
# GPIO Pin Assignments (BCM numbering)
# ---------------------------------------------------------------------------
# PIR sensor
PIR_PIN = 17

# Stepper motor (pan) — using A4988 or similar driver
STEPPER_STEP_PIN = 23
STEPPER_DIR_PIN = 24
STEPPER_ENABLE_PIN = 25

# Servo (tilt)
SERVO_TILT_PIN = 18  # hardware PWM capable

# Laser
LASER_PIN = 27

# LEDs
LED_GREEN_PIN = 5
LED_AMBER_PIN = 6
LED_RED_PIN = 13

# ---------------------------------------------------------------------------
# Stepper motor parameters
# ---------------------------------------------------------------------------
STEPPER_STEPS_PER_REV = 200       # 1.8 degrees per step
STEPPER_STEP_DELAY = 0.002        # seconds between pulses (500 Hz)
GIMBAL_PAN_TIMEOUT_SEC = 5.0      # max time for a single pan_to() call
STEPPER_DEGREES_PER_STEP = 360.0 / STEPPER_STEPS_PER_REV  # 1.8

# ---------------------------------------------------------------------------
# Servo parameters
# ---------------------------------------------------------------------------
SERVO_MIN_DUTY = 2.5   # 0 degrees
SERVO_MAX_DUTY = 12.5  # 180 degrees
SERVO_FREQ = 50        # Hz

# ---------------------------------------------------------------------------
# RF Sensor (RTL-SDR)
# ---------------------------------------------------------------------------
RF_CENTER_FREQ = 2.42e9       # 2.42 GHz — center of WiFi/BT band
RF_SAMPLE_RATE = 2.4e6        # 2.4 MHz bandwidth
RF_GAIN = 40                  # dB — adjust per environment
RF_NUM_SAMPLES = 256 * 1024   # FFT size
RF_THRESHOLD_DB = 10          # dB above noise floor = detection
RF_SCAN_INTERVAL = 0.5        # seconds between scans

# Additional frequencies to monitor
RF_FREQUENCIES = [
    433e6,    # RC control
    915e6,    # telemetry
    2.42e9,   # WiFi/BT (primary for demo)
    5.8e9,    # FPV video
]

# ---------------------------------------------------------------------------
# PIR Sensor
# ---------------------------------------------------------------------------
PIR_DEBOUNCE_MS = 500   # minimum ms between triggers
PIR_POLL_INTERVAL = 0.1  # seconds

# ---------------------------------------------------------------------------
# Visual Sensor (Camera + YOLOv8)
# ---------------------------------------------------------------------------
CAMERA_INDEX = 0
CAMERA_FOV_H = 62.2      # horizontal field of view in degrees (typical webcam)
CAMERA_RES_W = 640
CAMERA_RES_H = 480
YOLO_MODEL = "yolov8n.pt"  # nano model for speed
YOLO_CONFIDENCE = 0.4       # minimum detection confidence
# Classes of interest (COCO): person=0, cell phone=67, laptop=63
YOLO_TARGET_CLASSES = [0, 67, 63]

# Drone detection model (deployment mode)
YOLO_DRONE_MODEL = "drone_detect_yolov8s.pt"  # fine-tuned on UAV imagery
YOLO_DRONE_CLASSES = [0]  # class 0 = "drone" in the fine-tuned model
YOLO_USE_DRONE_MODEL = os.getenv("SILVER_DOME_DRONE_MODEL", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Sensor Fusion
# ---------------------------------------------------------------------------
FUSION_WINDOW_SEC = 3.0          # correlation time window
FUSION_PROCESS_INTERVAL = 0.25   # how often fusion engine runs (seconds)

# Confidence scores per sensor combination
CONFIDENCE_RF_ONLY = 0.30
CONFIDENCE_PIR_ONLY = 0.20
CONFIDENCE_ACOUSTIC_ONLY = 0.25
CONFIDENCE_RF_PIR = 0.65
CONFIDENCE_RF_PIR_VISUAL = 0.90
CONFIDENCE_RF_VISUAL = 0.70
CONFIDENCE_PIR_VISUAL = 0.50
CONFIDENCE_ACOUSTIC_VISUAL = 0.92       # audio + vision = very high confidence
CONFIDENCE_ACOUSTIC_RF = 0.55
CONFIDENCE_ACOUSTIC_RF_VISUAL = 0.95    # triple confirmation
CONFIDENCE_ACOUSTIC_PIR_VISUAL = 0.88
CONFIDENCE_ACOUSTIC_RF_PIR_VISUAL = 0.98  # all sensors = near certain

# Thresholds
FOUNDRY_WRITE_THRESHOLD = 0.60   # only write to Foundry above this
GIMBAL_SCAN_THRESHOLD = 0.60     # start gimbal panning above this
LASER_LOCK_THRESHOLD = 0.85      # laser on above this

# Clear timeout — how long after last detection to return to ALL CLEAR
CLEAR_TIMEOUT_SEC = 10.0

# Bearing smoothing (exponential moving average)
BEARING_FILTER_ALPHA = 0.3  # 0=max smoothing, 1=no filtering

# ---------------------------------------------------------------------------
# Palantir Foundry
# ---------------------------------------------------------------------------
FOUNDRY_BASE_URL = os.getenv("FOUNDRY_BASE_URL", "https://mukund.usw-18.palantirfoundry.com")
FOUNDRY_TOKEN = os.getenv("FOUNDRY_TOKEN", "")
FOUNDRY_ONTOLOGY_ID = os.getenv("FOUNDRY_ONTOLOGY_ID", "")
FOUNDRY_OBJECT_TYPE = os.getenv("FOUNDRY_OBJECT_TYPE", "ThreatObject")

# ---------------------------------------------------------------------------
# Distance Sensor
# ---------------------------------------------------------------------------
DISTANCE_TRIG_PIN = 20
DISTANCE_ECHO_PIN = 21
DISTANCE_MAX_RANGE_M = 4.0
DISTANCE_TIMEOUT_S = 0.03  # 30ms timeout for ultrasonic

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("SILVER_DOME_LOG_LEVEL", "INFO")
LOG_FILE = "silver_dome.log"
