# Virtual Gas Meter

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/nebbiadigiorno/ha-virtual-gas-meter/actions/workflows/validate.yml/badge.svg)](https://github.com/nebbiadigiorno/ha-virtual-gas-meter/actions/workflows/validate.yml)

Home Assistant custom integration that **estimates gas consumption in real time** from the
heating power (%) reported by your thermostatic valves / heating zones, and realigns itself
every time you enter a real meter reading. Works with any sensor whose unit is `%`
(e.g. tado X `sensor.*_heating_power`, but any brand is fine).

🇮🇹 Italiano: [README.it.md](README.it.md)

## Model

    consumption (m³) = a · days + k · I,   I = Σ over selected sensors of (% · hours)

- `a` – base daily use (hot water, cooking), m³/day.
- `I` – heating index: each sensor's % integrated over time (%·h). `unavailable`/`unknown` count as 0.
- `k` – m³ per %·h.

The default values (a = 0.1 m³/day, k = 0.005 m³/%·h) are **only rough starting points**,
not calibrated for any home. With *auto refit k* enabled (default), every new reading
refits k by least squares over all valid intervals with a fixed:
`k = Σ I·(m³ − a·d) / Σ I²`. If *keep a fixed* is disabled, a and k are fitted together
(needs at least 3 valid intervals). The more readings you enter (ideally during the
heating season, a few weeks apart), the better the estimate.

## Installation

### HACS (custom repository)
1. HACS → ⋮ → *Custom repositories* → add `https://github.com/nebbiadigiorno/ha-virtual-gas-meter`, category **Integration**.
2. Search **Virtual Gas Meter**, download, restart Home Assistant.

### Manual
Copy `custom_components/virtual_gas_meter` into `<config>/custom_components/` and restart.

Requires the `recorder` (default in HA OS): long-term statistics are used to backfill
gaps after restarts or downtime.

## Configuration
*Settings → Devices & services → Add integration → Virtual Gas Meter*, then:
1. Select any number of heating-power sensors (unit `%`).
2. Leave a and k at the defaults or enter your own.
3. Enter your last real meter reading (m³) and when you took it.

Options (later): sensors, a, k, *auto refit k*, *keep a fixed*.

## Entering readings
- **UI:** type the value in *New meter reading*, optionally set *Reading date*, press
  *Register reading* (no date = now). Readings lower than the previous one, older than the
  last reading or in the future are rejected.
- **Services:**

  ```yaml
  action: virtual_gas_meter.register_reading
  data:
    reading: 1234.567
    date: "2025-01-31 20:00:00"   # optional, local time; default now
  ```

  - `virtual_gas_meter.delete_reading` (`date`: day or exact date/time) – its interval is merged into the next one.
  - `virtual_gas_meter.recalculate` – refit coefficients now.

## Energy dashboard
Add **Estimated gas** (`device_class: gas`, `state_class: total_increasing`, m³) under
*Settings → Dashboards → Energy → Gas consumption*. It never decreases: if a real reading
is below the estimate, the value holds until the estimate catches up; if above, it steps up.

## Entities
| Entity | Description |
| --- | --- |
| Estimated gas | Virtual meter (m³) |
| Heating index since last reading | %·h |
| Estimated consumption since last reading | m³ |
| Heating coefficient k / Base consumption a | current coefficients |
| Last reading | value; attributes: date, reading history |
| Last interval error | estimate − real for the last closed interval |
| Consumption today / yesterday / this month / last month | per-period model increments (attributes `base_share`, `heating_share`, `heating_index`) |
| New meter reading / Reading date / Register reading | manual input |

Periods follow Home Assistant's time zone (DST days last 23/25 h). A new k applies only
from that moment on; already accumulated daily/monthly values are not restated.

## Limitations / accuracy
This is an **estimate**, not a certified measurement. Accuracy depends on how well
heating % tracks boiler gas use (boiler modulation, hot-water use, outdoor temperature,
zones not covered by sensors). Do not use it for billing. Single instance only.

## Development
`pip install pytest-homeassistant-custom-component ruff && ruff check . && pytest`

## License
MIT – see [LICENSE](LICENSE).
