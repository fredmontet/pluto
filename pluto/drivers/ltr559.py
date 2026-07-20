"""LTR559 driver: light and proximity (also powers tap-to-switch-page)."""

import logging
from typing import Any, Dict, Optional

from .base import Driver, Reading

log = logging.getLogger(__name__)


class LTR559Driver(Driver):
    name = "ltr559"
    provides = ("lux", "proximity")

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        super().__init__(settings)
        self._ltr559 = None

    def available(self) -> bool:
        try:
            from ltr559 import LTR559

            self._ltr559 = LTR559()
            return True
        except Exception:
            log.warning("LTR559 (light/proximity) unavailable", exc_info=True)
            return False

    def read(self) -> Dict[str, Reading]:
        try:
            return {
                "lux": Reading.ok(self._ltr559.get_lux(), "lx"),
                "proximity": Reading.ok(self._ltr559.get_proximity()),
            }
        except Exception:
            log.warning("LTR559 read failed", exc_info=True)
            return self.error_readings()

    def proximity(self) -> Optional[float]:
        """Fast proximity-only read, polled between full refreshes."""
        try:
            return self._ltr559.get_proximity()
        except Exception:
            return None
