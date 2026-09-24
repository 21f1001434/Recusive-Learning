# BizFlow KB Runtime Complete Dropdown Acceptance Results

Command:
```bash
python -m pytest -q
```

Result:
```text
185 passed, 1 warning in 5.55s
```

Key regression coverage added:
- Dropdown options are enriched from captured BizFlow inventory/runtime/deep-profile values.
- Source Application fallback options include existing source systems.
- Target Transport Profile fallback options include existing target TPs.
- Flow Identifier Operator receives safe operator options.
- Continue/Next clicker includes scoped Create Biz Flow JS fallback.
