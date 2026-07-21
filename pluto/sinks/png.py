"""PNG sink: render every LCD page to image files.

The dev/mock companion to the LCD sink: on each snapshot it renders
the full page set into ``<dir>/page-N-<name>.png``, overwriting the
previous frames, so the directory always shows the current view of
every page. ``--frames-dir DIR`` enables it from the CLI.
"""

import logging
import os
from typing import Any, Dict, Optional

from .base import Sink, SinkContext, Snapshot
from ..config import ConfigError
from ..drivers import flatten
from ..display import renderer_for_fields

log = logging.getLogger(__name__)


class PNGSink(Sink):
    name = "png"
    settings_keys = ("dir",)

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 context: Optional[SinkContext] = None):
        super().__init__(settings, context)
        self._dir = self.str_setting("dir", "frames")
        if not self._dir:
            raise ConfigError("outputs.png.dir must not be empty")
        os.makedirs(self._dir, exist_ok=True)
        self._renderer = renderer_for_fields(self.context.fields)
        log.info("Rendering pages to PNGs in %s/", self._dir)

    def publish(self, snapshot: Snapshot) -> None:
        readings = flatten(snapshot.readings)
        for index, (name, _) in enumerate(self._renderer.pages):
            path = os.path.join(self._dir, f"page-{index + 1}-{name.lower()}.png")
            self._renderer.render(index, readings).save(path)
