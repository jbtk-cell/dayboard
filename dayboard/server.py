"""The screen, the console, and the door sensors knock on.

Runs on the machine in her house and nowhere else. Nothing leaves the building,
because a continuous record of when an elderly person opens her pill box, her
fridge and her front door is exactly the record that should not sit on somebody
else's server. Nothing is fetched from the internet either, which is why the
pages use system fonts and ship no assets.

    GET  /            the screen she looks at. No buttons, refreshes itself.
    GET  /console     where a caregiver adds events, appointments and sensors,
                      and scrubs through the day to see what she will see.
    GET  /audit       every line and the sensor events behind it, as plain text.
    POST /event       where sensors report. Anything that can make an HTTP
                      request works: ESPHome, Home Assistant, Shelly and Tasmota
                      webhooks, or a short Zigbee2MQTT bridge.

        curl -X POST localhost:8080/event \\
             -d '{"sensor":"pill_box","kind":"contact","state":"opened"}'

Stdlib only, so it runs on whatever cheap machine is already in the house.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dayboard.board import build, explain
from dayboard.events import (
    Event, EventLog, day_end, logical_date, part_of_day, spoken_time,
)
from dayboard.rules import Home
from dayboard.store import Store

_lock = threading.Lock()
_store: Store | None = None
_log = EventLog()
_home = Home()

# Demo mode pins the clock to a moment inside the simulated day. Without this
# the demo only worked if you happened to run it during daylight hours: the
# simulated events sat in the future relative to a real 1am, were correctly
# filtered out, and the screen showed nothing at all.
_frozen_now: datetime | None = None


def configure(home: Home, now: datetime | None = None, store: Store | None = None) -> None:
    global _home, _frozen_now, _store
    _home = home
    _frozen_now = now
    _store = store


def load_from(store: Store) -> None:
    """Adopt a persisted day. Used by `serve`, not by the demo."""
    global _log, _home, _store
    _store = store
    _home = store.load_home()
    _log = store.load_events(_clock())


def _clock() -> datetime:
    return _frozen_now or datetime.now()


def _persist() -> None:
    if _store is not None:
        _store.save_events(_log, _clock())
        _store.save_home(_home)


def record(sensor: str, kind: str, state: str, at: datetime | None = None) -> None:
    with _lock:
        _log.add(Event(sensor, kind, state, at or _clock()))
        _persist()


def current_board(now: datetime | None = None):
    with _lock:
        return build(_log, _home, now or _clock())


def _day_payload(at: datetime) -> dict:
    """Everything the console draws, for one moment in the day."""
    with _lock:
        board = build(_log, _home, at)
        events = _log.since_day_start(day_end(at))
        return {
            "board": board.as_dict(),
            "now": at.isoformat(timespec="minutes"),
            "part_of_day": part_of_day(at),
            "logical_day": logical_date(at).isoformat(),
            "events": [
                {
                    "sensor": e.sensor,
                    "kind": e.kind,
                    "state": e.state,
                    "at": e.at.isoformat(timespec="minutes"),
                    "clock": spoken_time(e.at),
                    "minutes": e.at.hour * 60 + e.at.minute,
                    "future": e.at > at,
                }
                for e in events
            ],
            "schedule": [
                {
                    "at": when.isoformat(timespec="minutes"),
                    "clock": spoken_time(when),
                    "minutes": when.hour * 60 + when.minute,
                    "what": what,
                }
                for when, what in sorted(_home.schedule)
            ],
            "sensors": {
                "pill_box": _home.pill_box,
                "front_door": _home.front_door,
                "kitchen_motion": _home.kitchen_motion,
                "kitchen_sensors": list(_home.kitchen_sensors),
            },
        }


def _parse_when(value: str | None, at: datetime) -> datetime:
    """Accept a full timestamp or a bare HH:MM against the day being viewed."""
    if not value:
        return at
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    hour, _, minute = value.partition(":")
    return at.replace(hour=int(hour), minute=int(minute or 0),
                      second=0, microsecond=0)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _page(self, name: str) -> None:
        self._send(200, (Path(__file__).parent / name).read_bytes(),
                   "text/html; charset=utf-8")

    def _at(self) -> datetime:
        _, _, query = self.path.partition("?")
        for part in query.split("&"):
            key, _, value = part.partition("=")
            if key == "at" and value:
                try:
                    return datetime.fromisoformat(value.replace("%3A", ":"))
                except ValueError:
                    break
        return _clock()

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._page("display.html")
        elif route == "/console":
            self._page("console.html")
        elif route == "/api/board":
            self._json(200, current_board(self._at()).as_dict())
        elif route == "/api/day":
            self._json(200, _day_payload(self._at()))
        elif route == "/audit":
            self._send(200, explain(current_board(self._at())).encode(),
                       "text/plain; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 8192:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self) -> None:
        route = self.path.split("?", 1)[0]
        try:
            body = self._body()
        except (json.JSONDecodeError, ValueError) as exc:
            self._json(400, {"error": f"bad request: {exc}"})
            return

        try:
            if route in ("/event", "/api/event"):
                at = _parse_when(body.get("at"), self._at())
                record(str(body["sensor"]), str(body.get("kind", "contact")),
                       str(body["state"]), at)
            elif route == "/api/event/delete":
                self._delete_event(int(body["index"]))
            elif route == "/api/schedule":
                self._add_schedule(body)
            elif route == "/api/schedule/delete":
                self._delete_schedule(int(body["index"]))
            elif route == "/api/sensors":
                self._set_sensors(body)
            else:
                self._send(404, b"not found", "text/plain")
                return
        except (KeyError, TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return

        self._json(200, _day_payload(self._at()))

    def _delete_event(self, index: int) -> None:
        with _lock:
            events = _log.since_day_start(day_end(self._at()))
            if not 0 <= index < len(events):
                raise ValueError("no such event")
            target = events[index]
            _log._events.remove(target)
            _persist()

    def _add_schedule(self, body: dict) -> None:
        when = _parse_when(str(body["at"]), self._at())
        what = str(body["what"]).strip()
        if not what:
            raise ValueError("an appointment needs a description")
        with _lock:
            _home.schedule.append((when, what))
            _home.schedule.sort()
            _persist()

    def _delete_schedule(self, index: int) -> None:
        with _lock:
            entries = sorted(_home.schedule)
            if not 0 <= index < len(entries):
                raise ValueError("no such appointment")
            _home.schedule.remove(entries[index])
            _persist()

    def _set_sensors(self, body: dict) -> None:
        with _lock:
            if "pill_box" in body:
                _home.pill_box = str(body["pill_box"]).strip() or _home.pill_box
            if "front_door" in body:
                _home.front_door = str(body["front_door"]).strip() or _home.front_door
            if "kitchen_motion" in body:
                _home.kitchen_motion = (
                    str(body["kitchen_motion"]).strip() or _home.kitchen_motion
                )
            if "kitchen_sensors" in body:
                names = [str(s).strip() for s in body["kitchen_sensors"] if str(s).strip()]
                if names:
                    _home.kitchen_sensors = tuple(names)
            _persist()


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"dayboard is showing at http://{host}:{port}")
    print(f"console:  http://{host}:{port}/console")
    print("Everything stays on this machine. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
