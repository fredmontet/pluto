"""Tests for the data model: snapshots, metadata, clock plausibility."""

import pathlib
import socket
from datetime import datetime, timezone

from pluto.config import DeviceConfig
from pluto.drivers import read_all
from pluto.drivers.base import Quality, Reading
from pluto.drivers.mock import MockDriver
from pluto.model import (
    DERIVED_METRICS,
    METRICS,
    MIN_PLAUSIBLE_YEAR,
    VERSION,
    Snapshot,
    json_payload,
    make_snapshot,
)


def fixed_now(year=2026):
    return datetime(year, 7, 20, 12, 30, 45, 123000, tzinfo=timezone.utc)


def test_make_snapshot_attaches_metadata(monkeypatch):
    monkeypatch.setattr("pluto.model._utcnow", fixed_now)
    device = DeviceConfig(id="balcony-pi", location="balcony", description="north side")
    snapshot = make_snapshot({"temperature": Reading.ok(20.0, "°C")}, device)
    assert snapshot.device_id == "balcony-pi"
    assert snapshot.location == "balcony"
    assert snapshot.description == "north side"
    assert snapshot.version == VERSION and VERSION != ""
    assert snapshot.timestamp.tzinfo is timezone.utc
    assert snapshot.iso_timestamp == "2026-07-20T12:30:45.123Z"
    assert not snapshot.time_uncertain


def test_device_id_defaults_to_hostname():
    snapshot = make_snapshot({}, DeviceConfig())
    assert snapshot.device_id == socket.gethostname()


def test_clock_before_min_year_flags_time_uncertain(monkeypatch):
    monkeypatch.setattr("pluto.model._utcnow", lambda: fixed_now(year=2021))
    assert make_snapshot({}, DeviceConfig()).time_uncertain

    # Once NTP has synced the clock, the flag clears.
    monkeypatch.setattr("pluto.model._utcnow",
                        lambda: fixed_now(year=MIN_PLAUSIBLE_YEAR))
    assert not make_snapshot({}, DeviceConfig()).time_uncertain


def test_time_uncertain_appears_in_payload_only_when_true(monkeypatch):
    monkeypatch.setattr("pluto.model._utcnow", lambda: fixed_now(year=2021))
    payload = json_payload(make_snapshot({}, DeviceConfig(id="x")))
    assert payload["time_uncertain"] is True

    monkeypatch.setattr("pluto.model._utcnow", fixed_now)
    payload = json_payload(make_snapshot({}, DeviceConfig(id="x")))
    assert "time_uncertain" not in payload


def test_json_payload_metadata_and_omissions():
    snapshot = Snapshot(
        fixed_now(),
        {"temperature": Reading.ok(20.06789, "°C"),
         "pm25": Reading.missing("µg/m³"),
         "humidity": Reading.error("%")},
        device_id="pi", location="", description="")
    payload = json_payload(snapshot)
    assert payload == {"temperature": 20.068,  # rounded, non-ok omitted
                       "timestamp": "2026-07-20T12:30:45.123Z",
                       "device": "pi", "version": VERSION}


def test_snapshot_json_roundtrip():
    original = Snapshot(
        fixed_now(),
        {"temperature": Reading(20.0, "°C", Quality.OK, "bme280"),
         "noise": Reading(None, "dB", Quality.MISSING, "microphone")},
        device_id="pi", location="attic", description="d",
        version="1.2.3", time_uncertain=True)
    restored = Snapshot.from_json(original.to_json())
    assert restored == original


def test_read_all_stamps_driver_names():
    readings = read_all([MockDriver()])
    assert readings and all(r.driver == "mock" for r in readings.values())


def test_builtin_driver_units_match_catalogue():
    readings = MockDriver().read()
    assert set(readings) == set(METRICS) - set(DERIVED_METRICS)
    for name, reading in readings.items():
        assert reading.unit == METRICS[name].unit, name


def test_docs_cover_every_metric():
    docs = (pathlib.Path(__file__).resolve().parent.parent
            / "docs" / "metrics.md").read_text()
    for name, metric in METRICS.items():
        assert f"`{name}`" in docs, name
        assert metric.prometheus in docs, name
