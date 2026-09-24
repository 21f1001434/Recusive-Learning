# V242 / v2.4.2 Exact Package Certification — 2026-09-18

The release was built from the corrected V242 source, zipped, freshly extracted into a separate directory, and verified from that extracted copy.

## Code fixes certified

- Proposal-only model winners are not persisted as durable champions.
- Downstream traces preserve the exact model role and hashed task identity.
- Durable role/task champion promotion requires real downstream portal outcome evidence.
- Task-specific model statistics are maintained separately from generic role statistics.
- Model tournament candidates are filtered by declared role capability and On-Prem catalog membership.
- Exploration prioritizes under-tested eligible challengers without letting stale champions override exploration.
- V241 portfolio-state migration preserves evidence counters while removing proposal-only champion labels.
- Recursive replay/model/skill update switches are honored and skill outcomes are not double-counted.
- Universal-task version metadata falls back to package `__version__` rather than a hardcoded release value.
- Standalone wheel import smoke uses the real module paths and passes.

## Exact extracted package regression

- Tests collected: **1,212**
- Passed: **1,211**
- Skipped: **1**
- Failed: **0**

The single skip is the existing environment-dependent browser test.

## Exact extracted package static/config checks

- SHA-256 source manifest: PASS before certification evidence insertion
- Python compileall: PASS
- WebUI JavaScript syntax (`node --check webui/app.js`): PASS
- `config.yaml`: PASS
- `config.example.yaml`: PASS
- `config.mcp-required.windows.yaml`: PASS

## Exact extracted package local Chromium UAT

- Data Map: PASS
- Source Document Type: PASS
- Target Document Type: PASS
- Rule: PASS
- Source Transport Profile: PASS
- Target Transport Profile: PASS
- BizFlow: PASS
- BizFlow Edit / Save / Validate / Deploy: PASS
- Final BizFlow state: Deployed
- Final consolidation gate: PASS
- PyAutoGUI-MCP point contract: PASS
- Mock/local only; `dell_environment_contacted=false`

## Standalone wheel smoke

Clean `--target` installation succeeded and imported:

- `hip_id_agent` version `2.4.2`
- `AsyncMLflowTracker`
- `ReplayPolicyEngine`
- `SkillInductionEngine`
- `OnPremModelPortfolioRouter`
- `RecursiveSelfImprovementEngine`
- `UniversalPortalTaskPlanner`
- `UniversalPortalTaskExecutor`

Wheel SHA-256: `a186638f2c5f83b6e1c51d0714b1574ea68b56c4250c16b9c694b590a925fcf2`

## Scope statement

This is the strongest local/package verification available in the execution environment. It does **not** claim that a live Dell HIP tenant was mutated or that tenant-specific portal drift cannot exist. The live page, current `input.json`, user mutation authorization, and post-action verification remain authoritative at runtime.
