"""Daily and monthly consumption accumulators (pure functions).

The accumulators are fed directly with model increments
(a·Δt + k·ΔI, with the coefficients in force when the increment is added),
independently of the held/compensated 'Estimated gas' total.

Structure stored in the engine data under ``"periods"``::

    {"day": bucket, "prev_day": bucket, "month": bucket, "prev_month": bucket}

    bucket = {
        "key": "2025-01-31" | "2025-01",
        "period_start": ISO UTC of local midnight / first of month,
        "start": ISO UTC of the first instant actually covered,
        "base": m³ (a·Δt), "heat": m³ (k·ΔI), "index": %·h,
        "missing_h": hours without data (skipped downtime),
    }

Period boundaries are local midnight / first of month in HA's time zone,
so DST days are 23 h or 25 h long.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

KINDS = ("day", "month")
PREV = {"day": "prev_day", "month": "prev_month"}


def _first_of_next_month(d: date) -> date:
    return (d.replace(day=1) + timedelta(days=32)).replace(day=1)


def bounds(kind: str, when: datetime) -> tuple[str, datetime, datetime]:
    """Return (key, start_utc, end_utc) of the local period containing when."""
    local_d = dt_util.as_local(when).date()
    if kind == "day":
        start_d, end_d = local_d, local_d + timedelta(days=1)
        key = local_d.isoformat()
    else:
        start_d = local_d.replace(day=1)
        end_d = _first_of_next_month(local_d)
        key = f"{local_d.year:04d}-{local_d.month:02d}"
    return (
        key,
        dt_util.as_utc(dt_util.start_of_local_day(start_d)),
        dt_util.as_utc(dt_util.start_of_local_day(end_d)),
    )


def new_bucket(key: str, period_start: datetime, start: datetime) -> dict[str, Any]:
    """Return an empty accumulator."""
    return {
        "key": key,
        "period_start": period_start.isoformat(),
        "start": start.isoformat(),
        "base": 0.0,
        "heat": 0.0,
        "index": 0.0,
        "missing_h": 0.0,
    }


def _roll(
    periods: dict[str, Any], kind: str, when: datetime, *, create: bool
) -> dict[str, Any] | None:
    """Return the bucket for the period containing when, rolling over if needed."""
    key, p_start, _ = bounds(kind, when)
    cur = periods.get(kind)
    if cur is not None and cur["key"] == key:
        return cur
    if cur is not None and cur["key"] > key:
        return None  # time went backwards: ignore
    if cur is None and not create:
        return None
    if cur is None:
        periods[kind] = new_bucket(key, p_start, when)
        return periods[kind]
    prev_key, prev_start, prev_end = bounds(kind, p_start - timedelta(seconds=1))
    if cur["key"] == prev_key:
        periods[PREV[kind]] = cur
    else:
        # Whole previous period without any data.
        empty = new_bucket(prev_key, prev_start, prev_start)
        empty["missing_h"] = (prev_end - prev_start).total_seconds() / 3600
        periods[PREV[kind]] = empty
    periods[kind] = new_bucket(key, p_start, p_start)
    return periods[kind]


def _pieces(t0: datetime, t1: datetime) -> list[tuple[datetime, datetime]]:
    """Split [t0, t1) at local midnights."""
    out = []
    cur = t0
    while cur < t1:
        _, _, day_end = bounds("day", cur)
        nxt = min(day_end, t1)
        out.append((cur, nxt))
        cur = nxt
    return out


def add_increment(
    periods: dict[str, Any],
    t0: datetime,
    t1: datetime,
    d_index: float,
    *,
    a: float,
    k: float,
) -> None:
    """Add a·Δt + k·ΔI for [t0, t1); ΔI is spread uniformly over the span."""
    total_s = (t1 - t0).total_seconds()
    if total_s <= 0:
        return
    for p0, p1 in _pieces(t0, t1):
        sec = (p1 - p0).total_seconds()
        di = d_index * sec / total_s
        for kind in KINDS:
            bucket = _roll(periods, kind, p0, create=True)
            if bucket is None:
                continue
            bucket["base"] += a * sec / 86400.0
            bucket["heat"] += k * di
            bucket["index"] += di


def add_missing(periods: dict[str, Any], t0: datetime, t1: datetime) -> None:
    """Record [t0, t1) as having no data (only if accumulation already started)."""
    for p0, p1 in _pieces(t0, t1):
        for kind in KINDS:
            bucket = _roll(periods, kind, p0, create=False)
            if bucket is not None:
                bucket["missing_h"] += (p1 - p0).total_seconds() / 3600


def total(bucket: dict[str, Any] | None) -> float | None:
    """Return the consumption of a bucket in m³."""
    if bucket is None:
        return None
    return bucket["base"] + bucket["heat"]
