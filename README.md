# Silver Dome

**Portable Drone Early-Warning System** — 3rd Annual National Security Hackathon (Army xTech / Cerebral Valley)

A man-portable, self-contained drone early-warning and targeting kit that any forward unit can carry and deploy in under 10 minutes.

## The Problem

Forward Operating Positions, temporary command posts, and makeshift operations centers have **no organic early warning** against loitering munitions like the Shahed-136. High-end air defense (Patriot, THAAD) can't be allocated to every small unit. Soldiers die because no one had early warning.

## The Solution

Four-modality sensor fusion (RF + Infrared + Visual + Distance) that detects, classifies, ranges, and tracks aerial threats. When sensors correlate, a pan/tilt laser gimbal physically locks onto the threat bearing. The full threat picture syncs to Palantir Foundry for operator awareness.

## Why This Matters: Cost

A Patriot intercept costs **$4 million**. A Shahed-136 drone costs **$20,000**. You cannot win an attrition war at that ratio.

Silver Dome costs **under $200** in hardware:

| Component | Cost |
|-----------|------|
| Raspberry Pi 5 | $80 |
| RTL-SDR USB dongle | $25 |
| HC-SR04 ultrasonic sensor | $3 |
| HW-416 PIR sensor | $2 |
| Stepper motor + A4988 driver | $15 |
| Servo motor | $5 |
| USB webcam | $15 |
| LEDs, resistors, wires | $5 |
| Laser module | $3 |
| **Total** | **~$153** |

A $153 kit that gives any squad organic early warning. No dedicated operator. No cloud dependency. Deploy in under 10 minutes. The software is free and open source.

Scale: 1,000 kits = $153,000. One Patriot intercept = $4,000,000. You could field **26 Silver Dome kits** for the cost of a single missile.

## Sensor Array

| Sensor | Technology | Detection Method |
|--------|-----------|-----------------|
| **RF** | RTL-SDR (RTL2832U) | 2.4 GHz power anomaly detection via FFT |
| **IR** | HW-416 PIR | Passive infrared heat signature |
| **Visual** | Camera + YOLOv8 | Object detection and classification |
| **Distance** | HC-SR04 Ultrasonic | Range measurement for 2D threat picture |

## Sensor Fusion

Events from different sensors within a 3-second window are correlated:

| Combination | Confidence | Action |
|-------------|-----------|--------|
| RF only | 0.30 | Log locally |
| PIR only | 0.20 | Log locally |
| RF + PIR | 0.65 | Gimbal scan, LED amber |
| RF + Visual | 0.70 | Gimbal scan, write to Foundry |
| RF + PIR + Visual | 0.90 | Laser lock-on, write to Foundry |

## Hardware

- **Compute**: Raspberry Pi 5
- **Pan**: Stepper motor (360° with degree tracking)
- **Tilt**: Servo motor
- **Targeting**: Laser pointer on gimbal
- **Indicators**: Green/Amber/Red LEDs

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run in simulation mode (no hardware required)
SILVER_DOME_SIMULATE=true python main.py

# Run with hardware on Raspberry Pi
python main.py --site-id FOB-ALPHA
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SILVER_DOME_SIMULATE` | Set to `true` for simulation mode |
| `SILVER_DOME_SITE_ID` | Deployment site identifier |
| `FOUNDRY_BASE_URL` | Palantir Foundry stack URL |
| `FOUNDRY_TOKEN` | Foundry API bearer token |
| `FOUNDRY_ONTOLOGY_ID` | Target ontology ID |
| `FOUNDRY_OBJECT_TYPE` | Object type (default: ThreatObject) |

## Architecture

```
Phone/Drone → [RF Sensor] ──┐
             [PIR Sensor] ──┤→ Fusion Engine → Gimbal Lock-On → Palantir Foundry
             [Camera+YOLO] ─┤        ↓              ↓
             [HC-SR04 Dist] ┘   LED Status      Laser Target
                                    ↓
                            Web Dashboard (:8080)
```

## Live Dashboard

A self-hosted radar display runs on the Pi at `http://<pi-ip>:8080`. Zero cloud dependency. Shows real-time 2D polar radar view, sensor status, threat log, and confidence scoring. Designed to work air-gapped in contested environments.

## Demo

```bash
# Scripted 3-minute demo (simulated sensors)
python demo.py --simulate

# Full system with dashboard
SILVER_DOME_SIMULATE=true python main.py
# Then open http://localhost:8080
```

## Team

Built at Shack15 SF, May 2-3 2026.
