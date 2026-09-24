from __future__ import annotations

"""Goal-driven adaptive form execution for HIP Portal.

This module deliberately separates *what the mission wants* from *how the current
portal happens to render it*.  State-graph nodes carry the canonical business
contract from input.json.  Every cycle re-observes the current live form,
optionally asks Dell AIA's semantic form planner for advisory bindings, executes
through the shared BrowserSession broker (AutoWebGLM -> PyAutoGUI MCP ->
Playwright MCP -> Python Playwright), verifies exact state, and learns only from
successful live interactions.

No CSS selector, Angular id, DOM index, viewport coordinate, or screenshot pixel
position is persisted as long-term knowledge.
"""

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from playwright.async_api import Page

from .dds_control_driver import assert_active_surface, upload_file_control, get_active_form_root
from .autonomous_transition_runtime import choose_dynamic_portal_option
from .website_understanding import WebsiteUnderstandingEngine
from .llm_form_planner import LLMFormPlanner
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .stateful_form_runtime import (
    capture_stateful_controls,
    execute_phase_state_graph,
    resolve_stateful_control_diagnostics,
    phase_object,
)
from .upload_assets import attempt_upload_for_control, find_upload_asset


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())




def _world_model_for_page(page: Page):
    try:
        session = getattr(page, "_hip_browser_session", None)
        gate = getattr(session, "semantic_action_gate", None) if session is not None else None
        return getattr(gate, "world_model", None)
    except Exception:
        return None


def _node_label_aliases(node: Dict[str, Any]) -> List[str]:
    loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
    out: List[str] = []
    for value in list(loc.get("labels") or []) + [node.get("field_key")]:
        text = str(value or "").strip()
        if text and _norm(text) not in {_norm(x) for x in out}:
            out.append(text)
    return out


def _catalog_for_node(option_catalog: Dict[str, Any], node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    labels = {_norm(x) for x in _node_label_aliases(node) if _norm(x)}
    section = _norm(node.get("section"))
    ranked: List[Tuple[int, Dict[str, Any]]] = []
    for row in (option_catalog or {}).values():
        if not isinstance(row, dict):
            continue
        label = _norm(row.get("label"))
        actual_section = _norm(row.get("section"))
        score = 0
        if label and label in labels:
            score += 100
        elif label and any(label in x or x in label for x in labels):
            score += 55
        if section and actual_section:
            if section == actual_section:
                score += 50
            elif section in actual_section or actual_section in section:
                score += 25
            else:
                score -= 20
        options = [x for x in (row.get("options") or []) if isinstance(x, dict) and x.get("text") and not x.get("disabled")]
        if options:
            score += 10
        if score > 0:
            ranked.append((score, row))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked:
        return None
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return dict(ranked[0][1])


def _apply_dynamic_option_inference(
    graph: Dict[str, Any], *, phase: str, section: Optional[str], website_model: Dict[str, Any],
    mission_context: Dict[str, Any], page: Page, config: Any = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Bind select nodes to live portal option labels without inventing values.

    Explicit input values are canonicalized to the exact live option label. Missing
    required select values are inferred only from one unique mission-context option
    or one unique validated world-model branch that is still present live.
    """
    out = copy.deepcopy(graph)
    auto_cfg = getattr(config, "autonomous_form", None) if config is not None else None
    if auto_cfg is not None and not bool(getattr(auto_cfg, "dynamic_option_resolution_enabled", True)):
        return out, []
    catalog = website_model.get("option_catalog") if isinstance(website_model.get("option_catalog"), dict) else {}
    wm = _world_model_for_page(page)
    allow_memory = bool(getattr(auto_cfg, "allow_unique_validated_memory_option_when_input_missing", True)) if auto_cfg is not None else True
    decisions: List[Dict[str, Any]] = []
    for node in out.get("nodes") or []:
        if not isinstance(node, dict) or str(node.get("action") or "") != "select_single":
            continue
        if not _section_matches_local(section, node.get("section")):
            continue
        row = _catalog_for_node(catalog, node)
        if row is None:
            continue
        live_options = row.get("options") or []
        desired = node.get("expected_value")
        label = str(row.get("label") or (_node_label_aliases(node)[:1] or [node.get("field_key")])[0])
        decision = choose_dynamic_portal_option(
            phase=phase, label=label, section=str(row.get("section") or node.get("section") or ""),
            live_options=live_options, desired_value=desired, mission_context=mission_context, world_model=wm,
            allow_validated_memory_fallback=allow_memory,
        )
        decision = dict(decision, field_key=node.get("field_key"), previous_expected_value=desired, live_label=label)
        if decision.get("pass"):
            selected = str(decision.get("selected_option") or "")
            if selected:
                node["expected_value"] = selected
                node.setdefault("adaptive_option_evidence", []).append({
                    "source": decision.get("source"), "confidence": decision.get("confidence"),
                    "live_label": label, "section": row.get("section"), "portal_owned": True,
                })
        decisions.append(mask_sensitive_data(decision))
    return out, decisions


def autonomous_phase_enabled(config: Any, phase: str) -> bool:
    """Return whether the shared goal runtime owns completion for this phase.

    V231 enables all seven form phases by default.  The all-phases flag is the
    authoritative switch; individual flags exist for controlled rollback/UAT.
    """
    cfg = getattr(config, "autonomous_form", None) if config is not None else None
    if cfg is None or not bool(getattr(cfg, "enabled", True)):
        return False
    if bool(getattr(cfg, "apply_to_all_form_phases", True)):
        return True
    p = _norm(phase).replace(" ", "_")
    if p == "data_map":
        return bool(getattr(cfg, "apply_to_data_map", True))
    if "document_type" in p:
        return bool(getattr(cfg, "apply_to_document_types", True))
    if p == "rule":
        return bool(getattr(cfg, "apply_to_rules", True))
    if "transport_profile" in p:
        return bool(getattr(cfg, "apply_to_transport_profiles", True))
    if p == "biz_flow":
        return bool(getattr(cfg, "apply_to_biz_flow", True))
    return False


def _section_matches_local(expected: Optional[str], actual: Any) -> bool:
    if not expected:
        return True
    e = _norm(expected)
    a = _norm(actual)
    return bool(e and a and (e == a or e in a or a in e))


def _graph_goal_values(graph: Dict[str, Any], section: Optional[str] = None) -> Dict[str, Any]:
    return {
        str(n.get("field_key") or ""): n.get("expected_value")
        for n in (graph.get("nodes") or [])
        if isinstance(n, dict)
        and n.get("expected_value") not in (None, "", [])
        and _section_matches_local(section, n.get("section"))
    }


def _humanize_input_key(key: str) -> str:
    text = str(key or "").strip().replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in text.split())


def _flatten_phase_input_leaves(input_data: Dict[str, Any], phase: str) -> List[Dict[str, Any]]:
    """Return every nonblank runtime input leaf for the selected HIP phase.

    Lists of scalar values are a single logical leaf (DDS multi-select); lists of
    objects are expanded row-by-row.  This ledger is derived from the *actual*
    runtime input.json and therefore catches new attributes that are not yet in
    the handwritten phase compiler.
    """
    obj, root = phase_object(input_data if isinstance(input_data, dict) else {}, phase)
    leaves: List[Dict[str, Any]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}")
            return
        if isinstance(value, list):
            if not value:
                return
            if all(not isinstance(x, (dict, list)) for x in value):
                if any(x not in (None, "") for x in value):
                    leaves.append({"input_path": path, "value": value, "field_key": path.rsplit(".", 1)[-1]})
                return
            for idx, child in enumerate(value):
                walk(child, f"{path}[{idx}]")
            return
        if value in (None, ""):
            return
        leaves.append({"input_path": path, "value": value, "field_key": path.rsplit(".", 1)[-1]})

    walk(obj, root)
    return leaves


def _path_row_index(path: str) -> Optional[int]:
    import re
    matches = re.findall(r"\[(\d+)\]", str(path or ""))
    return int(matches[-1]) if matches else None


def _path_section_hint(phase: str, path: str) -> str:
    low = str(path or "").lower()
    if phase == "biz_flow":
        if ".flow_details." in low:
            return "Flow Details"
        if ".configure_source." in low or ".flow_identifiers." in low:
            return "Configure Source(s)"
        if ".configure_targets." in low:
            return "Configure Target(s)"
        if ".process_steps" in low:
            # Process Step accordions are rendered inside target configuration in
            # the approved U-HAUL golden flow.  Live section evidence can still
            # override this through exact label matching.
            return "Configure Target(s)"
        if ".configure_routing." in low:
            return "Configure Routing"
    return ""


def _infer_action_from_control(control: Dict[str, Any], expected: Any) -> str:
    typ = _norm(control.get("type"))
    role = _norm(control.get("role"))
    tag = _norm(control.get("tag"))
    component = _norm(control.get("component_tag"))
    if typ == "file" or "file" in component:
        return "upload_file"
    if str(control.get("selection_mode") or "").lower() == "multiple" or isinstance(expected, list):
        return "select_multi"
    if typ == "radio" or role == "radio":
        return "select_radio"
    if typ in {"checkbox"} or role in {"checkbox", "switch"} or "switch" in component:
        return "toggle"
    if role in {"combobox", "listbox"} or tag == "select" or "dropdown" in component:
        return "select_single"
    return "fill_text"


def _control_text_tokens(control: Dict[str, Any]) -> List[str]:
    values = [
        control.get("label"), control.get("name"), control.get("form_control_name"),
        control.get("framework_key"), control.get("placeholder"), control.get("semantic_key"),
    ]
    return [_norm(v) for v in values if _norm(v)]


def _leaf_control_score(leaf: Dict[str, Any], control: Dict[str, Any], phase: str) -> int:
    key = _norm(leaf.get("field_key"))
    human = _norm(_humanize_input_key(str(leaf.get("field_key") or "")))
    tokens = _control_text_tokens(control)
    score = 0
    if key and key in tokens:
        score += 140
    if human and human in tokens:
        score += 130
    for token in tokens:
        if key and (key in token or token in key):
            score += 45
        if human and (human in token or token in human):
            score += 35
    hint = _norm(_path_section_hint(phase, str(leaf.get("input_path") or "")))
    actual_section = _norm(control.get("section"))
    if hint and actual_section:
        if hint == actual_section:
            score += 70
        elif hint in actual_section or actual_section in hint:
            score += 35
        else:
            score -= 25
    row_idx = _path_row_index(str(leaf.get("input_path") or ""))
    control_idx = control.get("row_index")
    if row_idx is not None and control_idx is not None:
        score += 65 if int(row_idx) == int(control_idx) else -60
    if control.get("disabled") or control.get("readonly"):
        score -= 15
    if control.get("interactable") is False:
        score -= 20
    return score


def _supplement_runtime_input_graph(
    graph: Dict[str, Any], controls: Sequence[Dict[str, Any]], input_data: Dict[str, Any],
    *, phase: str, section: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Add safe semantic nodes for runtime input leaves missing from the static graph.

    The generated nodes persist *no selector or coordinates*.  A live control is
    used only to learn semantic aliases (label/name/framework key/section/row),
    after which the normal stateful resolver must bind the node again and the
    standard executor must exact-readback verify it.
    """
    out = copy.deepcopy(graph)
    nodes = [n for n in (out.get("nodes") or []) if isinstance(n, dict)]
    existing_paths = {str(n.get("input_path") or "") for n in nodes if str(n.get("input_path") or "")}
    # Older/externally supplied graphs may omit input_path but still carry a
    # unique canonical field_key.  Use that only when both the graph field and
    # runtime leaf key are unique; repeated-row fields such as value/operator are
    # never collapsed by this compatibility path.
    node_key_counts: Dict[str, int] = {}
    for n in nodes:
        key = _norm(n.get("field_key"))
        if key:
            node_key_counts[key] = node_key_counts.get(key, 0) + 1
    accounting = [x for x in (out.get("input_accounting") or []) if isinstance(x, dict)]
    accounted_paths = {str(x.get("input_path") or "") for x in accounting}
    canonical_alias = {str(x.get("input_path") or ""): str(x.get("canonical_input_path") or "") for x in accounting if x.get("canonical_input_path")}
    leaves = _flatten_phase_input_leaves(input_data, phase)
    leaf_key_counts: Dict[str, int] = {}
    for leaf in leaves:
        key = _norm(leaf.get("field_key"))
        if key:
            leaf_key_counts[key] = leaf_key_counts.get(key, 0) + 1
    scoped: List[Dict[str, Any]] = []
    additions: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    accounted: List[Dict[str, Any]] = []

    for leaf in leaves:
        path = str(leaf.get("input_path") or "")
        hint = _path_section_hint(phase, path)
        if section and hint and not _section_matches_local(section, hint):
            continue
        scoped.append(leaf)
        if path in existing_paths:
            accounted.append({"input_path": path, "status": "compiled_node"})
            continue
        leaf_key = _norm(leaf.get("field_key"))
        if leaf_key and leaf_key_counts.get(leaf_key) == 1 and node_key_counts.get(leaf_key) == 1:
            # Compatibility for a graph node that predates input_path metadata.
            # Match only when expected values agree, preventing accidental
            # aliasing of semantically different fields with the same key.
            candidate = next((n for n in nodes if _norm(n.get("field_key")) == leaf_key), None)
            if candidate is not None and candidate.get("expected_value") == leaf.get("value"):
                accounted.append({"input_path": path, "status": "compiled_unique_field_key", "node_id": candidate.get("node_id")})
                continue
        if path in accounted_paths:
            canon = canonical_alias.get(path, "")
            if canon and canon in existing_paths:
                accounted.append({"input_path": path, "status": "accounted_alias", "canonical_input_path": canon})
                continue
            # Nonblank runtime values are never silently excused by a generic
            # accounting record unless a concrete canonical executable exists.

        ranked = sorted(
            [(_leaf_control_score(leaf, c, phase), c) for c in controls if isinstance(c, dict)],
            key=lambda item: item[0], reverse=True,
        )
        best_score, best = ranked[0] if ranked else (0, None)
        second_score = ranked[1][0] if len(ranked) > 1 else -999
        margin = best_score - second_score
        if best is None or best_score < 75 or margin < 18:
            unresolved.append({
                "input_path": path, "field_key": leaf.get("field_key"), "reason": "no unique live semantic control binding",
                "best_score": best_score, "score_margin": margin,
                "candidate_labels": [str(c.get("label") or c.get("name") or c.get("framework_key") or "") for _, c in ranked[:4]],
            })
            continue
        action = _infer_action_from_control(best, leaf.get("value"))
        loc_labels = [x for x in [best.get("label"), _humanize_input_key(str(leaf.get("field_key") or ""))] if str(x or "").strip()]
        loc_names = [x for x in [best.get("name"), best.get("form_control_name"), best.get("framework_key")] if str(x or "").strip()]
        loc_placeholders = [x for x in [best.get("placeholder")] if str(x or "").strip()]
        loc_roles = [x for x in [best.get("role")] if str(x or "").strip()]
        row_idx = best.get("row_index") if best.get("row_index") is not None else _path_row_index(path)
        row_kind = str(best.get("row_kind") or "")
        safe_id = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
        node = {
            "node_id": f"{phase}.runtime_input.{safe_id}",
            "phase": phase,
            "section": str(best.get("section") or hint or section or ""),
            "field_key": str(leaf.get("field_key") or path.rsplit(".", 1)[-1]),
            "action": action,
            "expected_value": leaf.get("value"),
            "input_path": path,
            "semantic_locator": {
                "names": loc_names, "labels": loc_labels, "placeholders": loc_placeholders,
                "roles": loc_roles, "section_aliases": [x for x in [best.get("section"), hint] if str(x or "").strip()],
                "row_kind": row_kind, "row_index": row_idx,
            },
            "row_kind": row_kind, "row_index": row_idx, "required": True, "depends_on": [],
            "verification": "exact_committed_control_value",
            "executor": "runtime-input-ledger-semantic-binding",
            "fallback_executor": "autowebglm-browser-session-pyautogui-mcp-primary",
            "notes": "V237 runtime node synthesized from actual input.json + current live semantic control; selector/coordinates are intentionally not persisted",
        }
        nodes.append(node)
        existing_paths.add(path)
        additions.append({
            "input_path": path, "field_key": node["field_key"], "action": action,
            "live_label": best.get("label"), "section": node["section"], "row_index": row_idx,
            "binding_score": best_score, "score_margin": margin,
        })

    out["nodes"] = nodes
    ledger = {
        "schema_version": "hip.runtime-input-leaf-ledger.v1",
        "phase": phase, "section": section or "",
        "runtime_nonblank_leaf_count": len(scoped),
        "compiled_or_accounted_count": len(accounted),
        "runtime_synthesized_node_count": len(additions),
        "unresolved_leaf_count": len(unresolved),
        "unresolved_input_leaves": unresolved,
        "synthesized_nodes": additions,
        "pass": not unresolved,
    }
    return out, ledger


def _golden_runtime_context(page: Page, phase: str) -> Tuple[List[str], Any]:
    """Get run-attached golden references and visual advisor without new globals."""
    try:
        session = getattr(page, "_hip_browser_session", None)
        refs_by_phase = getattr(session, "golden_references_by_phase", {}) if session is not None else {}
        refs = []
        for row in (refs_by_phase.get(phase, []) if isinstance(refs_by_phase, dict) else []):
            if isinstance(row, dict):
                value = row.get("run_local_path") or row.get("path") or row.get("file")
            else:
                value = row
            if value and Path(str(value)).is_file():
                refs.append(str(value))
        advisor = getattr(session, "visual_feedback_agent", None) if session is not None else None
        if not refs:
            phase_names = {
                "data_map": ["Data Map.png"],
                "source_document_type": ["Source Document Type.png"],
                "target_document_type": ["Target Document Type.png"],
                "rule": ["Rules.png"],
                "source_transport_profile": ["Source Transport Profile.png"],
                "target_transport_profile": ["Target Transport Profile.png"],
                "biz_flow": ["BizFlow-FD.png", "BizFlow-CS.png", "BizFlow-CT-1.png", "BizFlow-CT-2.png", "BizFlow-CR.png"],
            }
            for base in [Path.cwd(), Path(__file__).resolve().parent.parent]:
                golden_dir = base / "golden_screenshots" / "UHAUL-POASN"
                if not golden_dir.is_dir():
                    continue
                for name in phase_names.get(phase, []):
                    candidate = golden_dir / name
                    if candidate.is_file():
                        refs.append(str(candidate))
                if refs:
                    break
        return refs, advisor
    except Exception:
        return [], None


def _clone_graph(graph: Dict[str, Any], nodes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    out = copy.deepcopy(graph)
    selected = [copy.deepcopy(n) for n in nodes if isinstance(n, dict)]
    ids = {str(n.get("node_id") or "") for n in selected}
    out["nodes"] = selected
    out["dependency_edges"] = [
        copy.deepcopy(e)
        for e in (graph.get("dependency_edges") or [])
        if isinstance(e, dict)
        and str(e.get("from") or "") in ids
        and str(e.get("to") or "") in ids
    ]
    return out


def _expected_input_values(graph: Dict[str, Any]) -> Dict[str, Any]:
    return {
        str(n.get("field_key") or ""): n.get("expected_value")
        for n in (graph.get("nodes") or [])
        if isinstance(n, dict) and n.get("expected_value") not in (None, "", [])
    }


def _control_fingerprint(controls: Sequence[Dict[str, Any]]) -> str:
    """Value-free current-form shape fingerprint used only for progress detection."""
    rows = []
    for c in controls:
        if not isinstance(c, dict):
            continue
        rows.append({
            "section": _norm(c.get("section")),
            "label": _norm(c.get("label") or c.get("placeholder") or c.get("name")),
            "role": _norm(c.get("role") or c.get("type") or c.get("tag")),
            "framework_key": _norm(c.get("framework_key") or c.get("form_control_name")),
            "required": bool(c.get("required")),
            "disabled": bool(c.get("disabled")),
            "readonly": bool(c.get("readonly")),
        })
    raw = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _binding_summary(controls: Sequence[Dict[str, Any]], graph: Dict[str, Any], section: Optional[str] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or node.get("expected_value") in (None, "", []):
            continue
        if not _section_matches_local(section, node.get("section")):
            continue
        diag = resolve_stateful_control_diagnostics(controls, node)
        control = diag.get("control") if isinstance(diag.get("control"), dict) else {}
        out.append(mask_sensitive_data({
            "field": node.get("field_key"),
            "action": node.get("action"),
            "required": bool(node.get("required", True)),
            "bound": bool(diag.get("resolved")),
            "reason": diag.get("reason"),
            "best_score": diag.get("best_score"),
            "margin": diag.get("score_margin"),
            "live_label": control.get("label"),
            "live_role": control.get("role") or control.get("type"),
            "live_section": control.get("section"),
            # Current-generation selectors are evidence, never memory.
            "selector_current_generation": control.get("selector") if diag.get("resolved") else "",
        }))
    return out


def _apply_advisory_plan_hints(
    graph: Dict[str, Any],
    controls: Sequence[Dict[str, Any]],
    plan: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Augment semantic aliases using only controls that exist in this DOM generation.

    The LLM cannot create a selector or input key.  A hint is accepted only when
    the key exists in the mission graph and the selector exactly matches one live
    captured control.  Only semantic attributes are copied into the graph; the
    selector itself is deliberately not persisted into the node.
    """
    out = copy.deepcopy(graph)
    controls_by_selector = {
        str(c.get("selector") or ""): c
        for c in controls
        if isinstance(c, dict) and str(c.get("selector") or "")
    }
    nodes_by_key = {
        str(n.get("field_key") or ""): n
        for n in (out.get("nodes") or [])
        if isinstance(n, dict)
    }
    accepted: List[Dict[str, Any]] = []
    steps = plan.get("field_steps") if isinstance(plan, dict) and isinstance(plan.get("field_steps"), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        key = str(step.get("key") or "").strip()
        node = nodes_by_key.get(key)
        if node is None:
            continue
        selector = str(step.get("selector") or "").strip()
        control = controls_by_selector.get(selector)
        if control is None:
            # Conservative label-hint fallback: accept only one exact normalized label.
            hint = _norm(step.get("label_hint"))
            matches = [
                c for c in controls
                if isinstance(c, dict)
                and hint
                and _norm(c.get("label") or c.get("placeholder") or c.get("name")) == hint
            ]
            control = matches[0] if len(matches) == 1 else None
        if control is None:
            continue
        loc = node.setdefault("semantic_locator", {})
        for target, value in (
            ("labels", control.get("label")),
            ("names", control.get("name") or control.get("framework_key") or control.get("form_control_name")),
            ("placeholders", control.get("placeholder")),
            ("roles", control.get("role")),
            ("section_aliases", control.get("section")),
        ):
            if value in (None, ""):
                continue
            bucket = loc.setdefault(target, [])
            if str(value) not in [str(x) for x in bucket]:
                bucket.append(str(value))
        node.setdefault("adaptive_binding_evidence", []).append({
            "source": "live_control_plus_dell_aia_advisory",
            "label": control.get("label"),
            "role": control.get("role") or control.get("type"),
            "section": control.get("section"),
            "confidence": step.get("confidence"),
            "reason": step.get("reason"),
        })
        accepted.append({
            "field": key,
            "live_label": control.get("label"),
            "live_role": control.get("role") or control.get("type"),
            "confidence": step.get("confidence"),
        })
    return out, mask_sensitive_data(accepted)


def _uncovered_required_controls(controls: Sequence[Dict[str, Any]], graph: Dict[str, Any], section: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return required writable live controls with neither a mapped goal nor a current value.

    This is the anti-fabrication gate. If Dell adds a required field that is not
    represented by input.json, the agent may discover it but must not invent a
    value. A required control already populated by the portal itself is treated as
    satisfied unless the mission graph explicitly owns that field.
    """
    bound_selectors: set[str] = set()
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or node.get("expected_value") in (None, "", []):
            continue
        if not _section_matches_local(section, node.get("section")):
            continue
        diag = resolve_stateful_control_diagnostics(controls, node)
        control = diag.get("control") if isinstance(diag.get("control"), dict) else None
        if diag.get("resolved") and control is not None and control.get("selector"):
            bound_selectors.add(str(control.get("selector")))

    uncovered: List[Dict[str, Any]] = []
    for c in controls:
        if not isinstance(c, dict) or not bool(c.get("required")):
            continue
        if c.get("disabled") or c.get("readonly"):
            continue
        selector = str(c.get("selector") or "")
        if selector and selector in bound_selectors:
            continue
        value = str(c.get("value") or "").strip()
        selected = [str(x).strip() for x in (c.get("selected_values") or []) if str(x).strip()]
        role = _norm(c.get("role") or c.get("type"))
        if value or selected or (role in {"switch", "checkbox", "radio"} and bool(c.get("checked"))):
            continue
        uncovered.append(mask_sensitive_data({
            "label": c.get("label") or c.get("placeholder") or c.get("name"),
            "role": c.get("role") or c.get("type") or c.get("tag"),
            "section": c.get("section"),
            "framework_key": c.get("framework_key") or c.get("form_control_name"),
            "reason": "required live control has no mapped mission value and no portal-provided value",
        }))
    return uncovered


async def _observe_autowebglm(page: Page, *, phase: str, cycle: int, goal: str) -> Dict[str, Any]:
    """Capture AutoWebGLM's live simplified-HTML observation without inventing actions."""
    session = getattr(page, "_hip_browser_session", None)
    bridge = getattr(session, "autowebglm_bridge", None) if session is not None else None
    if bridge is None or not hasattr(bridge, "build_observation"):
        return {"available": False, "reason": "AutoWebGLM bridge unavailable"}
    try:
        observation = await bridge.build_observation(
            page=page,
            task=f"Autonomously complete HIP phase {phase}. Cycle {cycle}. Goal: {goal}. Observe the current live form; do not invent a final mutation.",
            history=getattr(session, "action_events", [])[-30:] if session is not None else [],
        )
        # Keep only bounded, masked evidence in the phase audit.
        return mask_sensitive_data({
            "available": bool(observation.get("available")),
            "schema_version": observation.get("schema_version"),
            "position": observation.get("position"),
            "tabs": observation.get("tabs"),
            "simplified_html": str(observation.get("simplified_html") or "")[:12000],
        })
    except Exception as exc:
        return {"available": False, "reason": mask_sensitive_string(str(exc))[:500]}


async def execute_autonomous_phase_goal(
    *,
    page: Page,
    graph: Dict[str, Any],
    phase: str,
    input_data: Dict[str, Any],
    config: Any = None,
    output_dir: Optional[Path] = None,
    prior_attempts: Optional[Sequence[Dict[str, Any]]] = None,
    max_cycles: int = 4,
    repair: bool = True,
    strict_live_execution: bool = True,
    section: Optional[str] = None,
    executor: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Autonomously achieve one HIP form goal with bounded adaptive cycles.

    The routine has no phase-specific selector/coordinate list.  It uses the
    canonical input graph as the goal and the *current* form as the truth.
    Required file nodes are uploaded only after non-file dependencies have had a
    chance to reveal them.  On changed labels/wrappers, Dell AIA form planning may
    add current-live semantic aliases; exact state verification remains deterministic.
    """
    max_cycles = max(1, min(int(max_cycles or 1), 12))
    autonomous_cfg = getattr(config, "autonomous_form", None) if config is not None else None
    no_progress_limit = max(1, min(int(getattr(autonomous_cfg, "no_progress_cycle_limit", 2) or 2), 6))
    use_autowebglm_observation = bool(getattr(autonomous_cfg, "use_autowebglm_live_observation", True))
    use_binding_advisor = bool(getattr(autonomous_cfg, "use_dell_aia_binding_advisor_on_ambiguity", True))
    working_graph = copy.deepcopy(graph)
    all_prior: List[Dict[str, Any]] = [dict(x) for x in (prior_attempts or []) if isinstance(x, dict)]
    cycles: List[Dict[str, Any]] = []
    last_shape = ""
    no_progress = 0
    goal_values = _graph_goal_values(working_graph, section)
    goal_summary = ", ".join(f"{k}={v}" for k, v in goal_values.items() if "file" not in k)[:1600]
    understanding_engine = WebsiteUnderstandingEngine(config=config)
    last_golden_visual_feedback: Dict[str, Any] = {}

    async def _execute_graph(current_graph: Dict[str, Any], prior: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        if executor is None:
            return await execute_phase_state_graph(
                page, current_graph, phase=phase, section=section,
                max_retries=2, repair=repair, prior_attempts=prior,
                strict_live_execution=strict_live_execution,
            )
        # Dedicated executors (Document Type) keep their richer transaction model.
        # File upload is owned by this autonomous runtime; do not send upload_file
        # nodes to a dedicated executor that does not implement that action.
        exec_graph = current_graph
        if any(str(n.get("action") or "") == "upload_file" for n in (current_graph.get("nodes") or []) if isinstance(n, dict)):
            exec_graph = _clone_graph(
                current_graph,
                [n for n in (current_graph.get("nodes") or []) if isinstance(n, dict) and str(n.get("action") or "") != "upload_file"],
            )
        # Pass only parameters they declare so the shared runtime stays generic.
        import inspect
        params = inspect.signature(executor).parameters
        kwargs: Dict[str, Any] = {}
        candidates = {
            "phase": phase, "section": section, "max_retries": 2, "repair": repair,
            "prior_attempts": prior, "strict_live_execution": strict_live_execution,
        }
        for key, value in candidates.items():
            if key in params and (key != "section" or value is not None):
                kwargs[key] = value
        return await executor(page, exec_graph, **kwargs)

    for cycle in range(1, max_cycles + 1):
        gate = await assert_active_surface(page, phase)
        controls_before = await capture_stateful_controls(page, phase)
        # V237: reconcile the *actual* runtime input.json against the live form on
        # every cycle.  This closes the gap where an element was correctly seen
        # but never scheduled because the handwritten compiler did not know a new
        # field yet.
        working_graph, runtime_input_ledger = _supplement_runtime_input_graph(
            working_graph, controls_before, input_data, phase=phase, section=section
        )
        goal_values = _graph_goal_values(working_graph, section)
        goal_summary = ", ".join(f"{k}={v}" for k, v in goal_values.items() if "file" not in k)[:1600]
        golden_refs, golden_advisor = _golden_runtime_context(page, phase)
        shape_before = _control_fingerprint(controls_before)
        try:
            website_model = await understanding_engine.capture(
                page=page, phase=phase, stage=f"autonomous_cycle_{cycle}_pre_action",
                output_dir=(output_dir / "website_understanding" if output_dir is not None else None),
                include_registered_listeners=False,
            )
        except Exception as exc:
            website_model = {"available": False, "reason": mask_sensitive_string(str(exc))[:500]}
        dynamic_option_decisions: List[Dict[str, Any]] = []
        if isinstance(website_model, dict) and website_model.get("available"):
            working_graph, dynamic_option_decisions = _apply_dynamic_option_inference(
                working_graph, phase=phase, section=section, website_model=website_model,
                mission_context=input_data, page=page, config=config,
            )
            goal_values = _graph_goal_values(working_graph, section)
            goal_summary = ", ".join(f"{k}={v}" for k, v in goal_values.items() if "file" not in k)[:1600]
        cycle_audit: Dict[str, Any] = {
            "cycle": cycle,
            "section": section or "",
            "surface_gate": gate,
            "website_understanding": {
                "available": bool(website_model.get("available")) if isinstance(website_model, dict) else False,
                "url_path": website_model.get("url_path") if isinstance(website_model, dict) else "",
                "active_surface": website_model.get("active_surface") if isinstance(website_model, dict) else {},
                "understanding_gate": website_model.get("understanding_gate") if isinstance(website_model, dict) else {},
                "option_catalog_size": len(website_model.get("option_catalog") or {}) if isinstance(website_model, dict) else 0,
            },
            "dynamic_option_decisions": dynamic_option_decisions,
            "runtime_input_leaf_ledger": runtime_input_ledger,
            "golden_reference_count": len(golden_refs),
            "control_count_before": len(controls_before),
            "shape_before": shape_before,
            "bindings_before": _binding_summary(controls_before, working_graph, section),
            "autowebglm_observation": (
                await _observe_autowebglm(page, phase=phase, cycle=cycle, goal=goal_summary)
                if use_autowebglm_observation else
                {"available": False, "reason": "disabled by autonomous_form.use_autowebglm_live_observation"}
            ),
            "adaptive_hints": [],
            "file_attempts": [],
        }
        if gate.get("fatal"):
            cycle_audit["status"] = "surface_lost"
            cycles.append(cycle_audit)
            break

        # V237: use the human-approved golden screenshot proactively as visual
        # structure guidance before filling.  It never supplies customer values;
        # those still come exclusively from input.json.  The output is advisory
        # context for Dell AIA/AutoGen semantic binding on this/next cycle.
        if golden_refs and golden_advisor is not None and output_dir is not None and (cycle == 1 or not runtime_input_ledger.get("pass")):
            try:
                pre_shot = output_dir / f"cycle_{cycle:02d}_golden_prefill.png"
                pre_shot.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(pre_shot), full_page=True)
                pre_visual = await asyncio.to_thread(
                    golden_advisor.diagnose_visual_state,
                    screenshot=str(pre_shot), golden_screenshots=golden_refs,
                    expected={
                        "phase": phase, "section": section or "",
                        "runtime_input_values": goal_values,
                        "unresolved_input_leaves": runtime_input_ledger.get("unresolved_input_leaves") or [],
                    },
                )
                last_golden_visual_feedback = mask_sensitive_data(pre_visual if isinstance(pre_visual, dict) else {"raw": str(pre_visual)})
                cycle_audit["golden_prefill_visual_feedback"] = last_golden_visual_feedback
            except Exception as exc:
                cycle_audit["golden_prefill_visual_feedback_error"] = mask_sensitive_string(str(exc))[:500]

        # V236: every row in bindings_before represents a nonblank input-owned
        # mission value. Ask the semantic binding advisor about all unresolved
        # supplied attributes, not only controls marked required by the portal.
        unresolved_required = [
            row for row in cycle_audit["bindings_before"]
            if not row.get("bound") and row.get("action") != "upload_file"
        ]
        # Use Dell AIA/AutoGen semantic planning only when deterministic live
        # bindings are insufficient. This is advisory and cannot authorize actions.
        if unresolved_required and config is not None and use_binding_advisor:
            try:
                planner = LLMFormPlanner.from_env(getattr(config, "aia", None))
                if planner is not None:
                    plan_result = planner.plan_fill(
                        phase=phase,
                        controls=list(controls_before),
                        input_values=goal_values,
                        before_state={
                            "control_count": len(controls_before),
                            "runtime_input_leaf_ledger": runtime_input_ledger,
                            "golden_visual_feedback": last_golden_visual_feedback,
                            "golden_reference_count": len(golden_refs),
                        },
                        failures=unresolved_required + list(runtime_input_ledger.get("unresolved_input_leaves") or []),
                    )
                    if plan_result.used and isinstance(plan_result.plan, dict):
                        working_graph, hints = _apply_advisory_plan_hints(
                            working_graph, controls_before, plan_result.plan
                        )
                        cycle_audit["adaptive_hints"] = hints
                        cycle_audit["llm_form_plan_status"] = plan_result.status
                    else:
                        cycle_audit["llm_form_plan_status"] = plan_result.status
            except Exception as exc:
                cycle_audit["llm_form_plan_status"] = f"unavailable: {mask_sensitive_string(str(exc))[:500]}"

        non_file_nodes = [
            n for n in (working_graph.get("nodes") or [])
            if isinstance(n, dict) and str(n.get("action") or "") != "upload_file"
            and _section_matches_local(section, n.get("section"))
        ]
        non_file_graph = _clone_graph(working_graph, non_file_nodes)
        try:
            if non_file_nodes:
                non_file_result = await _execute_graph(non_file_graph, all_prior)
            else:
                non_file_result = {"pass": True, "attempts": [], "execution_stage_audit": {}}
            cycle_audit["non_file_execution"] = non_file_result
            all_prior.extend(
                dict(a, autonomous_cycle=cycle, execution_stage="autonomous_non_file")
                for a in (non_file_result.get("attempts") or []) if isinstance(a, dict)
            )
        except Exception as exc:
            non_file_result = {"pass": False, "error": mask_sensitive_string(str(exc))}
            cycle_audit["non_file_execution"] = non_file_result

        # Re-observe because parent selections can mount/replace file controls.
        controls_after_fields = await capture_stateful_controls(page, phase)
        for node in working_graph.get("nodes") or []:
            if not isinstance(node, dict) or str(node.get("action") or "") != "upload_file":
                continue
            if not _section_matches_local(section, node.get("section")):
                continue
            expected = node.get("expected_value")
            if expected in (None, "", []):
                continue
            # Reuse a successful upload from this mission if already proven.
            already = next((
                a for a in all_prior
                if _norm(a.get("field")) == _norm(node.get("field_key"))
                and bool(a.get("success", a.get("filled")))
                and not ((a.get("validation") or {}).get("blocking"))
            ), None)
            if already is not None:
                continue
            diag = resolve_stateful_control_diagnostics(controls_after_fields, node)
            control = diag.get("control") if isinstance(diag.get("control"), dict) else None
            if control is None:
                # V243R7: Data Map/DDS can mount its hidden file input only after
                # parent fields commit. If semantic capture has not yet exposed a
                # unique control, resolve the *asset* first and use the phase-aware
                # stable file-input driver as a bounded fallback. This is still
                # exact and browser-grounded: success requires input.files/filename
                # proof and never clicks Save/Create.
                asset = find_upload_asset(
                    input_data or {}, expected, field_key=str(node.get("field_key") or ""),
                    label=" ".join((node.get("semantic_locator") or {}).get("labels") or []),
                    phase=phase, require_explicit=True,
                )
                if asset and asset.get("path"):
                    try:
                        root = await get_active_form_root(page, phase)
                    except Exception:
                        root = None
                    attempt = await upload_file_control(
                        page, root, str(node.get("field_key") or "HIP file upload"),
                        str(asset.get("path")), phase=phase,
                    )
                    attempt["binding_diagnostics"] = diag
                    attempt["fallback"] = "phase-aware-stable-file-input"
                    attempt["asset_resolution"] = {
                        "file": asset.get("file"), "suffix": asset.get("suffix"),
                        "bytes": asset.get("bytes"), "explicit_input_match": True,
                    }
                else:
                    attempt = {
                        "field": node.get("field_key"), "success": False, "filled": False,
                        "reason": (
                            "required upload asset could not be resolved from input.json/uploads; "
                            f"file control binding also unresolved: {diag.get('reason')}"
                        ),
                        "binding_diagnostics": diag,
                        "code": "HIP_REQUIRED_UPLOAD_ASSET_OR_CONTROL_MISSING",
                    }
            else:
                attempt = await attempt_upload_for_control(
                    page, control, input_data, phase=phase,
                    field_key=str(node.get("field_key") or ""), desired_value=str(expected),
                )
                # A stale/re-rendered captured control may fail even though the
                # active drawer contains the stable DDS file input. Retry once via
                # the phase-aware driver before declaring the upload incomplete.
                if not bool(attempt.get("success", attempt.get("filled"))):
                    asset = find_upload_asset(
                        input_data or {}, expected, field_key=str(node.get("field_key") or ""),
                        label=str(control.get("label") or ""), phase=phase, require_explicit=True,
                    )
                    if asset and asset.get("path"):
                        try:
                            root = await get_active_form_root(page, phase)
                        except Exception:
                            root = None
                        fallback_attempt = await upload_file_control(
                            page, root, str(node.get("field_key") or control.get("label") or "HIP file upload"),
                            str(asset.get("path")), phase=phase,
                        )
                        if bool(fallback_attempt.get("success", fallback_attempt.get("filled"))):
                            fallback_attempt["fallback"] = "phase-aware-stable-file-input-after-semantic-upload-failure"
                            fallback_attempt["first_attempt"] = mask_sensitive_data(attempt)
                            attempt = fallback_attempt
            attempt = dict(attempt, autonomous_cycle=cycle, execution_stage="autonomous_file")
            cycle_audit["file_attempts"].append(attempt)
            all_prior.append(attempt)

        # Full graph is the authoritative goal check. It can repair any field
        # that changed during upload/rerender and requires exact read-back.
        try:
            full_result = await _execute_graph(working_graph, all_prior)
        except Exception as exc:
            full_result = {"pass": False, "error": mask_sensitive_string(str(exc)), "attempts": []}
        cycle_audit["full_goal_execution"] = full_result
        controls_after = await capture_stateful_controls(page, phase)
        # Reconcile once more because parent selections/repeatable-row additions
        # can reveal controls that did not exist at the beginning of the cycle.
        working_graph, runtime_input_ledger_after = _supplement_runtime_input_graph(
            working_graph, controls_after, input_data, phase=phase, section=section
        )
        cycle_audit["runtime_input_leaf_ledger_after"] = runtime_input_ledger_after
        shape_after = _control_fingerprint(controls_after)
        cycle_audit["shape_after"] = shape_after
        cycle_audit["control_count_after"] = len(controls_after)
        cycle_audit["bindings_after"] = _binding_summary(controls_after, working_graph, section)
        uncovered_required = _uncovered_required_controls(controls_after, working_graph, section)
        cycle_audit["uncovered_required_controls"] = uncovered_required

        stage = full_result.get("execution_stage_audit") if isinstance(full_result.get("execution_stage_audit"), dict) else {}
        cycle_audit["input_owned_field_coverage"] = {
            "expected": stage.get("input_owned_expected_node_count"),
            "exact": stage.get("input_owned_exact_node_count"),
            "unresolved_node_ids": stage.get("input_owned_unresolved_node_ids") or [],
            "percent": stage.get("input_owned_coverage_percent"),
        }
        file_failures = [
            a for a in cycle_audit.get("file_attempts", [])
            if isinstance(a, dict) and not bool(a.get("success", a.get("filled")))
        ]

        # Golden screenshots are an advisory structural/visual reference; input.json
        # remains the only value authority.  When any runtime input leaf is still
        # unresolved (or exact execution failed), ask the configured visual judge
        # to compare the current live form with the phase golden image.  The result
        # is fed into the next binding-planner cycle rather than directly clicking.
        needs_visual_repair = bool(
            runtime_input_ledger_after.get("unresolved_input_leaves")
            or file_failures
            or not full_result.get("pass")
            or (stage.get("input_owned_unresolved_node_ids") or [])
        )
        if needs_visual_repair and golden_refs and golden_advisor is not None and output_dir is not None:
            try:
                shot = output_dir / f"cycle_{cycle:02d}_golden_compare.png"
                shot.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(shot), full_page=True)
                expected_visual = {
                    "phase": phase, "section": section or "",
                    "runtime_input_values": goal_values,
                    "unresolved_input_leaves": runtime_input_ledger_after.get("unresolved_input_leaves") or [],
                }
                visual = await asyncio.to_thread(
                    golden_advisor.diagnose_visual_state,
                    screenshot=str(shot), golden_screenshots=golden_refs, expected=expected_visual,
                )
                last_golden_visual_feedback = mask_sensitive_data(visual if isinstance(visual, dict) else {"raw": str(visual)})
                cycle_audit["golden_visual_feedback"] = last_golden_visual_feedback
            except Exception as exc:
                cycle_audit["golden_visual_feedback_error"] = mask_sensitive_string(str(exc))[:500]

        # R9: runtime-synthesized semantic nodes are not themselves a failure.
        # They are exactly how the agent learns a live field that the static compiler
        # did not know yet.  If the synthesized node was executed and the final
        # state executor proves every input-owned node exactly with authoritative
        # provenance, the phase goal is achieved and must not be replayed simply
        # because runtime_synthesized_node_count is non-zero.
        synthesized_nodes_verified = bool(
            not runtime_input_ledger_after.get("unresolved_input_leaves")
            and not (stage.get("input_owned_unresolved_node_ids") or [])
            and (
                not strict_live_execution
                or (
                    stage.get("exact_execution_verified") is True
                    and stage.get("authoritative_execution_verified") is True
                )
            )
        )
        cycle_audit["runtime_synthesized_nodes_verified"] = synthesized_nodes_verified
        cycle_audit["runtime_synthesized_nodes_are_learning_not_blockers"] = True
        success = bool(
            full_result.get("pass")
            and not file_failures
            and not uncovered_required
            and bool(runtime_input_ledger_after.get("pass"))
            and synthesized_nodes_verified
            # V232 fail-closed proof: in strict live execution a missing stage flag
            # is NOT success.  This prevents pass=True from a partial/dedicated
            # executor being mistaken for a proven physical form transaction.
            and (not strict_live_execution or stage.get("exact_execution_verified") is True)
            and (not strict_live_execution or stage.get("authoritative_execution_verified") is True)
        )
        cycle_audit["status"] = "goal_achieved" if success else "retry_required"
        if not success:
            cycle_audit["unmet_success_checks"] = _unmet_success_checks(
                full_result, file_failures, uncovered_required, runtime_input_ledger_after,
                synthesized_nodes_verified, stage, strict_live_execution,
            )
        cycles.append(mask_sensitive_data(cycle_audit))

        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_write_json(output_dir / "autonomous_form_runtime_progress.json", {
                "phase": phase, "cycle": cycle, "status": cycle_audit["status"], "cycles": cycles,
            })

        if success:
            result = {
                "schema_version": "hip.autonomous-form-runtime.v1",
                "phase": phase,
                "section": section or "",
                "status": "pass",
                "pass": True,
                "goal_driven": True,
                "adaptive": True,
                "autowebglm_used_for_live_observation": any(
                    bool((c.get("autowebglm_observation") or {}).get("available")) for c in cycles
                ),
                "fixed_selectors_required": False,
                "fixed_coordinates_required": False,
                "cycles": cycles,
                "final_execution": full_result,
                "prior_attempts": all_prior,
            }
            if output_dir is not None:
                safe_write_json(output_dir / "autonomous_form_runtime.json", result)
            return mask_sensitive_data(result)

        # Bounded anti-stagnation. A newly mounted/replaced control shape or a new
        # successful transaction is progress; an identical shape with no success is not.
        successful_now = sum(
            1 for a in (full_result.get("attempts") or [])
            if isinstance(a, dict) and a.get("success") is True
        )
        progressed = bool(shape_after != shape_before or shape_after != last_shape or successful_now)
        if progressed:
            no_progress = 0
        else:
            no_progress += 1
        last_shape = shape_after
        if no_progress >= no_progress_limit:
            break

    needs_input = [
        item
        for cycle in cycles[-1:]
        for item in (cycle.get("uncovered_required_controls") or [])
        if isinstance(item, dict)
    ]
    unresolved_runtime_input = [
        item
        for cycle in cycles[-1:]
        for item in ((cycle.get("runtime_input_leaf_ledger_after") or {}).get("unresolved_input_leaves") or [])
        if isinstance(item, dict)
    ]
    needs_input.extend(unresolved_runtime_input)
    result = {
        "schema_version": "hip.autonomous-form-runtime.v1",
        "phase": phase,
        "section": section or "",
        "status": "needs_input" if needs_input else "failed_closed",
        "pass": False,
        "goal_driven": True,
        "adaptive": True,
        "fixed_selectors_required": False,
        "fixed_coordinates_required": False,
        "cycles": cycles,
        "reason": (
            "one or more nonblank input.json attributes could not be uniquely bound/verified on the live form"
            if needs_input else
            "goal not proven before bounded adaptive/no-progress guard"
        ),
        "needs_input": needs_input,
        "no_progress_cycle_limit": no_progress_limit,
        "prior_attempts": all_prior,
        # Deliberately not ``final_execution``: callers treat that key as the
        # proven goal state. This is diagnostic evidence of the last try only.
        "last_cycle_execution": (cycles[-1].get("full_goal_execution") or {}) if cycles else {},
    }
    result["failure_summary"] = autonomous_failure_summary(result)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_write_json(output_dir / "autonomous_form_runtime.json", result)
    return mask_sensitive_data(result)


def _unmet_success_checks(
    full_result: Dict[str, Any], file_failures: Sequence[Dict[str, Any]],
    uncovered_required: Sequence[Dict[str, Any]], ledger_after: Dict[str, Any],
    synthesized_nodes_verified: bool, stage: Dict[str, Any], strict_live_execution: bool,
) -> List[str]:
    """Name every success condition that failed in one autonomous cycle."""
    unmet: List[str] = []
    if not full_result.get("pass"):
        unmet.append("executor_pass")
    if file_failures:
        unmet.append("file_uploads")
    if uncovered_required:
        unmet.append("required_controls_without_input")
    if not ledger_after.get("pass"):
        unmet.append("runtime_input_leaves_bound")
    if not synthesized_nodes_verified:
        unmet.append("input_owned_nodes_verified")
    if strict_live_execution and stage.get("exact_execution_verified") is not True:
        unmet.append("exact_execution_verified")
    if strict_live_execution and stage.get("authoritative_execution_verified") is not True:
        unmet.append("authoritative_execution_verified")
    return unmet


def autonomous_target_execution(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return the proven final execution, or a failing stub that explains why.

    Phase modules historically used ``result.get("final_execution") or {}``;
    on failure that produced ``failed_attempts: []`` in every blocker message.
    The stub is always ``pass: False`` so it can never be mistaken for proof.
    """
    result = result if isinstance(result, dict) else {}
    final = result.get("final_execution")
    if result.get("pass") and isinstance(final, dict) and final:
        return final
    summary = result.get("failure_summary") or autonomous_failure_summary(result)
    return {
        "pass": False,
        "status": "failed",
        "failed_attempts": list(summary.get("failed_attempts") or []),
        "autonomous_failure_summary": summary,
    }


def autonomous_failure_summary(result: Dict[str, Any], *, max_attempts: int = 8) -> Dict[str, Any]:
    """Compact, value-safe explanation of why an autonomous phase goal failed.

    Phase modules raise with this summary so the Control Center shows the
    failing fields/checks instead of an empty ``failed_attempts`` list.
    """
    cycles = [c for c in (result.get("cycles") or []) if isinstance(c, dict)]
    last = cycles[-1] if cycles else {}
    execution = (
        result.get("last_cycle_execution") or last.get("full_goal_execution")
        or result.get("final_execution") or {}
    )
    failed = [a for a in (execution.get("failed_attempts") or []) if isinstance(a, dict)]
    file_failures = [
        a for a in (last.get("file_attempts") or [])
        if isinstance(a, dict) and not bool(a.get("success", a.get("filled")))
    ]
    return mask_sensitive_data({
        "reason": result.get("reason") or (execution.get("error") if isinstance(execution, dict) else ""),
        "cycles": [
            {"cycle": c.get("cycle"), "status": c.get("status"), "unmet": c.get("unmet_success_checks") or []}
            for c in cycles
        ],
        "executor_error": str(execution.get("error") or "")[:600] if isinstance(execution, dict) else "",
        "failed_attempts": [
            {"field": a.get("field"), "reason": str(a.get("reason") or "")[:300],
             "unresolved_node_ids": a.get("unresolved_node_ids") or []}
            for a in (failed + file_failures)[:max_attempts]
        ],
        "uncovered_required_controls": (last.get("uncovered_required_controls") or [])[:max_attempts],
        "needs_input": (result.get("needs_input") or [])[:max_attempts],
    })
