# Live Testing — Document Types Deep Learning

## Install

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
```

AutoGen is pinned to 0.7.5 in `requirements.txt`.

## Recommended run

```powershell
.\RUN_LEARN_DOCTYPES_DEEP.ps1 `
  -RunsDir "C:\hip_runs" `
  -InputJson ".\examples\uhaul_poasn_full_dummy_input.json"
```

Or directly:

```powershell
python -m hip_id_agent.cli learn-doctypes-deep `
  --config ".\config.yaml" `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --customer "HIP-DOCTYPE-DEEP-DISCOVERY" `
  --runs-dir "C:\hip_runs" `
  --require-mcp
```

Complete Dell SSO in the shared Chrome session when prompted.

## What should happen

The agent should first learn the Document Types listing surface, filters and pagination. It should then search the Source Document Type and Target Document Type from the current input, expand matching rows when present, inventory row actions, safely inspect read/draft surfaces, and safely probe mutation prerequisites.

After listing discovery it opens a fresh Create Document Type surface twice: once for the Source object and once for the Target object. It fills and verifies the complete unsaved form using the current `input.json`, observes parent-child DOM transitions and API traffic, promotes value-free replay topology, and closes the form without Save/Create/Submit.

## Main evidence

```text
<run>\doctype_deep_discovery_summary.json
<run>\deep_discovery\document_types\initial\
<run>\deep_discovery\document_types\filters\
<run>\deep_discovery\document_types\pagination\
<run>\deep_discovery\document_types\source_document_type\
<run>\deep_discovery\document_types\target_document_type\
```

For each Source/Target create-form exercise:

```text
create_form_parent_child\parent_child_dependency_blueprint.json
create_form_parent_child\controls_before_fill.structural.json
create_form_parent_child\stateful_form_execution.structural.json
create_form_parent_child\filled_surface.structural.json
create_form_parent_child\api\api_transactions_*.json
create_form_parent_child\create_form_exercise.json
```

Persistent reusable knowledge:

```text
data\hip_memory\portal_brain\capability_graph.json
```

## Expected replay profiles

After a successful live run, Portal Knowledge should show verified profiles for:

```text
create_source_document_type
create_target_document_type
```

The replay contains input paths and dependency metadata, not the actual Document Type names or customer values.

## Safety checks

If a mutation-grade row action is inspected, verify its evidence says `blocked_before_backend: true` and includes the blocked request where applicable.

The create-form exercise must report:

```text
save_create_submit_executed: false
closed_without_save: true
```

A live Dell portal result still depends on the current private HIP UI, SSO session, permissions and backend behavior; this package cannot certify those conditions outside the Dell environment.
