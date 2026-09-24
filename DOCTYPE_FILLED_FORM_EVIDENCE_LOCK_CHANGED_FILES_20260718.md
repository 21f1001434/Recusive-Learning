# Changed Files

- `hip_id_agent/doctype_kb.py`
  - Separates forbidden post-fill dropdown exploration from mandatory read-only evidence capture.
  - Captures final active-surface evidence, DOM HTML/text, and screenshot after exact fill.
  - Adds full-page to viewport screenshot fallback.
  - Writes `doctype_filled_form_evidence_lock.json`.
  - Fails with a precise evidence-capture error without replaying the completed form.

- `hip_id_agent/dummy_fill_e2e.py`
  - Prioritizes the immutable Document Type filled-form evidence lock and screenshot during verification.
  - Includes the evidence lock in strict replication diagnostics.

- `tests/test_doctype_structure_first_fill_once.py`
  - Adds regression coverage proving exact-fill freeze still captures judge evidence while post-fill exploration remains disabled.
