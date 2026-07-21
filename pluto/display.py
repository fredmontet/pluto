"""Page rendering for the Enviro+ 0.96" LCD (160x80, ST7735).

The Renderer draws pages as PIL images, independent of the output
device, so the same frames go to the real LCD (sinks/lcd.py) or to
PNG files (sinks/png.py).
"""

import logging
from typing import Callable, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .drivers.base import Readings
from .model import DERIVED_METRICS

log = logging.getLogger(__name__)

WIDTH = 160
HEIGHT = 80

BG = (0, 0, 0)
FG = (255, 255, 255)
DIM = (140, 140, 140)

COLORS = {
    "temp": (255, 99, 71),
    "hum": (100, 160, 255),
    "press": (80, 200, 120),
    "light": (255, 215, 0),
    "prox": (200, 200, 200),
    "ox": (255, 140, 0),
    "red": (255, 80, 160),
    "nh3": (170, 110, 255),
    "pm": (180, 180, 180),
    "noise": (0, 220, 220),
    "dew": (120, 190, 255),
    "ah": (90, 220, 180),
    "aqi": (255, 200, 90),
}


def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        from fonts.ttf import RobotoMedium

        return ImageFont.truetype(RobotoMedium, size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _fmt(value: Optional[float], spec: str = "{:.1f}", unit: str = "") -> str:
    if value is None:
        return "--"
    return spec.format(value) + unit


class Renderer:
    """Builds one PIL frame per page from the latest readings."""

    def __init__(self, has_particulates: bool = True, has_noise: bool = True,
                 derived=()):
        """derived: names of enabled derived metrics (dew_point,
        absolute_humidity, aqi); any of them adds an "Air" page."""
        self.font_small = _load_font(11)
        self.font_title = _load_font(12)
        self.font_value = _load_font(16)

        self.pages: List[Tuple[str, Callable[[ImageDraw.ImageDraw, Readings], None]]] = [
            ("Overview", self._page_overview),
            ("Climate", self._page_climate),
            ("Light", self._page_light),
            ("Gas", self._page_gas),
        ]
        if has_particulates:
            self.pages.append(("Particles", self._page_particles))
        if has_noise:
            self.pages.append(("Noise", self._page_noise))
        self._derived = set(derived)
        if self._derived:
            self.pages.append(("Air", self._page_air))
        self._has_particulates = has_particulates
        self._has_noise = has_noise

    def render(self, page_index: int, readings: Readings) -> Image.Image:
        image = Image.new("RGB", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(image)
        name, painter = self.pages[page_index % len(self.pages)]
        if name != "Overview":
            self._header(draw, name, page_index)
        painter(draw, readings)
        return image

    def _header(self, draw: ImageDraw.ImageDraw, title: str, page_index: int) -> None:
        draw.text((3, 1), title, font=self.font_title, fill=FG)
        # Page indicator dots, right-aligned.
        n = len(self.pages)
        x = WIDTH - 3 - n * 8
        for i in range(n):
            fill = FG if i == page_index % n else (70, 70, 70)
            draw.ellipse((x + i * 8, 5, x + i * 8 + 4, 9), fill=fill)
        draw.line((0, 15, WIDTH, 15), fill=(60, 60, 60))

    def _rows(self, draw: ImageDraw.ImageDraw, rows, top: int = 18, row_h: int = 20) -> None:
        for i, (label, value, color) in enumerate(rows):
            y = top + i * row_h
            draw.text((4, y + 2), label, font=self.font_small, fill=DIM)
            draw.text((66, y), value, font=self.font_value, fill=color)

    def _page_overview(self, draw: ImageDraw.ImageDraw, r: Readings) -> None:
        cells = [
            (_fmt(r.temperature, "{:.1f}", "°C"), COLORS["temp"]),
            (_fmt(r.humidity, "{:.0f}", "%"), COLORS["hum"]),
            (_fmt(r.pressure, "{:.0f}", " hPa"), COLORS["press"]),
            (_fmt(r.lux, "{:.0f}", " lx"), COLORS["light"]),
            ("Ox " + _fmt(r.oxidising, "{:.0f}", "k"), COLORS["ox"]),
            ("Rd " + _fmt(r.reducing, "{:.0f}", "k"), COLORS["red"]),
            ("NH3 " + _fmt(r.nh3, "{:.0f}", "k"), COLORS["nh3"]),
        ]
        if self._has_particulates:
            cells.append(("PM2.5 " + _fmt(r.pm25, "{:.0f}"), COLORS["pm"]))
        elif self._has_noise:
            cells.append(("Noise " + _fmt(r.noise, "{:.0f}", "dB"), COLORS["noise"]))
        else:
            cells.append(("Prox " + _fmt(r.proximity, "{:.0f}"), COLORS["prox"]))

        for i, (text, color) in enumerate(cells):
            col, row = i % 2, i // 2
            draw.text((4 + col * 80, 2 + row * 20), text, font=self.font_small, fill=color)

    def _page_climate(self, draw: ImageDraw.ImageDraw, r: Readings) -> None:
        self._rows(
            draw,
            [
                ("Temp", _fmt(r.temperature, "{:.1f}", " °C"), COLORS["temp"]),
                ("Humidity", _fmt(r.humidity, "{:.1f}", " %"), COLORS["hum"]),
                ("Pressure", _fmt(r.pressure, "{:.0f}", " hPa"), COLORS["press"]),
            ],
        )

    def _page_light(self, draw: ImageDraw.ImageDraw, r: Readings) -> None:
        self._rows(
            draw,
            [
                ("Light", _fmt(r.lux, "{:.0f}", " lx"), COLORS["light"]),
                ("Proximity", _fmt(r.proximity, "{:.0f}"), COLORS["prox"]),
            ],
        )

    def _page_gas(self, draw: ImageDraw.ImageDraw, r: Readings) -> None:
        self._rows(
            draw,
            [
                ("Oxidising", _fmt(r.oxidising, "{:.1f}", " kOhm"), COLORS["ox"]),
                ("Reducing", _fmt(r.reducing, "{:.1f}", " kOhm"), COLORS["red"]),
                ("NH3", _fmt(r.nh3, "{:.1f}", " kOhm"), COLORS["nh3"]),
            ],
        )

    def _page_particles(self, draw: ImageDraw.ImageDraw, r: Readings) -> None:
        self._rows(
            draw,
            [
                ("PM1.0", _fmt(r.pm1, "{:.0f}", " ug/m3"), COLORS["pm"]),
                ("PM2.5", _fmt(r.pm25, "{:.0f}", " ug/m3"), COLORS["pm"]),
                ("PM10", _fmt(r.pm10, "{:.0f}", " ug/m3"), COLORS["pm"]),
            ],
        )

    def _page_air(self, draw: ImageDraw.ImageDraw, r: Readings) -> None:
        rows = []
        if "dew_point" in self._derived:
            rows.append(("Dew pt", _fmt(r.dew_point, "{:.1f}", " °C"), COLORS["dew"]))
        if "absolute_humidity" in self._derived:
            rows.append(("Abs hum", _fmt(r.absolute_humidity, "{:.1f}", " g/m3"), COLORS["ah"]))
        if "aqi" in self._derived:
            rows.append(("EAQI", _fmt(r.aqi, "{:.0f}", " / 6"), COLORS["aqi"]))
        self._rows(draw, rows)

    def _page_noise(self, draw: ImageDraw.ImageDraw, r: Readings) -> None:
        draw.text((4, 20), "Level", font=self.font_small, fill=DIM)
        draw.text((66, 18), _fmt(r.noise, "{:.1f}", " dB"), font=self.font_value, fill=COLORS["noise"])
        if r.noise is not None:
            # The level bar spans the -60 dB floor up to full scale (0 dB).
            fraction = min(1.0, max(0.0, (r.noise + 60.0) / 60.0))
            draw.rectangle((4, 50, 4 + int(fraction * (WIDTH - 8)), 66), fill=COLORS["noise"])
        draw.rectangle((4, 50, WIDTH - 4, 66), outline=(60, 60, 60))


def renderer_for_fields(fields: Iterable[str]) -> Renderer:
    """A Renderer whose page set matches the metrics actually flowing."""
    fields = set(fields)
    return Renderer(
        has_particulates="pm25" in fields,
        has_noise="noise" in fields,
        derived=sorted(f for f in DERIVED_METRICS if f in fields),
    )
