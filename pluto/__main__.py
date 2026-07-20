"""Entry point: ``python -m pluto``."""

import argparse
import logging
import os
import sys
from typing import List, Optional

from . import config as config_module
from .app import App
from .config import ConfigError
from .display import Renderer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pluto",
        description="Show all Enviro+ sensor values on its LCD.",
    )
    # Value flags default to None so a flag that was actually given can
    # be told apart from its default and override the config file.
    parser.add_argument("--config", metavar="PATH",
                        help="path to the TOML config file (default: ./pluto.toml if present)")
    parser.add_argument("--mock", action="store_true",
                        help="run with simulated sensors and no LCD (for development off the Pi)")
    parser.add_argument("--refresh", type=float, metavar="SECONDS",
                        help="seconds between sensor reads (default: 1)")
    parser.add_argument("--cycle", type=float, metavar="SECONDS",
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

    pub = parser.add_argument_group("publishing")
    pub.add_argument("--mqtt", metavar="HOST",
                     help="publish readings as JSON to this MQTT broker")
    pub.add_argument("--mqtt-port", type=int, metavar="PORT",
                     help="MQTT broker port (default: 1883)")
    pub.add_argument("--mqtt-topic", metavar="TOPIC",
                     help="base MQTT topic (default: pluto/<hostname>)")
    pub.add_argument("--mqtt-user", metavar="USER", help="MQTT username")
    pub.add_argument("--mqtt-password", metavar="PASS",
                     default=os.environ.get("PLUTO_MQTT_PASSWORD"),
                     help="MQTT password (or set PLUTO_MQTT_PASSWORD)")
    pub.add_argument("--ha-discovery", action="store_true",
                     help="announce the sensors to Home Assistant via MQTT discovery")
    pub.add_argument("--prometheus", type=int, metavar="PORT",
                     help="expose Prometheus metrics on this port at /metrics")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("pluto")

    try:
        cfg = config_module.load_config(args.config)
        cfg = config_module.apply_cli_overrides(cfg, args)
    except ConfigError as e:
        log.error("Configuration error: %s", e)
        return 2

    if cfg.device.id or cfg.device.location:
        log.info("Device %s%s", cfg.device.id or "(unnamed)",
                 f" at {cfg.device.location}" if cfg.device.location else "")

    if args.mock:
        from .display import ConsoleDisplay
        from .sensors import MockSensors

        sensors = MockSensors(
            enable_pms=cfg.sensors.pms.enabled,
            enable_noise=cfg.sensors.noise.enabled,
        )
        display = ConsoleDisplay(out_dir=args.frames_dir)
    else:
        from .sensors import EnviroSensors

        sensors = EnviroSensors(
            enable_pms=cfg.sensors.pms.enabled,
            enable_noise=cfg.sensors.noise.enabled,
            noise_interval=cfg.sensors.noise.interval,
        )
        if cfg.outputs.display.enabled:
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
        else:
            from .display import NullDisplay

            display = NullDisplay()

    publishers = []
    if cfg.outputs.mqtt.enabled:
        from .publish import MQTTPublisher

        m = cfg.outputs.mqtt
        publishers.append(MQTTPublisher(
            host=m.host,
            port=m.port,
            base_topic=m.topic or None,
            username=m.username or None,
            password=m.password or None,
            ha_discovery=m.ha_discovery,
            has_particulates=sensors.has_particulates,
            has_noise=sensors.has_noise,
            device_id=cfg.device.id or None,
            location=cfg.device.location or None,
        ))
    if cfg.outputs.prometheus.enabled:
        from .publish import PrometheusExporter

        publishers.append(PrometheusExporter(port=cfg.outputs.prometheus.port))

    renderer = Renderer(
        has_particulates=sensors.has_particulates,
        has_noise=sensors.has_noise,
    )
    app = App(sensors, display, renderer, refresh=cfg.sensors.refresh,
              cycle=cfg.outputs.display.cycle, publishers=publishers)

    if args.once:
        app.render_all_pages()
    else:
        app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
