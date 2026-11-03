"""Standard Test workflow (spec items 5-10): trigger, timeout, scheduling,
T60 decision, missing T60 and Emergency Stop - using a fake camera and a
controllable clock."""
import pytest

from app.core.constants import (
    REASON_CAMERA_LOST,
    REASON_EMERGENCY,
    REASON_T60_MISSING,
    REASON_TIMEOUT,
)
from app.database.models import Preset
from app.testing.standard_test import (
    StandardTestController,
    TestStartError,
    build_schedule,
)
from app.thermal.region_calculator import RegionDefinition
from tests.conftest import make_matrix

REGIONS = [
    RegionDefinition("A", 10, 10, 40, 40, "#ff0000", 10.0),  # allowed dT 10
    RegionDefinition("B", 100, 60, 30, 30, "#00ff00", 20.0),  # allowed dT 20
]


@pytest.fixture()
def preset(db):
    pid = db.create_preset("A1", "1.00", REGIONS)
    return db.get_preset(pid)


@pytest.fixture()
def controller(db, qapp, clock):
    return StandardTestController(db, clock=clock)


def _run_to_completion(controller, clock, duration=2.0):
    """Advance the fake clock past the end and tick until finished."""
    for _ in range(40):
        controller.tick()
        if not controller.is_running:
            return
        clock.advance(0.25)


class TestScheduling:
    def test_schedule_10s(self):
        assert build_schedule(10, 60) == [10, 20, 30, 40, 50, 60]

    def test_schedule_5s(self):
        assert build_schedule(5, 60) == [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]

    def test_schedule_15s(self):
        assert build_schedule(15, 60) == [15, 30, 45, 60]

    def test_invalid_interval(self):
        with pytest.raises(ValueError):
            build_schedule(0, 60)


class TestPrerequisites:
    def test_no_preset_regions(self, controller, fake_camera, fast_settings):
        empty = Preset(id=None, device_code="A1", version="1.00", created_at="", regions=[])
        with pytest.raises(TestStartError):
            controller.start(empty, fast_settings, fake_camera)

    def test_camera_not_connected(self, controller, fake_camera, preset, fast_settings):
        fake_camera.state = "disconnected"
        with pytest.raises(TestStartError):
            controller.start(preset, fast_settings, fake_camera)

    def test_non_radiometric_frame(self, controller, fake_camera, preset, fast_settings):
        fake_camera.clear_frame()
        with pytest.raises(TestStartError):
            controller.start(preset, fast_settings, fake_camera)


class TestTemperatureRiseTrigger:
    def test_trigger_starts_measurement_and_sets_reference(self, controller, clock,
                                                           fake_camera, preset,
                                                           fast_settings, db):
        # Baseline: region A max = 28 (dT 3 -> below allowed 10)
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        assert controller.phase == "waiting_rise"

        # Region A now at 34: rise of 6 > threshold 5 -> trigger.
        fake_camera.set_matrix(make_matrix(25.0, [(34.0, 10, 10, 50, 50)]))
        clock.advance(1.0)
        controller.tick()
        assert controller.phase == "measuring"
        record = db.get_test_overview(controller.test_id)
        assert record.reference_timestamp is not None

    def test_second_region_rise_does_not_reset_reference(self, controller, clock,
                                                         fake_camera, preset,
                                                         fast_settings):
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        fake_camera.set_matrix(make_matrix(25.0, [(34.0, 10, 10, 50, 50)]))
        clock.advance(1.0)
        controller.tick()
        first_ref = controller._t_ref
        fake_camera.set_matrix(make_matrix(25.0, [(34.0, 10, 10, 50, 50),
                                                  (50.0, 100, 60, 130, 90)]))
        clock.advance(0.5)
        controller.tick()
        assert controller._t_ref == first_ref


class TestTimeout:
    def test_no_rise_times_out(self, controller, clock, fake_camera, preset,
                               fast_settings, db):
        results = []
        controller.testFinished.connect(lambda r: results.append(r))
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        clock.advance(3.5)  # beyond waiting_timeout of 3 s
        controller.tick()
        assert len(results) == 1
        result = results[0]
        assert result["status"] == "TIMEOUT"
        assert result["reason"] == REASON_TIMEOUT
        assert result["overall_result"] is None  # no thermal FAIL
        record = db.get_test_overview(controller.test_id)
        assert record.status == "TIMEOUT"


class TestFinalResult:
    def test_pass_at_t60(self, controller, clock, fake_camera, preset,
                         fast_settings, db):
        results = []
        controller.testFinished.connect(lambda r: results.append(r))
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        # Rise triggers; region A max 34 -> dT 9 <= 10 PASS; B cool.
        fake_camera.set_matrix(make_matrix(25.0, [(34.0, 10, 10, 50, 50)]))
        _run_to_completion(controller, clock)
        assert len(results) == 1
        assert results[0]["overall_result"] == "PASS"
        assert results[0]["deciding_sample_t"] == 2.0
        # Samples: baseline T0 + T1 + T2.
        details = db.get_test_details(controller.test_id)
        assert [s.sample_time_seconds for s in details["samples"]] == [0.0, 1.0, 2.0]

    def test_fail_at_t60(self, controller, clock, fake_camera, preset,
                         fast_settings):
        results = []
        controller.testFinished.connect(lambda r: results.append(r))
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        # Region A max 40 -> dT 15 > 10 FAIL; B max 42 -> dT 17 <= 20 PASS.
        fake_camera.set_matrix(make_matrix(25.0, [(40.0, 10, 10, 50, 50),
                                                  (42.0, 100, 60, 130, 90)]))
        _run_to_completion(controller, clock)
        result = results[0]
        assert result["overall_result"] == "FAIL"
        failing = [r for r in result["regions"] if r["status"] == "FAIL"]
        assert [r["label"] for r in failing] == ["A"]
        assert failing[0]["exceedance"] == pytest.approx(5.0)
        assert "Region A" in result["reason"]

    def test_earlier_fail_does_not_decide(self, controller, clock, fake_camera,
                                          preset, fast_settings):
        """A FAIL at T1 that recovers by T60 must yield PASS."""
        results = []
        controller.testFinished.connect(lambda r: results.append(r))
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        # Trigger + hot at T1...
        fake_camera.set_matrix(make_matrix(25.0, [(40.0, 10, 10, 50, 50)]))
        clock.advance(1.0)
        controller.tick()  # triggers measurement (rise 12)
        clock.advance(1.0)
        controller.tick()  # T1 recorded FAIL (dT 15)
        # ...cooled down by T2 (dT 9 -> PASS)
        fake_camera.set_matrix(make_matrix(25.0, [(34.0, 10, 10, 50, 50)]))
        clock.advance(1.2)
        controller.tick()
        assert results[0]["overall_result"] == "PASS"


class TestMissingT60:
    def test_missing_final_sample_is_incomplete(self, controller, clock,
                                                fake_camera, preset, fast_settings,
                                                db):
        results = []
        controller.testFinished.connect(lambda r: results.append(r))
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        fake_camera.set_matrix(make_matrix(25.0, [(34.0, 10, 10, 50, 50)]))
        clock.advance(1.0)
        controller.tick()  # trigger
        clock.advance(1.0)
        controller.tick()  # T1 captured
        # Camera frame lost right at the deciding sample:
        fake_camera.clear_frame()
        clock.advance(1.2)
        controller.tick()  # T2 missed
        assert not controller.is_running
        result = results[0]
        assert result["status"] == "INCOMPLETE"
        assert result["reason"] == REASON_T60_MISSING
        # T1 measurements preserved.
        details = db.get_test_details(controller.test_id)
        assert [s.sample_time_seconds for s in details["samples"]] == [0.0, 1.0]


class TestEmergencyStop:
    def test_emergency_stop_saves_and_marks_incomplete(self, controller, clock,
                                                       fake_camera, preset,
                                                       fast_settings, db):
        results = []
        controller.testFinished.connect(lambda r: results.append(r))
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        fake_camera.set_matrix(make_matrix(25.0, [(34.0, 10, 10, 50, 50)]))
        clock.advance(1.0)
        controller.tick()
        clock.advance(0.5)
        controller.emergency_stop()
        assert len(results) == 1
        assert results[0]["status"] == "INCOMPLETE"
        assert results[0]["reason"] == REASON_EMERGENCY
        # Only the baseline exists; no extra final snapshot was taken.
        details = db.get_test_details(controller.test_id)
        assert [s.sample_time_seconds for s in details["samples"]] == [0.0]

    def test_abort_with_reason(self, controller, clock, fake_camera, preset,
                               fast_settings):
        results = []
        controller.testFinished.connect(lambda r: results.append(r))
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        controller.abort("Application Closed During Test")
        assert results[0]["status"] == "INCOMPLETE"
        assert results[0]["reason"] == "Application Closed During Test"


class TestCameraLoss:
    def test_manager_gives_up_marks_incomplete(self, controller, clock,
                                               fake_camera, preset, fast_settings):
        results = []
        controller.testFinished.connect(lambda r: results.append(r))
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        fake_camera.state = "disconnected"
        controller.tick()
        assert results[0]["status"] == "INCOMPLETE"
        assert results[0]["reason"] == REASON_CAMERA_LOST

    def test_disconnect_during_sample_leaves_slot_blank(self, controller, clock,
                                                        fake_camera, preset,
                                                        fast_settings, db):
        """Camera briefly unavailable at T1: slot stays blank, T2 still decides."""
        results = []
        controller.testFinished.connect(lambda r: results.append(r))
        fake_camera.set_matrix(make_matrix(25.0, [(28.0, 10, 10, 50, 50)]))
        controller.start(preset, fast_settings, fake_camera, measurement_duration=2.0)
        fake_camera.set_matrix(make_matrix(25.0, [(34.0, 10, 10, 50, 50)]))
        clock.advance(1.0)
        controller.tick()  # trigger
        # Frame missing exactly at T1 (reconnecting, not given up):
        fake_camera.clear_frame()
        clock.advance(1.0)
        controller.tick()  # T1 missed
        # Frame back before T2:
        fake_camera.set_matrix(make_matrix(25.0, [(34.0, 10, 10, 50, 50)]))
        clock.advance(1.2)
        controller.tick()  # T2 captured on the ORIGINAL schedule
        assert results[0]["overall_result"] == "PASS"
        details = db.get_test_details(controller.test_id)
        slots = [s.sample_time_seconds for s in details["samples"]]
        assert 1.0 not in slots  # blank, not shifted
        assert 2.0 in slots
