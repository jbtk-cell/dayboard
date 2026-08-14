"""Keeping the day on disk.

A Raspberry Pi reboots. If that wipes the morning, the screen spends the rest of
the day unable to answer the one question it exists to answer, and nobody in the
house will know why. So events, the schedule and the sensor names are written
out as they change.

Events are stored per logical day in their own file. Old days are kept, because
"was she taking her pills last week" is a question a family will eventually ask,
and because deleting the evidence behind a claim makes the audit trail a
decoration. They are never loaded into today's screen.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dayboard.events import Event, EventLog, day_end, logical_date
from dayboard.rules import Home

DEFAULT_DIR = Path.home() / ".dayboard"


class Store:
    def __init__(self, directory: Path | None = None) -> None:
        self.dir = Path(directory or DEFAULT_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        # Her day is health-adjacent and lives on a shared family machine.
        # Owner-only is the proportionate answer; inventing a cipher here would
        # be worse than none, because the key would sit in the same directory.
        try:
            self.dir.chmod(0o700)
        except OSError:
            pass

    @property
    def token(self) -> str:
        """A shared secret for writing, made once and kept owner-readable.

        Without it, anything on the home network can POST "pill_box opened" and
        the screen will tell her she took a dose she never took. Every other
        rule in this project is about not asserting more than the sensors
        support; an open write endpoint hands that guarantee to the network.
        """
        path = self.dir / "token"
        if path.exists():
            existing = path.read_text().strip()
            if existing:
                return existing
        import secrets

        value = secrets.token_urlsafe(18)
        path.write_text(value)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value

    # ---- events, one file per logical day -------------------------------

    def _events_path(self, day) -> Path:
        return self.dir / f"events-{day.isoformat()}.json"

    def load_events(self, now: datetime) -> EventLog:
        log = EventLog()
        path = self._events_path(logical_date(now))
        if not path.exists():
            return log
        try:
            rows = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return log
        for row in rows:
            try:
                log.add(Event(row["sensor"], row["kind"], row["state"],
                              datetime.fromisoformat(row["at"])))
            except (KeyError, ValueError, TypeError):
                continue  # one corrupt row must not lose the whole day
        return log

    def save_events(self, log: EventLog, now: datetime | None = None) -> None:
        """Write the log's own day. `now` is accepted and ignored, for callers
        that still pass it; using it was the bug."""
        day = log.day
        if day is None:
            return
        rows = [
            {"sensor": e.sensor, "kind": e.kind, "state": e.state,
             "at": e.at.isoformat(timespec="seconds")}
            for e in log.all()
        ]
        self._write(self._events_path(day), rows)

    # ---- schedule and sensor names --------------------------------------

    @property
    def _config_path(self) -> Path:
        return self.dir / "config.json"

    def load_home(self) -> Home:
        if not self._config_path.exists():
            return Home()
        try:
            data = json.loads(self._config_path.read_text())
        except (json.JSONDecodeError, OSError):
            return Home()

        schedule = []
        for row in data.get("schedule", []):
            try:
                schedule.append((datetime.fromisoformat(row["at"]), str(row["what"])))
            except (KeyError, ValueError, TypeError):
                continue
        return Home(
            pill_box=data.get("pill_box", "pill_box"),
            front_door=data.get("front_door", "front_door"),
            kitchen_sensors=tuple(data.get("kitchen_sensors",
                                           ("fridge", "kettle", "microwave"))),
            kitchen_motion=data.get("kitchen_motion", "kitchen_motion"),
            schedule=schedule,
        )

    def save_home(self, home: Home) -> None:
        self._write(self._config_path, {
            "pill_box": home.pill_box,
            "front_door": home.front_door,
            "kitchen_sensors": list(home.kitchen_sensors),
            "kitchen_motion": home.kitchen_motion,
            "schedule": [
                {"at": when.isoformat(timespec="minutes"), "what": what}
                for when, what in home.schedule
            ],
        })

    def _write(self, path: Path, payload) -> None:
        """Write via a temporary file so a crash cannot leave a half-written day."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        tmp.replace(path)
