"""Shared plumbing for pluto's plugin systems (sensor drivers and sinks).

Both kinds of plugin are configured by a TOML table and discovered the
same way: a built-in registry extended by setuptools entry points.
"""

import logging
from importlib.metadata import entry_points
from typing import Any, Dict, Iterable, Optional, Tuple, Type

from .config import ConfigError

log = logging.getLogger(__name__)


class Configurable:
    """Something configured by a ``[section.name]`` TOML table.

    Validates that only ``settings_keys`` appear, and offers typed
    accessors that turn a bad value into a clear ConfigError.
    """

    name: str = ""
    section: str = ""  # "sensors" for drivers, "outputs" for sinks
    settings_keys: Tuple[str, ...] = ()

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = dict(settings or {})
        unknown = sorted(set(self.settings) - set(self.settings_keys))
        if unknown:
            allowed = (f"allowed: {', '.join(self.settings_keys)}"
                       if self.settings_keys else "takes no settings")
            raise ConfigError(
                f"unknown setting(s) for [{self.section}.{self.name}]: "
                f"{', '.join(unknown)} ({allowed})")

    def _key(self, key: str) -> str:
        return f"{self.section}.{self.name}.{key}"

    def str_setting(self, key: str, default: str = "") -> str:
        value = self.settings.get(key, default)
        if not isinstance(value, str):
            raise ConfigError(
                f"{self._key(key)} must be a str, got {type(value).__name__}")
        return value

    def int_setting(self, key: str, default: int,
                    minimum: Optional[int] = None,
                    maximum: Optional[int] = None) -> int:
        value = self.settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(
                f"{self._key(key)} must be an int, got {type(value).__name__}")
        if minimum is not None and value < minimum:
            raise ConfigError(f"{self._key(key)} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise ConfigError(f"{self._key(key)} must be <= {maximum}")
        return value

    def float_setting(self, key: str, default: float, positive: bool = False) -> float:
        value = self.settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(
                f"{self._key(key)} must be a number, got {type(value).__name__}")
        value = float(value)
        if positive and value <= 0:
            raise ConfigError(f"{self._key(key)} must be > 0")
        return value

    def bool_setting(self, key: str, default: bool) -> bool:
        value = self.settings.get(key, default)
        if not isinstance(value, bool):
            raise ConfigError(
                f"{self._key(key)} must be a bool, got {type(value).__name__}")
        return value


def entry_points_in(group: str) -> list:
    try:
        return list(entry_points(group=group))
    except TypeError:  # Python 3.9: entry_points() takes no group argument
        return list(entry_points().get(group, []))


def build_registry(builtin: Dict[str, type], eps: Iterable, base: type,
                   kind: str) -> Dict[str, type]:
    """Extend the built-in classes with entry-point plugins.

    Broken entry points are skipped with a warning, and an entry point
    can never shadow a built-in or an earlier plugin.
    """
    reg = dict(builtin)
    for ep in eps:
        try:
            cls = ep.load()
        except Exception:
            log.warning("Could not load %s entry point %r", kind, ep.name, exc_info=True)
            continue
        if not (isinstance(cls, type) and issubclass(cls, base)):
            log.warning("Entry point %r is not a %s subclass; ignoring",
                        ep.name, base.__name__)
            continue
        name = cls.name or ep.name
        if name in reg:
            log.warning("%s %r already registered; ignoring entry point %r",
                        kind, name, ep.name)
            continue
        reg[name] = cls
    return reg
