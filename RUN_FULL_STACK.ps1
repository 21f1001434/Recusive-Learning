param([int]$BackendPort = 8000, [int]$FrontendPort = 8501, [switch]$InstallDependencies)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($InstallDependencies) { python -m pip install -r .\requirements.txt }
$backend = Start-Process -FilePath "python" -ArgumentList @("-m","uvicorn","backend.app:app","--host","127.0.0.1","--port",$BackendPort) -WorkingDirectory $PSScriptRoot -PassThru
Write-Host "Backend PID: $($backend.Id) http://127.0.0.1:$BackendPort"
$env:HIP_BACKEND_URL = "http://127.0.0.1:$BackendPort"
try {
  python -m streamlit run .\frontend\app.py --server.port $FrontendPort
} finally {
  if (!$backend.HasExited) { Stop-Process -Id $backend.Id -Force }
}
