param(
    [int]$Port = 8501,
    [string]$Address = "localhost",
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\streamlit_app.py")) {
    throw "streamlit_app.py not found. Run this script from the extracted project directory."
}

if ($InstallDependencies) {
    python -m pip install -r .\requirements.txt
}

python -c "import streamlit" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Streamlit is not installed. Run: python -m pip install -r .\requirements.txt, or rerun with -InstallDependencies."
}

Write-Host "Starting HIP AgentQ Streamlit UI at http://$Address`:$Port" -ForegroundColor Cyan
Write-Host "Use API mode=capture for payload/response evidence without backend mutation." -ForegroundColor Yellow

python -m streamlit run .\streamlit_app.py `
    --server.port $Port `
    --server.address $Address `
    --browser.gatherUsageStats false `
    --server.fileWatcherType poll
