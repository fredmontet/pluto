"""Tests for the sink system: loading, buffering, queue cap, new sinks."""

import json

import pytest

from pluto import config as config_module
from pluto.config import BufferConfig, ConfigError
from pluto.drivers.base import Reading
from pluto.sinks import SinkContext, load_sinks, registry
from pluto.sinks.base import Sink, Snapshot
from pluto.sinks.buffer import BufferedSink, SnapshotQueue
from pluto.sinks.mqtt import MQTTSink


def outputs(data=None):
    """An OutputsConfig from a would-be [outputs] TOML table."""
    return config_module.parse_config({"outputs": data or {}}).outputs


def snap(ts, temperature=20.0):
    return Snapshot(ts, {"temperature": Reading.ok(temperature, "°C")})


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
    assert [p["timestamp"] for p in payloads] == [1.0, 2.0]
    assert queue.pending("mqtt") == 0

    # Subsequent publishes flow straight through.
    buffered.publish(snap(3.0))
    assert buffered.flush() is True
    assert [p["timestamp"] for p in readings_payloads(client)] == [1.0, 2.0, 3.0]


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
    assert kept == [3.0, 4.0, 5.0]


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
    assert [s.timestamp for s in restored] == [1.0, 2.0]
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
        "SELECT timestamp, data FROM readings ORDER BY id").fetchall()
    assert [ts for ts, _ in rows] == [3.0, 4.0, 5.0]
    assert json.loads(rows[-1][1]) == {"temperature": 25.0}


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
    assert rows[0][0] == "time" and "temperature" in rows[0]
    col = rows[0].index("temperature")
    assert [r[col] for r in rows[1:]] == ["21.0", "22.0"]
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
    sink = HTTPSink({"url": "https://example.com/ingest", "token": "tok", "timeout": 3.0},
                    SinkContext(device=config_module.DeviceConfig(id="balcony-pi")))
    sink.publish(snap(5.0))

    request = seen["request"]
    assert request.full_url == "https://example.com/ingest"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer tok"
    assert seen["timeout"] == 3.0
    body = json.loads(request.data.decode())
    assert body == {"temperature": 20.0, "timestamp": 5.0, "device": "balcony-pi"}


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
