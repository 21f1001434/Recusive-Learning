# V233 Stage 1 — Deep Website Understanding Before Action

## Goal

Before any HIP action is selected, the agent builds a current-generation, read-only website model. It does not rely on prior screenshots, fixed selectors or remembered coordinates.

## Implemented in Stage 1

`hip_id_agent/website_understanding.py` adds `WebsiteUnderstandingEngine`.

For the current HIP page it discovers:

- URL/path/title/readiness and framework hints (Angular/DDS/React)
- viewport, scroll, devicePixelRatio and browser-window geometry
- foreground surface stack (modal/drawer/form/tab panel/main) with z-index/layout scoring
- interactive controls and their semantic labels, sections, roles, types, required/disabled/read-only state
- FormControl/FormArray identity where present
- control layout and active-surface membership
- ARIA ownership (`aria-controls`, `aria-owns`, `aria-labelledby`)
- detached DDS/CDK popup ownership graph
- currently mounted dropdown/menu options, selected/disabled/visible state
- tabs, accordions, repeated/FormArray regions, forms and tables
- recent UI events and DOM mutations from the existing maximum-observability observer
- actual registered event-listener **types** using Chromium CDP `DOMDebugger.getEventListeners` when available
- action affordances such as fill, select, toggle, tab activation, expand, upload and mutation candidate

No action is performed by this engine.

## Foreground isolation

The surface model scores visible dialogs/drawers above underlying table/detail content. Each control has `inActiveSurface` so later action stages can reject controls that are visible but belong to an underlying expanded record.

This specifically addresses the Dell HIP pattern where the listing/detail page remains mounted behind a Create drawer on the same route.

## Dropdown / Process Step understanding

Every combobox with `aria-controls`/`aria-owns` is linked to the owned popup. Mounted options are catalogued by semantic control key.

Example learned structure:

```text
Configure Routing
  Process Step [combobox]
    owns -> process-step-list
      Translation
      Passthrough
      Split
```

The option catalog is observational. Stage 1 does not yet make the autonomous option-selection policy authoritative; that comes in the next action-planning/execution stage.

## Event understanding

Two complementary event sources are used:

1. Existing maximum-observability capture records real UI events and DOM mutations that occur during the run.
2. On Chromium/Edge, a read-only CDP session queries `DOMDebugger.getEventListeners` for foreground candidate controls and records only event types/counts/flags (e.g. `click`, `change`, capture/passive/once). Handler code and customer values are not persisted.

## Integration

The new website model is captured automatically by:

- `MaximumObservabilityCollector`
- `PortalLearningRuntime`

Artifacts written per capture include:

- `website_understanding.json`
- `website_option_catalog.json`
- `website_action_catalog.json`

The maximum-observability manifest also reports website-understanding availability, pass/fail and confidence.

## Safety / memory contract

Stage 1 is read-only.

It does not promote:

- customer-entered values
- CSS/XPath selectors as long-term memory
- generated Angular/DDS IDs as long-term memory
- screen coordinates or bounding boxes as long-term memory

Selectors/tokens/bounds in the snapshot are current-generation debugging evidence only.

## Configuration

The following defaults are now present in all three shipped YAML configs:

```yaml
portal_learning:
  deep_website_understanding_enabled: true
  website_understanding_max_controls: 3000
  website_understanding_max_options_per_control: 500
  website_understanding_max_events: 500
  capture_registered_event_listener_types: true
  website_understanding_event_listener_control_limit: 160
  website_understanding_min_confidence: 0.72
```

## Stage boundary

Stage 1 answers: **What is on the current page, which surface is authoritative, what controls/options/events exist, and what can the agent potentially do?**

The next stage should expose this live model in a Browser-Use-style Agent View and make the planner show the selected candidate plus rejected candidates/reasons before execution.
