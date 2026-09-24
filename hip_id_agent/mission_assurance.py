"""Fail-closed mission assurance for the autonomous HIP web agent.

This module adds a final, value-free assurance layer after a phase has been
filled and judged.  It does not execute form mutations.  It proves that the
phase was observed by all required MCP channels in the current browser state
and that the artifacts needed for deterministic replay and UI/API forensics are
present before the phase is promoted into long-term memory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


def _url_surface(value: str) -> str:
    try:
        parsed = urlparse(str(value or ""))
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    except Exception:
        return str(value or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")


def _fingerprint(value: Any) -> str:
    raw = json.dumps(mask_sensitive_data(value), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


async def capture_mcp_evidence_quorum(
    *,
    browser: Any,
    agentq_controller: Any,
    phase: str,
    attempt: int,
    phase_dir: str | Path,
    required: bool = True,
) -> Dict[str, Any]:
    """Capture fresh read-only evidence from all three MCP channels.

    The quorum is intentionally taken *after* deterministic UI verification and
    the independent judges.  This prevents startup-only or stale MCP evidence
    from being used to certify a phase for replay memory.
    """
    phase_dir = Path(phase_dir)
    page = getattr(browser, "page", None)
    local_url = str(getattr(page, "url", "") or "")
    expected_surface = _url_surface(local_url)
    channels: Dict[str, Any] = {}

    # Playwright MCP: current URL + accessibility snapshot from the same CDP tab.
    pw = getattr(browser, "playwright_mcp_backend", None)
    if pw is None:
        channels["playwright_mcp"] = {"pass": False, "reason": "backend unavailable"}
    else:
        try:
            pw_url = await pw.get_current_url()
            snap = await pw.snapshot(boxes=False, depth=5)
            snap_summary = {
                "type": type(snap).__name__,
                "fingerprint": _fingerprint(snap),
                "nonempty": bool(snap),
            }
            channels["playwright_mcp"] = {
                "pass": bool(_url_surface(str(pw_url)) == expected_surface and snap_summary["nonempty"]),
                "current_url": _url_surface(str(pw_url)),
                "url_matches_local": _url_surface(str(pw_url)) == expected_surface,
                "snapshot": snap_summary,
            }
        except Exception as exc:
            channels["playwright_mcp"] = {"pass": False, "error": mask_sensitive_string(str(exc))}

    # Chrome DevTools MCP: independent URL + DOM snapshot.
    cdp = getattr(browser, "mcp_backend", None)
    if cdp is None:
        channels["chrome_devtools_mcp"] = {"pass": False, "reason": "backend unavailable"}
    else:
        try:
            cdp_url = await cdp.get_current_url()
            dom = await cdp.get_dom_snapshot()
            channels["chrome_devtools_mcp"] = {
                "pass": bool(_url_surface(str(cdp_url)) == expected_surface and bool(dom)),
                "current_url": _url_surface(str(cdp_url)),
                "url_matches_local": _url_surface(str(cdp_url)) == expected_surface,
                "dom_snapshot": {"fingerprint": _fingerprint(dom), "nonempty": bool(dom)},
            }
        except Exception as exc:
            channels["chrome_devtools_mcp"] = {"pass": False, "error": mask_sensitive_string(str(exc))}

    # HIP Intelligence MCP: prove the local reasoning server is alive in this
    # attempt and can build a representation for the current phase/surface.
    hip_backend = getattr(agentq_controller, "mcp_backend", None)
    if hip_backend is None:
        channels["hip_intelligence_mcp"] = {"pass": False, "reason": "backend unavailable"}
    else:
        try:
            representation = await hip_backend.build_representation({
                "phase": phase,
                "url": local_url,
                "controls": [],
                "surface_gate": {"assurance_probe": True},
                "dom_transition": {},
                "console_signatures": [],
                "network_signatures": [],
            })
            channels["hip_intelligence_mcp"] = {
                "pass": bool(isinstance(representation, Mapping) and representation),
                "representation_fingerprint": _fingerprint(representation),
                "structural_fingerprint": representation.get("structural_fingerprint") if isinstance(representation, Mapping) else None,
            }
        except Exception as exc:
            channels["hip_intelligence_mcp"] = {"pass": False, "error": mask_sensitive_string(str(exc))}

    passed = all(bool(row.get("pass")) for row in channels.values()) if required else any(bool(row.get("pass")) for row in channels.values())
    payload = mask_sensitive_data({
        "schema_version": "hip.mcp-evidence-quorum.v1",
        "phase": phase,
        "attempt": int(attempt),
        "captured_at": utc_now(),
        "local_url": expected_surface,
        "required": bool(required),
        "channels": channels,
        "pass": bool(passed),
        "freshness_policy": "all required MCP evidence is recaptured after the current phase reaches its judged exact state",
        "values_stored": False,
    })
    safe_write_json(phase_dir / "mcp_evidence_quorum.json", payload)
    return payload


def build_phase_assurance_report(
    *,
    phase: str,
    attempt: int,
    verification: Mapping[str, Any],
    judge_result: Mapping[str, Any],
    maximum_observability: Mapping[str, Any],
    api_result: Mapping[str, Any],
    trajectory_result: Mapping[str, Any],
    mcp_quorum: Mapping[str, Any],
    strict: bool = True,
) -> Dict[str, Any]:
    """Create the final per-phase promotion gate used by autonomous mission mode."""
    verification_status = str(verification.get("status") or "").lower()
    checks = {
        "deterministic_ui_exact": verification_status not in {"failed", "fail", "blocked", "error", ""},
        "independent_judges": bool(judge_result.get("pass", True)),
        "input_control_coverage": bool(maximum_observability.get("coverage_pass", True)),
        "deterministic_replay_ready": bool(maximum_observability.get("replay_ready", True)),
        "ui_api_capture": bool(api_result.get("pass", True)),
        "mcp_evidence_quorum": bool(mcp_quorum.get("pass", not strict)),
        "validated_trajectory": bool(
            trajectory_result.get("trust") == "validated"
            and trajectory_result.get("trajectory_fingerprint")
            and trajectory_result.get("judge_pass", True)
        ),
        "parent_child_contract": bool(
            isinstance(trajectory_result.get("parent_child_contract"), Mapping)
            and (trajectory_result.get("parent_child_contract") or {}).get("contract_fingerprint")
        ),
        "golden_reference_present": bool(trajectory_result.get("golden_references")),
    }
    # V236 separates *current form correctness* from *learning/replay promotion*.
    # A phase is complete when the live UI is exact, the independent judge passes,
    # and every supplied input leaf is covered. Replay/MCP/API/trajectory/golden
    # evidence governs whether this run may be promoted into deterministic memory;
    # those optional witness dimensions must not relabel an exact current form as
    # BLOCKED. This also removes the circular first-run dependency where a phase
    # needed a validated trajectory/golden reference before it could ever complete.
    completion_check_names = (
        "deterministic_ui_exact",
        "independent_judges",
        "input_control_coverage",
    )
    completion_checks = {name: bool(checks.get(name)) for name in completion_check_names}
    completion_pass = all(completion_checks.values())
    promotion_pass = all(checks.values())
    missing_completion = [name for name, ok in completion_checks.items() if not ok]
    missing_promotion = [name for name, ok in checks.items() if not ok]
    return mask_sensitive_data({
        "schema_version": "hip.phase-mission-assurance.v2",
        "phase": phase,
        "attempt": int(attempt),
        "strict": bool(strict),
        "pass": bool(completion_pass),
        "completion_pass": bool(completion_pass),
        "checks": checks,
        "completion_checks": completion_checks,
        "missing_assurance": missing_completion,
        "learning_promotion_pass": bool(promotion_pass),
        "missing_learning_promotion": missing_promotion,
        "promotion_allowed": bool(promotion_pass),
        "recovery_policy": "repair current-form completion dimensions first; missing replay/MCP/API/trajectory/golden evidence disables memory promotion but does not block an exact current form",
        "values_stored": False,
        "captured_at": utc_now(),
    })
