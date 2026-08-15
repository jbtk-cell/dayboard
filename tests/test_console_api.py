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

# Set by the fixture, for the few tests that need to reach the server directly
# rather than through the `call` helper.
BASE = ""


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

    global BASE
    BASE = base
    yield call, store
    httpd.shutdown()
    server.configure(store.load_home(), now=None, store=None)
    server._log.__init__()
    server._health.clear()


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


class TestTheBridgeReachesTheScreen:
    """The whole chain, with only the broker left out: zigbee2mqtt payloads go
    through the real translator and the real HTTP client into the real server,
    and what comes out is the sentence she reads off the wall."""

    def _run(self, day):
        from dayboard.bridge import Client, Device, Translator

        translator = Translator({"pill box": Device("pill_box")})
        client = Client(BASE, server._store.token)
        for moment, payload in day:
            for event in translator.translate("pill box", payload, moment):
                assert client.send("/event", dict(event, at=moment.isoformat()))
        return translator

    def test_a_morning_of_real_payloads_becomes_one_sentence(self, api):
        call, _store = api
        day = [
            (datetime(2026, 8, 6, 4, 0), {"contact": True, "battery": 92}),
            (datetime(2026, 8, 6, 8, 15), {"contact": False, "battery": 92}),
            (datetime(2026, 8, 6, 8, 16), {"contact": True, "battery": 92}),
        ]
        self._run(day)
        _s, board = call("GET", f"/api/board?at={AT}", token=None)
        assert board["lines"] == ["Your pill box was opened at 8:15am."]

    def test_a_bouncing_lid_does_not_reach_her_as_five_doses(self, api):
        call, _store = api
        day = [(datetime(2026, 8, 6, 4, 0), {"contact": True})]
        day += [
            (datetime(2026, 8, 6, 8, 15, second), {"contact": bool(second % 2)})
            for second in range(0, 6)
        ]
        self._run(day)
        _s, board = call("GET", f"/api/board?at={AT}", token=None)
        assert board["lines"] == ["Your pill box was opened at 8:15am."]

    def test_an_unauthorised_bridge_changes_nothing(self, api):
        """A device on the wifi running this same code, without the token."""
        from dayboard.bridge import Client, Device, Translator

        translator = Translator({"pill box": Device("pill_box")})
        client = Client(BASE, "not-the-token")
        translator.translate("pill box", {"contact": True}, datetime(2026, 8, 6, 4, 0))
        for event in translator.translate("pill box", {"contact": False},
                                          datetime(2026, 8, 6, 8, 15)):
            assert client.send("/event", event) is False

        call, _ = api
        _s, board = call("GET", f"/api/board?at={AT}", token=None)
        assert board["lines"] == []


class TestSensorHealth:
    """The quietest failure this system has: a coin cell dies, the screen stops
    mentioning the pill box, and silence is the designed safe state -- so
    nothing looks wrong while the whole thing gradually stops working."""

    def _row(self, day, sensor):
        return next(r for r in day["health"] if r["sensor"] == sensor)

    def test_an_event_proves_the_sensor_is_alive(self, api):
        call, _ = api
        _s, day = call("POST", f"/api/event?at={AT}",
                       {"at": "08:15", "sensor": "pill_box", "state": "opened"})
        assert self._row(day, "pill_box")["since"] == "just now"
        assert self._row(day, "pill_box")["quiet"] is False

    def test_a_battery_report_is_not_something_that_happened_in_her_day(self, api):
        call, _ = api
        status, _b = call("POST", f"/api/health?at={AT}",
                          {"sensor": "pill_box", "battery": 84})
        assert status == 200
        _s, day = call("GET", f"/api/day?at={AT}")
        assert day["events"] == []                      # not in the record
        assert day["board"]["lines"] == []              # not on her screen
        assert self._row(day, "pill_box")["battery"] == 84

    def test_a_low_battery_is_flagged(self, api):
        call, _ = api
        _s, day = call("POST", f"/api/health?at={AT}",
                       {"sensor": "pill_box", "battery": 7})
        assert self._row(day, "pill_box")["low_battery"] is True

    def test_a_healthy_battery_is_not(self, api):
        call, _ = api
        _s, day = call("POST", f"/api/health?at={AT}",
                       {"sensor": "pill_box", "battery": 90})
        assert self._row(day, "pill_box")["low_battery"] is False

    def test_a_sensor_that_stopped_talking_is_flagged(self, api, tmp_path):
        """Written straight to the store, because the point is the gap."""
        call, store = api
        store.save_health({"pill_box": {"last_seen": "2026-08-01T09:00:00",
                                        "battery": 55}})
        server.load_from(store)
        server.configure(store.load_home(), now=datetime(2026, 8, 6, 13, 0),
                         store=store)
        _s, day = call("GET", f"/api/day?at={AT}")
        row = self._row(day, "pill_box")
        assert row["quiet"] is True
        assert row["since"] == "5 days ago"

    def test_liveness_is_measured_against_now_not_the_scrubber(self, api):
        """Scrubbing the console back to 7am must not make a live sensor look
        as though it went quiet, nor a dead one look fine."""
        call, _ = api
        call("POST", f"/api/health?at={AT}", {"sensor": "pill_box", "battery": 60})
        _s, early = call("GET", "/api/day?at=2026-08-06T07:00")
        assert self._row(early, "pill_box")["since"] == "just now"

    def test_health_needs_the_token(self, api):
        call, _ = api
        status, _b = call("POST", f"/api/health?at={AT}",
                          {"sensor": "pill_box", "battery": 5}, token=None)
        assert status == 401

    def test_health_survives_a_restart(self, api, tmp_path):
        call, _ = api
        call("POST", f"/api/health?at={AT}", {"sensor": "pill_box", "battery": 33})
        assert Store(tmp_path).load_health()["pill_box"]["battery"] == 33


class TestTheEdgesOfTheSite:
    """Small things, all of which someone standing at the tablet runs into."""

    def test_a_mistyped_address_offers_a_way_back(self, api):
        """She or a caregiver fat-fingers the URL on a tablet with no keyboard
        shortcuts and no back button in kiosk mode. A bare "not found" string
        is a dead end."""
        call, _ = api
        status, body = call("GET", "/wrong-page", token=None)
        assert status == 404
        assert b"The screen is at" in body
        assert b'href="/"' in body

    def test_a_sensor_posting_to_the_wrong_route_still_gets_plain_text(self, api):
        """No person reads this one, so it should not be a web page."""
        call, _ = api
        status, body = call("POST", "/api/nonsense", {})
        assert status == 404
        assert body == b"not found"

    def test_there_is_a_favicon(self, api):
        """Without it every page load asked for /favicon.ico and got a 404, and
        the tablet home-screen shortcut had nothing to show."""
        call, _ = api
        for path in ("/favicon.ico", "/favicon.svg"):
            status, body = call("GET", path, token=None)
            assert status == 200
            assert body.startswith(b"<svg")

    def test_her_screen_has_a_heading_element(self, api):
        """The day is the page's heading. It was a div, so the screen had no
        heading at all."""
        call, _ = api
        _s, page = call("GET", "/", token=None)
        assert b'<h1 class="heading"' in page
        assert page.count(b"<h1") == 1
        assert b'<html lang="en">' in page


class TestHeaders:
    def test_the_page_may_not_reach_the_internet(self, api, tmp_path):
        """The privacy promise should be enforced by the browser, not the README."""
        call, store = api
        # reach the server directly so response headers are visible
        _s, _b = call("GET", "/api/board", token=None)
        import urllib.request
        with urllib.request.urlopen(BASE + "/") as resp:
            csp = resp.headers.get("Content-Security-Policy", "")
            perms = resp.headers.get("Permissions-Policy", "")
            assert "default-src 'none'" in csp
            assert "connect-src 'self'" in csp
            assert "frame-ancestors 'none'" in csp
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"
            assert resp.headers.get("Referrer-Policy") == "no-referrer"
            assert "camera=()" in perms and "microphone=()" in perms
