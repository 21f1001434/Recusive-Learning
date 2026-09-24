from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlparse

from .models import utc_now
from .safe_io import safe_mkdir, safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


_DYNAMIC_OR_LOCATION_KEYS = {
    "selector", "css", "css_selector", "xpath", "locator", "selector_hint",
    "dispatch_selector", "bounding_box", "bbox", "box", "coordinates",
    "coordinate", "point", "x", "y", "left", "top", "right", "bottom",
    "screen_x", "screen_y", "client_x", "client_y", "viewport_x", "viewport_y",
    "inspectiontoken", "inspection_token", "id", "dom_id",
}
_VALUE_KEYS = {
    "value", "expected_value", "observed_value", "actual_value", "user_input",
    "password", "secret", "token", "authorization", "cookie", "cookies",
    "request_body", "response_body", "payload", "content", "file_path", "filepath",
}
_VOLATILE_TEXT_RE = re.compile(
    r"(?:dds-form-field-|mat-input-|react-select-|ng-tns-c|cdk-overlay-|hipinspect-|"
    r"data-hip-[a-z-]+|\b\d{5,}\b)", re.I,
)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _text(value: Any, limit: int = 300) -> str:
    return mask_sensitive_string(re.sub(r"\s+", " ", str(value or "")).strip())[:limit]


def _path_only(url: Any) -> str:
    try:
        return urlparse(str(url or "")).path or "/"
    except Exception:
        return str(url or "").split("?", 1)[0]


def _hash(payload: Any, length: int = 28) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _safe_semantic_text(value: Any, limit: int = 300) -> str:
    text = _text(value, limit)
    if _VOLATILE_TEXT_RE.search(text):
        # Keep human semantics while stripping generated implementation ids.
        text = _VOLATILE_TEXT_RE.sub("<volatile>", text)
    return text


def scrub_world_model_payload(value: Any) -> Any:
    """Remove selectors, coordinates, entered values and other brittle/runtime data.

    The world model is intentionally semantic. It may remember a control label/role,
    a section, an affordance, a dependency, an option *name* that is portal metadata,
    or a state transition. It must never become a hidden replay script.
    """
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            k = _norm(key)
            if k in _DYNAMIC_OR_LOCATION_KEYS or k in _VALUE_KEYS:
                continue
            if any(token in k for token in ("selector", "xpath", "coordinate", "bounding_box")):
                continue
            out[str(key)] = scrub_world_model_payload(item)
        return out
    if isinstance(value, (list, tuple, set)):
        return [scrub_world_model_payload(v) for v in value]
    if isinstance(value, str):
        return _safe_semantic_text(value, 600)
    return value


def semantic_control_descriptor(candidate: Mapping[str, Any], *, label: str = "", section: str = "") -> Dict[str, Any]:
    """Value-free, generation-independent identity for a live control."""
    return scrub_world_model_payload({
        "label": label or candidate.get("label") or candidate.get("aria_label") or candidate.get("placeholder"),
        "section": section or candidate.get("section"),
        "role": candidate.get("role") or candidate.get("tag"),
        "control_type": candidate.get("type") or candidate.get("tag"),
        "name": candidate.get("name"),
        "form_control_name": candidate.get("formControlName") or candidate.get("form_control_name") or candidate.get("framework_key"),
        "form_array_name": candidate.get("formArrayName") or candidate.get("form_array_name"),
        "row_kind": candidate.get("row_kind"),
        "selection_mode": candidate.get("selection_mode"),
        "required": bool(candidate.get("required")),
        "in_active_surface": bool(candidate.get("inActiveSurface", candidate.get("in_active_surface", True))),
        "affordances": candidate.get("affordances") or [],
        "registered_event_types": [
            x.get("type") for x in (candidate.get("registeredEventListeners") or candidate.get("registered_event_listeners") or [])
            if isinstance(x, Mapping) and x.get("type")
        ],
    })


def semantic_state_descriptor(state: Mapping[str, Any], *, phase: str) -> Dict[str, Any]:
    """Convert a live semantic/website snapshot into a value-free state signature."""
    controls: List[Dict[str, Any]] = []
    for raw in state.get("controls", []) if isinstance(state, Mapping) else []:
        if not isinstance(raw, Mapping):
            continue
        visible = bool(raw.get("visible", True))
        active = bool(raw.get("inActiveSurface", raw.get("in_active_surface", True)))
        if not visible or not active:
            continue
        desc = semantic_control_descriptor(raw)
        if desc.get("label") or desc.get("form_control_name"):
            controls.append(desc)
    controls = controls[:500]
    active = state.get("active_surface") if isinstance(state.get("active_surface"), Mapping) else state.get("activeSurface") if isinstance(state.get("activeSurface"), Mapping) else {}
    tabs = []
    for tab in state.get("tabs", []) if isinstance(state, Mapping) else []:
        if isinstance(tab, Mapping) and bool(tab.get("selected")):
            tabs.append(_safe_semantic_text(tab.get("text"), 180))
    surface = {
        "label": _safe_semantic_text(active.get("label"), 260),
        "role": _norm(active.get("role")),
        "tag": _norm(active.get("tag")),
        "aria_modal": str(active.get("ariaModal") or active.get("aria_modal") or ""),
    }
    material = {
        "phase": _norm(phase),
        "url_path": _path_only(state.get("url") or state.get("page_url") or state.get("url_path")),
        "surface": surface,
        "selected_tabs": sorted(x for x in tabs if x),
        "controls": sorted(
            [
                {
                    "label": _norm(c.get("label")), "section": _norm(c.get("section")),
                    "role": _norm(c.get("role")), "control_type": _norm(c.get("control_type")),
                    "form_control_name": _norm(c.get("form_control_name")),
                    "row_kind": _norm(c.get("row_kind")), "required": bool(c.get("required")),
                }
                for c in controls
            ],
            key=lambda x: json.dumps(x, sort_keys=True),
        ),
    }
    return {
        "phase": _norm(phase),
        "url_path": material["url_path"],
        "active_surface": surface,
        "selected_tabs": material["selected_tabs"],
        "control_count": len(controls),
        "control_semantics": material["controls"],
        "semantic_state_id": _hash(material),
        "values_stored": False,
        "selectors_stored": False,
        "coordinates_stored": False,
    }


class WebsiteWorldModelMemory:
    """Persistent semantic model of the HIP website learned from verified effects.

    It stores semantic controls, states and transitions across runs. Runtime selectors,
    coordinates and customer values are deliberately excluded. Validated evidence can
    boost later target ranking; failures lower confidence and become negative evidence.
    """

    SCHEMA_VERSION = "hip.website-world-model.v2"

    def __init__(self, root: str | Path, *, config: Any = None) -> None:
        self.root = Path(root)
        safe_mkdir(self.root, parents=True, exist_ok=True)
        self.controls_path = self.root / "controls.json"
        self.states_path = self.root / "states.json"
        self.transitions_path = self.root / "transitions.json"
        self.choice_branches_path = self.root / "portal_choice_branches.json"
        self.manifest_path = self.root / "manifest.json"
        self.observations_path = self.root / "observations.jsonl"
        self.enabled = bool(getattr(config, "world_model_enabled", True)) if config is not None else True
        self.min_validated_confirmations = max(1, int(getattr(config, "world_model_min_validated_confirmations", 1) or 1)) if config is not None else 1
        self.min_validated_confidence = float(getattr(config, "world_model_min_validated_confidence", 0.72) or 0.72) if config is not None else 0.72
        self.failure_decay = float(getattr(config, "world_model_failure_decay", 0.18) or 0.18) if config is not None else 0.18
        self.use_for_candidate_scoring = bool(getattr(config, "world_model_use_for_candidate_scoring", True)) if config is not None else True
        self.use_for_planning = bool(getattr(config, "world_model_use_for_planning", True)) if config is not None else True
        self.recency_decay_enabled = bool(getattr(config, "world_model_recency_decay_enabled", True)) if config is not None else True
        self.confidence_half_life_days = max(1.0, float(getattr(config, "world_model_confidence_half_life_days", 45.0) or 45.0)) if config is not None else 45.0
        self.stale_after_days = max(1.0, float(getattr(config, "world_model_stale_after_days", 90.0) or 90.0)) if config is not None else 90.0
        self.drift_failure_threshold = max(1, int(getattr(config, "world_model_drift_failure_threshold", 2) or 2)) if config is not None else 2
        self.drift_penalty = max(0.0, min(0.9, float(getattr(config, "world_model_drift_penalty", 0.22) or 0.22))) if config is not None else 0.22
        self.auto_demote_on_drift = bool(getattr(config, "world_model_auto_demote_on_drift", True)) if config is not None else True
        for path, kind in ((self.controls_path, "controls"), (self.states_path, "states"), (self.transitions_path, "transitions"), (self.choice_branches_path, "choice_branches")):
            if not path.exists():
                safe_write_json(path, {"schema_version": self.SCHEMA_VERSION, kind: {}})
        self._write_manifest()

    def _load(self, path: Path, key: str) -> Dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                data.setdefault(key, {})
                return data
        except Exception:
            pass
        return {"schema_version": self.SCHEMA_VERSION, key: {}}

    def _save(self, path: Path, key: str, data: Dict[str, Any]) -> None:
        data["schema_version"] = self.SCHEMA_VERSION
        data["updated_at"] = utc_now()
        data[key] = scrub_world_model_payload(data.get(key) or {})
        safe_write_json(path, data, mask=False)
        self._write_manifest()

    def _write_manifest(self) -> None:
        controls = self._load(self.controls_path, "controls") if self.controls_path.exists() else {"controls": {}}
        states = self._load(self.states_path, "states") if self.states_path.exists() else {"states": {}}
        transitions = self._load(self.transitions_path, "transitions") if self.transitions_path.exists() else {"transitions": {}}
        choices = self._load(self.choice_branches_path, "choice_branches") if self.choice_branches_path.exists() else {"choice_branches": {}}
        control_rows = [self._decorate_health(x) for x in controls.get("controls", {}).values() if isinstance(x, Mapping)]
        transition_rows = [self._decorate_health(x) for x in transitions.get("transitions", {}).values() if isinstance(x, Mapping)]
        choice_rows = [self._decorate_health(x) for x in choices.get("choice_branches", {}).values() if isinstance(x, Mapping)]
        health = self._learning_health(control_rows + transition_rows + choice_rows)
        safe_write_json(self.manifest_path, {
            "schema_version": self.SCHEMA_VERSION,
            "enabled": self.enabled,
            "validated_controls": sum(1 for x in control_rows if x.get("effective_trust") == "validated"),
            "candidate_controls": sum(1 for x in control_rows if x.get("effective_trust") == "candidate"),
            "negative_controls": sum(1 for x in control_rows if x.get("effective_trust") == "negative"),
            "semantic_states": len(states.get("states", {})),
            "validated_transitions": sum(1 for x in transition_rows if x.get("effective_trust") == "validated"),
            "candidate_transitions": sum(1 for x in transition_rows if x.get("effective_trust") == "candidate"),
            "negative_transitions": sum(1 for x in transition_rows if x.get("effective_trust") == "negative"),
            "validated_portal_choice_branches": sum(1 for x in choice_rows if x.get("effective_trust") == "validated"),
            "learning_health": health,
            "safety": {
                "customer_values_stored": False,
                "selectors_stored": False,
                "coordinates_stored": False,
                "generated_dom_ids_stored": False,
            },
            "purpose": "verified semantic website states/transitions reused as freshness-aware bounded priors; live page always reauthorizes actions",
            "updated_at": utc_now(),
        }, mask=False)

    @staticmethod
    def _confidence(successes: int, failures: int, evidence_confidence: float = 0.0) -> float:
        # One strong verified observation can be useful, repeated success raises trust,
        # and failures reliably reduce it. The prior prevents a single event becoming 1.0.
        bayes = (successes + 1.0) / (successes + failures + 2.0)
        return round(min(0.99, max(0.0, 0.72 * bayes + 0.28 * max(0.0, min(1.0, evidence_confidence)))), 4)

    @staticmethod
    def _parse_time(value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _age_days(self, last_seen_at: Any) -> float:
        dt = self._parse_time(last_seen_at)
        if dt is None:
            return 0.0
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)

    def _effective_confidence(self, confidence: float, last_seen_at: Any) -> float:
        raw = max(0.0, min(1.0, float(confidence or 0.0)))
        if not self.recency_decay_enabled:
            return round(raw, 4)
        age = self._age_days(last_seen_at)
        decay = math.pow(0.5, age / self.confidence_half_life_days)
        return round(max(0.0, min(0.99, raw * decay)), 4)

    def _trust(self, successes: int, failures: int, confidence: float, consecutive_failures: int = 0) -> str:
        if failures > successes and confidence < 0.45:
            return "negative"
        if self.auto_demote_on_drift and consecutive_failures >= self.drift_failure_threshold:
            return "candidate"
        if successes >= self.min_validated_confirmations and confidence >= self.min_validated_confidence:
            return "validated"
        return "candidate"

    def _decorate_health(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        out = dict(row or {})
        raw_conf = float(out.get("confidence") or 0.0)
        age_days = self._age_days(out.get("last_seen_at"))
        effective = self._effective_confidence(raw_conf, out.get("last_seen_at"))
        consecutive = int(out.get("consecutive_failure_count") or 0)
        stale = bool(age_days >= self.stale_after_days)
        drift_suspect = bool(out.get("drift_status") == "drift_suspect" or consecutive >= self.drift_failure_threshold)
        effective_trust = self._trust(int(out.get("success_count") or 0), int(out.get("failure_count") or 0), effective, consecutive)
        if stale and effective_trust == "validated":
            effective_trust = "candidate"
        out.update({
            "raw_confidence": round(raw_conf, 4),
            "effective_confidence": effective,
            "age_days": round(age_days, 2),
            "stale": stale,
            "drift_suspect": drift_suspect,
            "effective_trust": effective_trust,
        })
        return out

    def _control_key(self, phase: str, action: str, descriptor: Mapping[str, Any]) -> str:
        material = {
            "phase": _norm(phase), "action": _norm(action),
            "label": _norm(descriptor.get("label")), "section": _norm(descriptor.get("section")),
            "role": _norm(descriptor.get("role")), "type": _norm(descriptor.get("control_type")),
            "form_control_name": _norm(descriptor.get("form_control_name")),
            "row_kind": _norm(descriptor.get("row_kind")),
        }
        return _hash(material)

    def _append_observation(self, row: Mapping[str, Any]) -> None:
        try:
            safe_mkdir(self.observations_path.parent, parents=True, exist_ok=True)
            with self.observations_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(scrub_world_model_payload(mask_sensitive_data(dict(row))), ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def control_hint(self, *, phase: str, action: str, label: str, section: str, candidate: Mapping[str, Any]) -> Dict[str, Any]:
        if not self.enabled or not self.use_for_candidate_scoring:
            return {"matched": False, "score": 0.0, "trust": "disabled"}
        desc = semantic_control_descriptor(candidate, label=label, section=section)
        key = self._control_key(phase, action, desc)
        data = self._load(self.controls_path, "controls")
        row = (data.get("controls") or {}).get(key)
        if not isinstance(row, Mapping):
            return {"matched": False, "score": 0.0, "trust": "unknown"}
        health = self._decorate_health(row)
        trust = str(health.get("effective_trust") or row.get("trust") or "candidate")
        confidence = float(health.get("effective_confidence") or 0.0)
        score = confidence if trust == "validated" else 0.35 * confidence if trust == "candidate" else -max(0.35, confidence)
        return {
            "matched": True,
            "score": round(score, 4),
            "trust": trust,
            "stored_trust": row.get("trust") or "candidate",
            "confidence": confidence,
            "raw_confidence": health.get("raw_confidence"),
            "effective_confidence": confidence,
            "age_days": health.get("age_days"),
            "stale": health.get("stale"),
            "drift_suspect": health.get("drift_suspect"),
            "success_count": int(row.get("success_count") or 0),
            "failure_count": int(row.get("failure_count") or 0),
            "consecutive_failure_count": int(row.get("consecutive_failure_count") or 0),
            "semantic_descriptor": row.get("semantic_descriptor") or {},
        }

    def _record_control(self, *, phase: str, action: str, descriptor: Mapping[str, Any], success: bool, effect_confidence: float, effect_type: str) -> Dict[str, Any]:
        data = self._load(self.controls_path, "controls")
        controls = data.setdefault("controls", {})
        key = self._control_key(phase, action, descriptor)
        current = dict(controls.get(key) or {})
        successes = int(current.get("success_count") or 0) + (1 if success else 0)
        failures = int(current.get("failure_count") or 0) + (0 if success else 1)
        consecutive_failures = 0 if success else int(current.get("consecutive_failure_count") or 0) + 1
        conf = self._confidence(successes, failures, effect_confidence)
        if self.auto_demote_on_drift and consecutive_failures >= self.drift_failure_threshold:
            conf = max(0.0, conf - self.drift_penalty)
        row = {
            "semantic_control_key": key,
            "phase": _norm(phase),
            "action_family": _norm(action),
            "semantic_descriptor": scrub_world_model_payload(descriptor),
            "success_count": successes,
            "failure_count": failures,
            "consecutive_failure_count": consecutive_failures,
            "confidence": round(conf, 4),
            "trust": self._trust(successes, failures, conf, consecutive_failures),
            "drift_status": "drift_suspect" if consecutive_failures >= self.drift_failure_threshold else "stable",
            "last_effect_type": _safe_semantic_text(effect_type, 180),
            "first_seen_at": current.get("first_seen_at") or utc_now(),
            "last_seen_at": utc_now(),
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }
        controls[key] = row
        self._save(self.controls_path, "controls", data)
        return row

    def _record_state(self, descriptor: Mapping[str, Any], *, success: bool) -> Dict[str, Any]:
        data = self._load(self.states_path, "states")
        states = data.setdefault("states", {})
        key = str(descriptor.get("semantic_state_id") or _hash(descriptor))
        current = dict(states.get(key) or {})
        current.update(scrub_world_model_payload(descriptor))
        current["observations"] = int(current.get("observations") or 0) + 1
        current["successful_arrivals"] = int(current.get("successful_arrivals") or 0) + (1 if success else 0)
        current["last_seen_at"] = utc_now()
        current["first_seen_at"] = current.get("first_seen_at") or utc_now()
        states[key] = current
        self._save(self.states_path, "states", data)
        return current

    @staticmethod
    def _control_diff(before_desc: Mapping[str, Any], after_desc: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        def key(row: Mapping[str, Any]) -> str:
            return json.dumps({
                "label": _norm(row.get("label")), "section": _norm(row.get("section")),
                "role": _norm(row.get("role")), "control_type": _norm(row.get("control_type")),
                "form_control_name": _norm(row.get("form_control_name")), "row_kind": _norm(row.get("row_kind")),
            }, sort_keys=True)
        before_rows = {key(x): dict(x) for x in (before_desc.get("control_semantics") or []) if isinstance(x, Mapping)}
        after_rows = {key(x): dict(x) for x in (after_desc.get("control_semantics") or []) if isinstance(x, Mapping)}
        return {
            "revealed_controls": [after_rows[k] for k in sorted(set(after_rows) - set(before_rows))][:80],
            "hidden_controls": [before_rows[k] for k in sorted(set(before_rows) - set(after_rows))][:80],
        }

    def record_verified_portal_choice(
        self, *, phase: str, action: str, control: Mapping[str, Any], choice: str,
        available_options: Iterable[str], effect_type: str = "", effect_confidence: float = 0.0,
    ) -> Dict[str, Any]:
        """Learn a branch value only when it is proven to be portal-owned metadata.

        Arbitrary typed/customer values never enter this method. `choice` must exactly
        match one of the live mounted dropdown/listbox options captured before action.
        """
        if not self.enabled:
            return {"status": "disabled"}
        options = [_safe_semantic_text(x, 300) for x in available_options if _safe_semantic_text(x, 300)]
        choice_s = _safe_semantic_text(choice, 300)
        match = next((x for x in options if _norm(x) == _norm(choice_s)), "")
        if not choice_s or not match:
            return {"status": "not_portal_metadata"}
        desc = scrub_world_model_payload(dict(control))
        material = {
            "phase": _norm(phase), "action": _norm(action),
            "label": _norm(desc.get("label")), "section": _norm(desc.get("section")),
            "role": _norm(desc.get("role")), "portal_choice": _norm(match),
        }
        key = _hash(material)
        data = self._load(self.choice_branches_path, "choice_branches")
        bucket = data.setdefault("choice_branches", {})
        current = dict(bucket.get(key) or {})
        successes = int(current.get("success_count") or 0) + 1
        failures = int(current.get("failure_count") or 0)
        conf = self._confidence(successes, failures, effect_confidence)
        row = {
            "branch_id": key, "phase": _norm(phase), "action_family": _norm(action),
            "control": desc, "portal_choice": match,
            "effect_type": _safe_semantic_text(effect_type, 180),
            "effect_confidence": float(effect_confidence or 0.0),
            "success_count": successes, "failure_count": failures,
            "consecutive_failure_count": 0,
            "confidence": conf, "trust": self._trust(successes, failures, conf, 0),
            "drift_status": "stable",
            "option_catalog_size": len(options),
            "first_seen_at": current.get("first_seen_at") or utc_now(), "last_seen_at": utc_now(),
            "customer_values_stored": False, "portal_metadata_only": True,
            "selectors_stored": False, "coordinates_stored": False,
        }
        bucket[key] = row
        self._save(self.choice_branches_path, "choice_branches", data)
        self._append_observation({"kind": "verified_portal_choice", **row})
        return {"status": "promoted", "choice_branch": row}

    def portal_choice_hints(self, *, phase: str, label: str = "", section: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        data = self._load(self.choice_branches_path, "choice_branches")
        out: List[Dict[str, Any]] = []
        for row in (data.get("choice_branches") or {}).values():
            if not isinstance(row, Mapping) or _norm(row.get("phase")) != _norm(phase):
                continue
            ctrl = row.get("control") if isinstance(row.get("control"), Mapping) else {}
            if label and _norm(ctrl.get("label")) != _norm(label):
                continue
            if section and _norm(ctrl.get("section")) != _norm(section):
                continue
            out.append(self._decorate_health(row))
        out.sort(key=lambda x: (x.get("effective_trust") == "validated", float(x.get("effective_confidence") or 0), int(x.get("success_count") or 0)), reverse=True)
        return scrub_world_model_payload(out[:max(1, int(limit))])

    def record_verified_transition(
        self, *, phase: str, action: str, label: str, section: str,
        candidate: Mapping[str, Any], before: Mapping[str, Any], after: Mapping[str, Any], effect: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not self.enabled or not bool(effect.get("pass")):
            return {"status": "not_promoted"}
        control = semantic_control_descriptor(candidate, label=label, section=section)
        before_desc = semantic_state_descriptor(before, phase=phase)
        after_desc = semantic_state_descriptor(after, phase=phase)
        effect_confidence = float(effect.get("confidence") or 0.0)
        control_row = self._record_control(
            phase=phase, action=action, descriptor=control, success=True,
            effect_confidence=effect_confidence, effect_type=str(effect.get("effect_type") or ""),
        )
        self._record_state(before_desc, success=True)
        self._record_state(after_desc, success=True)
        data = self._load(self.transitions_path, "transitions")
        transitions = data.setdefault("transitions", {})
        material = {
            "phase": _norm(phase), "from": before_desc["semantic_state_id"],
            "action": _norm(action), "control": control_row["semantic_control_key"],
            "to": after_desc["semantic_state_id"],
        }
        key = _hash(material)
        current = dict(transitions.get(key) or {})
        successes = int(current.get("success_count") or 0) + 1
        failures = int(current.get("failure_count") or 0)
        conf = self._confidence(successes, failures, effect_confidence)
        dependency_diff = self._control_diff(before_desc, after_desc)
        row = {
            "transition_id": key,
            "phase": _norm(phase),
            "from_state": before_desc["semantic_state_id"],
            "to_state": after_desc["semantic_state_id"],
            "action_family": _norm(action),
            "semantic_control_key": control_row["semantic_control_key"],
            "control": control,
            "effect_type": _safe_semantic_text(effect.get("effect_type"), 180),
            "effect_confidence": effect_confidence,
            "revealed_controls": dependency_diff.get("revealed_controls") or [],
            "hidden_controls": dependency_diff.get("hidden_controls") or [],
            "success_count": successes,
            "failure_count": failures,
            "consecutive_failure_count": 0,
            "confidence": conf,
            "trust": self._trust(successes, failures, conf, 0),
            "drift_status": "stable",
            "first_seen_at": current.get("first_seen_at") or utc_now(),
            "last_seen_at": utc_now(),
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }
        transitions[key] = row
        self._save(self.transitions_path, "transitions", data)
        self._append_observation({"kind": "verified_transition", **row})
        return {"status": "promoted", "control": control_row, "transition": row}

    def record_failure(
        self, *, phase: str, action: str, label: str, section: str,
        candidate: Mapping[str, Any], before: Mapping[str, Any], after: Mapping[str, Any], effect: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        control = semantic_control_descriptor(candidate, label=label, section=section)
        before_desc = semantic_state_descriptor(before, phase=phase)
        after_desc = semantic_state_descriptor(after, phase=phase)
        effect_confidence = float(effect.get("confidence") or 0.0)
        control_row = self._record_control(
            phase=phase, action=action, descriptor=control, success=False,
            effect_confidence=effect_confidence, effect_type=str(effect.get("effect_type") or "no_proven_effect"),
        )
        data = self._load(self.transitions_path, "transitions")
        transitions = data.setdefault("transitions", {})
        # Failure edges use the attempted semantic action even when state did not move.
        material = {
            "phase": _norm(phase), "from": before_desc["semantic_state_id"],
            "action": _norm(action), "control": control_row["semantic_control_key"],
            "to": after_desc["semantic_state_id"],
        }
        key = _hash(material)
        current = dict(transitions.get(key) or {})
        successes = int(current.get("success_count") or 0)
        failures = int(current.get("failure_count") or 0) + 1
        consecutive_failures = int(current.get("consecutive_failure_count") or 0) + 1
        conf = max(0.0, self._confidence(successes, failures, effect_confidence) - self.failure_decay)
        if self.auto_demote_on_drift and consecutive_failures >= self.drift_failure_threshold:
            conf = max(0.0, conf - self.drift_penalty)
        row = {
            "transition_id": key, "phase": _norm(phase),
            "from_state": before_desc["semantic_state_id"], "to_state": after_desc["semantic_state_id"],
            "action_family": _norm(action), "semantic_control_key": control_row["semantic_control_key"],
            "control": control, "effect_type": _safe_semantic_text(effect.get("effect_type"), 180),
            "effect_confidence": effect_confidence, "success_count": successes, "failure_count": failures,
            "consecutive_failure_count": consecutive_failures,
            "confidence": round(conf, 4), "trust": self._trust(successes, failures, conf, consecutive_failures),
            "drift_status": "drift_suspect" if consecutive_failures >= self.drift_failure_threshold else "stable",
            "first_seen_at": current.get("first_seen_at") or utc_now(), "last_seen_at": utc_now(),
            "values_stored": False, "selectors_stored": False, "coordinates_stored": False,
        }
        transitions[key] = row
        self._save(self.transitions_path, "transitions", data)
        self._append_observation({"kind": "failed_transition", **row})
        return {"status": "negative_evidence_recorded", "control": control_row, "transition": row}

    def planner_hints(self, *, phase: str, action: str = "", label: str = "", section: str = "", limit: int = 6) -> List[Dict[str, Any]]:
        """Return freshness-aware semantic priors; never direct selectors/actions."""
        if not self.enabled or not self.use_for_planning:
            return []
        data = self._load(self.transitions_path, "transitions")
        out: List[Dict[str, Any]] = []
        phase_n, action_n, label_n, section_n = _norm(phase), _norm(action), _norm(label), _norm(section)
        for raw in (data.get("transitions") or {}).values():
            if not isinstance(raw, Mapping) or _norm(raw.get("phase")) != phase_n:
                continue
            row = self._decorate_health(raw)
            if row.get("effective_trust") not in {"validated", "candidate"}:
                continue
            if action_n and _norm(row.get("action_family")) != action_n:
                continue
            ctrl = row.get("control") if isinstance(row.get("control"), Mapping) else {}
            similarity = 0.0
            if label_n and _norm(ctrl.get("label")) == label_n:
                similarity += 0.7
            elif label_n and label_n in _norm(ctrl.get("label")):
                similarity += 0.45
            if section_n and _norm(ctrl.get("section")) == section_n:
                similarity += 0.2
            similarity += 0.1 * float(row.get("effective_confidence") or 0.0)
            out.append({
                "trust": row.get("effective_trust"),
                "stored_trust": row.get("trust"),
                "confidence": row.get("effective_confidence"),
                "raw_confidence": row.get("raw_confidence"),
                "age_days": row.get("age_days"),
                "stale": row.get("stale"),
                "drift_suspect": row.get("drift_suspect"),
                "action_family": row.get("action_family"),
                "control": ctrl,
                "effect_type": row.get("effect_type"),
                "revealed_controls": row.get("revealed_controls") or [],
                "hidden_controls": row.get("hidden_controls") or [],
                "success_count": row.get("success_count"), "failure_count": row.get("failure_count"),
                "match_score": round(similarity, 4),
            })
        out.sort(key=lambda x: (float(x.get("match_score") or 0), float(x.get("confidence") or 0), int(x.get("success_count") or 0)), reverse=True)
        return scrub_world_model_payload(out[: max(1, int(limit))])

    def recommend_actions(self, *, phase: str, current_state: Optional[Mapping[str, Any]] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Suggest previously successful semantic transitions from a similar phase/state.

        Recommendations are advisory and value-free. The caller must rediscover and
        prove a current live target before any action can execute.
        """
        if not self.enabled or not self.use_for_planning:
            return []
        state_id = semantic_state_descriptor(current_state or {}, phase=phase).get("semantic_state_id") if current_state else ""
        data = self._load(self.transitions_path, "transitions")
        rows: List[Dict[str, Any]] = []
        for raw in (data.get("transitions") or {}).values():
            if not isinstance(raw, Mapping) or _norm(raw.get("phase")) != _norm(phase):
                continue
            row = self._decorate_health(raw)
            if row.get("effective_trust") != "validated":
                continue
            exact_state = bool(state_id and row.get("from_state") == state_id)
            rows.append({
                "exact_state_match": exact_state,
                "action_family": row.get("action_family"),
                "control": row.get("control") or {},
                "effect_type": row.get("effect_type"),
                "confidence": row.get("effective_confidence"),
                "raw_confidence": row.get("raw_confidence"),
                "age_days": row.get("age_days"),
                "stale": row.get("stale"),
                "drift_suspect": row.get("drift_suspect"),
                "success_count": row.get("success_count"),
                "requires_live_reproof": True,
            })
        rows.sort(key=lambda x: (bool(x.get("exact_state_match")), float(x.get("confidence") or 0), int(x.get("success_count") or 0)), reverse=True)
        return scrub_world_model_payload(rows[: max(1, int(limit))])

    def _learning_health(self, rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
        decorated = [dict(x) for x in rows if isinstance(x, Mapping)]
        total = len(decorated)
        if not total:
            return {
                "learning_score": 0,
                "effective_confidence_average": 0.0,
                "fresh_knowledge_count": 0,
                "stale_knowledge_count": 0,
                "drift_suspect_count": 0,
                "negative_knowledge_count": 0,
                "confidence_half_life_days": self.confidence_half_life_days,
                "stale_after_days": self.stale_after_days,
                "drift_failure_threshold": self.drift_failure_threshold,
                "recency_decay_enabled": self.recency_decay_enabled,
                "memory_rule": "MEMORY PROPOSES; LIVE PAGE AUTHORIZES",
            }
        avg = sum(float(x.get("effective_confidence") or 0.0) for x in decorated) / total
        stale = sum(1 for x in decorated if x.get("stale"))
        drift = sum(1 for x in decorated if x.get("drift_suspect"))
        negative = sum(1 for x in decorated if x.get("effective_trust") == "negative")
        fresh = max(0, total - stale)
        # Confidence is the base signal; stale/drift/negative evidence lowers health.
        penalty = (0.22 * stale + 0.32 * drift + 0.18 * negative) / max(1, total)
        score = int(round(max(0.0, min(1.0, avg - penalty)) * 100.0))
        return {
            "learning_score": score,
            "effective_confidence_average": round(avg, 4),
            "fresh_knowledge_count": fresh,
            "stale_knowledge_count": stale,
            "drift_suspect_count": drift,
            "negative_knowledge_count": negative,
            "confidence_half_life_days": self.confidence_half_life_days,
            "stale_after_days": self.stale_after_days,
            "drift_failure_threshold": self.drift_failure_threshold,
            "recency_decay_enabled": self.recency_decay_enabled,
            "auto_demote_on_drift": self.auto_demote_on_drift,
            "memory_rule": "MEMORY PROPOSES; LIVE PAGE AUTHORIZES",
        }

    def summary(self, *, phase: str = "") -> Dict[str, Any]:
        controls = self._load(self.controls_path, "controls").get("controls") or {}
        states = self._load(self.states_path, "states").get("states") or {}
        transitions = self._load(self.transitions_path, "transitions").get("transitions") or {}
        choices = self._load(self.choice_branches_path, "choice_branches").get("choice_branches") or {}
        if phase:
            p = _norm(phase)
            controls = {k: v for k, v in controls.items() if isinstance(v, Mapping) and _norm(v.get("phase")) == p}
            states = {k: v for k, v in states.items() if isinstance(v, Mapping) and _norm(v.get("phase")) == p}
            transitions = {k: v for k, v in transitions.items() if isinstance(v, Mapping) and _norm(v.get("phase")) == p}
            choices = {k: v for k, v in choices.items() if isinstance(v, Mapping) and _norm(v.get("phase")) == p}
        control_rows = [self._decorate_health(v) for v in controls.values() if isinstance(v, Mapping)]
        transition_rows = [self._decorate_health(v) for v in transitions.values() if isinstance(v, Mapping)]
        choice_rows = [self._decorate_health(v) for v in choices.values() if isinstance(v, Mapping)]
        health = self._learning_health(control_rows + transition_rows + choice_rows)
        validated_choices = [x for x in choice_rows if x.get("effective_trust") == "validated"]
        validated_transitions = [x for x in transition_rows if x.get("effective_trust") == "validated"]
        return scrub_world_model_payload({
            "schema_version": self.SCHEMA_VERSION,
            "enabled": self.enabled,
            "phase": _norm(phase),
            "controls": {
                "total": len(control_rows),
                "validated": sum(1 for v in control_rows if v.get("effective_trust") == "validated"),
                "candidate": sum(1 for v in control_rows if v.get("effective_trust") == "candidate"),
                "negative": sum(1 for v in control_rows if v.get("effective_trust") == "negative"),
            },
            "states": len(states),
            "transitions": {
                "total": len(transition_rows),
                "validated": sum(1 for v in transition_rows if v.get("effective_trust") == "validated"),
                "candidate": sum(1 for v in transition_rows if v.get("effective_trust") == "candidate"),
                "negative": sum(1 for v in transition_rows if v.get("effective_trust") == "negative"),
            },
            "portal_choice_branches": {
                "total": len(choice_rows),
                "validated": sum(1 for v in choice_rows if v.get("effective_trust") == "validated"),
                "candidate": sum(1 for v in choice_rows if v.get("effective_trust") == "candidate"),
            },
            "learning_health": health,
            "recent_validated_choices": [
                {
                    "control": v.get("control") or {}, "portal_choice": v.get("portal_choice"),
                    "effect_type": v.get("effect_type"), "confidence": v.get("effective_confidence"),
                    "raw_confidence": v.get("raw_confidence"), "age_days": v.get("age_days"),
                    "success_count": v.get("success_count"),
                }
                for v in sorted(validated_choices, key=lambda x: str(x.get("last_seen_at") or ""), reverse=True)[:12]
            ],
            "recent_validated_hints": [
                {
                    "action_family": v.get("action_family"), "control": v.get("control") or {},
                    "effect_type": v.get("effect_type"), "confidence": v.get("effective_confidence"),
                    "raw_confidence": v.get("raw_confidence"), "age_days": v.get("age_days"),
                    "success_count": v.get("success_count"),
                }
                for v in sorted(validated_transitions, key=lambda x: str(x.get("last_seen_at") or ""), reverse=True)[:12]
            ],
            "safety": {"customer_values_stored": False, "selectors_stored": False, "coordinates_stored": False},
        })
