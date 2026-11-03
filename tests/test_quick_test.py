"""Quick Test (spec item 11)."""
import pytest

from app.testing.quick_test import QuickTestError, run_quick_test
from app.thermal.region_calculator import RegionDefinition
from tests.conftest import make_matrix

REGIONS = [
    RegionDefinition("A", 10, 10, 40, 40, "#ff0000", 10.0),
    RegionDefinition("B", 100, 60, 30, 30, "#00ff00", 20.0),
]


@pytest.fixture()
def preset(db):
    pid = db.create_preset("A1", "1.00", REGIONS)
    return db.get_preset(pid)


def test_quick_test_pass(fake_camera, preset, fast_settings, db):
    fake_camera.set_matrix(make_matrix(25.0, [(30.0, 10, 10, 50, 50)]))
    result = run_quick_test(fake_camera, preset, fast_settings, db)
    assert result["overall_result"] == "PASS"
    assert result["test_type"] == "quick"
    assert len(result["regions"]) == 2
    record = db.get_test_overview(result["test_id"])
    assert record.test_type == "quick"
    assert record.overall_result == "PASS"
    details = db.get_test_details(result["test_id"])
    assert len(details["samples"]) == 1
    assert details["samples"][0].sample_time_seconds == 0.0


def test_quick_test_fail(fake_camera, preset, fast_settings, db):
    # A max 40 -> dT 15 > 10 FAIL ; B max 42 -> dT 17 <= 20 PASS
    fake_camera.set_matrix(make_matrix(25.0, [(40.0, 10, 10, 50, 50),
                                              (42.0, 100, 60, 130, 90)]))
    result = run_quick_test(fake_camera, preset, fast_settings, db)
    assert result["overall_result"] == "FAIL"
    failing = [r for r in result["regions"] if r["status"] == "FAIL"]
    assert failing[0]["label"] == "A"
    assert failing[0]["delta_t"] == pytest.approx(15.0)
    assert failing[0]["exceedance"] == pytest.approx(5.0)
    assert failing[0]["ambient"] == pytest.approx(25.0)


def test_quick_test_requires_radiometric(fake_camera, preset, fast_settings, db):
    fake_camera.clear_frame()
    with pytest.raises(QuickTestError):
        run_quick_test(fake_camera, preset, fast_settings, db)


def test_quick_test_requires_connection(fake_camera, preset, fast_settings, db):
    fake_camera.state = "disconnected"
    fake_camera.set_matrix(make_matrix(25.0, []))
    with pytest.raises(QuickTestError):
        run_quick_test(fake_camera, preset, fast_settings, db)


def test_result_text_formatting(fake_camera, preset, fast_settings, db):
    from app.testing.result_format import format_result_text

    fake_camera.set_matrix(make_matrix(25.0, [(40.0, 10, 10, 50, 50)]))
    result = run_quick_test(fake_camera, preset, fast_settings, db)
    text = format_result_text(result)
    assert "QUALITY CONTROL: FAIL" in text
    assert "Region A: FAIL" in text
    assert "Measured Max: 40.0 C" in text
    assert "Ambient: 25.0 C" in text
    assert "Measured DeltaT: 15.0 C" in text
    assert "Maximum Allowed DeltaT: 10.0 C" in text
    assert "Exceedance: 5.0 C" in text

    fake_camera.set_matrix(make_matrix(25.0, [(30.0, 10, 10, 50, 50)]))
    passing = run_quick_test(fake_camera, preset, fast_settings, db)
    passing_text = format_result_text(passing)
    assert "QUALITY CONTROL: PASS" in passing_text
    assert "within their allowed temperature limits" in passing_text
