"""CSV sink: one daily-rotated file of snapshots.

Writes <dir>/pluto-YYYY-MM-DD.csv (local time), starting a fresh file
with a header row at midnight and appending to an existing file after
a restart. Columns are the standard fields plus any extra fields the
loaded drivers provide, so plugin readings are captured too.
"""

import csv
import logging
import os
import time
from dataclasses import fields as dataclass_fields
from typing import Any, Dict, Optional

from .base import Sink, SinkContext, Snapshot
from ..config import ConfigError
from ..drivers.base import Quality, Readings

log = logging.getLogger(__name__)


class CSVSink(Sink):
    name = "csv"
    settings_keys = ("dir",)

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None):
        super().__init__(settings, context)
        self._dir = self.str_setting("dir", "csv")
        if not self._dir:
            raise ConfigError("outputs.csv.dir must not be empty")
        os.makedirs(self._dir, exist_ok=True)
        standard = [f.name for f in dataclass_fields(Readings)]
        extras = sorted(set(self.context.fields) - set(standard))
        self._columns = standard + extras
        self._date: Optional[str] = None
        self._file = None
        self._writer = None
        log.info("Writing daily CSV files into %s/", self._dir)

    def publish(self, snapshot: Snapshot) -> None:
        local = time.localtime(snapshot.timestamp)
        day = time.strftime("%Y-%m-%d", local)
        if day != self._date:
            self._rotate(day)
        row = [time.strftime("%Y-%m-%dT%H:%M:%S", local)]
        for column in self._columns:
            r = snapshot.readings.get(column)
            ok = r is not None and r.quality is Quality.OK and r.value is not None
            row.append(round(r.value, 3) if ok else "")
        self._writer.writerow(row)
        self._file.flush()

    def _rotate(self, day: str) -> None:
        if self._file is not None:
            self._file.close()
        path = os.path.join(self._dir, f"pluto-{day}.csv")
        needs_header = not os.path.exists(path) or os.path.getsize(path) == 0
        self._file = open(path, "a", newline="")
        self._writer = csv.writer(self._file)
        if needs_header:
            self._writer.writerow(["time"] + self._columns)
        self._date = day

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
