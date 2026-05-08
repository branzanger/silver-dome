#!/usr/bin/env python3
"""
Silver Dome — Portable Drone Early-Warning System
Main orchestrator: starts all sensor threads, fusion engine, gimbal, and display.

Usage:
    python main.py                    # Run with real hardware
    SILVER_DOME_SIMULATE=true python main.py  # Simulation mode (no hardware)
"""
import argparse
import logging
import os
import signal
import sys
import threading
from queue import Queue

import config
from sensors.rf_sensor import RFSensor
from sensors.pir_sensor import PIRSensor
from sensors.visual_sensor import VisualSensor
from sensors.distance_sensor import DistanceSensor
from sensors.drone_detector import DroneDetector
from sensors.acoustic_sensor import AcousticSensor
from fusion.engine import FusionEngine
from gimbal.controller import GimbalController
from comms.foundry_client import FoundryClient
from display.indicators import StatusIndicator
from dashboard.server import DashboardServer


def setup_logging():
    log_format = "[%(asctime)s] %(levelname)-7s %(name)-20s %(message)s"
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_FILE),
        ],
    )


def preflight_check(components: dict, log: logging.Logger) -> None:
    """Run status() on each component and print a summary table."""
    log.info("=" * 60)
    log.info("  PRE-FLIGHT HEALTH CHECK")
    log.info("=" * 60)

    results = []
    for name, comp in components.items():
        try:
            if hasattr(comp, "status"):
                st = comp.status()
                sim = st.get("simulate", st.get("sim", False))
                hw = st.get("hardware", "")
                results.append((name, "SIM" if sim else "OK", hw))
            else:
                results.append((name, "NO STATUS", ""))
        except Exception as e:
            log.error("Preflight FAIL for %s: %s", name, e)
            results.append((name, "FAIL", str(e)))

    if not config.FOUNDRY_TOKEN:
        log.warning("FOUNDRY_TOKEN not set -- Foundry writes will be queued locally")
    if not config.FOUNDRY_ONTOLOGY_ID:
        log.warning("FOUNDRY_ONTOLOGY_ID not set -- configure via foundry_setup.py")

    log.info("%-15s %-10s %s", "COMPONENT", "STATUS", "HARDWARE")
    log.info("-" * 60)
    for name, status, hw in results:
        log.info("%-15s %-10s %s", name, status, hw)
    log.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Silver Dome — Drone Early-Warning System")
    parser.add_argument("--simulate", action="store_true", help="Run in simulation mode")
    parser.add_argument("--site-id", default=None, help="Override site ID")
    args = parser.parse_args()

    # Force simulate mode for non-vision sensors (laptop mode)
    os.environ["SILVER_DOME_SIMULATE"] = "true"
    config.SIMULATE = True

    if args.site_id:
        config.SITE_ID = args.site_id

    setup_logging()
    log = logging.getLogger("silver-dome")

    log.info("=" * 60)
    log.info("  SILVER DOME — Drone Early-Warning System")
    log.info("  Site: %s | Mode: VISION-PRIMARY (laptop)", config.SITE_ID)
    log.info("=" * 60)

    # Shared event queue — all sensors publish here
    event_queue = Queue()

    # --- Initialize components ---
    log.info("Initializing components...")

    gimbal = GimbalController()
    gimbal.home()

    indicators = StatusIndicator()
    indicators.set_all_clear()

    visual = VisualSensor()
    distance = DistanceSensor()

    foundry = FoundryClient()

    # --- Dashboard ---
    dashboard = DashboardServer(port=8080)
    dashboard.start()
    log.info("Dashboard: %s", dashboard.url)

    # --- Drone Detector (vision-primary) ---
    drone_detector = DroneDetector()
    log.info("Drone detector: %s", drone_detector.status())

    # --- Acoustic Sensor (microphone) ---
    acoustic = AcousticSensor()
    log.info("Acoustic sensor: %s", acoustic.status())

    fusion = FusionEngine(
        event_queue=event_queue,
        gimbal_controller=gimbal,
        visual_sensor=visual,
        distance_sensor=distance,
        status_indicator=indicators,
        foundry_client=foundry,
        dashboard=dashboard,
    )

    # --- Pre-flight health check ---
    preflight_check({
        "drone_detector": drone_detector, "acoustic": acoustic,
        "visual": visual, "distance": distance,
        "gimbal": gimbal, "indicators": indicators, "foundry": foundry,
    }, log)

    # --- Register components for /api/status health endpoint ---
    dashboard.register_component("drone_detector", drone_detector)
    dashboard.register_component("acoustic", acoustic)
    dashboard.register_component("visual", visual)
    dashboard.register_component("distance", distance)
    dashboard.register_component("gimbal", gimbal)
    dashboard.register_component("indicators", indicators)
    dashboard.register_component("foundry", foundry)
    dashboard.register_component("fusion", fusion)

    # --- Start threads ---
    threads = []

    log.info("Starting Drone Detector thread (webcam + YOLOv8)...")
    drone_thread = threading.Thread(
        target=drone_detector.run, args=(event_queue,),
        name="drone-detector", daemon=True
    )
    drone_thread.start()
    threads.append(("Drone Detector", drone_thread, drone_detector))

    log.info("Starting Acoustic Sensor thread (microphone + FFT)...")
    acoustic_thread = threading.Thread(
        target=acoustic.run, args=(event_queue,),
        name="acoustic-sensor", daemon=True
    )
    acoustic_thread.start()
    threads.append(("Acoustic Sensor", acoustic_thread, acoustic))

    log.info("Starting Fusion Engine thread...")
    fusion_thread = threading.Thread(target=fusion.run, name="fusion-engine", daemon=True)
    fusion_thread.start()
    threads.append(("Fusion Engine", fusion_thread, fusion))

    log.info("All systems online. Monitoring for Shahed drones...")
    log.info("-" * 60)

    # --- Graceful shutdown ---
    shutdown_event = threading.Event()

    def signal_handler(sig, frame):
        log.info("Shutdown signal received. Stopping...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(1.0)
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down...")

    # Stop all components
    log.info("Stopping components...")
    drone_detector.release()
    acoustic.cleanup()
    fusion.stop()
    visual.release()
    distance.cleanup()
    gimbal.stand_down()
    indicators.cleanup()
    gimbal.cleanup()
    dashboard.stop()

    # Wait for threads
    for name, thread, _ in threads:
        thread.join(timeout=3.0)
        if thread.is_alive():
            log.warning("%s thread did not exit cleanly", name)
        else:
            log.info("%s stopped.", name)

    # Flush any queued Foundry writes
    flushed = foundry.flush_queue()
    if flushed > 0:
        log.info("Flushed %d queued threat objects to Foundry", flushed)

    log.info("Silver Dome shut down. All clear.")


if __name__ == "__main__":
    main()
