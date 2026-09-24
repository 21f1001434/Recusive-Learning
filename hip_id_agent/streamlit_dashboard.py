from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .autogen_runtime import autogen_runtime_status
from .process_control import pause_process, resume_process
from .section_scope import PHASE_SEQUENCE, resolve_section, section_catalog
from .runtime_env import configure_utf8_stdio


PHASES: tuple[str, ...] = (
    "data_map",
    "source_document_type",
    "target_document_type",
    "rule",
    "source_transport_profile",
    "target_transport_profile",
    "biz_flow",
)

RUNTIME_DIR_NAME = ".streamlit_runtime"
PROCESS_STATE_FILE = "process.json"
CONSOLE_LOG_FILE = "mission_console.log"



configure_utf8_stdio()

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    temp.replace(target)


def tail_text(path: str | Path, max_chars: int = 30000) -> str:
    target = Path(path)
    if not target.is_file():
        return ""
    try:
        size = target.stat().st_size
        with target.open("rb") as handle:
            if size > max_chars:
                handle.seek(-max_chars, os.SEEK_END)
            data = handle.read()
        return data.decode("utf-8", errors="replace")[-max_chars:]
    except Exception:
        return ""


def project_runtime_dir(project_root: str | Path) -> Path:
    path = Path(project_root).resolve() / RUNTIME_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_mission_command(
    *,
    project_root: str | Path,
    config: str | Path,
    input_json: str | Path,
    runs_dir: str | Path,
    golden_screenshot_dir: str | Path,
    upload_assets_dir: str | Path,
    api_mode: str = "capture",
    write_heavy_evidence: bool = False,
    allow_api_mutation: bool = False,
    resume_run_dir: str | Path | None = None,
    witness_mode: bool = False,
    python_executable: str | Path | None = None,
) -> List[str]:
    root = Path(project_root).resolve()
    exe = str(python_executable or sys.executable)
    mode = str(api_mode or "capture").strip().lower()
    if mode not in {"capture", "dry_run", "validate", "write"}:
        raise ValueError(f"Unsupported API mode: {mode}")
    if mode == "write" and not allow_api_mutation:
        raise ValueError("API write mode requires explicit allow_api_mutation=True")
    if witness_mode and (mode == "write" or allow_api_mutation):
        raise ValueError("Live witness mode is strictly non-mutating")
    if witness_mode:
        mode = "capture"

    def resolved(value: str | Path) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else (root / path).resolve())

    command = [
        exe,
        "-u",
        "-m",
        "hip_id_agent.cli",
        "run-full-dummy-fill",
        "--config",
        resolved(config),
        "--customer",
        "UHAUL-POASN",
        "--input-json",
        resolved(input_json),
        "--autonomous-mission",
        "--agentq-crawler-fusion",
        "--dual-ui-api",
        "--capture-submit-api",
        "--api-mode",
        mode,
        "--api-capture-best-effort",
        "--fast-form-only",
        "--portal-brain",
        "--import-unified-kb",
        "--require-unified-kb",
        "--self-heal-kb",
        "--only-explore-unknown-parent-branches",
        "--kb-repair-min-confirmations",
        "10",
        "--kb-supersede-min-confirmations",
        "10",
        "--no-exploration-agent",
        "--observe-current-branch-only",
        "--exploration-max-values-per-parent",
        "1",
        "--section-judge",
        "--require-text-judge",
        "--require-vision-judge",
        "--section-judge-max-repairs",
        "5",
        "--vision-verify",
        "--vision-model",
        "gemma-3-27b-it",
        "--save-replay-blueprint",
        "--allow-executor-fallback",
        "--bounded-runtime-self-heal",
        "--runtime-self-heal-max-attempts",
        "5",
        "--forensic-evidence",
        "--maximum-observability",
        "--golden-screenshot-dir",
        resolved(golden_screenshot_dir),
        "--upload-assets-dir",
        resolved(upload_assets_dir),
        "--runs-dir",
        resolved(runs_dir),
        "--write-heavy-evidence" if write_heavy_evidence else "--no-write-heavy-evidence",
    ]
    if witness_mode:
        command.extend(["--live-witness", "--no-capture-submit-api", "--api-capture-best-effort"])
    if allow_api_mutation:
        command.append("--allow-api-mutation")
    if resume_run_dir:
        command.extend(["--resume-run", resolved(resume_run_dir)])
    return command



def build_section_mission_command(
    *,
    project_root: str | Path,
    config: str | Path,
    input_json: str | Path,
    runs_dir: str | Path,
    golden_screenshot_dir: str | Path,
    upload_assets_dir: str | Path,
    section: str,
    api_mode: str = "capture",
    write_heavy_evidence: bool = False,
    allow_api_mutation: bool = False,
    until_complete: bool = False,
    witness_mode: bool = False,
    python_executable: str | Path | None = None,
) -> List[str]:
    """Build the same governed form mission, limited to one user-facing HIP section."""
    root = Path(project_root).resolve()
    exe = str(python_executable or sys.executable)
    spec = resolve_section(section)
    mode = str(api_mode or "capture").strip().lower()
    if mode not in {"capture", "dry_run", "validate", "write"}:
        raise ValueError(f"Unsupported API mode: {mode}")
    if mode == "write" and not allow_api_mutation:
        raise ValueError("API write mode requires explicit allow_api_mutation=True")
    if witness_mode and (mode == "write" or allow_api_mutation):
        raise ValueError("Live witness mode is strictly non-mutating")
    if witness_mode:
        mode = "capture"

    def resolved(value: str | Path) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else (root / path).resolve())

    command = [
        exe, "-u", "-m", "hip_id_agent.cli", "run-full-dummy-fill",
        "--config", resolved(config),
        "--customer", f"SECTION-{str(spec['id']).upper().replace('-', '_')}-STREAMLIT",
        "--input-json", resolved(input_json),
        "--phases", str(spec["phase_csv"]),
        "--agentq-crawler-fusion",
        "--dual-ui-api",
        "--capture-submit-api",
        "--api-mode", mode,
        "--require-api-capture",
        "--fast-form-only",
        "--portal-brain",
        "--import-unified-kb",
        "--require-unified-kb",
        "--self-heal-kb",
        "--only-explore-unknown-parent-branches",
        "--exploration-agent",
        "--explore-parent-branches",
        "--section-judge",
        "--require-text-judge",
        "--require-vision-judge",
        "--vision-verify",
        "--save-replay-blueprint",
        "--allow-executor-fallback",
        "--runtime-self-heal",
        "--forensic-evidence",
        "--maximum-observability",
        "--golden-screenshot-dir", resolved(golden_screenshot_dir),
        "--upload-assets-dir", resolved(upload_assets_dir),
        "--runs-dir", resolved(runs_dir),
        "--write-heavy-evidence" if write_heavy_evidence else "--no-write-heavy-evidence",
    ]
    if witness_mode:
        command.extend(["--live-witness", "--no-capture-submit-api", "--api-capture-best-effort"])
    if until_complete:
        command.append("--runtime-self-heal-until-complete")
    else:
        command.append("--bounded-runtime-self-heal")
    if allow_api_mutation:
        command.append("--allow-api-mutation")
    return command


def build_phase_subset_mission_command(
    *,
    project_root: str | Path,
    config: str | Path,
    input_json: str | Path,
    runs_dir: str | Path,
    golden_screenshot_dir: str | Path,
    upload_assets_dir: str | Path,
    phases: Sequence[str],
    api_mode: str = "capture",
    write_heavy_evidence: bool = False,
    allow_api_mutation: bool = False,
    until_complete: bool = False,
    witness_mode: bool = False,
    python_executable: str | Path | None = None,
) -> List[str]:
    """Build a deterministic no-save mission for an exact phase subset.

    Used by description-triggered plans when the user asks for a combination that
    is more specific than the named section catalog.  No unmentioned phase is
    silently added.
    """
    root = Path(project_root).resolve()
    exe = str(python_executable or sys.executable)
    selected = [str(p) for p in PHASE_SEQUENCE if str(p) in set(str(x) for x in phases)]
    if not selected:
        raise ValueError("At least one valid HIP phase is required")
    mode = str(api_mode or "capture").strip().lower()
    if mode not in {"capture", "dry_run", "validate", "write"}:
        raise ValueError(f"Unsupported API mode: {mode}")
    if mode == "write" and not allow_api_mutation:
        raise ValueError("API write mode requires explicit allow_api_mutation=True")
    if witness_mode and (mode == "write" or allow_api_mutation):
        raise ValueError("Live witness mode is strictly non-mutating")
    if witness_mode:
        mode = "capture"

    def resolved(value: str | Path) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else (root / path).resolve())

    command = [
        exe, "-u", "-m", "hip_id_agent.cli", "run-full-dummy-fill",
        "--config", resolved(config),
        "--customer", "DESCRIPTION-TRIGGERED-HIP-MISSION",
        "--input-json", resolved(input_json),
        "--phases", ",".join(selected),
        "--agentq-crawler-fusion", "--dual-ui-api", "--capture-submit-api",
        "--api-mode", mode, "--require-api-capture", "--fast-form-only",
        "--portal-brain", "--import-unified-kb", "--require-unified-kb", "--self-heal-kb",
        "--revalidate-known-parent-branches", "--exploration-agent", "--explore-parent-branches",
        "--section-judge", "--require-text-judge", "--require-vision-judge", "--vision-verify",
        "--save-replay-blueprint", "--allow-executor-fallback", "--runtime-self-heal",
        "--forensic-evidence", "--maximum-observability",
        "--golden-screenshot-dir", resolved(golden_screenshot_dir),
        "--upload-assets-dir", resolved(upload_assets_dir),
        "--runs-dir", resolved(runs_dir),
        "--write-heavy-evidence" if write_heavy_evidence else "--no-write-heavy-evidence",
        "--runtime-self-heal-until-complete" if until_complete else "--bounded-runtime-self-heal",
    ]
    if witness_mode:
        command.extend(["--live-witness", "--no-capture-submit-api", "--api-capture-best-effort"])
    if allow_api_mutation:
        command.append("--allow-api-mutation")
    return command


def process_is_running(pid: int | str | None) -> bool:
    try:
        numeric_pid = int(pid or 0)
    except Exception:
        return False
    if numeric_pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {numeric_pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return str(numeric_pid) in (result.stdout or "")
        except Exception:
            return False
    try:
        os.kill(numeric_pid, 0)
        return True
    except OSError:
        return False


def load_process_state(project_root: str | Path) -> Dict[str, Any]:
    state_path = project_runtime_dir(project_root) / PROCESS_STATE_FILE
    state = read_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state["running"] = process_is_running(state.get("pid"))
    if not state["running"]:
        state["paused"] = False
    state["state_path"] = str(state_path)
    return state


def launch_mission(
    *,
    project_root: str | Path,
    command: Sequence[str],
    runs_dir: str | Path,
    extra_environment: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    root = Path(project_root).resolve()
    runtime_dir = project_runtime_dir(root)
    current = load_process_state(root)
    if current.get("running"):
        raise RuntimeError(f"A mission is already running with PID {current.get('pid')}")

    log_path = runtime_dir / CONSOLE_LOG_FILE
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
    if extra_environment:
        env.update({str(k): str(v) for k, v in extra_environment.items()})

    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    log_handle = log_path.open("a", encoding="utf-8", buffering=1)
    log_handle.write(f"\n\n===== Streamlit launch {utc_now()} =====\n")
    log_handle.write("COMMAND: " + subprocess.list2cmdline(list(command)) + "\n")
    process = subprocess.Popen(
        list(command),
        cwd=str(root),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=creationflags,
        close_fds=(os.name != "nt"),
    )
    state = {
        "schema_version": "hip.streamlit-process.v1",
        "pid": process.pid,
        "running": True,
        "paused": False,
        "started_at": utc_now(),
        "project_root": str(root),
        "runs_dir": str(Path(runs_dir).resolve()),
        "log_path": str(log_path),
        "command": list(command),
    }
    write_json(runtime_dir / PROCESS_STATE_FILE, state)
    return state


def stop_mission(project_root: str | Path, *, force: bool = False) -> Dict[str, Any]:
    state = load_process_state(project_root)
    pid = int(state.get("pid") or 0)
    if not pid or not state.get("running"):
        return {"status": "not_running", "pid": pid}
    try:
        if os.name == "nt":
            if not force:
                try:
                    os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                    time.sleep(1.0)
                except Exception:
                    pass
            if process_is_running(pid):
                command = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
                subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        else:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        result = {"status": "stop_requested", "pid": pid, "force": force, "stopped_at": utc_now()}
    except Exception as exc:
        result = {"status": "stop_failed", "pid": pid, "error": str(exc)}
    state.update(result)
    state["paused"] = False
    write_json(project_runtime_dir(project_root) / PROCESS_STATE_FILE, state)
    return result


def pause_mission(project_root: str | Path) -> Dict[str, Any]:
    state = load_process_state(project_root)
    pid = int(state.get("pid") or 0)
    if not pid or not state.get("running"):
        return {"status": "not_running", "pid": pid}
    if state.get("paused"):
        return {"status": "already_paused", "pid": pid}
    try:
        result = pause_process(pid)
        state["paused"] = result.get("status") in {"paused", "already_paused"}
        state["paused_at"] = utc_now() if state["paused"] else ""
        state.update(result)
        write_json(project_runtime_dir(project_root) / PROCESS_STATE_FILE, state)
        return {**result, "browser_remains_interactive": True}
    except Exception as exc:
        return {"status": "pause_failed", "pid": pid, "error": str(exc)}


def resume_mission(project_root: str | Path) -> Dict[str, Any]:
    state = load_process_state(project_root)
    pid = int(state.get("pid") or 0)
    if not pid or not state.get("running"):
        return {"status": "not_running", "pid": pid}
    try:
        result = resume_process(pid)
        state["paused"] = False
        state["resumed_at"] = utc_now()
        state.update(result)
        write_json(project_runtime_dir(project_root) / PROCESS_STATE_FILE, state)
        return result
    except Exception as exc:
        return {"status": "resume_failed", "pid": pid, "error": str(exc)}


def discover_runs(runs_dir: str | Path, *, limit: int = 50) -> List[Path]:
    root = Path(runs_dir)
    if not root.is_dir():
        return []
    candidates = [path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[:limit]


def latest_run(runs_dir: str | Path) -> Optional[Path]:
    runs = discover_runs(runs_dir, limit=1)
    return runs[0] if runs else None


def _latest_matching(root: Path, pattern: str) -> Optional[Path]:
    matches = list(root.glob(pattern))
    if not matches:
        return None
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0]


def collect_phase_status(run_dir: str | Path, phases: Sequence[str] = PHASES) -> List[Dict[str, Any]]:
    root = Path(run_dir)
    summary = read_json(root / "form_api_intelligence_summary.json", {})
    phase_results = summary.get("phase_results") if isinstance(summary, Mapping) else {}
    rows: List[Dict[str, Any]] = []
    for phase in [p for p in phases if p in PHASES]:
        result: Dict[str, Any] = {}
        if isinstance(phase_results, Mapping) and isinstance(phase_results.get(phase), Mapping):
            result = dict(phase_results[phase])
        if not result:
            result_path = _latest_matching(root, f"**/{phase}/**/form_api_agentq_result.json") or _latest_matching(root, f"**/{phase}/form_api_agentq_result.json")
            if result_path:
                result = read_json(result_path, {}) or {}
        assurance_path = _latest_matching(root, f"**/{phase}/phase_mission_assurance.json")
        assurance = read_json(assurance_path, {}) if assurance_path else {}
        quorum_path = _latest_matching(root, f"**/{phase}/mcp_evidence_quorum.json")
        quorum = read_json(quorum_path, {}) if quorum_path else {}
        rows.append({
            "phase": phase,
            "status": result.get("status") or ("pending" if not result else "unknown"),
            "pass": bool(result.get("pass")) and (bool(assurance.get("pass")) if assurance else True),
            "attempt": result.get("attempt"),
            "assurance": assurance.get("pass") if assurance else None,
            "mcp_quorum": quorum.get("pass") if quorum else None,
            "api_transactions": result.get("api_transaction_count", 0),
            "request_payloads": result.get("api_request_payload_count", 0),
            "response_statuses": result.get("api_response_status_count", 0),
            "response_payloads": result.get("api_response_payload_count", 0),
            "submit_requests": result.get("captured_submit_request_count", 0),
        })
    return rows


def collect_api_transactions(run_dir: str | Path) -> List[Dict[str, Any]]:
    root = Path(run_dir)
    output: List[Dict[str, Any]] = []
    for path in sorted(root.glob("**/form_api_transactions.json"), key=lambda item: item.stat().st_mtime):
        payload = read_json(path, {})
        phase = payload.get("phase") if isinstance(payload, Mapping) else ""
        transactions = payload.get("transactions") if isinstance(payload, Mapping) else []
        for row in transactions or []:
            if not isinstance(row, Mapping):
                continue
            item = dict(row)
            item["phase"] = phase or item.get("phase") or _infer_phase_from_path(path)
            item["artifact_path"] = str(path)
            output.append(item)
    return output


def _infer_phase_from_path(path: Path) -> str:
    lower_parts = [part.lower() for part in path.parts]
    for phase in PHASES:
        if phase in lower_parts:
            return phase
    return "unknown"


def collect_crosswalk_rows(run_dir: str | Path) -> List[Dict[str, Any]]:
    root = Path(run_dir)
    output: List[Dict[str, Any]] = []
    for path in sorted(root.glob("**/ui_api_input_crosswalk.json"), key=lambda item: item.stat().st_mtime):
        payload = read_json(path, {})
        phase = payload.get("phase") if isinstance(payload, Mapping) else _infer_phase_from_path(path)
        for row in (payload.get("rows") if isinstance(payload, Mapping) else []) or []:
            if isinstance(row, Mapping):
                output.append({"phase": phase, **dict(row), "artifact_path": str(path)})
    return output


def collect_assurance_and_causality(run_dir: str | Path) -> Dict[str, Any]:
    root = Path(run_dir)
    assurance: List[Dict[str, Any]] = []
    causal: List[Dict[str, Any]] = []
    for phase in PHASES:
        assurance_path = _latest_matching(root, f"**/{phase}/phase_mission_assurance.json")
        quorum_path = _latest_matching(root, f"**/{phase}/mcp_evidence_quorum.json")
        if assurance_path:
            row = read_json(assurance_path, {}) or {}
            row["phase"] = phase
            row["artifact_path"] = str(assurance_path)
            if quorum_path:
                row["mcp_quorum_detail"] = read_json(quorum_path, {}) or {}
            assurance.append(row)
        causal_path = _latest_matching(root, f"**/{phase}/**/ui_api_causal_trace.json")
        if causal_path:
            payload = read_json(causal_path, {}) or {}
            payload["phase"] = phase
            payload["artifact_path"] = str(causal_path)
            causal.append(payload)
    return {"assurance": assurance, "causal": causal}


def build_evidence_archive(run_dir: str | Path, *, max_file_bytes: int = 60_000_000) -> Path:
    root = Path(run_dir).resolve()
    output_dir = root.parent / RUNTIME_DIR_NAME / "downloads"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{root.name}_evidence.zip"
    allowed_suffixes = {".json", ".jsonl", ".md", ".txt", ".log", ".html", ".csv", ".png", ".jpg", ".jpeg"}
    excluded_parts = {"browser_profile", "user_data_dir", "node_modules", ".git", "portal_brain"}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
                continue
            relative = path.relative_to(root)
            if any(part.lower() in excluded_parts for part in relative.parts):
                continue
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
                handle.write(path, arcname=str(Path(root.name) / relative))
            except OSError:
                continue
    return archive


def validate_json_upload(data: bytes) -> Dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:
        return {"pass": False, "error": f"Invalid JSON: {exc}"}
    if not isinstance(payload, Mapping):
        return {"pass": False, "error": "Input JSON must contain an object at the root."}
    objects = payload.get("objects")
    if not isinstance(objects, Mapping):
        return {"pass": False, "error": "Input JSON must contain an 'objects' object."}
    return {"pass": True, "payload": payload, "object_keys": sorted(str(key) for key in objects.keys())}


def build_mission_preflight_report(
    *,
    input_json: str | Path,
    golden_screenshot_dir: str | Path,
    upload_assets_dir: str | Path,
    phases: Sequence[str] = PHASES,
) -> Dict[str, Any]:
    """Static fail-closed mission readiness check used before Chrome is opened."""
    input_path = Path(input_json)
    golden_dir = Path(golden_screenshot_dir)
    uploads_dir = Path(upload_assets_dir)
    if not input_path.is_file():
        return {"pass": False, "issues": [{"path": str(input_path), "reason": "input JSON does not exist"}], "phase_rows": []}
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"pass": False, "issues": [{"path": str(input_path), "reason": f"input JSON cannot be parsed: {exc}"}], "phase_rows": []}
    from .dummy_fill_e2e import validate_live_input_contract
    selected_phases = [phase for phase in phases if phase in PHASES]
    contract = validate_live_input_contract(payload if isinstance(payload, dict) else {}, phases=selected_phases)
    issues = list(contract.get("issues") or [])
    phase_rows: List[Dict[str, Any]] = []
    for phase in selected_phases:
        coverage = (contract.get("phase_coverage") or {}).get(phase) or {}
        phase_rows.append({
            "phase": phase,
            "pass": bool(coverage.get("pass")),
            "leaf_coverage": coverage.get("exact_leaf_coverage_percent", 0),
            "mapped_paths": coverage.get("mapped_input_path_count", 0),
            "accounted_nonmutable": coverage.get("accounted_nonmutable_path_count", 0),
            "unmapped": ", ".join(coverage.get("unmapped_input_leaf_paths") or []),
        })

    required_golden = {
        "data_map": ["Data Map.png"],
        "source_document_type": ["Source Document Type.png"],
        "target_document_type": ["Target Document Type.png"],
        "rule": ["Rules.png"],
        "source_transport_profile": ["Source Transport Profile.png"],
        "target_transport_profile": ["Target Transport Profile.png"],
        "biz_flow": ["BizFlow-FD.png", "BizFlow-CS.png", "BizFlow-CT-1.png", "BizFlow-CT-2.png", "BizFlow-CR.png"],
    }
    missing_golden = []
    for phase, names in required_golden.items():
        if phase not in selected_phases:
            continue
        for name in names:
            if not (golden_dir / name).is_file():
                missing_golden.append({"phase": phase, "file": name})
    if missing_golden:
        issues.append({"path": str(golden_dir), "reason": "required golden screenshot(s) missing", "missing": missing_golden})

    objects = payload.get("objects") if isinstance(payload, Mapping) and isinstance(payload.get("objects"), Mapping) else {}
    data_map = objects.get("data_map") if isinstance(objects.get("data_map"), Mapping) else {}
    upload_name = str(data_map.get("map_data_file") or "").strip()
    upload_ok = True
    if upload_name and "data_map" in selected_phases:
        upload_ok = (uploads_dir / Path(upload_name).name).is_file()
        if not upload_ok:
            issues.append({"path": str(uploads_dir / Path(upload_name).name), "reason": "required Data Map upload asset is missing"})

    return {
        "schema_version": "hip.streamlit-mission-preflight.v1",
        "pass": bool(contract.get("pass") and not missing_golden and upload_ok and not issues),
        "phase_rows": phase_rows,
        "issues": issues,
        "input_contract": contract,
        "golden_screenshots_pass": not missing_golden,
        "upload_assets_pass": upload_ok,
        "values_stored": False,
        "selected_phases": selected_phases,
        "section_scoped": list(selected_phases) != list(PHASES),
    }


def _json_pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def collect_repeatable_row_plan(input_json: str | Path, phases: Sequence[str] = PHASES) -> List[Dict[str, Any]]:
    """Preview every input-driven section that needs one or more + clicks."""
    from .repeatable_rows import build_repeatable_section_plan

    payload = read_json(input_json, {})
    if not isinstance(payload, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for phase in [p for p in phases if p in PHASES]:
        for item in build_repeatable_section_plan(payload, phase):
            rows.append({
                "phase": phase,
                "section": item.get("section"),
                "input_path": item.get("input_path"),
                "json_rows": item.get("row_count_from_input"),
                "plus_clicks_from_one_initial_row": item.get("add_clicks_needed"),
                "tab": item.get("tab") or "",
            })
    return rows




def main() -> None:
    try:
        import streamlit as st
        import streamlit.components.v1 as components
    except ImportError as exc:  # pragma: no cover - shown to the operator
        raise SystemExit("Streamlit is not installed. Run: python -m pip install -r requirements.txt") from exc

    st.set_page_config(page_title="HIP AgentQ UI/API Mission", page_icon="🧭", layout="wide")
    st.title("HIP AgentQ UI/API Autonomous Mission")
    st.caption("Parent-child UI execution, Browser-Use same-session state intelligence, AgentQ exploration/exploitation, MCP evidence, input-driven + row creation, and redacted API payload/response capture.")

    project_root = Path.cwd().resolve()
    defaults = {
        "config": str(project_root / "config.yaml"),
        "input": str(project_root / "examples" / "uhaul_poasn_full_dummy_input.json"),
        "runs": str(project_root / "runs"),
        "golden": str(project_root / "golden_screenshots" / "UHAUL-POASN"),
        "uploads": str(project_root / "uploads"),
    }

    with st.sidebar:
        st.header("Mission configuration")
        project_value = st.text_input("Project directory", str(project_root))
        project_root = Path(project_value).expanduser().resolve()
        config_path = st.text_input("Config YAML", defaults["config"])
        input_path = st.text_input("Input JSON", defaults["input"])
        runs_dir = st.text_input("Runs directory", defaults["runs"])
        golden_dir = st.text_input("Golden screenshots", defaults["golden"])
        uploads_dir = st.text_input("Upload assets", defaults["uploads"])
        scope_rows = section_catalog()
        scope_labels = {str(row.get("label")): row for row in scope_rows}
        scope_label = st.selectbox("Execution scope", list(scope_labels.keys()), index=len(scope_labels) - 1)
        scope_spec = scope_labels.get(scope_label) or resolve_section("all")
        selected_phases = list(scope_spec.get("phases") or PHASES)
        section_until_complete = False
        if scope_spec.get("id") != "all":
            st.info(f"Section-only mode: {scope_spec.get('label')}. Unrelated HIP forms will not be opened.")
            st.caption(str(scope_spec.get("dependency_note") or ""))
            section_until_complete = st.checkbox("Self-heal selected section until it passes", value=False)
        api_mode = st.selectbox("API mode", ["capture", "validate", "dry_run", "write"], index=0)
        heavy = st.checkbox("Capture heavy evidence", value=True)
        auto_refresh = st.checkbox("Auto-refresh every 5 seconds", value=True)
        resume_dir = st.text_input("Resume run directory (optional)", "")
        allow_write = False
        write_phrase = ""
        if api_mode == "write":
            st.error("Write mode can mutate the HIP backend. Capture mode is recommended.")
            write_phrase = st.text_input("Type ALLOW API WRITE", type="password")
            allow_write = write_phrase == "ALLOW API WRITE" and os.getenv("HIP_ALLOW_API_MUTATION", "").upper() == "YES"
            st.caption("Also set HIP_ALLOW_API_MUTATION=YES before starting Streamlit.")

        uploaded = st.file_uploader("Optional replacement input JSON", type=["json"])
        if uploaded is not None:
            validation = validate_json_upload(uploaded.getvalue())
            if validation.get("pass"):
                upload_dir = project_runtime_dir(project_root) / "inputs"
                upload_dir.mkdir(parents=True, exist_ok=True)
                uploaded_path = upload_dir / Path(uploaded.name).name
                uploaded_path.write_bytes(uploaded.getvalue())
                input_path = str(uploaded_path)
                st.success(f"Using uploaded input: {uploaded_path.name}")
            else:
                st.error(validation.get("error"))

        process_state = load_process_state(project_root)
        preflight = build_mission_preflight_report(
            input_json=input_path,
            golden_screenshot_dir=golden_dir,
            upload_assets_dir=uploads_dir,
            phases=selected_phases,
        )
        autogen_status = autogen_runtime_status(verify_imports=True)
        if preflight.get("pass"):
            st.success("Input/form/golden/upload preflight passed")
        else:
            st.error("Mission preflight failed. Review the Preflight tab before starting.")
        if autogen_status.get("pass"):
            source_text = ", ".join(
                f"{name}={version} ({autogen_status.get('version_sources', {}).get(name) or 'detected'})"
                for name, version in (autogen_status.get("packages") or {}).items()
            )
            st.success("Microsoft AutoGen AgentChat 0.7.5 ready")
            if source_text:
                st.caption(source_text)
            if autogen_status.get("metadata_errors"):
                st.warning("AutoGen package metadata is damaged/incomplete, but exact 0.7.5 was recovered from the installed dist-info directories and runtime imports passed. Repair pip metadata when convenient; this does not block the mission.")
        else:
            st.error(str(autogen_status.get("reason") or "AutoGen runtime preflight failed."))
            if autogen_status.get("packages"):
                st.caption("Detected versions: " + ", ".join(f"{k}={v or 'unknown'}" for k, v in autogen_status.get("packages", {}).items()))
            if autogen_status.get("import_error"):
                st.code(str(autogen_status.get("import_error")), language="text")
        bun_ready = bool(shutil.which("bun") and shutil.which("bunx"))
        if bun_ready:
            st.success("Bun + bunx ready for MCP tooling")
        else:
            st.warning("Bun/bunx not detected. Run the Bun installation before live MCP execution.")
        start_disabled = bool(process_state.get("running")) or (api_mode == "write" and not allow_write) or not bool(preflight.get("pass")) or not bool(autogen_status.get("pass"))
        start_col, stop_col = st.columns(2)
        with start_col:
            start_label = "Start full mission" if scope_spec.get("id") == "all" else "Run selected section only"
            if st.button(start_label, type="primary", disabled=start_disabled, use_container_width=True):
                try:
                    if scope_spec.get("id") == "all":
                        command = build_mission_command(
                            project_root=project_root,
                            config=config_path,
                            input_json=input_path,
                            runs_dir=runs_dir,
                            golden_screenshot_dir=golden_dir,
                            upload_assets_dir=uploads_dir,
                            api_mode=api_mode,
                            write_heavy_evidence=heavy,
                            allow_api_mutation=allow_write,
                            resume_run_dir=resume_dir or None,
                        )
                    else:
                        command = build_section_mission_command(
                            project_root=project_root,
                            config=config_path,
                            input_json=input_path,
                            runs_dir=runs_dir,
                            golden_screenshot_dir=golden_dir,
                            upload_assets_dir=uploads_dir,
                            section=str(scope_spec.get("id")),
                            api_mode=api_mode,
                            write_heavy_evidence=heavy,
                            allow_api_mutation=allow_write,
                            until_complete=section_until_complete,
                        )
                    env = {
                        "AIA_USE_AUTOGEN": "true",
                        "HIP_USE_LLM_FORM_PLANNER": "true",
                        "HIP_REQUIRE_AUTOGEN_075": "true",
                    }
                    if allow_write:
                        env["HIP_ALLOW_API_MUTATION"] = "YES"
                    state = launch_mission(
                        project_root=project_root,
                        command=command,
                        runs_dir=runs_dir,
                        extra_environment=env,
                    )
                    st.success(f"Mission started with PID {state['pid']}")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        with stop_col:
            if st.button("Stop", disabled=not bool(process_state.get("running")), use_container_width=True):
                st.warning(stop_mission(project_root))
                st.rerun()
        pause_col, resume_col = st.columns(2)
        with pause_col:
            if st.button("Pause agent", disabled=(not bool(process_state.get("running")) or bool(process_state.get("paused"))), use_container_width=True):
                st.info(pause_mission(project_root))
                st.rerun()
        with resume_col:
            if st.button("Resume agent", disabled=(not bool(process_state.get("running")) or not bool(process_state.get("paused"))), use_container_width=True):
                st.success(resume_mission(project_root))
                st.rerun()
        st.caption("Pause suspends the HIP Python controller only; Chrome stays interactive for Dell SSO or supervised recovery, then Resume continues the same mission.")

    process_state = load_process_state(project_root)
    run_paths = discover_runs(runs_dir)
    default_run = run_paths[0] if run_paths else None

    status_cols = st.columns(4)
    status_cols[0].metric("Process", "Paused" if process_state.get("paused") else ("Running" if process_state.get("running") else "Stopped"))
    status_cols[1].metric("PID", process_state.get("pid") or "—")
    status_cols[2].metric("Runs found", len(run_paths))
    status_cols[3].metric("API mode", api_mode)

    tabs = st.tabs(["Preflight", "Mission", "API payloads & responses", "UI/API crosswalk", "Assurance & causality", "Evidence", "Console"])

    with tabs[0]:
        preflight = build_mission_preflight_report(
            input_json=input_path,
            golden_screenshot_dir=golden_dir,
            upload_assets_dir=uploads_dir,
            phases=selected_phases,
        )
        if preflight.get("pass"):
            st.success("100% phase input accounting and static mission consistency checks passed.")
        else:
            st.error("Preflight failed. The browser mission is blocked until these issues are corrected.")
        st.dataframe(preflight.get("phase_rows") or [], use_container_width=True, hide_index=True)
        st.metric("Golden screenshots", "Ready" if preflight.get("golden_screenshots_pass") else "Missing")
        st.metric("Upload assets", "Ready" if preflight.get("upload_assets_pass") else "Missing")
        autogen_status = autogen_runtime_status(verify_imports=True)
        if autogen_status.get("pass"):
            autogen_metric = "0.7.5 Ready"
        elif autogen_status.get("wrong_version") or autogen_status.get("missing"):
            autogen_metric = "Missing / wrong version"
        else:
            autogen_metric = "0.7.5 detected / import failed"
        st.metric("AutoGen AgentChat", autogen_metric)
        with st.expander("AutoGen 0.7.5 runtime"):
            st.json(autogen_status)
        if preflight.get("issues"):
            st.subheader("Blocking issues")
            st.json(preflight.get("issues"))
        with st.expander("Full input contract"):
            st.json(preflight.get("input_contract") or {})
        repeatable_plan = collect_repeatable_row_plan(input_path, phases=selected_phases)
        st.subheader("Input-driven + row plan")
        if repeatable_plan:
            st.dataframe(repeatable_plan, use_container_width=True, hide_index=True)
            st.caption("Example: 2 JSON rows with 1 initial portal row => exactly 1 section-local + click, verified by an N→N+1 row-count transition before row 2 is filled.")
        else:
            st.info("No repeatable sections requiring extra + rows were found in this input JSON.")

    with tabs[1]:
        if not run_paths:
            st.info("No run directory found yet. Start the mission and complete Dell SSO in the opened Chrome window.")
        else:
            selected_name = st.selectbox("Run", [path.name for path in run_paths], index=0, key="mission_run")
            selected_run = next(path for path in run_paths if path.name == selected_name)
            phase_rows = collect_phase_status(selected_run, phases=selected_phases)
            st.dataframe(phase_rows, use_container_width=True, hide_index=True)
            passed = sum(1 for row in phase_rows if row["pass"])
            total_selected = max(1, len(selected_phases))
            st.progress(passed / total_selected, text=f"{passed}/{total_selected} selected phases verified")
            summary = read_json(selected_run / "form_api_intelligence_summary.json", {})
            if summary:
                with st.expander("Run API summary"):
                    st.json(summary)

    with tabs[2]:
        if default_run is None:
            st.info("API transactions will appear after the mission opens and fills a form.")
        else:
            selected_name = st.selectbox("Run", [path.name for path in run_paths], index=0, key="api_run")
            selected_run = next(path for path in run_paths if path.name == selected_name)
            transactions = collect_api_transactions(selected_run)
            phases = ["all"] + sorted({str(row.get("phase") or "unknown") for row in transactions})
            selected_phase = st.selectbox("Phase", phases, index=0)
            filtered = transactions if selected_phase == "all" else [row for row in transactions if row.get("phase") == selected_phase]
            table_rows = [{
                "phase": row.get("phase"),
                "stage": row.get("stage"),
                "method": row.get("method"),
                "endpoint": row.get("endpoint_template"),
                "status": (row.get("response") or {}).get("status"),
                "request_payload": bool((row.get("request") or {}).get("payload_captured")),
                "response_payload": bool((row.get("response") or {}).get("payload_captured")),
                "mutation_capable": row.get("mutation_capable"),
            } for row in filtered]
            st.dataframe(table_rows, use_container_width=True, hide_index=True)
            if filtered:
                choices = [f"{idx + 1}. {row.get('method')} {row.get('endpoint_template')} [{(row.get('response') or {}).get('status')}]" for idx, row in enumerate(filtered)]
                selected_tx = st.selectbox("Transaction details", choices)
                row = filtered[choices.index(selected_tx)]
                req_col, resp_col = st.columns(2)
                with req_col:
                    st.subheader("Request payload")
                    st.json(row.get("request") or {})
                with resp_col:
                    st.subheader("Response")
                    st.json(row.get("response") or {})
                st.caption("Authorization, cookies, tokens and sensitive values are masked before persistence.")
            else:
                st.info("No API transactions captured for this selection.")

    with tabs[3]:
        if default_run is None:
            st.info("The crosswalk appears after a form attempt completes.")
        else:
            selected_name = st.selectbox("Run", [path.name for path in run_paths], index=0, key="crosswalk_run")
            selected_run = next(path for path in run_paths if path.name == selected_name)
            rows = collect_crosswalk_rows(selected_run)
            compact = [{
                "phase": row.get("phase"),
                "input_path": row.get("input_path"),
                "field_key": row.get("field_key"),
                "section": row.get("section"),
                "framework_names": ", ".join(row.get("framework_names") or []),
                "observed_api_keys": ", ".join(row.get("observed_api_key_matches") or []),
                "api_key_match": row.get("api_key_match"),
            } for row in rows]
            st.dataframe(compact, use_container_width=True, hide_index=True)

    with tabs[4]:
        if default_run is None:
            st.info("Mission assurance and UI-to-API causal evidence will appear after a phase completes.")
        else:
            selected_name = st.selectbox("Run", [path.name for path in run_paths], index=0, key="assurance_run")
            selected_run = next(path for path in run_paths if path.name == selected_name)
            evidence = collect_assurance_and_causality(selected_run)
            assurance_rows = [{
                "phase": row.get("phase"),
                "pass": row.get("pass"),
                "missing": ", ".join(row.get("missing_assurance") or []),
                "mcp_quorum": ((row.get("mcp_quorum_detail") or {}).get("pass")),
            } for row in evidence.get("assurance") or []]
            st.subheader("Phase assurance")
            st.dataframe(assurance_rows, use_container_width=True, hide_index=True)
            st.subheader("UI → API causal coverage")
            causal_rows = [{
                "phase": row.get("phase"),
                "actions": row.get("action_count"),
                "transactions": row.get("transaction_count"),
                "linked_transactions": row.get("linked_transaction_count"),
                "coverage": row.get("linked_transaction_coverage"),
            } for row in evidence.get("causal") or []]
            st.dataframe(causal_rows, use_container_width=True, hide_index=True)
            if evidence.get("causal"):
                chosen_phase = st.selectbox("Causal trace details", [row.get("phase") for row in evidence.get("causal") or []])
                chosen = next(row for row in evidence.get("causal") or [] if row.get("phase") == chosen_phase)
                st.json(chosen)

    with tabs[5]:
        if default_run is None:
            st.info("Evidence downloads will appear after a run starts.")
        else:
            selected_name = st.selectbox("Run", [path.name for path in run_paths], index=0, key="evidence_run")
            selected_run = next(path for path in run_paths if path.name == selected_name)
            st.code(str(selected_run))
            if st.button("Build evidence ZIP"):
                with st.spinner("Building redacted evidence archive..."):
                    archive = build_evidence_archive(selected_run)
                st.session_state["evidence_archive"] = str(archive)
            archive_path = Path(st.session_state.get("evidence_archive", "")) if st.session_state.get("evidence_archive") else None
            if archive_path and archive_path.is_file():
                st.download_button(
                    "Download evidence ZIP",
                    data=archive_path.read_bytes(),
                    file_name=archive_path.name,
                    mime="application/zip",
                )
            bundles = list(selected_run.glob("**/form_api_payload_response_bundle.json"))
            if bundles:
                chosen = st.selectbox("Payload/response bundle", [str(path.relative_to(selected_run)) for path in bundles])
                bundle_path = selected_run / chosen
                st.download_button(
                    "Download selected API bundle",
                    data=bundle_path.read_bytes(),
                    file_name=f"{selected_run.name}_{bundle_path.parent.parent.parent.name}_api_bundle.json",
                    mime="application/json",
                )

    with tabs[6]:
        log_path = process_state.get("log_path") or str(project_runtime_dir(project_root) / CONSOLE_LOG_FILE)
        st.caption(str(log_path))
        st.code(tail_text(log_path, 50000) or "No console output yet.", language="text")

    if auto_refresh and process_state.get("running"):
        components.html(
            "<script>setTimeout(function(){window.parent.location.reload();}, 5000);</script>",
            height=0,
        )


if __name__ == "__main__":
    main()
