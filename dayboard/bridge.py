"""Zigbee sensors, translated into the only thing the screen accepts.

The parts list in the README is Zigbee, because a $12 contact sensor on a pill
box lid runs for a year on a coin cell and no wifi device does. Zigbee sensors
cannot make HTTP requests, so something has to sit between them and the screen.
That is this: zigbee2mqtt on one side, `POST /event` on the other.

Two things in here are safety code rather than plumbing, and both come from the
same fact -- a reed switch is a piece of metal, not a witness.

**Polarity is inverted and easy to get backwards.** Zigbee2mqtt's `contact`
field is true when the magnet is NEAR the switch, which is when the lid is
SHUT. Reading it the obvious way round would announce a dose every time she
closed the box: wrong, and wrong in the direction that hurts.

**One movement makes many messages.** A nudged lid, or a magnet sitting at the
edge of the switch's range, produces a burst of open/close reports. Forwarded
naively, a single opening of the pill box becomes "your pill box was opened five
times today", which reads as five doses to the person least able to check.

The filter for that is stated rather than timed, because the physical claim is
stronger than any threshold: you cannot open a lid twice without shutting it in
between. So an opening only counts when the lid was believed shut. A short floor
on the interval catches bounce that arrives as complete open/close pairs, and
the believed state is updated even when an event is suppressed, so a burst can
never leave the bridge out of step with the lid.

That filter is in tension with the project's own rule that a repeat must never
be collapsed into one event, and the tension is resolved in favour of the rule:
the floor is ten seconds, far below any interval in which a person could open a
box, take a tablet, shut it and open it again. `tests/test_bridge.py` holds the
double-dose day against it, half an hour apart, and both openings survive.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_BROKER = "127.0.0.1"
DEFAULT_MQTT_PORT = 1883
DEFAULT_BASE_TOPIC = "zigbee2mqtt"
DEFAULT_DAYBOARD = "http://127.0.0.1:8080"

# A hand cannot open a lid twice inside this. Anything quicker is the switch
# bouncing, not a person. Kept small on purpose: the cost of setting it high is
# that a genuine second dose gets hidden, which is the failure this whole
# project exists to prevent.
MIN_SECONDS_BETWEEN = 10.0

# Contact sensors report periodically even when nothing moves, which is what
# makes it possible to tell a quiet sensor from a dead one. Passing every one of
# those through would be pointless chatter, so liveness is reported at most this
# often per device.
HEALTH_EVERY = timedelta(minutes=10)

# Events held when the server cannot be reached, flushed when it comes back. A
# pill box opening dropped because the screen was restarting is exactly the kind
# of silent hole this project cannot have.
MAX_HELD = 500

CONTACT, MOTION, POWER = "contact", "motion", "power"


@dataclass
class Device:
    """One physical sensor, and what it is watching.

    `sensor` is the name dayboard knows it by, which is what the console's
    Sensors panel sets. The zigbee2mqtt friendly name is the key this is stored
    under, so the two can differ -- and they usually do, because zigbee2mqtt
    names tend to arrive as "0x00158d0001a2b3c4".
    """

    sensor: str
    kind: str = CONTACT
    watts: float = 15.0          # power: above this the appliance is in use
    min_seconds: float = MIN_SECONDS_BETWEEN

    @classmethod
    def from_json(cls, row: dict) -> "Device":
        return cls(
            sensor=str(row["sensor"]),
            kind=str(row.get("kind", CONTACT)),
            watts=float(row.get("watts", 15.0)),
            min_seconds=float(row.get("min_seconds", MIN_SECONDS_BETWEEN)),
        )


def state_of(device: Device, payload: dict) -> str | None:
    """What this payload says the sensor is now. None if it says nothing useful.

    Battery and link-quality reports arrive on the same topic as state changes,
    so most messages land here and produce nothing.
    """
    if device.kind == CONTACT:
        contact = payload.get("contact")
        if contact is None:
            return None
        # True means the magnet is near the switch: the lid is shut.
        return "closed" if contact else "opened"

    if device.kind == MOTION:
        occupancy = payload.get("occupancy")
        if occupancy is None:
            return None
        return "motion" if occupancy else "still"

    if device.kind == POWER:
        watts = payload.get("power")
        if watts is None:
            return None
        try:
            return "on" if float(watts) >= device.watts else "off"
        except (TypeError, ValueError):
            return None

    return None


@dataclass
class Translator:
    """Turns a stream of zigbee2mqtt messages into events worth recording.

    Holds the believed state of each sensor, which is the whole trick: a message
    that agrees with what we already believe is not an event, and a burst of
    disagreeing ones is a bouncing switch rather than a busy morning.
    """

    devices: dict[str, Device]
    believed: dict[str, str] = field(default_factory=dict)
    last_change: dict[str, datetime] = field(default_factory=dict)
    battery: dict[str, int] = field(default_factory=dict)

    def translate(self, friendly_name: str, payload: dict,
                  now: datetime) -> list[dict]:
        device = self.devices.get(friendly_name)
        if device is None:
            return []  # a device nobody mapped is not this program's business

        level = payload.get("battery")
        if isinstance(level, (int, float)):
            self.battery[device.sensor] = int(level)

        state = state_of(device, payload)
        if state is None:
            return []

        if device.sensor not in self.believed:
            # First word from this device since the bridge started. Zigbee2mqtt
            # publishes the state it last knew when something subscribes, so
            # this is a snapshot, not a transition -- and a lid left open
            # overnight would otherwise be announced as an opening that
            # happened at breakfast, which is a dose she never took.
            #
            # It costs a real opening if one lands in the same instant as a
            # restart. That failure is silence, which is this project's designed
            # safe state; the other is a false line she cannot check.
            self.believed[device.sensor] = state
            return []

        changed = self.believed.get(device.sensor) != state
        # Update the belief even when the event is dropped, so a bounce cannot
        # leave the bridge convinced the lid is open when it is shut -- which
        # would swallow the next real opening.
        self.believed[device.sensor] = state
        if not changed:
            return []

        previous = self.last_change.get(device.sensor)
        self.last_change[device.sensor] = now
        if previous is not None and (now - previous).total_seconds() < device.min_seconds:
            return []

        return [{"sensor": device.sensor, "kind": device.kind, "state": state}]


class Client:
    """Posts to dayboard, and holds on to what it could not deliver."""

    def __init__(self, base_url: str, token: str, opener=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._opener = opener or urllib.request.urlopen
        self.held: deque[tuple[str, dict]] = deque(maxlen=MAX_HELD)

    def _post(self, path: str, payload: dict) -> None:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json",
                     "X-Dayboard-Token": self.token},
        )
        with self._opener(request, timeout=5) as response:
            response.read()

    def send(self, path: str, payload: dict) -> bool:
        """Queue, then deliver everything held, oldest first.

        Going through the queue even when it is empty keeps the day in order:
        an event that arrives while older ones are still undelivered must not
        overtake them, or the console shows the box being closed before it was
        opened.
        """
        self.held.append((path, payload))
        while self.held:
            path_next, payload_next = self.held[0]
            try:
                self._post(path_next, payload_next)
            except (urllib.error.URLError, OSError):
                return False
            self.held.popleft()
        return True


def load_config(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    devices = {
        name: Device.from_json(row)
        for name, row in data.get("devices", {}).items()
    }
    return {
        "broker": data.get("broker", DEFAULT_BROKER),
        "port": int(data.get("port", DEFAULT_MQTT_PORT)),
        "base_topic": data.get("base_topic", DEFAULT_BASE_TOPIC),
        "dayboard": data.get("dayboard", DEFAULT_DAYBOARD),
        "username": data.get("username") or None,
        "password": data.get("password") or None,
        "devices": devices,
    }


STARTER_CONFIG = {
    "broker": DEFAULT_BROKER,
    "port": DEFAULT_MQTT_PORT,
    "base_topic": DEFAULT_BASE_TOPIC,
    "dayboard": DEFAULT_DAYBOARD,
    "username": "",
    "password": "",
    "devices": {
        "pill box": {"sensor": "pill_box", "kind": CONTACT},
        "fridge": {"sensor": "fridge", "kind": CONTACT},
        "front door": {"sensor": "front_door", "kind": CONTACT},
        "kitchen motion": {"sensor": "kitchen_motion", "kind": MOTION},
        "kettle plug": {"sensor": "kettle", "kind": POWER, "watts": 100},
    },
}


def write_starter_config(path: Path) -> Path:
    """The five devices in the README's parts list, named as they ship.

    The keys are zigbee2mqtt friendly names and will not match a real setup
    until they are renamed in zigbee2mqtt or corrected here.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(STARTER_CONFIG, indent=2) + "\n")
    return path


def say(*message) -> None:
    """Print so it actually arrives.

    Under systemd, or any redirect, stdout is block-buffered and nothing
    reaches the journal until the buffer fills -- which for a few lines an hour
    is never. `journalctl -u dayboard-bridge -f` is the documented way to find
    out which physical sensor is which, so a silent log makes the one setup
    step that needs watching impossible to watch.
    """
    print(*message, flush=True)


def run(config: dict, token: str, log=say) -> int:
    """Subscribe, translate, forward. Blocks until interrupted."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log("The bridge needs paho-mqtt:  uv sync --extra bridge")
        return 1

    translator = Translator(config["devices"])
    client = Client(config["dayboard"], token)
    base = config["base_topic"].rstrip("/")
    last_health: dict[str, datetime] = {}

    def on_connect(_client, _userdata, _flags, reason_code, _properties=None):
        if reason_code != 0:
            log(f"mqtt refused the connection: {reason_code}")
            return
        _client.subscribe(f"{base}/+")
        log(f"listening to {base}/+ on {config['broker']}, "
            f"forwarding to {config['dayboard']}")
        for name, device in sorted(config["devices"].items(),
                                   key=lambda kv: kv[1].sensor):
            log(f"  {name:<24} -> {device.sensor} ({device.kind})")

    def on_message(_client, _userdata, message):
        name = message.topic[len(base) + 1:]
        if "/" in name:
            return  # zigbee2mqtt/bridge/state and friends, not a device
        try:
            payload = json.loads(message.payload.decode() or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return

        now = datetime.now()
        for event in translator.translate(name, payload, now):
            delivered = client.send("/event", event)
            log(f"{now:%H:%M:%S}  {event['sensor']} {event['state']}"
                f"{'' if delivered else '  (held: dayboard unreachable)'}")

        device = config["devices"].get(name)
        if device is None:
            return
        # Liveness. A coin cell dying is this system's quietest failure: the
        # screen simply stops mentioning the pill box, silence is its designed
        # safe state, and nobody notices for a week.
        due = last_health.get(device.sensor)
        if due is None or now - due >= HEALTH_EVERY:
            last_health[device.sensor] = now
            client.send("/api/health", {
                "sensor": device.sensor,
                "battery": translator.battery.get(device.sensor),
            })

    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if config.get("username"):
        mqtt_client.username_pw_set(config["username"], config.get("password"))
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(config["broker"], config["port"], keepalive=60)
    try:
        mqtt_client.loop_forever()
    except KeyboardInterrupt:
        log("\nstopped")
    return 0
