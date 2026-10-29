"""Tests for the daily / monthly consumption sensors."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.virtual_gas_meter import periods as per
from custom_components.virtual_gas_meter.const import DOMAIN
from custom_components.virtual_gas_meter.engine import GasMeterEngine
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import S1, S2, eid

A = 0.1
K = 0.005


def utc(s: str):
    return dt_util.parse_datetime(s + "+00:00")


def _st(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    return hass.states.get(eid(hass, entry, "sensor", key))


async def _move(hass: HomeAssistant, freezer: FrozenDateTimeFactory, to: str) -> None:
    freezer.move_to(to + "+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_dst_day_lengths(hass: HomeAssistant, rome) -> None:
    # 26 Oct 2025: CEST -> CET, the local day lasts 25 h.
    _, s, e = per.bounds("day", utc("2025-10-26 12:00:00"))
    assert s == utc("2025-10-25 22:00:00")
    assert e == utc("2025-10-26 23:00:00")
    # 30 Mar 2025: CET -> CEST, 23 h.
    _, s, e = per.bounds("day", utc("2025-03-30 12:00:00"))
    assert (e - s) == timedelta(hours=23)
    # October as a month: 1 Oct 00:00 CEST -> 1 Nov 00:00 CET
    key, s, e = per.bounds("month", utc("2025-10-15 12:00:00"))
    assert key == "2025-10"
    assert s == utc("2025-09-30 22:00:00")
    assert e == utc("2025-10-31 23:00:00")

    periods: dict = {}
    # 100 % for the whole 25 h DST day, then 1 h into the next day
    per.add_increment(
        periods,
        utc("2025-10-25 22:00:00"),
        utc("2025-10-27 00:00:00"),
        2600.0,
        a=A,
        k=K,
    )
    day25 = periods["prev_day"]
    assert day25["key"] == "2025-10-26"
    assert day25["index"] == pytest.approx(2500.0)
    assert day25["base"] == pytest.approx(A * 25 / 24)
    assert periods["day"]["key"] == "2025-10-27"
    assert periods["day"]["index"] == pytest.approx(100.0)
    assert periods["month"]["index"] == pytest.approx(2600.0)


async def test_midnight_rollover(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    hass.states.async_set(S1, "100")
    await hass.async_block_till_done()
    await _move(hass, freezer, "2025-10-28 22:30:00")  # 23:30 local
    oggi = _st(hass, entry, "consumption_today")
    assert oggi.attributes["period"] == "2025-10-28"
    assert float(oggi.state) == pytest.approx(A * 10.5 / 24 + K * 1050, abs=1e-4)
    # first setup without statistics: accumulation started at setup time
    assert oggi.attributes["start_time"] == "2025-10-28T13:00:00+01:00"
    assert _st(hass, entry, "consumption_yesterday").state == "unknown"

    await _move(hass, freezer, "2025-10-28 23:30:00")  # 00:30 local, 29/10
    oggi = _st(hass, entry, "consumption_today")
    ieri = _st(hass, entry, "consumption_yesterday")
    assert oggi.attributes["period"] == "2025-10-29"
    assert oggi.attributes["start_time"] == "2025-10-29T00:00:00+01:00"
    assert oggi.attributes["last_reset"] == "2025-10-28T23:00:00+00:00"
    assert oggi.attributes["state_class"] == "total"
    assert oggi.attributes["device_class"] == "gas"
    assert float(oggi.state) == pytest.approx(A * 0.5 / 24 + K * 50, abs=1e-4)
    assert oggi.attributes["heating_index"] == pytest.approx(50.0)
    assert oggi.attributes["heating_share"] == pytest.approx(K * 50, abs=1e-4)
    assert oggi.attributes["base_share"] == pytest.approx(A * 0.5 / 24, abs=1e-4)
    assert ieri.attributes["period"] == "2025-10-28"
    assert "state_class" not in ieri.attributes
    assert "last_reset" not in ieri.attributes
    assert "start_time" not in ieri.attributes
    assert float(ieri.state) == pytest.approx(A * 11 / 24 + K * 1100, abs=1e-4)
    mese = _st(hass, entry, "consumption_this_month")
    assert float(mese.state) == pytest.approx(A * 11.5 / 24 + K * 1150, abs=1e-4)


async def test_month_rollover_and_no_restatement(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    hass.states.async_set(S1, "100")
    await hass.async_block_till_done()
    await _move(hass, freezer, "2025-10-31 22:30:00")
    # A lower reading changes k and holds 'Estimated gas', but does not restate
    # the consumption already accumulated in the periods.
    mese_before = float(_st(hass, entry, "consumption_this_month").state)
    await hass.services.async_call(
        DOMAIN, "register_reading", {"reading": 1662.0}, blocking=True
    )
    engine = entry.runtime_data
    new_k = engine.k
    assert new_k != K
    assert float(_st(hass, entry, "consumption_this_month").state) == pytest.approx(
        mese_before
    )

    await _move(hass, freezer, "2025-10-31 23:30:00")  # 00:30 local 1 Nov
    prev = _st(hass, entry, "consumption_last_month")
    cur = _st(hass, entry, "consumption_this_month")
    assert prev.attributes["period"] == "2025-10"
    hours_oct = 83  # 28/10 12:00 UTC -> 31/10 23:00 UTC
    expected_oct = A * hours_oct / 24 + K * 100 * 82.5 + new_k * 100 * 0.5
    assert float(prev.state) == pytest.approx(expected_oct, abs=1e-3)
    assert "state_class" not in prev.attributes
    assert cur.attributes["period"] == "2025-11"
    assert cur.attributes["start_time"] == "2025-11-01T00:00:00+01:00"
    assert cur.attributes["last_reset"] == "2025-10-31T23:00:00+00:00"
    assert float(cur.state) == pytest.approx(A * 0.5 / 24 + new_k * 50, abs=1e-4)
    ieri = _st(hass, entry, "consumption_yesterday")
    assert ieri.attributes["period"] == "2025-10-31"
    assert float(ieri.state) == pytest.approx(
        A + K * 100 * 23.5 + new_k * 100 * 0.5, abs=1e-3
    )


async def test_periods_restart_persistence(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    hass.states.async_set(S1, "100")
    await hass.async_block_till_done()
    await _move(hass, freezer, "2025-10-28 14:00:00")
    before = float(_st(hass, entry, "consumption_today").state)
    assert before == pytest.approx(A * 2 / 24 + K * 200, abs=1e-4)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(_st(hass, entry, "consumption_today").state) == pytest.approx(before)
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to("2025-10-28 14:02:00+00:00")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert float(_st(hass, entry, "consumption_today").state) == pytest.approx(
        A * (2 + 2 / 60) / 24 + K * (200 + 100 * 2 / 60), abs=1e-4
    )


async def test_backfill_spanning_midnight(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    await _move(hass, freezer, "2025-10-28 21:00:00")  # 22:00 local, sensors 0
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to("2025-10-29 01:00:00+00:00")  # 02:00 local next day

    base = utc("2025-10-28 21:00:00").timestamp()

    async def fake(_self, start, end, period):
        if period == "hour":  # 21, 22, 23, 00 UTC: 40 % + 0 %
            return {
                S1: [{"start": base + h * 3600, "mean": 40.0} for h in range(4)],
                S2: [{"start": base + h * 3600, "mean": 0.0} for h in range(4)],
            }
        return {}

    with patch.object(GasMeterEngine, "_async_fetch_statistics", fake):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    ieri = _st(hass, entry, "consumption_yesterday")
    oggi = _st(hass, entry, "consumption_today")
    assert ieri.attributes["period"] == "2025-10-28"
    # 12:00 -> 23:00 UTC (11 h base), heating only 21-23 UTC = 80 %·h
    assert ieri.attributes["heating_index"] == pytest.approx(80.0)
    assert float(ieri.state) == pytest.approx(A * 11 / 24 + K * 80, abs=1e-4)
    assert oggi.attributes["period"] == "2025-10-29"
    assert oggi.attributes["heating_index"] == pytest.approx(80.0)
    assert float(oggi.state) == pytest.approx(A * 2 / 24 + K * 80, abs=1e-4)
    assert entry.runtime_data.index_since == pytest.approx(160.0)


async def test_first_setup_initialized_from_backfill(
    hass: HomeAssistant, rome, entry_data: dict
) -> None:
    # Configured reading 27/10 20:00 local = 19:00 UTC, 'now' 28/10 12:00 UTC.
    base = utc("2025-10-27 19:00:00").timestamp()

    async def fake(_self, start, end, period):
        if period == "hour":
            return {S1: [{"start": base + h * 3600, "mean": 10.0} for h in range(17)]}
        return {}

    hass.states.async_set(S1, "10")
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    with patch.object(GasMeterEngine, "_async_fetch_statistics", fake):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    ieri = _st(hass, entry, "consumption_yesterday")
    oggi = _st(hass, entry, "consumption_today")
    mese = _st(hass, entry, "consumption_this_month")
    assert ieri.attributes["period"] == "2025-10-27"
    assert ieri.attributes["heating_index"] == pytest.approx(40.0)
    assert oggi.attributes["heating_index"] == pytest.approx(130.0)
    assert oggi.attributes["start_time"] == "2025-10-28T00:00:00+01:00"
    assert mese.attributes["start_time"] == "2025-10-27T20:00:00+01:00"
    assert float(mese.state) == pytest.approx(A * 17 / 24 + K * 170, abs=1e-4)
