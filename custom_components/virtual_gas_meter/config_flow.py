"""Config and options flow for Virtual Gas Meter."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_A,
    CONF_AUTO_K,
    CONF_FIX_A,
    CONF_K,
    CONF_READING_DATE,
    CONF_READING_VALUE,
    CONF_SENSORS,
    DEFAULT_A,
    DEFAULT_AUTO_K,
    DEFAULT_FIX_A,
    DEFAULT_K,
    DOMAIN,
    NAME,
)
from .engine import parse_local_datetime

SENSOR_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", multiple=True)
)
A_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=10,
        step="any",
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="m³/day",
    )
)
K_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=1,
        step="any",
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="m³/%·h",
    )
)


def _validate(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    sensors = user_input.get(CONF_SENSORS) or []
    if not sensors:
        errors[CONF_SENSORS] = "no_sensors"
    elif any(
        (st := hass.states.get(s)) is not None
        and st.attributes.get("unit_of_measurement") != "%"
        for s in sensors
    ):
        errors[CONF_SENSORS] = "not_percent"
    if CONF_READING_DATE in user_input:
        try:
            when = parse_local_datetime(user_input[CONF_READING_DATE])
        except ValueError:
            errors[CONF_READING_DATE] = "invalid_date"
        else:
            if when > dt_util.utcnow():
                errors[CONF_READING_DATE] = "date_in_future"
    if user_input.get(CONF_K, 1) <= 0:
        errors[CONF_K] = "invalid_k"
    return errors


class VirtualGasMeterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for sensors, coefficients and the last real reading."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title=NAME, data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_SENSORS): SENSOR_SELECTOR,
                vol.Required(CONF_A, default=DEFAULT_A): A_SELECTOR,
                vol.Required(CONF_K, default=DEFAULT_K): K_SELECTOR,
                vol.Required(CONF_READING_VALUE): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=9999999,
                        step=0.001,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="m³",
                    )
                ),
                vol.Required(CONF_READING_DATE): selector.DateTimeSelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowHandler:
        """Return the options flow."""
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlowWithReload):
    """Change sensors, coefficients and refit behaviour."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        conf = {**self.config_entry.data, **self.config_entry.options}
        engine = getattr(self.config_entry, "runtime_data", None)
        cur_a = engine.a if engine else conf.get(CONF_A, DEFAULT_A)
        cur_k = engine.k if engine else conf.get(CONF_K, DEFAULT_K)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SENSORS, default=conf.get(CONF_SENSORS, [])
                ): SENSOR_SELECTOR,
                vol.Required(CONF_A, default=round(cur_a, 6)): A_SELECTOR,
                vol.Required(CONF_K, default=round(cur_k, 8)): K_SELECTOR,
                vol.Required(
                    CONF_AUTO_K, default=conf.get(CONF_AUTO_K, DEFAULT_AUTO_K)
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_FIX_A, default=conf.get(CONF_FIX_A, DEFAULT_FIX_A)
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
