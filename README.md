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
| MEMS microphone | Noise amplitude |

## Pages

1. **Overview** — all values at a glance
2. **Climate** — temperature, humidity, pressure
3. **Light** — lux and proximity
4. **Gas** — oxidising / reducing / NH3
5. **Particles** — PM1.0 / PM2.5 / PM10 (only if a PMS5003 is attached)
6. **Noise** — microphone amplitude with a level bar (only if the mic overlay is enabled)

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

## Options

```
pluto [--refresh SECONDS] [--cycle SECONDS] [--no-pms] [--no-noise]
      [--mock] [--once] [--frames-dir DIR] [-v]
      [--mqtt HOST] [--mqtt-port PORT] [--mqtt-topic TOPIC]
      [--mqtt-user USER] [--mqtt-password PASS] [--ha-discovery]
      [--prometheus PORT]
```

- `--refresh` — seconds between sensor reads (default 1)
- `--cycle` — seconds between automatic page changes, `0` to disable (default 10)
- `--no-pms` / `--no-noise` — skip the particulate sensor / microphone
- `--once` — render each page once and exit (smoke test)

## Publishing readings

Besides the LCD, readings can be sent off the Pi. Both options can run
at the same time and don't interfere with the display.

### MQTT

```bash
uv run pluto --mqtt broker.local --ha-discovery
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

Exposes the latest snapshot at `http://<pi>:9099/metrics` as gauges
(`pluto_temperature_celsius`, `pluto_humidity_percent`,
`pluto_particulates_ug_per_m3{size="2.5"}`, …) for a Prometheus server
to scrape. Sensors that are missing report `NaN`.

## Developing off the Pi

Mock mode needs no hardware drivers, so a plain `uv sync` (without the
`hardware` extra) is enough:

```bash
uv sync
uv run pluto --mock --once --frames-dir frames  # writes one PNG per page
uv run pluto --mock -v                          # run the full loop
```

## Project layout

```
pluto/
├── __main__.py   # CLI entry point (python -m pluto)
├── app.py        # main loop: read → handle taps → draw
├── sensors.py    # hardware access with graceful fallback, plus mock sensors
└── display.py    # page rendering (PIL) and output devices (LCD / PNG)
```
