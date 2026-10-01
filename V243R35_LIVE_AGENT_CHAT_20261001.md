# V243R35: the live agent chat (2026-10-01)

## What was asked

"It should also be like a live chat interaction on the right, with the live actions the agent is taking — what it clicks and what is happening."

## What you get

A **Live agent** panel docked on the right of the Control Center, on every tab.
- On screens narrower than 1350 px it is a drawer: open it with the **💬 Live agent** button, which shows the number of new lines.
- On a phone it takes the full width.

From top to bottom, the panel shows:
- **Status.** A dot (green: working, amber: paused, red: blocked) and what the agent is doing ("Working on Source Transport Profile (attempt 2)").
- **The agent's browser, live.** A frame every 3 s, read-only. Click it to enlarge.
- **Progress.** The phase and the live input.json count ("8/14 on the form"), with a bar.
- **A question from the agent, when it has one.**
  - "Is Source Transport Profile correct?" with **Accept** and **Needs correction** buttons.
  - "I need help finding 2 fields", which opens the Teach panel.
  - "Paused", with **Resume**.
- **The conversation.** Every action the agent takes, as it takes it, with your messages in between.
- **Quick buttons** (Status, What's left?, Pause, Resume, Stop, Help) and a message box. Enter sends; Shift+Enter adds a new line.

### What the agent says

From a real fill of the Transport Profile replica, in order:

```
▶ Working on Source Transport Profile: Filling the form
Filling System Type with “Dell Application”
Opened the System Type dropdown
Selected “Dell Application” in System Type
✓ System Type = “Dell Application” — verified on the form
Filling System Name with “AIC - DCE”
…
Typed “SFTP_U-HAUL_ASN_PC_SRC_IB” into Profile Name
Filling Existing Account with “Yes”
Chose “Yes” for Existing Account
Live check: 9/14 input.json values are on the form
Selected “Move To Archive” in Post Transfer Action
…
Every input.json value is on the form (14/14); no more filling
✅ Source Transport Profile is complete
🏁 Mission complete — 1/1 phases done
```

It also says:
- which page it opened and which buttons it clicked ("Opened hip.dell.com/…", "Clicked “Create”");
- which tab it switched to, and each retry ("Retrying Interface Type (try 2)");
- a field it could not commit, and why;
- each self-heal step, for example "♻ Self-heal (whitelabel error page): closing the browser, opening a fresh one and starting this stage again; then I continue from input.json";
- a watchdog stop: "🛑 Every input.json value is on the form — I stop filling and go on to verification";
- a blocked phase, with the reason.

Fields are named by the form's own labels:
- "System Name" where input.json says `partner_name`;
- a radio is named by its question ("Existing Account"), not by its option ("Yes").

Values are masked when they are secrets. Selectors, coordinates and model reasoning are never shown or stored.

Similar lines fold into one bubble with "+N similar": fields already correct, key presses, live counts.

### What you can say

| You type | What happens |
|---|---|
| `status` (or "what are you doing?") | Answered at once: the phase and attempt, the live count, and the last thing the agent did. |
| `what's left?` | Every input.json value not yet on the form: the label, the expected value, and what the form shows instead or "not on screen yet". |
| `pause` | The agent finishes the field in hand, then waits, and says so: "⏸ Paused before Profile Name". Paused time does not count against the no-progress watchdog or the phase budget. |
| `resume` | The agent continues and says so: "▶ Resuming before Profile Name (paused 42s)". This also wakes a process paused with the Pause button. |
| `stop` | Asks for confirmation, then stops the mission process, like the Stop button. Stopping saves nothing on the portal. |
| `accept` / `looks correct`, `reject` / `needs correction` | Answers the phase review the agent is waiting on. |
| anything else, e.g. "Interface Type is on the Connection tab" | A **hint**. The agent acknowledges it in the chat when it picks it up ("📝 Got your note …") and gives the form planner the recent hints when it consults it. A hint never changes an input.json value and never authorizes Save / Submit / Deploy. |
| `help` | The list above. |

## How it works

- **The mission narrates.** Every line goes to `runs/<run>/agent_chat.jsonl`. The file is append-only and the mission is its only writer, so a line costs one small append. It comes from:
  - the form executor: each field's start, retry, verified and failed;
  - the action broker: each click, typing, selection and key press, for the field in hand;
  - the browser session: pages, buttons and menus;
  - the mission trace: phases, hand-offs, observations and warnings;
  - self-heal, the watchdog and the wall budget;
  - the live input.json map.

  The Transport Profile replica still fills in about 28 s.
- **The operator's side.** The backend writes the operator's side to `runs/<run>/operator_chat.jsonl`, and pause/resume to `runs/<run>/operator_control.json`. The control file records:
  - when the current pause began;
  - the total of finished pauses, so a pause counts in full however late the agent looks.

  The file is replaced atomically, so the agent never reads half of it.
- **Safe points.** The agent checks for a pause and for new hints between fields and before each attempt.
- **The live frame** is a read-only screenshot (`agent_chat/live_frame.jpg`). It injects nothing into the page, so the agent's DOM observer sees nothing.
- **The Control Center** polls `GET /api/mission/chat?cursor=…` every 1.5 s, receives only the new lines from both sides, and keeps the log scrolled to the newest line unless you scroll up.
- **Endpoints.** Messages go to `POST /api/mission/chat`; the frame comes from `GET /api/mission/chat/frame`.
- **Missions started elsewhere.** A mission started outside the Control Center (in a terminal) is followed too: a run in progress that is still narrating counts as running.

Settings (`config.yaml`):

```yaml
agent_chat:
  enabled: true            # narrate every action
  live_frame_seconds: 3.0  # 0 turns the live frame off
  live_frame_quality: 55
  pause_poll_seconds: 0.5
```

## Also fixed

On a phone-width screen, the Mission tab's "Input-driven + row plan" table widened the whole page. It now scrolls inside its panel.

## Proof

Tests in `tests/test_v243r35_live_agent_chat.py` (11):
- **A real replica fill is narrated in order, by the form's labels:** open, select, type, verified; radios by their question; 15 fields verified; phase and mission completion. There are no selectors in the chat, and the fill took under 60 s (about 28 s).
- **Broker and session lines:** dropdowns, search typing, options, a failed click with its reason, tabs, pages and buttons. Secrets are masked, and the session stays quiet while the broker narrates.
- **A pause holds the agent between fields:**
  - an 8 s pause did not trip a 4 s no-progress watchdog;
  - the field in hand finished first;
  - the agent said "Paused before Profile Name" and then "Resuming".
- **Paused time does not count against the wall budget:**
  - 2 s of work in a 1 s budget times out, but completes when 1.6 s of it is paused;
  - a pause that began and ended between two reads still counts in full.
- **The conversation through the Control Center API:**
  - the history, and incremental reads by cursor;
  - status, what's left, pause, resume;
  - a hint the agent then acknowledges, and the planner receives it;
  - review accept with no review, then a real one, resolved;
  - stop and help;
  - the live frame.
- **The planner** gets the hints as advice only.
- **Self-heal** says what it does, and the watchdog says why it stopped.
- **The live frame** is a JPEG, and capturing it causes zero DOM mutations on the page.
- **Wiring:** the mission wires all of this; the Control Center has the panel (both UI copies identical); the config defaults.

Demo with the Control Center open (`chat_demo.py`, real engine fill):
- a "status" question was answered mid-fill;
- "pause" held the agent before the next field, with the banner and amber dot;
- a hint was acknowledged by the agent;
- after "resume", the fill finished at 14/14;
- "what's left?" answered "Nothing is left";
- on a phone there is no horizontal scroll.
