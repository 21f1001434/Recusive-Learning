from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, List

from .aia_client import AIAClient
from .api_client import HipAPIClient
from .browser_session import BrowserSession
from .browser_backend import MCPBackend
from .config import AppConfig
from .id_extractor import IDExtractor
from .input_entities import extract_input_entities
from .knowledge_graph import KnowledgeGraphWriter
from .memory import HipMemory
from .models import ExtractedID, RunContext, StageResult, utc_now
from .page_explorer import PageExplorer
from .report import ReportWriter
from .security import mask_sensitive_data, mask_sensitive_string


class PartnerSystemIDFlow:
    """End-to-end Partner/System ID discovery flow with strict evidence validation.

    No custom MCP server is built. The browser backend is logged as Playwright/MCP/auto fallback;
    direct Playwright is the production-testable fallback in this package.
    """

    def __init__(self, config: AppConfig, memory: HipMemory):
        self.config = config
        self.memory = memory
        self.aia = AIAClient(config.aia)
        self.extractor = IDExtractor(self.aia, minimum_confidence=config.extraction.minimum_confidence, aliases=config.extraction.query_aliases)
        self.explorer = PageExplorer(config)
        self.api_client = HipAPIClient(config.api)
        # Give PageExplorer access to strict extractor/memory for controlled bulk discovery.
        self.explorer.configured_extractor = self.extractor
        self.explorer.configured_memory = self.memory

    def _safe_memory_call(self, ctx: RunContext, operation: str, func, *args, **kwargs) -> Any:
        """Run a memory write without failing the live portal discovery.

        OneDrive/AV can temporarily block JSON registry renames on Windows. ID
        discovery should still finish, report the extracted IDs, and generate
        evidence even if local cache persistence has a transient failure.
        """
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            ctx.registry.setdefault("memory_write_warnings", []).append({
                "operation": operation,
                "error": mask_sensitive_string(str(exc)),
                "timestamp": utc_now(),
            })
            return None

    async def run(self, *, ctx: RunContext, input_json: Optional[str] = None, run_api: bool = False) -> Dict[str, Any]:
        session: Optional[BrowserSession] = None
        failure: Optional[BaseException] = None
        report_paths: Dict[str, str] = {}
        graph_paths: Dict[str, str] = {}
        mcp_action_events = []
        mcp_network_events = []
        requested_backend = self.config.mcp.browser_backend
        ctx.registry["requested_browser_backend"] = requested_backend
        ctx.registry["attempted_browser_backend"] = requested_backend
        if requested_backend == "playwright":
            ctx.registry["browser_backend_used"] = "playwright"
            ctx.registry["browser_backend_note"] = "playwright requested; using local Playwright backend"
        elif requested_backend == "auto":
            ctx.registry["attempted_browser_backend"] = "mcp"
            ctx.registry["browser_backend_used"] = "playwright"
            ctx.registry["fallback_backend"] = "playwright"
            ctx.registry["fallback_reason"] = "MCP runtime not available in this local project; using Playwright fallback"
            ctx.registry["browser_backend_note"] = "Requested backend: auto; attempted backend: mcp; fallback backend: playwright; reason: MCP runtime unavailable"
        else:
            ctx.registry["browser_backend_used"] = "mcp"
            ctx.registry["browser_backend_note"] = "mcp requested; will use pre-existing Chrome DevTools MCP adapter only if direct backend is enabled"

        if input_json:
            try:
                input_original = ctx.run_dir / "input.original.json"
                input_original.parent.mkdir(parents=True, exist_ok=True)
                raw_text = Path(input_json).read_text(encoding="utf-8")
                input_original.write_text(raw_text, encoding="utf-8")
                ctx.registry["input_original_json"] = str(input_original)
                ctx.registry["input_entities_detected"] = extract_input_entities(input_json)
            except Exception as exc:
                ctx.registry["input_detection_warning"] = str(exc)

        try:
            if requested_backend == "mcp":
                if not self.config.mcp.use_chrome_devtools_mcp or not self.config.mcp.chrome_devtools_mcp_direct_backend_enabled:
                    raise RuntimeError(
                        'MCP backend was requested, but Chrome DevTools MCP direct backend is disabled. '
                        'Set mcp.use_chrome_devtools_mcp=true and mcp.chrome_devtools_mcp_direct_backend_enabled=true, '
                        'or set mcp.browser_backend to "playwright"/"auto".'
                    )
                mcp_backend = MCPBackend(config=self.config, run_dir=ctx.run_dir)
                await self._run_with_mcp_backend(mcp_backend, ctx, input_json=input_json, run_api=run_api)
                client = getattr(mcp_backend, "tool_client", None)
                mcp_action_events = getattr(client, "action_events", []) if client else []
                mcp_network_events = getattr(client, "network_events", []) if client else []
                try:
                    if client and hasattr(client, "close"):
                        await client.close()
                except Exception:
                    pass
            if requested_backend != "mcp":
                async with BrowserSession(self.config, ctx.run_dir) as session_obj:
                    session = session_obj
                    await session.goto_base_and_complete_sso()
                    ctx.registry["after_sso_url"] = session.page.url if session.page else ""
                    await session.screenshot(ctx.screenshots_dir / "00_after_sso.png")
    
                    partner = None
                    if ctx.partner_query:
                        partner = await self._extract_one(session, ctx, object_type="partner", query=ctx.partner_query)
                    else:
                        await self.explorer.navigate_to_area(session, "partner")
                    partner_bulk = self.extractor.extract_all_from_network("partner", session.network_tab_events)
                    account_bulk = self.extractor.extract_all_from_network("account", session.network_tab_events)
                    ctx.registry["partner_bulk_count"] = len(partner_bulk)
                    ctx.registry["account_bulk_count"] = len(account_bulk)
                    self._safe_memory_call(ctx, "save_many_partner_entities", self.memory.save_many_entities, "partner", partner_bulk)
                    self._safe_memory_call(ctx, "save_many_account_entities", self.memory.save_many_entities, "account", account_bulk)

                    if self.config.exploration.enabled and self.config.exploration.explore_partner:
                        partner_exploration = await self.explorer.explore_area(session, "partner", initial_query=None if self.config.exploration.prefer_nested_discovery else (ctx.partner_query or None))
                        ctx.registry["partner_exploration"] = partner_exploration
                        refreshed_partner_bulk = self.extractor.extract_all_from_network("partner", session.network_tab_events)
                        refreshed_account_bulk = self.extractor.extract_all_from_network("account", session.network_tab_events)
                        ctx.registry["partner_bulk_count_after_exploration"] = len(refreshed_partner_bulk)
                        ctx.registry["account_bulk_count_after_exploration"] = len(refreshed_account_bulk)
                        self._safe_memory_call(ctx, "save_many_partner_entities_after_exploration", self.memory.save_many_entities, "partner", refreshed_partner_bulk)
                        self._safe_memory_call(ctx, "save_many_account_entities_after_exploration", self.memory.save_many_entities, "account", refreshed_account_bulk)
    
                    system = None
                    if ctx.system_query:
                        system = await self._extract_one(session, ctx, object_type="system", query=ctx.system_query)
                    else:
                        await self.explorer.navigate_to_area(session, "system")
                    system_bulk = self.extractor.extract_all_from_network("system", session.network_tab_events)
                    ctx.registry["system_bulk_count"] = len(system_bulk)
                    self._safe_memory_call(ctx, "save_many_system_entities", self.memory.save_many_entities, "system", system_bulk)

                    if self.config.exploration.enabled and self.config.exploration.explore_system:
                        system_exploration = await self.explorer.explore_area(session, "system", initial_query=None if self.config.exploration.prefer_nested_discovery else (ctx.system_query or None))
                        ctx.registry["system_exploration"] = system_exploration
                        refreshed_system_bulk = self.extractor.extract_all_from_network("system", session.network_tab_events)
                        ctx.registry["system_bulk_count_after_exploration"] = len(refreshed_system_bulk)
                        self._safe_memory_call(ctx, "save_many_system_entities_after_exploration", self.memory.save_many_entities, "system", refreshed_system_bulk)
    
                    if partner:
                        self._safe_memory_call(ctx, "save_verified_partner_id", self.memory.save_extracted_id, ctx.customer, partner)
                        ctx.registry.update({
                            "partner_id": partner.object_id,
                            "partner_name": partner.name,
                            "partner_confidence": partner.confidence,
                            "partner_source": partner.source,
                        })
                    if system:
                        self._safe_memory_call(ctx, "save_verified_system_id", self.memory.save_extracted_id, ctx.customer, system)
                        ctx.registry.update({
                            "system_id": system.object_id,
                            "system_name": system.name,
                            "system_confidence": system.confidence,
                            "system_source": system.source,
                        })
    
                    ctx.registry["network_tab_event_count"] = len(session.network_tab_events)
                    ctx.registry["legacy_network_record_count"] = len(session.network_records)
                    ctx.registry["click_event_count"] = len(session.click_events)
                    ctx.registry["action_event_count"] = len(session.action_events)
                    ctx.registry["network_events_jsonl"] = str(ctx.run_dir / "network_events.jsonl")
                    ctx.registry["network_summary_json"] = str(ctx.run_dir / "network_summary.json")
                    ctx.registry["network_tab_events_path"] = str(ctx.run_dir / "network" / "network_tab_events.json")
                    ctx.registry["click_events_path"] = str(ctx.run_dir / "clicks" / "click_events.json")
                    ctx.registry["action_events_path"] = str(ctx.run_dir / "actions" / "action_events.json")
                    if self.config.exploration.enabled:
                        self._write_exploration_outputs(ctx)
    
                    if input_json:
                        await self._enrich_and_maybe_run_api(ctx, input_json, run_api)
    
                    logical_failures = self._logical_failures(ctx, require_partner=bool(ctx.partner_query), require_system=bool(ctx.system_query))
                    if logical_failures:
                        ctx.registry["logical_failures"] = logical_failures
                        ctx.registry["run_status"] = "partial_success" if (ctx.registry.get("partner_id") or ctx.registry.get("system_id")) else "failed"
                        ctx.registry.setdefault("recovery_suggestions", []).extend(self._default_logical_recoveries(logical_failures))
                        bundle_paths = await session.write_failure_bundle(None, extra={"failure_reason": "; ".join(logical_failures), "recovery_suggestions": ctx.registry.get("recovery_suggestions", [])})
                        ctx.registry["failure_bundle"] = bundle_paths
    
                    await session.flush_logs()
        except BaseException as exc:
            failure = exc
            if requested_backend == "mcp":
                ctx.registry["run_exception"] = f"MCP backend was requested and failed: {exc}"
            else:
                ctx.registry["run_exception"] = str(exc)
            ctx.registry["run_status"] = "failed"
            started = utc_now()
            ctx.stage_results.append(StageResult(
                stage="failure",
                status="failed",
                message=f"Run failed: {exc}",
                started_at=started,
                finished_at=utc_now(),
                errors=[str(exc)],
            ))
            if session:
                try:
                    bundle_paths = await session.write_failure_bundle(exc, extra={"recovery_suggestions": ctx.registry.get("recovery_suggestions", [])})
                    ctx.registry["failure_bundle"] = bundle_paths
                    await session.flush_logs()
                except Exception as bundle_exc:
                    ctx.registry["failure_bundle_error"] = str(bundle_exc)
            else:
                try:
                    ctx.registry["failure_bundle"] = self._write_static_failure_bundle(ctx, exc)
                except Exception as bundle_exc:
                    ctx.registry["failure_bundle_error"] = str(bundle_exc)
        finally:
            # Ensure first-class candidate files exist even in partial/failure cases.
            self._write_run_level_candidate_files(ctx)
            self._ensure_status_before_reporting(ctx, failure)
            click_events = session.click_events if session else []
            action_events = session.action_events if session else mcp_action_events
            network_events = session.network_tab_events if session else mcp_network_events
            try:
                graph_paths = KnowledgeGraphWriter(ctx).write(
                    click_events=click_events,
                    action_events=action_events,
                    network_events=network_events,
                    report_paths=None,
                )
                ctx.registry.update(graph_paths)
                self._safe_memory_call(ctx, "record_knowledge_graph_initial", self.memory.record_knowledge_graph, ctx.run_id, graph_paths, {
                    "click_events": len(click_events),
                    "action_events": len(action_events),
                    "network_tab_events": len(network_events),
                    "stage_results": len(ctx.stage_results),
                })
            except Exception as kg_exc:
                ctx.registry["knowledge_graph_error"] = str(kg_exc)
            report_paths = ReportWriter(ctx).write_all()
            ctx.registry["report_paths"] = report_paths
            # Re-write KG with report edges.
            try:
                graph_paths = KnowledgeGraphWriter(ctx).write(
                    click_events=click_events,
                    action_events=action_events,
                    network_events=network_events,
                    report_paths=report_paths,
                )
                ctx.registry.update(graph_paths)
            except Exception as kg_exc:
                ctx.registry["knowledge_graph_report_edge_error"] = str(kg_exc)

        summary = ReportWriter(ctx).build_summary()
        summary["report_paths"] = report_paths
        self._safe_memory_call(ctx, "record_run", self.memory.record_run, ctx.run_id, summary)
        if ctx.registry.get("partner_id") and ctx.registry.get("system_id") and not failure:
            ctx.registry["run_status"] = "success"
            self._safe_memory_call(ctx, "record_success", self.memory.record_success, ctx.run_id, summary)
        elif ctx.registry.get("partner_id") or ctx.registry.get("system_id"):
            ctx.registry["run_status"] = "partial_success"
        else:
            ctx.registry.setdefault("run_status", "failed" if failure else "partial_success")
        return summary


    def _ensure_status_before_reporting(self, ctx: RunContext, failure: Optional[BaseException]) -> None:
        if ctx.registry.get("run_status"):
            return
        if ctx.registry.get("partner_id") and ctx.registry.get("system_id") and not failure:
            ctx.registry["run_status"] = "success"
        elif ctx.registry.get("partner_id") or ctx.registry.get("system_id"):
            ctx.registry["run_status"] = "partial_success"
        else:
            ctx.registry["run_status"] = "failed" if failure else "partial_success"

    def _logical_failures(self, ctx: RunContext, *, require_partner: bool, require_system: bool) -> List[str]:
        failures: List[str] = []
        if require_partner and not ctx.registry.get("partner_id"):
            failures.append("Partner ID is required but was not verified/found")
        if require_system and not ctx.registry.get("system_id"):
            failures.append("System ID is required but was not verified/found")
        for stage in ctx.stage_results:
            debug = ((stage.evidence or {}).get("debug") or {}) if hasattr(stage, "evidence") else {}
            vr = debug.get("verified_result") if isinstance(debug, dict) else {}
            if isinstance(vr, dict) and vr.get("ambiguous"):
                failures.append(f"{stage.stage}: multiple conflicting IDs found; ambiguity prevents automatic save")
            if stage.status == "failed" and stage.stage.startswith("extract_"):
                failures.append(stage.message)
            if any("SSO" in str(e) or "login" in str(e).lower() for e in stage.errors):
                failures.append("SSO/login page remained active or blocked extraction")
        return sorted(set(failures))

    def _default_logical_recoveries(self, failures: List[str]) -> List[str]:
        suggestions = []
        text = " ".join(failures).lower()
        if "partner" in text or "system" in text:
            suggestions.append("Open the exact matching row/details page and verify a typed partnerId/systemId/domainId field before saving.")
        if "ambiguous" in text or "conflicting" in text:
            suggestions.append("Ambiguous IDs found. Narrow the search query or open the detail page for the exact row.")
        if "sso" in text or "login" in text:
            suggestions.append("Complete Dell SSO in the persistent browser profile, then rerun in Playwright mode.")
        suggestions.append("Check network_events.jsonl for partner/system/domain endpoints and verify response body capture is enabled.")
        return suggestions

    def _write_static_failure_bundle(self, ctx: RunContext, exc: BaseException | None) -> Dict[str, str]:
        bundle = ctx.run_dir / "failure_bundle"
        bundle.mkdir(parents=True, exist_ok=True)
        unavailable = {"available": False, "reason": "Not captured before failure"}
        paths: Dict[str, str] = {}
        def write_text(name: str, value: str) -> None:
            p = bundle / name
            p.write_text(mask_sensitive_string(value), encoding="utf-8")
            paths[name] = str(p)
        def write_json(name: str, value: Any) -> None:
            p = bundle / name
            p.write_text(json.dumps(mask_sensitive_data(value), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            paths[name] = str(p)
        write_text("exception.txt", repr(exc) if exc else "Logical failure without exception")
        write_text("current_url.txt", "")
        for name in ["last_screenshot.png", "last_dom_snapshot.html", "last_dom_text.txt", "last_20_actions.json", "last_50_network_events.json", "id_candidates.json", "rejected_candidates.json"]:
            write_json(name, unavailable)
        (bundle / "console_logs.jsonl").write_text(json.dumps(unavailable), encoding="utf-8")
        paths["console_logs.jsonl"] = str(bundle / "console_logs.jsonl")
        suggestions = ctx.registry.get("recovery_suggestions") or ["MCP backend unavailable. Set mcp.browser_backend to playwright or auto, or provide an MCP adapter implementation."]
        write_json("recovery_suggestions.json", suggestions)
        partial = {"available": True, "partial": True, "failure": repr(exc) if exc else "Logical failure", "registry": mask_sensitive_data(ctx.registry), "recovery_suggestions": suggestions}
        write_json("partial_knowledge_graph.json", partial)
        (bundle / "partial_knowledge_graph.html").write_text("<html><body><h1>Partial HIP Failure Knowledge Graph</h1><pre>" + json.dumps(mask_sensitive_data(partial), indent=2, ensure_ascii=False, default=str) + "</pre></body></html>", encoding="utf-8")
        paths["partial_knowledge_graph.html"] = str(bundle / "partial_knowledge_graph.html")
        return paths


    async def _run_with_mcp_backend(self, backend: MCPBackend, ctx: RunContext, *, input_json: Optional[str], run_api: bool) -> None:
        """Experimental full MCP path using the pre-existing Chrome DevTools MCP server.

        It performs the same high-level Partner/System flow with MCP tools: navigate, snapshot,
        fill search, press Enter, collect network, verify IDs, and write evidence. It is intentionally
        conservative and evidence-gated; if Chrome DevTools MCP is unavailable or the page cannot be
        controlled via snapshot UIDs, it fails with a normal failure bundle/report instead of silently
        falling back to Playwright.
        """
        ctx.registry["browser_backend_used"] = "mcp"
        ctx.registry["mcp_runtime_adapter"] = "chrome-devtools-mcp stdio"
        for object_type, query, url in [
            ("partner", ctx.partner_query, self.config.navigation.partner_candidate_paths[0]),
            ("system", ctx.system_query, self.config.navigation.system_candidate_paths[0]),
        ]:
            started = utc_now()
            errors: List[str] = []
            warnings: List[str] = []
            screenshots: List[str] = []
            try:
                await backend.navigate(url)
                screenshots.append(await backend.screenshot(ctx.screenshots_dir / f"{object_type}_mcp_01_after_navigation.png"))
                snapshot = await backend.get_dom_snapshot()
                snapshot_path = ctx.run_dir / "dom_snapshots" / f"{object_type}_mcp_snapshot.json"
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                snapshot_path.write_text(json.dumps(mask_sensitive_data(snapshot), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                try:
                    await backend.fill("Search", query)
                except Exception as fill_exc:
                    warnings.append(f"MCP search fill via label 'Search' failed: {fill_exc}")
                    # Some DevTools snapshots name the control by the placeholder or entity type.
                    await backend.fill(f"{object_type} name", query)
                await backend.press("Search", "Enter")
                screenshots.append(await backend.screenshot(ctx.screenshots_dir / f"{object_type}_mcp_02_after_search.png"))
                network_events_raw = await backend.get_network_events()
                snapshot_after = await backend.get_dom_snapshot()
                extracted, debug = self.extractor.extract(
                    object_type=object_type,
                    query=query,
                    url=await backend.get_current_url(),
                    html="",
                    body_text=(snapshot_after.get("snapshot_text") or "")[:200000],
                    network_records=[],
                    network_tab_events=getattr(getattr(backend, "tool_client", None), "network_events", []),
                )
                self._append_candidate_files(ctx, object_type, debug)
                if extracted:
                    self._safe_memory_call(ctx, f"save_verified_{object_type}_id", self.memory.save_extracted_id, ctx.customer, extracted)
                    ctx.registry.update({
                        f"{object_type}_id": extracted.object_id,
                        f"{object_type}_name": extracted.name,
                        f"{object_type}_confidence": extracted.confidence,
                        f"{object_type}_source": extracted.source,
                    })
                    status = "success"
                    message = f"Extracted verified {object_type} ID {extracted.object_id} through Chrome DevTools MCP"
                else:
                    status = "failed"
                    message = f"Could not verify {object_type} ID through Chrome DevTools MCP"
                    errors.append(message)
                    vr = debug.get("verified_result", {}) if isinstance(debug, dict) else {}
                    ctx.registry.setdefault("recovery_suggestions", []).extend(vr.get("recovery_suggestions") or [
                        "Chrome DevTools MCP controlled the page but no verified ID was found. Open the matching row/details page or use Playwright mode for richer DOM table handling."
                    ])
                ctx.stage_results.append(StageResult(
                    stage=f"extract_{object_type}_id_mcp",
                    status=status,
                    message=message,
                    started_at=started,
                    finished_at=utc_now(),
                    screenshots=screenshots,
                    extracted_ids=[extracted] if extracted else [],
                    warnings=warnings,
                    errors=errors,
                    evidence={"snapshot_path": str(snapshot_path), "network_events_seen": len(network_events_raw), "debug": mask_sensitive_data(debug)},
                ))
            except Exception as exc:
                ctx.stage_results.append(StageResult(
                    stage=f"extract_{object_type}_id_mcp",
                    status="failed",
                    message=f"Chrome DevTools MCP stage failed: {exc}",
                    started_at=started,
                    finished_at=utc_now(),
                    screenshots=screenshots,
                    warnings=warnings,
                    errors=[str(exc)],
                ))
                raise
        if input_json:
            await self._enrich_and_maybe_run_api(ctx, input_json, run_api)
        logical_failures = self._logical_failures(ctx, require_partner=bool(ctx.partner_query), require_system=bool(ctx.system_query))
        if logical_failures:
            ctx.registry["logical_failures"] = logical_failures
            ctx.registry["run_status"] = "partial_success" if (ctx.registry.get("partner_id") or ctx.registry.get("system_id")) else "failed"
            ctx.registry.setdefault("recovery_suggestions", []).extend(self._default_logical_recoveries(logical_failures))
            raise RuntimeError("; ".join(logical_failures))
        ctx.registry["run_status"] = "success"

    def _write_exploration_outputs(self, ctx: RunContext) -> None:
        payload = {
            "run_id": ctx.run_id,
            "customer": ctx.customer,
            "partner_query": ctx.partner_query,
            "system_query": ctx.system_query,
            "partner_exploration": ctx.registry.get("partner_exploration"),
            "system_exploration": ctx.registry.get("system_exploration"),
            "notes": [
                "Exploration clicks only safe/read-only menu actions by default.",
                "Unsafe actions such as Delete/Update/Save/Add are skipped unless explicitly enabled.",
                "IDs are saved only through the existing strict evidence-gated extractor/memory path.",
            ],
        }
        p = ctx.run_dir / "portal_exploration.json"
        p.write_text(json.dumps(mask_sensitive_data(payload), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        ctx.registry["portal_exploration_json"] = str(p)

    async def _enrich_and_maybe_run_api(self, ctx: RunContext, input_json: str, run_api: bool) -> None:
        started = utc_now()
        try:
            enriched_path = ctx.run_dir / "enriched_input.json"
            enrich_info = self.api_client.save_enriched_input(
                input_json,
                enriched_path,
                partner_id=ctx.registry.get("partner_id"),
                system_id=ctx.registry.get("system_id"),
            )
            ctx.registry["enriched_input_json"] = str(enriched_path)
            ctx.registry["enriched_input_redacted_json"] = enrich_info.get("redacted_output")
            ctx.registry["enrichment"] = enrich_info
            api_result = None
            if run_api:
                raw = json.loads(Path(input_json).read_text(encoding="utf-8"))
                enriched_payload = self.api_client.enrich_payload(
                    raw,
                    partner_id=ctx.registry.get("partner_id"),
                    system_id=ctx.registry.get("system_id"),
                    account_id=self.config.api.account_id or None,
                )
                api_result = self.api_client.run_configured_requests(enriched_payload)
                ctx.registry["api_result"] = mask_sensitive_data(api_result)
            ctx.stage_results.append(StageResult(
                stage="api_payload_enrichment",
                status="success",
                message="Saved real enriched_input.json and redacted enriched_input.redacted.json" + (" and executed configured API requests" if run_api else ""),
                started_at=started,
                finished_at=utc_now(),
                evidence={"enrichment": enrich_info, "api_result": mask_sensitive_data(api_result)},
            ))
        except Exception as exc:
            ctx.stage_results.append(StageResult(
                stage="api_payload_enrichment",
                status="failed",
                message=f"Failed to enrich/run API: {exc}",
                started_at=started,
                finished_at=utc_now(),
                errors=[str(exc)],
            ))

    async def _extract_one(self, session: BrowserSession, ctx: RunContext, *, object_type: str, query: str) -> Optional[ExtractedID]:
        assert session.page is not None
        page = session.page
        started = utc_now()
        screenshots = []
        warnings = []
        errors = []
        dom_paths: Dict[str, str] = {}

        nav = await self.explorer.navigate_to_area(session, object_type)
        screenshots.append(await session.screenshot(ctx.screenshots_dir / f"{object_type}_01_after_navigation.png"))
        dom_paths.update(await session.save_dom_snapshot(f"{object_type}_after_navigation"))
        if nav.get("status") != "success":
            warnings.append(nav.get("message", f"Navigation to {object_type} was not fully confirmed"))

        search: Dict[str, Any] = {"status": "skipped", "reason": "not run yet"}
        opened: Dict[str, Any] = {"status": "skipped", "reason": "not run yet"}
        extracted: Optional[ExtractedID] = None
        debug: Dict[str, Any] = {}
        verified_result: Dict[str, Any] = {}
        hierarchical_discovery: Dict[str, Any] | None = None

        async def _run_nested_discovery(reason: str) -> bool:
            nonlocal extracted, debug, verified_result, hierarchical_discovery
            nested = await self.explorer.find_nested_entity_via_parent_actions(
                session,
                area=object_type,
                target_type=object_type,
                query=query,
            )
            nested_extracted = nested.pop("_extracted", None) if isinstance(nested, dict) else None
            nested_debug = nested.get("found_debug") if isinstance(nested, dict) else None
            if isinstance(nested_debug, dict):
                self._append_candidate_files(ctx, object_type, nested_debug)
                debug.setdefault("nested_candidate_debug", nested_debug)
            hierarchical_discovery = mask_sensitive_data(nested)
            if isinstance(nested, dict) and nested.get("recovery_suggestions"):
                ctx.registry.setdefault("recovery_suggestions", [])
                ctx.registry["recovery_suggestions"].extend(nested.get("recovery_suggestions") or [])
            if nested_extracted:
                extracted = nested_extracted
                debug.setdefault("hierarchical_discovery", hierarchical_discovery)
                debug.setdefault("verified_result", {
                    "accepted": True,
                    "id_value": nested_extracted.object_id,
                    "confidence": nested_extracted.confidence,
                    "reasons": list((nested_extracted.evidence or {}).get("reasons", [])),
                })
                verified_result = debug.get("verified_result", {}) if isinstance(debug, dict) else {}
                warnings.append(
                    f"Found {object_type} through parent-card nested discovery ({reason}); direct child search was not required."
                )
                return True
            return False

        # In BizLink, child Partner/System records are often not searchable in the
        # parent grid. Example: AS2TEST is under Gmail Account -> Show Partner(s),
        # and AIC-DCE is under Customer Experience (CX) -> View Domain. When
        # exploration is enabled, try that parent-card flow first so we do not
        # keep typing a child query into the wrong top-level Account/Domain search.
        prefer_nested = bool(
            self.config.exploration.enabled
            and getattr(self.config.exploration, "prefer_nested_discovery", True)
            and object_type in {"partner", "system"}
        )
        if prefer_nested:
            await _run_nested_discovery("preferred_nested_discovery")
            try:
                screenshots.append(await session.screenshot(ctx.screenshots_dir / f"{object_type}_02_after_nested_discovery.png"))
                dom_paths.update(await session.save_dom_snapshot(f"{object_type}_after_nested_discovery"))
            except Exception:
                pass

        allow_direct_child_search = bool(
            (not prefer_nested)
            or getattr(self.config.exploration, "allow_direct_child_search_fallback", False)
        )

        if not extracted and allow_direct_child_search:
            search = await self.explorer.search_query(session, query, object_type)
            screenshots.append(await session.screenshot(ctx.screenshots_dir / f"{object_type}_02_after_search.png"))
            dom_paths.update(await session.save_dom_snapshot(f"{object_type}_after_search"))
            if search.get("status") == "failed":
                warnings.append(search.get("message", "Search failed"))

            opened = await self.explorer.maybe_open_first_matching_row(session, query)
            screenshots.append(await session.screenshot(ctx.screenshots_dir / f"{object_type}_03_after_open_details.png"))
            dom_paths.update(await session.save_dom_snapshot(f"{object_type}_after_open_details"))
            if opened.get("status") in {"not_opened", "warning"}:
                warnings.append(opened.get("message", "Detail page was not opened; extracting from list/search page"))

            snapshot = await self.explorer.collect_snapshot(page)
            extracted, debug = self.extractor.extract(
                object_type=object_type,
                query=query,
                url=snapshot.get("url", page.url),
                html=snapshot.get("html", ""),
                body_text=snapshot.get("body_text", ""),
                network_records=session.network_records,
                network_tab_events=session.network_tab_events,
            )
            self._append_candidate_files(ctx, object_type, debug)

            verified_result = debug.get("verified_result", {}) if isinstance(debug, dict) else {}
            if verified_result.get("recovery_suggestions"):
                ctx.registry.setdefault("recovery_suggestions", [])
                ctx.registry["recovery_suggestions"].extend(verified_result.get("recovery_suggestions") or [])
        elif not extracted and prefer_nested:
            search = {
                "status": "skipped",
                "reason": "Nested parent-card discovery is enabled; direct child top-level search is disabled to avoid searching AS2TEST/AIC-DCE in the wrong Account/System search box.",
            }
            opened = {"status": "skipped", "reason": "Direct child row open skipped because direct child search is disabled."}
            debug.setdefault("direct_child_search_skipped", True)

        # Fallback nested discovery if direct extraction still failed and it was not already tried.
        if not extracted and object_type in {"partner", "system"} and not prefer_nested:
            await _run_nested_discovery("direct_search_failed")

        if not extracted and object_type in {"partner", "system"} and prefer_nested:
            # It was already tried before direct search; add a more specific recovery note.
            ctx.registry.setdefault("recovery_suggestions", []).append(
                f"{object_type.title()} was not found after parent-card nested discovery. Direct child top-level search was intentionally skipped to avoid searching the wrong grid. Verify the parent account/card is visible, increase max_pages/max_total_actions, or provide a parent-account hint."
            )

        snapshot = await self.explorer.collect_snapshot(page)
        status = "success" if extracted else "failed"
        message = f"Extracted verified {object_type} ID {extracted.object_id}" if extracted else f"Could not verify {object_type} ID"
        if not extracted:
            errors.append(message)
            if verified_result.get("ambiguous"):
                warnings.append("Conflicting ID candidates found; not saved automatically")
            elif verified_result.get("needs_secondary_verification"):
                warnings.append("Best candidate needs secondary verification before saving")
        elif extracted.confidence < 0.90:
            warnings.append(f"Low confidence extraction: {extracted.confidence}")

        await session.collect_dom_click_log()
        stage = StageResult(
            stage=f"extract_{object_type}_id",
            status=status,
            message=message,
            started_at=started,
            finished_at=utc_now(),
            screenshots=screenshots,
            extracted_ids=[extracted] if extracted else [],
            warnings=warnings,
            errors=errors,
            evidence={
                "navigation": nav,
                "search": search,
                "opened": opened,
                "snapshot_url": snapshot.get("url"),
                "page_title": snapshot.get("title"),
                "dom_snapshot_paths": dom_paths,
                "headings": snapshot.get("headings", [])[:25],
                "tables_detected": len(snapshot.get("tables", [])),
                "controls_detected": len(snapshot.get("controls", [])),
                "network_tab_events_seen": len(session.network_tab_events),
                "click_events_seen": len(session.click_events),
                "action_events_seen": len(session.action_events),
                "hierarchical_discovery": hierarchical_discovery,
                "debug": mask_sensitive_data(debug),
            },
        )
        ctx.stage_results.append(stage)
        return extracted

    def _append_candidate_files(self, ctx: RunContext, object_type: str, debug: Dict[str, Any]) -> None:
        files = {
            "id_candidates": ctx.run_dir / "id_candidates.json",
            "verified_ids": ctx.run_dir / "verified_ids.json",
            "rejected_ids": ctx.run_dir / "rejected_ids.json",
            "recovery_suggestions": ctx.run_dir / "recovery_suggestions.json",
        }
        current = {k: self._read_json(v, [] if k != "recovery_suggestions" else []) for k, v in files.items()}
        for c in debug.get("candidates", []) or []:
            c["object_type"] = object_type
            current["id_candidates"].append(c)
        vr = debug.get("verified_result", {}) or {}
        if vr.get("accepted"):
            current["verified_ids"].append({"object_type": object_type, **vr})
        for c in vr.get("rejected_candidates", []) or []:
            if isinstance(c, dict):
                c["object_type"] = object_type
            current["rejected_ids"].append(c)
        current["recovery_suggestions"].extend(vr.get("recovery_suggestions") or [])
        for k, v in files.items():
            if k == "recovery_suggestions":
                payload = sorted(set(str(x) for x in current[k]))
            else:
                payload = current[k]
            v.write_text(json.dumps(mask_sensitive_data(payload), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            ctx.registry[f"{k}_path"] = str(v)

    def _write_run_level_candidate_files(self, ctx: RunContext) -> None:
        for name, default in [
            ("id_candidates.json", []), ("verified_ids.json", []), ("rejected_ids.json", []), ("recovery_suggestions.json", [])
        ]:
            p = ctx.run_dir / name
            if not p.exists():
                p.write_text(json.dumps(default, indent=2), encoding="utf-8")

    def _read_json(self, path: Path, default: Any) -> Any:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return default
