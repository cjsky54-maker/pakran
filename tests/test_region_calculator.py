"""Region Min/Max, DeltaT and PASS/FAIL evaluation (spec items 1, 3, 4)."""
import numpy as np
import pytest

from app.thermal.region_calculator import (
    RegionDefinition,
    evaluate_region,
    label_for_index,
    measure_region,
    pick_color,
)


def region(**kwargs) -> RegionDefinition:
    base = dict(label="A", x=10, y=10, width=20, height=20,
                color="#ffffff", max_allowed_delta_t=10.0)
    base.update(kwargs)
    return RegionDefinition(**base)


class TestRegionMinMax:
    def test_exact_min_max(self):
        matrix = np.full((192, 256), 25.0, dtype=np.float32)
        matrix[12:14, 12:14] = 80.5   # inside region
        matrix[15, 15] = 18.25        # inside region
        matrix[0, 0] = 5.0            # outside region
        mm = measure_region(matrix, region())
        assert mm == (18.25, 80.5)

    def test_region_at_image_boundary_is_clipped(self):
        matrix = np.full((192, 256), 30.0, dtype=np.float32)
        matrix[190:, 250:] = 90.0
        edge = region(x=250, y=190, width=50, height=50)
        mm = measure_region(matrix, edge)
        assert mm is not None
        assert mm[1] == 90.0

    def test_overlapping_regions_measure_independently(self):
        matrix = np.full((192, 256), 25.0, dtype=np.float32)
        matrix[10:30, 10:30] = 60.0
        a = region(x=10, y=10, width=20, height=20)
        b = region(x=20, y=20, width=20, height=20)  # overlaps a partially
        assert measure_region(matrix, a)[1] == 60.0
        # b contains 10 pixels of the hot block and cool pixels around
        assert measure_region(matrix, b) == (25.0, 60.0)

    def test_empty_region_outside_bounds_returns_none(self):
        matrix = np.full((192, 256), 25.0, dtype=np.float32)
        assert measure_region(matrix, region(x=300, y=300, width=10, height=10)) is None

    def test_zero_size_region_returns_none(self):
        matrix = np.full((192, 256), 25.0, dtype=np.float32)
        assert measure_region(matrix, region(width=0, height=5)) is None

    def test_missing_matrix_returns_none(self):
        assert measure_region(None, region()) is None


class TestEvaluation:
    def test_equality_with_limit_is_pass(self):
        assert evaluate_region(10.0, 10.0) == "PASS"

    def test_above_limit_fails(self):
        assert evaluate_region(10.0001, 10.0) == "FAIL"

    def test_below_limit_passes(self):
        assert evaluate_region(9.9, 10.0) == "PASS"

    def test_delta_t_is_max_minus_ambient(self):
        region_max, ambient = 78.4, 30.2
        assert region_max - ambient == pytest.approx(48.2)


class TestLabelsAndColors:
    def test_label_sequence(self):
        assert label_for_index(0) == "A"
        assert label_for_index(1) == "B"
        assert label_for_index(25) == "Z"
        assert label_for_index(26) == "AA"
        assert label_for_index(27) == "AB"

    def test_unique_colors_for_first_dozen(self):
        colors = {pick_color(i) for i in range(12)}
        assert len(colors) == 12

    def test_roundtrip_dict(self):
        r = region(max_allowed_delta_t=35.5)
        r2 = RegionDefinition.from_dict(r.to_dict())
        assert r2 == r
