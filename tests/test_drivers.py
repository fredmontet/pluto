"""Tests for the driver system: mock driver, loading, merging, plugins."""

import pytest

from pluto import config as config_module
from pluto.config import ConfigError
from pluto.drivers import (
    load_drivers,
    provided_fields,
    read_all,
    registry,
)
from pluto.drivers.base import Driver, Quality, Reading, flatten
from pluto.drivers.microphone import MicrophoneDriver
from pluto.drivers.mock import MockDriver


def sensors(data=None):
    """A SensorsConfig from a would-be [sensors] TOML table."""
    return config_module.parse_config({"sensors": data or {}}).sensors


class BoomDriver(Driver):
    """A misbehaving third-party driver: read() raises."""

    name = "boom"
    provides = ("boom_metric",)

    def available(self) -> bool:
        return True

    def read(self):
        raise RuntimeError("boom")


class CustomDriver(Driver):
    """A well-behaved third-party driver, as an entry point would add."""

    name = "custom"
    provides = ("custom_metric",)

    def available(self) -> bool:
        return True

    def read(self):
        return {"custom_metric": Reading.ok(42.0, "things")}


class FakeEntryPoint:
    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


# ── Mock driver ─────────────────────────────────────────────────────

def test_mock_driver_reads_every_field_it_provides():
    driver = MockDriver()
    assert driver.available()
    readings = driver.read()
    assert set(readings) == set(driver.provides)
    for field in ("temperature", "humidity", "pressure", "lux", "oxidising",
                  "pm1", "pm25", "pm10", "noise"):
        assert field in readings
    assert all(r.quality is Quality.OK and r.value is not None
               for r in readings.values())
    assert readings["temperature"].unit == "°C"
    assert readings["pm25"].unit == "µg/m³"
    assert readings["proximity"].value == 0.0


def test_mock_driver_settings_drop_fields():
    driver = MockDriver({"pms": False, "noise": False})
    readings = driver.read()
    for field in ("pm1", "pm25", "pm10", "noise"):
        assert field not in driver.provides
        assert field not in readings
    assert "temperature" in readings


# ── Driver settings validation ──────────────────────────────────────

def test_unknown_setting_is_an_error():
    with pytest.raises(ConfigError, match=r"unknown setting\(s\) for \[sensors.mock\]: bogus"):
        MockDriver({"bogus": 1})


@pytest.mark.parametrize("value, match", [
    (0, "must be > 0"),
    (-2.0, "must be > 0"),
    ("fast", "must be a number"),
    (True, "must be a number"),
])
def test_microphone_interval_validation(value, match):
    with pytest.raises(ConfigError, match=match):
        MicrophoneDriver({"interval": value})


def test_mock_bool_settings_validation():
    with pytest.raises(ConfigError, match="sensors.mock.pms must be a bool"):
        MockDriver({"pms": 1})


# ── read_all / snapshot ─────────────────────────────────────────────

def test_read_all_merges_drivers():
    merged = read_all([MockDriver({"pms": False, "noise": False}), CustomDriver()])
    assert merged["custom_metric"].value == 42.0
    assert merged["temperature"].quality is Quality.OK


def test_read_all_survives_a_driver_that_raises():
    merged = read_all([MockDriver({"pms": False, "noise": False}), BoomDriver()])
    assert merged["boom_metric"].quality is Quality.ERROR
    assert merged["boom_metric"].value is None
    # The healthy driver is unaffected.
    assert merged["temperature"].quality is Quality.OK
    assert merged["temperature"].value is not None


def test_flatten_blanks_bad_readings():
    snap = flatten({
        "temperature": Reading.ok(21.5, "°C"),
        "humidity": Reading.error("%"),
        "noise": Reading.missing(),
        "custom_metric": Reading.ok(1.0),  # not a standard field: ignored
    })
    assert snap.temperature == 21.5
    assert snap.humidity is None
    assert snap.noise is None
    assert snap.pm25 is None  # never reported


# ── Loading ─────────────────────────────────────────────────────────

def test_mock_mode_loads_only_the_mock_driver():
    drivers = load_drivers(sensors(), mock=True)
    assert [d.name for d in drivers] == ["mock"]
    assert {"temperature", "pm25", "noise"} <= provided_fields(drivers)


def test_mock_mode_honours_disabled_hardware_drivers():
    cfg = sensors({"pms5003": {"enabled": False}, "microphone": {"enabled": False}})
    fields = provided_fields(load_drivers(cfg, mock=True))
    assert "pm25" not in fields and "noise" not in fields
    assert "temperature" in fields


def test_mock_settings_table_wins_in_mock_mode():
    cfg = sensors({"mock": {"pms": False}})
    fields = provided_fields(load_drivers(cfg, mock=True))
    assert "pm25" not in fields


def test_no_pms_flag_shapes_mock_mode():
    from pluto.__main__ import build_parser

    cfg = config_module.apply_cli_overrides(
        config_module.Config(), build_parser().parse_args(["--no-pms"]))
    fields = provided_fields(load_drivers(cfg.sensors, mock=True))
    assert "pm25" not in fields


def test_unknown_driver_name_is_an_error():
    with pytest.raises(ConfigError, match="unknown sensor driver"):
        load_drivers(sensors({"nope": {"enabled": True}}))


def test_hardware_drivers_degrade_gracefully_off_pi():
    # No Enviro+ here: every hardware probe fails, nothing crashes,
    # nothing loads.
    assert load_drivers(sensors()) == []


def test_declared_mock_loads_outside_mock_mode():
    drivers = load_drivers(sensors({"mock": {"enabled": True}}))
    assert [d.name for d in drivers] == ["mock"]


def test_undeclared_mock_stays_out():
    assert all(d.name != "mock" for d in load_drivers(sensors()))


# ── Entry-point discovery ───────────────────────────────────────────

def test_entry_point_driver_is_discovered_and_loaded(monkeypatch):
    monkeypatch.setattr("pluto.drivers._entry_points",
                        lambda: [FakeEntryPoint("custom", CustomDriver)])
    assert registry()["custom"] is CustomDriver
    drivers = load_drivers(sensors({"custom": {"enabled": True}}))
    assert [d.name for d in drivers] == ["custom"]
    assert read_all(drivers)["custom_metric"].value == 42.0


def test_broken_entry_point_is_ignored(monkeypatch):
    monkeypatch.setattr("pluto.drivers._entry_points", lambda: [
        FakeEntryPoint("bad", ImportError("no such module")),
        FakeEntryPoint("notadriver", object),
        FakeEntryPoint("custom", CustomDriver),
    ])
    reg = registry()
    assert "bad" not in reg and "notadriver" not in reg
    assert "custom" in reg


def test_entry_point_cannot_shadow_builtin(monkeypatch):
    class Impostor(CustomDriver):
        name = "bme280"

    monkeypatch.setattr("pluto.drivers._entry_points",
                        lambda: [FakeEntryPoint("bme280", Impostor)])
    assert registry()["bme280"] is not Impostor


# ── End-to-end wiring ───────────────────────────────────────────────

def test_app_publishes_to_png_sink_from_the_mock_driver(tmp_path):
    from pluto.app import App
    from pluto.sinks import SinkContext
    from pluto.sinks.png import PNGSink

    drivers = load_drivers(sensors(), mock=True)
    fields = provided_fields(drivers)
    sink = PNGSink({"dir": str(tmp_path)}, SinkContext(fields=fields))
    App(drivers, sinks=[sink]).run_once()
    assert len(list(tmp_path.glob("*.png"))) == len(sink._renderer.pages)
