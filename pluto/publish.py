"""Optional outbound publishers for sensor readings.

Both consume the merged per-driver readings the display is built from:

- MQTTPublisher pushes a JSON document per refresh (every field any
  loaded driver provides, so plugin drivers publish too), optionally
  announcing its sensors to Home Assistant via MQTT discovery.
- PrometheusExporter exposes the latest snapshot on a /metrics
  endpoint for a Prometheus server to scrape.
"""

import json
import logging
import math
import re
import socket
import time
from typing import Dict, Iterable, Optional

from .drivers.base import Quality, Reading

log = logging.getLogger(__name__)

# field -> (friendly name, unit, Home Assistant device_class)
_HA_SENSORS = {
    "temperature": ("Temperature", "°C", "temperature"),
    "humidity": ("Humidity", "%", "humidity"),
    "pressure": ("Pressure", "hPa", "atmospheric_pressure"),
    "lux": ("Light", "lx", "illuminance"),
    "proximity": ("Proximity", None, None),
    "oxidising": ("Gas oxidising", "kΩ", None),
    "reducing": ("Gas reducing", "kΩ", None),
    "nh3": ("Gas NH3", "kΩ", None),
    "pm1": ("PM1.0", "µg/m³", "pm1"),
    "pm25": ("PM2.5", "µg/m³", "pm25"),
    "pm10": ("PM10", "µg/m³", "pm10"),
    "noise": ("Noise amplitude", None, None),
}


class MQTTPublisher:
    """Publishes each snapshot as JSON to <base>/readings (qos 1).

    Connection handling is left to paho's background thread, so a broker
    that is down at startup (or drops out later) is retried automatically;
    a retained status topic plus a last-will mark the device on/offline.
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        base_topic: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        ha_discovery: bool = False,
        fields: Optional[Iterable[str]] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
    ):
        """fields: field names the loaded drivers provide; limits which
        sensors Home Assistant discovery announces (None = all known)."""
        import paho.mqtt.client as mqtt

        self._node = device_id or socket.gethostname()
        self._location = location
        self._base = base_topic or f"pluto/{self._node}"
        self._readings_topic = f"{self._base}/readings"
        self._status_topic = f"{self._base}/status"
        self._ha_discovery = ha_discovery
        self._fields = set(fields) if fields is not None else None

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"pluto-{self._node}",
        )
        if username:
            client.username_pw_set(username, password)
        client.will_set(self._status_topic, "offline", retain=True)
        client.on_connect = self._on_connect
        client.connect_async(host, port)
        client.loop_start()
        self._client = client
        log.info("Publishing MQTT to %s:%d, topic %s", host, port, self._readings_topic)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        log.info("Connected to MQTT broker")
        client.publish(self._status_topic, "online", retain=True)
        if self._ha_discovery:
            self._announce()

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
        for field, (name, unit, device_class) in _HA_SENSORS.items():
            if self._fields is not None and field not in self._fields:
                continue
            config = {
                "name": name,
                "unique_id": f"pluto_{node}_{field}",
                "state_topic": self._readings_topic,
                "value_template": "{{ value_json.%s }}" % field,
                "availability_topic": self._status_topic,
                "device": device,
            }
            if unit:
                config["unit_of_measurement"] = unit
            if device_class:
                config["device_class"] = device_class
            self._client.publish(
                f"homeassistant/sensor/pluto_{node}/{field}/config",
                json.dumps(config),
                retain=True,
            )

    def publish(self, readings: Dict[str, Reading]) -> None:
        # Anything not "ok" is omitted rather than sent as null, so Home
        # Assistant shows it as unknown instead of the string "None".
        payload = {
            field: round(r.value, 3)
            for field, r in readings.items()
            if r.quality is Quality.OK and isinstance(r.value, (int, float))
        }
        payload["timestamp"] = round(time.time(), 3)
        self._client.publish(self._readings_topic, json.dumps(payload), qos=1)

    def close(self) -> None:
        try:
            self._client.publish(self._status_topic, "offline", retain=True)
            self._client.disconnect()
            self._client.loop_stop()
        except Exception:
            pass


class PrometheusExporter:
    """Exposes the latest snapshot on http://0.0.0.0:<port>/metrics."""

    def __init__(self, port: int):
        from prometheus_client import Gauge, start_http_server

        self._gauges = {
            "temperature": Gauge("pluto_temperature_celsius", "CPU-heat compensated temperature"),
            "raw_temperature": Gauge("pluto_raw_temperature_celsius", "Uncompensated BME280 temperature"),
            "humidity": Gauge("pluto_humidity_percent", "Relative humidity"),
            "pressure": Gauge("pluto_pressure_hpa", "Barometric pressure"),
            "lux": Gauge("pluto_light_lux", "Illuminance"),
            "proximity": Gauge("pluto_proximity", "LTR559 proximity counts"),
            "oxidising": Gauge("pluto_gas_oxidising_kohms", "MICS6814 oxidising gas resistance"),
            "reducing": Gauge("pluto_gas_reducing_kohms", "MICS6814 reducing gas resistance"),
            "nh3": Gauge("pluto_gas_nh3_kohms", "MICS6814 NH3 gas resistance"),
            "noise": Gauge("pluto_noise_amplitude", "Relative noise amplitude"),
        }
        self._pm = Gauge("pluto_particulates_ug_per_m3", "Particulate matter", ["size"])
        start_http_server(port)
        log.info("Prometheus metrics on port %d at /metrics", port)

    def publish(self, readings: Dict[str, Reading]) -> None:
        for field, gauge in self._gauges.items():
            gauge.set(self._value(readings, field))
        for size, field in (("1.0", "pm1"), ("2.5", "pm25"), ("10", "pm10")):
            self._pm.labels(size=size).set(self._value(readings, field))

    @staticmethod
    def _value(readings: Dict[str, Reading], field: str) -> float:
        r = readings.get(field)
        if r is None or r.quality is not Quality.OK or r.value is None:
            return math.nan
        return r.value

    def close(self) -> None:
        pass
