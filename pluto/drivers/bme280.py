"""BME280 driver: temperature, humidity, pressure."""

import logging
from typing import Any, Dict, Optional

from .base import Driver, Reading

log = logging.getLogger(__name__)


class BME280Driver(Driver):
    """BME280 with the usual Pimoroni CPU-heat compensation.

    The sensor sits right next to the Pi's SoC and reads warm, so the
    reported temperature subtracts a fraction of the (smoothed) CPU
    temperature. ``comp_factor`` tunes that fraction: higher values
    mean weaker compensation.
    """

    name = "bme280"
    provides = ("temperature", "raw_temperature", "humidity", "pressure")
    settings_keys = ("comp_factor",)

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        super().__init__(settings)
        self._comp_factor = self.float_setting("comp_factor", 2.25, positive=True)
        self._bme280 = None
        self._cpu_temps = []

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
        self._cpu_temps = [self._cpu_temp() or 50.0] * 5
        return True

    def read(self) -> Dict[str, Reading]:
        try:
            raw = self._bme280.get_temperature()
            return {
                "raw_temperature": Reading.ok(raw, "°C"),
                "temperature": Reading.ok(self._compensate(raw), "°C"),
                "pressure": Reading.ok(self._bme280.get_pressure(), "hPa"),
                "humidity": Reading.ok(self._bme280.get_humidity(), "%"),
            }
        except Exception:
            log.warning("BME280 read failed", exc_info=True)
            return self.error_readings()

    @staticmethod
    def _cpu_temp() -> Optional[float]:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read()) / 1000.0
        except (OSError, ValueError):
            return None

    def _compensate(self, raw: float) -> float:
        cpu = self._cpu_temp()
        if cpu is None:
            return raw
        self._cpu_temps = self._cpu_temps[1:] + [cpu]
        avg_cpu = sum(self._cpu_temps) / len(self._cpu_temps)
        return raw - ((avg_cpu - raw) / self._comp_factor)
