from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
import re
import secrets
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from hip_id_agent.autogen_runtime import autogen_runtime_status
from hip_id_agent.runtime_env import load_runtime_env, configure_utf8_stdio
from hip_id_agent.aia_client import AIAClient
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import load_config
from hip_id_agent.future_task_agent import HIPFutureTaskPlanner, MUTATION_CONFIRMATION
from hip_id_agent.certified_future_task_agent import CertifiedHIPFutureTaskPlanner
from hip_id_agent.universal_portal_operator import UniversalPortalTaskPlanner, _skill_library
from hip_id_agent.change_governance import HIPChangeGovernance
from hip_id_agent.full_deep_learning import HIPCapabilityCertifier, latest_certification
from hip_id_agent.process_control import pause_process, resume_process
from hip_id_agent.pyautogui_tool import PyAutoGUIFallbackTool
from hip_id_agent.vision_runtime import VisionRuntimeBridge
from hip_id_agent.security import mask_sensitive_string
from hip_id_agent.autowebglm_bridge import AutoWebGLMRecoveryBridge
from hip_id_agent.mission_trace import read_mission_trace
from hip_id_agent.agent_live_view import read_agent_live_view
from hip_id_agent.website_world_model import WebsiteWorldModelMemory
from hip_id_agent.browser_backend import validate_dual_browser_mcps
from hip_id_agent.mlflow_async import mlflow_runtime_probe
from hip_id_agent.replay_policy import replay_policy_engine_from_config
from hip_id_agent.model_portfolio import model_portfolio_from_config
from hip_id_agent.recursive_self_improvement import recursive_improvement_from_config
from hip_id_agent.human_teaching import human_teaching_from_config
from hip_id_agent.human_phase_review import human_phase_review_from_config
from hip_id_agent.interactive_teaching import interactive_teaching_from_config
from hip_id_agent.deterministic_recipe import deterministic_recipe_from_config
from hip_id_agent.trace_self_repair import trace_self_repair_from_config
from hip_id_agent.live_readiness import (
    build_live_readiness_report,
    probe_browser_launch,
    probe_runs_path,
    readiness_fingerprint,
)
from hip_id_agent.live_runtime_certification import certify_live_runtime, verify_latest_live_runtime_certificate
from hip_id_agent.semantic_affordance import status as semantic_affordance_status
from hip_id_agent.semantic_control import semantic_control_mcp_status
from hip_id_agent.expert_skills import build_context_budget, expert_skill_catalog, infer_execution_from_description, vet_expert_skills
from hip_id_agent.section_scope import resolve_section, section_catalog
from hip_id_agent.production_e2e import ProductionE2EOrchestrator, build_request, HashChainedJournal
from hip_id_agent.streamlit_dashboard import (
    build_mission_command,
    build_section_mission_command,
    build_phase_subset_mission_command,
    build_mission_preflight_report,
    collect_repeatable_row_plan,
)


_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_CWD_ROOT = Path.cwd().resolve()
# When installed as a wheel, operate on the caller's project directory when it
# contains a HIP config. HIP_PROJECT_ROOT remains the explicit override.
ROOT = Path(os.getenv("HIP_PROJECT_ROOT", "")).expanduser().resolve() if os.getenv("HIP_PROJECT_ROOT") else (_CWD_ROOT if (_CWD_ROOT / "config.yaml").is_file() else _PACKAGE_ROOT)
UTF8_STDIO_STATUS = configure_utf8_stdio()
RUNTIME_ENV_STATUS = load_runtime_env(ROOT)
PROCESS_FILE = ROOT / ".backend_runtime" / "process.json"
LIVE_READINESS_FILE = ROOT / ".backend_runtime" / "live_readiness.json"
LIVE_READINESS_TTL_SECONDS = 600
PROCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
_LOCK = Lock()


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def _is_running(pid: Any) -> bool:
    try:
        pid_i = int(pid or 0)
    except Exception:
        return False
    if pid_i <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid_i}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=5, check=False)
            return str(pid_i) in (out.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid_i, 0); return True
    except OSError:
        return False


def _process_state() -> Dict[str, Any]:
    state = _read_json(PROCESS_FILE, {})
    if not isinstance(state, dict): state = {}
    state["running"] = _is_running(state.get("pid"))
    if not state["running"]:
        state["paused"] = False
    return state


def _cfg(config: str = "config.yaml"):
    path = Path(config)
    if not path.is_absolute(): path = ROOT / path
    return load_config(path)


def _graph(config: str = "config.yaml") -> HIPCapabilityGraph:
    cfg = _cfg(config)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    if bool(getattr(cfg.brain, "auto_import_unified_kb", True)):
        kb_path = Path(str(getattr(cfg.brain, "unified_kb_path", "./knowledge_base/HIP_Unified_Deep_KB.json")))
        if not kb_path.is_absolute():
            kb_path = (ROOT / kb_path).resolve()
        graph.bootstrap_from_unified_kb(
            kb_path, force=bool(getattr(cfg.brain, "force_unified_kb_reimport", False))
        )
    return graph


def _resolved_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


def _resolve_runs_root(config: str = "config.yaml", runs_dir: str = "") -> Path:
    cfg = _cfg(config)
    root = Path(runs_dir) if str(runs_dir or "").strip() else Path(cfg.reporting.runs_dir)
    if not root.is_absolute():
        root = (ROOT / root).resolve()
    return root


def _resolve_trace_run_dir(*, config: str = "config.yaml", runs_dir: str = "", run_id: str = "") -> Optional[Path]:
    root = _resolve_runs_root(config, runs_dir)
    if run_id:
        candidate = (root / run_id).resolve()
        try:
            candidate.relative_to(root.resolve())
        except Exception:
            return None
        return candidate if (candidate / "mission_trace.json").is_file() else None
    if not root.is_dir():
        return None
    candidates = [p for p in root.iterdir() if p.is_dir() and (p / "mission_trace.json").is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p / "mission_trace.json").stat().st_mtime)


def _read_input_payload(value: str) -> Dict[str, Any]:
    path = _resolved_path(value)
    payload = _read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def _preflight_for_phases(req: Any, phases: list[str]) -> Dict[str, Any]:
    report = build_mission_preflight_report(
        input_json=_resolved_path(req.input_json),
        golden_screenshot_dir=_resolved_path(req.golden_screenshot_dir),
        upload_assets_dir=_resolved_path(req.upload_assets_dir),
        phases=phases,
    )
    repeatable = collect_repeatable_row_plan(_resolved_path(req.input_json), phases=phases) if _resolved_path(req.input_json).is_file() else []
    skills = vet_expert_skills(phases, phase_rows=report.get("phase_rows") or [])
    try:
        cfg = _cfg(getattr(req, "config", "config.yaml"))
    except Exception:
        cfg = None
    try:
        graph = _graph(getattr(req, "config", "config.yaml"))
    except Exception:
        graph = None
    skill_cfg = getattr(cfg, "expert_skills", None) if cfg is not None else None
    context_budget = build_context_budget(
        phases=phases,
        input_payload=_read_input_payload(req.input_json),
        capability_graph=graph,
        max_capabilities_per_family=int(getattr(skill_cfg, "max_capabilities_per_family", 24) or 24),
        max_serialized_chars=int(getattr(skill_cfg, "context_max_chars", 64000) or 64000),
    )
    return {**report, "repeatable_row_plan": repeatable, "skill_vetting": skills, "context_budget": context_budget, "autogen": autogen_runtime_status(verify_imports=True)}


def _readiness_fingerprint_for(req: Any, phases: list[str], *, api_mode: str | None = None) -> str:
    return readiness_fingerprint(
        config=str(getattr(req, "config", "config.yaml") or "config.yaml"),
        input_json=str(getattr(req, "input_json", "") or ""),
        runs_dir=str(getattr(req, "runs_dir", "./runs") or "./runs"),
        golden_screenshot_dir=str(getattr(req, "golden_screenshot_dir", "") or ""),
        upload_assets_dir=str(getattr(req, "upload_assets_dir", "") or ""),
        phases=phases,
        api_mode=str(api_mode if api_mode is not None else getattr(req, "api_mode", "capture") or "capture"),
        execution_profile="live_witness" if bool(getattr(req, "witness_mode", False)) else "standard",
    )


def _store_live_readiness_receipt(report: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    token = secrets.token_urlsafe(24) if report.get("pass") else ""
    receipt = {
        "schema_version": "hip.live-readiness-receipt.v1",
        "token": token,
        "pass": bool(report.get("pass")),
        "decision": report.get("decision"),
        "fingerprint": report.get("fingerprint"),
        "created_at_epoch": now,
        "expires_at_epoch": now + LIVE_READINESS_TTL_SECONDS,
        "blocker_count": int(report.get("blocker_count") or 0),
        "warning_count": int(report.get("warning_count") or 0),
    }
    _write_json(LIVE_READINESS_FILE, receipt)
    return receipt


def _verify_live_readiness_receipt(req: Any, phases: list[str], *, api_mode: str) -> Dict[str, Any]:
    token = str(getattr(req, "readiness_token", "") or "").strip()
    if not token:
        raise HTTPException(status_code=412, detail="Run Live GO/NO-GO readiness before starting the mission.")
    receipt = _read_json(LIVE_READINESS_FILE, {})
    if not isinstance(receipt, dict) or not receipt.get("pass"):
        raise HTTPException(status_code=412, detail="No passing live-readiness receipt is available.")
    if not secrets.compare_digest(str(receipt.get("token") or ""), token):
        raise HTTPException(status_code=412, detail="Live-readiness receipt token does not match the latest server-side readiness run.")
    if float(receipt.get("expires_at_epoch") or 0) < time.time():
        raise HTTPException(status_code=412, detail="Live-readiness receipt expired. Run the live readiness gate again.")
    expected = _readiness_fingerprint_for(req, phases, api_mode=api_mode)
    if str(receipt.get("fingerprint") or "") != expected:
        raise HTTPException(status_code=412, detail="Mission config/input/scope changed after live readiness. Run the live readiness gate again.")
    return receipt


async def _run_live_readiness(req: LiveReadinessRequest, phases: list[str]) -> Dict[str, Any]:
    if bool(getattr(req, "witness_mode", False)) and str(getattr(req, "api_mode", "capture") or "capture").lower() == "write":
        raise HTTPException(status_code=422, detail="Live witness readiness cannot be run with API write mode.")
    cfg = _cfg(req.config)
    static = _preflight_for_phases(req, phases)
    fingerprint = _readiness_fingerprint_for(req, phases, api_mode=req.api_mode)
    probe_root = ROOT / ".backend_runtime" / "live_readiness_probe"
    probe_root.mkdir(parents=True, exist_ok=True)
    browser = await probe_browser_launch(cfg)
    mcp = await validate_dual_browser_mcps(cfg, probe_root / "mcp")
    text_probe = test_text_model(ModelAvailabilityRequest(config=req.config))
    vision_probe = await test_vision_model(ModelAvailabilityRequest(config=req.config))
    runs_root = _resolve_runs_root(req.config, req.runs_dir)
    path_probe = probe_runs_path(runs_root)
    runtime_certificate_probe = verify_latest_live_runtime_certificate(cfg, runs_root)
    live_cfg = getattr(cfg, "live_runtime_certification", None)
    auto_refresh = bool(live_cfg is not None and getattr(live_cfg, "auto_refresh_on_live_readiness", False))
    idle_required = bool(live_cfg is not None and getattr(live_cfg, "auto_refresh_only_when_mission_idle", True))
    mission_idle = not bool(_process_state().get("running"))
    if (
        auto_refresh
        and not bool(runtime_certificate_probe.get("pass"))
        and (mission_idle or not idle_required)
        and bool(static.get("pass", True))
        and bool(browser.get("pass"))
        and bool(text_probe.get("pass"))
        and bool(vision_probe.get("pass"))
        and bool(path_probe.get("pass"))
    ):
        try:
            refresh_result = await certify_live_runtime(
                config=cfg,
                runs_root=runs_root,
                target_url=str(cfg.portal.base_url or ""),
                ttl_seconds=int(getattr(live_cfg, "ttl_seconds", 3600) or 3600),
                require_pyautogui_mcp=bool(getattr(live_cfg, "require_pyautogui_mcp", False)),
            )
            runtime_certificate_probe = verify_latest_live_runtime_certificate(cfg, runs_root)
            runtime_certificate_probe = {
                **runtime_certificate_probe,
                "auto_refresh_attempted": True,
                "auto_refresh_decision": refresh_result.get("decision"),
                "auto_refresh_blockers": refresh_result.get("blockers") or [],
            }
        except Exception as exc:
            runtime_certificate_probe = {
                **runtime_certificate_probe,
                "auto_refresh_attempted": True,
                "auto_refresh_decision": "NO_GO",
                "auto_refresh_error": mask_sensitive_string(str(exc))[:1000],
            }
    report = build_live_readiness_report(
        fingerprint=fingerprint,
        static_preflight=static,
        browser_probe=browser,
        mcp_probe=mcp,
        text_probe=text_probe,
        vision_probe=vision_probe,
        path_probe=path_probe,
        process_state=_process_state(),
        config=cfg,
        runtime_certificate_probe=runtime_certificate_probe,
    )
    receipt = _store_live_readiness_receipt(report)
    return {
        **report,
        "execution_profile": "live_witness" if bool(getattr(req, "witness_mode", False)) else "standard",
        "witness_guarantee": (
            "Submit capture disabled; API write prohibited; mutation-control clicks prohibited; dynamic +Add/openers remain allowed for structural testing."
            if bool(getattr(req, "witness_mode", False)) else ""
        ),
        "token": receipt.get("token") or "",
        "expires_in_seconds": LIVE_READINESS_TTL_SECONDS if report.get("pass") else 0,
        "receipt": {k: v for k, v in receipt.items() if k != "token"},
        "static_preflight": static,
    }


def _start_cli(command: list[str], *, runs_dir: str = "", extra_environment: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    with _LOCK:
        state = _process_state()
        if state.get("running"):
            raise HTTPException(409, f"A HIP backend mission is already running with PID {state.get('pid')}")
        log_path = ROOT / ".backend_runtime" / "mission.log"
        log = log_path.open("a", encoding="utf-8", buffering=1)
        env = os.environ.copy(); env["PYTHONUNBUFFERED"] = "1"; env["PYTHONUTF8"] = "1"; env["PYTHONIOENCODING"] = "utf-8"; env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
        for key, value in (extra_environment or {}).items():
            env[str(key)] = str(value)
        flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
        proc = subprocess.Popen(command, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, creationflags=flags, close_fds=(os.name != "nt"))
        state = {"pid": proc.pid, "running": True, "paused": False, "command": command, "log_path": str(log_path), "runs_dir": runs_dir}
        _write_json(PROCESS_FILE, state)
        return state


def _stop_cli(force: bool = False) -> Dict[str, Any]:
    with _LOCK:
        state = _process_state(); pid = int(state.get("pid") or 0)
        if not state.get("running"): return {"status": "not_running", "pid": pid}
        try:
            if os.name == "nt":
                if not force:
                    try: os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                    except Exception: pass
                subprocess.run(["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else []), capture_output=True, text=True, timeout=15, check=False)
            else:
                os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        finally:
            state["running"] = False; state["paused"] = False; _write_json(PROCESS_FILE, state)
        return {"status": "stopped", "pid": pid}


def _pause_cli() -> Dict[str, Any]:
    with _LOCK:
        state = _process_state(); pid = int(state.get("pid") or 0)
        if not state.get("running"):
            return {"status": "not_running", "pid": pid}
        if state.get("paused"):
            return {"status": "already_paused", "pid": pid}
        result = pause_process(pid)
        state["paused"] = result.get("status") in {"paused", "already_paused"}
        state["paused_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat() if state["paused"] else ""
        _write_json(PROCESS_FILE, state)
        return {**result, "browser_remains_interactive": True}


def _resume_cli() -> Dict[str, Any]:
    with _LOCK:
        state = _process_state(); pid = int(state.get("pid") or 0)
        if not state.get("running"):
            return {"status": "not_running", "pid": pid}
        result = resume_process(pid)
        state["paused"] = False
        state["resumed_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        _write_json(PROCESS_FILE, state)
        return result


class DiscoveryStart(BaseModel):
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    require_mcp: bool = True


class DataMapDeepStart(BaseModel):
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    require_mcp: bool = True


class DocumentTypeDeepStart(BaseModel):
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    require_mcp: bool = True


class RuleDeepStart(BaseModel):
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    require_mcp: bool = True


class TransportProfileDeepStart(BaseModel):
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    require_mcp: bool = True


class BizFlowDeepStart(BaseModel):
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    require_mcp: bool = True


class FullDeepStart(BaseModel):
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    require_mcp: bool = True
    resume_run: str = ""
    continue_on_family_failure: bool = True


class SectionRunStart(BaseModel):
    section: str = "transport-profile"
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    # Adaptive mission default: one healthy governed executor is enough.
    # Set true only for explicit all-MCP certification/debugging.
    require_mcp: bool = False
    vision_verify: bool = True
    full_kb_context: bool = False
    runtime_self_heal: bool = True
    until_complete: bool = False
    write_heavy_evidence: bool = False


class InputUploadRequest(BaseModel):
    filename: str = "input.json"
    content: str


class MissionPreflightRequest(BaseModel):
    section: str = "all"
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    golden_screenshot_dir: str = "./golden_screenshots/UHAUL-POASN"
    upload_assets_dir: str = "./uploads"


class LiveRuntimeCertificationRequest(BaseModel):
    config: str = "config.yaml"
    runs_dir: str = "./runs"
    target_url: str = ""
    ttl_seconds: int = 0
    require_pyautogui_mcp: bool = False


class LiveReadinessRequest(BaseModel):
    section: str = "all"
    witness_mode: bool = False
    description: str = ""
    use_description_trigger: bool = False
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    golden_screenshot_dir: str = "./golden_screenshots/UHAUL-POASN"
    upload_assets_dir: str = "./uploads"
    api_mode: str = "capture"


class DescriptionPlanRequest(BaseModel):
    description: str
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    golden_screenshot_dir: str = "./golden_screenshots/UHAUL-POASN"
    upload_assets_dir: str = "./uploads"


class MissionStart(BaseModel):
    section: str = "all"
    witness_mode: bool = False
    description: str = ""
    use_description_trigger: bool = False
    config: str = "config.yaml"
    input_json: str = "./examples/uhaul_poasn_full_dummy_input.json"
    runs_dir: str = "./runs"
    golden_screenshot_dir: str = "./golden_screenshots/UHAUL-POASN"
    upload_assets_dir: str = "./uploads"
    api_mode: str = "capture"
    write_heavy_evidence: bool = True
    allow_api_mutation: bool = False
    resume_run: str = ""
    until_complete: bool = False
    readiness_token: str = ""




class ModelAvailabilityRequest(BaseModel):
    config: str = "config.yaml"


def _model_test_error_kind(exc: Exception) -> str:
    text = str(exc or "").lower()
    if any(token in text for token in ("401", "403", "unauthor", "forbidden", "token", "credential")):
        return "authentication"
    if any(token in text for token in ("timeout", "timed out", "connection", "dns", "name resolution")):
        return "connectivity_or_timeout"
    if "endpoint" in text or "404" in text:
        return "endpoint_or_route"
    if any(token in text for token in ("429", "rate limit", "too many requests")):
        return "rate_limited"
    if "http " in text or any(code in text for code in ("400", "500", "502", "503", "504")):
        return "http_error"
    return "model_call_error"


def _safe_model_summary(client: AIAClient) -> Dict[str, Any]:
    summary = client.provider_summary()
    return {
        "provider": summary.get("llm_provider") or "Dell AIA GenAI Gateway",
        "model": summary.get("model") or "",
        "endpoint_configured": bool(summary.get("endpoint_configured")),
        "auth_mode": summary.get("auth_mode") or "",
        "provider_lock": summary.get("provider_lock") or "",
    }


class FutureTaskRequest(BaseModel):
    task: str
    config: str = "config.yaml"
    runs_dir: str = "./runs"
    allow_portal_mutation: bool = False
    confirmation: str = ""
    allow_adaptive_exploration: bool = True
    input_json: str = "./input.json"
    input_root: str = ""
    start_url: str = ""
    deep_learn: bool = True
    operator_role: str = ""
    approval_id: str = ""
    force_repeat_mutation: bool = False


class ProductionE2ERequest(BaseModel):
    task: str = ""
    config: str = "config.yaml"
    input_json: str = "./input.json"
    input_root: str = ""
    start_url: str = ""
    runs_dir: str = "./runs"
    golden_dir: str = "./golden_screenshots/UHAUL-POASN"
    uploads_dir: str = "./uploads"
    deep_learn: bool = True
    allow_portal_mutation: bool = False
    confirmation: str = ""
    operator_role: str = ""
    approval_id: str = ""
    force_repeat_mutation: bool = False
    mutation_expected: bool = False


class HumanTeachingRequest(BaseModel):
    request_id: str
    input_path: str
    semantic_control_id: str = ""
    control_label: str = ""
    section: str = ""
    role: str = ""
    note: str = ""
    verified_by_human: bool = True
    config: str = "config.yaml"


class HumanPhaseReviewRequest(BaseModel):
    request_id: str
    verdict: str
    note: str = ""
    reviewer: str = "human"
    config: str = "config.yaml"


class InteractiveTeachingStartRequest(BaseModel):
    run_id: str = ""
    phase: str = ""
    task: str = ""
    note: str = ""
    config: str = "config.yaml"


class InteractiveTeachingFinishRequest(BaseModel):
    session_id: str
    note: str = ""
    config: str = "config.yaml"


app = FastAPI(title="HIP Browser Intelligence Backend", version="4.3.0")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "hip-browser-intelligence-backend"}




@app.post("/api/models/text/test")
def test_text_model(req: ModelAvailabilityRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config)
    client = AIAClient(cfg.aia)
    summary = _safe_model_summary(client)
    started = time.perf_counter()
    try:
        response = client.chat_rest(
            [
                {"role": "system", "content": "You are an availability probe. Follow the response contract exactly."},
                {"role": "user", "content": "Return exactly HIP_TEXT_OK and nothing else."},
            ],
            temperature=0.0,
            max_tokens=None,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        text = str(response or "").strip()
        marker_seen = "HIP_TEXT_OK" in text
        available = bool(text)
        return {
            "kind": "text",
            "pass": available,
            "status": "available" if available else "empty_probe_response",
            **summary,
            "latency_ms": latency_ms,
            "response_received": available,
            "response_chars": len(text),
            "response_preview": mask_sensitive_string(text)[:800],
            "output_token_cap": "native/uncapped" if not os.getenv("AIA_TEXT_MAX_OUTPUT_TOKENS") and not os.getenv("AIA_MAX_OUTPUT_TOKENS") else "configured",
            "expected_marker_seen": marker_seen,
            "capability_verified": available,
            "contract_warning": "" if marker_seen else (
                "The Dell AIA deployment returned a valid non-empty response but did not echo the synthetic marker exactly. "
                "Availability is PASS; downstream HIP judges still validate task-specific output before any action."
                if available else ""
            ),
            "error_kind": "" if available else "empty_response",
            "error": "" if available else "The configured Dell AIA deployment returned an empty response.",
            "env_files_loaded": list((RUNTIME_ENV_STATUS or {}).get("loaded_files") or []),
        }
    except Exception as exc:
        return {
            "kind": "text",
            "pass": False,
            "status": "unavailable",
            **summary,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "response_received": False,
            "response_chars": 0,
            "expected_marker_seen": False,
            "error_kind": _model_test_error_kind(exc),
            "error": str(exc)[:700],
        }


@app.post("/api/models/vision/test")
async def test_vision_model(req: ModelAvailabilityRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config)
    bridge = VisionRuntimeBridge(cfg.vision_runtime, aia_config=cfg.aia)
    started = time.perf_counter()
    try:
        result = await bridge.preflight(force=True)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        ok = bool(result.get("pass")) and str(result.get("status") or "") == "ok"
        attempts = result.get("attempts") if isinstance(result.get("attempts"), list) else []
        http_statuses = [a.get("http_status") for a in attempts if isinstance(a, dict) and a.get("http_status") is not None]
        status = bridge.status()
        configured_candidates = list(status.get("explicit_model_candidates") or [])
        if not configured_candidates:
            candidate_fn = getattr(bridge, "_candidates", None)
            configured_candidates = list(candidate_fn() if callable(candidate_fn) else [])
        resolved_model = result.get("model") or status.get("selected_model") or (configured_candidates[0] if configured_candidates else "")
        return {
            "kind": "vision",
            "pass": ok,
            "status": "available" if ok else "unavailable",
            "provider": "Dell AIA GenAI Gateway",
            "model": resolved_model,
            "configured_candidates": configured_candidates,
            "endpoint_configured": bool(bridge._endpoint()),
            "env_files_loaded": list((RUNTIME_ENV_STATUS or {}).get("loaded_files") or []),
            "latency_ms": latency_ms,
            "image_understanding_verified": ok,
            "response_preview": str(result.get("response_preview") or next((a.get("response") for a in reversed(attempts) if isinstance(a, dict) and a.get("response")), ""))[:800],
            "output_token_cap": "native/uncapped" if not os.getenv("AIA_VISION_MAX_OUTPUT_TOKENS") and not os.getenv("AIA_MAX_OUTPUT_TOKENS") else "configured",
            "attempt_count": len(attempts),
            "http_statuses": http_statuses[-5:],
            "error_kind": "" if ok else _model_test_error_kind(RuntimeError(result.get("error") or "vision probe failed")),
            "error": "" if ok else str(result.get("error") or "No configured vision deployment proved image understanding.")[:700],
        }
    except Exception as exc:
        status = bridge.status()
        configured_candidates = list(status.get("explicit_model_candidates") or [])
        if not configured_candidates:
            candidate_fn = getattr(bridge, "_candidates", None)
            configured_candidates = list(candidate_fn() if callable(candidate_fn) else [])
        return {
            "kind": "vision",
            "pass": False,
            "status": "unavailable",
            "provider": "Dell AIA GenAI Gateway",
            "model": status.get("selected_model") or (configured_candidates[0] if configured_candidates else ""),
            "configured_candidates": configured_candidates,
            "endpoint_configured": bool(bridge._endpoint()),
            "env_files_loaded": list((RUNTIME_ENV_STATUS or {}).get("loaded_files") or []),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "image_understanding_verified": False,
            "attempt_count": 0,
            "http_statuses": [],
            "error_kind": _model_test_error_kind(exc),
            "error": str(exc)[:700],
        }


@app.get("/api/runtime/status")
def runtime_status(config: str = "config.yaml") -> Dict[str, Any]:
    # A freshly installed wheel may be launched before the operator has copied a
    # project config beside it.  The Control Center must still boot and explain
    # what is missing rather than converting first-run setup into an HTTP 500.
    try:
        cfg = _cfg(config)
        graph = _graph(config)
    except FileNotFoundError as exc:
        config_path = Path(config)
        if not config_path.is_absolute():
            config_path = (ROOT / config_path).resolve()
        return {
            "status": "setup_required",
            "pass": False,
            "setup_required": True,
            "config": {
                "found": False,
                "requested": str(config),
                "resolved_path": str(config_path),
                "error": str(exc),
                "hint": "Run hip-portal from a HIP project directory containing config.yaml, or set HIP_PROJECT_ROOT.",
            },
            "runtime_env": {
                "loaded": bool((RUNTIME_ENV_STATUS or {}).get("loaded")),
                "loaded_file_count": len((RUNTIME_ENV_STATUS or {}).get("loaded_files") or []),
                "explicit_env_file_configured": bool((RUNTIME_ENV_STATUS or {}).get("explicit_env_file")),
            },
            "autogen": autogen_runtime_status(verify_imports=True),
            "mlflow": {"enabled": False, "package_available": False, "setup_required": True},
            "skill_induction": {"enabled": False, "skill_count": 0, "validated_skill_count": 0, "setup_required": True},
            "replay_policy": {"enabled": False, "policy_count": 0, "episode_count": 0, "setup_required": True},
            "model_portfolio": {"enabled": False, "text_models": [], "vision_models": [], "role_champions": {}, "setup_required": True},
            "recursive_self_improvement": {"enabled": False, "cycle": 0, "setup_required": True},
            "trace_self_repair": {"enabled": False, "setup_required": True},
            "deterministic_recipe": {"enabled": False, "validated_recipe_count": 0, "setup_required": True},
            "human_in_the_loop": {"enabled": False, "pending_count": 0, "setup_required": True},
            "process": _process_state(),
            "capability_graph": {"capability_count": 0, "setup_required": True},
            "full_deep_readiness": {"pass": False, "status": "setup_required"},
            "production_e2e": {
                "enabled": False,
                "setup_required": True,
                "same_process_control_center": True,
                "one_command_entrypoint": "hip-agent run-production-e2e",
            },
            "control_center": {
                "available": bool(_WEBUI_DIR and (_WEBUI_DIR / "index.html").is_file()),
                "served_by_backend": True,
            },
        }
    latest = latest_certification(cfg.reporting.runs_dir)
    return {
        "runtime_env": {
            "loaded": bool((RUNTIME_ENV_STATUS or {}).get("loaded")),
            "loaded_file_count": len((RUNTIME_ENV_STATUS or {}).get("loaded_files") or []),
            "explicit_env_file_configured": bool((RUNTIME_ENV_STATUS or {}).get("explicit_env_file")),
        },
        "autogen": autogen_runtime_status(verify_imports=True),
        "mlflow": mlflow_runtime_probe(cfg.mlflow),
        "skill_induction": {
            "enabled": bool(cfg.skill_induction.enabled),
            "fast_replay_enabled": bool(cfg.skill_induction.fast_replay_enabled),
            **_skill_library(cfg).manifest(),
        },
        "replay_policy": {
            "enabled": bool(cfg.replay_policy.enabled),
            "dreaming_enabled": bool(cfg.replay_policy.dreaming_enabled),
            **replay_policy_engine_from_config(cfg).manifest(),
        },
        "model_portfolio": model_portfolio_from_config(cfg).manifest(),
        "recursive_self_improvement": recursive_improvement_from_config(
            cfg, replay_policy=replay_policy_engine_from_config(cfg),
            model_portfolio=model_portfolio_from_config(cfg), skill_library=_skill_library(cfg)
        ).manifest(),
        "trace_self_repair": trace_self_repair_from_config(
            cfg, model_portfolio=model_portfolio_from_config(cfg)
        ).manifest(),
        "deterministic_recipe": deterministic_recipe_from_config(cfg).manifest(),
        "human_in_the_loop": human_teaching_from_config(cfg).manifest(),
        "human_phase_review": human_phase_review_from_config(cfg).manifest(),
        "pyautogui": PyAutoGUIFallbackTool(cfg, ROOT / ".backend_runtime" / "pyautogui_status").status(),
        "process": _process_state(),
        "capability_graph": graph.manifest(),
        "full_deep_readiness": (latest.get("certification") or {}) if latest.get("found") else HIPCapabilityCertifier(graph).certify(),
        "mutation_confirmation": MUTATION_CONFIRMATION,
        "independent_sections": section_catalog(),
        "expert_skills": {
            "enabled": bool(cfg.expert_skills.enabled),
            "description_trigger_enabled": bool(cfg.expert_skills.description_trigger_enabled),
            "require_skill_vetting": bool(cfg.expert_skills.require_skill_vetting),
            "deterministic_first": bool(cfg.expert_skills.deterministic_first),
            "primary_browser_framework": "autowebglm" if bool(cfg.autowebglm.primary_framework) else "deterministic",
            "deterministic_drivers_role": "verified_tool_adapters",
            "context_max_chars": int(cfg.expert_skills.context_max_chars),
            "recovery_context_max_chars": int(cfg.expert_skills.recovery_context_max_chars),
            "catalog": expert_skill_catalog(),
        },
        "playwright_mcp": {
            "enabled": bool(cfg.mcp.use_playwright_mcp),
            "required_when_mcp_required": bool(cfg.mcp.playwright_mcp_required_when_require_mcp),
            "primary_for_safe_actions": bool(cfg.mcp.playwright_mcp_primary_for_safe_actions),
            "verify_every_action": bool(cfg.mcp.playwright_mcp_verify_every_action),
            "snapshot_after_action": bool(cfg.mcp.playwright_mcp_snapshot_after_action),
            "same_browser_cdp_port": int(cfg.mcp.playwright_mcp_remote_debugging_port),
            "execution_contract": "AutoWebGLM planner -> semantic target proof -> PyAutoGUI MCP primary physical interaction -> Playwright MCP fallback/verification -> deterministic Python Playwright last compatibility fallback",
        },
        "browser_use": {
            "enabled": bool(cfg.browser_use.enabled),
            "same_browser_cdp": bool(cfg.browser_use.attach_same_browser),
            "recovery_context": bool(cfg.expert_skills.browser_use_recovery_context),
            "role": "same-CDP recovery perception; optional browser-use/web-ui sidecar is observational/debugging, while PyAutoGUI MCP is the primary physical interaction engine",
        },
        "browser": {
            "channel": str(cfg.portal.chromium_channel or "chrome"),
            "primary_name": ("Google Chrome" if str(cfg.portal.chromium_channel or "").strip().lower() == "chrome" else "Microsoft Edge" if str(cfg.portal.chromium_channel or "").strip().lower() == "msedge" else str(cfg.portal.chromium_channel or "Chromium")),
            "chrome_primary": str(cfg.portal.chromium_channel or "").strip().lower() == "chrome",
            "edge_primary": str(cfg.portal.chromium_channel or "").strip().lower() == "msedge",
            "user_data_dir": str(cfg.portal.browser_user_data_dir),
            "chrome_executable_configured": bool(str(getattr(cfg.portal, "chrome_executable_path", "") or "").strip()),
            "edge_executable_configured": bool(str(getattr(cfg.portal, "edge_executable_path", "") or "").strip()),
            "fallback_to_edge": bool(getattr(cfg.portal, "fallback_to_edge", True)),
            "cdp_shared_session": bool(cfg.browser_use.attach_same_browser),
        },
        "vision_runtime": {
            **VisionRuntimeBridge(cfg.vision_runtime, aia_config=cfg.aia).status(),
            "use_for_recovery": bool(cfg.vision_runtime.use_for_recovery),
            "use_for_loading_watchdog": bool(cfg.vision_runtime.use_for_loading_watchdog),
            "loading_refresh_after_seconds": int(cfg.vision_runtime.loading_refresh_after_seconds),
            "loading_confidence_threshold": float(cfg.vision_runtime.loading_confidence_threshold),
            "loading_fail_closed": bool(cfg.vision_runtime.fail_closed_when_unavailable),
        },
        "autowebglm": {
            **AutoWebGLMRecoveryBridge(cfg.autowebglm, aia_config=cfg.aia).status(),
            "reward_gate_required": bool(cfg.autowebglm.require_agentq_reward_gate),
            "allowed_actions": list(cfg.autowebglm.allowed_actions),
            "official_action_count": len(list(cfg.autowebglm.allowed_actions)),
        },
        "semantic_affordances": {
            **semantic_affordance_status(),
            "repeatable_row_semantic_identity": True,
            "exact_add_effect": "N -> N+1",
            "row_rebind_after_angular_generation": True,
            "autowebglm_primary_policy": bool(cfg.autowebglm.primary_framework),
        },
        "semantic_understanding": {
            **semantic_control_mcp_status(),
            "enabled": bool(cfg.semantic_understanding.enabled),
            "execute_confidence_threshold": float(cfg.semantic_understanding.execute_confidence_threshold),
            "reobserve_confidence_threshold": float(cfg.semantic_understanding.reobserve_confidence_threshold),
            "ambiguity_margin": float(cfg.semantic_understanding.ambiguity_margin),
            "fail_closed": bool(cfg.semantic_understanding.fail_closed),
            "revalidate_before_dispatch": bool(cfg.semantic_understanding.revalidate_before_dispatch),
            "require_post_action_effect": bool(cfg.semantic_understanding.require_post_action_effect),
            "playwright_mcp_evidence_required": bool(cfg.semantic_understanding.require_playwright_mcp_evidence),
            "devtools_mcp_evidence_required": bool(cfg.semantic_understanding.require_devtools_evidence),
            "hip_intelligence_mcp_consensus": bool(cfg.semantic_understanding.use_hip_intelligence_mcp_consensus),
            "hip_intelligence_mcp_evidence_required": bool(cfg.semantic_understanding.require_hip_intelligence_mcp_evidence),
            "vision_role": "ambiguity confirmation only; never coordinate execution",
            "execution_contract": "AutoWebGLM intent -> HIP semantic evidence fusion -> Playwright MCP execute -> exact semantic effect verification",
        },
        "langchain_browser_toolkit": {
            "enabled": bool(cfg.langchain_browser_toolkit.enabled),
            "same_browser_cdp": bool(cfg.langchain_browser_toolkit.attach_same_browser),
            "read_only": bool(cfg.langchain_browser_toolkit.read_only),
            "allowed_tools": list(cfg.langchain_browser_toolkit.allowed_tools),
            "role": "read-only recovery perception under AutoWebGLM primary policy; navigation/click tools are not exposed",
        },
        "production_e2e": {
            "enabled": bool(cfg.production_e2e.enabled),
            "single_active_browser_session": bool(cfg.production_e2e.single_active_browser_session),
            "lock_filename": str(cfg.production_e2e.lock_filename),
            "lock_stale_seconds": int(cfg.production_e2e.lock_stale_seconds),
            "lock_heartbeat_seconds": int(cfg.production_e2e.lock_heartbeat_seconds),
            "require_autogen_075": bool(cfg.production_e2e.require_autogen_075),
            "require_input_json_when_fill_requested": bool(cfg.production_e2e.require_input_json_when_fill_requested),
            "require_live_runtime_certificate_for_mutation": bool(cfg.production_e2e.require_live_runtime_certificate_for_mutation),
            "require_golden_reference_for_known_phase_tasks": bool(cfg.production_e2e.require_golden_reference_for_known_phase_tasks),
            "require_operator_role_for_mutation": bool(cfg.production_e2e.require_operator_role_for_mutation),
            "require_approval_id_for_mutation": bool(cfg.production_e2e.require_approval_id_for_mutation),
            "require_execution_input_immutability": bool(cfg.production_e2e.require_execution_input_immutability),
            "require_governance_ledger_integrity_for_mutation": bool(cfg.production_e2e.require_governance_ledger_integrity_for_mutation),
            "capture_mutation_before_after_evidence": bool(cfg.production_e2e.capture_mutation_before_after_evidence),
            "create_safe_review_bundle": bool(cfg.production_e2e.create_safe_review_bundle),
            "safe_review_bundle_name": str(cfg.production_e2e.safe_review_bundle_name),
            "safe_review_strict_allowlist": bool(cfg.production_e2e.safe_review_strict_allowlist),
            "execution_integrity_receipt": bool(cfg.production_e2e.write_execution_integrity_receipt),
            "failure_diagnostics": bool(cfg.production_e2e.write_failure_diagnostics),
            "hash_chained_journal": bool(cfg.production_e2e.write_hash_chained_journal),
            "same_process_control_center": True,
            "one_command_entrypoint": "hip-agent run-production-e2e",
        },
        "governance": {
            "enabled": bool(cfg.governance.enabled),
            "operator_role_env_var": cfg.governance.operator_role_env_var,
            "default_operator_role": cfg.governance.default_operator_role,
            "require_approval_id_for_mutation": cfg.governance.require_approval_id_for_mutation,
            "mutation_roles": cfg.governance.mutation_roles,
            "duplicate_window_hours": cfg.governance.duplicate_window_hours,
        },
    }



@app.post("/api/production/doctor")
def production_doctor(req: ProductionE2ERequest) -> Dict[str, Any]:
    config_path = _resolved_path(req.config)
    orchestrator = ProductionE2EOrchestrator.from_path(config_path, root=ROOT)
    request = build_request(
        root=ROOT, task=req.task or "production doctor", config_path=config_path, input_json=req.input_json,
        input_root=req.input_root, start_url=req.start_url, runs_dir=req.runs_dir, golden_dir=req.golden_dir,
        uploads_dir=req.uploads_dir, deep_learn=req.deep_learn, allow_portal_mutation=req.allow_portal_mutation,
        confirmation=req.confirmation, operator_role=req.operator_role, approval_id=req.approval_id,
        force_repeat_mutation=req.force_repeat_mutation,
    )
    return orchestrator.doctor(request, mutation_expected=bool(req.mutation_expected))


@app.post("/api/production/start")
def production_start(req: ProductionE2ERequest) -> Dict[str, Any]:
    if not str(req.task or "").strip():
        raise HTTPException(status_code=422, detail="A production task description is required.")
    command = [
        sys.executable, "-u", "-m", "hip_id_agent.cli", "run-production-e2e", req.task,
        "--config", req.config, "--input-json", req.input_json, "--runs-dir", req.runs_dir,
        "--golden-dir", req.golden_dir, "--uploads-dir", req.uploads_dir,
    ]
    if req.input_root:
        command.extend(["--input-root", req.input_root])
    if req.start_url:
        command.extend(["--start-url", req.start_url])
    command.append("--deep-learn" if req.deep_learn else "--no-deep-learn")
    if req.allow_portal_mutation:
        command.append("--allow-portal-mutation")
    if req.confirmation:
        command.extend(["--confirmation", req.confirmation])
    if req.operator_role:
        command.extend(["--operator-role", req.operator_role])
    if req.approval_id:
        command.extend(["--approval-id", req.approval_id])
    if req.force_repeat_mutation:
        command.append("--force-repeat-mutation")
    return _start_cli(command, runs_dir=req.runs_dir)


@app.get("/api/production/journal/verify")
def production_journal_verify(path: str = Query(..., min_length=1)) -> Dict[str, Any]:
    target = _resolved_path(path)
    return HashChainedJournal(target).verify()


@app.get("/api/sections")
def available_sections() -> Dict[str, Any]:
    rows = section_catalog()
    return {"count": len(rows), "sections": rows}


@app.post("/api/input/upload")
def upload_input_json(req: InputUploadRequest) -> Dict[str, Any]:
    try:
        payload = json.loads(req.content)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("objects"), dict):
        raise HTTPException(status_code=422, detail="Input JSON must contain an object root with an 'objects' object.")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(req.filename or "input.json").name)
    if not safe_name.lower().endswith(".json"):
        safe_name += ".json"
    target_dir = ROOT / ".backend_runtime" / "inputs"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "stored", "filename": safe_name, "path": str(target), "object_keys": sorted(payload["objects"].keys())}


@app.post("/api/mission/preflight")
def mission_preflight(req: MissionPreflightRequest) -> Dict[str, Any]:
    try:
        spec = resolve_section(req.section)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    report = _preflight_for_phases(req, list(spec["phases"]))
    return {**report, "section": spec}


@app.post("/api/mission/description-plan")
def mission_description_plan(req: DescriptionPlanRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config)
    if not bool(cfg.expert_skills.enabled and cfg.expert_skills.description_trigger_enabled):
        raise HTTPException(status_code=422, detail="Description-triggered expert skills are disabled by config")
    plan = infer_execution_from_description(req.description)
    if not plan.get("pass"):
        raise HTTPException(status_code=422, detail=str(plan.get("reason") or "Could not infer HIP execution from description"))
    phases = list(plan.get("phases") or [])
    report = _preflight_for_phases(req, phases)
    return {**report, "description_plan": plan, "section": plan.get("section"), "description_triggered": True}




@app.post("/api/mission/live-runtime-certification")
async def mission_live_runtime_certification(req: LiveRuntimeCertificationRequest) -> Dict[str, Any]:
    if bool(_process_state().get("running")):
        raise HTTPException(status_code=409, detail="Stop the active mission before running live runtime certification.")
    cfg = _cfg(req.config)
    runs_root = _resolve_runs_root(req.config, req.runs_dir)
    live_cfg = getattr(cfg, "live_runtime_certification", None)
    ttl = int(req.ttl_seconds or getattr(live_cfg, "ttl_seconds", 3600) or 3600)
    require_py = bool(req.require_pyautogui_mcp if req.require_pyautogui_mcp is not None else getattr(live_cfg, "require_pyautogui_mcp", False))
    return await certify_live_runtime(
        config=cfg,
        runs_root=runs_root,
        target_url=req.target_url or str(cfg.portal.base_url or ""),
        ttl_seconds=ttl,
        require_pyautogui_mcp=require_py,
    )


@app.post("/api/mission/live-readiness")
async def mission_live_readiness(req: LiveReadinessRequest) -> Dict[str, Any]:
    if req.use_description_trigger or str(req.section).strip().lower() == "auto":
        cfg_for_skill = _cfg(req.config)
        if not bool(cfg_for_skill.expert_skills.enabled and cfg_for_skill.expert_skills.description_trigger_enabled):
            raise HTTPException(status_code=422, detail="Description-triggered expert skills are disabled by config")
        plan = infer_execution_from_description(req.description)
        if not plan.get("pass"):
            raise HTTPException(status_code=422, detail=str(plan.get("reason") or "Description trigger could not infer a HIP section"))
        spec = dict(plan.get("section") or {})
    else:
        try:
            spec = resolve_section(req.section)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    phases = list(spec.get("phases") or [])
    if not phases:
        raise HTTPException(status_code=422, detail="Live readiness has no selected HIP phases")
    report = await _run_live_readiness(req, phases)
    return {**report, "section": spec}


@app.post("/api/mission/start")
def start_mission(req: MissionStart) -> Dict[str, Any]:
    description_plan: Dict[str, Any] = {}
    if req.use_description_trigger or str(req.section).strip().lower() == "auto":
        cfg_for_skill = _cfg(req.config)
        if not bool(cfg_for_skill.expert_skills.enabled and cfg_for_skill.expert_skills.description_trigger_enabled):
            raise HTTPException(status_code=422, detail="Description-triggered expert skills are disabled by config")
        description_plan = infer_execution_from_description(req.description)
        if not description_plan.get("pass"):
            raise HTTPException(status_code=422, detail=str(description_plan.get("reason") or "Description trigger could not infer a HIP section"))
        spec = dict(description_plan.get("section") or {})
    else:
        try:
            spec = resolve_section(req.section)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    phases = list(spec.get("phases") or [])
    if not phases:
        raise HTTPException(status_code=422, detail="Mission has no selected HIP phases")
    # Vet the deterministic expert skill and selected input contract before any
    # browser process is started. Browser-Use/PyAutoGUI cannot substitute for a
    # missing or invalid phase skill.
    vet_report = _preflight_for_phases(req, phases)
    if not bool(vet_report.get("pass")):
        raise HTTPException(status_code=422, detail="Mission preflight failed for the selected description/section")
    cfg_for_vetting = _cfg(req.config)
    if bool(cfg_for_vetting.expert_skills.require_skill_vetting) and not bool((vet_report.get("skill_vetting") or {}).get("pass")):
        raise HTTPException(status_code=422, detail="One or more deterministic HIP expert skills failed vetting")
    mode = str(req.api_mode or "capture").strip().lower()
    if mode not in {"capture", "dry_run", "validate", "write"}:
        raise HTTPException(status_code=422, detail="api_mode must be capture, dry_run, validate, or write")
    if mode == "write" and not req.allow_api_mutation:
        raise HTTPException(status_code=422, detail="API write mode requires explicit allow_api_mutation=true")
    if req.witness_mode:
        if mode == "write" or req.allow_api_mutation:
            raise HTTPException(status_code=422, detail="Live witness mode is strictly non-mutating: API write and mutation authorization are prohibited.")
        # Witness runs are designed to prove the integrated agent without ever
        # clicking a Create/Save/Submit/Deploy/Delete mutation control.
        mode = "capture"
    _verify_live_readiness_receipt(req, phases, api_mode=mode)
    if spec["id"] == "all":
        command = build_mission_command(
            project_root=ROOT, config=req.config, input_json=req.input_json, runs_dir=req.runs_dir,
            golden_screenshot_dir=req.golden_screenshot_dir, upload_assets_dir=req.upload_assets_dir,
            api_mode=mode, write_heavy_evidence=req.write_heavy_evidence,
            allow_api_mutation=req.allow_api_mutation, resume_run_dir=req.resume_run or None,
            witness_mode=bool(req.witness_mode),
            python_executable=sys.executable,
        )
    elif spec.get("id") == "custom":
        command = build_phase_subset_mission_command(
            project_root=ROOT, config=req.config, input_json=req.input_json, runs_dir=req.runs_dir,
            golden_screenshot_dir=req.golden_screenshot_dir, upload_assets_dir=req.upload_assets_dir,
            phases=phases, api_mode=mode, write_heavy_evidence=req.write_heavy_evidence,
            allow_api_mutation=req.allow_api_mutation, until_complete=req.until_complete,
            witness_mode=bool(req.witness_mode),
            python_executable=sys.executable,
        )
    else:
        command = build_section_mission_command(
            project_root=ROOT, config=req.config, input_json=req.input_json, runs_dir=req.runs_dir,
            golden_screenshot_dir=req.golden_screenshot_dir, upload_assets_dir=req.upload_assets_dir,
            section=spec["id"], api_mode=mode, write_heavy_evidence=req.write_heavy_evidence,
            allow_api_mutation=req.allow_api_mutation, until_complete=req.until_complete,
            witness_mode=bool(req.witness_mode),
            python_executable=sys.executable,
        )
    env = {
        "AIA_USE_AUTOGEN": "true",
        "HIP_USE_LLM_FORM_PLANNER": "true",
        "HIP_REQUIRE_AUTOGEN_075": "true",
    }
    if req.allow_api_mutation:
        env["HIP_ALLOW_API_MUTATION"] = "YES"
    if req.witness_mode:
        env["HIP_LIVE_WITNESS"] = "true"
    state = _start_cli(command, runs_dir=req.runs_dir, extra_environment=env)
    return {
        **state, "section": spec, "api_mode": mode, "witness_mode": bool(req.witness_mode),
        "description_triggered": bool(description_plan),
        "description_plan": description_plan,
        "skill_vetting": vet_report.get("skill_vetting"),
        "context_budget": vet_report.get("context_budget"),
    }


@app.post("/api/section-run/start")
def start_section_run(req: SectionRunStart) -> Dict[str, Any]:
    try:
        spec = resolve_section(req.section)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    command = [
        sys.executable, "-u", "-m", "hip_id_agent.cli", "run-full-dummy-fill",
        "--config", req.config,
        "--input-json", req.input_json,
        "--customer", f"SECTION-{str(spec['id']).upper().replace('-', '_')}",
        "--runs-dir", req.runs_dir,
        "--phases", str(spec["phase_csv"]),
        "--full-kb-context" if req.full_kb_context else "--fast-form-only",
        "--vision-verify" if req.vision_verify else "--no-vision-verify",
        "--require-mcp" if req.require_mcp else "--allow-executor-fallback",
        "--runtime-self-heal" if req.runtime_self_heal else "--no-runtime-self-heal",
        "--runtime-self-heal-until-complete" if req.until_complete else "--bounded-runtime-self-heal",
        "--write-heavy-evidence" if req.write_heavy_evidence else "--no-write-heavy-evidence",
    ]
    state = _start_cli(command, runs_dir=req.runs_dir)
    return {
        **state,
        "section": spec["id"],
        "section_label": spec["label"],
        "selected_phases": spec["phases"],
        "isolated": spec["isolated"],
        "dependency_note": spec["dependency_note"],
    }


@app.post("/api/discovery/start")
def start_discovery(req: DiscoveryStart) -> Dict[str, Any]:
    command = [sys.executable, "-u", "-m", "hip_id_agent.cli", "learn-hip", "--config", req.config, "--input-json", req.input_json, "--customer", "HIP-PORTAL-DISCOVERY", "--runs-dir", req.runs_dir]
    command.append("--require-mcp" if req.require_mcp else "--no-require-mcp")
    return _start_cli(command, runs_dir=req.runs_dir)


@app.post("/api/datamaps/deep/start")
def start_datamap_deep(req: DataMapDeepStart) -> Dict[str, Any]:
    command = [sys.executable, "-u", "-m", "hip_id_agent.cli", "learn-datamaps-deep", "--config", req.config, "--input-json", req.input_json, "--customer", "HIP-DATAMAP-DEEP-DISCOVERY", "--runs-dir", req.runs_dir]
    command.append("--require-mcp" if req.require_mcp else "--no-require-mcp")
    return _start_cli(command, runs_dir=req.runs_dir)


@app.post("/api/document-types/deep/start")
def start_document_type_deep(req: DocumentTypeDeepStart) -> Dict[str, Any]:
    command = [sys.executable, "-u", "-m", "hip_id_agent.cli", "learn-doctypes-deep", "--config", req.config, "--input-json", req.input_json, "--customer", "HIP-DOCTYPE-DEEP-DISCOVERY", "--runs-dir", req.runs_dir]
    command.append("--require-mcp" if req.require_mcp else "--no-require-mcp")
    return _start_cli(command, runs_dir=req.runs_dir)


@app.post("/api/rules/deep/start")
def start_rule_deep(req: RuleDeepStart) -> Dict[str, Any]:
    command = [sys.executable, "-u", "-m", "hip_id_agent.cli", "learn-rules-deep", "--config", req.config, "--input-json", req.input_json, "--customer", "HIP-RULES-DEEP-DISCOVERY", "--runs-dir", req.runs_dir]
    command.append("--require-mcp" if req.require_mcp else "--no-require-mcp")
    return _start_cli(command, runs_dir=req.runs_dir)


@app.post("/api/transport-profiles/deep/start")
def start_transport_profile_deep(req: TransportProfileDeepStart) -> Dict[str, Any]:
    command = [sys.executable, "-u", "-m", "hip_id_agent.cli", "learn-transport-profiles-deep", "--config", req.config, "--input-json", req.input_json, "--customer", "HIP-TP-DEEP-DISCOVERY", "--runs-dir", req.runs_dir]
    command.append("--require-mcp" if req.require_mcp else "--no-require-mcp")
    return _start_cli(command, runs_dir=req.runs_dir)


@app.post("/api/bizflows/deep/start")
def start_bizflow_deep(req: BizFlowDeepStart) -> Dict[str, Any]:
    command = [sys.executable, "-u", "-m", "hip_id_agent.cli", "learn-bizflows-deep", "--config", req.config, "--input-json", req.input_json, "--customer", "HIP-BIZFLOW-DEEP-DISCOVERY", "--runs-dir", req.runs_dir]
    command.append("--require-mcp" if req.require_mcp else "--no-require-mcp")
    return _start_cli(command, runs_dir=req.runs_dir)


@app.post("/api/full-deep/start")
def start_full_deep(req: FullDeepStart) -> Dict[str, Any]:
    command = [
        sys.executable, "-u", "-m", "hip_id_agent.cli", "learn-hip-full-deep",
        "--config", req.config, "--input-json", req.input_json,
        "--customer", "HIP-FULL-DEEP-LEARNING", "--runs-dir", req.runs_dir,
    ]
    command.append("--require-mcp" if req.require_mcp else "--no-require-mcp")
    command.append("--continue-on-family-failure" if req.continue_on_family_failure else "--fail-fast-family")
    if req.resume_run:
        command.extend(["--resume-run", req.resume_run])
    return _start_cli(command, runs_dir=req.runs_dir)


@app.get("/api/full-deep/readiness")
def full_deep_readiness(config: str = "config.yaml") -> Dict[str, Any]:
    cfg = _cfg(config)
    latest = latest_certification(cfg.reporting.runs_dir)
    if latest.get("found"):
        return latest
    graph = _graph(config)
    return {"found": False, "derived_from_persistent_graph": True, "certification": HIPCapabilityCertifier(graph).certify()}


@app.get("/api/replay-profiles")
def replay_profiles(config: str = "config.yaml", page_family: str = "", verified_only: bool = False) -> Dict[str, Any]:
    graph = _graph(config)
    rows = graph.replay_profiles(page_family=page_family, verified_only=verified_only)
    return {"count": len(rows), "replay_profiles": rows}


@app.get("/api/discovery/status")
def discovery_status() -> Dict[str, Any]:
    state = _process_state()
    log_path = Path(str(state.get("log_path") or ""))
    tail = ""
    if log_path.is_file():
        try: tail = log_path.read_text(encoding="utf-8", errors="replace")[-30000:]
        except Exception: pass
    return {**state, "console_tail": tail}


@app.post("/api/discovery/stop")
def stop_discovery(force: bool = False) -> Dict[str, Any]:
    return _stop_cli(force=force)


@app.post("/api/discovery/pause")
def pause_discovery() -> Dict[str, Any]:
    return _pause_cli()


@app.post("/api/discovery/resume")
def resume_discovery() -> Dict[str, Any]:
    return _resume_cli()


@app.get("/api/capabilities/pages")
def capability_pages(config: str = "config.yaml") -> Dict[str, Any]:
    graph = _graph(config)
    return {"manifest": graph.manifest(), "pages": [graph.page_summary(name) for name in sorted(graph.data.get("pages", {}))]}


@app.get("/api/capabilities")
def capabilities(config: str = "config.yaml", page_family: str = "", text: str = "", risk: str = "") -> Dict[str, Any]:
    graph = _graph(config)
    rows = graph.query_capabilities(page_family=page_family, text=text, risk=risk)
    return {"count": len(rows), "capabilities": rows, "manifest": graph.manifest()}


@app.get("/api/capabilities/{capability_id}")
def capability(capability_id: str, config: str = "config.yaml") -> Dict[str, Any]:
    graph = _graph(config)
    row = graph.data.get("capabilities", {}).get(capability_id)
    if not isinstance(row, dict): raise HTTPException(404, "Capability not found")
    api_rows = [graph.data.get("api_contracts", {}).get(x) for x in row.get("api_contract_ids", [])]
    return {"capability": row, "api_contracts": [x for x in api_rows if isinstance(x, dict)]}


@app.get("/api/apis")
def apis(config: str = "config.yaml", page_family: str = "", method: str = "") -> Dict[str, Any]:
    graph = _graph(config); rows=[]
    for row in graph.data.get("api_contracts", {}).values():
        if page_family and page_family not in (row.get("page_families") or []): continue
        if method and str(row.get("method") or "").upper() != method.upper(): continue
        rows.append(row)
    rows.sort(key=lambda x: (str(x.get("endpoint") or ""), str(x.get("method") or "")))
    return {"count": len(rows), "api_contracts": rows, "manifest": graph.manifest()}


@app.post("/api/future-task/plan")
def future_task_plan(req: FutureTaskRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config); graph = _graph(req.config)
    return HIPFutureTaskPlanner(cfg, graph).plan(req.task)


@app.post("/api/future-task/run")
def future_task_run(req: FutureTaskRequest) -> Dict[str, Any]:
    # Execute through the CLI in a separate process so the backend event loop is
    # never used as the long-running browser owner.
    command = [sys.executable, "-u", "-m", "hip_id_agent.cli", "run-future-task", req.task, "--config", req.config, "--runs-dir", req.runs_dir]
    if req.allow_portal_mutation:
        command.append("--allow-portal-mutation")
    if req.confirmation:
        command.extend(["--confirmation", req.confirmation])
    return _start_cli(command, runs_dir=req.runs_dir)


@app.post("/api/portal-task/plan")
def universal_portal_task_plan(req: FutureTaskRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config); graph = _graph(req.config)
    if not bool(getattr(cfg.universal_operator, "enabled", True)):
        return {"pass": False, "reason": "Universal portal operator is disabled in configuration"}
    return UniversalPortalTaskPlanner(cfg, graph).plan(
        req.task, input_json=req.input_json, input_root=req.input_root,
        start_url=req.start_url, deep_learn=req.deep_learn,
    )


@app.get("/api/skills")
def induced_skill_library(
    config: str = "config.yaml", query: str = "", status: str = "", limit: int = 100,
) -> Dict[str, Any]:
    cfg = _cfg(config)
    library = _skill_library(cfg)
    return {
        "manifest": library.manifest(),
        "skills": library.list_skills(status=status, query=query, limit=max(1, min(int(limit), 500))),
    }


@app.get("/api/replay-policy")
def replay_policy_status(config: str = "config.yaml", limit: int = 100) -> Dict[str, Any]:
    cfg = _cfg(config)
    engine = replay_policy_engine_from_config(cfg)
    try:
        old_run_import = engine.ingest_old_runs(cfg.reporting.runs_dir)
    except Exception as exc:
        old_run_import = {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))}
    return {
        "manifest": engine.manifest(),
        "policies": engine.list_policies(limit=max(1, min(int(limit), 500))),
        "old_run_import": old_run_import,
        "cache_on_disk": True,
        "values_stored": False,
        "live_reproof_required": True,
    }


@app.get("/api/model-portfolio")
def model_portfolio_status(config: str = "config.yaml", probe: bool = False) -> Dict[str, Any]:
    cfg = _cfg(config)
    router = model_portfolio_from_config(cfg)
    probe_result = {}
    if probe:
        try:
            probe_result = router.probe_text_models(force=True)
        except Exception as exc:
            probe_result = {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))[:500]}
    return {
        "manifest": router.manifest(),
        "probe": probe_result,
        "catalog_on_prem_only": True,
        "learning_uses_multiple_models": bool(getattr(cfg.model_portfolio, "force_multi_model_during_learning", True)),
        "complex_tasks_use_multiple_models": bool(getattr(cfg.model_portfolio, "force_multi_model_for_complex_tasks", True)),
        "values_stored": False,
        "browser_action_authority": False,
    }


@app.get("/api/recursive-improvement")
def recursive_improvement_status(config: str = "config.yaml") -> Dict[str, Any]:
    cfg = _cfg(config)
    replay = replay_policy_engine_from_config(cfg)
    portfolio = model_portfolio_from_config(cfg)
    engine = recursive_improvement_from_config(cfg, replay_policy=replay, model_portfolio=portfolio, skill_library=_skill_library(cfg))
    return {
        "manifest": engine.manifest(),
        "bounded": True,
        "source_code_self_modification": False,
        "live_page_authority_preserved": True,
    }


@app.post("/api/portal-task/run")
def universal_portal_task_run(req: FutureTaskRequest) -> Dict[str, Any]:
    command = [
        sys.executable, "-u", "-m", "hip_id_agent.cli", "run-portal-task", req.task,
        "--config", req.config, "--runs-dir", req.runs_dir,
        "--input-json", req.input_json,
    ]
    if req.input_root:
        command.extend(["--input-root", req.input_root])
    if req.start_url:
        command.extend(["--start-url", req.start_url])
    command.append("--deep-learn" if req.deep_learn else "--no-deep-learn")
    if req.allow_portal_mutation:
        command.append("--allow-portal-mutation")
    if req.confirmation:
        command.extend(["--confirmation", req.confirmation])
    return _start_cli(command, runs_dir=req.runs_dir)


@app.post("/api/certified-task/plan")
def certified_task_plan(req: FutureTaskRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config); graph = _graph(req.config)
    return CertifiedHIPFutureTaskPlanner(cfg, graph).plan(req.task, allow_adaptive_exploration=req.allow_adaptive_exploration)


@app.post("/api/certified-task/run")
def certified_task_run(req: FutureTaskRequest) -> Dict[str, Any]:
    command = [sys.executable, "-u", "-m", "hip_id_agent.cli", "run-certified-task", req.task, "--config", req.config, "--runs-dir", req.runs_dir]
    command.append("--adaptive" if req.allow_adaptive_exploration else "--no-adaptive")
    if req.allow_portal_mutation:
        command.append("--allow-portal-mutation")
    if req.confirmation:
        command.extend(["--confirmation", req.confirmation])
    return _start_cli(command, runs_dir=req.runs_dir)


@app.get("/api/certified-task/replays")
def certified_task_replays(config: str = "config.yaml", verified_only: bool = True) -> Dict[str, Any]:
    graph = _graph(config)
    rows = graph.task_replay_profiles(verified_only=verified_only)
    return {"count": len(rows), "task_replay_profiles": rows, "manifest": graph.manifest()}


@app.post("/api/governed-change/preview")
def governed_change_preview(req: FutureTaskRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config); graph = _graph(req.config)
    plan = CertifiedHIPFutureTaskPlanner(cfg, graph).plan(req.task, allow_adaptive_exploration=req.allow_adaptive_exploration)
    preview = HIPChangeGovernance(cfg, graph).preview(
        task=req.task, plan=plan, operator_role=req.operator_role, approval_id=req.approval_id,
        force_repeat_mutation=req.force_repeat_mutation,
    )
    return {"plan": plan, "change_preview": preview}


@app.post("/api/governed-change/run")
def governed_change_run(req: FutureTaskRequest) -> Dict[str, Any]:
    command = [sys.executable, "-u", "-m", "hip_id_agent.cli", "run-governed-change", req.task, "--config", req.config, "--runs-dir", req.runs_dir]
    command.append("--adaptive" if req.allow_adaptive_exploration else "--no-adaptive")
    if req.operator_role:
        command.extend(["--operator-role", req.operator_role])
    if req.approval_id:
        command.extend(["--approval-id", req.approval_id])
    if req.allow_portal_mutation:
        command.append("--allow-portal-mutation")
    if req.confirmation:
        command.extend(["--confirmation", req.confirmation])
    if req.force_repeat_mutation:
        command.append("--force-repeat-mutation")
    return _start_cli(command, runs_dir=req.runs_dir)


@app.get("/api/governed-change/audit")
def governed_change_audit(config: str = "config.yaml", limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    cfg = _cfg(config); graph = _graph(config)
    return HIPChangeGovernance(cfg, graph).ledger.status(limit=limit)


@app.get("/api/mission/trace")
def mission_trace(config: str = "config.yaml", runs_dir: str = "", run_id: str = "") -> Dict[str, Any]:
    run_dir = _resolve_trace_run_dir(config=config, runs_dir=runs_dir, run_id=run_id)
    if run_dir is None:
        return {"found": False, "run_id": run_id or "", "trace": {}, "path": ""}
    trace = read_mission_trace(run_dir)
    return {
        "found": bool(trace),
        "run_id": str(trace.get("run_id") or run_dir.name),
        "path": str(run_dir / "mission_trace.json"),
        "trace": trace,
    }


@app.get("/api/mission/live-view")
def mission_live_view(config: str = "config.yaml", runs_dir: str = "", run_id: str = "") -> Dict[str, Any]:
    run_dir = _resolve_trace_run_dir(config=config, runs_dir=runs_dir, run_id=run_id)
    if run_dir is None:
        return {"found": False, "run_id": run_id or "", "live_view": {}, "screenshot_url": ""}
    live_view = read_agent_live_view(run_dir)
    current = dict(live_view.get("current") or {}) if isinstance(live_view, dict) else {}
    screenshot_rel = str(current.get("screenshot_relative_path") or "")
    screenshot_url = ""
    if screenshot_rel:
        screenshot_url = "/api/mission/live-view/screenshot?" + urlencode({
            "config": config,
            "runs_dir": runs_dir,
            "run_id": run_dir.name,
            "path": screenshot_rel,
        })
    return {
        "found": bool(live_view),
        "run_id": str((live_view or {}).get("run_id") or run_dir.name),
        "path": str(run_dir / "agent_live_view.json"),
        "live_view": live_view,
        "screenshot_url": screenshot_url,
    }


@app.get("/api/mission/world-model")
def mission_world_model(config: str = "config.yaml", phase: str = "") -> Dict[str, Any]:
    cfg = _cfg(config)
    root = Path(cfg.reporting.memory_dir)
    if not root.is_absolute():
        root = (ROOT / root).resolve()
    brain_cfg = getattr(cfg, "brain", None)
    brain_dir = str(getattr(brain_cfg, "directory", "portal_brain") or "portal_brain")
    model = WebsiteWorldModelMemory(
        root / brain_dir / "website_world_model",
        config=brain_cfg,
    )
    return {
        "found": True,
        "phase": phase,
        "root": str(model.root),
        "summary": model.summary(phase=phase),
        "recommendations": model.recommend_actions(phase=phase or "standalone", limit=20) if phase else [],
    }


@app.get("/api/mission/live-view/screenshot")
def mission_live_view_screenshot(
    config: str = "config.yaml", runs_dir: str = "", run_id: str = "", path: str = ""
):
    run_dir = _resolve_trace_run_dir(config=config, runs_dir=runs_dir, run_id=run_id)
    if run_dir is None:
        raise HTTPException(status_code=404, detail="Run not found")
    rel = Path(str(path or ""))
    if rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(status_code=400, detail="Invalid screenshot path")
    target = (run_dir / rel).resolve()
    try:
        target.relative_to(run_dir.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Screenshot path escapes run directory")
    if not target.is_file() or target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(target), headers={"Cache-Control": "no-store"})




@app.get("/api/human-assistance")
def human_assistance(config: str = "config.yaml", run_id: str = "") -> Dict[str, Any]:
    cfg = _cfg(config)
    store = human_teaching_from_config(cfg)
    return {"pending": store.pending(run_id=run_id), "manifest": store.manifest()}


@app.post("/api/human-assistance/teach")
def human_assistance_teach(req: HumanTeachingRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config)
    store = human_teaching_from_config(cfg)
    try:
        row = store.submit(
            request_id=req.request_id, input_path=req.input_path,
            semantic_control_id=req.semantic_control_id, control_label=req.control_label,
            section=req.section, role=req.role, note=req.note, verified_by_human=req.verified_by_human,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "recorded", "teaching": row, "manifest": store.manifest()}


@app.get("/api/human-phase-review")
def human_phase_review(config: str = "config.yaml", run_id: str = "") -> Dict[str, Any]:
    cfg = _cfg(config)
    store = human_phase_review_from_config(cfg)
    return {"pending": store.pending(run_id=run_id), "manifest": store.manifest()}


@app.post("/api/human-phase-review/resolve")
def human_phase_review_resolve(req: HumanPhaseReviewRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config)
    store = human_phase_review_from_config(cfg)
    try:
        row = store.resolve(request_id=req.request_id, verdict=req.verdict, note=req.note, reviewer=req.reviewer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "recorded", "review": row, "manifest": store.manifest()}


@app.get("/api/interactive-teaching")
def interactive_teaching_status(config: str = "config.yaml", run_id: str = "", phase: str = "") -> Dict[str, Any]:
    cfg = _cfg(config)
    store = interactive_teaching_from_config(cfg)
    return {"active": store.active(run_id=run_id, phase=phase), "manifest": store.manifest()}


@app.post("/api/interactive-teaching/start")
def interactive_teaching_start(req: InteractiveTeachingStartRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config)
    store = interactive_teaching_from_config(cfg)
    row = store.start(run_id=req.run_id, phase=req.phase, task=req.task, note=req.note)
    return {"status": "recording", "session": row, "manifest": store.manifest()}


@app.post("/api/interactive-teaching/finish")
def interactive_teaching_finish(req: InteractiveTeachingFinishRequest) -> Dict[str, Any]:
    cfg = _cfg(req.config)
    store = interactive_teaching_from_config(cfg)
    try:
        row = store.finish(session_id=req.session_id, note=req.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "capture_requested", "session": row, "manifest": store.manifest()}


@app.get("/api/deterministic-recipes")
def deterministic_recipes(config: str = "config.yaml") -> Dict[str, Any]:
    cfg = _cfg(config)
    lib = deterministic_recipe_from_config(cfg)
    return lib.manifest()

@app.get("/api/runs")
def runs(config: str = "config.yaml", runs_dir: str = "", limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    cfg = _cfg(config)
    root = Path(runs_dir) if str(runs_dir or "").strip() else Path(cfg.reporting.runs_dir)
    if not root.is_absolute():
        root = (ROOT / root).resolve()
    rows=[]
    if root.is_dir():
        for path in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            summary = _read_json(path / "production_final_summary.json", None) or _read_json(path / "full_hip_deep_learning_summary.json", None) or _read_json(path / "bizflow_deep_discovery_summary.json", None) or _read_json(path / "transport_profile_deep_discovery_summary.json", None) or _read_json(path / "rules_deep_discovery_summary.json", None) or _read_json(path / "doctype_deep_discovery_summary.json", None) or _read_json(path / "datamap_deep_discovery_summary.json", None) or _read_json(path / "portal_discovery_summary.json", None) or _read_json(path / "future_task_execution.json", None)
            rows.append({
                "run_id": path.name, "path": str(path),
                "has_full_deep": (path / "full_hip_deep_learning_summary.json").is_file(),
                "has_bizflow_deep": (path / "bizflow_deep_discovery_summary.json").is_file(),
                "has_transport_profile_deep": (path / "transport_profile_deep_discovery_summary.json").is_file(),
                "has_doctype_deep": (path / "doctype_deep_discovery_summary.json").is_file(),
                "has_rules_deep": (path / "rules_deep_discovery_summary.json").is_file(),
                "has_datamap_deep": (path / "datamap_deep_discovery_summary.json").is_file(),
                "has_discovery_summary": (path / "portal_discovery_summary.json").is_file(),
                "has_future_task": (path / "future_task_execution.json").is_file(),
                "has_certified_future_task": (path / "certified_future_task_execution.json").is_file(),
                "has_governed_change": (path / "governed_change_execution.json").is_file(),
                "has_production_e2e": (path / "production_final_summary.json").is_file(),
                "has_mission_trace": (path / "mission_trace.json").is_file(),
                "mission_trace_status": (_read_json(path / "mission_trace.json", {}) or {}).get("status", "") if (path / "mission_trace.json").is_file() else "",
                "summary": summary if isinstance(summary, dict) else {},
            })
    return {"count": len(rows), "runs": rows}


# Production convenience: serve the JavaScript Control Center from the same
# FastAPI process. API routes are registered above this catch-all static mount,
# so /api/* and /health keep their normal behavior while / opens webui/index.html.
_WEBUI_CANDIDATES = [ROOT / "webui", Path(__file__).resolve().parent / "webui"]
_WEBUI_DIR = next((p for p in _WEBUI_CANDIDATES if p.is_dir() and (p / "index.html").is_file()), None)
if _WEBUI_DIR is not None:
    app.mount("/", StaticFiles(directory=str(_WEBUI_DIR), html=True), name="webui")
