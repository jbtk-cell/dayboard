"""Why the screen is not saying something.

Silence is this project's most important behaviour and its least legible one.
The screen stays quiet whenever the sensors cannot support a sentence, which is
the correct thing to do and is indistinguishable, from the outside, from the
system being broken. Quiet because the pill box was not opened, quiet because
the coin cell in the pill box died on Tuesday, and quiet because nobody ever
said when her doses are, all look exactly alike on the wall.

That ambiguity is fine for her -- she does not need a diagnosis, she needs a
screen that never lies. It is not fine for whoever set the thing up, and until
now they had no way to tell those apart either.

**This module states negatives on purpose, and that is not a contradiction of
the rule it appears to break.** The prohibition in claims.py exists because of
one specific asymmetry: she cannot check the screen, so a false "you have not
taken your pills" is acted on and someone takes a second dose. A caregiver
reading a console can check. They can walk into the kitchen and look at the pill
box. So "the pill box has not reported an opening today" is a safe and useful
sentence *here*, addressed to someone who can verify it, and would be a
dangerous one *there*, addressed to someone who cannot.

Nothing in this file may reach her screen. It is not in `all_claims`, it does
not produce `Claim` objects, and it has its own place in the console.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dayboard.events import EventLog, spoken_time
from dayboard.rules import (
    ACTIVE_STATES, DOSE_LEAD, MEAL_MIN_DISTINCT_SIGNALS, MEAL_WINDOWS,
    Home, default_dose_label, dose_claims,
)


@dataclass(frozen=True)
class Silence:
    """One thing the screen is not saying, and what would change that."""

    subject: str        # what it is quiet about, in a word
    because: str        # why, in one sentence a person can act on
    would_speak: str    # what would have to be true instead
    looks_broken: bool = False   # a sensor is implicated, not just an absence

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "because": self.because,
            "would_speak": self.would_speak,
            "looks_broken": self.looks_broken,
        }


def _quiet_sensors(health: dict, names) -> list[str]:
    """Which of these have said nothing for long enough to look dead."""
    from dayboard.server import SILENT_AFTER_HOURS  # late: server imports rules

    out = []
    for name in names:
        entry = health.get(name)
        if entry is None or not entry.get("last_seen"):
            # Never heard from is not the same as fallen silent. On a fresh
            # install nothing has reported yet, and announcing that every
            # sensor looks dead would be crying wolf on the first day. The
            # health panel already shows these as "never".
            continue
        last = entry["last_seen"]
        try:
            hours = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 3600
        except ValueError:
            continue
        if hours >= SILENT_AFTER_HOURS:
            out.append(name)
    return out


def _doses(home: Home, now: datetime) -> Silence | None:
    if dose_claims(home, now):
        return None  # it is on her screen right now; there is no silence to explain
    if not home.doses:
        return Silence(
            subject="pills",
            because="No regimen has been set, so the screen never mentions pills.",
            would_speak="Add a dose time above. It repeats every day.",
        )
    upcoming = [(at, what) for at, what in sorted(home.doses)
                if datetime.combine(now.date(), at) >= now]
    if not upcoming:
        return Silence(
            subject="pills",
            because="Every dose today is more than three hours past.",
            would_speak="The next one is named again tomorrow morning.",
        )
    at, what = upcoming[0]
    name = what or default_dose_label(at)
    when = datetime.combine(now.date(), at)
    minutes = int((when - now).total_seconds() // 60)
    return Silence(
        subject="pills",
        because=(f"The {name} dose is at {spoken_time(when)}, which is "
                 f"{minutes} minutes away."),
        would_speak=(f"It appears on her screen "
                     f"{int(DOSE_LEAD.total_seconds() // 60)} minutes before."),
    )


def _pill_box(log: EventLog, home: Home, now: datetime, health: dict) -> Silence | None:
    openings = [e for e in log.by_sensor(home.pill_box, now) if e.state in ACTIVE_STATES]
    if openings:
        return None
    dead = _quiet_sensors(health, [home.pill_box])
    if dead:
        return Silence(
            subject="the pill box",
            because=(f"{home.pill_box} has not reported anything for over a day, "
                     f"so an opening today would probably be missed."),
            would_speak="Check the battery and that the sensor is paired.",
            looks_broken=True,
        )
    return Silence(
        subject="the pill box",
        because="The pill box has not reported an opening today.",
        would_speak="One opening, and her screen shows the time it happened.",
    )


def _meals(log: EventLog, home: Home, now: datetime) -> list[Silence]:
    out = []
    events = log.since_day_start(now)
    watched = set(home.kitchen_sensors) | {home.kitchen_motion}
    for name, start_hour, end_hour in MEAL_WINDOWS:
        if now.hour < start_hour:
            continue  # the window has not opened yet; nothing to explain
        in_window = [e for e in events
                     if e.sensor in watched and e.state in ACTIVE_STATES
                     and start_hour <= e.at.hour < end_hour]
        distinct = sorted({e.sensor for e in in_window})
        if len(distinct) >= MEAL_MIN_DISTINCT_SIGNALS:
            continue
        if distinct:
            because = (f"One kitchen signal in the {name} window "
                       f"({distinct[0]}), and two different ones are needed.")
            would = f"Any other kitchen sensor between {start_hour}:00 and {end_hour}:00."
        else:
            because = f"No kitchen sensor reported anything in the {name} window."
            would = f"Two different kitchen sensors between {start_hour}:00 and {end_hour}:00."
        out.append(Silence(subject=name, because=because, would_speak=would))
    return out


def _schedule(home: Home, now: datetime) -> Silence | None:
    if home.schedule:
        return None
    return Silence(
        subject="appointments",
        because="Nothing is in the calendar.",
        would_speak="Add one above and she sees it in the words you write.",
    )


def explain_silence(log: EventLog, home: Home, now: datetime,
                    health: dict | None = None) -> list[Silence]:
    """Everything the screen is not currently saying, and why.

    Ordered the way the board is: pills first, because that is the only place
    where being quiet for the wrong reason is dangerous.
    """
    health = health or {}
    found: list[Silence] = []
    for maybe in (_doses(home, now),
                  _pill_box(log, home, now, health)):
        if maybe is not None:
            found.append(maybe)
    found.extend(_meals(log, home, now))
    schedule = _schedule(home, now)
    if schedule is not None:
        found.append(schedule)
    return found
