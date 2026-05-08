"""Tests for the Silver Dome gimbal controller."""
import config
from gimbal.controller import GimbalController


class TestGimbalController:
    def test_initial_bearing(self):
        g = GimbalController()
        assert g.current_bearing == 0.0
        g.cleanup()

    def test_pan_to_90(self):
        g = GimbalController()
        g.pan_to(90.0)
        assert abs(g.current_bearing - 90.0) <= config.STEPPER_DEGREES_PER_STEP
        g.cleanup()

    def test_pan_to_270_shortest_path(self):
        g = GimbalController()
        g.pan_to(270.0)
        # Should take the short path (90 steps CCW = -90 deg equivalent)
        assert abs(g.current_bearing - 270.0) <= config.STEPPER_DEGREES_PER_STEP
        g.cleanup()

    def test_home_resets_to_zero(self):
        g = GimbalController()
        g.pan_to(180.0)
        g.home()
        assert g.current_bearing == 0.0
        g.cleanup()

    def test_current_tilt_default(self):
        g = GimbalController()
        assert g.current_tilt == 90.0
        g.cleanup()

    def test_tilt_to(self):
        g = GimbalController()
        g.tilt_to(45.0)
        assert g.current_tilt == 45.0
        g.cleanup()

    def test_laser_on_off(self):
        g = GimbalController()
        g.laser_on()
        assert g._laser_active is True
        g.laser_off()
        assert g._laser_active is False
        g.cleanup()

    def test_lock_on(self):
        g = GimbalController()
        g.lock_on(bearing_deg=45.0, tilt_deg=60.0)
        assert abs(g.current_bearing - 45.0) <= config.STEPPER_DEGREES_PER_STEP
        assert g.current_tilt == 60.0
        assert g._laser_active is True
        g.cleanup()

    def test_stand_down(self):
        g = GimbalController()
        g.lock_on(bearing_deg=90.0)
        g.stand_down()
        assert g._laser_active is False
        assert g.current_tilt == 90.0
        g.cleanup()

    def test_status_method(self):
        g = GimbalController()
        st = g.status()
        assert st["name"] == "gimbal"
        assert st["simulate"] is True
        assert st["bearing"] == 0.0
        g.cleanup()
