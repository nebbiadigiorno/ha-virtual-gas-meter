"""Tests for the pure model math."""

from __future__ import annotations

import pytest

from custom_components.virtual_gas_meter.model import (
    fit_a_k,
    fit_k,
    interpolate_checkpoints,
    period_overlap_sum,
)

# Synthetic intervals generated exactly from a=0.1 m3/day and k=0.005 m3/%·h;
# the first one is excluded and deliberately inconsistent.
A, K = 0.1, 0.005
_RAW = [
    (56.0, 50000.0, True),
    (28.0, 49000.0, False),
    (30.0, 31000.0, False),
    (35.0, 0.0, False),
    (89.0, 0.0, False),
    (28.0, 0.0, False),
    (30.0417, 4000.0, False),
]
ROWS = [
    {"days": d, "m3": 999.0 if ex else A * d + K * i, "index": i, "excluded": ex}
    for d, i, ex in _RAW
]


def test_fit_k_recovers_k() -> None:
    res = fit_k(ROWS, A)
    assert res is not None
    assert res.k == pytest.approx(K, rel=1e-9)
    assert res.n == 6
    assert res.rmse == pytest.approx(0.0, abs=1e-9)


def test_fit_a_k_recovers_both() -> None:
    res = fit_a_k(ROWS)
    assert res is not None
    assert res.a == pytest.approx(A, rel=1e-6)
    assert res.k == pytest.approx(K, rel=1e-6)


def test_fit_needs_data() -> None:
    assert fit_k([{"days": 10, "m3": 1, "index": 0.0}], 0.1) is None
    assert fit_a_k(ROWS[:2]) is None


def test_interpolate_and_overlap() -> None:
    cps = [(0.0, 0.0), (3600.0, 10.0), (7200.0, 30.0)]
    assert interpolate_checkpoints(cps, 1800.0) == pytest.approx(5.0)
    assert interpolate_checkpoints(cps, 5400.0) == pytest.approx(20.0)
    assert interpolate_checkpoints(cps, 9000.0) == pytest.approx(30.0)
    out = period_overlap_sum([(0.0, 50.0), (3600.0, 20.0)], 1800.0, 5400.0)
    assert sum(v for _, v in out) == pytest.approx(25.0 + 10.0)
