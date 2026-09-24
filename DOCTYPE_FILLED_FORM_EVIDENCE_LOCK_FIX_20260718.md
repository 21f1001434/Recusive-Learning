# Document Type Filled-Form Evidence Lock Fix — 2026-07-18

## Supplied run
`UHAUL-POASN-FULL-DUMMY-20260718-033819`

## Root cause
Source Document Type filled successfully once: the stateful execution artifact passed all 29 exact actions with zero failed actions. The new no-repeat guard also worked and did not reopen the form.

The structure-first change froze the completed form with this condition:

- Skip post-fill dropdown exploration after exact execution.

However, the final DOM snapshot and screenshot were inside the same conditional block. Therefore exact execution passed, but these files were never written:

- `doctype_add_form_after_dummy_fill_no_save.html`
- `doctype_add_form_after_dummy_fill_no_save.txt`
- `doctype_add_form_after_dummy_fill_no_save.png`

The deterministic artifact judge then reconstructed values from execution attempts, but row counts and active-surface identity require the final DOM. The vision judge had no screenshot. The phase correctly stopped without replay, but could not advance.

## Correction
Post-fill actions are now separated:

1. **Forbidden after exact fill**: dropdown opening, branch exploration, field clicking, Version interaction, mutation.
2. **Mandatory after exact fill**: active-surface inspection, DOM HTML capture, visible-text capture, screenshot capture, evidence-lock write.

A new immutable artifact is written immediately after exact execution:

`doctype_kb/doctype_filled_form_evidence_lock.json`

It records the exact-execution status, active-surface status, DOM paths, screenshot path, and confirms no post-fill mutation occurred.

Full-page screenshot failure automatically falls back to a viewport screenshot. If the final evidence bundle is incomplete, the runtime raises a precise evidence-capture error; it never reopens the completed Document Type form.

## Expected next-run behavior

- Source Document Type exact fill: once.
- 29 exact actions: pass.
- Final form frozen: true.
- Post-fill exploration: false.
- Final DOM HTML/text: present.
- Final screenshot: present.
- Deterministic row counts: 1 Document Identifier row and 5 Attribute rows.
- GPT and Gemma judge the locked filled form.
- Continue to Target Document Type when all required judges pass.
