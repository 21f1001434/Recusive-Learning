param(
    [int]$Port = 8501,
    [string]$Address = "localhost",
    [switch]$InstallDependencies
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path ".\streamlit_app.py")) { throw "Run from the extracted project directory." }
if ($InstallDependencies) { python -m pip install -r .\requirements.txt }
python -c "from hip_id_agent.autogen_runtime import assert_autogen_075; s=assert_autogen_075(verify_imports=True); print('Microsoft AutoGen AgentChat/Core/Ext', s['required_version'], 'ready')"
if ($LASTEXITCODE -ne 0) { throw "AutoGen 0.7.5 is required. Run: python -m pip install -r .\requirements.txt" }
python -c "import streamlit" 2>$null
if ($LASTEXITCODE -ne 0) { throw "Streamlit is not installed. Rerun with -InstallDependencies." }
Write-Host "HIP AgentQ assured UI: http://$Address`:$Port" -ForegroundColor Cyan
python -m streamlit run .\streamlit_app.py --server.port $Port --server.address $Address --browser.gatherUsageStats false --server.fileWatcherType poll
