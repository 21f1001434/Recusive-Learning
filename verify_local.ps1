$ErrorActionPreference = "Stop"
python -m compileall -q hip_id_agent tests
python -m pytest -q
