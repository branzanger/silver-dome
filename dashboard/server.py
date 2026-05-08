"""
Silver Dome -- Real-time web dashboard server.
Flask + Flask-SocketIO for WebSocket push to browser clients.
"""
import json
import logging
import threading
from pathlib import Path

import time

from flask import Flask, render_template, Response
from flask_socketio import SocketIO

import config

log = logging.getLogger("silver-dome.dashboard")

_TEMPLATE_DIR = Path(__file__).parent / "templates"


class DashboardServer:
    """Lightweight web dashboard that broadcasts system state over WebSocket."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self._thread: threading.Thread | None = None
        self._app = Flask(__name__, template_folder=str(_TEMPLATE_DIR))
        self._app.config["SECRET_KEY"] = "silver-dome-internal"
        self._app.config["TEMPLATES_AUTO_RELOAD"] = True
        self._sio = SocketIO(self._app, async_mode="threading", cors_allowed_origins="*")
        self._clients: int = 0
        self._last_state: dict | None = None
        self._components: dict[str, object] = {}
        self._register_routes()
        self._register_events()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    def _register_routes(self):
        @self._app.route("/")
        def index():
            return render_template("index.html")

        @self._app.route("/health")
        def health():
            return {"status": "ok", "clients": self._clients, "mode": "simulate" if config.SIMULATE else "live"}

        @self._app.route("/video_feed")
        def video_feed():
            """MJPEG stream from the drone detector camera."""
            detector = self._components.get("drone_detector")
            if detector is None or not hasattr(detector, "get_latest_jpeg"):
                return "No camera available", 503

            def generate():
                while True:
                    jpeg = detector.get_latest_jpeg()
                    if jpeg is not None:
                        yield (b"--frame\r\n"
                               b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
                    time.sleep(0.15)  # ~7 fps — match inference rate, reduce CPU

            return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

        @self._app.route("/api/status")
        def api_status():
            components = {}
            for name, comp in self._components.items():
                try:
                    if hasattr(comp, "status"):
                        components[name] = comp.status()
                    else:
                        components[name] = {"registered": True}
                except Exception as e:
                    components[name] = {"error": str(e)}
            return {
                "system": "silver-dome",
                "timestamp": time.time(),
                "mode": "simulate" if config.SIMULATE else "live",
                "site_id": config.SITE_ID,
                "dashboard_clients": self._clients,
                "components": components,
            }

    # ------------------------------------------------------------------
    # WebSocket events
    # ------------------------------------------------------------------
    def _register_events(self):
        @self._sio.on("connect")
        def on_connect():
            self._clients += 1
            log.info("Dashboard client connected (%d total)", self._clients)
            # Send current state immediately so new clients don't see a blank screen
            if self._last_state is not None:
                self._sio.emit("state", self._last_state)

        @self._sio.on("disconnect")
        def on_disconnect():
            self._clients = max(0, self._clients - 1)
            log.info("Dashboard client disconnected (%d remaining)", self._clients)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register_component(self, name: str, component):
        """Register a component that exposes a status() method."""
        self._components[name] = component

    def start(self):
        """Start the server in a daemon background thread."""
        if self._thread and self._thread.is_alive():
            log.warning("Dashboard server already running")
            return

        def _run():
            log.info(
                "Dashboard server starting on http://%s:%d", self.host, self.port
            )
            # use_reloader=False is critical when running inside another app
            self._sio.run(
                self._app,
                host=self.host,
                port=self.port,
                debug=False,
                use_reloader=False,
                allow_unsafe_werkzeug=True,
                log_output=False,
            )

        self._thread = threading.Thread(target=_run, name="dashboard-server", daemon=True)
        self._thread.start()

    def stop(self):
        """Request the server to shut down."""
        log.info("Dashboard server shutting down")
        try:
            self._sio.stop()
        except RuntimeError:
            pass  # "Working outside of request context" on shutdown — harmless

    def push_state(self, state: dict):
        """Broadcast a state snapshot to every connected WebSocket client."""
        self._last_state = state
        self._sio.emit("state", state)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def connected_clients(self) -> int:
        return self._clients
