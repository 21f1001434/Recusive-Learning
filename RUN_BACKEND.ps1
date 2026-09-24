param([int]$Port = 8000, [switch]$InstallDependencies)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($InstallDependencies) { python -m pip install -r .\requirements.txt }
python -m uvicorn backend.app:app --host 127.0.0.1 --port $Port
