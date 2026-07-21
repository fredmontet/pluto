"""The sink contract, plus the Snapshot type that flows into sinks.

A Sink is one output for readings (broker, database, file, ...). Sinks
are enabled and configured purely via ``[outputs.<name>]`` tables; the
app loop hands every enabled sink the same Snapshot and one failing
sink never affects the others or the display.
"""

import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from ..config import DeviceConfig
from ..model import Snapshot, json_payload  # noqa: F401 (re-exported)
from ..plugins import Configurable


class SinkError(RuntimeError):
    """A publish that could not be delivered (and may be retried)."""


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
    so the snapshot stays queued. ``autoload = True`` lets a sink run
    without being declared in config, gated by its ``available()``
    probe (the LCD, which only runs when its hardware responds).
    """

    section = "outputs"
    buffered: bool = False
    autoload: bool = False
    # Set by the buffering wrapper; a network sink may call it when
    # connectivity returns so the backlog is flushed immediately.
    notify_ready: Optional[Callable[[], None]] = None

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None):
        super().__init__(settings)
        self.context = context or SinkContext()

    def available(self) -> bool:
        """Probe whatever the sink needs (hardware, ...); False keeps
        it out of the loaded set. Only meaningful work for autoload
        sinks — everything else defaults to True."""
        return True

    @abstractmethod
    def publish(self, snapshot: Snapshot) -> None:
        """Deliver one snapshot. Buffered sinks raise on failure."""

    def close(self) -> None:
        pass
