# Rerun Guide

Use the same `run-full-dummy-fill` command and flags.

After Source Document Type completes, verify these files exist:

- `source_document_type/doctype_kb/doctype_filled_form_evidence_lock.json`
- `source_document_type/dom_snapshots/doctype_add_form_after_dummy_fill_no_save.html`
- `source_document_type/dom_snapshots/doctype_add_form_after_dummy_fill_no_save.txt`
- `source_document_type/doctype_kb/doctype_add_form_after_dummy_fill_no_save.png`

The evidence lock should contain:

- `target_execution_pass: true`
- `failed_attempt_count: 0`
- `surface_pass: true`
- `screenshot_exists: true`
- `form_mutated_after_exact_fill: false`
- `judge_evidence_ready: true`

The phase should not create a second Source Document Type execution attempt. If evidence capture fails, it must stop with `HIP_DOCTYPE_FILLED_FORM_EVIDENCE_CAPTURE_FAILED` without refilling the form.
