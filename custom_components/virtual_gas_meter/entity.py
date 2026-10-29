"""Base entity for Virtual Gas Meter."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME, signal_update
from .engine import GasMeterEngine


class GasEntity(Entity):
    """Common entity: one device per config entry, refreshed by dispatcher."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: GasMeterEngine, key: str) -> None:
        """Initialize the entity."""
        self.engine = engine
        self._attr_translation_key = key
        self._attr_unique_id = f"{engine.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, engine.entry.entry_id)},
            name=NAME,
            model="m³ = a·days + k·Σ%·h",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to engine updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self.engine.entry.entry_id), self._handle
            )
        )

    @callback
    def _handle(self) -> None:
        self.async_write_ha_state()
