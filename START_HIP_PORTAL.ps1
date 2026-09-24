param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 8000
)
$ErrorActionPreference = "Stop"
$env:HIP_BACKEND_HOST = $HostAddress
$env:HIP_BACKEND_PORT = "$Port"
python -m backend
