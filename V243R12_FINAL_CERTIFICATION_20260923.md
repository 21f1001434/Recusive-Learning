> **Historical R12 certification.** This file describes the originally uploaded R12 bytes and their 1,272-test/hash certification. The hardened package in this folder supersedes those exact bytes. For the current package, use `V243R12H1_HARDENED_AUDIT_20260923.md` and `V243R12H1_FINAL_SOURCE_MANIFEST.json`.

# V243R12 Final Source Certification — 2026-09-23

Release: **Continuous Portal Learning + Dell On-Prem Model Orchestra**  
Compatibility package version: **2.4.3**

## Source verification

- Pytest collected: **1,272**
- Passed: **1,271**
- Skipped: **1**
- Failed: **0**
- Python compileall: **PASS**
- WebUI/backend JavaScript syntax: **PASS**
- Clean wheel installation/import smoke: **PASS**

The suite was executed in disjoint test-file batches to avoid known browser-style process-shutdown latency. All collected tests are accounted for exactly once.

## R12 capability verification

- filling/clicking/navigation produce persistent, value-free portal experience;
- continuous experience updates Capability Graph, Portal Brain transitions, trajectory memory and Replay Policy;
- trusted promotion requires exact + judge + human proof;
- hidden AutoWebGLM direct single-model decision path is replaced by portfolio routing for learning/complex tasks;
- learning mode does not collapse to one champion when multiple eligible Dell models are available;
- Dell text-model availability probe + TTL cache + persistent availability ledger;
- actual per-tournament model usage ledger;
- role-aware planning/action-selection/judge/recovery routing retained;
- Control Center exposes configured/available/recently-used model evidence;
- Production Doctor exposes multi-model-learning readiness;
- customer values/selectors/coordinates are not intentionally stored in reusable learning;
- mutation governance and live exact proof remain authoritative;
- source-code self-modification remains disabled.

## Model catalog

Text: `gpt-oss-120b`, `gpt-oss-20b`, `mistral-small-3-1-24b-instruct-2503`, `llama-3-3-70b-instruct`, `gemma-3-27b-it`, `llama-3-2-3b-instruct`.

Vision: `gemma-3-27b-it`, `pixtral-12b-2409`, `florence-2-large-ft`.

Embedding: `nomic-embed-vision-v1-5`.

Actual live availability is probed at runtime and may be a subset of this configured catalog.

## Validation boundary

This certification validates source, tests, packaging contracts and local runtime behavior. It does not claim that every Dell AIA deployment is enabled in the user's current tenant or that a real HIP tenant was mutated. Live availability, SSO and authorized mutation outcomes remain authoritative live checks.
