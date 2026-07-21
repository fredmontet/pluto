"""Calibration, smoothing and derived metrics.

A configurable pipeline sits between the merged driver read and
everything downstream (LCD and sinks). Each metric can carry a
``[sensors.<driver>.<metric>]`` table applying, in order:
``cpu_temp_compensation`` → ``scale`` → ``offset`` → ``smooth``
(moving average). Derived metrics — enabled per metric in the
``[derived]`` section — are computed from the transformed values and
flow through the Snapshot model like native metrics.

All the maths lives in pure functions; TransformPipeline only carries
state (smoothing windows, the CPU temperature history).
"""

import logging
import math
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from .config import ConfigError, DerivedConfig, SensorsConfig
from .drivers import provided_fields
from .drivers.base import Driver, Quality, Reading
from .model import METRICS

log = logging.getLogger(__name__)

CPU_TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"
CPU_SMOOTHING = 5  # samples of CPU temperature averaged for compensation


@dataclass(frozen=True)
class MetricTransform:
    """The transform settings for one metric."""

    offset: float = 0.0
    scale: float = 1.0
    smooth: int = 1  # moving-average window in samples; 1 = off
    cpu_temp_compensation: float = 0.0  # compensation factor; 0 = off


# ── Pure functions ──────────────────────────────────────────────────

def calibrate(value: float, offset: float = 0.0, scale: float = 1.0) -> float:
    """Two-point calibration: the value is scaled, then shifted."""
    return value * scale + offset


def moving_average(values: Iterable[float]) -> float:
    """Mean of the samples seen so far (a warming-up window included)."""
    values = list(values)
    return sum(values) / len(values)


def cpu_temp_compensation(raw: float, cpu_temp: float, factor: float) -> float:
    """The usual Pimoroni BME280 correction: the sensor sits next to
    the Pi's SoC and reads warm, so subtract a fraction of the CPU
    heat. Higher factors mean weaker compensation."""
    return raw - ((cpu_temp - raw) / factor)


def dew_point(temperature: float, humidity: float) -> float:
    """Dew point in °C via the Magnus formula (Sonntag constants
    b = 17.62, c = 243.12 °C), valid for -45..60 °C."""
    rh = min(max(humidity, 0.01), 100.0)
    gamma = math.log(rh / 100.0) + (17.62 * temperature) / (243.12 + temperature)
    return 243.12 * gamma / (17.62 - gamma)


def absolute_humidity(temperature: float, humidity: float) -> float:
    """Absolute humidity in g/m³, from the Magnus saturation vapour
    pressure and the ideal gas law."""
    rh = min(max(humidity, 0.0), 100.0)
    svp = 6.112 * math.exp((17.62 * temperature) / (243.12 + temperature))  # hPa
    return 216.7 * (rh / 100.0 * svp) / (273.15 + temperature)


# European Air Quality Index band upper bounds (µg/m³), bands 1-5;
# anything above the last bound is band 6 ("extremely poor").
EAQI_PM25_BOUNDS = (10.0, 20.0, 25.0, 50.0, 75.0)
EAQI_PM10_BOUNDS = (20.0, 40.0, 50.0, 100.0, 150.0)


def _band(value: float, bounds) -> int:
    for i, bound in enumerate(bounds):
        if value <= bound:
            return i + 1
    return len(bounds) + 1


def european_aqi(pm25: Optional[float] = None,
                 pm10: Optional[float] = None) -> Optional[int]:
    """European Air Quality Index band, 1 (good) .. 6 (extremely
    poor): the worst band among the available particulate readings.
    docs/metrics.md lists the bands."""
    bands = []
    if pm25 is not None:
        bands.append(_band(pm25, EAQI_PM25_BOUNDS))
    if pm10 is not None:
        bands.append(_band(pm10, EAQI_PM10_BOUNDS))
    return max(bands) if bands else None


def _read_cpu_temp() -> Optional[float]:
    try:
        with open(CPU_TEMP_PATH) as f:
            return int(f.read()) / 1000.0
    except (OSError, ValueError):
        return None


# ── The pipeline ────────────────────────────────────────────────────

class TransformPipeline:
    """Applies per-metric transforms and computes derived metrics.

    Stateful only where it must be: moving-average windows and the
    smoothed CPU temperature; everything else delegates to the pure
    functions above.
    """

    def __init__(self, transforms: Dict[str, MetricTransform],
                 derived: Set[str],
                 cpu_temp_reader: Callable[[], Optional[float]] = _read_cpu_temp):
        self.transforms = dict(transforms)
        self.derived_fields = set(derived)
        self._cpu_temp_reader = cpu_temp_reader
        self._windows: Dict[str, deque] = {}
        self._cpu_history: deque = deque(maxlen=CPU_SMOOTHING)
        self._needs_cpu = any(t.cpu_temp_compensation for t in self.transforms.values())

    def apply(self, readings: Dict[str, Reading]) -> Dict[str, Reading]:
        out = dict(readings)
        cpu_avg = self._cpu_average() if self._needs_cpu else None

        for metric, t in self.transforms.items():
            r = out.get(metric)
            if r is None or r.quality is not Quality.OK or r.value is None:
                continue
            value = r.value
            if t.cpu_temp_compensation and cpu_avg is not None:
                value = cpu_temp_compensation(value, cpu_avg, t.cpu_temp_compensation)
            value = calibrate(value, t.offset, t.scale)
            if t.smooth > 1:
                window = self._windows.setdefault(metric, deque(maxlen=t.smooth))
                window.append(value)
                value = moving_average(window)
            out[metric] = replace(r, value=value)

        self._add_derived(out)
        return out

    def _cpu_average(self) -> Optional[float]:
        cpu = self._cpu_temp_reader()
        if cpu is not None:
            if not self._cpu_history:  # seed so the first reads aren't skewed
                self._cpu_history.extend([cpu] * CPU_SMOOTHING)
            self._cpu_history.append(cpu)
        if not self._cpu_history:
            return None
        return moving_average(self._cpu_history)

    def _add_derived(self, out: Dict[str, Reading]) -> None:
        def ok_value(metric: str) -> Optional[float]:
            r = out.get(metric)
            return r.value if r is not None and r.quality is Quality.OK else None

        def emit(metric: str, value: float) -> None:
            out[metric] = Reading(value, METRICS[metric].unit, Quality.OK, "derived")

        temperature = ok_value("temperature")
        humidity = ok_value("humidity")
        if temperature is not None and humidity is not None:
            if "dew_point" in self.derived_fields:
                emit("dew_point", dew_point(temperature, humidity))
            if "absolute_humidity" in self.derived_fields:
                emit("absolute_humidity", absolute_humidity(temperature, humidity))
        if "aqi" in self.derived_fields:
            aqi = european_aqi(ok_value("pm25"), ok_value("pm10"))
            if aqi is not None:
                emit("aqi", float(aqi))


# ── Building the pipeline from config ───────────────────────────────

_TRANSFORM_KEYS = ("offset", "scale", "smooth", "cpu_temp_compensation")

_DERIVED_INPUTS = {
    "dew_point": {"temperature", "humidity"},
    "absolute_humidity": {"temperature", "humidity"},
    "aqi": set(),  # needs pm25 OR pm10, checked separately
}


def build_pipeline(drivers: List[Driver], cfg: SensorsConfig,
                   derived_cfg: DerivedConfig) -> TransformPipeline:
    """Merge driver default transforms with the config's per-metric
    tables, validate them, and pick the computable derived metrics."""
    transforms: Dict[str, MetricTransform] = {}
    for driver in drivers:
        merged = {metric: dict(spec)
                  for metric, spec in driver.default_transforms.items()}
        declared = cfg.drivers.get(driver.name)
        if declared is not None:
            for metric, spec in declared.transforms.items():
                if metric not in driver.provides:
                    raise ConfigError(
                        f"[sensors.{driver.name}.{metric}]: driver "
                        f"{driver.name} provides no metric {metric!r} "
                        f"(provides: {', '.join(driver.provides)})")
                merged.setdefault(metric, {}).update(spec)
        for metric, spec in merged.items():
            transforms[metric] = _parse_transform(driver.name, metric, spec)

    provided = provided_fields(drivers)
    derived: Set[str] = set()
    for metric, wanted in (("dew_point", derived_cfg.dew_point),
                           ("absolute_humidity", derived_cfg.absolute_humidity),
                           ("aqi", derived_cfg.aqi)):
        if not wanted:
            continue
        needs = _DERIVED_INPUTS[metric]
        computable = (needs <= provided if needs
                      else bool({"pm25", "pm10"} & provided))
        if computable:
            derived.add(metric)
        else:
            log.warning("Derived metric %s enabled but its inputs are not "
                        "provided by any loaded driver; skipping", metric)
    return TransformPipeline(transforms, derived)


def _parse_transform(driver: str, metric: str, spec: Dict[str, Any]) -> MetricTransform:
    where = f"sensors.{driver}.{metric}"
    unknown = sorted(set(spec) - set(_TRANSFORM_KEYS))
    if unknown:
        raise ConfigError(
            f"unknown key(s) in [{where}]: {', '.join(unknown)} "
            f"(allowed: {', '.join(_TRANSFORM_KEYS)})")

    def number(key: str, default: float) -> float:
        value = spec.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where}.{key} must be a number, "
                              f"got {type(value).__name__}")
        return float(value)

    smooth = spec.get("smooth", 1)
    if isinstance(smooth, bool) or not isinstance(smooth, int):
        raise ConfigError(f"{where}.smooth must be an int (samples), "
                          f"got {type(smooth).__name__}")
    if smooth < 1:
        raise ConfigError(f"{where}.smooth must be >= 1")
    factor = number("cpu_temp_compensation", 0.0)
    if factor < 0:
        raise ConfigError(f"{where}.cpu_temp_compensation must be >= 0 (0 disables)")
    return MetricTransform(
        offset=number("offset", 0.0),
        scale=number("scale", 1.0),
        smooth=smooth,
        cpu_temp_compensation=factor,
    )
