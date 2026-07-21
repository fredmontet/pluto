"""Tests for calibration, smoothing, and derived metrics."""

import math

import pytest

from pluto import config as config_module
from pluto.config import ConfigError, DerivedConfig
from pluto.drivers.base import Quality, Reading
from pluto.drivers.mock import MockDriver
from pluto.transforms import (
    MetricTransform,
    TransformPipeline,
    absolute_humidity,
    build_pipeline,
    calibrate,
    cpu_temp_compensation,
    dew_point,
    european_aqi,
    moving_average,
)


def sensors(data=None):
    return config_module.parse_config({"sensors": data or {}}).sensors


def ok(value, unit=""):
    return Reading.ok(value, unit)


# ── Pure functions ──────────────────────────────────────────────────

def test_calibrate_scales_then_offsets():
    assert calibrate(10.0, offset=2.0, scale=1.5) == 17.0
    assert calibrate(10.0) == 10.0


def test_moving_average():
    assert moving_average([10.0]) == 10.0
    assert moving_average([10.0, 20.0, 30.0]) == 20.0


def test_cpu_temp_compensation():
    # raw 25 °C with a 50 °C CPU and the default 2.25 factor.
    assert cpu_temp_compensation(25.0, 50.0, 2.25) == pytest.approx(13.888, abs=0.01)
    # CPU at ambient: no correction.
    assert cpu_temp_compensation(25.0, 25.0, 2.25) == 25.0


@pytest.mark.parametrize("t, rh, expected", [
    (20.0, 50.0, 9.3),   # classic reference point
    (25.0, 60.0, 16.7),
    (0.0, 100.0, 0.0),   # saturated air: dew point = temperature
    (30.0, 100.0, 30.0),
])
def test_dew_point_reference_values(t, rh, expected):
    assert dew_point(t, rh) == pytest.approx(expected, abs=0.3)


@pytest.mark.parametrize("t, rh, expected", [
    (20.0, 50.0, 8.6),
    (30.0, 80.0, 24.3),
    (20.0, 100.0, 17.3),
])
def test_absolute_humidity_reference_values(t, rh, expected):
    assert absolute_humidity(t, rh) == pytest.approx(expected, abs=0.4)


@pytest.mark.parametrize("pm25, pm10, expected", [
    (5.0, None, 1),
    (15.0, None, 2),
    (22.0, None, 3),
    (30.0, None, 4),
    (60.0, None, 5),
    (100.0, None, 6),
    (10.0, None, 1),    # band boundaries are inclusive
    (None, 30.0, 2),
    (5.0, 120.0, 5),    # overall index = worst pollutant band
    (None, None, None),
])
def test_european_aqi_bands(pm25, pm10, expected):
    assert european_aqi(pm25, pm10) == expected


# ── Pipeline behaviour ──────────────────────────────────────────────

def test_offset_and_scale_are_applied():
    pipeline = TransformPipeline(
        {"temperature": MetricTransform(offset=-0.5, scale=2.0)}, set())
    out = pipeline.apply({"temperature": ok(10.0, "°C"),
                          "humidity": ok(40.0, "%")})
    assert out["temperature"].value == 19.5
    assert out["temperature"].unit == "°C"  # everything else untouched
    assert out["humidity"].value == 40.0


def test_smoothing_warm_up_and_window():
    pipeline = TransformPipeline({"lux": MetricTransform(smooth=3)}, set())

    def value(v):
        return pipeline.apply({"lux": ok(v)})["lux"].value

    # Warm-up: the window is not full yet, so average what we have.
    assert value(10.0) == 10.0
    assert value(20.0) == 15.0
    assert value(30.0) == 20.0
    # Full window: the oldest sample falls out.
    assert value(40.0) == 30.0


def test_non_ok_readings_pass_through_untouched():
    pipeline = TransformPipeline({"temperature": MetricTransform(offset=5.0)}, set())
    missing = Reading.missing("°C")
    out = pipeline.apply({"temperature": missing})
    assert out["temperature"] is missing


def test_cpu_compensation_uses_injected_reader():
    pipeline = TransformPipeline(
        {"temperature": MetricTransform(cpu_temp_compensation=2.25)}, set(),
        cpu_temp_reader=lambda: 50.0)
    out = pipeline.apply({"temperature": ok(25.0, "°C"),
                          "raw_temperature": ok(25.0, "°C")})
    assert out["temperature"].value == pytest.approx(13.888, abs=0.01)
    assert out["raw_temperature"].value == 25.0  # never compensated


def test_cpu_compensation_skipped_without_cpu_temp():
    pipeline = TransformPipeline(
        {"temperature": MetricTransform(cpu_temp_compensation=2.25)}, set(),
        cpu_temp_reader=lambda: None)
    out = pipeline.apply({"temperature": ok(25.0, "°C")})
    assert out["temperature"].value == 25.0


def test_derived_metrics_flow_like_native_ones():
    pipeline = TransformPipeline({}, {"dew_point", "absolute_humidity", "aqi"})
    out = pipeline.apply({
        "temperature": ok(20.0, "°C"),
        "humidity": ok(50.0, "%"),
        "pm25": ok(15.0, "µg/m³"),
    })
    assert out["dew_point"].value == pytest.approx(9.3, abs=0.3)
    assert out["dew_point"].unit == "°C"
    assert out["dew_point"].driver == "derived"
    assert out["dew_point"].quality is Quality.OK
    assert out["absolute_humidity"].value == pytest.approx(8.6, abs=0.4)
    assert out["absolute_humidity"].unit == "g/m³"
    assert out["aqi"].value == 2.0


def test_derived_metrics_skipped_without_inputs():
    pipeline = TransformPipeline({}, {"dew_point", "aqi"})
    out = pipeline.apply({"temperature": ok(20.0, "°C"),
                          "humidity": Reading.error("%")})
    assert "dew_point" not in out
    assert "aqi" not in out


def test_derived_use_transformed_values():
    # A humidity offset must feed into the dew point.
    pipeline = TransformPipeline(
        {"humidity": MetricTransform(offset=50.0)}, {"dew_point"})
    out = pipeline.apply({"temperature": ok(20.0, "°C"), "humidity": ok(50.0, "%")})
    assert out["humidity"].value == 100.0
    assert out["dew_point"].value == pytest.approx(20.0, abs=0.1)  # saturated


# ── Building from config ────────────────────────────────────────────

def test_build_pipeline_merges_driver_defaults_with_config():
    class FakeBME(MockDriver):
        name = "mock"
        default_transforms = {"temperature": {"cpu_temp_compensation": 2.25}}

    driver = FakeBME()
    cfg = sensors({"mock": {"temperature": {"offset": -1.0,
                                            "cpu_temp_compensation": 4.0}}})
    pipeline = build_pipeline([driver], cfg, DerivedConfig())
    t = pipeline.transforms["temperature"]
    assert t.cpu_temp_compensation == 4.0  # config overrides the default
    assert t.offset == -1.0


def test_build_pipeline_rejects_unknown_metric():
    with pytest.raises(ConfigError, match="provides no metric 'tempreture'"):
        build_pipeline([MockDriver()],
                       sensors({"mock": {"tempreture": {"offset": 1.0}}}),
                       DerivedConfig())


@pytest.mark.parametrize("spec, match", [
    ({"offst": 1.0}, r"unknown key\(s\) in \[sensors.mock.temperature\]: offst"),
    ({"offset": "big"}, "offset must be a number"),
    ({"smooth": 2.5}, "smooth must be an int"),
    ({"smooth": 0}, "smooth must be >= 1"),
    ({"cpu_temp_compensation": -1}, "must be >= 0"),
])
def test_build_pipeline_validates_transform_specs(spec, match):
    with pytest.raises(ConfigError, match=match):
        build_pipeline([MockDriver()],
                       sensors({"mock": {"temperature": spec}}),
                       DerivedConfig())


def test_derived_enabled_without_inputs_is_skipped_with_warning(caplog):
    driver = MockDriver({"pms": False})  # no particulates
    with caplog.at_level("WARNING"):
        pipeline = build_pipeline([driver], sensors(),
                                  DerivedConfig(dew_point=True, aqi=True))
    assert pipeline.derived_fields == {"dew_point"}
    assert "aqi" in caplog.text


def test_derived_metrics_reach_snapshot_and_flatten():
    from pluto.drivers import flatten, read_all

    pipeline = build_pipeline([MockDriver()], sensors(),
                              DerivedConfig(dew_point=True, aqi=True))
    readings = pipeline.apply(read_all([MockDriver()]))
    flat = flatten(readings)
    assert flat.dew_point is not None
    assert flat.aqi is not None
