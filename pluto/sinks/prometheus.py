"""Prometheus sink: expose the latest snapshot on a /metrics endpoint."""

import logging
import math
from typing import Any, Dict, Optional

from .base import Sink, SinkContext, Snapshot
from ..drivers.base import Quality

log = logging.getLogger(__name__)


class PrometheusSink(Sink):
    """Exposes the latest snapshot on http://0.0.0.0:<port>/metrics."""

    name = "prometheus"
    settings_keys = ("port",)

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None):
        super().__init__(settings, context)
        from prometheus_client import Gauge, start_http_server

        port = self.int_setting("port", 9099, minimum=1, maximum=65535)
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

    def publish(self, snapshot: Snapshot) -> None:
        for field, gauge in self._gauges.items():
            gauge.set(self._value(snapshot, field))
        for size, field in (("1.0", "pm1"), ("2.5", "pm25"), ("10", "pm10")):
            self._pm.labels(size=size).set(self._value(snapshot, field))

    @staticmethod
    def _value(snapshot: Snapshot, field: str) -> float:
        r = snapshot.readings.get(field)
        if r is None or r.quality is not Quality.OK or r.value is None:
            return math.nan
        return r.value
