# V243R33: WebMCP, the tools a page offers the agent, used to complete the task (2026-09-30)

## What was asked

- "Let's integrate WebMCP in this to help the agent to complete the task."

## What WebMCP is

WebMCP is a proposal of the W3C Web Machine Learning Community Group. It lets a web page expose **tools** to an AI agent through `navigator.modelContext`. Each tool has a name, a description and a JSON input schema.

- **Imperative:** `navigator.modelContext.registerTool({name, description, inputSchema, annotations, execute})` or `provideContext({tools})`.
- **Declarative:** a form becomes a tool, `<form toolname="…" tooldescription="…" [toolautosubmit]>`. Its fields (`toolparamtitle`, `toolparamdescription`) are the tool's parameters.
- **Agent side:** Chrome's early preview (behind a flag) exposes `navigator.modelContextTesting.listTools()` and `executeTool(name, argsJson)`.

A page's own tool is the page's contract. Using it is faster and sturdier than driving every field by hand.

## What the agent does now

### 1. A WebMCP layer on every page

`hip_id_agent/webmcp.py` adds a script to the browser context before any page script runs, so it is on every page, also after a browser restart (R28, R32). The same script is added late to a page opened earlier.

- **With native WebMCP** (Chrome's preview): tools are listed through `modelContextTesting`, and what the page registers is mirrored.
- **Without it** (the bundled Chromium 141, today's Edge and Chrome): a standards-shaped polyfill provides `navigator.modelContext`. A page that feature-detects WebMCP registers its tools anyway.
- **Declarative forms** (`form[toolname]`) are read as tools: the parameters come from the fields, the enums from the options, and a placeholder option is left out.
- **Nothing on the page changes.**

### 2. The agent's own in-page tools

These are private to the agent; they are never given to the page's context.

| Tool | Kind | What it does |
|---|---|---|
| `hip_page_state` | read-only | Route and title; whether a Whitelabel Error Page, a blocking loader or an open dropdown is showing; the wizard tabs; the active form. |
| `hip_read_form` | read-only | Every field of the active form: label, name, type, committed value or chips, checked, required, invalid. |
| `hip_form_matches` | read-only | Which expected values the form holds exactly: matched, missing, complete. |
| `hip_open_tab` | navigation | Shows a wizard tab by its label. |
| `hip_fill_text` | form edit | Types a plain text field found by its label. Saves nothing; dropdowns stay with the DDS driver. |

### 3. Every tool is classified; mutating tools need the gate

The classes are `read_only`, `navigation`, `form_edit` and `mutating`. A tool is `mutating` when:
- its name has a save / submit / deploy / delete / publish / validate … verb, or a create / update / edit verb that does not only fill a form;
- its description says it saves;
- it is marked `destructiveHint`;
- it is a declarative form with `toolautosubmit`;
- it is anything unknown (fail closed).

A mutating tool runs only with the three-part mutation gate: `--allow-portal-mutation`, `HIP_ALLOW_PORTAL_MUTATION=YES` and the phrase `ALLOW HIP MUTATION`. A mission's fill never calls one; Save stays the governed UI commit. A tool that asks for a person (`requestUserInteraction`) is not run autonomously.

### 4. It completes the task with the page's tool, and input.json decides

Before its first fill cycle, the goal engine looks for a page tool that can take this phase's input.json values:
- it must be a form-edit tool;
- every required parameter must be given;
- it must cover at least 60% of the values (`webmcp.min_input_coverage`).

Values are mapped by parameter name or title (`profile_name` ↔ `profileName` / "Profile Name"), and rows onto array parameters. When the page lists its options (`enum`), the page's own spelling wins: "Move to Archive" becomes "Move To Archive", and "…IB(1.0)" becomes "…IB (1.0)". A value the page does not offer is not sent.

After one call of the best tool, the live form is proved read-only against input.json (R32):
- **exact:** the phase is complete (`completed_by: webmcp_page_tool_then_input_json_proof`), and no field is driven one by one;
- **not exact** (a missing parameter, or a buggy tool): the normal fill completes the form, and the proof has named what was wrong.

The page's tool list is also given to the model's live observation, and recorded in the cycle audit and in `webmcp_page_tools.json`. That file holds tool and parameter names only, never values.

### 5. See what a page offers

- **CLI:** `python -m hip_id_agent.cli webmcp-tools` opens every phase link and lists its tools, in a table and in `webmcp_tools.json`.
  - `--phases`: which phase links to open;
  - `--url`: open a given URL instead;
  - `--call <tool> --args '{…}'`: call one tool; a mutating tool also needs the gate.
  - `--browser-executable <chromium>`: a local check with a plain browser session and no MCP servers; leave it empty on the Dell machine.
- **Control Center:** a *WebMCP tools* tile and `GET /api/webmcp` show the policy and the page tools the latest run found (or which page tool filled the form).

## Proof

Tests in `tests/test_v243r33_webmcp.py` (10). New replicas are in `tests/webmcp_portal_support.py`: a Transport Profile page with its own tools, a declarative form page, and a stand-in for Chrome's native API.

- **The page's `fill_transport_profile_form`:**
  - one call filled the form, and the proof matched 14 of 14 values;
  - the phase was complete in about 4 s, against about 30 s field by field;
  - no field was driven one by one, and `save_transport_profile` was never called.
- **A buggy page tool** (wrong Profile Name): the proof named `profile_name`, and the normal fill corrected it.
- **Discovery:** `registerTool` tools and the agent's five in-page tools are listed and classified. The read-only tool returns its JSON.
  - `save_transport_profile` is refused without the gate and nothing is saved;
  - with the gate, it runs.
- **Declarative form:**
  - parameters, titles, descriptions, enums and required fields are read;
  - it fills the text, select, radio and checkbox fields and never submits, even when asked to;
  - the `toolautosubmit` form is mutating and is refused.
- **Native WebMCP:** the stand-in `navigator.modelContextTesting` is used (no polyfill), and page tools come from it.
- **In-page tools:** page state (tabs; the Whitelabel page), read form, fill text, form matches (matched / missing) and open tab all work.
- **Every page:** the layer is on every page of a real browser session, also after a restart.
- **CLI:** `webmcp-tools` on the replica lists the page's three tools and the agent's five (polyfill), calls the read-only tool, and refuses `save_transport_profile` without the gate.

## Settings (all have defaults)

```yaml
webmcp:
  enabled: true                 # HIP_WEBMCP=off switches it off
  inject_polyfill: true
  agent_tools: true
  use_page_tools_for_fill: true
  min_input_coverage: 0.6
  tool_timeout_seconds: 20
  max_calls_per_phase: 20
  settle_ms: 400
```

## Honest scope

The Dell HIP portal pages may not register WebMCP tools today. Run `webmcp-tools` to see.

The layer, the agent's in-page tools and the classification work now. The page-tool fill is used on its own as soon as a page offers a fitting tool. Whether a page tool is used or not, input.json and the live proof stay the judge.
