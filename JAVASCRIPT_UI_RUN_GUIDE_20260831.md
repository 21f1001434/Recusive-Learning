# HIP Agent v1.8.1 — JavaScript UI Run Guide

The primary UI is now a Bun-served JavaScript single-page application. Streamlit is no longer used by `bun run ui`, `npm run ui`, `bun run platform`, or `npm run platform`.

## Architecture

- `webui/index.html` — browser UI shell
- `webui/app.js` — all UI state, API calls, section execution, task/governance controls
- `webui/styles.css` — application styling
- `webui/server.js` — Bun static server and same-origin proxy to FastAPI
- `webui/platform.js` — one-command launcher for FastAPI + JavaScript UI
- `backend/app.py` — Python/FastAPI control plane for the HIP agent
- `hip_id_agent/` — unchanged browser automation, AgentQ, MCP, AutoGen, dynamic rows, governance

## Install

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
npm install
```

`npm install` is suitable on the Dell network and installs the MCP versions validated there:

- `@playwright/mcp@0.0.78`
- `chrome-devtools-mcp@1.6.0`

## Recommended one-command run

```powershell
bun run platform
```

This starts:

- FastAPI: `http://127.0.0.1:8000`
- JavaScript UI: `http://localhost:8501`

## Separate terminals

Terminal 1:

```powershell
.\venv\Scripts\Activate.ps1
bun run api
```

Terminal 2:

```powershell
bun run ui
```

## Section-only execution

Use the Execution scope dropdown in the JavaScript UI. `Transport Profiles only (Source + Target)` starts only those two phases. Source TP and Target TP can each run alone.

CLI remains available:

```powershell
python -m hip_id_agent.cli run-section transport-profile --config .\config.yaml --input-json .\input.json --runs-dir C:\hip_runs
```

## Legacy Streamlit

The old Streamlit dashboard is retained only as a compatibility/debug fallback and is not installed by the normal requirements file. If specifically needed:

```powershell
python -m pip install -e ".[legacy-streamlit]"
npm run ui:legacy-streamlit
```
