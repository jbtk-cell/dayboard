"""Command line: run the screen, or inspect what it would say.

    uv run python -m dayboard.cli demo                 screen with a simulated day
    uv run python -m dayboard.cli demo --scenario double-dose
    uv run python -m dayboard.cli show --scenario quiet    print it, no browser
    uv run python -m dayboard.cli serve                real sensors only
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from dayboard import server
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
    server.configure(home)
    print(f"scenario: {args.scenario}")
    server.serve(args.host, args.port)
    return 0


def cmd_serve(args) -> int:
    server.serve(args.host, args.port)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dayboard", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print the board for a scenario")
    show.add_argument("--scenario", default="ordinary", choices=sorted(SCENARIOS))
    show.add_argument("--hour", type=int, default=13, help="hour of day to render")
    show.set_defaults(func=cmd_show)

    demo = sub.add_parser("demo", help="serve the screen with a simulated day")
    demo.add_argument("--scenario", default="ordinary", choices=sorted(SCENARIOS))
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8080)
    demo.set_defaults(func=cmd_demo)

    run = sub.add_parser("serve", help="serve the screen, real sensors only")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=8080)
    run.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
