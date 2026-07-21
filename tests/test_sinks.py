"""Tests for the sink system: loading, buffering, queue cap, new sinks."""

import json
from datetime import datetime, timezone

import pytest

from pluto import config as config_module
from pluto.config import BufferConfig, ConfigError
from pluto.drivers.base import Reading
from pluto.model import VERSION, Snapshot
from pluto.sinks import SinkContext, load_sinks, registry
from pluto.sinks.base import Sink
from pluto.sinks.buffer import BufferedSink, SnapshotQueue
from pluto.sinks.mqtt import MQTTSink


def outputs(data=None):
    """An OutputsConfig from a would-be [outputs] TOML table."""
    return config_module.parse_config({"outputs": data or {}}).outputs


def ts(seconds):
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def iso(seconds):
    return ts(seconds).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def snap(seconds, temperature=20.0):
    return Snapshot(ts(seconds),
                    {"temperature": Reading.ok(temperature, "°C")},
                    device_id="test-pi")


def buffer_cfg(tmp_path, **kwargs):
    return BufferConfig(path=str(tmp_path / "buffer.db"), **kwargs)


class RecordingSink(Sink):
    """A sink that records snapshots and can be told to fail."""

    name = "recording"

    def __init__(self, settings=None, context=None):
        super().__init__(settings, context)
        self.published = []
        self.fail = False

    def publish(self, snapshot):
        if self.fail:
            raise RuntimeError("down")
        self.published.append(snapshot)


class FakeInfo:
    def __init__(self, rc=0):
        self.rc = rc


class FakeMQTTClient:
    """Stands in for paho.mqtt.client.Client during tests."""

    def __init__(self, callback_api_version=None, client_id=""):
        self.client_id = client_id
        self.published = []  # (topic, payload)
        self.on_connect = None
        self.on_disconnect = None
        self.connect_target = None

    def username_pw_set(self, username, password=None):
        pass

    def will_set(self, topic, payload=None, retain=False):
        self.will = (topic, payload)

    def connect_async(self, host, port=1883):
        self.connect_target = (host, port)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload))
        return FakeInfo(0)


@pytest.fixture
def fake_mqtt(monkeypatch):
    monkeypatch.setattr("paho.mqtt.client.Client", FakeMQTTClient)


# ── Loading from config ─────────────────────────────────────────────

def test_no_declared_sinks_loads_nothing(tmp_path):
    assert load_sinks(outputs(), SinkContext(), buffer_cfg(tmp_path)) == []


def test_local_sinks_load_unwrapped(tmp_path):
    cfg = outputs({
        "sqlite": {"path": str(tmp_path / "data.db")},
        "csv": {"dir": str(tmp_path / "csv")},
    })
    sinks = load_sinks(cfg, SinkContext(), buffer_cfg(tmp_path))
    assert [s.name for s in sinks] == ["csv", "sqlite"]
    assert not any(isinstance(s, BufferedSink) for s in sinks)


def test_network_sink_gets_wrapped_in_buffer(tmp_path, fake_mqtt):
    cfg = outputs({"mqtt": {"host": "broker.local"}})
    sinks = load_sinks(cfg, SinkContext(), buffer_cfg(tmp_path), start_workers=False)
    (sink,) = sinks
    assert isinstance(sink, BufferedSink)
    assert sink.name == "mqtt"
    assert isinstance(sink.inner, MQTTSink)


def test_buffering_can_be_disabled(tmp_path, fake_mqtt):
    cfg = outputs({"mqtt": {"host": "broker.local"}})
    (sink,) = load_sinks(cfg, SinkContext(),
                         buffer_cfg(tmp_path, enabled=False))
    assert isinstance(sink, MQTTSink)


def test_disabled_sink_is_skipped(tmp_path):
    cfg = outputs({"sqlite": {"enabled": False, "path": str(tmp_path / "x.db")}})
    assert load_sinks(cfg, SinkContext(), buffer_cfg(tmp_path)) == []


def test_unknown_sink_name_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="unknown output sink"):
        load_sinks(outputs({"nope": {}}), SinkContext(), buffer_cfg(tmp_path))


@pytest.mark.parametrize("data, match", [
    ({"mqtt": {}}, "outputs.mqtt.host is required"),
    ({"mqtt": {"host": "b", "qos": 2}}, r"unknown setting\(s\) for \[outputs.mqtt\]: qos"),
    ({"mqtt": {"host": "b", "port": 0}}, "outputs.mqtt.port must be >= 1"),
    ({"http": {}}, "outputs.http.url is required"),
    ({"http": {"url": "ftp://x"}}, "must start with http"),
    ({"http": {"url": "http://x", "timeout": 0}}, "outputs.http.timeout must be > 0"),
    ({"sqlite": {"path": ""}}, "outputs.sqlite.path must not be empty"),
    ({"csv": {"dir": 3}}, "outputs.csv.dir must be a str"),
])
def test_sink_settings_validation(tmp_path, fake_mqtt, data, match):
    with pytest.raises(ConfigError, match=match):
        load_sinks(outputs(data), SinkContext(), buffer_cfg(tmp_path))


def test_entry_point_sink_is_discovered(tmp_path, monkeypatch):
    class CustomSink(RecordingSink):
        name = "custom"

    class FakeEntryPoint:
        name = "custom"

        @staticmethod
        def load():
            return CustomSink

    monkeypatch.setattr("pluto.sinks._entry_points", lambda: [FakeEntryPoint()])
    assert registry()["custom"] is CustomSink
    sinks = load_sinks(outputs({"custom": {}}), SinkContext(), buffer_cfg(tmp_path))
    assert [s.name for s in sinks] == ["custom"]


# ── Buffering across an outage ──────────────────────────────────────

def make_buffered_mqtt(tmp_path, clock, max_snapshots=100):
    sink = MQTTSink({"host": "broker.local"}, SinkContext())
    queue = SnapshotQueue(str(tmp_path / "buffer.db"), max_snapshots=max_snapshots)
    return BufferedSink(sink, queue, start_worker=False, clock=clock), sink, queue


def readings_payloads(fake_client):
    return [json.loads(payload) for topic, payload in fake_client.published
            if topic.endswith("/readings")]


def test_buffering_across_a_network_outage(tmp_path, fake_mqtt):
    now = [100.0]
    buffered, mqtt_sink, queue = make_buffered_mqtt(tmp_path, lambda: now[0])
    client = mqtt_sink._client

    # Broker not connected yet: snapshots pile up in the queue.
    buffered.publish(snap(1.0))
    buffered.publish(snap(2.0))
    assert buffered.flush() is False
    assert readings_payloads(client) == []
    assert queue.pending("mqtt") == 2

    # While backing off, flush is a cheap no-op.
    assert buffered.flush() is False
    assert queue.pending("mqtt") == 2

    # The broker comes back: on_connect resets the backoff via
    # notify_ready, and the backlog flushes in order.
    client.on_connect(client, None, None, 0, None)
    assert buffered.flush() is True
    payloads = readings_payloads(client)
    assert [p["timestamp"] for p in payloads] == [iso(1.0), iso(2.0)]
    assert queue.pending("mqtt") == 0

    # Subsequent publishes flow straight through.
    buffered.publish(snap(3.0))
    assert buffered.flush() is True
    assert [p["timestamp"] for p in readings_payloads(client)] == [
        iso(1.0), iso(2.0), iso(3.0)]


def test_backoff_grows_exponentially(tmp_path, fake_mqtt):
    now = [0.0]
    buffered, mqtt_sink, queue = make_buffered_mqtt(tmp_path, lambda: now[0])

    buffered.publish(snap(1.0))
    assert buffered.flush() is False
    assert buffered._backoff == BufferedSink.INITIAL_BACKOFF

    now[0] += buffered._backoff + 0.1
    assert buffered.flush() is False
    assert buffered._backoff == BufferedSink.INITIAL_BACKOFF * 2

    now[0] += buffered._backoff + 0.1
    assert buffered.flush() is False
    assert buffered._backoff == BufferedSink.INITIAL_BACKOFF * 4

    # ... capped at MAX_BACKOFF.
    buffered._backoff = BufferedSink.MAX_BACKOFF
    now[0] += buffered._backoff + 0.1
    assert buffered.flush() is False
    assert buffered._backoff == BufferedSink.MAX_BACKOFF


def test_disconnect_buffers_again(tmp_path, fake_mqtt):
    now = [0.0]
    buffered, mqtt_sink, queue = make_buffered_mqtt(tmp_path, lambda: now[0])
    client = mqtt_sink._client
    client.on_connect(client, None, None, 0, None)

    buffered.publish(snap(1.0))
    assert buffered.flush() is True

    client.on_disconnect(client, None, None, 0, None)
    buffered.publish(snap(2.0))
    assert buffered.flush() is False
    assert queue.pending("mqtt") == 1


# ── Queue cap and persistence ───────────────────────────────────────

def test_queue_caps_and_drops_oldest(tmp_path):
    queue = SnapshotQueue(str(tmp_path / "buffer.db"), max_snapshots=3)
    drops = [queue.push("mqtt", snap(float(i))) for i in range(1, 6)]
    assert drops == [0, 0, 0, 1, 1]
    assert queue.pending("mqtt") == 3
    kept = [s.timestamp for _, s in queue.oldest("mqtt", 10)]
    assert kept == [ts(3.0), ts(4.0), ts(5.0)]


def test_buffered_sink_logs_drops(tmp_path, caplog):
    inner = RecordingSink()
    inner.buffered = True
    queue = SnapshotQueue(str(tmp_path / "buffer.db"), max_snapshots=1)
    buffered = BufferedSink(inner, queue, start_worker=False)
    inner.fail = True
    buffered.publish(snap(1.0))
    buffered.flush()
    with caplog.at_level("WARNING"):
        buffered.publish(snap(2.0))
    assert "dropped 1 oldest" in caplog.text


def test_queue_survives_restart(tmp_path):
    path = str(tmp_path / "buffer.db")
    queue = SnapshotQueue(path, max_snapshots=10)
    queue.push("mqtt", snap(1.0))
    queue.push("mqtt", snap(2.0))
    queue.close()

    reopened = SnapshotQueue(path, max_snapshots=10)
    assert reopened.pending("mqtt") == 2
    restored = [s for _, s in reopened.oldest("mqtt", 10)]
    assert [s.timestamp for s in restored] == [ts(1.0), ts(2.0)]
    assert restored[0].device_id == "test-pi"
    assert restored[0].version == VERSION
    r = restored[0].readings["temperature"]
    assert (r.value, r.unit, r.quality) == (20.0, "°C", Reading.ok(0).quality)


def test_queues_are_per_sink(tmp_path):
    queue = SnapshotQueue(str(tmp_path / "buffer.db"), max_snapshots=10)
    queue.push("mqtt", snap(1.0))
    queue.push("http", snap(2.0))
    assert queue.pending("mqtt") == 1
    assert queue.pending("http") == 1
    queue.remove([rowid for rowid, _ in queue.oldest("mqtt", 10)])
    assert queue.pending("mqtt") == 0
    assert queue.pending("http") == 1


# ── The new local sinks ─────────────────────────────────────────────

def test_sqlite_sink_appends_and_prunes(tmp_path):
    import sqlite3

    from pluto.sinks.sqlite import SQLiteSink

    path = str(tmp_path / "data.db")
    sink = SQLiteSink({"path": path, "max_rows": 3})
    for i in range(1, 6):
        sink.publish(snap(float(i), temperature=20.0 + i))
    sink.close()

    rows = sqlite3.connect(path).execute(
        "SELECT timestamp, device, version, time_uncertain, data"
        " FROM readings ORDER BY id").fetchall()
    assert [row[0] for row in rows] == [iso(3.0), iso(4.0), iso(5.0)]
    assert rows[-1][1] == "test-pi"
    assert rows[-1][2] == VERSION
    assert rows[-1][3] == 0
    assert json.loads(rows[-1][4]) == {"temperature": {
        "value": 25.0, "unit": "°C", "quality": "ok", "driver": ""}}


def test_sqlite_sink_keeps_quality_of_missing_sensors(tmp_path):
    """Payloads omit non-ok readings, but SQLite records them."""
    import sqlite3

    from pluto.model import json_payload
    from pluto.sinks.sqlite import SQLiteSink

    snapshot = snap(1.0)
    snapshot.readings["pm25"] = Reading.missing("µg/m³")
    snapshot.readings["pm25"].driver = "pms5003"
    assert "pm25" not in json_payload(snapshot)  # current wire behaviour

    path = str(tmp_path / "data.db")
    sink = SQLiteSink({"path": path})
    sink.publish(snapshot)
    sink.close()
    (data,) = sqlite3.connect(path).execute(
        "SELECT data FROM readings").fetchone()
    assert json.loads(data)["pm25"] == {
        "value": None, "unit": "µg/m³", "quality": "missing", "driver": "pms5003"}


def test_csv_sink_rotates_daily(tmp_path):
    import csv as csv_module

    from pluto.sinks.csv import CSVSink

    sink = CSVSink({"dir": str(tmp_path)}, SinkContext(fields={"temperature"}))
    day1 = 1_750_000_000.0
    sink.publish(snap(day1, temperature=21.0))
    sink.publish(snap(day1 + 60, temperature=22.0))
    sink.publish(snap(day1 + 86400, temperature=23.0))  # next day
    sink.close()

    files = sorted(tmp_path.glob("pluto-*.csv"))
    assert len(files) == 2
    with open(files[0], newline="") as f:
        rows = list(csv_module.reader(f))
    assert rows[0][:6] == ["time", "device", "location", "description",
                           "version", "time_uncertain"]
    assert "temperature" in rows[0]
    col = rows[0].index("temperature")
    assert [r[col] for r in rows[1:]] == ["21.0", "22.0"]
    assert rows[1][0] == iso(day1)
    assert rows[1][1] == "test-pi"
    assert rows[1][4] == VERSION
    with open(files[1], newline="") as f:
        assert len(list(csv_module.reader(f))) == 2  # header + one row


def test_http_sink_posts_json_with_token(monkeypatch):
    import urllib.request

    from pluto.sinks.http import HTTPSink

    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"ok"

    def fake_urlopen(request, timeout=None):
        seen["request"] = request
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sink = HTTPSink({"url": "https://example.com/ingest", "token": "tok", "timeout": 3.0})
    sink.publish(snap(5.0))

    request = seen["request"]
    assert request.full_url == "https://example.com/ingest"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer tok"
    assert seen["timeout"] == 3.0
    body = json.loads(request.data.decode())
    assert body == {"temperature": 20.0, "timestamp": iso(5.0),
                    "device": "test-pi", "version": VERSION}


def test_mqtt_payload_carries_metadata(fake_mqtt):
    sink = MQTTSink({"host": "broker.local"}, SinkContext())
    client = sink._client
    client.on_connect(client, None, None, 0, None)

    snapshot = snap(7.0)
    snapshot.location = "balcony"
    snapshot.time_uncertain = True
    sink.publish(snapshot)
    (payload,) = readings_payloads(client)
    assert payload == {"temperature": 20.0, "timestamp": iso(7.0),
                       "device": "test-pi", "location": "balcony",
                       "version": VERSION, "time_uncertain": True}


def test_prometheus_gains_device_and_location_labels():
    from prometheus_client import CollectorRegistry

    from pluto.sinks.prometheus import PrometheusSink

    registry_ = CollectorRegistry()
    sink = PrometheusSink({}, SinkContext(), registry=registry_, start_server=False)
    snapshot = snap(1.0, temperature=21.5)
    snapshot.location = "balcony"
    snapshot.readings["pm25"] = Reading.ok(12.0, "µg/m³")
    sink.publish(snapshot)

    labels = {"device": "test-pi", "location": "balcony"}
    assert registry_.get_sample_value("pluto_temperature_celsius", labels) == 21.5
    assert registry_.get_sample_value(
        "pluto_particulates_ug_per_m3", {**labels, "size": "2.5"}) == 12.0
    # A metric with no reading reports NaN.
    import math
    assert math.isnan(registry_.get_sample_value("pluto_humidity_percent", labels))


def test_http_sink_failure_is_buffered(tmp_path, monkeypatch):
    import urllib.request

    from pluto.sinks.http import HTTPSink

    def fail_urlopen(request, timeout=None):
        raise OSError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    sink = HTTPSink({"url": "http://example.com/ingest"})
    queue = SnapshotQueue(str(tmp_path / "buffer.db"), max_snapshots=10)
    buffered = BufferedSink(sink, queue, start_worker=False)
    buffered.publish(snap(1.0))
    assert buffered.flush() is False
    assert queue.pending("http") == 1


# ── LCD and PNG display sinks ───────────────────────────────────────

class FakeST7735Device:
    def __init__(self, **kwargs):
        self.frames = []
        self.backlight = None

    def begin(self):
        pass

    def display(self, image):
        self.frames.append(image)

    def set_backlight(self, value):
        self.backlight = value


@pytest.fixture
def fake_st7735(monkeypatch):
    import types

    module = types.ModuleType("st7735")
    module.ST7735 = FakeST7735Device
    monkeypatch.setitem(__import__("sys").modules, "st7735", module)
    return module


def prox_snap(seconds, proximity):
    from pluto.model import make_snapshot
    from pluto.config import DeviceConfig
    s = snap(seconds)
    s.readings["proximity"] = Reading.ok(proximity)
    return s


def make_lcd(clock):
    """An LCD sink wired to a fake panel, worker thread not started."""
    from pluto.sinks.lcd import LCDSink
    sink = LCDSink({"cycle": 10.0}, SinkContext(), clock=clock)
    sink._device = FakeST7735Device()
    return sink


def test_lcd_autodetects_when_hardware_present(tmp_path, fake_st7735):
    sinks = load_sinks(outputs(), SinkContext(), buffer_cfg(tmp_path))
    assert [s.name for s in sinks] == ["lcd"]
    sinks[0].close()  # stop the worker thread
    # available() opened and began the panel.
    assert isinstance(sinks[0]._device, FakeST7735Device)


def test_lcd_absent_hardware_loads_nothing(tmp_path):
    # No st7735 module in this env: available() fails, nothing loads.
    assert load_sinks(outputs(), SinkContext(), buffer_cfg(tmp_path)) == []


def test_lcd_disabled_by_config_is_skipped(tmp_path, fake_st7735):
    cfg = outputs({"lcd": {"enabled": False}})
    assert load_sinks(cfg, SinkContext(), buffer_cfg(tmp_path)) == []


def test_lcd_invalid_cycle_is_an_error(fake_st7735):
    from pluto.sinks.lcd import LCDSink
    with pytest.raises(ConfigError, match="cycle must be >= 0"):
        LCDSink({"cycle": -1.0}, SinkContext())


def test_lcd_draws_latest_snapshot():
    now = [0.0]
    lcd = make_lcd(lambda: now[0])
    lcd.publish(snap(1.0))
    lcd.step()
    assert len(lcd._device.frames) == 1
    # Nothing new: no redraw.
    lcd.step()
    assert len(lcd._device.frames) == 1


def test_lcd_proximity_tap_switches_page():
    now = [0.0]
    lcd = make_lcd(lambda: now[0])
    lcd.publish(snap(1.0))
    lcd.step()
    assert lcd._page == 0

    now[0] = 1.0
    lcd.publish(prox_snap(2.0, proximity=2000))  # a wave over the sensor
    assert lcd._page == 1  # advanced immediately on publish
    lcd.step()
    assert len(lcd._device.frames) == 2


def test_lcd_auto_cycles_pages():
    now = [0.0]
    lcd = make_lcd(lambda: now[0])
    lcd.publish(snap(1.0))
    lcd.step()
    assert lcd._page == 0

    now[0] = 10.0  # cycle interval elapsed
    lcd.step()
    assert lcd._page == 1


def test_lcd_cycle_zero_disables_auto_cycling():
    now = [0.0]
    from pluto.sinks.lcd import LCDSink
    lcd = LCDSink({"cycle": 0.0}, SinkContext(), clock=lambda: now[0])
    lcd._device = FakeST7735Device()
    lcd.publish(snap(1.0))
    now[0] = 1000.0
    assert lcd.step() is None  # no next-cycle deadline
    assert lcd._page == 0


def test_lcd_close_blanks_backlight(tmp_path, fake_st7735):
    (lcd,) = load_sinks(outputs(), SinkContext(), buffer_cfg(tmp_path))
    device = lcd._device
    lcd.close()
    assert device.backlight == 0


def test_png_sink_renders_all_pages(tmp_path):
    from pluto.sinks.png import PNGSink
    sink = PNGSink({"dir": str(tmp_path)}, SinkContext(fields={"pm25", "noise"}))
    sink.publish(snap(1.0))
    files = sorted(p.name for p in tmp_path.glob("*.png"))
    assert files == [f"page-{i + 1}-{n.lower()}.png"
                     for i, (n, _) in enumerate(sink._renderer.pages)]
    # A second publish overwrites rather than accumulating.
    sink.publish(snap(2.0))
    assert len(list(tmp_path.glob("*.png"))) == len(sink._renderer.pages)


# ── App wiring: headless, LCD-only, LCD disabled ────────────────────

def app_with(drivers=None, sinks=()):
    from pluto.app import App
    from pluto.drivers import load_drivers
    if drivers is None:
        drivers = load_drivers(config_module.SensorsConfig(), mock=True)
    return App(drivers, sinks=sinks, refresh=0.01)


def test_app_runs_headless_with_zero_sinks():
    app = app_with(sinks=[])
    app.run_once()  # must not raise, nothing to publish to


def test_app_publishes_to_a_mocked_lcd_sink():
    now = [0.0]
    lcd = make_lcd(lambda: now[0])
    app = app_with(sinks=[lcd])
    app.run_once()
    lcd.step()
    assert lcd._readings is not None  # the snapshot reached the LCD
    assert len(lcd._device.frames) == 1


def test_app_with_lcd_disabled_by_config(tmp_path, fake_st7735):
    cfg = outputs({"lcd": {"enabled": False},
                   "sqlite": {"path": str(tmp_path / "d.db")}})
    sinks = load_sinks(cfg, SinkContext(), buffer_cfg(tmp_path))
    assert [s.name for s in sinks] == ["sqlite"]
    app = app_with(sinks=sinks)
    app.run_once()  # runs fine with no display


def test_one_failing_sink_does_not_block_others():
    good = RecordingSink()
    bad = RecordingSink()
    bad.fail = True
    app = app_with(sinks=[bad, good])
    app.tick()
    assert len(good.published) == 1  # the failing sink was isolated
