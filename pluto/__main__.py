"""Entry point: ``python -m pluto``."""

import argparse
import logging
import sys

from .app import App
from .display import Renderer


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pluto",
        description="Show all Enviro+ sensor values on its LCD.",
    )
    parser.add_argument("--mock", action="store_true",
                        help="run with simulated sensors and no LCD (for development off the Pi)")
    parser.add_argument("--refresh", type=float, default=1.0,
                        help="seconds between sensor reads (default: 1)")
    parser.add_argument("--cycle", type=float, default=10.0,
                        help="seconds between automatic page changes, 0 to disable (default: 10)")
    parser.add_argument("--no-pms", action="store_true",
                        help="skip the PMS5003 particulate sensor")
    parser.add_argument("--no-noise", action="store_true",
                        help="skip the microphone/noise sensor")
    parser.add_argument("--once", action="store_true",
                        help="render every page once and exit (smoke test / screenshots)")
    parser.add_argument("--frames-dir", metavar="DIR",
                        help="with --mock, save rendered frames as PNGs into DIR")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("pluto")

    if args.mock:
        from .display import ConsoleDisplay
        from .sensors import MockSensors

        sensors = MockSensors(enable_pms=not args.no_pms, enable_noise=not args.no_noise)
        display = ConsoleDisplay(out_dir=args.frames_dir)
    else:
        from .sensors import EnviroSensors

        sensors = EnviroSensors(enable_pms=not args.no_pms, enable_noise=not args.no_noise)
        try:
            from .display import LCD

            display = LCD()
        except Exception:
            log.error(
                "Could not initialise the ST7735 LCD. Are you running on the Pi "
                "with SPI enabled (sudo raspi-config nonint do_spi 0)? "
                "Use --mock to run without hardware.",
                exc_info=True,
            )
            return 1

    renderer = Renderer(
        has_particulates=sensors.has_particulates,
        has_noise=sensors.has_noise,
    )
    app = App(sensors, display, renderer, refresh=args.refresh, cycle=args.cycle)

    if args.once:
        app.render_all_pages()
    else:
        app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
