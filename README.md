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
- **What she sees** is the actual screen, live, beside the controls.
- **What supports each line** shows every line with its basis and the exact
  sensors behind it. If a line is on her screen, this says why.

The scrubber under the preview runs the whole day. Drag it and the screen shows
what she would see at that moment, with tick marks where events landed. It is
the fastest way to answer the question that matters when setting this up: what
will she see at three in the afternoon if the pill box never opens?

The day is written to `~/.dayboard` as it changes, one file per day, so a reboot
at noon does not erase the morning.

## Running it for real

Point sensors at it. Anything that can make an HTTP request works:

```sh
curl -X POST http://<host>:8080/event \
     -d '{"sensor":"pill_box","kind":"contact","state":"opened"}'
```

Three surfaces: `/` is the screen, `/audit` is the caregiver view showing every
line and the sensor events behind it, `/event` is where sensors report.

Nothing leaves the building. A continuous record of when an elderly woman opens
her pill box, her fridge and her front door is precisely the record that should
not be sitting on somebody else's server.

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
uv run pytest          # 63 tests
```

They are mostly not ordinary unit tests. Each one corresponds to a specific way
the screen could tell a person who cannot check it something that is not true,
including a fuzz pass that pushes 400 random event streams through the whole
pipeline and asserts that no unsafe sentence ever reaches the screen.
