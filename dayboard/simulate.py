"""A day's worth of sensor events, so the screen can be judged before anything is bought.

The scenarios are chosen to include the cases that are easy to get wrong, not
the ones that make the screen look good. In particular `double_dose` is the
reason the pill box rule counts openings instead of reporting the latest one,
and `quiet_morning` is the reason the screen stays silent instead of announcing
that nothing has happened.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from dayboard.events import Event
from dayboard.rules import Home


def _day(base: datetime, hour: int, minute: int = 0) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def ordinary_day(base: datetime) -> tuple[list[Event], Home]:
    """Pills once, breakfast and lunch, a visitor due, one trip to the door."""
    e = [
        Event("pill_box", "contact", "opened", _day(base, 8, 15)),
        Event("pill_box", "contact", "closed", _day(base, 8, 16)),
        Event("fridge", "contact", "opened", _day(base, 8, 20)),
        Event("kettle", "power", "on", _day(base, 8, 22)),
        Event("kitchen_motion", "motion", "motion", _day(base, 8, 25)),
        Event("front_door", "contact", "opened", _day(base, 11, 5)),
        Event("fridge", "contact", "opened", _day(base, 12, 40)),
        Event("microwave", "power", "on", _day(base, 12, 45)),
    ]
    home = Home(schedule=[(_day(base, 16, 0), "Sarah is coming")])
    return e, home


def double_dose(base: datetime) -> tuple[list[Event], Home]:
    """The dangerous one: the box opened twice, half an hour apart.

    A screen that reported only the most recent opening would say "8:45am" and
    quietly erase the evidence that a dose may already have been taken at 8:15.
    """
    e = [
        Event("pill_box", "contact", "opened", _day(base, 8, 15)),
        Event("pill_box", "contact", "closed", _day(base, 8, 16)),
        Event("pill_box", "contact", "opened", _day(base, 8, 45)),
        Event("pill_box", "contact", "closed", _day(base, 8, 46)),
        Event("fridge", "contact", "opened", _day(base, 8, 50)),
        Event("kettle", "power", "on", _day(base, 8, 52)),
    ]
    return e, Home()


def quiet_morning(base: datetime) -> tuple[list[Event], Home]:
    """Nothing has happened. The screen must not say so.

    Silence is correct here: the sensors cannot tell "she has not taken her
    pills" from "the sensor missed it" or "she takes them from a bottle in the
    bathroom". Announcing the former would be a guess with a dose riding on it.
    """
    return [Event("kitchen_motion", "motion", "motion", _day(base, 7, 30))], Home()


def ambiguous_door(base: datetime) -> tuple[list[Event], Home]:
    """Someone came in, or she went out. A contact sensor cannot tell which."""
    e = [
        Event("front_door", "contact", "opened", _day(base, 14, 10)),
        Event("front_door", "contact", "closed", _day(base, 14, 11)),
        Event("pill_box", "contact", "opened", _day(base, 8, 5)),
    ]
    return e, Home(schedule=[(_day(base, 14, 0), "The nurse is visiting")])


SCENARIOS = {
    "ordinary": ordinary_day,
    "double-dose": double_dose,
    "quiet": quiet_morning,
    "ambiguous-door": ambiguous_door,
}


def build_scenario(name: str, base: datetime | None = None):
    base = base or datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; try {', '.join(SCENARIOS)}")
    return SCENARIOS[name](base)
