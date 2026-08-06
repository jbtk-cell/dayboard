"""The screen, and the door sensors knock on.

Runs on the machine in her house and nowhere else. Nothing leaves the building,
because a continuous record of when an elderly person opens her pill box, her
fridge and her front door is exactly the record that should not sit on somebody
else's server.

Three surfaces:

    GET  /         the screen she looks at. No buttons, no navigation, refreshes
                   itself. She never has to operate it or know it is a computer.
    GET  /audit    the caregiver view: every line, and the sensor events behind
                   it. This is where you check whether the thing is telling the
                   truth.
    POST /event    where sensors report. Anything that can make an HTTP request
                   works: ESPHome, Home Assistant automations, Shelly and
                   Tasmota webhooks, or a five-line script bridging Zigbee2MQTT.

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
from dayboard.events import Event, EventLog
from dayboard.rules import Home

_lock = threading.Lock()
_log = EventLog()
_home = Home()


def configure(home: Home) -> None:
    global _home
    _home = home


def record(sensor: str, kind: str, state: str, at: datetime | None = None) -> None:
    with _lock:
        _log.add(Event(sensor, kind, state, at or datetime.now()))


def current_board(now: datetime | None = None):
    with _lock:
        return build(_log, _home, now or datetime.now())


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

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            page = (Path(__file__).parent / "display.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
            return
        if self.path == "/api/board":
            payload = json.dumps(current_board().as_dict()).encode()
            self._send(200, payload, "application/json")
            return
        if self.path == "/audit":
            text = explain(current_board())
            self._send(200, text.encode(), "text/plain; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/event":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 4096:
            self._send(413, b"too large", "text/plain")
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            sensor = str(payload["sensor"])
            kind = str(payload.get("kind", "contact"))
            state = str(payload["state"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            self._send(400, f"bad event: {exc}".encode(), "text/plain")
            return

        stamp = payload.get("at")
        try:
            at = datetime.fromisoformat(stamp) if stamp else datetime.now()
        except ValueError:
            self._send(400, b"bad timestamp", "text/plain")
            return

        record(sensor, kind, state, at)
        self._send(202, b"recorded", "text/plain")


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"dayboard is showing at http://{host}:{port}")
    print("Sensor events:  POST /event      Caregiver view:  GET /audit")
    print("Everything stays on this machine. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
