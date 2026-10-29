"""Fixtures for Virtual Gas Meter tests."""

from __future__ import annotations

from collections.abc import Generator

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_gas_meter.const import (
    CONF_A,
    CONF_HISTORY,
    CONF_K,
    CONF_READING_DATE,
    CONF_READING_VALUE,
    CONF_SENSORS,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

S1 = "sensor.zone_1_heating_power"
S2 = "sensor.zone_2_heating_power"
# Synthetic readings (local 20:00): values and heating indices are made up.
HISTORY = (
    ("2025-01-05 20:00:00", 500.0, None, False),
    ("2025-03-01 20:00:00", 1200.0, 50000.0, True),
    ("2025-03-29 20:00:00", 1450.0, 49000.0, False),
    ("2025-04-28 20:00:00", 1610.0, 31000.0, False),
    ("2025-06-02 20:00:00", 1613.5, 0.0, False),
    ("2025-08-30 20:00:00", 1622.4, 0.0, False),
    ("2025-09-27 20:00:00", 1625.2, 0.0, False),
    ("2025-10-27 20:00:00", 1650.0, 4000.0, False),
)
NOW = "2025-10-28 12:00:00+00:00"


@pytest.fixture(autouse=True)
def auto_enable(recorder_mock, enable_custom_integrations: None) -> Generator[None]:
    """Enable custom integrations."""
    yield


@pytest.fixture
async def rome(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Use the user's time zone and a fixed 'now'."""
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Rome")


@pytest.fixture
def entry_data() -> dict:
    """Config entry data with two sensors."""
    return {
        CONF_SENSORS: [S1, S2],
        CONF_A: 0.1,
        CONF_K: 0.005,
        CONF_READING_VALUE: 1650.0,
        CONF_READING_DATE: "2025-10-27 20:00:00",
        CONF_HISTORY: [list(r) for r in HISTORY],
    }


@pytest.fixture
async def setup_entry(hass: HomeAssistant, rome, entry_data: dict) -> MockConfigEntry:
    """Set up a config entry (recorder has no statistics -> gap skipped)."""
    hass.states.async_set(S1, "0", {"unit_of_measurement": "%"})
    hass.states.async_set(S2, "0", {"unit_of_measurement": "%"})
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    return entry


def eid(hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str) -> str:
    """Return the entity_id for one of our entities by translation key."""
    from homeassistant.helpers import entity_registry as er

    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id, key
    return entity_id
