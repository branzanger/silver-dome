"""Shared fixtures for Silver Dome test suite."""
import os
import sys
import importlib

import pytest

# Ensure simulation mode before any Silver Dome imports
os.environ["SILVER_DOME_SIMULATE"] = "true"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
importlib.reload(config)

from queue import Queue


@pytest.fixture
def event_queue():
    return Queue()


@pytest.fixture
def stub_gimbal():
    class StubGimbal:
        _bearing = 0.0
        _tilt_angle = 90.0

        @property
        def current_bearing(self):
            return self._bearing

        @property
        def current_tilt(self):
            return self._tilt_angle

        def pan_to(self, deg):
            self._bearing = deg % 360.0

        def laser_on(self): pass
        def laser_off(self): pass

        def lock_on(self, bearing_deg=0.0, tilt_deg=90.0):
            self._bearing = bearing_deg % 360.0
            self._tilt_angle = tilt_deg

        def stand_down(self): pass
        def home(self): pass
    return StubGimbal()


@pytest.fixture
def stub_visual():
    class StubVisual:
        def scan(self):
            return None
        def release(self): pass
        def status(self):
            return {"name": "visual", "simulate": True}
    return StubVisual()


@pytest.fixture
def stub_foundry():
    class StubFoundry:
        written = []
        def write_threat(self, threat):
            self.written.append(threat)
        def status(self):
            return {"name": "foundry", "simulate": True}
        def flush_queue(self):
            return 0
    return StubFoundry()


@pytest.fixture
def stub_indicators():
    from display.indicators import StatusIndicator
    return StatusIndicator()
