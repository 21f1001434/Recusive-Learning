from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .safe_io import safe_mkdir, safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


def readiness_fingerprint(
    *,
    config: str,
    input_json: str,
    runs_dir: str,
    golden_screenshot_dir: str,
    upload_assets_dir: str,
    phases: Iterable[str],
    api_mode: str = "capture",
    execution_profile: str = "standard",
) -> str:
    """Stable fingerprint for the exact mission configuration covered by a live receipt."""
    payload = {
        "config": str(config or ""),
        "input_json": str(input_json or ""),
        "runs_dir": str(runs_dir or ""),
        "golden_screenshot_dir": str(golden_screenshot_dir or ""),
        "upload_assets_dir": str(upload_assets_dir or ""),
        "phases": list(phases or []),
        "api_mode": str(api_mode or "capture").strip().lower(),
        "execution_profile": str(execution_profile or "standard").strip().lower(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _known_browser_paths() -> Dict[str, List[str]]:
    paths: Dict[str, List[str]] = {"edge": [], "chrome": []}
    if os.name == "nt":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pfx86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        paths["edge"] = [
            str(Path(pfx86) / "Microsoft/Edge/Application/msedge.exe"),
            str(Path(pf) / "Microsoft/Edge/Application/msedge.exe"),
            str(Path(local) / "Microsoft/Edge/Application/msedge.exe") if local else "",
        ]
        paths["chrome"] = [
            str(Path(pf) / "Google/Chrome/Application/chrome.exe"),
            str(Path(pfx86) / "Google/Chrome/Application/chrome.exe"),
            str(Path(local) / "Google/Chrome/Application/chrome.exe") if local else "",
        ]
    else:
        paths["edge"] = [p for p in [shutil.which("microsoft-edge"), shutil.which("microsoft-edge-stable"), shutil.which("msedge")] if p]
        paths["chrome"] = [p for p in [shutil.which("google-chrome"), shutil.which("google-chrome-stable"), shutil.which("chrome")] if p]
    return {k: [x for x in v if x] for k, v in paths.items()}


def _browser_launch_plan(portal: Any) -> List[tuple[str, Dict[str, str], bool]]:
    """Return readiness launch order using the same Chrome-first startup policy as BrowserSession."""
    explicit_edge = str(getattr(portal, "edge_executable_path", "") or "").strip()
    explicit_chrome = str(getattr(portal, "chrome_executable_path", "") or "").strip()
    configured = str(getattr(portal, "chromium_channel", "") or "").strip().lower()
    allow_fallback = bool(getattr(portal, "allow_browser_fallback", True))
    rows: List[tuple[str, Dict[str, str], bool]] = []

    def add(name: str, kwargs: Dict[str, str], allowed: bool = True) -> None:
        if name not in [x[0] for x in rows]:
            rows.append((name, kwargs, bool(allowed)))

    if configured == "chrome":
        add("chrome", {"executable_path": explicit_chrome} if explicit_chrome else {"channel": "chrome"})
    elif configured == "msedge":
        add("edge", {"executable_path": explicit_edge} if explicit_edge else {"channel": "msedge"})
    elif configured:
        add(configured, {"channel": configured})
    elif explicit_chrome:
        add("chrome", {"executable_path": explicit_chrome})
    elif explicit_edge:
        add("edge", {"executable_path": explicit_edge})
    else:
        add("playwright_chromium", {})

    if allow_fallback:
        if configured != "chrome":
            add("chrome", {"executable_path": explicit_chrome} if explicit_chrome else {"channel": "chrome"}, bool(getattr(portal, "fallback_to_chrome", True)))
        if configured != "msedge":
            add("edge", {"executable_path": explicit_edge} if explicit_edge else {"channel": "msedge"}, bool(getattr(portal, "fallback_to_edge", True)))
        add("playwright_chromium", {}, bool(getattr(portal, "fallback_to_playwright_chromium", True)))
    return rows


def browser_candidate_inventory(config: Any) -> Dict[str, Any]:
    """Non-invasive browser inventory ordered exactly like the managed HIP session."""
    portal = config.portal
    known = _known_browser_paths()
    explicit_edge = str(getattr(portal, "edge_executable_path", "") or "").strip()
    explicit_chrome = str(getattr(portal, "chrome_executable_path", "") or "").strip()
    edge_path = explicit_edge if explicit_edge and Path(explicit_edge).is_file() else next((p for p in known["edge"] if Path(p).is_file()), "")
    chrome_path = explicit_chrome if explicit_chrome and Path(explicit_chrome).is_file() else next((p for p in known["chrome"] if Path(p).is_file()), "")
    playwright_installed = importlib.util.find_spec("playwright") is not None
    plan = _browser_launch_plan(portal)
    detected = {"edge": bool(edge_path), "chrome": bool(chrome_path), "playwright_chromium": playwright_installed}
    executable = {"edge": edge_path, "chrome": chrome_path, "playwright_chromium": "managed-by-playwright"}
    candidates = [
        {"name": name, "configured": bool(allowed), "executable": executable.get(name, kwargs.get("executable_path") or kwargs.get("channel") or ""), "detected": bool(detected.get(name, True))}
        for name, kwargs, allowed in plan
    ]
    available = any(row["configured"] and row["detected"] for row in candidates)
    order = [name for name, _kwargs, allowed in plan if allowed]
    return {
        "pass": available,
        "available": available,
        "playwright_python_installed": playwright_installed,
        "candidate_order": order,
        "primary": order[0] if order else "",
        "candidates": candidates,
        "policy": " -> ".join(order) + "; startup fallback only; selected browser locked after Dell SSO",
    }


async def probe_browser_launch(config: Any) -> Dict[str, Any]:
    """Launch the configured browser order headlessly without touching HIP/SSO profiles."""
    inventory = browser_candidate_inventory(config)
    if not inventory.get("playwright_python_installed"):
        return {**inventory, "pass": False, "available": False, "error": "Python Playwright is not installed."}
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover
        return {**inventory, "pass": False, "available": False, "error": mask_sensitive_string(str(exc))}

    attempts: List[Dict[str, Any]] = []
    async with async_playwright() as p:
        for name, kwargs, allowed in _browser_launch_plan(config.portal):
            if not allowed:
                continue
            browser = None
            started = time.perf_counter()
            try:
                browser = await p.chromium.launch(headless=True, **kwargs)
                attempts.append({"browser": name, "pass": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)})
                return {**inventory, "pass": True, "available": True, "selected": name, "attempts": attempts}
            except Exception as exc:
                attempts.append({"browser": name, "pass": False, "error": mask_sensitive_string(str(exc))[:500]})
            finally:
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass
    return {**inventory, "pass": False, "available": False, "attempts": attempts, "error": "No configured browser candidate could be launched."}


def probe_runs_path(runs_dir: str | Path) -> Dict[str, Any]:
    """Prove the evidence root is writable using the same safe-I/O primitives as missions."""
    root = Path(runs_dir).expanduser()
    probe_dir = root / ".hip_live_readiness"
    probe_file = probe_dir / "write_probe.json"
    try:
        safe_mkdir(probe_dir, parents=True, exist_ok=True)
        payload = {"probe": "HIP_READINESS_WRITE_OK", "created_ns": time.time_ns()}
        safe_write_json(probe_file, payload, mask=False)
        loaded = json.loads(probe_file.read_text(encoding="utf-8"))
        ok = loaded.get("probe") == payload["probe"]
        try:
            probe_file.unlink(missing_ok=True)
            probe_dir.rmdir()
        except Exception:
            pass
        return {"pass": bool(ok), "writable": bool(ok), "path": str(root), "safe_io": True}
    except Exception as exc:
        return {"pass": False, "writable": False, "path": str(root), "safe_io": True, "error": mask_sensitive_string(str(exc))[:700]}


def build_live_readiness_report(
    *,
    fingerprint: str,
    static_preflight: Dict[str, Any],
    browser_probe: Dict[str, Any],
    mcp_probe: Dict[str, Any],
    text_probe: Dict[str, Any],
    vision_probe: Dict[str, Any],
    path_probe: Dict[str, Any],
    process_state: Dict[str, Any],
    config: Any,
    runtime_certificate_probe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Converge all pre-session requirements into one fail-closed GO/NO-GO decision."""
    checks: List[Dict[str, Any]] = []

    def add(check_id: str, label: str, passed: bool, *, blocker: bool = True, detail: str = "", evidence: Any = None) -> None:
        checks.append({
            "id": check_id,
            "label": label,
            "pass": bool(passed),
            "severity": "blocker" if blocker else "warning",
            "detail": str(detail or ""),
            "evidence": mask_sensitive_data(evidence) if evidence is not None else None,
        })

    skill_ok = bool((static_preflight.get("skill_vetting") or {}).get("pass"))
    autogen_ok = bool((static_preflight.get("autogen") or {}).get("pass"))
    add("static_preflight", "Input / selected phase contract", bool(static_preflight.get("pass")), evidence={"issues": static_preflight.get("issues") or []})
    add("expert_skills", "Deterministic expert-skill vetting", skill_ok)
    add("autogen", "Microsoft AutoGen 0.7.5 runtime", autogen_ok, evidence=static_preflight.get("autogen") or {})
    add("autowebglm_primary", "AutoWebGLM primary planning policy", bool(config.autowebglm.enabled and config.autowebglm.primary_framework), detail="AutoWebGLM must remain the primary decision framework.")
    strict_mcp_runtime = bool(getattr(config.mcp, "strict_runtime_required", True))
    playwright_policy_ok = bool(
        config.mcp.use_playwright_mcp
        and config.mcp.playwright_mcp_primary_for_safe_actions
        and getattr(config.mcp, "playwright_mcp_find_first", True)
        and getattr(config.mcp, "playwright_mcp_fill_form_enabled", True)
    )
    add(
        "playwright_mcp_policy",
        "Playwright MCP deterministic fallback + DOM verifier",
        playwright_policy_ok,
        blocker=strict_mcp_runtime,
        detail=(
            "Adaptive mode uses Playwright MCP whenever healthy but does not block if the governed Python Playwright executor is available. "
            "Strict MCP mode requires Playwright MCP. Governed order: AutoWebGLM -> semantic target proof -> PyAutoGUI MCP when healthy -> Playwright MCP -> Python Playwright -> effect proof."
        ),
    )
    semantic_cfg = getattr(config, "semantic_understanding", None)
    strict_external_evidence = bool(getattr(semantic_cfg, "strict_external_evidence", True)) if semantic_cfg is not None else False
    semantic_core_ok = bool(
        semantic_cfg is not None
        and getattr(semantic_cfg, "enabled", False)
        and getattr(semantic_cfg, "fail_closed", False)
        and getattr(semantic_cfg, "revalidate_before_dispatch", False)
        and getattr(semantic_cfg, "require_post_action_effect", False)
        and float(getattr(semantic_cfg, "execute_confidence_threshold", 0.90)) >= 0.90
        and float(getattr(semantic_cfg, "reobserve_confidence_threshold", 0.75)) >= 0.75
        and float(getattr(semantic_cfg, "self_heal_confidence_threshold", 0.55)) >= 0.55
    )
    semantic_external_policy_ok = bool(
        semantic_cfg is not None
        and getattr(semantic_cfg, "require_playwright_mcp_evidence", False)
        and getattr(semantic_cfg, "require_devtools_evidence", False)
        and getattr(semantic_cfg, "require_hip_intelligence_mcp_evidence", False)
        and getattr(semantic_cfg, "use_hip_intelligence_mcp_consensus", False)
    )
    semantic_policy_ok = semantic_core_ok and (semantic_external_policy_ok if strict_external_evidence else True)
    add(
        "semantic_action_gate_policy", "Layer 11 semantic website-understanding gate", semantic_policy_ok,
        detail=(
            "Every operational click/fill/select must prove a unique current-generation semantic target, revalidate after rerender, and verify its effect. "
            + ("Strict mode additionally requires all configured external MCP witnesses." if strict_external_evidence else "Adaptive mode uses external MCP witnesses when healthy but keeps current DOM/effect proof authoritative.")
        )
    )
    add("browser_launch", "Supported browser launch", bool(browser_probe.get("pass")), evidence=browser_probe)
    pw = (mcp_probe.get("playwright_mcp") or {}) if isinstance(mcp_probe, dict) else {}
    cdp = (mcp_probe.get("chrome_devtools_mcp") or {}) if isinstance(mcp_probe, dict) else {}
    hip = (mcp_probe.get("hip_intelligence_mcp") or {}) if isinstance(mcp_probe, dict) else {}
    pyauto = (mcp_probe.get("pyautogui_mcp") or {}) if isinstance(mcp_probe, dict) else {}
    pw_status = pw.get("required_tool_status") if isinstance(pw.get("required_tool_status"), dict) else {}
    cdp_status = cdp.get("required_tool_status") if isinstance(cdp.get("required_tool_status"), dict) else {}
    pw_required = ["browser_navigate", "browser_snapshot", "browser_find", "browser_click", "browser_type", "browser_fill_form", "browser_select_option", "browser_take_screenshot"]
    cdp_required = ["take_snapshot", "list_network_requests", "list_console_messages"]
    pw_tools_ok = bool(pw.get("available")) and bool(pw_status) and all(bool(pw_status.get(name)) for name in pw_required)
    cdp_tools_ok = bool(cdp.get("available")) and bool(cdp_status) and all(bool(cdp_status.get(name)) for name in cdp_required)
    add(
        "playwright_mcp_live",
        "Official Playwright MCP server + semantic action tools",
        pw_tools_ok,
        blocker=strict_mcp_runtime,
        detail="Adaptive witness/executor when healthy; strict MCP mode requires navigate, snapshot, browser_find, click, type, browser_fill_form, select-option and screenshot.",
        evidence=pw,
    )
    add(
        "chrome_devtools_mcp_live",
        "Chrome DevTools MCP independent witness",
        cdp_tools_ok,
        blocker=strict_mcp_runtime,
        detail="Adaptive independent DOM/network/console witness when healthy; strict MCP mode requires it.",
        evidence=cdp,
    )
    hip_required = [
        "build_web_representation", "plan_form_action",
        "resolve_semantic_control", "rank_semantic_candidates",
        "verify_semantic_action_effect", "get_semantic_control_fingerprint",
        "get_semantic_control_capabilities",
        "hip_get_current_surface", "hip_get_form_schema", "hip_find_control",
        "hip_find_owned_popup", "hip_get_repeatable_rows", "hip_get_required_fields",
        "hip_get_current_values", "hip_compare_expected_actual", "hip_get_safe_actions",
        "hip_verify_action_effect", "hip_get_route_identity", "hip_get_form_generation",
    ]
    hip_status = hip.get("required_tool_status") if isinstance(hip.get("required_tool_status"), dict) else {}
    hip_tools_ok = bool(hip.get("available")) and all(bool(hip_status.get(name)) for name in hip_required)
    hip_runtime_required = bool(strict_mcp_runtime or getattr(config.mcp, "hip_intelligence_mcp_required", False))
    add(
        "hip_intelligence_mcp_live", "HIP Intelligence MCP semantic website brain", hip_tools_ok,
        blocker=hip_runtime_required,
        detail=(
            "Adaptive semantic witness/accelerator when healthy. Strict MCP mode requires semantic resolve/rank/fingerprint/effect verification plus HIP surface/schema/control/popup/rows/required/current/compare/safe-actions/route/generation intelligence."
        ),
        evidence=hip,
    )
    py_cfg = getattr(config, "pyautogui", None)
    py_enabled = bool(py_cfg is not None and getattr(py_cfg, "enabled", True) and getattr(py_cfg, "mcp_enabled", True))
    py_required = bool(py_cfg is not None and getattr(py_cfg, "mcp_required", False))
    py_status = pyauto.get("required_tool_status") if isinstance(pyauto.get("required_tool_status"), dict) else {}
    py_required_tools = [
        "pyautogui_size", "pyautogui_position", "pyautogui_click",
        "pyautogui_write", "pyautogui_press", "pyautogui_hotkey", "pyautogui_screenshot",
    ]
    py_tools_ok = bool(pyauto.get("available")) and bool(py_status) and all(bool(py_status.get(name)) for name in py_required_tools)
    if py_enabled:
        add(
            "pyautogui_mcp_live", "PyAutoGUI MCP primary visible-desktop executor", py_tools_ok,
            blocker=py_required,
            detail="Primary click/type/key executor when healthy. Playwright MCP/Python Playwright can continue if it is unavailable unless the strict PyAutoGUI profile is selected.",
            evidence=pyauto,
        )
    python_playwright_ok = bool(browser_probe.get("pass"))
    executor_quorum_ok = bool(py_tools_ok or pw_tools_ok or python_playwright_ok)
    add(
        "hybrid_executor_quorum",
        "At least one governed interaction executor is available",
        executor_quorum_ok,
        blocker=True,
        detail="GO requires at least one of PyAutoGUI MCP, Playwright MCP, or Python Playwright. Missing optional witness channels become warnings in adaptive mode.",
        evidence={"pyautogui_mcp": py_tools_ok, "playwright_mcp": pw_tools_ok, "python_playwright": python_playwright_ok},
    )
    add("text_model", "Dell AIA text model live probe", bool(text_probe.get("pass")), evidence=text_probe)
    add("vision_model", "Dell AIA vision model live image-understanding probe", bool(vision_probe.get("pass")), evidence=vision_probe)
    add("runs_path", "Runs / evidence directory writable", bool(path_probe.get("pass")), evidence=path_probe)
    live_cert_cfg = getattr(config, "live_runtime_certification", None)
    live_cert_enabled = bool(live_cert_cfg is not None and getattr(live_cert_cfg, "enabled", True))
    live_cert_required = bool(live_cert_cfg is not None and getattr(live_cert_cfg, "require_for_live_go_no_go", False))
    cert_probe = runtime_certificate_probe or {"pass": False, "status": "not_checked", "reason": "No live runtime certificate probe supplied."}
    if live_cert_enabled:
        add(
            "live_runtime_certificate",
            "Recent Windows live-runtime certification",
            bool(cert_probe.get("pass")),
            blocker=live_cert_required,
            detail=(
                "Proves the headed browser + Dell SSO + Dell AIA + hybrid executor quorum against the actual workstation; optional MCP witnesses are recorded as warnings in adaptive mode and required in strict MCP mode. "
                + ("An invalid/stale certificate was auto-refreshed during this GO/NO-GO run." if cert_probe.get("auto_refresh_attempted") and cert_probe.get("pass") else "")
            ).strip(),
            evidence=cert_probe,
        )
    add("mission_idle", "No conflicting mission process", not bool(process_state.get("running")), evidence={"running": bool(process_state.get("running")), "pid": process_state.get("pid")})
    add("browser_use", "Browser-Use same-CDP recovery perception", bool(getattr(config.browser_use, "enabled", False)), blocker=False, detail="Optional perception/recovery layer. PyAutoGUI MCP remains the primary physical interaction engine; Playwright MCP remains deterministic fallback/verification.")

    blockers = [row for row in checks if row["severity"] == "blocker" and not row["pass"]]
    warnings = [row for row in checks if row["severity"] == "warning" and not row["pass"]]
    return {
        "schema_version": "hip.live-readiness.v1",
        "pass": not blockers,
        "decision": "GO" if not blockers else "NO_GO",
        "fingerprint": fingerprint,
        "checks": checks,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": [{"id": x["id"], "label": x["label"], "detail": x["detail"]} for x in blockers],
        "warnings": [{"id": x["id"], "label": x["label"], "detail": x["detail"]} for x in warnings],
        "execution_contract": "AutoWebGLM planner -> current-generation semantic target proof -> PyAutoGUI MCP primary physical click/type/key when healthy -> Playwright MCP deterministic fallback -> Python Playwright governed fallback -> optional HIP Intelligence/DevTools/Browser-Use/Gemma witnesses -> exact post-action verification; strict MCP profile requires the complete witness stack",
        "runtime_mode": "strict_mcp" if strict_mcp_runtime else "adaptive_hybrid",
        "sso_gate": "Dell SSO is validated after the selected browser opens; the browser is then locked for the mission.",
    }
