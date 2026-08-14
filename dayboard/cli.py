"""Command line: run the screen, or inspect what it would say.

    dayboard demo                     the screen, with a simulated day
    dayboard demo --scenario double-dose
    dayboard show --scenario quiet    print it, no browser
    dayboard serve                    the real thing: screen, console, sensors
    dayboard bridge                   Zigbee sensors, forwarded into serve

Before it is installed, each of these is `uv run python -m dayboard.cli ...`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from dayboard import server
from dayboard.store import Store
from dayboard.board import build, explain
from dayboard.events import EventLog
from dayboard.simulate import SCENARIOS, build_scenario


def _load(scenario: str, base: datetime):
    events, home = build_scenario(scenario, base)
    log = EventLog()
    log.extend(events)
    return log, home


def cmd_show(args) -> int:
    base = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    log, home = _load(args.scenario, base)
    now = base.replace(hour=args.hour, minute=0)

    board = build(log, home, now)
    print()
    print(f"  ┌─ what she sees at {now:%H:%M} " + "─" * 26)
    print(f"  │  {board.heading}")
    print("  │")
    if board.lines:
        for line in board.lines:
            print(f"  │  {line}")
    else:
        print("  │  Nothing to show yet today.")
    print("  └" + "─" * 47)
    print()
    print(explain(board))
    print()
    return 0


def cmd_demo(args) -> int:
    base = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    log, home = _load(args.scenario, base)
    server._log = log
    # Pin the clock inside the simulated day, so the demo shows the same thing
    # whatever time you happen to run it.
    server.configure(home, now=base.replace(hour=args.hour, minute=0))
    print(f"scenario: {args.scenario}, shown as {args.hour}:00 on the simulated day")
    server.serve(args.host, args.port)
    return 0


def cmd_serve(args) -> int:
    store = Store(Path(args.data) if args.data else None)
    server.load_from(store)
    print(f"data: {store.dir}")
    server.serve(args.host, args.port)
    return 0


def cmd_bridge(args) -> int:
    """Zigbee sensors, forwarded to a dayboard that is already running."""
    from dayboard.bridge import load_config, run, write_starter_config

    store = Store(Path(args.data) if args.data else None)
    path = Path(args.config) if args.config else store.dir / "bridge.json"
    if not path.exists():
        write_starter_config(path)
        print(f"wrote a starter config to {path}")
        print("The device names in it are placeholders. Set them to the "
              "friendly names zigbee2mqtt shows, then run this again.")
        return 0

    config = load_config(path)
    if args.broker:
        config["broker"] = args.broker
    if args.dayboard:
        config["dayboard"] = args.dayboard
    if not config["devices"]:
        print(f"{path} maps no devices, so there is nothing to forward.")
        return 1
    return run(config, args.token or store.token)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="dayboard", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print the board for a scenario")
    show.add_argument("--scenario", default="ordinary", choices=sorted(SCENARIOS))
    show.add_argument("--hour", type=int, default=13, help="hour of day to render")
    show.set_defaults(func=cmd_show)

    demo = sub.add_parser("demo", help="serve the screen with a simulated day")
    demo.add_argument("--scenario", default="ordinary", choices=sorted(SCENARIOS))
    demo.add_argument("--hour", type=int, default=13, help="hour of the simulated day")
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8080)
    demo.set_defaults(func=cmd_demo)

    run = sub.add_parser("serve", help="serve the screen and console")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=8080)
    run.add_argument("--data", default="", help="where to keep the day (default ~/.dayboard)")
    run.set_defaults(func=cmd_serve)

    bridge = sub.add_parser("bridge", help="forward Zigbee sensors into dayboard")
    bridge.add_argument("--config", default="", help="default ~/.dayboard/bridge.json")
    bridge.add_argument("--broker", default="", help="override the MQTT broker host")
    bridge.add_argument("--dayboard", default="", help="override the dayboard url")
    bridge.add_argument("--token", default="", help="default: read from the data dir")
    bridge.add_argument("--data", default="", help="where the day is kept (default ~/.dayboard)")
    bridge.set_defaults(func=cmd_bridge)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
