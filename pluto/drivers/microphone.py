"""MEMS microphone driver: noise level in dB relative to full scale.

The mic is uncalibrated, so the value is dBFS-style (20·log10 of the
relative amplitude, 0 dB = full scale, floored at -60 dB), not an
absolute sound pressure level.
"""

import logging
import math
import time
from typing import Any, Dict, Optional

from .base import Driver, Reading

log = logging.getLogger(__name__)

NOISE_FLOOR_DB = -60.0


def amplitude_to_db(amplitude: float) -> float:
    """Relative amplitude (0..1-ish) -> dB re full scale, floored."""
    if amplitude <= 0:
        return NOISE_FLOOR_DB
    return max(NOISE_FLOOR_DB, 20.0 * math.log10(amplitude))


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
                self._value = amplitude_to_db(amp)
            except Exception:
                # Keep the previous value; a lone glitch shouldn't blank it.
                log.warning("Noise read failed", exc_info=True)
        if self._value is None:
            return {"noise": Reading.missing("dB")}
        return {"noise": Reading.ok(self._value, "dB")}
