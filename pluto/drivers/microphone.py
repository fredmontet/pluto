"""MEMS microphone driver: relative noise amplitude."""

import logging
import time
from typing import Any, Dict, Optional

from .base import Driver, Reading

log = logging.getLogger(__name__)


class MicrophoneDriver(Driver):
    """Samples the mic every ``interval`` seconds and caches the value
    in between — reading the microphone blocks, so it runs much less
    often than the other sensors."""

    name = "microphone"
    provides = ("noise",)
    settings_keys = ("interval",)

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        super().__init__(settings)
        self._interval = self.float_setting("interval", 5.0, positive=True)
        self._noise = None
        self._value: Optional[float] = None
        self._last_read = 0.0

    def available(self) -> bool:
        try:
            from enviroplus.noise import Noise

            self._noise = Noise(duration=0.25)
            return True
        except Exception:
            log.warning("Microphone (noise) unavailable", exc_info=True)
            return False

    def read(self) -> Dict[str, Reading]:
        now = time.monotonic()
        if now - self._last_read >= self._interval:
            self._last_read = now
            try:
                _low, _mid, _high, amp = self._noise.get_noise_profile()
                self._value = amp
            except Exception:
                # Keep the previous value; a lone glitch shouldn't blank it.
                log.warning("Noise read failed", exc_info=True)
        if self._value is None:
            return {"noise": Reading.missing()}
        return {"noise": Reading.ok(self._value)}
