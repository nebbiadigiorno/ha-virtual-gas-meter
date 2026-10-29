"""Number entity to type a new meter reading."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GasConfigEntry
from .engine import GasMeterEngine
from .entity import GasEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number entity."""
    async_add_entities([NewReadingNumber(entry.runtime_data)])


class NewReadingNumber(GasEntity, NumberEntity):
    """Value of the next real reading, confirmed with the 'Register reading' button."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 9_999_999
    _attr_native_step = 0.001
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    def __init__(self, engine: GasMeterEngine) -> None:
        """Initialize."""
        super().__init__(engine, "new_reading")

    @property
    def native_value(self) -> float | None:
        """Return the pending value, or the last reading as a starting point."""
        pending = self.engine.pending_reading
        return pending if pending is not None else self.engine.last_reading["value"]

    async def async_set_native_value(self, value: float) -> None:
        """Store the typed value (not yet registered)."""
        self.engine.set_pending_reading(float(value))
