"""Mock driver: plausible drifting values for development off the Pi.

Mimics every hardware driver in one place. It is never auto-detected
(it would "work" everywhere); it loads with ``--mock`` or when a
``[sensors.mock]`` table declares it explicitly.
"""

import math
import time
from typing import Any, Dict, Optional

from .base import Driver, Reading


class MockDriver(Driver):
    name = "mock"
    settings_keys = ("pms", "noise")
    autoload = False

    _BASE_FIELDS = ("temperature", "raw_temperature", "humidity", "pressure",
                    "lux", "proximity", "oxidising", "reducing", "nh3")

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        super().__init__(settings)
        self._pms = self.bool_setting("pms", True)
        self._noise = self.bool_setting("noise", True)
        provides = list(self._BASE_FIELDS)
        if self._pms:
            provides += ["pm1", "pm25", "pm10"]
        if self._noise:
            provides.append("noise")
        self.provides = tuple(provides)
        self._t0 = time.monotonic()

    def available(self) -> bool:
        return True

    def proximity(self) -> float:
        return 0.0

    def _wave(self, period: float, lo: float, hi: float, phase: float = 0.0) -> float:
        t = time.monotonic() - self._t0
        mid, span = (lo + hi) / 2.0, (hi - lo) / 2.0
        return mid + span * math.sin(2 * math.pi * (t / period) + phase)

    def read(self) -> Dict[str, Reading]:
        raw = self._wave(120, 24.0, 28.0)
        readings = {
            "raw_temperature": Reading.ok(raw, "°C"),
            "temperature": Reading.ok(raw - 2.0, "°C"),
            "humidity": Reading.ok(self._wave(90, 38.0, 55.0, 1.0), "%"),
            "pressure": Reading.ok(self._wave(300, 1008.0, 1016.0, 2.0), "hPa"),
            "lux": Reading.ok(max(0.0, self._wave(60, -20.0, 400.0, 3.0)), "lx"),
            "proximity": Reading.ok(0.0),
            "oxidising": Reading.ok(self._wave(80, 8.0, 25.0, 0.5), "kΩ"),
            "reducing": Reading.ok(self._wave(70, 200.0, 450.0, 1.5), "kΩ"),
            "nh3": Reading.ok(self._wave(75, 60.0, 120.0, 2.5), "kΩ"),
        }
        if self._pms:
            readings["pm1"] = Reading.ok(max(0.0, self._wave(50, 1.0, 6.0)), "µg/m³")
            readings["pm25"] = Reading.ok(max(0.0, self._wave(50, 3.0, 12.0, 1.0)), "µg/m³")
            readings["pm10"] = Reading.ok(max(0.0, self._wave(50, 5.0, 18.0, 2.0)), "µg/m³")
        if self._noise:
            readings["noise"] = Reading.ok(self._wave(15, -35.0, -6.0), "dB")
        return readings
