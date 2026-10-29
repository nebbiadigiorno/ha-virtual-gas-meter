"""Button to register the reading typed in the number entity."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GasConfigEntry
from .const import DOMAIN
from .engine import GasMeterEngine
from .entity import GasEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button."""
    async_add_entities([RegisterReadingButton(entry.runtime_data)])


class RegisterReadingButton(GasEntity, ButtonEntity):
    """Register the pending reading at the chosen date (or now)."""

    def __init__(self, engine: GasMeterEngine) -> None:
        """Initialize."""
        super().__init__(engine, "register_reading")

    async def async_press(self) -> None:
        """Register the reading."""
        value = self.engine.pending_reading
        if value is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_pending_reading"
            )
        # Uses 'Reading date' when set, otherwise now; both fields are
        # cleared by the engine after a successful registration.
        self.engine.register_reading(value, self.engine.pending_reading_date)
