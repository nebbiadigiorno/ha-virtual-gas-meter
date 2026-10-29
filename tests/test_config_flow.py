"""Config and options flow tests."""

from __future__ import annotations

import pytest

from custom_components.virtual_gas_meter.const import (
    CONF_A,
    CONF_AUTO_K,
    CONF_FIX_A,
    CONF_K,
    CONF_READING_DATE,
    CONF_READING_VALUE,
    CONF_SENSORS,
    DEFAULT_K,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import S1, eid


async def test_user_flow(hass: HomeAssistant, rome) -> None:
    hass.states.async_set(S1, "0", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.outdoor_temp", "12", {"unit_of_measurement": "°C"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    defaults = {str(k): k.default() for k in schema if callable(k.default)}
    assert CONF_SENSORS not in defaults  # no preselected sensors
    assert defaults[CONF_K] == DEFAULT_K
    defaults.update(
        {
            CONF_SENSORS: [S1],
            CONF_READING_VALUE: 1234.5,
            CONF_READING_DATE: "2025-10-27 20:00:00",
        }
    )

    # Sensors must be in %
    bad_unit = {**defaults, CONF_SENSORS: [S1, "sensor.outdoor_temp"]}
    result1 = await hass.config_entries.flow.async_configure(
        result["flow_id"], bad_unit
    )
    assert result1["errors"] == {CONF_SENSORS: "not_percent"}

    # Future date is rejected
    bad = {**defaults, CONF_READING_DATE: "2030-01-01 20:00:00"}
    result2 = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {CONF_READING_DATE: "date_in_future"}

    # Defaults create the entry
    result3 = await hass.config_entries.flow.async_configure(
        result["flow_id"], defaults
    )
    assert result3["type"] is FlowResultType.CREATE_ENTRY
    assert result3["title"] == "Virtual Gas Meter"
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert hass.states.get(eid(hass, entry, "sensor", "estimated_gas")) is not None

    # Only one instance allowed
    result4 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result4["type"] is FlowResultType.ABORT


@pytest.mark.usefixtures("setup_entry")
async def test_options_flow(hass: HomeAssistant, setup_entry) -> None:
    entry = setup_entry
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SENSORS: [S1],
            CONF_A: 0.06,
            CONF_K: 0.004,
            CONF_AUTO_K: False,
            CONF_FIX_A: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    engine = entry.runtime_data
    assert engine.sensors == [S1]
    assert engine.a == 0.06
    assert engine.k == 0.004
    assert engine.auto_k is False
    assert engine.fix_a is False
    assert (
        float(hass.states.get(eid(hass, entry, "sensor", "coefficient_k")).state)
        == 0.004
    )
