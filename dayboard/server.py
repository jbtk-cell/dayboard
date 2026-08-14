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
import secrets
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

# Caps on anything a caller can name. The appointment text goes onto her wall
# screen verbatim, so an unbounded string is not just untidy, it destroys the
# one display she relies on.
MAX_SENSOR_NAME = 64
MAX_STATE = 32
MAX_APPOINTMENT = 120

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


def _bounded(value, limit: int, what: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{what} cannot be blank")
    if len(text) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return text


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
        # default-src 'none' is doing real work here rather than ticking a box:
        # it makes "nothing leaves the building" a property the browser enforces,
        # not a promise in a README. The camera and microphone denials likewise
        # hold the line the design already drew.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; "
            "img-src 'self' data:; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy",
                         "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _page(self, name: str) -> None:
        self._send(200, (Path(__file__).parent / name).read_bytes(),
                   "text/html; charset=utf-8")

    def _authorised(self) -> bool:
        """Writes and the full day log need the shared token; her screen does not.

        The split is deliberate. `/` and `/api/board` show the four lines she can
        read off the wall anyway, and the tablet showing them cannot hold a
        secret. `/api/day`, `/audit` and every write expose or change the whole
        record, which is where both the privacy and the safety risk live.
        """
        if _store is None:
            return True  # demo and tests run without a store, so without a token
        expected = _store.token
        supplied = self.headers.get("X-Dayboard-Token", "")
        if not supplied:
            _, _, query = self.path.partition("?")
            for part in query.split("&"):
                key, _, value = part.partition("=")
                if key == "token":
                    supplied = value
                    break
        return secrets.compare_digest(supplied, expected)

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
            if not self._authorised():
                self._send(401, b"Open the console with the link printed when "
                                b"dayboard started.", "text/plain")
                return
            self._page("console.html")
        elif route == "/api/board":
            self._json(200, current_board(self._at()).as_dict())
        elif route == "/api/day":
            if not self._authorised():
                self._json(401, {"error": "token required"})
                return
            self._json(200, _day_payload(self._at()))
        elif route == "/audit":
            if not self._authorised():
                self._send(401, b"token required", "text/plain")
                return
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
        if not self._authorised():
            self._json(401, {"error": "token required"})
            return
        try:
            body = self._body()
        except (json.JSONDecodeError, ValueError) as exc:
            self._json(400, {"error": f"bad request: {exc}"})
            return

        try:
            if route in ("/event", "/api/event"):
                at = _parse_when(body.get("at"), self._at())
                record(_bounded(body["sensor"], MAX_SENSOR_NAME, "sensor name"),
                       _bounded(body.get("kind", "contact"), MAX_STATE, "kind"),
                       _bounded(body["state"], MAX_STATE, "state"), at)
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
        what = _bounded(body["what"], MAX_APPOINTMENT, "an appointment description")
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
            for field in ("pill_box", "front_door", "kitchen_motion"):
                raw = str(body.get(field, "")).strip()
                if raw:
                    setattr(_home, field,
                            _bounded(raw, MAX_SENSOR_NAME, f"{field} name"))
            if "kitchen_sensors" in body:
                names = [
                    _bounded(s, MAX_SENSOR_NAME, "kitchen sensor name")
                    for s in body["kitchen_sensors"] if str(s).strip()
                ]
                if names:
                    _home.kitchen_sensors = tuple(names)
            _persist()


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    shown = "localhost" if host in ("0.0.0.0", "") else host
    # flush: under systemd or a redirect this is block-buffered, and the
    # token would never reach whoever needs it.
    print(f"her screen:  http://{shown}:{port}", flush=True)
    if _store is not None:
        print(f"console:     http://{shown}:{port}/console?token={_store.token}", flush=True)
        print(f"sensors:     send header  X-Dayboard-Token: {_store.token}", flush=True)
    else:
        print(f"console:     http://{shown}:{port}/console", flush=True)
    print("Everything stays on this machine. Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
