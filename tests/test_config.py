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
    assert cfg.sensors.pms.enabled and cfg.sensors.noise.enabled
    assert not cfg.outputs.mqtt.enabled
    assert not cfg.outputs.prometheus.enabled
    assert cfg.outputs.display.enabled


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

        [sensors.pms]
        enabled = false

        [sensors.noise]
        enabled = false
        interval = 10.0

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
    """))
    assert cfg.device.id == "balcony-pi"
    assert cfg.device.location == "balcony"
    assert cfg.device.description == "north-facing"
    assert cfg.sensors.refresh == 2.0
    assert not cfg.sensors.pms.enabled
    assert not cfg.sensors.noise.enabled
    assert cfg.sensors.noise.interval == 10.0
    assert not cfg.outputs.display.enabled
    assert cfg.outputs.display.cycle == 0.0
    m = cfg.outputs.mqtt
    assert m.enabled and m.host == "broker.local" and m.port == 8883
    assert m.topic == "home/balcony" and m.username == "pluto"
    assert m.password == "secret" and m.ha_discovery
    assert cfg.outputs.prometheus.enabled
    assert cfg.outputs.prometheus.port == 9100


def test_partial_file_keeps_other_defaults(tmp_path):
    cfg = load_config(write(tmp_path, "[outputs.mqtt]\nenabled = true\nhost = \"b\"\n"))
    assert cfg.outputs.mqtt.port == 1883
    assert cfg.sensors.refresh == 1.0
    assert cfg.outputs.display.enabled


# ── Validation errors ───────────────────────────────────────────────

@pytest.mark.parametrize("doc, match", [
    ("[displays]\n", "unknown key"),
    ("[device]\nname = \"x\"\n", r"unknown key\(s\) in \[device\]: name"),
    ("[sensors.pms]\ninterval = 1.0\n", r"\[sensors.pms\]"),
    ("[outputs.mqtt]\nhosts = \"b\"\n", r"\[outputs.mqtt\]"),
    ("[sensors]\nrefresh = \"fast\"\n", "sensors.refresh must be a float"),
    ("[sensors]\nrefresh = true\n", "sensors.refresh must be a float"),
    ("[sensors.pms]\nenabled = 1\n", "sensors.pms.enabled must be a bool"),
    ("[outputs.mqtt]\nport = 1883.5\n", "outputs.mqtt.port must be a int"),
    ("[outputs.mqtt]\nhost = 42\n", "outputs.mqtt.host must be a str"),
    ("[sensors]\nrefresh = 0\n", "sensors.refresh must be > 0"),
    ("[sensors.noise]\ninterval = -1\n", "sensors.noise.interval must be > 0"),
    ("[outputs.display]\ncycle = -5\n", "outputs.display.cycle must be >= 0"),
    ("[outputs.mqtt]\nport = 0\n", "between 1 and 65535"),
    ("[outputs.prometheus]\nport = 70000\n", "between 1 and 65535"),
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
        "sensors": {"refresh": 7.0, "pms": {"enabled": True}},
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
    assert not cfg.sensors.pms.enabled
    assert cfg.outputs.mqtt.host == "cli-broker"
    assert cfg.outputs.mqtt.port == 3000
    assert cfg.outputs.mqtt.topic == "cli/topic"
    assert cfg.outputs.prometheus.enabled
    assert cfg.outputs.prometheus.port == 9200


def test_no_flags_no_file_equals_legacy_defaults():
    cfg = apply([])
    assert cfg.sensors.refresh == 1.0
    assert cfg.outputs.display.cycle == 10.0
    assert cfg.sensors.pms.enabled and cfg.sensors.noise.enabled
    assert not cfg.outputs.mqtt.enabled and not cfg.outputs.prometheus.enabled


def test_mqtt_flag_enables_publishing():
    cfg = apply(["--mqtt", "broker.local", "--ha-discovery"])
    assert cfg.outputs.mqtt.enabled
    assert cfg.outputs.mqtt.host == "broker.local"
    assert cfg.outputs.mqtt.port == 1883  # untouched default
    assert cfg.outputs.mqtt.ha_discovery


def test_file_enables_mqtt_without_flags():
    cfg = parse_config({"outputs": {"mqtt": {"enabled": True, "host": "b"}}})
    cfg = apply([], cfg)
    assert cfg.outputs.mqtt.enabled and cfg.outputs.mqtt.host == "b"


def test_mqtt_enabled_without_host_is_an_error():
    cfg = parse_config({"outputs": {"mqtt": {"enabled": True}}})
    with pytest.raises(ConfigError, match="no broker host"):
        apply([], cfg)


def test_password_env_overrides_file(monkeypatch):
    monkeypatch.setenv("PLUTO_MQTT_PASSWORD", "env-secret")
    cfg = parse_config(
        {"outputs": {"mqtt": {"enabled": True, "host": "b", "password": "file-secret"}}})
    cfg = apply([], cfg)
    assert cfg.outputs.mqtt.password == "env-secret"


def test_password_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("PLUTO_MQTT_PASSWORD", "env-secret")
    cfg = apply(["--mqtt", "b", "--mqtt-password", "cli-secret"])
    assert cfg.outputs.mqtt.password == "cli-secret"


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

    example = pathlib.Path(__file__).resolve().parent.parent / "pluto.example.toml"
    assert load_config(str(example)) == config.Config()
