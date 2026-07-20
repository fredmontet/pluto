"""Tests for config loading, validation, and CLI override precedence."""

import pytest

from pluto import config
from pluto.__main__ import build_parser
from pluto.config import ConfigError, load_config, parse_config


def write(tmp_path, text, name="pluto.toml"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


# ── Loading ─────────────────────────────────────────────────────────

def test_no_config_file_yields_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg == config.Config()
    assert cfg.sensors.refresh == 1.0
    assert cfg.outputs.display.cycle == 10.0
    assert cfg.sensors.drivers == {}  # nothing declared: all auto-detected
    assert cfg.outputs.sinks == {}  # no sinks declared: none run
    assert cfg.outputs.display.enabled
    assert cfg.buffer == config.BufferConfig()


def test_default_file_in_cwd_is_picked_up(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "[sensors]\nrefresh = 3.5\n")
    assert load_config().sensors.refresh == 3.5


def test_explicit_missing_path_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config file"):
        load_config(str(tmp_path / "nope.toml"))


def test_invalid_toml_is_an_error(tmp_path):
    path = write(tmp_path, "not toml ===")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)


def test_empty_file_equals_defaults(tmp_path):
    assert load_config(write(tmp_path, "")) == config.Config()


def test_full_file(tmp_path):
    cfg = load_config(write(tmp_path, """
        [device]
        id = "balcony-pi"
        location = "balcony"
        description = "north-facing"

        [sensors]
        refresh = 2

        [sensors.pms5003]
        enabled = false

        [sensors.microphone]
        enabled = false
        interval = 10.0

        [sensors.bme280]
        comp_factor = 3.0

        [outputs.display]
        enabled = false
        cycle = 0

        [outputs.mqtt]
        enabled = true
        host = "broker.local"
        port = 8883
        topic = "home/balcony"
        username = "pluto"
        password = "secret"
        ha_discovery = true

        [outputs.prometheus]
        enabled = true
        port = 9100

        [outputs.sqlite]
        path = "data.db"

        [buffer]
        path = "queue.db"
        max_snapshots = 500
    """))
    assert cfg.device.id == "balcony-pi"
    assert cfg.device.location == "balcony"
    assert cfg.device.description == "north-facing"
    assert cfg.sensors.refresh == 2.0
    assert not cfg.sensors.drivers["pms5003"].enabled
    assert not cfg.sensors.drivers["microphone"].enabled
    assert cfg.sensors.drivers["microphone"].settings == {"interval": 10.0}
    assert cfg.sensors.drivers["bme280"].enabled
    assert cfg.sensors.drivers["bme280"].settings == {"comp_factor": 3.0}
    assert not cfg.outputs.display.enabled
    assert cfg.outputs.display.cycle == 0.0
    m = cfg.outputs.sinks["mqtt"]
    assert m.enabled
    assert m.settings == {"host": "broker.local", "port": 8883,
                          "topic": "home/balcony", "username": "pluto",
                          "password": "secret", "ha_discovery": True}
    assert cfg.outputs.sinks["prometheus"] == config.SinkConfig(True, {"port": 9100})
    assert cfg.outputs.sinks["sqlite"] == config.SinkConfig(True, {"path": "data.db"})
    assert cfg.buffer == config.BufferConfig(True, "queue.db", 500)


def test_partial_file_keeps_other_defaults(tmp_path):
    cfg = load_config(write(tmp_path, "[outputs.mqtt]\nhost = \"b\"\n"))
    assert cfg.outputs.sinks == {"mqtt": config.SinkConfig(True, {"host": "b"})}
    assert cfg.sensors.refresh == 1.0
    assert cfg.outputs.display.enabled
    assert cfg.buffer.enabled


# ── Validation errors ───────────────────────────────────────────────

@pytest.mark.parametrize("doc, match", [
    ("[displays]\n", "unknown key"),
    ("[device]\nname = \"x\"\n", r"unknown key\(s\) in \[device\]: name"),
    ("[sensors]\nrefresh = \"fast\"\n", "sensors.refresh must be a float"),
    ("[sensors]\nrefresh = true\n", "sensors.refresh must be a float"),
    ("[sensors]\nbme280 = 3\n", r"\[sensors.bme280\] must be a table"),
    ("[sensors.pms5003]\nenabled = 1\n", "sensors.pms5003.enabled must be a bool"),
    ("[outputs]\nmqtt = 3\n", r"\[outputs.mqtt\] must be a table"),
    ("[outputs.mqtt]\nenabled = 1\n", "outputs.mqtt.enabled must be a bool"),
    ("[sensors]\nrefresh = 0\n", "sensors.refresh must be > 0"),
    ("[outputs.display]\ncycle = -5\n", "outputs.display.cycle must be >= 0"),
    ("[buffer]\nmax_snapshots = 0\n", "buffer.max_snapshots must be >= 1"),
    ("[buffer]\npath = \"\"\n", "buffer.path must not be empty"),
    ("[buffer]\nsize = 3\n", r"unknown key\(s\) in \[buffer\]: size"),
    ("device = 3\n", r"\[device\] must be a table"),
])
def test_invalid_config(tmp_path, doc, match):
    with pytest.raises(ConfigError, match=match):
        load_config(write(tmp_path, doc))


# ── CLI override precedence ─────────────────────────────────────────

def apply(argv, cfg=None):
    args = build_parser().parse_args(argv)
    return config.apply_cli_overrides(cfg or config.Config(), args)


def test_no_flags_keep_file_values():
    cfg = parse_config({"sensors": {"refresh": 7.0}, "outputs": {"display": {"cycle": 3.0}}})
    cfg = apply([], cfg)
    assert cfg.sensors.refresh == 7.0
    assert cfg.outputs.display.cycle == 3.0


def test_flags_override_file():
    cfg = parse_config({
        "sensors": {"refresh": 7.0, "pms5003": {"enabled": True}},
        "outputs": {
            "display": {"cycle": 3.0},
            "mqtt": {"enabled": True, "host": "file-broker", "port": 2000,
                     "topic": "file/topic"},
        },
    })
    cfg = apply([
        "--refresh", "0.5", "--cycle", "0", "--no-pms",
        "--mqtt", "cli-broker", "--mqtt-port", "3000", "--mqtt-topic", "cli/topic",
        "--prometheus", "9200",
    ], cfg)
    assert cfg.sensors.refresh == 0.5
    assert cfg.outputs.display.cycle == 0.0
    assert not cfg.sensors.drivers["pms5003"].enabled
    settings = cfg.outputs.sinks["mqtt"].settings
    assert settings["host"] == "cli-broker"
    assert settings["port"] == 3000
    assert settings["topic"] == "cli/topic"
    assert cfg.outputs.sinks["prometheus"].enabled
    assert cfg.outputs.sinks["prometheus"].settings["port"] == 9200


def test_no_flags_no_file_equals_legacy_defaults():
    cfg = apply([])
    assert cfg.sensors.refresh == 1.0
    assert cfg.outputs.display.cycle == 10.0
    assert cfg.sensors.drivers == {}
    assert cfg.outputs.sinks == {}


def test_mqtt_flag_enables_publishing():
    cfg = apply(["--mqtt", "broker.local", "--ha-discovery"])
    m = cfg.outputs.sinks["mqtt"]
    assert m.enabled
    assert m.settings == {"host": "broker.local", "ha_discovery": True}


def test_secondary_mqtt_flags_alone_enable_nothing():
    cfg = apply(["--mqtt-port", "3000", "--mqtt-topic", "t"])
    assert cfg.outputs.sinks == {}  # matches the legacy CLI behaviour


def test_file_enables_mqtt_without_flags():
    cfg = parse_config({"outputs": {"mqtt": {"enabled": True, "host": "b"}}})
    cfg = apply([], cfg)
    m = cfg.outputs.sinks["mqtt"]
    assert m.enabled and m.settings["host"] == "b"


def test_password_env_overrides_file(monkeypatch):
    monkeypatch.setenv("PLUTO_MQTT_PASSWORD", "env-secret")
    cfg = parse_config(
        {"outputs": {"mqtt": {"enabled": True, "host": "b", "password": "file-secret"}}})
    cfg = apply([], cfg)
    assert cfg.outputs.sinks["mqtt"].settings["password"] == "env-secret"


def test_password_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("PLUTO_MQTT_PASSWORD", "env-secret")
    cfg = apply(["--mqtt", "b", "--mqtt-password", "cli-secret"])
    assert cfg.outputs.sinks["mqtt"].settings["password"] == "cli-secret"


@pytest.mark.parametrize("argv, match", [
    (["--refresh", "0"], "--refresh must be > 0"),
    (["--cycle", "-1"], "--cycle must be >= 0"),
    (["--mqtt", "b", "--mqtt-port", "0"], "--mqtt-port"),
    (["--prometheus", "99999"], "--prometheus"),
])
def test_invalid_flag_values(argv, match):
    with pytest.raises(ConfigError, match=match):
        apply(argv)


# ── Example config ──────────────────────────────────────────────────

def test_example_config_is_valid_and_matches_defaults():
    """pluto.example.toml must parse and describe exactly the defaults."""
    import pathlib

    from pluto.drivers import load_drivers
    from pluto.sinks import SinkContext, load_sinks

    example = pathlib.Path(__file__).resolve().parent.parent / "pluto.example.toml"
    cfg = load_config(str(example))
    assert cfg.device == config.DeviceConfig()
    assert cfg.outputs.display == config.DisplayConfig()
    assert cfg.buffer == config.BufferConfig()
    assert cfg.sensors.refresh == 1.0
    # The declared driver tables must spell out exactly the defaults.
    assert cfg.sensors.drivers == {
        "bme280": config.DriverConfig(True, {"comp_factor": 2.25}),
        "ltr559": config.DriverConfig(True, {}),
        "mics6814": config.DriverConfig(True, {}),
        "pms5003": config.DriverConfig(True, {}),
        "microphone": config.DriverConfig(True, {"interval": 5.0}),
    }
    # Every sink appears, disabled, with its default settings.
    assert cfg.outputs.sinks == {
        "mqtt": config.SinkConfig(False, {
            "host": "", "port": 1883, "topic": "", "username": "",
            "password": "", "ha_discovery": False}),
        "prometheus": config.SinkConfig(False, {"port": 9099}),
        "sqlite": config.SinkConfig(False, {
            "path": "pluto-readings.db", "max_rows": 100000}),
        "csv": config.SinkConfig(False, {"dir": "csv"}),
        "http": config.SinkConfig(False, {"url": "", "token": "", "timeout": 10.0}),
    }
    # The loaders accept every declared name (off-Pi the hardware probes
    # fail and all sinks are disabled, so nothing actually loads).
    load_drivers(cfg.sensors)
    assert load_sinks(cfg.outputs, SinkContext(), cfg.buffer) == []
