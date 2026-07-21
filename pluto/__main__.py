"""Entry point: ``python -m pluto``."""

import argparse
import logging
import os
import sys
from typing import List, Optional

from . import config as config_module
from .app import App
from .config import ConfigError
from .drivers import load_drivers, provided_fields
from .sinks import SinkContext, load_sinks
from .transforms import build_pipeline


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
                        help="run with simulated sensors (for development off the Pi)")
    parser.add_argument("--refresh", type=float, metavar="SECONDS",
                        help="seconds between sensor reads (default: 1)")
    parser.add_argument("--cycle", type=float, metavar="SECONDS",
                        help="seconds between automatic LCD page changes, 0 to disable (default: 10)")
    parser.add_argument("--no-pms", action="store_true",
                        help="skip the PMS5003 particulate sensor")
    parser.add_argument("--no-noise", action="store_true",
                        help="skip the microphone/noise sensor")
    parser.add_argument("--once", action="store_true",
                        help="read once, publish to every sink, and exit (smoke test)")
    parser.add_argument("--frames-dir", metavar="DIR",
                        help="enable the PNG sink, rendering each page into DIR")
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
        drivers = load_drivers(cfg.sensors, mock=args.mock)
        pipeline = build_pipeline(drivers, cfg.sensors, cfg.derived)
        fields = provided_fields(drivers) | pipeline.derived_fields
        # The LCD only auto-detects on real hardware; --mock skips it
        # so a dev box never tries to open /dev/spidev.
        sinks = load_sinks(cfg.outputs, SinkContext(device=cfg.device, fields=fields),
                           cfg.buffer, autoload=not args.mock)
    except ConfigError as e:
        log.error("Configuration error: %s", e)
        return 2

    if cfg.device.id or cfg.device.location:
        log.info("Device %s%s", cfg.device.id or "(unnamed)",
                 f" at {cfg.device.location}" if cfg.device.location else "")
    if not drivers:
        log.warning("No sensor drivers available; every reading will show as --")
    if not sinks:
        log.info("No output sinks active; running headless")

    app = App(drivers, sinks=sinks, refresh=cfg.sensors.refresh,
              device=cfg.device, pipeline=pipeline)

    if args.once:
        app.run_once()
    else:
        app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
