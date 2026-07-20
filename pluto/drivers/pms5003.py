"""PMS5003 driver: particulate matter (the optional plug-in sensor)."""

import logging
from typing import Any, Dict, Optional

from .base import Driver, Reading

log = logging.getLogger(__name__)


class PMS5003Driver(Driver):
    name = "pms5003"
    provides = ("pm1", "pm25", "pm10")

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        super().__init__(settings)
        self._pms = None

    def available(self) -> bool:
        try:
            from pms5003 import PMS5003

            self._pms = PMS5003()
            return True
        except Exception:
            log.warning("PMS5003 (particulates) unavailable", exc_info=True)
            return False

    def read(self) -> Dict[str, Reading]:
        try:
            pm = self._pms.read()
            return {
                "pm1": Reading.ok(float(pm.pm_ug_per_m3(1.0)), "µg/m³"),
                "pm25": Reading.ok(float(pm.pm_ug_per_m3(2.5)), "µg/m³"),
                "pm10": Reading.ok(float(pm.pm_ug_per_m3(10)), "µg/m³"),
            }
        except Exception:
            log.warning("PMS5003 read failed", exc_info=True)
            return self.error_readings()
