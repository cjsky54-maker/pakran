"""Ambient calculation and matrix validation (spec item 2)."""
import numpy as np
import pytest

from app.thermal.temperature_processor import compute_ambient, validate_matrix


def test_ambient_is_matrix_minimum():
    matrix = np.full((192, 256), 24.3, dtype=np.float32)
    matrix[100, 100] = 150.0
    matrix[5, 5] = 21.7
    assert compute_ambient(matrix) == pytest.approx(21.7)


def test_ambient_full_precision_float32():
    matrix = np.array([[25.123456, 25.654321]], dtype=np.float32)
    assert compute_ambient(matrix) == pytest.approx(25.123456, rel=1e-6)


def test_validate_rejects_none():
    with pytest.raises(ValueError):
        compute_ambient(None)


def test_validate_rejects_wrong_shape():
    with pytest.raises(ValueError):
        compute_ambient(np.zeros(10, dtype=np.float32))


def test_validate_rejects_non_finite():
    matrix = np.full((4, 4), 25.0, dtype=np.float32)
    matrix[2, 2] = np.nan
    with pytest.raises(ValueError):
        compute_ambient(matrix)


def test_validate_normalizes_dtype():
    arr = validate_matrix(np.zeros((4, 4)))
    assert arr.dtype == np.float32
