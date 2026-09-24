# Windows Chrome Run Guide

This project is configured to use your installed **Google Chrome**, not the Playwright-downloaded Chromium browser.

## Correct command syntax

Use:

```powershell
python -m hip_id_agent.cli --help
```

Do not use relative module syntax like:

```powershell
python -m .\hip_id_agent\.cli --help
```

Python reports `Relative module names not supported` for that form.

## No Chromium download required

Do not run:

```powershell
python -m playwright install chromium
```

The config uses:

```yaml
portal:
  chromium_channel: "chrome"
```

Playwright will launch installed Google Chrome through its Chrome channel.

## If Chrome channel lookup fails

Set an explicit path in `config.yaml`:

```yaml
portal:
  chromium_channel: null
  chrome_executable_path: "C:/Program Files/Google/Chrome/Application/chrome.exe"
```

Common alternatives:

```yaml
portal:
  chrome_executable_path: "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"
```

## Run

```powershell
.\run_chrome.ps1 -Customer UHAL -PartnerQuery UHAL -SystemQuery UHAL-POASN -InputJson .\customer\UHAL-POASN\input.json
```

Or directly:

```powershell
python -m hip_id_agent.cli extract-ids `
  --config config.yaml `
  --customer UHAL `
  --partner-query UHAL `
  --system-query UHAL-POASN `
  --input-json .\customer\UHAL-POASN\input.json
```

## Corporate SSL notes

For Python package installation behind Dell/corporate SSL interception:

```powershell
python -m pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

If a Dell root CA file is available, configure pip with it instead of using trusted-host permanently.


## Broad Partner/System Discovery and Safe Button Exploration

To search one example first, then safely explore visible Partner/System buttons and menus:

```powershell
python -m hip_id_agent.cli extract-ids `
  --config .\config.yaml `
  --customer UHAL `
  --partner-query AS2TEST `
  --system-query AIC-DCE `
  --explore-all-buttons `
  --collect-all-pages `
  --max-pages 10 `
  --max-total-actions 160
```

To crawl Partner and System pages broadly, including multiple pages, without requiring one exact input JSON:

```powershell
python -m hip_id_agent.cli discover-portal `
  --config .\config.yaml `
  --customer DISCOVERY `
  --partner-query AS2TEST `
  --system-query AIC-DCE `
  --collect-all-pages `
  --max-pages 10
```

The exploration mode is read-only by default. It opens safe actions such as View, Show, Details, Open, and Edit pages only for evidence capture. It skips Delete, Update, Save, Submit, Add, Create, Reset, Enable, Disable, and Remove unless `--allow-unsafe-clicks` is explicitly set.

Outputs include:

```text
runs/<RUN_ID>/portal_exploration.json
runs/<RUN_ID>/network_events.jsonl
runs/<RUN_ID>/action_sequence.json
runs/<RUN_ID>/knowledge_graph/flow_knowledge_graph.html
runs/<RUN_ID>/report.html
```


## Full inventory audit/retry mode

For full Accounts, Partners, Domains and Systems export, run:

```powershell
python -m hip_id_agent.cli export-all `
  --config .\config.yaml `
  --customer FULL-INVENTORY `
  --max-pages 25 `
  --max-total-actions 1000
```

After the run, review:

```text
runs\<RUN_ID>\inventoryccounts.json
runs\<RUN_ID>\inventory\partners.json
runs\<RUN_ID>\inventory\domains.json
runs\<RUN_ID>\inventory\systems.json
runs\<RUN_ID>\inventory
elationships.json
runs\<RUN_ID>\inventory\inventory_request_audit.json
runs\<RUN_ID>\inventoryailed_requests.json
```

If `failed_requests.json` is not empty, the export is partial. The agent retries transient gateway errors and then attempts read-only UI fallback, but unresolved 500/permission errors still need a rerun or portal/API fix.

## Export complete Account/Partner/Domain/System details

```powershell
python -m hip_id_agent.cli export-all `
  --config .\config.yaml `
  --customer FULL-INVENTORY `
  --max-pages 25 `
  --max-total-actions 1000
```

Open these files after the run:

```powershell
start .\runs\<RUN_ID>\inventory\complete_inventory_tree.json
start .\runs\<RUN_ID>\inventory\accounts_with_partners_details.json
start .\runs\<RUN_ID>\inventory\domains_with_systems_details.json
start .\runs\<RUN_ID>\inventory\inventory_request_audit.json
```

## Full Partner/System ID inventory command

```powershell
python -m hip_id_agent.cli export-all `
  --config .\config.yaml `
  --customer FULL-INVENTORY `
  --max-pages 25 `
  --max-total-actions 1000
```

This does the same nested flow for every parent card: Account → Show Partner(s), Domain → View Domain/System(s), and saves all captured IDs/details under `runs\<RUN_ID>\inventory` and `data\hip_memory`.

## Watching export progress

While `export-all` runs, the terminal shows a live progress bar. The same heartbeat is saved here:

```powershell
Get-Content .\runs\<RUN_ID>\inventory\progress.json
Get-Content .\runs\<RUN_ID>\inventory\progress_events.jsonl -Tail 20
Get-Content .\runs\<RUN_ID>\inventory\progress_heartbeat.txt
```

If the heartbeat timestamp is not changing, the run is probably waiting on SSO, a gateway call, or a UI blocker.

## Uploading run evidence safely

Use the generated summary pack instead of uploading the full run folder:

```powershell
python -m hip_id_agent.cli summarize-run .\runs\<RUN_ID>
```

Upload:

```text
runs\<RUN_ID>\UPLOAD_THIS_SUMMARY.zip
```

Do not upload huge evidence unless requested:

```text
network_events.jsonl
network\
knowledge_graph\
dom_snapshots\
screenshots\
```


## Data Map KB command

```powershell
python -m hip_id_agent.cli discover-datamap-kb `
  --config .\config.yaml `
  --customer DATAMAP-KB `
  --input-json .\examples\uhaul_datamap_dummy_input.json `
  --known-map-id 1670
```

Upload `runs\<RUN_ID>\UPLOAD_DATAMAP_KB_SUMMARY.zip` for review.
