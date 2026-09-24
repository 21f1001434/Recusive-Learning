# HIP Full End-to-End Governed Browser Intelligence Platform — 2026-08-26

## Final architecture

This package consolidates the complete HIP browser-intelligence program into one codebase:

1. **Full HIP deep learning** across Data Maps, Document Types, Rules, Transport Profiles and BizFlows.
2. **Seven-phase `input.json` configuration runtime** with parent/child dependency scheduling, repeatable rows, exact verification and golden references.
3. **AutoGen AgentChat/Core/Ext 0.7.5** for guarded semantic planning/recovery around deterministic browser execution.
4. **AgentQ-style exploration/exploitation**, critic, state representation and trajectory memory.
5. **Playwright + Playwright MCP + Chrome DevTools MCP + HIP Intelligence MCP** using one authenticated browser session.
6. **UI → API causality** including request URL/method, redacted payload, response status/body and action attribution.
7. **Persistent value-free Capability Graph** with deterministic form and cross-family task replay profiles.
8. **Certified Future Task Agent** for natural-language tasks across all five HIP portal families.
9. **Certified Change Execution & Governance** for production mutations.
10. **Separate FastAPI backend and Streamlit frontend**.

## Governed mutation flow

A mutation cannot jump directly from natural language to a click. The final path is:

`task → certified capability plan → preview → role/policy → duplicate/idempotency gate → existing three-key mutation authorization → one-shot UI execution → 2xx API response verification → fresh MCP assurance → tamper-evident audit receipt → rollback guidance`

Mutation actions are never automatically retried after a click attempt because the backend result can be ambiguous.

## Governance controls

The new `hip_id_agent/change_governance.py` adds:

- risk classification across read / draft / mutation;
- role policy (`viewer`, `business`, `support`, `technical`, `admin`);
- optional change-approval ID policy;
- certified-family requirement for mutation;
- pre-execution structural change preview;
- observed API-contract preview for the selected capability;
- 168-hour default duplicate/idempotency protection;
- explicit force-repeat override for intentional repeated mutations;
- 2xx-only write-response acceptance;
- fresh MCP post-change assurance requirement;
- no automatic rollback for external HIP mutations;
- action-specific rollback guidance;
- append-only SHA-256 hash-chained audit ledger with chain verification;
- per-run `change_preview.json`, `governed_change_execution.json` and `change_receipt.json`.

Persistent audit records store structural metadata/hashes rather than customer values.

## Interfaces

### CLI

- `learn-hip-full-deep`
- `full-deep-readiness`
- `plan-certified-task`
- `run-certified-task`
- `preview-governed-change`
- `run-governed-change`
- `change-audit-status`

### Backend

- `POST /api/full-deep/start`
- `GET /api/full-deep/readiness`
- `POST /api/certified-task/plan`
- `POST /api/certified-task/run`
- `GET /api/certified-task/replays`
- `POST /api/governed-change/preview`
- `POST /api/governed-change/run`
- `GET /api/governed-change/audit`

### Frontend

The Streamlit application now exposes:

- Learn HIP
- Portal Knowledge
- Full HIP Readiness
- API Explorer
- Future Task Agent
- Change Governance
- Runs / Console

The frontend remains a pure backend client and does not own Playwright/browser execution.

## Input contract

The bundled UHAUL input passes the complete static preflight with 100% scalar-leaf accounting for all seven phases:

- Data Map: 7/7
- Source Document Type: 31/31 accounted
- Target Document Type: 31/31 accounted
- Rule: 19/19
- Source Transport Profile: 15/15
- Target Transport Profile: 15/15
- BizFlow: 61/61 accounted

Golden references and the required Data Map upload asset are also present under the bundled UHAUL profile.

## Important live-environment boundary

The package is code/test verified here. An authenticated Dell HIP end-to-end mutation cannot be truthfully certified from this environment because it does not have the user's Dell SSO/private HIP tenant. The runtime is deliberately designed to fail closed and produce evidence when a live tenant differs.
