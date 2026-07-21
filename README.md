# Pluto

A little sensor dashboard for a **Raspberry Pi Zero 2 W** wearing a
**Pimoroni Enviro+** pHAT. It reads every onboard sensor and shows the
values on the Enviro+'s built-in 0.96" LCD, cycling through a set of
pages.

## Sensors shown

| Sensor | Values |
|---|---|
| BME280 | Temperature (CPU-heat compensated), humidity, pressure |
| LTR559 | Light (lux), proximity |
| MICS6814 | Oxidising, reducing and NH3 gas resistance (kΩ) |
| PMS5003 (optional, plugged into the Enviro+ port) | PM1.0, PM2.5, PM10 |
| MEMS microphone | Noise level (dB relative to full scale) |

## Pages

1. **Overview** — all values at a glance
2. **Climate** — temperature, humidity, pressure
3. **Light** — lux and proximity
4. **Gas** — oxidising / reducing / NH3
5. **Particles** — PM1.0 / PM2.5 / PM10 (only if a PMS5003 is attached)
6. **Noise** — noise level in dB with a level bar (only if the mic overlay is enabled)

Pages advance automatically every 10 seconds. **Wave your hand over the
proximity sensor to switch to the next page manually.**

Sensors that are missing or broken show `--` instead of crashing the app.

## Install (on the Pi)

Raspberry Pi OS Bookworm (Lite is fine):

```bash
git clone https://github.com/fredmontet/pluto.git
cd pluto
./install.sh
```

The script enables I2C/SPI, adds the `adau7002-simple` overlay for the
microphone (reboot once afterwards for the mic to appear), installs
[uv](https://docs.astral.sh/uv/) if needed, and installs the
dependencies with `uv sync --extra hardware` (the `hardware` extra
holds the Enviro+ drivers, which only make sense on the Pi).

Run it:

```bash
uv run pluto
```

### Run on boot

```bash
sudo cp pluto.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pluto
```

(The unit assumes the repo lives at `/home/pi/pluto` and runs as user
`pi` — edit `pluto.service` if your setup differs.)

## Configuration

Pluto is configured with a TOML file, CLI flags, or both. On startup it
loads `pluto.toml` from the current directory (the repo root when using
the systemd unit) if one exists; `--config PATH` points it somewhere
else. With no file and no flags it runs with the defaults described
below.

```bash
cp pluto.example.toml pluto.toml   # then edit to taste
uv run pluto
```

[`pluto.example.toml`](pluto.example.toml) documents every option with
its default:

```toml
[device]                  # identity of this Pi
id = "balcony-pi"         # default: hostname; used in MQTT topics & HA
location = "balcony"      # becomes the suggested area in Home Assistant
description = "north-facing, behind the planter"

[sensors]
refresh = 1.0             # seconds between sensor reads

[sensors.pms5003]
enabled = false           # skip the particulate sensor

[sensors.microphone]
interval = 5.0            # seconds between (blocking) mic samples

[outputs.lcd]             # the LCD is just a sink (auto-detected)
enabled = true            # false = never use the LCD, even if attached
cycle = 10.0              # seconds between page changes, 0 to disable

[outputs.mqtt]
enabled = true
host = "broker.local"
ha_discovery = true       # port, topic, username, password also available

[outputs.prometheus]
enabled = true
port = 9099

[outputs.sqlite]          # local logging: SQLite, daily CSVs, HTTP POST
enabled = true
path = "pluto-readings.db"

[buffer]                  # store-and-forward for mqtt/http (see below)
max_snapshots = 10000
```

**CLI flags always win over the file**, so existing command lines and
service units keep working unchanged. Precedence is: CLI flag →
`PLUTO_MQTT_PASSWORD` environment variable (password only) → config
file → built-in default.

### Sensor drivers

Each chip is read by its own driver: `bme280` (temperature, humidity,
pressure), `ltr559` (light, proximity), `mics6814` (gas), `pms5003`
(particulates), `microphone` (noise), plus `mock` for development.
Built-in drivers are auto-detected — a driver loads when its hardware
responds and drops out gracefully when it doesn't — and each
`[sensors.<driver>]` table can disable one (`enabled = false`) or pass
it settings. The mock driver is the exception: it only loads with
`--mock` or when a `[sensors.mock]` table declares it.

### Calibration and derived metrics

Any metric can be calibrated per device with a
`[sensors.<driver>.<metric>]` table: `scale` (applied first), `offset`
(then added) and `smooth` (moving average over the last N samples).
The BME280 temperature's CPU-heat correction is the same mechanism —
a `cpu_temp_compensation` transform, on by default with factor 2.25
(`0` disables it; `raw_temperature` stays untouched):

```toml
[sensors.bme280.temperature]
cpu_temp_compensation = 2.25
offset = -0.5    # this unit reads half a degree warm
smooth = 5
```

The `[derived]` section enables metrics computed from the calibrated
values — `dew_point` (°C), `absolute_humidity` (g/m³) and `aqi` (the
European Air Quality Index band from PM2.5/PM10). They get their own
LCD page and reach every sink like native metrics; formulas and bands
are documented in [docs/metrics.md](docs/metrics.md).

Third-party packages can add drivers without touching pluto: subclass
`pluto.drivers.base.Driver`, implement `available()` and `read()`
(returning a `Reading` per field), and register the class under the
`pluto.drivers` entry-point group:

```toml
[project.entry-points."pluto.drivers"]
mysensor = "my_package:MySensorDriver"
```

Once the package is installed, the driver is configured like any other
via `[sensors.mysensor]`, and its readings are published over MQTT
alongside the built-in fields.

## Options

```
pluto [--config PATH] [--refresh SECONDS] [--cycle SECONDS]
      [--no-pms] [--no-noise] [--mock] [--once] [--frames-dir DIR] [-v]
      [--mqtt HOST] [--mqtt-port PORT] [--mqtt-topic TOPIC]
      [--mqtt-user USER] [--mqtt-password PASS] [--ha-discovery]
      [--prometheus PORT]
```

- `--config` — path to the TOML config file (default: `./pluto.toml` if present)
- `--refresh` — seconds between sensor reads (default 1)
- `--cycle` — seconds between automatic LCD page changes, `0` to disable (default 10)
- `--no-pms` / `--no-noise` — skip the particulate sensor / microphone
- `--once` — read once, publish to every sink, and exit (smoke test)
- `--frames-dir DIR` — enable the `png` sink, rendering each page into `DIR`

Every flag except the run-mode ones (`--mock`, `--once`, `-v`) has a
config-file equivalent — see [Configuration](#configuration).

## Publishing readings

Readings go to any number of output sinks: `lcd`, `png`, `mqtt`,
`prometheus`, `sqlite`, `csv` and `http` are built in. The LCD is just
one sink among them — it auto-detects (see [Headless mode](#headless-mode)),
while the rest run when their `[outputs.<sink>]` table is declared and
enabled (or turned on by a CLI flag). All of them run at the same
time, and a failing sink never affects the others.

Every sink emits the same self-describing data model: consistent
metric names and units ([docs/metrics.md](docs/metrics.md) has the
full catalogue), a UTC ISO 8601 timestamp, the device id (hostname
unless `[device] id` overrides it), the configured location and
description, and the pluto version. On an RTC-less Pi that hasn't
NTP-synced yet, snapshots carry `time_uncertain: true` until the
clock looks plausible.

### MQTT

```bash
uv run pluto --mqtt broker.local --ha-discovery
```

or, in `pluto.toml`:

```toml
[outputs.mqtt]
enabled = true
host = "broker.local"
ha_discovery = true
```

Each refresh publishes a JSON snapshot to `pluto/<hostname>/readings`
(override with `--mqtt-topic`), with a retained `online`/`offline`
status on `pluto/<hostname>/status` backed by a last-will. Missing
sensors are omitted from the payload rather than sent as `null`.

With `--ha-discovery`, the device announces every sensor via [Home
Assistant MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery),
so it appears in Home Assistant automatically as an "Enviro+" device
with proper units and availability — no YAML needed.

Use `--mqtt-user` and `--mqtt-password` for an authenticated broker
(the password can also come from the `PLUTO_MQTT_PASSWORD` environment
variable, handy in the systemd unit).

### Prometheus

```bash
uv run pluto --prometheus 9099
```

or, in `pluto.toml`:

```toml
[outputs.prometheus]
enabled = true
port = 9099
```

Exposes the latest snapshot at `http://<pi>:9099/metrics` as gauges
(`pluto_temperature_celsius`, `pluto_humidity_percent`,
`pluto_particulates_ug_per_m3{size="2.5"}`, …) for a Prometheus server
to scrape, each labelled with the `device` and `location` from the
`[device]` section. Sensors that are missing report `NaN`.

### Local logging: SQLite and CSV

```toml
[outputs.sqlite]
enabled = true
path = "pluto-readings.db"
max_rows = 100000   # prune the oldest rows beyond this; 0 = keep all

[outputs.csv]
enabled = true
dir = "csv"         # one file per day: csv/pluto-YYYY-MM-DD.csv
```

The SQLite sink appends one row per snapshot (timestamp plus a JSON
document of the values) and prunes itself so the file stays bounded on
a small SD card. The CSV sink writes one dated, headered file per day
and appends across restarts.

### HTTP

```toml
[outputs.http]
enabled = true
url = "https://example.com/ingest"
token = "..."       # optional; sent as "Authorization: Bearer <token>"
timeout = 10.0
```

POSTs the same JSON document MQTT publishes (plus a `device` field) to
the URL on every refresh.

### Offline buffering

The network sinks (`mqtt`, `http`) sit behind a persistent
store-and-forward queue, on by default. While the broker or endpoint
is unreachable, snapshots accumulate in a small SQLite file and are
retried with exponential backoff; when the connection returns, the
backlog is replayed in order with the original timestamps — so a WiFi
dropout, a broker restart, or even a power cut loses nothing. The
queue is capped per sink (oldest dropped first, with a log line when
it happens):

```toml
[buffer]
enabled = true
path = "pluto-buffer.db"
max_snapshots = 10000   # per sink
```

### Custom sinks

Third-party packages can add sinks the same way they add drivers:
subclass `pluto.sinks.base.Sink`, implement `publish(snapshot)` (and
set `buffered = True` if it should go through the offline queue), and
register the class under the `pluto.sinks` entry-point group:

```toml
[project.entry-points."pluto.sinks"]
mysink = "my_package:MySink"
```

Once installed, `[outputs.mysink]` enables and configures it like any
built-in sink.

## Headless mode

The Enviro+ LCD is not privileged — it is the `lcd` sink, and pluto
runs perfectly with no display attached. The LCD is the one sink that
*auto-detects*: it turns itself on only when the ST7735 panel actually
responds at startup, so a board with no display (or a
`pluto-on-a-server` reading a remote sensor) runs headless with zero
display code in the loop and no wasted CPU on rendering — the read
loop just reads and publishes.

- **No display present** — nothing to configure. The LCD probe fails
  quietly and the sink stays out of the loop.
- **Force it off even when attached** — `[outputs.lcd] enabled = false`
  (handy to save power, or when the panel is used by something else).
- **Page cycling and the proximity wave** work exactly as before when
  the LCD is active. Page switching now rides on the published
  snapshots (the `proximity` reading) rather than the sink poking at
  the LTR559 driver, and rendering happens on the sink's own thread so
  a slow SPI write can't stall reads.

The `png` sink is the LCD's off-Pi twin: it renders the same pages to
image files for development and screenshots (`--frames-dir DIR`, or
`[outputs.png]`).

## Developing off the Pi

Mock mode needs no hardware drivers, so a plain `uv sync` (without the
`hardware` extra) is enough:

```bash
uv sync
uv run pluto --mock --once --frames-dir frames  # render each page to a PNG
uv run pluto --mock -v                          # run the full loop, headless
```

## Project layout

```
pluto/
├── __main__.py   # CLI entry point (python -m pluto)
├── app.py        # main loop: read drivers → transform → publish to sinks
├── config.py     # pluto.toml loading, validation, CLI override merging
├── model.py      # Snapshot data model + the metric catalogue (docs/metrics.md)
├── transforms.py # calibration, smoothing and derived metrics
├── plugins.py    # shared plugin plumbing (settings, entry points)
├── display.py    # LCD page rendering (PIL), device-independent
├── drivers/      # sensor drivers: one module per chip, mock, plugin loading
│   ├── base.py   # Driver ABC + Reading (value, unit, quality)
│   └── ...       # bme280, ltr559, mics6814, pms5003, microphone, mock
└── sinks/        # output sinks (the LCD is one of them)
    ├── base.py   # Sink ABC + Snapshot
    ├── lcd.py    # Enviro+ ST7735 display (auto-detected)
    ├── png.py    # render pages to PNG files (dev/screenshots)
    ├── buffer.py # persistent store-and-forward queue for network sinks
    └── ...       # mqtt, prometheus, sqlite, csv, http
```

Run the tests with `uv run pytest`.
