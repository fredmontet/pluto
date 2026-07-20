"""Driver loading: built-ins, config declarations, entry-point plugins.

Built-in drivers are auto-detected — each is instantiated and kept only
if its ``available()`` probe succeeds. A ``[sensors.<name>]`` config
table can disable a driver or pass it settings. Third-party packages
add drivers through the ``pluto.drivers`` entry-point group; they are
discovered here and treated exactly like built-ins.
"""

import logging
from importlib.metadata import entry_points
from typing import Dict, Iterable, List, Set, Type

from ..config import ConfigError, SensorsConfig
from .base import Driver, Quality, Reading, Readings, snapshot  # noqa: F401 (re-exported)

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "pluto.drivers"


def builtin_drivers() -> Dict[str, Type[Driver]]:
    from .bme280 import BME280Driver
    from .ltr559 import LTR559Driver
    from .microphone import MicrophoneDriver
    from .mics6814 import MICS6814Driver
    from .mock import MockDriver
    from .pms5003 import PMS5003Driver

    return {cls.name: cls for cls in (
        BME280Driver, LTR559Driver, MICS6814Driver,
        PMS5003Driver, MicrophoneDriver, MockDriver,
    )}


def registry() -> Dict[str, Type[Driver]]:
    """All known driver classes by name: built-ins plus entry points."""
    reg = builtin_drivers()
    for ep in _entry_points():
        try:
            cls = ep.load()
        except Exception:
            log.warning("Could not load sensor driver entry point %r", ep.name, exc_info=True)
            continue
        if not (isinstance(cls, type) and issubclass(cls, Driver)):
            log.warning("Entry point %r is not a Driver subclass; ignoring", ep.name)
            continue
        name = cls.name or ep.name
        if name in reg:
            log.warning("Driver %r already registered; ignoring entry point %r", name, ep.name)
            continue
        reg[name] = cls
    return reg


def _entry_points():
    try:
        return list(entry_points(group=ENTRY_POINT_GROUP))
    except TypeError:  # Python 3.9: entry_points() takes no group argument
        return list(entry_points().get(ENTRY_POINT_GROUP, []))


def load_drivers(cfg: SensorsConfig, mock: bool = False) -> List[Driver]:
    """Instantiate the drivers that should run, per config + detection.

    In mock mode only the mock driver loads; it inherits the enabled
    state of the pms5003/microphone drivers so ``--no-pms`` and
    ``--no-noise`` shape the fake data the same way they shape the
    real thing.
    """
    reg = registry()
    unknown = sorted(set(cfg.drivers) - set(reg))
    if unknown:
        raise ConfigError(
            f"unknown sensor driver(s) in config: {', '.join(unknown)} "
            f"(known: {', '.join(sorted(reg))})")

    if mock:
        declared = cfg.drivers.get("mock")
        settings = dict(declared.settings) if declared else {}
        settings.setdefault("pms", _enabled(cfg, "pms5003"))
        settings.setdefault("noise", _enabled(cfg, "microphone"))
        return [reg["mock"](settings)]

    loaded: List[Driver] = []
    for name in sorted(reg):
        cls = reg[name]
        declared = cfg.drivers.get(name)
        if declared is not None and not declared.enabled:
            log.info("Driver %s disabled in config", name)
            continue
        if declared is None and not cls.autoload:
            continue
        driver = cls(dict(declared.settings) if declared else {})
        if not driver.available():  # the driver logs the reason itself
            continue
        loaded.append(driver)
    return loaded


def _enabled(cfg: SensorsConfig, name: str) -> bool:
    declared = cfg.drivers.get(name)
    return declared.enabled if declared is not None else True


def read_all(drivers: Iterable[Driver]) -> Dict[str, Reading]:
    """One merged read across all drivers; a raising driver degrades to
    ``error`` readings for its fields instead of taking the loop down."""
    merged: Dict[str, Reading] = {}
    for driver in drivers:
        try:
            merged.update(driver.read())
        except Exception:
            log.warning("Driver %s read() raised", driver.name, exc_info=True)
            merged.update(driver.error_readings())
    return merged


def provided_fields(drivers: Iterable[Driver]) -> Set[str]:
    fields: Set[str] = set()
    for driver in drivers:
        fields.update(driver.provides)
    return fields
