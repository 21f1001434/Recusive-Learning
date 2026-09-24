# Rerun Guide — Universal HIP Form Policy and Same-Flow Memory

## 1. Replace the package

Extract the complete package over a clean folder. Do not merge Python bytecode or an old virtual environment into the new source tree.

## 2. Keep the persistent memory directory

Do not delete the configured Portal Brain directory between normal runs:

```yaml
reporting:
  memory_dir: "./data/hip_memory"
brain:
  directory: "portal_brain"
```

Validated patterns will be stored under:

```text
./data/hip_memory/portal_brain/flow_patterns/
```

Use `--rebuild-portal-brain` only when intentionally resetting learned knowledge.

## 3. Run the same command

No additional command-line flag is needed. The existing `--portal-brain`, `--self-heal-kb`, section-judge and MCP flags continue to control learning and promotion.

## 4. Verify startup artifacts

At run root, verify:

```text
all_hip_form_family_catalog.json
all_phase_form_interaction_policy.json
flow_pattern_memory_bootstrap.json
portal_brain_bootstrap.json
```

## 5. Verify per-phase memory behavior

Before execution:

```text
<phase>/flow_pattern_memory_match.json
```

For a new structure, expected status:

```json
{"status":"no_validated_match","validated_match":false}
```

For a safely reusable flow:

```json
{"status":"validated_match","validated_match":true}
```

The match must show a similarity score at or above the configured threshold.

After a judged pass:

```text
<phase>/flow_pattern_memory_promotion.json
```

Expected:

```json
{
  "status":"promoted",
  "trust":"validated",
  "values_stored":false
}
```

## 6. Confirm values are not reused

The current values must still come from the active phase input JSON. The memory replay profile should contain:

```json
{"values_reused":false}
```

## 7. Confirm all-form policy behavior

Generic Partner/System/Account/Domain actions write preflight evidence under:

```text
<form-run>/form_policy/<action_id>_preflight.json
```

The evidence should prove visibility, structural-parent handling, bounding-box stability, hit-test success and form-family classification.

## 8. Drift and ambiguity behavior

When the live surface contains ambiguous/duplicate bindings or changed structure, expected behavior is:

```text
validated pattern retained as historical knowledge
→ current execution switches to learning mode
→ no blind fast replay
```

## 9. Safety

The runtime continues to block Save, Create, Submit, Delete, Remove, Deploy, Publish, Update, Enable, Disable and Confirm actions.
