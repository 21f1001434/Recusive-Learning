# V243R6 — Judge Reconciliation + One-Time Learning HITL

Date: 2026-09-22
Package compatibility version: `2.4.3`
Release identity: `V243R6`

## Why R6 exists

A live run can correctly fill a HIP phase and still be blocked when the section-judge stack disagrees with the browser evidence. The observed failure pattern was a completed Data Map attempt with non-zero observed/filled/clicked evidence while the phase remained `blocked by section judge`.

R6 changes the decision boundary so exact browser evidence is not discarded by a stale or over-strict model judge, while still preventing a human or LLM from approving a phase whose required values are not actually proven.

## Reconciled verdict pipeline

```text
Phase-native executor
        |
        v
Exact browser readback / completion checkpoint
        |
        v
Deterministic section judge
        |
        +----------------------+
        |                      |
        v                      v
Text judge                 Vision judge
        |                      |
        +-----------+----------+
                    |
          disagreement / uncertainty?
                    |
             +------+------+
             |             |
            no            yes
             |             |
             |             v
             |     On-prem multi-model panel
             |     champion + challengers
             |             |
             +------+-+----+
                    | |
                    v v
           reconciled automated verdict
                    |
       newly learned phase in this run?
                    |
             +------+------+
             |             |
            no            yes
             |             |
             |             v
             |      one Human Review
             |   Looks correct / Needs correction
             |             |
             +------+-+----+
                    |
                    v
       verified PASS or supervised repair
                    |
                    v
        policy / model / recipe feedback
```

## Exact evidence remains the safety authority

The multi-model panel may repair a **model-only false negative** only when deterministic/exact browser evidence already proves the phase. A human `Looks correct` response follows the same rule. Neither mechanism can convert missing required values, failed repeatable rows, blocking validation errors, or an unproven exact-completion checkpoint into PASS.

A human `Needs correction` response can reject an automated PASS and send the phase back through supervised self-heal.

## One-time human review during learning

`NativeHIPPhaseMissionCoordinator` determines which capability families were not yet learned at the start of the run. Only phases belonging to those newly learned families receive the review checkpoint.

For each `run_id + phase`, `HumanPhaseReviewStore` creates at most one review request. The Control Center exposes:

- `Looks correct`
- `Needs correction`
- optional operator note
- automated verdict
- deterministic/exact evidence state
- multi-model vote summary when a panel was used

The browser mission can wait for the answer while keeping the phase open. Default wait is 600 seconds and is configurable.

Later exploitation runs do not repeatedly ask for review unless that family enters a learning path again because knowledge is missing/drifted.

## Multi-model judge panel

`MultiModelJudgeConsensus` uses the existing on-prem `OnPremModelPortfolioRouter`. The panel is consulted only on primary judge disagreement/uncertainty by default. It can be forced during learning through configuration, but is not required for every phase.

Panel proposals are advisory. The final safety gate still checks exact evidence. Resolved human review provides downstream reward feedback to the model portfolio so judge champions/challengers can improve from actual operator-supervised outcomes.

## Control Center APIs

```text
GET  /api/human-phase-review
POST /api/human-phase-review/resolve
```

Existing semantic field-teaching APIs remain separate. Phase review teaches the correctness of a completed phase; field teaching maps an unresolved input path to a semantic control.

## Configuration

```yaml
human_in_the_loop:
  enabled: true
  review_newly_learned_phase_once: true
  review_on_judge_pass: true
  review_on_judge_block: true
  phase_review_wait_seconds: 600
  phase_review_poll_seconds: 2.0
  multi_model_judge_on_disagreement: true
  multi_model_judge_force_during_learning: false
  human_pass_requires_exact_evidence: true
  store_values: false
  store_selectors: false
  store_coordinates: false
```

## Failure behavior

### Correct fill, model judge blocks

```text
exact readback PASS
+ deterministic completion PASS
+ text/vision disagreement
        -> multi-model panel
        -> optional one-time human review during learning
        -> PASS can be reconciled without destructive replay
```

### Automated PASS, human sees a problem

```text
automated PASS
        -> learning review
        -> Needs correction
        -> phase becomes blocked_human_review
        -> self-heal / repair
        -> exact verification again
```

### Exact evidence fails

```text
required value missing / row incomplete / blocking validation
        -> neither model panel nor human approval can force PASS
        -> repair is required
```

## Persisted learning

The review result is stored as supervised, value-free evidence. The system may use it to:

- reward or penalize judge-model proposals;
- update replay/policy evidence;
- reinforce the learned phase trajectory;
- promote a deterministic semantic recipe only after the existing verified-success thresholds are met.

Customer values, raw selectors and screen coordinates are not intentionally stored in phase-review memory.

## Verification performed for R6 source

- Python compilation: PASS
- backend and fallback WebUI JavaScript syntax: PASS
- focused judge/HITL/native/production regression: `110 passed, 0 failed`
- full collected suite: `1,245`
- full accounted result: `1,244 passed, 1 skipped, 0 failed`
- installable wheel built with both `human_phase_review.py` and `judge_consensus.py`
- clean wheel import and FastAPI endpoint smoke: PASS

The real Dell tenant remains authoritative for SSO, current tenant DOM/DDS behavior, model availability, and any actual governed mutation.
