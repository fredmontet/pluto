"""MICS6814 driver: oxidising / reducing / NH3 gas resistance."""

import logging
from typing import Any, Dict, Optional

from .base import Driver, Reading

log = logging.getLogger(__name__)


class MICS6814Driver(Driver):
    name = "mics6814"
    provides = ("oxidising", "reducing", "nh3")

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        super().__init__(settings)
        self._gas = None

    def available(self) -> bool:
        try:
            from enviroplus import gas

            gas.read_all()
            self._gas = gas
            return True
        except Exception:
            log.warning("MICS6814 (gas) unavailable", exc_info=True)
            return False

    def read(self) -> Dict[str, Reading]:
        try:
            g = self._gas.read_all()
            return {
                "oxidising": Reading.ok(g.oxidising / 1000.0, "kΩ"),
                "reducing": Reading.ok(g.reducing / 1000.0, "kΩ"),
                "nh3": Reading.ok(g.nh3 / 1000.0, "kΩ"),
            }
        except Exception:
            log.warning("Gas read failed", exc_info=True)
            return self.error_readings()
