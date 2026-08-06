"""Turning sensor events into statements that are true.

Each rule answers one question she actually asks, and each is written to the
weakest claim the evidence supports rather than the most useful one.

The pill box rule is the one that matters most, and it is worth reading closely.
A reed switch on the lid sees the lid move. It cannot see a tablet leave the
box, and it certainly cannot see it swallowed. So the screen says the lid was
opened, at a time, in plain words. That is a fact she can act on -- if the box
was opened at 8:15 and it is now 8:40, she has her answer -- without the screen
ever telling her something it does not know.

It also counts. If the box was opened twice, the screen says twice, with both
times. Suppressing the second opening to keep the display tidy would hide the
exact event most likely to hurt her, which is a second dose taken because the
first was forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from dayboard.claims import Claim, inferred, observed, scheduled
from dayboard.events import EventLog, logical_date, part_of_day, spoken_time

MEAL_WINDOWS = (
    ("breakfast", 6, 11),
    ("lunch", 11, 15),
    ("dinner", 16, 21),
)

# A meal needs corroboration from this many distinct kitchen sensors before the
# screen will mention it. One fridge opening is someone fetching milk.
MEAL_MIN_DISTINCT_SIGNALS = 2


@dataclass
class Home:
    """Which sensor is on what. Everything here is per-household."""

    pill_box: str = "pill_box"
    front_door: str = "front_door"
    kitchen_sensors: tuple[str, ...] = ("fridge", "kettle", "microwave")
    kitchen_motion: str = "kitchen_motion"
    schedule: list[tuple[datetime, str]] = field(default_factory=list)


def _plural_times(moments: list[datetime]) -> str:
    spoken = [spoken_time(m) for m in moments]
    if len(spoken) == 1:
        return spoken[0]
    return ", ".join(spoken[:-1]) + " and " + spoken[-1]


def pill_box_claims(log: EventLog, home: Home, now: datetime) -> list[Claim]:
    openings = [e for e in log.by_sensor(home.pill_box, now) if e.state == "opened"]
    if not openings:
        # Deliberately silent. "You have not taken your pills" would be a claim
        # about something not happening, which these sensors cannot support.
        return []

    moments = [e.at for e in openings]
    if len(moments) == 1:
        text = f"Your pill box was opened at {spoken_time(moments[0])}."
    else:
        text = (
            f"Your pill box was opened {_count_phrase(len(moments))} today, "
            f"at {_plural_times(moments)}."
        )
    return [observed(text, moments[-1], (home.pill_box,))]


def _count_phrase(n: int) -> str:
    """Said the way a person says it. "Twice" reads faster than "two times"."""
    if n == 2:
        return "twice"
    word = {3: "three", 4: "four", 5: "five"}.get(n, str(n))
    return f"{word} times"


def door_claims(log: EventLog, home: Home, now: datetime) -> list[Claim]:
    openings = [e for e in log.by_sensor(home.front_door, now) if e.state == "opened"]
    if not openings:
        return []
    last = openings[-1]
    # A door sensor cannot tell arrival from departure, so the sentence does not
    # pretend to. "You went out" would be a guess wearing the clothes of a fact.
    text = f"The front door was opened at {spoken_time(last.at)}."
    return [observed(text, last.at, (last.sensor,))]


def meal_claims(log: EventLog, home: Home, now: datetime) -> list[Claim]:
    claims: list[Claim] = []
    events = log.since_day_start(now)
    watched = set(home.kitchen_sensors) | {home.kitchen_motion}

    for name, start_hour, end_hour in MEAL_WINDOWS:
        in_window = [
            e for e in events
            if e.sensor in watched and start_hour <= e.at.hour < end_hour
        ]
        distinct = {e.sensor for e in in_window}
        if len(distinct) < MEAL_MIN_DISTINCT_SIGNALS:
            continue
        last = max(e.at for e in in_window)
        claims.append(
            inferred(f"you had {name}.", last, tuple(sorted(distinct)))
        )
    return claims


def schedule_claims(home: Home, now: datetime, horizon_hours: int = 14) -> list[Claim]:
    horizon = now + timedelta(hours=horizon_hours)
    out = []
    for when, description in sorted(home.schedule):
        if now <= when <= horizon:
            out.append(scheduled(f"{description} at {spoken_time(when)}.", when))
    return out


def day_heading(now: datetime) -> str:
    """The anchor line. This alone is what a dementia day clock provides.

    The weekday comes from the logical day, not the calendar date. At 1am on a
    Thursday the calendar says Thursday, but the pills on the screen were taken
    during Wednesday and everybody in the house would call it Wednesday night.
    Naming it Thursday put the heading and the lines beneath it on different
    days, which is the one thing a memory aid cannot afford.
    """
    return f"{logical_date(now).strftime('%A')} {part_of_day(now)}"


def all_claims(log: EventLog, home: Home, now: datetime) -> list[Claim]:
    claims: list[Claim] = []
    claims += pill_box_claims(log, home, now)
    claims += meal_claims(log, home, now)
    claims += door_claims(log, home, now)
    claims += schedule_claims(home, now)
    return claims
