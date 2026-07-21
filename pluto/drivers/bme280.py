"""BME280 driver: temperature, humidity, pressure.

The sensor sits right next to the Pi's SoC and reads warm, so the
``temperature`` metric carries a default ``cpu_temp_compensation``
transform (see pluto/transforms.py) — tune or disable it with a
``[sensors.bme280.temperature]`` table. ``raw_temperature`` is always
the untouched sensor value.
"""

import logging
from typing import Dict

from .base import Driver, Reading

log = logging.getLogger(__name__)


class BME280Driver(Driver):
    name = "bme280"
    provides = ("temperature", "raw_temperature", "humidity", "pressure")
    default_transforms = {"temperature": {"cpu_temp_compensation": 2.25}}

    def __init__(self, settings=None):
        super().__init__(settings)
        self._bme280 = None

    def available(self) -> bool:
        try:
            from bme280 import BME280
            from smbus2 import SMBus

            self._bme280 = BME280(i2c_dev=SMBus(1))
            # The first reading after power-up is garbage; discard it.
            self._bme280.get_temperature()
        except Exception:
            log.warning("BME280 (temperature/humidity/pressure) unavailable", exc_info=True)
            return False
        return True

    def read(self) -> Dict[str, Reading]:
        try:
            raw = self._bme280.get_temperature()
            return {
                "raw_temperature": Reading.ok(raw, "°C"),
                "temperature": Reading.ok(raw, "°C"),
                "pressure": Reading.ok(self._bme280.get_pressure(), "hPa"),
                "humidity": Reading.ok(self._bme280.get_humidity(), "%"),
            }
        except Exception:
            log.warning("BME280 read failed", exc_info=True)
            return self.error_readings()
