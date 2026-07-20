"""Main loop: read drivers, handle page switching, draw frames."""

import logging
import time
from typing import Dict, Optional

from .display import Renderer
from .drivers import Reading, read_all, snapshot
from .drivers.base import Readings

log = logging.getLogger(__name__)

TICK = 0.1  # seconds between proximity/tap checks
TAP_DEBOUNCE = 0.5
# Proximity value above which we consider the sensor "tapped".
TAP_THRESHOLD = 1500


class App:
    def __init__(self, drivers, display, renderer: Renderer, refresh: float = 1.0,
                 cycle: float = 10.0, publishers=()):
        """cycle=0 disables auto page cycling (tap the proximity sensor to switch)."""
        self.drivers = list(drivers)
        self.display = display
        self.renderer = renderer
        self.refresh = refresh
        self.cycle = cycle
        self.publishers = list(publishers)

    def run(self) -> None:
        page = 0
        n_pages = len(self.renderer.pages)
        readings = snapshot(read_all(self.drivers))
        last_refresh = time.monotonic()
        last_cycle = last_refresh
        last_tap = 0.0
        dirty = True

        log.info("Running with drivers: %s",
                 ", ".join(d.name for d in self.drivers) or "(none)")
        log.info("Running with pages: %s", ", ".join(name for name, _ in self.renderer.pages))
        try:
            while True:
                now = time.monotonic()

                if now - last_refresh >= self.refresh:
                    merged = read_all(self.drivers)
                    readings = snapshot(merged)
                    last_refresh = now
                    dirty = True
                    self._log_readings(readings)
                    self._publish(merged)

                prox = self._proximity()
                if prox is not None and prox > TAP_THRESHOLD and now - last_tap > TAP_DEBOUNCE:
                    page = (page + 1) % n_pages
                    last_tap = now
                    last_cycle = now
                    dirty = True

                if self.cycle > 0 and now - last_cycle >= self.cycle:
                    page = (page + 1) % n_pages
                    last_cycle = now
                    dirty = True

                if dirty:
                    self.display.show(self.renderer.render(page, readings))
                    dirty = False

                time.sleep(TICK)
        except KeyboardInterrupt:
            log.info("Exiting")
        finally:
            self._close()

    def render_all_pages(self) -> None:
        """Render every page once with a single snapshot, then return."""
        merged = read_all(self.drivers)
        readings = snapshot(merged)
        self._log_readings(readings)
        self._publish(merged)
        for i in range(len(self.renderer.pages)):
            self.display.show(self.renderer.render(i, readings))
        self._close()

    def _proximity(self) -> Optional[float]:
        for driver in self.drivers:
            prox = driver.proximity()
            if prox is not None:
                return prox
        return None

    def _publish(self, readings: Dict[str, Reading]) -> None:
        for publisher in self.publishers:
            try:
                publisher.publish(readings)
            except Exception:
                log.warning("%s failed", type(publisher).__name__, exc_info=True)

    def _close(self) -> None:
        self.display.close()
        for publisher in self.publishers:
            try:
                publisher.close()
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
