"""Prometheus sink: expose the latest snapshot on a /metrics endpoint.

Gauge names come from the metric catalogue (pluto/model.py); every
series carries ``device`` and ``location`` labels from the snapshot
metadata, and the particulate sizes share one gauge with a ``size``
label.
"""

import logging
import math
from typing import Any, Dict, Optional

from .base import Sink, SinkContext, Snapshot
from ..drivers.base import Quality
from ..model import METRICS, PM_SIZES

log = logging.getLogger(__name__)


class PrometheusSink(Sink):
    """Exposes the latest snapshot on http://0.0.0.0:<port>/metrics."""

    name = "prometheus"
    settings_keys = ("port",)

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None,
                 registry=None, start_server: bool = True):
        """registry/start_server exist for tests: pass a private
        CollectorRegistry and skip binding the HTTP port."""
        super().__init__(settings, context)
        import prometheus_client
        from prometheus_client import Gauge

        port = self.int_setting("port", 9099, minimum=1, maximum=65535)
        if registry is None:
            registry = prometheus_client.REGISTRY
        labels = ["device", "location"]
        self._gauges = {
            name: Gauge(metric.prometheus, metric.description, labels,
                        registry=registry)
            for name, metric in METRICS.items() if name not in PM_SIZES
        }
        self._pm = Gauge("pluto_particulates_ug_per_m3", "Particulate matter",
                         labels + ["size"], registry=registry)
        if start_server:
            prometheus_client.start_http_server(port, registry=registry)
            log.info("Prometheus metrics on port %d at /metrics", port)

    def publish(self, snapshot: Snapshot) -> None:
        labels = {"device": snapshot.device_id, "location": snapshot.location}
        for field, gauge in self._gauges.items():
            gauge.labels(**labels).set(self._value(snapshot, field))
        for field, size in PM_SIZES.items():
            self._pm.labels(size=size, **labels).set(self._value(snapshot, field))

    @staticmethod
    def _value(snapshot: Snapshot, field: str) -> float:
        r = snapshot.readings.get(field)
        if r is None or r.quality is not Quality.OK or r.value is None:
            return math.nan
        return r.value
