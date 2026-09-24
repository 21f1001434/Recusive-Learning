# Unified HIP KB → Persistent Portal Brain Integration

## Verdict

Implemented and locally verified.

The reviewed `HIP_Unified_Deep_KB.json` and `HIP_Unified_Knowledge_Graph.json` are now imported into the persistent HIP Portal brain before deterministic plan compilation.

The importer keeps four evidence classes separate:

1. **Canonical reviewed knowledge** — page identity, semantic fields, input paths, dependency rules, hard gates, portal bugs and recovery guidance.
2. **Live validated knowledge** — observations from sections that passed deterministic DOM, text and vision judges.
3. **Candidate knowledge** — useful observations from warning/incomplete runs.
4. **Negative evidence** — rejected/false-positive run results and known failed strategies.

Canonical knowledge guides planning but does not count as proof that the current live form was filled correctly. The section judges still require live MCP/DOM/text/vision evidence.

## Implemented files

- `hip_id_agent/unified_kb.py`
- updated `hip_id_agent/portal_brain.py`
- updated `hip_id_agent/form_knowledge_plan.py`
- updated `hip_id_agent/dummy_fill_e2e.py`
- updated `hip_id_agent/cli.py`
- updated `hip_id_agent/config.py`
- `knowledge_base/HIP_Unified_Deep_KB.json`
- `knowledge_base/HIP_Unified_Knowledge_Graph.json`
- `tests/test_unified_kb_import.py`

## Planning changes

Every phase plan now begins with an active-surface identity gate. The plan verifies the canonical URL pattern and correct form/drawer/wizard before any field is resolved.

Canonical KB input paths are resolved exactly before fuzzy aliases. This prevents similarly named fields from stealing values from other sections.

The plan contains:

- active-surface verification;
- semantic field mappings;
- parent-value-child dependencies;
- composite row operations;
- effect-validated repeatable-row actions;
- canonical hard gates;
- section judges;
- failure/recovery knowledge;
- reviewed negative evidence.

## Validation

```text
python -m compileall -q hip_id_agent
pytest -q
226 passed in 8.77s
```

A live authenticated Dell HIP Portal run was not possible in this environment. The package therefore does not claim a live portal pass.
