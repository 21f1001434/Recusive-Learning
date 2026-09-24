from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence


def _norm(value: Any) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def load_plan(input_data: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(input_data, dict):
        return {}
    plan = input_data.get("_deterministic_plan")
    return plan if isinstance(plan, dict) else {}



def state_graph(input_data: Dict[str, Any] | None) -> Dict[str, Any]:
    plan = load_plan(input_data)
    graph = plan.get("state_graph")
    return graph if isinstance(graph, dict) else {}

def field_actions(
    input_data: Dict[str, Any] | None,
    *,
    section: str | None = None,
    phase: str | None = None,
) -> List[Dict[str, Any]]:
    plan = load_plan(input_data)
    rows = []
    section_n = _norm(section)
    phase_n = _norm(phase)
    for action in plan.get("actions", []) if isinstance(plan.get("actions"), list) else []:
        if not isinstance(action, dict) or action.get("operation") not in {"fill", "select", "select_multi", "radio", "upload_file"}:
            continue
        if section_n and section_n not in _norm(action.get("section")) and _norm(action.get("section")) not in section_n:
            continue
        if phase_n and phase_n != _norm(action.get("phase")):
            continue
        rows.append(action)
    rows.sort(key=lambda a: int(a.get("order") or 100000))
    return rows


def ordered_keys(
    input_data: Dict[str, Any] | None,
    defaults: Sequence[str],
    *,
    section: str | None = None,
    phase: str | None = None,
) -> List[str]:
    """Return deterministic field order from persistent form knowledge.

    Existing phase-specific defaults remain as a fallback for fields not yet
    promoted into the long-term brain. This means the plan is actually consumed
    by execution rather than merely written to disk.
    """
    planned = []
    for action in field_actions(input_data, section=section, phase=phase):
        key = _norm(action.get("field_key") or str(action.get("knowledge_node") or "").split(".")[-1])
        if key and key not in planned:
            planned.append(key)
    result = [k for k in planned if k in defaults]
    result.extend(k for k in defaults if k not in result)
    return result


def sort_controls(
    controls: Sequence[Dict[str, Any]],
    input_data: Dict[str, Any] | None,
    *,
    key_fields: Sequence[str],
    section: str | None = None,
    phase: str | None = None,
) -> List[Dict[str, Any]]:
    actions = field_actions(input_data, section=section, phase=phase)
    rank: Dict[str, int] = {}
    label_rank: Dict[str, int] = {}
    for index, action in enumerate(actions):
        key = _norm(action.get("field_key") or str(action.get("knowledge_node") or "").split(".")[-1])
        label = _norm(action.get("label") or action.get("fallback_label"))
        if key and key not in rank:
            rank[key] = index
        if label and label not in label_rank:
            label_rank[label] = index

    def key_for(control: Dict[str, Any]) -> str:
        for field in key_fields:
            value = _norm(control.get(field))
            if value:
                return value
        return ""

    def score(item: tuple[int, Dict[str, Any]]) -> tuple[int, int]:
        original, control = item
        key = key_for(control)
        label = _norm(control.get("label") or control.get("ariaLabel") or control.get("placeholder"))
        if key in rank:
            return rank[key], original
        if label in label_rank:
            return label_rank[label], original
        return 100000 + original, original

    return [control for _, control in sorted(enumerate(list(controls)), key=score)]


def plan_action_for_key(
    input_data: Dict[str, Any] | None,
    key: str,
    *,
    section: str | None = None,
    phase: str | None = None,
) -> Optional[Dict[str, Any]]:
    target = _norm(key)
    for action in field_actions(input_data, section=section, phase=phase):
        action_key = _norm(action.get("field_key") or str(action.get("knowledge_node") or "").split(".")[-1])
        if action_key == target:
            return action
    return None


def annotate_attempt(
    attempt: Dict[str, Any],
    input_data: Dict[str, Any] | None,
    key: str,
    *,
    section: str | None = None,
    phase: str | None = None,
) -> Dict[str, Any]:
    action = plan_action_for_key(input_data, key, section=section, phase=phase)
    if not action:
        attempt.setdefault("deterministic_plan_used", False)
        return attempt
    attempt.update(
        {
            "deterministic_plan_used": True,
            "deterministic_plan_order": action.get("order"),
            "deterministic_plan_node": action.get("knowledge_node"),
            "brain_node_id": action.get("brain_node_id"),
            "brain_confidence": action.get("brain_confidence"),
            "plan_executor": action.get("executor"),
            "plan_preconditions": action.get("preconditions", []),
            "plan_expected_effects": action.get("expected_effects", []),
        }
    )
    return attempt


def plan_summary(input_data: Dict[str, Any] | None) -> Dict[str, Any]:
    plan = load_plan(input_data)
    return {
        "plan_id": plan.get("plan_id"),
        "phase": plan.get("phase"),
        "action_count": plan.get("action_count", 0),
        "long_term_memory": plan.get("long_term_memory", {}),
        "source": plan.get("plan_source"),
    }


def apply_live_exploration_overlay(input_data: Dict[str, Any] | None, knowledge: Dict[str, Any] | None) -> Dict[str, Any]:
    """Patch the current in-memory plan from live safe exploration.

    This is intentionally ephemeral: it lets the same phase benefit from freshly
    observed parent/child behavior before the persistent brain is promoted at the
    end of the judged run. The persistent KB is updated only by the adaptive repair
    engine after deterministic + text + vision approval.
    """
    if not isinstance(input_data, dict) or not isinstance(knowledge, dict):
        return {"status": "skipped"}
    plan = load_plan(input_data)
    actions = plan.get("actions") if isinstance(plan.get("actions"), list) else []
    if not actions:
        return {"status": "no_plan"}
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for action in actions:
        if not isinstance(action, dict):
            continue
        key = _norm(action.get("field_key") or str(action.get("knowledge_node") or "").split(".")[-1])
        if key:
            by_key.setdefault(key, []).append(action)

    dependencies: List[tuple[str, str, str]] = []
    applied = 0
    for edge in knowledge.get("dependency_edges", []) if isinstance(knowledge.get("dependency_edges"), list) else []:
        if not isinstance(edge, dict):
            continue
        relation = _norm(edge.get("relation"))
        if relation not in {"parent_value_reveals_child", "parent_value_enables_child", "reveals_or_enables", "reveals_or_enables_child"}:
            continue
        parent_key = _norm(str(edge.get("from") or "").split(".")[-1])
        child_key = _norm(str(edge.get("to") or "").split(".")[-1])
        parent_value = str(edge.get("when_parent_value") or "").strip()
        if not parent_key or not child_key:
            continue
        dependencies.append((parent_key, child_key, parent_value))
        for parent_action in by_key.get(parent_key, []):
            effect = f"Selecting {parent_value or 'the planned value'} reveals/enables {child_key}"
            parent_action["expected_effects"] = list(dict.fromkeys([*(parent_action.get("expected_effects") or []), effect]))
            parent_action["live_exploration_overlay"] = True
        for child_action in by_key.get(child_key, []):
            pre = f"{parent_key} must be selected as {parent_value}" if parent_value else f"{parent_key} must be selected first"
            child_action["preconditions"] = list(dict.fromkeys([*(child_action.get("preconditions") or []), pre]))
            child_action["live_exploration_overlay"] = True
        applied += 1

    # Stable topological reordering while retaining original order for unrelated fields.
    if dependencies:
        original_rank = {id(a): int(a.get("order") or i + 1) for i, a in enumerate(actions)}
        key_rank: Dict[str, int] = {}
        for i, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            key = _norm(action.get("field_key") or str(action.get("knowledge_node") or "").split(".")[-1])
            if key and key not in key_rank:
                key_rank[key] = i
        changed = True
        guard = 0
        while changed and guard < len(actions) * 2:
            changed = False
            guard += 1
            for parent_key, child_key, _ in dependencies:
                p_positions = [i for i, a in enumerate(actions) if _norm(a.get("field_key") or str(a.get("knowledge_node") or "").split(".")[-1]) == parent_key]
                c_positions = [i for i, a in enumerate(actions) if _norm(a.get("field_key") or str(a.get("knowledge_node") or "").split(".")[-1]) == child_key]
                if p_positions and c_positions and min(p_positions) > min(c_positions):
                    item = actions.pop(min(p_positions))
                    target = min(c_positions)
                    actions.insert(target, item)
                    changed = True
        for i, action in enumerate(actions, start=1):
            if isinstance(action, dict):
                action["order"] = i
    plan["live_exploration_overlay"] = {
        "status": "applied",
        "dependency_count": applied,
        "source": knowledge.get("knowledge_file") or "current live exploration",
    }
    input_data["_deterministic_plan"] = plan
    input_data["_runtime_form_knowledge_overlay"] = knowledge
    return plan["live_exploration_overlay"]
