"""What the screen is allowed to say, and why.

This module exists because of one asymmetry. The person reading the screen
cannot check whether it is telling the truth -- that is the whole reason the
screen is there. If it says "you took your pills" and she did not, she will
believe it, and she may skip a dose or take a second one. So the screen is not
allowed to say anything it cannot support.

A contact sensor on a pill box detects a lid moving. It does not detect a
person swallowing medication. Those are different facts, and the gap between
them is where someone gets hurt. Every statement here is therefore tagged with
the kind of support it has:

    OBSERVED   a sensor fired, and the sentence describes the sensor event
               itself. "Your pill box was opened at 8:15." True by construction.

    INFERRED   several signals together look like an activity. Phrased with
               "It looks like", which is honest without being confusing.

    SCHEDULED  it came from the calendar. Nothing sensed it. Always future
               tense, never presented as something that happened.

The hedging vocabulary is deliberately tiny. "Probably", "I think", "approximately"
and confidence percentages are all worse than useless to a reader with memory
impairment -- they add a second thing to reason about. Plain past tense for what
was observed, "it looks like" for what was inferred, and nothing at all when
there is no support.

Two rules are absolute and enforced by tests:

* Never state a negative. "You have not eaten today" requires knowing that no
  meal happened, and a sensor network cannot know that -- it can only fail to
  notice. Absence of evidence is not evidence of absence, and a false negative
  here tells someone to eat a second lunch or take a second dose.
* Never describe an inferred activity in the language of an observed one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Basis(Enum):
    """How much support a statement has. Ordering is meaningful: stronger first."""

    OBSERVED = "observed"
    INFERRED = "inferred"
    SCHEDULED = "scheduled"


# Verbs that assert a person did something with their body. A contact sensor,
# a motion sensor and a power meter cannot see any of these, so no OBSERVED
# statement may contain one. Checked in tests/test_safety.py.
BODILY_VERBS = frozenset({
    "took", "taken", "swallowed", "ate", "eaten", "drank", "drunk",
    "washed", "dressed", "slept", "fed",
})

# Words that turn a statement into a claim about something NOT happening.
NEGATIVE_MARKERS = frozenset({
    "not", "n't", "never", "no", "missed", "forgot", "forgotten",
    "failed", "skipped", "without", "nothing", "none", "yet",
})

INFERRED_PREFIX = "It looks like"


@dataclass(frozen=True)
class Claim:
    """One line the screen may show, with everything needed to audit it."""

    text: str
    basis: Basis
    when: datetime
    sensors: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        lowered = self.text.lower()
        words = {w.strip(".,!?;:") for w in lowered.replace("'", "'").split()}

        if words & NEGATIVE_MARKERS or "n't" in lowered:
            raise ValueError(
                f"negative statement refused: {self.text!r}. The sensors can "
                f"fail to notice something that happened; they cannot establish "
                f"that it did not happen."
            )

        if self.basis is Basis.OBSERVED and words & BODILY_VERBS:
            raise ValueError(
                f"observed statement claims a bodily action: {self.text!r}. "
                f"Describe what the sensor saw instead."
            )

        if self.basis is Basis.INFERRED and not self.text.startswith(INFERRED_PREFIX):
            raise ValueError(
                f"inferred statement must open with {INFERRED_PREFIX!r}: {self.text!r}"
            )

        if self.basis is not Basis.SCHEDULED and not self.sensors:
            raise ValueError(
                f"sensed statement carries no provenance: {self.text!r}"
            )


def observed(text: str, when: datetime, sensors: tuple[str, ...]) -> Claim:
    return Claim(text=text, basis=Basis.OBSERVED, when=when, sensors=sensors)


def inferred(text: str, when: datetime, sensors: tuple[str, ...]) -> Claim:
    if not text.startswith(INFERRED_PREFIX):
        text = f"{INFERRED_PREFIX} {text[0].lower()}{text[1:]}"
    return Claim(text=text, basis=Basis.INFERRED, when=when, sensors=sensors)


def scheduled(text: str, when: datetime) -> Claim:
    return Claim(text=text, basis=Basis.SCHEDULED, when=when, sensors=())
