"""The driver contract every sensor backend implements.

A Driver wraps one piece of hardware (or a simulation of it). It probes
for the hardware in ``available()`` and reports values as a dict of
``Reading``s from ``read()``, one per field named in ``provides``.
Reads must never raise: a broken sensor degrades to ``missing`` /
``error`` readings instead of crashing the app.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields as dataclass_fields
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from ..config import ConfigError


class Quality(Enum):
    """How much to trust a Reading."""

    OK = "ok"
    MISSING = "missing"  # sensor absent, or no data yet
    ERROR = "error"      # sensor present but the read failed


@dataclass
class Reading:
    """One value from one sensor field."""

    value: Optional[float]
    unit: str = ""
    quality: Quality = Quality.OK

    @classmethod
    def ok(cls, value: float, unit: str = "") -> "Reading":
        return cls(value, unit, Quality.OK)

    @classmethod
    def missing(cls, unit: str = "") -> "Reading":
        return cls(None, unit, Quality.MISSING)

    @classmethod
    def error(cls, unit: str = "") -> "Reading":
        return cls(None, unit, Quality.ERROR)


class Driver(ABC):
    """A sensor backend.

    Subclasses set ``name`` (also the ``[sensors.<name>]`` config key),
    ``provides`` (the field names ``read()`` reports) and
    ``settings_keys`` (config keys accepted besides ``enabled``).
    ``autoload = False`` keeps a driver out of auto-detection, so it
    only loads when its config table declares it (used by the mock).
    """

    name: str = ""
    provides: Tuple[str, ...] = ()
    settings_keys: Tuple[str, ...] = ()
    autoload: bool = True

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = dict(settings or {})
        unknown = sorted(set(self.settings) - set(self.settings_keys))
        if unknown:
            allowed = (f"allowed: {', '.join(self.settings_keys)}"
                       if self.settings_keys else "this driver takes no settings")
            raise ConfigError(
                f"unknown setting(s) for [sensors.{self.name}]: "
                f"{', '.join(unknown)} ({allowed})")

    @abstractmethod
    def available(self) -> bool:
        """Probe the hardware; False keeps the driver out of the loop."""

    @abstractmethod
    def read(self) -> Dict[str, Reading]:
        """Return a Reading for each field in ``provides``. Must not raise."""

    def proximity(self) -> Optional[float]:
        """Fast proximity poll for tap detection; None if unsupported."""
        return None

    def close(self) -> None:
        pass

    def error_readings(self) -> Dict[str, Reading]:
        """An all-``error`` result, for when a read goes sideways."""
        return {f: Reading.error() for f in self.provides}

    # Typed accessors for self.settings, raising ConfigError on misuse.

    def float_setting(self, key: str, default: float, positive: bool = False) -> float:
        value = self.settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(
                f"sensors.{self.name}.{key} must be a number, got {type(value).__name__}")
        value = float(value)
        if positive and value <= 0:
            raise ConfigError(f"sensors.{self.name}.{key} must be > 0")
        return value

    def bool_setting(self, key: str, default: bool) -> bool:
        value = self.settings.get(key, default)
        if not isinstance(value, bool):
            raise ConfigError(
                f"sensors.{self.name}.{key} must be a bool, got {type(value).__name__}")
        return value


@dataclass
class Readings:
    """A flat snapshot of the standard fields, for the display pages.

    Built from the merged driver readings by ``snapshot()``; anything
    not ``ok`` (or not provided by a loaded driver) is None, which the
    renderer shows as ``--``.
    """

    temperature: Optional[float] = None  # °C, CPU-heat compensated
    raw_temperature: Optional[float] = None  # °C, straight from the BME280
    humidity: Optional[float] = None  # % relative humidity
    pressure: Optional[float] = None  # hPa
    lux: Optional[float] = None  # lux
    proximity: Optional[float] = None  # unitless (LTR559 counts)
    oxidising: Optional[float] = None  # kΩ
    reducing: Optional[float] = None  # kΩ
    nh3: Optional[float] = None  # kΩ
    pm1: Optional[float] = None  # µg/m³
    pm25: Optional[float] = None  # µg/m³
    pm10: Optional[float] = None  # µg/m³
    noise: Optional[float] = None  # relative amplitude


def snapshot(readings: Dict[str, Reading]) -> Readings:
    """Flatten merged driver readings into a Readings for rendering."""
    values = {}
    for f in dataclass_fields(Readings):
        r = readings.get(f.name)
        values[f.name] = r.value if r is not None and r.quality is Quality.OK else None
    return Readings(**values)
