"""The data model: self-describing snapshots and the metric catalogue.

Every reading pluto emits is wrapped in a Snapshot carrying the device
identity, location, the pluto version and a UTC timestamp, so
downstream storage and analysis need no out-of-band context. The
metric catalogue below is the single source of truth for metric names,
units and per-sink conventions; docs/metrics.md documents it.
"""

import json
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version
from typing import Dict, Optional

from .config import DeviceConfig
from .drivers.base import Quality, Reading

try:
    VERSION = _package_version("pluto")
except PackageNotFoundError:  # running from a bare checkout
    VERSION = "0+unknown"

# Before NTP sync an RTC-less Pi wakes up in the past; timestamps from
# before this year mark their snapshots as time_uncertain.
MIN_PLAUSIBLE_YEAR = 2024


@dataclass(frozen=True)
class Metric:
    """One catalogued metric: its unit and per-sink conventions."""

    label: str  # human-friendly name (used as the Home Assistant entity name)
    unit: str  # canonical unit string, identical across all sinks
    description: str
    device_class: Optional[str]  # Home Assistant device class
    prometheus: str  # Prometheus gauge name


# Units follow Home Assistant / OpenMetrics conventions. Metric names
# are snake_case and shared across every sink; docs/metrics.md holds
# the rendered table.
METRICS: Dict[str, Metric] = {
    "temperature": Metric(
        "Temperature", "°C", "Ambient temperature, CPU-heat compensated",
        "temperature", "pluto_temperature_celsius"),
    "raw_temperature": Metric(
        "Raw temperature", "°C", "Uncompensated BME280 temperature",
        "temperature", "pluto_raw_temperature_celsius"),
    "humidity": Metric(
        "Humidity", "%", "Relative humidity",
        "humidity", "pluto_humidity_percent"),
    "pressure": Metric(
        "Pressure", "hPa", "Barometric pressure",
        "atmospheric_pressure", "pluto_pressure_hpa"),
    "lux": Metric(
        "Light", "lx", "Illuminance",
        "illuminance", "pluto_light_lux"),
    "proximity": Metric(
        "Proximity", "", "LTR559 proximity counts, unitless",
        None, "pluto_proximity"),
    "oxidising": Metric(
        "Gas oxidising", "kΩ", "MICS6814 oxidising gas resistance",
        None, "pluto_gas_oxidising_kohms"),
    "reducing": Metric(
        "Gas reducing", "kΩ", "MICS6814 reducing gas resistance",
        None, "pluto_gas_reducing_kohms"),
    "nh3": Metric(
        "Gas NH3", "kΩ", "MICS6814 NH3 gas resistance",
        None, "pluto_gas_nh3_kohms"),
    "pm1": Metric(
        "PM1.0", "µg/m³", "Particulate matter ≤ 1.0 µm",
        "pm1", "pluto_particulates_ug_per_m3"),
    "pm25": Metric(
        "PM2.5", "µg/m³", "Particulate matter ≤ 2.5 µm",
        "pm25", "pluto_particulates_ug_per_m3"),
    "pm10": Metric(
        "PM10", "µg/m³", "Particulate matter ≤ 10 µm",
        "pm10", "pluto_particulates_ug_per_m3"),
    "noise": Metric(
        "Noise", "dB", "Noise level relative to full scale (uncalibrated dBFS)",
        "sound_pressure", "pluto_noise_decibels"),
}

# The particulate metrics share one Prometheus gauge with a size label.
PM_SIZES = {"pm1": "1.0", "pm25": "2.5", "pm10": "10"}


@dataclass
class Snapshot:
    """One timestamped, self-describing set of merged driver readings."""

    timestamp: datetime  # timezone-aware, UTC
    readings: Dict[str, Reading] = field(default_factory=dict)
    device_id: str = ""
    location: str = ""
    description: str = ""
    version: str = VERSION
    time_uncertain: bool = False

    @property
    def iso_timestamp(self) -> str:
        """ISO 8601 in UTC, e.g. ``2026-07-20T20:15:03.123Z``."""
        return (self.timestamp.astimezone(timezone.utc)
                .isoformat(timespec="milliseconds").replace("+00:00", "Z"))

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.iso_timestamp,
            "device": self.device_id,
            "location": self.location,
            "description": self.description,
            "version": self.version,
            "time_uncertain": self.time_uncertain,
            "readings": {f: [r.value, r.unit, r.quality.value, r.driver]
                         for f, r in self.readings.items()},
        })

    @classmethod
    def from_json(cls, text: str) -> "Snapshot":
        doc = json.loads(text)
        return cls(
            timestamp=datetime.fromisoformat(doc["timestamp"].replace("Z", "+00:00")),
            readings={f: Reading(value, unit, Quality(quality), driver)
                      for f, (value, unit, quality, driver) in doc["readings"].items()},
            device_id=doc["device"],
            location=doc["location"],
            description=doc["description"],
            version=doc["version"],
            time_uncertain=doc["time_uncertain"],
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_snapshot(readings: Dict[str, Reading], device: DeviceConfig) -> Snapshot:
    """Wrap merged driver readings with metadata and a UTC timestamp."""
    now = _utcnow()
    return Snapshot(
        timestamp=now,
        readings=dict(readings),
        device_id=device.id or socket.gethostname(),
        location=device.location,
        description=device.description,
        time_uncertain=now.year < MIN_PLAUSIBLE_YEAR,
    )


def json_payload(snapshot: Snapshot) -> Dict[str, object]:
    """The flat JSON document network sinks send.

    Ok values plus the snapshot metadata; missing/error readings are
    omitted (consumers see them as absent, not null), empty location
    and description are dropped, and ``time_uncertain`` appears only
    when true. Timestamps are the snapshot's read time in UTC, so
    buffered snapshots replayed after an outage keep their original
    time.
    """
    payload: Dict[str, object] = {
        f: round(r.value, 3)
        for f, r in snapshot.readings.items()
        if r.quality is Quality.OK and isinstance(r.value, (int, float))
    }
    payload["timestamp"] = snapshot.iso_timestamp
    payload["device"] = snapshot.device_id
    if snapshot.location:
        payload["location"] = snapshot.location
    if snapshot.description:
        payload["description"] = snapshot.description
    payload["version"] = snapshot.version
    if snapshot.time_uncertain:
        payload["time_uncertain"] = True
    return payload
