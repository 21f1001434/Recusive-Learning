# V243R12H1 Hardened Audit — 2026-09-23

## Scope
Independent code-and-package verification of the uploaded `HIP_PORTAL_V243R12_CONTINUOUS_LEARNING_FINAL_20260923.zip`, followed by hardening of gaps found outside the original regression coverage.

## Original package verification
- Uploaded ZIP SHA-256 matched the advertised source hash exactly: `7569f685c65cc92718398235910d1e53d7f986b0cfa4f8138c583e12e78faa48`.
- Original source manifest: 1,055 entries, 0 missing, 0 hash mismatches.
- Original test collection: 1,272 tests. Reconciled result: 1,271 passed, 1 expected skip, 0 failed.
- Python compile and JavaScript syntax checks passed.
- Original bundled wheel hash matched the advertised hash.
- AutoWebGLM live decision path, multi-model planning/action/judge/recovery routing, availability probing/usage ledger, Production Doctor readiness checks, continuous-learning stores, replay/Portal Brain/capability graph/RSI integration were all present and wired.

## Gaps found in the original R12
1. **Strict human-gated promotion was not fully enforced.** No human review payload was interpreted as human PASS by the main phase integration. The public `promote_only_after_exact_judge_human_pass` switch was configured but not used by the learning engine.
2. **Failed actions could contaminate learned sequences.** Failed actions were correctly recorded as experiences, but their capability IDs and Portal Brain transitions could still be included in the same sequence later marked validated when the overall phase passed.
3. **Several learning switches were configuration-only.** `learn_from_fill`, `learn_from_click`, `learn_from_navigation`, and failed-action policy were defined but not consulted by `ContinuousPortalLearningEngine`.
4. **Browser action-selection tournaments were not directly rewarded per physical action in the main fill path.** Universal-operator tasks had downstream portfolio reward, but native click/fill/press decisions did not feed their exact tournament trace back after semantic/exact-effect verification.

## H1 fixes implemented
- Missing human review is now `UNKNOWN` (`None`), not PASS. Trusted promotion requires explicit human PASS when strict gating is enabled.
- Failed actions remain `negative_evidence`; semantically unverified actions remain observed evidence. Neither can enter trusted `followed_by` sequences, validated Portal Brain edges, or replay steps.
- Runtime learning switches are enforced. A dedicated `learn_from_search` switch was added with default `true`.
- AutoWebGLM exposes `record_downstream_outcome()` and BrowserSession calls it after click/fill/search/press based on the actual verified browser effect; exceptions feed negative reward/drift.
- Human/negative/reward state is auditable in continuous-learning receipts and action execution provenance.

## Hardened verification
- Test collection: **1,276**.
- Result: **1,275 passed, 1 expected skip, 0 failed**.
- New hardening tests: strict human gate, negative-evidence route isolation, runtime learning switches, exact model-tournament downstream reward.
- Hardened wheel clean target import: PASS.
- Hardened wheel source-guard smoke: PASS.
- Hardened wheel SHA-256: `e2c7b986f0e82e3679f7c982f26a06c3f473b89c0eebc3666eb733445c8bd9d7`.

## Operational boundary
The code can prove configuration, routing, browser/effect gates, persistence behavior, and tests in this package. It cannot prove that every Dell AIA deployment is reachable or that the live HIP tenant/SSO/API contracts have not changed until `PRODUCTION_DOCTOR.ps1` and a live governed run are executed in the Dell environment. The runtime is implemented to probe and report that state rather than assume it.
