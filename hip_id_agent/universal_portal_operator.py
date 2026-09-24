from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from .aia_client import AIAClient
from .browser_session import BrowserSession
from .capability_graph import HIPCapabilityGraph, classify_risk
from .config import AppConfig
from .future_task_agent import ACTION_ALIASES, MUTATION_CONFIRMATION
from .mlflow_async import AsyncMLflowTracker
from .models import utc_now
from .portal_discovery_flow import _action_label, _page_surface
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .semantic_affordance import canonical_intent
from .skill_induction import InducedSkillLibrary, SkillInductionEngine, build_form_blueprint
from .replay_policy import replay_policy_engine_from_config
from .model_portfolio import model_portfolio_from_config
from .recursive_self_improvement import recursive_improvement_from_config
from .trace_self_repair import trace_self_repair_from_config
from .deterministic_recipe import deterministic_recipe_from_config
from .human_teaching import human_teaching_from_config
from .stateful_form_runtime import capture_stateful_controls, execute_phase_state_graph


PLAN_SCHEMA = "hip.universal-portal-task-plan.v1"
EXEC_SCHEMA = "hip.universal-portal-task-execution.v1"
FORM_SCHEMA = "hip.universal-input-form-graph.v1"


@asynccontextmanager
async def _browser_context(config: AppConfig, run_dir: Path, browser_override: BrowserSession | None = None):
    """Borrow an existing authenticated browser without closing it.

    Persistent operator/convergence flows use this so retries, learning, and human
    intervention keep the same live HIP session. Ordinary callers still receive a
    normal managed BrowserSession.
    """
    if browser_override is not None:
        yield browser_override
        return
    async with BrowserSession(config, run_dir) as browser:
        yield browser


def _skill_library(config: AppConfig) -> InducedSkillLibrary:
    cfg = config.skill_induction
    root = Path(config.reporting.memory_dir) / str(config.brain.directory or "portal_brain") / str(cfg.memory_subdir or "induced_skills")
    return InducedSkillLibrary(
        root,
        min_verified_successes=int(cfg.min_verified_successes or 1),
        demote_after_failures=int(cfg.demote_after_failures or 2),
        min_replay_confidence=float(cfg.min_replay_confidence or 0.66),
        confidence_half_life_days=float(cfg.confidence_half_life_days or 45.0),
        stale_after_days=float(cfg.stale_after_days or 120.0),
        max_skills=int(cfg.max_skills or 500),
    )


def redact_form_graph_for_artifact(graph: Mapping[str, Any]) -> Dict[str, Any]:
    """Create a value-free persisted view of a dynamic form graph.

    Runtime values remain only in memory long enough to drive exact fill/readback.
    The persisted learning/evidence graph records field identity and value shape,
    never the customer value itself, selectors, XPath, or coordinates.
    """
    clean = json.loads(json.dumps(dict(graph), ensure_ascii=False, default=str))
    for node in clean.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        value = node.pop("expected_value", None)
        node["expected_value_redacted"] = True
        node["expected_value_type"] = type(value).__name__ if value is not None else "none"
        if isinstance(value, (list, tuple, set)):
            node["expected_value_item_count"] = len(value)
    clean["values_stored"] = False
    return mask_sensitive_data(clean)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _words(value: Any) -> set[str]:
    return {x for x in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(x) > 1}


def _humanize(value: str) -> str:
    return " ".join(x.capitalize() for x in re.split(r"[_\-\s]+", str(value or "").strip()) if x)


def _stable(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(x or "") for x in parts).encode("utf-8")).hexdigest()[:18]


def _nonblank(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return False
    return True


def _path_get(payload: Mapping[str, Any], path: str) -> Any:
    text = str(path or "").strip()
    if not text or text == "$":
        return payload
    text = re.sub(r"^\$\.?", "", text)
    cur: Any = payload
    for token in re.findall(r"[^.\[\]]+|\[\d+\]", text):
        if token.startswith("["):
            if not isinstance(cur, list):
                return None
            idx = int(token[1:-1])
            if idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, Mapping) or token not in cur:
                return None
            cur = cur[token]
    return cur


def flatten_nonblank_leaves(value: Any, *, root: str = "$") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                walk(child, f"{path}.{key}")
            return
        if isinstance(node, list):
            if node and all(not isinstance(x, (dict, list)) for x in node):
                clean = [x for x in node if _nonblank(x)]
                if clean:
                    out.append({"input_path": path, "field_key": path.rsplit(".", 1)[-1], "value": clean})
                return
            for idx, child in enumerate(node):
                walk(child, f"{path}[{idx}]")
            return
        if _nonblank(node):
            out.append({"input_path": path, "field_key": path.rsplit(".", 1)[-1], "value": node})

    walk(value, root)
    return out


def _scope_candidates(payload: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    objects = payload.get("objects") if isinstance(payload.get("objects"), Mapping) else None
    if objects:
        return [(f"$.objects.{key}", value) for key, value in objects.items() if isinstance(value, (Mapping, list))]
    return [("$", payload)]


def _control_tokens(control: Mapping[str, Any]) -> set[str]:
    values = [
        control.get("label"), control.get("name"), control.get("placeholder"),
        control.get("framework_key"), control.get("form_control_name"),
        control.get("semantic_key"), control.get("section"), control.get("row_text"),
    ]
    tokens: set[str] = set()
    for value in values:
        tokens |= _words(value)
        n = _norm(value)
        if n:
            tokens.add(n)
    return tokens


def select_input_scope(
    payload: Mapping[str, Any], *, task: str, controls: Sequence[Mapping[str, Any]] = (), input_root: str = ""
) -> Dict[str, Any]:
    if input_root:
        selected = _path_get(payload, input_root)
        if selected is None:
            return {"pass": False, "reason": "input_root_not_found", "input_root": input_root, "payload": None}
        return {"pass": True, "reason": "explicit_input_root", "input_root": input_root, "payload": selected}

    candidates = _scope_candidates(payload)
    if len(candidates) == 1:
        root, selected = candidates[0]
        return {"pass": True, "reason": "single_scope", "input_root": root, "payload": selected}

    task_tokens = _words(task)
    live_tokens: set[str] = set()
    for control in controls:
        live_tokens |= _control_tokens(control)

    ranked: List[Dict[str, Any]] = []
    for root, selected in candidates:
        key = root.rsplit(".", 1)[-1]
        key_tokens = _words(key) | {_norm(key)}
        leaves = flatten_nonblank_leaves(selected, root=root)
        leaf_tokens = set()
        for leaf in leaves:
            leaf_tokens |= _words(leaf.get("field_key"))
            leaf_tokens.add(_norm(leaf.get("field_key")))
        task_score = 25 * len({x for x in key_tokens if x and x in task_tokens})
        live_score = 4 * len({x for x in leaf_tokens if x and x in live_tokens})
        ranked.append({"input_root": root, "payload": selected, "score": task_score + live_score, "leaf_count": len(leaves)})
    ranked.sort(key=lambda x: (int(x["score"]), int(x["leaf_count"])), reverse=True)
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    if int(best["score"]) <= 0 or (second and int(best["score"]) == int(second["score"])):
        return {
            "pass": False, "reason": "ambiguous_input_scope", "input_root": "", "payload": None,
            "candidates": [{"input_root": x["input_root"], "score": x["score"], "leaf_count": x["leaf_count"]} for x in ranked[:10]],
        }
    return {"pass": True, "reason": "semantic_scope_match", **best}


def _row_index(path: str) -> Optional[int]:
    matches = re.findall(r"\[(\d+)\]", str(path or ""))
    return int(matches[-1]) if matches else None


def _infer_action(control: Mapping[str, Any], value: Any) -> str:
    typ = _norm(control.get("type")); role = _norm(control.get("role")); tag = _norm(control.get("tag")); component = _norm(control.get("component_tag"))
    if typ == "file" or "file" in component:
        return "upload_file"
    if isinstance(value, list) or str(control.get("selection_mode") or "").lower() == "multiple":
        return "select_multi"
    if typ == "radio" or role == "radio":
        return "select_radio"
    if typ == "checkbox" or role in {"checkbox", "switch"} or "switch" in component:
        return "toggle"
    if role in {"combobox", "listbox"} or tag == "select" or "dropdown" in component:
        return "select_single"
    return "fill_text"


def _leaf_score(leaf: Mapping[str, Any], control: Mapping[str, Any]) -> int:
    key = _norm(leaf.get("field_key")); human = _norm(_humanize(str(leaf.get("field_key") or "")))
    label = _norm(control.get("label")); name = _norm(control.get("name")); placeholder = _norm(control.get("placeholder")); framework = _norm(control.get("framework_key") or control.get("form_control_name")); semantic = _norm(control.get("semantic_key"))
    pool = [x for x in (label, name, placeholder, framework, semantic) if x]
    score = 0
    if key and key in pool: score += 150
    if human and human in pool: score += 135
    for token in pool:
        if key and (key in token or token in key): score += 45
        if human and (human in token or token in human): score += 30
    expected_idx = _row_index(str(leaf.get("input_path") or ""))
    actual_idx = control.get("row_index")
    if expected_idx is not None and actual_idx is not None:
        score += 85 if int(expected_idx) == int(actual_idx) else -90
    if control.get("interactable") is True: score += 10
    if control.get("disabled") or control.get("readonly"): score -= 40
    return score



def _skill_hint_score(leaf: Mapping[str, Any], control: Mapping[str, Any], skill_field: Mapping[str, Any] | None) -> int:
    if not isinstance(skill_field, Mapping):
        return 0
    loc = skill_field.get("semantic_locator") if isinstance(skill_field.get("semantic_locator"), Mapping) else {}
    control_tokens = _control_tokens(control)
    hint_tokens: set[str] = set()
    for key in ("names", "labels", "placeholders", "roles", "section_aliases"):
        for value in loc.get(key) or []:
            hint_tokens |= _words(value)
            n = _norm(value)
            if n:
                hint_tokens.add(n)
    score = 0
    if hint_tokens and control_tokens:
        overlap = len(hint_tokens & control_tokens)
        score += min(90, overlap * 18)
    expected_section = _norm(skill_field.get("section") or " ".join(loc.get("section_aliases") or []))
    actual_section = _norm(control.get("section"))
    if expected_section and actual_section:
        score += 35 if expected_section == actual_section else (12 if expected_section in actual_section or actual_section in expected_section else -8)
    expected_idx = skill_field.get("row_index")
    actual_idx = control.get("row_index")
    if expected_idx is not None and actual_idx is not None:
        score += 65 if int(expected_idx) == int(actual_idx) else -65
    expected_role = {_norm(x) for x in loc.get("roles") or [] if _norm(x)}
    actual_role = _norm(control.get("role") or control.get("type"))
    if expected_role and actual_role:
        score += 22 if actual_role in expected_role else -6
    return score

def compile_universal_form_graph(
    *, payload: Mapping[str, Any], task: str, controls: Sequence[Mapping[str, Any]], input_root: str = "",
    skill_blueprint: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    scope = select_input_scope(payload, task=task, controls=controls, input_root=input_root)
    if not scope.get("pass"):
        return {"schema_version": FORM_SCHEMA, "pass": False, "scope": mask_sensitive_data(scope), "nodes": [], "unresolved_input_leaves": []}
    root = str(scope.get("input_root") or "$")
    leaves = flatten_nonblank_leaves(scope.get("payload"), root=root)
    nodes: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    used_controls: set[str] = set()
    skill_fields = {
        str(row.get("input_path") or ""): row
        for row in ((skill_blueprint or {}).get("fields") or [])
        if isinstance(row, Mapping) and row.get("input_path")
    }
    skill_reused_nodes = 0

    for leaf in leaves:
        skill_field = skill_fields.get(str(leaf.get("input_path") or ""))
        ranked = sorted(
            [(_leaf_score(leaf, control) + _skill_hint_score(leaf, control, skill_field), dict(control)) for control in controls if isinstance(control, Mapping)],
            key=lambda x: x[0], reverse=True,
        )
        best_score, best = ranked[0] if ranked else (0, None)
        second_score = ranked[1][0] if len(ranked) > 1 else -999
        selector_id = str((best or {}).get("selector") or "")
        unique_enough = bool(best and best_score >= 80 and (best_score - second_score >= 16 or (len(ranked) > 1 and selector_id and selector_id == str(ranked[1][1].get("selector") or ""))))
        if not unique_enough or (selector_id and selector_id in used_controls):
            unresolved.append({
                "input_path": leaf.get("input_path"), "field_key": leaf.get("field_key"),
                "reason": "no_unique_live_control", "best_score": best_score,
                "score_margin": best_score - second_score,
                "candidate_labels": [str(row[1].get("label") or row[1].get("framework_key") or "") for row in ranked[:4]],
            })
            continue
        if selector_id:
            used_controls.add(selector_id)
        if skill_field is not None:
            skill_reused_nodes += 1
        row_idx = _row_index(str(leaf.get("input_path") or ""))
        labels = [x for x in [best.get("label"), _humanize(str(leaf.get("field_key") or ""))] if x]
        names = [x for x in [best.get("name"), best.get("framework_key"), best.get("form_control_name")] if x]
        node = {
            "node_id": f"universal_task.{_stable(leaf.get('input_path'), best.get('section'), best.get('label'), row_idx)}",
            "phase": "universal_task",
            "section": str(best.get("section") or ""),
            "field_key": str(leaf.get("field_key") or ""),
            "action": _infer_action(best, leaf.get("value")),
            "expected_value": leaf.get("value"),
            "input_path": str(leaf.get("input_path") or ""),
            "semantic_locator": {
                "names": names,
                "labels": labels,
                "placeholders": [str(best.get("placeholder") or "")] if best.get("placeholder") else [],
                "roles": [str(best.get("role") or "")] if best.get("role") else [],
                "section_aliases": [str(best.get("section") or "")] if best.get("section") else [],
                "row_kind": str(best.get("row_kind") or ""),
                "row_index": row_idx if row_idx is not None else best.get("row_index"),
            },
            "row_kind": str(best.get("row_kind") or ""),
            "row_index": row_idx if row_idx is not None else best.get("row_index"),
            "required": True,
            "depends_on": [],
            "verification": "exact_committed_control_value",
            "executor": "universal-live-semantic-state-transaction",
            "notes": "Dynamically compiled from runtime input.json + current foreground control semantics; selector/coordinates are not persisted",
        }
        nodes.append(node)

    return {
        "schema_version": FORM_SCHEMA,
        "pass": not unresolved and bool(leaves),
        "phase": "universal_task",
        "object_path": root,
        "object_family": "dynamic",
        "strategy": "runtime-input-ledger+live-semantic-binding+exact-readback",
        "nodes": nodes,
        "dependency_edges": [],
        "repeatable_rows": {},
        "input_accounting": [{"input_path": x.get("input_path"), "disposition": "live_semantic_node"} for x in leaves if x.get("input_path") not in {u.get("input_path") for u in unresolved}],
        "unresolved_input_leaves": unresolved,
        "runtime_input_leaf_count": len(leaves),
        "mapped_input_leaf_count": len(nodes),
        "skill_hint_field_count": len(skill_fields),
        "skill_reused_node_count": skill_reused_nodes,
        "skill_fast_replay_active": bool(skill_fields),
        "input_root": root,
        "values_stored": False,
    }


def derive_live_page_family(url: str, title: str = "", heading: str = "") -> str:
    try:
        path = [x for x in urlsplit(str(url or "")).path.split("/") if x]
    except Exception:
        path = []
    route = path[-1] if path else ""
    basis = heading or route or title or "portal_page"
    return _norm(basis) or "portal_page"


def infer_target_area(task: str) -> str:
    text = str(task or "")
    patterns = [
        r"(?:navigate|go|move)\s+to\s+(?:the\s+)?(.+?)(?:\s+(?:and|then|to)\s+|[,;.]|$)",
        r"(?:open|visit)\s+(?:the\s+)?(.+?\s+(?:page|section|tab|module|area))(?:\s+(?:and|then)\s+|[,;.]|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return m.group(1).strip(" .")[:180]
    return ""


def infer_entity(task: str) -> str:
    for pattern in (r'"([^"\n]{2,})"', r"'([^'\n]{2,})'"):
        m = re.search(pattern, str(task or ""))
        if m:
            return m.group(1).strip()
    m = re.search(r"(?:search|find|locate)\s+(?:for\s+)?(.+?)(?:\s+(?:and|then)\s+|[,;.]|$)", str(task or ""), flags=re.I)
    return m.group(1).strip(" .")[:300] if m else ""


UNIVERSAL_MUTATION_TERMS = {
    "create", "submit", "save", "update", "delete", "remove", "deploy", "migrate", "publish", "unpublish",
    "archive", "approve", "reject", "activate", "deactivate", "enable", "disable", "assign", "unassign",
    "promote", "release", "commit", "reset", "reprocess", "restore", "clone", "duplicate", "import", "apply",
}


def universal_risk(label: str) -> str:
    base = classify_risk(label)
    tokens = _words(label)
    if tokens & UNIVERSAL_MUTATION_TERMS:
        return "mutation"
    return base


def ordered_actions(task: str) -> List[str]:
    text = str(task or "").lower()
    aliases = dict(ACTION_ALIASES)
    aliases.update({
        "approve": {"approve"}, "activate": {"activate"}, "deactivate": {"deactivate"},
        "publish": {"publish"}, "refresh": {"refresh"}, "filter": {"filter"},
        "settings": {"settings", "configure settings"}, "upload": {"upload"},
    })
    found: List[Tuple[int, int, str]] = []
    for order, (action, names) in enumerate(aliases.items()):
        positions = [text.find(name) for name in names if name and text.find(name) >= 0]
        if positions:
            found.append((min(positions), order, action))
    found.sort()
    return list(dict.fromkeys(action for _, _, action in found))


class UniversalPortalTaskPlanner:
    """Plan user-directed tasks without restricting the portal to five hardcoded pages."""

    def __init__(self, config: AppConfig, graph: HIPCapabilityGraph):
        self.config = config
        self.graph = graph
        self.skills = _skill_library(config)
        self.skill_engine = SkillInductionEngine(
            self.skills,
            min_match_score=float(getattr(config.skill_induction, "min_match_score", 0.45) or 0.45),
        )
        self.replay_policy = replay_policy_engine_from_config(config)
        self.model_portfolio = model_portfolio_from_config(config)
        self.deterministic_recipes = deterministic_recipe_from_config(config)
        try:
            self.replay_policy.ingest_old_runs(config.reporting.runs_dir)
            self.model_portfolio.dream(reason="planner_start")
        except Exception:
            pass

    def _instantiate_policy_workflow(
        self, workflow: Sequence[Mapping[str, Any]], *, task: str, input_json: str,
        input_root: str, start_url: str, entity: str,
    ) -> List[Dict[str, Any]]:
        """Instantiate a value-free replay-policy workflow with current task data."""
        rows: List[Dict[str, Any]] = []
        has_input = bool(str(input_json or "").strip())
        for raw in workflow or []:
            if not isinstance(raw, Mapping):
                continue
            typ = str(raw.get("type") or "")
            if typ == "navigate":
                rows.append({"type": "navigate", "target": start_url or self.config.portal.base_url, "risk": "read", "policy_replay": True})
            elif typ == "fill_from_input" and has_input:
                rows.append({"type": "fill_from_input", "input_json": input_json, "input_root": input_root or str(raw.get("input_root") or ""), "risk": "draft", "policy_replay": True})
            elif typ == "search" and entity:
                rows.append({"type": "search", "value": entity, "risk": "read", "policy_replay": True})
            elif typ in {"learn_surface", "navigate_label"}:
                row = dict(raw); row["policy_replay"] = True; row["memory_is_advisory"] = True
                rows.append(row)
            elif typ == "semantic_action":
                row = dict(raw)
                if not SkillInductionEngine._explicit_mutation_allowed(task, row, has_runtime_input=has_input):
                    continue
                row["policy_replay"] = True; row["memory_is_advisory"] = True
                rows.append(row)
        return [mask_sensitive_data(x) for x in rows]

    def _remembered_explicit_actions(self, task: str) -> List[Dict[str, Any]]:
        """Return learned action labels explicitly named by the user.

        Memory is only a planning prior: execution still has to locate/re-prove the
        capability on the current live page before any click can occur.
        """
        task_norm = _norm(task)
        task_words = _words(task)
        rows: List[Dict[str, Any]] = []
        try:
            caps = self.graph.query_capabilities()
        except Exception:
            caps = []
        for cap in caps:
            if str(cap.get("kind") or "") not in {"action", "row_action"}:
                continue
            label = str(cap.get("label") or "").strip()
            if not label:
                continue
            label_norm = _norm(label)
            label_words = _words(label)
            explicit = bool(label_norm and (label_norm in task_norm or (label_words and label_words <= task_words)))
            if not explicit:
                continue
            rows.append({
                "label": label,
                "risk": universal_risk(label if not cap.get("risk") else f"{label} {cap.get('risk') or ''}"),
                "capability_id": str(cap.get("capability_id") or ""),
                "observations": int(cap.get("observations") or 0),
                "successful_observations": int(cap.get("successful_observations") or cap.get("success_observations") or 0),
            })
        rows.sort(key=lambda r: (-r["successful_observations"], -r["observations"], -len(r["label"])))
        dedup: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            key = _norm(row["label"])
            if key in seen:
                continue
            seen.add(key); dedup.append(row)
        return dedup[:12]

    def plan(
        self, task: str, *, input_json: str = "", input_root: str = "", start_url: str = "", deep_learn: bool = True
    ) -> Dict[str, Any]:
        actions = ordered_actions(task)
        remembered_actions = self._remembered_explicit_actions(task)
        target_area = infer_target_area(task)
        entity = infer_entity(task)
        has_input = bool(str(input_json or "").strip())
        fill_requested = has_input and bool(
            re.search(r"\b(fill|create|edit|update|configure|input\s*json|form)\b", str(task or ""), flags=re.I)
        )
        planning_model_trace: Dict[str, Any] = {}
        if bool(getattr(self.config.aia, "enabled", False)) and bool(getattr(self.config.model_portfolio, "enabled", True)):
            try:
                planning_model_trace = self.model_portfolio.tournament_text(
                    system="Classify the requested portal task only. Return strict JSON with task_family, confidence, and whether live discovery is advisable. Do not invent values or browser actions.",
                    task=json.dumps({
                        "task": task, "detected_actions": actions, "target_area": target_area,
                        "entity_present": bool(entity), "runtime_input_present": has_input,
                    }, ensure_ascii=False),
                    role="planning", expected_json=True, require_keys=["task_family"],
                    exploration=not bool(actions and target_area),
                    learning=bool(deep_learn),
                    complex_task=bool(fill_requested or len(actions) >= 2 or deep_learn),
                    force_multi_model=bool(deep_learn or fill_requested or len(actions) >= 2),
                )
            except Exception:
                planning_model_trace = {}
        steps: List[Dict[str, Any]] = [
            {"type": "navigate", "target": start_url or self.config.portal.base_url, "risk": "read"},
            {"type": "learn_surface", "deep": bool(deep_learn), "risk": "read"},
        ]
        if target_area:
            steps += [
                {"type": "navigate_label", "label": target_area, "risk": "read"},
                {"type": "learn_surface", "deep": bool(deep_learn), "risk": "read"},
            ]
        if entity and any(a in actions for a in ("view", "edit", "clone", "deploy", "delete", "migrate", "history", "audit")):
            steps.append({"type": "search", "value": entity, "risk": "read"})

        create_requested = "create" in actions
        if create_requested:
            steps.append({"type": "semantic_action", "action": "open_add_form", "label": "Add / Create", "risk": "read"})
            if fill_requested:
                steps.append({"type": "fill_from_input", "input_json": input_json, "input_root": input_root, "risk": "draft"})
            # User asking to create means commit only after the form has been verified.
            steps.append({"type": "semantic_action", "action": "create", "label": "Create / Submit", "risk": "mutation"})
        else:
            for action in actions:
                if action in {"create", "save"}:
                    continue
                risk = universal_risk(action)
                steps.append({"type": "semantic_action", "action": action, "label": action.replace("_", " ").title(), "risk": risk, "entity": entity})
                if action == "edit" and fill_requested:
                    steps.append({"type": "fill_from_input", "input_json": input_json, "input_root": input_root, "risk": "draft"})
            if fill_requested and "edit" not in actions:
                steps.append({"type": "fill_from_input", "input_json": input_json, "input_root": input_root, "risk": "draft"})
            if fill_requested and ("edit" in actions or re.search(r"\b(update|save|apply)\b", task, flags=re.I)):
                steps.append({"type": "semantic_action", "action": "save", "label": "Save / Update", "risk": "mutation"})

        # Learned arbitrary capabilities explicitly named in the request are allowed
        # into the plan, but only as semantic priors. The executor must re-locate
        # the exact action on the current live page before it can act.
        existing_labels = {_norm(str(s.get("label") or s.get("action") or "")) for s in steps}
        for remembered in remembered_actions:
            if _norm(remembered["label"]) in existing_labels:
                continue
            steps.append({
                "type": "semantic_action", "action": remembered["label"], "label": remembered["label"],
                "risk": remembered["risk"], "memory_capability_id": remembered["capability_id"],
                "memory_is_advisory": True,
            })
            existing_labels.add(_norm(remembered["label"]))

        # If deterministic parsing cannot extract an action, keep a guarded live-goal
        # step. It may only choose from actions visible on the current page.
        meaningful = [s for s in steps if s["type"] not in {"navigate", "learn_surface"}]
        if not meaningful:
            steps.append({"type": "live_goal", "goal": task, "risk": universal_risk(task)})

        generic_steps = [dict(x) for x in steps]

        policy_decision = self.replay_policy.decide(
            task=task, actions=actions, target_area=target_area, input_root=input_root,
        ) if bool(getattr(self.config.replay_policy, "enabled", True)) else {
            "mode": "exploration", "reason": "replay_policy_disabled", "workflow": []
        }

        recipe_activation: Dict[str, Any] = {"active": False, "reason": "deterministic_recipe_disabled"}
        recipe_match: Dict[str, Any] = {}
        if bool(getattr(self.config.deterministic_recipe, "enabled", True)):
            recipe_activation = self.deterministic_recipes.match(task=task, actions=actions, target_area=target_area, input_root=input_root)
            if recipe_activation.get("active") and isinstance(recipe_activation.get("recipe"), Mapping):
                recipe_match = dict(recipe_activation.get("recipe") or {})
                recipe_steps = self._instantiate_policy_workflow(
                    recipe_match.get("steps") or [], task=task, input_json=input_json, input_root=input_root,
                    start_url=start_url or self.config.portal.base_url, entity=entity,
                )
                if recipe_steps:
                    steps = recipe_steps

        mutation_actions = [str(s.get("action") or s.get("label") or "") for s in steps if s.get("risk") == "mutation"]
        # If the request explicitly names a generic mutation verb not yet present
        # in learned memory (for example Archive/Approve), require authorization
        # up front. The live-goal executor may use it only when the currently
        # visible action is an explicit semantic match for that user wording.
        task_words = _words(task)
        for term in sorted(UNIVERSAL_MUTATION_TERMS & task_words):
            label = _humanize(term)
            if _norm(label) not in {_norm(x) for x in mutation_actions}:
                mutation_actions.append(label)

        skill_matches: List[Dict[str, Any]] = []
        skill_match: Dict[str, Any] = {}
        skill_activation: Dict[str, Any] = {"active": False, "reason": "skill_induction_disabled"}
        allow_fast_replay = (str(policy_decision.get("mode") or "exploration") in {"hybrid", "exploitation"} or str(policy_decision.get("reason") or "") == "no_replay_policy")
        if allow_fast_replay and not recipe_match and bool(getattr(self.config.skill_induction, "enabled", True)) and bool(getattr(self.config.skill_induction, "fast_replay_enabled", True)):
            skill_activation = self.skill_engine.activate(
                task=task,
                actions=actions,
                target_area=target_area,
                input_root=input_root,
                input_json=input_json,
                start_url=start_url or self.config.portal.base_url,
                entity=entity,
            )
            skill_matches = list(skill_activation.get("matches") or [])
            if skill_activation.get("active") and isinstance(skill_activation.get("skill"), Mapping):
                skill_match = dict(skill_activation.get("skill") or {})
                # Reuse the successful workflow itself, not only its form-field
                # hints. Current values and every action are still resolved live.
                activated_steps = [dict(x) for x in skill_activation.get("steps") or [] if isinstance(x, Mapping)]
                if activated_steps:
                    steps = activated_steps

        # A replay policy can be useful before a formal skill has been promoted.
        # Reuse only value-free workflow structure and rebind every action live.
        policy_workflow_active = False
        if allow_fast_replay and not recipe_match and not skill_match and policy_decision.get("workflow"):
            replay_steps = self._instantiate_policy_workflow(
                policy_decision.get("workflow") or [], task=task, input_json=input_json,
                input_root=input_root, start_url=start_url or self.config.portal.base_url, entity=entity,
            )
            if replay_steps:
                steps = replay_steps
                policy_workflow_active = True

        # Recalculate mutation scope after skill activation so a stored skill can
        # never broaden permissions beyond the current user request.
        mutation_actions = [str(s.get("action") or s.get("label") or "") for s in steps if s.get("risk") == "mutation"]
        task_words = _words(task)
        for term in sorted(UNIVERSAL_MUTATION_TERMS & task_words):
            label = _humanize(term)
            if _norm(label) not in {_norm(x) for x in mutation_actions}:
                mutation_actions.append(label)
        return mask_sensitive_data({
            "schema_version": PLAN_SCHEMA, "pass": True, "task": task,
            "start_url": start_url or self.config.portal.base_url, "target_area": target_area,
            "entity": entity, "input_json": input_json, "input_root": input_root,
            "steps": steps, "mutation_required": bool(mutation_actions),
            "mutation_actions": mutation_actions, "deep_learning_enabled": bool(deep_learn),
            "planner": "universal_live_capability_graph+runtime_input_ledger+guarded_semantic_react+trace_self_repair+human_teaching+deterministic_recipe+skill_induction+replay_policy",
            "execution_mode": (
                "deterministic_recipe_with_live_reproof" if recipe_match else
                "induced_skill_fast_replay_with_live_reproof" if skill_match else
                "replay_policy_fast_path_with_live_reproof" if policy_workflow_active else
                "adaptive_live_discovery"
            ),
            "deterministic_recipe": {"active": bool(recipe_match), "reason": recipe_activation.get("reason"), "match_score": recipe_activation.get("match_score"), "recipe_id": recipe_match.get("recipe_id") if recipe_match else "", "live_reproof_required": True, "values_reused": False},
            "replay_policy": policy_decision,
            "model_routing_advisory": planning_model_trace,
            "skill_match": skill_match,
            "skill_activation": {
                "active": bool(skill_activation.get("active")),
                "reason": skill_activation.get("reason"),
                "skill_id": skill_match.get("skill_id") if skill_match else "",
                "skill_name": skill_match.get("name") if skill_match else "",
                "step_count": len(skill_activation.get("steps") or []),
                "live_reproof_required": True,
                "values_reused": False,
            },
            "adaptive_fallback_steps": generic_steps,
            "skill_match_candidates": [
                {"skill_id": x.get("skill_id"), "name": x.get("name"), "match_score": x.get("match_score"), "confidence": x.get("confidence"), "status": x.get("status")}
                for x in skill_matches[:5]
            ],
            "skill_library_manifest": self.skills.manifest(),
            "memory_is_advisory": True, "live_reproof_required": True, "values_stored": False,
        })


class UniversalPortalTaskExecutor:
    def __init__(self, config: AppConfig, graph: HIPCapabilityGraph):
        self.config = config
        self.graph = graph
        self.skills = _skill_library(config)
        self.skill_engine = SkillInductionEngine(
            self.skills,
            min_match_score=float(getattr(config.skill_induction, "min_match_score", 0.45) or 0.45),
        )
        self.replay_policy = replay_policy_engine_from_config(config)
        self.model_portfolio = model_portfolio_from_config(config)
        self.deterministic_recipes = deterministic_recipe_from_config(config)
        self.human_teaching = human_teaching_from_config(config)
        self.trace_self_repair = trace_self_repair_from_config(config, model_portfolio=self.model_portfolio)
        self.recursive_improvement = recursive_improvement_from_config(
            config, replay_policy=self.replay_policy, model_portfolio=self.model_portfolio, skill_library=self.skills
        )
        try:
            self.replay_policy.ingest_old_runs(config.reporting.runs_dir)
            self.model_portfolio.dream(reason="executor_start")
        except Exception:
            pass

    @staticmethod
    def _skill_step_from_execution(source: Mapping[str, Any], result: Mapping[str, Any]) -> Dict[str, Any]:
        """Return the reusable/value-free step that actually succeeded."""
        typ = str(source.get("type") or "")
        if typ == "live_goal":
            actions = result.get("actions") if isinstance(result.get("actions"), list) else []
            if actions:
                # The caller expands multi-action adaptive goals separately.
                return {"type": "adaptive_goal", "risk": "read"}
        row: Dict[str, Any] = {
            "type": typ,
            "action": str(result.get("action") or source.get("action") or ""),
            "label": str(result.get("label") or source.get("label") or ""),
            "risk": str(result.get("risk") or source.get("risk") or ""),
        }
        if typ == "navigate_label":
            row["label"] = str(source.get("label") or result.get("label") or "")
        elif typ == "fill_from_input":
            row["input_root"] = str(result.get("input_root") or source.get("input_root") or "")
        elif typ == "navigate":
            row["target_source"] = "configured_portal_base_or_current_task_start_url"
        elif typ == "search":
            row["value_source"] = "current_task.entity"
        return mask_sensitive_data(row)

    def _mutation_gate(self, plan: Mapping[str, Any], *, allow_portal_mutation: bool, confirmation: str) -> Dict[str, Any]:
        required = bool(plan.get("mutation_required"))
        if not required:
            return {"pass": True, "mutation_required": False, "allowed_labels": []}
        env_ok = str(os.getenv("HIP_ALLOW_PORTAL_MUTATION", "")).strip().upper() == "YES"
        phrase_ok = str(confirmation or "").strip() == MUTATION_CONFIRMATION
        passed = bool(allow_portal_mutation and env_ok and phrase_ok)
        return {
            "pass": passed, "mutation_required": True, "explicit_flag": bool(allow_portal_mutation),
            "environment_gate": env_ok, "confirmation_gate": phrase_ok,
            "allowed_labels": list(plan.get("mutation_actions") or []), "required_confirmation": MUTATION_CONFIRMATION,
        }

    async def _learn_surface(self, browser: BrowserSession, *, task_id: str, deep: bool = False) -> Dict[str, Any]:
        surface = await _page_surface(browser.page)
        title = ""
        try:
            title = await browser.page.title()
        except Exception:
            pass
        heading = ""
        try:
            h = browser.page.locator("h1:visible,h2:visible,[role=heading]:visible").first
            if await h.count():
                heading = (await h.inner_text()).strip()
        except Exception:
            pass
        family = derive_live_page_family(str(surface.get("url") or browser.page.url), title, heading)
        page_row = self.graph.observe_page(page_family=family, url=str(surface.get("url") or browser.page.url), title=title, evidence={"task_id": task_id, "dynamic_page_family": True})
        learned: List[Dict[str, Any]] = []
        for action in surface.get("actions") or []:
            if not isinstance(action, Mapping):
                continue
            label = _action_label(action)
            if not label:
                continue
            kind = "row_action" if action.get("scope") else "action"
            cap = self.graph.observe_capability(
                page_family=family, kind=kind, label=label,
                selector="", role=str(action.get("role") or ""),
                scope=str(action.get("scope") or ""), risk=universal_risk(label), run_id=task_id,
                evidence={"url": surface.get("url"), "dynamic_learning": True},
            )
            learned.append({"capability_id": cap.get("capability_id"), "label": label, "risk": cap.get("risk")})
        try:
            controls = await capture_stateful_controls(browser.page, "universal_task")
        except Exception:
            controls = []
        for control in controls:
            label = str(control.get("label") or control.get("framework_key") or control.get("placeholder") or "form field")
            self.graph.observe_capability(
                page_family=family, kind="form_field", label=label,
                role=str(control.get("role") or control.get("type") or ""),
                placeholder=str(control.get("placeholder") or ""), scope=str(control.get("section") or ""),
                risk="draft", run_id=task_id,
                evidence={"url": surface.get("url"), "dynamic_learning": True, "values_stored": False},
            )
        self.graph.save()
        return mask_sensitive_data({
            "pass": True, "page_family": family, "url": surface.get("url") or browser.page.url,
            "title": title, "heading": heading, "action_count": len(learned), "control_count": len(controls),
            "learned_actions": learned[:100], "deep_requested": bool(deep), "values_stored": False,
        })

    async def _live_action_locator(self, browser: BrowserSession, label: str, *, entity: str = "") -> Tuple[Any, str]:
        page = browser.page
        text = str(label or "").strip()
        # Prefer entity-row scope where possible.
        if entity:
            rows = page.locator('tr,[role="row"],.dds__table__row,.dds__card,[class*="card"],[class*="row"]')
            try:
                for idx in range(min(await rows.count(), 500)):
                    row = rows.nth(idx)
                    try:
                        row_text = (await row.inner_text()).strip()
                    except Exception:
                        continue
                    if entity.lower() not in row_text.lower():
                        continue
                    for role in ("button", "link", "menuitem", "tab"):
                        loc = row.get_by_role(role, name=re.compile(re.escape(text), re.I)).first
                        if await loc.count() and await loc.is_visible():
                            return loc, f"row({entity}) role={role} name={text}"
            except Exception:
                pass
        for role in ("button", "link", "menuitem", "tab", "option"):
            try:
                loc = page.get_by_role(role, name=re.compile(re.escape(text), re.I)).first
                if await loc.count() and await loc.is_visible():
                    return loc, f"role={role} name={text}"
            except Exception:
                pass
        try:
            loc = page.get_by_text(re.compile(r"^\s*" + re.escape(text) + r"\s*$", re.I)).first
            if await loc.count() and await loc.is_visible():
                return loc, f"text={text}"
        except Exception:
            pass
        raise RuntimeError(f"Universal live action not uniquely located: {text}")

    async def _navigate_label(self, browser: BrowserSession, label: str) -> Dict[str, Any]:
        before = browser.page.url
        loc, selector = await self._live_action_locator(browser, label)
        await browser.click_and_wait(action=f"navigate to {label}", locator=loc, selector=selector, mutation_risk=False)
        await browser.wait_ready()
        return {"pass": True, "label": label, "before_url": before, "after_url": browser.page.url}

    async def _search(self, browser: BrowserSession, value: str) -> Dict[str, Any]:
        page = browser.page
        selectors = [
            'input[type="search"]', 'input[placeholder*="search" i]', '[role="searchbox"]',
            'input[aria-label*="search" i]', 'input[name*="search" i]',
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if await loc.count() and await loc.is_visible():
                    await browser.fill_and_log(locator=loc, value=value, selector=selector, action_type="universal_search")
                    try: await browser.press_and_log(locator=loc, key="Enter", selector=selector)
                    except Exception: pass
                    await asyncio.sleep(0.4)
                    return {"pass": True, "value_present": bool(value), "selector_kind": "live_search_control"}
            except Exception:
                continue
        raise RuntimeError("Universal task could not find a visible Search control")

    async def _semantic_action(self, browser: BrowserSession, step: Mapping[str, Any], *, gate: Mapping[str, Any], entity: str) -> Dict[str, Any]:
        action = str(step.get("action") or step.get("label") or "")
        risk = str(step.get("risk") or universal_risk(action))
        aliases = [x for x in [str(step.get("label") or ""), action, entity] if x]
        if risk == "mutation":
            allowed = {_norm(x) for x in (gate.get("allowed_labels") or []) if str(x or "").strip()}
            candidates = {_norm(action), _norm(step.get("label"))}
            if not gate.get("pass") or not gate.get("mutation_required") or not (allowed & candidates):
                raise RuntimeError(f"Universal mutation not explicitly authorized for live action: {step.get('label') or action}")
        canonical = canonical_intent(action)
        # Known semantic intents get the full foreground/compound-menu/effect gate.
        known = canonical in {
            "open_add_form", "create", "edit", "save", "validate", "clone", "migrate", "deploy", "delete",
            "download", "upload", "expand", "collapse", "more_actions", "next", "back", "close", "filter",
            "settings", "retry", "refresh", "add_row",
        }
        if known:
            result = await browser.click_semantic_affordance(
                intent=canonical, aliases=aliases, allow_mutation=(risk == "mutation"),
                action_label=str(step.get("label") or action), allow_compound_menu=True,
            )
            return {"pass": True, "action": action, "intent": canonical, "risk": risk, "semantic": True, "effect": mask_sensitive_data(result)}
        # Unknown future portal capability: only execute a live label that exists now.
        loc, selector = await self._live_action_locator(browser, str(step.get("label") or action), entity=entity)
        await browser.click_and_wait(
            action=str(step.get("label") or action), locator=loc, selector=selector,
            mutation_risk=(risk == "mutation"),
        )
        return {"pass": True, "action": action, "risk": risk, "semantic": False, "binding": "live_label_only"}

    async def _capture_mutation_evidence(
        self, browser: BrowserSession, *, run_dir: Path, step_index: int, stage: str, step: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Capture local before/after evidence for a mutation without persisting input values into policy memory."""
        if not bool(getattr(getattr(self.config, "production_e2e", None), "capture_mutation_before_after_evidence", True)):
            return {"captured": False, "reason": "disabled"}
        evidence_dir = Path(run_dir) / "mutation_evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"step_{int(step_index):03d}_{stage}"
        shot = evidence_dir / f"{prefix}.png"
        surface_path = evidence_dir / f"{prefix}_surface.json"
        screenshot_path = ""
        try:
            screenshot_path = await browser.screenshot(shot, full_page=False)
        except Exception:
            screenshot_path = ""
        try:
            surface = await _page_surface(browser.page)
        except Exception:
            surface = {"url": str(getattr(browser.page, "url", ""))}
        safe_write_json(surface_path, mask_sensitive_data({
            "schema_version": "hip.mutation-evidence.v1", "stage": stage, "step_index": int(step_index),
            "action": str(step.get("action") or step.get("label") or ""), "risk": str(step.get("risk") or ""),
            "surface": surface, "values_stored": False,
        }))
        return {"captured": True, "screenshot": str(screenshot_path or shot), "surface": str(surface_path)}

    async def _fill_from_input(
        self, browser: BrowserSession, *, task: str, input_json: str, input_root: str,
        run_dir: Path, max_cycles: int = 6, skill: Mapping[str, Any] | None = None, task_id: str = "",
    ) -> Dict[str, Any]:
        path = Path(input_json)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_file():
            raise RuntimeError(f"Universal task input JSON not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise RuntimeError("Universal task input JSON must contain an object at the root")
        out_dir = run_dir / "universal_form_fill"
        out_dir.mkdir(parents=True, exist_ok=True)
        cycles: List[Dict[str, Any]] = []
        last_unresolved: List[str] = []
        skill_blueprint: Dict[str, Any] = {}
        if bool(getattr(self.config.skill_induction, "enabled", True)) and bool(getattr(self.config.skill_induction, "prefer_skill_semantics_for_form_binding", True)):
            skill_blueprint = self.skills.form_blueprint_for(skill, input_root=input_root)
        human_blueprint = self.human_teaching.blueprint(task=task, input_root=input_root) if bool(getattr(self.config.human_in_the_loop, "enabled", True)) else {}
        if human_blueprint.get("fields"):
            merged = {str(x.get("input_path") or ""): dict(x) for x in (skill_blueprint.get("fields") or []) if isinstance(x, Mapping)}
            for taught in human_blueprint.get("fields") or []:
                if isinstance(taught, Mapping) and taught.get("input_path"):
                    merged[str(taught.get("input_path"))] = dict(taught)
            skill_blueprint = {"schema_version": "hip.combined-human-skill-blueprint.v1", "input_root": input_root, "fields": list(merged.values()), "human_taught_field_count": len(human_blueprint.get("fields") or []), "values_stored": False, "selectors_stored": False, "coordinates_stored": False, "live_reproof_required": True}

        for cycle in range(1, max(1, int(max_cycles)) + 1):
            controls = await capture_stateful_controls(browser.page, "universal_task")
            graph = compile_universal_form_graph(payload=payload, task=task, controls=controls, input_root=input_root, skill_blueprint=skill_blueprint)
            safe_write_json(out_dir / f"cycle_{cycle:02d}_graph.json", redact_form_graph_for_artifact(graph))
            unresolved = [str(x.get("input_path") or "") for x in graph.get("unresolved_input_leaves") or []]
            execution: Dict[str, Any] = {"pass": False, "reason": "no_mapped_nodes"}
            if graph.get("nodes"):
                execution = await execute_phase_state_graph(
                    browser.page, graph, phase="universal_task", section=None,
                    max_retries=1, repair=True, strict_live_execution=True,
                )
            post_controls = await capture_stateful_controls(browser.page, "universal_task")
            post_graph = compile_universal_form_graph(payload=payload, task=task, controls=post_controls, input_root=input_root, skill_blueprint=skill_blueprint)
            post_unresolved = [str(x.get("input_path") or "") for x in post_graph.get("unresolved_input_leaves") or []]
            stage = execution.get("execution_stage_audit") if isinstance(execution.get("execution_stage_audit"), Mapping) else {}
            exact = bool(execution.get("pass") and stage.get("exact_execution_verified") is True and stage.get("authoritative_execution_verified") is True)
            row = {
                "cycle": cycle, "mapped": len(post_graph.get("nodes") or []), "unresolved": post_unresolved,
                "execution_pass": bool(execution.get("pass")), "exact_execution_verified": exact,
                "input_root": post_graph.get("input_root"),
                "skill_fast_replay_active": bool(post_graph.get("skill_fast_replay_active")),
                "skill_reused_node_count": int(post_graph.get("skill_reused_node_count") or 0),
            }
            cycles.append(mask_sensitive_data(row))
            if not post_unresolved and exact:
                result = {
                    "schema_version": "hip.universal-form-fill.v1", "pass": True, "status": "complete",
                    "input_root": post_graph.get("input_root"), "input_leaf_count": post_graph.get("runtime_input_leaf_count"),
                    "mapped_input_leaf_count": post_graph.get("mapped_input_leaf_count"), "cycles": cycles,
                    "skill_fast_replay_active": bool(post_graph.get("skill_fast_replay_active")),
                    "skill_reused_node_count": int(post_graph.get("skill_reused_node_count") or 0),
                    "skill_blueprint": build_form_blueprint(post_graph),
                    "values_stored": False, "verification": "100_percent_runtime_input_exact_readback",
                }
                safe_write_json(out_dir / "universal_form_fill.json", result)
                return result
            # A parent selection may have revealed a new field; allow another cycle.
            if post_unresolved == last_unresolved and not execution.get("pass"):
                # One conservative generic row-expansion attempt is allowed only
                # when the foreground surface exposes an Add-row affordance.
                try:
                    resolution = await browser.resolve_semantic_affordance(intent="add_row", aliases=["Add", "row", "condition", "action", "attribute", "step"], allow_mutation=False)
                    if resolution.get("resolved") and resolution.get("alias_hits"):
                        await browser.click_semantic_affordance(intent="add_row", aliases=["Add", "row", "condition", "action", "attribute", "step"], allow_mutation=False, action_label="Add input-driven row", allow_compound_menu=False)
                        await asyncio.sleep(0.25)
                except Exception:
                    pass
            last_unresolved = post_unresolved

        assistance: Dict[str, Any] = {}
        if last_unresolved and bool(getattr(self.config.human_in_the_loop, "enabled", True)) and bool(getattr(self.config.human_in_the_loop, "create_assistance_request_on_unresolved", True)):
            try:
                controls = await capture_stateful_controls(browser.page, "universal_task")
                assistance = self.human_teaching.create_request(run_id=task_id or run_dir.name, task=task, input_root=input_root, unresolved_input_paths=last_unresolved, controls=controls, reason="Automatic semantic binding could not uniquely map every runtime input leaf")
            except Exception as exc:
                assistance = {"status": "error", "error": mask_sensitive_string(str(exc))[:500]}
        result = {
            "schema_version": "hip.universal-form-fill.v1", "pass": False, "status": "needs_human_assistance" if assistance.get("request_id") else "incomplete",
            "cycles": cycles, "unresolved_input_leaves": last_unresolved,
            "reason": "Not every applicable nonblank runtime input leaf could be uniquely bound and exact-readback verified",
            "human_assistance": assistance, "values_stored": False,
        }
        safe_write_json(out_dir / "universal_form_fill.json", result)
        return result

    async def _execute_live_goal(
        self, browser: BrowserSession, *, goal: str, task_id: str, gate: Mapping[str, Any],
        excluded_labels: Sequence[str] = (),
    ) -> Dict[str, Any]:
        surface = await _page_surface(browser.page)
        candidates = []
        goal_tokens = _words(goal)
        excluded = {_norm(x) for x in excluded_labels if _norm(x)}
        for action in surface.get("actions") or []:
            if not isinstance(action, Mapping):
                continue
            label = _action_label(action)
            if not label:
                continue
            if _norm(label) in excluded:
                continue
            overlap = len(goal_tokens & _words(label))
            if overlap:
                candidates.append({"label": label, "score": overlap, "risk": universal_risk(label)})
        candidates.sort(key=lambda x: x["score"], reverse=True)
        if not candidates:
            raise RuntimeError("No currently visible action semantically overlaps the user request")
        top = candidates[0]
        if len(candidates) > 1 and candidates[1]["score"] == top["score"]:
            model_trace = {}
            if bool(getattr(self.config.aia, "enabled", False)):
                try:
                    decision = self.model_portfolio.tournament_text(
                        system="Choose exactly one currently visible portal action for the user's request. Return strict JSON with candidate_index and confidence. Never invent actions.",
                        task=json.dumps({"goal": goal, "candidates": candidates[:20]}, ensure_ascii=False),
                        role="action_selection", expected_json=True, require_keys=["candidate_index"],
                        exploration=False, complex_task=True, force_multi_model=True,
                    )
                    answer = decision.get("parsed") if decision.get("used") else {}
                    idx = int((answer or {}).get("candidate_index", -1))
                    if 0 <= idx < min(20, len(candidates)):
                        top = candidates[idx]
                        model_trace = decision
                except Exception:
                    model_trace = {}
            if len(candidates) > 1 and candidates[1]["score"] == top["score"]:
                raise RuntimeError("Ambiguous live goal: multiple visible actions match equally")
        if top["risk"] == "mutation":
            label_words = _words(top["label"]); goal_words = _words(goal)
            explicit = bool(_norm(top["label"]) in _norm(goal) or (label_words and label_words <= goal_words) or (label_words & UNIVERSAL_MUTATION_TERMS & goal_words))
            if not explicit:
                raise RuntimeError("A mutation action discovered from the live page was not explicitly named by the user")
            # Convert the already-authorized generic verb into the exact live label
            # for the final executor gate without broadening the permission scope.
            gate = dict(gate)
            allowed = list(gate.get("allowed_labels") or [])
            if any(_norm(x) in goal_words or (_words(x) & label_words & UNIVERSAL_MUTATION_TERMS) for x in allowed):
                gate["allowed_labels"] = list(dict.fromkeys(allowed + [top["label"]]))
                browser.set_portal_mutation_authorization(enabled=True, allowed_labels=gate["allowed_labels"], task_id=task_id)
        executed = await self._semantic_action(browser, {"action": top["label"], "label": top["label"], "risk": top["risk"]}, gate=gate, entity="")
        if 'model_trace' in locals() and model_trace:
            executed["model_routing"] = model_trace
        return executed

    async def _execute_adaptive_goal(
        self, browser: BrowserSession, *, goal: str, task_id: str, gate: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Execute a previously unknown user request using only currently visible actions.

        This is intentionally bounded. Each chosen action must overlap the user's
        wording, mutation actions must already be authorized, and an action label is
        never repeated in the same adaptive pass. Successful actions are suitable
        for later skill induction.
        """
        maximum = max(1, int(getattr(self.config.universal_operator, "max_safe_discovery_actions", 12) or 12))
        seen: List[str] = []
        actions: List[Dict[str, Any]] = []
        requested_mutations = UNIVERSAL_MUTATION_TERMS & _words(goal)
        completed_mutations: set[str] = set()
        for index in range(1, maximum + 1):
            try:
                row = await self._execute_live_goal(
                    browser, goal=goal, task_id=task_id, gate=gate, excluded_labels=seen,
                )
            except RuntimeError as exc:
                if actions and ("No currently visible action" in str(exc) or "Ambiguous live goal" in str(exc)):
                    break
                raise
            label = str(row.get("action") or row.get("label") or "")
            if label:
                seen.append(label)
            row = {**row, "sequence_index": index}
            actions.append(mask_sensitive_data(row))
            if str(row.get("risk") or "") == "mutation":
                completed_mutations |= (_words(label) & requested_mutations)
                # Do not chain unrelated mutations. Continue only when the current
                # user request explicitly named another mutation still outstanding.
                if not (requested_mutations - completed_mutations):
                    break
            await asyncio.sleep(0.2)
        if not actions:
            raise RuntimeError("Adaptive live goal produced no authorized live action")
        return {
            "pass": True,
            "action": "adaptive_live_goal",
            "risk": "mutation" if any(x.get("risk") == "mutation" for x in actions) else "read",
            "actions": actions,
            "action_count": len(actions),
            "bounded_max_actions": maximum,
            "live_reproof_required": True,
        }

    async def execute(
        self, *, task: str, plan: Mapping[str, Any], run_dir: str | Path,
        allow_portal_mutation: bool = False, confirmation: str = "",
        browser_override: BrowserSession | None = None,
    ) -> Dict[str, Any]:
        run_dir = Path(run_dir); run_dir.mkdir(parents=True, exist_ok=True)
        gate = self._mutation_gate(plan, allow_portal_mutation=allow_portal_mutation, confirmation=confirmation)
        safe_write_json(run_dir / "universal_mutation_gate.json", gate)
        if not gate.get("pass"):
            blocked = {"schema_version": EXEC_SCHEMA, "pass": False, "status": "blocked_mutation_authorization", "mutation_gate": gate, "plan": plan}
            safe_write_json(run_dir / "universal_portal_task_execution.json", blocked)
            return mask_sensitive_data(blocked)

        task_id = f"portal-task-{uuid.uuid4().hex[:12]}"
        try:
            from importlib.metadata import version
            app_version = version("hip-portal-id-agent")
        except Exception:
            from . import __version__ as app_version
        telemetry = AsyncMLflowTracker(self.config.mlflow, run_id=task_id, run_dir=run_dir, phases=["universal_portal_task"], app_version=app_version)
        telemetry.start(tags={"hip.execution_type": "universal_portal_task"}, params={"hip.step_count": len(plan.get("steps") or [])})
        result: Dict[str, Any] = {
            "schema_version": EXEC_SCHEMA, "task_id": task_id, "task": task, "started_at": utc_now(),
            "steps": [], "mutation_gate": gate, "values_stored": False,
        }
        entity = str(plan.get("entity") or "")
        matched_skill = plan.get("skill_match") if isinstance(plan.get("skill_match"), Mapping) else {}
        form_blueprints: List[Dict[str, Any]] = []
        observed_page_families: List[str] = []
        executed_skill_steps: List[Dict[str, Any]] = []
        skill_fallback_triggered = False
        async with _browser_context(self.config, run_dir, browser_override) as browser:
            if gate.get("mutation_required"):
                browser.set_portal_mutation_authorization(enabled=True, allowed_labels=gate.get("allowed_labels") or [], task_id=task_id)

            async def run_sequence(sequence: Sequence[Mapping[str, Any]], *, mode: str) -> List[Dict[str, Any]]:
                local_skill_steps: List[Dict[str, Any]] = []
                for index, step in enumerate(sequence, start=1):
                    typ = str(step.get("type") or "")
                    telemetry.log_event("universal_step_started", {"index": index, "type": typ, "action": step.get("action"), "mode": mode})
                    try:
                        if typ == "navigate":
                            await browser.goto_base_and_complete_sso(str(step.get("target") or self.config.portal.base_url))
                            row = {"type": typ, "pass": True, "url": browser.page.url}
                        elif typ == "learn_surface":
                            row = {"type": typ, **(await self._learn_surface(browser, task_id=task_id, deep=bool(step.get("deep"))))}
                        elif typ == "navigate_label":
                            row = {"type": typ, **(await self._navigate_label(browser, str(step.get("label") or "")))}
                        elif typ == "search":
                            row = {"type": typ, **(await self._search(browser, str(step.get("value") or entity)))}
                        elif typ == "semantic_action":
                            mutation_before = {}
                            if str(step.get("risk") or "") == "mutation":
                                mutation_before = await self._capture_mutation_evidence(
                                    browser, run_dir=run_dir, step_index=index, stage="before", step=step
                                )
                            row = {"type": typ, **(await self._semantic_action(browser, step, gate=gate, entity=entity))}
                            if str(step.get("risk") or "") == "mutation":
                                mutation_after = await self._capture_mutation_evidence(
                                    browser, run_dir=run_dir, step_index=index, stage="after", step=step
                                )
                                row["mutation_evidence"] = {"before": mutation_before, "after": mutation_after}
                        elif typ == "fill_from_input":
                            fill = await self._fill_from_input(
                                browser, task=task, input_json=str(step.get("input_json") or plan.get("input_json") or ""),
                                input_root=str(step.get("input_root") or plan.get("input_root") or ""), run_dir=run_dir,
                                max_cycles=int(getattr(self.config.universal_operator, "max_form_fill_cycles", 8) or 8),
                                skill=matched_skill, task_id=task_id,
                            )
                            row = {"type": typ, **fill}
                            if isinstance(fill.get("skill_blueprint"), Mapping):
                                form_blueprints.append(dict(fill.get("skill_blueprint") or {}))
                            if not fill.get("pass"):
                                raise RuntimeError("Universal input fill did not reach 100% exact runtime-input coverage")
                        elif typ == "live_goal":
                            row = {"type": typ, **(await self._execute_adaptive_goal(browser, goal=str(step.get("goal") or task), task_id=task_id, gate=gate))}
                        else:
                            raise RuntimeError(f"Unsupported universal task step: {typ}")
                    except Exception as exc:
                        setattr(exc, "hip_step_risk", str(step.get("risk") or ""))
                        setattr(exc, "hip_step_type", typ)
                        setattr(exc, "hip_step_index", index)
                        raise

                    result["steps"].append(mask_sensitive_data(row))
                    if typ == "live_goal" and isinstance(row.get("actions"), list):
                        for action_row in row.get("actions") or []:
                            if isinstance(action_row, Mapping):
                                local_skill_steps.append(mask_sensitive_data({
                                    "type": "semantic_action",
                                    "action": str(action_row.get("action") or ""),
                                    "label": str(action_row.get("action") or action_row.get("label") or ""),
                                    "risk": str(action_row.get("risk") or "read"),
                                }))
                    else:
                        local_skill_steps.append(self._skill_step_from_execution(step, row))
                    telemetry.log_metrics({f"universal.step.{index}.pass": 1.0 if row.get("pass") else 0.0}, step=index)
                    telemetry.log_event("universal_step_completed", {"index": index, "type": typ, "pass": bool(row.get("pass")), "mode": mode})
                    if typ not in {"learn_surface", "navigate"}:
                        try:
                            learned = await self._learn_surface(browser, task_id=task_id, deep=False)
                            if learned.get("page_family"):
                                observed_page_families.append(str(learned.get("page_family")))
                            result["steps"].append({"type": "post_action_learning", "pass": True, "page_family": learned.get("page_family"), "action_count": learned.get("action_count"), "control_count": learned.get("control_count")})
                        except Exception:
                            pass
                return local_skill_steps

            try:
                try:
                    executed_skill_steps = await run_sequence(list(plan.get("steps") or []), mode=str(plan.get("execution_mode") or "adaptive_live_discovery"))
                except Exception as skill_exc:
                    fallback = [dict(x) for x in plan.get("adaptive_fallback_steps") or [] if isinstance(x, Mapping)]
                    failed_risk = str(getattr(skill_exc, "hip_step_risk", "") or "")
                    safe_write_json(run_dir / "universal_portal_task_execution.partial.json", {**result, "error": mask_sensitive_string(str(skill_exc))})
                    repair = self.trace_self_repair.analyze(run_dir=run_dir, task=task, error=str(skill_exc), failed_step={"type": str(getattr(skill_exc, "hip_step_type", "") or ""), "index": int(getattr(skill_exc, "hip_step_index", 0) or 0), "risk": failed_risk}, mutation_risk=(failed_risk == "mutation"))
                    result["trace_self_repair"] = repair
                    recipe_id = str(((plan.get("deterministic_recipe") or {}).get("recipe_id") if isinstance(plan.get("deterministic_recipe"), Mapping) else "") or "")
                    if recipe_id:
                        self.deterministic_recipes.record_failure(recipe_id, reason=str(skill_exc))
                    auto_ok = bool(getattr(self.config.trace_self_repair, "auto_repair_enabled", True)) and not bool(repair.get("human_required")) and float(repair.get("confidence") or 0.0) >= float(getattr(self.config.trace_self_repair, "auto_repair_min_confidence", 0.72) or 0.72)
                    can_fallback = bool(fallback and failed_risk != "mutation" and (matched_skill.get("skill_id") or recipe_id or auto_ok))
                    if not can_fallback:
                        raise
                    skill_fallback_triggered = True
                    replay_attempt_steps = list(result.get("steps") or [])
                    result["skill_replay_attempt"] = {
                        "pass": False,
                        "skill_id": str(matched_skill.get("skill_id") or ""),
                        "reason": mask_sensitive_string(str(skill_exc)),
                        "failed_step_type": str(getattr(skill_exc, "hip_step_type", "") or ""),
                        "failed_step_index": int(getattr(skill_exc, "hip_step_index", 0) or 0),
                        "fallback": "adaptive_live_discovery",
                        "attempt_step_count": len(replay_attempt_steps),
                    }
                    telemetry.log_event("skill_replay_drift_fallback", result["skill_replay_attempt"])
                    if matched_skill.get("skill_id"):
                        self.skills.record_outcome(str(matched_skill.get("skill_id")), success=False, reason=str(skill_exc), run_id=task_id)
                    # Keep the failed replay evidence separate from the authoritative
                    # final execution so a successful rediscovery can complete the task.
                    result["skill_replay_attempt_steps"] = replay_attempt_steps
                    result["steps"] = []
                    form_blueprints.clear()
                    observed_page_families.clear()
                    executed_skill_steps = await run_sequence(fallback, mode="adaptive_live_discovery_after_skill_drift")
            except Exception as exc:
                result["error"] = mask_sensitive_string(str(exc))
                telemetry.log_event("universal_task_error", {"error": result["error"]})
            finally:
                browser.clear_portal_mutation_authorization()
                await browser.flush_logs()
        result["finished_at"] = utc_now()
        user_steps = [x for x in result.get("steps") or [] if x.get("type") != "post_action_learning"]
        result["pass"] = bool(user_steps) and not result.get("error") and all(bool(x.get("pass")) for x in user_steps)
        result["learned_capability_manifest"] = self.graph.manifest()
        result["skill_replay"] = {
            "matched_skill_id": str(matched_skill.get("skill_id") or ""),
            "matched_skill_name": str(matched_skill.get("name") or ""),
            "match_score": matched_skill.get("match_score"),
            "workflow_activated": bool(matched_skill.get("skill_id")),
            "fallback_triggered": skill_fallback_triggered,
            "used_for_form_binding": any(bool(x.get("skill_fast_replay_active")) for x in user_steps if isinstance(x, Mapping)),
            "live_reproof_required": True,
        }
        if bool(getattr(self.config.skill_induction, "enabled", True)):
            if result["pass"]:
                exact_verified = all(
                    bool(x.get("pass")) and (x.get("type") != "fill_from_input" or x.get("verification") == "100_percent_runtime_input_exact_readback")
                    for x in user_steps
                )
                if exact_verified or not bool(getattr(self.config.skill_induction, "require_exact_verification_for_induction", True)):
                    induced = self.skill_engine.induce_from_execution(
                        task=task, actions=ordered_actions(task), target_area=str(plan.get("target_area") or ""),
                        input_root=str(plan.get("input_root") or ""), executed_steps=executed_skill_steps,
                        form_blueprints=form_blueprints, page_families=observed_page_families, run_id=task_id,
                        exact_verified=True, skill_id_override=str(matched_skill.get("skill_id") or ""),
                        evidence={
                            "execution_mode": plan.get("execution_mode"), "step_count": len(user_steps),
                            "form_blueprint_count": len(form_blueprints), "fallback_triggered": skill_fallback_triggered,
                            "induction_source": "actual_successful_execution",
                        },
                    )
                    result["skill_induction"] = {
                        "pass": True, "status": induced.get("status"), "skill_id": induced.get("skill_id"),
                        "confidence": induced.get("confidence"), "success_count": induced.get("success_count"),
                    }
                    telemetry.log_event("skill_induced_or_reinforced", result["skill_induction"])
            elif matched_skill.get("skill_id") and not skill_fallback_triggered:
                demoted = self.skills.record_outcome(
                    str(matched_skill.get("skill_id")), success=False, reason=str(result.get("error") or "execution_failed"), run_id=task_id
                )
                result["skill_induction"] = {
                    "pass": False, "status": demoted.get("status"), "skill_id": demoted.get("skill_id"),
                    "confidence": demoted.get("confidence"), "failure_count": demoted.get("failure_count"),
                }
                telemetry.log_event("skill_replay_failed", result["skill_induction"])
        result["skill_library_manifest"] = self.skills.manifest()
        # V240: every completed task becomes a replay episode.  The score is based
        # on actual goal achievement, 100% input coverage/readback, mutation
        # verification and execution efficiency.  Values/selectors are not stored.
        policy_episode: Dict[str, Any] = {}
        policy_dream: Dict[str, Any] = {}
        if bool(getattr(self.config.replay_policy, "enabled", True)):
            try:
                recovery_count = int(bool(skill_fallback_triggered))
                policy_episode = self.replay_policy.record_episode(
                    task=task,
                    actions=ordered_actions(task),
                    target_area=str(plan.get("target_area") or ""),
                    input_root=str(plan.get("input_root") or ""),
                    page_families=observed_page_families,
                    steps=user_steps,
                    success=bool(result["pass"]),
                    run_id=task_id,
                    source="universal_portal_live_run",
                    mutation_required=bool(gate.get("mutation_required")),
                    mutation_verified=bool(result["pass"]),
                    blocked=not bool(result["pass"]),
                    recovery_count=recovery_count,
                    evidence={
                        "execution_mode": plan.get("execution_mode"),
                        "skill_fallback_triggered": skill_fallback_triggered,
                        "replay_policy_mode": (plan.get("replay_policy") or {}).get("mode") if isinstance(plan.get("replay_policy"), Mapping) else "",
                    },
                )
                if bool(getattr(self.config.replay_policy, "dream_after_every_run", True)):
                    policy_dream = self.replay_policy.dream(reason="universal_portal_task_completed")
                telemetry.log_metrics({
                    "policy.episode_score": float(policy_episode.get("score") or 0.0),
                    "policy.goal_success": 1.0 if result["pass"] else 0.0,
                })
                telemetry.log_event("replay_policy_episode_recorded", {
                    "score": policy_episode.get("score"),
                    "success": result["pass"],
                    "dream_updates": policy_dream.get("policy_updates"),
                })
            except Exception as exc:
                result["replay_policy_error"] = mask_sensitive_string(str(exc))
        result["replay_policy"] = {
            "decision": plan.get("replay_policy") if isinstance(plan.get("replay_policy"), Mapping) else {},
            "episode": policy_episode,
            "dream": policy_dream,
            "manifest": self.replay_policy.manifest(),
        }
        model_traces = []
        if isinstance(plan.get("model_routing_advisory"), Mapping) and plan.get("model_routing_advisory", {}).get("used"):
            model_traces.append(plan.get("model_routing_advisory"))
        if isinstance(result.get("trace_self_repair"), Mapping) and isinstance(result.get("trace_self_repair", {}).get("model_routing"), Mapping):
            model_traces.append(result.get("trace_self_repair", {}).get("model_routing"))
        for step in user_steps:
            if not isinstance(step, Mapping):
                continue
            if isinstance(step.get("model_routing"), Mapping):
                model_traces.append(step.get("model_routing"))
            for action_row in step.get("actions") or []:
                if isinstance(action_row, Mapping) and isinstance(action_row.get("model_routing"), Mapping):
                    model_traces.append(action_row.get("model_routing"))
        reward = float(policy_episode.get("score") or (1.0 if result["pass"] else 0.0))
        if bool(result["pass"]) and bool(getattr(self.config.deterministic_recipe, "enabled", True)):
            try:
                result["deterministic_recipe_learning"] = self.deterministic_recipes.record_success(task=task, actions=ordered_actions(task), target_area=str(plan.get("target_area") or ""), input_root=str(plan.get("input_root") or ""), steps=executed_skill_steps, form_blueprints=form_blueprints, reward=reward, run_id=task_id)
            except Exception as exc:
                result["deterministic_recipe_learning"] = {"status": "error", "error": mask_sensitive_string(str(exc))[:500]}
        result["deterministic_recipe_manifest"] = self.deterministic_recipes.manifest()
        result["human_teaching_manifest"] = self.human_teaching.manifest()
        model_outcomes = []
        for trace in model_traces:
            try:
                model_outcomes.append(self.model_portfolio.record_downstream_outcome(
                    trace=trace, success=bool(result["pass"]), reward=reward, drift=bool(skill_fallback_triggered)
                ))
            except Exception:
                pass
        result["model_portfolio"] = {
            "trace_count": len(model_traces),
            "outcome_updates": len(model_outcomes),
            "manifest": self.model_portfolio.manifest(),
        }
        try:
            skill_feedback_id = str((result.get("skill_induction") or {}).get("skill_id") or matched_skill.get("skill_id") or "")
            result["recursive_self_improvement"] = self.recursive_improvement.improve(
                run_reward=reward, success=bool(result["pass"]), reason="universal_portal_task_completed",
                model_trace=model_traces[-1] if model_traces else None,
                skill_feedback={
                    "skill_id": skill_feedback_id,
                    "success": bool(result["pass"]),
                    "run_id": task_id,
                    "outcome_already_recorded": True,
                    "reason": str(result.get("error") or "universal_portal_task_completed"),
                },
            )
            telemetry.log_metrics({"recursive_improvement.reward": reward})
            telemetry.log_event("recursive_self_improvement", {
                "reward": reward, "success": bool(result["pass"]),
                "cycles": len(result["recursive_self_improvement"].get("cycles") or []),
            })
        except Exception as exc:
            result["recursive_self_improvement"] = {"status": "error", "error": mask_sensitive_string(str(exc))[:500]}
        safe_write_json(run_dir / "universal_portal_task_execution.json", result)
        telemetry.finish(status="complete" if result["pass"] else "blocked", application_complete=bool(result["pass"]), final_report={"phases": {"universal_portal_task": {"status": "complete" if result["pass"] else "blocked"}}})
        return mask_sensitive_data(result)
