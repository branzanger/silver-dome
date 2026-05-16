# Silver Dome

Portable drone early-warning prototype built for the 3rd Annual National Security Hackathon.

Silver Dome pulls together cheap sensors, a Raspberry Pi, a small gimbal, and a local dashboard into a kit that can detect and track likely aerial threats. The repo includes simulation paths so the software can be run on a laptop without wiring up the hardware first.

## What It Does

- Reads events from RF, PIR, camera, distance, and acoustic sensors
- Correlates detections in a short time window and assigns a confidence score
- Drives status LEDs and a pan/tilt gimbal when confidence crosses configured thresholds
- Publishes high-confidence events to Palantir Foundry when credentials are configured
- Serves a local dashboard at `http://localhost:8080`

The main goal is a low-cost field prototype, not a finished air-defense product. Most modules have hardware fallbacks or simulated modes so development can happen away from the Pi.

## Hardware

Approximate bill of materials:

| Component | Approx. cost |
| --- | ---: |
| Raspberry Pi 5 | $80 |
| RTL-SDR USB dongle | $25 |
| HC-SR04 ultrasonic sensor | $3 |
| HW-416 PIR sensor | $2 |
| Stepper motor + A4988 driver | $15 |
| Servo motor | $5 |
| USB webcam | $15 |
| LEDs, resistors, jumper wires | $5 |
| Laser module | $3 |
| **Total** | **~$153** |

See [HARDWARE_GUIDE.md](HARDWARE_GUIDE.md) for wiring notes and setup order.

## Sensors

| Module | Hardware | Role |
| --- | --- | --- |
| RF | RTL-SDR | Watches common control/video bands for power anomalies |
| PIR | HW-416 | Detects infrared motion triggers |
| Visual | Webcam + YOLOv8 | Detects candidate objects in the camera feed |
| Distance | HC-SR04 | Estimates short-range distance for the local threat picture |
| Acoustic | Microphone | Adds another signal path for audible drone signatures |

## Fusion Logic

Sensor events are grouped within a 3 second window. The fusion engine raises confidence as independent signals agree with each other.

| Inputs | Confidence | Behavior |
| --- | ---: | --- |
| RF only | 0.30 | Log event |
| PIR only | 0.20 | Log event |
| Acoustic only | 0.25 | Log event |
| RF + PIR | 0.65 | Start gimbal scan, amber status |
| RF + Visual | 0.70 | Start gimbal scan, eligible for Foundry write |
| Acoustic + Visual | 0.92 | High-confidence track |
| RF + PIR + Visual | 0.90 | Lock gimbal/laser, write event |
| RF + PIR + Visual + Acoustic | 0.98 | Highest-confidence track |

Thresholds live in [config.py](config.py).

## Quick Start

Create an environment, install dependencies, then run the app:

```bash
pip install -r requirements.txt
SILVER_DOME_SIMULATE=true python main.py
```

Open the dashboard:

```text
http://localhost:8080
```

Run the scripted demo:

```bash
python demo.py --simulate
```

On the Pi, pass a site id if you want events tagged by location:

```bash
python main.py --site-id FOB-ALPHA
```

## Configuration

Common environment variables:

| Variable | Purpose |
| --- | --- |
| `SILVER_DOME_SIMULATE` | Enables simulated hardware paths |
| `SILVER_DOME_SITE_ID` | Site identifier used in logs and events |
| `SILVER_DOME_DRONE_MODEL` | Uses the drone-specific YOLO model when set to `true` |
| `SILVER_DOME_LOG_LEVEL` | Python logging level, default `INFO` |
| `FOUNDRY_BASE_URL` | Foundry stack URL |
| `FOUNDRY_TOKEN` | Foundry bearer token |
| `FOUNDRY_ONTOLOGY_ID` | Target ontology id |
| `FOUNDRY_OBJECT_TYPE` | Foundry object type, default `ThreatObject` |

Foundry is optional. If credentials are missing, events are logged locally and writes are skipped or queued depending on the client path.

## Project Layout

```text
comms/       Foundry client
dashboard/   Flask dashboard server
display/     LED/status output
fusion/      event correlation and confidence scoring
gimbal/      pan/tilt controller
sensors/     RF, PIR, visual, distance, acoustic sensor modules
tests/       unit tests for fusion, gimbal, and shared models
```

## Tests

```bash
pytest
```

## Notes

- `main.py` currently starts in a laptop-friendly, vision-primary mode.
- The bundled model files are for demo/prototype work and should be validated before any real deployment.
- Laser hardware should be treated carefully during testing. Keep it pointed away from people, reflective surfaces, and aircraft.

Built at Shack15 SF, May 2-3 2026.
