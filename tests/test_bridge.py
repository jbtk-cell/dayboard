"""The Zigbee bridge, which is where a piece of metal becomes a claim.

Most of these are about one movement of one lid. A reed switch bounces, and the
difference between "she opened the box" and "she opened the box five times" is
invented entirely in this layer -- the rules downstream have no way to tell a
bouncing magnet from a busy morning, and neither has she.

The two tests to read first are the pair at the top of TestOneMovement: the same
pipeline must collapse a burst into one opening and must leave a genuine second
dose, half an hour later, standing as two.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta

import pytest

from dayboard.board import build
from dayboard.bridge import (
    Client, Device, Translator, load_config, state_of, write_starter_config,
)
from dayboard.events import Event, EventLog
from dayboard.rules import Home

DAY = datetime(2026, 8, 6)


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute, second=second)


def pill_box() -> Translator:
    return Translator({"pill box": Device("pill_box")})


def shut_box() -> Translator:
    """A bridge that has already heard the lid is shut.

    Zigbee2mqtt sends the state it last knew as soon as anything subscribes, so
    this is the ordinary starting condition rather than a convenience.
    """
    translator = pill_box()
    translator.translate("pill box", {"contact": True}, at(4, 0))
    return translator


def drive(translator: Translator, name: str, messages) -> EventLog:
    """Push (moment, payload) pairs through and collect what reached the log."""
    log = EventLog()
    for moment, payload in messages:
        for event in translator.translate(name, payload, moment):
            log.add(Event(event["sensor"], event["kind"], event["state"], moment))
    return log


class TestPolarity:
    """`contact` is true when the magnet is NEAR the switch: the lid is shut.

    Backwards, this announces a dose every time she closes the box."""

    def test_contact_false_is_open(self):
        assert state_of(Device("pill_box"), {"contact": False}) == "opened"

    def test_contact_true_is_shut(self):
        assert state_of(Device("pill_box"), {"contact": True}) == "closed"

    def test_a_message_about_something_else_says_nothing(self):
        assert state_of(Device("pill_box"), {"battery": 90, "linkquality": 60}) is None


class TestOneMovement:
    def test_a_bouncing_switch_is_one_opening(self):
        """A nudged lid reports a burst. It is still one opening."""
        burst = [
            (at(8, 15, 0), {"contact": False}),
            (at(8, 15, 1), {"contact": True}),
            (at(8, 15, 1), {"contact": False}),
            (at(8, 15, 2), {"contact": True}),
            (at(8, 15, 3), {"contact": False}),
        ]
        log = drive(shut_box(), "pill box", burst)
        openings = [e for e in log.all() if e.state == "opened"]
        assert len(openings) == 1
        assert build(log, Home(), at(13)).lines[0] == (
            "Your pill box was opened at 8:15am."
        )

    def test_a_real_second_dose_still_reads_as_two(self):
        """The filter above must not be able to hide this. It is the single
        event most likely to hurt her, and the reason the rule counts."""
        day = [
            (at(8, 15), {"contact": False}),
            (at(8, 16), {"contact": True}),
            (at(8, 45), {"contact": False}),
            (at(8, 46), {"contact": True}),
        ]
        log = drive(shut_box(), "pill box", day)
        line = build(log, Home(), at(13)).lines[0]
        assert "twice" in line
        assert "8:15am" in line and "8:45am" in line

    def test_a_burst_leaves_the_bridge_in_step_with_the_lid(self):
        """The dangerous residue of a debounce: if a suppressed message leaves
        the bridge believing the lid is open when it is shut, the next genuine
        opening is swallowed and the screen never mentions the dose."""
        translator = shut_box()
        burst = [
            (at(8, 15, 0), {"contact": False}),
            (at(8, 15, 1), {"contact": True}),   # suppressed, but believed
        ]
        drive(translator, "pill box", burst)
        assert translator.believed["pill_box"] == "closed"

        later = translator.translate("pill box", {"contact": False}, at(12, 30))
        assert [e["state"] for e in later] == ["opened"]

    def test_a_lid_left_open_is_not_reopened_every_report(self):
        """Contact sensors repeat themselves. Agreement is not an event."""
        repeats = [(at(8, 15), {"contact": False})]
        repeats += [(at(9 + n, 0), {"contact": False}) for n in range(4)]
        log = drive(shut_box(), "pill box", repeats)
        assert len(log.all()) == 1


class TestStartingUp:
    def test_a_lid_already_open_is_not_an_opening(self):
        """The bridge restarts at 6am and zigbee2mqtt replays what it knew. A
        lid left open overnight must not become a dose taken at breakfast."""
        translator = pill_box()
        log = drive(translator, "pill box", [(at(6, 0), {"contact": False})])
        assert log.all() == []
        assert build(log, Home(), at(13)).lines == []
        assert translator.believed["pill_box"] == "opened"

    def test_but_the_next_real_movement_counts(self):
        translator = pill_box()
        drive(translator, "pill box", [(at(6, 0), {"contact": False})])
        shut = translator.translate("pill box", {"contact": True}, at(6, 30))
        assert [e["state"] for e in shut] == ["closed"]
        opened = translator.translate("pill box", {"contact": False}, at(8, 15))
        assert [e["state"] for e in opened] == ["opened"]


class TestOtherKinds:
    def test_motion_is_only_reported_when_there_is_some(self):
        device = Device("kitchen_motion", kind="motion")
        assert state_of(device, {"occupancy": True}) == "motion"
        assert state_of(device, {"occupancy": False}) == "still"

    def test_a_plug_crosses_a_threshold_rather_than_reporting_watts(self):
        kettle = Device("kettle", kind="power", watts=100)
        assert state_of(kettle, {"power": 1800}) == "on"
        assert state_of(kettle, {"power": 0.4}) == "off"

    def test_a_plug_idling_below_the_threshold_never_turns_on(self):
        """Standby draw on a microwave clock is a few watts and constant."""
        kettle = Device("kettle", kind="power", watts=100)
        readings = [(at(8, n), {"power": 3.1}) for n in range(0, 40, 5)]
        log = drive(Translator({"kettle plug": kettle}), "kettle plug", readings)
        assert log.all() == []


class TestWhatItIgnores:
    def test_a_device_nobody_mapped_is_ignored(self):
        assert pill_box().translate("someone elses lamp", {"contact": False}, at(9)) == []

    def test_a_battery_report_is_recorded_but_is_not_an_event(self):
        translator = pill_box()
        assert translator.translate("pill box", {"battery": 41}, at(9)) == []
        assert translator.battery["pill_box"] == 41


class TestDelivery:
    """An opening lost because the screen was restarting is a silent hole."""

    def test_events_are_held_when_dayboard_is_unreachable(self):
        server = FakeDayboard(reachable=False)
        client = Client("http://x", "tok", opener=server)
        assert client.send("/event", {"sensor": "pill_box", "state": "opened"}) is False
        assert len(client.held) == 1

    def test_held_events_are_delivered_in_order_when_it_returns(self):
        server = FakeDayboard(reachable=False)
        client = Client("http://x", "tok", opener=server)
        client.send("/event", {"sensor": "pill_box", "state": "opened"})
        client.send("/event", {"sensor": "pill_box", "state": "closed"})
        server.reachable = True
        assert client.send("/event", {"sensor": "fridge", "state": "opened"}) is True
        assert [body["state"] for _url, body in server.received] == [
            "opened", "closed", "opened",
        ]
        assert not client.held

    def test_the_token_travels_with_every_event(self):
        server = FakeDayboard()
        Client("http://x", "s3cret", opener=server).send("/event", {"a": 1})
        assert server.tokens == ["s3cret"]


class TestConfig:
    def test_the_starter_config_round_trips(self, tmp_path):
        path = write_starter_config(tmp_path / "bridge.json")
        config = load_config(path)
        assert config["devices"]["pill box"].sensor == "pill_box"
        assert config["devices"]["kettle plug"].kind == "power"
        assert config["devices"]["kettle plug"].watts == 100

    def test_a_config_without_devices_is_not_a_crash(self, tmp_path):
        path = tmp_path / "bridge.json"
        path.write_text(json.dumps({"broker": "pi.local"}))
        config = load_config(path)
        assert config["devices"] == {}
        assert config["broker"] == "pi.local"


class FakeDayboard:
    """Stands in for the server, and can be switched off."""

    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable
        self.received: list[tuple[str, dict]] = []
        self.tokens: list[str] = []

    def __call__(self, request, timeout=None):
        if not self.reachable:
            raise urllib.error.URLError("dayboard is down")
        self.received.append((request.full_url, json.loads(request.data)))
        self.tokens.append(request.headers.get("X-dayboard-token", ""))
        return _Reply()


class _Reply:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"
