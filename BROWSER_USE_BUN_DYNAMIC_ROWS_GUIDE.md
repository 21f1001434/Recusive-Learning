# HIP Browser-Use + Bun + Dynamic Repeatable Rows

## What is integrated

- `browser-use==0.13.8` is attached to the **same persistent Chrome** over CDP; it does not launch a second HIP session.
- Browser-Use is used as a state/perception and recovery layer. It is **not** given a free-form mutation agent.
- Existing Playwright + Playwright MCP + Chrome DevTools MCP + HIP governance remain the execution authority.
- MCP JavaScript tooling is managed with Bun (`bun install`, `bunx`).
- Repeatable sections derive the required row count from `input.json`.

## Exact two-row behavior

If `objects.rule.conditions.rows` contains two entries and the HIP form initially renders one row:

1. Fill/validate row 1.
2. Resolve the **Conditions-local** `+` control.
3. Click it exactly once.
4. Verify visible rows changed **1 -> 2**.
5. Fill row 2 with its own JSON values.
6. Verify the final row count and values.

The generic repeatable-row engine uses `expected - current` and validates every click with an exact `N -> N+1` transition. It refuses ambiguous/global plus controls and refuses destructive row removal if the portal already has more rows than the input.

## Install (Windows PowerShell)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
bun install
```

Check tooling:

```powershell
bun --version
bunx --bun @playwright/mcp@0.0.79 --help
bunx --bun chrome-devtools-mcp@1.7.0 --help
python -m hip_id_agent.cli dual-mcp-check --config .\config.yaml --run-dir C:\hip_runs\dual-mcp-check
```

## Run UI

```powershell
bun run ui
```

## Run API

```powershell
bun run api
```

## Run tests

```powershell
bun run test:python
```

## Safety boundary

Browser-Use only supplies structured state/recovery evidence in this integration. Create/save/delete/deploy/migrate operations still go through the existing governed task executor and explicit mutation gates.
