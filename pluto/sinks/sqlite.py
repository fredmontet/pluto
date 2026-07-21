"""SQLite sink: append every snapshot to a local database.

Each row stores the full snapshot metadata (UTC timestamp, device,
location, description, version, time_uncertain) plus a JSON document
of every reading — including missing/error ones with their quality
flag and driver name, so the database doubles as a diagnostics log
even though network payloads omit non-ok readings. The table is pruned
oldest-first whenever it grows past ``max_rows``, keeping the file
bounded on a small SD card.
"""

import json
import logging
import sqlite3
from typing import Any, Dict, Optional

from .base import Sink, SinkContext, Snapshot
from ..config import ConfigError

log = logging.getLogger(__name__)

_COLUMNS = (
    ("timestamp", "TEXT NOT NULL DEFAULT ''"),
    ("device", "TEXT NOT NULL DEFAULT ''"),
    ("location", "TEXT NOT NULL DEFAULT ''"),
    ("description", "TEXT NOT NULL DEFAULT ''"),
    ("version", "TEXT NOT NULL DEFAULT ''"),
    ("time_uncertain", "INTEGER NOT NULL DEFAULT 0"),
    ("data", "TEXT NOT NULL DEFAULT '{}'"),
)


class SQLiteSink(Sink):
    name = "sqlite"
    settings_keys = ("path", "max_rows")

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None):
        super().__init__(settings, context)
        path = self.str_setting("path", "pluto-readings.db")
        if not path:
            raise ConfigError("outputs.sqlite.path must not be empty")
        self._max_rows = self.int_setting("max_rows", 100_000, minimum=0)  # 0 = keep all
        self._conn = sqlite3.connect(path)
        columns = ", ".join(f"{name} {decl}" for name, decl in _COLUMNS)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS readings ("
            f" id INTEGER PRIMARY KEY AUTOINCREMENT, {columns})")
        # A database from an older pluto may miss newer columns; SQLite
        # can add them in place.
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(readings)")}
        for name, decl in _COLUMNS:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE readings ADD COLUMN {name} {decl}")
        self._conn.commit()
        self._count = self._conn.execute(
            "SELECT COUNT(*) FROM readings").fetchone()[0]
        log.info("Appending readings to SQLite database %s", path)

    def publish(self, snapshot: Snapshot) -> None:
        data = {f: {"value": r.value, "unit": r.unit,
                    "quality": r.quality.value, "driver": r.driver}
                for f, r in snapshot.readings.items()}
        self._conn.execute(
            "INSERT INTO readings"
            " (timestamp, device, location, description, version, time_uncertain, data)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (snapshot.iso_timestamp, snapshot.device_id, snapshot.location,
             snapshot.description, snapshot.version,
             int(snapshot.time_uncertain), json.dumps(data)))
        self._count += 1
        if self._max_rows and self._count > self._max_rows:
            excess = self._count - self._max_rows
            self._conn.execute(
                "DELETE FROM readings WHERE id IN ("
                " SELECT id FROM readings ORDER BY id LIMIT ?)", (excess,))
            self._count = self._max_rows
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
