"""HTTP sink: POST each snapshot as JSON to a configurable URL.

The payload is the same flat document MQTT publishes — metric values
plus the snapshot metadata (device, location, version, UTC timestamp).
Delivery failures raise, so the buffering wrapper queues the snapshot
and replays it once the endpoint is reachable again.
"""

import json
import logging
import urllib.request
from typing import Any, Dict, Optional

from .base import Sink, SinkContext, Snapshot, json_payload
from ..config import ConfigError

log = logging.getLogger(__name__)


class HTTPSink(Sink):
    name = "http"
    buffered = True
    settings_keys = ("url", "token", "timeout")

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None):
        super().__init__(settings, context)
        self._url = self.str_setting("url")
        if not self._url:
            raise ConfigError("outputs.http.url is required")
        if not self._url.startswith(("http://", "https://")):
            raise ConfigError("outputs.http.url must start with http:// or https://")
        self._token = self.str_setting("token")
        self._timeout = self.float_setting("timeout", 10.0, positive=True)
        log.info("POSTing readings to %s", self._url)

    def publish(self, snapshot: Snapshot) -> None:
        payload = json_payload(snapshot)
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            self._url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        # urlopen raises on connection failure and on HTTP >= 400, which
        # is exactly what the buffering wrapper needs to keep the snapshot.
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            response.read()
