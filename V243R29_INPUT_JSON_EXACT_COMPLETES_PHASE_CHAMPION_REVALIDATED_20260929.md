# V243R29: an exactly filled form completes the phase; every model re-validated, champion chosen (2026-09-29)

## What was asked

- "It should be able to understand that everything has been filled correctly and committed according to the input.json, and complete that task or phase."
- "It needs to revalidate all the models and then choose the champion."

## Why a correctly filled phase could stay open

| # | Cause |
|---|---|
| 1 | **The read-only check of the live form was wrong for the Document Type.** input.json holds enum values (`TRANSACTION_ROOT_ELEMENT`, `ELEMENT_IN_PAYLOAD`), and the dropdowns correctly show "Transaction Root Element" / "Element In Payload". The fill treats them as equal; the check did not. The portal-owned **Version** (a read-only field showing "1") was also counted as missing. So right after an exact fill the check reported 3 missing fields, and every path that relies on it could not complete the phase: R26's "a filled form is kept", the watchdog's terminal re-proof and the human-approval re-proof. |
| 2 | **A model judge could block an exact form.** The phase passed only when the text model judge and the vision judge also said "complete". A model false negative went to the champion/challenger panel. Since R24 that panel is the one locked model, so it agreed with itself and the phase was held for a human. |
| 3 | **A newly learned phase always waited for a human**, also when every value was exact. |
| 4 | **An attempt that failed after the form was complete** (a late executor or evidence error) reopened the form and filled it again. |
| 5 | **The champion was never tested on judging.** It was chosen once (R24) on listing-navigation questions only: which control opens Edit, which one searches. Yet it also answers the judges ("is this form complete?"). |

## What changed

### 1. The live form is the answer

`hip_id_agent/input_json_authority.py` runs a fresh, read-only proof of the current form:
- it closes open dropdowns and waits until none is open, so a value only typed into a dropdown's search box reverts and only committed values count;
- it compares every input.json value, repeatable row and required upload.

Nothing is clicked, filled or saved.

The mission runs this proof:
- after the section judge;
- when an attempt fails, before anything reopens the form. Failures where the form cannot be the answer are skipped: not signed in, a mutation question, a dead browser, empty lists, another page.

When the form is **exact**:
- the phase is complete (`pass_input_json_exact`). The judges' objection is kept in `overruled_diagnosis` for the audit, and the model panel is not needed;
- a newly learned phase does not wait for a human (`phase_acceptance_commit.json`: `acceptance_source: input_json_exact_authority`). Set `human_in_the_loop.review_learning_phase_even_when_exact: true` to keep the confirmation;
- a failed attempt is not replayed. The mission goes on to the judges and hands off.

When the form is **not** exact, nothing changes: the judges, panel, human review and self-healing work as before. The proof never passes a form that is not exact:
- nothing matched;
- a field missing, invalid or in the wrong row;
- a required upload unproven.

Evidence: `<phase>/input_json_completion_authority.json` (matched fields, each model judge's verdict and whether it agreed with the live form). The live view shows "Every input.json value is filled and committed on the live form".

The judge comparison was fixed (`section_judge._values_equal`, `deterministic_judge`):
- an input.json enum equals its DDS label;
- a disabled or read-only portal field whose displayed value is the expected one counts as filled.

### 2. Every model re-validated, the champion chosen again

The qualification (`model_qualification`, version 2) now also asks **completion judgments**:
- For rows of the live Document Types table, each model gets an expected record. Half are exact copies of a row; the other half have one value changed to another row's value, a different column each time.
- The question is: *"Does the live row hold exactly these values?"* The right answer, `{"match": true}` or `{"match": false, "fields": [<the changed column>]}`, is read from the page.

Every available Dell AIA model gets the same navigation and judgment questions:
- a champion must answer at least 60% of all questions and at least 50% of the judgments (`qualification_min_judge_accuracy`);
- accuracy decides, then judgment accuracy, then speed.

**When all models are asked again:**

| Trigger | When |
|---|---|
| The lock is from before R29 (version 1) | Once, at the next Live GO/NO-GO certification or the first live mission page. Until then the old champion stays in use. |
| The lock is older than `qualification_max_age_days` | 30 days by default (0 = never) |
| The champion keeps judging against the live form | Each phase's text-judge verdict is scored against the live-form proof. If the champion is wrong in 3 of its last 5 verdicts (`qualification_revalidate_after_judge_errors`), it is re-validated. |
| On request | "Re-run the model qualification" in the Control Center, `certify-live-runtime --requalify-models`, `qualify-models --force` |

A re-validation that cannot finish keeps the champion in use (`kept_previous_selection`).

**Where you see it:**
- **Live certification:** the row "Model champion chosen by live task performance (all models validated)", for example *"re-validated all models, champion: llama-3-3-70b-instruct (9/10 live questions correct, judged 3/4 completion checks right, …)"*.
- **Model Champion tile:** "champion by live task: n/m correct • judged j/k • date • live verdicts ✓/✗", and "re-validation due" when it is.
- **`qualify-models --show`:** every model's correct answers, accuracy, judgment accuracy, latency and whether it qualified, plus the champion's live verdict record.

## Tests

`tests/test_v243r29_input_json_authority_and_champion_revalidation.py` (10):
- **Real browser:**
  - a Document Type filled by the executor is proven exact (every field, including Version and both Derived From);
  - text only typed into Data Format Type's search box reverts (only committed values count);
  - a cleared Description is named as missing.
- **The decision:**
  - an exact form overrules a text judge's block, and the proof becomes the phase's checkpoint;
  - a form that is not exact (nothing matched, missing field, unproven upload) is never passed;
  - a learning phase waits for a human only when not exact (or when configured).
- **The mission's wiring:** the proof runs after the judges and before the panel; the panel and human review are skipped when exact; a failed attempt is proven before any reopen.
- **Judgment questions:** built from the live table, with the right answer read from the page and a different column changed each time.
- **Re-validation:**
  - an old (v1) lock re-validates every model;
  - a model that navigates perfectly but judges 1/4 is not the champion;
  - it runs once per version.
- **A champion wrong 3 times** against the live form is re-validated and replaced.
- **A re-validation that cannot finish** keeps the champion.
- Certification, mission, CLI and Control Center wiring; the text judge records its model.

Updated for the new questions: the R24 and R27 stand-in models also answer the judgments. The screen fixture now includes the table.

## Verification

| Check | Result |
|---|---|
| The live-form proof right after an exact Document Type fill (replica) | Before: *live_state_not_exact*, with `document_type_version` and both `derived_from` reported missing. After: *exact_live_state_reproved*, every field matched. |
| Text typed into Data Format Type's search box (not chosen) | Reverts; the form is still exact on the committed XML |
| Description cleared by the portal | Not exact; Description named as missing |
| Re-validation (stand-in for Dell AIA, 3 models) | The R24 lock (mistral, navigation 8/8) was re-validated. mistral navigates perfectly but judged 1/4 and is not qualified; the champion is llama-3-3-70b-instruct (judged 3/4). It runs once per version. |
| R29 tests | 10 passed |
| Judge-related suites (section judge gate, completed-phase reconciliation, R6 judge/HITL, R10 live reproof watchdog, R13, R16, R4 learning review, Data Map judge) | 59 passed with R29 |
| Full suite (204 files) | 1,524 passed, 1 skipped. Two cases apply only outside this environment: `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden` needs the gitignored `uploads/*.jar`, which ships in the package; `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| Package `HIP_PORTAL_V243R29_FINAL_FULL_E2E_20260929.zip` | Every tracked file identical to the branch; the three wheels match the source; the R13–R29 install smoke checks (53) pass from the extracted package; 55 tests pass from it (R29, section judge gate, completed-phase reconciliation, R10 live reproof, R13 judge/human accept, self-heal loop, input contract incl. the upload assets, Control Center) |
| `VERIFY_V243R29_INSTALL.ps1` R29 smoke checks | `R29_INPUT_JSON_EXACT_COMPLETES_PHASE_OK`, `R29_ALL_MODELS_REVALIDATED_CHAMPION_OK` (and the R28 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R29_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R29_INSTALL.ps1
```

R29 includes R13–R28. No config.yaml change is needed:
- `human_in_the_loop`: `input_json_exact_is_authoritative` (true), `review_learning_phase_even_when_exact` (false);
- `model_portfolio`: `qualification_judge_questions` (4), `qualification_min_judge_accuracy` (0.5), `qualification_max_age_days` (30), `qualification_revalidate_after_judge_errors` (3).

Then run **Certify Windows runtime** once (or start a mission). Every model is re-validated and the champion chosen. Your current lock is from R24, so this happens automatically once.

## Validation boundary

- The judgment questions use the live Document Types listing (read-only), like the R24 questions; the Dell AIA models were represented here by a stand-in. On your machine the real deployments answer.
- The vision judge's model is not re-qualified here: it needs screenshots and a multimodal deployment. It can no longer block an exact form.
