param(
    [string]$RepoDir = "./third_party/browser-use-web-ui",
    [string]$HostIp = "127.0.0.1",
    [int]$Port = 7788,
    [switch]$Setup,
    [switch]$InstallChromium
)

$ErrorActionPreference = "Stop"
$repoUrl = "https://github.com/browser-use/web-ui.git"
$repo = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $RepoDir))

Write-Host "Browser-Use WebUI is OPTIONAL." -ForegroundColor Yellow
Write-Host "HIP actions remain: semantic proof -> PyAutoGUI MCP PRIMARY -> Playwright MCP fallback/verification." -ForegroundColor Cyan
Write-Host "Do not run a free-form WebUI agent against the same authenticated Dell HIP tab while the HIP mission controller is active." -ForegroundColor Yellow

if ($Setup -or -not (Test-Path (Join-Path $repo "webui.py"))) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git is required to clone browser-use/web-ui."
    }
    if (-not (Test-Path $repo)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $repo -Parent) | Out-Null
        git clone $repoUrl $repo
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is required. Install uv first, then rerun this script with -Setup."
    }
    Push-Location $repo
    try {
        if (-not (Test-Path ".venv")) {
            uv venv --python 3.11
        }
        uv pip install --python ".\.venv\Scripts\python.exe" -r requirements.txt
        if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
            Copy-Item ".env.example" ".env"
        }
        if ($InstallChromium) {
            & ".\.venv\Scripts\python.exe" -m playwright install chromium
        }
    }
    finally {
        Pop-Location
    }
}

$python = Join-Path $repo ".venv\Scripts\python.exe"
$webui = Join-Path $repo "webui.py"
if (-not (Test-Path $python)) { throw "WebUI virtual environment not found. Run with -Setup first." }
if (-not (Test-Path $webui)) { throw "webui.py not found at $webui" }

Write-Host "Starting optional Browser-Use WebUI at http://$HostIp`:$Port" -ForegroundColor Green
& $python $webui --ip $HostIp --port $Port
