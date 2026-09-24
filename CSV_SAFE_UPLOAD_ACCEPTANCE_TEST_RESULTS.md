# Acceptance Test Results

```text
python -m compileall -q hip_id_agent
PASS

pytest -q tests --disable-warnings
194 passed in 9.73s
```

Additional checks:

```text
safe_write_csv helper exists: PASS
Data Map CSV writer uses safe_write_csv: PASS
Document Type CSV writer uses safe_write_csv: PASS
Rule CSV writer uses safe_write_csv: PASS
Transport Profile CSV writer uses safe_write_csv: PASS
BizFlow CSV writer uses safe_write_csv: PASS
Summarizer CSV writer uses safe_write_csv: PASS
uploads/ folder included: PASS
Transform_DELLCoXMLASNXX08C.jar included: PASS
```
