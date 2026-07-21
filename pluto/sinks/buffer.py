"""Store-and-forward buffering for network sinks.

SnapshotQueue is a small SQLite-backed FIFO shared by all buffered
sinks (one backlog per sink name, capped, oldest dropped first).
BufferedSink wraps a network sink: every snapshot is enqueued first,
then a background worker drains the backlog in order, backing off
exponentially while the sink keeps failing. Because the queue lives on
disk, a power cut or restart during an outage loses nothing.
"""

import logging
import sqlite3
import threading
import time
from typing import Iterable, List, Tuple

from .base import Sink, Snapshot

log = logging.getLogger(__name__)


class SnapshotQueue:
    """Persistent per-sink FIFO of serialized snapshots."""

    def __init__(self, path: str, max_snapshots: int = 10000):
        self._max = max_snapshots
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS queue ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " sink TEXT NOT NULL,"
                " payload TEXT NOT NULL)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS queue_sink ON queue (sink, id)")
            self._conn.commit()

    def push(self, sink: str, snapshot: Snapshot) -> int:
        """Append a snapshot; returns how many old ones were dropped
        to stay within the cap (0 normally)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO queue (sink, payload) VALUES (?, ?)",
                (sink, snapshot.to_json()))
            dropped = 0
            count = self._conn.execute(
                "SELECT COUNT(*) FROM queue WHERE sink = ?", (sink,)).fetchone()[0]
            if count > self._max:
                dropped = count - self._max
                self._conn.execute(
                    "DELETE FROM queue WHERE id IN ("
                    " SELECT id FROM queue WHERE sink = ? ORDER BY id LIMIT ?)",
                    (sink, dropped))
            self._conn.commit()
            return dropped

    def oldest(self, sink: str, limit: int) -> List[Tuple[int, Snapshot]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, payload FROM queue WHERE sink = ? ORDER BY id LIMIT ?",
                (sink, limit)).fetchall()
        return [(rowid, Snapshot.from_json(payload)) for rowid, payload in rows]

    def remove(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "DELETE FROM queue WHERE id = ?", [(i,) for i in ids])
            self._conn.commit()

    def pending(self, sink: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM queue WHERE sink = ?", (sink,)).fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class BufferedSink(Sink):
    """Wraps a network sink with the persistent queue and a retry loop.

    ``publish()`` only enqueues (never raises, never blocks the read
    loop on the network); the worker thread drains the backlog in
    order and retries with exponential backoff while the inner sink
    keeps failing.
    """

    INITIAL_BACKOFF = 2.0
    MAX_BACKOFF = 300.0
    BATCH = 50

    def __init__(self, inner: Sink, queue: SnapshotQueue,
                 start_worker: bool = True, clock=time.monotonic):
        # Deliberately no super().__init__: this wrapper has no settings
        # of its own, it impersonates the sink it wraps.
        self.inner = inner
        self.name = inner.name
        self._queue = queue
        self._clock = clock
        self._backoff = 0.0
        self._next_attempt = 0.0
        self._failing = False
        self._flush_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        inner.notify_ready = self._ready
        self._thread = None
        if start_worker:
            self._thread = threading.Thread(
                target=self._run, name=f"pluto-{self.name}-flush", daemon=True)
            self._thread.start()

    def publish(self, snapshot: Snapshot) -> None:
        dropped = self._queue.push(self.name, snapshot)
        if dropped:
            log.warning("%s buffer full; dropped %d oldest snapshot(s)",
                        self.name, dropped)
        self._wake.set()

    def flush(self) -> bool:
        """Try to drain the backlog; True when it is empty afterwards.

        A no-op returning False while a retry backoff is pending.
        """
        with self._flush_lock:
            if self._clock() < self._next_attempt:
                return False
            sent = 0
            while True:
                batch = self._queue.oldest(self.name, self.BATCH)
                if not batch:
                    break
                done = []
                error = None
                for rowid, snap in batch:
                    try:
                        self.inner.publish(snap)
                    except Exception as e:
                        error = e
                        break
                    done.append(rowid)
                sent += len(done)
                self._queue.remove(done)
                if error is not None:
                    self._backoff = min(self._backoff * 2 or self.INITIAL_BACKOFF,
                                        self.MAX_BACKOFF)
                    self._next_attempt = self._clock() + self._backoff
                    if not self._failing:
                        self._failing = True
                        log.warning("%s publish failed, buffering snapshots "
                                    "(next retry in %.0fs): %s",
                                    self.name, self._backoff, error)
                    return False
            if self._failing:
                log.info("%s recovered; flushed %d buffered snapshot(s)",
                         self.name, sent)
            self._failing = False
            self._backoff = 0.0
            self._next_attempt = 0.0
            return True

    def _ready(self) -> None:
        """Called by the inner sink on reconnection: retry right away."""
        self._next_attempt = 0.0
        self._wake.set()

    def _run(self) -> None:
        while True:
            if self._queue.pending(self.name) == 0:
                self._wake.wait()
            else:
                delay = self._next_attempt - self._clock()
                if delay > 0:
                    self._wake.wait(timeout=delay)
            self._wake.clear()
            if self._stop.is_set():
                return
            self.flush()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.flush()  # last chance; skipped automatically if backing off
        self.inner.close()
