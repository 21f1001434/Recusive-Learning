# Document Types Deep Capability Learning — 2026-08-25

## Goal

Extend the HIP Browser Intelligence Platform from Data Maps into Document Types so the browser agent learns not only listing controls and row actions, but the complete unsaved Create Document Type interaction contract for both Source and Target objects in `input.json`.

## Implemented runtime

New module: `hip_id_agent/doctype_deep_discovery.py`.

The mission uses one authenticated shared Chrome session and combines:

- Python Playwright as the deterministic browser owner.
- Playwright MCP, Chrome DevTools MCP and HIP Intelligence MCP through the required MCP profile.
- AutoGen AgentChat 0.7.5 + AgentQ via the existing browser/session controller.
- The current dependency-aware Document Type state graph and exact-commit form runtime.
- Persistent HIP Capability Graph v4.
- UI→API network capture with request/response evidence in run artifacts.

## Listing intelligence

The learner opens Document Types and records:

1. Search controls.
2. Filter controls and the currently exposed option labels without selecting a filter.
3. Pagination controls and, when available, a safe Next → Previous validation transition.
4. Search and exact-row restoration for both `objects.source_document_type` and `objects.target_document_type`.
5. Row expansion.
6. Revealed Edit/Clone/View/Details/History/Audit/Migrate/Deploy/Delete capabilities.

Dynamic IDs remain current-run evidence only; semantic role/label/section/row scope is the reusable identity.

## Safe row-action inspection

Read/draft surfaces such as Edit, Clone, View, Details, History and Audit may be opened, structurally captured and closed without Save.

Migrate/Deploy/Delete are probed only behind a Playwright `route("**/*")` barrier. POST/PUT/PATCH/DELETE plus mutation-token GET requests are aborted before backend delivery. The probe records structural prerequisites and any generated request payload shape.

## Complete parent-child form learning

For each of Source and Target Document Type:

1. Return to the Document Types listing.
2. Open Add/Create using a temporary authorization limited to the entry-point click.
3. Keep a network-abort barrier active during that entry-point click so an unexpected backend create request cannot be delivered.
4. Compile the current input into `compile_document_type_state_graph`.
5. Persist a value-free dependency blueprint.
6. Execute `execute_document_type_state_graph` against the unsaved create surface.
7. Rebind controls after every Angular/DDS rerender.
8. Verify exact committed values.
9. Observe parent→child mounts and repeated rows.
10. Capture API traffic caused during the fill.
11. Promote only structural field/action/dependency metadata into the capability graph.
12. Close the form without Save/Create/Submit.

The hierarchy includes:

- Document Type Details
  - Name
  - Transaction Type
  - Data Format Type
  - Version verification
  - Status
  - Description
- Document Identifier
  - Operation
  - repeated rows
    - Derived From
    - conditional Value child
- Attributes To Configure
  - repeated input-driven rows
    - Attribute Name
    - Derived From
    - Usage exact multi-select set
    - conditional Expression child
- Validation
  - Validation Type

Rows remain sequential: row N is resolved and verified before row N+1 is permitted to advance.

## Capability Graph v4

The graph schema is now `hip.capability-graph.v4`.

Replay steps can persist additional value-free form metadata:

- `input_path`
- `section`
- `row_kind`
- `row_index`
- `depends_on`
- `verification`

They reference runtime values via `value_source` / `input_path`; no actual customer values are stored in replay memory.

Verified profiles are produced separately for:

- `create_source_document_type`
- `create_target_document_type`

A profile is verified only when the complete unsaved state-graph execution passes exact verification.

## Frontend/backend integration

New CLI command:

`python -m hip_id_agent.cli learn-doctypes-deep ...`

New FastAPI endpoint:

`POST /api/document-types/deep/start`

New Streamlit control:

`Deep Learn Document Types`

Runs are identified in the Runs / Console tab using `has_doctype_deep`.

## Safety

This milestone does not weaken mutation safety.

- Save/Create/Submit are never executed by the deep form exercise.
- Add/Create is authorized only to open an unsaved form and is protected by a network-abort barrier during the click.
- Migrate/Deploy/Delete probes abort all possible mutation requests before backend delivery.
- Future real mutations still require the separate three-part task authorization gate.

## Test result

Complete project inventory after this milestone: **558 tests**.
