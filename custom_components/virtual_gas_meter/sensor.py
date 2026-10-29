"""Sensors for Virtual Gas Meter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import GasConfigEntry, periods as per
from .engine import GasMeterEngine
from .entity import GasEntity


def _local(iso: str) -> str:
    return dt_util.as_local(dt_util.parse_datetime(iso)).isoformat()


def _readings_attr(engine: GasMeterEngine) -> list[dict[str, Any]]:
    return [
        {
            "date": _local(r["date"]),
            "value": r["value"],
            "index": None if r.get("index") is None else round(r["index"], 1),
            "days": None if r.get("days") is None else round(r["days"], 3),
            "m3": r.get("m3"),
            "estimated_m3": r.get("estimated_m3"),
            "error_m3": r.get("error_m3"),
            "excluded_from_fit": bool(r.get("excluded")),
        }
        for r in engine.readings
    ]


def _total_attrs(e: GasMeterEngine) -> dict[str, Any]:
    return {
        "raw_estimate": round(e.raw_total, 4),
        "compensation_active": round(e.held_offset, 4),
        "last_update": _local(e.data["last_update"]),
    }


def _since_attrs(e: GasMeterEngine) -> dict[str, Any]:
    return {
        "days": round(e.days_since, 4),
        "base_share": round(e.a * e.days_since, 4),
        "heating_share": round(e.k * e.index_since, 4),
    }


def _error_attrs(e: GasMeterEngine) -> dict[str, Any]:
    r = e.last_reading
    attrs: dict[str, Any] = {
        "date": _local(r["date"]),
        "estimated_m3": r.get("estimated_m3"),
        "real_m3": r.get("m3"),
    }
    if r.get("m3"):
        attrs["error_percent"] = round(100 * r["error_m3"] / r["m3"], 2)
    return attrs


def _fit_attrs(e: GasMeterEngine) -> dict[str, Any]:
    return {
        "auto_refit_k": e.auto_k,
        "a_fixed": e.fix_a,
        "last_fit": e.data.get("last_fit"),
    }


def _period_value(kind: str) -> Callable[[GasMeterEngine], Any]:
    def fn(e: GasMeterEngine) -> float | None:
        value = per.total(e.period(kind))
        return None if value is None else round(value, 4)

    return fn


def _period_attrs(kind: str) -> Callable[[GasMeterEngine], dict[str, Any]]:
    current = kind in ("day", "month")

    def fn(e: GasMeterEngine) -> dict[str, Any]:
        b = e.period(kind)
        if b is None:
            return {}
        attrs: dict[str, Any] = {
            "period": b["key"],
            "base_share": round(b["base"], 4),
            "heating_share": round(b["heat"], 4),
            "heating_index": round(b["index"], 2),
        }
        if current:
            attrs["start_time"] = _local(b["start"])
        if b.get("missing_h"):
            attrs["hours_without_data"] = round(b["missing_h"], 2)
        return attrs

    return fn


def _period_reset(kind: str) -> Callable[[GasMeterEngine], datetime | None]:
    def fn(e: GasMeterEngine) -> datetime | None:
        b = e.period(kind)
        return dt_util.parse_datetime(b["period_start"]) if b else None

    return fn


@dataclass(frozen=True, kw_only=True)
class GasSensorDescription(SensorEntityDescription):
    """Describe a gas sensor."""

    value_fn: Callable[[GasMeterEngine], Any]
    attrs_fn: Callable[[GasMeterEngine], dict[str, Any]] | None = None
    last_reset_fn: Callable[[GasMeterEngine], datetime | None] | None = None


SENSORS: tuple[GasSensorDescription, ...] = (
    GasSensorDescription(
        key="estimated_gas",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=lambda e: round(e.total, 4),
        attrs_fn=_total_attrs,
    ),
    GasSensorDescription(
        key="heating_index_since_last_reading",
        native_unit_of_measurement="%·h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda e: round(e.index_since, 2),
    ),
    GasSensorDescription(
        key="consumption_since_last_reading",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda e: round(e.consumption_since, 4),
        attrs_fn=_since_attrs,
    ),
    GasSensorDescription(
        key="coefficient_k",
        native_unit_of_measurement="m³/%·h",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=6,
        value_fn=lambda e: round(e.k, 8),
        attrs_fn=_fit_attrs,
    ),
    GasSensorDescription(
        key="base_consumption_a",
        native_unit_of_measurement="m³/d",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=4,
        value_fn=lambda e: round(e.a, 6),
        attrs_fn=_fit_attrs,
    ),
    GasSensorDescription(
        key="last_reading",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=lambda e: e.last_reading["value"],
        attrs_fn=lambda e: {
            "date": _local(e.last_reading["date"]),
            "readings": _readings_attr(e),
        },
    ),
    GasSensorDescription(
        key="last_interval_error",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=3,
        value_fn=lambda e: e.last_reading.get("error_m3"),
        attrs_fn=_error_attrs,
    ),
    # Daily / monthly consumption straight from the model increments.
    # Current periods: state_class total + last_reset (= local midnight or
    # first of month), so long-term statistics restart cleanly at the boundary.
    GasSensorDescription(
        key="consumption_today",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=_period_value("day"),
        attrs_fn=_period_attrs("day"),
        last_reset_fn=_period_reset("day"),
    ),
    # Closed periods: no state_class, so no misleading statistics.
    GasSensorDescription(
        key="consumption_yesterday",
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=_period_value("prev_day"),
        attrs_fn=_period_attrs("prev_day"),
    ),
    GasSensorDescription(
        key="consumption_this_month",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=_period_value("month"),
        attrs_fn=_period_attrs("month"),
        last_reset_fn=_period_reset("month"),
    ),
    GasSensorDescription(
        key="consumption_last_month",
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=_period_value("prev_month"),
        attrs_fn=_period_attrs("prev_month"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(GasSensor(entry.runtime_data, d) for d in SENSORS)


class GasSensor(GasEntity, SensorEntity):
    """A virtual gas meter sensor."""

    entity_description: GasSensorDescription
    # The full readings list can grow: keep it out of the recorder database.
    _unrecorded_attributes = frozenset({"readings", "last_fit"})

    def __init__(
        self, engine: GasMeterEngine, description: GasSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(engine, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the state."""
        return self.entity_description.value_fn(self.engine)

    @property
    def last_reset(self) -> datetime | None:
        """Return the start of the current period (total sensors only)."""
        if self.entity_description.last_reset_fn is None:
            return None
        return self.entity_description.last_reset_fn(self.engine)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.engine)
