"""Assembling the screen.

Two constraints fight each other here. Everything true is worth saying, and a
crowded screen is unreadable to someone with memory impairment -- more lines
means more to hold in mind at once, which is the one thing she cannot do. So the
board is capped, and the cap is enforced by dropping the least safety-relevant
lines rather than the oldest.

Order is fixed and never changes between refreshes. A screen that reshuffles
itself has to be re-read from the top every time; a screen whose third line is
always about meals can be read at a glance. Stability beats recency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dayboard.claims import Basis, Claim
from dayboard.events import EventLog
from dayboard.rules import (
    Home, day_heading, door_claims, meal_claims, pill_box_claims, schedule_claims,
)

MAX_LINES = 4


@dataclass(frozen=True)
class Board:
    """Exactly what the screen shows, and the audit trail behind it."""

    heading: str
    lines: list[str]
    claims: list[Claim]
    generated_at: datetime

    def as_dict(self) -> dict:
        return {
            "heading": self.heading,
            "lines": self.lines,
            "generated_at": self.generated_at.isoformat(timespec="seconds"),
            "audit": [
                {
                    "text": c.text,
                    "basis": c.basis.value,
                    "when": c.when.isoformat(timespec="seconds"),
                    "sensors": list(c.sensors),
                }
                for c in self.claims
            ],
        }


def build(log: EventLog, home: Home, now: datetime) -> Board:
    """Compose the board in fixed priority order.

    Medication first because it is the only line where being wrong is
    dangerous, then meals, then what is coming, then the door. The door is last
    because it is the least actionable and the first thing worth dropping.
    """
    groups = [
        pill_box_claims(log, home, now),
        meal_claims(log, home, now),
        schedule_claims(home, now),
        door_claims(log, home, now),
    ]

    chosen: list[Claim] = []
    for group in groups:
        for claim in group:
            if len(chosen) >= MAX_LINES:
                break
            chosen.append(claim)

    return Board(
        heading=day_heading(now),
        lines=[c.text for c in chosen],
        claims=chosen,
        generated_at=now,
    )


def explain(board: Board) -> str:
    """Plain-text audit for a caregiver: every line, and what supports it."""
    out = [f"{board.heading}   (generated {board.generated_at:%H:%M})", ""]
    if not board.claims:
        out.append("  nothing to show yet today")
    for claim in board.claims:
        support = ", ".join(claim.sensors) if claim.sensors else "calendar"
        marker = {
            Basis.OBSERVED: "sensor",
            Basis.INFERRED: "inferred",
            Basis.SCHEDULED: "calendar",
        }[claim.basis]
        out.append(f"  {claim.text}")
        out.append(f"      [{marker}] {support}  at {claim.when:%H:%M}")
    return "\n".join(out)
