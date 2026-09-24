from __future__ import annotations

"""Generation-safe identity reconciliation for HIP repeatable form rows.

HIP/DDS repeatable arrays are frequently rebuilt by Angular after +Add or after a
parent dropdown commits.  Physical DOM order and generated selectors therefore
cannot be treated as durable row identity.  This module derives value-free row
structure plus committed semantic anchors and reconciles expected input rows to
the current live generation.

The important policy is:

* a row with committed semantic anchors is matched by those anchors, not position;
* a completely blank row may use DOM order only as a *provisional* binding;
* once any expected anchor is committed, later captures can survive row reordering;
* conflicting non-blank rows are never silently rebound by ordinal position.
"""

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _semantic_value(value: Any) -> str:
    """Normalize enum-style and display-style values to one comparison form."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        parts = sorted({_semantic_value(v) for v in value if _semantic_value(v)})
        return "|".join(parts)
    text = _norm_text(value)
    # Dell input contracts commonly carry enum tokens while DDS renders title text.
    return re.sub(r"[^a-z0-9]+", "", text)


def values_equivalent(expected: Any, actual: Any) -> bool:
    if isinstance(expected, (list, tuple, set)):
        e = sorted({_semantic_value(v) for v in expected if _semantic_value(v)})
        if isinstance(actual, (list, tuple, set)):
            a = sorted({_semantic_value(v) for v in actual if _semantic_value(v)})
        else:
            a = sorted({_semantic_value(v) for v in str(actual or "").split(",") if _semantic_value(v)})
        return e == a
    return _semantic_value(expected) == _semantic_value(actual)


def _meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_meaningful(v) for v in value)
    return bool(str(value).strip())


def _control_semantic_keys(control: Mapping[str, Any]) -> List[str]:
    raw = [
        control.get("semantic_key"),
        control.get("framework_key"),
        control.get("form_control_name"),
        control.get("ng_reflect_name"),
        control.get("name"),
        control.get("label"),
        control.get("placeholder"),
    ]
    out: List[str] = []
    for item in raw:
        key = _norm(item)
        if key and key not in out:
            out.append(key)
    return out


def _node_semantic_keys(node: Mapping[str, Any]) -> List[str]:
    loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), Mapping) else {}
    raw: List[Any] = [node.get("field_key")]
    for k in ("names", "labels", "placeholders"):
        vals = loc.get(k, []) if isinstance(loc, Mapping) else []
        if isinstance(vals, (list, tuple, set)):
            raw.extend(vals)
    out: List[str] = []
    for item in raw:
        key = _norm(item)
        if key and key not in out:
            out.append(key)
    return out


def _control_committed_value(control: Mapping[str, Any]) -> Any:
    selected = control.get("selected_values")
    if isinstance(selected, list) and selected:
        return selected
    role = _norm(control.get("role") or control.get("type"))
    if role in {"checkbox", "radio", "switch"} and control.get("checked") is not None:
        if bool(control.get("checked")):
            # Radio labels often carry the real semantic value.
            return control.get("value") or control.get("label") or True
        return ""
    return control.get("value")


def _stable_hash(payload: Any, size: int = 14) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


@dataclass(frozen=True)
class LiveRow:
    row_kind: str
    physical_index: int
    controls: Tuple[Dict[str, Any], ...]
    structure_keys: Tuple[str, ...]
    committed_anchors: Tuple[Tuple[str, str], ...]
    structure_fingerprint: str
    semantic_fingerprint: str
    provisional: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "row_kind": self.row_kind,
            "physical_index": self.physical_index,
            "structure_keys": list(self.structure_keys),
            "committed_anchors": dict(self.committed_anchors),
            "structure_fingerprint": self.structure_fingerprint,
            "semantic_fingerprint": self.semantic_fingerprint,
            "provisional": self.provisional,
        }


def build_live_row_groups(controls: Sequence[Mapping[str, Any]]) -> List[LiveRow]:
    groups: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for raw in controls:
        if not isinstance(raw, Mapping):
            continue
        kind = _norm(raw.get("row_kind"))
        idx = raw.get("row_index")
        if not kind or idx is None:
            continue
        try:
            physical = int(idx)
        except Exception:
            continue
        groups.setdefault((kind, physical), []).append(dict(raw))

    rows: List[LiveRow] = []
    for (kind, physical), members in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        structure: List[str] = []
        anchors: Dict[str, str] = {}
        for control in members:
            keys = _control_semantic_keys(control)
            primary = keys[0] if keys else ""
            role = _norm(control.get("role") or control.get("type") or control.get("tag"))
            framework = _norm(control.get("framework_key") or control.get("form_control_name") or control.get("name"))
            if primary:
                structure.append("|".join(x for x in (primary, framework, role) if x))
            value = _control_committed_value(control)
            if primary and _meaningful(value):
                anchors[primary] = _semantic_value(value)
        structure_keys = tuple(sorted(set(structure)))
        anchor_items = tuple(sorted((k, v) for k, v in anchors.items() if v))
        structure_fp = _stable_hash({"row_kind": kind, "structure": structure_keys})
        semantic_fp = _stable_hash({"row_kind": kind, "structure": structure_keys, "anchors": anchor_items})
        rows.append(LiveRow(
            row_kind=kind,
            physical_index=physical,
            controls=tuple(members),
            structure_keys=structure_keys,
            committed_anchors=anchor_items,
            structure_fingerprint=structure_fp,
            semantic_fingerprint=semantic_fp,
            provisional=not bool(anchor_items),
        ))
    return rows


def _expected_rows(graph: Mapping[str, Any]) -> Dict[str, Dict[int, List[Dict[str, Any]]]]:
    out: Dict[str, Dict[int, List[Dict[str, Any]]]] = {}
    for raw in graph.get("nodes", []) if isinstance(graph, Mapping) else []:
        if not isinstance(raw, Mapping):
            continue
        kind = _norm(raw.get("row_kind"))
        idx = raw.get("row_index")
        if not kind or idx is None:
            continue
        try:
            expected_idx = int(idx)
        except Exception:
            continue
        out.setdefault(kind, {}).setdefault(expected_idx, []).append(dict(raw))
    return out


def _find_control_for_node(row: LiveRow, node: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    wanted = set(_node_semantic_keys(node))
    if not wanted:
        return None
    ranked: List[Tuple[int, Dict[str, Any]]] = []
    for control in row.controls:
        keys = set(_control_semantic_keys(control))
        overlap = wanted & keys
        if not overlap:
            continue
        score = 10 * len(overlap)
        field_key = _norm(node.get("field_key"))
        if field_key and field_key in keys:
            score += 30
        framework = _norm(control.get("framework_key") or control.get("form_control_name"))
        if framework and framework in wanted:
            score += 20
        ranked.append((score, control))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _score_expected_to_live(
    nodes: Sequence[Mapping[str, Any]],
    row: LiveRow,
    *,
    identity_node_ids: Optional[set[str]] = None,
) -> Dict[str, Any]:
    score = 0
    exact_anchors = 0
    mismatches = 0
    observed_nonblank = 0
    identity_observed_nonblank = 0
    structure_hits = 0
    comparisons: List[Dict[str, Any]] = []
    for node in nodes:
        control = _find_control_for_node(row, node)
        expected = node.get("expected_value")
        node_id = str(node.get("node_id") or "")
        identity_eligible = identity_node_ids is None or node_id in identity_node_ids
        if control is None:
            comparisons.append({"field": node.get("field_key"), "present": False, "identity_eligible": identity_eligible})
            if node.get("required"):
                score -= 4
            continue
        structure_hits += 1
        score += 5
        actual = _control_committed_value(control)
        entry = {
            "field": node.get("field_key"),
            "present": True,
            "actual_nonblank": _meaningful(actual),
            "identity_eligible": identity_eligible,
        }
        if _meaningful(actual):
            observed_nonblank += 1
            if identity_eligible:
                identity_observed_nonblank += 1
                if _meaningful(expected) and values_equivalent(expected, actual):
                    exact_anchors += 1
                    score += 120
                    entry["match"] = True
                elif _meaningful(expected):
                    mismatches += 1
                    score -= 150
                    entry["match"] = False
            else:
                # Non-discriminating values (for example Condition Type=Attributes
                # in every row) are useful state evidence but cannot establish row
                # identity by themselves.
                score += 1
                entry["match"] = None
        comparisons.append(entry)
    return {
        "score": score,
        "exact_anchors": exact_anchors,
        "mismatches": mismatches,
        "observed_nonblank": observed_nonblank,
        "identity_observed_nonblank": identity_observed_nonblank,
        "structure_hits": structure_hits,
        "comparisons": comparisons,
    }


def _identity_node_ids_for_kind(
    expected_rows: Mapping[int, Sequence[Mapping[str, Any]]],
    committed_node_ids: Optional[Iterable[str]],
) -> set[str]:
    """Return nodes that are safe to use as semantic row identity anchors.

    A field is discriminating when its expected value is unique across rows for
    the same field key. A node explicitly proven complete by the runtime is also
    safe, even when the expected value is shared by several rows: only that one
    row has been committed at that point in the transaction sequence.
    """
    committed = {str(x) for x in (committed_node_ids or [])}
    by_field: Dict[str, List[Tuple[str, str]]] = {}
    for nodes in expected_rows.values():
        for node in nodes:
            field = _norm(node.get("field_key"))
            value = _semantic_value(node.get("expected_value"))
            if field and value:
                by_field.setdefault(field, []).append((str(node.get("node_id") or ""), value))
    allowed = set(committed)
    for pairs in by_field.values():
        counts: Dict[str, int] = {}
        for _, value in pairs:
            counts[value] = counts.get(value, 0) + 1
        for node_id, value in pairs:
            if counts.get(value, 0) == 1:
                allowed.add(node_id)
    return allowed


def reconcile_repeatable_row_bindings(
    controls: Sequence[Mapping[str, Any]],
    graph: Mapping[str, Any],
    *,
    committed_node_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Reconcile expected JSON rows to the current physical Angular row order.

    Strong matches are established from already committed expected values.  Only
    remaining *blank* rows may use DOM order as a provisional binding.  This makes
    the mapping self-correcting after the first semantic anchor is committed.
    """
    live_rows = build_live_row_groups(controls)
    expected = _expected_rows(graph)
    result: Dict[str, Any] = {
        "schema_version": "hip.repeatable-row-binding.v2",
        "bindings": {},
        "physical_to_expected": {},
        "live_rows": [r.as_dict() for r in live_rows],
        "pass": True,
        "ambiguities": [],
    }

    for kind, expected_rows in expected.items():
        live = [r for r in live_rows if r.row_kind == kind]
        expected_indexes = sorted(expected_rows)
        if not expected_indexes:
            continue
        identity_node_ids = _identity_node_ids_for_kind(expected_rows, committed_node_ids)
        kind_bindings: Dict[int, Dict[str, Any]] = {}
        unassigned_live = {r.physical_index: r for r in live}
        unassigned_expected = list(expected_indexes)

        candidates: List[Dict[str, Any]] = []
        for eidx in expected_indexes:
            for row in live:
                proof = _score_expected_to_live(expected_rows[eidx], row, identity_node_ids=identity_node_ids)
                candidates.append({"expected_index": eidx, "physical_index": row.physical_index, **proof})

        # First bind rows with actual semantic evidence.  Sort by exact anchors,
        # then fewer mismatches, then score.  A conflicting nonblank row is never
        # accepted merely because it occupies the old ordinal position.
        candidates.sort(key=lambda c: (int(c["exact_anchors"]), -int(c["mismatches"]), int(c["score"])), reverse=True)
        for cand in candidates:
            eidx = int(cand["expected_index"])
            pidx = int(cand["physical_index"])
            if eidx not in unassigned_expected or pidx not in unassigned_live:
                continue
            if int(cand["exact_anchors"]) <= 0 or int(cand["mismatches"]) > 0:
                continue
            # Require uniqueness among remaining rows for the same expected row.
            competitors = [
                c for c in candidates
                if int(c["expected_index"]) == eidx
                and int(c["physical_index"]) in unassigned_live
                and int(c["exact_anchors"]) == int(cand["exact_anchors"])
                and int(c["mismatches"]) == 0
                and int(c["score"]) == int(cand["score"])
            ]
            if len(competitors) > 1:
                # More anchors can still resolve later; do not guess now.
                continue
            row = unassigned_live.pop(pidx)
            unassigned_expected.remove(eidx)
            kind_bindings[eidx] = {
                "expected_index": eidx,
                "physical_index": pidx,
                "row_semantic_identity": row.semantic_fingerprint,
                "row_structure_identity": row.structure_fingerprint,
                "strategy": "semantic_anchor_match",
                "confidence": "strong",
                "exact_anchor_count": int(cand["exact_anchors"]),
                "score": int(cand["score"]),
            }

        # Rows that remain nonblank but could not match the expected row are
        # conflicting evidence. Do not mask that conflict with positional mapping.
        conflicting_live: List[int] = []
        for pidx, row in unassigned_live.items():
            identity_observed = False
            any_compatible = False
            for eidx in unassigned_expected:
                proof = _score_expected_to_live(expected_rows[eidx], row, identity_node_ids=identity_node_ids)
                identity_observed = identity_observed or int(proof.get("identity_observed_nonblank") or 0) > 0
                if int(proof["mismatches"]) == 0 and int(proof["exact_anchors"]) > 0:
                    any_compatible = True
                    break
            if identity_observed and not any_compatible:
                conflicting_live.append(pidx)
        if conflicting_live:
            result["pass"] = False
            result["ambiguities"].append({
                "row_kind": kind,
                "reason": "nonblank live row conflicts with every remaining expected row",
                "physical_indexes": conflicting_live,
                "expected_indexes": list(unassigned_expected),
            })

        # A fully blank row has no semantic identity yet.  The only honest binding
        # is provisional order among the remaining blank rows.  As soon as its first
        # expected value is committed, the next capture upgrades it to a strong
        # semantic-anchor match, surviving any physical reorder.
        provisional_live = [r for p, r in sorted(unassigned_live.items()) if p not in conflicting_live]
        for eidx, row in zip(sorted(unassigned_expected), provisional_live):
            strategy = "provisional_blank_order" if not row.committed_anchors else "provisional_order_non_discriminating_values"
            kind_bindings[eidx] = {
                "expected_index": int(eidx),
                "physical_index": int(row.physical_index),
                "row_semantic_identity": row.semantic_fingerprint,
                "row_structure_identity": row.structure_fingerprint,
                "strategy": strategy,
                "confidence": "provisional",
                "exact_anchor_count": 0,
                "score": 0,
            }
            unassigned_live.pop(row.physical_index, None)
            if eidx in unassigned_expected:
                unassigned_expected.remove(eidx)

        if unassigned_expected or unassigned_live:
            # Extra/missing rows are surfaced even if the caller separately checks
            # the row count, because a structurally unmatched live row is dangerous.
            result["pass"] = False
            result["ambiguities"].append({
                "row_kind": kind,
                "reason": "repeatable row reconciliation incomplete",
                "expected_unbound": list(sorted(unassigned_expected)),
                "physical_unbound": list(sorted(unassigned_live)),
            })

        result["bindings"][kind] = {str(k): v for k, v in sorted(kind_bindings.items())}
        for eidx, binding in kind_bindings.items():
            result["physical_to_expected"][f"{kind}:{binding['physical_index']}"] = int(eidx)

    return result


def annotate_controls_with_repeatable_bindings(
    controls: Sequence[Mapping[str, Any]],
    graph: Mapping[str, Any],
    *,
    committed_node_ids: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    annotated = [dict(c) for c in controls if isinstance(c, Mapping)]
    reconciliation = reconcile_repeatable_row_bindings(annotated, graph, committed_node_ids=committed_node_ids)
    physical = reconciliation.get("physical_to_expected") if isinstance(reconciliation, Mapping) else {}
    bindings = reconciliation.get("bindings") if isinstance(reconciliation, Mapping) else {}

    for control in annotated:
        kind = _norm(control.get("row_kind"))
        idx = control.get("row_index")
        if not kind or idx is None:
            continue
        try:
            physical_idx = int(idx)
        except Exception:
            continue
        expected_idx = physical.get(f"{kind}:{physical_idx}") if isinstance(physical, Mapping) else None
        if expected_idx is None:
            continue
        binding = (bindings.get(kind) or {}).get(str(expected_idx), {}) if isinstance(bindings, Mapping) else {}
        control["physical_row_index"] = physical_idx
        control["expected_row_index"] = int(expected_idx)
        control["row_semantic_identity"] = str(binding.get("row_semantic_identity") or "")
        control["row_structure_identity"] = str(binding.get("row_structure_identity") or "")
        control["row_binding_strategy"] = str(binding.get("strategy") or "")
        control["row_binding_confidence"] = str(binding.get("confidence") or "")
    return annotated, reconciliation


def row_binding_for_node(reconciliation: Mapping[str, Any], node: Mapping[str, Any]) -> Dict[str, Any]:
    kind = _norm(node.get("row_kind"))
    idx = node.get("row_index")
    if not kind or idx is None:
        return {}
    try:
        key = str(int(idx))
    except Exception:
        return {}
    bindings = reconciliation.get("bindings") if isinstance(reconciliation, Mapping) else {}
    return dict((bindings.get(kind) or {}).get(key) or {}) if isinstance(bindings, Mapping) else {}
