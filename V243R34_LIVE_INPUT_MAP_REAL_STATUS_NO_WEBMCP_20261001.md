# V243R34: the live input.json map, real learning status, faster fill, and no WebMCP (2026-10-01)

## What was asked

With a screenshot of the Control Center:

- "There is no WebMCP for the HIP Portal, so remove it."
- "Why is everything off and not learning and improving itself?"
- "Is it mapping the HIP portal input field values to the input.json, so it knows when to stop when everything is filled correctly? And once it knows everything, it should see what is happening in real time too, to complete fast and work excellently."

## 1. WebMCP removed

The HIP portal offers no WebMCP tools, so R33 is taken out completely:
- the `hip_id_agent/webmcp.py` module and its replicas and tests;
- the goal engine's page-tool pass and the browser-context script;
- the `webmcp` config section, the `webmcp-tools` command, `GET /api/webmcp` and the *WebMCP tools* tile.

R34's installer verifies against R32 and does not ship R33's files.

## 2. Why every tile said "Off": the status was failing, not the learning

Every learning feature was switched on: skill induction, replay policy, MLflow, model portfolio, recursive improvement, run-history learning, portal skills. They are on in the code defaults and in `config.yaml`.

The Control Center builds one status from about 30 parts. **One part that raised an error threw the whole status away.** The backend then answered "partial status" without any learning section, and the page showed each missing section as "Off … disabled". "AutoGen blocked" and "Learned from past runs: Not yet" had the same cause.

On your machine the likely trigger was R33's own WebMCP status part. It walked every run folder outside any error guard, and long Windows paths in deep run folders can make that walk raise.

Now:
- **Each part of the status is computed on its own.** A failing part returns its own error and nothing else is lost.
- The error and its traceback go to `.backend_runtime/runtime_status_errors.json`.
- The **badge** says *"Backend ready (N status parts failed)"*, and its tooltip names them.
- A tile shows **Off** only when the feature is really switched off. A failed part shows **Error** with the message, and a part the backend did not send shows "—".
- The AutoGen badge says *"AutoGen status unavailable"* instead of *"AutoGen blocked"* when its part failed.

This was tested by making two parts fail (MLflow and run-history learning): every other tile still reported, all of them on.

## 3. Yes: every input.json value is mapped to the live form, and you can now watch it

The agent already compared input.json with the live form to know when to stop (R29, R32). That comparison is now visible as the **live input.json map**.

For every input.json value it shows:
- the **form field** it maps to, by the form's own label ("System Name", "Profile Name");
- the **expected** value and the **live** value the form holds;
- the **state**:
  - ✓ **exact**: committed and equal, with the portal's spelling accepted ("Move to Archive" → "Move To Archive");
  - ≠ **different**: the field shows another value;
  - … **not on screen yet**: another tab, a section not opened, or a row not added;
  - ! **invalid**: the portal flags the field;
  - – **not checked**: an "off" value that requires nothing.

**How it runs:**
- It is refreshed every 5 s during a phase, read without touching the page. While a dropdown is open or the portal is loading, the last map is kept.
- It is written to `runs/<run>/input_json_live_map.json` and `runs/<run>/<phase>/input_json_live_map.json`.
- **Control Center:** Mission tab → *Live input.json ↔ HIP form*. It shows the phase, exact / total, the time it became complete, and every row.

**When it is complete** (every value exact), the mission trace records *"Every input.json value is on the form (14/14); no more filling"*:
- if the agent keeps filling fields anyway for 30 s, the attempt is stopped as complete and goes to verification without reopening the form;
- a complete form that is only finishing its checks and learning is left to finish.

The settings are `runtime_self_heal.live_map_seconds` (5) and `post_complete_fill_seconds` (30).

## 4. Faster, and still learning

- **One pass instead of two.** After the first pass, the goal engine used to fill the whole form a second time as its check. When the first pass succeeded and the live input.json proof is exact, that proof is now the check (`autonomous_form.single_pass_when_input_json_exact`). The success path, including skill and structure learning, runs as before.
- **No waiting for an observer that is not there.** After each dropdown the executor waits for the DOM change the agent's observer reports. On a page without that observer it used to wait the full timeout, about 5 s per dropdown. It now goes straight on to the control's own stability check. The live portal pages have the observer, so there the wait still ends at the first change.
- **On the Transport Profile replica:** the field-by-field fill went from about 86 s to about 25 s (about 6.5 s to about 1.5 s per dropdown), and the check pass from about 6 s to about 0.3 s.

## Proof

Tests in `tests/test_v243r34_live_input_map_and_status.py` (7):
- **WebMCP is gone everywhere:** the module, config, CLI, endpoint (404), UI and `config.yaml`.
- **Status:** a healthy status reports every learning part as on. With two parts failing, only those two report errors and everything else is intact; the traceback is logged and the UI handles both cases.
- **Live map** on the Transport Profile replica:
  - before the fill: 0 of 14, all not on screen;
  - a wrong Profile Name: *different*, with the live value shown;
  - after the fill: 14 of 14 exact, complete, with the form's labels and the radio and portal spellings, and "FALSE" not checked;
  - the in-flight probe returns the map;
  - the engine ran one cycle with the single pass, and the fill finished in under 60 s (was about 95 s).
- **DOM observer:** without one there is no wait (under 0.5 s); with one it still waits for the change.
- **Watchdog:** the live map refreshes on its own cadence. A complete form that keeps being refilled is stopped as complete; a complete form that is only finishing is not interrupted.
- **Mission and Control Center:** the mission writes the map, `GET /api/mission/live-input-map` serves it, and the panel renders it.
