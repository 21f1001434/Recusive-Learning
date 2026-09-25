"""Remember how a HIP form is built, so the next run starts from it (V243R17).

A successful autonomous run teaches three value-free things about a phase:

* **fields** -- which live control an input.json key the phase compiler does not
  know maps to: its label or radio/checkbox group label, section, row kind and
  action (fill, select, radio, checkbox group, switch);
* **sections** -- which collapsed sections had to be opened to reach input;
* **rows** -- which "+ Add ..." control creates rows of a repeatable list.

The next run seeds those fields into the goal graph before it looks at the page,
opens the learned sections first, and adds rows with the learned control.  Every
seeded field is still bound live and proven by exact read-back; a learned field
that no longer binds is dropped for the run and demoted, so the live page always
wins over memory.  Input values, selectors and coordinates are never stored.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .safe_io import safe_write_json
from .security import mask_sensitive_string

SCHEMA = "hip.form-structure-memory.v1"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def path_pattern(input_path: str) -> str:
    """``$.objects.<phase>.tags[1].key`` -> ``tags[*].key`` (phase-relative)."""
    parts = str(input_path or "").split(".")
    rel = ".".join(parts[3:]) if len(parts) > 3 and parts[0] == "$" else str(input_path or "")
    return re.sub(r"\[\d+\]", "[*]", rel)


def _mask_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _mask_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_strings(v) for v in value]
    if isinstance(value, str):
        return mask_sensitive_string(value)
    return value


def resolve_memory_dir(config: Any = None, page: Any = None) -> Optional[Path]:
    for source in (config, getattr(getattr(page, "_hip_browser_session", None), "config", None)):
        reporting = getattr(source, "reporting", None) if source is not None else None
        memory_dir = getattr(reporting, "memory_dir", None) if reporting is not None else None
        if memory_dir:
            return Path(memory_dir)
    return None


class FormStructureMemory:
    def __init__(self, root: Path):
        self.root = Path(root) / "form_structure_memory"

    @classmethod
    def for_run(cls, config: Any = None, page: Any = None) -> Optional["FormStructureMemory"]:
        memory_dir = resolve_memory_dir(config, page)
        return cls(memory_dir) if memory_dir is not None else None

    def _file(self, phase: str) -> Path:
        return self.root / f"{re.sub(r'[^a-z0-9_]+', '_', str(phase or 'unknown').lower())}.json"

    def load(self, phase: str) -> Dict[str, Any]:
        try:
            data = json.loads(self._file(phase).read_text(encoding="utf-8")) if self._file(phase).exists() else {}
        except Exception:
            data = {}
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
            data = {"schema_version": SCHEMA, "phase": phase, "fields": {}, "sections": {}, "rows": {}, "runs": 0}
        for key in ("fields", "sections", "rows"):
            data.setdefault(key, {})
        return data

    def save(self, phase: str, data: Dict[str, Any]) -> None:
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Mask secret-looking *strings* only: key-based masking would blank a
        # learned field whose input key merely contains "token" or "session".
        safe_write_json(self._file(phase), _mask_strings(data), mask=False)

    # ------------------------------------------------------------------ learn
    @staticmethod
    def extract_learning(
        *, graph: Dict[str, Any], final_execution: Dict[str, Any], cycles: Sequence[Dict[str, Any]],
        memory_reveal: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """What one proven run teaches (value-free), without saving it (V243R19).

        ``portal_skills`` keeps this with the candidate skill and merges it into
        memory only once a deterministic replay has certified the skill.
        """
        proven = {
            str(a.get("node_id")) for a in (final_execution.get("attempts") or [])
            if isinstance(a, dict) and a.get("success") is True
        }
        groups: Dict[str, Dict[str, Any]] = {}
        for node in graph.get("nodes") or []:
            if not isinstance(node, dict) or ".runtime_input." not in str(node.get("node_id") or ""):
                continue
            if str(node.get("node_id")) not in proven:
                continue
            pattern = path_pattern(str(node.get("input_path") or ""))
            loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
            if node.get("choice_option"):
                # One checkbox of a group: remember the group once, with its options.
                entry = groups.setdefault(pattern, {
                    "pattern": pattern, "field_key": node.get("field_key"), "action": "select_checkbox_group",
                    "group_label": node.get("choice_group") or "", "options": [], "section": node.get("section"),
                    "section_aliases": list(dict.fromkeys(loc.get("section_aliases") or [])), "names": list(dict.fromkeys(loc.get("names") or [])),
                    "row_kind": node.get("row_kind") or "",
                })
                if node["choice_option"] not in entry["options"]:
                    entry["options"].append(node["choice_option"])
                continue
            groups[pattern] = {
                "pattern": pattern, "field_key": node.get("field_key"), "action": node.get("action"),
                "group_label": node.get("choice_group") or "",
                "labels": list(dict.fromkeys(x for x in (loc.get("labels") or []) if str(x or "").strip()))[:4],
                "names": list(dict.fromkeys(loc.get("names") or []))[:4], "placeholders": list(dict.fromkeys(loc.get("placeholders") or []))[:2],
                "roles": list(loc.get("roles") or [])[:2], "section": node.get("section"),
                "options": list(node.get("choice_options") or [])[:12],
                "section_aliases": list(loc.get("section_aliases") or [])[:3], "row_kind": node.get("row_kind") or "",
            }
        sections: Dict[str, Dict[str, Any]] = {}
        rows: Dict[str, Dict[str, Any]] = {}
        heals: List[Any] = [{"reveal": memory_reveal}] if memory_reveal else []
        for cycle in cycles or []:
            heals.extend([cycle.get("structure_heal"), cycle.get("structure_heal_mid")])
        for heal in heals:
            if not isinstance(heal, dict):
                continue
            for step in ((heal.get("reveal") or {}).get("expanded") or []):
                if step.get("expanded") and step.get("matched_input") and step.get("title"):
                    sections[_norm(step["title"])] = {"title": step["title"]}
            for group in ((heal.get("rows") or {}).get("groups") or []):
                clicks = [c for c in (group.get("clicks") or []) if c.get("clicked") and int(c.get("rows_after") or 0) > int(c.get("rows_before") or 0)]
                if not clicks:
                    continue
                key = str(group.get("group") or "")
                key = key if key.startswith("kind:") else f"list:{path_pattern(key[len('list:'):])}"
                rows[key] = {"add_label": clicks[-1].get("label")}
        return _mask_strings({"fields": groups, "sections": sections, "rows": rows})

    def merge_learning(self, phase: str, learning: Dict[str, Any]) -> Dict[str, Any]:
        """Add one proven run's learning to the phase memory and save it."""
        data = self.load(phase)
        fields = dict(learning.get("fields") or {})
        sections = dict(learning.get("sections") or {})
        rows = dict(learning.get("rows") or {})
        for pattern, entry in fields.items():
            old = data["fields"].get(pattern) or {}
            entry = dict(entry)
            entry["success_count"] = int(old.get("success_count") or 0) + 1
            entry["failure_count"] = int(old.get("failure_count") or 0)
            data["fields"][pattern] = entry
        for key, entry in sections.items():
            old = data["sections"].get(key) or {}
            data["sections"][key] = {"title": entry.get("title"), "success_count": int(old.get("success_count") or 0) + 1}
        for key, entry in rows.items():
            old = data["rows"].get(key) or {}
            data["rows"][key] = {"add_label": entry.get("add_label"), "success_count": int(old.get("success_count") or 0) + 1}
        summary = {"learned_fields": len(fields), "learned_sections": len(sections), "learned_rows": len(rows), "file": str(self._file(phase))}
        if not (fields or sections or rows) and not self._file(phase).exists():
            # A form fully covered by the phase compiler teaches nothing new.
            return dict(summary, file="")
        data["runs"] = int(data.get("runs") or 0) + 1
        self.save(phase, data)
        return summary

    def record_success(
        self, phase: str, *, graph: Dict[str, Any], final_execution: Dict[str, Any], cycles: Sequence[Dict[str, Any]],
        memory_reveal: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.merge_learning(phase, self.extract_learning(
            graph=graph, final_execution=final_execution, cycles=cycles, memory_reveal=memory_reveal,
        ))

    def demote(self, phase: str, patterns: Sequence[str]) -> None:
        if not patterns:
            return
        data = self.load(phase)
        for pattern in patterns:
            entry = data["fields"].get(pattern)
            if entry is None:
                continue
            entry["failure_count"] = int(entry.get("failure_count") or 0) + 1
            if entry["failure_count"] > int(entry.get("success_count") or 0):
                data["fields"].pop(pattern, None)
        self.save(phase, data)

    # ------------------------------------------------------------------ reuse
    def section_titles(self, phase: str) -> List[str]:
        return [str(v.get("title")) for v in self.load(phase)["sections"].values() if v.get("title")]

    def seed_graph(
        self, phase: str, graph: Dict[str, Any], leaves: Sequence[Dict[str, Any]], *, section: Optional[str] = None,
        option_mapper: Optional[Callable[[Any, Sequence[str]], Optional[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """Add learned nodes for input leaves the graph does not cover yet."""
        return seed_fields(self.load(phase)["fields"], phase, graph, leaves, section=section, option_mapper=option_mapper)


def seed_fields(
    fields: Dict[str, Any], phase: str, graph: Dict[str, Any], leaves: Sequence[Dict[str, Any]], *, section: Optional[str] = None,
    option_mapper: Optional[Callable[[Any, Sequence[str]], Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """Add nodes for input leaves the graph does not cover, from learned ``fields``."""
    if not fields:
        return []
    nodes = graph.setdefault("nodes", [])
    known_paths = {str(n.get("input_path") or "") for n in nodes if isinstance(n, dict)}
    added: List[Dict[str, Any]] = []
    for leaf in leaves:
        path = str(leaf.get("input_path") or "")
        if not path or path in known_paths:
            continue
        entry = fields.get(path_pattern(path))
        if not entry:
            continue
        match = re.findall(r"\[(\d+)\]", path)
        row_index = int(match[-1]) if match else None
        safe_id = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
        base = {
            "phase": phase, "section": section or entry.get("section") or "", "field_key": entry.get("field_key"),
            "input_path": path, "row_kind": entry.get("row_kind") or "", "row_index": row_index,
            "required": True, "depends_on": [], "verification": "exact_committed_control_value",
            "executor": "form-structure-memory", "learned_from_memory": True,
            "notes": "Seeded from value-free form structure memory; bound live and proven by exact read-back.",
        }
        locator = {
            "names": list(entry.get("names") or []), "placeholders": list(entry.get("placeholders") or []),
            "roles": list(entry.get("roles") or []),
            "section_aliases": [x for x in [entry.get("section"), *(entry.get("section_aliases") or [])] if x],
            "row_kind": entry.get("row_kind") or "", "row_index": row_index,
        }
        value = leaf.get("value")
        if entry.get("action") == "select_checkbox_group":
            wanted = {_norm(v) for v in (value if isinstance(value, list) else re.split(r"\s*,\s*", str(value or ""))) if _norm(v)}
            options = [str(o) for o in entry.get("options") or []]
            if not wanted <= {_norm(o) for o in options}:
                continue
            for option in options:
                node = dict(base, node_id=f"{phase}.runtime_input.{safe_id}.{''.join(ch for ch in option.lower() if ch.isalnum())[:24]}",
                            action="toggle", expected_value="true" if _norm(option) in wanted else "false",
                            choice_group=entry.get("group_label") or "", choice_option=option,
                            semantic_locator=dict(locator, labels=[option, entry.get("group_label") or ""]))
                nodes.append(node)
                added.append({"input_path": path, "node_id": node["node_id"], "action": "toggle", "option": option})
        else:
            expected = value
            if entry.get("action") == "select_radio" and entry.get("options") and option_mapper is not None:
                # true/"Y" -> the learned option label ("Yes").
                expected = option_mapper(value, entry.get("options") or []) or value
            node = dict(base, node_id=f"{phase}.runtime_input.{safe_id}", action=entry.get("action") or "fill_text",
                        expected_value=expected, choice_group=entry.get("group_label") or "",
                        semantic_locator=dict(locator, labels=list(entry.get("labels") or [])))
            nodes.append(node)
            added.append({"input_path": path, "node_id": node["node_id"], "action": node["action"]})
        known_paths.add(path)
    return added
