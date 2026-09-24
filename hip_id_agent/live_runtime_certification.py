from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import __version__
from .aia_client import AIAClient
from .browser_session import BrowserSession
from .config import AppConfig
from .hip_intelligence_mcp import HIPIntelligenceMCPBackend
from .safe_io import safe_mkdir, safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .semantic_control import capture_semantic_state
from .vision_runtime import VisionRuntimeBridge

CERT_SCHEMA = "hip.live-runtime-certification.v1"
LATEST_CERT_RELATIVE = Path(".hip_runtime") / "live_runtime_certificate.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_environment_fingerprint(config: AppConfig) -> str:
    """Fingerprint only the non-secret runtime settings proved by a live certificate."""
    payload = {
        "package_version": str(__version__),
        "portal": {
            "base_url": str(config.portal.base_url or ""),
            "environment": str(config.portal.environment or ""),
            "browser_user_data_dir": str(config.portal.browser_user_data_dir or ""),
            "chromium_channel": str(config.portal.chromium_channel or ""),
            "edge_executable_path": str(config.portal.edge_executable_path or ""),
            "chrome_executable_path": str(config.portal.chrome_executable_path or ""),
        },
        "mcp": {
            "browser_backend": str(config.mcp.browser_backend),
            "playwright_mcp_args": list(config.mcp.playwright_mcp_args or []),
            "chrome_devtools_args": list(config.mcp.chrome_devtools_args or []),
            "use_playwright_mcp": bool(config.mcp.use_playwright_mcp),
            "use_chrome_devtools_mcp": bool(config.mcp.use_chrome_devtools_mcp),
            "use_hip_intelligence_mcp": bool(config.mcp.use_hip_intelligence_mcp),
            "strict_runtime_required": bool(getattr(config.mcp, "strict_runtime_required", False)),
        },
        "pyautogui": {
            "enabled": bool(config.pyautogui.enabled),
            "mcp_enabled": bool(config.pyautogui.mcp_enabled),
            "mcp_prefix": str(config.pyautogui.mcp_prefix or ""),
            "mcp_args": list(config.pyautogui.mcp_args or []),
            "windows_only": bool(config.pyautogui.windows_only),
        },
        "aia": {
            "model": str(getattr(config.aia, "model", "") or ""),
            "vision_model": str(getattr(config.aia, "vision_model", "") or ""),
            "base_url_env_var": str(getattr(config.aia, "base_url_env_var", "") or ""),
            "auth_mode": str(getattr(config.aia, "auth_mode", "") or ""),
        },
        "semantic": {
            "enabled": bool(config.semantic_understanding.enabled),
            "fail_closed": bool(config.semantic_understanding.fail_closed),
            "require_playwright_mcp_evidence": bool(config.semantic_understanding.require_playwright_mcp_evidence),
            "require_devtools_evidence": bool(config.semantic_understanding.require_devtools_evidence),
            "require_hip_intelligence_mcp_evidence": bool(config.semantic_understanding.require_hip_intelligence_mcp_evidence),
            "strict_external_evidence": bool(getattr(config.semantic_understanding, "strict_external_evidence", False)),
        },
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check(check_id: str, label: str, passed: bool, *, detail: str = "", evidence: Any = None, blocker: bool = True) -> Dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "pass": bool(passed),
        "severity": "blocker" if blocker else "warning",
        "detail": str(detail or ""),
        "evidence": mask_sensitive_data(evidence) if evidence is not None else None,
    }


def _is_windows_interactive() -> Dict[str, Any]:
    is_windows = platform.system().lower() == "windows"
    # These variables are advisory; a healthy headed Playwright + PyAutoGUI MCP probe
    # below is the authoritative proof that a usable desktop exists.
    session_name = str(os.getenv("SESSIONNAME") or "")
    user_name = str(os.getenv("USERNAME") or os.getenv("USER") or "")
    return {
        "pass": is_windows,
        "platform": platform.system(),
        "session_name": session_name,
        "user_present": bool(user_name),
        "reason": "" if is_windows else "Live desktop certification requires Windows because PyAutoGUI MCP is Windows-only in this HIP profile.",
    }


async def _probe_text_model(config: AppConfig) -> Dict[str, Any]:
    client = AIAClient(config.aia)
    started = time.perf_counter()
    try:
        response = client.chat_rest(
            [
                {"role": "system", "content": "You are an availability probe. Follow the response contract exactly."},
                {"role": "user", "content": "Return exactly HIP_LIVE_TEXT_OK and nothing else."},
            ],
            temperature=0.0,
            max_tokens=None,
        )
        text = str(response or "").strip()
        marker_seen = "HIP_LIVE_TEXT_OK" in text
        available = bool(text)
        return {
            "pass": available,
            "kind": "text",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "expected_marker_seen": marker_seen,
            "capability_verified": available,
            "response_chars": len(text),
            "warning": "" if marker_seen else (
                "Dell AIA returned a non-empty response but did not echo the synthetic marker exactly; task-specific HIP judges remain fail-closed."
                if available else ""
            ),
            "error": "" if available else "Dell AIA text model returned an empty response.",
        }
    except Exception as exc:
        return {
            "pass": False,
            "kind": "text",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": mask_sensitive_string(str(exc))[:1000],
        }


async def _probe_pyautogui_mcp_readonly(py: Any, capabilities: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run the non-mutating desktop smoke with bounded screenshot evidence."""
    evidence: Dict[str, Any] = {"capabilities": capabilities or {}}
    if py is None:
        return {"pass": False, "detail": "PyAutoGUI MCP backend is unavailable.", "evidence": evidence}
    try:
        width, height = await py.size()
        pos_x, pos_y = await py.position()
        region = [0, 0, max(1, min(128, int(width))), max(1, min(128, int(height)))]
        try:
            shot = await py.screenshot(region=region)
        except TypeError:
            # Compatibility with older/test MCP wrappers without a region argument.
            shot = await py.screenshot()
        evidence.update({
            "screen_size": [int(width), int(height)],
            "cursor_position": [int(pos_x), int(pos_y)],
            "screenshot_region": region,
            "screenshot_probe_returned": shot is not None,
        })
        passed = int(width) > 0 and int(height) > 0 and shot is not None
        return {
            "pass": passed,
            "detail": "No coordinate click/key is issued during certification." if passed else "PyAutoGUI MCP returned incomplete read-only desktop evidence.",
            "evidence": evidence,
        }
    except Exception as exc:
        error = mask_sensitive_string(str(exc))[:1000]
        evidence["error"] = error
        return {
            "pass": False,
            "detail": f"Read-only PyAutoGUI MCP probe failed: {error[:500]}",
            "evidence": evidence,
        }


async def _probe_vision_model(config: AppConfig) -> Dict[str, Any]:
    bridge = VisionRuntimeBridge(config.vision_runtime, aia_config=config.aia)
    started = time.perf_counter()
    try:
        result = await bridge.preflight(force=True)
        ok = bool(result.get("pass")) and str(result.get("status") or "") == "ok"
        return {
            "pass": ok,
            "kind": "vision",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "model": result.get("model") or bridge.status().get("selected_model") or "",
            "image_understanding_verified": ok,
            "error": "" if ok else mask_sensitive_string(str(result.get("error") or "vision probe failed"))[:1000],
        }
    except Exception as exc:
        return {
            "pass": False,
            "kind": "vision",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": mask_sensitive_string(str(exc))[:1000],
        }


def latest_runtime_certificate_path(runs_root: str | Path, config: Optional[AppConfig] = None) -> Path:
    relative = LATEST_CERT_RELATIVE
    if config is not None:
        live_cfg = getattr(config, "live_runtime_certification", None)
        configured = str(getattr(live_cfg, "latest_certificate_relative_path", "") or "").strip()
        if configured:
            relative = Path(configured)
    return Path(runs_root).expanduser() / relative


def verify_latest_live_runtime_certificate(config: AppConfig, runs_root: str | Path) -> Dict[str, Any]:
    """Verify a recent live certificate without trusting its claimed decision."""
    path = latest_runtime_certificate_path(runs_root, config)
    if not path.is_file():
        return {"pass": False, "status": "missing", "path": str(path), "reason": "Run `python -m hip_id_agent.cli certify-live-runtime` on the Windows HIP workstation."}
    try:
        cert = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"pass": False, "status": "invalid_json", "path": str(path), "reason": mask_sensitive_string(str(exc))[:500]}
    now = time.time()
    expected = runtime_environment_fingerprint(config)
    decision_ok = bool(cert.get("pass")) and str(cert.get("decision") or "") == "GO"
    fresh = float(cert.get("expires_at_epoch") or 0) > now
    fingerprint_ok = str(cert.get("runtime_fingerprint") or "") == expected
    digest_payload = dict(cert)
    claimed_digest = str(digest_payload.pop("certificate_sha256", "") or "")
    raw = json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest_ok = bool(claimed_digest) and hashlib.sha256(raw.encode("utf-8")).hexdigest() == claimed_digest
    passed = decision_ok and fresh and fingerprint_ok and digest_ok
    return {
        "pass": passed,
        "status": "valid" if passed else "invalid",
        "path": str(path),
        "decision_ok": decision_ok,
        "fresh": fresh,
        "fingerprint_ok": fingerprint_ok,
        "digest_ok": digest_ok,
        "expires_at_epoch": cert.get("expires_at_epoch"),
        "run_dir": cert.get("run_dir"),
        "certificate_id": cert.get("certificate_id"),
        "reason": "" if passed else "Live runtime certificate is missing, expired, mismatched to current runtime settings, or failed integrity validation.",
    }


async def certify_live_runtime(
    *,
    config: AppConfig,
    runs_root: str | Path,
    target_url: str = "",
    ttl_seconds: int = 3600,
    require_pyautogui_mcp: bool = False,
) -> Dict[str, Any]:
    """Perform a non-mutating certification against the actual Windows HIP runtime.

    The only human interaction permitted is Dell SSO in the opened browser window.
    Automation performs navigation/snapshots/reads only; no HIP Create/Save/Delete/
    Deploy/Submit control is clicked and PyAutoGUI MCP is exercised read-only through
    screen-size, cursor-position and screenshot probes.
    """
    runtime_fp = runtime_environment_fingerprint(config)
    strict_mcp_runtime = bool(getattr(config.mcp, "strict_runtime_required", False))
    cfg = config.model_copy(deep=True)
    cfg.portal.headless = False
    cfg.mcp.browser_backend = "mcp"
    # Certification probes every available witness, but adaptive mode does not
    # turn an unavailable witness into a blocker. The strict MCP profile does.
    cfg.mcp.use_playwright_mcp = True
    cfg.mcp.use_chrome_devtools_mcp = True
    cfg.mcp.use_hip_intelligence_mcp = True
    cfg.mcp.hip_intelligence_mcp_required = strict_mcp_runtime
    cfg.mcp.playwright_mcp_required_when_require_mcp = strict_mcp_runtime
    cfg.mcp.strict_runtime_required = strict_mcp_runtime
    cfg.semantic_understanding.strict_external_evidence = strict_mcp_runtime
    cfg.pyautogui.enabled = True
    cfg.pyautogui.mcp_enabled = True
    cfg.pyautogui.mcp_required = bool(require_pyautogui_mcp)
    cfg.pyautogui.allow_mutation_clicks = False

    target = str(target_url or cfg.portal.base_url or "").strip()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(runs_root).expanduser() / "live_runtime_certification" / f"CERT-{stamp}"
    safe_mkdir(run_dir, parents=True, exist_ok=True)
    checks: List[Dict[str, Any]] = []
    checks.append(_check("windows_desktop", "Windows interactive desktop", _is_windows_interactive()["pass"], evidence=_is_windows_interactive()))
    checks.append(_check("target_url", "HIP target URL configured", bool(target and urlparse(target).scheme in {"http", "https"}), evidence={"target": target.split("?", 1)[0]}))

    text_probe = await _probe_text_model(cfg)
    vision_probe = await _probe_vision_model(cfg)
    checks.append(_check("dell_aia_text", "Dell AIA text model", bool(text_probe.get("pass")), evidence=text_probe))
    checks.append(_check("dell_aia_vision", "Dell AIA vision model", bool(vision_probe.get("pass")), evidence=vision_probe))

    browser: Optional[BrowserSession] = None
    hip_backend: Optional[HIPIntelligenceMCPBackend] = None
    browser_evidence: Dict[str, Any] = {}
    try:
        browser = BrowserSession(cfg, run_dir / "browser")
        await browser.start()
        checks.append(_check("headed_browser", "Headed Edge/Chrome browser + Python Playwright session", bool(browser.page and browser.context), evidence={"backend_name": browser.backend_name, "cdp_health": getattr(browser, "_cdp_health", {})}))

        await browser.goto_base_and_complete_sso(target)
        page = browser.page
        assert page is not None
        logged_in = await browser._looks_logged_in(page)
        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text(timeout=5000))[:30000]
        except Exception:
            pass
        checks.append(_check("dell_sso", "Dell SSO authenticated HIP session", logged_in, evidence={"final_url": str(page.url).split("?", 1)[0], "authenticated_once": bool(getattr(browser, "_authenticated_once", False))}))

        pw = browser.playwright_mcp_backend
        pw_cap = browser.playwright_mcp_capabilities or {}
        pw_required = ["browser_snapshot", "browser_find", "browser_click", "browser_type", "browser_fill_form", "browser_select_option", "browser_take_screenshot"]
        pw_tools = set(pw_cap.get("tools") or [])
        pw_tools_ok = bool(pw and all(x in pw_tools for x in pw_required))
        pw_snapshot: Dict[str, Any] = {}
        pw_find: Dict[str, Any] = {}
        pw_error = ""
        if pw:
            try:
                pw_snapshot = await pw.snapshot(boxes=True, depth=6)
                keyword = next((k for k in cfg.portal.sso_positive_texts if str(k).lower() in body_text.lower()), "")
                if keyword:
                    pw_find = await pw.find(text=keyword)
            except Exception as exc:
                pw_error = mask_sensitive_string(str(exc))[:800]
        pw_live_ok = pw_tools_ok and bool(str(pw_snapshot.get("text") or "").strip())
        checks.append(_check(
            "playwright_mcp_same_browser",
            "Playwright MCP attached to authenticated HIP browser",
            pw_live_ok,
            blocker=strict_mcp_runtime,
            detail=pw_error or ("Required by strict MCP runtime." if strict_mcp_runtime else "Optional adaptive executor/witness; Python Playwright remains available."),
            evidence={"capabilities": pw_cap, "snapshot_chars": len(str(pw_snapshot.get("text") or "")), "browser_find_chars": len(str(pw_find.get("text") or ""))},
        ))

        cdp = browser.mcp_backend
        cdp_cap = browser.mcp_capabilities or {}
        cdp_select: Dict[str, Any] = {}
        cdp_snapshot: Dict[str, Any] = {}
        cdp_network: Any = []
        cdp_console: Any = []
        cdp_error = ""
        if cdp:
            try:
                cdp_select = await cdp.select_page_for_url(str(page.url))
                cdp_snapshot = await cdp.get_dom_snapshot()
                cdp_network = await cdp.get_network_events()
                cdp_console = await cdp.get_console_messages()
            except Exception as exc:
                cdp_error = mask_sensitive_string(str(exc))[:800]
        cdp_ok = bool(cdp and cdp_select.get("pass") and (cdp_snapshot.get("snapshot_text") or cdp_snapshot.get("text") or cdp_snapshot))
        checks.append(_check(
            "devtools_same_surface",
            "Chrome DevTools MCP independent same-surface witness",
            cdp_ok,
            blocker=strict_mcp_runtime,
            detail=cdp_error or ("Required by strict MCP runtime." if strict_mcp_runtime else "Optional independent witness in adaptive mode."),
            evidence={"capabilities": cdp_cap, "surface": cdp_select, "network_event_count": len(cdp_network) if isinstance(cdp_network, list) else None, "console_observed": cdp_console is not None},
        ))

        semantic_state = await capture_semantic_state(page)
        hip_tools: List[str] = []
        hip_surface: Dict[str, Any] = {}
        hip_route: Dict[str, Any] = {}
        hip_error = ""
        required_hip = [
            "build_web_representation", "resolve_semantic_control", "rank_semantic_candidates", "verify_semantic_action_effect",
            "hip_get_current_surface", "hip_get_form_schema", "hip_find_control", "hip_get_safe_actions",
            "hip_get_route_identity", "hip_get_form_generation",
        ]
        hip_ok = False
        try:
            hip_backend = HIPIntelligenceMCPBackend.from_config(cfg, run_dir=run_dir / "hip_intelligence_mcp", memory_dir=run_dir / "hip_intelligence_mcp" / "memory")
            await hip_backend.start()
            hip_tools = sorted(hip_backend.client.tools)
            hip_surface = await hip_backend.get_current_surface({"surface": semantic_state, "page": "live_runtime_certification"})
            hip_route = await hip_backend.get_route_identity({"surface": semantic_state, "page": "live_runtime_certification"})
            hip_ok = all(x in hip_backend.client.tools for x in required_hip) and str(hip_surface.get("status") or "") == "ok"
        except Exception as exc:
            hip_error = mask_sensitive_string(str(exc))[:800]
        checks.append(_check(
            "hip_intelligence_mcp",
            "HIP Intelligence MCP live semantic website brain",
            hip_ok,
            blocker=strict_mcp_runtime,
            detail=hip_error or ("Required by strict MCP runtime." if strict_mcp_runtime else "Optional semantic witness/accelerator in adaptive mode."),
            evidence={"tool_count": len(hip_tools), "required_tools": {x: x in hip_tools for x in required_hip}, "surface": hip_surface, "route": hip_route},
        ))

        py = browser.pyautogui_mcp_backend
        py_cap = browser.pyautogui_mcp_capabilities or {}
        py_result = await _probe_pyautogui_mcp_readonly(py, py_cap)
        checks.append(_check(
            "pyautogui_mcp",
            "PyAutoGUI MCP read-only desktop smoke",
            bool(py_result.get("pass")),
            blocker=require_pyautogui_mcp,
            detail=str(py_result.get("detail") or ""),
            evidence=py_result.get("evidence") or {},
        ))
        python_playwright_ok = bool(page and browser.context)
        hybrid_executor_ok = bool(pw_live_ok or py_result.get("pass") or python_playwright_ok)
        checks.append(_check(
            "hybrid_interaction_executor",
            "At least one authoritative interaction executor is live",
            hybrid_executor_ok,
            evidence={
                "playwright_mcp": bool(pw_live_ok),
                "pyautogui_mcp": bool(py_result.get("pass")),
                "python_playwright": python_playwright_ok,
                "policy": "PyAutoGUI first when healthy; Playwright MCP next; governed Python Playwright fallback remains authoritative with exact effect verification.",
            },
        ))

        mutation_auth_off = not bool((getattr(browser, "_portal_mutation_authorization", {}) or {}).get("enabled")) and not bool(cfg.pyautogui.allow_mutation_clicks)
        checks.append(_check("mutation_guard", "Tenant mutation channels disabled during certification", mutation_auth_off, evidence={"portal_mutation_authorization": False, "pyautogui_mutation_clicks": bool(cfg.pyautogui.allow_mutation_clicks)}))

        await browser.collect_dom_click_log()
        await browser.collect_dom_event_log()
        await browser.flush_logs(force=True)
        browser_evidence = {
            "final_url": str(page.url).split("?", 1)[0],
            "backend_name": browser.backend_name,
            "playwright_mcp": pw_cap,
            "chrome_devtools_mcp": cdp_cap,
            "pyautogui_mcp": py_cap,
            "semantic_control_count": len(semantic_state.get("controls") or []),
            "dom_generation": semantic_state.get("dom_generation"),
        }
    except Exception as exc:
        checks.append(_check("live_session_exception", "Live runtime session completed without exception", False, detail=mask_sensitive_string(str(exc))[:1500]))
        if browser is not None:
            try:
                await browser.write_failure_bundle(exc, extra={"recovery_suggestions": ["Re-run certify-live-runtime after fixing the first failed blocker."]})
            except Exception:
                pass
    finally:
        if hip_backend is not None:
            try:
                await hip_backend.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    blockers = [x for x in checks if x["severity"] == "blocker" and not x["pass"]]
    warnings = [x for x in checks if x["severity"] == "warning" and not x["pass"]]
    now = time.time()
    cert: Dict[str, Any] = {
        "schema_version": CERT_SCHEMA,
        "certificate_id": f"HIP-LIVE-{stamp}-{runtime_fp[:10]}",
        "pass": not blockers,
        "decision": "GO" if not blockers else "NO_GO",
        "created_at": _utc_now(),
        "created_at_epoch": now,
        "expires_at_epoch": now + max(60, int(ttl_seconds)),
        "runtime_fingerprint": runtime_fp,
        "target_url": target.split("?", 1)[0],
        "run_dir": str(run_dir),
        "checks": checks,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": [{"id": x["id"], "label": x["label"], "detail": x["detail"]} for x in blockers],
        "warnings": [{"id": x["id"], "label": x["label"], "detail": x["detail"]} for x in warnings],
        "browser_evidence": mask_sensitive_data(browser_evidence),
        "runtime_mode": "strict_mcp" if strict_mcp_runtime else "adaptive_hybrid",
        "non_mutating_contract": "Automation performs only navigation, snapshots, semantic reads, MCP capability reads, model probes and PyAutoGUI size/position/screenshot. Dell SSO may require human interaction. No HIP Create/Save/Delete/Deploy/Submit action is authorized.",
    }
    digest_raw = json.dumps(cert, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    cert["certificate_sha256"] = hashlib.sha256(digest_raw.encode("utf-8")).hexdigest()
    safe_write_json(run_dir / "live_runtime_certificate.json", cert, mask=False)
    latest = latest_runtime_certificate_path(runs_root, config)
    safe_mkdir(latest.parent, parents=True, exist_ok=True)
    safe_write_json(latest, cert, mask=False)
    return mask_sensitive_data(cert)
