"""Main loop: read drivers, transform, publish to sinks.

The loop is now purely read-then-publish. Everything visual — page
rendering, cycling and proximity-wave page switching — lives in the
LCD sink (sinks/lcd.py), which subscribes to the same snapshots as
every other sink. With no display attached the loop spends no cycles
on rendering at all.
"""

import logging
import time
from typing import Optional

from .config import DeviceConfig
from .drivers import flatten, read_all
from .drivers.base import Readings
from .model import make_snapshot

log = logging.getLogger(__name__)


class App:
    def __init__(self, drivers, sinks=(), refresh: float = 1.0,
                 device: Optional[DeviceConfig] = None, pipeline=None):
        self.drivers = list(drivers)
        self.sinks = list(sinks)
        self.refresh = refresh
        self.device = device or DeviceConfig()
        self.pipeline = pipeline  # optional TransformPipeline

    def _read(self):
        readings = read_all(self.drivers)
        if self.pipeline is not None:
            readings = self.pipeline.apply(readings)
        return readings

    def tick(self) -> None:
        """Read once and publish the snapshot to every sink."""
        snap = make_snapshot(self._read(), self.device)
        self._log_readings(flatten(snap.readings))
        for sink in self.sinks:
            try:
                sink.publish(snap)
            except Exception:
                log.warning("%s sink failed", sink.name, exc_info=True)

    def run(self) -> None:
        log.info("Running with drivers: %s",
                 ", ".join(d.name for d in self.drivers) or "(none)")
        log.info("Publishing to sinks: %s",
                 ", ".join(s.name for s in self.sinks) or "(none)")
        try:
            while True:
                started = time.monotonic()
                self.tick()
                # Sleep out the rest of the interval; a slow read just
                # shortens the wait rather than drifting the cadence.
                time.sleep(max(0.0, self.refresh - (time.monotonic() - started)))
        except KeyboardInterrupt:
            log.info("Exiting")
        finally:
            self.close()

    def run_once(self) -> None:
        """One read/publish cycle, then shut down (smoke test)."""
        self.tick()
        self.close()

    def close(self) -> None:
        for sink in self.sinks:
            try:
                sink.close()
            except Exception:
                pass
        for driver in self.drivers:
            try:
                driver.close()
            except Exception:
                pass

    @staticmethod
    def _log_readings(r: Readings) -> None:
        log.debug(
            "temp=%s hum=%s press=%s lux=%s prox=%s ox=%s red=%s nh3=%s "
            "pm1=%s pm2.5=%s pm10=%s noise=%s",
            r.temperature, r.humidity, r.pressure, r.lux, r.proximity,
            r.oxidising, r.reducing, r.nh3, r.pm1, r.pm25, r.pm10, r.noise,
        )
