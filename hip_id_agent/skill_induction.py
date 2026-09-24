from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data

SCHEMA_VERSION = "hip.induced-skill-library.v1"
SKILL_SCHEMA = "hip.induced-skill.v1"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _words(value: Any) -> set[str]:
    return {x for x in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(x) > 1}


def _stable(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:22]


def _clean_locator(locator: Mapping[str, Any] | None) -> Dict[str, Any]:
    loc = dict(locator or {})
    return mask_sensitive_data({
        "names": [str(x) for x in loc.get("names") or [] if str(x)],
        "labels": [str(x) for x in loc.get("labels") or [] if str(x)],
        "placeholders": [str(x) for x in loc.get("placeholders") or [] if str(x)],
        "roles": [str(x) for x in loc.get("roles") or [] if str(x)],
        "section_aliases": [str(x) for x in loc.get("section_aliases") or [] if str(x)],
        "row_kind": str(loc.get("row_kind") or ""),
        "row_index": loc.get("row_index"),
    })


def build_form_blueprint(graph: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert a successful runtime form graph into a value-free reusable skill.

    Customer values, selectors, XPath, coordinates and browser IDs are intentionally
    excluded.  Replay must resolve the semantic locator against the current live page.
    """
    fields: List[Dict[str, Any]] = []
    for raw in graph.get("nodes") or []:
        if not isinstance(raw, Mapping):
            continue
        path = str(raw.get("input_path") or "")
        if not path:
            continue
        fields.append(mask_sensitive_data({
            "input_path": path,
            "field_key": str(raw.get("field_key") or ""),
            "action": str(raw.get("action") or ""),
            "section": str(raw.get("section") or ""),
            "row_kind": str(raw.get("row_kind") or ""),
            "row_index": raw.get("row_index"),
            "semantic_locator": _clean_locator(raw.get("semantic_locator") if isinstance(raw.get("semantic_locator"), Mapping) else {}),
            "verification": str(raw.get("verification") or "exact_committed_control_value"),
        }))
    fields.sort(key=lambda x: str(x.get("input_path") or ""))
    return {
        "schema_version": "hip.induced-form-skill.v1",
        "input_root": str(graph.get("input_root") or graph.get("object_path") or ""),
        "field_count": len(fields),
        "fields": fields,
        "values_stored": False,
        "selectors_stored": False,
        "coordinates_stored": False,
        "live_reproof_required": True,
    }


def _sanitize_workflow_steps(steps: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in steps:
        if not isinstance(raw, Mapping):
            continue
        typ = str(raw.get("type") or "")
        if typ in {"post_action_learning"}:
            continue
        row: Dict[str, Any] = {
            "type": typ,
            "action": str(raw.get("action") or ""),
            "label": str(raw.get("label") or ""),
            "risk": str(raw.get("risk") or ""),
        }
        if typ == "navigate_label":
            row["label"] = str(raw.get("label") or "")
        elif typ == "search":
            row["value_source"] = "current_task.entity"
        elif typ == "fill_from_input":
            row["input_root"] = str(raw.get("input_root") or "")
            row["value_source"] = "current_runtime_input_json"
        elif typ == "navigate":
            # Do not persist environment-specific URLs from the successful task.
            row["target_source"] = "configured_portal_base_or_current_task_start_url"
        elif typ == "live_goal":
            # A free-form live goal is discovery evidence, not a deterministic skill step.
            continue
        out.append(mask_sensitive_data(row))
    return out


class InducedSkillLibrary:
    """Value-free semantic skill induction and fast-replay memory.

    A skill is promoted only from a successful, exact-verified execution.  It stores
    semantic structure and action order, never business/customer values or transient
    browser locators.  Every reuse still requires live semantic re-proof.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        min_verified_successes: int = 1,
        demote_after_failures: int = 2,
        min_replay_confidence: float = 0.66,
        confidence_half_life_days: float = 45.0,
        stale_after_days: float = 120.0,
        max_skills: int = 500,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "induced_skills.json"
        self.min_verified_successes = max(1, int(min_verified_successes))
        self.demote_after_failures = max(1, int(demote_after_failures))
        self.min_replay_confidence = max(0.0, min(1.0, float(min_replay_confidence)))
        self.confidence_half_life_days = max(1.0, float(confidence_half_life_days))
        self.stale_after_days = max(1.0, float(stale_after_days))
        self.max_skills = max(10, int(max_skills))
        self.data = self._load()

    def _empty(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "skills": {},
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
            "updated_at": utc_now(),
        }

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("skills"), dict):
                return raw
        except Exception:
            pass
        return self._empty()

    def save(self) -> None:
        self.data["updated_at"] = utc_now()
        skills = self.data.get("skills") or {}
        if len(skills) > self.max_skills:
            ranked = sorted(
                skills.items(),
                key=lambda kv: (
                    float((kv[1] or {}).get("confidence") or 0),
                    int((kv[1] or {}).get("success_count") or 0),
                    str((kv[1] or {}).get("last_success_at") or ""),
                ),
                reverse=True,
            )[: self.max_skills]
            self.data["skills"] = dict(ranked)
        safe_write_json(self.path, self.data)

    @staticmethod
    def _signature(task: str, *, actions: Sequence[str] = (), target_area: str = "", input_root: str = "") -> Dict[str, Any]:
        words = sorted(_words(task) | {_norm(x) for x in actions if _norm(x)} | _words(target_area))
        return {
            "task_tokens": words,
            "actions": sorted(set(_norm(x) for x in actions if _norm(x))),
            "target_area": _norm(target_area),
            "input_root": str(input_root or ""),
        }

    @staticmethod
    def _age_days(timestamp: Any) -> float:
        text = str(timestamp or "").strip()
        if not text:
            return 10_000.0
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 86400.0)
        except Exception:
            return 10_000.0

    def _effective_confidence(self, row: Mapping[str, Any]) -> float:
        base = max(0.0, min(1.0, float(row.get("confidence") or 0.0)))
        age = self._age_days(row.get("last_success_at"))
        decay = math.pow(0.5, age / self.confidence_half_life_days)
        # Do not erase a successfully learned skill solely because it is old; make
        # stale skills weak priors that need fresh live re-proof instead.
        floor = 0.18 if age >= self.stale_after_days else 0.35
        return max(floor * base, base * decay)

    def match(
        self,
        task: str,
        *,
        actions: Sequence[str] = (),
        target_area: str = "",
        input_root: str = "",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        current = self._signature(task, actions=actions, target_area=target_area, input_root=input_root)
        current_tokens = set(current["task_tokens"])
        current_actions = set(current["actions"])
        ranked: List[Dict[str, Any]] = []
        for raw in (self.data.get("skills") or {}).values():
            if not isinstance(raw, Mapping):
                continue
            if str(raw.get("status") or "") != "validated":
                continue
            confidence = float(raw.get("confidence") or 0.0)
            effective_confidence = self._effective_confidence(raw)
            if effective_confidence < self.min_replay_confidence:
                continue
            sig = raw.get("signature") if isinstance(raw.get("signature"), Mapping) else {}
            skill_tokens = set(str(x) for x in sig.get("task_tokens") or [])
            skill_actions = set(str(x) for x in sig.get("actions") or [])
            union = current_tokens | skill_tokens
            token_score = (len(current_tokens & skill_tokens) / len(union)) if union else 0.0
            action_score = (len(current_actions & skill_actions) / max(1, len(current_actions | skill_actions))) if (current_actions or skill_actions) else 1.0
            target_score = 1.0 if current.get("target_area") and current.get("target_area") == sig.get("target_area") else (0.35 if not current.get("target_area") else 0.0)
            root_score = 1.0 if input_root and str(sig.get("input_root") or "") == str(input_root) else (0.5 if not input_root else 0.0)
            score = (0.38 * token_score) + (0.34 * action_score) + (0.18 * target_score) + (0.10 * root_score)
            score *= (0.65 + 0.35 * effective_confidence)
            if score < 0.38:
                continue
            ranked.append(mask_sensitive_data({
                **dict(raw),
                "match_score": round(score, 6),
                "effective_confidence": round(effective_confidence, 6),
                "age_days": round(self._age_days(raw.get("last_success_at")), 3),
                "stale": self._age_days(raw.get("last_success_at")) >= self.stale_after_days,
            }))
        ranked.sort(key=lambda x: (float(x.get("match_score") or 0), float(x.get("confidence") or 0), int(x.get("success_count") or 0)), reverse=True)
        return ranked[: max(1, int(limit))]

    def induce(
        self,
        *,
        task: str,
        actions: Sequence[str],
        target_area: str,
        input_root: str,
        workflow_steps: Sequence[Mapping[str, Any]],
        form_blueprints: Sequence[Mapping[str, Any]],
        page_families: Sequence[str],
        run_id: str,
        exact_verified: bool,
        evidence: Optional[Mapping[str, Any]] = None,
        skill_id_override: str = "",
    ) -> Dict[str, Any]:
        signature = self._signature(task, actions=actions, target_area=target_area, input_root=input_root)
        identity_material = json.dumps(signature, sort_keys=True, ensure_ascii=False)
        skill_id = str(skill_id_override or "").strip() or f"skill-{_stable(identity_material)}"
        skills = self.data.setdefault("skills", {})
        row = skills.setdefault(skill_id, {
            "schema_version": SKILL_SCHEMA,
            "skill_id": skill_id,
            "name": " / ".join([x for x in [target_area.strip(), ",".join(actions)] if x]) or "Learned portal task",
            "signature": signature,
            "workflow_steps": [],
            "form_blueprints": [],
            "page_families": [],
            "status": "candidate",
            "confidence": 0.55,
            "success_count": 0,
            "failure_count": 0,
            "consecutive_failures": 0,
            "created_at": utc_now(),
            "last_success_at": "",
            "last_failure_at": "",
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
            "live_reproof_required": True,
        })
        row["signature"] = signature
        row["workflow_steps"] = _sanitize_workflow_steps(workflow_steps)
        row["form_blueprints"] = [mask_sensitive_data(dict(x)) for x in form_blueprints if isinstance(x, Mapping)]
        row["page_families"] = sorted(set(_norm(x) for x in page_families if _norm(x)))
        if exact_verified:
            row["success_count"] = int(row.get("success_count") or 0) + 1
            row["consecutive_failures"] = 0
            row["last_success_at"] = utc_now()
            successes = int(row.get("success_count") or 0)
            # One exact-verified run is sufficient to make the skill reusable, but
            # confidence continues to strengthen with repeated successful replay.
            row["status"] = "validated" if successes >= self.min_verified_successes else "candidate"
            row["confidence"] = round(min(0.985, max(float(row.get("confidence") or 0.0), 0.72) + 0.055 * math.log2(successes + 1)), 6)
        if run_id:
            row["last_run_id"] = str(run_id)
        if evidence:
            row["latest_evidence"] = mask_sensitive_data(dict(evidence))
        self.save()
        return mask_sensitive_data(dict(row))

    def record_outcome(self, skill_id: str, *, success: bool, reason: str = "", run_id: str = "") -> Dict[str, Any]:
        row = (self.data.get("skills") or {}).get(str(skill_id))
        if not isinstance(row, dict):
            return {"pass": False, "reason": "skill_not_found", "skill_id": skill_id}
        if success:
            row["success_count"] = int(row.get("success_count") or 0) + 1
            row["consecutive_failures"] = 0
            row["last_success_at"] = utc_now()
            row["status"] = "validated"
            row["confidence"] = round(min(0.99, float(row.get("confidence") or 0.6) + 0.035), 6)
        else:
            row["failure_count"] = int(row.get("failure_count") or 0) + 1
            row["consecutive_failures"] = int(row.get("consecutive_failures") or 0) + 1
            row["last_failure_at"] = utc_now()
            row["last_failure_reason"] = _norm(reason)[:180]
            row["confidence"] = round(max(0.05, float(row.get("confidence") or 0.6) * 0.72), 6)
            if int(row.get("consecutive_failures") or 0) >= self.demote_after_failures:
                row["status"] = "drift_suspect"
        if run_id:
            row["last_run_id"] = str(run_id)
        self.save()
        return mask_sensitive_data(dict(row))

    def form_blueprint_for(self, skill: Mapping[str, Any] | None, *, input_root: str = "") -> Dict[str, Any]:
        if not isinstance(skill, Mapping):
            return {}
        candidates = [x for x in skill.get("form_blueprints") or [] if isinstance(x, Mapping)]
        if not candidates:
            return {}
        if input_root:
            for row in candidates:
                if str(row.get("input_root") or "") == str(input_root):
                    return mask_sensitive_data(dict(row))
        return mask_sensitive_data(dict(candidates[0]))

    def manifest(self) -> Dict[str, Any]:
        rows = list((self.data.get("skills") or {}).values())
        return {
            "schema_version": SCHEMA_VERSION,
            "path": str(self.path),
            "skill_count": len(rows),
            "validated_skill_count": sum(1 for x in rows if isinstance(x, Mapping) and x.get("status") == "validated"),
            "drift_suspect_count": sum(1 for x in rows if isinstance(x, Mapping) and x.get("status") == "drift_suspect"),
            "stale_skill_count": sum(
                1 for x in rows
                if isinstance(x, Mapping) and x.get("status") == "validated" and self._age_days(x.get("last_success_at")) >= self.stale_after_days
            ),
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
            "updated_at": self.data.get("updated_at"),
        }

    def list_skills(self, *, status: str = "", query: str = "", limit: int = 100) -> List[Dict[str, Any]]:
        wanted = _norm(status)
        qwords = _words(query)
        rows: List[Dict[str, Any]] = []
        for raw in (self.data.get("skills") or {}).values():
            if not isinstance(raw, Mapping):
                continue
            if wanted and _norm(raw.get("status")) != wanted:
                continue
            hay = _words(raw.get("name")) | set(str(x) for x in ((raw.get("signature") or {}).get("task_tokens") or []))
            if qwords and not (qwords & hay):
                continue
            rows.append(mask_sensitive_data({
                **dict(raw),
                "effective_confidence": round(self._effective_confidence(raw), 6),
                "age_days": round(self._age_days(raw.get("last_success_at")), 3),
                "stale": self._age_days(raw.get("last_success_at")) >= self.stale_after_days,
            }))
        rows.sort(
            key=lambda x: (str(x.get("status") or "") == "validated", float(x.get("effective_confidence") or 0), int(x.get("success_count") or 0)),
            reverse=True,
        )
        return rows[: max(1, int(limit))]


class SkillInductionEngine:
    """Promote successful portal trajectories into reusable, governed skills.

    The engine intentionally separates *workflow memory* from *execution authority*:
    a learned skill can propose an ordered workflow and semantic form blueprint, but
    current input values and the current live DOM/accessibility tree remain the only
    authorities for values and actions.
    """

    MUTATION_WORDS = {
        "create", "submit", "save", "update", "delete", "remove", "deploy", "migrate",
        "publish", "unpublish", "approve", "reject", "archive", "restore", "enable", "disable",
        "activate", "deactivate", "clone", "duplicate", "import", "upload",
    }

    def __init__(self, library: InducedSkillLibrary, *, min_match_score: float = 0.45) -> None:
        self.library = library
        self.min_match_score = max(0.0, min(1.0, float(min_match_score)))

    @staticmethod
    def _explicit_mutation_allowed(task: str, step: Mapping[str, Any], *, has_runtime_input: bool = False) -> bool:
        if str(step.get("risk") or "").lower() != "mutation":
            return True
        task_words = _words(task)
        step_words = _words(step.get("action")) | _words(step.get("label"))
        named = bool(step_words & task_words & SkillInductionEngine.MUTATION_WORDS)
        full_label = _norm(step.get("label") or step.get("action"))
        if named or (full_label and full_label in _norm(task)):
            return True
        # Filling an Edit/Create/Configure request from input.json normally needs
        # the learned Save/Submit commit step. It is still mutation-gated later.
        commit_words = step_words & {"save", "submit", "update", "create", "apply", "commit"}
        task_implies_commit = bool(task_words & {"edit", "create", "configure", "fill", "update", "apply"})
        return bool(has_runtime_input and commit_words and task_implies_commit)

    def activate(
        self,
        *,
        task: str,
        actions: Sequence[str],
        target_area: str,
        input_root: str,
        input_json: str,
        start_url: str,
        entity: str = "",
    ) -> Dict[str, Any]:
        matches = self.library.match(task, actions=actions, target_area=target_area, input_root=input_root, limit=5)
        if not matches or float(matches[0].get("match_score") or 0.0) < self.min_match_score:
            return {"active": False, "reason": "no_validated_skill_match", "matches": matches}
        skill = dict(matches[0])
        instantiated: List[Dict[str, Any]] = []
        for raw in skill.get("workflow_steps") or []:
            if not isinstance(raw, Mapping):
                continue
            typ = str(raw.get("type") or "")
            row = dict(raw)
            if typ == "navigate":
                row = {"type": "navigate", "target": start_url, "risk": "read", "skill_replay": True}
            elif typ == "fill_from_input":
                if not str(input_json or "").strip():
                    continue
                row = {
                    "type": "fill_from_input", "input_json": input_json,
                    "input_root": input_root or str(raw.get("input_root") or ""),
                    "risk": "draft", "skill_replay": True,
                }
            elif typ == "search":
                if not entity:
                    continue
                row = {"type": "search", "value": entity, "risk": "read", "skill_replay": True}
            elif typ in {"semantic_action", "navigate_label"}:
                if not self._explicit_mutation_allowed(task, row, has_runtime_input=bool(str(input_json or "").strip())):
                    # A skill can never smuggle a mutation that the current user
                    # request did not explicitly name.
                    continue
                row["skill_replay"] = True
                row["memory_is_advisory"] = True
            elif typ == "learn_surface":
                row = {"type": "learn_surface", "deep": False, "risk": "read", "skill_replay": True}
            else:
                continue
            instantiated.append(mask_sensitive_data(row))
        if not instantiated:
            return {"active": False, "reason": "matched_skill_has_no_safe_instantiable_steps", "matches": matches, "skill": skill}
        return {
            "active": True,
            "reason": "validated_skill_instantiated",
            "skill": skill,
            "steps": instantiated,
            "matches": matches,
            "live_reproof_required": True,
            "values_reused": False,
        }

    def induce_from_execution(
        self,
        *,
        task: str,
        actions: Sequence[str],
        target_area: str,
        input_root: str,
        executed_steps: Sequence[Mapping[str, Any]],
        form_blueprints: Sequence[Mapping[str, Any]],
        page_families: Sequence[str],
        run_id: str,
        exact_verified: bool,
        evidence: Optional[Mapping[str, Any]] = None,
        skill_id_override: str = "",
    ) -> Dict[str, Any]:
        return self.library.induce(
            task=task,
            actions=actions,
            target_area=target_area,
            input_root=input_root,
            workflow_steps=executed_steps,
            form_blueprints=form_blueprints,
            page_families=page_families,
            run_id=run_id,
            exact_verified=exact_verified,
            evidence=evidence,
            skill_id_override=skill_id_override,
        )
