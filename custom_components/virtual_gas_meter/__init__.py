"""The Virtual Gas Meter integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DATE,
    ATTR_READING,
    DOMAIN,
    SERVICE_DELETE,
    SERVICE_RECALC,
    SERVICE_REGISTER,
)
from .engine import GasMeterEngine, parse_local_datetime

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SENSOR,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type GasConfigEntry = ConfigEntry[GasMeterEngine]

REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_READING): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional(ATTR_DATE): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)
DELETE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DATE): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)
RECALC_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


def _engine(hass: HomeAssistant, call: ServiceCall) -> GasMeterEngine:
    """Return the engine targeted by a service call."""
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )
    return entries[0].runtime_data


def _parse_date(value: str) -> object:
    try:
        return parse_local_datetime(value)
    except (ValueError, TypeError) as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_date",
            translation_placeholders={"date": str(value)},
        ) from err


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services."""

    async def register(call: ServiceCall) -> ServiceResponse:
        engine = _engine(hass, call)
        when = _parse_date(call.data[ATTR_DATE]) if ATTR_DATE in call.data else None
        reading = engine.register_reading(call.data[ATTR_READING], when)
        return {"reading": reading, "a": engine.a, "k": engine.k}

    async def delete(call: ServiceCall) -> ServiceResponse:
        engine = _engine(hass, call)
        removed = engine.delete_reading(_parse_date(call.data[ATTR_DATE]))
        return {"deleted": removed, "a": engine.a, "k": engine.k}

    async def recalc(call: ServiceCall) -> ServiceResponse:
        engine = _engine(hass, call)
        fit = engine.recalculate()
        return {"fit": fit, "a": engine.a, "k": engine.k}

    hass.services.async_register(
        DOMAIN, SERVICE_REGISTER, register, REGISTER_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE, delete, DELETE_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECALC, recalc, RECALC_SCHEMA, SupportsResponse.OPTIONAL
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GasConfigEntry) -> bool:
    """Set up the virtual gas meter from a config entry."""
    engine = GasMeterEngine(hass, entry)
    await engine.async_load()
    await engine.async_start()
    entry.runtime_data = engine
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GasConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: GasConfigEntry) -> None:
    """Delete stored data when the entry is removed."""
    await GasMeterEngine(hass, entry).async_remove()
