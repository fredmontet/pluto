"""The sink contract, plus the Snapshot type that flows into sinks.

A Sink is one output for readings (broker, database, file, ...). Sinks
are enabled and configured purely via ``[outputs.<name>]`` tables; the
app loop hands every enabled sink the same Snapshot and one failing
sink never affects the others or the display.
"""

import json
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from ..config import DeviceConfig
from ..drivers.base import Quality, Reading
from ..plugins import Configurable


class SinkError(RuntimeError):
    """A publish that could not be delivered (and may be retried)."""


@dataclass
class Snapshot:
    """One timestamped set of merged driver readings."""

    timestamp: float  # unix epoch seconds, taken at read time
    readings: Dict[str, Reading]

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp,
            "readings": {f: [r.value, r.unit, r.quality.value]
                         for f, r in self.readings.items()},
        })

    @classmethod
    def from_json(cls, text: str) -> "Snapshot":
        doc = json.loads(text)
        return cls(doc["timestamp"],
                   {f: Reading(value, unit, Quality(quality))
                    for f, (value, unit, quality) in doc["readings"].items()})


def json_payload(snapshot: Snapshot) -> Dict[str, float]:
    """The flat JSON document network sinks send: ok values + timestamp.

    The timestamp is the snapshot's read time, so buffered snapshots
    replayed after an outage keep their original time.
    """
    payload = {
        f: round(r.value, 3)
        for f, r in snapshot.readings.items()
        if r.quality is Quality.OK and isinstance(r.value, (int, float))
    }
    payload["timestamp"] = round(snapshot.timestamp, 3)
    return payload


@dataclass
class SinkContext:
    """What sinks may need to know beyond their own settings."""

    device: DeviceConfig = field(default_factory=DeviceConfig)
    fields: Set[str] = field(default_factory=set)  # fields the loaded drivers provide

    @property
    def node(self) -> str:
        return self.device.id or socket.gethostname()


class Sink(Configurable, ABC):
    """One output for snapshots.

    Subclasses set ``name`` (also the ``[outputs.<name>]`` config key)
    and ``settings_keys``. ``buffered = True`` marks a network sink:
    the loader wraps it in the persistent store-and-forward queue, and
    its ``publish()`` must raise (e.g. SinkError) when delivery fails
    so the snapshot stays queued.
    """

    section = "outputs"
    buffered: bool = False
    # Set by the buffering wrapper; a network sink may call it when
    # connectivity returns so the backlog is flushed immediately.
    notify_ready: Optional[Callable[[], None]] = None

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None):
        super().__init__(settings)
        self.context = context or SinkContext()

    @abstractmethod
    def publish(self, snapshot: Snapshot) -> None:
        """Deliver one snapshot. Buffered sinks raise on failure."""

    def close(self) -> None:
        pass
