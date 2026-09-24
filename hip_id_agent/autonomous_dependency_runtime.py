"""Dependency-aware scheduling for autonomous HIP form execution.

The HIP Portal is a family of dynamic Angular forms.  A child control is often
not mounted until its parent value has been committed, its network request has
completed and the DDS widget has rerendered.  This module turns each phase state
graph into an auditable execution contract that is used by both learning and
validated fast replay.

The contract is deliberately value-light: expected customer values remain in the
current run input, while long-term memory stores only topology, selectors,
interaction profiles and successful ordering.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from .security import mask_sensitive_data


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]


# These are form-section barriers, not assumptions about customer values.  They
# prevent a later section from being executed before the structural parents that
# make it valid.  Fine-grained parent-value edges remain authoritative.
_SECTION_SEQUENCE: Dict[str, Tuple[str, ...]] = {
    "data_map": ("create_map",),
    "document_type": (
        "document_type_details",
        "document_identifier",
        "attributes_to_configure",
        "validation",
    ),
    "rule": ("rule_details", "actions", "conditions"),
    "transport_profile": ("create_transport_profile",),
    "biz_flow": (
        "flow_details",
        "configure_source",
        "configure_targets",
        "configure_routing",
    ),
}

# Required form-level prerequisites that HIP evaluates before enabling nested
# row creation or conditional children.  They are encoded by field key so the
# same policy survives dynamic IDs, labels and rerenders.
_FAMILY_GATES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "rule": {
        "conditions": (
            "rule_name",
            "document_type_name_version",
            "action_name",
            "action_type",
            "mapping_identifier_name_version",
        ),
        "mapping_identifier_name_version": ("action_type",),
    },
    "document_type": {
        "document_identifier": ("data_format_type",),
        "attributes_to_configure": ("data_format_type",),
        "validation": ("data_format_type",),
    },
    "transport_profile": {
        "interface_environment": ("interface_type",),
        "existing_account": ("interface_type",),
        "existing_account_name": ("interface_type",),
        "use_existing_folder": ("existing_account_name",),
        "subscription_folder": ("existing_account_name",),
        "file_filtering_pattern": ("interface_type",),
        "post_transfer_action": ("interface_type",),
        "document_type": ("interface_type",),
    },
    "biz_flow": {
        "source_application": ("source_type",),
        "source_transport_profile": ("source_application",),
        "source_document_type": ("source_application",),
        "target_application": ("target_type",),
        "target_transport_profile": ("target_application",),
        "target_document_type": ("target_application",),
        "route_action_target": ("route_action_type",),
    },
}


def _family(graph: Mapping[str, Any], phase: str = "") -> str:
    explicit = _norm(graph.get("object_family"))
    if explicit:
        return explicit
    p = _norm(phase or graph.get("phase"))
    if "document_type" in p:
        return "document_type"
    if "transport_profile" in p:
        return "transport_profile"
    if p in {"biz_flow", "bizflow"}:
        return "biz_flow"
    return p


def _canonical_section(value: Any) -> str:
    section = _norm(value)
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
        "create_rule": "rule_details",
        "rule": "rule_details",
        "action": "actions",
        "condition": "conditions",
    }
    return aliases.get(section, section)


def _node_signature(node: Mapping[str, Any]) -> str:
    return "|".join(
        [
            _canonical_section(node.get("section")),
            _norm(node.get("row_kind")),
            str(node.get("row_index") if node.get("row_index") is not None else ""),
            _norm(node.get("field_key")),
            _norm(node.get("action")),
        ]
    )


def _merge_dependencies(
    graph: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
    *,
    family: str,
) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]], List[str]]:
    node_ids = {str(n.get("node_id") or "") for n in nodes if str(n.get("node_id") or "")}
    by_key: Dict[str, List[str]] = defaultdict(list)
    by_section: Dict[str, List[str]] = defaultdict(list)
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        if not node_id:
            continue
        by_key[_norm(node.get("field_key"))].append(node_id)
        by_section[_canonical_section(node.get("section"))].append(node_id)

    dependencies: Dict[str, List[str]] = {node_id: [] for node_id in node_ids}
    edges: List[Dict[str, Any]] = []
    missing: List[str] = []

    def add(parent: str, child: str, *, relation: str, source: str, when: Any = None) -> None:
        parent = str(parent or "")
        child = str(child or "")
        if not parent or not child or parent == child:
            return
        if child not in node_ids:
            return
        # Dependencies may reference a structural parent owned by an outer
        # section/phase.  Keep it as external evidence but do not block this graph.
        if parent not in node_ids:
            missing.append(parent)
            return
        if parent not in dependencies[child]:
            dependencies[child].append(parent)
        edge = {
            "edge_id": _stable_id(parent, when, child, relation),
            "from": parent,
            "to": child,
            "relation": relation,
            "when_parent_value": when,
            "source": source,
            "values_stored": False,
        }
        if not any(e.get("from") == parent and e.get("to") == child for e in edges):
            edges.append(edge)

    # Preserve rich observed/value-specific edge evidence before adding the
    # simpler node.depends_on declarations. Reapplying the contract is therefore
    # idempotent and does not downgrade parent-value evidence to a generic edge.
    for edge in graph.get("dependency_edges", []) if isinstance(graph.get("dependency_edges"), list) else []:
        if not isinstance(edge, Mapping):
            continue
        add(
            str(edge.get("from") or ""),
            str(edge.get("to") or ""),
            relation=str(edge.get("relation") or "observed_dependency"),
            source=str(edge.get("source") or "state_graph_edge"),
            when=edge.get("when_parent_value"),
        )

    for node in nodes:
        child = str(node.get("node_id") or "")
        for parent in node.get("depends_on", []) or []:
            add(str(parent), child, relation="declared_dependency", source="state_graph")

    # Field-level family gates.  If multiple repeated rows share a field key,
    # every matching required parent precedes the child.  Row-local dependencies
    # already remain more specific and are preserved.
    gates = _FAMILY_GATES.get(family, {})
    for child_key, parent_keys in gates.items():
        child_ids: List[str] = []
        if child_key in by_key:
            child_ids.extend(by_key[child_key])
        if child_key in by_section:
            child_ids.extend(by_section[child_key])
        for child in dict.fromkeys(child_ids):
            for parent_key in parent_keys:
                for parent in by_key.get(parent_key, []):
                    add(parent, child, relation="form_validity_gate", source="family_parent_child_policy")

    # A later section depends on every required field in the previous populated
    # section.  This is intentionally stricter than a visual top-to-bottom sort:
    # Angular frequently leaves a later child visible while the form is still
    # invalid because an earlier sibling has not committed.
    sequence = _SECTION_SEQUENCE.get(family, ())
    populated = [s for s in sequence if by_section.get(s)]
    node_by_id = {str(n.get("node_id")): n for n in nodes}
    for previous, current in zip(populated, populated[1:]):
        required_previous = [
            node_id
            for node_id in by_section[previous]
            if bool(node_by_id[node_id].get("required", True))
        ] or list(by_section[previous])
        for child in by_section[current]:
            for barrier_parent in required_previous:
                add(
                    barrier_parent,
                    child,
                    relation="section_sequence_gate",
                    source="family_parent_child_policy",
                )

    # Repeatable rows are also sequential transactions.  Complete and verify row
    # N before touching row N+1; otherwise DDS rerenders can cause selectors from
    # the first row to be reused for later input data.
    repeated: Dict[Tuple[str, str], Dict[int, List[str]]] = defaultdict(lambda: defaultdict(list))
    for node in nodes:
        row_kind = _norm(node.get("row_kind"))
        row_index = node.get("row_index")
        if row_kind and isinstance(row_index, int):
            repeated[(_canonical_section(node.get("section")), row_kind)][row_index].append(str(node.get("node_id")))
    for (_section, _row_kind), rows in repeated.items():
        indexes = sorted(rows)
        for previous_index, current_index in zip(indexes, indexes[1:]):
            previous_required = [
                node_id for node_id in rows[previous_index]
                if bool(node_by_id[node_id].get("required", True))
            ] or list(rows[previous_index])
            for child in rows[current_index]:
                for parent in previous_required:
                    add(parent, child, relation="repeatable_row_sequence_gate", source="family_parent_child_policy")

    return dependencies, edges, sorted(set(missing))


def _topological_order(
    nodes: Sequence[Mapping[str, Any]],
    dependencies: Mapping[str, Sequence[str]],
    *,
    family: str,
) -> Tuple[List[str], List[str], Dict[str, int]]:
    node_by_id = {str(n.get("node_id") or ""): n for n in nodes if str(n.get("node_id") or "")}
    original = {node_id: index for index, node_id in enumerate(node_by_id)}
    indegree = {node_id: 0 for node_id in node_by_id}
    children: Dict[str, List[str]] = defaultdict(list)
    for child, parents in dependencies.items():
        for parent in parents:
            if child in indegree and parent in indegree:
                indegree[child] += 1
                children[parent].append(child)

    section_sequence = _SECTION_SEQUENCE.get(family, ())
    section_rank = {s: i for i, s in enumerate(section_sequence)}

    def priority(node_id: str) -> Tuple[int, int, int, int, str]:
        node = node_by_id[node_id]
        memory = node.get("validated_memory_order")
        memory_rank = int(memory) if isinstance(memory, (int, float)) and int(memory) > 0 else 1_000_000
        sec = section_rank.get(_canonical_section(node.get("section")), 50_000)
        row = int(node.get("row_index")) if isinstance(node.get("row_index"), int) else -1
        return memory_rank, sec, row, original[node_id], node_id

    ready = sorted((n for n, d in indegree.items() if d == 0), key=priority)
    ordered: List[str] = []
    levels: Dict[str, int] = {node_id: 0 for node_id in ready}
    while ready:
        node_id = ready.pop(0)
        ordered.append(node_id)
        for child in sorted(children.get(node_id, []), key=priority):
            levels[child] = max(levels.get(child, 0), levels.get(node_id, 0) + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort(key=priority)
    cycles = [node_id for node_id in node_by_id if node_id not in ordered]
    if cycles:
        # Fail closed in the contract, but retain deterministic original order so
        # forensic evidence can identify the bad edges without crashing import.
        ordered.extend(sorted(cycles, key=priority))
    return ordered, cycles, levels


def compile_dependency_execution_contract(graph: Mapping[str, Any], *, phase: str = "") -> Dict[str, Any]:
    nodes = [dict(n) for n in graph.get("nodes", []) if isinstance(n, Mapping)]
    family = _family(graph, phase)
    dependencies, inferred_edges, external = _merge_dependencies(graph, nodes, family=family)
    ordered, cycles, levels = _topological_order(nodes, dependencies, family=family)
    node_by_id = {str(n.get("node_id") or ""): n for n in nodes}
    children: Dict[str, List[str]] = defaultdict(list)
    for child, parents in dependencies.items():
        for parent in parents:
            children[parent].append(child)

    validated_replay = bool(graph.get("validated_replay") or graph.get("replay_blueprint_validated"))
    mode = "exploitation" if validated_replay else "exploration"
    wait_profiles: Dict[str, Dict[str, Any]] = {}
    for node_id in ordered:
        node = node_by_id[node_id]
        is_child = bool(dependencies.get(node_id))
        action = _norm(node.get("action"))
        field = _norm(node.get("field_key"))
        asynchronous = any(token in field for token in ("mapping", "application", "transport_profile", "document_type"))
        mount_timeout = 25000 if asynchronous else 12000 if is_child else 5000
        if validated_replay:
            mount_timeout = min(mount_timeout, 7000 if asynchronous else 3500)
        wait_profile = {
            "mount_timeout_ms": mount_timeout,
            "poll_interval_ms": 100 if validated_replay else 150,
            "stable_samples": 2,
            "rebind_after_parent": is_child,
            "owned_popup_required": action in {"select_single", "select_multi"},
            "state_proof_over_transport_timeout": True,
        }
        remembered_wait = node.get("validated_memory_wait_profile") if isinstance(node.get("validated_memory_wait_profile"), Mapping) else {}
        if validated_replay and remembered_wait:
            # Reuse only timing/interaction mechanics. Customer values never enter
            # this profile and the current live state remains authoritative.
            for key in ("mount_timeout_ms", "poll_interval_ms", "stable_samples", "rebind_after_parent", "owned_popup_required"):
                if key in remembered_wait:
                    wait_profile[key] = remembered_wait[key]
            wait_profile["source"] = "judge_validated_flow_pattern_memory"
        wait_profiles[node_id] = wait_profile

    material = {
        "family": family,
        "ordered": ordered,
        "dependencies": dependencies,
        "sections": list(_SECTION_SEQUENCE.get(family, ())),
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:24]
    return mask_sensitive_data(
        {
            "schema_version": "hip.autonomous-parent-child-contract.v1",
            "phase": phase or graph.get("phase") or "",
            "family": family,
            "mode": mode,
            "contract_fingerprint": fingerprint,
            "ordered_node_ids": ordered,
            "dependency_map": dependencies,
            "children_map": {k: v for k, v in children.items()},
            "dependency_levels": levels,
            "root_node_ids": [node_id for node_id in ordered if not dependencies.get(node_id)],
            "leaf_node_ids": [node_id for node_id in ordered if not children.get(node_id)],
            "inferred_edges": inferred_edges,
            "external_dependencies": external,
            "cycle_node_ids": cycles,
            "pass": not cycles,
            "section_sequence": list(_SECTION_SEQUENCE.get(family, ())),
            "wait_profiles": wait_profiles,
            "scheduler_policy": {
                "parent_must_commit_before_child": True,
                "child_must_mount_and_rebind": True,
                "verify_each_transition": True,
                "protect_completed_fields": True,
                "exploit_validated_path_first": True,
                "switch_to_exploration_on_drift": True,
                "promote_only_after_deterministic_text_and_vision_judges": True,
                "customer_values_persisted": False,
            },
        }
    )


def apply_dependency_execution_contract(graph: Mapping[str, Any], *, phase: str = "") -> Dict[str, Any]:
    """Return a copy of *graph* ordered and annotated for autonomous execution."""
    result: Dict[str, Any] = copy.deepcopy(dict(graph))
    contract = compile_dependency_execution_contract(result, phase=phase)
    node_by_id = {
        str(node.get("node_id") or ""): node
        for node in result.get("nodes", [])
        if isinstance(node, MutableMapping) and str(node.get("node_id") or "")
    }
    ordered_nodes: List[Dict[str, Any]] = []
    for order, node_id in enumerate(contract.get("ordered_node_ids") or [], start=1):
        node = node_by_id.get(str(node_id))
        if not node:
            continue
        node["depends_on"] = list((contract.get("dependency_map") or {}).get(node_id) or [])
        node["autonomous_order"] = order
        node["dependency_level"] = (contract.get("dependency_levels") or {}).get(node_id, 0)
        node["dependency_wait_profile"] = (contract.get("wait_profiles") or {}).get(node_id, {})
        node["execution_mode"] = contract.get("mode")
        ordered_nodes.append(node)
    result["nodes"] = ordered_nodes
    result["dependency_edges"] = list(contract.get("inferred_edges") or [])
    result["dependency_execution_contract"] = contract
    result["strategy"] = (
        "validated-parent-child-fast-replay" if contract.get("mode") == "exploitation"
        else "dependency-aware-exploration-then-deterministic-replay"
    )
    return result


def scheduler_snapshot(
    contract: Mapping[str, Any],
    node_status: Mapping[str, Any],
    *,
    visible_node_ids: Iterable[str] = (),
) -> Dict[str, Any]:
    """Pure diagnostic snapshot used by the runtime and offline tests."""
    visible = set(str(x) for x in visible_node_ids)
    pending: List[str] = []
    ready: List[str] = []
    waiting: List[Dict[str, Any]] = []
    complete: List[str] = []
    for node_id in contract.get("ordered_node_ids", []) or []:
        state = node_status.get(node_id)
        if state is True:
            complete.append(node_id)
            continue
        pending.append(node_id)
        parents = list((contract.get("dependency_map") or {}).get(node_id) or [])
        unmet = [p for p in parents if node_status.get(p) is not True]
        if unmet:
            waiting.append({"node_id": node_id, "reason": "waiting_for_parent_commit", "unmet": unmet})
        elif visible and node_id not in visible:
            waiting.append({"node_id": node_id, "reason": "waiting_for_child_mount", "unmet": []})
        else:
            ready.append(node_id)
    return {
        "mode": contract.get("mode"),
        "ready_node_ids": ready,
        "waiting_nodes": waiting,
        "pending_node_ids": pending,
        "completed_node_ids": complete,
        "progress": len(complete),
        "total": len(contract.get("ordered_node_ids") or []),
        "deadlocked": bool(pending and not ready and all(w.get("reason") == "waiting_for_parent_commit" for w in waiting)),
    }


def replay_speed_profile(contract: Mapping[str, Any]) -> Dict[str, Any]:
    exploitation = contract.get("mode") == "exploitation" and not contract.get("cycle_node_ids")
    return {
        "mode": "deterministic_fast_replay" if exploitation else "learning_exploration",
        "exploit_first": exploitation,
        "parallel_actions_allowed": False,
        "reason": "HIP parent-child controls must be committed sequentially",
        "expected_speedup": "selector/event/wait reuse; no structural rediscovery" if exploitation else "none until judges validate the path",
        "values_source": "current input.json",
    }
