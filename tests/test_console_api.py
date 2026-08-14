"""The console's API: adding, removing and persisting a day.

Exercised over real HTTP against a real server on a temp directory, because the
failure that matters -- a reboot losing the morning -- only shows up when the
process actually restarts.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer

import pytest

from dayboard import server
from dayboard.store import Store

AT = "2026-08-06T13:00"


@pytest.fixture()
def api(tmp_path):
    store = Store(tmp_path)
    server.load_from(store)
    server.configure(store.load_home(), now=datetime(2026, 8, 6, 13, 0), store=store)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, token=store.token):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Dayboard-Token"] = token
        req = urllib.request.Request(
            base + path, data=data, method=method, headers=headers,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw.startswith(b"{") else raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, (json.loads(raw) if raw.startswith(b"{") else raw)

    TestHeaders._base = base
    yield call, store
    httpd.shutdown()
    server.configure(store.load_home(), now=None, store=None)
    server._log.__init__()


class TestEvents:
    def test_add_then_see_it_on_the_screen(self, api):
        call, _ = api
        status, day = call("POST", f"/api/event?at={AT}",
                           {"at": "08:15", "sensor": "pill_box", "state": "opened"})
        assert status == 200
        assert len(day["events"]) == 1
        assert "pill box was opened at 8:15am" in day["board"]["lines"][0]

    def test_remove_a_row(self, api):
        call, _ = api
        call("POST", f"/api/event?at={AT}",
             {"at": "08:15", "sensor": "pill_box", "state": "opened"})
        status, day = call("POST", f"/api/event/delete?at={AT}", {"index": 0})
        assert status == 200
        assert day["events"] == []
        assert day["board"]["lines"] == []

    def test_removing_a_row_that_is_not_there(self, api):
        call, _ = api
        status, body = call("POST", f"/api/event/delete?at={AT}", {"index": 7})
        assert status == 400
        assert "no such event" in body["error"]

    def test_an_event_missing_its_sensor_is_refused(self, api):
        call, _ = api
        status, _body = call("POST", f"/api/event?at={AT}", {"state": "opened"})
        assert status == 400

    def test_future_events_are_marked_not_hidden(self, api):
        """The console shows the whole day; the screen only shows what has happened."""
        call, _ = api
        _s, day = call("POST", f"/api/event?at={AT}",
                       {"at": "18:00", "sensor": "pill_box", "state": "opened"})
        assert day["events"][0]["future"] is True
        assert day["board"]["lines"] == []


class TestSchedule:
    def test_add_an_appointment(self, api):
        call, _ = api
        status, day = call("POST", f"/api/schedule?at={AT}",
                           {"at": "16:00", "what": "Sarah is coming"})
        assert status == 200
        assert day["schedule"][0]["what"] == "Sarah is coming"
        assert any("Sarah is coming at 4:00pm" in l for l in day["board"]["lines"])

    def test_an_appointment_needs_words(self, api):
        call, _ = api
        status, body = call("POST", f"/api/schedule?at={AT}", {"at": "16:00", "what": "  "})
        assert status == 400
        assert "description" in body["error"]

    def test_remove_an_appointment(self, api):
        call, _ = api
        call("POST", f"/api/schedule?at={AT}", {"at": "16:00", "what": "Sarah is coming"})
        _s, day = call("POST", f"/api/schedule/delete?at={AT}", {"index": 0})
        assert day["schedule"] == []


class TestSensors:
    def test_renaming_the_pill_box_changes_what_is_watched(self, api):
        call, _ = api
        call("POST", f"/api/sensors?at={AT}", {"pill_box": "meds_tin"})
        _s, day = call("POST", f"/api/event?at={AT}",
                       {"at": "08:15", "sensor": "meds_tin", "state": "opened"})
        assert "pill box was opened" in day["board"]["lines"][0]
        assert day["sensors"]["pill_box"] == "meds_tin"

    def test_blank_names_are_ignored_rather_than_applied(self, api):
        call, _ = api
        _s, day = call("POST", f"/api/sensors?at={AT}", {"pill_box": "   "})
        assert day["sensors"]["pill_box"] == "pill_box"


class TestPersistence:
    def test_the_morning_survives_a_restart(self, api, tmp_path):
        """A Pi reboot at noon must not erase whether she took her pills."""
        call, store = api
        call("POST", f"/api/event?at={AT}",
             {"at": "08:15", "sensor": "pill_box", "state": "opened"})
        call("POST", f"/api/schedule?at={AT}", {"at": "16:00", "what": "Sarah is coming"})

        reopened = Store(tmp_path)
        log = reopened.load_events(datetime(2026, 8, 6, 13, 0))
        home = reopened.load_home()
        assert len(log.since_day_start(datetime(2026, 8, 6, 23, 0))) == 1
        assert home.schedule[0][1] == "Sarah is coming"


class TestScrubbing:
    def test_the_board_changes_with_the_requested_time(self, api):
        call, _ = api
        call("POST", f"/api/event?at={AT}",
             {"at": "08:15", "sensor": "pill_box", "state": "opened"})
        _s, early = call("GET", "/api/day?at=2026-08-06T07:00")
        _s, later = call("GET", "/api/day?at=2026-08-06T09:00")
        assert early["board"]["lines"] == []
        assert later["board"]["lines"]
        assert early["board"]["clock"] == "7:00am"


class TestDayBoundaryInTheConsole:
    """Bugs found by screenshotting the console at 1am."""

    def test_record_and_screen_agree_on_which_day_it_is(self, api):
        """Between midnight and 4am the logical day is the previous date.
        `replace(hour=23)` lands on the NEXT logical day, so the console listed
        tomorrow's events beside today's screen."""
        call, _ = api
        call("POST", "/api/event?at=2026-08-06T13:00",
             {"at": "08:15", "sensor": "pill_box", "state": "opened"})
        _s, small_hours = call("GET", "/api/day?at=2026-08-06T01:06")
        # 01:06 on the 6th belongs to the 5th, which has no events at all.
        assert small_hours["events"] == []
        assert small_hours["board"]["lines"] == []

    def test_saving_uses_the_logs_day_not_the_wall_clock(self, api, tmp_path):
        """Adding an event while viewing another day must not write an empty file."""
        call, store = api
        call("POST", "/api/event?at=2026-08-06T13:00",
             {"at": "08:15", "sensor": "pill_box", "state": "opened"})
        written = sorted(p.name for p in tmp_path.glob("events-*.json"))
        assert written == ["events-2026-08-06.json"]
        reloaded = Store(tmp_path).load_events(datetime(2026, 8, 6, 13, 0))
        assert len(reloaded.all()) == 1


class TestAuthorisation:
    """Anything on the home network can reach this port. A fake pill box event
    would put a dose she never took onto her screen."""

    def test_writing_without_the_token_is_refused(self, api):
        call, _ = api
        status, body = call("POST", f"/api/event?at={AT}",
                            {"at": "08:15", "sensor": "pill_box", "state": "opened"},
                            token=None)
        assert status == 401
        assert body["error"] == "token required"

    def test_a_wrong_token_is_refused(self, api):
        call, _ = api
        status, _b = call("POST", f"/api/event?at={AT}",
                          {"at": "08:15", "sensor": "pill_box", "state": "opened"},
                          token="not-the-token")
        assert status == 401

    def test_the_refused_write_did_not_land(self, api):
        call, _ = api
        call("POST", f"/api/event?at={AT}",
             {"at": "08:15", "sensor": "pill_box", "state": "opened"}, token=None)
        _s, day = call("GET", f"/api/day?at={AT}")
        assert day["events"] == []

    def test_the_full_day_log_needs_the_token(self, api):
        call, _ = api
        status, _b = call("GET", f"/api/day?at={AT}", token=None)
        assert status == 401

    def test_her_screen_needs_no_token(self, api):
        """The tablet on the wall cannot hold a secret, and the four lines on it
        are visible to anyone already in the room."""
        call, _ = api
        status, board = call("GET", "/api/board", token=None)
        assert status == 200
        assert "lines" in board

    def test_the_token_can_be_passed_in_the_query(self, api):
        call, store = api
        status, _b = call("GET", f"/api/day?at={AT}&token={store.token}", token=None)
        assert status == 200


class TestInputLimits:
    def test_an_enormous_appointment_is_refused(self, api):
        """It would be rendered verbatim on her screen."""
        call, _ = api
        status, body = call("POST", f"/api/schedule?at={AT}",
                            {"at": "16:00", "what": "x" * 500})
        assert status == 400
        assert "longer than" in body["error"]

    def test_an_enormous_sensor_name_is_refused(self, api):
        call, _ = api
        status, body = call("POST", f"/api/event?at={AT}",
                            {"at": "08:15", "sensor": "s" * 500, "state": "opened"})
        assert status == 400
        assert "longer than" in body["error"]

    def test_a_blank_sensor_name_is_refused(self, api):
        call, _ = api
        status, _b = call("POST", f"/api/event?at={AT}",
                          {"at": "08:15", "sensor": "   ", "state": "opened"})
        assert status == 400


class TestHeaders:
    def test_the_page_may_not_reach_the_internet(self, api, tmp_path):
        """The privacy promise should be enforced by the browser, not the README."""
        call, store = api
        # reach the server directly so response headers are visible
        _s, _b = call("GET", "/api/board", token=None)
        import urllib.request
        base = TestHeaders._base
        with urllib.request.urlopen(base + "/") as resp:
            csp = resp.headers.get("Content-Security-Policy", "")
            perms = resp.headers.get("Permissions-Policy", "")
            assert "default-src 'none'" in csp
            assert "connect-src 'self'" in csp
            assert "frame-ancestors 'none'" in csp
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"
            assert resp.headers.get("Referrer-Policy") == "no-referrer"
            assert "camera=()" in perms and "microphone=()" in perms
