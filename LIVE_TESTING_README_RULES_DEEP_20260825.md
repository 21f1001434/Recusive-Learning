# Live Testing — Rules Deep Capability Learning

## Run

```powershell
.\venv\Scripts\Activate.ps1

.\RUN_LEARN_RULES_DEEP.ps1 `
  -RunsDir "C:\hip_runs" `
  -InputJson ".\examples\uhaul_poasn_full_dummy_input.json"
```

Or directly:

```powershell
python -m hip_id_agent.cli learn-rules-deep `
  --config ".\config.yaml" `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --customer "HIP-RULES-DEEP-DISCOVERY" `
  --runs-dir "C:\hip_runs" `
  --require-mcp
```

Complete Dell SSO in the shared Chrome session when prompted.

## Expected mission

1. Open Rules listing.
2. Learn visible filters and option sets without changing persistent data.
3. Validate pagination Next -> Previous when available.
4. Search the Rule named by the current `input.json`.
5. Expand the exact matching row.
6. Inventory row actions and safely inspect read/draft surfaces.
7. Probe Migrate/Deploy/Delete only behind the mutation-abort barrier.
8. Open a fresh unsaved Create Rule surface.
9. Fill/verify Rule Details and required default Action prerequisites.
10. Commit Action Type and wait/rebind the asynchronous Mapping Identifier child.
11. Commit Mapping Identifier exactly.
12. Commit Execute Action(s) When.
13. Complete every Conditions row sequentially, requiring an exact +1 row transition before moving on.
14. Run the shared dependency-aware state verifier against all Rule input nodes.
15. Capture API request/response evidence and UI->API causality.
16. Close the unsaved surface without Save/Create/Submit.
17. Promote the verified, value-free replay path only when exact form/row checks pass.

## Evidence

Main summary:

```text
<run>\rules_deep_discovery_summary.json
```

Detailed evidence:

```text
<run>\deep_discovery\rules\
  initial\
  filters\
  pagination\
  listing\
  draft_actions\
  mutation_probes\
  create_form\
     parent_child_dependency_blueprint.json
     controls_before_fill.structural.json
     condition_row_transaction.structural.json
     stateful_form_execution.structural.json
     condition_exact_proof.structural.json
     rule_form_validity.structural.json
     filled_surface.structural.json
     api\
     create_form_exercise.json
```

Persistent capability memory:

```text
data\hip_memory\portal_brain\capability_graph.json
```

## Safety

The discovery command does not execute Save/Create/Submit. Migrate/Deploy/Delete backend mutation requests are aborted during probe learning. Real future mutation tasks remain behind the explicit task flag, `HIP_ALLOW_PORTAL_MUTATION=YES`, and the exact confirmation phrase `ALLOW HIP MUTATION`.
