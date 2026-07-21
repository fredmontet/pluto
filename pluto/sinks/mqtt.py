"""MQTT sink: JSON snapshots to a broker, with HA discovery.

Publishes each snapshot as JSON to <base>/readings (qos 1), with a
retained online/offline status topic backed by a last-will. Connection
handling is left to paho's background thread; while disconnected,
``publish()`` raises so the buffering wrapper keeps the snapshot
queued, and the backlog replays (in order, original timestamps) once
the broker is back.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

from .base import Sink, SinkContext, SinkError, Snapshot, json_payload
from ..config import ConfigError
from ..model import METRICS

log = logging.getLogger(__name__)


class MQTTSink(Sink):
    name = "mqtt"
    buffered = True
    settings_keys = ("host", "port", "topic", "username", "password", "ha_discovery")

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None):
        super().__init__(settings, context)
        import paho.mqtt.client as mqtt

        host = self.str_setting("host")
        if not host:
            raise ConfigError(
                "outputs.mqtt.host is required (or pass --mqtt HOST)")
        port = self.int_setting("port", 1883, minimum=1, maximum=65535)
        username = self.str_setting("username")
        password = self.str_setting("password")
        self._ha_discovery = self.bool_setting("ha_discovery", False)

        self._node = self.context.node
        self._location = self.context.device.location or None
        # An empty field set means "unknown"; only a non-empty set
        # limits what Home Assistant discovery announces.
        self._fields = set(self.context.fields) or None
        self._base = self.str_setting("topic") or f"pluto/{self._node}"
        self._readings_topic = f"{self._base}/readings"
        self._status_topic = f"{self._base}/status"
        self._connected = False

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"pluto-{self._node}",
        )
        if username:
            client.username_pw_set(username, password or None)
        client.will_set(self._status_topic, "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.connect_async(host, port)
        client.loop_start()
        self._client = client
        log.info("Publishing MQTT to %s:%d, topic %s", host, port, self._readings_topic)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        log.info("Connected to MQTT broker")
        self._connected = True
        client.publish(self._status_topic, "online", retain=True)
        if self._ha_discovery:
            self._announce()
        if self.notify_ready is not None:
            self.notify_ready()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        log.info("MQTT broker connection lost; buffering readings")
        self._connected = False

    def _announce(self) -> None:
        """Publish retained Home Assistant discovery configs for every sensor."""
        node = re.sub(r"\W", "_", self._node)
        device = {
            "identifiers": [f"pluto_{node}"],
            "name": f"Enviro+ {self._node}",
            "manufacturer": "Pimoroni",
            "model": "Enviro+",
        }
        if self._location:
            device["suggested_area"] = self._location
        for field, metric in METRICS.items():
            if self._fields is not None and field not in self._fields:
                continue
            config = {
                "name": metric.label,
                "unique_id": f"pluto_{node}_{field}",
                "state_topic": self._readings_topic,
                "value_template": "{{ value_json.%s }}" % field,
                "availability_topic": self._status_topic,
                "device": device,
            }
            if metric.unit:
                config["unit_of_measurement"] = metric.unit
            if metric.device_class:
                config["device_class"] = metric.device_class
            self._client.publish(
                f"homeassistant/sensor/pluto_{node}/{field}/config",
                json.dumps(config),
                retain=True,
            )

    def publish(self, snapshot: Snapshot) -> None:
        if not self._connected:
            raise SinkError("not connected to the MQTT broker")
        info = self._client.publish(
            self._readings_topic, json.dumps(json_payload(snapshot)), qos=1)
        if info.rc != 0:
            raise SinkError(f"MQTT publish failed (rc={info.rc})")

    def close(self) -> None:
        try:
            self._client.publish(self._status_topic, "offline", retain=True)
            self._client.disconnect()
            self._client.loop_stop()
        except Exception:
            pass
