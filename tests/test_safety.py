"""The rules that must never break, whatever else changes.

These are not ordinary unit tests. Every one of them corresponds to a way the
screen could tell a person who cannot check something that is not true.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from dayboard.board import MAX_LINES, build
from dayboard.claims import (
    BODILY_VERBS, Basis, Claim, inferred, observed, scheduled,
)
from dayboard.events import Event, EventLog
from dayboard.rules import Home, pill_box_claims

DAY = datetime(2026, 8, 6, 9, 0)


def at(hour, minute=0):
    return DAY.replace(hour=hour, minute=minute)


class TestClaimRefusals:
    def test_negative_statement_is_refused(self):
        with pytest.raises(ValueError, match="negative"):
            observed("You have not taken your pills.", at(9), ("pill_box",))

    def test_missed_and_forgot_are_refused(self):
        for text in ("You missed breakfast.", "You forgot your pills.",
                     "No pills taken today."):
            with pytest.raises(ValueError, match="negative"):
                observed(text, at(9), ("pill_box",))

    def test_observed_cannot_claim_a_bodily_action(self):
        with pytest.raises(ValueError, match="bodily"):
            observed("You took your pills at 8:15.", at(8, 15), ("pill_box",))

    def test_every_bodily_verb_is_caught(self):
        for verb in BODILY_VERBS:
            with pytest.raises(ValueError, match="bodily"):
                observed(f"You {verb} something.", at(9), ("s",))

    def test_sensed_claim_needs_provenance(self):
        with pytest.raises(ValueError, match="provenance"):
            Claim(text="Something happened.", basis=Basis.OBSERVED, when=at(9))

    def test_inferred_must_be_hedged(self):
        with pytest.raises(ValueError, match="must open with"):
            Claim(text="You had breakfast.", basis=Basis.INFERRED,
                  when=at(9), sensors=("fridge",))

    def test_inferred_helper_adds_the_hedge(self):
        c = inferred("you had breakfast.", at(9), ("fridge", "kettle"))
        assert c.text == "It looks like you had breakfast."

    def test_scheduled_needs_no_sensors(self):
        assert scheduled("Sarah is coming at 4:00pm.", at(16)).sensors == ()


class TestPillBox:
    def test_single_opening_reports_the_lid_not_the_dose(self):
        log = EventLog()
        log.add(Event("pill_box", "contact", "opened", at(8, 15)))
        claims = pill_box_claims(log, Home(), at(9))
        assert len(claims) == 1
        assert claims[0].text == "Your pill box was opened at 8:15am."
        assert claims[0].basis is Basis.OBSERVED

    def test_two_openings_are_both_reported(self):
        """The double-dose case. Tidying this away is the dangerous choice."""
        log = EventLog()
        log.add(Event("pill_box", "contact", "opened", at(8, 15)))
        log.add(Event("pill_box", "contact", "opened", at(8, 45)))
        text = pill_box_claims(log, Home(), at(9))[0].text
        assert "twice" in text
        assert "8:15am" in text and "8:45am" in text

    def test_no_opening_produces_silence_not_a_denial(self):
        log = EventLog()
        assert pill_box_claims(log, Home(), at(9)) == []


class TestMeals:
    def test_one_kitchen_signal_is_not_a_meal(self):
        log = EventLog()
        log.add(Event("fridge", "contact", "opened", at(8)))
        board = build(log, Home(), at(9))
        assert not any("breakfast" in line for line in board.lines)

    def test_two_distinct_signals_infer_a_meal(self):
        log = EventLog()
        log.add(Event("fridge", "contact", "opened", at(8)))
        log.add(Event("kettle", "power", "on", at(8, 5)))
        board = build(log, Home(), at(9))
        assert any(line.startswith("It looks like") and "breakfast" in line
                   for line in board.lines)

    def test_same_sensor_twice_is_still_one_signal(self):
        log = EventLog()
        log.add(Event("fridge", "contact", "opened", at(8)))
        log.add(Event("fridge", "contact", "opened", at(8, 10)))
        board = build(log, Home(), at(9))
        assert not any("breakfast" in line for line in board.lines)


class TestDayBoundary:
    def test_yesterdays_pill_box_does_not_appear_today(self):
        log = EventLog()
        log.add(Event("pill_box", "contact", "opened", at(8, 15)))
        tomorrow = DAY + timedelta(days=1)
        log.add(Event("fridge", "contact", "opened", tomorrow.replace(hour=7)))
        assert pill_box_claims(log, Home(), tomorrow.replace(hour=9)) == []

    def test_late_night_still_counts_as_the_same_day(self):
        log = EventLog()
        log.add(Event("pill_box", "contact", "opened", at(21, 0)))
        just_after_midnight = (DAY + timedelta(days=1)).replace(hour=1)
        assert len(pill_box_claims(log, Home(), just_after_midnight)) == 1


class TestBoard:
    def test_board_is_capped(self):
        home = Home(schedule=[(at(h), f"Visitor {h}") for h in range(10, 20)])
        log = EventLog()
        log.add(Event("pill_box", "contact", "opened", at(8, 15)))
        board = build(log, home, at(9))
        assert len(board.lines) <= MAX_LINES

    def test_medication_survives_the_cap(self):
        home = Home(schedule=[(at(h), f"Visitor {h}") for h in range(10, 20)])
        log = EventLog()
        log.add(Event("pill_box", "contact", "opened", at(8, 15)))
        board = build(log, home, at(9))
        assert "pill box" in board.lines[0]

    def test_order_is_stable_across_refreshes(self):
        log = EventLog()
        log.add(Event("pill_box", "contact", "opened", at(8, 15)))
        log.add(Event("fridge", "contact", "opened", at(8)))
        log.add(Event("kettle", "power", "on", at(8, 5)))
        first = build(log, Home(), at(9)).lines
        second = build(log, Home(), at(9, 30)).lines
        assert first == second


class TestNothingUnsafeEverReachesTheScreen:
    def test_random_event_streams_never_produce_an_unsafe_line(self):
        """Fuzz the whole pipeline. No board line may deny anything, and no
        observed line may claim a bodily action."""
        rng = random.Random(20260806)
        sensors = ["pill_box", "fridge", "kettle", "microwave",
                   "kitchen_motion", "front_door"]
        for _ in range(400):
            log = EventLog()
            for _ in range(rng.randint(0, 25)):
                log.add(Event(
                    rng.choice(sensors), "contact",
                    rng.choice(["opened", "closed", "on", "off", "motion"]),
                    at(rng.randint(5, 22), rng.randint(0, 59)),
                ))
            home = Home(schedule=[(at(rng.randint(9, 20)), "A visitor")])
            board = build(log, home, at(22))
            for claim in board.claims:
                words = {w.strip(".,") for w in claim.text.lower().split()}
                assert not (words & {"not", "never", "missed", "forgot", "no"})
                if claim.basis is Basis.OBSERVED:
                    assert not (words & BODILY_VERBS)
            assert len(board.lines) <= MAX_LINES


class TestTheHeadingAgreesWithTheLines:
    def test_after_midnight_the_heading_names_the_logical_day(self):
        """At 1am Thursday the events shown are Wednesday's, so the heading
        must say Wednesday. Found by screenshotting the console at 1am."""
        from dayboard.rules import day_heading
        one_am_thursday = datetime(2026, 8, 6, 1, 8)
        assert one_am_thursday.strftime("%A") == "Thursday"
        assert day_heading(one_am_thursday) == "Wednesday night"

    def test_during_the_day_heading_is_unchanged(self):
        from dayboard.rules import day_heading
        assert day_heading(datetime(2026, 8, 6, 13, 0)) == "Thursday afternoon"
