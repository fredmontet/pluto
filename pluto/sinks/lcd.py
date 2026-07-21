"""LCD sink: the Enviro+ onboard 0.96" ST7735 display.

The display is just another output. It auto-loads only when the panel
actually responds (``[outputs.lcd] enabled = false`` forces it off),
so headless deployments spend zero CPU on rendering. Page rendering
and cycling behave exactly as before: pages advance every ``cycle``
seconds and a wave over the proximity sensor switches manually — the
tap now comes from the ``proximity`` reading in the published
snapshots rather than from polling the LTR559 driver directly.

Rendering happens on the sink's own thread, so a slow SPI write can
never stall the read loop.
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

from .base import Sink, SinkContext, Snapshot
from ..config import ConfigError
from ..drivers import flatten
from ..display import renderer_for_fields

log = logging.getLogger(__name__)

# Proximity value above which we consider the sensor "tapped".
TAP_THRESHOLD = 1500
TAP_DEBOUNCE = 0.5  # seconds; also bounded below by the refresh rate


class LCDSink(Sink):
    name = "lcd"
    autoload = True
    settings_keys = ("cycle",)

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None,
                 clock=time.monotonic):
        super().__init__(settings, context)
        self._cycle = self.float_setting("cycle", 10.0)
        if self._cycle < 0:
            raise ConfigError("outputs.lcd.cycle must be >= 0 (0 disables cycling)")
        self._clock = clock
        self._renderer = renderer_for_fields(self.context.fields)
        self._device = None
        self._page = 0
        self._readings = None
        self._dirty = False
        self._last_tap = float("-inf")
        self._last_cycle = clock()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def available(self) -> bool:
        try:
            import st7735

            device = st7735.ST7735(
                port=0,
                cs=1,
                dc="GPIO9",
                backlight="GPIO12",
                rotation=270,
                spi_speed_hz=10_000_000,
            )
            device.begin()
        except Exception:
            log.info("No ST7735 LCD detected; running without the display")
            log.debug("LCD probe failed", exc_info=True)
            return False
        self._device = device
        log.info("LCD pages: %s", ", ".join(name for name, _ in self._renderer.pages))
        self.start_worker()
        return True

    def start_worker(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="pluto-lcd", daemon=True)
            self._thread.start()

    def publish(self, snapshot: Snapshot) -> None:
        """Store the latest snapshot and detect proximity taps; the
        worker thread does the actual drawing."""
        now = self._clock()
        prox = snapshot.readings.get("proximity")
        tapped = (prox is not None and prox.value is not None
                  and prox.value > TAP_THRESHOLD
                  and now - self._last_tap > TAP_DEBOUNCE)
        with self._lock:
            self._readings = flatten(snapshot.readings)
            self._dirty = True
            if tapped:
                self._advance(now)
                self._last_tap = now
        self._wake.set()

    def _advance(self, now: float) -> None:
        self._page = (self._page + 1) % len(self._renderer.pages)
        self._last_cycle = now
        self._dirty = True

    def step(self) -> Optional[float]:
        """One scheduling step: auto-cycle when due, draw when dirty.

        Returns the seconds until the next auto page change (None when
        cycling is disabled). Public for tests; normally only the
        worker thread calls it.
        """
        now = self._clock()
        with self._lock:
            if (self._cycle > 0 and self._readings is not None
                    and now - self._last_cycle >= self._cycle):
                self._advance(now)
            frame = None
            if self._dirty and self._readings is not None:
                frame = self._renderer.render(self._page, self._readings)
                self._dirty = False
        if frame is not None and self._device is not None:
            self._device.display(frame)
        if self._cycle > 0:
            return max(0.05, self._cycle - (self._clock() - self._last_cycle))
        return None

    def _run(self) -> None:
        while not self._stopping.is_set():
            timeout = self.step()
            self._wake.wait(timeout=timeout)
            self._wake.clear()

    def close(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._device is not None:
            try:
                self._device.set_backlight(0)
            except Exception:
                pass
