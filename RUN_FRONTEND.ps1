param([int]$Port = 8501, [string]$BackendUrl = "http://127.0.0.1:8000", [switch]$InstallDependencies)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($InstallDependencies) { python -m pip install -r .\requirements.txt }
$env:HIP_BACKEND_URL = $BackendUrl
python -m streamlit run .\frontend\app.py --server.port $Port
