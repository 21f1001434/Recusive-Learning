from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from .runtime_env import load_runtime_env, configure_utf8_stdio
from rich.console import Console
from rich.table import Table

from .config import load_config
from .memory import HipMemory
from .models import RunContext
from .partner_system_flow import PartnerSystemIDFlow
from .inventory import PortalInventoryFlow
from .summarizer import summarize_run
from .datamap_kb import DataMapKBFlow, DATAMAPS_URL
from .doctype_kb import DocumentTypeKBFlow, DOCTYPES_URL
from .rules_kb import RuleKBFlow, RULES_URL
from .transport_profile_kb import TransportProfileKBFlow, TRANSPORT_PROFILES_URL
from .bizflow_kb import BizFlowKBFlow, BIZFLOWS_URL
from .input_entities import extract_input_entities, first_or_none
from .dummy_fill_e2e import FullDummyFillE2EFlow, FullDummyFillOptions, PHASE_SEQUENCE
from .safe_io import safe_write_json, install_safe_path_io, compact_path_component
from .autogen_runtime import autogen_runtime_status, assert_autogen_075
from .capability_graph import HIPCapabilityGraph
from .portal_discovery_flow import HIPPortalDiscoveryFlow
from .datamap_deep_discovery import DataMapDeepDiscoveryFlow
from .doctype_deep_discovery import DocumentTypeDeepDiscoveryFlow
from .rules_deep_discovery import RuleDeepDiscoveryFlow
from .transport_profile_deep_discovery import TransportProfileDeepDiscoveryFlow
from .bizflow_deep_discovery import BizFlowDeepDiscoveryFlow
from .full_deep_learning import FullHIPDeepLearningMission, HIPCapabilityCertifier, latest_certification
from .future_task_agent import HIPFutureTaskPlanner, HIPFutureTaskExecutor, MUTATION_CONFIRMATION
from .certified_future_task_agent import CertifiedHIPFutureTaskPlanner, CertifiedHIPFutureTaskExecutor
from .universal_portal_operator import UniversalPortalTaskPlanner, UniversalPortalTaskExecutor
from .persistent_operator import PersistentHIPOperator
from .change_governance import HIPChangeGovernance, GovernedCertifiedTaskExecutor
from .section_scope import resolve_section, section_catalog
from .live_runtime_certification import certify_live_runtime
from .final_mission_uat import run_final_mission_local_uat
from .production_e2e import ProductionE2EOrchestrator, build_request, HashChainedJournal

install_safe_path_io()
configure_utf8_stdio()

app = typer.Typer(help="HIP Portal Partner/System ID extractor with Dell SSO browser automation, Network-tab capture, click evidence, input enrichment, and reports.")
console = Console()


def load_dotenv() -> dict:
    """Backward-compatible CLI shim using deterministic HIP .env discovery."""
    return load_runtime_env(Path.cwd())


def make_run_id(customer: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", customer.strip().upper()).strip("-") or "HIP"
    # Keep the physical run directory comfortably below Windows MAX_PATH even
    # when the project itself lives under a long corporate OneDrive prefix.
    safe = compact_path_component(safe, max_len=28, fallback="HIP")
    return f"{safe}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def enforce_required_mcp_profile(cfg):
    """Apply the strict live-run contract for all three complementary MCPs.

    Playwright MCP and Chrome DevTools MCP attach to the shared authenticated
    Chrome session. HIP Intelligence MCP is local and browserless, but it is
    still mandatory when --require-mcp is selected.
    """
    cfg.mcp.browser_backend = "mcp"
    cfg.mcp.use_playwright_mcp = True
    cfg.mcp.use_chrome_devtools_mcp = True
    cfg.mcp.use_hip_intelligence_mcp = True
    cfg.mcp.hip_intelligence_mcp_required = True
    cfg.mcp.playwright_mcp_required_when_require_mcp = True
    cfg.mcp.chrome_devtools_mcp_direct_backend_enabled = True
    cfg.mcp.strict_runtime_required = True
    cfg.semantic_understanding.strict_external_evidence = True
    return cfg


@app.command("certify-live-runtime")
def certify_live_runtime_cmd(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    runs_dir: str = typer.Option("", help="Evidence root. Empty uses reporting.runs_dir from config."),
    target_url: str = typer.Option("", help="HIP URL to authenticate and prove. Empty uses portal.base_url."),
    ttl_seconds: int = typer.Option(0, help="Certificate validity. 0 uses live_runtime_certification.ttl_seconds."),
    require_pyautogui_mcp: bool = typer.Option(False, "--require-pyautogui-mcp/--allow-missing-pyautogui-mcp", help="Require the PyAutoGUI MCP read-only desktop smoke to pass."),
):
    """Certify the actual Windows/Dell runtime without mutating the HIP tenant.

    Opens the configured headed Edge/Chrome profile, waits for Dell SSO when needed,
    probes same-browser Playwright MCP and DevTools MCP, HIP Intelligence MCP, Dell AIA
    text/vision, read-only PyAutoGUI MCP, and the governed Python Playwright fallback.
    Adaptive config records unavailable optional witnesses as warnings; the strict MCP
    profile requires the complete witness stack. The signed-by-hash certificate is
    consumed by the normal Live GO/NO-GO gate.
    """
    load_dotenv()
    cfg = load_config(config)
    root = Path(runs_dir or cfg.reporting.runs_dir).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    live_cfg = getattr(cfg, "live_runtime_certification", None)
    ttl = int(ttl_seconds or getattr(live_cfg, "ttl_seconds", 3600) or 3600)
    result = asyncio.run(certify_live_runtime(
        config=cfg,
        runs_root=root,
        target_url=target_url or str(cfg.portal.base_url or ""),
        ttl_seconds=ttl,
        require_pyautogui_mcp=require_pyautogui_mcp,
    ))
    table = Table(title="HIP Live Runtime Certification")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for row in result.get("checks") or []:
        table.add_row(str(row.get("label") or row.get("id") or ""), "PASS" if row.get("pass") else ("WARN" if row.get("severity") == "warning" else "BLOCK"), str(row.get("detail") or ""))
    console.print(table)
    console.print(f"Decision: [bold]{result.get('decision')}[/bold]")
    console.print(f"Certificate: {result.get('run_dir')}/live_runtime_certificate.json")
    console.print(f"Latest receipt: {root / '.hip_runtime' / 'live_runtime_certificate.json'}")
    if not result.get("pass"):
        raise typer.Exit(code=2)


@app.command("certify-final-mission")
def certify_final_mission_cmd(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    output_dir: str = typer.Option("./final_mission_uat", "--output-dir", help="Directory for local final-mission UAT evidence."),
    browser_executable: str = typer.Option("", "--browser-executable", help="Optional Chrome/Chromium/Edge executable for the local mock UAT."),
    headless: bool = typer.Option(True, "--headless/--headed", help="Run the local mock browser headless or headed."),
):
    """Run the final seven-phase local browser UAT without contacting Dell.

    Exercises the same-page create flow for all seven phases, dynamic Document
    Type/Rule/Transport-Profile/BizFlow dropdown resolution, role-aware SFTP-HAFT
    deployment groups, the PyAutoGUI-MCP point interaction contract, persistent
    semantic world-model learning, and BizFlow Edit/Save/Validate/Deploy.
    """
    load_dotenv()
    cfg = load_config(config)
    result = asyncio.run(run_final_mission_local_uat(
        config=cfg, output_dir=output_dir, browser_executable=browser_executable, headless=headless,
    ))
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    console.print(f"UAT report: {Path(output_dir) / 'final_mission_local_uat.md'}")
    if not result.get("pass"):
        raise typer.Exit(code=2)


@app.command("extract-ids")
def extract_ids(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    customer: str = typer.Option(..., help="Customer key, e.g. UHAL."),
    partner_query: Optional[str] = typer.Option(None, help="Partner name/code to search in BizLink Partner page. If omitted, inferred from input_json when possible."),
    system_query: Optional[str] = typer.Option(None, help="System name/code to search in BizLink System page. If omitted, inferred from input_json when possible."),
    input_json: Optional[str] = typer.Option(None, help="Optional input.json to enrich with extracted partner/system IDs."),
    run_api: bool = typer.Option(False, help="After enrichment, call configured API requests. Keep api.dry_run=true until verified."),
    headless: Optional[bool] = typer.Option(None, help="Override config.portal.headless."),
    run_id: Optional[str] = typer.Option(None, help="Optional run ID."),
    partner_url: Optional[str] = typer.Option(None, help="Override partner navigation URL. Defaults to Dell BizLink Partner URL in config."),
    system_url: Optional[str] = typer.Option(None, help="Override system navigation URL. Defaults to Dell BizLink System URL in config."),
    explore_all_buttons: bool = typer.Option(False, help="After the normal search, safely explore visible Partner/System row/card action menus and detail buttons."),
    collect_all_pages: bool = typer.Option(False, help="When exploring, walk Next pagination up to --max-pages."),
    max_pages: int = typer.Option(5, help="Maximum Partner/System pages to crawl in exploration mode."),
    max_total_actions: int = typer.Option(80, help="Maximum safe exploration actions per run."),
    allow_unsafe_clicks: bool = typer.Option(False, help="Dangerous: allows Delete/Update/Save/Add clicks. Default false keeps the run read-only."),
):
    """Complete SSO, navigate Partner/System pages, capture clicks/network, extract IDs, save memory, and create report."""
    load_dotenv()
    cfg = load_config(config)
    if headless is not None:
        cfg.portal.headless = headless
    if partner_url:
        cfg.navigation.partner_candidate_paths.insert(0, partner_url)
        cfg.portal.base_url = partner_url
    if system_url:
        cfg.navigation.system_candidate_paths.insert(0, system_url)
    if explore_all_buttons or collect_all_pages:
        cfg.exploration.enabled = True
        cfg.exploration.collect_all_pages = collect_all_pages
        cfg.exploration.max_pages = max_pages
        cfg.exploration.max_total_actions = max_total_actions
        cfg.exploration.allow_unsafe_clicks = allow_unsafe_clicks
        cfg.exploration.prefer_nested_discovery = True
        cfg.exploration.search_first = False
        cfg.exploration.allow_direct_child_search_fallback = False
    if (not partner_query or not system_query) and input_json:
        entities = extract_input_entities(input_json)
        partner_query = partner_query or first_or_none(entities.get("source_partner", []) or entities.get("partner", []))
        system_query = system_query or first_or_none(entities.get("source_system", []) or entities.get("system", []))
    if not partner_query or not system_query:
        raise typer.BadParameter("partner-query and system-query are required unless they can be inferred from --input-json")
    rid = run_id or make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    screenshots_dir = run_dir / cfg.reporting.screenshot_dir_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    memory = HipMemory(cfg.reporting.memory_dir)
    ctx = RunContext(run_id=rid, customer=customer, partner_query=partner_query, system_query=system_query, run_dir=run_dir, screenshots_dir=screenshots_dir)
    flow = PartnerSystemIDFlow(cfg, memory)
    console.print(f"[bold]Starting HIP ID extraction[/bold] run_id={rid}")
    console.print("Browser will open. Complete Dell SSO if prompted. The agent then navigates Partner and System pages.")
    summary = asyncio.run(flow.run(ctx=ctx, input_json=input_json, run_api=run_api))
    _print_summary(summary)


@app.command("discover-portal")
def discover_portal(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    customer: str = typer.Option("DISCOVERY", help="Customer/run key used for memory/report grouping."),
    partner_query: Optional[str] = typer.Option(None, help="Optional partner example to search first, e.g. AS2TEST."),
    system_query: Optional[str] = typer.Option(None, help="Optional system/domain example to search first."),
    collect_all_pages: bool = typer.Option(True, help="Walk pagination in Partner/System pages."),
    max_pages: int = typer.Option(10, help="Maximum pages per area."),
    max_total_actions: int = typer.Option(160, help="Maximum safe exploration actions."),
    allow_unsafe_clicks: bool = typer.Option(False, help="Dangerous: click destructive actions. Keep false for read-only discovery."),
):
    """Safely discover Partner/System cards, menus, details, endpoints, IDs, and pagination.

    This is the mode for exploring the portal broadly. It skips Delete/Update/Save/Add by default,
    captures what every safe click does, saves accepted IDs to local memory, and creates KG/report evidence.
    """
    load_dotenv()
    cfg = load_config(config)
    cfg.exploration.enabled = True
    cfg.exploration.collect_all_pages = collect_all_pages
    cfg.exploration.max_pages = max_pages
    cfg.exploration.max_total_actions = max_total_actions
    cfg.exploration.allow_unsafe_clicks = allow_unsafe_clicks
    cfg.exploration.prefer_nested_discovery = True
    cfg.exploration.search_first = False
    cfg.exploration.allow_direct_child_search_fallback = False
    # Query values are optional in discovery mode; broad crawl still captures all network list IDs.
    pquery = partner_query or ""
    squery = system_query or ""
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    screenshots_dir = run_dir / cfg.reporting.screenshot_dir_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    memory = HipMemory(cfg.reporting.memory_dir)
    ctx = RunContext(run_id=rid, customer=customer, partner_query=pquery, system_query=squery, run_dir=run_dir, screenshots_dir=screenshots_dir)
    flow = PartnerSystemIDFlow(cfg, memory)
    console.print(f"[bold]Starting HIP portal discovery[/bold] run_id={rid}")
    summary = asyncio.run(flow.run(ctx=ctx, input_json=None, run_api=False))
    _print_summary(summary)


@app.command("export-all")
def export_all(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    customer: str = typer.Option("FULL-INVENTORY", help="Customer/run key used for memory/report grouping."),
    max_pages: int = typer.Option(25, help="Maximum pages to crawl per Partner/System area."),
    max_total_actions: int = typer.Option(1000, help="Maximum safe menu/detail actions during the crawl."),
    allow_unsafe_clicks: bool = typer.Option(False, help="Dangerous: allow destructive actions. Keep false for read-only inventory export."),
    full_ui_nested_walk: bool = typer.Option(True, help="Repeat the validated UI flow for every parent: Account -> Show Partner(s), Domain -> View Domain/System(s). Use --no-full-ui-nested-walk for API-only."),
    enrich_link_details: bool = typer.Option(True, help="Best-effort GET of per-ID detail links after list extraction. Use --no-enrich-link-details to skip."),
    write_heavy_evidence: bool = typer.Option(False, "--write-heavy-evidence/--no-write-heavy-evidence", help="Write full KG/raw evidence. Default false keeps runs uploadable and uses compact summaries."),
):
    """Extract ALL Accounts, Partners, Domains and Systems visible to the logged-in Dell SSO user.

    This command does not search for one child item. It crawls Partner and System pages,
    paginates parent cards, clicks safe nested actions such as Show Partner(s) and View Domain(s),
    captures Network-tab JSON, and exports all discovered entity IDs with parent relationships.
    """
    load_dotenv()
    cfg = load_config(config)
    cfg.exploration.enabled = True
    cfg.exploration.collect_all_pages = True
    cfg.exploration.max_pages = max_pages
    cfg.exploration.max_total_actions = max_total_actions
    cfg.exploration.allow_unsafe_clicks = allow_unsafe_clicks
    cfg.exploration.prefer_nested_discovery = True
    cfg.exploration.search_first = False
    cfg.exploration.allow_direct_child_search_fallback = False
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    screenshots_dir = run_dir / cfg.reporting.screenshot_dir_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    memory = HipMemory(cfg.reporting.memory_dir)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=screenshots_dir)
    flow = PortalInventoryFlow(cfg, memory, full_ui_nested_walk=full_ui_nested_walk, enrich_link_details=enrich_link_details, write_heavy_evidence=write_heavy_evidence)
    console.print(f"[bold]Starting full BizLink inventory export[/bold] run_id={rid}")
    console.print("This will crawl Partner/System pages, click safe nested actions, and export Accounts, Partners, Domains, Systems and Deployment Groups.")
    summary = asyncio.run(flow.run(ctx))
    counts = summary.get("inventory_counts") or summary.get("registry", {}).get("inventory_counts") or ctx.registry.get("inventory_counts") or {}
    table = Table(title="Full BizLink Inventory Summary")
    table.add_column("Entity")
    table.add_column("Count")
    for k in ["account", "partner", "domain", "system", "deployment_group"]:
        table.add_row(k, str(counts.get(k, 0)))
    console.print(table)
    console.print("\nInventory files:")
    for key, value in (ctx.registry or {}).items():
        if key.startswith("inventory_"):
            console.print(f"- {key}: {value}")
    console.print("\nReports:")
    for key, value in (ctx.registry.get("report_paths") or {}).items():
        console.print(f"- {key}: {value}")


@app.command("summarize-run")
def summarize_run_cmd(
    run_dir: str = typer.Argument(..., help="Existing run directory to summarize, e.g. runs/FULL-INVENTORY-20260705-223105"),
    create_zip: bool = typer.Option(True, help="Create UPLOAD_THIS_SUMMARY.zip inside the run directory."),
):
    """Create a small uploadable review pack from a large run folder.

    This excludes raw network_events.jsonl, DOM snapshots, screenshots and the full
    Knowledge Graph. Use this when the full run is too large to upload.
    """
    paths = summarize_run(Path(run_dir), create_zip=create_zip)
    console.print("[bold green]Summary agent completed.[/bold green]")
    for k, v in paths.items():
        console.print(f"- {k}: {v}")



@app.command("discover-datamap-kb")
def discover_datamap_kb(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    customer: str = typer.Option("DATAMAP-KB", help="Customer/run key for the Data Map KB run."),
    input_json: Optional[str] = typer.Option(None, help="Optional input.json containing objects.data_map values to use as the seed/dummy profile."),
    datamaps_url: str = typer.Option(DATAMAPS_URL, help="SecureLink Data Maps URL."),
    fill_dummy: bool = typer.Option(True, "--fill-dummy/--no-fill-dummy", help="Fill disposable dummy values into the + Add form but never save."),
    known_map_id: Optional[str] = typer.Option(None, help="Optional known prior mapId, e.g. 1670, to include in the KB summary."),
    crawl_old_datamaps: bool = typer.Option(True, "--crawl-old-datamaps/--no-crawl-old-datamaps", help="Learn/list old Data Maps from triggered listing APIs before opening + Add."),
    max_api_pages: int = typer.Option(25, help="Maximum observed Data Map API pages to replay when the list API has pagination parameters."),
    max_detail_rows: Optional[int] = typer.Option(None, help="Maximum old Data Map rows to detail-enrich for numeric mapId. Defaults to max-api-pages; use 250 for all current rows."),
    write_heavy_evidence: bool = typer.Option(False, "--write-heavy-evidence/--no-write-heavy-evidence", help="Write full raw evidence. Default false keeps output uploadable."),
):
    """Build a read-only KB of the SecureLink Data Map + Add form.

    The command first opens the Data Maps list and learns which APIs are triggered,
    extracts old Data Map IDs/details from those API responses, then clicks + Add,
    captures all visible controls, dropdown options, required fields, DOM event hints,
    network calls and fills dummy values. It never clicks Save/Create/Submit.
    """
    load_dotenv()
    cfg = load_config(config)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    if form_only:
        # Fast form-only mode: avoid the expensive 600+ row Transport Profile
        # inventory/ID/deep-profile phases and go straight to + Add wizard
        # capture. Users can still force a full run with --full-kb.
        crawl_old_transport_profiles = False
        capture_deep_profiles = False
        capture_runtime_details = False
        max_api_pages = 1
        max_detail_rows = 0
        max_deep_profile_rows = 0
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    screenshots_dir = run_dir / cfg.reporting.screenshot_dir_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=screenshots_dir)
    flow = DataMapKBFlow(cfg, datamaps_url=datamaps_url, fill_dummy=fill_dummy, known_map_id=known_map_id, write_heavy_evidence=write_heavy_evidence, crawl_old_datamaps=crawl_old_datamaps, max_api_pages=max_api_pages, max_detail_rows=max_detail_rows)
    console.print(f"[bold]Starting SecureLink Data Map KB discovery[/bold] run_id={rid}")
    console.print("This will learn old Data Map listing APIs/IDs, click + Add, capture form/dropdown/DOM/network evidence, fill dummy values, and NOT save/create the map.")
    summary = asyncio.run(flow.run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    console.print(f"Upload summary zip: {summary.get('files', {}).get('upload_zip', '')}")


@app.command("discover-doctype-kb")
def discover_doctype_kb(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    customer: str = typer.Option("DOCTYPE-KB", help="Customer/run key for the Document Type KB run."),
    input_json: Optional[str] = typer.Option(None, help="Optional input.json containing source/target document type values to use as seed/dummy profile."),
    doctypes_url: str = typer.Option(DOCTYPES_URL, help="SecureLink Document Types URL."),
    fill_dummy: bool = typer.Option(True, "--fill-dummy/--no-fill-dummy", help="Fill disposable dummy values into the + Add form but never save."),
    known_document_type_id: Optional[str] = typer.Option(None, help="Optional known prior documentTypeId, e.g. 10483, to include in the KB summary."),
    crawl_old_doctypes: bool = typer.Option(True, "--crawl-old-doctypes/--no-crawl-old-doctypes", help="Learn/list old Document Types from triggered listing APIs before opening + Add."),
    max_api_pages: int = typer.Option(250, help="Maximum observed Document Type API pages to replay when the list API has pagination parameters."),
    max_detail_rows: Optional[int] = typer.Option(None, help="Maximum old Document Type rows to detail-enrich/UI-learn for numeric documentTypeId. Omit for all discovered Document Types."),
    capture_deep_profiles: bool = typer.Option(True, "--capture-deep-profiles/--no-capture-deep-profiles", help="After ID learning, capture full per-Document-Type profiles from the details API, including identifiers, root/schema, attributes, and related usage evidence."),
    max_deep_profile_rows: Optional[int] = typer.Option(None, help="Maximum old Document Type rows to deep-profile. Omit for all discovered Document Types."),
    write_heavy_evidence: bool = typer.Option(False, "--write-heavy-evidence/--no-write-heavy-evidence", help="Write full raw evidence. Default false keeps output uploadable."),
):
    """Build a read-only KB of SecureLink Document Types + Add form.

    The command opens the Document Types list, learns the listing APIs, extracts old
    Document Type IDs/details, repeats safe UI row/action detail learning when list APIs
    hide numeric IDs, then clicks + Add, captures controls/dropdowns/required fields,
    fills dummy values, and never clicks Save/Create/Submit.
    """
    load_dotenv()
    cfg = load_config(config)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    if form_only:
        # Fast form-only mode: avoid the expensive 600+ row Transport Profile
        # inventory/ID/deep-profile phases and go straight to + Add wizard
        # capture. Users can still force a full run with --full-kb.
        crawl_old_transport_profiles = False
        capture_deep_profiles = False
        capture_runtime_details = False
        max_api_pages = 1
        max_detail_rows = 0
        max_deep_profile_rows = 0
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    screenshots_dir = run_dir / cfg.reporting.screenshot_dir_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=screenshots_dir)
    flow = DocumentTypeKBFlow(cfg, doctypes_url=doctypes_url, fill_dummy=fill_dummy, known_document_type_id=known_document_type_id, write_heavy_evidence=write_heavy_evidence, crawl_old_doctypes=crawl_old_doctypes, max_api_pages=max_api_pages, max_detail_rows=max_detail_rows, capture_deep_profiles=capture_deep_profiles, max_deep_profile_rows=max_deep_profile_rows)
    console.print(f"[bold]Starting SecureLink Document Type KB discovery[/bold] run_id={rid}")
    console.print("This will learn old Document Type listing APIs/IDs, capture full details profiles, click + Add, capture form/dropdown/DOM/network evidence, fill dummy values, and NOT save/create the document type.")
    summary = asyncio.run(flow.run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    console.print(f"Upload summary zip: {summary.get('files', {}).get('upload_zip', '')}")


@app.command("discover-rules-kb")
def discover_rules_kb(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    customer: str = typer.Option("RULES-KB", help="Customer/run key for the Rules KB run."),
    input_json: Optional[str] = typer.Option(None, help="Optional input.json containing objects.rule values to use as seed/dummy profile."),
    rules_url: str = typer.Option(RULES_URL, help="SecureLink Rules URL."),
    fill_dummy: bool = typer.Option(True, "--fill-dummy/--no-fill-dummy", help="Fill disposable dummy values into the + Add form but never save."),
    known_rule_id: Optional[str] = typer.Option(None, help="Optional known prior ruleId to include in the KB summary."),
    crawl_old_rules: bool = typer.Option(True, "--crawl-old-rules/--no-crawl-old-rules", help="Learn/list old Rules from triggered listing APIs before opening + Add."),
    max_api_pages: int = typer.Option(250, help="Maximum observed Rule API pages to replay when the list API has pagination parameters."),
    max_detail_rows: Optional[int] = typer.Option(None, help="Maximum old Rule rows to detail-enrich/UI-learn for numeric ruleId. Omit for all discovered Rules."),
    capture_deep_profiles: bool = typer.Option(True, "--capture-deep-profiles/--no-capture-deep-profiles", help="After ID learning, capture full per-Rule profiles from the details API, including conditions/actions/source-target doc types/mapping evidence."),
    max_deep_profile_rows: Optional[int] = typer.Option(None, help="Maximum old Rule rows to deep-profile. Omit for all discovered Rules."),
    write_heavy_evidence: bool = typer.Option(False, "--write-heavy-evidence/--no-write-heavy-evidence", help="Write full raw evidence. Default false keeps output uploadable."),
):
    """Build a read-only KB of SecureLink Rules + Add form.

    The command opens the Rules list, learns listing APIs, extracts old Rule IDs,
    repeats safe UI row/action detail learning when APIs hide numeric IDs, captures
    full per-Rule deep profiles, opens + Add, captures form/dropdown evidence,
    fills dummy values, and never clicks Save/Create/Submit.
    """
    load_dotenv()
    cfg = load_config(config)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    if form_only:
        # Fast form-only mode: avoid the expensive 600+ row Transport Profile
        # inventory/ID/deep-profile phases and go straight to + Add wizard
        # capture. Users can still force a full run with --full-kb.
        crawl_old_transport_profiles = False
        capture_deep_profiles = False
        capture_runtime_details = False
        max_api_pages = 1
        max_detail_rows = 0
        max_deep_profile_rows = 0
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    screenshots_dir = run_dir / cfg.reporting.screenshot_dir_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=screenshots_dir)
    flow = RuleKBFlow(cfg, rules_url=rules_url, fill_dummy=fill_dummy, known_rule_id=known_rule_id, write_heavy_evidence=write_heavy_evidence, crawl_old_rules=crawl_old_rules, max_api_pages=max_api_pages, max_detail_rows=max_detail_rows, capture_deep_profiles=capture_deep_profiles, max_deep_profile_rows=max_deep_profile_rows)
    console.print(f"[bold]Starting SecureLink Rules KB discovery[/bold] run_id={rid}")
    console.print("This will learn old Rule listing APIs/IDs, capture full Rule profiles, click + Add, capture form/dropdown/DOM/network evidence, fill dummy values, and NOT save/create the rule.")
    summary = asyncio.run(flow.run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    console.print(f"Upload summary zip: {summary.get('files', {}).get('upload_zip', '')}")


@app.command("discover-transport-profile-kb")
def discover_transport_profile_kb(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    customer: str = typer.Option("TRANSPORT-PROFILE-KB", help="Customer/run key for the Transport Profile KB run."),
    input_json: Optional[str] = typer.Option(None, help="Optional input.json containing transport profile values to use as seed/dummy profile."),
    transport_profiles_url: str = typer.Option(TRANSPORT_PROFILES_URL, help="SecureLink Transport Profiles URL."),
    fill_dummy: bool = typer.Option(True, "--fill-dummy/--no-fill-dummy", help="Fill disposable dummy values into the + Add form but never save."),
    form_only: bool = typer.Option(False, "--form-only/--full-kb", help="Capture only the + Add Transport Profile form/wizard. Skips old inventory, ID learning and deep-profile crawl."),
    known_transport_profile_id: Optional[str] = typer.Option(None, help="Optional known prior transportProfileId to include in the KB summary."),
    crawl_old_transport_profiles: bool = typer.Option(True, "--crawl-old-transport-profiles/--no-crawl-old-transport-profiles", help="Learn/list old Transport Profiles from listing APIs before opening + Add."),
    max_api_pages: int = typer.Option(250, help="Maximum observed Transport Profile API pages to replay when the list API has pagination parameters."),
    max_detail_rows: Optional[int] = typer.Option(None, help="Maximum old Transport Profile rows to detail-enrich/UI-learn for numeric transportProfileId. Omit for all discovered Transport Profiles."),
    capture_deep_profiles: bool = typer.Option(True, "--capture-deep-profiles/--no-capture-deep-profiles", help="After ID learning, capture full per-Transport Profile details including interfaceDetails, parameters, documentTypeDetails, partner/system/account evidence."),
    max_deep_profile_rows: Optional[int] = typer.Option(None, help="Maximum old Transport Profile rows to deep-profile. Omit for all discovered Transport Profiles."),
    write_heavy_evidence: bool = typer.Option(False, "--write-heavy-evidence/--no-write-heavy-evidence", help="Write full raw evidence. Default false keeps output uploadable."),
):
    """Build a read-only KB of SecureLink Transport Profiles + Add form.

    The command opens the Transport Profiles list, learns listing APIs, extracts old
    transportProfileIds, repeats safe UI row/action detail learning when APIs hide
    numeric IDs, captures full per-Transport Profile deep profiles, opens + Add,
    captures form/dropdown evidence, fills dummy values, and never clicks Save/Create/Submit.
    """
    load_dotenv()
    cfg = load_config(config)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    if form_only:
        # Fast form-only mode: avoid the expensive 600+ row Transport Profile
        # inventory/ID/deep-profile phases and go straight to + Add wizard
        # capture. Users can still force a full run with --full-kb.
        crawl_old_transport_profiles = False
        capture_deep_profiles = False
        capture_runtime_details = False
        max_api_pages = 1
        max_detail_rows = 0
        max_deep_profile_rows = 0
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    screenshots_dir = run_dir / cfg.reporting.screenshot_dir_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=screenshots_dir)
    flow = TransportProfileKBFlow(
        cfg,
        transport_profiles_url=transport_profiles_url,
        fill_dummy=fill_dummy,
        known_transport_profile_id=known_transport_profile_id,
        write_heavy_evidence=write_heavy_evidence,
        crawl_old_transport_profiles=crawl_old_transport_profiles,
        max_api_pages=max_api_pages,
        max_detail_rows=max_detail_rows,
        capture_deep_profiles=capture_deep_profiles,
        capture_runtime_details=capture_runtime_details,
        max_deep_profile_rows=max_deep_profile_rows,
    )
    console.print(f"[bold]Starting SecureLink Transport Profile KB discovery[/bold] run_id={rid}")
    if form_only:
        console.print("FORM-ONLY mode: skips old Transport Profile inventory/deep-profile crawl; opens + Add, advances the wizard, captures form/dropdown/required-field evidence, fills dummy values, and NOT save/create the transport profile.")
    else:
        console.print("This will learn old Transport Profile listing APIs/IDs, capture full profiles, click + Add, capture form/dropdown/DOM/network evidence, fill dummy values, and NOT save/create the transport profile.")
    summary = asyncio.run(flow.run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    console.print(f"Upload summary zip: {summary.get('files', {}).get('upload_zip', '')}")


@app.command("discover-bizflow-kb")
def discover_bizflow_kb(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    customer: str = typer.Option("BIZFLOW-KB", help="Customer/run key for the BizFlow KB run."),
    input_json: Optional[str] = typer.Option(None, help="Optional input.json containing biz_flow/source/target values to use as seed/dummy profile."),
    bizflows_url: str = typer.Option(BIZFLOWS_URL, help="BizExchange BizFlows URL."),
    fill_dummy: bool = typer.Option(True, "--fill-dummy/--no-fill-dummy", help="Fill disposable dummy values into the + Add form but never save/create."),
    form_only: bool = typer.Option(False, "--form-only/--full-kb", help="Capture only the + Add BizFlow form. Skips old inventory and deep-profile crawl."),
    crawl_old_bizflows: bool = typer.Option(True, "--crawl-old-bizflows/--no-crawl-old-bizflows", help="Learn/list old BizFlows from visible grid/API before opening + Add."),
    max_api_pages: int = typer.Option(250, help="Maximum BizFlow UI/list pages to crawl."),
    capture_deep_profiles: bool = typer.Option(True, "--capture-deep-profiles/--no-capture-deep-profiles", help="Best-effort per-BizFlow definition/detail capture after inventory."),
    capture_runtime_details: bool = typer.Option(True, "--capture-runtime-details/--no-capture-runtime-details", help="Open each BizFlow View screen and capture expanded runtime/deployment sections: environment tabs, Source/Target Details, Flow Servers, Interface Details, TP-ROUTING and FLOW-ROUTING."),
    max_deep_profile_rows: Optional[int] = typer.Option(None, help="Maximum old BizFlow rows to deep-profile. Omit for all discovered BizFlows."),
    write_heavy_evidence: bool = typer.Option(False, "--write-heavy-evidence/--no-write-heavy-evidence", help="Write full raw evidence. Default false keeps output uploadable."),
):
    """Build a read-only KB of BizExchange BizFlows + Add form.

    The command opens the BizFlows list, captures listing/API/UI evidence, crawls
    pagination, performs best-effort deep-profile capture, opens + Add, captures
    form/dropdown/required-field evidence, fills dummy values, and never clicks
    Save/Create/Submit/Delete.
    """
    load_dotenv()
    cfg = load_config(config)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    if form_only:
        crawl_old_bizflows = False
        capture_deep_profiles = False
        capture_runtime_details = False
        max_api_pages = 1
        max_deep_profile_rows = 0
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    screenshots_dir = run_dir / cfg.reporting.screenshot_dir_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=screenshots_dir)
    flow = BizFlowKBFlow(
        cfg,
        bizflows_url=bizflows_url,
        fill_dummy=fill_dummy,
        write_heavy_evidence=write_heavy_evidence,
        crawl_old_bizflows=crawl_old_bizflows,
        max_api_pages=max_api_pages,
        capture_deep_profiles=capture_deep_profiles,
        capture_runtime_details=capture_runtime_details,
        max_deep_profile_rows=max_deep_profile_rows,
        form_only=form_only,
    )
    console.print(f"[bold]Starting BizExchange BizFlow KB discovery[/bold] run_id={rid}")
    if form_only:
        console.print("FORM-ONLY mode: skips old BizFlow inventory/deep-profile crawl; opens + Add, captures form/dropdown/required-field evidence, fills dummy values, and NOT save/create the BizFlow.")
    else:
        console.print("This will learn old BizFlow inventory/listing APIs, capture definition + runtime/deployment deep profiles, click + Add, capture every form/dropdown/required-field it can see, fill dummy values, and NOT save/create/deploy the BizFlow.")
    summary = asyncio.run(flow.run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    console.print(f"Upload summary zip: {summary.get('files', {}).get('upload_zip', '')}")


@app.command("run-full-dummy-fill")
def run_full_dummy_fill(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    customer: str = typer.Option("FULL-DUMMY-FILL", help="Customer/run key for the full dummy-fill E2E run."),
    input_json: str = typer.Option(..., help="Input JSON/TXT containing all objects: data_map, source/target document types, rule, source/target transport profiles, and biz_flow."),
    full_kb_context: bool = typer.Option(False, "--full-kb-context/--fast-form-only", help="Use full old KB/context capture before form fill. Fast mode goes straight to + Add where supported."),
    vision_verify: bool = typer.Option(True, "--vision-verify/--no-vision-verify", help="Generate vision prompts and optionally call an OpenAI-compatible vision endpoint if HIP_VISION_ENDPOINT/HIP_VISION_TOKEN/HIP_VISION_MODEL are configured."),
    phases: Optional[str] = typer.Option(None, help="Comma-separated phase list. Default: data_map,source_document_type,target_document_type,rule,source_transport_profile,target_transport_profile,biz_flow"),
    rules_only: bool = typer.Option(False, "--rules-only", help="Run only the Rule phase. Equivalent to --phases rule and intended for focused live repair after Data Map and Document Type are already proven."),
    write_heavy_evidence: bool = typer.Option(False, "--write-heavy-evidence/--no-write-heavy-evidence", help="Write full raw evidence. Default false keeps output uploadable."),
    save_replay_blueprint: bool = typer.Option(True, "--save-replay-blueprint/--no-save-replay-blueprint", help="Save per-phase fast-fill blueprints, replay manifest, and agent playbook so a future agent can fill the same forms quickly without re-crawling."),
    max_dropdown_options_per_field: int = typer.Option(250, help="Maximum dropdown options to store per field in fast-fill blueprints."),
    golden_screenshot_dir: Optional[str] = typer.Option(None, "--golden-screenshot-dir", help="Folder containing human-approved correctly filled screenshots to use as golden visual replication targets."),
    upload_assets_dir: Optional[str] = typer.Option("./uploads", "--upload-assets-dir", help="Folder containing files to attach to file-upload controls, e.g. JAR/XBM/XML/XLSX. Default: ./uploads."),
    require_mcp: bool = typer.Option(False, "--require-mcp/--allow-executor-fallback", help="Strict mode: require Playwright MCP, Chrome DevTools MCP, and HIP Intelligence MCP. Default adaptive mode uses every healthy MCP but can continue with PyAutoGUI MCP, Playwright MCP, or governed Python Playwright fallback."),
    exploration_agent: bool = typer.Option(True, "--exploration-agent/--no-exploration-agent", help="Explore HIP parent-value branches, child controls, row creation effects and persist a plan-ready form knowledge graph."),
    explore_parent_branches: bool = typer.Option(True, "--explore-parent-branches/--observe-current-branch-only", help="Safely enumerate parent dropdown values and record the child controls revealed by each value before deterministic fill."),
    exploration_max_values_per_parent: int = typer.Option(30, "--exploration-max-values-per-parent", min=1, max=100, help="Maximum parent values to live-explore per structural parent control; larger reference dropdowns remain catalogued without unsafe exhaustive selection."),
    section_judge: bool = typer.Option(True, "--section-judge/--no-section-judge", help="After every phase and every BizFlow section, require deterministic DOM plus Dell AIA judge approval before moving forward."),
    require_text_judge: bool = typer.Option(True, "--require-text-judge/--no-require-text-judge", help="Require Dell AIA gpt-oss-120b text/state judge approval."),
    require_vision_judge: bool = typer.Option(True, "--require-vision-judge/--no-require-vision-judge", help="Require Dell AIA multimodal vision judge approval. Configure HIP_VISION_MODEL/AIA_VISION_MODEL/VISION_MODEL_NAME."),
    vision_model: Optional[str] = typer.Option(None, "--vision-model", help="Dell AIA multimodal deployment. Optional: when omitted, explicit environment aliases are used, then a bounded capability-probed Gemma/Pixtral discovery is attempted."),
    vision_endpoint: Optional[str] = typer.Option(None, "--vision-endpoint", help="Optional separate Dell AIA vision base URL or /chat/completions endpoint. Existing BASE_URL is reused when omitted."),
    vision_auto_discovery: bool = typer.Option(True, "--vision-auto-discovery/--no-vision-auto-discovery", help="When no explicit vision model is supplied, safely probe reviewed Dell AIA multimodal candidates before opening the portal."),
    section_judge_max_repairs: int = typer.Option(2, "--section-judge-max-repairs", min=0, max=5, help="Maximum deterministic repair/refill attempts for a failed live section before blocking progression."),
    portal_brain: bool = typer.Option(True, "--portal-brain/--no-portal-brain", help="Use persistent long-term HIP Portal memory from all prior judged runs when creating deterministic plans."),
    rebuild_portal_brain: bool = typer.Option(False, "--rebuild-portal-brain", help="Rebuild persistent portal brain from historical run evidence before this run."),
    brain_dir: Optional[str] = typer.Option(None, "--brain-dir", help="Optional persistent brain directory. Relative values are stored under reporting.memory_dir."),
    import_unified_kb: bool = typer.Option(True, "--import-unified-kb/--no-import-unified-kb", help="Import the reviewed HIP Unified Deep KB into long-term memory before deterministic planning."),
    unified_kb_path: Optional[str] = typer.Option(None, "--unified-kb-path", help="Path to HIP_Unified_Deep_KB.json. Defaults to brain.unified_kb_path."),
    unified_kg_path: Optional[str] = typer.Option(None, "--unified-kg-path", help="Path to HIP_Unified_Knowledge_Graph.json. Defaults to brain.unified_kg_path."),
    require_unified_kb: bool = typer.Option(True, "--require-unified-kb/--allow-missing-unified-kb", help="Fail before opening HIP forms when the reviewed KB cannot be imported."),
    self_heal_kb: bool = typer.Option(True, "--self-heal-kb/--no-self-heal-kb", help="Revalidate canonical KB facts through live Playwright-MCP exploration and apply only dual-judge-approved corrections."),
    kb_repair_min_confirmations: int = typer.Option(1, "--kb-repair-min-confirmations", min=1, max=10, help="Validated live confirmations required to add a missing KB fact."),
    kb_supersede_min_confirmations: int = typer.Option(2, "--kb-supersede-min-confirmations", min=1, max=10, help="Repeated validated live confirmations required before a proven-wrong canonical fact is superseded."),
    revalidate_known_parent_branches: bool = typer.Option(True, "--revalidate-known-parent-branches/--only-explore-unknown-parent-branches", help="Re-explore known parent-value branches because the reviewed KB or portal behavior may have changed."),
    runtime_self_heal: bool = typer.Option(True, "--runtime-self-heal/--no-runtime-self-heal", help="Use a bounded ReAct self-heal loop that gathers MCP/DOM/network evidence, applies only safe no-save repairs, reruns the interrupted phase, and requires the independent judge to pass."),
    runtime_self_heal_max_attempts: int = typer.Option(5, "--runtime-self-heal-max-attempts", min=1, max=8, help="Maximum complete attempts for one phase, including the initial attempt and safe self-heal replays."),
    runtime_self_heal_max_total_repairs: int = typer.Option(20, "--runtime-self-heal-max-total-repairs", min=1, max=50, help="Maximum safe runtime repairs across the complete run."),
    runtime_self_heal_until_complete: bool = typer.Option(False, "--runtime-self-heal-until-complete/--bounded-runtime-self-heal", help="Use progress-driven safe exploration/exploitation until the phase passes. Attempt count may extend, but repeated no-progress states and the phase wall-clock limit are always enforced; proven unsafe/mutating actions remain blocked."),
    all_phases_until_complete: bool = typer.Option(False, "--all-phases-until-complete", help="Run the complete seven-phase sequence with unlimited progress-driven self-heal. This cannot be combined with --rules-only or a partial --phases list."),
    autonomous_mission: bool = typer.Option(False, "--autonomous-mission", help="One-switch autonomous web-agent profile: complete all seven phases, self-heal until every deterministic/text/vision contract passes, capture forensic MCP evidence, and auto-resume judge-approved completed phases after interruption."),
    live_witness: bool = typer.Option(False, "--live-witness/--standard-mission", help="Run the integrated live browser agent in strictly non-mutating witness mode: SSO + AutoWebGLM + Playwright MCP + DevTools MCP + text/vision judges + phase traversal/fill/verify, but never click Create/Save/Submit/Finish/Deploy/Delete or send mutating API requests."),
    resume_run: Optional[str] = typer.Option(None, "--resume-run", help="Adopt judge-approved completed phases from this prior run directory instead of replaying them. Adoption is fail-closed: each phase needs its exact-completion lock, a passing section-judge gate, and the persisted verification payload; anything less re-executes live."),
    auto_resume: bool = typer.Option(False, "--auto-resume/--no-auto-resume", help="Automatically locate the newest resumable prior run in the runs directory and adopt its judge-approved completed phases before starting live execution."),
    forensic_evidence: bool = typer.Option(True, "--forensic-evidence/--standard-evidence", help="Capture local DOM/form state plus Playwright MCP and Chrome DevTools MCP accessibility, network and console evidence for every failed attempt, and save judge-gated deterministic trajectories after success."),
    maximum_observability: bool = typer.Option(True, "--maximum-observability/--standard-observability", help="Capture read-only structural DOM, hidden and visible controls, DDS/ARIA ownership, mutation and UI-event timelines, resources, input-to-control coverage and deterministic replay readiness on failures and successful phases."),
    agentq_crawler_fusion: bool = typer.Option(False, "--agentq-crawler-fusion", help="Apply the Dell.com crawler's AgentQ web representation, deterministic safety gate, actor/critic ranking, UCB exploration/exploitation, mutation-settle observation and API trajectory memory to every HIP form."),
    dual_ui_api: bool = typer.Option(False, "--dual-ui-api/--ui-only", help="After deterministic UI fill, extract form-open/fill API contracts, build the exact UI/API payload crosswalk, export OpenAPI/Postman artifacts and optionally execute an observed API request."),
    capture_submit_api: bool = typer.Option(False, "--capture-submit-api/--no-capture-submit-api", help="Capture the exact Create/Save request after a judged UI fill by aborting all mutation requests before backend delivery. This does not save the UI form."),
    api_mode: str = typer.Option("capture", "--api-mode", help="Observed API path mode: capture, dry_run, validate, or write. Write requires --allow-api-mutation and HIP_ALLOW_API_MUTATION=YES."),
    allow_api_mutation: bool = typer.Option(False, "--allow-api-mutation", help="Allow the separately replayed observed API request to mutate HIP. Requires --api-mode write and HIP_ALLOW_API_MUTATION=YES; UI submit capture remains blocked."),
    require_api_capture: bool = typer.Option(False, "--require-api-capture/--api-capture-best-effort", help="Fail closed and self-heal when a phase has no observed API contract or incomplete UI/API evidence."),
    runs_dir: Optional[str] = typer.Option(None, "--runs-dir", help="Optional short output directory, e.g. C:/hip_runs, to avoid Windows/OneDrive MAX_PATH failures."),
):
    """Open every HIP link, click + Add, fill safe dummy values, screenshot, and verify.

    Phase coverage:
      Data Map -> Source Document Type -> Target Document Type -> Rule ->
      Source Transport Profile -> Target Transport Profile -> BizFlow.

    Safety: normal UI execution never sends Save/Create/Submit/Delete/Deploy. When
    --capture-submit-api is enabled, the final UI button may be clicked only while
    Playwright aborts every mutation request before backend delivery. Real API write
    additionally requires explicit CLI and environment confirmation.
    """
    load_dotenv()
    if vision_model and str(vision_model).strip():
        os.environ["HIP_VISION_MODEL"] = str(vision_model).strip()
    if vision_endpoint and str(vision_endpoint).strip():
        os.environ["HIP_VISION_ENDPOINT"] = str(vision_endpoint).strip()
    os.environ["HIP_VISION_AUTO_DISCOVERY"] = "true" if vision_auto_discovery else "false"
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    cfg.brain.enabled = bool(portal_brain)
    if brain_dir:
        cfg.brain.directory = brain_dir
    cfg.brain.auto_import_unified_kb = bool(import_unified_kb)
    cfg.brain.unified_kb_required = bool(require_unified_kb)
    cfg.brain.self_heal_kb = bool(self_heal_kb)
    cfg.brain.kb_repair_min_confirmations = int(kb_repair_min_confirmations)
    cfg.brain.kb_supersede_min_confirmations = int(kb_supersede_min_confirmations)
    cfg.brain.revalidate_known_parent_branches = bool(revalidate_known_parent_branches)
    cfg.runtime_self_heal.max_phase_attempts = int(runtime_self_heal_max_attempts)
    cfg.runtime_self_heal.max_total_repairs = int(runtime_self_heal_max_total_repairs)
    if autonomous_mission:
        if rules_only or phases:
            raise typer.BadParameter("--autonomous-mission owns the complete seven-phase objective and cannot be combined with --rules-only or --phases")
        # V231 autonomous mission: all seven phases are goal-driven and keep applying
        # safe adaptive recovery while progress is being made. This is not an
        # unbounded loop: repeated-no-progress and phase wall-clock guards remain
        # mandatory, and final mutating portal actions are still separately gated.
        all_phases_until_complete = False
        runtime_self_heal = True
        runtime_self_heal_until_complete = True
        forensic_evidence = True
        maximum_observability = True
        agentq_crawler_fusion = True
        dual_ui_api = True
        capture_submit_api = True
        # UI/API capture is evidence by default, not a progression blocker.  A
        # caller may still opt into --require-api-capture or --require-mcp explicitly.
        # V232 intentionally does NOT force the full three-MCP stack: the autonomous
        # mission is allowed to execute with the healthy PyAutoGUI/Playwright quorum.
        if not golden_screenshot_dir:
            default_golden = Path("./golden_screenshots/UHAUL-POASN")
            if default_golden.is_dir():
                golden_screenshot_dir = str(default_golden)
        # A user asking for a full mission expects every selected phase to run
        # against the current input.  Never silently adopt an older incomplete
        # mission.  Resume remains available only when --resume-run or the explicit
        # --auto-resume flag was requested by the caller.
        # AutoGen 0.7.5 is a required reasoning layer for the autonomous mission.
        # This makes the form planner, text judge, dependency analyst, and runtime
        # recovery advisor use AgentChat instead of silently falling back to raw REST.
        os.environ["AIA_USE_AUTOGEN"] = "true"
        os.environ["HIP_USE_LLM_FORM_PLANNER"] = "true"
        os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
        # V231: one autonomous goal runtime owns completion for all seven form phases.
        cfg.autonomous_form.enabled = True
        cfg.autonomous_form.apply_to_all_form_phases = True
        cfg.autonomous_form.apply_to_data_map = True
        cfg.autonomous_form.apply_to_document_types = True
        cfg.autonomous_form.apply_to_rules = True
        cfg.autonomous_form.apply_to_transport_profiles = True
        cfg.autonomous_form.apply_to_biz_flow = True
    if all_phases_until_complete:
        runtime_self_heal = True
        runtime_self_heal_until_complete = True
    cfg.runtime_self_heal.enabled = bool(runtime_self_heal)
    cfg.runtime_self_heal.until_complete = bool(runtime_self_heal_until_complete)
    cfg.runtime_self_heal.forensic_evidence = bool(forensic_evidence)
    cfg.portal_learning.maximum_observability_enabled = bool(maximum_observability)
    valid_api_modes = {"capture", "dry_run", "validate", "write"}
    api_mode = str(api_mode or "capture").strip().lower()
    if api_mode not in valid_api_modes:
        raise typer.BadParameter(f"--api-mode must be one of {sorted(valid_api_modes)}")
    if agentq_crawler_fusion:
        dual_ui_api = True
        capture_submit_api = True
        forensic_evidence = True
        maximum_observability = True
        cfg.portal_learning.maximum_observability_enabled = True
        cfg.runtime_self_heal.forensic_evidence = True
    if live_witness:
        # Final pre-UAT witness safety boundary.  It is deliberately applied
        # after the AgentQ profile because that profile normally enables submit
        # capture.  Witness mode must never click a mutation control even behind
        # an abort barrier, and it must never authorize API mutation.
        capture_submit_api = False
        require_api_capture = False
        allow_api_mutation = False
        api_mode = "capture"
        os.environ["HIP_LIVE_WITNESS"] = "true"
    if api_mode == "write" and not allow_api_mutation:
        raise typer.BadParameter("--api-mode write requires --allow-api-mutation and HIP_ALLOW_API_MUTATION=YES")
    cfg.api.capture_observed_contracts = bool(agentq_crawler_fusion or dual_ui_api or capture_submit_api)
    cfg.api.dual_ui_api = bool(dual_ui_api)
    cfg.api.capture_submit_payload = bool(capture_submit_api)
    cfg.api.execution_mode = api_mode
    cfg.api.require_capture_for_completion = bool(require_api_capture)
    cfg.api.allow_mutating_methods = bool(allow_api_mutation)
    if unified_kb_path:
        cfg.brain.unified_kb_path = unified_kb_path
    if unified_kg_path:
        cfg.brain.unified_kg_path = unified_kg_path
    cfg.exploration.form_knowledge_agent_enabled = bool(exploration_agent)
    cfg.exploration.explore_parent_value_branches = bool(explore_parent_branches)
    cfg.exploration.max_values_per_parent = int(exploration_max_values_per_parent)
    if require_mcp:
        enforce_required_mcp_profile(cfg)
    else:
        # V232 hybrid quorum: external MCPs remain enabled/attempted, but an
        # unavailable witness cannot prevent safe form execution.
        cfg.mcp.strict_runtime_required = False
        cfg.mcp.hip_intelligence_mcp_required = False
        cfg.semantic_understanding.strict_external_evidence = False
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    selected_phases = list(PHASE_SEQUENCE)
    if all_phases_until_complete and rules_only:
        raise typer.BadParameter("--all-phases-until-complete cannot be combined with --rules-only")
    if resume_run and auto_resume:
        raise typer.BadParameter("--resume-run and --auto-resume cannot be combined; pick one")
    if resume_run and not Path(resume_run).is_dir():
        raise typer.BadParameter(f"--resume-run directory does not exist: {resume_run}")
    if rules_only:
        selected_phases = ["rule"]
    if phases:
        requested = [p.strip() for p in phases.split(",") if p.strip()]
        bad = [p for p in requested if p not in PHASE_SEQUENCE]
        if bad:
            raise typer.BadParameter(f"Unknown phase(s): {bad}. Valid phases: {list(PHASE_SEQUENCE)}")
        if rules_only and requested != ["rule"]:
            raise typer.BadParameter("--rules-only cannot be combined with phases other than 'rule'")
        if all_phases_until_complete and requested != list(PHASE_SEQUENCE):
            raise typer.BadParameter("--all-phases-until-complete requires the complete default phase sequence; omit --phases")
        selected_phases = requested
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    screenshots_dir = run_dir / cfg.reporting.screenshot_dir_name
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=screenshots_dir)
    if autonomous_mission:
        autogen_status = autogen_runtime_status(verify_imports=True)
        safe_write_json(run_dir / "autogen_preflight.json", autogen_status)
        ctx.registry["autogen_required"] = True
        ctx.registry["autogen_required_version"] = "0.7.5"
        ctx.registry["autogen_preflight"] = autogen_status
        if not autogen_status.get("pass"):
            console.print("[bold red]AutoGen 0.7.5 preflight failed.[/bold red]")
            console.print_json(json.dumps(autogen_status, indent=2, ensure_ascii=False, default=str))
            console.print(autogen_status.get("install_command") or "")
            raise typer.Exit(code=2)
        console.print("[bold green]AutoGen AgentChat 0.7.5 preflight passed[/bold green]: planner/judge/recovery reasoning will use Microsoft AutoGen.")
    else:
        ctx.registry["autogen_required"] = False
    if require_mcp:
        from .browser_backend import validate_dual_browser_mcps
        console.print("[bold]Three MCPs required:[/bold] validating Playwright MCP + Chrome DevTools MCP; HIP Intelligence MCP is required and starts before the shared browser opens...")
        mcp_result = asyncio.run(validate_dual_browser_mcps(cfg, run_dir / "mcp_preflight"))
        safe_write_json(run_dir / "mcp_preflight" / "dual_mcp_capabilities.json", mcp_result)
        ctx.registry["mcp_required"] = True
        ctx.registry["requested_browser_backend"] = "dual-mcp"
        ctx.registry["browser_backend_used"] = "dual-mcp" if mcp_result.get("all_required_available") else "mcp-unavailable"
        ctx.registry["mcp_runtime_adapter"] = "@playwright/mcp + chrome-devtools-mcp + local HIP Intelligence MCP over stdio"
        ctx.registry["mcp_required_count"] = 3
        ctx.registry["hip_intelligence_mcp_required"] = bool(cfg.mcp.hip_intelligence_mcp_required)
        ctx.registry["mcp_preflight"] = mcp_result
        if not mcp_result.get("all_required_available"):
            console.print_json(json.dumps(mcp_result, indent=2, ensure_ascii=False, default=str))
            raise typer.Exit(code=2)
    else:
        ctx.registry["mcp_required"] = False
        ctx.registry["requested_browser_backend"] = cfg.mcp.browser_backend
    flow = FullDummyFillE2EFlow(
        cfg,
        FullDummyFillOptions(
            full_kb_context=full_kb_context,
            vision_verify=vision_verify,
            phases=selected_phases,
            write_heavy_evidence=write_heavy_evidence,
            save_replay_blueprint=save_replay_blueprint,
            max_dropdown_options_per_field=max_dropdown_options_per_field,
            golden_screenshot_dir=golden_screenshot_dir,
            upload_assets_dir=upload_assets_dir,
            section_judge=section_judge,
            require_text_judge=require_text_judge,
            require_vision_judge=require_vision_judge,
            section_judge_max_repairs=section_judge_max_repairs,
            section_judge_fail_closed=True,
            portal_brain_enabled=portal_brain,
            rebuild_portal_brain=rebuild_portal_brain,
            runtime_self_heal_enabled=runtime_self_heal,
            runtime_self_heal_max_phase_attempts=runtime_self_heal_max_attempts,
            runtime_self_heal_until_complete=runtime_self_heal_until_complete,
            forensic_evidence=forensic_evidence,
            agentq_crawler_fusion=agentq_crawler_fusion,
            dual_ui_api=dual_ui_api,
            capture_submit_api=capture_submit_api,
            api_mode=api_mode,
            require_api_capture_for_completion=require_api_capture,
            strict_mission_assurance=bool(autonomous_mission and require_api_capture),
            resume_run_dir=resume_run,
            auto_resume=auto_resume,
            continue_after_phase_block=bool(autonomous_mission),
            live_witness_mode=bool(live_witness),
        ),
    )
    console.print(f"[bold]Starting HIP full dummy-fill E2E[/bold] run_id={rid}")
    if autonomous_mission:
        console.print("Autonomous mission profile enabled: completion-first Data Map through BizFlow execution with Microsoft AutoGen AgentChat 0.7.5 reasoning, AutoWebGLM primary decisions, AgentQ gates, bounded self-heal, three-channel MCP evidence, text/vision judges, and deterministic phase completion checkpoints.")
    if live_witness:
        console.print("[bold cyan]LIVE WITNESS MODE[/bold cyan]: real Dell SSO and all selected HIP phases will be exercised through AutoWebGLM + Playwright MCP, but mutation controls and mutating API requests are prohibited. Submit capture is disabled.")
    if resume_run:
        console.print(f"Mission resume requested from prior run: {resume_run}. Judge-approved completed phases are adopted fail-closed; every unproven phase re-executes live.")
    elif auto_resume:
        console.print("Auto-resume enabled: the newest resumable prior run in the runs directory will be adopted fail-closed before live execution starts.")
    if selected_phases == ["rule"]:
        console.print("Rules-only mode: opens only Create Rule, fills the exact Rule/Conditions/Actions state, uses golden-image feedback, and never clicks Save/Create/Submit/Delete/Deploy.")
    elif all_phases_until_complete:
        console.print("All-phases until-complete mode: Data Map, Source/Target Document Type, Rule, Source/Target Transport Profile and BizFlow each self-heal until their exact deterministic, text and vision contracts pass.")
    else:
        console.print("This opens every phase link, clicks + Add, fills dummy values, captures screenshots/DOM/network evidence, verifies required-field fill, and never clicks Save/Create/Submit/Delete/Deploy.")
    if save_replay_blueprint:
        if bool(getattr(cfg.autonomous_form, "semantic_replay_blueprint_only", True)) and bool(getattr(cfg.autonomous_form, "never_persist_selectors_or_coordinates", True)):
            console.print("Adaptive semantic replay blueprint will be saved: labels/roles/business goal/evidence only; transient selectors and screen coordinates are stripped.")
        else:
            console.print("Fast replay blueprint will be saved with current-run selector evidence for compatibility mode.")
    if golden_screenshot_dir:
        console.print(f"Golden replication screenshots will be attached from: {golden_screenshot_dir}")
    if upload_assets_dir:
        console.print(f"Upload assets will be attached from: {upload_assets_dir}")
    if portal_brain:
        console.print(f"Persistent HIP Portal brain enabled: directory={cfg.brain.directory}; historical runs will be merged; only judge-approved live knowledge is promoted." + (" Rebuild requested." if rebuild_portal_brain else ""))
        if import_unified_kb:
            console.print(f"Reviewed Unified HIP KB will seed canonical page/field/gate knowledge: {cfg.brain.unified_kb_path}")
        if self_heal_kb:
            console.print(f"Adaptive KB repair enabled: revalidate known branches={revalidate_known_parent_branches}; add confirmations={kb_repair_min_confirmations}; supersede confirmations={kb_supersede_min_confirmations}; original KB preserved.")
    if exploration_agent:
        console.print(f"Portal exploration agent enabled: parent-value branch discovery={'on' if explore_parent_branches else 'current branch only'}; max values/parent={exploration_max_values_per_parent}; PyAutoGUI MCP primary physical executor; Playwright MCP deterministic fallback/verification.")
    if section_judge:
        console.print(f"Strict section judge enabled: DOM + text judge={'required' if require_text_judge else 'optional'} + vision judge={'required' if require_vision_judge else 'optional'}; max repairs={section_judge_max_repairs}.")
    if runtime_self_heal:
        if runtime_self_heal_until_complete:
            console.print("Runtime self-heal until-complete enabled for every selected phase: no attempt/signature/repair-count limit; safe exploration and exploitation alternate using the earliest unresolved dependency gate, MCP/DOM/network evidence, Dell AIA text advice, golden-image vision feedback, and independent final judges.")
        else:
            console.print(
                f"Runtime self-heal enabled: max phase attempts={runtime_self_heal_max_attempts}; "
                f"max total safe repairs={runtime_self_heal_max_total_repairs}; MCP/DOM/network evidence + KB-guided ReAct plan; independent judge required after every repair."
            )
    if forensic_evidence:
        console.print("Forensic evidence enabled: each failure captures local form/Angular state, Playwright MCP accessibility/network/console, Chrome DevTools MCP DOM/network/console, action/state transitions, golden-image deltas and the chosen recovery plan. Successful paths are promoted only after all judges pass.")
    if maximum_observability:
        console.print("Maximum observability enabled: structural DOM, hidden/visible controls, DDS ownership, mutation/UI-event timelines, resource inventory, input coverage and replay-readiness evidence are captured without storing credentials or customer values in reusable memory.")
    if agentq_crawler_fusion:
        console.print("Dell.com crawler fusion enabled: AgentQ actor/critic + UCB exploration/exploitation + mutation-settle observation + durable UI/API trajectory memory are applied to all HIP forms. The LLM can rank observed actions but cannot invent endpoints, selectors or payload keys.")
    if dual_ui_api:
        console.print(f"Dual UI/API mode enabled: API mode={api_mode}; form-open and UI-fill traffic will be catalogued, cross-walked to input.json, and exported as OpenAPI/Postman evidence." + (" Exact submit payload capture is protected by a network-abort barrier." if capture_submit_api else ""))
        if api_mode == "write":
            console.print("[bold red]API mutation mode requested.[/bold red] The request is sent only when HIP_ALLOW_API_MUTATION=YES is also present; submit capture itself remains blocked before backend delivery.")
    if getattr(cfg.portal_learning, "enabled", True):
        console.print(
            "Deep portal learning enabled: Python DOM + Playwright MCP accessibility + Chrome DevTools MCP network/console; "
            "API schemas, validation rules, state transitions, coverage and drift are promoted only after judge approval."
        )
    if vision_verify:
        configured_vision = os.getenv("HIP_VISION_MODEL") or os.getenv("AIA_VISION_MODEL") or os.getenv("VISION_MODEL_NAME") or os.getenv("VISION_MODEL") or os.getenv("GEMMA_MODEL_NAME") or os.getenv("GEMMA_MODEL")
        if configured_vision:
            console.print(f"Vision verification enabled with explicit candidate: {str(configured_vision).split(',')[0].strip()}; existing BASE_URL and Dell authentication will be reused unless overridden.")
        else:
            console.print("Vision verification enabled: no explicit model supplied, so bounded Dell AIA Gemma/Pixtral capability discovery will run before the portal opens. Use --vision-model to force a deployment.")
    summary = asyncio.run(flow.run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    console.print(f"Upload summary zip: {summary.get('files', {}).get('upload_zip', '')}")


@app.command("list-sections")
def list_sections() -> None:
    """List user-facing HIP sections that can be executed independently."""
    console.print_json(json.dumps({"sections": section_catalog()}, indent=2, ensure_ascii=False))


@app.command("run-section")
def run_section(
    section: str = typer.Argument(..., help="Section to run: data-map, document-type, source-document-type, target-document-type, rule, transport-profile, source-transport-profile, target-transport-profile, bizflow, or all."),
    input_json: str = typer.Option(..., "--input-json", help="Customer input JSON used to fill only the selected HIP section(s)."),
    config: str = typer.Option("config.yaml", "--config", help="Path to config YAML."),
    customer: Optional[str] = typer.Option(None, "--customer", help="Optional run/customer label. Defaults to SECTION-<selected-section>."),
    runs_dir: Optional[str] = typer.Option(None, "--runs-dir", help="Optional output directory, e.g. C:/hip_runs."),
    full_kb_context: bool = typer.Option(False, "--full-kb-context/--fast-form-only", help="Use full historical KB/context crawl before the selected form(s); fast mode goes directly to + Add."),
    vision_verify: bool = typer.Option(True, "--vision-verify/--no-vision-verify", help="Run the same vision verification used by full execution."),
    require_mcp: bool = typer.Option(False, "--require-mcp/--allow-executor-fallback", help="Strict mode requires Playwright MCP + Chrome DevTools MCP + HIP Intelligence MCP. Default adaptive mode uses any healthy governed executor."),
    runtime_self_heal: bool = typer.Option(True, "--runtime-self-heal/--no-runtime-self-heal", help="Enable safe section-local self-healing."),
    until_complete: bool = typer.Option(False, "--until-complete/--bounded", help="Keep safe exploration/recovery running until the selected section passes its deterministic/text/vision gates."),
    write_heavy_evidence: bool = typer.Option(False, "--write-heavy-evidence/--no-write-heavy-evidence", help="Capture full raw evidence for the selected section(s)."),
) -> None:
    """Run only the requested HIP configuration section(s), without forcing the full flow.

    Examples:
      run-section transport-profile  -> Source + Target Transport Profile only
      run-section source-transport-profile -> Source Transport Profile only
      run-section rule               -> Rule only
      run-section bizflow            -> BizFlow only

    Isolated execution deliberately does not auto-create upstream dependencies.
    Referenced objects required by dropdowns must already exist in HIP.
    """
    try:
        spec = resolve_section(section)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    label = customer or f"SECTION-{str(spec['id']).upper().replace('-', '_')}"
    command = [
        sys.executable, "-u", "-m", "hip_id_agent.cli", "run-full-dummy-fill",
        "--config", config,
        "--customer", label,
        "--input-json", input_json,
        "--phases", str(spec["phase_csv"]),
        "--full-kb-context" if full_kb_context else "--fast-form-only",
        "--vision-verify" if vision_verify else "--no-vision-verify",
        "--require-mcp" if require_mcp else "--allow-executor-fallback",
        "--runtime-self-heal" if runtime_self_heal else "--no-runtime-self-heal",
        "--runtime-self-heal-until-complete" if until_complete else "--bounded-runtime-self-heal",
        "--write-heavy-evidence" if write_heavy_evidence else "--no-write-heavy-evidence",
    ]
    if runs_dir:
        command.extend(["--runs-dir", runs_dir])

    console.print(f"[bold]HIP section execution[/bold]: {spec['label']}")
    console.print(f"Selected internal phases: {spec['phase_csv']}")
    if spec.get("isolated"):
        console.print("Only the selected section phases will be opened; unrelated HIP forms are skipped.")
        console.print(f"Dependency note: {spec['dependency_note']}")
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise typer.Exit(code=int(result.returncode))


@app.command("vision-preflight")
def vision_preflight_command(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    vision_model: Optional[str] = typer.Option(None, "--vision-model", help="Optional Dell AIA multimodal deployment to probe."),
    vision_endpoint: Optional[str] = typer.Option(None, "--vision-endpoint", help="Optional vision base URL or /chat/completions endpoint. Existing BASE_URL is reused when omitted."),
    auto_discovery: bool = typer.Option(True, "--auto-discovery/--no-auto-discovery", help="Probe bounded reviewed Gemma/Pixtral candidates when no explicit model is supplied."),
):
    """Validate Dell AIA image understanding without opening the HIP Portal."""
    load_dotenv()
    if vision_model and str(vision_model).strip():
        os.environ["HIP_VISION_MODEL"] = str(vision_model).strip()
    if vision_endpoint and str(vision_endpoint).strip():
        os.environ["HIP_VISION_ENDPOINT"] = str(vision_endpoint).strip()
    os.environ["HIP_VISION_AUTO_DISCOVERY"] = "true" if auto_discovery else "false"
    cfg = load_config(config)
    from .section_judge import DualModelSectionJudge, SectionJudgePolicy
    judge = DualModelSectionJudge(
        SectionJudgePolicy(enabled=True, require_text_model=False, require_vision_model=True, fail_closed=True),
        aia_config=cfg.aia,
    )
    result = judge.vision_preflight()
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("pass"):
        raise typer.Exit(code=2)


@app.command("kb-repair-status")
def kb_repair_status(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
):
    """Show adaptive KB correction proposals, applied repairs and suspects."""
    from .portal_brain import PortalBrain
    cfg = load_config(config)
    brain = PortalBrain.from_config(cfg)
    result = brain.kb_repair_status()
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))


@app.command("export-self-healed-kb")
def export_self_healed_kb(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="Output folder for the corrected KB and KG. Defaults to the portal brain repair export folder."),
):
    """Export a versioned corrected KB without modifying the original reviewed KB."""
    from .portal_brain import PortalBrain
    cfg = load_config(config)
    brain = PortalBrain.from_config(cfg)
    result = brain.export_corrected_kb(output_dir)
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))


@app.command("portal-brain-status")
def portal_brain_status(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
):
    """Show persistent HIP Portal long-term form-memory status."""
    from .portal_brain import PortalBrain

    cfg = load_config(config)
    brain = PortalBrain.from_config(cfg)
    result = brain.status()
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    console.print(f"Portal brain: {brain.root}")


@app.command("rebuild-portal-brain")
def rebuild_portal_brain_command(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    runs_dir: Optional[str] = typer.Option(None, "--runs-dir", help="Historical runs directory. Defaults to reporting.runs_dir."),
):
    """Rebuild long-term HIP Portal brain from all historical judged runs."""
    from .portal_brain import PortalBrain

    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    brain = PortalBrain.from_config(cfg)
    result = brain.bootstrap(cfg.reporting.runs_dir, rebuild=True)
    unified = brain.import_unified_kb() if brain.policy.auto_import_unified_kb else {"status": "disabled"}
    if brain.policy.unified_kb_required and unified.get("status") in {"missing", "error"}:
        console.print_json(json.dumps({"rebuild": result, "unified_kb": unified}, indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(code=2)
    status = brain.status()
    console.print_json(json.dumps({"rebuild": result, "unified_kb": unified, "status": status}, indent=2, ensure_ascii=False, default=str))
    console.print(f"Portal brain rebuilt at: {brain.root}")


@app.command("import-unified-kb")
def import_unified_kb_command(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    kb_path: Optional[str] = typer.Option(None, "--kb-path", help="Path to HIP_Unified_Deep_KB.json."),
    graph_path: Optional[str] = typer.Option(None, "--graph-path", help="Path to HIP_Unified_Knowledge_Graph.json."),
    force: bool = typer.Option(False, "--force", help="Reimport even when the exact KB digest was already imported."),
):
    """Import reviewed canonical HIP form knowledge into persistent long-term memory."""
    from .portal_brain import PortalBrain

    cfg = load_config(config)
    brain = PortalBrain.from_config(cfg)
    result = brain.import_unified_kb(kb_path=kb_path, graph_path=graph_path, force=force)
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if cfg.brain.unified_kb_required and result.get("status") in {"missing", "error"}:
        raise typer.Exit(code=2)
    console.print(f"Unified KB imported into: {brain.root}")


@app.command("unified-kb-status")
def unified_kb_status_command(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
):
    """Show the reviewed Unified HIP KB import status and digest."""
    from .portal_brain import PortalBrain

    cfg = load_config(config)
    brain = PortalBrain.from_config(cfg)
    console.print_json(json.dumps(brain.unified_kb_status(), indent=2, ensure_ascii=False, default=str))


@app.command("show-memory")
def show_memory(config: str = typer.Option("config.yaml"), customer: Optional[str] = typer.Option(None)):
    cfg = load_config(config)
    memory = HipMemory(cfg.reporting.memory_dir)
    data = memory.objects.read()
    if customer:
        data = data.get(customer.upper(), {})
    console.print_json(json.dumps(data, indent=2))


@app.command("enrich-input")
def enrich_input(
    config: str = typer.Option("config.yaml"),
    customer: str = typer.Option(...),
    partner_query: str = typer.Option(...),
    system_query: str = typer.Option(...),
    input_json: str = typer.Option(...),
    output_json: str = typer.Option("enriched_input.json"),
):
    """Use IDs already stored in memory to enrich an input.json without browser."""
    from .api_client import HipAPIClient

    cfg = load_config(config)
    memory = HipMemory(cfg.reporting.memory_dir)
    partner_id = memory.get_id(customer, "partner", partner_query)
    system_id = memory.get_id(customer, "system", system_query)
    if not partner_id or not system_id:
        raise typer.BadParameter("Missing partner/system ID in memory. Run extract-ids first.")
    client = HipAPIClient(cfg.api)
    result = client.save_enriched_input(input_json, output_json, partner_id=partner_id, system_id=system_id)
    console.print_json(json.dumps(result, indent=2))


@app.command("network-summary")
def network_summary(run_dir: str = typer.Argument(..., help="Run directory containing network/network_tab_events.json"), contains: Optional[str] = typer.Option(None, help="Optional URL/body text filter.")):
    path = Path(run_dir) / "network" / "network_tab_events.json"
    if not path.exists():
        raise typer.BadParameter(f"File not found: {path}")
    events = json.loads(path.read_text(encoding="utf-8"))
    if contains:
        events = [e for e in events if contains.lower() in json.dumps(e, ensure_ascii=False).lower()]
    table = Table(title="Network-tab Events")
    for col in ["status", "method", "resource_type", "stage", "url"]:
        table.add_column(col)
    for e in events[:80]:
        table.add_row(str(e.get("status", "")), str(e.get("method", "")), str(e.get("resource_type", "")), str(e.get("stage", "")), str(e.get("url", ""))[:90])
    console.print(table)


@app.command("graph-summary")
def graph_summary(run_dir: str = typer.Argument(..., help="Run directory containing knowledge_graph/flow_knowledge_graph.json")):
    """Show run Knowledge Graph node/edge counts and the first click/network/ID edges."""
    path = Path(run_dir) / "knowledge_graph" / "flow_knowledge_graph.json"
    if not path.exists():
        raise typer.BadParameter(f"File not found: {path}")
    graph = json.loads(path.read_text(encoding="utf-8"))
    table = Table(title="HIP Flow Knowledge Graph")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Run", str(graph.get("run_id", "")))
    table.add_row("Customer", str(graph.get("customer", "")))
    table.add_row("Nodes", str(len(graph.get("nodes", []))))
    table.add_row("Edges", str(len(graph.get("edges", []))))
    summary = graph.get("summary", {})
    for key in ["stages", "clicks", "network_events"]:
        table.add_row(key, str(summary.get(key, "")))
    console.print(table)

    edge_table = Table(title="Important Graph Edges")
    for col in ["relation", "source", "target"]:
        edge_table.add_column(col)
    for edge in graph.get("edges", []):
        if edge.get("relation") in {"EXTRACTED_ID", "PERFORMED_CLICK", "OBSERVED_NETWORK_EVENT", "CALLS_ENDPOINT"}:
            edge_table.add_row(str(edge.get("relation")), str(edge.get("source"))[:70], str(edge.get("target"))[:70])
    console.print(edge_table)



@app.command("learn-hip")
def learn_hip(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: Optional[str] = typer.Option("./examples/uhaul_poasn_full_dummy_input.json", help="Optional input.json used only for safe search examples and form/input context."),
    customer: str = typer.Option("HIP-PORTAL-DISCOVERY", help="Discovery run key."),
    runs_dir: Optional[str] = typer.Option(None, help="Override reporting runs directory."),
    require_mcp: bool = typer.Option(True, "--require-mcp/--no-require-mcp", help="Require Playwright, Chrome DevTools, and HIP Intelligence MCP."),
):
    """Learn HIP page capabilities, safe listing actions, form entry points, DOM/ARIA state and UI->API contracts.

    Mutation-grade actions such as Deploy/Migrate/Delete are learned from the UI but are never executed by this discovery mission.
    """
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    os.environ["AIA_USE_AUTOGEN"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    if require_mcp:
        enforce_required_mcp_profile(cfg)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    cfg.portal_learning.enabled = True
    cfg.portal_learning.maximum_observability_enabled = True
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    shots = run_dir / cfg.reporting.screenshot_dir_name
    shots.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=shots)
    flow = HIPPortalDiscoveryFlow(cfg)
    console.print(f"[bold]Starting HIP capability discovery[/bold] run_id={rid}")
    console.print("This mission learns Search/Expand/Add/forms/actions/API behavior without executing Deploy/Migrate/Delete/Save/Create mutations.")
    summary = asyncio.run(flow.run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps(summary.get("capability_graph_manifest") or summary, indent=2, ensure_ascii=False, default=str))
    console.print(f"Discovery summary: {run_dir / 'portal_discovery_summary.json'}")
    console.print(f"Capability graph: {summary.get('capability_graph')}")



@app.command("learn-datamaps-deep")
def learn_datamaps_deep(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: str = typer.Option("./examples/uhaul_poasn_full_dummy_input.json", help="Input JSON used to identify the exact Data Map row; values are not persisted to capability memory."),
    customer: str = typer.Option("HIP-DATAMAP-DEEP-DISCOVERY", help="Deep discovery run key."),
    runs_dir: Optional[str] = typer.Option(None, help="Override reporting runs directory."),
    require_mcp: bool = typer.Option(True, "--require-mcp/--no-require-mcp", help="Require Playwright, Chrome DevTools, and HIP Intelligence MCP."),
):
    """Deep-learn Data Maps: listing mechanics, row actions, forms, prerequisites, APIs and deterministic replay.

    Edit/Clone/read surfaces may be opened and closed without save. Migrate/Deploy/Delete
    are probed only behind a Playwright network-abort barrier; their backend mutation
    requests are never delivered during this discovery command.
    """
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    os.environ["AIA_USE_AUTOGEN"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    if require_mcp:
        enforce_required_mcp_profile(cfg)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    cfg.portal_learning.enabled = True
    cfg.portal_learning.maximum_observability_enabled = True
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    shots = run_dir / cfg.reporting.screenshot_dir_name
    shots.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=shots)
    console.print(f"[bold]Starting Data Maps deep capability learning[/bold] run_id={rid}")
    console.print("Search/filter/pagination + row expansion + Edit/Clone/read forms + safe mutation prerequisite/API probes will be learned in one shared SSO session.")
    summary = asyncio.run(DataMapDeepDiscoveryFlow(cfg).run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps({
        "target_row": summary.get("target_row"),
        "row_actions": summary.get("row_action_inventory"),
        "replay": summary.get("deterministic_replay"),
        "capability_graph": summary.get("capability_graph_manifest"),
    }, indent=2, ensure_ascii=False, default=str))
    console.print(f"Deep discovery summary: {run_dir / 'datamap_deep_discovery_summary.json'}")


@app.command("learn-doctypes-deep")
def learn_doctypes_deep(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: str = typer.Option("./examples/uhaul_poasn_full_dummy_input.json", help="Input JSON used to exercise Source and Target Document Type forms; values are not persisted to capability memory."),
    customer: str = typer.Option("HIP-DOCTYPE-DEEP-DISCOVERY", help="Deep discovery run key."),
    runs_dir: Optional[str] = typer.Option(None, help="Override reporting runs directory."),
    require_mcp: bool = typer.Option(True, "--require-mcp/--no-require-mcp", help="Require Playwright, Chrome DevTools, and HIP Intelligence MCP."),
):
    """Deep-learn Document Types listing/actions plus the complete parent-child form hierarchy.

    Both Source and Target input objects are filled into unsaved Create Document Type
    surfaces using the dependency-aware runtime, then closed. Save/Create/Submit are not
    executed. Migrate/Deploy/Delete are probed only behind a network-abort barrier.
    """
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    os.environ["AIA_USE_AUTOGEN"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    if require_mcp:
        enforce_required_mcp_profile(cfg)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    cfg.portal_learning.enabled = True
    cfg.portal_learning.maximum_observability_enabled = True
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    shots = run_dir / cfg.reporting.screenshot_dir_name
    shots.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=shots)
    console.print(f"[bold]Starting Document Types deep capability learning[/bold] run_id={rid}")
    console.print("Listing/search/actions + Source/Target Create-form parent-child execution + API causality + safe mutation prerequisite probes will be learned in one shared SSO session.")
    summary = asyncio.run(DocumentTypeDeepDiscoveryFlow(cfg).run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps({
        "phases": {k: {
            "searched": v.get("searched"),
            "expanded": v.get("expanded"),
            "create_form_pass": (v.get("create_form_parent_child_learning") or {}).get("pass"),
            "replay": ((v.get("create_form_parent_child_learning") or {}).get("promoted") or {}).get("replay_profile"),
        } for k, v in (summary.get("phases") or {}).items()},
        "capability_graph": summary.get("capability_graph_manifest"),
    }, indent=2, ensure_ascii=False, default=str))
    console.print(f"Deep discovery summary: {run_dir / 'doctype_deep_discovery_summary.json'}")


@app.command("learn-rules-deep")
def learn_rules_deep(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: str = typer.Option("./examples/uhaul_poasn_full_dummy_input.json", help="Input JSON used to exercise the unsaved Rule form; values are not persisted to capability memory."),
    customer: str = typer.Option("HIP-RULES-DEEP-DISCOVERY", help="Deep discovery run key."),
    runs_dir: Optional[str] = typer.Option(None, help="Override reporting runs directory."),
    require_mcp: bool = typer.Option(True, "--require-mcp/--no-require-mcp", help="Require Playwright, Chrome DevTools, and HIP Intelligence MCP."),
):
    """Deep-learn Rules listing/actions plus the production-safe Rule form transaction.

    The unsaved Create Rule surface is filled through the same hardened Rules helpers
    used by the configuration mission: Action Type -> async Mapping Identifier, global
    validity before Conditions +, physical row rebinding and exact repeatable rows.
    Save/Create/Submit are never executed. Mutation-grade row actions are probed only
    behind a network-abort barrier.
    """
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    os.environ["AIA_USE_AUTOGEN"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    if require_mcp:
        enforce_required_mcp_profile(cfg)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    cfg.portal_learning.enabled = True
    cfg.portal_learning.maximum_observability_enabled = True
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    shots = run_dir / cfg.reporting.screenshot_dir_name
    shots.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=shots)
    console.print(f"[bold]Starting Rules deep capability learning[/bold] run_id={rid}")
    console.print("Listing/search/actions + production Rule parent-child execution + async Mapping Identifier + exact Conditions rows + API causality + safe mutation prerequisite probes will be learned in one shared SSO session.")
    summary = asyncio.run(RuleDeepDiscoveryFlow(cfg).run(ctx=ctx, input_json=input_json))
    create = summary.get("create_form_parent_child_learning") or {}
    console.print_json(json.dumps({
        "searched": summary.get("searched"),
        "expanded": summary.get("expanded"),
        "create_form_pass": create.get("pass"),
        "conditions_exact": (create.get("condition_exact_proof") or {}).get("pass"),
        "replay": (create.get("promoted") or {}).get("replay_profile"),
        "capability_graph": summary.get("capability_graph_manifest"),
    }, indent=2, ensure_ascii=False, default=str))
    console.print(f"Deep discovery summary: {run_dir / 'rules_deep_discovery_summary.json'}")


@app.command("learn-transport-profiles-deep")
def learn_transport_profiles_deep(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: str = typer.Option("./examples/uhaul_poasn_full_dummy_input.json", help="Input JSON used to exercise Source and Target unsaved Transport Profile forms; values are not persisted to capability memory."),
    customer: str = typer.Option("HIP-TP-DEEP-DISCOVERY", help="Deep discovery run key."),
    runs_dir: Optional[str] = typer.Option(None, help="Override reporting runs directory."),
    require_mcp: bool = typer.Option(True, "--require-mcp/--no-require-mcp", help="Require Playwright, Chrome DevTools, and HIP Intelligence MCP."),
):
    """Deep-learn Transport Profiles listing/actions plus Source/Target create-form dependencies.

    The mission learns System Type -> System/Partner/Application, Interface Type ->
    interface-specific children, Existing Account -> Account, Use Existing Folder ->
    folder branch, API causality, safe mutation prerequisites and value-free replay.
    Save/Create/Submit are never executed.
    """
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    os.environ["AIA_USE_AUTOGEN"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    if require_mcp:
        enforce_required_mcp_profile(cfg)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    cfg.portal_learning.enabled = True
    cfg.portal_learning.maximum_observability_enabled = True
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    shots = run_dir / cfg.reporting.screenshot_dir_name
    shots.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=shots)
    console.print(f"[bold]Starting Transport Profiles deep capability learning[/bold] run_id={rid}")
    console.print("Listing/search/actions + Source/Target dependency-aware create-form execution + interface/account/folder branches + API causality + safe mutation prerequisite probes will be learned in one shared SSO session.")
    summary = asyncio.run(TransportProfileDeepDiscoveryFlow(cfg).run(ctx=ctx, input_json=input_json))
    console.print_json(json.dumps({
        "phases": {k: {
            "searched": v.get("searched"),
            "expanded": v.get("expanded"),
            "create_form_pass": (v.get("create_form_parent_child_learning") or {}).get("pass"),
            "replay": ((v.get("create_form_parent_child_learning") or {}).get("promoted") or {}).get("replay_profile"),
        } for k, v in (summary.get("phases") or {}).items()},
        "capability_graph": summary.get("capability_graph_manifest"),
    }, indent=2, ensure_ascii=False, default=str))
    console.print(f"Deep discovery summary: {run_dir / 'transport_profile_deep_discovery_summary.json'}")


@app.command("learn-bizflows-deep")
def learn_bizflows_deep(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: str = typer.Option("./examples/uhaul_poasn_full_dummy_input.json", help="Input JSON used to exercise the unsaved multi-tab BizFlow form; values are not persisted to capability memory."),
    customer: str = typer.Option("HIP-BIZFLOW-DEEP-DISCOVERY", help="Deep discovery run key."),
    runs_dir: Optional[str] = typer.Option(None, help="Override reporting runs directory."),
    require_mcp: bool = typer.Option(True, "--require-mcp/--no-require-mcp", help="Require Playwright, Chrome DevTools, and HIP Intelligence MCP."),
):
    """Deep-learn BizFlows listing/actions plus the complete unsaved multi-tab form.

    Reuses the production BizFlow transaction runtime for Flow Details, Configure
    Source, Flow Identifiers, Configure Target(s), nested Process Steps, Enricher
    filename-part rows, Configure Routing + Add, Routing Conditions/Actions,
    section judges, UI/API causality and value-free deterministic replay.
    Save/Create/Submit are never executed.
    """
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    os.environ["AIA_USE_AUTOGEN"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    if require_mcp:
        enforce_required_mcp_profile(cfg)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    cfg.portal_learning.enabled = True
    cfg.portal_learning.maximum_observability_enabled = True
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    shots = run_dir / cfg.reporting.screenshot_dir_name
    shots.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=shots)
    console.print(f"[bold]Starting BizFlow deep capability learning[/bold] run_id={rid}")
    console.print("Listing/search/actions + full multi-tab target-first dependency execution + nested Process Steps + Routing rows + UI/API causality + safe mutation prerequisite probes will be learned in one shared SSO session.")
    summary = asyncio.run(BizFlowDeepDiscoveryFlow(cfg).run(ctx=ctx, input_json=input_json))
    form = summary.get("create_form_parent_child_learning") or {}
    console.print_json(json.dumps({
        "searched": summary.get("searched"),
        "expanded": summary.get("expanded"),
        "create_form_pass": form.get("pass"),
        "replay": (form.get("promoted") or {}).get("replay_profile"),
        "capability_graph": summary.get("capability_graph_manifest"),
    }, indent=2, ensure_ascii=False, default=str))
    console.print(f"Deep discovery summary: {run_dir / 'bizflow_deep_discovery_summary.json'}")


@app.command("learn-hip-full-deep")
def learn_hip_full_deep(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: str = typer.Option("./examples/uhaul_poasn_full_dummy_input.json", help="Input JSON used by all five deep learners; customer values remain run-scoped and are not persisted to capability memory."),
    customer: str = typer.Option("HIP-FULL-DEEP-LEARNING", help="Full deep-learning run key."),
    runs_dir: Optional[str] = typer.Option(None, help="Override reporting runs directory."),
    resume_run: Optional[str] = typer.Option(None, help="Optional prior full-deep run directory. Verified family summaries are adopted and incomplete families rerun."),
    require_mcp: bool = typer.Option(True, "--require-mcp/--no-require-mcp", help="Require Playwright, Chrome DevTools, and HIP Intelligence MCP."),
    continue_on_family_failure: bool = typer.Option(True, "--continue-on-family-failure/--fail-fast-family", help="Continue learning remaining families so the final gap queue is complete."),
):
    """Deep-learn and certify all major HIP portal families in one crash-resumable mission.

    Runs Data Maps -> Document Types -> Rules -> Transport Profiles -> BizFlows using
    the same persistent Dell SSO browser profile, merges capability/API/replay memory,
    and emits a single operational-readiness certification plus a visible gap queue.
    """
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    os.environ["AIA_USE_AUTOGEN"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    if require_mcp:
        enforce_required_mcp_profile(cfg)
    cfg.exploration.enabled = True
    cfg.exploration.allow_unsafe_clicks = False
    cfg.portal_learning.enabled = True
    cfg.portal_learning.maximum_observability_enabled = True
    rid = make_run_id(customer)
    run_dir = Path(cfg.reporting.runs_dir) / rid
    shots = run_dir / cfg.reporting.screenshot_dir_name
    shots.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    safe_write_json(run_dir / "full_deep_sso_reuse_contract.json", {
        "schema_version": "hip.full-deep-sso-reuse.v1",
        "browser_user_data_dir": str(cfg.portal.browser_user_data_dir),
        "policy": "reuse the same persistent authenticated Chrome profile sequentially across all five deep learners",
        "expected_user_sso_authentications": 1,
    })
    ctx = RunContext(run_id=rid, customer=customer, partner_query="", system_query="", run_dir=run_dir, screenshots_dir=shots)
    console.print(f"[bold]Starting full HIP deep-learning and certification mission[/bold] run_id={rid}")
    console.print("Data Maps -> Document Types -> Rules -> Transport Profiles -> BizFlows; capability/API/replay memory is merged and certified at the end.")
    summary = asyncio.run(FullHIPDeepLearningMission(cfg).run(
        ctx=ctx, input_json=input_json, resume_run=resume_run,
        continue_on_family_failure=continue_on_family_failure,
    ))
    cert = summary.get("certification") or {}
    console.print_json(json.dumps({
        "status": summary.get("status"),
        "operational_readiness": cert.get("operational_readiness"),
        "full_visible_action_coverage": cert.get("full_visible_action_coverage"),
        "blocker_gap_count": cert.get("blocker_gap_count"),
        "warning_gap_count": cert.get("warning_gap_count"),
        "families": {k: {
            "operational_ready": v.get("operational_ready"),
            "metrics": v.get("metrics"),
        } for k, v in (cert.get("families") or {}).items()},
        "artifacts": summary.get("artifacts"),
    }, indent=2, ensure_ascii=False, default=str))
    console.print(f"Full deep summary: {run_dir / 'full_hip_deep_learning_summary.json'}")
    console.print(f"Capability certification: {run_dir / 'hip_capability_certification.json'}")
    console.print(f"Gap queue: {run_dir / 'hip_capability_gap_queue.json'}")


@app.command("full-deep-readiness")
def full_deep_readiness(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
):
    """Show the latest full-deep certification, or derive graph-only readiness if none exists."""
    cfg = load_config(config)
    latest = latest_certification(cfg.reporting.runs_dir)
    if latest.get("found"):
        console.print_json(json.dumps(latest, indent=2, ensure_ascii=False, default=str))
        return
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    payload = HIPCapabilityCertifier(graph).certify()
    console.print_json(json.dumps({"found": False, "derived_from_persistent_graph": True, "certification": payload}, indent=2, ensure_ascii=False, default=str))


@app.command("capability-status")
def capability_status(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    page_family: str = typer.Option("", help="Optional page family: data_maps, document_types, rules, transport_profiles, bizflows."),
):
    """Show the persistent HIP capability/API graph learned from discovery and configuration runs."""
    cfg = load_config(config)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    payload = graph.page_summary(page_family) if page_family else {"manifest": graph.manifest(), "pages": sorted(graph.data.get("pages", {}))}
    console.print_json(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


@app.command("plan-future-task")
def plan_future_task(
    task: str = typer.Argument(..., help="Natural-language HIP task, e.g. 'Search data map ABC, expand it, then Edit'."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    output: Optional[str] = typer.Option(None, help="Optional output JSON path."),
):
    """Plan a semantic future HIP task using only learned capability IDs."""
    load_dotenv()
    cfg = load_config(config)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    plan = HIPFutureTaskPlanner(cfg, graph).plan(task)
    if output:
        safe_write_json(Path(output), plan)
    console.print_json(json.dumps(plan, indent=2, ensure_ascii=False, default=str))


@app.command("run-future-task")
def run_future_task(
    task: str = typer.Argument(..., help="Natural-language HIP task."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    runs_dir: Optional[str] = typer.Option(None, help="Override run directory."),
    allow_portal_mutation: bool = typer.Option(False, help="Allow only explicitly planned mutation capabilities after all other gates pass."),
    confirmation: str = typer.Option("", help=f"Required for mutation tasks: {MUTATION_CONFIRMATION}"),
):
    """Execute a learned semantic HIP task. Mutation actions require a three-part explicit authorization gate."""
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    enforce_required_mcp_profile(cfg)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    planner = HIPFutureTaskPlanner(cfg, graph)
    plan = planner.plan(task)
    rid = make_run_id("HIP-FUTURE-TASK")
    run_dir = Path(cfg.reporting.runs_dir) / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    safe_write_json(run_dir / "future_task_plan.json", plan)
    if not plan.get("pass"):
        console.print_json(json.dumps(plan, indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(code=2)
    result = asyncio.run(HIPFutureTaskExecutor(cfg, graph).execute(task=task, plan=plan, run_dir=run_dir, allow_portal_mutation=allow_portal_mutation, confirmation=confirmation))
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("pass"):
        raise typer.Exit(code=3)

@app.command("plan-portal-task")
def plan_portal_task(
    task: str = typer.Argument(..., help="Natural-language task for any currently accessible portal capability."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: str = typer.Option("./input.json", help="Runtime JSON whose nonblank values are the form value authority."),
    input_root: str = typer.Option("", help="Optional JSON path selecting the object/subtree to fill."),
    start_url: str = typer.Option("", help="Optional starting portal URL. Empty uses portal.base_url."),
    deep_learn: bool = typer.Option(True, "--deep-learn/--no-deep-learn", help="Inventory unfamiliar pages/actions/form controls into semantic learning memory."),
    output: Optional[str] = typer.Option(None, help="Optional plan JSON output path."),
):
    """Plan a general governed portal task without restricting execution to known HIP page families."""
    load_dotenv()
    cfg = load_config(config)
    if not bool(getattr(cfg.universal_operator, "enabled", True)):
        raise typer.BadParameter("universal_operator.enabled is false")
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    plan = UniversalPortalTaskPlanner(cfg, graph).plan(
        task, input_json=input_json, input_root=input_root, start_url=start_url,
        deep_learn=bool(deep_learn and getattr(cfg.universal_operator, "deep_learning_enabled", True)),
    )
    if output:
        safe_write_json(Path(output), plan)
    console.print_json(json.dumps(plan, indent=2, ensure_ascii=False, default=str))


@app.command("run-portal-task")
def run_portal_task(
    task: str = typer.Argument(..., help="Natural-language task for any currently accessible portal capability."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    runs_dir: Optional[str] = typer.Option(None, help="Override run directory."),
    input_json: str = typer.Option("./input.json", help="Runtime JSON whose nonblank values are the form value authority."),
    input_root: str = typer.Option("", help="Optional JSON path selecting the object/subtree to fill."),
    start_url: str = typer.Option("", help="Optional starting portal URL. Empty uses portal.base_url."),
    deep_learn: bool = typer.Option(True, "--deep-learn/--no-deep-learn", help="Learn unfamiliar live page/action/form semantics while executing."),
    allow_portal_mutation: bool = typer.Option(False, help="Allow only explicitly planned mutation actions after every mutation gate passes."),
    confirmation: str = typer.Option("", help=f"Required for mutation tasks: {MUTATION_CONFIRMATION}"),
):
    """Learn and execute a user-requested portal workflow through live semantic evidence and exact input-state verification."""
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if not bool(getattr(cfg.universal_operator, "enabled", True)):
        raise typer.BadParameter("universal_operator.enabled is false")
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    enforce_required_mcp_profile(cfg)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    planner = UniversalPortalTaskPlanner(cfg, graph)
    plan = planner.plan(
        task, input_json=input_json, input_root=input_root, start_url=start_url,
        deep_learn=bool(deep_learn and getattr(cfg.universal_operator, "deep_learning_enabled", True)),
    )
    max_steps = int(getattr(cfg.universal_operator, "max_execution_steps", 40) or 40)
    if len(plan.get("steps") or []) > max_steps:
        plan = {**plan, "pass": False, "reason": f"Universal task plan exceeds configured max_execution_steps={max_steps}"}
    rid = make_run_id("HIP-PORTAL-TASK")
    run_dir = Path(cfg.reporting.runs_dir) / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    safe_write_json(run_dir / "universal_portal_task_plan.json", plan)
    if not plan.get("pass"):
        console.print_json(json.dumps(plan, indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(code=2)
    result = asyncio.run(UniversalPortalTaskExecutor(cfg, graph).execute(
        task=task, plan=plan, run_dir=run_dir, allow_portal_mutation=allow_portal_mutation,
        confirmation=confirmation,
    ))
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("pass"):
        raise typer.Exit(code=3)


@app.command("operate-hip")
def operate_hip(
    task: str = typer.Argument(..., help="Arbitrary HIP task: search, fill, edit, validate, deploy, migrate, or change a specific field."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    runs_dir: Optional[str] = typer.Option(None, help="Override run directory."),
    input_json: str = typer.Option("", help="Optional runtime input JSON. Inline 'change FIELD to VALUE' requests can synthesize a run-local patch."),
    input_root: str = typer.Option("", help="Optional JSON path selecting the object/subtree to fill."),
    start_url: str = typer.Option("", help="Optional starting portal URL."),
    deep_learn: bool = typer.Option(True, "--deep-learn/--no-deep-learn", help="Learn unfamiliar live controls/actions while operating."),
    allow_portal_mutation: bool = typer.Option(False, help="Required for Save/Create/Update/Delete/Deploy/Migrate actions."),
    confirmation: str = typer.Option("", help=f"Required for mutation tasks: {MUTATION_CONFIRMATION}"),
):
    """Persistent HIP operator: keep learning/self-healing until live proof + final human acceptance."""
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    enforce_required_mcp_profile(cfg)
    rid = make_run_id("HIP-OPERATOR")
    run_dir = Path(cfg.reporting.runs_dir) / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    result = asyncio.run(PersistentHIPOperator(cfg, graph).execute(
        task=task, run_dir=run_dir, input_json=input_json, input_root=input_root,
        start_url=start_url, deep_learn=deep_learn,
        allow_portal_mutation=allow_portal_mutation, confirmation=confirmation,
    ))
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("pass"):
        raise typer.Exit(code=3)


@app.command("plan-certified-task")
def plan_certified_task(
    task: str = typer.Argument(..., help="Natural-language HIP task; may span multiple learned HIP families."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    output: Optional[str] = typer.Option(None, help="Optional output JSON path."),
    allow_adaptive_exploration: bool = typer.Option(True, "--adaptive/--no-adaptive", help="Allow guarded live DOM/MCP exploration when certified replay drifts or a capability is unresolved."),
):
    """Plan a certification-gated future HIP task across the full learned platform."""
    load_dotenv()
    cfg = load_config(config)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    plan = CertifiedHIPFutureTaskPlanner(cfg, graph).plan(task, allow_adaptive_exploration=allow_adaptive_exploration)
    if output:
        safe_write_json(Path(output), plan)
    console.print_json(json.dumps(plan, indent=2, ensure_ascii=False, default=str))


@app.command("run-certified-task")
def run_certified_task(
    task: str = typer.Argument(..., help="Natural-language HIP task; may span multiple learned HIP families."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    runs_dir: Optional[str] = typer.Option(None, help="Override run directory."),
    allow_portal_mutation: bool = typer.Option(False, help="Allow only explicitly planned mutation capabilities after all three mutation gates pass."),
    confirmation: str = typer.Option("", help=f"Required for mutation tasks: {MUTATION_CONFIRMATION}"),
    allow_adaptive_exploration: bool = typer.Option(True, "--adaptive/--no-adaptive", help="Use semantic/AutoGen/MCP recovery when learned selectors drift."),
):
    """Execute a certification-gated, drift-aware HIP task with guarded self-healing."""
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    enforce_required_mcp_profile(cfg)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    planner = CertifiedHIPFutureTaskPlanner(cfg, graph)
    plan = planner.plan(task, allow_adaptive_exploration=allow_adaptive_exploration)
    rid = make_run_id("HIP-CERTIFIED-TASK")
    run_dir = Path(cfg.reporting.runs_dir) / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    safe_write_json(run_dir / "certified_future_task_plan.json", plan)
    if not plan.get("pass"):
        console.print_json(json.dumps(plan, indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(code=2)
    result = asyncio.run(CertifiedHIPFutureTaskExecutor(cfg, graph).execute(
        task=task, plan=plan, run_dir=run_dir, allow_portal_mutation=allow_portal_mutation,
        confirmation=confirmation, allow_adaptive_exploration=allow_adaptive_exploration,
    ))
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("pass"):
        raise typer.Exit(code=3)


@app.command("preview-governed-change")
def preview_governed_change(
    task: str = typer.Argument(..., help="Natural-language HIP task to preview under production change governance."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    operator_role: str = typer.Option("", help="Operator role. If omitted, HIP_OPERATOR_ROLE/config default is used."),
    approval_id: str = typer.Option("", help="Optional change approval/ticket ID. Can also come from HIP_CHANGE_APPROVAL_ID."),
    force_repeat_mutation: bool = typer.Option(False, help="Preview an otherwise duplicate mutation as explicitly repeatable."),
    allow_adaptive_exploration: bool = typer.Option(True, "--adaptive/--no-adaptive", help="Allow guarded exploration when certified replay/capabilities drift."),
    output: Optional[str] = typer.Option(None, help="Optional preview JSON output path."),
):
    """Create the final change preview, policy, role, certification and duplicate-protection verdict without opening the browser."""
    load_dotenv()
    cfg = load_config(config)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    plan = CertifiedHIPFutureTaskPlanner(cfg, graph).plan(task, allow_adaptive_exploration=allow_adaptive_exploration)
    preview = HIPChangeGovernance(cfg, graph).preview(
        task=task, plan=plan, operator_role=operator_role, approval_id=approval_id,
        force_repeat_mutation=force_repeat_mutation,
    )
    payload = {"plan": plan, "change_preview": preview}
    if output:
        safe_write_json(Path(output), payload)
    console.print_json(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    if not preview.get("pass"):
        raise typer.Exit(code=2)


@app.command("run-governed-change")
def run_governed_change(
    task: str = typer.Argument(..., help="Natural-language HIP task to execute through certified production change governance."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    runs_dir: Optional[str] = typer.Option(None, help="Override run directory."),
    operator_role: str = typer.Option("", help="Operator role. Mutation defaults are fail-closed unless an allowed role is supplied by option/env."),
    approval_id: str = typer.Option("", help="Optional change approval/ticket ID. Can also come from HIP_CHANGE_APPROVAL_ID."),
    allow_portal_mutation: bool = typer.Option(False, help="First explicit mutation authorization key."),
    confirmation: str = typer.Option("", help=f"Third mutation authorization key; exact phrase: {MUTATION_CONFIRMATION}"),
    force_repeat_mutation: bool = typer.Option(False, help="Explicitly bypass duplicate protection for an intentional repeated mutation."),
    allow_adaptive_exploration: bool = typer.Option(True, "--adaptive/--no-adaptive", help="Allow semantic/AutoGen/MCP recovery for safe read/draft drift."),
):
    """Execute a certified HIP task with preview, role/policy, idempotency, post-verification, audit receipt and rollback guidance."""
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    autogen = assert_autogen_075(verify_imports=True)
    cfg = load_config(config)
    if runs_dir:
        cfg.reporting.runs_dir = runs_dir
    enforce_required_mcp_profile(cfg)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    plan = CertifiedHIPFutureTaskPlanner(cfg, graph).plan(task, allow_adaptive_exploration=allow_adaptive_exploration)
    rid = make_run_id("HIP-GOVERNED-CHANGE")
    run_dir = Path(cfg.reporting.runs_dir) / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    safe_write_json(run_dir / "autogen_preflight.json", autogen)
    safe_write_json(run_dir / "certified_future_task_plan.json", plan)
    if not plan.get("pass"):
        console.print_json(json.dumps(plan, indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(code=2)
    result = asyncio.run(GovernedCertifiedTaskExecutor(cfg, graph).execute(
        task=task, plan=plan, run_dir=run_dir,
        allow_portal_mutation=allow_portal_mutation, confirmation=confirmation,
        allow_adaptive_exploration=allow_adaptive_exploration,
        operator_role=operator_role, approval_id=approval_id,
        force_repeat_mutation=force_repeat_mutation,
    ))
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("pass"):
        raise typer.Exit(code=3)


@app.command("change-audit-status")
def change_audit_status(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    limit: int = typer.Option(50, min=1, max=500, help="Recent structural audit events to show."),
):
    """Show the hash-chained, value-free change audit ledger."""
    load_dotenv()
    cfg = load_config(config)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    payload = HIPChangeGovernance(cfg, graph).ledger.status(limit=limit)
    console.print_json(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


@app.command("dual-mcp-check")
def dual_mcp_check(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    run_dir: str = typer.Option("./runs/dual-mcp-check", help="Directory for Playwright MCP and Chrome DevTools MCP capability evidence."),
):
    """Validate Microsoft's official Playwright MCP and Chrome DevTools MCP."""
    from .browser_backend import validate_dual_browser_mcps

    cfg = load_config(config)
    result = asyncio.run(validate_dual_browser_mcps(cfg, run_dir))
    path = Path(run_dir) / "dual_mcp_capabilities.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    console.print(f"Saved: {path}")


@app.command("playwright-mcp-check")
def playwright_mcp_check(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    run_dir: str = typer.Option("./runs/playwright-mcp-check", help="Directory for official Playwright MCP capability evidence."),
):
    """Start @playwright/mcp and list the available browser tools."""
    from .browser_backend import validate_playwright_mcp

    cfg = load_config(config)
    result = asyncio.run(validate_playwright_mcp(cfg, run_dir))
    path = Path(run_dir) / "playwright_mcp_capabilities.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    console.print(f"Saved: {path}")


@app.command("chrome-devtools-check")
def chrome_devtools_check(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    run_dir: str = typer.Option("./runs/chrome-devtools-mcp-check", help="Directory for MCP capability evidence."),
):
    """Start the pre-existing Chrome DevTools MCP server and list available tools.

This validates that the local environment can use Chrome DevTools MCP before running HIP extraction
with mcp.browser_backend=mcp. It does not create a custom MCP server.
    """
    from .browser_backend import validate_chrome_devtools_mcp

    cfg = load_config(config)
    result = asyncio.run(validate_chrome_devtools_mcp(cfg, run_dir))
    path = Path(run_dir) / "chrome_devtools_mcp_capabilities.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    console.print(f"Saved: {path}")


def _print_summary(summary: dict) -> None:
    table = Table(title="HIP ID Extraction Summary")
    table.add_column("Field")
    table.add_column("Value")
    registry = summary.get("registry", {})
    for key in ["partner_id", "partner_name", "partner_confidence", "partner_source", "system_id", "system_name", "system_confidence", "system_source", "partner_bulk_count", "account_bulk_count", "system_bulk_count", "network_tab_event_count", "click_event_count", "enriched_input_json"]:
        table.add_row(key, str(registry.get(key, "")))
    console.print(table)
    console.print("\nReports:")
    for k, v in summary.get("report_paths", {}).items():
        console.print(f"- {k}: {v}")
    console.print("\nEvidence:")
    for k, v in summary.get("evidence_files", {}).items():
        console.print(f"- {k}: {v}")
    kg_files = summary.get("knowledge_graph_files", {}) or {}
    if any(kg_files.values()):
        console.print("\nKnowledge Graph:")
        for k, v in kg_files.items():
            if v:
                console.print(f"- {k}: {v}")


if __name__ == "__main__":
    app()


@app.command("production-doctor")
def production_doctor_cmd(
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: str = typer.Option("./examples/uhaul_poasn_full_dummy_input.json", help="Runtime input JSON to validate."),
    golden_dir: str = typer.Option("./golden_screenshots/UHAUL-POASN", help="Golden reference directory."),
    uploads_dir: str = typer.Option("./uploads", help="Upload asset directory."),
    runs_dir: str = typer.Option("", help="Optional runs root override."),
    mutation_expected: bool = typer.Option(False, "--mutation-expected/--read-only", help="Also require a valid live runtime certificate for a mutation run."),
):
    """Run the non-mutating production readiness gate."""
    load_dotenv()
    root = Path(config).resolve().parent
    orchestrator = ProductionE2EOrchestrator.from_path(config, root=root)
    req = build_request(
        root=root, task="production doctor", config_path=config, input_json=input_json,
        runs_dir=runs_dir or None, golden_dir=golden_dir, uploads_dir=uploads_dir,
    )
    result = orchestrator.doctor(req, mutation_expected=mutation_expected)
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("pass"):
        raise typer.Exit(code=2)


@app.command("run-production-e2e")
def run_production_e2e_cmd(
    task: str = typer.Argument(..., help="Natural-language end-to-end portal task."),
    config: str = typer.Option("config.yaml", help="Path to config YAML."),
    input_json: str = typer.Option("./input.json", help="Current runtime input.json; the only business-value authority."),
    input_root: str = typer.Option("", help="Optional JSON subtree path."),
    start_url: str = typer.Option("", help="Optional starting portal URL."),
    runs_dir: str = typer.Option("", help="Optional runs root override."),
    golden_dir: str = typer.Option("./golden_screenshots/UHAUL-POASN", help="Golden screenshot directory."),
    uploads_dir: str = typer.Option("./uploads", help="Upload asset directory."),
    deep_learn: bool = typer.Option(True, "--deep-learn/--no-deep-learn", help="Learn unfamiliar portal capabilities during execution."),
    allow_portal_mutation: bool = typer.Option(False, help="Enable the explicit mutation execution gate."),
    confirmation: str = typer.Option("", help=f"Required for mutation: {MUTATION_CONFIRMATION}"),
    operator_role: str = typer.Option("", help="Operator role; otherwise HIP_OPERATOR_ROLE/config default is used."),
    approval_id: str = typer.Option("", help="Optional change approval ID when policy requires it."),
    force_repeat_mutation: bool = typer.Option(False, help="Allow an identical previously successful mutation after explicit operator intent; unresolved mutations remain quarantined."),
):
    """Run the one-command production lifecycle: doctor -> governance -> lock -> plan -> execute -> verify -> journal -> safe bundle."""
    load_dotenv()
    os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"
    config_path = Path(config).resolve()
    root = config_path.parent
    orchestrator = ProductionE2EOrchestrator.from_path(config_path, root=root)
    req = build_request(
        root=root, task=task, config_path=config_path, input_json=input_json, input_root=input_root,
        start_url=start_url, runs_dir=runs_dir or None, golden_dir=golden_dir, uploads_dir=uploads_dir,
        deep_learn=deep_learn, allow_portal_mutation=allow_portal_mutation, confirmation=confirmation,
        operator_role=operator_role, approval_id=approval_id, force_repeat_mutation=force_repeat_mutation,
    )
    result = asyncio.run(orchestrator.execute(req))
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("pass"):
        raise typer.Exit(code=3)


@app.command("verify-production-journal")
def verify_production_journal_cmd(
    journal: str = typer.Argument(..., help="Path to production_execution_journal.jsonl"),
):
    """Verify the append-only production execution journal hash chain."""
    result = HashChainedJournal(Path(journal)).verify()
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("pass"):
        raise typer.Exit(code=2)
