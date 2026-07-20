"""Sensor access for the Pimoroni Enviro+ pHAT.

Every sensor is initialised independently and guarded, so a missing or
broken sensor (e.g. no PMS5003 plugged in) degrades to ``None`` readings
instead of crashing the app.
"""

import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Proximity value above which we consider the sensor "tapped".
TAP_THRESHOLD = 1500


@dataclass
class Readings:
    """A single snapshot of every sensor on the Enviro+."""

    temperature: Optional[float] = None  # °C, CPU-heat compensated
    raw_temperature: Optional[float] = None  # °C, straight from the BME280
    humidity: Optional[float] = None  # % relative humidity
    pressure: Optional[float] = None  # hPa
    lux: Optional[float] = None  # lux
    proximity: Optional[float] = None  # unitless (LTR559 counts)
    oxidising: Optional[float] = None  # kΩ
    reducing: Optional[float] = None  # kΩ
    nh3: Optional[float] = None  # kΩ
    pm1: Optional[float] = None  # µg/m³
    pm25: Optional[float] = None  # µg/m³
    pm10: Optional[float] = None  # µg/m³
    noise: Optional[float] = None  # relative amplitude


class EnviroSensors:
    """Reads the real Enviro+ hardware."""

    # The BME280 sits right next to the Pi's SoC and reads warm; the usual
    # Pimoroni-recommended compensation subtracts a fraction of the CPU heat.
    COMP_FACTOR = 2.25

    def __init__(self, enable_pms: bool = True, enable_noise: bool = True,
                 noise_interval: float = 5.0):
        """noise_interval: seconds between mic samples (reading it blocks)."""
        self._noise_interval = noise_interval
        self._bme280 = None
        self._ltr559 = None
        self._gas = None
        self._pms = None
        self._noise = None
        self._noise_value: Optional[float] = None
        self._last_noise_read = 0.0

        try:
            from smbus2 import SMBus
            from bme280 import BME280

            self._bme280 = BME280(i2c_dev=SMBus(1))
            # The first reading after power-up is garbage; discard it.
            self._bme280.get_temperature()
        except Exception:
            log.warning("BME280 (temperature/humidity/pressure) unavailable", exc_info=True)

        try:
            from ltr559 import LTR559

            self._ltr559 = LTR559()
        except Exception:
            log.warning("LTR559 (light/proximity) unavailable", exc_info=True)

        try:
            from enviroplus import gas

            gas.read_all()
            self._gas = gas
        except Exception:
            log.warning("MICS6814 (gas) unavailable", exc_info=True)

        if enable_pms:
            try:
                from pms5003 import PMS5003

                self._pms = PMS5003()
            except Exception:
                log.warning("PMS5003 (particulates) unavailable", exc_info=True)

        if enable_noise:
            try:
                from enviroplus.noise import Noise

                self._noise = Noise(duration=0.25)
            except Exception:
                log.warning("Microphone (noise) unavailable", exc_info=True)

        self._cpu_temps = [self._cpu_temp() or 50.0] * 5

    @property
    def has_particulates(self) -> bool:
        return self._pms is not None

    @property
    def has_noise(self) -> bool:
        return self._noise is not None

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
        return raw - ((avg_cpu - raw) / self.COMP_FACTOR)

    def proximity(self) -> Optional[float]:
        """Fast proximity-only read, used for tap detection between refreshes."""
        if self._ltr559 is None:
            return None
        try:
            return self._ltr559.get_proximity()
        except Exception:
            return None

    def read(self) -> Readings:
        r = Readings()

        if self._bme280 is not None:
            try:
                r.raw_temperature = self._bme280.get_temperature()
                r.temperature = self._compensate(r.raw_temperature)
                r.pressure = self._bme280.get_pressure()
                r.humidity = self._bme280.get_humidity()
            except Exception:
                log.warning("BME280 read failed", exc_info=True)

        if self._ltr559 is not None:
            try:
                r.lux = self._ltr559.get_lux()
                r.proximity = self._ltr559.get_proximity()
            except Exception:
                log.warning("LTR559 read failed", exc_info=True)

        if self._gas is not None:
            try:
                g = self._gas.read_all()
                r.oxidising = g.oxidising / 1000.0
                r.reducing = g.reducing / 1000.0
                r.nh3 = g.nh3 / 1000.0
            except Exception:
                log.warning("Gas read failed", exc_info=True)

        if self._pms is not None:
            try:
                pm = self._pms.read()
                r.pm1 = float(pm.pm_ug_per_m3(1.0))
                r.pm25 = float(pm.pm_ug_per_m3(2.5))
                r.pm10 = float(pm.pm_ug_per_m3(10))
            except Exception:
                log.warning("PMS5003 read failed", exc_info=True)

        if self._noise is not None:
            now = time.monotonic()
            if now - self._last_noise_read >= self._noise_interval:
                self._last_noise_read = now
                try:
                    _low, _mid, _high, amp = self._noise.get_noise_profile()
                    self._noise_value = amp
                except Exception:
                    log.warning("Noise read failed", exc_info=True)
            r.noise = self._noise_value

        return r


class MockSensors:
    """Generates plausible drifting values so the app can run off-device."""

    def __init__(self, enable_pms: bool = True, enable_noise: bool = True):
        self._t0 = time.monotonic()
        self.has_particulates = enable_pms
        self.has_noise = enable_noise

    def _wave(self, period: float, lo: float, hi: float, phase: float = 0.0) -> float:
        t = time.monotonic() - self._t0
        mid, span = (lo + hi) / 2.0, (hi - lo) / 2.0
        return mid + span * math.sin(2 * math.pi * (t / period) + phase)

    def proximity(self) -> float:
        return 0.0

    def read(self) -> Readings:
        r = Readings(
            raw_temperature=self._wave(120, 24.0, 28.0),
            humidity=self._wave(90, 38.0, 55.0, 1.0),
            pressure=self._wave(300, 1008.0, 1016.0, 2.0),
            lux=max(0.0, self._wave(60, -20.0, 400.0, 3.0)),
            proximity=0.0,
            oxidising=self._wave(80, 8.0, 25.0, 0.5),
            reducing=self._wave(70, 200.0, 450.0, 1.5),
            nh3=self._wave(75, 60.0, 120.0, 2.5),
        )
        r.temperature = r.raw_temperature - 2.0
        if self.has_particulates:
            r.pm1 = max(0.0, self._wave(50, 1.0, 6.0))
            r.pm25 = max(0.0, self._wave(50, 3.0, 12.0, 1.0))
            r.pm10 = max(0.0, self._wave(50, 5.0, 18.0, 2.0))
        if self.has_noise:
            r.noise = max(0.0, self._wave(15, 0.1, 0.9))
        return r
