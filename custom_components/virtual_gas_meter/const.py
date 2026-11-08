"""Constants for the Virtual Gas Meter integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "virtual_gas_meter"
NAME: Final = "Virtual Gas Meter"

# Config entry keys
CONF_SENSORS: Final = "sensors"
CONF_A: Final = "a"
CONF_K: Final = "k"
CONF_READING_VALUE: Final = "reading_value"
CONF_READING_DATE: Final = "reading_date"
CONF_AUTO_K: Final = "auto_k"
CONF_FIX_A: Final = "fix_a"

# Generic starting points only. They are NOT calibrated for any home: k is
# refitted automatically from your meter readings (least squares), and a can
# be refitted too (disable "keep a fixed" in the options).
DEFAULT_A: Final = 0.1  # m3/day, base use (hot water, cooking)
DEFAULT_K: Final = 0.005  # m3 per %·h
DEFAULT_AUTO_K: Final = True
DEFAULT_FIX_A: Final = True

# Optional hidden config-entry key: list of past readings
# (local "YYYY-MM-DD HH:MM:SS", value m3, heating index %·h or None, excluded)
# imported on first setup. Not exposed in the UI.
CONF_HISTORY: Final = "history"

STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 10  # seconds
TICK_INTERVAL: Final = timedelta(seconds=60)
CHECKPOINT_INTERVAL: Final = timedelta(hours=1)
# Gaps shorter than this are integrated with the last known values instead of
# being backfilled from statistics.
BACKFILL_MIN_GAP: Final = timedelta(minutes=5)

# Sensors that are unavailable/unknown are not counted as 0 %: their power is
# estimated from a per-sensor hourly profile (one value per local hour of the
# day), learnt from the sensor's own valid values. Each hour slot follows an
# exponential average with this time constant measured in time spent in that
# slot, so 3 h of slot time ~ the same hour of the last 3 days.
PROFILE_TAU: Final = timedelta(hours=3)
# On first start the profile is seeded from this many days of recorder
# hourly statistics.
PROFILE_SEED_DAYS: Final = 7
# An interval whose estimated (filled) share of sensor-hours exceeds this
# fraction is excluded automatically from the coefficient fit.
MAX_FILLED_FRACTION: Final = 0.10

SERVICE_REGISTER: Final = "register_reading"
SERVICE_DELETE: Final = "delete_reading"
SERVICE_RECALC: Final = "recalculate"
SERVICE_EXCLUDE: Final = "exclude_reading"
ATTR_READING: Final = "reading"
ATTR_DATE: Final = "date"
ATTR_EXCLUDE: Final = "exclude"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"


def signal_update(entry_id: str) -> str:
    """Return the dispatcher signal used to refresh entities."""
    return f"{DOMAIN}_{entry_id}_update"
