# Changelog

## Unreleased
- README rewritten in English with a full description, entity/service tables and screenshots (`docs/images/`, demo data).
- Reading history attribute `errore_m3` renamed to `error_m3`; *Last interval error* attribute `reale_m3` renamed to `real_m3`.
- Removed the leftover Italian manufacturer name from the device info.
- Fix: heating index lost when the periodic tick ran before a pending `state_changed` listener (on older Home Assistant releases, e.g. 2026.2, listeners are dispatched on a later loop iteration). Sensor changes are now applied from the state machine at their `last_changed` time, independent of callback order.
- Minimum Home Assistant version raised to 2025.8.0 (the options flow uses `OptionsFlowWithReload`; 2025.1 could not load the integration).
- CI tests against both the minimum supported (2025.8.0) and the latest Home Assistant.

## 0.2.0
- First public release as **Virtual Gas Meter** (domain `virtual_gas_meter`).
- English identifiers, entity keys and services (`register_reading`, `delete_reading`, `recalculate`); full Italian translation.
- Config flow: no preselected sensors; any number of sensors with unit `%` (any brand); sensors with a different unit are rejected.
- Generic starting coefficients (a = 0.1 m³/day, k = 0.005 m³/%·h), refined automatically from readings.
- Removed the built-in reading history import.
- Daily/monthly attribute `base_share` (was a cooking-specific name).
- MIT license, HACS metadata, CI workflow (hassfest, HACS, ruff, pytest).
