"""Optional outbound publishers for sensor readings.

Both consume the same Readings snapshot the display uses:

- MQTTPublisher pushes a JSON document per refresh, optionally
  announcing its sensors to Home Assistant via MQTT discovery.
- PrometheusExporter exposes the latest snapshot on a /metrics
  endpoint for a Prometheus server to scrape.
"""

import dataclasses
import json
import logging
import math
import re
import socket
import time
from typing import Optional

from .sensors import Readings

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

_PM_FIELDS = ("pm1", "pm25", "pm10")


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
        has_particulates: bool = True,
        has_noise: bool = True,
    ):
        import paho.mqtt.client as mqtt

        hostname = socket.gethostname()
        self._base = base_topic or f"pluto/{hostname}"
        self._readings_topic = f"{self._base}/readings"
        self._status_topic = f"{self._base}/status"
        self._ha_discovery = ha_discovery
        self._skip_fields = set()
        if not has_particulates:
            self._skip_fields.update(_PM_FIELDS)
        if not has_noise:
            self._skip_fields.add("noise")

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"pluto-{hostname}",
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
        node = re.sub(r"\W", "_", socket.gethostname())
        device = {
            "identifiers": [f"pluto_{node}"],
            "name": f"Enviro+ {socket.gethostname()}",
            "manufacturer": "Pimoroni",
            "model": "Enviro+",
        }
        for field, (name, unit, device_class) in _HA_SENSORS.items():
            if field in self._skip_fields:
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

    def publish(self, readings: Readings) -> None:
        # Missing sensors are omitted rather than sent as null, so Home
        # Assistant shows them as unknown instead of the string "None".
        payload = {
            k: round(v, 3)
            for k, v in dataclasses.asdict(readings).items()
            if v is not None
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

    def publish(self, readings: Readings) -> None:
        values = dataclasses.asdict(readings)
        for field, gauge in self._gauges.items():
            v = values.get(field)
            gauge.set(v if v is not None else math.nan)
        for size, v in (("1.0", readings.pm1), ("2.5", readings.pm25), ("10", readings.pm10)):
            self._pm.labels(size=size).set(v if v is not None else math.nan)

    def close(self) -> None:
        pass
