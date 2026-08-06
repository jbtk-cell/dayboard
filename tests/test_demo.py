"""The demo must show something whatever time of day it is run.

It did not. The simulated day was built around 9am, so running `demo` at 1am put
every simulated event in the future, where the day-boundary filter correctly
discarded it, and the screen said "Nothing to show yet today". The filtering was
right; pinning the demo's clock to real wall-clock time was wrong.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from dayboard import server
from dayboard.board import build
from dayboard.events import EventLog
from dayboard.simulate import SCENARIOS, build_scenario

POPULATED = [name for name in SCENARIOS if name != "quiet"]


@pytest.mark.parametrize("wall_clock_hour", [0, 1, 3, 5, 9, 13, 17, 23])
@pytest.mark.parametrize("scenario", POPULATED)
def test_demo_shows_content_at_any_hour(scenario, wall_clock_hour):
    """Whatever time the person running it happens to be awake."""
    real_now = datetime(2026, 8, 6, wall_clock_hour, 30)
    base = real_now.replace(hour=9, minute=0, second=0, microsecond=0)

    events, home = build_scenario(scenario, base)
    log = EventLog()
    log.extend(events)

    board = build(log, home, base.replace(hour=13))
    assert board.lines, f"{scenario} at {wall_clock_hour}:30 rendered an empty screen"


def test_quiet_scenario_is_still_allowed_to_be_empty():
    base = datetime(2026, 8, 6, 9, 0)
    events, home = build_scenario("quiet", base)
    log = EventLog()
    log.extend(events)
    assert build(log, home, base.replace(hour=13)).lines == []


def test_server_clock_is_frozen_in_demo_mode():
    base = datetime(2026, 8, 6, 9, 0)
    events, home = build_scenario("ordinary", base)
    log = EventLog()
    log.extend(events)
    server._log = log
    server.configure(home, now=base.replace(hour=13))
    try:
        board = server.current_board()
        assert board.lines
        assert board.clock == "1:00pm"
        assert board.heading == "Thursday afternoon"
    finally:
        server.configure(home, now=None)
        server._log = EventLog()


def test_board_carries_its_own_clock():
    """The screen must not depend on the browser agreeing about the time."""
    base = datetime(2026, 8, 6, 9, 0)
    events, home = build_scenario("ordinary", base)
    log = EventLog()
    log.extend(events)
    assert build(log, home, base.replace(hour=8, minute=5)).clock == "8:05am"
    assert build(log, home, base.replace(hour=20, minute=45)).clock == "8:45pm"
