"""Declarative configuration loaded from ``pluto.toml``.

The file is optional: without one, pluto runs with the same defaults it
always had. CLI flags take precedence over the file, so existing
command lines keep working unchanged.

Plain dataclasses + the stdlib TOML parser keep the dependency
footprint at zero on Python 3.11+ (a tiny ``tomli`` backport covers
older interpreters) — this runs on a Pi Zero.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

DEFAULT_CONFIG_FILE = "pluto.toml"


class ConfigError(Exception):
    """Invalid or unreadable configuration."""


@dataclass
class DeviceConfig:
    id: str = ""  # empty -> hostname
    location: str = ""
    description: str = ""


@dataclass
class DriverConfig:
    """One ``[sensors.<driver>]`` table: an enabled flag plus whatever
    driver-specific settings the table carries. Settings are validated
    by the driver itself when it is instantiated."""

    enabled: bool = True
    settings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SensorsConfig:
    refresh: float = 1.0
    drivers: Dict[str, DriverConfig] = field(default_factory=dict)

    def driver(self, name: str) -> DriverConfig:
        """The config for ``name``, creating a default entry if absent."""
        return self.drivers.setdefault(name, DriverConfig())


@dataclass
class DisplayConfig:
    enabled: bool = True
    cycle: float = 10.0  # 0 disables automatic page cycling


@dataclass
class MQTTConfig:
    enabled: bool = False
    host: str = ""
    port: int = 1883
    topic: str = ""  # empty -> pluto/<device id>
    username: str = ""
    password: str = ""
    ha_discovery: bool = False


@dataclass
class PrometheusConfig:
    enabled: bool = False
    port: int = 9099


@dataclass
class OutputsConfig:
    display: DisplayConfig = field(default_factory=DisplayConfig)
    mqtt: MQTTConfig = field(default_factory=MQTTConfig)
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)


@dataclass
class Config:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    sensors: SensorsConfig = field(default_factory=SensorsConfig)
    outputs: OutputsConfig = field(default_factory=OutputsConfig)


def load_config(path: Optional[str] = None) -> Config:
    """Load ``path`` (or ``./pluto.toml`` if present) into a Config.

    An explicitly given path must exist; the implicit default file is
    simply skipped when absent, yielding pure defaults.
    """
    if path is None:
        if not os.path.exists(DEFAULT_CONFIG_FILE):
            return Config()
        path = DEFAULT_CONFIG_FILE
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}")
    return parse_config(data)


def parse_config(data: Dict[str, Any]) -> Config:
    """Validate a parsed TOML document and build a Config from it."""
    _check_keys(data, ("device", "sensors", "outputs"), "top level")
    cfg = Config()

    device = _table(data, "device")
    _check_keys(device, ("id", "location", "description"), "[device]")
    cfg.device.id = _get(device, "id", str, cfg.device.id, "device")
    cfg.device.location = _get(device, "location", str, cfg.device.location, "device")
    cfg.device.description = _get(device, "description", str, cfg.device.description, "device")

    sensors = _table(data, "sensors")
    cfg.sensors.refresh = _positive(
        _get(sensors, "refresh", float, cfg.sensors.refresh, "sensors"), "sensors.refresh")
    # Every other key is a [sensors.<driver>] table. Driver names and
    # their settings are validated when the drivers are loaded, so
    # entry-point drivers unknown to this module still work.
    for name, table in sensors.items():
        if name == "refresh":
            continue
        if not isinstance(table, dict):
            raise ConfigError(
                f"[sensors.{name}] must be a table of driver settings, "
                f"got {type(table).__name__}")
        enabled = _get(table, "enabled", bool, True, f"sensors.{name}")
        settings = {k: v for k, v in table.items() if k != "enabled"}
        cfg.sensors.drivers[name] = DriverConfig(enabled=enabled, settings=settings)

    outputs = _table(data, "outputs")
    _check_keys(outputs, ("display", "mqtt", "prometheus"), "[outputs]")

    display = _table(outputs, "display", "outputs")
    _check_keys(display, ("enabled", "cycle"), "[outputs.display]")
    cfg.outputs.display.enabled = _get(
        display, "enabled", bool, cfg.outputs.display.enabled, "outputs.display")
    cfg.outputs.display.cycle = _get(
        display, "cycle", float, cfg.outputs.display.cycle, "outputs.display")
    if cfg.outputs.display.cycle < 0:
        raise ConfigError("outputs.display.cycle must be >= 0 (0 disables cycling)")

    mqtt = _table(outputs, "mqtt", "outputs")
    _check_keys(
        mqtt,
        ("enabled", "host", "port", "topic", "username", "password", "ha_discovery"),
        "[outputs.mqtt]")
    m = cfg.outputs.mqtt
    m.enabled = _get(mqtt, "enabled", bool, m.enabled, "outputs.mqtt")
    m.host = _get(mqtt, "host", str, m.host, "outputs.mqtt")
    m.port = _port(_get(mqtt, "port", int, m.port, "outputs.mqtt"), "outputs.mqtt.port")
    m.topic = _get(mqtt, "topic", str, m.topic, "outputs.mqtt")
    m.username = _get(mqtt, "username", str, m.username, "outputs.mqtt")
    m.password = _get(mqtt, "password", str, m.password, "outputs.mqtt")
    m.ha_discovery = _get(mqtt, "ha_discovery", bool, m.ha_discovery, "outputs.mqtt")

    prometheus = _table(outputs, "prometheus", "outputs")
    _check_keys(prometheus, ("enabled", "port"), "[outputs.prometheus]")
    p = cfg.outputs.prometheus
    p.enabled = _get(prometheus, "enabled", bool, p.enabled, "outputs.prometheus")
    p.port = _port(_get(prometheus, "port", int, p.port, "outputs.prometheus"),
                   "outputs.prometheus.port")

    return cfg


def apply_cli_overrides(cfg: Config, args: Any) -> Config:
    """Overlay parsed CLI arguments onto ``cfg`` (flags win over the file).

    ``args`` is the argparse namespace from ``pluto.__main__``; value
    flags default to None there so "not given" is distinguishable from
    any real value.
    """
    if args.refresh is not None:
        cfg.sensors.refresh = _positive(args.refresh, "--refresh")
    if args.cycle is not None:
        if args.cycle < 0:
            raise ConfigError("--cycle must be >= 0 (0 disables cycling)")
        cfg.outputs.display.cycle = args.cycle
    if args.no_pms:
        cfg.sensors.driver("pms5003").enabled = False
    if args.no_noise:
        cfg.sensors.driver("microphone").enabled = False

    m = cfg.outputs.mqtt
    if args.mqtt:
        m.enabled = True
        m.host = args.mqtt
    if args.mqtt_port is not None:
        m.port = _port(args.mqtt_port, "--mqtt-port")
    if args.mqtt_topic:
        m.topic = args.mqtt_topic
    if args.mqtt_user:
        m.username = args.mqtt_user
    if args.mqtt_password:  # CLI flag or PLUTO_MQTT_PASSWORD
        m.password = args.mqtt_password
    if args.ha_discovery:
        m.ha_discovery = True
    if m.enabled and not m.host:
        raise ConfigError(
            "MQTT is enabled but no broker host is set "
            "(set outputs.mqtt.host or pass --mqtt HOST)")

    if args.prometheus is not None:
        cfg.outputs.prometheus.enabled = True
        cfg.outputs.prometheus.port = _port(args.prometheus, "--prometheus")

    return cfg


def _table(parent: Dict[str, Any], key: str, path: str = "") -> Dict[str, Any]:
    name = f"{path}.{key}" if path else key
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table, got {type(value).__name__}")
    return value


def _check_keys(table: Dict[str, Any], allowed: Tuple[str, ...], where: str) -> None:
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise ConfigError(
            f"unknown key(s) in {where}: {', '.join(unknown)} "
            f"(allowed: {', '.join(allowed)})")


def _get(table: Dict[str, Any], key: str, kind: type, default: Any, path: str) -> Any:
    if key not in table:
        return default
    value = table[key]
    # bool is a subclass of int, so keep the checks strict in both directions.
    if kind is bool:
        ok = isinstance(value, bool)
    elif kind is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        value = float(value) if ok else value
    elif kind is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, kind)
    if not ok:
        raise ConfigError(
            f"{path}.{key} must be a {kind.__name__}, got {type(value).__name__}")
    return value


def _positive(value: float, name: str) -> float:
    if value <= 0:
        raise ConfigError(f"{name} must be > 0")
    return value


def _port(value: int, name: str) -> int:
    if not 1 <= value <= 65535:
        raise ConfigError(f"{name} must be between 1 and 65535")
    return value
