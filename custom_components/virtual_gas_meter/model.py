"""Pure model math for the virtual gas meter (no Home Assistant imports)."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(slots=True)
class FitResult:
    """Result of a coefficient fit."""

    a: float
    k: float
    n: int
    rmse: float | None
    joint: bool


def estimate(a: float, k: float, days: float, index: float) -> float:
    """Return the estimated consumption in m3 for an interval."""
    return a * days + k * index


def _included(readings: Iterable[dict]) -> list[dict]:
    return [
        r
        for r in readings
        if r.get("index") is not None
        and not r.get("excluded")
        and r.get("m3") is not None
        and r.get("days")
    ]


def _rmse(rows: Sequence[dict], a: float, k: float) -> float | None:
    if not rows:
        return None
    err = [(r["m3"] - estimate(a, k, r["days"], r["index"])) ** 2 for r in rows]
    return (sum(err) / len(err)) ** 0.5


def fit_k(readings: Iterable[dict], a: float) -> FitResult | None:
    """Least squares k with a fixed: k = sum(I*(m3-a*d)) / sum(I^2)."""
    rows = _included(readings)
    den = sum(r["index"] ** 2 for r in rows)
    if den <= 0:
        return None
    num = sum(r["index"] * (r["m3"] - a * r["days"]) for r in rows)
    k = num / den
    if k <= 0:
        return None
    return FitResult(a=a, k=k, n=len(rows), rmse=_rmse(rows, a, k), joint=False)


def fit_a_k(readings: Iterable[dict]) -> FitResult | None:
    """Joint least squares for m3 = a*d + k*I (no intercept)."""
    rows = _included(readings)
    if len(rows) < 3:
        return None
    sdd = sum(r["days"] ** 2 for r in rows)
    sii = sum(r["index"] ** 2 for r in rows)
    sdi = sum(r["days"] * r["index"] for r in rows)
    sdm = sum(r["days"] * r["m3"] for r in rows)
    sim = sum(r["index"] * r["m3"] for r in rows)
    det = sdd * sii - sdi**2
    if abs(det) < 1e-9 * max(sdd * sii, 1e-12):
        return None
    a = (sdm * sii - sim * sdi) / det
    k = (sim * sdd - sdm * sdi) / det
    if a < 0 or k <= 0:
        return None
    return FitResult(a=a, k=k, n=len(rows), rmse=_rmse(rows, a, k), joint=True)


def interpolate_checkpoints(
    checkpoints: Sequence[tuple[float, float]], ts: float
) -> float:
    """Return the cumulative index at timestamp ts by linear interpolation.

    checkpoints is a list of (timestamp, cumulative_index) sorted by time.
    """
    if not checkpoints:
        return 0.0
    times = [c[0] for c in checkpoints]
    if ts <= times[0]:
        return checkpoints[0][1]
    if ts >= times[-1]:
        return checkpoints[-1][1]
    i = bisect_left(times, ts)
    (t0, v0), (t1, v1) = checkpoints[i - 1], checkpoints[i]
    if t1 == t0:
        return v1
    return v0 + (v1 - v0) * (ts - t0) / (t1 - t0)


def period_overlap_sum(
    rows: Iterable[tuple[float, float]],
    start: float,
    end: float,
    period: float = 3600.0,
) -> list[tuple[float, float]]:
    """Integrate statistic means (row_start_ts, mean_pct) over [start, end).

    Each row covers [row_start, row_start + period).

    Returns a list of (period_end_ts, %·h contributed) clipped to the window.
    """
    out: list[tuple[float, float]] = []
    for row_start, mean in rows:
        lo = max(row_start, start)
        hi = min(row_start + period, end)
        if hi <= lo or mean is None:
            continue
        out.append((hi, max(float(mean), 0.0) * (hi - lo) / 3600.0))
    return out


def period_overlap_segments(
    rows: Iterable[tuple[float, float]],
    start: float,
    end: float,
    period: float = 3600.0,
) -> list[tuple[float, float, float]]:
    """Like period_overlap_sum, but return (lo_ts, hi_ts, %·h) segments.

    Rows of different sensors covering the same period are summed, so the
    result has non-overlapping segments sorted by time.
    """
    acc: dict[tuple[float, float], float] = {}
    for row_start, mean in rows:
        lo = max(row_start, start)
        hi = min(row_start + period, end)
        if hi <= lo or mean is None:
            continue
        acc[(lo, hi)] = (
            acc.get((lo, hi), 0.0) + max(float(mean), 0.0) * (hi - lo) / 3600.0
        )
    return sorted((lo, hi, v) for (lo, hi), v in acc.items())
