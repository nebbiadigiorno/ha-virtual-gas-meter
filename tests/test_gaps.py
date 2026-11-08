"""Tests for missing sensor data (hourly profile) and fit protection."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.virtual_gas_meter.const import DOMAIN
from custom_components.virtual_gas_meter.engine import GasMeterEngine
from custom_components.virtual_gas_meter.model import fit_k
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .conftest import S1, S2, eid


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Move time forward minute by minute, as the real tick does."""
    for _ in range(int(delta.total_seconds() // 60)):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _attrs(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> dict:
    return hass.states.get(eid(hass, entry, "sensor", key)).attributes


async def test_unavailable_is_estimated_not_zero(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    engine: GasMeterEngine = setup_entry.runtime_data
    hass.states.async_set(S1, "40", {"unit_of_measurement": "%"})
    hass.states.async_set(S2, "20", {"unit_of_measurement": "%"})
    await _advance(hass, freezer, timedelta(hours=2))
    assert engine.index_since == pytest.approx(120.0, rel=1e-3)
    assert engine.filled_h_since == 0.0

    # S1 goes unavailable for 2 h: those hours were never seen, so the mean
    # of its known slots (40 %) is used, not 0.
    hass.states.async_set(S1, "unavailable")
    await _advance(hass, freezer, timedelta(hours=2))
    assert engine.index_since == pytest.approx(240.0, rel=1e-3)
    assert engine.filled_h_since == pytest.approx(1.0, rel=1e-3)
    assert engine.filled_index_since == pytest.approx(80.0, rel=1e-3)
    since = _attrs(hass, setup_entry, "consumption_since_last_reading")
    assert since["estimated_hours"] == pytest.approx(1.0, rel=1e-2)
    today = _attrs(hass, setup_entry, "consumption_today")
    assert today["estimated_hours"] == pytest.approx(1.0, rel=1e-2)


async def test_profile_uses_same_hour_of_previous_day(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    engine: GasMeterEngine = setup_entry.runtime_data
    hass.states.async_set(S1, "90", {"unit_of_measurement": "%"})
    await _advance(hass, freezer, timedelta(hours=1))
    hass.states.async_set(S1, "10", {"unit_of_measurement": "%"})
    await _advance(hass, freezer, timedelta(hours=23))

    hour = dt_util.as_local(dt_util.utcnow()).hour
    prof = engine.data["profile"][S1]
    assert prof[hour] == pytest.approx(90.0, rel=1e-3)
    assert prof[(hour + 1) % 24] == pytest.approx(10.0, rel=1e-3)

    before = engine.index_since
    hass.states.async_set(S1, "unknown")
    await _advance(hass, freezer, timedelta(hours=1))
    assert engine.index_since - before == pytest.approx(90.0, rel=1e-3)


async def test_profile_seeded_from_statistics(
    hass: HomeAssistant, rome, entry_data: dict, freezer: FrozenDateTimeFactory
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    hass.states.async_set(S2, "0", {"unit_of_measurement": "%"})
    now_ts = dt_util.utcnow().timestamp()

    async def fake(_self, start, end, period):
        if period != "hour":
            return {}
        return {S1: [{"start": now_ts - 3600 * h, "mean": 30.0} for h in range(1, 49)]}

    with patch.object(GasMeterEngine, "_async_fetch_statistics", fake):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    engine: GasMeterEngine = entry.runtime_data
    assert all(v == pytest.approx(30.0) for v in engine.data["profile"][S1])
    # S1 has no state yet (not loaded): estimated at 30 %.
    before = engine.index_since
    await _advance(hass, freezer, timedelta(hours=1))
    assert engine.index_since - before == pytest.approx(30.0, rel=1e-3)
    hass.states.async_set(S1, "50", {"unit_of_measurement": "%"})
    await _advance(hass, freezer, timedelta(hours=1))
    assert engine.index_since - before == pytest.approx(80.0, rel=1e-3)


async def test_long_outage_excludes_interval_from_fit(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    engine: GasMeterEngine = setup_entry.runtime_data
    k_before = engine.k
    for value in ("30", "unavailable", "30"):
        hass.states.async_set(S1, value, {"unit_of_measurement": "%"})
        hass.states.async_set(S2, value, {"unit_of_measurement": "%"})
        hours = {"30": 10, "unavailable": 6}[value]
        await _advance(hass, freezer, timedelta(hours=hours))

    await hass.services.async_call(
        DOMAIN, "register_reading", {"reading": 1650.0 + 40.0}, blocking=True
    )
    reading = engine.last_reading
    assert reading["excluded"] is True
    assert reading["exclusion"] == "automatic"
    assert reading["filled_h"] == pytest.approx(6.0, rel=1e-2)
    # k was refitted without this interval; with it, k would be higher.
    k_excluded = engine.k
    assert k_excluded == pytest.approx(fit_k(engine.readings, engine.a).k)
    included = [dict(r, excluded=False) if r is reading else r for r in engine.readings]
    assert fit_k(included, engine.a).k > k_excluded
    assert k_before > 0
    assert engine.filled_h_since == pytest.approx(0.0, abs=1e-6)
    err = _attrs(hass, setup_entry, "last_interval_error")
    assert err["excluded_from_fit"] is True
    assert err["exclusion"] == "automatic"
    assert err["estimated_hours"] == pytest.approx(6.0, rel=1e-2)

    # Include it again by hand: k is refitted with it (and goes up).
    when = dt_util.as_local(dt_util.parse_datetime(reading["date"]))
    await hass.services.async_call(
        DOMAIN,
        "exclude_reading",
        {"date": when.strftime("%Y-%m-%d %H:%M:%S"), "exclude": False},
        blocking=True,
    )
    assert engine.last_reading["excluded"] is False
    assert "exclusion" not in engine.last_reading
    assert engine.k > k_excluded


async def test_short_outage_keeps_interval_in_fit(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    engine: GasMeterEngine = setup_entry.runtime_data
    hass.states.async_set(S1, "30", {"unit_of_measurement": "%"})
    await _advance(hass, freezer, timedelta(hours=10))
    hass.states.async_set(S1, "unavailable")
    await _advance(hass, freezer, timedelta(hours=1))
    hass.states.async_set(S1, "30", {"unit_of_measurement": "%"})
    await _advance(hass, freezer, timedelta(hours=10))
    await hass.services.async_call(
        DOMAIN, "register_reading", {"reading": 1652.0}, blocking=True
    )
    assert engine.last_reading["excluded"] is False
    assert engine.last_reading["filled_h"] == pytest.approx(0.5, rel=1e-2)


async def test_manual_exclusion_and_errors(hass: HomeAssistant, setup_entry) -> None:
    engine: GasMeterEngine = setup_entry.runtime_data
    await hass.services.async_call(
        DOMAIN, "exclude_reading", {"date": "2025-10-27"}, blocking=True
    )
    r = engine.readings[-1]
    assert r["excluded"] is True
    assert r["exclusion"] == "manual"
    await hass.services.async_call(
        DOMAIN,
        "exclude_reading",
        {"date": "2025-10-27", "exclude": False},
        blocking=True,
    )
    assert engine.readings[-1]["excluded"] is False
    # The first reading has no interval: it cannot be included.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "exclude_reading",
            {"date": "2025-01-05", "exclude": False},
            blocking=True,
        )


async def test_profile_and_counters_persist_restart(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    engine: GasMeterEngine = setup_entry.runtime_data
    hass.states.async_set(S1, "60", {"unit_of_measurement": "%"})
    await _advance(hass, freezer, timedelta(hours=1))
    hass.states.async_set(S1, "unavailable")
    await _advance(hass, freezer, timedelta(hours=1))
    filled = engine.filled_h_since
    assert filled > 0
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    engine2: GasMeterEngine = setup_entry.runtime_data
    assert engine2 is not engine
    assert engine2.filled_h_since >= filled
    assert any(v is not None for v in engine2.data["profile"][S1])


async def test_store_from_previous_version_loads(
    hass: HomeAssistant, rome, entry_data: dict, hass_storage: dict
) -> None:
    """A 0.2.0 store (no profile / filled keys) loads without losing data."""
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    old = {
        "a": 0.1,
        "k": 0.005,
        "opt_a": 0.1,
        "opt_k": 0.005,
        "readings": [
            {
                "date": "2025-10-27T19:00:00+00:00",
                "value": 1650.0,
                "index": None,
                "days": None,
                "m3": None,
                "excluded": True,
                "estimated_m3": None,
                "error_m3": None,
            }
        ],
        "index_since": 10.0,
        "last_update": "2025-10-28T11:58:00+00:00",
        "checkpoints": [],
        "published_total": 1650.5,
        "pending_reading": None,
        "pending_reading_date": None,
        "last_fit": None,
        "periods": {},
    }
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": dict(old),
    }
    hass.states.async_set(S1, "0", {"unit_of_measurement": "%"})
    hass.states.async_set(S2, "0", {"unit_of_measurement": "%"})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    data = entry.runtime_data.data
    for key in ("a", "k", "readings", "published_total", "last_fit"):
        assert data[key] == old[key]
    assert data["index_since"] == pytest.approx(10.0)
    assert entry.runtime_data.filled_h_since == 0.0
