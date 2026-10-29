"""Datetime entity to choose the date of the next meter reading."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
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
    """Set up the datetime entity."""
    async_add_entities([ReadingDateEntity(entry.runtime_data)])


class ReadingDateEntity(GasEntity, DateTimeEntity):
    """Date/time of the pending reading; unset means 'now' when registering.

    The value is persisted in the integration Store together with the pending
    reading value, so it survives restarts.
    """

    def __init__(self, engine: GasMeterEngine) -> None:
        """Initialize."""
        super().__init__(engine, "reading_date")

    @property
    def native_value(self) -> datetime | None:
        """Return the chosen datetime, or None (unknown) when unset."""
        return self.engine.pending_reading_date

    async def async_set_value(self, value: datetime) -> None:
        """Validate and store the chosen datetime (not yet registered)."""
        self.engine.set_pending_reading_date(value)
