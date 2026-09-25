from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from pathlib import Path

from playwright.async_api import Page

from .dds_control_driver import (
    close_open_dropdown,
    get_active_form_root,
    assert_active_surface,
    select_dds_combobox,
    select_dds_multiselect,
    read_last_single_select_audit,
    read_last_multiselect_audit,
    restore_filled_values,
    select_radio_option,
    select_radio_value,
    set_text_control,
    set_text_controls_batch,
    set_boolean_control,
    read_last_control_execution,
)
from .security import mask_sensitive_data, mask_sensitive_string
from .deployment_group_policy import resolve_transport_deployment_group
from .repeatable_row_identity import (
    annotate_controls_with_repeatable_bindings,
    row_binding_for_node,
)
from .autonomous_dependency_runtime import (
    apply_dependency_execution_contract,
    replay_speed_profile,
    scheduler_snapshot,
)
from .form_interaction_policy import (
    DEFAULT_FORM_INTERACTION_POLICY,
    derive_execution_profile,
    eligible_child_nodes,
    explicit_event_proof,
    inspect_interaction_state,
    is_parent_node,
    multiselect_exact_set_proof,
    ordering_only_dependencies,
    policy_manifest,
    reveal_hidden_structural_parent,
    scroll_control_into_view,
    structural_dependencies,
    wait_for_stable_bounding_box,
)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _clean_selected_values(values: Any) -> List[str]:
    """Normalize DDS multi-select values and drop presentation-only summaries."""
    out: List[str] = []
    iterable = values if isinstance(values, (list, tuple, set)) else []
    for value in iterable:
        text = _norm_text(value)
        if not text or text.lower() == "select all" or re.fullmatch(r"\d+\s+selected", text, flags=re.I):
            continue
        if text.lower() not in {item.lower() for item in out}:
            out.append(text)
    return out


def _apply_repeatable_row_bindings(
    controls: Sequence[Dict[str, Any]],
    graph: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Annotate controls with generation-safe expected-row identity.

    Physical DOM row indexes are current-generation evidence only.  The binding
    layer upgrades repeatable rows to semantic-anchor identity as soon as a row
    has a committed value, so later Angular reordering cannot redirect another
    input.json row into that physical slot.
    """
    try:
        annotated, proof = annotate_controls_with_repeatable_bindings(controls, graph)
        return annotated, proof
    except Exception as exc:
        # Fail closed at resolution time rather than making capture itself fatal.
        return [dict(c) for c in controls if isinstance(c, dict)], {
            "schema_version": "hip.repeatable-row-binding.v2",
            "pass": False,
            "ambiguities": [{"reason": "row identity reconciliation error", "error": mask_sensitive_string(str(exc))}],
            "bindings": {},
            "physical_to_expected": {},
        }


async def _capture_stateful_controls_bound(page: Page, phase: str, graph: Dict[str, Any], *, document_type: bool = False) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    raw = await (capture_document_type_controls(page) if document_type else capture_stateful_controls(page, phase))
    return _apply_repeatable_row_bindings(raw, graph)


def _repair_document_type_control_semantics(controls: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Repair DDS semantic identity after dynamic Document Type rerenders.

    Dell renders the Document Identifier operation as a section-level control before
    the repeatable identifier rows.  The raw DOM container index therefore labels the
    first real identifier row as index 1.  The input contract, deterministic graph and
    judge all use zero-based *data-row* indexes.  Normalize that offset here and never
    count Operation as a repeatable row.

    Dell also drops the visible ``Usage`` label on later attribute multi-selects.  The
    unique multiple-selection combobox inside an attribute row is structurally Usage.
    """
    prepared: List[Dict[str, Any]] = []
    identifier_raw_rows: List[int] = []
    for raw in controls or []:
        if not isinstance(raw, dict):
            continue
        c = dict(raw)
        key = _norm(c.get("semantic_key"))
        section = _norm(c.get("section"))
        if key in {"document_identifier_derived_from", "document_identifier_value"}:
            try:
                raw_index = int(c.get("row_index"))
            except (TypeError, ValueError):
                raw_index = None
            if raw_index is not None and raw_index not in identifier_raw_rows:
                identifier_raw_rows.append(raw_index)
        prepared.append(c)

    identifier_row_map = {raw_index: logical for logical, raw_index in enumerate(sorted(identifier_raw_rows))}
    repaired: List[Dict[str, Any]] = []
    for c in prepared:
        section = _norm(c.get("section"))
        row_kind = _norm(c.get("row_kind"))
        role = _norm(c.get("role"))
        key = _norm(c.get("semantic_key"))

        if key == "document_identifier_operation":
            c["row_kind"] = ""
            c["row_index"] = None
            c["semantic_inference"] = "document-identifier-section-operation"
        elif key in {"document_identifier_derived_from", "document_identifier_value"}:
            try:
                raw_index = int(c.get("row_index"))
            except (TypeError, ValueError):
                raw_index = None
            if raw_index in identifier_row_map:
                c["row_kind"] = "document_identifier"
                c["row_index"] = identifier_row_map[raw_index]
                c["raw_dom_row_index"] = raw_index
                c["semantic_inference"] = "document-identifier-data-row-normalized"

        if (
            "attribute" in section
            and row_kind == "attribute"
            and str(c.get("selection_mode") or "").lower() == "multiple"
            and (role == "combobox" or str(c.get("tag") or "").lower() == "select")
        ):
            c["semantic_key"] = "attribute_usage"
            if not str(c.get("label") or "").strip():
                c["label"] = "Usage"
            c["semantic_inference"] = "attribute-row-multiple-combobox"

        # DDS keeps every single-select option mounted in the DOM and some portal
        # builds apply a presentation class containing ``item-selected`` to the
        # entire option set.  Never treat that option universe as committed state.
        # A single-select can expose at most one selected value; prefer the live
        # input value, then an exact locked-value match, and otherwise fail closed.
        if str(c.get("selection_mode") or "").lower() == "single" and _norm(c.get("role")) == "combobox":
            selected = _clean_selected_values(c.get("selected_values", []))
            value = _norm_text(c.get("value"))
            locked = _norm_text(c.get("locked_value"))
            if len(selected) > 1:
                preferred = value or locked
                matches = [item for item in selected if preferred and _norm_text(item).lower() == preferred.lower()]
                selected = matches[:1]
                c["selection_state_repaired"] = "single-select-option-universe-collapsed"
            if not value and len(selected) == 1:
                c["value"] = selected[0]
                c["value_source"] = "selected-option"
            c["selected_values"] = selected
            c["selected_count"] = len(selected)
        repaired.append(c)
    return repaired


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]


def summarize_dom_transition_window(window: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce raw DOM event/mutation evidence into graph-relevant state changes."""
    events = window.get("events") if isinstance(window, dict) and isinstance(window.get("events"), list) else []
    mutations = window.get("mutations") if isinstance(window, dict) and isinstance(window.get("mutations"), list) else []
    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    state_changes: List[Dict[str, Any]] = []
    for mutation in mutations:
        if not isinstance(mutation, dict):
            continue
        for control in mutation.get("added_controls") or []:
            if isinstance(control, dict):
                added.append(control)
        for control in mutation.get("removed_controls") or []:
            if isinstance(control, dict):
                removed.append(control)
        attr = str(mutation.get("attribute") or "")
        if attr:
            state_changes.append({
                "attribute": attr, "old_value": mutation.get("old_value"), "new_value": mutation.get("new_value"),
                "target": mutation.get("target"),
            })
    return mask_sensitive_data({
        "event_count": len(events), "mutation_count": len(mutations),
        "event_types": sorted({str(e.get("type")) for e in events if isinstance(e, dict) and e.get("type")}),
        "added_controls": added[:60], "removed_controls": removed[:60], "state_changes": state_changes[:100],
        "added_control_count": len(added), "removed_control_count": len(removed), "state_change_count": len(state_changes),
        "commit_events_seen": any(isinstance(e, dict) and e.get("type") in {"input", "change", "focusout"} for e in events),
    })


async def _mark_dom_transition_cursor(page: Page) -> Dict[str, int]:
    session = getattr(page, "_hip_browser_session", None)
    if session is not None and hasattr(session, "mark_dom_event_cursor"):
        try:
            return await session.mark_dom_event_cursor()
        except Exception:
            pass
    try:
        return await page.evaluate("() => ({event_seq:Number(window.__HIP_DOM_EVENT_SEQ||0), mutation_seq:Number(window.__HIP_DOM_MUTATION_SEQ||0)})")
    except Exception:
        return {"event_seq": 0, "mutation_seq": 0}


async def _collect_dom_transition_window(page: Page, cursor: Dict[str, int]) -> Dict[str, Any]:
    session = getattr(page, "_hip_browser_session", None)
    if session is not None and hasattr(session, "collect_dom_event_window"):
        try:
            return await session.collect_dom_event_window(cursor, clear=False)
        except Exception:
            pass
    try:
        payload = await page.evaluate("""
({eventSeq, mutationSeq}) => ({
 events:(window.__HIP_DOM_EVENT_LOG||[]).filter(x=>Number(x.seq||0)>Number(eventSeq||0)),
 mutations:(window.__HIP_DOM_MUTATION_LOG||[]).filter(x=>Number(x.seq||0)>Number(mutationSeq||0))
})
""", {"eventSeq": int(cursor.get("event_seq", 0)), "mutationSeq": int(cursor.get("mutation_seq", 0))})
        return mask_sensitive_data(payload or {"events": [], "mutations": []})
    except Exception:
        return {"events": [], "mutations": []}


async def _wait_for_dom_transition_activity(page: Page, cursor: Dict[str, int], timeout_ms: int = 1800) -> Dict[str, Any]:
    """Wait for a committed control event or structural DOM mutation after a parent action."""
    deadline = asyncio.get_running_loop().time() + max(0.1, timeout_ms / 1000)
    last: Dict[str, Any] = {"events": [], "mutations": []}
    while asyncio.get_running_loop().time() < deadline:
        last = await _collect_dom_transition_window(page, cursor)
        summary = summarize_dom_transition_window(last)
        if summary.get("mutation_count") or summary.get("commit_events_seen"):
            return last
        await page.wait_for_timeout(60)
    return last


def _binding_generation_digest(
    graph: Dict[str, Any],
    controls: Sequence[Dict[str, Any]],
    node: Dict[str, Any],
    *,
    node_status: Dict[str, bool],
    document_type: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """Return a value-free generation digest for one parent and its eligible children.

    Angular/DDS can replace a control without changing its label/value.  A durable
    action transaction therefore cannot use the old selector as proof that the next
    child belongs to the same DOM generation.  This digest intentionally includes
    semantic identity *and the current selector* for the parent/eligible children,
    while excluding customer values.
    """
    resolver = resolve_document_type_control_diagnostics if document_type else resolve_stateful_control_diagnostics
    parent_id = str(node.get("node_id") or "")
    tracked = [node]
    if parent_id:
        tracked.extend(eligible_child_nodes(graph, parent_id, {**node_status, parent_id: True}))
    rows: List[Dict[str, Any]] = []
    for tracked_node in tracked:
        diag = resolver(controls, tracked_node)
        control = diag.get("control") if isinstance(diag.get("control"), dict) else None
        rows.append({
            "node_id": str(tracked_node.get("node_id") or ""),
            "field_key": str(tracked_node.get("field_key") or ""),
            "row_kind": str(tracked_node.get("row_kind") or ""),
            "row_index": tracked_node.get("row_index"),
            "resolved": bool(control),
            "selector": str((control or {}).get("selector") or ""),
            "semantic_key": str((control or {}).get("semantic_key") or ""),
            "role": str((control or {}).get("role") or ""),
            "section": str((control or {}).get("section") or ""),
        })
    raw = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), {"bindings": rows}


async def _wait_for_post_commit_generation_barrier(
    page: Page,
    graph: Dict[str, Any],
    node: Dict[str, Any],
    *,
    phase: str,
    node_status: Dict[str, bool],
    profile: Dict[str, Any],
    selector_before: str = "",
    document_type: bool = False,
) -> Dict[str, Any]:
    """Wait for Angular/DDS to settle, then rebind the parent in the new DOM generation.

    The barrier is topology-based instead of waiting for *all* mutations to stop;
    background spinners/telemetry can mutate continuously.  Success requires the
    parent to remain exactly committed while the relevant parent/child binding
    topology is identical for N consecutive captures.
    """
    capture = capture_document_type_controls if document_type else (lambda pg: capture_stateful_controls(pg, phase))
    resolver = resolve_document_type_control_diagnostics if document_type else resolve_stateful_control_diagnostics
    equal = _value_equal if document_type else _stateful_value_equal
    stable_needed = max(2, int(profile.get("post_commit_generation_stable_samples", 2) or 2))
    timeout_ms = max(800, int(profile.get("post_commit_generation_timeout_ms", profile.get("transaction_timeout_ms", 5000)) or 5000))
    interval_ms = max(60, int(profile.get("poll_interval_ms", 120) or 120))
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000.0
    last_digest = ""
    stable_count = 0
    samples = 0
    last_controls: List[Dict[str, Any]] = []
    last_diag: Dict[str, Any] = {}
    last_generation: Dict[str, Any] = {}
    selector_after = ""
    while asyncio.get_running_loop().time() < deadline:
        samples += 1
        last_controls = await capture(page)
        last_diag = resolver(last_controls, node)
        parent = last_diag.get("control") if isinstance(last_diag.get("control"), dict) else None
        parent_exact = bool(parent is not None and equal(node, parent))
        selector_after = str((parent or {}).get("selector") or "")
        digest, generation = _binding_generation_digest(
            graph, last_controls, node, node_status=node_status, document_type=document_type
        )
        last_generation = generation
        if parent_exact:
            stable_count = stable_count + 1 if digest == last_digest else 1
            last_digest = digest
            if stable_count >= stable_needed:
                return mask_sensitive_data({
                    "pass": True,
                    "stable": True,
                    "samples": samples,
                    "consecutive_samples": stable_count,
                    "generation_digest": digest,
                    "selector_before": selector_before,
                    "selector_after": selector_after,
                    "selector_replaced": bool(selector_before and selector_after and selector_before != selector_after),
                    "parent_exact": True,
                    "binding": last_diag,
                    "generation": generation,
                    "controls": last_controls,
                })
        else:
            stable_count = 0
            last_digest = ""
        await page.wait_for_timeout(interval_ms)
    return mask_sensitive_data({
        "pass": False,
        "stable": False,
        "samples": samples,
        "consecutive_samples": stable_count,
        "generation_digest": last_digest,
        "selector_before": selector_before,
        "selector_after": selector_after,
        "selector_replaced": bool(selector_before and selector_after and selector_before != selector_after),
        "parent_exact": bool(
            isinstance(last_diag.get("control"), dict) and equal(node, last_diag.get("control"))
        ),
        "binding": last_diag,
        "generation": last_generation,
        "controls": last_controls,
        "reason": "HIP_POST_COMMIT_DOM_GENERATION_NOT_STABLE",
    })




def _apply_validated_flow_pattern_memory(page: Page, graph: Dict[str, Any], phase: str) -> Dict[str, Any]:
    """Attach judge-validated same-family replay knowledge to the live graph.

    Memory is value-free and advisory. It may enable the validated fast profile
    and provide semantic binding identities, but exact live binding and every
    state-transaction proof remain mandatory.
    """
    session = getattr(page, "_hip_browser_session", None)
    memory = getattr(session, "flow_pattern_memory", None) if session is not None else None
    if memory is None or not hasattr(memory, "apply_to_graph"):
        return graph
    try:
        return memory.apply_to_graph(graph, phase=phase)
    except Exception as exc:
        safe = dict(graph)
        safe["flow_pattern_memory_match"] = {
            "status": "error_fail_open",
            "validated_match": False,
            "error": mask_sensitive_string(str(exc)),
        }
        return safe

def _agentq_controller(page: Page) -> Any:
    session = getattr(page, "_hip_browser_session", None)
    return getattr(session, "agentq_controller", None) if session is not None else None


async def _agentq_begin_phase(
    page: Page, *, phase: str, controls: Sequence[Dict[str, Any]], surface_gate: Dict[str, Any] | None = None,
    graph: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    controller = _agentq_controller(page)
    if controller is None or not hasattr(controller, "begin_phase"):
        return {}
    try:
        return await controller.begin_phase(
            page=page, phase=phase, controls=controls, surface_gate=surface_gate or {}, graph=graph or {}
        )
    except Exception as exc:
        return {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))}


async def _agentq_plan_node(
    page: Page, *, phase: str, node: Dict[str, Any], controls: Sequence[Dict[str, Any]],
    binding: Dict[str, Any], surface_gate: Dict[str, Any] | None = None,
    interaction_state: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    controller = _agentq_controller(page)
    if controller is None or not hasattr(controller, "plan_node"):
        return dict(node), {}, {}
    try:
        return await controller.plan_node(
            page=page, phase=phase, node=node, controls=controls, binding=binding,
            surface_gate=surface_gate or {}, interaction_state=interaction_state or {},
        )
    except Exception as exc:
        return dict(node), {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))}, {}


async def _agentq_record_outcome(
    page: Page, *, phase: str, node: Dict[str, Any], representation_before: Dict[str, Any],
    action_plan: Dict[str, Any], outcome: Dict[str, Any], controls_after: Sequence[Dict[str, Any]],
    surface_gate: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    controller = _agentq_controller(page)
    if controller is None or not hasattr(controller, "record_outcome"):
        return {}
    try:
        return await controller.record_outcome(
            page=page, phase=phase, node=node, representation_before=representation_before,
            action_plan=action_plan, outcome=outcome, controls_after=controls_after,
            surface_gate=surface_gate or {},
        )
    except Exception as exc:
        return {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))}


async def _agentq_finalize_phase(
    page: Page, *, phase: str, controls: Sequence[Dict[str, Any]], success: bool, surface_gate: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    controller = _agentq_controller(page)
    if controller is None or not hasattr(controller, "finalize_phase"):
        return {}
    try:
        return await controller.finalize_phase(
            page=page, phase=phase, controls=controls, success=success, surface_gate=surface_gate or {},
        )
    except Exception as exc:
        return {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))}


def phase_object(payload: Dict[str, Any], phase: str) -> Tuple[Dict[str, Any], str]:
    """Return the object owned by *phase* and its canonical JSON path.

    The older planner searched the entire input payload for similarly named keys.
    That allowed Rule/BizFlow values to leak into Document Type plans.  Stateful
    plans are phase-local by construction.
    """
    if not isinstance(payload, dict):
        return {}, "$"
    objects = payload.get("objects") if isinstance(payload.get("objects"), dict) else {}
    phase_key = str(phase or "").strip()
    if isinstance(objects.get(phase_key), dict):
        return objects[phase_key], f"$.objects.{phase_key}"
    if "document_type" in phase_key:
        for key in (phase_key, "document_type"):
            if isinstance(objects.get(key), dict):
                return objects[key], f"$.objects.{key}"
    if "transport_profile" in phase_key:
        for key in (phase_key, "transport_profile"):
            if isinstance(objects.get(key), dict):
                return objects[key], f"$.objects.{key}"
    if isinstance(objects.get(phase_key), dict):
        return objects[phase_key], f"$.objects.{phase_key}"
    return {}, f"$.objects.{phase_key}"


def split_multi_value(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            for token in split_multi_value(item):
                if token and token.lower() not in {x.lower() for x in out}:
                    out.append(token)
        return out
    raw = str(value).strip()
    if not raw:
        return []
    # HIP configuration manuals commonly serialize a DDS multi-select as a
    # comma-separated display string.
    parts = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\s*,\s*", raw)]
    return [p for p in parts if p]


def _node(
    *,
    phase: str,
    section: str,
    field_key: str,
    action: str,
    expected: Any,
    input_path: str,
    names: Sequence[str] = (),
    labels: Sequence[str] = (),
    placeholders: Sequence[str] = (),
    roles: Sequence[str] = (),
    section_aliases: Sequence[str] = (),
    row_kind: str = "",
    row_index: Optional[int] = None,
    required: bool = True,
    depends_on: Sequence[str] = (),
    parent_value: Any = None,
    notes: str = "",
) -> Dict[str, Any]:
    node_id = f"{phase}.{_norm(section)}"
    if row_kind:
        node_id += f".{_norm(row_kind)}[{row_index or 0}]"
    node_id += f".{_norm(field_key)}"
    return {
        "node_id": node_id,
        "phase": phase,
        "section": section,
        "field_key": field_key,
        "action": action,
        "expected_value": expected,
        "input_path": input_path,
        "semantic_locator": {
            "names": list(names),
            "labels": list(labels),
            "placeholders": list(placeholders),
            "roles": list(roles),
            "section_aliases": list(section_aliases),
            "row_kind": row_kind,
            "row_index": row_index,
        },
        "row_kind": row_kind,
        "row_index": row_index,
        "required": bool(required),
        "depends_on": list(depends_on),
        "parent_value": parent_value,
        "verification": "exact_committed_control_value",
        "executor": "autowebglm-browser-session-pyautogui-mcp-primary",
        "fallback_executor": "playwright-mcp-then-python-playwright",
        "notes": notes,
    }


def compile_document_type_state_graph(payload: Dict[str, Any], phase: str) -> Dict[str, Any]:
    obj, root_path = phase_object(payload, phase)
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    def add(**kwargs: Any) -> str:
        n = _node(phase=phase, **kwargs)
        nodes.append(n)
        return str(n["node_id"])

    name_id = add(
        section="Document Type Details", field_key="document_type_name", action="fill_text",
        expected=obj.get("name"), input_path=f"{root_path}.name", names=("name",), labels=("Name",), placeholders=("Name",),
    )
    tx_id = add(
        section="Document Type Details", field_key="transaction_type", action="fill_text",
        expected=obj.get("transaction_type"), input_path=f"{root_path}.transaction_type", names=("transactionType",), labels=("Transaction Type",), placeholders=("Transaction Type",), required=False,
    )
    fmt_id = add(
        section="Document Type Details", field_key="data_format_type", action="select_single",
        expected=obj.get("data_format_type"), input_path=f"{root_path}.data_format_type", labels=("Data Format Type",), placeholders=("Data Format Type",),
    )
    version_id = add(
        section="Document Type Details", field_key="document_type_version", action="verify_only",
        expected=obj.get("version"), input_path=f"{root_path}.version", names=("version",), labels=("Version",), placeholders=("Version",), required=False,
        notes="Portal may render 1 as read-only 1.0; numeric version equivalence is accepted.",
    )
    status_expected = obj.get("status")
    status_id = add(
        section="Document Type Details", field_key="status", action="select_radio",
        expected=("Enabled" if _norm(status_expected) in {"enable", "enabled", "true"} else "Disabled" if status_expected not in {None, ""} else None),
        input_path=f"{root_path}.status", labels=("Enabled", "Disabled", "Status"), required=False,
    )
    desc_id = add(
        section="Document Type Details", field_key="description", action="fill_text",
        expected=obj.get("description"), input_path=f"{root_path}.description", names=("description",), labels=("Description",), placeholders=("Enter description", "Description"), required=False,
    )

    identifier = obj.get("document_identifier") if isinstance(obj.get("document_identifier"), dict) else {}
    op_id = add(
        section="Document Identifier", field_key="document_identifier_operation", action="select_single",
        expected=identifier.get("operation") or obj.get("operation"), input_path=f"{root_path}.document_identifier.operation", labels=("Operation",), placeholders=("Operation",),
        depends_on=(fmt_id,),
    )
    id_rows = identifier.get("rows") if isinstance(identifier.get("rows"), list) else []
    for index, row in enumerate(id_rows):
        if not isinstance(row, dict):
            continue
        parent_id = add(
            section="Document Identifier", field_key="document_identifier_derived_from", action="select_single",
            expected=row.get("derived_from"), input_path=f"{root_path}.document_identifier.rows[{index}].derived_from",
            labels=("Derived From",), placeholders=("Derived From",), row_kind="document_identifier", row_index=index,
            depends_on=(op_id,),
        )
        value_id = add(
            section="Document Identifier", field_key="document_identifier_value", action="fill_text",
            expected=row.get("value"), input_path=f"{root_path}.document_identifier.rows[{index}].value",
            names=("value",), labels=("Value",), placeholders=("Value",), row_kind="document_identifier", row_index=index,
            depends_on=(parent_id,), parent_value=row.get("derived_from"), required=bool(row.get("value")),
        )
        edges.append({
            "edge_id": _stable_id(parent_id, row.get("derived_from"), value_id),
            "from": parent_id, "to": value_id, "relation": "parent_value_reveals_child",
            "when_parent_value": row.get("derived_from"), "source": "current_input_target_branch", "evidence": "live_input_contract",
        })

    attrs = obj.get("attributes_to_configure") if isinstance(obj.get("attributes_to_configure"), list) else []
    for index, row in enumerate(attrs):
        if not isinstance(row, dict):
            continue
        attr_name_id = add(
            section="Attributes To Configure", field_key="attribute_name", action="fill_text",
            expected=row.get("attribute_name"), input_path=f"{root_path}.attributes_to_configure[{index}].attribute_name",
            names=("attributeName",), labels=("Attribute Name",), placeholders=("Attribute Name",), row_kind="attribute", row_index=index,
            depends_on=(fmt_id,),
        )
        derived_id = add(
            section="Attributes To Configure", field_key="attribute_derived_from", action="select_single",
            expected=row.get("derived_from"), input_path=f"{root_path}.attributes_to_configure[{index}].derived_from",
            labels=("Derived From",), placeholders=("Derived From",), row_kind="attribute", row_index=index,
            depends_on=(attr_name_id,),
        )
        usage_values = split_multi_value(row.get("usage"))
        usage_id = add(
            section="Attributes To Configure", field_key="attribute_usage", action="select_multi",
            expected=usage_values, input_path=f"{root_path}.attributes_to_configure[{index}].usage",
            labels=("Usage",), placeholders=("Usage",), row_kind="attribute", row_index=index,
            depends_on=(derived_id,), required=bool(usage_values),
        )
        expression = row.get("expression")
        expression_id = add(
            section="Attributes To Configure", field_key="attribute_expression", action="fill_text",
            expected=expression, input_path=f"{root_path}.attributes_to_configure[{index}].expression",
            names=("expression",), labels=("Expression/Value", "Expression"), placeholders=("Expression/Value", "Expression"),
            row_kind="attribute", row_index=index, depends_on=(derived_id,), parent_value=row.get("derived_from"),
            required=bool(expression), notes="Conditional child; it is resolved only after Derived From is committed.",
        )
        edges.append({
            "edge_id": _stable_id(derived_id, row.get("derived_from"), expression_id),
            "from": derived_id, "to": expression_id, "relation": "parent_value_reveals_child",
            "when_parent_value": row.get("derived_from"), "source": "current_input_target_branch", "evidence": "live_input_contract",
        })
        edges.append({
            "edge_id": _stable_id(derived_id, row.get("derived_from"), usage_id),
            "from": derived_id, "to": usage_id, "relation": "parent_value_enables_child",
            "when_parent_value": row.get("derived_from"), "source": "current_input_target_branch", "evidence": "live_input_contract",
        })

    validation_id = add(
        section="Validation", field_key="validation_type", action="select_single",
        expected=obj.get("validation_type"), input_path=f"{root_path}.validation_type",
        labels=("Validation Type",), placeholders=("Validation Type",), depends_on=(fmt_id,),
    )

    # Remove absent optional values while keeping graph shape metadata in edges.
    executable = [n for n in nodes if n.get("expected_value") is not None and n.get("expected_value") != "" and n.get("expected_value") != []]
    return {
        "schema_version": "hip.stateful-form-graph.v1",
        "graph_id": f"{phase}-{_stable_id(root_path, json.dumps(obj, sort_keys=True, default=str))}",
        "phase": phase,
        "object_path": root_path,
        "object_family": "document_type",
        "strategy": "target-branch-first-then-safe-exploration",
        "nodes": executable,
        "dependency_edges": edges,
        "repeatable_rows": {
            "document_identifier": len(id_rows),
            "attribute": len(attrs),
        },
        "input_accounting": [
            {
                "input_path": f"{root_path}.operation",
                "disposition": "alias_of_executable_control",
                "canonical_input_path": f"{root_path}.document_identifier.operation",
                "reason": "legacy top-level operation duplicates Document Identifier Operation",
            },
            *[
                {
                    "input_path": f"{root_path}.attributes_to_configure[{index}].expression",
                    "disposition": "conditional_child_absent_or_blank",
                    "parent_input_path": f"{root_path}.attributes_to_configure[{index}].derived_from",
                    "reason": "blank expression is correct when the selected Derived From branch does not expose an expression child",
                }
                for index, row in enumerate(attrs)
                if isinstance(row, dict) and row.get("expression") in (None, "")
            ],
        ],
        "target_value_digest": hashlib.sha256(json.dumps(mask_sensitive_data(obj), sort_keys=True, default=str).encode("utf-8")).hexdigest(),
    }



def _compact_graph(phase: str, root_path: str, family: str, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], repeatable_rows: Dict[str, int], obj: Dict[str, Any], input_accounting: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    executable = [n for n in nodes if n.get("expected_value") is not None and n.get("expected_value") != "" and n.get("expected_value") != []]
    existing = {(str(e.get("from")), str(e.get("to"))) for e in edges if isinstance(e, dict)}
    for node in executable:
        for parent in node.get("depends_on", []) or []:
            pair = (str(parent), str(node.get("node_id")))
            if pair in existing:
                continue
            edges.append({
                "edge_id": _stable_id(parent, node.get("parent_value"), node.get("node_id")),
                "from": parent, "to": node.get("node_id"),
                "relation": "precedes_or_enables", "when_parent_value": node.get("parent_value"),
                "source": "current_input_target_branch", "evidence": "input_graph_dependency",
            })
            existing.add(pair)
    return {
        "schema_version": "hip.stateful-form-graph.v1", "graph_id": f"{phase}-{_stable_id(root_path, json.dumps(obj, sort_keys=True, default=str))}",
        "phase": phase, "object_path": root_path, "object_family": family,
        "strategy": "target-branch-first-then-safe-exploration", "nodes": executable,
        "dependency_edges": edges, "repeatable_rows": repeatable_rows,
        "input_accounting": list(input_accounting or []),
        "target_value_digest": hashlib.sha256(json.dumps(mask_sensitive_data(obj), sort_keys=True, default=str).encode("utf-8")).hexdigest(),
    }


def compile_data_map_state_graph(payload: Dict[str, Any], phase: str) -> Dict[str, Any]:
    obj, root = phase_object(payload, phase); nodes=[]; edges=[]
    def add(field, action, value, labels, required=True, *, names=(), roles=(), section_aliases=()):
        nodes.append(_node(
            phase=phase,
            section="Create Map",
            field_key=field,
            action=action,
            expected=value,
            input_path=f"{root}.{field}",
            labels=labels,
            names=names,
            roles=roles,
            section_aliases=section_aliases,
            required=required,
        ))
    add(
        "map_identifier", "fill_text", obj.get("map_identifier"), ("Map Identifier",),
        names=("mapIdentifier",), section_aliases=("Map Reference", "Map Reference :"),
    )
    add(
        "map_identifier_version", "verify_only", obj.get("map_identifier_version"),
        ("Map Identifier Version", "Version"), False,
        names=("mapIdentifierVersion",), section_aliases=("Map Reference", "Map Reference :"),
    )
    add(
        "status", "select_radio",
        "Enabled" if _norm(obj.get("status")) in {"enable", "enabled"} else obj.get("status"),
        ("Enabled", "Disabled", "Status"), False,
        roles=("switch", "radio"), section_aliases=("Map Reference", "Map Reference :"),
    )
    add(
        "map_name", "fill_text", obj.get("map_name"), ("Map Name",),
        names=("mapName",), section_aliases=("Mapping Details", "Mapping Details :"),
    )
    add(
        "map_class", "fill_text", obj.get("map_class"), ("Map Class",),
        names=("mapClass",), section_aliases=("Mapping Details", "Mapping Details :"),
    )
    add(
        "contivo_version", "select_single", obj.get("contivo_version"), ("Contivo Version",),
        roles=("combobox",), section_aliases=("Mapping Details", "Mapping Details :"),
    )
    nodes.append(_node(
        phase=phase,
        section="Create Map",
        field_key="map_data_file",
        action="upload_file",
        expected=obj.get("map_data_file"),
        input_path=f"{root}.map_data_file",
        labels=("Map Data", "Map Data File", "mapData"),
        names=("mapData",),
        section_aliases=("Mapping Details", "Mapping Details :"),
    ))
    for optional in ("input_schema_file", "output_schema_file"):
        if obj.get(optional):
            control_name = "inputSchema" if optional == "input_schema_file" else "outputSchema"
            nodes.append(_node(
                phase=phase,
                section="Create Map",
                field_key=optional,
                action="upload_file",
                expected=obj.get(optional),
                input_path=f"{root}.{optional}",
                labels=(optional.replace("_", " ").title(),),
                names=(control_name,),
                section_aliases=("Map Validation Details", "Map Validation Details :"),
                required=False,
            ))
    return _compact_graph(phase, root, "data_map", nodes, edges, {}, obj)


def compile_rule_state_graph(payload: Dict[str, Any], phase: str) -> Dict[str, Any]:
    obj, root = phase_object(payload, phase); nodes=[]; edges=[]
    def add(section, field, action, value, path, labels, required=True, row_kind="", row_index=None, deps=()):
        n=_node(phase=phase, section=section, field_key=field, action=action, expected=value, input_path=path, labels=labels, required=required, row_kind=row_kind, row_index=row_index, depends_on=deps); nodes.append(n); return n["node_id"]
    details="Rule Details"
    add(details,"rule_name","fill_text",obj.get("name"),f"{root}.name",("Name","Rule Name"))
    add(details,"rule_version","verify_only",obj.get("version"),f"{root}.version",("Version",),False)
    dt=add(details,"document_type_name_version","select_single",obj.get("document_type_name_version"),f"{root}.document_type_name_version",("Document Type Name","Document Type Name (Version)"))
    add(details,"rule_type","select_single",obj.get("rule_type"),f"{root}.rule_type",("Rule Type",))
    add(details,"rule_scope","select_single",obj.get("rule_scope"),f"{root}.rule_scope",("Rule Scope",))
    add(details,"status","select_radio","Enabled" if _norm(obj.get("status")) in {"enable","enabled"} else obj.get("status"),f"{root}.status",("Enabled","Disabled","Status"),False)
    add(details,"description","fill_text",obj.get("description"),f"{root}.description",("Description",),False)
    cond=obj.get("conditions") if isinstance(obj.get("conditions"),dict) else {}; rows=cond.get("rows") if isinstance(cond.get("rows"),list) else []
    add("Conditions","execute_actions_when","select_single",cond.get("execute_actions_when"),f"{root}.conditions.execute_actions_when",("Execute Action(s) When","Execute Actions When"))
    for i,row in enumerate(rows):
        if not isinstance(row,dict): continue
        parent=add("Conditions","condition_type","select_single",row.get("condition_type"),f"{root}.conditions.rows[{i}].condition_type",("Condition Type",),row_kind="condition",row_index=i,deps=(dt,))
        add("Conditions","condition_operator","select_single",row.get("operator"),f"{root}.conditions.rows[{i}].operator",("Operator",),row_kind="condition",row_index=i,deps=(parent,))
        add("Conditions","condition_value","fill_text",row.get("value"),f"{root}.conditions.rows[{i}].value",("Value",),row_kind="condition",row_index=i,deps=(parent,))
        child=add("Conditions","condition_attribute","select_single",row.get("attribute_name_unit") or row.get("attribute_name"),f"{root}.conditions.rows[{i}].attribute_name_unit",("Attribute Name/Unit","Attribute Name"),row_kind="condition",row_index=i,deps=(parent,))
        edges.append({"edge_id":_stable_id(parent,row.get("condition_type"),child),"from":parent,"to":child,"relation":"parent_value_reveals_child","when_parent_value":row.get("condition_type"),"source":"current_input_target_branch","evidence":"live_input_contract"})
    actions=obj.get("actions") if isinstance(obj.get("actions"),dict) else {}
    add("Actions","action_name","fill_text",actions.get("action_name"),f"{root}.actions.action_name",("Action Name","Name"))
    at=add("Actions","action_type","select_single",actions.get("action_type"),f"{root}.actions.action_type",("Action Type","Type"))
    mapping=add("Actions","mapping_identifier_name_version","select_single",actions.get("mapping_identifier_name_version"),f"{root}.actions.mapping_identifier_name_version",("Mapping Identifier Name (Version)","Mapping"),deps=(at,))
    edges.append({"edge_id":_stable_id(at,actions.get("action_type"),mapping),"from":at,"to":mapping,"relation":"parent_value_reveals_child","when_parent_value":actions.get("action_type"),"source":"current_input_target_branch","evidence":"live_input_contract"})
    return _compact_graph(phase, root, "rule", nodes, edges, {"condition":len(rows)}, obj)


def compile_transport_profile_state_graph(payload: Dict[str, Any], phase: str) -> Dict[str, Any]:
    obj, root=phase_object(payload,phase); nodes=[]; edges=[]
    def add(field,action,value,labels,required=True,deps=()):
        n=_node(phase=phase,section="Create Transport Profile",field_key=field,action=action,expected=value,input_path=f"{root}.{field}",labels=labels,required=required,depends_on=deps);nodes.append(n);return n["node_id"]
    def edge(parent, child, when, relation="parent_value_reveals_child"):
        edges.append({"edge_id":_stable_id(parent,when,child),"from":parent,"to":child,"relation":relation,"when_parent_value":when,"source":"current_input_target_branch","evidence":"live_input_contract"})

    st=add("system_type","select_single",obj.get("system_type"),("System Type",))
    system=add("partner_name","select_single",obj.get("partner_name"),("System","Partner","Application","System Name","Partner Name","Application Name"),deps=(st,))
    edge(st, system, obj.get("system_type"))

    profile=add("profile_name","fill_text",obj.get("profile_name"),("Transport Profile Name","Profile Name","Name"),deps=(system,))
    usage=add("profile_usage","select_single",obj.get("profile_usage"),("Profile Usage","Usage"),deps=(profile,))
    deployment_value = resolve_transport_deployment_group(
        interface_type=obj.get("interface_type"),
        profile_usage=obj.get("profile_usage"),
        current_value=obj.get("deployment_group"),
    )
    deployment=add("deployment_group","select_single",deployment_value,("Deployment Group",),deps=(usage,))
    interface=add("interface_type","select_single",obj.get("interface_type"),("Interface Type","Interface"),deps=(deployment,))
    # Deployment Group options come from Profile Usage.  Profile Name, Profile
    # Usage and Interface Type are only filled top to bottom: one of them failing
    # must not skip the others (see form_interaction_policy.ORDERING_ONLY_RELATIONS).
    edge(usage, deployment, obj.get("profile_usage"), "parent_value_enables_child")
    edge(system, profile, None, "field_sequence_gate")
    edge(profile, usage, None, "field_sequence_gate")
    edge(deployment, interface, None, "field_sequence_gate")

    env=add("interface_environment","select_single",obj.get("interface_environment"),("Interface Environment","Available Environment","Environment"),deps=(interface,))
    edge(interface, env, obj.get("interface_type"))
    existing=add("existing_account","select_radio",obj.get("existing_account"),("Existing Account",),deps=(interface,))
    edge(interface, existing, obj.get("interface_type"))

    account=add("existing_account_name","select_single",obj.get("existing_account_name"),("Existing Account Name","Account"),required=bool(obj.get("existing_account_name")),deps=(existing,))
    edge(existing, account, obj.get("existing_account"))
    folder_mode=add("use_existing_folder","select_radio",obj.get("use_existing_folder"),("Use Existing Folder","Existing Folder"),deps=(account if obj.get("existing_account_name") else existing,))
    edge(account if obj.get("existing_account_name") else existing, folder_mode, obj.get("existing_account_name") or obj.get("existing_account"))
    folder=add("subscription_folder","fill_text",obj.get("subscription_folder"),("Subscription Folder","Folder Path","Folder"),required=bool(obj.get("subscription_folder")),deps=(folder_mode,))
    edge(folder_mode, folder, obj.get("use_existing_folder"))

    pattern=add("file_filtering_pattern","fill_text",obj.get("file_filtering_pattern"),("File Filtering Pattern","File Pattern","Pattern"),required=bool(obj.get("file_filtering_pattern")),deps=(interface,))
    post=add("post_transfer_action","select_single",obj.get("post_transfer_action"),("Post Transfer Action","Post Process"),required=bool(obj.get("post_transfer_action")),deps=(interface,))
    compression=add("is_compression_required","select_single",obj.get("is_compression_required"),("Is Compression Required","Compression Required","Splitter Required"),False,deps=(interface,))
    document=add("document_type","select_single",obj.get("document_type"),("Document Type Supported","Document Type","Document(s) Supported"),deps=(interface,))
    for child in (pattern, post, compression, document):
        edge(interface, child, obj.get("interface_type"))

    return _compact_graph(phase,root,"transport_profile",nodes,edges,{},obj)


def compile_bizflow_state_graph(payload: Dict[str, Any], phase: str) -> Dict[str, Any]:
    obj,root=phase_object(payload,phase);nodes=[];edges=[];accounting=[]
    def add(section,field,action,value,path,labels,required=True,row_kind="",row_index=None,deps=(),names=(),placeholders=()):
        n=_node(phase=phase,section=section,field_key=field,action=action,expected=value,input_path=path,labels=labels,names=names,placeholders=placeholders,required=required,row_kind=row_kind,row_index=row_index,depends_on=deps);nodes.append(n);return n["node_id"]
    def account(path, disposition, reason, **extra):
        accounting.append({"input_path": path, "disposition": disposition, "reason": reason, **extra})

    fd=obj.get("flow_details") if isinstance(obj.get("flow_details"),dict) else {}
    if fd.get("current_flow_version") not in (None, ""):
        account(f"{root}.flow_details.current_flow_version", "read_only_display", "Current Flow version is portal-rendered text and is verified by Flow Details evidence/golden state")
    add("Flow Details","business_flow_name","fill_text",fd.get("business_flow_name"),f"{root}.flow_details.business_flow_name",("Business Flow Name","Flow Name"))
    add("Flow Details","flow_description","fill_text",fd.get("flow_description"),f"{root}.flow_details.flow_description",("Flow Description","Description"),False)

    src=obj.get("configure_source") if isinstance(obj.get("configure_source"),dict) else {}
    st=add("Configure Source","source_type","select_single",src.get("source_type"),f"{root}.configure_source.source_type",("Source Type",))
    app=add("Configure Source","source_application","select_single",src.get("source_application"),f"{root}.configure_source.source_application",("Source Application","Application"),deps=(st,))
    src_tp=add("Configure Source","source_transport_profile","select_single",src.get("source_transport_profile"),f"{root}.configure_source.source_transport_profile",("Source Transport Profile",),deps=(app,))
    src_doc=add("Configure Source","source_document_type","select_single",src.get("document_type_name_version"),f"{root}.configure_source.document_type_name_version",("Document Type Name","Document Type Name (Version)","Source Document Type"),deps=(app,))

    fi=obj.get("flow_identifiers") if isinstance(obj.get("flow_identifiers"),dict) else {}; firows=fi.get("conditions") if isinstance(fi.get("conditions"),list) else []
    fiop=add("Configure Source","flow_identifier_operator","select_single",fi.get("operator"),f"{root}.flow_identifiers.operator",("Flow Identifier Operator","Execute When"),deps=(src_doc,))
    for i,row in enumerate(firows):
        if not isinstance(row,dict): continue
        row_doc=add("Configure Source","flow_identifier_document_type","select_single",row.get("document_type_name_version") or src.get("document_type_name_version"),f"{root}.flow_identifiers.conditions[{i}].document_type_name_version",("Document Type Name (Version)","Document Type Name"),row_kind="flow_identifier",row_index=i,deps=(fiop,))
        parent=add("Configure Source","flow_attribute_name","select_single",row.get("attribute_name"),f"{root}.flow_identifiers.conditions[{i}].attribute_name",("Attribute Name",),row_kind="flow_identifier",row_index=i,deps=(row_doc,))
        add("Configure Source","flow_attribute_operator","select_single",row.get("operator"),f"{root}.flow_identifiers.conditions[{i}].operator",("Operator",),row_kind="flow_identifier",row_index=i,deps=(parent,))
        add("Configure Source","flow_attribute_value","fill_text",row.get("value"),f"{root}.flow_identifiers.conditions[{i}].value",("Value",),row_kind="flow_identifier",row_index=i,deps=(parent,))

    tgt=obj.get("configure_targets") if isinstance(obj.get("configure_targets"),dict) else {}
    tt=add("Configure Target(s)","target_type","select_single",tgt.get("target_type"),f"{root}.configure_targets.target_type",("Target Type",))
    ta=add("Configure Target(s)","target_application","select_single",tgt.get("target_application"),f"{root}.configure_targets.target_application",("Target Application","Application"),deps=(tt,))
    add("Configure Target(s)","target_transport_profile","select_single",tgt.get("target_transport_profile"),f"{root}.configure_targets.target_transport_profile",("Target Transport Profile",),deps=(ta,))
    add("Configure Target(s)","target_document_type","select_single",tgt.get("document_type_name_version"),f"{root}.configure_targets.document_type_name_version",("Target Document Type","Document Type Name (Version)","Document Type Name"),deps=(ta,))

    steps=obj.get("process_steps") if isinstance(obj.get("process_steps"),list) else []
    for i,step in enumerate(steps):
        if not isinstance(step,dict):continue
        step_path=f"{root}.process_steps[{i}]"
        if step.get("step_number") not in (None, ""):
            account(f"{step_path}.step_number", "repeatable_row_position", "Step Number identifies the required process-step row order", row_index=i)
        parent=add("Configure Target(s)","process_step_type","select_single",step.get("step_type"),f"{step_path}.step_type",("Process Step Type","Step Type","Type"),row_kind="process_step",row_index=i)
        step_name=add("Configure Target(s)","process_step_name","fill_text",step.get("step_name"),f"{step_path}.step_name",("Process Step Name","Step Name","Name"),row_kind="process_step",row_index=i,deps=(parent,))
        cfg=step.get("configuration") if isinstance(step.get("configuration"),dict) else {}
        source_default=None
        if cfg.get("source_document_type") not in (None, ""):
            source_default=add("Configure Target(s)","process_source_document_type","verify_only",cfg.get("source_document_type"),f"{step_path}.configuration.source_document_type",("Source Document Type","Document Type"),row_kind="process_step",row_index=i,deps=(parent,))
        doc_default=None
        if cfg.get("document_type_version") not in (None, ""):
            doc_default=add("Configure Target(s)","process_document_type_version","verify_only",cfg.get("document_type_version"),f"{step_path}.configuration.document_type_version",("Document Type","Document Type (Version)"),row_kind="process_step",row_index=i,deps=(parent,))
        target_filename=None
        if cfg.get("target_file_name_config") not in (None, ""):
            target_filename=add("Configure Target(s)","process_target_file_name_config","toggle",cfg.get("target_file_name_config"),f"{step_path}.configuration.target_file_name_config",("Target File Name Config","Target File Name Config Yes","Yes"),row_kind="process_step",row_index=i,deps=(parent,))
        field_nodes={}
        for field,label in (("action","Action"),("target_document_type_version","Target Document Type (Version)"),("rule_version","Rule (Version)"),("file_separator","File Separator"),("file_extension","File Extension")):
            if cfg.get(field) not in (None,""):
                action="select_single" if field in {"action","target_document_type_version","rule_version"} else "fill_text"
                dep=target_filename if field in {"file_separator","file_extension"} and target_filename else parent
                field_nodes[field]=add("Configure Target(s)",f"process_{field}",action,cfg.get(field),f"{step_path}.configuration.{field}",(label,),row_kind="process_step",row_index=i,deps=(dep,))
        if cfg.get("add_rule_if_not_listed") not in (None, ""):
            rule_dep=field_nodes.get("rule_version") or parent
            add("Configure Target(s)","process_add_rule_if_not_listed","toggle",cfg.get("add_rule_if_not_listed"),f"{step_path}.configuration.add_rule_if_not_listed",("Add Rule If not Listed Above?","Add Rule If Not Listed Above"),row_kind="process_step",row_index=i,deps=(rule_dep,))
        parts=cfg.get("file_name_parts") if isinstance(cfg.get("file_name_parts"),list) else []
        for j,part in enumerate(parts):
            if not isinstance(part,dict):continue
            part_path=f"{step_path}.configuration.file_name_parts[{j}]"
            part_dep=target_filename or parent
            pn=add("Configure Target(s)","filename_part_number","select_single",part.get("part_number"),f"{part_path}.part_number",("Part Number",),row_kind="filename_part",row_index=j,deps=(part_dep,),required=part.get("part_number") not in (None,""))
            d=add("Configure Target(s)","filename_part_derived_from","select_single",part.get("derived_from"),f"{part_path}.derived_from",("Derived From",),row_kind="filename_part",row_index=j,deps=(pn,))
            add("Configure Target(s)","filename_part_value","fill_text",part.get("value"),f"{part_path}.value",("Value",),row_kind="filename_part",row_index=j,deps=(d,))

    routing=obj.get("configure_routing") if isinstance(obj.get("configure_routing"),dict) else {}; rr=routing.get("rule") if isinstance(routing.get("rule"),dict) else {}
    route_nodes={}
    for field,label,action in (("name","Rule Name","fill_text"),("version","Version","fill_text"),("document_type_name_version","Document Type Name (Version)","select_single"),("rule_type","Rule Type","select_single"),("rule_scope","Rule Scope","select_single")):
        route_nodes[field]=add("Configure Routing",f"routing_rule_{field}",action,rr.get(field),f"{root}.configure_routing.rule.{field}",(label,),required=bool(rr.get(field)))
    if rr.get("status") not in (None, ""):
        add("Configure Routing","routing_rule_status","select_radio",rr.get("status"),f"{root}.configure_routing.rule.status",("Status","Enabled","Disabled"),required=True,deps=(route_nodes.get("rule_scope"),))
    if rr.get("execute_always") not in (None, ""):
        add("Configure Routing","routing_rule_execute_always","toggle",rr.get("execute_always"),f"{root}.configure_routing.rule.execute_always",("Execute Always","Execute Always?"),required=True,deps=(route_nodes.get("rule_scope"),))

    rc=routing.get("conditions") if isinstance(routing.get("conditions"),dict) else {}; rcrows=rc.get("rows") if isinstance(rc.get("rows"),list) else []
    add("Configure Routing","routing_execute_when","select_single",rc.get("execute_actions_when"),f"{root}.configure_routing.conditions.execute_actions_when",("Execute Actions When",))
    for i,row in enumerate(rcrows):
        if not isinstance(row,dict):continue
        parent=add("Configure Routing","route_condition_type","select_single",row.get("condition_type"),f"{root}.configure_routing.conditions.rows[{i}].condition_type",("Condition Type",),row_kind="routing_condition",row_index=i)
        add("Configure Routing","route_condition_operator","select_single",row.get("operator"),f"{root}.configure_routing.conditions.rows[{i}].operator",("Operator",),row_kind="routing_condition",row_index=i,deps=(parent,))
        add("Configure Routing","route_condition_value","fill_text",row.get("value"),f"{root}.configure_routing.conditions.rows[{i}].value",("Value",),row_kind="routing_condition",row_index=i,deps=(parent,))
        add("Configure Routing","route_condition_attribute","select_single",row.get("attribute_name") or row.get("attribute_name_unit"),f"{root}.configure_routing.conditions.rows[{i}].attribute_name",("Attribute Name","Attribute Name/Unit"),row_kind="routing_condition",row_index=i,deps=(parent,))
    ra=routing.get("actions") if isinstance(routing.get("actions"),dict) else {}
    add("Configure Routing","route_action_name","fill_text",ra.get("name"),f"{root}.configure_routing.actions.name",("Action Name","Name"))
    rat=add("Configure Routing","route_action_type","select_single",ra.get("type"),f"{root}.configure_routing.actions.type",("Action Type","Type"))
    add("Configure Routing","route_action_target","select_single",ra.get("target"),f"{root}.configure_routing.actions.target",("Target","Transport Profile"),deps=(rat,))
    return _compact_graph(phase,root,"biz_flow",nodes,edges,{"flow_identifier":len(firows),"process_step":len(steps),"routing_condition":len(rcrows),"routing_action":1 if ra else 0},obj,input_accounting=accounting)

def compile_phase_state_graph(payload: Dict[str, Any], phase: str) -> Dict[str, Any]:
    phase_n = str(phase or "")
    if "document_type" in phase_n:
        return compile_document_type_state_graph(payload, phase)
    if phase_n == "data_map":
        return compile_data_map_state_graph(payload, phase)
    if phase_n == "rule":
        return compile_rule_state_graph(payload, phase)
    if "transport_profile" in phase_n:
        return compile_transport_profile_state_graph(payload, phase)
    if phase_n == "biz_flow":
        return compile_bizflow_state_graph(payload, phase)
    return {}


async def capture_document_type_controls(page: Page) -> List[Dict[str, Any]]:
    """Capture the current stateful Document Type form, including conditional children.

    Unlike the legacy collector this includes custom radio controls, multiple DDS
    dropdown state, fieldset section, and row occurrence. Dynamic DDS IDs are used
    only for the current action and are never the durable semantic identity.
    """
    root = await get_active_form_root(page, "source_document_type")
    script = r"""
(els) => {
  function clean(v){return String(v||'').replace(/\s+/g,' ').trim();}
  function visible(el){if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect();const s=getComputedStyle(el);const host=el.closest('label,.dds__radio-button,.dds__checkbox,[role=radio],[role=checkbox]')||el;const hr=host.getBoundingClientRect?host.getBoundingClientRect():r;return !!(hr.width&&hr.height&&s.display!=='none'&&s.visibility!=='hidden');}
  function css(el){if(el.id)return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;const p=[];let n=el;while(n&&n.nodeType===1&&p.length<9){let x=n.tagName.toLowerCase();const par=n.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===n.tagName);if(same.length>1)x+=`:nth-of-type(${same.indexOf(n)+1})`;}p.unshift(x);n=par;}return p.join(' > ');}
  function label(el){if(el.id){const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`);if(l&&clean(l.innerText||l.textContent))return clean(l.innerText||l.textContent);}const own=el.closest('label');if(own&&clean(own.innerText||own.textContent))return clean(own.innerText||own.textContent);const labelledBy=el.getAttribute('aria-labelledby');if(labelledBy){const l=document.getElementById(labelledBy);if(l&&clean(l.innerText||l.textContent))return clean(l.innerText||l.textContent);}const group=el.closest('.dds__form-group,.dds__input-text__container,app-generic-dropdown,dds-dropdown,dds-radio-button,.dds__radio-button');const l=group&&group.querySelector('label,.dds__label');return clean((l&&(l.innerText||l.textContent))||el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||'');}
  function section(el){const fs=el.closest('fieldset');const lg=fs&&fs.querySelector(':scope > legend');if(lg&&clean(lg.innerText||lg.textContent))return clean(lg.innerText||lg.textContent);let cur=el;while(cur&&cur!==document.body){const h=cur.querySelector(':scope > h1,:scope > h2,:scope > h3,:scope > h4,:scope > [role=heading]');if(h&&clean(h.innerText||h.textContent))return clean(h.innerText||h.textContent);cur=cur.parentElement;}return '';}
  function rowMeta(el, sec){const fs=el.closest('fieldset');if(!fs)return {kind:'',index:null};const low=clean(sec).toLowerCase();
    if(!low.includes('attribute')&&!low.includes('identifier'))return {kind:'',index:null};
    let row=el;while(row&&row.parentElement&&row.parentElement!==fs)row=row.parentElement;
    if(!row||row.parentElement!==fs)return {kind:'',index:null};
    const selector='input:not([type=hidden]),textarea,select,[role=combobox],[role=radio],[role=checkbox],[role=switch]';
    const containers=Array.from(fs.children).filter(x=>x.tagName!=='LEGEND'&&x.querySelector&&x.querySelector(selector));
    const index=containers.indexOf(row);
    return {kind:low.includes('attribute')?'attribute':'document_identifier',index:index>=0?index:null};}
  function semantic(el, sec){const n=clean(el.getAttribute('name')).toLowerCase();const ph=clean(el.getAttribute('placeholder')).toLowerCase();const lab=label(el).toLowerCase();const low=clean(sec).toLowerCase();
    if(low.includes('document type details')){if(n==='name'||lab==='name')return 'document_type_name';if(n==='transactiontype'||lab.includes('transaction type'))return 'transaction_type';if(n==='version'||lab==='version')return 'document_type_version';if(ph.includes('data format')||lab.includes('data format'))return 'data_format_type';if(n==='description'||lab.includes('description'))return 'description';if((el.type==='radio'||el.getAttribute('role')==='radio'||el.type==='checkbox'||el.getAttribute('role')==='switch')&&(lab.includes('enable')||lab.includes('disable')||lab.includes('status')))return 'status';}
    if(low.includes('document identifier')){if(ph==='operation'||lab==='operation')return 'document_identifier_operation';if(ph.includes('derived from')||lab.includes('derived from'))return 'document_identifier_derived_from';if(n==='value'||ph==='value'||lab==='value')return 'document_identifier_value';}
    if(low.includes('attribute')){if(n==='attributename'||ph.includes('attribute name'))return 'attribute_name';if(ph.includes('derived from')||lab.includes('derived from'))return 'attribute_derived_from';const dd=el.closest('dds-dropdown');const multi=!!(dd&&(dd.getAttribute('selection')==='multiple'||dd.querySelector('.dds__dropdown--is-multiple')));if(n==='usage'||ph==='usage'||lab==='usage'||multi)return 'attribute_usage';if(n==='expression'||ph.includes('expression'))return 'attribute_expression';}
    if(low.includes('validation')&&(ph.includes('validation')||lab.includes('validation')))return 'validation_type';return '';}
  function groupInfo(el){
    // A radio's own label is its option ("Yes"); the question it answers
    // ("Existing Account") lives on the group.  Find the smallest container
    // holding only this group's radios and read its label.
    const isRadio=(el.type||'').toLowerCase()==='radio'||el.getAttribute('role')==='radio';
    const isCheck=!isRadio&&el.getAttribute('role')!=='switch'&&((el.type||'').toLowerCase()==='checkbox'||el.getAttribute('role')==='checkbox');
    if(!isRadio&&!isCheck)return {label:'',name:''};
    const name=el.getAttribute('name')||'';
    /* Checkboxes form a group only when several answer one question
       ("Notify On": Success, Failure); a lone checkbox is its own question. */
    const radiosIn=n=>Array.from(n.querySelectorAll(isRadio?'input[type=radio],[role=radio]':'input[type=checkbox]:not([role=switch]),[role=checkbox]'));
    let container=el.parentElement||el;let n=el.parentElement;
    while(n&&n!==document.body){
      const rs=radiosIn(n);
      if(name?!rs.every(x=>(x.getAttribute('name')||'')===name):rs.length>radiosIn(container).length&&radiosIn(container).length>=2)break;
      container=n;
      if(n.matches('[role=radiogroup],[role=group],fieldset'))break;
      n=n.parentElement;
    }
    if(isCheck&&radiosIn(container).length<2)return {label:'',name:''};
    const isOptionLabel=l=>{const f=l.getAttribute&&l.getAttribute('for');if(f){const t=document.getElementById(f);if(t&&(/^(radio|checkbox)$/.test((t.type||'').toLowerCase())||/^(radio|checkbox)$/.test(t.getAttribute('role')||'')))return true;}return !!(l.querySelector&&l.querySelector('input[type=radio],[role=radio],input[type=checkbox],[role=checkbox]'));};
    let text='';
    const lb=container.getAttribute('aria-labelledby');
    if(lb)text=clean(lb.split(/\s+/).map(i=>{const x=document.getElementById(i);return x?(x.innerText||x.textContent):'';}).join(' '));
    if(!text)text=clean(container.getAttribute('aria-label')||'');
    if(!text){const lg=container.querySelector(':scope > legend');if(lg)text=clean(lg.innerText||lg.textContent);}
    if(!text){const c=Array.from(container.querySelectorAll('label,.dds__label,legend')).find(l=>!isOptionLabel(l));if(c)text=clean(c.innerText||c.textContent);}
    if(!text){let p=container;for(let i=0;i<3&&p&&!text;i++){let sib=p.previousElementSibling;while(sib&&!text){if(!sib.querySelector('input,select,textarea,[role=combobox]')){const t=clean(sib.innerText||sib.textContent);if(t&&t.length<80)text=t;}sib=sib.previousElementSibling;}p=p.parentElement;}}
    return {label:text,name};
  }
  function customHost(el){let n=el;while(n&&n!==document.body){const tag=(n.tagName||'').toLowerCase();if(tag.includes('-'))return n;n=n.parentElement;}return null;}
  function semanticPath(el){const parts=[];let n=el;while(n&&n!==document.body&&parts.length<10){const tag=(n.tagName||'').toLowerCase();if(tag){let part=tag;const fc=n.getAttribute&&n.getAttribute('formcontrolname');const role=n.getAttribute&&n.getAttribute('role');if(fc)part+=`[formcontrolname=${fc}]`;else if(role)part+=`[role=${role}]`;parts.unshift(part);}n=n.parentElement;}return parts.join(' > ');}
  return els.filter(visible).map((el,index)=>{const sec=section(el);const row=rowMeta(el,sec);const dd=el.closest('dds-dropdown');const host=customHost(el);const multiple=!!(dd&&(dd.getAttribute('selection')==='multiple'||dd.querySelector('.dds__dropdown--is-multiple')));const listId=el.getAttribute('aria-controls')||el.getAttribute('aria-owns')||'';const ownedList=(listId&&document.getElementById(listId))||(dd&&dd.querySelector('[role=listbox],.dds__dropdown__list,.dds__menu'));const optionRoot=ownedList||dd;const selected=optionRoot?Array.from(optionRoot.querySelectorAll('[role=option]')).filter(x=>x.getAttribute('aria-selected')==='true'||x.getAttribute('data-selected')==='true'||x.getAttribute('aria-checked')==='true'||x.classList.contains('dds__dropdown__item-selected')||x.classList.contains('dds__dropdown__item--selected')).map(x=>clean(x.innerText||x.textContent)).filter(x=>x&&!/^\d+\s+selected$/i.test(x)&&x.toLowerCase()!=='select all'):[];const chips=dd?Array.from(dd.querySelectorAll('.dds__tag,.dds__chip,[class*=selected-value],[class*=selection__label],[class*=dropdown__selection]')).map(x=>clean(x.innerText||x.textContent)).filter(x=>x&&!/^\d+\s+selected$/i.test(x)&&x.toLowerCase()!=='select all'):[];const checked=!!(el.checked||el.getAttribute('aria-checked')==='true');let value=clean(el.value||el.getAttribute('aria-valuetext')||el.getAttribute('data-value')||'');if((el.type==='radio'||el.type==='checkbox'||el.getAttribute('role')==='radio'||el.getAttribute('role')==='checkbox')&&!checked)value='';const r=el.getBoundingClientRect();const st=getComputedStyle(el);const centerX=r.left+r.width/2;const centerY=r.top+r.height/2;const hit=(r.width&&r.height)?document.elementFromPoint(Math.max(0,Math.min(innerWidth-1,centerX)),Math.max(0,Math.min(innerHeight-1,centerY))):null;const inView=centerX>=0&&centerY>=0&&centerX<innerWidth&&centerY<innerHeight;const interactable=!!(r.width&&r.height&&!el.disabled&&el.getAttribute('aria-disabled')!=='true'&&(!inView||hit===el||el.contains(hit)||hit&&hit.contains(el)));const frameworkKey=el.getAttribute('formcontrolname')||el.getAttribute('ng-reflect-name')||el.getAttribute('data-control-name')||el.getAttribute('name')||'';const gi=groupInfo(el);return {index,selector:css(el),group_label:gi.label,group_name:gi.name,semantic_path:semanticPath(el),id:el.id||'',tag:(el.tagName||'').toLowerCase(),component_tag:(host&&host.tagName||'').toLowerCase(),type:el.getAttribute('type')||'',role:el.getAttribute('role')||'',name:el.getAttribute('name')||'',form_control_name:el.getAttribute('formcontrolname')||'',ng_reflect_name:el.getAttribute('ng-reflect-name')||'',framework_key:frameworkKey,data_testid:el.getAttribute('data-testid')||el.getAttribute('data-test-id')||'',aria_controls:el.getAttribute('aria-controls')||'',aria_labelledby:el.getAttribute('aria-labelledby')||'',placeholder:el.getAttribute('placeholder')||'',label:label(el),section:sec,row_kind:row.kind,row_index:row.index,semantic_key:semantic(el,sec),raw_value:value,locked_value:el.getAttribute('data-hip-locked-value')||'',value,selected_values:Array.from(new Set([...selected,...chips])),selection_mode:multiple?'multiple':'single',selected_count:Array.from(new Set([...selected,...chips])).length,checked,required:!!(el.required||el.getAttribute('aria-required')==='true'),disabled:!!(el.disabled||el.getAttribute('aria-disabled')==='true'),readonly:!!el.readOnly,aria_invalid:el.getAttribute('aria-invalid')||'',expanded:el.getAttribute('aria-expanded')||'',interactable,pointer_events:st.pointerEvents||'',z_index:st.zIndex||'',bbox:{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}};});
}
"""
    try:
        controls = await root.locator("input:not([type=hidden]),textarea,select,[role='combobox'],[role='radio'],[role='checkbox'],[role='switch']").evaluate_all(script)
        return mask_sensitive_data(_repair_document_type_control_semantics(controls or []))
    except Exception:
        return []


def _canonical_section(value: str) -> str:
    normalized = _norm(value)
    aliases = {
        "basic_details": "flow_details",
        "create_biz_flow": "flow_details",
        "biz_flow_details": "flow_details",
        "source_details": "configure_source",
        "source_configuration": "configure_source",
        "target_details": "configure_targets",
        "configure_target": "configure_targets",
        "configure_target_s": "configure_targets",
        "target_configuration": "configure_targets",
        "configure_routing_add": "configure_routing",
        "routing_configuration": "configure_routing",
        "create_map": "create_map",
        "add_data_map": "create_map",
        "create_rule": "rule_details",
        "create_transport_profile": "create_transport_profile",
    }
    return aliases.get(normalized, normalized)


def _section_matches(expected: str, actual: str) -> bool:
    a = _canonical_section(expected)
    b = _canonical_section(actual)
    return bool(a and b and (a == b or a in b or b in a))


def _generic_create_section(value: str) -> bool:
    """Return True for form-family labels that intentionally span fieldsets.

    HIP renders Data Map and Transport Profile create drawers as several nested
    fieldsets.  A graph node may be owned by the overall ``Create Map`` surface
    while the live control reports ``Map Reference`` or ``Mapping Details``.
    Treating that as a hard section mismatch caused exact, already-filled values
    to be reported as missing.
    """
    return _canonical_section(value) in {
        "create_map",
        "create_transport_profile",
    }


def _node_section_score(node: Dict[str, Any], actual: str) -> int:
    expected = str(node.get("section") or "")
    # A node owned by the whole create surface (Create Map / Create Transport
    # Profile) says nothing about which fieldset holds it.  Score every live
    # section alike; otherwise a control placed directly under the page heading
    # (e.g. System Type) outranks the right control inside a fieldset (System
    # Name) on section alone.
    if _generic_create_section(expected) and str(actual or "").strip():
        loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
        aliases = [str(v) for v in loc.get("section_aliases", []) if str(v).strip()]
        return 55 if any(_section_matches(alias, actual) for alias in aliases) else 10
    if _section_matches(expected, actual):
        return 55
    loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
    aliases = [str(v) for v in loc.get("section_aliases", []) if str(v).strip()]
    if any(_section_matches(alias, actual) for alias in aliases):
        return 55
    # Generic create-surface nodes are scoped by the already-validated active
    # drawer.  Do not penalize their nested fieldset labels, but keep this as a
    # weak score so a precise subsection alias can disambiguate multiple switches.
    if _generic_create_section(expected) and str(actual or "").strip():
        return 10
    return 0


# A control proven to sit in another repeatable row is never a candidate for
# this row's field, however well its label matches.  Scoring it merely lower
# let "row 2" bind to row 1 when row 2 did not exist yet, overwriting row 1
# (and reporting an already-equal row-1 value as row 2's success).
ROW_EXCLUDED_SCORE = -1000


def control_row_ordinal(control: Dict[str, Any], node: Dict[str, Any]) -> Optional[int]:
    """Best evidence of which data row ``control`` belongs to, for ``node``'s row kind."""
    for key in ("expected_row_index",):
        if control.get(key) is not None:
            try:
                return int(control[key])
            except (TypeError, ValueError):
                pass
    same_kind = _norm(control.get("row_kind")) == _norm(node.get("row_kind"))
    if same_kind and control.get("row_kind_ordinal") is not None:
        try:
            return int(control["row_kind_ordinal"])
        except (TypeError, ValueError):
            pass
    if same_kind and control.get("row_index") is not None and control.get("row_kind_ordinal") is None:
        try:
            return int(control["row_index"])
        except (TypeError, ValueError):
            pass
    if not control.get("row_signature") and not control.get("row_kind") and control.get("label_occurrence") is not None:
        try:
            return int(control["label_occurrence"])
        except (TypeError, ValueError):
            pass
    return None


def control_in_other_row(control: Dict[str, Any], node: Dict[str, Any]) -> bool:
    if node.get("row_index") is None:
        return False
    try:
        wanted = int(node["row_index"])
    except (TypeError, ValueError):
        return False
    actual = control_row_ordinal(control, node)
    return actual is not None and actual != wanted


def _control_score(control: Dict[str, Any], node: Dict[str, Any]) -> int:
    loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
    if control_in_other_row(control, node):
        return ROW_EXCLUDED_SCORE
    score = 0
    if _norm(control.get("semantic_key")) == _norm(node.get("field_key")):
        score += 100
    if _section_matches(str(node.get("section") or ""), str(control.get("section") or "")):
        score += 40
    row_index = node.get("row_index")
    if row_index is not None:
        bound_index = control.get("expected_row_index")
        if bound_index is not None:
            if bound_index == row_index and _norm(control.get("row_kind")) == _norm(node.get("row_kind")):
                score += 165
            else:
                score -= 210
        elif control.get("row_index") == row_index and _norm(control.get("row_kind")) == _norm(node.get("row_kind")):
            # Blank rows may temporarily use current-generation order until the
            # first semantic anchor upgrades them to a durable binding.
            score += 80
        else:
            score -= 100
    names = {_norm(v) for v in loc.get("names", [])}
    labels = {_norm(v) for v in loc.get("labels", [])}
    placeholders = {_norm(v) for v in loc.get("placeholders", [])}
    if _norm(control.get("name")) in names and names:
        score += 30
    clabel = _norm(control.get("label"))
    # Containment only for real words: a radio option "No" is not part of
    # "Acknowledgement Required".
    if labels and any(v == clabel or (len(clabel) >= 4 and (v in clabel or clabel in v)) for v in labels if clabel):
        score += 20
    cph = _norm(control.get("placeholder"))
    if placeholders and cph in placeholders:
        score += 20
    if control.get("disabled") and node.get("action") != "fill_text":
        score -= 20
    return score



def _document_type_control_identity(control: Dict[str, Any]) -> str:
    """Return a durable, value-free identity for a live Document Type control."""
    return "|".join([
        _canonical_section(str(control.get("section") or "")),
        _norm(control.get("row_kind")),
        str(control.get("row_index") if control.get("row_index") is not None else ""),
        _norm(control.get("semantic_key")),
        _norm(control.get("framework_key") or control.get("form_control_name") or control.get("ng_reflect_name")),
        _norm(control.get("role") or control.get("type") or control.get("tag")),
        _norm(control.get("label") or control.get("placeholder") or control.get("name")),
    ])


def _document_type_control_state(control: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "identity": _document_type_control_identity(control),
        "value": _norm_text(control.get("value")),
        "selected_values": sorted(x.lower() for x in _clean_selected_values(control.get("selected_values", []))),
        "checked": bool(control.get("checked")),
        "disabled": bool(control.get("disabled")),
        "readonly": bool(control.get("readonly")),
        "aria_invalid": str(control.get("aria_invalid") or ""),
    }


def _document_type_control_score(control: Dict[str, Any], node: Dict[str, Any]) -> int:
    score = _control_score(control, node)
    loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
    expected_names = {_norm(v) for v in loc.get("names", []) if _norm(v)}
    framework = _norm(control.get("framework_key") or control.get("form_control_name") or control.get("ng_reflect_name"))
    if framework and framework in expected_names:
        score += 55
    if _norm(control.get("component_tag")) in {"dds_dropdown", "app_generic_dropdown"} and str(node.get("action")) in {"select_single", "select_multi"}:
        score += 18
    if control.get("interactable"):
        score += 12
    elif str(node.get("action")) != "verify_only" and not control.get("readonly"):
        score -= 35
    if str(node.get("action")) == "verify_only" and (control.get("readonly") or control.get("disabled")):
        score += 30
    # Radios/checkbox groups learned at runtime: the group label names the
    # question, the option label must equal the wanted value.
    group_label = _norm(control.get("group_label"))
    node_labels = [_norm(v) for v in loc.get("labels", []) if _norm(v)]
    if group_label and node_labels:
        if group_label in node_labels:
            score += 65
        elif any(v in group_label or group_label in v for v in node_labels if len(v) >= 4):
            score += 35
    if str(node.get("action")) in {"select_radio", "toggle"}:
        expected_n = _norm(node.get("expected_value"))
        if expected_n and expected_n in {_norm(control.get("label")), _norm(control.get("value"))}:
            score += 90
        if str(node.get("action")) == "toggle" and (_norm(control.get("role")) in {"switch", "checkbox"} or _norm(control.get("type")) == "checkbox"):
            score += 90
    preferred = [str(x) for x in node.get("_preferred_control_identities", []) if str(x)]
    identity = _document_type_control_identity(control)
    if identity in preferred:
        score += max(8, 30 - preferred.index(identity) * 5)
    return score


def resolve_document_type_control_diagnostics(
    controls: Sequence[Dict[str, Any]],
    node: Dict[str, Any],
    *,
    min_score: int = 80,
    min_margin: int = 16,
) -> Dict[str, Any]:
    """Resolve one control with an auditable confidence margin.

    The old resolver selected the highest-scoring label/row candidate even when a
    second control scored almost the same.  That is unsafe on DDS repeated rows.
    This resolver rejects ambiguous bindings and exposes the evidence used by the
    execution engine and Portal Brain.
    """
    field_key = _norm(node.get("field_key"))
    strict_repeatable = node.get("row_index") is not None and field_key in {
        "attribute_name", "attribute_derived_from", "attribute_usage", "attribute_expression",
        "document_identifier_derived_from", "document_identifier_value",
    }
    pool = [dict(c) for c in controls if isinstance(c, dict)]
    semantic = [c for c in pool if _norm(c.get("semantic_key")) == field_key]
    if semantic:
        pool = semantic
    elif strict_repeatable and field_key == "attribute_usage":
        pool = [
            c for c in pool
            if _norm(c.get("row_kind")) == _norm(node.get("row_kind"))
            and c.get("row_index") == node.get("row_index")
            and str(c.get("selection_mode") or "").lower() == "multiple"
            and (_norm(c.get("role")) == "combobox" or str(c.get("tag") or "").lower() == "select")
            and _section_matches(str(node.get("section") or ""), str(c.get("section") or ""))
        ]
    elif strict_repeatable:
        pool = []
    ranked = sorted(
        [
            {
                "score": _document_type_control_score(c, node),
                "identity": _document_type_control_identity(c),
                "control": c,
            }
            for c in pool
        ],
        key=lambda item: (int(item["score"]), -int((item["control"] or {}).get("index") or 0)),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    margin = int(best["score"] - second["score"]) if best and second else int(best["score"]) if best else 0
    reason = "resolved"
    same_physical_control = bool(
        best and second
        and str((best.get("control") or {}).get("selector") or "")
        and str((best.get("control") or {}).get("selector") or "") == str((second.get("control") or {}).get("selector") or "")
    )
    resolved = bool(best and int(best["score"]) >= min_score and (second is None or margin >= min_margin or same_physical_control))
    if not best:
        reason = "no semantic candidate"
    elif int(best["score"]) < min_score:
        reason = "best candidate below confidence threshold"
    elif second is not None and margin < min_margin and not same_physical_control:
        reason = "ambiguous candidate margin"
    return mask_sensitive_data({
        "resolved": resolved,
        "reason": reason,
        "field_key": node.get("field_key"),
        "node_id": node.get("node_id"),
        "row_kind": node.get("row_kind"),
        "row_index": node.get("row_index"),
        "best_score": int(best["score"]) if best else None,
        "second_score": int(second["score"]) if second else None,
        "score_margin": margin,
        "selected_identity": best["identity"] if resolved and best else "",
        "control": dict(best["control"]) if resolved and best else None,
        "candidates": [
            {
                "score": int(item["score"]),
                "identity": item["identity"],
                "semantic_key": (item["control"] or {}).get("semantic_key"),
                "section": (item["control"] or {}).get("section"),
                "row_kind": (item["control"] or {}).get("row_kind"),
                "row_index": (item["control"] or {}).get("row_index"),
                "framework_key": (item["control"] or {}).get("framework_key"),
                "label": (item["control"] or {}).get("label"),
            }
            for item in ranked[:5]
        ],
    })


def build_document_type_form_state_model(graph: Dict[str, Any], controls: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a one-to-one semantic model from graph nodes to the live form."""
    bindings: List[Dict[str, Any]] = []
    identity_owners: Dict[str, List[str]] = {}
    missing_required: List[str] = []
    ambiguous: List[str] = []
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        diagnostic = resolve_document_type_control_diagnostics(controls, node)
        deferred = bool(node.get("depends_on")) and not diagnostic.get("resolved")
        phase_specific_upload = str(node.get("action") or "") == "upload_file" and not diagnostic.get("resolved")
        status = "resolved" if diagnostic.get("resolved") else "phase_specific_upload" if phase_specific_upload else "deferred_conditional" if deferred else "missing"
        if diagnostic.get("reason") == "ambiguous candidate margin":
            status = "ambiguous"
            ambiguous.append(str(node.get("node_id") or ""))
        if status == "missing" and node.get("required", True):
            missing_required.append(str(node.get("node_id") or ""))
        identity = str(diagnostic.get("selected_identity") or "")
        if identity:
            identity_owners.setdefault(identity, []).append(str(node.get("node_id") or ""))
        bindings.append({
            "node_id": node.get("node_id"),
            "field_key": node.get("field_key"),
            "input_path": node.get("input_path"),
            "section": node.get("section"),
            "row_kind": node.get("row_kind"),
            "row_index": node.get("row_index"),
            "action": node.get("action"),
            "status": status,
            "binding": diagnostic,
        })
    duplicate_bindings = {k: v for k, v in identity_owners.items() if len(v) > 1}
    structural = sorted(
        {
            _document_type_control_identity(c)
            for c in controls
            if isinstance(c, dict) and _norm(c.get("semantic_key"))
        }
    )
    return mask_sensitive_data({
        "schema_version": "hip.doctype-form-state-model.v2",
        "phase": graph.get("phase"),
        "graph_id": graph.get("graph_id"),
        "control_count": len([c for c in controls if isinstance(c, dict)]),
        "bindings": bindings,
        "missing_required": missing_required,
        "ambiguous_nodes": ambiguous,
        "duplicate_bindings": duplicate_bindings,
        "one_to_one_pass": not duplicate_bindings and not ambiguous,
        "structure_fingerprint": hashlib.sha256(json.dumps(structural, sort_keys=True).encode("utf-8")).hexdigest(),
    })


def _snapshot_bound_node_states(
    graph: Dict[str, Any],
    controls: Sequence[Dict[str, Any]],
    node_ids: Sequence[str],
    *,
    reference: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    return _snapshot_node_states(
        graph, controls, node_ids, resolve_document_type_control_diagnostics, _document_type_control_state, reference
    )


def _snapshot_node_states(
    graph: Dict[str, Any],
    controls: Sequence[Dict[str, Any]],
    node_ids: Sequence[str],
    resolver: Callable[..., Dict[str, Any]],
    state_of: Callable[[Dict[str, Any]], Dict[str, Any]],
    reference: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Snapshot committed nodes; ``reference`` is the before-snapshot.

    New controls can make an already committed node ambiguous to re-resolve
    (Step 1's disabled "Source Document Type" and Step 2's disabled "Document
    Type" both show "Default (All/Other)").  That is not a mutation, so the
    after-snapshot falls back to the same physical control the before-snapshot
    used.
    """
    wanted = {str(x) for x in node_ids}
    by_selector = {str(c.get("selector") or ""): c for c in controls if isinstance(c, dict) and c.get("selector")}
    out: Dict[str, Dict[str, Any]] = {}
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if not isinstance(node, dict) or str(node.get("node_id")) not in wanted:
            continue
        node_id = str(node.get("node_id"))
        diag = resolver(controls, node)
        control = diag.get("control") if isinstance(diag.get("control"), dict) else None
        if control is None and reference and node_id in reference:
            control = by_selector.get(str(reference[node_id].get("selector") or ""))
        if control is not None:
            out[node_id] = dict(state_of(control), selector=str(control.get("selector") or ""))
    return out


def _committed_semantics(state: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a control's state that a user would call its value.

    DDS renders option ``aria-selected`` flags lazily (often only while the popup
    is open) and Angular flips ``aria-invalid`` between "" and "false".  The row
    binder also upgrades a row's identity from its position to a semantic anchor
    once the row holds a value.  None of these is a mutation of a committed
    field; the snapshot is already keyed by graph node.
    """
    value = _norm_text(state.get("value")).lower()
    selected = [str(x).strip().lower() for x in state.get("selected_values") or [] if str(x).strip()]
    return {
        "committed": sorted(set(([value] if value else []) + selected)),
        "checked": bool(state.get("checked")),
        "disabled": bool(state.get("disabled")),
        "readonly": bool(state.get("readonly")),
        "invalid": str(state.get("aria_invalid") or "").lower() == "true",
    }


def _protected_state_changes(before: Dict[str, Dict[str, Any]], after: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    for node_id, old in before.items():
        new = after.get(node_id)
        if new is None:
            changes.append({"node_id": node_id, "change": "control_disappeared", "before": old, "after": None})
        elif _committed_semantics(old) != _committed_semantics(new):
            changes.append({"node_id": node_id, "change": "committed_state_changed", "before": old, "after": new})
    return changes


async def _wait_for_document_type_transaction_stable(
    page: Page,
    node: Dict[str, Any],
    *,
    timeout_ms: int = 5000,
    consecutive_samples: int = 2,
    interval_ms: int = 160,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000.0
    last_digest = ""
    stable_count = 0
    last_controls: List[Dict[str, Any]] = []
    last_diag: Dict[str, Any] = {}
    samples = 0
    while asyncio.get_running_loop().time() < deadline:
        samples += 1
        last_controls = await capture_document_type_controls(page)
        last_diag = resolve_document_type_control_diagnostics(last_controls, node)
        control = last_diag.get("control") if isinstance(last_diag.get("control"), dict) else None
        if control is not None and _value_equal(node, control):
            digest_payload = [
                _document_type_control_state(c)
                for c in last_controls
                if isinstance(c, dict) and _norm(c.get("semantic_key"))
            ]
            digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            stable_count = stable_count + 1 if digest == last_digest else 1
            last_digest = digest
            if stable_count >= consecutive_samples:
                return dict(control), last_controls, {
                    "stable": True,
                    "samples": samples,
                    "consecutive_samples": stable_count,
                    "form_state_digest": digest,
                    "binding": last_diag,
                }
        else:
            stable_count = 0
            last_digest = ""
        await page.wait_for_timeout(interval_ms)
    control = last_diag.get("control") if isinstance(last_diag.get("control"), dict) else None
    return dict(control) if control else None, last_controls, {
        "stable": False,
        "samples": samples,
        "consecutive_samples": stable_count,
        "form_state_digest": last_digest,
        "binding": last_diag,
    }


def resolve_control(controls: Sequence[Dict[str, Any]], node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    diagnostic = resolve_document_type_control_diagnostics(controls, node)
    control = diagnostic.get("control") if isinstance(diagnostic, dict) else None
    return dict(control) if isinstance(control, dict) else None


# Each HIP object's natural key. Dell validates it asynchronously and shows
# "... already exists" when the object is already in DEV (see every golden
# screenshot). That message means "reuse the existing object", not "bad input".
NATURAL_KEY_FIELDS = frozenset({
    "map_identifier", "document_type_name", "rule_name", "profile_name", "business_flow_name",
})
_EXISTING_OBJECT_MESSAGE_RE = re.compile(r"already\s+exists?", re.IGNORECASE)


def existing_object_validation(node: Dict[str, Any], interaction_state: Dict[str, Any]) -> bool:
    """True when the only blocking validation is the natural-key duplicate message."""
    return bool(
        interaction_state.get("blockingValidation")
        and str(node.get("field_key") or "").strip().lower() in NATURAL_KEY_FIELDS
        and _EXISTING_OBJECT_MESSAGE_RE.search(str(interaction_state.get("validationMessage") or ""))
    )


def _portal_owned_display_values(control: Dict[str, Any], values: Sequence[str]) -> List[str]:
    """A disabled/read-only control's placeholder is its displayed value.

    Dell renders portal-generated fields (Version, Rule Type, Rule Scope) as
    disabled inputs whose greyed text may be a placeholder. The agent cannot type
    into them, so the displayed text is the only observable value.
    """
    if values or not (control.get("disabled") or control.get("readonly")):
        return list(values)
    placeholder = _norm_text(control.get("placeholder"))
    return [placeholder] if placeholder else []


def _is_boolean_switch(control: Optional[Dict[str, Any]]) -> bool:
    control = control or {}
    return _norm(control.get("role")) in {"switch", "checkbox"} or _norm(control.get("type")) == "checkbox"


def _boolean_intent(value: Any) -> Optional[bool]:
    text = _norm(value)
    if text in {"enable", "enabled", "true", "yes", "on"}:
        return True
    if text in {"disable", "disabled", "false", "no", "off", "not enabled", "not enable"}:
        return False
    return None


def _is_dropdown_control(control: Optional[Dict[str, Any]]) -> bool:
    control = control or {}
    return bool(
        _norm(control.get("role")) in {"combobox", "listbox"}
        or str(control.get("tag") or "").lower() == "select"
        or _norm(control.get("component_tag")) in {"dds_dropdown", "app_generic_dropdown"}
    )


def _effective_action(node: Dict[str, Any], control: Optional[Dict[str, Any]]) -> str:
    """Choose the driver from the live control, not only from the compiled graph.

    input.json cannot know that the portal renders a File Name part's Value as a
    free-text box for "Fixed Text" but as a format dropdown for "Date And Time".
    Typing into a DDS dropdown only fills its search box (the text vanishes on
    blur and no option is committed), and a dropdown driver cannot operate a
    plain text box.  Pick the driver that matches what is on screen.
    """
    action = str(node.get("action") or "")
    control = control or {}
    if action == "fill_text" and _is_dropdown_control(control):
        return "select_multi" if str(control.get("selection_mode") or "").lower() == "multiple" else "select_single"
    if action == "select_single" and not _is_dropdown_control(control):
        tag = str(control.get("tag") or "").lower()
        typ = _norm(control.get("type"))
        if tag == "textarea" or (tag == "input" and typ in {"", "text", "search", "email", "number"}):
            return "fill_text"
    return action


def attempt_actual_value(node: Dict[str, Any], control: Optional[Dict[str, Any]]) -> Any:
    """Committed value as the judge must read it.

    A DDS switch's DOM value is "on" whether or not it is checked, so recording
    it made Status (Enabled) unprovable to the section judge. Boolean switch
    nodes record their checked state in the vocabulary of the expected value.
    """
    control = control or {}
    if node.get("action") == "select_multi":
        return control.get("selected_values")
    if node.get("action") == "select_single" and not _norm_text(control.get("value")):
        # A DDS multi-select used for one value keeps an empty search input and
        # shows the choice as a selected option/chip.
        selected = _clean_selected_values(control.get("selected_values", []))
        if selected:
            return selected[0] if len(selected) == 1 else selected
    wanted = _boolean_intent(node.get("expected_value"))
    if str(node.get("action") or "") in {"select_radio", "toggle"} and _is_boolean_switch(control) and wanted is not None:
        checked = bool(control.get("checked"))
        if checked == wanted:
            return node.get("expected_value")
        return "Enabled" if checked else "Disabled"
    if str(node.get("action") or "") == "select_radio" and _norm(control.get("type") or control.get("role")) == "radio":
        # A radio's DOM value is often an internal token ("true"); the user-facing
        # answer is its option label ("Yes").
        return (control.get("label") or control.get("value")) if control.get("checked") else ""
    return control.get("value")


def _version_equal(expected: Any, actual: Any) -> bool:
    try:
        return float(str(expected).strip()) == float(str(actual).strip())
    except Exception:
        return _norm_text(expected).lower() == _norm_text(actual).lower()


def _semantic_value_key(value: Any) -> str:
    """Normalize enum/display punctuation while preserving the actual value tokens."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[_\-/]+", " ", text)
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _value_equal(node: Dict[str, Any], control: Dict[str, Any]) -> bool:
    expected = node.get("expected_value")
    if node.get("action") == "select_multi":
        wanted = {x.lower() for x in split_multi_value(expected)}
        actual = {x.lower() for x in _clean_selected_values(control.get("selected_values", []))}
        return wanted == actual
    if node.get("action") == "select_radio":
        if _is_boolean_switch(control):
            wanted = _boolean_intent(expected)
            if wanted is not None:
                return bool(control.get("checked")) == wanted
        return bool(control.get("checked")) and _norm(expected) in {_norm(control.get("label")), _norm(control.get("value"))}
    if node.get("action") == "toggle":
        wanted = _boolean_intent(expected)
        if wanted is not None:
            return bool(control.get("checked")) == wanted
    actual = control.get("value")
    if not _norm_text(actual):
        actual = next(iter(_portal_owned_display_values(control, [])), actual)
    if node.get("field_key") == "document_type_version":
        return _version_equal(expected, actual)
    expected_norm = _semantic_value_key(expected)
    actual_norm = _semantic_value_key(actual)
    if expected_norm and expected_norm == actual_norm:
        return True
    if not expected_norm and _norm_text(expected):
        # Punctuation-only values (EDI separators "~", "*", ">") have no
        # semantic key; they must match exactly.
        return _norm_text(expected) == _norm_text(actual)
    # DDS searchable single-selects can blank their inner search input after a
    # real option commit.  Exact selected-option/chip state is authoritative.
    # HIP input contracts also use enum-like values (ELEMENT_IN_PAYLOAD) while
    # the portal renders human labels (Element In Payload), so compare their
    # semantic token sequence rather than punctuation/case.
    if str(control.get("selection_mode") or "").lower() == "single":
        selected = _clean_selected_values(control.get("selected_values", []))
        return any(_semantic_value_key(v) == expected_norm for v in selected if _semantic_value_key(v))
    return False


async def _wait_for_control(page: Page, node: Dict[str, Any], *, graph: Optional[Dict[str, Any]] = None, timeout_ms: int = 5000) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000.0
    last: List[Dict[str, Any]] = []
    while asyncio.get_running_loop().time() < deadline:
        last = await capture_document_type_controls(page)
        if graph is not None:
            last, _ = _apply_repeatable_row_bindings(last, graph)
        control = resolve_control(last, node)
        if control is not None:
            return control, last
        await page.wait_for_timeout(180)
    return None, last


from .environment_faults import ENVIRONMENT_FATAL_CODES, raise_if_environment_fatal  # noqa: E402,F401


def _begin_executor_run(page: Any) -> None:
    try:
        setattr(page, "_hip_executor_run_seq", int(getattr(page, "_hip_executor_run_seq", 0) or 0) + 1)
    except Exception:
        pass


def publish_executor_progress(page: Any, *, phase: str, node: Dict[str, Any], stage: str, retry: int = 0) -> None:
    """Heartbeat for the phase no-progress watchdog.

    The watchdog counts only new DOM states as progress.  Retrying one DDS
    dropdown (open, wait for options, close) revisits states it has already seen,
    and the model decisions around each action change nothing on screen, so a
    slow but advancing fill was cancelled mid-form.  Each new unit of executor
    work (a field, a retry, a completion) now publishes a new token; the number
    of such units is bounded by nodes x retries x cycles.
    """
    try:
        run = int(getattr(page, "_hip_executor_run_seq", 0) or 0)
        seq = int(getattr(page, "_hip_executor_progress_seq", 0) or 0) + 1
        setattr(page, "_hip_executor_progress_seq", seq)
        setattr(page, "_hip_executor_progress", {
            "token": f"{run}|{node.get('node_id')}|{stage}|{retry}",
            "seq": seq, "phase": phase, "field": node.get("field_key"),
            "row_index": node.get("row_index"), "stage": stage, "retry": retry,
        })
    except Exception:
        pass


def _node_time_budget_seconds(profile: Dict[str, Any]) -> float:
    return max(5.0, float(profile.get("node_time_budget_ms", 75000) or 75000) / 1000.0)


async def execute_document_type_state_graph(
    page: Page,
    graph: Dict[str, Any],
    *,
    phase: str,
    max_retries: int = 2,
) -> Dict[str, Any]:
    """Execute the target branch in dependency order and verify every commit.

    The function intentionally recaptures the live form before every node.  Parent
    selection can rerender Angular/DDS controls and create new child fields, so a
    selector captured from the empty form is never reused as durable knowledge.
    """
    graph = _apply_validated_flow_pattern_memory(page, graph, phase)
    graph = apply_dependency_execution_contract(graph, phase=phase)
    dependency_contract = graph.get("dependency_execution_contract") or {}
    if dependency_contract.get("cycle_node_ids"):
        raise RuntimeError(
            "HIP_PARENT_CHILD_DEPENDENCY_CYCLE: "
            + ", ".join(dependency_contract.get("cycle_node_ids") or [])
        )
    attempts: List[Dict[str, Any]] = []
    observed_edges: List[Dict[str, Any]] = []
    node_status: Dict[str, bool] = {}
    ordering_predecessor_failures: Dict[str, List[str]] = {}
    _begin_executor_run(page)
    root = await get_active_form_root(page, phase)
    controls_before = await capture_document_type_controls(page)
    controls_before, initial_row_binding = _apply_repeatable_row_bindings(controls_before, graph)
    surface_gate = await assert_active_surface(page, phase)
    if surface_gate.get("fatal"):
        expected_keys = {_norm(n.get("field_key")) for n in graph.get("nodes", []) if isinstance(n, dict)}
        observed_keys = {_norm(c.get("semantic_key")) for c in controls_before if isinstance(c, dict)}
        if not {k for k in expected_keys if k and k in observed_keys}:
            raise RuntimeError("HIP_PHASE_ACTIVE_FORM_SURFACE_LOST: " + "; ".join(surface_gate.get("fatal") or []))
    agentq_phase_start = await _agentq_begin_phase(
        page, phase=phase, controls=controls_before, surface_gate=surface_gate, graph=graph
    )
    initial_form_model = build_document_type_form_state_model(graph, controls_before)
    execution_profile = derive_execution_profile(initial_form_model, graph)
    interaction_profile = dict(execution_profile.get("profile") or {})
    completed_node_ids: List[str] = []
    node_by_id = {str(n.get("node_id")): n for n in graph.get("nodes", []) if isinstance(n, dict)}

    for order, node in enumerate(graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else [], start=1):
        if not isinstance(node, dict):
            continue
        publish_executor_progress(page, phase=phase, node=node, stage="start")
        expected = node.get("expected_value")
        if expected is None or expected == "" or expected == []:
            node_status[str(node.get("node_id"))] = True
            continue
        # Only a failed structural parent (the field that reveals or enables this
        # one) makes the node impossible.  A failed earlier section or row is an
        # ordering predecessor only: keep filling the rest of the form so the
        # adaptive cycle has to repair just the failed field.
        unmet = [dep for dep in structural_dependencies(graph, node) if node_status.get(str(dep)) is False]
        ordering_unmet = [dep for dep in ordering_only_dependencies(graph, node) if node_status.get(str(dep)) is False]
        if ordering_unmet:
            ordering_predecessor_failures[str(node.get("node_id"))] = [str(d) for d in ordering_unmet]
        if unmet:
            attempts.append({
                "field": node.get("field_key"), "node_id": node.get("node_id"), "input_path": node.get("input_path"),
                "expected_value": expected, "success": False, "filled": False, "reason": f"dependency failed: {unmet}",
                "row_index": node.get("row_index"), "section": node.get("section"), "order": order,
            })
            node_status[str(node.get("node_id"))] = False
            continue

        # Always recapture from the current Angular generation before each node.
        # Cached controls are evidence only; they are never durable locators.
        before = await capture_document_type_controls(page)
        before, row_binding_before = _apply_repeatable_row_bindings(before, graph)
        base_binding = resolve_document_type_control_diagnostics(before, node)
        node, action_model_plan, representation_before = await _agentq_plan_node(
            page, phase=phase, node=node, controls=before, binding=base_binding, surface_gate=surface_gate
        )
        learned_binding = resolve_document_type_control_diagnostics(before, node)
        if learned_binding.get("resolved") and not base_binding.get("resolved"):
            action_model_plan = {
                **action_model_plan,
                "selected_action": "execute_bound_action",
                "selected_reason": "trajectory memory prior produced a unique live binding",
                "selected_control_identity": learned_binding.get("selected_identity"),
                "binding_score": learned_binding.get("best_score"),
                "binding_margin": learned_binding.get("score_margin"),
                "memory_rebound": True,
            }
        selected_model_action = str(action_model_plan.get("selected_action") or "")
        if selected_model_action == "stop_surface_lost":
            raise RuntimeError("HIP_PHASE_ACTIVE_FORM_SURFACE_LOST: action model rejected the current Document Type surface")
        if selected_model_action in {"wait_for_rerender", "rebind_after_rerender"}:
            await page.wait_for_timeout(int(interaction_profile.get("poll_interval_ms", 120)) * 2)
            before = await capture_document_type_controls(page)
            before, row_binding_before = _apply_repeatable_row_bindings(before, graph)
        if selected_model_action == "recover_stale_overlay":
            session = getattr(page, "_hip_browser_session", None)
            candidate_control = base_binding.get("control") if isinstance(base_binding.get("control"), dict) else {}
            selector_hint = str(candidate_control.get("selector") or "")
            if session is not None and selector_hint and hasattr(session, "_neutralize_stale_dds_loading_overlay"):
                await session._neutralize_stale_dds_loading_overlay(
                    target_selector=selector_hint, reason=f"agentq action model before {node.get('field_key')}"
                )
                before = await capture_document_type_controls(page)
                before, row_binding_before = _apply_repeatable_row_bindings(before, graph)
        control, before, binding_before, preparation = await _prepare_phase_control_for_action(
            page, graph, node, phase=phase, controls=before, profile=interaction_profile, document_type=True
        )
        parent_recommit = {}
        if control is None and node.get("depends_on"):
            parent_recommit = await _recommit_structural_parent_if_needed(
                page, graph, node, phase=phase, node_by_id=node_by_id, profile=interaction_profile, document_type=True
            )
            node_wait = node.get("dependency_wait_profile") if isinstance(node.get("dependency_wait_profile"), dict) else {}
            child_timeout_ms = max(
                int(interaction_profile.get("child_visibility_timeout_ms", 4000)),
                int(node_wait.get("mount_timeout_ms", 0) or 0),
            )
            if ordering_unmet:
                # Best effort after a failed earlier section/row: try the field if
                # it is already there, but do not wait out a full mount timeout.
                child_timeout_ms = min(child_timeout_ms, int(interaction_profile.get("child_visibility_timeout_ms", 1500)), 1500)
            deadline = asyncio.get_running_loop().time() + child_timeout_ms / 1000.0
            while control is None and asyncio.get_running_loop().time() < deadline:
                await page.wait_for_timeout(int(interaction_profile.get("poll_interval_ms", 120)))
                before = await capture_document_type_controls(page)
                before, row_binding_before = _apply_repeatable_row_bindings(before, graph)
                control, before, binding_before, preparation = await _prepare_phase_control_for_action(
                    page, graph, node, phase=phase, controls=before, profile=interaction_profile, document_type=True
                )
        if control is None:
            success = not bool(node.get("required"))
            attempts.append({
                "field": node.get("field_key"), "node_id": node.get("node_id"), "input_path": node.get("input_path"),
                "expected_value": expected, "success": success, "filled": False, "skipped": success,
                "reason": "conditional child not present" if success else f"required semantic control unresolved: {binding_before.get('reason')}",
                "binding_diagnostics": binding_before, "interaction_preparation": preparation,
                "parent_recommit": parent_recommit, "action_model_plan": action_model_plan,
                "row_index": node.get("row_index"), "row_kind": node.get("row_kind"), "section": node.get("section"), "order": order,
            })
            attempts[-1]["action_critic"] = await _agentq_record_outcome(
                page, phase=phase, node=node, representation_before=representation_before,
                action_plan=action_model_plan, outcome=attempts[-1], controls_after=before, surface_gate=surface_gate
            )
            node_status[str(node.get("node_id"))] = success
            continue

        # Generated/read-only values are verification-only.  In particular Version
        # must never receive exploratory or fallback typing, even if a future portal
        # release accidentally exposes it as enabled.
        if str(node.get("action") or "") == "verify_only":
            success = _value_equal(node, control)
            attempts.append({
                "field": node.get("field_key"), "node_id": node.get("node_id"), "input_path": node.get("input_path"),
                "expected_value": expected, "actual_value": attempt_actual_value(node, control), "success": success, "filled": False,
                "reason": "portal-generated value verified without mutation" if success else "portal-generated value did not match expected value; mutation is forbidden",
                "exact_verified": bool(success), "selector": control.get("selector"),
                "row_index": node.get("row_index"), "row_kind": node.get("row_kind"), "section": node.get("section"), "order": order,
                "executor": "verification-only", "binding_diagnostics": binding_before,
                "action_model_plan": action_model_plan,
            })
            attempts[-1]["action_critic"] = await _agentq_record_outcome(
                page, phase=phase, node=node, representation_before=representation_before,
                action_plan=action_model_plan, outcome=attempts[-1], controls_after=before, surface_gate=surface_gate
            )
            node_status[str(node.get("node_id"))] = bool(success)
            if success:
                completed_node_ids.append(str(node.get("node_id")))
            continue

        # Other read-only generated values are verified rather than forcibly changed.
        if (control.get("readonly") or control.get("disabled")) and _value_equal(node, control):
            attempts.append({
                "field": node.get("field_key"), "node_id": node.get("node_id"), "input_path": node.get("input_path"),
                "expected_value": expected, "actual_value": attempt_actual_value(node, control), "success": True, "filled": False,
                "reason": "portal-generated read-only value verified", "exact_verified": True, "selector": control.get("selector"),
                "row_index": node.get("row_index"), "row_kind": node.get("row_kind"), "section": node.get("section"), "order": order,
                "executor": "verification-only", "binding_diagnostics": binding_before,
                "action_model_plan": action_model_plan,
            })
            attempts[-1]["action_critic"] = await _agentq_record_outcome(
                page, phase=phase, node=node, representation_before=representation_before,
                action_plan=action_model_plan, outcome=attempts[-1], controls_after=before, surface_gate=surface_gate
            )
            node_status[str(node.get("node_id"))] = True
            completed_node_ids.append(str(node.get("node_id")))
            continue

        success = False
        actual_control = control
        last_error = ""
        protected_before = _snapshot_bound_node_states(graph, before, completed_node_ids)
        transaction_proof: Dict[str, Any] = {
            "target_binding_before": binding_before,
            "row_identity_before": row_binding_before,
            "row_binding_for_node": row_binding_for_node(row_binding_before, node),
            "interaction_preparation": preparation,
            "parent_recommit": parent_recommit,
            "protected_node_count": len(protected_before),
            "protected_state_changes": [],
            "explicit_event_proof": {},
            "conditional_child_visibility": {},
            "action_model_plan": action_model_plan,
        }
        dom_cursor = await _mark_dom_transition_cursor(page)
        node_started = asyncio.get_running_loop().time()
        for retry in range(max_retries + 1):
            if retry and asyncio.get_running_loop().time() - node_started > _node_time_budget_seconds(interaction_profile):
                # Leave this field to the repair pass instead of holding the form.
                last_error = "HIP_NODE_TIME_BUDGET_EXCEEDED: field did not commit within its time budget; the repair pass retries it"
                break
            publish_executor_progress(page, phase=phase, node=node, stage="attempt", retry=retry)
            try:
                action = _effective_action(node, actual_control)
                if action != str(node.get("action") or ""):
                    transaction_proof["adapted_action"] = {"compiled": node.get("action"), "live": action}
                selector = str(actual_control.get("selector") or "")
                if action == "fill_text":
                    ok = await set_text_control(page, root, selector, str(expected), phase=phase)
                elif action == "select_single":
                    ok = await select_dds_combobox(page, root, selector, str(expected), phase=phase)
                    transaction_proof["single_select_driver_audit"] = await read_last_single_select_audit(page)
                    if ok:
                        # A committed DDS value can still leave its popup/focus layer
                        # active.  Blur it before child discovery or the next field;
                        # otherwise the next click may clear the previous parent or
                        # conditional children may not materialize.
                        await close_open_dropdown(page, phase)
                elif action == "select_multi":
                    ok = await select_dds_multiselect(page, root, selector, split_multi_value(expected), phase=phase)
                elif action == "select_radio" and _is_boolean_switch(actual_control):
                    # Dell renders Document Type Status as a DDS switch, not radios.
                    ok = await set_boolean_control(page, root, selector, expected, phase=phase)
                elif action == "select_radio":
                    ok = await select_radio_option(page, selector, str(expected), phase=phase)
                    if ok is None:
                        ok = await select_radio_value(page, root, str(expected), section=str(node.get("section") or ""), phase=phase)
                elif action == "toggle":
                    # Switches/checkboxes learned at runtime (e.g. one option of a
                    # checkbox group) on the Document Type form.
                    ok = await set_boolean_control(page, root, selector, expected, phase=phase)
                else:
                    ok = False
                    last_error = f"unsupported stateful action: {action}"
                await page.wait_for_timeout(int(interaction_profile.get("poll_interval_ms", 100)))
                if action in {"select_single", "select_radio", "select_multi", "toggle"}:
                    await _wait_for_dom_transition_activity(
                        page, dom_cursor, timeout_ms=int(interaction_profile.get("child_visibility_timeout_ms", 2600))
                    )
                current, current_controls, stability = await _wait_for_document_type_transaction_stable(
                    page, node,
                    timeout_ms=int(interaction_profile.get("transaction_timeout_ms", 5200)),
                    consecutive_samples=2,
                    interval_ms=int(interaction_profile.get("poll_interval_ms", 100)),
                )
                transaction_proof["stability"] = stability
                transition_now = await _collect_dom_transition_window(page, dom_cursor)
                transition_now_summary = summarize_dom_transition_window(transition_now)
                event_proof = explicit_event_proof(transition_now_summary, action, executor_ok=bool(ok))
                transaction_proof["explicit_event_proof"] = event_proof
                if action in {"select_single", "select_multi", "select_radio", "toggle"} and current is not None and _value_equal(node, current):
                    if action in {"select_single", "select_multi"}:
                        await close_open_dropdown(page, phase)
                    generation_barrier = await _wait_for_post_commit_generation_barrier(
                        page, graph, node, phase=phase, node_status=node_status,
                        profile=interaction_profile, selector_before=selector, document_type=True
                    )
                    barrier_controls = generation_barrier.pop("controls", [])
                    transaction_proof["post_commit_generation_barrier"] = generation_barrier
                    if not generation_barrier.get("pass"):
                        current_controls = barrier_controls or current_controls
                        rebound = resolve_document_type_control_diagnostics(current_controls, node)
                        rebound_control = rebound.get("control") if isinstance(rebound.get("control"), dict) else None
                        if rebound_control is not None:
                            current = rebound_control
                            actual_control = rebound_control
                        success = False
                        last_error = str(generation_barrier.get("reason") or "HIP_POST_COMMIT_DOM_GENERATION_NOT_STABLE")
                        break
                    current_controls = barrier_controls or current_controls
                    rebound = resolve_document_type_control_diagnostics(current_controls, node)
                    rebound_control = rebound.get("control") if isinstance(rebound.get("control"), dict) else None
                    if rebound_control is None or not _value_equal(node, rebound_control):
                        success = False
                        last_error = "HIP_PARENT_COMMIT_LOST_AFTER_RERENDER"
                        break
                    current = rebound_control
                    actual_control = rebound_control
                multi_proof = {"pass": True, "not_applicable": True}
                if action == "select_multi":
                    multi_audit = await read_last_multiselect_audit(page)
                    final_snapshot = multi_audit.get("final_snapshot") if isinstance(multi_audit.get("final_snapshot"), dict) else {}
                    available_options = [
                        x.get("text") for x in final_snapshot.get("options", [])
                        if isinstance(x, dict) and x.get("text") and str(x.get("text")).strip().lower() != "select all"
                    ]
                    multi_proof = multiselect_exact_set_proof(
                        split_multi_value(expected),
                        (current or {}).get("selected_values", []),
                        selection_mode=str((current or {}).get("selection_mode") or final_snapshot.get("selection_mode") or ""),
                        selected_count=(current or {}).get("selected_count", final_snapshot.get("selected_count_summary")),
                        available_options=available_options,
                        option_universe_stable=final_snapshot.get("option_universe_stable"),
                    )
                    transaction_proof["multi_select_exact_set_proof"] = multi_proof
                    transaction_proof["multi_select_driver_audit"] = multi_audit
                if current is not None and _value_equal(node, current):
                    protected_after = _snapshot_bound_node_states(graph, current_controls, completed_node_ids, reference=protected_before)
                    unintended = _protected_state_changes(protected_before, protected_after)
                    transaction_proof["protected_state_changes_initial"] = unintended
                    if unintended:
                        # DDS blur/rerender can transiently clear a previously exact
                        # parent field.  Attempt one bounded restore from the existing
                        # validated sticky locks, then recapture and require both the
                        # target and every protected control to be exact.  A genuine
                        # mutation still fails closed because the change remains.
                        restore_audit = await restore_filled_values(
                            page, phase, reason=f"protect completed nodes before {node.get('field_key')}"
                        )
                        transaction_proof["protected_value_restore"] = restore_audit
                        if restore_audit.get("restored"):
                            current_controls = await capture_document_type_controls(page)
                            current_controls, rebound_row_binding = _apply_repeatable_row_bindings(current_controls, graph)
                            transaction_proof["row_binding_after_restore"] = rebound_row_binding
                            rebound = resolve_document_type_control_diagnostics(current_controls, node)
                            rebound_control = rebound.get("control") if isinstance(rebound.get("control"), dict) else None
                            if rebound_control is not None and _value_equal(node, rebound_control):
                                current = rebound_control
                            protected_after = _snapshot_bound_node_states(graph, current_controls, completed_node_ids, reference=protected_before)
                            unintended = _protected_state_changes(protected_before, protected_after)
                    transaction_proof["protected_state_changes"] = unintended
                    interaction_state = await inspect_interaction_state(page, str(current.get("selector") or ""))
                    transaction_proof["post_action_interaction_state"] = interaction_state
                    existing_object = existing_object_validation(node, interaction_state)
                    if existing_object:
                        transaction_proof["existing_object_validation"] = interaction_state.get("validationMessage")
                    if unintended:
                        success = False
                        actual_control = current
                        last_error = "HIP_DOCTYPE_UNINTENDED_MUTATION: action changed a previously committed control"
                    elif interaction_state.get("blockingValidation") and not existing_object:
                        success = False
                        actual_control = current
                        last_error = "HIP_FIELD_VALIDATION_BLOCKING"
                    elif not event_proof.get("pass"):
                        success = False
                        actual_control = current
                        last_error = "HIP_EXPLICIT_EVENT_PROOF_MISSING"
                    elif action == "select_multi" and not multi_proof.get("pass"):
                        success = False
                        actual_control = current
                        last_error = "HIP_MULTISELECT_EXACT_SET_PROOF_FAILED"
                    else:
                        child_gate = await _wait_for_parent_children_visible(
                            page, graph, node, phase=phase, node_status=node_status, profile=interaction_profile, document_type=True
                        ) if is_parent_node(graph, str(node.get("node_id") or "")) else {"pass": True, "children": []}
                        transaction_proof["conditional_child_visibility"] = child_gate
                        if not child_gate.get("pass"):
                            success = False
                            actual_control = current
                            last_error = str(child_gate.get("reason") or "HIP_CONDITIONAL_CHILD_NOT_VISIBLE_AFTER_PARENT")
                        else:
                            success = bool(ok) or _value_equal(node, current)
                            actual_control = current
                    if success or unintended or last_error in {"HIP_FIELD_VALIDATION_BLOCKING", "HIP_EXPLICIT_EVENT_PROOF_MISSING", "HIP_MULTISELECT_EXACT_SET_PROOF_FAILED", "HIP_CONDITIONAL_CHILD_NOT_VISIBLE_AFTER_PARENT"}:
                        break
                if current is not None:
                    actual_control = current
                last_error = "control did not reach a stable exact expected value after blur/rerender"
            except Exception as exc:
                raise_if_environment_fatal(exc)
                last_error = mask_sensitive_string(str(exc))
            if retry < max_retries:
                await close_open_dropdown(page, phase)
                await page.wait_for_timeout(int(interaction_profile.get("poll_interval_ms", 100)))
                refreshed, _ = await _wait_for_control(page, node, graph=graph, timeout_ms=int(interaction_profile.get("child_visibility_timeout_ms", 2600)))
                if refreshed is not None:
                    actual_control = refreshed

        attempts.append(mask_sensitive_data({
            "field": node.get("field_key"), "node_id": node.get("node_id"), "input_path": node.get("input_path"),
            "expected_value": expected, "value_redacted": expected, "actual_value": attempt_actual_value(node, actual_control),
            "success": success, "filled": success, "exact_verified": bool(success), "reason": "exact committed value verified" if success else last_error,
            "selector": actual_control.get("selector"), "semantic_locator": node.get("semantic_locator"),
            "row_index": node.get("row_index"), "row_kind": node.get("row_kind"), "section": node.get("section"), "order": order,
            "action": node.get("action"), "executor": node.get("executor"), "dependencies": node.get("depends_on", []),
            "binding_diagnostics": binding_before, "action_model_plan": action_model_plan,
            "transaction_proof": transaction_proof,
        }))
        node_status[str(node.get("node_id"))] = success
        publish_executor_progress(page, phase=phase, node=node, stage="done" if success else "failed")
        if success:
            completed_node_ids.append(str(node.get("node_id")))

        # Capture the real event/mutation sequence and the resulting control state.
        after = await capture_document_type_controls(page)
        after, row_binding_after = _apply_repeatable_row_bindings(after, graph)
        if attempts:
            attempts[-1].setdefault("transaction_proof", {})["row_identity_after"] = row_binding_after
        transition_window = await _collect_dom_transition_window(page, dom_cursor)
        transition_summary = summarize_dom_transition_window(transition_window)
        if attempts:
            attempts[-1]["dom_transition"] = transition_summary
            attempts[-1]["dom_events"] = (transition_window.get("events") or [])[-40:]
            attempts[-1]["dom_mutations"] = (transition_window.get("mutations") or [])[-40:]
            attempts[-1]["action_critic"] = await _agentq_record_outcome(
                page, phase=phase, node=node, representation_before=representation_before,
                action_plan=action_model_plan, outcome=attempts[-1], controls_after=after, surface_gate=surface_gate
            )
        if node.get("action") in {"select_single", "select_radio"}:
            before_ids = {(c.get("semantic_key"), c.get("row_kind"), c.get("row_index")) for c in before}
            after_ids = {(c.get("semantic_key"), c.get("row_kind"), c.get("row_index")) for c in after}
            for child in sorted(after_ids - before_ids):
                observed_edges.append({
                    "edge_id": _stable_id(node.get("node_id"), expected, child),
                    "from": node.get("node_id"),
                    "to": f"{phase}.{child[1]}[{child[2]}].{child[0]}",
                    "relation": "parent_value_reveals_child",
                    "when_parent_value": expected,
                    "source": "live_target_branch_execution", "evidence": "live_state_transition",
                    "event_evidence": transition_summary,
                    "judge_status": "candidate_until_section_pass",
                })
        controls_before = after

    final_controls = await capture_document_type_controls(page)
    final_controls, final_row_binding = _apply_repeatable_row_bindings(final_controls, graph)
    final_form_model = build_document_type_form_state_model(graph, final_controls)
    failed = [a for a in attempts if a.get("success") is False and not a.get("skipped")]
    model_blocking = bool(
        final_form_model.get("missing_required")
        or final_form_model.get("ambiguous_nodes")
        or final_form_model.get("duplicate_bindings")
        or not final_form_model.get("one_to_one_pass", False)
    )
    if model_blocking and not failed:
        failed.append({
            "field": "document_type_form_state_model",
            "success": False,
            "filled": False,
            "exact_verified": False,
            "reason": "HIP_DOCTYPE_FORM_MODEL_NOT_ONE_TO_ONE",
            "model": final_form_model,
        })
    agentq_phase_summary = await _agentq_finalize_phase(
        page, phase=phase, controls=final_controls, success=not failed, surface_gate=surface_gate
    )
    writable_attempts = [
        a for a in attempts
        if isinstance(a, dict) and str(a.get("action") or "") not in {"verify_only", ""}
    ]
    execution_stage_audit = {
        "fields_filled_or_verified": bool(attempts) and all(
            bool(a.get("success")) for a in attempts if isinstance(a, dict)
        ),
        "exact_execution_verified": all(
            bool(a.get("success")) and bool(a.get("exact_verified"))
            for a in attempts if isinstance(a, dict)
        ) if attempts else True,
        "authoritative_execution_verified": all(
            bool(((a.get("transaction_proof") or {}).get("explicit_event_proof") or {}).get("pass"))
            for a in writable_attempts
        ) if writable_attempts else True,
        "writable_attempt_count": len(writable_attempts),
        "phase_specific_transaction_model": "document_type",
    }
    return mask_sensitive_data({
        "schema_version": "hip.stateful-form-execution.v1",
        "phase": phase,
        "graph_id": graph.get("graph_id"),
        "strategy": graph.get("strategy"),
        "pass": not failed,
        "status": "pass" if not failed else "failed",
        "execution_stage_audit": execution_stage_audit,
        "attempts": attempts,
        "failed_attempts": failed,
        "final_controls": final_controls,
        "observed_dependency_edges": observed_edges,
        "node_status": node_status,
        "ordering_predecessor_failures": ordering_predecessor_failures,
        "initial_form_state_model": initial_form_model,
        "final_form_state_model": final_form_model,
        "initial_repeatable_row_binding": initial_row_binding,
        "final_repeatable_row_binding": final_row_binding,
        "completed_node_ids": completed_node_ids,
        "dependency_execution_contract": dependency_contract,
        "scheduler_final_state": scheduler_snapshot(
            dependency_contract, node_status, visible_node_ids=completed_node_ids
        ),
        "deterministic_replay_speed_profile": replay_speed_profile(dependency_contract),
        "interaction_policy": policy_manifest(),
        "execution_profile": execution_profile,
        "flow_pattern_memory_match": graph.get("flow_pattern_memory_match") or {},
        "memory_replay_profile": graph.get("memory_replay_profile") or {},
        "agentq_phase_start": agentq_phase_start,
        "agentq_phase_summary": agentq_phase_summary,
        "fast_replay_blueprint": {
            "eligible": execution_profile.get("mode") == "validated_fast_replay",
            "structure_fingerprint": final_form_model.get("structure_fingerprint"),
            "skip_known_branch_exploration": execution_profile.get("mode") == "validated_fast_replay",
            "binding_identities": [
                (a.get("binding_diagnostics") or {}).get("selected_identity")
                for a in attempts if isinstance(a, dict) and (a.get("binding_diagnostics") or {}).get("selected_identity")
            ],
        },
    })



async def capture_stateful_controls(page: Page, phase: str) -> List[Dict[str, Any]]:
    """Capture visible controls for any HIP create/wizard surface.

    This collector deliberately records semantic/row context and treats generated
    DDS ids as current-action evidence only.  Document Type keeps its richer
    dedicated collector; other phases use this generic stateful view.
    """
    if "document_type" in str(phase or ""):
        return await capture_document_type_controls(page)
    root = await get_active_form_root(page, phase)
    script = r"""
(els) => {
  function clean(v){return String(v||'').replace(/\s+/g,' ').trim();}
  function visible(el){if(!el||!el.getBoundingClientRect)return false;const host=el.closest('label,.dds__radio-button,.dds__checkbox,[role=radio],[role=checkbox]')||el;const r=host.getBoundingClientRect();const s=getComputedStyle(host);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0');}
  function css(el){if(el.id)return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;const p=[];let n=el;while(n&&n.nodeType===1&&p.length<10){let x=n.tagName.toLowerCase();const par=n.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===n.tagName);if(same.length>1)x+=`:nth-of-type(${same.indexOf(n)+1})`;}p.unshift(x);n=par;}return p.join(' > ');}
  function label(el){if(/^(radio|checkbox)$/.test(el.getAttribute('role')||'')&&el.tagName!=='INPUT'){const own=clean(el.getAttribute('aria-label')||el.innerText||el.textContent);if(own)return own;}if(el.id){const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`);if(l&&clean(l.innerText||l.textContent))return clean(l.innerText||l.textContent);}const own=el.closest('label');if(own&&clean(own.innerText||own.textContent))return clean(own.innerText||own.textContent);const labelledBy=el.getAttribute('aria-labelledby');if(labelledBy){const l=document.getElementById(labelledBy);if(l&&clean(l.innerText||l.textContent))return clean(l.innerText||l.textContent);}const group=el.closest('.dds__form-group,.dds__input-text__container,app-generic-dropdown,dds-dropdown,dds-radio-button,.dds__radio-button,[class*=form-field],[class*=field-container]');const l=group&&group.querySelector(':scope > label,:scope > .dds__label,label,.dds__label');return clean((l&&(l.innerText||l.textContent))||el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||'');}
  function headingFrom(cur){while(cur&&cur!==document.body){const candidates=Array.from(cur.children||[]).filter(x=>/^(H1|H2|H3|H4)$/.test(x.tagName)||x.getAttribute('role')==='heading'||x.tagName==='LEGEND');for(const h of candidates){const t=clean(h.innerText||h.textContent);if(t)return t;}cur=cur.parentElement;}return '';}
  function regionLabel(rg){const lb=rg.getAttribute('aria-labelledby');if(lb){const t=clean(lb.split(/\s+/).map(i=>{const x=document.getElementById(i);return x?(x.innerText||x.textContent):'';}).join(' '));if(t)return t;}return clean(rg.getAttribute('aria-label')||'');}
  function section(el){const rg=el.closest('[role=region][aria-labelledby],[role=region][aria-label]');const nearFs=el.closest('fieldset');if(rg&&(!nearFs||nearFs.contains(rg))){const t=regionLabel(rg);if(t)return t;}const tab=el.closest('[role=tabpanel]');if(tab){const labelled=tab.getAttribute('aria-labelledby');if(labelled){const l=document.getElementById(labelled);if(l&&clean(l.innerText||l.textContent))return clean(l.innerText||l.textContent);}const h=tab.querySelector('h1,h2,h3,h4,[role=heading],legend');if(h&&clean(h.innerText||h.textContent))return clean(h.innerText||h.textContent);}const fs=el.closest('fieldset');const lg=fs&&fs.querySelector(':scope > legend');if(lg&&clean(lg.innerText||lg.textContent))return clean(lg.innerText||lg.textContent);return headingFrom(el.parentElement);}
  function row(el){const array=el.closest('[formarrayname=conditions]');if(array){const direct=Array.from(array.children||[]).filter(x=>x.querySelector&&x.querySelector('[formcontrolname=conditionType],dds-dropdown[name=conditionType]'));const rr=direct.find(x=>x===el||x.contains(el));if(rr)return {signature:css(rr),text:clean(rr.innerText||rr.textContent).slice(0,240),parent:css(array)};}
    /* Angular FormArray row: [formgroupname=<n>] inside [formarrayname] */
    const fg=el.closest('[formgroupname]');if(fg&&/^\d+$/.test(fg.getAttribute('formgroupname')||'')){const arr=fg.closest('[formarrayname]');const an=(arr&&arr.getAttribute('formarrayname'))||'';return {signature:css(fg),text:clean(fg.innerText||fg.textContent).slice(0,240),parent:arr?css(arr):(fg.parentElement?css(fg.parentElement):''),hint:an?('array:'+an):''};}const selectors=['tr','[role=row]','.dds__table__row','[class*=condition-row]','[class*=action-row]','[class*=attribute-row]','[class*=process-step]','[class*=filename-part]','[class*=file-name-part]','.dds__d-flex.dds__justify-content-start'];const cands=selectors.map(s=>el.closest(s)).filter(r=>r&&r.querySelectorAll('input:not([type=hidden]),textarea,select,[role=combobox],[role=radio]').length>1);/* innermost container wins: a File Name part row inside a Process Step is its own row */const inner=cands.find(c=>cands.every(o=>o===c||!c.contains(o)));if(inner){const hint=inner.matches('[class*=filename-part],[class*=file-name-part]')?'filename_part':inner.matches('[class*=process-step]')?'process_step':inner.matches('[class*=condition-row]')?'condition':inner.matches('[class*=action-row]')?'action':inner.matches('[class*=attribute-row]')?'attribute':'';return {signature:css(inner),text:clean(inner.innerText||inner.textContent).slice(0,240),parent:inner.parentElement?css(inner.parentElement):'',hint};}return {signature:'',text:'',parent:'',hint:''};}
  function groupInfo(el){
    // A radio's own label is its option ("Yes"); the question it answers
    // ("Existing Account") lives on the group.  Find the smallest container
    // holding only this group's radios and read its label.
    const isRadio=(el.type||'').toLowerCase()==='radio'||el.getAttribute('role')==='radio';
    const isCheck=!isRadio&&el.getAttribute('role')!=='switch'&&((el.type||'').toLowerCase()==='checkbox'||el.getAttribute('role')==='checkbox');
    if(!isRadio&&!isCheck)return {label:'',name:''};
    const name=el.getAttribute('name')||'';
    /* Checkboxes form a group only when several answer one question
       ("Notify On": Success, Failure); a lone checkbox is its own question. */
    const radiosIn=n=>Array.from(n.querySelectorAll(isRadio?'input[type=radio],[role=radio]':'input[type=checkbox]:not([role=switch]),[role=checkbox]'));
    let container=el.parentElement||el;let n=el.parentElement;
    while(n&&n!==document.body){
      const rs=radiosIn(n);
      if(name?!rs.every(x=>(x.getAttribute('name')||'')===name):rs.length>radiosIn(container).length&&radiosIn(container).length>=2)break;
      container=n;
      if(n.matches('[role=radiogroup],[role=group],fieldset'))break;
      n=n.parentElement;
    }
    if(isCheck&&radiosIn(container).length<2)return {label:'',name:''};
    const isOptionLabel=l=>{const f=l.getAttribute&&l.getAttribute('for');if(f){const t=document.getElementById(f);if(t&&(/^(radio|checkbox)$/.test((t.type||'').toLowerCase())||/^(radio|checkbox)$/.test(t.getAttribute('role')||'')))return true;}return !!(l.querySelector&&l.querySelector('input[type=radio],[role=radio],input[type=checkbox],[role=checkbox]'));};
    let text='';
    const lb=container.getAttribute('aria-labelledby');
    if(lb)text=clean(lb.split(/\s+/).map(i=>{const x=document.getElementById(i);return x?(x.innerText||x.textContent):'';}).join(' '));
    if(!text)text=clean(container.getAttribute('aria-label')||'');
    if(!text){const lg=container.querySelector(':scope > legend');if(lg)text=clean(lg.innerText||lg.textContent);}
    if(!text){const c=Array.from(container.querySelectorAll('label,.dds__label,legend')).find(l=>!isOptionLabel(l));if(c)text=clean(c.innerText||c.textContent);}
    if(!text){let p=container;for(let i=0;i<3&&p&&!text;i++){let sib=p.previousElementSibling;while(sib&&!text){if(!sib.querySelector('input,select,textarea,[role=combobox]')){const t=clean(sib.innerText||sib.textContent);if(t&&t.length<80)text=t;}sib=sib.previousElementSibling;}p=p.parentElement;}}
    return {label:text,name};
  }
  return els.filter(visible).map((el,index)=>{const sec=section(el);const r=row(el);const dd=el.closest('dds-dropdown');const multiple=!!(dd&&(dd.getAttribute('selection')==='multiple'||dd.querySelector('.dds__dropdown--is-multiple')));const selected=dd?Array.from(dd.querySelectorAll('[role=option]')).filter(x=>x.getAttribute('aria-selected')==='true'||x.getAttribute('data-selected')==='true'||x.getAttribute('aria-checked')==='true'||x.classList.contains('dds__dropdown__item-selected')||x.classList.contains('dds__dropdown__item--selected')).map(x=>clean(x.innerText||x.textContent)).filter(x=>x&&!/^\d+\s+selected$/i.test(x)&&x.toLowerCase()!=='select all'):[];const chips=dd?Array.from(dd.querySelectorAll('.dds__tag,.dds__chip,[class*=selected-value],[class*=selection__label]')).map(x=>clean(x.innerText||x.textContent)).filter(x=>x&&!/^\d+\s+selected$/i.test(x)&&x.toLowerCase()!=='select all'):[];const checked=!!(el.checked||el.getAttribute('aria-checked')==='true');let value=clean(el.value||el.getAttribute('aria-valuetext')||el.getAttribute('data-value')||'');if((el.type==='radio'||el.type==='checkbox'||el.getAttribute('role')==='radio'||el.getAttribute('role')==='checkbox')&&!checked)value='';const box=el.getBoundingClientRect();const style=getComputedStyle(el);const cx=box.left+box.width/2;const cy=box.top+box.height/2;const hit=(box.width&&box.height)?document.elementFromPoint(Math.max(0,Math.min(innerWidth-1,cx)),Math.max(0,Math.min(innerHeight-1,cy))):null;/* A visually hidden radio/checkbox (DDS clips the real input) is operated through its label. */const lp=(/^(radio|checkbox)$/.test((el.type||'').toLowerCase())&&(box.width<4||box.height<4))?((el.id&&document.querySelector(`label[for="${CSS.escape(el.id)}"]`))||el.closest('label')):null;let proxyHit=false;if(lp){const lb=lp.getBoundingClientRect();const lx=lb.left+lb.width/2,ly=lb.top+lb.height/2;if(lb.width&&lb.height){const inV=lx>=0&&ly>=0&&lx<innerWidth&&ly<innerHeight;const h2=inV?document.elementFromPoint(lx,ly):null;proxyHit=!inV||!!(h2&&(h2===lp||lp.contains(h2)));}}const component=el.closest('dds-dropdown,app-generic-dropdown,dds-input,dds-textarea,dds-radio-button,dds-checkbox,dds-switch,dds-file-input,[class*=dds__]');const frameworkHost=el.closest('[formcontrolname],[ng-reflect-name],[data-control-name],dds-dropdown[name],dds-input[name],dds-textarea[name],dds-switch[name]');const inheritedFormControl=(frameworkHost&&frameworkHost.getAttribute('formcontrolname'))||'';const inheritedReflect=(frameworkHost&&frameworkHost.getAttribute('ng-reflect-name'))||'';const inheritedName=(frameworkHost&&frameworkHost.getAttribute('name'))||'';const frameworkKey=el.getAttribute('formcontrolname')||inheritedFormControl||el.getAttribute('ng-reflect-name')||inheritedReflect||el.getAttribute('data-control-name')||(frameworkHost&&frameworkHost.getAttribute('data-control-name'))||el.getAttribute('name')||inheritedName||'';const semanticPath=[sec,r.signature,frameworkKey,label(el),el.getAttribute('role')||el.getAttribute('type')||el.tagName].map(clean).filter(Boolean).join(' > ');const gi=groupInfo(el);return {index,selector:css(el),group_label:gi.label,group_name:gi.name,id:el.id||'',tag:(el.tagName||'').toLowerCase(),type:el.getAttribute('type')||'',role:el.getAttribute('role')||'',name:el.getAttribute('name')||inheritedName||'',placeholder:el.getAttribute('placeholder')||'',label:label(el),section:sec,row_signature:r.signature,row_text:r.text,row_parent:r.parent||'',row_kind_hint:r.hint||'',value,selected_values:Array.from(new Set([...selected,...chips])),selection_mode:multiple?'multiple':'single',checked,required:!!(el.required||el.getAttribute('aria-required')==='true'),disabled:!!(el.disabled||el.getAttribute('aria-disabled')==='true'),readonly:!!el.readOnly,aria_invalid:el.getAttribute('aria-invalid')||'',expanded:el.getAttribute('aria-expanded')||'',form_control_name:el.getAttribute('formcontrolname')||inheritedFormControl||'',ng_reflect_name:el.getAttribute('ng-reflect-name')||inheritedReflect||'',framework_key:frameworkKey,component_tag:component?(component.tagName||'').toLowerCase():'',semantic_path:semanticPath,bbox:{x:box.x,y:box.y,width:box.width,height:box.height},pointer_events:style.pointerEvents||'',z_index:style.zIndex||'',hit_test_selector:hit?css(hit):'',hit_test_pass:!!(hit&&(hit===el||el.contains(hit)||hit.contains(el))),click_proxy:lp?css(lp):'',interactable:!!(!el.disabled&&!el.readOnly&&((style.pointerEvents!=='none'&&box.width&&box.height&&(!(cx>=0&&cy>=0&&cx<innerWidth&&cy<innerHeight)||hit&&(hit===el||el.contains(hit)||hit.contains(el))))||proxyHit))};});
}
"""
    selector = "input:not([type=hidden]),textarea,select,[role='combobox'],[role='radio'],[role='checkbox'],[role='switch']"
    rows = []
    capture_scope = "active_form_root"
    try:
        rows = await root.locator(selector).evaluate_all(script)
    except Exception:
        rows = []
    # v2.2.5: Dell DDS/web components occasionally make the active-root CSS
    # resolver stale while Playwright can still pierce the open shadow tree from
    # the page locator.  Fall back to page-wide Playwright discovery only when the
    # proven active form root yielded nothing.  Semantic binding/section/row gates
    # below still decide which controls are authoritative, so listing Search fields
    # cannot satisfy an unrelated expected field.
    if not rows:
        try:
            rows = await page.locator(selector).evaluate_all(script)
            capture_scope = "page_playwright_fallback"
        except Exception:
            rows = []
    controls = [dict(x) for x in (rows or []) if isinstance(x, dict)]
    for c in controls:
        c.setdefault("capture_scope", capture_scope)
    # Assign durable semantic identities and occurrences from DOM order. Dynamic
    # CSS paths remain current-run evidence only. Label occurrence is assigned for
    # every control, even inside a detected row, because repeated DDS labels are
    # more stable than framework-generated row containers.
    row_order: Dict[Tuple[str, str], int] = {}
    label_order: Dict[Tuple[str, str], int] = {}
    for c in controls:
        sec = _canonical_section(str(c.get("section") or ""))
        semantic = _norm(c.get("label") or c.get("placeholder") or c.get("name"))
        c["semantic_key"] = semantic
        label_key = (sec, semantic)
        c["label_occurrence"] = label_order.get(label_key, 0)
        label_order[label_key] = int(c["label_occurrence"]) + 1
        sig = str(c.get("row_signature") or "")
        if sig:
            row_key = (sec, sig)
            if row_key not in row_order:
                row_order[row_key] = sum(1 for (existing_section, _) in row_order if existing_section == sec)
            c["row_index"] = row_order[row_key]
            low = f"{sec} {_norm(c.get('row_text'))}"
            hint = str(c.get("row_kind_hint") or "")
            # The container's own class is stronger evidence than its text: a
            # Process Step's text also contains its File Name parts' labels.
            if hint == "filename_part" and "configure_targets" in sec:
                c["row_kind"] = "filename_part"
            elif hint == "process_step" and "configure_targets" in sec:
                c["row_kind"] = "process_step"
            elif "configure_source" in low and any(x in low for x in ("attribute", "operator", "value")):
                c["row_kind"] = "flow_identifier"
            elif "configure_targets" in low and "derived_from" in low:
                c["row_kind"] = "filename_part"
            elif "configure_targets" in low:
                c["row_kind"] = "process_step"
            elif "configure_routing" in low and "condition" in low:
                c["row_kind"] = "routing_condition"
            elif "configure_routing" in low and "action" in low:
                c["row_kind"] = "routing_action"
            elif "condition" in low:
                c["row_kind"] = "condition"
            elif hint.startswith("array:"):
                # Any other Angular FormArray (Tags, Cross Reference rows, ...):
                # its own name is its row kind.
                c["row_kind"] = _norm(hint[len("array:"):])
    # DDS labels only the first row of a repeatable group, so an unlabelled row
    # has no text to classify it by.  Sibling rows under the same container are
    # the same kind: inherit it.
    kinds_by_parent: Dict[Tuple[str, str], Dict[str, int]] = {}
    for c in controls:
        parent = str(c.get("row_parent") or "")
        if parent and c.get("row_kind"):
            bucket = kinds_by_parent.setdefault((_canonical_section(str(c.get("section") or "")), parent), {})
            bucket[str(c["row_kind"])] = bucket.get(str(c["row_kind"]), 0) + 1
    for c in controls:
        parent = str(c.get("row_parent") or "")
        if c.get("row_signature") and not c.get("row_kind") and parent:
            bucket = kinds_by_parent.get((_canonical_section(str(c.get("section") or "")), parent)) or {}
            if len(bucket) == 1:
                c["row_kind"] = next(iter(bucket))
                c["row_kind_inferred_from_siblings"] = True
    # Physical row indexes count every row in a section; the ordinal within one
    # row kind is what a node's zero-based row index means.
    kind_rows: Dict[Tuple[str, str], List[str]] = {}
    for c in controls:
        sig = str(c.get("row_signature") or "")
        if sig and c.get("row_kind"):
            ordered = kind_rows.setdefault((_canonical_section(str(c.get("section") or "")), str(c["row_kind"])), [])
            if sig not in ordered:
                ordered.append(sig)
            c["row_kind_ordinal"] = ordered.index(sig)
    return mask_sensitive_data(controls)



def _stateful_control_identity(control: Dict[str, Any]) -> str:
    """Return a durable, value-free identity for any HIP form control."""
    row_identity: Any = control.get("row_semantic_identity")
    if not row_identity:
        if control.get("expected_row_index") is not None:
            row_identity = control.get("expected_row_index")
        elif control.get("row_index") is not None:
            row_identity = control.get("row_index")
        elif control.get("label_occurrence") is not None:
            row_identity = control.get("label_occurrence")
        else:
            row_identity = ""
    return "|".join([
        _canonical_section(str(control.get("section") or "")),
        _norm(control.get("row_kind")),
        str(row_identity),
        _norm(control.get("semantic_key")),
        _norm(control.get("framework_key") or control.get("form_control_name") or control.get("ng_reflect_name")),
        _norm(control.get("component_tag")),
        _norm(control.get("role") or control.get("type") or control.get("tag")),
        _norm(control.get("label") or control.get("placeholder") or control.get("name")),
    ])


def _stateful_control_state(control: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "identity": _stateful_control_identity(control),
        "value": _norm_text(control.get("value")),
        "selected_values": sorted(x.lower() for x in _clean_selected_values(control.get("selected_values", []))),
        "checked": bool(control.get("checked")),
        "disabled": bool(control.get("disabled")),
        "readonly": bool(control.get("readonly")),
        "aria_invalid": str(control.get("aria_invalid") or ""),
    }


def _stateful_control_score(control: Dict[str, Any], node: Dict[str, Any]) -> int:
    loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
    if control_in_other_row(control, node):
        return ROW_EXCLUDED_SCORE
    labels = [_norm(v) for v in loc.get("labels", []) if _norm(v)]
    names = {_norm(v) for v in loc.get("names", []) if _norm(v)}
    placeholders = {_norm(v) for v in loc.get("placeholders", []) if _norm(v)}
    roles = {_norm(v) for v in loc.get("roles", []) if _norm(v)}
    section = str(node.get("section") or "")
    row_index = node.get("row_index")
    score = 0
    section_score = _node_section_score(node, str(control.get("section") or ""))
    if section and section_score:
        score += section_score
    elif section and not _generic_create_section(section):
        score -= 35
    cl = _norm(control.get("label")); cp = _norm(control.get("placeholder")); cn = _norm(control.get("name"))
    cs = _norm(control.get("semantic_key")); cr = _norm(control.get("role")); ct = _norm(control.get("type"))
    framework = _norm(control.get("framework_key") or control.get("form_control_name") or control.get("ng_reflect_name"))
    field_key = _norm(node.get("field_key"))
    if cs and cs == field_key:
        score += 125
    if framework and (framework == field_key or framework in names):
        score += 55
    if labels and any(v == cl for v in labels):
        score += 65
    elif labels and len(cl) >= 4 and any(v and (v in cl or cl in v) for v in labels):
        score += 35
    # A radio's own label is its option ("Yes"); its group label names the
    # question ("Existing Account") and is what tells two Yes/No groups apart.
    group_label = _norm(control.get("group_label"))
    if group_label and labels:
        if any(v == group_label for v in labels):
            score += 65
        elif any(v and (v in group_label or group_label in v) for v in labels):
            score += 35
    if names and cn in names:
        score += 35
    if placeholders and cp in placeholders:
        score += 30
    if roles and cr in roles:
        score += 35
    action = str(node.get("action") or "")
    component = _norm(control.get("component_tag"))
    if action == "fill_text" and (ct in {"text", "number", "search", "email", ""} and cr != "switch"):
        score += 10
    elif action in {"select_single", "select_multi"} and (cr == "combobox" or control.get("tag") == "select" or component in {"dds_dropdown", "app_generic_dropdown"}):
        score += 18
    elif action == "upload_file" and ct == "file":
        score += 25
    elif action in {"select_radio", "toggle"}:
        expected_n = _norm(node.get("expected_value"))
        if expected_n in {_norm(control.get("label")), _norm(control.get("value"))}:
            score += 90
        if cr in {"switch", "checkbox"} or ct == "checkbox" or component in {"dds_switch", "dds_checkbox"}:
            score += 90
    expected_row_kind = _norm(node.get("row_kind"))
    actual_row_kind = _norm(control.get("row_kind"))
    if expected_row_kind:
        if actual_row_kind == expected_row_kind:
            score += 45
        elif actual_row_kind:
            score -= 55
    elif actual_row_kind and row_index is None:
        # A section-level field (Source Document Type Name) never lives inside
        # a repeatable row (a Flow Identifier row's Document Type Name (Version)).
        score -= 40
    if row_index is not None:
        bound_index = control.get("expected_row_index")
        if bound_index is not None:
            if bound_index == row_index:
                score += 165
            else:
                score -= 210
        elif control.get("row_index") == row_index or control.get("label_occurrence") == row_index:
            score += 80
        else:
            score -= 80
    if control.get("interactable") is True:
        score += 12
    elif "interactable" in control and control.get("interactable") is False and action != "verify_only" and not control.get("readonly"):
        score -= 35
    if action == "verify_only" and (control.get("readonly") or control.get("disabled")):
        score += 30
    if control.get("disabled") and action not in {"verify_only"}:
        score -= 20
    preferred = [str(x) for x in node.get("_preferred_control_identities", []) if str(x)]
    identity = _stateful_control_identity(control)
    if identity in preferred:
        score += max(8, 30 - preferred.index(identity) * 5)
    return score


def resolve_stateful_control_diagnostics(
    controls: Sequence[Dict[str, Any]],
    node: Dict[str, Any],
    *,
    min_score: int = 70,
    min_margin: int = 14,
) -> Dict[str, Any]:
    """Resolve one control for any HIP phase with a confidence margin.

    A highest-score-only resolver is unsafe on repeated DDS/Angular controls. This
    function rejects ambiguous bindings and exposes the evidence used by the
    action transaction and Portal Brain.
    """
    pool = [dict(c) for c in controls if isinstance(c, dict)]
    ranked = sorted(
        [
            {"score": _stateful_control_score(c, node), "identity": _stateful_control_identity(c), "control": c}
            for c in pool
        ],
        key=lambda item: (int(item["score"]), -int((item["control"] or {}).get("index") or 0)),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    margin = int(best["score"] - second["score"]) if best and second else int(best["score"]) if best else 0
    same_physical_control = bool(
        best and second
        and str((best.get("control") or {}).get("selector") or "")
        and str((best.get("control") or {}).get("selector") or "") == str((second.get("control") or {}).get("selector") or "")
    )
    resolved = bool(best and int(best["score"]) >= min_score and (second is None or margin >= min_margin or same_physical_control))
    reason = "resolved"
    if not best:
        reason = "no semantic candidate"
    elif int(best["score"]) < min_score:
        reason = "best candidate below confidence threshold"
    elif second is not None and margin < min_margin and not same_physical_control:
        reason = "ambiguous candidate margin"
    return mask_sensitive_data({
        "resolved": resolved,
        "reason": reason,
        "phase": node.get("phase"),
        "field_key": node.get("field_key"),
        "node_id": node.get("node_id"),
        "row_kind": node.get("row_kind"),
        "row_index": node.get("row_index"),
        "best_score": int(best["score"]) if best else None,
        "second_score": int(second["score"]) if second else None,
        "score_margin": margin,
        "selected_identity": best["identity"] if resolved and best else "",
        "control": dict(best["control"]) if resolved and best else None,
        "candidates": [
            {
                "score": int(item["score"]),
                "identity": item["identity"],
                "semantic_key": (item["control"] or {}).get("semantic_key"),
                "section": (item["control"] or {}).get("section"),
                "row_kind": (item["control"] or {}).get("row_kind"),
                "row_index": (item["control"] or {}).get("row_index"),
                "framework_key": (item["control"] or {}).get("framework_key"),
                "label": (item["control"] or {}).get("label"),
            }
            for item in ranked[:5]
        ],
    })


def resolve_stateful_control(controls: Sequence[Dict[str, Any]], node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    diagnostic = resolve_stateful_control_diagnostics(controls, node)
    control = diagnostic.get("control") if isinstance(diagnostic, dict) else None
    return dict(control) if isinstance(control, dict) else None


def build_phase_form_state_model(graph: Dict[str, Any], controls: Sequence[Dict[str, Any]], *, phase: str) -> Dict[str, Any]:
    """Build a one-to-one semantic input-to-control model for any HIP phase."""
    if "document_type" in str(phase or ""):
        return build_document_type_form_state_model(graph, controls)
    bindings: List[Dict[str, Any]] = []
    identity_owners: Dict[str, List[str]] = {}
    missing_required: List[str] = []
    ambiguous: List[str] = []
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        diagnostic = resolve_stateful_control_diagnostics(controls, node)
        deferred = bool(node.get("depends_on")) and not diagnostic.get("resolved")
        status = "resolved" if diagnostic.get("resolved") else "deferred_conditional" if deferred else "missing"
        if diagnostic.get("reason") == "ambiguous candidate margin":
            status = "ambiguous"
            ambiguous.append(str(node.get("node_id") or ""))
        if status == "missing" and node.get("required", True):
            missing_required.append(str(node.get("node_id") or ""))
        identity = str(diagnostic.get("selected_identity") or "")
        if identity:
            identity_owners.setdefault(identity, []).append(str(node.get("node_id") or ""))
        bindings.append({
            "node_id": node.get("node_id"), "field_key": node.get("field_key"),
            "input_path": node.get("input_path"), "section": node.get("section"),
            "row_kind": node.get("row_kind"), "row_index": node.get("row_index"),
            "action": node.get("action"), "status": status, "binding": diagnostic,
        })
    duplicate_bindings = {k: v for k, v in identity_owners.items() if len(v) > 1}
    structural = sorted({_stateful_control_identity(c) for c in controls if isinstance(c, dict) and _norm(c.get("semantic_key"))})
    return mask_sensitive_data({
        "schema_version": "hip.all-phase-form-state-model.v1",
        "phase": phase,
        "graph_id": graph.get("graph_id"),
        "control_count": len([c for c in controls if isinstance(c, dict)]),
        "bindings": bindings,
        "missing_required": missing_required,
        "ambiguous_nodes": ambiguous,
        "duplicate_bindings": duplicate_bindings,
        "one_to_one_pass": not duplicate_bindings and not ambiguous and not missing_required,
        "structure_fingerprint": hashlib.sha256(json.dumps(structural, sort_keys=True).encode("utf-8")).hexdigest(),
    })


def _snapshot_stateful_node_states(
    graph: Dict[str, Any], controls: Sequence[Dict[str, Any]], node_ids: Sequence[str],
    *, reference: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    return _snapshot_node_states(
        graph, controls, node_ids, resolve_stateful_control_diagnostics, _stateful_control_state, reference
    )


async def _wait_for_stateful_transaction_stable(
    page: Page,
    node: Dict[str, Any],
    *,
    phase: str,
    timeout_ms: int = 6000,
    consecutive_samples: int = 2,
    interval_ms: int = 180,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000.0
    last_digest = ""
    stable_count = 0
    last_controls: List[Dict[str, Any]] = []
    last_diag: Dict[str, Any] = {}
    samples = 0
    while asyncio.get_running_loop().time() < deadline:
        samples += 1
        last_controls = await capture_stateful_controls(page, phase)
        last_diag = resolve_stateful_control_diagnostics(last_controls, node)
        control = last_diag.get("control") if isinstance(last_diag.get("control"), dict) else None
        if control is not None and _stateful_value_equal(node, control):
            digest_payload = [_stateful_control_state(c) for c in last_controls if isinstance(c, dict) and _norm(c.get("semantic_key"))]
            digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            stable_count = stable_count + 1 if digest == last_digest else 1
            last_digest = digest
            if stable_count >= consecutive_samples:
                return dict(control), last_controls, {
                    "stable": True, "samples": samples, "consecutive_samples": stable_count,
                    "form_state_digest": digest, "binding": last_diag,
                }
        else:
            stable_count = 0
            last_digest = ""
        await page.wait_for_timeout(interval_ms)
    control = last_diag.get("control") if isinstance(last_diag.get("control"), dict) else None
    return dict(control) if control else None, last_controls, {
        "stable": False, "samples": samples, "consecutive_samples": stable_count,
        "form_state_digest": last_digest, "binding": last_diag,
    }

def _default_all_other_equal(expected: Any, actual: Any) -> bool:
    def canon(value: Any) -> str:
        text = _norm_text(value).lower()
        text = re.sub(r"\s+", " ", text)
        compact = re.sub(r"[^a-z0-9]+", "", text)
        if "allother" in compact and ("default" in compact or "disabled" in compact):
            return "default_all_other"
        return compact
    return bool(canon(expected)) and canon(expected) == canon(actual)


def _stateful_value_equal(node: Dict[str, Any], control: Dict[str, Any]) -> bool:
    if "document_type" in str(node.get("phase") or ""):
        return _value_equal(node, control)
    expected = node.get("expected_value")
    action = str(node.get("action") or "")
    if action == "select_multi":
        return {x.lower() for x in split_multi_value(expected)} == {x.lower() for x in _clean_selected_values(control.get("selected_values", []))}
    if action in {"select_radio", "toggle"}:
        if action == "toggle" or _norm(control.get("role")) in {"switch", "checkbox"} or _norm(control.get("type")) == "checkbox":
            expected_n = _norm(expected)
            if expected_n in {"enable", "enabled", "true", "yes", "on"}:
                return bool(control.get("checked"))
            if expected_n in {"disable", "disabled", "false", "no", "off", "not_enabled", "not_enable"}:
                return not bool(control.get("checked"))
        return bool(control.get("checked")) and _norm(expected) in {_norm(control.get("label")), _norm(control.get("value"))}
    if action == "upload_file":
        value = str(control.get("value") or "")
        return bool(value) and value.replace('\\','/').rsplit('/',1)[-1].lower() == str(expected).replace('\\','/').rsplit('/',1)[-1].lower()
    actual = _norm_text(control.get("value"))
    exp = _norm_text(expected)
    field_key = _norm(node.get("field_key"))
    candidate_values = [actual] + [_norm_text(x) for x in _clean_selected_values(control.get("selected_values", []))]
    candidate_values = [x for x in candidate_values if x]
    # The agent cannot type into a portal-owned disabled/read-only control; Dell
    # renders e.g. Map Identifier Version / Rule Scope with only a placeholder.
    candidate_values = _portal_owned_display_values(control, candidate_values)
    if not actual and candidate_values and (control.get("disabled") or control.get("readonly")):
        actual = candidate_values[0]
    if field_key in {"version", "map_identifier_version", "document_type_version", "routing_rule_version", "current_flow_version"}:
        return any(_version_equal(exp, value) for value in candidate_values)
    if field_key in {"process_source_document_type", "process_document_type_version"} and any(_default_all_other_equal(exp, value) for value in candidate_values):
        return True
    # DDS single-selects frequently keep the selected option/chip while the inner
    # input value becomes blank after the menu closes.  Treat the selected option
    # as authoritative so an already committed dropdown is not clicked repeatedly.
    if action == "select_single":
        # The DDS driver picks the option by semantic key, so verify the same way:
        # "Name (1.0)" in the portal is the input's "Name(1.0)", and an enum such
        # as ELEMENT_IN_PAYLOAD is the label "Element In Payload".
        expected_key = _semantic_value_key(exp)
        return any(
            exp.lower() == value.lower() or (expected_key and _semantic_value_key(value) == expected_key)
            for value in candidate_values
        )
    return exp.lower() == actual.lower()


async def _prepare_phase_control_for_action(
    page: Page,
    graph: Dict[str, Any],
    node: Dict[str, Any],
    *,
    phase: str,
    controls: Sequence[Dict[str, Any]],
    profile: Dict[str, Any],
    document_type: bool = False,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """Resolve, reveal and stabilize a control before any browser action."""
    current_controls = [dict(c) for c in controls if isinstance(c, dict)]
    resolver = resolve_document_type_control_diagnostics if document_type else resolve_stateful_control_diagnostics
    capture = capture_document_type_controls if document_type else (lambda pg: capture_stateful_controls(pg, phase))
    diagnostic = resolver(current_controls, node)
    preparation: Dict[str, Any] = {
        "structural_parent_analysis": {},
        "bbox_stability": {},
        "interaction_state": {},
        "rebound_after_reveal": False,
    }
    control = diagnostic.get("control") if isinstance(diagnostic.get("control"), dict) else None
    if control is None:
        reveal = await reveal_hidden_structural_parent(page, node)
        preparation["structural_parent_analysis"] = reveal
        if reveal.get("revealed"):
            await page.wait_for_timeout(int(profile.get("poll_interval_ms", 120)))
            current_controls = await capture(page)
            current_controls, reveal_row_binding = _apply_repeatable_row_bindings(current_controls, graph)
            preparation["row_binding_after_reveal"] = reveal_row_binding
            diagnostic = resolver(current_controls, node)
            control = diagnostic.get("control") if isinstance(diagnostic.get("control"), dict) else None
            preparation["rebound_after_reveal"] = control is not None
    if control is None and node.get("row_kind") and node.get("row_index") is not None and int(node.get("row_index") or 0) > 0:
        # Row N of a repeatable list still does not exist: click the list's own
        # "+ Add" now (effect-verified) instead of failing and waiting a cycle.
        try:
            from .form_structure_healer import ensure_repeatable_rows, plan_row_groups, reveal_collapsed_sections
            # An existing row may only be collapsed (BizFlow process step 2):
            # open collapsed sections before counting rows, never add a duplicate.
            preparation["row_heal_reveal"] = await reveal_collapsed_sections(
                page, phase, wanted=[str(node.get("row_kind") or ""), str(node.get("section") or "")], expand_unmatched=True,
            )
            if (preparation["row_heal_reveal"] or {}).get("expanded_count"):
                await page.wait_for_timeout(int(profile.get("poll_interval_ms", 120)))
                current_controls = await capture(page)
                current_controls, _ = _apply_repeatable_row_bindings(current_controls, graph)
                diagnostic = resolver(current_controls, node)
                control = diagnostic.get("control") if isinstance(diagnostic.get("control"), dict) else None
            kind_nodes = [] if control is not None else [
                n for n in graph.get("nodes") or []
                if isinstance(n, dict) and _norm(n.get("row_kind")) == _norm(node.get("row_kind"))
                and n.get("row_index") is not None and int(n.get("row_index") or 0) <= int(node.get("row_index") or 0)
            ]
            groups = plan_row_groups({"nodes": kind_nodes}, [], {})
            heal = await ensure_repeatable_rows(page, phase, groups, capture=lambda: capture(page)) if groups else {}
        except Exception as exc:
            heal = {"error": mask_sensitive_string(str(exc))[:300]}
        preparation["row_heal"] = heal
        if heal.get("rows_added"):
            await page.wait_for_timeout(int(profile.get("poll_interval_ms", 120)))
            current_controls = await capture(page)
            current_controls, heal_row_binding = _apply_repeatable_row_bindings(current_controls, graph)
            preparation["row_binding_after_row_heal"] = heal_row_binding
            diagnostic = resolver(current_controls, node)
            control = diagnostic.get("control") if isinstance(diagnostic.get("control"), dict) else None
    if control is None:
        return None, current_controls, diagnostic, mask_sensitive_data(preparation)

    selector = str(control.get("selector") or "")
    # Lightweight injected test/replay pages may expose captured controls without
    # a live Playwright page. The captured interactability metadata is then the
    # authoritative evidence; real portal runs always execute the live geometry
    # and hit-test path below.
    if not hasattr(page, "evaluate") or not hasattr(page, "wait_for_timeout"):
        preparation["bbox_stability"] = {"stable": True, "source": "captured-control-metadata"}
        preparation["interaction_state"] = {
            "exists": True, "visible": True,
            "hitTestPass": bool(control.get("interactable", True)),
            "disabled": bool(control.get("disabled")),
            "readonly": bool(control.get("readonly")),
            "blockingValidation": str(control.get("aria_invalid") or "") == "true",
        }
        return dict(control), current_controls, diagnostic, mask_sensitive_data(preparation)
    # A visually hidden radio/checkbox is operated through its label: probe the
    # label's geometry and hit test, keep the input's own disabled state.
    probe = str(control.get("click_proxy") or "") or selector
    if probe != selector:
        preparation["operated_through_label"] = probe
    # Controls below the fold (Document Type identifier rows, attributes,
    # validation) fail an elementFromPoint hit test until they are scrolled in.
    preparation["scroll_into_view"] = await scroll_control_into_view(page, probe)
    bbox = await wait_for_stable_bounding_box(
        page,
        probe,
        timeout_ms=int(profile.get("transaction_timeout_ms", 5000)),
        stable_samples=int(profile.get("bbox_stable_samples", 2)),
        interval_ms=int(profile.get("poll_interval_ms", 100)),
        tolerance_px=float(profile.get("bbox_tolerance_px", 0.75)),
    )
    preparation["bbox_stability"] = bbox
    state = bbox.get("state") if isinstance(bbox.get("state"), dict) else await inspect_interaction_state(page, probe)
    if probe != selector:
        own = await inspect_interaction_state(page, selector)
        state = dict(state, disabled=bool(own.get("disabled")), readonly=bool(own.get("readonly")))
    preparation["interaction_state"] = state
    if not bbox.get("stable") or not state.get("visible"):
        return None, current_controls, diagnostic, mask_sensitive_data(preparation)
    if str(node.get("action") or "") not in {"verify_only"} and (
        state.get("disabled") or state.get("readonly") or not state.get("hitTestPass")
    ):
        # A control that is disabled or covered while the portal shows its own
        # blocking loader is waiting for the portal, not wrong: wait for the
        # portal (bounded by the loading watchdog).  If the loader never clears
        # the portal-level error ends the attempt for the refresh/restart ladder.
        session = getattr(page, "_hip_browser_session", None)
        if session is not None and hasattr(session, "_current_loading_state") and hasattr(session, "ensure_interactable"):
            try:
                loader = await session._current_loading_state(target_selector=selector)
            except Exception:
                loader = {}
            if loader.get("active"):
                preparation["portal_loader_wait"] = {"blocking_loader": True}
                try:
                    await session.ensure_interactable(action=f"wait for portal loading before {node.get('field_key')}", selector=selector, timeout_ms=5000)
                except Exception as exc:
                    raise_if_environment_fatal(exc)
                    preparation["portal_loader_wait"]["error"] = mask_sensitive_string(str(exc))[:300]
                state = await inspect_interaction_state(page, probe)
                if probe != selector:
                    own = await inspect_interaction_state(page, selector)
                    state = dict(state, disabled=bool(own.get("disabled")), readonly=bool(own.get("readonly")))
                preparation["interaction_state_after_portal_loader"] = state
    if str(node.get("action") or "") not in {"verify_only"}:
        if state.get("disabled") or state.get("readonly"):
            equal = _value_equal if document_type else _stateful_value_equal
            if equal(node, dict(control, disabled=True)):
                # Portal-owned value already shows the requested state; the
                # caller records it as verification-only, never as a mutation.
                preparation["portal_owned_readonly_value"] = True
                return dict(control), current_controls, diagnostic, mask_sensitive_data(preparation)
            preparation["blocked_reason"] = "HIP_READONLY_PORTAL_VALUE_MISMATCH: target control is disabled/read-only and shows a different value"
            return None, current_controls, diagnostic, mask_sensitive_data(preparation)
        if not state.get("hitTestPass"):
            preparation["scroll_into_view_retry"] = await scroll_control_into_view(page, probe)
            state = await inspect_interaction_state(page, probe)
        if not state.get("hitTestPass"):
            session = getattr(page, "_hip_browser_session", None)
            if session is not None and hasattr(session, "ensure_interactable"):
                try:
                    await session.ensure_interactable(action="prepare stateful control", selector=probe, timeout_ms=5000)
                    state = await inspect_interaction_state(page, probe)
                    preparation["interaction_state_after_guard"] = state
                except Exception as exc:
                    raise_if_environment_fatal(exc)
                    preparation["interaction_guard_error"] = mask_sensitive_string(str(exc))
            if not state.get("hitTestPass"):
                preparation["blocked_reason"] = "target center intercepted"
                return None, current_controls, diagnostic, mask_sensitive_data(preparation)
    return dict(control), current_controls, diagnostic, mask_sensitive_data(preparation)


async def _wait_for_parent_children_visible(
    page: Page,
    graph: Dict[str, Any],
    parent_node: Dict[str, Any],
    *,
    phase: str,
    node_status: Dict[str, bool],
    profile: Dict[str, Any],
    document_type: bool = False,
) -> Dict[str, Any]:
    """Gate a parent commit on visibility of its eligible conditional children."""
    parent_id = str(parent_node.get("node_id") or "")
    children = eligible_child_nodes(graph, parent_id, {**node_status, parent_id: True})
    if not children:
        return {"pass": True, "parent_node_id": parent_id, "children": [], "reason": "no eligible conditional children"}
    capture = capture_document_type_controls if document_type else (lambda pg: capture_stateful_controls(pg, phase))
    resolver = resolve_document_type_control_diagnostics if document_type else resolve_stateful_control_diagnostics
    if document_type and str(parent_node.get("action") or "") == "select_single":
        # Conditional Document Type children are rendered after the parent DDS
        # overlay commits and loses focus, not merely after its input text changes.
        await close_open_dropdown(page, phase)
        await page.wait_for_timeout(int(profile.get("poll_interval_ms", 120)))
    deadline = asyncio.get_running_loop().time() + int(profile.get("child_visibility_timeout_ms", 4000)) / 1000.0
    matrix: List[Dict[str, Any]] = []
    stable_needed = max(2, int(profile.get("post_commit_generation_stable_samples", 2) or 2))
    stable_count = 0
    last_digest = ""
    samples = 0
    while asyncio.get_running_loop().time() < deadline:
        samples += 1
        controls = await capture(page)
        controls, row_binding = _apply_repeatable_row_bindings(controls, graph)
        matrix = []
        all_required = True
        digest_rows: List[Dict[str, Any]] = []
        for child in children:
            diag = resolver(controls, child)
            control = diag.get("control") if isinstance(diag.get("control"), dict) else None
            visible = bool(diag.get("resolved") and control)
            # A parent reveals a repeatable section with its first row; rows 2..N
            # come from "+ Add", so their absence does not fail the parent.
            later_row = child.get("row_index") is not None and int(child.get("row_index") or 0) > 0
            required = bool(child.get("required", True)) and not later_row
            if required and not visible:
                all_required = False
            row = {
                "node_id": child.get("node_id"),
                "field_key": child.get("field_key"),
                "required": required,
                "visible_and_bindable": visible,
                "binding": diag,
            }
            matrix.append(row)
            digest_rows.append({
                "node_id": str(child.get("node_id") or ""),
                "resolved": visible,
                "selector": str((control or {}).get("selector") or ""),
                "semantic_key": str((control or {}).get("semantic_key") or ""),
                "row_index": (control or {}).get("row_index"),
                "expected_row_index": (control or {}).get("expected_row_index"),
                "row_semantic_identity": (control or {}).get("row_semantic_identity"),
            })
        if all_required:
            digest = hashlib.sha256(json.dumps(digest_rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            stable_count = stable_count + 1 if digest == last_digest else 1
            last_digest = digest
            if stable_count >= stable_needed:
                return {
                    "pass": True, "parent_node_id": parent_id, "children": matrix,
                    "samples": samples, "consecutive_samples": stable_count,
                    "child_binding_digest": digest,
                    "row_identity_reconciliation": row_binding,
                }
        else:
            stable_count = 0
            last_digest = ""
        await page.wait_for_timeout(int(profile.get("poll_interval_ms", 120)))
    return {
        "pass": False,
        "parent_node_id": parent_id,
        "children": matrix,
        "samples": samples,
        "consecutive_samples": stable_count,
        "child_binding_digest": last_digest,
        "reason": "HIP_CONDITIONAL_CHILD_NOT_VISIBLE_AFTER_PARENT",
    }


async def _recommit_structural_parent_if_needed(
    page: Page,
    graph: Dict[str, Any],
    node: Dict[str, Any],
    *,
    phase: str,
    node_by_id: Dict[str, Dict[str, Any]],
    profile: Dict[str, Any],
    document_type: bool = False,
) -> Dict[str, Any]:
    """Re-fire an already selected parent through real browser events when a child is absent."""
    audit: Dict[str, Any] = {"attempted": False, "parents": []}
    if not node.get("depends_on"):
        return audit
    capture = capture_document_type_controls if document_type else (lambda pg: capture_stateful_controls(pg, phase))
    resolver = resolve_document_type_control_diagnostics if document_type else resolve_stateful_control_diagnostics
    equal = _value_equal if document_type else _stateful_value_equal
    controls = await capture(page)
    controls, row_binding = _apply_repeatable_row_bindings(controls, graph)
    audit["row_identity_reconciliation"] = row_binding
    root = await get_active_form_root(page, phase)
    for parent_id in [str(x) for x in node.get("depends_on", [])]:
        parent = node_by_id.get(parent_id)
        if not isinstance(parent, dict):
            continue
        diag = resolver(controls, parent)
        control = diag.get("control") if isinstance(diag.get("control"), dict) else None
        if control is None or not equal(parent, control):
            continue
        action = str(parent.get("action") or "")
        if action not in {"select_single", "select_multi", "select_radio"}:
            continue
        audit["attempted"] = True
        cursor = await _mark_dom_transition_cursor(page)
        ok = False
        try:
            if action == "select_single":
                ok = await select_dds_combobox(page, root, str(control.get("selector") or ""), str(parent.get("expected_value") or ""), phase=phase)
            elif action == "select_multi":
                ok = await select_dds_multiselect(page, root, str(control.get("selector") or ""), split_multi_value(parent.get("expected_value")), phase=phase)
            elif action == "select_radio":
                ok = await select_radio_option(page, str(control.get("selector") or ""), str(parent.get("expected_value") or ""), phase=phase)
                if ok is None:
                    ok = await select_radio_value(page, root, str(parent.get("expected_value") or ""), section=str(parent.get("section") or ""), phase=phase)
        except Exception as exc:
            audit["parents"].append({"node_id": parent_id, "success": False, "error": mask_sensitive_string(str(exc))})
            continue
        await _wait_for_dom_transition_activity(page, cursor, timeout_ms=int(profile.get("child_visibility_timeout_ms", 2600)))
        window = await _collect_dom_transition_window(page, cursor)
        audit["parents"].append({
            "node_id": parent_id,
            "success": bool(ok),
            "event_proof": explicit_event_proof(summarize_dom_transition_window(window), action, executor_ok=bool(ok)),
        })
    return mask_sensitive_data(audit)


def _control_broker_proof(page: Page, selector: str = "") -> Dict[str, Any]:
    """Find authoritative physical interaction evidence for the current control generation."""
    target = str(selector or "")
    rows = getattr(page, "_hip_control_execution_history", None)
    if isinstance(rows, list):
        matching = [
            row for row in rows
            if isinstance(row, dict) and row.get("success")
            and (not target or str(row.get("selector") or "") == target)
        ]
        for row in reversed(matching):
            if row.get("authoritative") is True:
                return dict(row)
        if matching:
            return dict(matching[-1])
    latest = read_last_control_execution(page)
    if latest and (not target or str(latest.get("selector") or "") == target):
        return latest
    return {}


async def execute_phase_state_graph(
    page: Page,
    graph: Dict[str, Any],
    *,
    phase: str,
    section: str | None = None,
    max_retries: int = 1,
    repair: bool = True,
    prior_attempts: Sequence[Dict[str, Any]] | None = None,
    strict_live_execution: bool = False,
) -> Dict[str, Any]:
    """Verify/repair any HIP phase through one-to-one state transactions.

    Complex phase modules still create rows/accordions. This shared engine then
    binds every input node to exactly one live control, protects already committed
    values, waits for stable Angular/DDS state, and rejects ambiguous repairs.
    """
    graph = _apply_validated_flow_pattern_memory(page, graph, phase)
    graph = apply_dependency_execution_contract(graph, phase=phase)
    dependency_contract = graph.get("dependency_execution_contract") or {}
    if dependency_contract.get("cycle_node_ids"):
        raise RuntimeError(
            "HIP_PARENT_CHILD_DEPENDENCY_CYCLE: "
            + ", ".join(dependency_contract.get("cycle_node_ids") or [])
        )
    attempts: List[Dict[str, Any]] = []
    observed_edges: List[Dict[str, Any]] = []
    selected_nodes = [
        n for n in graph.get("nodes", [])
        if isinstance(n, dict) and (not section or _section_matches(section, str(n.get("section") or "")))
    ]
    selected_ids = {str(n.get("node_id")) for n in selected_nodes}
    # A section run (BizFlow tabs) is judged on that section's nodes only; the
    # other tabs' controls are not on screen and cannot be one-to-one bound.
    model_graph = dict(graph, nodes=selected_nodes) if section else graph
    node_status: Dict[str, bool] = {
        str(dep): True for n in selected_nodes for dep in n.get("depends_on", []) if str(dep) not in selected_ids
    }
    ordering_predecessor_failures: Dict[str, List[str]] = {}
    completed_node_ids: List[str] = []
    _begin_executor_run(page)
    initial_controls = await capture_stateful_controls(page, phase)
    initial_controls, initial_row_binding = _apply_repeatable_row_bindings(initial_controls, graph)
    # Never bind a create-state graph against the listing/search surface. A reload
    # can keep the same route URL while closing Dell's unsaved drawer/wizard.
    # Tests/custom sessions may inject a valid semantic control model without a full
    # browser surface, so exact semantic overlap remains an accepted proof.
    surface_gate = await assert_active_surface(page, phase)
    expected_keys = {_norm(n.get("field_key")) for n in selected_nodes if isinstance(n, dict)}
    observed_keys = {_norm(c.get("semantic_key") or c.get("label")) for c in initial_controls if isinstance(c, dict)}
    semantic_overlap = {k for k in expected_keys if k and k in observed_keys}
    if surface_gate.get("fatal") and not semantic_overlap:
        raise RuntimeError(
            "HIP_PHASE_ACTIVE_FORM_SURFACE_LOST: " + "; ".join(surface_gate.get("fatal") or [])
        )
    agentq_phase_start = await _agentq_begin_phase(
        page, phase=phase, controls=initial_controls, surface_gate=surface_gate, graph=graph
    )
    initial_form_model = build_phase_form_state_model(model_graph, initial_controls, phase=phase)
    execution_profile = derive_execution_profile(initial_form_model, graph)
    interaction_profile = dict(execution_profile.get("profile") or {})
    node_by_id = {str(n.get("node_id")): n for n in selected_nodes if isinstance(n, dict)}
    current_controls = initial_controls

    # v2.2.5: live missions must prove the execution pipeline itself before any
    # independent judge is allowed to review the phase.  The old module-specific
    # collectors can miss DDS/shadow-DOM controls and historically caused a form
    # to be observed/open while the authoritative state graph was silently skipped.
    # Strict mode therefore requires at least one semantic binding on the active
    # create surface and later requires at least one successful writable node
    # transaction whenever the input graph contains writable values.
    contract_nodes = [
        n for n in selected_nodes
        if isinstance(n, dict) and n.get("expected_value") not in (None, "", [])
    ]
    writable_contract_nodes = [n for n in contract_nodes if str(n.get("action") or "") != "verify_only"]
    initial_root_nodes = [n for n in writable_contract_nodes if not (n.get("depends_on") or [])] or writable_contract_nodes
    initial_binding_diagnostics = [resolve_stateful_control_diagnostics(initial_controls, n) for n in initial_root_nodes]
    initially_bound_node_ids = [
        str(n.get("node_id") or "")
        for n, diag in zip(initial_root_nodes, initial_binding_diagnostics)
        if isinstance(diag, dict) and diag.get("resolved")
    ]
    strict_stage_audit: Dict[str, Any] = {
        "schema_version": "hip.live-execution-stage-audit.v1",
        "phase": phase,
        "section": section or "all",
        "form_opened": not bool(surface_gate.get("fatal")),
        "expected_node_count": len(contract_nodes),
        "writable_expected_node_count": len(writable_contract_nodes),
        "controls_discovered": bool(initial_controls),
        "initial_control_count": len(initial_controls),
        "controls_bound": bool(initially_bound_node_ids) if writable_contract_nodes else True,
        "initially_bound_node_ids": initially_bound_node_ids,
        "fields_filled_or_verified": False,
        "exact_execution_verified": False,
    }
    if strict_live_execution and writable_contract_nodes and not initial_controls:
        raise RuntimeError(
            f"HIP_FORM_CONTROLS_NOT_DISCOVERED: phase={phase} section={section or 'all'} "
            f"form was reached but 0 stateful controls were captured for {len(writable_contract_nodes)} writable expected nodes"
        )
    if strict_live_execution and writable_contract_nodes and not initially_bound_node_ids:
        sample = [
            {
                "field": n.get("field_key"),
                "section": n.get("section"),
                "row_kind": n.get("row_kind"),
                "row_index": n.get("row_index"),
                "reason": (diag or {}).get("reason"),
                "best_score": (diag or {}).get("best_score"),
            }
            for n, diag in list(zip(initial_root_nodes, initial_binding_diagnostics))[:8]
        ]
        raise RuntimeError(
            "HIP_FORM_CONTROLS_NOT_BOUND: active form was captured but no authoritative root input node bound to a live control; "
            + mask_sensitive_string(json.dumps(sample, ensure_ascii=False, default=str))
        )

    # v2.1.7: structured Playwright MCP form filling for ordinary, independent
    # text controls.  Dynamic/dependent/repeatable/DDS controls stay on the existing
    # one-to-one transaction path.  The batch is only attempted when Layer-11 owns
    # the page and the live MCP actually exposes browser_fill_form.
    session = getattr(page, "_hip_browser_session", None)
    mcp_backend = getattr(page, "_hip_playwright_mcp_backend", None)
    mcp_cfg = getattr(getattr(session, "config", None), "mcp", None) if session is not None else None
    batch_enabled = bool(
        session is not None
        and getattr(getattr(session, "semantic_action_gate", None), "enabled", False)
        and mcp_backend is not None
        and "browser_fill_form" in getattr(getattr(mcp_backend, "client", None), "tools", {})
        and bool(getattr(mcp_cfg, "playwright_mcp_fill_form_enabled", True))
    )
    min_batch_fields = max(2, int(getattr(mcp_cfg, "playwright_mcp_fill_form_min_fields", 2) or 2)) if mcp_cfg is not None else 2
    if batch_enabled:
        by_section: Dict[str, List[Dict[str, Any]]] = {}
        for n in selected_nodes:
            if str(n.get("action") or "") != "fill_text" or n.get("depends_on") or n.get("row_index") is not None:
                continue
            expected = n.get("expected_value")
            if expected in (None, "", []):
                continue
            diag = resolve_stateful_control_diagnostics(initial_controls, n)
            control = diag.get("control") if isinstance(diag.get("control"), dict) else None
            if control is None or not diag.get("resolved") or _stateful_value_equal(n, control):
                continue
            selector = str(control.get("selector") or "")
            if not selector:
                continue
            section_key = str(n.get("section") or control.get("section") or "")
            by_section.setdefault(section_key, []).append({
                "selector": selector,
                "value": str(expected),
                "label": str(control.get("label") or n.get("field_key") or "HIP Portal text field"),
                "field": str(n.get("field_key") or ""),
                "section": section_key,
            })
        for section_key, batch_items in by_section.items():
            if len(batch_items) < min_batch_fields:
                continue
            batch_result = await set_text_controls_batch(page, batch_items, phase=phase)
            if not batch_result.get("pass"):
                raise RuntimeError(
                    "HIP_STRUCTURED_FORM_FILL_FAILED: "
                    + str(batch_result.get("status") or "browser_fill_form did not verify")
                    + (f" section={section_key}" if section_key else "")
                )
            # Re-capture so the normal transaction loop sees exact committed values
            # and records verification-only attempts for each batch-filled node.
            initial_controls = await capture_stateful_controls(page, phase)
            initial_controls, initial_row_binding = _apply_repeatable_row_bindings(initial_controls, graph)
            current_controls = initial_controls

    for order, node in enumerate(selected_nodes, start=1):
        publish_executor_progress(page, phase=phase, node=node, stage="start")
        expected = node.get("expected_value")
        node_id = str(node.get("node_id") or "")
        if expected is None or expected == "" or expected == []:
            node_status[node_id] = True
            continue
        # Ordering-only predecessors (earlier section/row) never block this node;
        # see execute_document_type_state_graph.
        unmet = [str(d) for d in structural_dependencies(graph, node) if node_status.get(str(d)) is False]
        ordering_unmet = [str(d) for d in ordering_only_dependencies(graph, node) if node_status.get(str(d)) is False]
        if ordering_unmet:
            ordering_predecessor_failures[node_id] = ordering_unmet
        if unmet:
            attempts.append({
                "node_id": node.get("node_id"), "field": node.get("field_key"),
                "input_path": node.get("input_path"), "section": node.get("section"),
                "row_kind": node.get("row_kind"), "row_index": node.get("row_index"),
                "expected_value": expected, "success": False, "filled": False,
                "exact_verified": False, "reason": f"dependency failed: {unmet}", "order": order,
            })
            node_status[node_id] = False
            continue

        if str(node.get("action") or "") == "upload_file":
            matched = next((
                a for a in (prior_attempts or [])
                if isinstance(a, dict)
                and _norm(a.get("field")) == _norm(node.get("field_key"))
                and bool(a.get("success", a.get("filled")))
                and not ((a.get("validation") or {}).get("blocking"))
            ), None)
            if matched is not None:
                attempts.append({
                    "node_id": node.get("node_id"), "field": node.get("field_key"),
                    "input_path": node.get("input_path"), "section": node.get("section"),
                    "row_kind": node.get("row_kind"), "row_index": node.get("row_index"),
                    "expected_value": expected,
                    "actual_value": matched.get("uploaded_file_name") or matched.get("value_redacted"),
                    "success": True, "filled": True, "exact_verified": True,
                    "reason": "phase-specific upload contract accepted file",
                    "selector": matched.get("file_input_selector") or matched.get("selector"),
                    "executor": "phase-specific-contract-aware-uploader", "authoritative_execution": True, "order": order,
                    "binding_diagnostics": {"resolved": True, "reason": "phase-specific uploader"},
                    "transaction_proof": {"stable": True, "protected_state_changes": []},
                })
                node_status[node_id] = True
                completed_node_ids.append(node_id)
                continue

        # Always recapture from the current Angular generation before each node.
        # Cached controls are evidence only; they are never durable locators.
        before = await capture_stateful_controls(page, phase)
        before, row_binding_before = _apply_repeatable_row_bindings(before, graph)
        base_binding = resolve_stateful_control_diagnostics(before, node)
        node, action_model_plan, representation_before = await _agentq_plan_node(
            page, phase=phase, node=node, controls=before, binding=base_binding, surface_gate=surface_gate
        )
        learned_binding = resolve_stateful_control_diagnostics(before, node)
        if learned_binding.get("resolved") and not base_binding.get("resolved"):
            action_model_plan = {
                **action_model_plan,
                "selected_action": "execute_bound_action",
                "selected_reason": "trajectory memory prior produced a unique live binding",
                "selected_control_identity": learned_binding.get("selected_identity"),
                "binding_score": learned_binding.get("best_score"),
                "binding_margin": learned_binding.get("score_margin"),
                "memory_rebound": True,
            }
        selected_model_action = str(action_model_plan.get("selected_action") or "")
        if selected_model_action == "stop_surface_lost":
            raise RuntimeError("HIP_PHASE_ACTIVE_FORM_SURFACE_LOST: action model rejected the current surface")
        if selected_model_action in {"wait_for_rerender", "rebind_after_rerender"}:
            await page.wait_for_timeout(int(interaction_profile.get("poll_interval_ms", 120)) * 2)
            before = await capture_stateful_controls(page, phase)
            before, row_binding_before = _apply_repeatable_row_bindings(before, graph)
        if selected_model_action == "recover_stale_overlay":
            session = getattr(page, "_hip_browser_session", None)
            candidate_control = base_binding.get("control") if isinstance(base_binding.get("control"), dict) else {}
            selector_hint = str(candidate_control.get("selector") or "")
            if session is not None and selector_hint and hasattr(session, "_neutralize_stale_dds_loading_overlay"):
                await session._neutralize_stale_dds_loading_overlay(
                    target_selector=selector_hint, reason=f"agentq action model before {node.get('field_key')}"
                )
                before = await capture_stateful_controls(page, phase)
                before, row_binding_before = _apply_repeatable_row_bindings(before, graph)
        control, before, binding_before, preparation = await _prepare_phase_control_for_action(
            page, graph, node, phase=phase, controls=before, profile=interaction_profile, document_type=False
        )
        parent_recommit = {}
        if control is None and node.get("depends_on"):
            parent_recommit = await _recommit_structural_parent_if_needed(
                page, graph, node, phase=phase, node_by_id=node_by_id, profile=interaction_profile, document_type=False
            )
            node_wait = node.get("dependency_wait_profile") if isinstance(node.get("dependency_wait_profile"), dict) else {}
            child_timeout_ms = max(
                int(interaction_profile.get("child_visibility_timeout_ms", 4000)),
                int(node_wait.get("mount_timeout_ms", 0) or 0),
            )
            if ordering_unmet:
                # Best effort after a failed earlier section/row: try the field if
                # it is already there, but do not wait out a full mount timeout.
                child_timeout_ms = min(child_timeout_ms, int(interaction_profile.get("child_visibility_timeout_ms", 1500)), 1500)
            deadline = asyncio.get_running_loop().time() + child_timeout_ms / 1000.0
            while control is None and asyncio.get_running_loop().time() < deadline:
                await page.wait_for_timeout(int(interaction_profile.get("poll_interval_ms", 120)))
                before = await capture_stateful_controls(page, phase)
                before, row_binding_before = _apply_repeatable_row_bindings(before, graph)
                control, before, binding_before, preparation = await _prepare_phase_control_for_action(
                    page, graph, node, phase=phase, controls=before, profile=interaction_profile, document_type=False
                )
        protected_before = _snapshot_stateful_node_states(graph, before, completed_node_ids)
        dom_cursor = await _mark_dom_transition_cursor(page)
        transaction_proof: Dict[str, Any] = {
            "binding_before": binding_before,
            "row_identity_before": row_binding_before,
            "row_binding_for_node": row_binding_for_node(row_binding_before, node),
            "interaction_preparation": preparation,
            "parent_recommit": parent_recommit,
            "protected_before": protected_before,
            "stability": {},
            "protected_state_changes": [],
            "explicit_event_proof": {},
            "conditional_child_visibility": {},
            "action_model_plan": action_model_plan,
        }

        if control is not None and _stateful_value_equal(node, control):
            prior_broker = _control_broker_proof(page, str(control.get("selector") or ""))
            attempts.append(mask_sensitive_data({
                "node_id": node.get("node_id"), "field": node.get("field_key"),
                "input_path": node.get("input_path"), "section": node.get("section"),
                "row_kind": node.get("row_kind"), "row_index": node.get("row_index"),
                "expected_value": expected,
                "actual_value": attempt_actual_value(node, control),
                "success": True, "filled": True, "exact_verified": True,
                "reason": "specialized filler committed exact value" if prior_broker.get("authoritative") else "value already present but no authoritative physical fill provenance",
                "selector": control.get("selector"),
                "executor": prior_broker.get("executor") or "verification-only",
                "authoritative_execution": bool(prior_broker.get("authoritative")),
                "broker_proof": prior_broker,
                "order": order,
                "binding_diagnostics": binding_before,
                "action_model_plan": action_model_plan,
                "transaction_proof": {**transaction_proof, "stability": {"stable": True, "verification_only": True}},
            }))
            attempts[-1]["action_critic"] = await _agentq_record_outcome(
                page, phase=phase, node=node, representation_before=representation_before,
                action_plan=action_model_plan, outcome=attempts[-1], controls_after=before, surface_gate=surface_gate
            )
            node_status[node_id] = True
            completed_node_ids.append(node_id)
            continue

        success = False
        reason = "required semantic control not found"
        actual = control
        if not binding_before.get("resolved"):
            reason = f"HIP_PHASE_AMBIGUOUS_CONTROL_BINDING: {binding_before.get('reason')}"
        elif (
            str(node.get("action")) == "verify_only" and control is not None
            and (control.get("disabled") or control.get("readonly"))
        ):
            # Retrying cannot change a portal-owned read-only value; say so
            # instead of reporting a failed "repair" every adaptive cycle.
            reason = (
                "HIP_READONLY_PORTAL_VALUE_MISMATCH: portal-owned read-only control shows a "
                "different value than input.json; correct input.json or the portal object"
            )
        elif repair and control is not None and str(node.get("action")) != "upload_file":
            node_started = asyncio.get_running_loop().time()
            for retry in range(max_retries + 1):
                if retry and asyncio.get_running_loop().time() - node_started > _node_time_budget_seconds(interaction_profile):
                    reason = "HIP_NODE_TIME_BUDGET_EXCEEDED: field did not commit within its time budget; the repair pass retries it"
                    break
                publish_executor_progress(page, phase=phase, node=node, stage="attempt", retry=retry)
                root = await get_active_form_root(page, phase)
                selector = str(control.get("selector") or "")
                try:
                    action = _effective_action(node, control)
                    if action != str(node.get("action") or ""):
                        transaction_proof["adapted_action"] = {"compiled": node.get("action"), "live": action}
                    ok = True
                    if action == "fill_text":
                        ok = await set_text_control(page, root, selector, str(expected), phase=phase)
                    elif action == "select_single":
                        ok = await select_dds_combobox(page, root, selector, str(expected), phase=phase)
                    elif action == "select_multi":
                        ok = await select_dds_multiselect(page, root, selector, split_multi_value(expected), phase=phase)
                    elif action == "select_radio":
                        if _norm(control.get("role")) in {"switch", "checkbox"} or _norm(control.get("type")) == "checkbox":
                            ok = await set_boolean_control(page, root, selector, expected, phase=phase)
                        else:
                            ok = await select_radio_option(page, selector, str(expected), phase=phase)
                            if ok is None:
                                ok = await select_radio_value(page, root, str(expected), section=str(node.get("section") or ""), phase=phase)
                    elif action == "toggle":
                        ok = await set_boolean_control(page, root, selector, expected, phase=phase)
                    elif action == "verify_only":
                        ok = True
                    if action in {"select_single", "select_radio", "select_multi", "toggle"}:
                        await _wait_for_dom_transition_activity(
                            page, dom_cursor, timeout_ms=int(interaction_profile.get("child_visibility_timeout_ms", 2600))
                        )
                    current, after_now, stability = await _wait_for_stateful_transaction_stable(
                        page, node, phase=phase,
                        timeout_ms=int(interaction_profile.get("transaction_timeout_ms", 6000)),
                        consecutive_samples=2,
                        interval_ms=int(interaction_profile.get("poll_interval_ms", 100)),
                    )
                    transaction_proof["stability"] = stability
                    transition_now = await _collect_dom_transition_window(page, dom_cursor)
                    transition_now_summary = summarize_dom_transition_window(transition_now)
                    event_proof = explicit_event_proof(transition_now_summary, action, executor_ok=bool(ok))
                    transaction_proof["explicit_event_proof"] = event_proof
                    if action in {"select_single", "select_multi", "select_radio", "toggle"} and current is not None and _stateful_value_equal(node, current):
                        if action in {"select_single", "select_multi"}:
                            await close_open_dropdown(page, phase)
                        generation_barrier = await _wait_for_post_commit_generation_barrier(
                            page, graph, node, phase=phase, node_status=node_status,
                            profile=interaction_profile, selector_before=selector, document_type=False
                        )
                        barrier_controls = generation_barrier.pop("controls", [])
                        transaction_proof["post_commit_generation_barrier"] = generation_barrier
                        if not generation_barrier.get("pass"):
                            after_now = barrier_controls or after_now
                            rebound = resolve_stateful_control_diagnostics(after_now, node)
                            rebound_control = rebound.get("control") if isinstance(rebound.get("control"), dict) else None
                            if rebound_control is not None:
                                current = rebound_control
                                actual = rebound_control
                            success = False
                            reason = str(generation_barrier.get("reason") or "HIP_POST_COMMIT_DOM_GENERATION_NOT_STABLE")
                            break
                        after_now = barrier_controls or after_now
                        rebound = resolve_stateful_control_diagnostics(after_now, node)
                        rebound_control = rebound.get("control") if isinstance(rebound.get("control"), dict) else None
                        if rebound_control is None or not _stateful_value_equal(node, rebound_control):
                            success = False
                            reason = "HIP_PARENT_COMMIT_LOST_AFTER_RERENDER"
                            break
                        current = rebound_control
                        actual = rebound_control
                    multi_proof = {"pass": True, "not_applicable": True}
                    if action == "select_multi":
                        multi_audit = await read_last_multiselect_audit(page)
                        final_snapshot = multi_audit.get("final_snapshot") if isinstance(multi_audit.get("final_snapshot"), dict) else {}
                        available_options = [
                            x.get("text") for x in final_snapshot.get("options", [])
                            if isinstance(x, dict) and x.get("text") and str(x.get("text")).strip().lower() != "select all"
                        ]
                        multi_proof = multiselect_exact_set_proof(
                            split_multi_value(expected),
                            (current or {}).get("selected_values", []),
                            selection_mode=str((current or {}).get("selection_mode") or final_snapshot.get("selection_mode") or ""),
                            selected_count=(current or {}).get("selected_count", final_snapshot.get("selected_count_summary")),
                            available_options=available_options,
                            option_universe_stable=final_snapshot.get("option_universe_stable"),
                        )
                        transaction_proof["multi_select_exact_set_proof"] = multi_proof
                        transaction_proof["multi_select_driver_audit"] = multi_audit
                    protected_after = _snapshot_stateful_node_states(graph, after_now, completed_node_ids, reference=protected_before)
                    unintended = _protected_state_changes(protected_before, protected_after)
                    transaction_proof["protected_after"] = protected_after
                    transaction_proof["protected_state_changes"] = unintended
                    if unintended:
                        reason = "HIP_PHASE_UNINTENDED_MUTATION"
                        actual = current
                        success = False
                        break
                    if current is not None and stability.get("stable") and _stateful_value_equal(node, current):
                        interaction_state = await inspect_interaction_state(page, str(current.get("selector") or ""))
                        transaction_proof["post_action_interaction_state"] = interaction_state
                        existing_object = existing_object_validation(node, interaction_state)
                        if existing_object:
                            transaction_proof["existing_object_validation"] = interaction_state.get("validationMessage")
                        if interaction_state.get("blockingValidation") and not existing_object:
                            reason = "HIP_FIELD_VALIDATION_BLOCKING"
                            actual = current
                            success = False
                            break
                        if not event_proof.get("pass"):
                            reason = "HIP_EXPLICIT_EVENT_PROOF_MISSING"
                            actual = current
                            success = False
                            break
                        if action == "select_multi" and not multi_proof.get("pass"):
                            reason = "HIP_MULTISELECT_EXACT_SET_PROOF_FAILED"
                            actual = current
                            success = False
                            break
                        child_gate = await _wait_for_parent_children_visible(
                            page, graph, node, phase=phase, node_status=node_status, profile=interaction_profile, document_type=False
                        ) if is_parent_node(graph, str(node.get("node_id") or "")) else {"pass": True, "children": []}
                        transaction_proof["conditional_child_visibility"] = child_gate
                        if not child_gate.get("pass"):
                            reason = str(child_gate.get("reason") or "HIP_CONDITIONAL_CHILD_NOT_VISIBLE_AFTER_PARENT")
                            actual = current
                            success = False
                            break
                        success = True
                        actual = current
                        reason = "targeted state transaction committed exact value"
                        break
                    if current is not None:
                        actual = current
                    reason = "control did not commit exact stable value after repair"
                except Exception as exc:
                    raise_if_environment_fatal(exc)
                    reason = mask_sensitive_string(str(exc))
                if retry < max_retries:
                    await close_open_dropdown(page, phase)
                    await page.wait_for_timeout(int(interaction_profile.get("poll_interval_ms", 100)))
                    refreshed_controls = await capture_stateful_controls(page, phase)
                    refreshed_controls, refreshed_row_binding = _apply_repeatable_row_bindings(refreshed_controls, graph)
                    transaction_proof["retry_row_identity"] = refreshed_row_binding
                    refreshed_diag = resolve_stateful_control_diagnostics(refreshed_controls, node)
                    refreshed = refreshed_diag.get("control") if isinstance(refreshed_diag.get("control"), dict) else None
                    transaction_proof["retry_binding"] = refreshed_diag
                    if refreshed is not None:
                        control = refreshed
        elif control is not None and str(node.get("action")) == "upload_file":
            reason = "file upload remains owned by the phase-specific contract-aware uploader"
        elif not node.get("required"):
            # V236: optional in the portal schema does NOT mean optional for the
            # current mission when the user supplied a concrete value. A supplied
            # input-owned attribute must be discovered/revealed and exact-readback
            # verified just like a required field. Only blank/non-applicable nodes
            # are removed when the graph is compiled.
            success = False
            reason = "HIP_INPUT_OWNED_OPTIONAL_CONTROL_NOT_PRESENT: supplied value has no live control"

        after = await capture_stateful_controls(page, phase)
        after, row_binding_after = _apply_repeatable_row_bindings(after, graph)
        transaction_proof["row_identity_after"] = row_binding_after
        current_controls = after
        transition_window = await _collect_dom_transition_window(page, dom_cursor)
        transition_summary = summarize_dom_transition_window(transition_window)
        if success and str(node.get("action")) in {"select_single", "select_radio", "select_multi"}:
            before_ids = {(_norm(c.get("section")), _norm(c.get("label")), c.get("row_index"), c.get("label_occurrence")) for c in before}
            after_ids = {(_norm(c.get("section")), _norm(c.get("label")), c.get("row_index"), c.get("label_occurrence")) for c in after}
            for child in sorted(after_ids - before_ids, key=str):
                observed_edges.append({
                    "edge_id": _stable_id(node.get("node_id"), expected, child), "from": node.get("node_id"),
                    "to": f"{phase}.{child[0]}.{child[1]}[{child[2] if child[2] is not None else child[3]}]",
                    "relation": "parent_value_reveals_child", "when_parent_value": expected,
                    "source": "live_target_branch_execution", "evidence": "live_state_transition",
                    "event_evidence": transition_summary, "judge_status": "candidate_until_section_pass",
                })
        broker_proof = _control_broker_proof(page, str((actual or {}).get("selector") or (control or {}).get("selector") or ""))
        attempts.append(mask_sensitive_data({
            "node_id": node.get("node_id"), "field": node.get("field_key"), "input_path": node.get("input_path"),
            "section": node.get("section"), "row_kind": node.get("row_kind"), "row_index": node.get("row_index"),
            "expected_value": expected,
            "actual_value": attempt_actual_value(node, actual),
            "success": success, "filled": success, "exact_verified": success, "reason": reason,
            "selector": (actual or {}).get("selector"),
            "executor": broker_proof.get("executor") or node.get("executor"),
            "authoritative_execution": bool(broker_proof.get("authoritative")),
            "broker_proof": broker_proof,
            "order": order,
            "dependencies": node.get("depends_on", []), "dom_transition": transition_summary,
            "dom_events": (transition_window.get("events") or [])[-40:],
            "dom_mutations": (transition_window.get("mutations") or [])[-40:],
            "binding_diagnostics": binding_before, "action_model_plan": action_model_plan,
            "transaction_proof": transaction_proof,
        }))
        attempts[-1]["action_critic"] = await _agentq_record_outcome(
            page, phase=phase, node=node, representation_before=representation_before,
            action_plan=action_model_plan, outcome=attempts[-1], controls_after=after, surface_gate=surface_gate
        )
        node_status[node_id] = success
        publish_executor_progress(page, phase=phase, node=node, stage="done" if success else "failed")
        if success:
            completed_node_ids.append(node_id)

    final_controls = await capture_stateful_controls(page, phase)
    final_controls, final_row_binding = _apply_repeatable_row_bindings(final_controls, graph)
    node_by_id = {str(n.get("node_id")): n for n in selected_nodes}
    for attempt in attempts:
        if attempt.get("success") is not False or attempt.get("skipped"):
            continue
        node = node_by_id.get(str(attempt.get("node_id")))
        if not node:
            continue
        diagnostic = resolve_stateful_control_diagnostics(final_controls, node)
        control = diagnostic.get("control") if isinstance(diagnostic.get("control"), dict) else None
        if control is None or not _stateful_value_equal(node, control):
            continue
        prior_broker = _control_broker_proof(page, str(control.get("selector") or ""))
        attempt.update(mask_sensitive_data({
            "initial_failure_reason": attempt.get("reason"),
            "actual_value": attempt_actual_value(node, control),
            "success": True, "filled": True, "exact_verified": True,
            "reason": "final live-control exact reconciliation",
            "selector": control.get("selector"),
            "executor": prior_broker.get("executor") or "verification-only-final-reconciliation",
            "authoritative_execution": bool(prior_broker.get("authoritative")),
            "broker_proof": prior_broker,
            "binding_diagnostics": diagnostic,
        }))
        node_status[str(node.get("node_id"))] = True
        if str(node.get("node_id")) not in completed_node_ids:
            completed_node_ids.append(str(node.get("node_id")))

    final_form_model = build_phase_form_state_model(model_graph, final_controls, phase=phase)
    if not final_form_model.get("one_to_one_pass", False):
        attempts.append({
            "node_id": f"{phase}.form_state_model", "field": "phase_form_state_model",
            "success": False, "filled": False, "exact_verified": False,
            "reason": "HIP_PHASE_FORM_MODEL_NOT_ONE_TO_ONE", "model": final_form_model,
        })
    # V236 mission-owned completion contract: every graph node remaining after
    # compilation has a concrete nonblank input value and is therefore part of
    # the requested final state. Portal-level `required=False` only describes the
    # UI schema; it must never allow a supplied user attribute to disappear.
    contract_node_ids = {str(n.get("node_id") or "") for n in contract_nodes}
    failed = [
        a for a in attempts
        if a.get("success") is False and not a.get("skipped")
        and (
            str(a.get("field")) == "phase_form_state_model"
            or str(a.get("node_id") or "") in contract_node_ids
        )
    ]
    satisfied_contract_node_ids = {
        str(a.get("node_id") or "")
        for a in attempts
        if isinstance(a, dict)
        and a.get("success") is True
        and a.get("exact_verified") is True
        and str(a.get("node_id") or "") in contract_node_ids
    }
    unresolved_contract_node_ids = sorted(contract_node_ids - satisfied_contract_node_ids)
    if unresolved_contract_node_ids:
        # Add one synthetic blocking row so older checkpoint/reporting code that
        # consumes failed_attempts cannot accidentally treat partial coverage as pass.
        attempts.append({
            "node_id": f"{phase}.input_owned_completion_coverage",
            "field": "input_owned_completion_coverage",
            "success": False,
            "filled": False,
            "exact_verified": False,
            "reason": "HIP_INPUT_OWNED_FIELD_COVERAGE_INCOMPLETE",
            "unresolved_node_ids": unresolved_contract_node_ids,
        })
        failed.append(attempts[-1])
    successful_writable_attempts = [
        a for a in attempts
        if isinstance(a, dict)
        and a.get("success") is True
        and str(a.get("node_id") or "") in {str(n.get("node_id") or "") for n in writable_contract_nodes}
        and not a.get("skipped")
    ]
    exact_writable_attempts = [a for a in successful_writable_attempts if a.get("exact_verified") is True]
    authoritative_writable_attempts = [
        a for a in successful_writable_attempts
        if a.get("authoritative_execution") is True
        or str(a.get("executor") or "") == "phase-specific-contract-aware-uploader"
    ]
    authoritative_exact_attempts = [a for a in authoritative_writable_attempts if a.get("exact_verified") is True]
    non_authoritative_mutation_attempts = [
        a for a in exact_writable_attempts
        if not a.get("authoritative_execution")
        and str(a.get("executor") or "") not in {"verification-only", "verification-only-final-reconciliation"}
        and "value already present" not in str(a.get("reason") or "").lower()
    ]
    strict_stage_audit.update({
        "final_control_count": len(final_controls),
        "successful_writable_attempt_count": len(successful_writable_attempts),
        "exact_writable_attempt_count": len(exact_writable_attempts),
        "authoritative_writable_attempt_count": len(authoritative_writable_attempts),
        "authoritative_exact_attempt_count": len(authoritative_exact_attempts),
        "input_owned_expected_node_count": len(contract_node_ids),
        "input_owned_exact_node_count": len(satisfied_contract_node_ids),
        "input_owned_unresolved_node_ids": unresolved_contract_node_ids,
        "input_owned_coverage_percent": round((100.0 * len(satisfied_contract_node_ids) / len(contract_node_ids)), 2) if contract_node_ids else 100.0,
        "fields_filled_or_verified": not unresolved_contract_node_ids,
        "exact_execution_verified": bool(not failed and not unresolved_contract_node_ids),
        # Authoritative physical execution is mandatory for nodes that actually
        # required mutation. Nodes already equal to the requested value may be
        # verification-only, so one such readback must not make the whole phase
        # fail when every input-owned node is exact.
        "non_authoritative_mutation_attempt_count": len(non_authoritative_mutation_attempts),
        "authoritative_execution_verified": bool(
            not failed and not unresolved_contract_node_ids and not non_authoritative_mutation_attempts
        ),
        "authoritative_executor_policy": "BrowserSession broker (PyAutoGUI MCP primary) or contract-aware file uploader",
        "failed_attempt_count": len(failed),
    })
    if strict_live_execution and writable_contract_nodes and not successful_writable_attempts:
        raise RuntimeError(
            f"HIP_PHASE_EXACT_EXECUTION_NOT_COMPLETED: phase={phase} section={section or 'all'} "
            f"has {len(writable_contract_nodes)} writable expected nodes but no authoritative successful field transaction"
        )
    if strict_live_execution and writable_contract_nodes and not exact_writable_attempts:
        raise RuntimeError(
            f"HIP_PHASE_EXACT_EXECUTION_NOT_VERIFIED: phase={phase} section={section or 'all'} "
            "had field activity but no exact verified writable transaction"
        )

    if strict_live_execution and writable_contract_nodes and non_authoritative_mutation_attempts:
        raise RuntimeError(
            f"HIP_PHASE_AUTHORITATIVE_INTERACTION_NOT_VERIFIED: phase={phase} section={section or 'all'} "
            f"has {len(non_authoritative_mutation_attempts)} exact value(s) reached without authoritative BrowserSession/PyAutoGUI-MCP-first provenance"
            f" (fields: {', '.join(sorted({str(a.get('field') or a.get('node_id')) for a in non_authoritative_mutation_attempts})[:8])};"
            f" executors: {', '.join(sorted({str(a.get('executor') or '') for a in non_authoritative_mutation_attempts})[:4])})"
        )

    agentq_phase_summary = await _agentq_finalize_phase(
        page, phase=phase, controls=final_controls, success=not failed, surface_gate=surface_gate
    )
    return mask_sensitive_data({
        "schema_version": "hip.stateful-form-execution.v2",
        "phase": phase, "section": section or "all", "graph_id": graph.get("graph_id"),
        "strategy": graph.get("strategy"), "pass": not failed, "status": "pass" if not failed else "failed",
        "attempts": attempts, "failed_attempts": failed, "observed_dependency_edges": observed_edges,
        "strict_live_execution": bool(strict_live_execution), "execution_stage_audit": strict_stage_audit,
        "node_status": node_status, "final_controls": final_controls,
        "ordering_predecessor_failures": ordering_predecessor_failures,
        "initial_form_state_model": initial_form_model, "final_form_state_model": final_form_model,
        "completed_node_ids": completed_node_ids,
        "dependency_execution_contract": dependency_contract,
        "scheduler_final_state": scheduler_snapshot(
            dependency_contract, node_status, visible_node_ids=completed_node_ids
        ),
        "deterministic_replay_speed_profile": replay_speed_profile(dependency_contract),
        "transaction_policy": {
            "unique_binding_required": True, "minimum_confidence_margin": 14,
            "stable_consecutive_samples": 2, "protect_previously_committed_fields": True,
            "unintended_mutation_fails_action": True,
            "structural_parent_first": True,
            "explicit_widget_events_required": True,
            "parent_child_visibility_gate": True,
            "animation_bbox_stability_required": True,
        },
        "interaction_policy": policy_manifest(),
        "execution_profile": execution_profile,
        "flow_pattern_memory_match": graph.get("flow_pattern_memory_match") or {},
        "memory_replay_profile": graph.get("memory_replay_profile") or {},
        "agentq_phase_start": agentq_phase_start,
        "agentq_phase_summary": agentq_phase_summary,
        "fast_replay_blueprint": {
            "eligible": execution_profile.get("mode") == "validated_fast_replay",
            "structure_fingerprint": final_form_model.get("structure_fingerprint"),
            "skip_known_branch_exploration": execution_profile.get("mode") == "validated_fast_replay",
            "binding_identities": [
                (a.get("binding_diagnostics") or {}).get("selected_identity")
                for a in attempts if isinstance(a, dict) and (a.get("binding_diagnostics") or {}).get("selected_identity")
            ],
        },
    })

def build_target_branch_knowledge(graph: Dict[str, Any], execution: Dict[str, Any]) -> Dict[str, Any]:
    controls = []
    for c in execution.get("final_controls", []) if isinstance(execution.get("final_controls"), list) else []:
        if not isinstance(c, dict) or not c.get("semantic_key"):
            continue
        controls.append({
            "node_id": f"{graph.get('phase')}.{_norm(c.get('section'))}.{c.get('row_kind') or 'form'}[{c.get('row_index')}].{c.get('semantic_key')}",
            "key": c.get("semantic_key"), "label": c.get("label"), "section": c.get("section"),
            "row_kind": c.get("row_kind"), "row_index": c.get("row_index"), "role": c.get("role"),
            "type": c.get("type"), "name": c.get("name"), "placeholder": c.get("placeholder"),
            "framework_key": c.get("framework_key"), "form_control_name": c.get("form_control_name"),
            "component_tag": c.get("component_tag"), "semantic_path": c.get("semantic_path"),
            "selection_mode": c.get("selection_mode"), "required": c.get("required"),
            "interactable": c.get("interactable"), "bbox": c.get("bbox"),
            "selectors": {"live_dynamic": {"selector": c.get("selector"), "dynamic": True}},
            "preferred_locator": {
                "strategy": "semantic-row-scope",
                "section": c.get("section"), "row_kind": c.get("row_kind"), "row_index": c.get("row_index"),
                "name": c.get("name"), "label": c.get("label"), "placeholder": c.get("placeholder"),
                "framework_key": c.get("framework_key"), "component_tag": c.get("component_tag"),
                "semantic_path": c.get("semantic_path"),
            },
        })
    edges = [*(graph.get("dependency_edges") or []), *(execution.get("observed_dependency_edges") or [])]
    return mask_sensitive_data({
        "schema_version": "hip.portal-form-knowledge.v2",
        "phase": graph.get("phase"),
        "section": "Create Document Type" if "document_type" in str(graph.get("phase") or "") else str(execution.get("section") or graph.get("object_family") or graph.get("phase") or "form"),
        "status": "candidate" if not execution.get("pass") else "judge_pending",
        "learning_strategy": "target-input-branch-first",
        "field_nodes": controls,
        "control_registry": [
            {
                "key": c.get("key"), "label": c.get("label"), "section": c.get("section"),
                "row_kind": c.get("row_kind"), "row_index": c.get("row_index"),
                "role": c.get("role"), "type": c.get("type"), "name": c.get("name"),
                "placeholder": c.get("placeholder"), "selection_mode": c.get("selection_mode"),
                "required": c.get("required"), "framework_key": c.get("framework_key"),
                "form_control_name": c.get("form_control_name"), "component_tag": c.get("component_tag"),
                "semantic_path": c.get("semantic_path"), "interactable": c.get("interactable"),
                "selector": ((c.get("selectors") or {}).get("live_dynamic") or {}).get("selector"),
                "selector_dynamic": True,
            }
            for c in controls
        ],
        "dependency_edges": edges,
        "dom_event_contracts": [
            {
                "node_id": a.get("node_id"), "field": a.get("field"), "input_path": a.get("input_path"),
                "expected_value": a.get("expected_value"), "section": a.get("section"),
                "row_kind": a.get("row_kind"), "row_index": a.get("row_index"),
                "transition": a.get("dom_transition"),
                "evidence_status": "judge_pending" if execution.get("pass") else "candidate",
            }
            for a in (execution.get("attempts") or [])
            if isinstance(a, dict) and isinstance(a.get("dom_transition"), dict)
            and (a.get("dom_transition", {}).get("mutation_count") or a.get("dom_transition", {}).get("commit_events_seen"))
        ],
        "repeatable_rows": [
            {"section": str(execution.get("section") or graph.get("object_family") or graph.get("phase") or "form"), "row_kind": str(kind), "row_count": int(count or 0)}
            for kind, count in (graph.get("repeatable_rows") or {}).items()
        ],
        "target_branch_graph": graph,
        "target_branch_execution": {
            "pass": execution.get("pass"), "failed_attempts": execution.get("failed_attempts", []),
            "attempt_count": len(execution.get("attempts", [])),
            "initial_form_state_model": execution.get("initial_form_state_model") or {},
            "final_form_state_model": execution.get("final_form_state_model") or {},
            "transaction_proofs": [
                {
                    "node_id": a.get("node_id"), "field": a.get("field"),
                    "input_path": a.get("input_path"), "success": a.get("success"),
                    "binding_diagnostics": a.get("binding_diagnostics") or {},
                    "transaction_proof": a.get("transaction_proof") or {},
                }
                for a in (execution.get("attempts") or []) if isinstance(a, dict)
            ],
        },
        "promotion_rule": "promote only after deterministic DOM + text + vision section judges pass",
    })
