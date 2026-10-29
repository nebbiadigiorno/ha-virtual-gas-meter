"""Tests for accumulation, persistence and readings."""

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
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .conftest import S1, S2, eid


def _state(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> float:
    return float(hass.states.get(eid(hass, entry, "sensor", key)).state)


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_seed_and_entities(hass: HomeAssistant, setup_entry) -> None:
    entry = setup_entry
    engine: GasMeterEngine = entry.runtime_data
    assert len(engine.readings) == 8
    assert engine.readings[1]["excluded"] is True  # partial Jan-Mar interval
    assert engine.last_reading["value"] == 1650.0
    # No statistics in the test recorder: the gap since 27/10 20:00 is skipped.
    assert engine.backfill_report["skipped_hours"] == pytest.approx(17.0)
    assert engine.index_since == 0
    assert _state(hass, entry, "estimated_gas") == pytest.approx(1650.0 + 0.1 * 17 / 24)
    st = hass.states.get(eid(hass, entry, "sensor", "estimated_gas"))
    assert st.attributes["state_class"] == "total_increasing"
    assert st.attributes["device_class"] == "gas"
    assert st.attributes["unit_of_measurement"] == "m³"
    last = hass.states.get(eid(hass, entry, "sensor", "last_reading"))
    assert len(last.attributes["readings"]) == 8
    # error of the last seeded interval: a*30.04 d + k*4000 - 24.8 m3
    assert _state(hass, entry, "last_interval_error") == pytest.approx(
        0.1 * 30.0417 + 0.005 * 4000 - 24.8, abs=0.01
    )


async def test_accumulation_with_unavailable(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    engine = entry.runtime_data
    hass.states.async_set(S1, "50")
    hass.states.async_set(S2, "30")
    await hass.async_block_till_done()
    await _advance(hass, freezer, timedelta(hours=1))
    assert engine.index_since == pytest.approx(80.0, rel=1e-6)
    hass.states.async_set(S2, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    await _advance(hass, freezer, timedelta(minutes=30))
    assert engine.index_since == pytest.approx(80.0 + 25.0, rel=1e-6)
    hass.states.async_set(S1, "unknown")
    await hass.async_block_till_done()
    await _advance(hass, freezer, timedelta(hours=2))
    assert engine.index_since == pytest.approx(105.0, rel=1e-6)
    assert _state(hass, entry, "heating_index_since_last_reading") == pytest.approx(
        105.0
    )
    expected = 0.1 * engine.days_since + engine.k * 105.0
    assert _state(hass, entry, "consumption_since_last_reading") == pytest.approx(
        expected, abs=1e-3
    )


async def test_restart_persistence_no_double_count(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    hass.states.async_set(S1, "60")
    await hass.async_block_till_done()
    await _advance(hass, freezer, timedelta(hours=1))
    assert entry.runtime_data.index_since == pytest.approx(60.0)

    # Short restart (2 min): integrated with current values, no backfill.
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(minutes=2))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.index_since == pytest.approx(62.0)

    # Restart again immediately: nothing is added twice.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.index_since == pytest.approx(62.0)


async def test_backfill_from_statistics(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    assert await hass.config_entries.async_unload(entry.entry_id)
    down_at = dt_util.utcnow()  # 12:00 UTC
    freezer.tick(timedelta(hours=3, minutes=30))  # 15:30 UTC

    async def fake(_self, start, end, period):
        base = dt_util.parse_datetime("2025-10-28 12:00:00+00:00").timestamp()
        if period == "hour":
            # 12:00, 13:00, 14:00 compiled; 15:00 not yet
            return {
                S1: [{"start": base + h * 3600, "mean": 40.0} for h in range(3)],
                S2: [{"start": base + h * 3600, "mean": 10.0} for h in range(3)],
            }
        # 5-minute statistics for 15:00-15:25
        return {
            S1: [{"start": base + 3 * 3600 + i * 300, "mean": 12.0} for i in range(6)]
        }

    with patch.object(GasMeterEngine, "_async_fetch_statistics", fake):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    engine = entry.runtime_data
    # 3 h * 50 % from hourly LTS + 0.5 h * 12 % from 5-minute statistics
    assert engine.index_since == pytest.approx(150.0 + 6.0)
    assert engine.backfill_report["added_index"] == pytest.approx(156.0)
    assert engine.last_update >= down_at


async def test_register_reading_realign_refit_monotonic(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    engine = entry.runtime_data
    hass.states.async_set(S1, "100")
    await hass.async_block_till_done()
    await _advance(hass, freezer, timedelta(hours=10))
    total_before = _state(hass, entry, "estimated_gas")
    assert total_before == pytest.approx(
        1650 + 0.1 * (17 / 24 + 10 / 24) + 0.005 * 1000, abs=1e-3
    )

    # Real reading below the estimate -> total is held (never decreases).
    response = await hass.services.async_call(
        DOMAIN,
        "register_reading",
        {"reading": 1652.0},
        blocking=True,
        return_response=True,
    )
    assert response["reading"]["index"] == pytest.approx(1000.0)
    assert engine.last_reading["value"] == 1652.0
    assert engine.index_since == 0
    # refit with fixed a over all included intervals (incl. the new one)
    rows = [r for r in engine.readings if r["index"] is not None and not r["excluded"]]
    num = sum(r["index"] * (r["m3"] - 0.1 * r["days"]) for r in rows)
    den = sum(r["index"] ** 2 for r in rows)
    assert engine.k == pytest.approx(num / den)
    assert engine.a == 0.1
    assert _state(hass, entry, "estimated_gas") == pytest.approx(total_before)
    held = engine.held_offset
    assert held == pytest.approx(total_before - 1652.0, abs=1e-6)

    # Growth is absorbed until the raw estimate catches up.
    await _advance(hass, freezer, timedelta(hours=1))
    assert _state(hass, entry, "estimated_gas") == pytest.approx(total_before)
    hass.states.async_set(S1, "100")
    await _advance(hass, freezer, timedelta(hours=20))
    assert engine.raw_total > total_before
    assert _state(hass, entry, "estimated_gas") == pytest.approx(
        engine.raw_total, abs=1e-4
    )

    # Reading above the estimate -> step up.
    await hass.services.async_call(
        DOMAIN, "register_reading", {"reading": 1672.0}, blocking=True
    )
    assert _state(hass, entry, "estimated_gas") == pytest.approx(1672.0)
    assert engine.held_offset == 0


async def test_register_reading_in_past_splits_index(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    engine = entry.runtime_data
    hass.states.async_set(S1, "100")
    await hass.async_block_till_done()
    for _ in range(4):
        await _advance(hass, freezer, timedelta(hours=1))  # 12:00 -> 16:00 UTC
    # Reading taken at 15:00 local (14:00 UTC) = 2 h after start -> 200 %·h
    await hass.services.async_call(
        DOMAIN,
        "register_reading",
        {"reading": 1662.0, "date": "2025-10-28 15:00:00"},
        blocking=True,
    )
    assert engine.last_reading["index"] == pytest.approx(200.0, rel=1e-3)
    assert engine.index_since == pytest.approx(200.0, rel=1e-3)


async def test_reject_bad_readings(hass: HomeAssistant, setup_entry) -> None:
    cases = [
        ({"reading": 1000.0}, "reading_too_low"),
        ({"reading": 1662.0, "date": "2025-10-20 20:00:00"}, "reading_too_old"),
        ({"reading": 1662.0, "date": "2025-12-01 20:00:00"}, "reading_in_future"),
    ]
    for data, key in cases:
        with pytest.raises(ServiceValidationError) as exc:
            await hass.services.async_call(
                DOMAIN, "register_reading", data, blocking=True
            )
        assert exc.value.translation_key == key
    assert len(setup_entry.runtime_data.readings) == 8


async def test_number_button_delete_recalc(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    engine = entry.runtime_data
    number = eid(hass, entry, "number", "new_reading")
    button = eid(hass, entry, "button", "register_reading")
    assert float(hass.states.get(number).state) == 1650.0
    hass.states.async_set(S1, "100")
    await _advance(hass, freezer, timedelta(hours=5))
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 1653.5}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    assert engine.last_reading["value"] == 1653.5
    assert len(engine.readings) == 9
    total = engine.total

    # Delete it again: interval rejoins the current one, total never decreases.
    await hass.services.async_call(
        DOMAIN, "delete_reading", {"date": "2025-10-28"}, blocking=True
    )
    assert len(engine.readings) == 8
    assert engine.last_reading["value"] == 1650.0
    assert engine.index_since == pytest.approx(500.0, rel=1e-3)
    assert engine.total >= total

    # Delete a middle reading: its interval merges into the next one.
    await hass.services.async_call(
        DOMAIN, "delete_reading", {"date": "2025-08-30"}, blocking=True
    )
    merged = next(r for r in engine.readings if r["date"].startswith("2025-09-27"))
    assert merged["m3"] == pytest.approx(1625.2 - 1613.5)
    assert merged["days"] == pytest.approx(117.0)

    resp = await hass.services.async_call(
        DOMAIN, "recalculate", {}, blocking=True, return_response=True
    )
    assert resp["k"] == engine.k


async def test_diagnostics(hass: HomeAssistant, setup_entry) -> None:
    from custom_components.virtual_gas_meter.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    diag = await async_get_config_entry_diagnostics(hass, setup_entry)
    assert diag["model"]["k"] == 0.005
    assert len(diag["stored"]["readings"]) == 8


async def test_seed_only_first_setup(
    hass: HomeAssistant, setup_entry, hass_storage
) -> None:
    entry = setup_entry
    await hass.services.async_call(
        DOMAIN, "delete_reading", {"date": "2025-01-05"}, blocking=True
    )
    assert len(entry.runtime_data.readings) == 7
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.readings) == 7


async def test_joint_refit_when_a_not_fixed(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "fix_a": False, "auto_k": True}
    )
    await hass.async_block_till_done()
    engine = entry.runtime_data
    hass.states.async_set(S1, "100")
    await _advance(hass, freezer, timedelta(hours=24))
    await hass.services.async_call(
        DOMAIN, "register_reading", {"reading": 1656.0}, blocking=True
    )
    assert engine.data["last_fit"]["joint"] is True
    assert engine.a != 0.1
    assert engine.a > 0
    assert engine.k > 0


async def test_no_history_import(hass: HomeAssistant, rome, entry_data: dict) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={**entry_data, "history": []}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.readings) == 1
    assert entry.runtime_data.k == 0.005


async def _press(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": eid(hass, entry, "button", "register_reading")},
        blocking=True,
    )


async def _set_value(hass: HomeAssistant, entry: MockConfigEntry, value: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": eid(hass, entry, "number", "new_reading"), "value": value},
        blocking=True,
    )


async def _set_date(hass: HomeAssistant, entry: MockConfigEntry, value: str) -> None:
    await hass.services.async_call(
        "datetime",
        "set_value",
        {"entity_id": eid(hass, entry, "datetime", "reading_date"), "datetime": value},
        blocking=True,
    )


async def test_button_with_past_date_splits_index(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    engine = entry.runtime_data
    date_eid = eid(hass, entry, "datetime", "reading_date")
    assert hass.states.get(date_eid).state == "unknown"
    hass.states.async_set(S1, "100")
    await hass.async_block_till_done()
    for _ in range(4):
        await _advance(hass, freezer, timedelta(hours=1))  # 12:00 -> 16:00 UTC
    await _set_value(hass, entry, 1662.0)
    # 15:00 local = 14:00 UTC, 2 h after 'start' -> 200 %·h
    await _set_date(hass, entry, "2025-10-28 15:00:00")
    assert hass.states.get(date_eid).state == "2025-10-28T14:00:00+00:00"
    await _press(hass, entry)
    last = engine.last_reading
    assert last["date"].startswith("2025-10-28T14:00:00")
    assert last["index"] == pytest.approx(200.0, rel=1e-3)
    assert engine.index_since == pytest.approx(200.0, rel=1e-3)
    # Both fields are cleared after success
    assert hass.states.get(date_eid).state == "unknown"
    assert engine.pending_reading is None
    assert engine.pending_reading_date is None
    num = hass.states.get(eid(hass, entry, "number", "new_reading"))
    assert float(num.state) == 1662.0  # shows the last reading again


async def test_button_empty_date_uses_now(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    hass.states.async_set(S1, "100")
    await _advance(hass, freezer, timedelta(hours=3))
    await _set_value(hass, entry, 1657.0)
    await _press(hass, entry)
    last = entry.runtime_data.last_reading
    assert dt_util.parse_datetime(last["date"]) == dt_util.utcnow()
    assert last["index"] == pytest.approx(300.0)
    assert entry.runtime_data.index_since == 0


async def test_invalid_reading_date_rejected(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = setup_entry
    engine = entry.runtime_data
    date_eid = eid(hass, entry, "datetime", "reading_date")
    for value, key in [
        ("2025-10-27 19:00:00", "reading_too_old"),
        ("2025-10-29 20:00:00", "reading_in_future"),
    ]:
        with pytest.raises(ServiceValidationError) as exc:
            await _set_date(hass, entry, value)
        assert exc.value.translation_key == key
    assert hass.states.get(date_eid).state == "unknown"

    # A date valid when set but older than a reading registered later is
    # rejected on press, and nothing is cleared.
    await _set_date(hass, entry, "2025-10-28 09:00:00")
    await hass.services.async_call(
        DOMAIN,
        "register_reading",
        {"reading": 1652.0, "date": "2025-10-28 10:00:00"},
        blocking=True,
    )
    # the service registration clears the pending date too; set it again
    engine.data["pending_reading_date"] = "2025-10-28T08:00:00+00:00"
    await _set_value(hass, entry, 1654.0)
    with pytest.raises(ServiceValidationError) as exc:
        await _press(hass, entry)
    assert exc.value.translation_key == "reading_too_old"
    assert engine.pending_reading == 1654.0
    assert engine.pending_reading_date is not None
    assert len(engine.readings) == 9


async def test_reading_date_persists_across_restart(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = setup_entry
    await _set_value(hass, entry, 1653.0)
    await _set_date(hass, entry, "2025-10-28 08:30:00")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    engine = entry.runtime_data
    assert engine.pending_reading == 1653.0
    assert engine.pending_reading_date == dt_util.parse_datetime(
        "2025-10-28 07:30:00+00:00"
    )
    state = hass.states.get(eid(hass, entry, "datetime", "reading_date"))
    assert state.state == "2025-10-28T07:30:00+00:00"
