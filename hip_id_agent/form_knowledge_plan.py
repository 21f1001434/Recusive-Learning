from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .safe_io import safe_write_json
from .security import mask_sensitive_data
from .stateful_form_runtime import compile_phase_state_graph


_SKIP_KEYS = {"llm_form_planner", "sticky_restore", "", None}
_KEY_ALIASES: Dict[str, Sequence[str]] = {
    "flow_description": ("description", "flow_description"),
    "source_system": ("source_system", "application_name", "system_name", "source_application"),
    "source_transport_profile": ("source_transport_profile", "transport_profile_name", "profile_name"),
    "source_document_type": ("source_document_type", "document_type_name_version", "document_type"),
    "target_transport_profile": ("target_transport_profile", "transport_profile_name", "profile_name", "target"),
    "target_document_type": ("target_document_type", "document_type_name_version", "document_type"),
    "condition_attribute": ("attribute_name", "attribute", "condition_attribute"),
    "condition_operator": ("operator", "condition_operator"),
    "condition_value": ("value", "condition_value"),
    "route_condition_attribute": ("attribute_name", "attribute", "route_condition_attribute"),
    "route_condition_operator": ("operator", "route_condition_operator"),
    "route_condition_value": ("value", "route_condition_value"),
    "process_step_type": ("type", "step_type", "process_step_type"),
    "process_step_name": ("name", "step_name", "process_step_name"),
    "process_step_action": ("action", "process_step_action"),
    "mapping_identifier": ("mapping_identifier", "mapping_identifier_name_version", "map_identifier"),
    "rule": ("rule", "rule_name_version", "rule_name"),
    "route_name": ("name", "route_name", "rule_name"),
    "route_document_type": ("document_type_name_version", "route_document_type", "document_type"),
    "action_name": ("name", "action_name"),
    "action_type": ("type", "action_type"),
    "action_target": ("target", "action_target", "target_transport_profile"),
}


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _walk(data: Any, path: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(data, dict):
        for key, value in data.items():
            p = f"{path}.{key}"
            yield p, _norm_key(key), value
            yield from _walk(value, p)
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield from _walk(value, f"{path}[{index}]")


def _scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and str(value).strip() != ""


def _value_at_input_path(payload: Dict[str, Any], path: Any) -> tuple[Any, Optional[str]]:
    """Resolve a canonical KB input path exactly, including list indices.

    Canonical KB paths normally start at the logical object (for example
    ``rule.conditions.rows``), while phase inputs may wrap objects under
    ``objects``. Both shapes are supported. Exact path lookup has priority over
    fuzzy key search so similarly named fields cannot steal each other's value.
    """
    raw = str(path or "").strip().lstrip("$.")
    if not raw:
        return None, None
    candidates = [raw]
    if not raw.startswith("objects."):
        candidates.insert(0, f"objects.{raw}")

    def tokens(value: str) -> List[Any]:
        out: List[Any] = []
        for name, idx in re.findall(r"([^.\[\]]+)|\[(\d+)\]", value):
            out.append(name if name else int(idx))
        return out

    for candidate in candidates:
        cur: Any = payload
        ok = True
        for token in tokens(candidate):
            if isinstance(token, int):
                if not isinstance(cur, list) or token >= len(cur):
                    ok = False
                    break
                cur = cur[token]
            else:
                if not isinstance(cur, dict) or token not in cur:
                    ok = False
                    break
                cur = cur[token]
        if ok:
            return cur, f"$.{candidate}"
    return None, None


def _current_value(payload: Dict[str, Any], key: str, old_value: Any = None) -> tuple[Any, Optional[str]]:
    aliases = {_norm_key(key)} | {_norm_key(v) for v in _KEY_ALIASES.get(key, ())}
    candidates: List[tuple[int, str, Any]] = []
    for path, item_key, value in _walk(payload):
        if not _scalar(value):
            continue
        if item_key in aliases:
            score = 10 if item_key == _norm_key(key) else 5
            if "objects" in path:
                score += 2
            candidates.append((score, path, value))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        _, path, value = candidates[0]
        return value, path
    return old_value, None


def _latest_blueprint(runs_root: Path, phase: str, current_run_dir: Path) -> Optional[Path]:
    filename = f"{phase}_fast_fill_blueprint.json"
    found: List[Path] = []
    if not runs_root.exists():
        return None
    for path in runs_root.glob(f"*/fast_replay_blueprints/{filename}"):
        try:
            if current_run_dir.resolve() in path.resolve().parents:
                continue
        except Exception:
            pass
        if path.is_file():
            found.append(path)
    if not found:
        return None
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    # Prefer a fully passed learning blueprint. Fall back to newest learned one,
    # but retain its confidence/status in the generated plan.
    for path in found:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("verification_status") == "pass":
                return path
        except Exception:
            continue
    return found[0]


def _latest_form_knowledge(runs_root: Path, phase: str, current_run_dir: Path) -> Optional[Path]:
    """Return newest phase-level exploration graph from a previous run."""
    found: List[Path] = []
    if not runs_root.exists():
        return None
    for path in runs_root.glob(f"*/portal_form_knowledge/{phase}_form_knowledge.json"):
        try:
            if current_run_dir.resolve() in path.resolve().parents:
                continue
        except Exception:
            pass
        if path.is_file():
            found.append(path)
    if not found:
        # Single-section phases use a section-suffixed file when merge was unnecessary.
        for path in runs_root.glob(f"*/portal_form_knowledge/{phase}_*_form_knowledge.json"):
            try:
                if current_run_dir.resolve() in path.resolve().parents:
                    continue
            except Exception:
                pass
            if path.is_file():
                found.append(path)
    if not found:
        return None
    found.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return found[0]


def _apply_exploration_knowledge(actions: List[Dict[str, Any]], knowledge: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add observed parent/value dependencies and topologically order actions.

    Only live-observed graph edges are authoritative. LLM dependency analysis remains
    advisory and is deliberately excluded from normal deterministic ordering.
    """
    if not isinstance(knowledge, dict):
        return actions
    edges = knowledge.get("dependency_edges") if isinstance(knowledge.get("dependency_edges"), list) else []
    by_key: Dict[str, List[int]] = {}
    for index, action in enumerate(actions):
        key = _norm_key(action.get("field_key") or str(action.get("knowledge_node") or "").split(".")[-1])
        if key:
            by_key.setdefault(key, []).append(index)
    precedence: set[tuple[int, int]] = set()
    for edge in edges:
        if not isinstance(edge, dict) or not str(edge.get("evidence") or "").startswith("live"):
            continue
        parent_key = _norm_key(str(edge.get("from") or "").split(".")[-1])
        child_key = _norm_key(str(edge.get("to") or "").split(".")[-1])
        value = edge.get("when_parent_value")
        for child_index in by_key.get(child_key, []):
            pre = actions[child_index].setdefault("preconditions", [])
            condition = f"observed-parent:{parent_key}={value}"
            if condition not in pre:
                pre.append(condition)
            actions[child_index]["dependency_source"] = "HIP Portal exploration graph"
        for parent_index in by_key.get(parent_key, []):
            effects = actions[parent_index].setdefault("expected_effects", [])
            child_label = edge.get("child_label") or child_key
            effect = f"when value={value}, {edge.get('relation')} -> {child_label}"
            if effect not in effects:
                effects.append(effect)
            for child_index in by_key.get(child_key, []):
                if parent_index != child_index:
                    precedence.add((parent_index, child_index))

    # Stable Kahn sort: preserve original ordering unless an observed dependency requires a move.
    count = len(actions)
    incoming = {i: 0 for i in range(count)}
    outgoing: Dict[int, List[int]] = {i: [] for i in range(count)}
    for left, right in precedence:
        if right not in outgoing[left]:
            outgoing[left].append(right)
            incoming[right] += 1
    ready = [i for i in range(count) if incoming[i] == 0]
    ready.sort()
    ordered_indices: List[int] = []
    while ready:
        current = ready.pop(0)
        ordered_indices.append(current)
        for child in sorted(outgoing[current]):
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
                ready.sort()
    if len(ordered_indices) != count:
        return actions
    return [actions[i] for i in ordered_indices]


def _dependency_for_step(step: Dict[str, Any]) -> List[str]:
    key = _norm_key(step.get("key"))
    label = str(step.get("label") or "")
    tab = str(step.get("tab") or "")
    deps: List[str] = []
    if tab:
        deps.append(f"tab:{tab}:active")
    if any(token in key for token in ["condition_attribute", "route_condition_attribute"]):
        deps.append("parent:Condition Type=Attributes")
    if any(token in key for token in ["process_step_action", "mapping_identifier", "target_document_type", "rule"]):
        deps.append("parent:Process Step Type selected and dependency controls rendered")
    if key in {"action_target", "target"} or "Target" == label:
        deps.append("parent:Action Type=Route Document")
    if "process step" in label.lower() or "process_step" in key:
        deps.append("row:Process Step exists and accordion expanded")
    return deps


def _effects_for_step(step: Dict[str, Any]) -> List[str]:
    key = _norm_key(step.get("key"))
    effects = ["exact selected/typed value visible in live DOM", "value remains unchanged after section rerender"]
    if key == "process_step_type":
        effects.extend(["Action control becomes visible", "Target Document Type/Rule/Mapping controls become visible when applicable"])
    elif key in {"action_type", "route_action_type"}:
        effects.append("Action Target control becomes visible")
    elif key in {"condition_type", "route_condition_type"}:
        effects.append("Attribute Name/Unit control becomes visible")
    return effects


def _repeatable_actions(phase: str, learned_blueprint: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compile effect-validated row-creation actions from learned form knowledge."""
    plans = learned_blueprint.get("repeatable_section_plan", []) if isinstance(learned_blueprint, dict) else []
    actions: List[Dict[str, Any]] = []
    for raw in plans:
        if not isinstance(raw, dict):
            continue
        count = int(raw.get("row_count_from_input") or 0)
        if count <= 0:
            continue
        section = str(raw.get("section") or "repeatable rows")
        tab = str(raw.get("tab") or section)
        aliases = [str(x) for x in raw.get("aliases", []) if str(x).strip()]
        actions.append(mask_sensitive_data({
            "order": 0,
            "phase": phase,
            "section": tab,
            "operation": "ensure_repeatable_row_count",
            "knowledge_node": f"{phase}.{_norm_key(tab)}.{_norm_key(section)}.row_count",
            "input_path": raw.get("input_path"),
            "expected_value": count,
            "expected_row_count": count,
            "minimum_add_clicks_from_empty": max(0, count - 1),
            "section_aliases": aliases,
            "row_values": raw.get("row_values", []),
            "preconditions": [f"tab:{tab}:active", f"section:{section}:visible"],
            "expected_effects": [
                f"exactly {count} {section} row(s) exist",
                "each + Add click must increase the intended row/component count",
                "new row is expanded when child controls are conditional",
            ],
            "executor": "playwright-mcp",
            "fallback_executor": "python-playwright-dds-overlay-only",
            "verification": [
                "Playwright MCP accessibility snapshot row/component count",
                "live DOM row/component count",
                "Chrome DevTools MCP state delta",
            ],
            "required": True,
            "effect_validation": {
                "before": "count intended rows/components",
                "after": "count must increase by one for each Add click",
                "reject_candidates": ["Add Target", "Reset", "dropdown chevron", "background page Add"],
            },
        }))
    return actions


def _section_gate_actions(phase: str, actions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Append one fail-closed judge gate after each learned section."""
    sections: List[str] = []
    judged_operations = {
        "fill", "select", "upload_file", "radio", "toggle",
        "fill_doc_id_rows", "fill_attributes", "fill_condition_rows",
        "fill_flow_attributes", "fill_process_steps", "ensure_repeatable_row_count",
    }
    for action in actions:
        if action.get("operation") not in judged_operations:
            continue
        section = str(action.get("section") or phase)
        if section not in sections:
            sections.append(section)
    gates: List[Dict[str, Any]] = []
    for section in sections:
        gates.append({
            "order": 0,
            "phase": phase,
            "section": section,
            "operation": "judge_section_and_block_on_failure",
            "knowledge_node": f"{phase}.{_norm_key(section)}.judge_gate",
            "expected_value": "pass",
            "preconditions": [f"all planned actions for section:{section} attempted"],
            "expected_effects": ["section cannot advance until every required expected value is verified"],
            "executor": "playwright-mcp+section-judge",
            "verification": [
                "Playwright MCP accessibility snapshot",
                "live DOM exact-value matrix",
                "Dell AIA gpt-oss-120b text/state judge",
                "Dell AIA vision judge",
            ],
            "required": True,
            "advance_policy": "PASS only; otherwise repair this section and re-judge, then stop if retry budget is exhausted",
        })
    return gates




def _merge_blueprint_sources(
    latest: Optional[Dict[str, Any]],
    brain: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Merge latest run evidence with persistent brain knowledge.

    The long-term brain is primary for semantic ordering/locators because it is
    accumulated across judged runs. The latest blueprint fills any gaps not yet
    promoted into memory. Current input.json still supplies all actual values.
    """
    if not isinstance(latest, dict) and not isinstance(brain, dict):
        return None
    latest = latest if isinstance(latest, dict) else {}
    brain = brain if isinstance(brain, dict) else {}
    merged: Dict[str, Any] = {**latest, **brain}
    index: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for source, is_brain in ((latest.get("field_steps", []), False), (brain.get("field_steps", []), True)):
        for raw in source if isinstance(source, list) else []:
            if not isinstance(raw, dict):
                continue
            ident = (
                _norm_key(raw.get("tab") or raw.get("section")),
                _norm_key(raw.get("key")),
                _norm_key(raw.get("label")),
            )
            prior = index.get(ident, {})
            item = {**prior, **raw} if is_brain else {**raw, **prior}
            # Brain deliberately discards unstable generated selectors. Retain a
            # latest selector only as a fallback when the brain has no stable one.
            if not item.get("selector") and prior.get("selector"):
                item["selector"] = prior.get("selector")
            index[ident] = item
    merged["field_steps"] = sorted(
        index.values(),
        key=lambda row: (int(row.get("order") or 100000), _norm_key(row.get("tab")), _norm_key(row.get("key"))),
    )
    repeatable: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for source in (latest.get("repeatable_section_plan", []), brain.get("repeatable_section_plan", [])):
        for row in source if isinstance(source, list) else []:
            if not isinstance(row, dict):
                continue
            ident = (_norm_key(row.get("tab")), _norm_key(row.get("section")), str(row.get("input_path") or ""))
            repeatable[ident] = {**repeatable.get(ident, {}), **row}
    merged["repeatable_section_plan"] = list(repeatable.values())
    merged["evidence_files"] = list(dict.fromkeys([
        *[str(x) for x in latest.get("evidence_files", []) if x],
        *[str(x) for x in brain.get("evidence_files", []) if x],
    ]))
    merged["memory_source"] = "persistent_portal_brain_plus_latest_run"
    merged["brain_stats"] = brain.get("brain_stats", {})
    if brain.get("verification_status") == "pass":
        merged["verification_status"] = "pass"
    return merged


def _merge_exploration_sources(
    latest: Optional[Dict[str, Any]],
    brain: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(latest, dict) and not isinstance(brain, dict):
        return None
    latest = latest if isinstance(latest, dict) else {}
    brain = brain if isinstance(brain, dict) else {}
    merged: Dict[str, Any] = {**latest, **brain}
    edges: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    for source in (latest.get("dependency_edges", []), brain.get("dependency_edges", [])):
        for edge in source if isinstance(source, list) else []:
            if not isinstance(edge, dict):
                continue
            ident = (
                _norm_key(edge.get("from")),
                _norm_key(edge.get("when_parent_value")),
                _norm_key(edge.get("relation")),
                _norm_key(edge.get("to")),
            )
            edges[ident] = {**edges.get(ident, {}), **edge}
    merged["dependency_edges"] = list(edges.values())
    controls: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for source in (latest.get("control_registry", []), brain.get("control_registry", [])):
        for control in source if isinstance(source, list) else []:
            if not isinstance(control, dict):
                continue
            ident = (
                _norm_key(control.get("section")),
                _norm_key(control.get("key")),
                _norm_key(control.get("label")),
            )
            controls[ident] = {**controls.get(ident, {}), **control}
    merged["control_registry"] = list(controls.values())
    repeatable: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for source in (latest.get("repeatable_rows", []), brain.get("repeatable_rows", [])):
        for row in source if isinstance(source, list) else []:
            if not isinstance(row, dict):
                continue
            ident = (_norm_key(row.get("tab")), _norm_key(row.get("section")), str(row.get("input_path") or ""))
            repeatable[ident] = {**repeatable.get(ident, {}), **row}
    merged["repeatable_rows"] = list(repeatable.values())
    gaps = []
    seen = set()
    for source in (latest.get("unexplored_branches", []), brain.get("unexplored_branches", [])):
        for gap in source if isinstance(source, list) else []:
            if not isinstance(gap, dict):
                continue
            ident = json.dumps(gap, sort_keys=True, default=str)
            if ident not in seen:
                seen.add(ident)
                gaps.append(gap)
    merged["unexplored_branches"] = gaps
    merged["memory_source"] = "persistent_portal_brain_plus_latest_run"
    merged["brain_stats"] = brain.get("brain_stats", {})
    return merged

def _active_surface_action(phase: str, learned_blueprint: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    identity = learned_blueprint.get("page_identity") if isinstance(learned_blueprint, dict) else None
    if not isinstance(identity, dict) or not identity:
        return []
    return [{
        "order": 0,
        "phase": phase,
        "section": "active_surface",
        "operation": "verify_active_surface",
        "knowledge_node": f"{phase}.active_surface.identity_gate",
        "expected_value": identity,
        "preconditions": ["official Playwright MCP connected to the current HIP tab"],
        "expected_effects": [
            "URL matches canonical page pattern",
            "expected create/edit drawer or BizFlow wizard is active",
            "no unrelated background drawer is used for field resolution",
        ],
        "executor": "playwright-mcp+chrome-devtools-mcp",
        "verification": ["Playwright MCP accessibility snapshot", "live URL/title/active-root identity"],
        "required": True,
        "advance_policy": "block and recover the correct active surface on mismatch",
        "canonical_trust": "HIP Unified Deep KB",
    }]


def _canonical_gate_actions(phase: str, learned_blueprint: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    gates = learned_blueprint.get("hard_gates") if isinstance(learned_blueprint, dict) else []
    out: List[Dict[str, Any]] = []
    for gate in gates if isinstance(gates, list) else []:
        if not isinstance(gate, dict):
            continue
        out.append({
            "order": 0,
            "phase": phase,
            "section": gate.get("section") or phase,
            "operation": "enforce_canonical_gate",
            "knowledge_node": gate.get("gate_id") or f"{phase}.gate.{_norm_key(gate.get('label'))}",
            "expected_value": "pass",
            "gate_label": gate.get("label"),
            "blocks_until": gate.get("blocks_until") or [],
            "preconditions": ["all prerequisite deterministic actions attempted"],
            "expected_effects": ["gate evidence is true before dependent action/section progression"],
            "executor": "playwright-mcp+section-judge",
            "verification": ["live DOM exact values", "row count/effect delta", "text judge", "vision judge"],
            "required": True,
            "canonical_trust": gate.get("trust_class") or "canonical",
        })
    return out


def _state_graph_actions(phase: str, graph: Dict[str, Any], learned_steps: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert a phase-local state graph into deterministic runtime actions.

    Learned blueprints contribute locator hints only. Values and ownership always
    come from the current phase object in input.json, preventing cross-object key
    collisions such as Rule.name being planned for Document Type.name.
    """
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for raw in learned_steps:
        if not isinstance(raw, dict):
            continue
        key = _norm_key(raw.get("key") or raw.get("label"))
        if key:
            by_key.setdefault(key, []).append(raw)
    op_map = {
        "fill_text": "fill",
        "select_single": "select",
        "select_multi": "select_multi",
        "select_radio": "radio",
        "upload_file": "upload_file",
    }
    out: List[Dict[str, Any]] = []
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        key = _norm_key(node.get("field_key"))
        hint = (by_key.get(key) or [{}])[0]
        locator = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
        expected = node.get("expected_value")
        if expected is None or expected == "" or expected == []:
            continue
        out.append(mask_sensitive_data({
            "order": 0,
            "phase": phase,
            "section": node.get("section") or phase,
            "operation": op_map.get(str(node.get("action") or ""), str(node.get("action") or "fill")),
            "field_key": key,
            "knowledge_node": node.get("node_id"),
            "input_path": node.get("input_path"),
            "expected_value": expected,
            "label": (locator.get("labels") or [hint.get("label")])[0] if (locator.get("labels") or hint.get("label")) else None,
            "selector": None,
            "fallback_label": (locator.get("labels") or [hint.get("fallback_label") or hint.get("label")])[0] if (locator.get("labels") or hint.get("fallback_label") or hint.get("label")) else None,
            "fallback_name": (locator.get("names") or [hint.get("fallback_name")])[0] if (locator.get("names") or hint.get("fallback_name")) else None,
            "semantic_locator": locator,
            "row_kind": node.get("row_kind"),
            "row_index": node.get("row_index"),
            "preconditions": [f"state-node:{dep}:pass" for dep in node.get("depends_on", [])],
            "expected_effects": [
                "semantic row-scoped control appears after parent state is committed",
                "exact selected/typed value is committed after blur and Angular rerender",
            ],
            "executor": node.get("executor") or "official-playwright-mcp-primary",
            "fallback_executor": node.get("fallback_executor") or "python-playwright-semantic-dds-only",
            "verification": ["live row-scoped DOM exact value", "DDS selected option/chip state", "text judge", "vision judge"],
            "required": bool(node.get("required", True)),
            "locator_strategy": "semantic-section-row-first",
            "portal_action": node.get("action"),
            "condition": {"parent_value": node.get("parent_value")} if node.get("parent_value") not in {None, ""} else None,
            "notes": node.get("notes"),
            "brain_node_id": hint.get("brain_node_id"),
            "brain_confidence": hint.get("brain_confidence"),
            "brain_validated_count": hint.get("brain_validated_count"),
            "selector_stability": "dynamic-selector-current-action-only",
        }))
    return out


_STRUCTURAL_COMPOSITE_OPERATIONS = {
    "fill_doc_id_rows",
    "fill_attributes",
    "fill_condition_rows",
    "fill_flow_attributes",
    "fill_process_steps",
}


def _state_graph_structural_actions(
    phase: str,
    payload: Dict[str, Any],
    learned_steps: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Retain canonical group executors without letting them source values fuzzily.

    The state graph owns every scalar/select value.  Canonical group operations
    still matter because phase-specific runtimes use them to create and scope
    repeatable rows/components.  Their payload is resolved only through the
    reviewed KB's exact ``input_path``; a missing path is never guessed from a
    similarly named object elsewhere in input.json.
    """
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in learned_steps:
        if not isinstance(raw, dict):
            continue
        operation = _norm_key(raw.get("portal_action"))
        if operation not in _STRUCTURAL_COMPOSITE_OPERATIONS:
            continue
        expected, input_path = _value_at_input_path(payload, raw.get("input_path"))
        if input_path is None or expected is None:
            continue
        ident = (operation, input_path)
        if ident in seen:
            continue
        seen.add(ident)
        section = raw.get("tab") or raw.get("section") or phase
        out.append(mask_sensitive_data({
            "order": 0,
            "phase": phase,
            "section": section,
            "operation": operation,
            "field_key": _norm_key(raw.get("key") or operation),
            "knowledge_node": f"{phase}.{_norm_key(section)}.{operation}",
            "input_path": input_path,
            "expected_value": expected,
            "expected_row_count": len(expected) if isinstance(expected, list) else None,
            "label": raw.get("label"),
            "selector": None,
            "fallback_label": raw.get("fallback_label") or raw.get("label"),
            "fallback_name": raw.get("fallback_name"),
            "preconditions": [
                "correct active form surface is verified",
                "parent controls for this repeatable group are committed before child controls",
            ],
            "expected_effects": [
                "exact input-driven row/component count exists",
                "each row is filled through its row-scoped state-graph nodes",
            ],
            "executor": "phase-specific-state-graph-executor+official-playwright-mcp",
            "fallback_executor": "python-playwright-semantic-dds-only",
            "verification": [
                "exact repeatable row/component count",
                "row-scoped live DOM exact values",
                "section judge",
            ],
            "required": bool(raw.get("required", True)),
            "locator_strategy": "canonical-structure+semantic-row-scope",
            "portal_action": raw.get("portal_action"),
            "per_row_fields": raw.get("per_row_fields") or [],
            "step_types_known": raw.get("step_types_known") or [],
            "canonical_count": raw.get("canonical_count", 0),
            "canonical_trust": raw.get("canonical_trust"),
            "value_source": "exact-current-input-path-only",
        }))
    return out


def compile_phase_plan(*, phase: str, payload: Dict[str, Any], learned_blueprint: Optional[Dict[str, Any]], blueprint_path: Optional[Path], exploration_knowledge: Optional[Dict[str, Any]] = None, exploration_path: Optional[Path] = None, brain_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    learned_steps = learned_blueprint.get("field_steps", []) if isinstance(learned_blueprint, dict) else []
    state_graph = compile_phase_state_graph(payload, phase)
    actions: List[Dict[str, Any]] = _active_surface_action(phase, learned_blueprint)
    actions.extend(_repeatable_actions(phase, learned_blueprint))

    if state_graph and state_graph.get("nodes"):
        actions.extend(_state_graph_structural_actions(phase, payload, learned_steps))
        actions.extend(_state_graph_actions(phase, state_graph, learned_steps))
    else:
        # Legacy phases still use the learned blueprint, but their value lookup is
        # exact-path first. New state-graph phase executors can migrate here one by one.
        for raw in learned_steps:
            if not isinstance(raw, dict):
                continue
            key = raw.get("key")
            if key in _SKIP_KEYS:
                continue
            expected, input_path = _value_at_input_path(payload, raw.get("input_path"))
            if expected is None:
                expected, input_path = _current_value(payload, str(key or ""), raw.get("value"))
            if expected is None or (isinstance(expected, str) and not expected.strip()):
                continue
            strategy = str(raw.get("fill_strategy") or "type_or_set_value")
            portal_action = _norm_key(raw.get("portal_action"))
            if portal_action in {"upload_file", "radio", "toggle", "fill_doc_id_rows", "fill_attributes", "fill_condition_rows", "fill_flow_attributes", "fill_process_steps"}:
                operation = portal_action
            else:
                operation = "select" if "select" in strategy or raw.get("dropdown_option_count", 0) else "fill"
            actions.append(mask_sensitive_data({
                "order": len(actions) + 1,
                "phase": phase,
                "section": raw.get("tab") or raw.get("section") or phase,
                "operation": operation,
                "field_key": _norm_key(key or raw.get("label")),
                "knowledge_node": f"{phase}.{_norm_key(raw.get('tab') or 'form')}.{_norm_key(key or raw.get('label'))}",
                "input_path": input_path,
                "expected_value": expected,
                "label": raw.get("label"),
                "selector": raw.get("selector"),
                "fallback_label": raw.get("fallback_label") or raw.get("label"),
                "fallback_name": raw.get("fallback_name"),
                "selector_stability": raw.get("selector_stability"),
                "preconditions": _dependency_for_step(raw),
                "expected_effects": _effects_for_step(raw),
                "executor": "playwright-mcp",
                "fallback_executor": "python-playwright-dds-overlay-only",
                "verification": ["playwright-mcp accessibility snapshot", "live DOM exact value", "Dell AIA text judge", "Dell AIA vision judge"],
                "required": bool(raw.get("required", True)),
                "brain_node_id": raw.get("brain_node_id"),
                "brain_confidence": raw.get("brain_confidence"),
                "brain_validated_count": raw.get("brain_validated_count"),
                "locator_strategy": raw.get("locator_strategy") or ("semantic-first" if raw.get("fallback_label") else "selector-first"),
                "portal_action": raw.get("portal_action"),
                "aliases": raw.get("aliases") or [],
                "do_not_use": raw.get("do_not_use") or [],
                "condition": raw.get("condition"),
                "notes": raw.get("notes"),
                "per_row_fields": raw.get("per_row_fields") or [],
                "step_types_known": raw.get("step_types_known") or [],
                "canonical_count": raw.get("canonical_count", 0),
                "canonical_trust": raw.get("canonical_trust"),
            }))

    actions = _apply_exploration_knowledge(actions, exploration_knowledge)
    actions.extend(_canonical_gate_actions(phase, learned_blueprint))
    actions.extend(_section_gate_actions(phase, actions))
    for index, action in enumerate(actions, start=1):
        action["order"] = index

    source_files = []
    if blueprint_path:
        source_files.append(str(blueprint_path))
    if exploration_path:
        source_files.append(str(exploration_path))
    if isinstance(learned_blueprint, dict):
        source_files.extend(str(p) for p in learned_blueprint.get("evidence_files", []) if p)
    digest = hashlib.sha256(json.dumps(mask_sensitive_data(actions), sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return {
        "schema_version": "hip.deterministic-form-plan.v3",
        "phase": phase,
        "plan_id": f"{phase}-{digest}",
        "plan_source": "HIP Portal learned form knowledge + phase-local input.json state graph",
        "form_knowledge_sources": source_files,
        "form_knowledge_contract": {
            "source": "current phase object in input.json + reviewed HIP Unified KB + persistent portal brain + live state transitions",
            "not_llm_invented": True,
            "llm_role": "judge/recovery only; it cannot supply normal field values or reorder the target path",
            "memory_promotion_rule": "only section-judge-approved observations become validated deterministic knowledge",
            "target_branch_first": True,
            "dynamic_ids_are_not_durable": True,
        },
        "state_graph": state_graph,
        "long_term_memory": brain_metadata or {},
        "learned_verification_status": learned_blueprint.get("verification_status") if isinstance(learned_blueprint, dict) else "no_previous_blueprint",
        "exploration_knowledge_status": exploration_knowledge.get("status") if isinstance(exploration_knowledge, dict) else "no_previous_exploration",
        "knowledge_gaps": exploration_knowledge.get("unexplored_branches", []) if isinstance(exploration_knowledge, dict) else [],
        "page_identity": learned_blueprint.get("page_identity", {}) if isinstance(learned_blueprint, dict) else {},
        "canonical_hard_gates": learned_blueprint.get("hard_gates", []) if isinstance(learned_blueprint, dict) else [],
        "failure_recovery_knowledge": learned_blueprint.get("failure_recovery", []) if isinstance(learned_blueprint, dict) else [],
        "negative_evidence": learned_blueprint.get("negative_evidence", []) if isinstance(learned_blueprint, dict) else [],
        "action_count": len(actions),
        "actions": actions,
        "execution_policy": {
            "primary_executor": "official @playwright/mcp attached through CDP",
            "secondary_observer": "chrome-devtools-mcp",
            "fallback": "Python Playwright only for semantic DDS controls when MCP cannot expose the action",
            "mode": "validated graph replay" if (isinstance(learned_blueprint, dict) and learned_blueprint.get("verification_status") == "pass") else "target branch learning",
            "advance_only_when": ["row-scoped DOM judge pass", "text-model judge pass", "vision judge pass", "no required field blank/Select"],
            "blocked_mutations": ["Save", "Create", "Submit", "Delete", "Deploy", "Publish", "Update"],
        },
    }


def compile_and_attach_plans(
    *,
    phase_input_paths: Dict[str, str],
    runs_root: str | Path,
    current_run_dir: str | Path,
    brain: Any = None,
) -> Dict[str, Any]:
    runs_root = Path(runs_root)
    current_run_dir = Path(current_run_dir)
    out_dir = current_run_dir / "deterministic_plans"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {"schema_version": "hip.deterministic-plan-manifest.v2", "plans": {}}
    for phase, input_path in phase_input_paths.items():
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        bp_path = _latest_blueprint(runs_root, phase, current_run_dir)
        latest_blueprint = None
        if bp_path:
            try:
                latest_blueprint = json.loads(bp_path.read_text(encoding="utf-8"))
            except Exception:
                latest_blueprint = None
        exploration_path = _latest_form_knowledge(runs_root, phase, current_run_dir)
        latest_exploration = None
        if exploration_path:
            try:
                latest_exploration = json.loads(exploration_path.read_text(encoding="utf-8"))
            except Exception:
                latest_exploration = None
        brain_bundle = brain.phase_bundle(phase) if brain is not None else {}
        brain_blueprint = brain_bundle.get("blueprint") if isinstance(brain_bundle, dict) else None
        brain_knowledge = brain_bundle.get("knowledge") if isinstance(brain_bundle, dict) else None
        blueprint = _merge_blueprint_sources(latest_blueprint, brain_blueprint)
        exploration_knowledge = _merge_exploration_sources(latest_exploration, brain_knowledge)
        brain_source = Path(brain_bundle.get("source_path")) if isinstance(brain_bundle, dict) and brain_bundle.get("source_path") else None
        plan = compile_phase_plan(
            phase=phase,
            payload=payload,
            learned_blueprint=blueprint,
            blueprint_path=brain_source or bp_path,
            exploration_knowledge=exploration_knowledge,
            exploration_path=brain_source or exploration_path,
            brain_metadata={
                "enabled": brain is not None,
                "brain_file": str(brain_source or ""),
                "manifest": str(brain_bundle.get("manifest") or "") if isinstance(brain_bundle, dict) else "",
                "stats": brain_bundle.get("stats", {}) if isinstance(brain_bundle, dict) else {},
                "latest_blueprint_fallback": str(bp_path or ""),
                "latest_exploration_fallback": str(exploration_path or ""),
            },
        )
        plan_path = out_dir / f"{phase}_deterministic_plan.json"
        safe_write_json(plan_path, plan)
        payload["_deterministic_plan_path"] = str(plan_path)
        payload["_deterministic_plan"] = plan
        payload["_portal_brain"] = plan.get("long_term_memory", {})
        if isinstance(brain_bundle, dict) and isinstance(brain_bundle.get("knowledge"), dict):
            payload["_portal_brain_phase_knowledge"] = brain_bundle.get("knowledge")
        safe_write_json(Path(input_path), payload, mask=False)
        manifest["plans"][phase] = {
            "path": str(plan_path),
            "plan_id": plan.get("plan_id"),
            "action_count": plan.get("action_count"),
            "knowledge_sources": plan.get("form_knowledge_sources"),
            "learned_verification_status": plan.get("learned_verification_status"),
            "exploration_knowledge_status": plan.get("exploration_knowledge_status"),
            "knowledge_gap_count": len(plan.get("knowledge_gaps") or []),
            "long_term_memory": plan.get("long_term_memory", {}),
        }
    manifest["long_term_memory"] = {
        "enabled": brain is not None,
        "brain_dir": str(getattr(brain, "root", "")) if brain is not None else "",
        "manifest": str(getattr(brain, "manifest_path", "")) if brain is not None else "",
        "contract": "persistent cross-run memory; judged passes promoted, failed observations retained only as negative evidence",
    }
    manifest_path = out_dir / "deterministic_plan_manifest.json"
    safe_write_json(manifest_path, manifest)
    manifest["manifest_file"] = str(manifest_path)
    return manifest
