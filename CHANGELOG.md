# Changelog

## 0.3.0
- Sensors that are `unavailable`/`unknown` (e.g. during a cloud outage of the thermostat vendor) are no longer counted as 0 %. Their power is estimated from a per-sensor hourly profile (one value per local hour, learnt from the sensor's own values and seeded on first start from 7 days of recorder statistics).
- Estimated hours are reported as `estimated_hours` on the period sensors, on *Estimated consumption since last reading* (with `estimated_percent` and `estimated_index`), on *Last interval error* and in the readings history.
- An interval in which more than 10 % of the time was estimated is automatically excluded from the coefficient fit (`exclusion: automatic`), so outages no longer bias k.
- New service `exclude_reading` (`date`, `exclude`) to exclude a reading's interval from the fit, or include it again, and refit.
- *Last interval error* now also shows `excluded_from_fit`.
- Existing stored data is kept: only new fields are added.

## 0.2.0
- README rewritten in English with a full description, entity/service tables and screenshots (`docs/images/`, demo data).
- Reading history attribute `errore_m3` renamed to `error_m3`; *Last interval error* attribute `reale_m3` renamed to `real_m3`.
- Removed the leftover Italian manufacturer name from the device info.
- Fix: heating index lost when the periodic tick ran before a pending `state_changed` listener (on older Home Assistant releases, e.g. 2026.2, listeners are dispatched on a later loop iteration). Sensor changes are now applied from the state machine at their `last_changed` time, independent of callback order.
- Minimum Home Assistant version raised to 2025.8.0 (the options flow uses `OptionsFlowWithReload`; 2025.1 could not load the integration).
- CI tests against both the minimum supported (2025.8.0) and the latest Home Assistant.

- First public release as **Virtual Gas Meter** (domain `virtual_gas_meter`).
- English identifiers, entity keys and services (`register_reading`, `delete_reading`, `recalculate`); full Italian translation.
- Config flow: no preselected sensors; any number of sensors with unit `%` (any brand); sensors with a different unit are rejected.
- Generic starting coefficients (a = 0.1 m³/day, k = 0.005 m³/%·h), refined automatically from readings.
- Removed the built-in reading history import.
- Daily/monthly attribute `base_share` (was a cooking-specific name).
- MIT license, HACS metadata, CI workflow (hassfest, HACS, ruff, pytest).
