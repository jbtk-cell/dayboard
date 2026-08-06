"""Sensor events, and the day they belong to.

Deliberately dumb. A sensor reports that a thing changed state at a time; it
reports nothing about what a person did. All interpretation happens in rules.py,
where it can be argued with and tested, rather than being baked into whatever
firmware happens to be on a $10 door sensor.

Everything is scoped to a single day. Yesterday's pill box opening is not just
irrelevant on the screen, it is dangerous -- so events are dropped at the day
boundary rather than being allowed to linger and be mistaken for today's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

# When a "day" starts for someone who may be awake at odd hours. Using midnight
# would wipe the screen at 00:00 while she is still up and still wondering
# whether she took her evening pills.
DAY_START = time(4, 0)


@dataclass(frozen=True)
class Event:
    """One state change from one sensor."""

    sensor: str
    kind: str
    state: str
    at: datetime

    def __post_init__(self) -> None:
        if not self.sensor or not self.kind or not self.state:
            raise ValueError("event needs sensor, kind and state")


def logical_date(moment: datetime) -> date:
    """The day an event belongs to, with the boundary at DAY_START."""
    if moment.time() < DAY_START:
        return (moment - _ONE_DAY).date()
    return moment.date()


_ONE_DAY = __import__("datetime").timedelta(days=1)


def part_of_day(moment: datetime) -> str:
    """Morning, afternoon, evening or night, in the words a person uses."""
    hour = moment.hour
    if 4 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def spoken_time(moment: datetime) -> str:
    """A clock time as it would be said aloud, not as a 24-hour string."""
    hour = moment.hour % 12 or 12
    suffix = "am" if moment.hour < 12 else "pm"
    return f"{hour}:{moment.minute:02d}{suffix}"


class EventLog:
    """Events for the current logical day."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._day: date | None = None

    def add(self, event: Event) -> None:
        day = logical_date(event.at)
        if self._day is None:
            self._day = day
        elif day != self._day:
            # A new day: today's screen must not inherit yesterday's answers.
            self._events.clear()
            self._day = day
        self._events.append(event)
        self._events.sort(key=lambda e: e.at)

    def extend(self, events) -> None:
        for event in events:
            self.add(event)

    def since_day_start(self, now: datetime) -> list[Event]:
        day = logical_date(now)
        return [e for e in self._events if logical_date(e.at) == day and e.at <= now]

    def by_sensor(self, sensor: str, now: datetime) -> list[Event]:
        return [e for e in self.since_day_start(now) if e.sensor == sensor]

    def __len__(self) -> int:
        return len(self._events)
