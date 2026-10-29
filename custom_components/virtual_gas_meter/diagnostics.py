"""Diagnostics for Virtual Gas Meter."""

from __future__ import annotations

import copy
from typing import Any

from homeassistant.core import HomeAssistant

from . import GasConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GasConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (no secrets are involved)."""
    engine = entry.runtime_data
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "model": {
            "a": engine.a,
            "k": engine.k,
            "auto_k": engine.auto_k,
            "fix_a": engine.fix_a,
            "total_published": engine.total,
            "total_raw": engine.raw_total,
            "held_offset": engine.held_offset,
            "index_since": engine.index_since,
            "days_since": engine.days_since,
        },
        "current_values": {
            eid: hass.states.get(eid).state if hass.states.get(eid) else None
            for eid in engine.sensors
        },
        "last_backfill": engine.backfill_report,
        "stored": copy.deepcopy(engine.data),
    }
