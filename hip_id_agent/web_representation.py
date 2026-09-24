from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, Iterable, List, Sequence

from .security import mask_sensitive_data


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _bool_token(name: str, value: Any) -> str:
    return f"{name}:{'1' if bool(value) else '0'}"


def _hash_vector(tokens: Iterable[str], dimensions: int = 256) -> List[float]:
    """Build a deterministic signed-hash embedding without external model calls.

    This is intentionally value-free: it embeds semantic/control topology and state,
    not customer-entered text.  It is fast enough to run before every field action.
    """
    vector = [0.0] * max(32, int(dimensions))
    for token in tokens:
        if not token:
            continue
        digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
        index = int.from_bytes(digest[:4], "big") % len(vector)
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = 1.0 + (digest[5] / 255.0) * 0.25
        vector[index] += sign * weight
    norm = math.sqrt(sum(x * x for x in vector))
    return [round(x / norm, 8) for x in vector] if norm else vector


def _control_tokens(control: Dict[str, Any]) -> List[str]:
    section = _norm(control.get("section"))
    key = _norm(control.get("semantic_key") or control.get("label"))
    role = _norm(control.get("role") or control.get("type") or control.get("tag"))
    framework = _norm(
        control.get("framework_key")
        or control.get("form_control_name")
        or control.get("ng_reflect_name")
    )
    row_kind = _norm(control.get("row_kind"))
    row_index = control.get("row_index")
    component = _norm(control.get("component_tag"))
    selection = _norm(control.get("selection_mode"))
    tokens = [
        f"section:{section}",
        f"field:{key}",
        f"role:{role}",
        f"framework:{framework}",
        f"row:{row_kind}:{row_index if row_index is not None else ''}",
        f"component:{component}",
        f"selection:{selection}",
        _bool_token("visible", control.get("visible", True)),
        _bool_token("interactable", control.get("interactable", False)),
        _bool_token("disabled", control.get("disabled", False)),
        _bool_token("readonly", control.get("readonly", False)),
        _bool_token("checked", control.get("checked", False)),
        _bool_token("required", control.get("required", False)),
        _bool_token("invalid", str(control.get("aria_invalid") or "").lower() == "true"),
        f"selected_count:{int(control.get('selected_count') or 0)}",
        _bool_token("has_value", bool(control.get("value") or control.get("selected_values"))),
    ]
    return [token for token in tokens if not token.endswith(":")]


def build_web_representation(
    *,
    phase: str,
    url: str,
    controls: Sequence[Dict[str, Any]],
    surface_gate: Dict[str, Any] | None = None,
    dom_transition: Dict[str, Any] | None = None,
    console_signatures: Sequence[str] | None = None,
    network_signatures: Sequence[str] | None = None,
    dimensions: int = 256,
) -> Dict[str, Any]:
    """Create a stable web-state representation for planning, memory and drift.

    Raw field values, IDs, credentials and upload content are deliberately excluded.
    """
    tokens: List[str] = [f"phase:{_norm(phase)}"]
    path = re.sub(r"/\d+(?=/|$)", "/{id}", str(url or "").split("?", 1)[0])
    tokens.append(f"path:{_norm(path)}")
    visible_count = interactable_count = invalid_count = 0
    semantic_controls: List[Dict[str, Any]] = []
    for raw in controls or []:
        if not isinstance(raw, dict):
            continue
        control_tokens = _control_tokens(raw)
        tokens.extend(control_tokens)
        visible_count += int(bool(raw.get("visible", True)))
        interactable_count += int(bool(raw.get("interactable", False)))
        invalid_count += int(str(raw.get("aria_invalid") or "").lower() == "true")
        semantic_controls.append({
            "semantic_key": raw.get("semantic_key"),
            "section": raw.get("section"),
            "row_kind": raw.get("row_kind"),
            "row_index": raw.get("row_index"),
            "role": raw.get("role"),
            "component_tag": raw.get("component_tag"),
            "framework_key": raw.get("framework_key") or raw.get("form_control_name") or raw.get("ng_reflect_name"),
            "selection_mode": raw.get("selection_mode"),
            "visible": bool(raw.get("visible", True)),
            "interactable": bool(raw.get("interactable", False)),
            "disabled": bool(raw.get("disabled", False)),
            "readonly": bool(raw.get("readonly", False)),
            "has_value": bool(raw.get("value") or raw.get("selected_values")),
            "selected_count": int(raw.get("selected_count") or 0),
        })

    surface_gate = surface_gate if isinstance(surface_gate, dict) else {}
    tokens.extend([
        _bool_token("surface_pass", surface_gate.get("pass", not surface_gate.get("fatal"))),
        _bool_token("surface_fatal", bool(surface_gate.get("fatal"))),
        f"surface_status:{_norm(surface_gate.get('status'))}",
    ])
    transition = dom_transition if isinstance(dom_transition, dict) else {}
    tokens.extend([
        f"event_count_bucket:{min(9, int(transition.get('event_count') or 0))}",
        f"mutation_count_bucket:{min(9, int(transition.get('mutation_count') or 0))}",
        _bool_token("commit_events", transition.get("commit_events_seen")),
    ])
    for signature in console_signatures or []:
        tokens.append(f"console:{_norm(signature)[:80]}")
    for signature in network_signatures or []:
        tokens.append(f"network:{_norm(signature)[:80]}")

    structural_tokens = sorted(set(token for token in tokens if not token.startswith(("has_value:", "selected_count:"))))
    state_tokens = sorted(tokens)
    structural_fingerprint = hashlib.sha256(
        json.dumps(structural_tokens, sort_keys=True).encode("utf-8")
    ).hexdigest()
    state_fingerprint = hashlib.sha256(
        json.dumps(state_tokens, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return mask_sensitive_data({
        "schema_version": "hip.web-representation.v1",
        "phase": phase,
        "url_path": path,
        "control_count": len(semantic_controls),
        "visible_control_count": visible_count,
        "interactable_control_count": interactable_count,
        "invalid_control_count": invalid_count,
        "surface_gate": surface_gate,
        "semantic_controls": semantic_controls,
        "structural_tokens": structural_tokens,
        "state_tokens": state_tokens,
        "structural_fingerprint": structural_fingerprint,
        "state_fingerprint": state_fingerprint,
        "embedding": _hash_vector(state_tokens, dimensions=dimensions),
        "values_stored": False,
    })


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    ln = math.sqrt(sum(float(a) * float(a) for a in left))
    rn = math.sqrt(sum(float(b) * float(b) for b in right))
    return round(dot / (ln * rn), 6) if ln and rn else 0.0


def representation_drift(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    structural_equal = bool(
        current.get("structural_fingerprint")
        and current.get("structural_fingerprint") == previous.get("structural_fingerprint")
    )
    similarity = cosine_similarity(current.get("embedding") or [], previous.get("embedding") or [])
    return {
        "schema_version": "hip.web-representation-drift.v1",
        "structural_equal": structural_equal,
        "embedding_similarity": similarity,
        "drift_detected": not structural_equal and similarity < 0.94,
        "current_control_count": current.get("control_count"),
        "previous_control_count": previous.get("control_count"),
    }
