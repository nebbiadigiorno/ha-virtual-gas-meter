# Changelog

## Unreleased
- README rewritten in English with a full description, entity/service tables and screenshots (`docs/images/`, demo data).
- Reading history attribute `errore_m3` renamed to `error_m3`; *Last interval error* attribute `reale_m3` renamed to `real_m3`.
- Removed the leftover Italian manufacturer name from the device info.

## 0.2.0
- First public release as **Virtual Gas Meter** (domain `virtual_gas_meter`).
- English identifiers, entity keys and services (`register_reading`, `delete_reading`, `recalculate`); full Italian translation.
- Config flow: no preselected sensors; any number of sensors with unit `%` (any brand); sensors with a different unit are rejected.
- Generic starting coefficients (a = 0.1 m³/day, k = 0.005 m³/%·h), refined automatically from readings.
- Removed the built-in reading history import.
- Daily/monthly attribute `base_share` (was a cooking-specific name).
- MIT license, HACS metadata, CI workflow (hassfest, HACS, ruff, pytest).
