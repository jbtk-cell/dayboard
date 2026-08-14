# dayboard

A screen that answers the question "what have I already done today?" for someone
whose memory no longer does.

It is built for one person in particular: my great-grandmother, who forgets to do
things, forgets what she has just done, and loses track of where things are. She
cannot operate a phone, an app or a wearable, because operating anything requires
remembering that it exists. So this is a screen on a wall that she never touches,
never charges and never has to understand. She looks at it the way she looks at a
clock.

```
  Thursday afternoon

  Your pill box was opened at 8:15am.
  It looks like you had breakfast.
  Sarah is coming at 4:00pm.
```

## The one rule everything else follows

She cannot check whether the screen is telling the truth. That is the entire
reason the screen exists, and it is what makes getting this wrong dangerous
rather than merely annoying. If it says "you took your pills" and she did not,
she believes it and skips a dose. If it says she has not taken them when she has,
she takes a second one.

So the screen never says anything it cannot support.

A reed switch on a pill box lid detects a lid moving. It does not detect a
person swallowing medication. Those are different facts, and the gap between
them is exactly where somebody gets hurt. The screen therefore says:

> Your pill box was opened at 8:15am.

which is true, is enough for her to act on, and never pretends to knowledge the
sensors do not have. Every statement carries the kind of support it has:

| basis | what it means | how it reads |
|---|---|---|
| observed | a sensor fired, and the sentence describes that event | "Your pill box was opened at 8:15am." |
| inferred | several signals together look like an activity | "It looks like you had breakfast." |
| scheduled | it came from the calendar; nothing sensed it | "Sarah is coming at 4:00pm." |

Two prohibitions are absolute and enforced by tests:

**It never states a negative.** "You have not eaten today" requires knowing that
no meal happened. A sensor network cannot know that; it can only fail to notice.
Absence of evidence is not evidence of absence, and a false negative here tells
someone to eat a second lunch or take a second dose. When nothing is known, the
screen stays silent.

**It never counts a repeat as a single event.** If the pill box was opened twice,
the screen says twice, with both times. Tidying the second opening away to keep
the display clean would hide the single event most likely to hurt her.

## Try it now, with no hardware

```sh
uv sync
uv run python -m dayboard.cli show --scenario ordinary
uv run python -m dayboard.cli show --scenario double-dose
uv run python -m dayboard.cli show --scenario quiet
```

`show` prints both the screen and the audit trail underneath it, so you can see
what supports each line. Four scenarios ship, chosen because they are the ones
easy to get wrong rather than the ones that flatter the screen:

- `ordinary` — a normal day
- `double-dose` — the box opened twice, half an hour apart
- `quiet` — nothing has happened, and the screen must not say so
- `ambiguous-door` — a door opened, and no sensor can tell arrival from departure

For the real screen in a browser:

```sh
uv run python -m dayboard.cli demo --scenario double-dose
# open http://127.0.0.1:8080
```

The demo pins its clock to 1pm of the simulated day, so it shows the same thing
whatever time you run it. `--hour 8` to see the same day earlier on, before
lunch has happened. In `serve` the clock is real, and it comes from the server
rather than the browser, so the time on screen can never disagree with the
events the screen is reporting.

## The console

```sh
uv run python -m dayboard.cli serve
# her screen:  http://localhost:8080
# the console: http://localhost:8080/console
```

The console is where everything is set up and nothing is guessed at. It is laid
out as a medication chart, because that is the paper artifact it replaces and
because a drug chart already makes the distinction this project is built on:
what was observed, and what was merely noted.

- **Today's record** is every sensor event, with times in a monospace column.
  Rows can be added by hand to try something out, or removed. A pill box opened
  more than once is flagged `repeat`.
- **Coming up** is the appointments she will see, written in the words she would
  use.
- **Sensors** maps your sensor ids onto what they watch, so `meds_tin` works as
  well as `pill_box`.
- **Still reporting** is which sensors have said anything lately, and what their
  batteries are down to. This is the maintenance panel, and it exists because a
  flat coin cell is this system's quietest failure: the screen simply stops
  mentioning the pill box, silence is its designed safe state, so nothing looks
  wrong while the whole thing gradually stops working. A sensor that has said
  nothing for 36 hours is flagged, because Zigbee devices report battery and
  link quality on their own every few hours. None of it reaches her screen. She
  cannot act on a battery percentage, and the value of those four lines is that
  every one of them is worth reading.
- **What she sees** is the actual screen, live, beside the controls.
- **What supports each line** shows every line with its basis and the exact
  sensors behind it. If a line is on her screen, this says why.

The scrubber under the preview runs the whole day. Drag it and the screen shows
what she would see at that moment, with tick marks where events landed. It is
the fastest way to answer the question that matters when setting this up: what
will she see at three in the afternoon if the pill box never opens?

The day is written to `~/.dayboard` as it changes, one file per day, so a reboot
at noon does not erase the morning.

## Putting it in a house

```sh
git clone https://github.com/jbtk-cell/dayboard
cd dayboard
sudo ./install.sh --bridge
```

That copies the code to `/opt/dayboard`, builds a virtualenv, makes a system
user that owns nothing else, installs two systemd services, starts them, and
prints the two links you need. It comes back by itself after a power cut, which
is the only reliability property that matters in a house where nobody is going
to be reading logs.

The day is kept in `/var/lib/dayboard`, owner-only, deliberately not in anyone's
home directory. `sudo ./install.sh --uninstall` takes it all off and leaves the
record alone.

### Telling it which sensor is which

The bridge listens to [zigbee2mqtt](https://www.zigbee2mqtt.io) and forwards
what it hears. Its config is `/var/lib/dayboard/bridge.json`, written on first
run with the five devices from the parts list below:

```json
{
  "broker": "127.0.0.1",
  "devices": {
    "pill box":       { "sensor": "pill_box",       "kind": "contact" },
    "kitchen motion": { "sensor": "kitchen_motion", "kind": "motion" },
    "kettle plug":    { "sensor": "kettle", "kind": "power", "watts": 100 }
  }
}
```

The keys are zigbee2mqtt friendly names, which arrive looking like
`0x00158d0001a2b3c4` until you rename them. The values are the ids dayboard
knows, which the console's Sensors panel sets. Then
`sudo systemctl restart dayboard-bridge`, and `journalctl -u dayboard-bridge -f`
shows every sensor as it reports, which is the fastest way to find out that the
thing you labelled the fridge is the front door.

Nothing has to go through Zigbee. Anything that can make an HTTP request can
report directly, which covers ESPHome, Home Assistant, Shelly and Tasmota:

```sh
curl -X POST http://<host>:8080/event \
     -H "X-Dayboard-Token: $(sudo cat /var/lib/dayboard/token)" \
     -d '{"sensor":"pill_box","kind":"contact","state":"opened"}'
```

### The screen itself

A tablet is the cheap way: open the first link, set the display to never sleep,
add it to the home screen. If you would rather drive a monitor from the Pi,
`./deploy/kiosk.sh` sets up Chromium full screen with the blanking turned off.

Five surfaces: `/` is the screen, `/console` is where it is set up, `/audit` is
the caregiver view as plain text, `/event` is where sensors report, and
`/health` is where they say they are still alive. All but the screen need the
token.

Nothing leaves the building. A continuous record of when an elderly woman opens
her pill box, her fridge and her front door is precisely the record that should
not be sitting on somebody else's server.

## What the bridge has to get right

A reed switch is a piece of metal, not a witness, and two things in that gap are
safety code rather than plumbing.

**Polarity is inverted.** Zigbee2mqtt's `contact` field is true when the magnet
is *near* the switch, which is when the lid is *shut*. Read the obvious way
round, dayboard would announce a dose every time she closed the box.

**One movement makes many messages.** A nudged lid produces a burst of
open/close reports, and forwarded naively a single opening becomes "your pill
box was opened five times today" — five doses, to the person least able to
check. The filter for that is stated rather than timed, because the physical
claim is stronger than any threshold: you cannot open a lid twice without
shutting it in between, so an opening only counts when the lid was believed
shut. A ten-second floor catches bounce arriving as complete open/close pairs.

That filter is in direct tension with this project's own rule that a repeat must
never be collapsed into one event, and the tension is resolved in favour of the
rule. Ten seconds is far below any interval in which a person opens a box, takes
a tablet, shuts it and opens it again. The double-dose day is held against the
whole chain in the tests, half an hour apart, and both openings survive.

**A lid already open when the bridge starts is not an opening.** Zigbee2mqtt
replays the last state it knew as soon as anything subscribes. Treating that as
a transition would turn a lid left open overnight into a dose taken at
breakfast, so the first message from a device is adopted silently. The cost is
one real opening if it lands in the same instant as a restart; that failure is
silence, which is this project's designed safe state.

## Security

This listens on a home network and holds a health-adjacent record of a
vulnerable person. Four things follow from that, and most of a normal web
checklist does not.

**Writing needs a token.** Without one, anything on the wifi can POST
`pill_box opened` and her screen will tell her she took a dose she never took.
Every other rule in this project is about not asserting more than the sensors
support; an open write endpoint hands that guarantee to whatever else is on the
network. A token is made on first run, printed at startup, and kept owner-only
in `~/.dayboard/token`. Sensors send it as `X-Dayboard-Token`.

Her screen is deliberately exempt. `/` and `/api/board` show four lines anyone
standing in the room can already read, and the tablet on the wall cannot keep a
secret. `/api/day`, `/audit` and every write need the token, because those
expose or change the whole record.

**Values are never turned into markup.** Sensor names arrive from the network,
so anything that can reach the port chooses them. An earlier version
interpolated one into `innerHTML` in the console, which let a device on the wifi
run script in the caregiver's browser. Everything now goes through
`textContent`.

**Names and appointments are length-capped.** An appointment is printed on her
wall verbatim, so an unbounded string does not merely look untidy, it destroys
the one display she depends on.

**The headers enforce the privacy claim.** `default-src 'none'` with
`connect-src 'self'` means the browser blocks any outbound request, so "nothing
leaves the building" is checked rather than promised. `Permissions-Policy`
denies camera, microphone and geolocation, holding a line the design already
drew.

**The service runs with almost nothing.** It reads four HTML files and writes
one directory, and the systemd unit holds it to exactly that: no capabilities,
no access to any home directory, a read-only filesystem, and only the address
families it needs. This matters more than usual because the thing listens on a
home network full of cheap devices and nobody will be watching it after the
first week.

Not applicable, and worth saying why rather than pretending: there is no
database, so nothing to parameterize and no row-level security; no accounts, so
no passwords, sessions or login to rate-limit; no uploads; and no API keys or
secrets of any kind. The screen, the console and the server are stdlib only, so
the part that runs in the house has nothing to install and nothing to keep
patched; the bridge adds `paho-mqtt` and is the one optional extra. HTTPS is
deliberately absent: a self-signed certificate on a Pi teaches the household to
click through browser warnings, which is worse than the plain HTTP it replaces
on a network the token already gates.

Set against a real attacker with a foothold on the LAN this is modest. It is
proportionate to the actual risk, which is a cheap IoT device or a guest phone,
not a targeted adversary.

## What it costs

| part | for | roughly |
|---|---|---|
| Zigbee USB coordinator | talking to the sensors | $25 |
| 3 contact sensors | pill box lid, fridge, front door | $36 |
| motion sensor | kitchen presence | $12 |
| power-monitoring plug | kettle or microwave | $12 |
| Raspberry Pi Zero 2 W | running it | $15 |
| display | any old tablet propped on a shelf | $0 |

About $100, and less if you already have a spare tablet. An ESP32 with reed
switches is cheaper still if you would rather solder than buy.

## What this deliberately does not do

No cameras and no microphones. Both would work better and neither is acceptable
in the home of someone who cannot meaningfully consent to being recorded all day.

No wearable. She would take it off and lose it, which is one of the three
problems this is meant to help with.

No alerts to family. That is what the existing commercial products do, and it
solves the caregiver's problem rather than hers. This is person-facing on
purpose.

## Honest limitations

**It has not been used by a real person yet.** Everything below the code is
untested against the thing that matters.

**No real Zigbee sensor has ever reported to it.** The bridge is written against
the zigbee2mqtt message format and tested end to end against a real broker with
real payloads, but the payloads were typed by me rather than sent by a magnet on
a pill box lid. If something is wrong when the hardware arrives, look at
polarity first: `contact` true means shut, and getting that backwards produces a
screen that is confidently, dangerously inverted.

**It must never be load-bearing for medication.** A human stays in the loop for
anything that could hurt her. This is a memory aid, not a medical device, and it
should be treated as the former even after it starts working well.

**Inference is weak by design and still weaker than it looks.** Two kitchen
sensors firing in the breakfast window is evidence that someone was in the
kitchen. It is not evidence that she ate, and "It looks like you had breakfast"
is doing real work in that sentence.

**It assumes one person in the house.** A visiting nurse who opens the fridge
becomes, to this system, indistinguishable from her opening the fridge. In a
household with regular visitors the meal inference will be wrong sometimes, and
that is a reason to keep it phrased weakly.

**There is no clinical evidence that it helps.** Dementia day clocks, which show
only the date and time of day, are an established and popular product, and this
extends that idea to what has already happened. Whether the extension is
actually useful to her is an open question that only she can answer.

## Tests

```sh
uv sync --extra bridge
uv run pytest          # 105 tests
```

They are mostly not ordinary unit tests. Each one corresponds to a specific way
the screen could tell a person who cannot check it something that is not true,
including a fuzz pass that pushes 400 random event streams through the whole
pipeline and asserts that no unsafe sentence ever reaches the screen.

Three of them run the chain end to end with only the broker left out:
zigbee2mqtt payloads go through the real translator and the real HTTP client
into a real running server, and what is asserted is the sentence she would read
off the wall. One of those is the bouncing lid, and one is a device on the wifi
running this same bridge code without the token.
