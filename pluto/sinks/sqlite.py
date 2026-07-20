"""SQLite sink: append every snapshot to a local database.

Each row is (timestamp, JSON of the ok values), so arbitrary plugin
driver fields fit without schema changes. The table is pruned oldest-
first whenever it grows past ``max_rows``, keeping the file bounded on
a small SD card.
"""

import json
import logging
import sqlite3
from typing import Any, Dict, Optional

from .base import Sink, SinkContext, Snapshot
from ..config import ConfigError
from ..drivers.base import Quality

log = logging.getLogger(__name__)


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
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS readings ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " timestamp REAL NOT NULL,"
            " data TEXT NOT NULL)")
        self._conn.commit()
        self._count = self._conn.execute(
            "SELECT COUNT(*) FROM readings").fetchone()[0]
        log.info("Appending readings to SQLite database %s", path)

    def publish(self, snapshot: Snapshot) -> None:
        data = {f: r.value for f, r in snapshot.readings.items()
                if r.quality is Quality.OK}
        self._conn.execute(
            "INSERT INTO readings (timestamp, data) VALUES (?, ?)",
            (snapshot.timestamp, json.dumps(data)))
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
