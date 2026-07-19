"""Main loop: read sensors, handle page switching, draw frames."""

import logging
import time

from .display import Renderer
from .sensors import TAP_THRESHOLD, Readings

log = logging.getLogger(__name__)

TICK = 0.1  # seconds between proximity/tap checks
TAP_DEBOUNCE = 0.5


class App:
    def __init__(self, sensors, display, renderer: Renderer, refresh: float = 1.0, cycle: float = 10.0,
                 publishers=()):
        """cycle=0 disables auto page cycling (tap the proximity sensor to switch)."""
        self.sensors = sensors
        self.display = display
        self.renderer = renderer
        self.refresh = refresh
        self.cycle = cycle
        self.publishers = list(publishers)

    def run(self) -> None:
        page = 0
        n_pages = len(self.renderer.pages)
        readings = self.sensors.read()
        last_refresh = time.monotonic()
        last_cycle = last_refresh
        last_tap = 0.0
        dirty = True

        log.info("Running with pages: %s", ", ".join(name for name, _ in self.renderer.pages))
        try:
            while True:
                now = time.monotonic()

                if now - last_refresh >= self.refresh:
                    readings = self.sensors.read()
                    last_refresh = now
                    dirty = True
                    self._log_readings(readings)
                    self._publish(readings)

                prox = self.sensors.proximity()
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
        readings = self.sensors.read()
        self._log_readings(readings)
        self._publish(readings)
        for i in range(len(self.renderer.pages)):
            self.display.show(self.renderer.render(i, readings))
        self._close()

    def _publish(self, readings: Readings) -> None:
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

    @staticmethod
    def _log_readings(r: Readings) -> None:
        log.debug(
            "temp=%s hum=%s press=%s lux=%s prox=%s ox=%s red=%s nh3=%s "
            "pm1=%s pm2.5=%s pm10=%s noise=%s",
            r.temperature, r.humidity, r.pressure, r.lux, r.proximity,
            r.oxidising, r.reducing, r.nh3, r.pm1, r.pm25, r.pm10, r.noise,
        )
