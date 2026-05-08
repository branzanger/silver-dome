#!/usr/bin/env python3
"""
Silver Dome -- Hackathon Demo Controller
=========================================
Scripted 3-minute demo sequence for live judging. Two modes:

  --hardware   (default)  Real sensors; this script provides timing cues.
  --simulate              Injects fake sensor events on a precise timeline.

Usage:
    python demo.py                # hardware mode (timing cues only)
    python demo.py --simulate     # full simulation with injected events
    python demo.py --simulate --site-id FOB-BRAVO
"""

import argparse
import importlib
import logging
import os
import signal
import sys
import threading
import time
from queue import Queue

# ---------------------------------------------------------------------------
# ANSI colours for terminal output
# ---------------------------------------------------------------------------
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_GREEN  = "\033[92m"
_AMBER  = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_WHITE  = "\033[97m"
_BG_GREEN  = "\033[42m"
_BG_AMBER  = "\033[43m"
_BG_RED    = "\033[41m"
_BG_CYAN   = "\033[46m"


# ---------------------------------------------------------------------------
# Demo timeline
# ---------------------------------------------------------------------------
# Each stage: (start_sec, label, colour, description, presenter_note)
STAGES = [
    (  0, "SYSTEM BOOT",       _GREEN, "ALL CLEAR",
     "Show judges the green LEDs and dashboard at http://localhost:8080"),
    ( 15, "RF DETECTED",       _AMBER, "Single RF anomaly",
     "Point at dashboard -- low-confidence blip appears"),
    ( 30, "PIR TRIGGERS",      _AMBER, "RF + PIR correlate",
     "Watch for amber LEDs -- fusion confidence rises"),
    ( 45, "VISUAL CONFIRMS",   _RED,   "Camera classifies target",
     "Confidence hits 0.90. Gimbal pans. LASER LOCKS ON. Red LEDs."),
    ( 60, "FOUNDRY WRITE",     _RED,   "ThreatObject written",
     "\"This threat event is now in Palantir Foundry.\""),
    ( 75, "TRACKING",          _RED,   "System holds lock",
     "Distance readings update. Gimbal tracks. Dashboard shows red dot."),
    ( 90, "THREAT CLEARS",     _GREEN, "Target moves away",
     "Events expire. Green LEDs. Dashboard shows ALL CLEAR."),
    (105, "SECOND DETECTION",  _RED,   "New threat, different bearing",
     "Full cycle: RF -> PIR -> Visual -> Lock-on from another angle"),
    (120, "MULTI-SITE",        _CYAN,  "Change SITE_ID",
     "\"Same kit, different FOB.\" Switch site and show dashboard update."),
    (135, "Q&A BUFFER",        _WHITE, "Open floor",
     "Answer judge questions. System keeps running."),
    (165, "CLOSE",             _DIM,   "Demo complete",
     "Thank the judges. System shuts down cleanly."),
]

DEMO_DURATION = 180  # 3 minutes


# ===================================================================
# Terminal display helpers
# ===================================================================

def _clear_line():
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def _banner():
    print(f"""
{_BOLD}{_WHITE}{'=' * 64}
   SILVER DOME  --  Live Demo Controller
   3-minute scripted sequence for hackathon judging
{'=' * 64}{_RESET}
""")


def _print_stage(elapsed: float, idx: int, stage: tuple):
    _, label, colour, desc, note = stage
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    print()
    print(f"{_BOLD}{colour}[{mins}:{secs:02d}] STAGE {idx + 1}: {label} -- {desc}{_RESET}")
    print(f"  {_DIM}Presenter: {note}{_RESET}")
    print()


def _print_countdown(elapsed: float, next_stage_time: float | None):
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    if next_stage_time is not None:
        remaining = max(0, next_stage_time - elapsed)
        r_secs = int(remaining)
        tag = f"  next stage in {r_secs}s"
    else:
        tag = "  Q&A / wrapping up"
    _clear_line()
    sys.stdout.write(f"  {_DIM}[{mins}:{secs:02d}]{tag}{_RESET}")
    sys.stdout.flush()


# ===================================================================
# System bootstrap (shared by both modes)
# ===================================================================

def _bootstrap_system(simulate: bool, site_id: str | None):
    """
    Initialise all Silver Dome components and return them as a dict.
    In simulate mode, sensor threads (rf, pir) are NOT started -- the
    demo script injects events directly into the queue instead.
    """
    # Force environment before importing config
    if simulate:
        os.environ["SILVER_DOME_SIMULATE"] = "true"
    os.environ.setdefault("SILVER_DOME_SIMULATE", "false")

    import config as _cfg
    importlib.reload(_cfg)
    _cfg.SIMULATE = simulate or _cfg.SIMULATE
    if site_id:
        _cfg.SITE_ID = site_id

    # Shorten the clear timeout so the demo cycles faster
    _cfg.CLEAR_TIMEOUT_SEC = 5.0

    from sensors.rf_sensor import RFSensor
    from sensors.pir_sensor import PIRSensor
    from sensors.visual_sensor import VisualSensor
    from sensors.distance_sensor import DistanceSensor
    from fusion.engine import FusionEngine
    from gimbal.controller import GimbalController
    from comms.foundry_client import FoundryClient
    from display.indicators import StatusIndicator
    from dashboard.server import DashboardServer

    event_queue = Queue()

    gimbal = GimbalController()
    gimbal.home()

    indicators = StatusIndicator()
    indicators.set_all_clear()

    visual = VisualSensor()
    distance = DistanceSensor()
    foundry = FoundryClient()

    dashboard = DashboardServer(port=8080)
    dashboard.start()

    rf = RFSensor()
    pir = PIRSensor()

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
    _log = logging.getLogger("silver-dome")
    _log.info("=" * 60)
    _log.info("  PRE-FLIGHT HEALTH CHECK")
    _log.info("=" * 60)
    _preflight_components = {
        "rf": rf, "pir": pir, "visual": visual, "distance": distance,
        "gimbal": gimbal, "indicators": indicators, "foundry": foundry,
    }
    for _name, _comp in _preflight_components.items():
        try:
            if hasattr(_comp, "status"):
                _st = _comp.status()
                _sim = _st.get("simulate", _st.get("sim", False))
                _hw = _st.get("hardware", "")
                _log.info("%-15s %-10s %s", _name, "SIM" if _sim else "OK", _hw)
            else:
                _log.info("%-15s %-10s", _name, "NO STATUS")
        except Exception as _e:
            _log.error("%-15s %-10s %s", _name, "FAIL", _e)
    _log.info("=" * 60)

    # Register components for /api/status
    for _name, _comp in _preflight_components.items():
        dashboard.register_component(_name, _comp)
    dashboard.register_component("fusion", fusion)

    # Always start the fusion engine
    threads = []
    fusion_thread = threading.Thread(target=fusion.run, name="fusion-engine", daemon=True)
    fusion_thread.start()
    threads.append(("Fusion Engine", fusion_thread))

    # In hardware mode, start the real sensor threads.
    # In simulate mode, skip them -- we inject events manually.
    if not simulate:
        rf_thread = threading.Thread(target=rf.run, args=(event_queue,),
                                     name="rf-sensor", daemon=True)
        rf_thread.start()
        threads.append(("RF Sensor", rf_thread))

        pir_thread = threading.Thread(target=pir.run, args=(event_queue,),
                                      name="pir-sensor", daemon=True)
        pir_thread.start()
        threads.append(("PIR Sensor", pir_thread))

    return {
        "event_queue": event_queue,
        "gimbal": gimbal,
        "indicators": indicators,
        "visual": visual,
        "distance": distance,
        "foundry": foundry,
        "dashboard": dashboard,
        "rf": rf,
        "pir": pir,
        "fusion": fusion,
        "threads": threads,
        "config": _cfg,
    }


def _teardown(ctx: dict):
    """Graceful shutdown of all components."""
    ctx["rf"].stop()
    ctx["pir"].stop()
    ctx["fusion"].stop()
    ctx["visual"].release()
    ctx["distance"].cleanup()
    ctx["gimbal"].stand_down()
    ctx["indicators"].cleanup()
    ctx["gimbal"].cleanup()
    ctx["dashboard"].stop()
    ctx["foundry"].flush_queue()

    for name, thread in ctx["threads"]:
        thread.join(timeout=3.0)


# ===================================================================
# Simulate-mode event injection
# ===================================================================

def _inject_events(ctx: dict, stop_event: threading.Event):
    """
    Inject precisely-timed fake SensorEvents into the event queue
    to produce a perfect demo without any real hardware.
    """
    from models import SensorEvent

    q: Queue = ctx["event_queue"]
    cfg = ctx["config"]
    t0 = time.time()

    def elapsed():
        return time.time() - t0

    def wait_until(target_sec: float) -> bool:
        """Sleep until target time. Returns True if stop was requested."""
        remaining = target_sec - elapsed()
        if remaining > 0:
            return stop_event.wait(timeout=remaining)
        return stop_event.is_set()

    # ------------------------------------------------------------------
    # THREAT 1: bearing ~45 degrees, range ~2.1m
    # ------------------------------------------------------------------

    # 0:15 -- RF anomaly (low confidence)
    if wait_until(15):
        return
    q.put(SensorEvent(
        sensor_type="rf",
        timestamp=time.time(),
        confidence=0.55,
        bearing_hint=45.0,
        metadata={
            "signal_strength_db": 14.0,
            "frequency_mhz": 2420.0,
            "simulated": True,
        },
    ))

    # Keep RF alive with periodic pings
    for t in (18, 21, 24, 27):
        if wait_until(t):
            return
        q.put(SensorEvent(
            sensor_type="rf",
            timestamp=time.time(),
            confidence=0.60 + (t - 15) * 0.02,
            bearing_hint=45.0 + (t - 15) * 0.3,
            metadata={
                "signal_strength_db": 14.0 + (t - 15) * 0.5,
                "frequency_mhz": 2420.0,
                "simulated": True,
            },
        ))

    # 0:30 -- PIR triggers (motion detected, RF + PIR correlate)
    if wait_until(30):
        return
    q.put(SensorEvent(
        sensor_type="pir",
        timestamp=time.time(),
        confidence=1.0,
        metadata={"pin_state": 1, "simulated": True},
    ))
    # Sustain RF so RF+PIR window overlaps
    q.put(SensorEvent(
        sensor_type="rf",
        timestamp=time.time(),
        confidence=0.72,
        bearing_hint=47.0,
        metadata={
            "signal_strength_db": 18.5,
            "frequency_mhz": 2420.0,
            "simulated": True,
        },
    ))

    # Keep both alive through the visual confirmation window
    for t in (33, 36, 39, 42):
        if wait_until(t):
            return
        q.put(SensorEvent(
            sensor_type="rf",
            timestamp=time.time(),
            confidence=0.75,
            bearing_hint=46.0,
            metadata={
                "signal_strength_db": 19.0,
                "frequency_mhz": 2420.0,
                "simulated": True,
            },
        ))
        q.put(SensorEvent(
            sensor_type="pir",
            timestamp=time.time(),
            confidence=1.0,
            metadata={"pin_state": 1, "simulated": True},
        ))

    # 0:45 -- Visual confirmation (camera/YOLO classifies target)
    #         bearing_hint is offset from camera center, ~7 degrees
    if wait_until(45):
        return
    q.put(SensorEvent(
        sensor_type="visual",
        timestamp=time.time(),
        confidence=0.88,
        bearing_hint=7.5,   # offset from gimbal center -> absolute ~52.5 deg
        metadata={
            "yolo_class": "person",
            "yolo_class_id": 0,
            "bbox": [240.0, 120.0, 160.0, 280.0],
            "frame_x_center": 358.7,
            "frame_width": 640,
            "simulated": True,
        },
    ))
    # Sustain RF + PIR so all three are in the fusion window
    q.put(SensorEvent(
        sensor_type="rf",
        timestamp=time.time(),
        confidence=0.80,
        bearing_hint=46.0,
        metadata={
            "signal_strength_db": 22.0,
            "frequency_mhz": 2420.0,
            "simulated": True,
        },
    ))
    q.put(SensorEvent(
        sensor_type="pir",
        timestamp=time.time(),
        confidence=1.0,
        metadata={"pin_state": 1, "simulated": True},
    ))

    # 0:50-1:00 -- Keep all three sensors firing for sustained lock
    for t in (50, 53, 56, 59):
        if wait_until(t):
            return
        q.put(SensorEvent(
            sensor_type="rf",
            timestamp=time.time(),
            confidence=0.82,
            bearing_hint=46.5,
            metadata={
                "signal_strength_db": 22.5,
                "frequency_mhz": 2420.0,
                "simulated": True,
            },
        ))
        q.put(SensorEvent(
            sensor_type="pir",
            timestamp=time.time(),
            confidence=1.0,
            metadata={"pin_state": 1, "simulated": True},
        ))
        q.put(SensorEvent(
            sensor_type="visual",
            timestamp=time.time(),
            confidence=0.90,
            bearing_hint=6.8,
            metadata={
                "yolo_class": "person",
                "bbox": [245.0, 115.0, 155.0, 285.0],
                "frame_x_center": 355.0,
                "frame_width": 640,
                "simulated": True,
            },
        ))

    # 1:00-1:15 -- FOUNDRY WRITE window (fusion engine handles the write
    #              automatically when confidence >= threshold; we just keep
    #              events alive)
    for t in (62, 65, 68, 71, 74):
        if wait_until(t):
            return
        q.put(SensorEvent(
            sensor_type="rf",
            timestamp=time.time(),
            confidence=0.80,
            bearing_hint=46.0,
            metadata={
                "signal_strength_db": 21.0,
                "frequency_mhz": 2420.0,
                "simulated": True,
            },
        ))
        q.put(SensorEvent(
            sensor_type="pir",
            timestamp=time.time(),
            confidence=1.0,
            metadata={"pin_state": 1, "simulated": True},
        ))

    # 1:15-1:30 -- TRACKING: sustain lock, distance readings drift
    for t in (77, 80, 83, 86, 89):
        if wait_until(t):
            return
        q.put(SensorEvent(
            sensor_type="rf",
            timestamp=time.time(),
            confidence=0.78,
            bearing_hint=47.0 + (t - 77) * 0.2,
            metadata={
                "signal_strength_db": 20.0 - (t - 77) * 0.3,
                "frequency_mhz": 2420.0,
                "simulated": True,
            },
        ))
        q.put(SensorEvent(
            sensor_type="pir",
            timestamp=time.time(),
            confidence=1.0,
            metadata={"pin_state": 1, "simulated": True},
        ))

    # 1:30-1:45 -- THREAT CLEARS: stop injecting events.
    #              Fusion window (3s) + clear timeout (5s) will expire.
    #              Wait for the system to return to ALL CLEAR.
    if wait_until(90):
        return
    # No more events -- let the window drain

    # ------------------------------------------------------------------
    # THREAT 2: bearing ~270 degrees (opposite side), range ~1.8m
    # ------------------------------------------------------------------

    # 1:45 -- RF from new bearing
    if wait_until(105):
        return
    q.put(SensorEvent(
        sensor_type="rf",
        timestamp=time.time(),
        confidence=0.60,
        bearing_hint=270.0,
        metadata={
            "signal_strength_db": 16.0,
            "frequency_mhz": 2420.0,
            "simulated": True,
        },
    ))

    # 1:48 -- PIR
    if wait_until(108):
        return
    q.put(SensorEvent(
        sensor_type="pir",
        timestamp=time.time(),
        confidence=1.0,
        metadata={"pin_state": 1, "simulated": True},
    ))
    q.put(SensorEvent(
        sensor_type="rf",
        timestamp=time.time(),
        confidence=0.70,
        bearing_hint=272.0,
        metadata={
            "signal_strength_db": 19.0,
            "frequency_mhz": 2420.0,
            "simulated": True,
        },
    ))

    # 1:51 -- Visual from second bearing (offset ~-8 degrees from center)
    if wait_until(111):
        return
    q.put(SensorEvent(
        sensor_type="visual",
        timestamp=time.time(),
        confidence=0.85,
        bearing_hint=-8.2,  # left of center -> absolute ~264 deg
        metadata={
            "yolo_class": "cell phone",
            "yolo_class_id": 67,
            "bbox": [100.0, 180.0, 80.0, 120.0],
            "frame_x_center": 236.5,
            "frame_width": 640,
            "simulated": True,
        },
    ))
    q.put(SensorEvent(
        sensor_type="rf",
        timestamp=time.time(),
        confidence=0.78,
        bearing_hint=271.0,
        metadata={
            "signal_strength_db": 21.0,
            "frequency_mhz": 2420.0,
            "simulated": True,
        },
    ))
    q.put(SensorEvent(
        sensor_type="pir",
        timestamp=time.time(),
        confidence=1.0,
        metadata={"pin_state": 1, "simulated": True},
    ))

    # Sustain second threat through 2:00
    for t in (114, 117):
        if wait_until(t):
            return
        q.put(SensorEvent(
            sensor_type="rf",
            timestamp=time.time(),
            confidence=0.80,
            bearing_hint=270.5,
            metadata={
                "signal_strength_db": 20.0,
                "frequency_mhz": 2420.0,
                "simulated": True,
            },
        ))
        q.put(SensorEvent(
            sensor_type="pir",
            timestamp=time.time(),
            confidence=1.0,
            metadata={"pin_state": 1, "simulated": True},
        ))
        q.put(SensorEvent(
            sensor_type="visual",
            timestamp=time.time(),
            confidence=0.87,
            bearing_hint=-7.5,
            metadata={
                "yolo_class": "cell phone",
                "bbox": [105.0, 175.0, 82.0, 118.0],
                "frame_x_center": 240.0,
                "frame_width": 640,
                "simulated": True,
            },
        ))

    # 2:00-2:15 -- MULTI-SITE: change SITE_ID mid-run
    if wait_until(120):
        return
    cfg = ctx["config"]
    original_site = cfg.SITE_ID
    new_site = "FOB-BRAVO" if cfg.SITE_ID != "FOB-BRAVO" else "FOB-CHARLIE"
    cfg.SITE_ID = new_site
    print(f"\n  {_BOLD}{_CYAN}>>> SITE_ID changed: {original_site} -> {new_site}{_RESET}")

    # One more event burst under the new site ID
    if wait_until(123):
        return
    q.put(SensorEvent(
        sensor_type="rf",
        timestamp=time.time(),
        confidence=0.65,
        bearing_hint=180.0,
        metadata={
            "signal_strength_db": 15.0,
            "frequency_mhz": 2420.0,
            "simulated": True,
        },
    ))

    # Let events drain for the rest of the demo
    wait_until(DEMO_DURATION)


# ===================================================================
# Main demo loop
# ===================================================================

def run_demo(mode: str, site_id: str | None):
    """Run the full 3-minute demo sequence."""
    simulate = mode == "simulate"

    _banner()
    print(f"  Mode    : {_BOLD}{'SIMULATE (injected events)' if simulate else 'HARDWARE (real sensors)'}{_RESET}")
    print(f"  Site    : {_BOLD}{site_id or 'FOB-ALPHA (default)'}{_RESET}")
    print(f"  Duration: {_BOLD}{DEMO_DURATION}s (3 minutes){_RESET}")
    print()

    # --- Suppress noisy library logs; keep fusion visible ---
    logging.basicConfig(
        level=logging.INFO,
        format=f"{_DIM}[%(name)-20s] %(message)s{_RESET}",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("silver_dome_demo.log"),
        ],
    )
    # Quiet down chatty loggers
    for name in ("werkzeug", "engineio", "socketio",
                 "silver_dome.rf_sensor", "silver_dome.pir_sensor"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # --- Bootstrap ---
    print(f"  {_DIM}Initializing system...{_RESET}")
    ctx = _bootstrap_system(simulate=simulate, site_id=site_id)
    print(f"  {_GREEN}System online.{_RESET}  Dashboard: {_BOLD}http://localhost:8080{_RESET}")
    print()
    time.sleep(1)

    # --- Shutdown signal ---
    stop_event = threading.Event()

    def handle_signal(sig, frame):
        print(f"\n\n  {_RED}Demo aborted (Ctrl+C).{_RESET}")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # --- Start simulate injection thread ---
    inject_thread = None
    if simulate:
        inject_thread = threading.Thread(
            target=_inject_events, args=(ctx, stop_event),
            name="demo-inject", daemon=True,
        )
        inject_thread.start()

    # --- Main timeline loop ---
    t0 = time.time()
    stage_idx = 0
    last_stage_printed = -1

    try:
        while not stop_event.is_set():
            elapsed = time.time() - t0

            if elapsed >= DEMO_DURATION:
                break

            # Check if we've reached the next stage
            if stage_idx < len(STAGES):
                stage_time = STAGES[stage_idx][0]
                if elapsed >= stage_time and stage_idx != last_stage_printed:
                    _print_stage(elapsed, stage_idx, STAGES[stage_idx])
                    last_stage_printed = stage_idx
                    stage_idx += 1

            # Countdown ticker (update every second)
            next_time = STAGES[stage_idx][0] if stage_idx < len(STAGES) else None
            _print_countdown(elapsed, next_time)

            # Sleep in short increments so Ctrl+C is responsive
            stop_event.wait(timeout=0.5)

    except KeyboardInterrupt:
        pass

    # --- Wrap up ---
    _clear_line()
    elapsed = time.time() - t0
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    print(f"\n\n{_BOLD}{_GREEN}[{mins}:{secs:02d}] Demo complete.{_RESET}")
    print(f"  {_DIM}Shutting down gracefully...{_RESET}")

    stop_event.set()
    if inject_thread and inject_thread.is_alive():
        inject_thread.join(timeout=5)

    _teardown(ctx)
    print(f"  {_GREEN}All systems shut down. Clean exit.{_RESET}\n")


# ===================================================================
# CLI
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Silver Dome -- Hackathon Demo Controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo.py --hardware          Real sensors, timing cues on screen
  python demo.py --simulate          Injected events, no hardware needed
  python demo.py --simulate --site-id FOB-BRAVO
        """,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--hardware", action="store_const", const="hardware", dest="mode",
        help="Use real sensors; display timing cues for presenter (default)",
    )
    group.add_argument(
        "--simulate", action="store_const", const="simulate", dest="mode",
        help="Inject fake sensor events on a scripted timeline",
    )
    parser.add_argument(
        "--site-id", default=None,
        help="Override SITE_ID (default: FOB-ALPHA)",
    )
    parser.set_defaults(mode="hardware")

    args = parser.parse_args()
    run_demo(mode=args.mode, site_id=args.site_id)


if __name__ == "__main__":
    main()
