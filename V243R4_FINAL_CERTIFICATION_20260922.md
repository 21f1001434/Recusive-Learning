# V243R4 Final Source Certification — 2026-09-22

## Scope
Source-level certification for the V243R4 build adding trace-reading LLM self-repair, multi-model downstream-reward learning, deterministic recipe promotion/replay, Human-in-the-Loop semantic teaching, and the September 21 live-failure runtime corrections.

## Test results

- Pytest tests collected: **1,238**
- Passed: **1,237**
- Skipped: **1**
- Failed: **0**

The full suite was partitioned into independent test-file batches because one legacy FastAPI/TestClient group can hang when co-resident with many other files during process shutdown. Seven batches completed normally; the remaining batch was isolated. Its non-UI files all passed, and each of its five UI/model-availability tests passed individually. No assertion/test failure was observed.

Focused learning + regression packs:

- New trace/HITL/deterministic tests: PASS
- Replay/Dreaming/RSI/model portfolio: PASS
- Production hardening: PASS
- PyAutoGUI fallback/exact fill: PASS
- Runtime self-heal: PASS
- DDS single-select commit: PASS
- Phase handoff/transition: PASS
- Autonomous until-complete: PASS
- Navigation timeout hardening: PASS
- Backend/Control Center: PASS

## Static/package checks

- Python `compileall`: PASS
- Root WebUI JavaScript syntax: PASS
- Bundled backend WebUI JavaScript syntax: PASS
- `config.yaml` parses with trace self-repair, deterministic recipe and HITL enabled: PASS
- Package compatibility version remains **2.4.3** to preserve existing version-contract tests.

## Safety properties

- Source-code self-modification: disabled
- Learned customer values: disabled
- Learned selectors: disabled
- Learned coordinates: disabled
- Live reproof on deterministic replay: required
- Human teaching stores semantic mapping only
- Mutation approval remains independent from model/policy learning

## Live-environment limitation
This certification does not claim mutation of a real Dell tenant. Dell SSO, current tenant-specific page behavior, model availability and real mutation outcomes remain authoritative live checks.
