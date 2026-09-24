# Acceptance Test Results

```text
python -m compileall -q hip_id_agent
PASS

pytest -q --disable-warnings
194 passed in 7.26s
```

Additional targeted tests:

```text
pytest -q tests/test_full_dummy_fill_e2e.py tests/test_datamap_kb.py tests/test_doctype_kb.py tests/test_rules_kb.py tests/test_transport_profile_kb.py tests/test_bizflow_kb.py tests/test_mcp_stdio_json_lines_patch.py --disable-warnings
88 passed in 2.38s
```
