# Changed Files

- `hip_id_agent/doctype_kb.py`
  - structure-first Document Type lifecycle
  - clean target form rebuild
  - exact fill once and post-fill freeze
  - removed post-fill exploration/restore/refill
  - disabled final interactive dropdown catalogue after exact fill

- `hip_id_agent/stateful_form_runtime.py`
  - normalized Document Identifier data-row identity
  - excluded Operation from repeatable rows
  - made Version `verify_only`

- `hip_id_agent/dummy_fill_e2e.py`
  - corrected saved-DOM Document Identifier row counting
  - added read-only completed Document Type rejudge
  - prohibited destructive phase replay after exact completion

- `hip_id_agent/portal_form_exploration.py`
  - excluded disabled/read-only parents
  - deduplicated dynamic selectors and repeated rows by logical schema contract

- `hip_id_agent/section_judge.py`
  - canonicalized model/deterministic field aliases
  - reconciled field-bound visual contradictions against exact DOM evidence

- `tests/test_doctype_structure_first_fill_once.py`
  - new regressions for row identity, Version protection, parent dedupe, judge aliases and lifecycle order
