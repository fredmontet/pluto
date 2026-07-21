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
from typing import Dict, Optional, Tuple

from ..plugins import Configurable


class Quality(Enum):
    """How much to trust a Reading."""

    OK = "ok"
    MISSING = "missing"  # sensor absent, or no data yet
    ERROR = "error"      # sensor present but the read failed


@dataclass
class Reading:
    """One value from one sensor field.

    ``driver`` is stamped with the producing driver's name during the
    merged read, so downstream storage can tell where a metric came
    from without extra context.
    """

    value: Optional[float]
    unit: str = ""
    quality: Quality = Quality.OK
    driver: str = ""

    @classmethod
    def ok(cls, value: float, unit: str = "") -> "Reading":
        return cls(value, unit, Quality.OK)

    @classmethod
    def missing(cls, unit: str = "") -> "Reading":
        return cls(None, unit, Quality.MISSING)

    @classmethod
    def error(cls, unit: str = "") -> "Reading":
        return cls(None, unit, Quality.ERROR)


class Driver(Configurable, ABC):
    """A sensor backend.

    Subclasses set ``name`` (also the ``[sensors.<name>]`` config key),
    ``provides`` (the field names ``read()`` reports) and
    ``settings_keys`` (config keys accepted besides ``enabled``).
    ``autoload = False`` keeps a driver out of auto-detection, so it
    only loads when its config table declares it (used by the mock).
    """

    section = "sensors"
    provides: Tuple[str, ...] = ()
    autoload: bool = True
    # Per-metric transform specs applied by default (the user's
    # [sensors.<driver>.<metric>] tables overlay these), e.g. the
    # BME280's CPU-heat temperature compensation.
    default_transforms: Dict[str, Dict[str, object]] = {}

    @abstractmethod
    def available(self) -> bool:
        """Probe the hardware; False keeps the driver out of the loop."""

    @abstractmethod
    def read(self) -> Dict[str, Reading]:
        """Return a Reading for each field in ``provides``. Must not raise."""

    def close(self) -> None:
        pass

    def error_readings(self) -> Dict[str, Reading]:
        """An all-``error`` result, for when a read goes sideways."""
        return {f: Reading.error() for f in self.provides}


@dataclass
class Readings:
    """A flat snapshot of the standard fields, for the display pages.

    Built from the merged driver readings by ``flatten()``; anything
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
    noise: Optional[float] = None  # dB relative to full scale (uncalibrated)
    dew_point: Optional[float] = None  # °C (derived)
    absolute_humidity: Optional[float] = None  # g/m³ (derived)
    aqi: Optional[float] = None  # European AQI band 1-6 (derived)


def flatten(readings: Dict[str, Reading]) -> Readings:
    """Flatten merged driver readings into a Readings for rendering."""
    values = {}
    for f in dataclass_fields(Readings):
        r = readings.get(f.name)
        values[f.name] = r.value if r is not None and r.quality is Quality.OK else None
    return Readings(**values)
