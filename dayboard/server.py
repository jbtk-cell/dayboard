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
from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dayboard.board import build, explain
from dayboard.events import (
    Event, EventLog, day_end, logical_date, part_of_day, spoken_time,
)
from dayboard.rules import Home, default_dose_label
from dayboard.silence import explain_silence
from dayboard.store import Store

# Caps on anything a caller can name. The appointment text goes onto her wall
# screen verbatim, so an unbounded string is not just untidy, it destroys the
# one display she relies on.
MAX_SENSOR_NAME = 64
MAX_STATE = 32
MAX_APPOINTMENT = 120

# This system's quietest failure is a flat coin cell. The screen simply stops
# mentioning the pill box, silence is its designed safe state, and so nothing
# looks wrong -- the family just gradually stops being told anything. Zigbee
# sensors report battery and link quality on their own every few hours, so
# silence for this long means the device is gone rather than the cupboard being
# unopened.
#
# None of this reaches her screen. She cannot act on a battery percentage, and
# the value of those four lines is that every one of them is worth reading.
SILENT_AFTER_HOURS = 36
LOW_BATTERY = 20

# The screen in miniature: a dark card, the clock, two lines under it. Served
# from here rather than linked as a file, because the pages ship no assets and
# fetch nothing -- and because without it every single page load asked for
# /favicon.ico and got a 404. It matters on the tablet, where whoever sets this
# up puts it on the home screen and then has to find it again.
FAVICON = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="6" fill="#1a1a1a"/>'
    b'<rect x="6" y="6" width="15" height="7" rx="1.5" fill="#fbf8f2"/>'
    b'<rect x="6" y="18" width="20" height="3" rx="1.5" fill="#a9a294"/>'
    b'<rect x="6" y="24" width="13" height="3" rx="1.5" fill="#a9a294"/>'
    b'</svg>'
)

# Someone mistypes the address on the tablet and gets a dead end. This is a way
# back to the only page that matters, in the same face as the screen itself.
NOT_FOUND = (
    b'<!doctype html><html lang="en"><head><meta charset="utf-8">'
    b'<meta name="viewport" content="width=device-width, initial-scale=1">'
    b'<title>Not this page</title><link rel="icon" href="/favicon.svg">'
    b'<style>body{background:#fbf8f2;color:#1a1a1a;font-family:Georgia,serif;'
    b'display:flex;min-height:100vh;margin:0;padding:6vmin}'
    b'main{margin:auto;max-width:26ch}h1{font-size:clamp(1.6rem,7vmin,3rem);'
    b'font-weight:700;margin:0 0 .4em}p{font-size:clamp(1.1rem,4vmin,1.6rem);'
    b'line-height:1.4;margin:0}a{color:#1a1a1a}'
    b'@media(prefers-color-scheme:dark){body{background:#16150f;color:#f2ede3}'
    b'a{color:#f2ede3}}</style></head><body><main>'
    b'<h1>There is nothing here.</h1>'
    b'<p>The screen is at <a href="/">this address</a>.</p>'
    b'</main></body></html>'
)

COOKIE_NAME = "dayboard_token"

# What you get for arriving at /console without the token. The old text said to
# use "the link printed when dayboard started", which is no help at all once
# that terminal is closed -- which it is, on a machine screwed to a wall.
CONSOLE_LOCKED = (
    b'<!doctype html><html lang="en"><head><meta charset="utf-8">'
    b'<meta name="viewport" content="width=device-width, initial-scale=1">'
    b'<title>The console is locked</title><link rel="icon" href="/favicon.svg">'
    b'<style>body{background:#e7eee8;color:#14201a;margin:0;padding:6vmin;'
    b'font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;'
    b'display:flex;min-height:100vh}main{margin:auto;max-width:42ch}'
    b'h1{font-size:1.35rem;margin:0 0 .7em}p{line-height:1.55;margin:0 0 .9em}'
    b'code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.92em;'
    b'background:#f8fbf7;border:1px solid #c2d1c5;border-radius:3px;'
    b'padding:.5em .7em;display:block;margin:.5em 0 1.1em}'
    b'a{color:#3a5a8c}small{color:#56655d}</style></head><body><main>'
    b'<h1>The console needs its link.</h1>'
    b'<p>It is not password protected, it is link protected: anything on this '
    b'network can reach this port, and a faked pill box event would put a dose '
    b'on her screen that she never took.</p>'
    b'<p>To print the link again, on the machine running dayboard:</p>'
    b'<code>dayboard console --open</code>'
    b'<p>Installed as a service, the token is in '
    b'<code>/var/lib/dayboard/token</code></p>'
    b'<p><small><a href="/">Her screen is here</a>, and needs no link.</small></p>'
    b'</main></body></html>'
)

_lock = threading.Lock()
_store: Store | None = None
_log = EventLog()
_home = Home()
_health: dict[str, dict] = {}

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
    global _log, _home, _store, _health
    _store = store
    _home = store.load_home()
    _log = store.load_events(_clock())
    _health = store.load_health()


def _clock() -> datetime:
    return _frozen_now or datetime.now()


def _persist() -> None:
    if _store is not None:
        _store.save_events(_log, _clock())
        _store.save_home(_home)
        _store.save_health(_health)


def _note_health(sensor: str, battery=None) -> None:
    """Record that a sensor is alive. The caller holds the lock."""
    entry = _health.setdefault(sensor, {})
    entry["last_seen"] = _clock().isoformat(timespec="seconds")
    if battery is not None:
        entry["battery"] = max(0, min(100, int(battery)))


def note_health(sensor: str, battery=None) -> None:
    with _lock:
        _note_health(sensor, battery)
        _persist()


def record(sensor: str, kind: str, state: str, at: datetime | None = None) -> None:
    moment = at or _clock()
    with _lock:
        # An event dated before the day held in memory would be taken for a day
        # rollover and clear this morning on its way past. Rolling *forward* at
        # 4am is the intended behaviour and stays allowed. Going backwards is a
        # sensor with a wrong clock, or somebody adding a row while looking at
        # Tuesday, and either way it silently erases today.
        if _log.day is not None and logical_date(moment) < _log.day:
            raise ValueError(
                "that event is dated before the day on screen, and recording "
                "it would clear today")
        _log.add(Event(sensor, kind, state, moment))
        # An event proves the device is alive, so sensors that speak plain HTTP
        # get liveness without having to report anything extra.
        _note_health(sensor)
        _persist()


def current_board(now: datetime | None = None):
    with _lock:
        return build(_log, _home, now or _clock())


def _spoken_gap(hours: float) -> str:
    """How long ago, said the way a person would say it."""
    if hours < 0.05:
        return "just now"
    if hours < 1:
        return f"{int(hours * 60)} minutes ago"
    if hours < 24:
        count = int(hours)
        return f"{count} hour{'s' if count != 1 else ''} ago"
    days = int(hours // 24)
    return f"{days} day{'s' if days != 1 else ''} ago"


def _health_rows() -> list[dict]:
    """Which sensors are still talking. Measured against the real clock, not
    the moment the console happens to be scrubbed to -- whether a battery is
    flat is a fact about now, not about three o'clock this afternoon."""
    now = _clock()
    rows = []
    for sensor, entry in sorted(_health.items()):
        last = entry.get("last_seen")
        hours = None
        if last:
            try:
                hours = (now - datetime.fromisoformat(last)).total_seconds() / 3600
            except ValueError:
                hours = None
        battery = entry.get("battery")
        rows.append({
            "sensor": sensor,
            "last_seen": last,
            "since": _spoken_gap(hours) if hours is not None else "never",
            "battery": battery,
            "quiet": hours is None or hours >= SILENT_AFTER_HOURS,
            "low_battery": battery is not None and battery <= LOW_BATTERY,
        })
    return rows


def _log_for(at: datetime) -> EventLog:
    """The events of whatever logical day `at` falls in.

    Today's live log for today, and a day read back off disk for anything
    earlier. This is what makes "what did her screen actually say at seven on
    Tuesday" a question with an answer. The old days were already being kept on
    purpose; nothing had ever read them.
    """
    if _log.day is not None and logical_date(at) == _log.day:
        return _log
    if _store is None:
        return _log
    return _store.load_events(at)


def known_days() -> list[str]:
    """Every logical day there is a record of, newest first."""
    if _store is None:
        return []
    days = sorted(path.name[len("events-"):-len(".json")]
                  for path in _store.dir.glob("events-*.json"))
    return list(reversed(days))


def _day_payload(at: datetime) -> dict:
    """Everything the console draws, for one moment in one day."""
    at_day = logical_date(at)
    with _lock:
        log = _log_for(at)
        board = build(log, _home, at)
        events = log.since_day_start(day_end(at))
        return {
            "board": board.as_dict(),
            "now": at.isoformat(timespec="minutes"),
            "part_of_day": part_of_day(at),
            "logical_day": logical_date(at).isoformat(),
            "known_days": known_days(),
            "is_today": _log.day is None or logical_date(at) == _log.day,
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
            "health": _health_rows(),
            # Caregiver-only, and deliberately allowed to state negatives that
            # her screen never may. See the docstring in silence.py.
            "silence": [s.as_dict() for s in explain_silence(log, _home, at, _health)],
            "doses": [
                {
                    "at": at.isoformat(timespec="minutes"),
                    "clock": spoken_time(datetime.combine(at_day, at)),
                    "what": what or default_dose_label(at),
                    "named": bool(what),
                }
                for at, what in sorted(_home.doses)
            ],
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
        # HttpOnly: the console's own script cannot read this, which is stricter
        # than the JavaScript variable it replaces -- an injected script can no
        # longer walk off with the token. SameSite=Strict is what makes it safe
        # to authorise writes with, since no other origin can make the browser
        # send it. No Secure flag, because there is deliberately no HTTPS here.
        if getattr(self, "_grant_cookie", ""):
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={self._grant_cookie}; Path=/; Max-Age=31536000; "
                "HttpOnly; SameSite=Strict")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _page(self, name: str) -> None:
        self._send(200, (Path(__file__).parent / name).read_bytes(),
                   "text/html; charset=utf-8")

    def _token_in_query(self) -> str:
        _, _, query = self.path.partition("?")
        for part in query.split("&"):
            key, _, value = part.partition("=")
            if key == "token":
                return value
        return ""

    def _token_in_cookie(self) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME:
                return value
        return ""

    def _authorised(self) -> bool:
        """Writes and the full day log need the shared token; her screen does not.

        The split is deliberate. `/` and `/api/board` show the four lines she can
        read off the wall anyway, and the tablet showing them cannot hold a
        secret. `/api/day`, `/audit` and every write expose or change the whole
        record, which is where both the privacy and the safety risk live.

        Three ways to present it. Sensors send the header. A person follows the
        link, which carries it in the query once. After that the cookie carries
        it, because the alternative was that refreshing the console logged you
        out of it -- the token was stripped from the address bar on load and
        lived only in a JavaScript variable, so a reload, a bookmark or a
        reopened tab was a lockout with no way back but the terminal.
        """
        if _store is None:
            return True  # demo and tests run without a store, so without a token
        supplied = (self.headers.get("X-Dayboard-Token", "")
                    or self._token_in_query()
                    or self._token_in_cookie())
        return secrets.compare_digest(supplied, _store.token)

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
        # Only the request that carried the link may hand the cookie back. The
        # attribute lives on the handler, and a handler can serve more than one
        # request on a kept-alive connection.
        self._grant_cookie = ""
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._page("display.html")
        elif route in ("/favicon.svg", "/favicon.ico"):
            self._send(200, FAVICON, "image/svg+xml")
        elif route == "/console":
            if not self._authorised():
                self._send(401, CONSOLE_LOCKED, "text/html; charset=utf-8")
                return
            # Remember it, so this is the last time anyone needs the link.
            self._grant_cookie = self._token_in_query()
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
            # A person typed this. Give them the way back, not a bare string.
            self._send(404, NOT_FOUND, "text/html; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 8192:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self) -> None:
        self._grant_cookie = ""
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
            elif route in ("/health", "/api/health"):
                # Not an event: a device saying it is still there. Keeping it
                # out of the record is the point -- a battery report is not
                # something that happened in her day.
                battery = body.get("battery")
                note_health(
                    _bounded(body["sensor"], MAX_SENSOR_NAME, "sensor name"),
                    battery if isinstance(battery, (int, float)) else None,
                )
            elif route == "/api/event/delete":
                self._delete_event(int(body["index"]))
            elif route == "/api/schedule":
                self._add_schedule(body)
            elif route == "/api/schedule/delete":
                self._delete_schedule(int(body["index"]))
            elif route == "/api/doses":
                self._add_dose(body)
            elif route == "/api/doses/delete":
                self._delete_dose(int(body["index"]))
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

    def _add_dose(self, body: dict) -> None:
        """When her pills are due. A time of day, not a date: it repeats."""
        raw = str(body.get("at", "")).strip()
        try:
            hour, _, minute = raw.partition(":")
            at = time(int(hour), int(minute or 0))
        except ValueError:
            raise ValueError("a dose needs a time of day, like 08:00")
        label = str(body.get("what", "")).strip()
        if label:
            label = _bounded(label, MAX_STATE, "what to call the dose")
        with _lock:
            if any(existing == at for existing, _ in _home.doses):
                raise ValueError("there is already a dose at that time")
            _home.doses.append((at, label))
            _home.doses.sort()
            _persist()

    def _delete_dose(self, index: int) -> None:
        with _lock:
            if not 0 <= index < len(_home.doses):
                raise ValueError("no such dose")
            _home.doses.pop(index)
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


def lan_address() -> str:
    """The address other devices in the house can reach this on.

    Printing "localhost" was unhelpful in the case that matters: the screen is a
    tablet and the console is a phone, and neither of them is this machine. No
    packet is sent -- connecting a UDP socket only picks the route.
    """
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 1))  # TEST-NET-1, never routed anywhere
        return probe.getsockname()[0]
    except OSError:
        return "localhost"
    finally:
        probe.close()


def stable_address() -> str:
    """A name for this machine that survives a new DHCP lease. "" if there is none.

    The IP is the wrong thing to write on a tablet. A wall screen bookmarked to
    192.168.1.234 goes blank the day the router hands out .77 instead, or the
    day the house gets a new router -- and it goes blank for the one person in
    the building who cannot work out why, which is the whole population this is
    built for.

    Both macOS and Raspberry Pi OS publish <hostname>.local on the network
    without being asked, so there is usually a name to use instead.
    """
    import socket

    name = socket.gethostname().rstrip(".")
    if not name:
        return ""
    if not name.endswith(".local"):
        name = f"{name.split('.')[0]}.local"
    try:
        socket.gethostbyname(name)
    except OSError:
        return ""  # no mDNS here; the caller falls back to the address
    return name


def console_url(host: str = "", port: int = 8080) -> str:
    """The one link worth keeping."""
    where = host or stable_address() or lan_address()
    if _store is None:
        return f"http://{where}:{port}/console"
    return f"http://{where}:{port}/console?token={_store.token}"


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    if host in ("0.0.0.0", ""):
        # The name first, because it is the one that still works next month.
        shown = stable_address() or lan_address()
        fallback = lan_address() if shown != lan_address() else ""
    else:
        shown, fallback = host, ""

    # flush: under systemd or a redirect this is block-buffered, and the
    # token would never reach whoever needs it.
    print(f"her screen:  http://{shown}:{port}", flush=True)
    print(f"console:     {console_url(shown, port)}", flush=True)
    if fallback:
        print(f"also at:     http://{fallback}:{port}"
              f"   (if the name above does not resolve)", flush=True)
    if _store is not None:
        print(f"sensors:     send header  X-Dayboard-Token: {_store.token}", flush=True)
        print("The console link only has to be opened once per device.", flush=True)
    print("Everything stays on this machine. Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
