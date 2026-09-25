"""Certified portal skills: learn a form, prove it by deterministic replay, then replay it fast (V243R19).

A *skill* is what the agent knows about one HIP form for one **operation**
(create, edit, clone, merge, deploy, ...) and one **branch**.  A branch is the
set of values of the choice fields that change the form's shape: Data Format
EDI shows separator fields, Interface Type AS2 shows an AS2 section, Existing
Account No shows Account Name instead of a dropdown.

Lifecycle
---------
learn     The adaptive run (live discovery, models, self-repair) proves every
          input.json value by exact read-back.  The result is a *candidate*.
prove     The candidate is replayed deterministically on a fresh form: only the
          learned bindings, no model calls, no exploration; every value read
          back exactly and every binding identical to the learning run.  This
          happens in the same run when the caller can reopen the form (the
          operation runner does), otherwise on the next run.
certify   Only a passing replay saves the skill and merges its form structure
          into long-term memory.  Nothing learned is saved before that.
replay    Later runs with a certified skill take the fast path.
re-learn  A new input field, an unseen branch, a field revealed by a selected
          value, a missing option or a changed binding marks the skill stale or
          sends the run to learning; the new skill must be certified again.

Which choice fields are *branch* fields is learned too: two skills whose forms
have a different shape differ in some choice values, and those fields become
branch fields; two skills with the same shape despite a different value show
that field is not one.

Stored skills are value-free: choice values only as short hashes (to tell
branches apart), no free-text values, selectors or coordinates.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from .form_structure_memory import FormStructureMemory, path_pattern, resolve_memory_dir
from .safe_io import safe_write_json
from .security import mask_sensitive_string

SCHEMA = "hip.portal-skill-library.v1"
CHOICE_ACTIONS = {"select_single", "select_multi", "select_radio", "toggle", "select_checkbox_group"}
STRUCTURE_NOVELTY = {"new_input_field", "new_field_revealed", "unresolved_input", "uncovered_required", "surface_changed"}
OPERATION_ALIASES: Dict[str, Sequence[str]] = {
    "create": ("create", "add", "new"),
    "edit": ("edit", "update", "modify", "change"),
    "clone": ("clone", "copy", "duplicate"),
    "merge": ("merge", "merger", "combine"),
    "deploy": ("deploy", "deployment", "publish"),
    "migrate": ("migrate", "migration", "promote"),
    "validate": ("validate", "validation"),
    "delete": ("delete", "remove"),
}
# Buttons that commit each operation, most specific first.  The label actually
# used is learned per skill once a commit has been verified.
COMMIT_LABELS: Dict[str, Sequence[str]] = {
    "create": ("Create", "Save", "Submit"),
    "edit": ("Save", "Update", "Save Changes", "Submit"),
    "clone": ("Save", "Create", "Clone", "Submit"),
    "merge": ("Merge", "Confirm", "Save"),
    "deploy": ("Deploy", "Confirm"),
    "migrate": ("Migrate", "Confirm"),
    "validate": ("Validate",),
    "delete": ("Delete", "Confirm"),
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def _hash(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        value = sorted(_norm(v) for v in value)
    elif isinstance(value, bool):
        value = "true" if value else "false"
    else:
        value = _norm(value)
        if value in {"yes", "y", "true", "on", "enabled"}:
            value = "true"
        elif value in {"no", "n", "false", "off", "disabled"}:
            value = "false"
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()[:10]


def canonical_operation(value: Any) -> str:
    text = _norm(value)
    if not text:
        return "create"
    for name, aliases in OPERATION_ALIASES.items():
        if text == name or text in aliases:
            return name
    for name, aliases in OPERATION_ALIASES.items():
        if any(re.search(rf"\b{re.escape(a)}\b", text) for a in aliases):
            return name
    # A portal action the vocabulary does not know yet ("Archive") is its own operation.
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "create"


def operation_specs(input_data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Every operation input.json asks for, in order.

    ``operations`` may be a list of ``{"phase", "operation", "target", "values",
    "commit"}`` or a mapping ``{phase: spec-or-operation-name}``.
    """
    raw = input_data.get("operations") if isinstance(input_data, Mapping) else None
    rows: List[Dict[str, Any]] = []
    if isinstance(raw, Mapping):
        for phase, spec in raw.items():
            spec = {"operation": spec} if isinstance(spec, str) else dict(spec or {})
            spec.setdefault("phase", phase)
            rows.append(spec)
    elif isinstance(raw, list):
        rows = [dict(x) for x in raw if isinstance(x, Mapping)]
    out = []
    for spec in rows:
        spec["operation"] = canonical_operation(spec.get("operation") or spec.get("action"))
        spec["phase"] = str(spec.get("phase") or "")
        out.append(spec)
    return out


def resolve_operation(input_data: Mapping[str, Any], phase: str) -> str:
    """The operation this phase's form is filled for; ``create`` by default."""
    if not isinstance(input_data, Mapping):
        return "create"
    explicit = input_data.get("_operation")
    if explicit:
        return canonical_operation(explicit)
    objects = input_data.get("objects") if isinstance(input_data.get("objects"), Mapping) else {}
    obj = objects.get(phase) if isinstance(objects.get(phase), Mapping) else {}
    if obj.get("_operation"):
        return canonical_operation(obj.get("_operation"))
    for spec in operation_specs(input_data):
        if spec.get("phase") == phase:
            return spec["operation"]
    return "create"


def _strip_identity(identity: str, *, choice: bool = False) -> str:
    """Value-free part of a control identity.

    Parts: section | row_kind | row | semantic_key | framework_key | component | role | label.
    The row part can be a fingerprint of the row's values, and a radio's
    semantic key and label are the option chosen, so both are left out.
    """
    parts = str(identity or "").split("|")
    if len(parts) < 8:
        return str(identity or "")
    if choice:
        return "|".join([parts[0], parts[1], parts[4], parts[5], parts[6]])
    return "|".join([parts[0], parts[1], *parts[3:]])


def surface_of(controls: Iterable[Mapping[str, Any]]) -> List[str]:
    """Value-free shape of the visible form: the set of control identities."""
    from .stateful_form_runtime import _stateful_control_identity

    out: Set[str] = set()
    for control in controls or []:
        if not isinstance(control, Mapping):
            continue
        typ = _norm(control.get("type") or control.get("role"))
        choice = typ in {"radio", "checkbox"}
        out.add(_strip_identity(_stateful_control_identity(dict(control)), choice=choice))
    return sorted(x for x in out if x.strip("|"))


def _rel_path(input_path: str) -> str:
    parts = str(input_path or "").split(".")
    return ".".join(parts[3:]) if len(parts) > 3 and parts[0] == "$" else str(input_path or "")


class PortalSkillStore:
    """``<memory_dir>/portal_skills/<phase>.json``."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.root = self.memory_dir / "portal_skills"

    @classmethod
    def for_run(cls, config: Any = None, page: Any = None) -> Optional["PortalSkillStore"]:
        memory_dir = resolve_memory_dir(config, page)
        return cls(memory_dir) if memory_dir is not None else None

    def file(self, phase: str) -> Path:
        return self.root / f"{re.sub(r'[^a-z0-9_]+', '_', str(phase or 'unknown').lower())}.json"

    def load(self, phase: str) -> Dict[str, Any]:
        try:
            data = json.loads(self.file(phase).read_text(encoding="utf-8")) if self.file(phase).exists() else {}
        except Exception:
            data = {}
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
            data = {"schema_version": SCHEMA, "phase": phase, "skills": {}, "branch_fields": {}, "choice_fields": [], "history": []}
        for key, default in (("skills", {}), ("branch_fields", {}), ("choice_fields", []), ("history", [])):
            data.setdefault(key, default)
        return data

    def save(self, phase: str, data: Dict[str, Any]) -> None:
        data["updated_at"] = _now()
        data["history"] = list(data.get("history") or [])[-60:]
        safe_write_json(self.file(phase), data, mask=False)

    @staticmethod
    def log(data: Dict[str, Any], event: str, skill: Optional[Mapping[str, Any]] = None, **extra: Any) -> None:
        row = {"at": _now(), "event": event}
        if skill:
            row.update({"skill_id": skill.get("skill_id"), "operation": skill.get("operation"), "scope": skill.get("scope")})
        row.update({k: (mask_sensitive_string(v) if isinstance(v, str) else v) for k, v in extra.items()})
        data.setdefault("history", []).append(row)

    def commit_label(self, phase: str, skill_id: str) -> str:
        """The commit button a verified commit of this skill used before."""
        skill = self.load(phase)["skills"].get(str(skill_id or "")) or {}
        return str((skill.get("commit") or {}).get("label") or "")

    def record_commit(self, phase: str, skill_id: str, label: str) -> None:
        data = self.load(phase)
        skill = data["skills"].get(str(skill_id or ""))
        if skill is None or not label:
            return
        commit = skill.setdefault("commit", {})
        commit["label"] = label
        commit["verified"] = int(commit.get("verified") or 0) + 1
        self.log(data, "commit_verified", skill, label=label)
        self.save(phase, data)

    def summary(self, phase: str) -> Dict[str, Any]:
        data = self.load(phase)
        skills = list(data["skills"].values())
        return {
            "phase": phase,
            "certified": sum(1 for s in skills if s.get("status") == "certified"),
            "candidate": sum(1 for s in skills if s.get("status") == "candidate"),
            "stale": sum(1 for s in skills if s.get("status") == "stale"),
            "operations": sorted({str(s.get("operation")) for s in skills}),
            "branch_fields": sorted(data["branch_fields"]),
            "file": str(self.file(phase)),
        }


def _skills_config(config: Any, page: Any) -> Any:
    for source in (config, getattr(getattr(page, "_hip_browser_session", None), "config", None)):
        cfg = getattr(source, "portal_skills", None) if source is not None else None
        if cfg is not None:
            return cfg
    return None


def skills_setting(config: Any, page: Any, name: str, default: Any) -> Any:
    cfg = _skills_config(config, page)
    return getattr(cfg, name, default) if cfg is not None else default


class SkillSession:
    """The skill decision for one goal-engine run of one phase (or section)."""

    def __init__(self) -> None:
        self.enabled = False
        self.store: Optional[PortalSkillStore] = None
        self.phase = ""
        self.scope = "form"
        self.operation = "create"
        self.choice_hashes: Dict[str, str] = {}
        self.field_patterns: List[str] = []
        self.branch_key = ""
        self.skill: Optional[Dict[str, Any]] = None
        self.plan = "learn"
        self.reason = "skills_disabled"
        self.novelty: List[Dict[str, Any]] = []
        self.replay_only = False
        self.save_only_when_certified = True
        self.binding_check = True
        self.started = time.monotonic()
        self.events: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ begin
    @classmethod
    def begin(
        cls, *, config: Any, page: Any, phase: str, section: Optional[str], graph: Mapping[str, Any],
        leaves: Sequence[Mapping[str, Any]], input_data: Mapping[str, Any], mode: str = "auto",
        override: Optional[Mapping[str, Any]] = None,
    ) -> "SkillSession":
        self = cls()
        self.phase = phase
        self.scope = str(section or "form")
        self.operation = resolve_operation(input_data, phase)
        self.field_patterns = sorted({path_pattern(str(leaf.get("input_path") or "")) for leaf in leaves if leaf.get("input_path")})
        self.save_only_when_certified = bool(skills_setting(config, page, "save_learning_only_when_certified", True))
        self.binding_check = bool(skills_setting(config, page, "replay_binding_identity_check", True))
        if mode in {"off", "learn_no_save"} or not bool(skills_setting(config, page, "enabled", True)):
            self.reason = {"off": "learning_run_requested", "learn_no_save": "refill_after_failed_proof"}.get(mode, "skills_disabled")
            # Still resolve the store so a learning run can stage its candidate.
            self.store = PortalSkillStore.for_run(config, page) if mode in {"off", "learn_no_save"} else None
            self.enabled = self.store is not None
            if self.enabled:
                self._hash_choices(graph, leaves, self.store.load(phase))
            return self
        self.store = PortalSkillStore.for_run(config, page)
        if self.store is None:
            self.reason = "no_memory_dir"
            return self
        self.enabled = True
        data = self.store.load(phase)
        self._hash_choices(graph, leaves, data)
        if override is not None:
            self.skill = dict(override)
            self.plan, self.reason, self.replay_only = "replay", "replay_proof", True
            return self
        if not bool(skills_setting(config, page, "deterministic_replay", True)):
            self.reason = "deterministic_replay_disabled"
            return self
        same_scope = [
            s for s in data["skills"].values()
            if s.get("operation") == self.operation and s.get("scope") == self.scope and s.get("status") in {"certified", "candidate"}
        ]
        branch_fields = set(data["branch_fields"])
        matching = [s for s in same_scope if self._same_branch(s, branch_fields)]
        if not matching:
            self.reason = "unseen_branch" if same_scope else "no_skill"
            if same_scope:
                self.novelty.append({"kind": "unseen_branch", "branch_fields": sorted(branch_fields & set(self.choice_hashes))})
            return self
        covering = [s for s in matching if set(self.field_patterns) <= set(s.get("field_patterns") or [])]
        if not covering:
            nearest = max(matching, key=lambda s: len(set(self.field_patterns) & set(s.get("field_patterns") or [])))
            missing = sorted(set(self.field_patterns) - set(nearest.get("field_patterns") or []))
            self.reason = "new_input_field"
            self.novelty.append({"kind": "new_input_field", "fields": missing, "skill_id": nearest.get("skill_id")})
            self._learn_branch_fields(data, nearest, reason="new_input_field")
            self.store.save(phase, data)
            return self
        covering.sort(key=lambda s: (s.get("status") == "certified", int((s.get("stats") or {}).get("replays") or 0), str(s.get("learned_at") or "")), reverse=True)
        self.skill = dict(covering[0])
        self.plan = "replay"
        self.reason = "certified_skill" if self.skill.get("status") == "certified" else "candidate_awaiting_replay_proof"
        return self

    def _hash_choices(self, graph: Mapping[str, Any], leaves: Sequence[Mapping[str, Any]], data: Mapping[str, Any]) -> None:
        choice_patterns = set(data.get("choice_fields") or [])
        for node in graph.get("nodes") or []:
            if isinstance(node, Mapping) and str(node.get("action") or "") in CHOICE_ACTIONS and node.get("input_path"):
                choice_patterns.add(path_pattern(str(node.get("input_path"))))
        values: Dict[str, List[Any]] = {}
        for leaf in leaves:
            pattern = path_pattern(str(leaf.get("input_path") or ""))
            if pattern in choice_patterns:
                values.setdefault(pattern, []).append(leaf.get("value"))
        self.choice_hashes = {p: _hash(v[0] if len(v) == 1 else sorted(_norm(x) for x in v)) for p, v in values.items()}
        branch = sorted((p, self.choice_hashes.get(p, "")) for p in (data.get("branch_fields") or {}))
        self.branch_key = hashlib.sha256(json.dumps(branch).encode("utf-8")).hexdigest()[:12] if branch else "base"

    def _same_branch(self, skill: Mapping[str, Any], branch_fields: Set[str]) -> bool:
        hashes = skill.get("choice_hashes") or {}
        return all(str(hashes.get(p, "")) == str(self.choice_hashes.get(p, "")) for p in branch_fields)

    def _learn_branch_fields(self, data: Dict[str, Any], other: Mapping[str, Any], *, reason: str) -> List[str]:
        """Choice fields whose value differs from a skill of another form shape are branch fields."""
        theirs = other.get("choice_hashes") or {}
        diff = sorted(p for p in set(theirs) | set(self.choice_hashes) if str(theirs.get(p, "")) != str(self.choice_hashes.get(p, "")))
        for pattern in diff:
            row = data["branch_fields"].setdefault(pattern, {"evidence": 0, "sources": []})
            row["evidence"] = int(row.get("evidence") or 0) + 1
            if reason not in row["sources"]:
                row["sources"].append(reason)
        if diff:
            PortalSkillStore.log(data, "branch_fields_learned", other, fields=diff, reason=reason)
            self.events.append({"event": "branch_fields_learned", "fields": diff, "reason": reason})
        return diff

    # ------------------------------------------------------------------ replay checks
    def binding_changes(self, attempts: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        if not self.skill or not self.binding_check:
            return []
        learned = self.skill.get("bindings") or {}
        changes = []
        for attempt in attempts or []:
            if not isinstance(attempt, Mapping) or attempt.get("success") is not True:
                continue
            rel = _rel_path(str(attempt.get("input_path") or ""))
            key = f"{rel}#{attempt.get('choice_option') or ''}" if attempt.get("choice_option") else rel
            old = learned.get(key)
            identity = str((attempt.get("binding_diagnostics") or {}).get("selected_identity") or "")
            if not old or not identity or not old.get("identity"):
                continue
            now = _strip_identity(identity, choice=bool(old.get("choice")))
            if now != old.get("identity"):
                changes.append({"field": rel, "learned": old.get("identity"), "now": now})
        return changes

    @staticmethod
    def classify_failure(cycle: Mapping[str, Any], full_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Why a deterministic replay did not prove the goal, as novelty kinds."""
        out: List[Dict[str, Any]] = []
        after = cycle.get("runtime_input_leaf_ledger_after") or {}
        unresolved = after.get("unresolved_input_leaves") or []
        if unresolved:
            out.append({"kind": "unresolved_input", "fields": [str(x.get("input_path") or x.get("field_key") or "") for x in unresolved][:20]})
        if cycle.get("unexecuted_runtime_nodes") or cycle.get("nodes_discovered_after_execution"):
            out.append({"kind": "new_field_revealed", "count": int(cycle.get("nodes_discovered_after_execution") or 0)})
        if cycle.get("uncovered_required_controls"):
            out.append({"kind": "uncovered_required", "controls": [str(x.get("label") or x.get("semantic_key") or "") for x in cycle["uncovered_required_controls"]][:20]})
        failed = [a for a in (full_result.get("attempts") or []) if isinstance(a, Mapping) and a.get("success") is not True]
        missing_option = [a for a in failed if re.search(r"option|not (?:available|present|found|in list)", str(a.get("reason") or ""), re.I)]
        if missing_option:
            out.append({"kind": "missing_option", "fields": [str(a.get("field") or "") for a in missing_option][:20]})
        if failed and not missing_option:
            out.append({"kind": "replay_failed", "fields": [str(a.get("field") or "") for a in failed][:20],
                        "reasons": [mask_sensitive_string(str(a.get("reason") or ""))[:160] for a in failed][:5]})
        if cycle.get("status") == "surface_lost":
            out.append({"kind": "surface_lost"})
        return out or [{"kind": "replay_failed"}]

    def replay_failed(self, novelty: Sequence[Mapping[str, Any]], *, controls_after: Sequence[Mapping[str, Any]] = ()) -> None:
        """A replay did not hold: the skill is stale (or the candidate discarded); learn again."""
        self.novelty.extend(dict(x) for x in novelty)
        self.plan = "learn"
        if not self.enabled or self.store is None or not self.skill:
            return
        data = self.store.load(self.phase)
        skill = data["skills"].get(str(self.skill.get("skill_id")))
        kinds = {str(x.get("kind")) for x in novelty}
        diff: List[str] = []
        if kinds & STRUCTURE_NOVELTY:
            diff = self._learn_branch_fields(data, self.skill, reason=sorted(kinds & STRUCTURE_NOVELTY)[0])
        if skill is not None:
            stats = skill.setdefault("stats", {})
            stats["replay_failures"] = int(stats.get("replay_failures") or 0) + 1
            if skill.get("status") == "candidate":
                # An unproven candidate that does not replay is never saved.
                data["skills"].pop(str(skill.get("skill_id")), None)
                PortalSkillStore.log(data, "candidate_discarded", skill, novelty=sorted(kinds))
            elif diff:
                # The input chose another branch; the skill still holds for its own.
                PortalSkillStore.log(data, "branch_split", skill, fields=diff)
            elif kinds <= {"missing_option"}:
                # The input asks for an option the portal does not offer: an input
                # problem, not a changed form.
                PortalSkillStore.log(data, "replay_input_option_missing", skill)
            else:
                skill["status"] = "stale"
                skill["stale_reason"] = sorted(kinds)
                skill["stale_at"] = _now()
                PortalSkillStore.log(data, "skill_stale", skill, novelty=sorted(kinds))
        self.store.save(self.phase, data)
        self.reason = "relearn_after_" + (sorted(kinds)[0] if kinds else "replay_failed")

    # ------------------------------------------------------------------ learn / certify
    def build_candidate(
        self, *, graph: Mapping[str, Any], final_execution: Mapping[str, Any], cycles: Sequence[Mapping[str, Any]],
        controls_after: Sequence[Mapping[str, Any]], memory_reveal: Optional[Mapping[str, Any]] = None,
        duration_seconds: float = 0.0, option_inference_used: bool = False, base_structure: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        structure = FormStructureMemory.extract_learning(
            graph=dict(graph), final_execution=dict(final_execution), cycles=list(cycles), memory_reveal=dict(memory_reveal or {}) or None,
        )
        if base_structure:
            # A replayed skill that needed re-learning keeps what it already knew.
            for key in ("fields", "sections", "rows"):
                merged = dict((base_structure.get(key) or {}))
                merged.update(structure.get(key) or {})
                structure[key] = merged
        bindings: Dict[str, Dict[str, Any]] = {}
        node_by_id = {str(n.get("node_id")): n for n in graph.get("nodes") or [] if isinstance(n, Mapping)}
        choice_fields: Set[str] = set()
        for attempt in final_execution.get("attempts") or []:
            if not isinstance(attempt, Mapping) or attempt.get("success") is not True:
                continue
            node = node_by_id.get(str(attempt.get("node_id"))) or {}
            action = str(node.get("action") or attempt.get("action") or "")
            identity = str((attempt.get("binding_diagnostics") or {}).get("selected_identity") or "")
            rel = _rel_path(str(attempt.get("input_path") or node.get("input_path") or ""))
            if not rel:
                continue
            choice = action in CHOICE_ACTIONS
            if choice:
                choice_fields.add(path_pattern(str(attempt.get("input_path") or node.get("input_path") or "")))
            option = node.get("choice_option") or ""
            key = f"{rel}#{option}" if option else rel
            bindings[key] = {"action": action, "choice": choice, "identity": _strip_identity(identity, choice=choice) if identity else ""}
        now = _now()
        skill_id = hashlib.sha256(f"{self.operation}|{self.scope}|{self.branch_key}|{now}|{time.monotonic()}".encode("utf-8")).hexdigest()[:16]
        return {
            "skill_id": skill_id, "phase": self.phase, "operation": self.operation, "scope": self.scope,
            "branch_key": self.branch_key, "choice_hashes": dict(self.choice_hashes),
            "field_patterns": list(self.field_patterns), "bindings": bindings, "choice_fields": sorted(choice_fields),
            "structure": structure, "surface": surface_of(controls_after), "option_inference_used": bool(option_inference_used),
            "status": "candidate", "learned_at": now, "values_stored": False,
            "stats": {"learn_seconds": round(float(duration_seconds or 0.0), 2), "replays": 0, "replay_failures": 0},
        }

    def _learn_from_shapes(self, data: Dict[str, Any], candidate: Mapping[str, Any]) -> None:
        """Compare this form's shape with the other branches of the same operation."""
        mine = set(candidate.get("surface") or [])
        for other in list(data["skills"].values()):
            if other.get("operation") != candidate.get("operation") or other.get("scope") != candidate.get("scope"):
                continue
            if other.get("skill_id") == candidate.get("skill_id") or not other.get("surface"):
                continue
            theirs = set(other.get("surface") or [])
            hashes = other.get("choice_hashes") or {}
            diff = {p for p in set(hashes) | set(candidate.get("choice_hashes") or {})
                    if str(hashes.get(p, "")) != str((candidate.get("choice_hashes") or {}).get(p, ""))}
            if not diff:
                continue
            if mine != theirs:
                for p in diff:
                    row = data["branch_fields"].setdefault(p, {"evidence": 0, "sources": []})
                    row["evidence"] = int(row.get("evidence") or 0) + 1
                    if "shape_difference" not in row["sources"]:
                        row["sources"].append("shape_difference")
            else:
                # Same shape despite a different value: not a branch field.
                for p in diff:
                    row = data["branch_fields"].get(p)
                    if row is None:
                        continue
                    row["evidence"] = int(row.get("evidence") or 0) - 1
                    if row["evidence"] <= 0:
                        data["branch_fields"].pop(p, None)
                        PortalSkillStore.log(data, "branch_field_dropped", candidate, field=p, reason="same_shape")

    def _retire_same_branch(self, data: Dict[str, Any], keep: Mapping[str, Any], statuses: Set[str]) -> None:
        branch_fields = set(data["branch_fields"])
        for sid, other in list(data["skills"].items()):
            if sid == keep.get("skill_id") or other.get("operation") != keep.get("operation") or other.get("scope") != keep.get("scope"):
                continue
            if other.get("status") not in statuses:
                continue
            hashes = other.get("choice_hashes") or {}
            if all(str(hashes.get(p, "")) == str((keep.get("choice_hashes") or {}).get(p, "")) for p in branch_fields):
                if set(other.get("field_patterns") or []) <= set(keep.get("field_patterns") or []):
                    data["skills"].pop(sid, None)
                    PortalSkillStore.log(data, "skill_superseded", other, by=keep.get("skill_id"))

    def stage(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Keep an unproven candidate for its replay proof; nothing is learned yet."""
        if not self.enabled or self.store is None:
            return {"status": "not_saved", "reason": "no memory directory"}
        data = self.store.load(self.phase)
        self._retire_same_branch(data, candidate, {"candidate", "stale"})
        data["skills"][candidate["skill_id"]] = candidate
        PortalSkillStore.log(data, "candidate_staged", candidate)
        self.store.save(self.phase, data)
        return {"status": "candidate", "skill_id": candidate["skill_id"], "file": str(self.store.file(self.phase)),
                "note": "saved as knowledge only after a deterministic replay proves it"}

    def certify(self, candidate: Dict[str, Any], *, proof: Mapping[str, Any], config: Any = None, page: Any = None) -> Dict[str, Any]:
        """The replay proved the candidate: save the skill and its form structure."""
        if not self.enabled or self.store is None:
            return {"status": "not_saved", "reason": "no memory directory"}
        data = self.store.load(self.phase)
        skill = dict(data["skills"].get(candidate["skill_id"]) or candidate)
        skill.update({k: v for k, v in candidate.items() if k not in {"stats", "status"}})
        stats = dict(candidate.get("stats") or {})
        stats.update(skill.get("stats") or {})
        stats["replays"] = int(stats.get("replays") or 0) + 1
        stats["last_replay_seconds"] = round(float(proof.get("seconds") or 0.0), 2)
        if stats.get("learn_seconds") and stats["last_replay_seconds"]:
            stats["speedup"] = round(float(stats["learn_seconds"]) / max(0.01, float(stats["last_replay_seconds"])), 1)
        if proof.get("surface"):
            skill["surface"] = list(proof["surface"])
        skill.update({"status": "certified", "certified_at": _now(), "stats": stats,
                      "proof": {k: proof.get(k) for k in ("kind", "seconds", "exact_nodes", "binding_changes")}})
        skill.pop("stale_reason", None)
        data["choice_fields"] = sorted(set(data.get("choice_fields") or []) | set(skill.get("choice_fields") or []))
        self._learn_from_shapes(data, skill)
        data["skills"][skill["skill_id"]] = skill
        self._retire_same_branch(data, skill, {"candidate", "stale", "certified"})
        PortalSkillStore.log(data, "skill_certified", skill, proof=str(proof.get("kind") or ""))
        self.store.save(self.phase, data)
        memory = FormStructureMemory(self.store.memory_dir)
        merged = memory.merge_learning(self.phase, skill.get("structure") or {})
        return {"status": "certified", "skill_id": skill["skill_id"], "file": str(self.store.file(self.phase)),
                "form_structure_memory": merged, "stats": stats}

    def record_replay(self, *, seconds: float, surface: Sequence[str]) -> Dict[str, Any]:
        """A deterministic replay of this skill proved the goal."""
        if not self.enabled or self.store is None or not self.skill:
            return {}
        if self.skill.get("status") == "candidate":
            return self.certify(self.skill, proof={"kind": "next_run_replay", "seconds": seconds, "surface": list(surface)})
        data = self.store.load(self.phase)
        skill = data["skills"].get(str(self.skill.get("skill_id")))
        if skill is None:
            return {}
        stats = skill.setdefault("stats", {})
        stats["replays"] = int(stats.get("replays") or 0) + 1
        stats["last_replay_seconds"] = round(float(seconds), 2)
        if stats.get("learn_seconds"):
            stats["speedup"] = round(float(stats["learn_seconds"]) / max(0.01, float(seconds)), 1)
        drift = sorted(set(surface) ^ set(skill.get("surface") or []))
        if drift:
            skill["surface_drift"] = drift[:30]
        self.store.save(self.phase, data)
        return {"status": "certified", "skill_id": skill.get("skill_id"), "stats": stats, "surface_drift": drift[:30]}

    def log_event(self, event: str, skill: Optional[Mapping[str, Any]] = None, **extra: Any) -> None:
        """History only (never knowledge): e.g. a proof that did not hold."""
        if not self.enabled or self.store is None:
            return
        data = self.store.load(self.phase)
        PortalSkillStore.log(data, event, skill, **extra)
        self.store.save(self.phase, data)

    def audit(self) -> Dict[str, Any]:
        skill = self.skill or {}
        return {
            "enabled": self.enabled, "operation": self.operation, "scope": self.scope, "branch_key": self.branch_key,
            "plan": self.plan, "reason": self.reason, "novelty": list(self.novelty), "events": list(self.events),
            "skill_id": skill.get("skill_id", ""), "skill_status": skill.get("status", ""),
            "choice_field_count": len(self.choice_hashes), "input_field_count": len(self.field_patterns),
        }


def lifecycle_self_check(memory_dir: Path) -> Dict[str, Any]:
    """Prove the skill lifecycle on a throw-away store (used by VERIFY_V243R19)."""
    from types import SimpleNamespace

    phase = "source_transport_profile"
    config = SimpleNamespace(reporting=SimpleNamespace(memory_dir=str(memory_dir)), portal_skills=None)

    def leaves(**values: Any) -> List[Dict[str, Any]]:
        return [{"input_path": f"$.objects.{phase}.{k}", "field_key": k, "value": v} for k, v in values.items()]

    def graph(**values: Any) -> Dict[str, Any]:
        return {"nodes": [{"node_id": f"{phase}.x.{k}", "input_path": f"$.objects.{phase}.{k}", "field_key": k,
                           "action": "select_single" if k != "profile_name" else "fill_text", "expected_value": v}
                          for k, v in values.items()]}

    def begin(**values: Any) -> SkillSession:
        return SkillSession.begin(config=config, page=None, phase=phase, section=None, graph=graph(**values),
                                  leaves=leaves(**values), input_data={"objects": {phase: values}})

    steps: Dict[str, Any] = {}
    base = {"profile_name": "SELF_CHECK_NAME", "interface_type": "SFTP HAFT"}
    first = begin(**base)
    steps["first_run_learns"] = first.plan == "learn"
    g = graph(**base)
    execution = {"attempts": [{"node_id": n["node_id"], "input_path": n["input_path"], "success": True} for n in g["nodes"]]}
    first.stage(first.build_candidate(graph=g, final_execution=execution, cycles=[], controls_after=[], duration_seconds=10))
    steps["candidate_not_yet_knowledge"] = not (Path(memory_dir) / "form_structure_memory").exists()
    second = begin(**base)
    steps["next_run_replays_candidate"] = (second.plan, second.reason) == ("replay", "candidate_awaiting_replay_proof")
    steps["replay_certifies"] = second.record_replay(seconds=2.0, surface=[]).get("status") == "certified"
    third = begin(**dict(base, profile_name="OTHER"))
    steps["certified_replay"] = (third.plan, third.reason) == ("replay", "certified_skill")
    fourth = begin(**dict(base, as2_identifier="X"))
    steps["new_field_relearns"] = (fourth.plan, fourth.reason) == ("learn", "new_input_field")
    text = PortalSkillStore(Path(memory_dir)).file(phase).read_text(encoding="utf-8")
    steps["value_free"] = "SELF_CHECK_NAME" not in text and "SFTP HAFT" not in text
    return {"pass": all(steps.values()), "steps": steps}
