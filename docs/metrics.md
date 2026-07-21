# Metric catalogue

Every reading pluto emits uses the metric names and units below, on
every sink (MQTT, HTTP, SQLite, CSV, Prometheus). The machine-readable
version of this table lives in `pluto/model.py` (`METRICS`); units
follow Home Assistant / OpenMetrics conventions.

| Metric | Unit | Description | Driver | HA device class | Prometheus metric |
|---|---|---|---|---|---|
| `temperature` | °C | Ambient temperature, CPU-heat compensated | `bme280` | `temperature` | `pluto_temperature_celsius` |
| `raw_temperature` | °C | Uncompensated BME280 temperature | `bme280` | `temperature` | `pluto_raw_temperature_celsius` |
| `humidity` | % | Relative humidity | `bme280` | `humidity` | `pluto_humidity_percent` |
| `pressure` | hPa | Barometric pressure | `bme280` | `atmospheric_pressure` | `pluto_pressure_hpa` |
| `lux` | lx | Illuminance | `ltr559` | `illuminance` | `pluto_light_lux` |
| `proximity` | — | LTR559 proximity counts, unitless | `ltr559` | — | `pluto_proximity` |
| `oxidising` | kΩ | MICS6814 oxidising gas resistance | `mics6814` | — | `pluto_gas_oxidising_kohms` |
| `reducing` | kΩ | MICS6814 reducing gas resistance | `mics6814` | — | `pluto_gas_reducing_kohms` |
| `nh3` | kΩ | MICS6814 NH3 gas resistance | `mics6814` | — | `pluto_gas_nh3_kohms` |
| `pm1` | µg/m³ | Particulate matter ≤ 1.0 µm | `pms5003` | `pm1` | `pluto_particulates_ug_per_m3{size="1.0"}` |
| `pm25` | µg/m³ | Particulate matter ≤ 2.5 µm | `pms5003` | `pm25` | `pluto_particulates_ug_per_m3{size="2.5"}` |
| `pm10` | µg/m³ | Particulate matter ≤ 10 µm | `pms5003` | `pm10` | `pluto_particulates_ug_per_m3{size="10"}` |
| `noise` | dB | Noise level relative to full scale (uncalibrated dBFS: 20·log10 of the mic amplitude, 0 dB = full scale, floored at −60 dB) | `microphone` | `sound_pressure` | `pluto_noise_decibels` |

The `mock` driver mimics all of the above with the same names and
units. Third-party drivers add their own metrics; they should follow
the same conventions (snake_case names, HA-style unit strings) and
must not reuse the reserved metadata keys below.

## Snapshot metadata

Every snapshot carries these fields alongside the metric values:

| Field | Type | Meaning |
|---|---|---|
| `timestamp` | ISO 8601 string | Read time, always UTC (e.g. `2026-07-20T20:15:03.123Z`) |
| `device` | string | Device id from `[device] id`, defaulting to the hostname |
| `location` | string | `[device] location`; omitted from JSON payloads when empty |
| `description` | string | `[device] description`; omitted from JSON payloads when empty |
| `version` | string | The pluto version that produced the snapshot |
| `time_uncertain` | bool | `true` while the system clock looks unsynchronised (year < 2024, e.g. an RTC-less Pi before NTP sync); omitted from JSON payloads when `false` |

## Per-sink notes

- **MQTT / HTTP** publish one flat JSON document per snapshot: the ok
  metric values (rounded to 3 decimals) plus the metadata above.
  Missing or failed sensors are omitted rather than sent as `null`.
- **Prometheus** exposes one gauge per metric with `device` and
  `location` labels; missing sensors report `NaN`, and the particulate
  sizes share one gauge with a `size` label.
- **SQLite** stores the metadata as columns and *every* reading —
  including missing/error ones — as JSON
  (`{"value": …, "unit": …, "quality": "ok|missing|error", "driver": …}`),
  so it doubles as a diagnostics log.
- **CSV** stores the metadata columns followed by one column per
  metric; non-ok readings are left empty.
