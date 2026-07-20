"""Sink loading: built-ins, config declarations, entry-point plugins.

Unlike sensor drivers, sinks are never auto-detected: a sink runs only
when a ``[outputs.<name>]`` table declares it (and does not disable
it). Third-party packages add sinks through the ``pluto.sinks``
entry-point group. Network sinks (``buffered = True``) are wrapped in
the persistent store-and-forward queue so an outage loses no data.
"""

import logging
from typing import Dict, List, Type

from ..config import BufferConfig, ConfigError, OutputsConfig
from ..plugins import build_registry, entry_points_in
from .base import Sink, SinkContext, SinkError, Snapshot  # noqa: F401 (re-exported)
from .buffer import BufferedSink, SnapshotQueue

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "pluto.sinks"


def builtin_sinks() -> Dict[str, Type[Sink]]:
    from .csv import CSVSink
    from .http import HTTPSink
    from .mqtt import MQTTSink
    from .prometheus import PrometheusSink
    from .sqlite import SQLiteSink

    return {cls.name: cls for cls in (
        MQTTSink, PrometheusSink, SQLiteSink, CSVSink, HTTPSink,
    )}


def registry() -> Dict[str, Type[Sink]]:
    """All known sink classes by name: built-ins plus entry points."""
    return build_registry(builtin_sinks(), _entry_points(), Sink, "sink")


def _entry_points():
    return entry_points_in(ENTRY_POINT_GROUP)


def load_sinks(outputs: OutputsConfig, context: SinkContext,
               buffer_cfg: BufferConfig, start_workers: bool = True) -> List[Sink]:
    """Instantiate every declared-and-enabled sink.

    Network sinks are wrapped in a BufferedSink sharing one on-disk
    SnapshotQueue (unless buffering is disabled in [buffer]).
    ``start_workers=False`` keeps the retry threads off, for tests.
    """
    reg = registry()
    unknown = sorted(set(outputs.sinks) - set(reg))
    if unknown:
        raise ConfigError(
            f"unknown output sink(s) in config: {', '.join(unknown)} "
            f"(known: {', '.join(sorted(reg))})")

    loaded: List[Sink] = []
    queue = None
    for name in sorted(outputs.sinks):
        scfg = outputs.sinks[name]
        if not scfg.enabled:
            log.info("Sink %s disabled in config", name)
            continue
        sink = reg[name](dict(scfg.settings), context)
        if sink.buffered and buffer_cfg.enabled:
            if queue is None:
                queue = SnapshotQueue(buffer_cfg.path, buffer_cfg.max_snapshots)
            sink = BufferedSink(sink, queue, start_worker=start_workers)
        loaded.append(sink)
    return loaded
