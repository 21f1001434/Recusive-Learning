# V243 Production End-to-End Solution

V243 is the production-operability layer above the HIP learning runtime. It does not replace the semantic world model, AgentQ, skill induction, replay/dreaming, recursive improvement, multi-model routing, golden references or exact `input.json` completion logic. It coordinates those capabilities into a deterministic production lifecycle.

## End-to-end lifecycle

```text
User request
  -> production doctor / dependency health
  -> task plan + mutation classification
  -> operator / approval / three-key governance
  -> duplicate + ambiguous prior-mutation quarantine
  -> cross-process single-browser execution lease
  -> current input.json + golden structure + live portal observation
  -> skills + replay/dreaming + On-Prem model portfolio proposals
  -> single governed browser executor
  -> exact input coverage + repeatable-row + mutation-effect verification
  -> skill/replay/model/recursive learning updates
  -> async MLflow telemetry (fail-open)
  -> tamper-evident hash-chained journal
  -> final human/JSON summary
  -> safe review bundle
```

## Production invariants

1. Current `input.json` is the business-value authority.
2. Golden screenshots are structural guidance only.
3. Current live portal evidence authorizes every browser action.
4. Multiple models may propose in parallel, but only one governed executor can act.
5. A mutation requires the existing explicit mutation gate and, when configured, an allowed operator role and approval id.
6. A previous successful mutation with the same idempotency key is quarantined unless the operator explicitly forces a repeat.
7. An unresolved/ambiguous prior mutation is always quarantined for manual review.
8. Browser/session ownership is protected by a cross-process lease.
9. Screenshots and raw portal evidence remain local by default and are excluded from the safe review ZIP.
10. MLflow is observability-only and fail-open.
11. Automatic rollback of external mutations is intentionally disabled. The run records before/after evidence and emits compensating-action guidance instead.

## One-process Control Center

Normal operation no longer requires a separate Bun process. Run:

```powershell
python -m backend
```

or, after installation:

```powershell
hip-portal
```

FastAPI serves both `/api/*` and `webui/` from the same process.

## Production doctor

```powershell
python -m hip_id_agent.cli production-doctor `
  'Fill the U-HAUL configuration from input.json' `
  --config .\config.yaml `
  --input-json .\input.json `
  --golden-dir .\golden_screenshots\UHAUL-POASN
```

The doctor is non-mutating. It checks the runtime, storage, lock, input contract when the task requires input, MLflow availability, model portfolio, replay cache and (for a mutation) the live-runtime certificate.

## Full read-only execution

```powershell
python -m hip_id_agent.cli run-production-e2e `
  'Open the Transport Profile and inspect its configuration' `
  --config .\config.yaml `
  --input-json .\input.json
```

## Full governed mutation execution

Mutations retain the existing three-key gate and production governance:

```powershell
$env:HIP_ALLOW_PORTAL_MUTATION='YES'
$env:HIP_OPERATOR_ROLE='admin'
$env:HIP_CHANGE_APPROVAL_ID='CHG-12345'

python -m hip_id_agent.cli run-production-e2e `
  'Edit the Source Transport Profile from input.json, save, validate and deploy' `
  --config .\config.yaml `
  --input-json .\input.json `
  --golden-dir .\golden_screenshots\UHAUL-POASN `
  --allow-portal-mutation `
  --confirmation 'ALLOW HIP MUTATION' `
  --operator-role admin `
  --approval-id CHG-12345
```

A second identical successful mutation is blocked by default. `--force-repeat-mutation` exists for an explicitly reviewed repeat request; it does not bypass the normal mutation gate.

## Run evidence

Each production run can contain:

- `production_doctor.json`
- `production_request_manifest.json`
- `production_governance_gate.json`
- `production_execution_lease.json`
- `production_execution_journal.jsonl`
- `universal_portal_task_plan.json`
- `universal_portal_task_execution.json`
- `mutation_evidence/` (local only)
- `production_final_summary.json`
- `PRODUCTION_FINAL_SUMMARY.md`
- `SAFE_REVIEW_BUNDLE.zip`

The request manifest stores file hashes and counts rather than customer values. The journal is hash-chained and can be checked with:

```powershell
python -m hip_id_agent.cli verify-production-journal .\runs\<RUN>\production_execution_journal.jsonl
```

## Failure behavior

- Dependency/preflight problem: `NO-GO`, browser is not started.
- Another production session owns the browser: blocked by the lease.
- Duplicate mutation: quarantined.
- Ambiguous earlier mutation: quarantined for manual review.
- MLflow outage: warning/fail-open if configured as fail-open.
- Browser disconnect: existing V235+ session recovery applies.
- Portal drift: skills/replay/model confidence decays and adaptive exploration reopens.
- Mutation exception: run is marked ambiguous/manual-review-required; it is not blindly retried.

## Learning after a successful run

Verified successful behavior still flows into the existing value-free learning layers:

```text
verified trajectory
 -> skill induction
 -> replay episode
 -> dreaming policy improvement
 -> downstream model reward
 -> bounded recursive improvement
 -> faster future exploitation when the live portal still agrees
```

Selectors, XPath, coordinates, customer values and environment-specific URLs are not intended to become reusable policy memory.

## Fresh wheel / first-run behavior

The installed `hip-portal` command bundles the Control Center. A wheel can therefore be launched before a project has been configured. In that state `/api/runtime/status` returns HTTP 200 with `status=setup_required` and the UI remains available instead of raising an internal server error. Run the command from a HIP project directory containing `config.yaml`, or set `HIP_PROJECT_ROOT`, to activate the full runtime.

## V243 final verification snapshot

- Package version: `2.4.3`
- Repository tests: `1,227 collected`, `1,226 passed`, `1 skipped`, `0 failed`
- Seven-phase local Chromium mission: `7/7 PASS`
- BizFlow Edit / Save / Validate / Deploy: `PASS`
- Clean-wheel Control Center smoke: `PASS`
- Clean-wheel no-config setup state: `PASS`
- Clean-wheel project-config runtime status: `PASS`

The Chromium mission is intentionally local/mock and does not claim that a Dell tenant was contacted or mutated.
