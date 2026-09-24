from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _strip_query(url: str) -> str:
    try:
        p = urlsplit(str(url or ""))
        return urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    except Exception:
        return str(url or "").split("?", 1)[0]


def _stable_id(*parts: Any) -> str:
    text = "|".join(_norm(x) for x in parts if str(x or "").strip())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


MUTATION_WORDS = {
    "delete", "deploy", "migrate", "publish", "remove", "enable", "disable",
    "archive", "submit", "save", "create", "update", "confirm",
    "approve", "reject", "activate", "deactivate", "assign", "unassign",
    "grant", "revoke", "lock", "unlock", "trigger", "execute", "send",
    "resubmit", "promote", "rollback", "restore", "restart", "start", "stop",
}
DRAFT_WORDS = {"edit", "clone", "copy", "duplicate", "configure", "modify"}
READ_WORDS = {"view", "details", "detail", "open", "expand", "collapse", "search", "filter", "show"}


def classify_risk(label: str, *, method: str = "") -> str:
    text = _norm(label)
    method_u = str(method or "").upper()
    if method_u in {"POST", "PUT", "PATCH", "DELETE"}:
        return "mutation"
    if any(word in text for word in MUTATION_WORDS):
        return "mutation"
    if any(word in text for word in DRAFT_WORDS):
        return "draft"
    return "read"


def capability_id(page_family: str, kind: str, label: str, scope: str = "") -> str:
    return f"cap-{_stable_id(page_family, kind, label, scope)}"


class HIPCapabilityGraph:
    """Persistent, value-free knowledge of what the HIP Portal can do.

    The graph stores semantic controls/actions, parent/child relations, page
    fingerprints and observed API contracts.  It deliberately does not store
    customer values, cookies, authorization headers or browser profile data.
    """

    SCHEMA_VERSION = "hip.capability-graph.v7"

    def __init__(self, root: str | Path):
        root_path = Path(root)
        if root_path.suffix.lower() == ".json":
            self.path = root_path
        else:
            self.path = root_path / "capability_graph.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _empty(self) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": utc_now(),
            "pages": {},
            "capabilities": {},
            "api_contracts": {},
            "replay_profiles": {},
            "task_replay_profiles": {},
            "relations": [],
            "source_runs": [],
            "bootstrap_sources": {},
            "values_stored": False,
        }

    def _load(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return self._empty()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return self._empty()
            raw.setdefault("pages", {})
            raw.setdefault("capabilities", {})
            raw.setdefault("api_contracts", {})
            raw.setdefault("replay_profiles", {})
            raw.setdefault("task_replay_profiles", {})
            raw.setdefault("relations", [])
            raw.setdefault("source_runs", [])
            raw.setdefault("bootstrap_sources", {})
            raw["schema_version"] = self.SCHEMA_VERSION
            raw["values_stored"] = False
            return raw
        except Exception:
            return self._empty()

    def save(self) -> str:
        self.data["updated_at"] = utc_now()
        self.data["values_stored"] = False
        safe_write_json(self.path, mask_sensitive_data(self.data))
        return str(self.path)

    def observe_page(self, *, page_family: str, url: str, title: str = "", fingerprint: str = "", run_id: str = "", evidence: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        family = _norm(page_family)
        page = self.data["pages"].setdefault(family, {
            "page_family": family,
            "urls": [],
            "titles": [],
            "fingerprints": [],
            "capability_ids": [],
            "api_contract_ids": [],
            "last_seen_at": "",
            "observations": 0,
        })
        safe_url = _strip_query(url)
        if safe_url and safe_url not in page["urls"]:
            page["urls"].append(safe_url)
        if title and title not in page["titles"]:
            page["titles"].append(mask_sensitive_string(title))
        if fingerprint and fingerprint not in page["fingerprints"]:
            page["fingerprints"].append(str(fingerprint))
            page["fingerprints"] = page["fingerprints"][-20:]
        page["last_seen_at"] = utc_now()
        page["observations"] = int(page.get("observations") or 0) + 1
        if run_id and run_id not in self.data["source_runs"]:
            self.data["source_runs"].append(run_id)
            self.data["source_runs"] = self.data["source_runs"][-100:]
        if evidence:
            page["latest_evidence"] = mask_sensitive_data(dict(evidence))
        return page

    def observe_capability(
        self,
        *,
        page_family: str,
        kind: str,
        label: str,
        selector: str = "",
        role: str = "",
        placeholder: str = "",
        scope: str = "",
        section: str = "",
        parent_capability_id: str = "",
        risk: str = "",
        revealed_by: str = "",
        evidence: Optional[Mapping[str, Any]] = None,
        run_id: str = "",
        knowledge_source: str = "live",
        trust: str = "observed",
        verified: Optional[bool] = None,
    ) -> Dict[str, Any]:
        family = _norm(page_family)
        kind_n = _norm(kind) or "control"
        label_clean = re.sub(r"\s+", " ", str(label or "")).strip()[:500]
        scope_clean = re.sub(r"\s+", " ", str(scope or "")).strip()[:500]
        cid = capability_id(family, kind_n, label_clean or role or selector, scope_clean)
        cap = self.data["capabilities"].setdefault(cid, {
            "capability_id": cid,
            "page_family": family,
            "kind": kind_n,
            "label": label_clean,
            "selectors": [],
            "roles": [],
            "placeholders": [],
            "scopes": [],
            "sections": [],
            "risk": risk or classify_risk(label_clean),
            "revealed_by": [],
            "parent_capability_ids": [],
            "api_contract_ids": [],
            "observations": 0,
            "success_observations": 0,
            "last_seen_at": "",
            "knowledge_source": knowledge_source or "live",
            "trust": trust or "observed",
            "verified": bool(verified) if verified is not None else False,
            "values_stored": False,
        })
        for key, value in (("selectors", selector), ("roles", role), ("placeholders", placeholder), ("scopes", scope_clean), ("sections", section)):
            if value and value not in cap[key]:
                cap[key].append(mask_sensitive_string(str(value)))
                cap[key] = cap[key][-20:]
        if revealed_by and revealed_by not in cap["revealed_by"]:
            cap["revealed_by"].append(revealed_by)
        if parent_capability_id and parent_capability_id not in cap["parent_capability_ids"]:
            cap["parent_capability_ids"].append(parent_capability_id)
        cap["risk"] = risk or cap.get("risk") or classify_risk(label_clean)
        if knowledge_source:
            cap["knowledge_source"] = str(knowledge_source)
        if trust:
            cap["trust"] = str(trust)
        if verified is not None:
            cap["verified"] = bool(cap.get("verified") or verified)
        cap["observations"] = int(cap.get("observations") or 0) + 1
        cap["last_seen_at"] = utc_now()
        if evidence:
            cap["latest_evidence"] = mask_sensitive_data(dict(evidence))
        page = self.observe_page(page_family=family, url=str((evidence or {}).get("url") or ""), run_id=run_id)
        if cid not in page["capability_ids"]:
            page["capability_ids"].append(cid)
        return cap

    def mark_success(self, capability_id_value: str) -> None:
        cap = self.data.get("capabilities", {}).get(capability_id_value)
        if isinstance(cap, dict):
            cap["success_observations"] = int(cap.get("success_observations") or 0) + 1
            cap["last_success_at"] = utc_now()
            cap["verified"] = True
            cap["trust"] = "validated_live"
            cap["knowledge_source"] = "live_judge_approved"

    def add_relation(self, *, source: str, relation: str, target: str, evidence: Optional[Mapping[str, Any]] = None) -> None:
        if not source or not relation or not target:
            return
        row = {"source": source, "relation": _norm(relation), "target": target}
        key = (row["source"], row["relation"], row["target"])
        for existing in self.data["relations"]:
            if (existing.get("source"), existing.get("relation"), existing.get("target")) == key:
                existing["observations"] = int(existing.get("observations") or 1) + 1
                existing["last_seen_at"] = utc_now()
                if evidence:
                    existing["latest_evidence"] = mask_sensitive_data(dict(evidence))
                return
        row.update({"observations": 1, "last_seen_at": utc_now(), "latest_evidence": mask_sensitive_data(dict(evidence or {}))})
        self.data["relations"].append(row)

    def observe_api_contract(
        self,
        *,
        page_family: str,
        method: str,
        url: str,
        request_shape: Any = None,
        response_status: Optional[int] = None,
        response_shape: Any = None,
        stage: str = "",
        caused_by_capability_id: str = "",
        evidence: Optional[Mapping[str, Any]] = None,
        knowledge_source: str = "live",
        trust: str = "observed",
        verified: Optional[bool] = None,
    ) -> Dict[str, Any]:
        family = _norm(page_family)
        method_u = str(method or "GET").upper()
        endpoint = _strip_query(url)
        contract_id = f"api-{_stable_id(method_u, endpoint)}"
        row = self.data["api_contracts"].setdefault(contract_id, {
            "api_contract_id": contract_id,
            "method": method_u,
            "endpoint": endpoint,
            "risk": classify_risk(endpoint, method=method_u),
            "page_families": [],
            "stages": [],
            "response_statuses": [],
            "caused_by_capability_ids": [],
            "observations": 0,
            "last_seen_at": "",
            "knowledge_source": knowledge_source or "live",
            "trust": trust or "observed",
            "verified": bool(verified) if verified is not None else False,
            "values_stored": False,
        })
        if family and family not in row["page_families"]:
            row["page_families"].append(family)
        if stage and stage not in row["stages"]:
            row["stages"].append(stage)
        if response_status is not None and response_status not in row["response_statuses"]:
            row["response_statuses"].append(int(response_status))
        if caused_by_capability_id and caused_by_capability_id not in row["caused_by_capability_ids"]:
            row["caused_by_capability_ids"].append(caused_by_capability_id)
        if request_shape is not None:
            row["latest_request_shape"] = mask_sensitive_data(request_shape)
        if response_shape is not None:
            row["latest_response_shape"] = mask_sensitive_data(response_shape)
        if evidence:
            row["latest_evidence"] = mask_sensitive_data(dict(evidence))
        if knowledge_source:
            row["knowledge_source"] = str(knowledge_source)
        if trust:
            row["trust"] = str(trust)
        if verified is not None:
            row["verified"] = bool(row.get("verified") or verified)
        row["observations"] = int(row.get("observations") or 0) + 1
        row["last_seen_at"] = utc_now()
        page = self.observe_page(page_family=family, url="")
        if contract_id not in page["api_contract_ids"]:
            page["api_contract_ids"].append(contract_id)
        if caused_by_capability_id:
            cap = self.data["capabilities"].get(caused_by_capability_id)
            if isinstance(cap, dict) and contract_id not in cap["api_contract_ids"]:
                cap["api_contract_ids"].append(contract_id)
            self.add_relation(source=caused_by_capability_id, relation="causes_api", target=contract_id)
        return row


    def bootstrap_from_unified_kb(self, kb_path: str | Path, *, force: bool = False) -> Dict[str, Any]:
        """Seed the user-visible capability/API graph from reviewed canonical KB.

        This makes Capabilities and API Contracts useful before the first live run.
        The seed is value-free and clearly marked canonical/unverified-live; live
        judge-approved observations later promote entries to ``validated_live``.
        """
        path = Path(kb_path)
        if not path.is_file():
            return {"status": "missing", "path": str(path), "capabilities_seeded": 0, "apis_seeded": 0}
        try:
            raw_bytes = path.read_bytes()
            digest = hashlib.sha256(raw_bytes).hexdigest()
            kb = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            return {"status": "invalid", "path": str(path), "error": mask_sensitive_string(str(exc))}
        sources = self.data.setdefault("bootstrap_sources", {})
        source_key = "unified_deep_kb"
        previous = sources.get(source_key) if isinstance(sources.get(source_key), dict) else {}
        if not force and previous.get("sha256") == digest:
            return {
                "status": "already_seeded", "path": str(path), "sha256": digest,
                "capabilities_seeded": int(previous.get("capabilities_seeded") or 0),
                "apis_seeded": int(previous.get("apis_seeded") or 0),
            }

        phase_family = {
            "data_map": "data_maps",
            "source_document_type": "document_types",
            "target_document_type": "document_types",
            "rule": "rules",
            "source_transport_profile": "transport_profiles",
            "target_transport_profile": "transport_profiles",
            "biz_flow": "bizflows",
        }
        cap_ids: set[str] = set()
        page_models = kb.get("portal_page_models") if isinstance(kb, dict) else {}
        if isinstance(page_models, dict):
            for phase, model in page_models.items():
                if not isinstance(model, dict):
                    continue
                family = phase_family.get(str(phase), _norm(phase))
                self.observe_page(
                    page_family=family,
                    url=str(model.get("url_pattern") or ""),
                    title=str(model.get("portal_path") or ""),
                    evidence={"knowledge_source": "canonical_kb", "phase": phase},
                )
                for field in model.get("fields") or []:
                    if not isinstance(field, dict):
                        continue
                    hints = [str(x) for x in (field.get("selector_hints") or []) if str(x)]
                    cap = self.observe_capability(
                        page_family=family,
                        kind="form_field",
                        label=str(field.get("label") or field.get("input_json_key") or "form field"),
                        selector=hints[0] if hints else "",
                        scope=str(field.get("input_json_key") or ""),
                        section=str(model.get("portal_path") or ""),
                        evidence={
                            "knowledge_source": "canonical_kb",
                            "phase": phase,
                            "action": field.get("action") or "",
                            "selector_hints": hints[:8],
                            "values_stored": False,
                        },
                        knowledge_source="canonical_kb",
                        trust="canonical",
                        verified=False,
                    )
                    cap["action"] = str(field.get("action") or "")
                    cap["input_json_key"] = str(field.get("input_json_key") or "")
                    cap_ids.add(str(cap.get("capability_id") or ""))

        api_count = 0
        api_catalog = kb.get("agentic_api_catalog") if isinstance(kb, dict) else {}
        api_rows: list[tuple[str, str]] = []
        if isinstance(api_catalog, dict):
            for section in ("orchestration_preferred", "underlying_apis"):
                rows = api_catalog.get(section)
                if not isinstance(rows, dict):
                    continue
                for name, spec in rows.items():
                    text = str(spec or "").strip()
                    match = re.match(r"^(GET|POST|PUT|PATCH|DELETE)\s+(.+)$", text, re.I)
                    if match:
                        api_rows.append((str(name), f"{match.group(1).upper()} {match.group(2).strip()}"))
        for name, spec in api_rows:
            method, endpoint = spec.split(" ", 1)
            name_n = _norm(name)
            if "document_type" in name_n:
                families = ["document_types"]
            elif name_n in {"rule"} or "rule" in name_n:
                families = ["rules"]
            elif name_n in {"map"} or name_n.startswith("map_"):
                families = ["data_maps"]
            elif name_n.startswith("tp_") or "transport" in name_n:
                families = ["transport_profiles"]
            elif name_n.startswith("flow_"):
                families = ["bizflows"]
            elif name_n in {"partner", "account"}:
                families = ["transport_profiles", "bizflows"]
            else:
                families = ["workflow"]
            for family in families:
                row = self.observe_api_contract(
                    page_family=family,
                    method=method,
                    url=endpoint,
                    stage="canonical_contract",
                    evidence={"knowledge_source": "canonical_kb", "catalog_key": name},
                    knowledge_source="canonical_kb",
                    trust="canonical_contract",
                    verified=False,
                )
                row["catalog_key"] = name
                api_count += 1

        sources[source_key] = {
            "path": str(path), "sha256": digest, "seeded_at": utc_now(),
            "capabilities_seeded": len([x for x in cap_ids if x]), "apis_seeded": api_count,
            "values_stored": False,
        }
        self.save()
        return {"status": "seeded", **sources[source_key]}


    def observe_replay_profile(
        self,
        *,
        page_family: str,
        name: str,
        steps: Iterable[Mapping[str, Any]],
        preconditions: Optional[Iterable[str]] = None,
        verified: bool = False,
        evidence: Optional[Mapping[str, Any]] = None,
        run_id: str = "",
    ) -> Dict[str, Any]:
        """Persist a value-free deterministic replay path for a learned capability flow.

        Replay profiles store only structural action/capability references and wait
        contracts. Runtime entity values still come from the current task/input.
        """
        family = _norm(page_family)
        name_n = _norm(name) or "replay"
        replay_id = f"replay-{_stable_id(family, name_n)}"
        clean_steps: List[Dict[str, Any]] = []
        for raw in steps:
            if not isinstance(raw, Mapping):
                continue
            row = {
                "type": _norm(raw.get("type") or "action"),
                "capability_id": str(raw.get("capability_id") or ""),
                "action": _norm(raw.get("action") or ""),
                "value_source": str(raw.get("value_source") or ""),
                "wait_for": [str(x) for x in (raw.get("wait_for") or []) if str(x)],
                "scope": str(raw.get("scope") or ""),
                # Structural form-replay metadata. These fields point to the current
                # runtime input and dependency graph; they never persist field values.
                "input_path": str(raw.get("input_path") or ""),
                "section": str(raw.get("section") or ""),
                "row_kind": str(raw.get("row_kind") or ""),
                "row_index": raw.get("row_index") if isinstance(raw.get("row_index"), int) else None,
                "depends_on": [str(x) for x in (raw.get("depends_on") or []) if str(x)],
                "verification": str(raw.get("verification") or ""),
            }
            clean_steps.append(mask_sensitive_data(row))
        row = self.data["replay_profiles"].setdefault(replay_id, {
            "replay_profile_id": replay_id,
            "page_family": family,
            "name": name_n,
            "steps": [],
            "preconditions": [],
            "verified": False,
            "observations": 0,
            "successful_observations": 0,
            "last_seen_at": "",
            "values_stored": False,
        })
        row["steps"] = clean_steps
        row["preconditions"] = sorted(set(str(x) for x in (preconditions or []) if str(x)))
        row["verified"] = bool(row.get("verified") or verified)
        row["observations"] = int(row.get("observations") or 0) + 1
        if verified:
            row["successful_observations"] = int(row.get("successful_observations") or 0) + 1
        row["last_seen_at"] = utc_now()
        if evidence:
            row["latest_evidence"] = mask_sensitive_data(dict(evidence))
        if run_id and run_id not in self.data["source_runs"]:
            self.data["source_runs"].append(run_id)
            self.data["source_runs"] = self.data["source_runs"][-100:]
        return row

    def replay_profiles(self, *, page_family: str = "", verified_only: bool = False) -> List[Dict[str, Any]]:
        family = _norm(page_family)
        rows: List[Dict[str, Any]] = []
        for row in self.data.get("replay_profiles", {}).values():
            if family and row.get("page_family") != family:
                continue
            if verified_only and not bool(row.get("verified")):
                continue
            rows.append(mask_sensitive_data(dict(row)))
        rows.sort(key=lambda x: (-int(x.get("successful_observations") or 0), -int(x.get("observations") or 0), str(x.get("name") or "")))
        return rows


    def observe_task_replay_profile(
        self,
        *,
        name: str,
        steps: Iterable[Mapping[str, Any]],
        families: Optional[Iterable[str]] = None,
        preconditions: Optional[Iterable[str]] = None,
        verified: bool = False,
        evidence: Optional[Mapping[str, Any]] = None,
        run_id: str = "",
    ) -> Dict[str, Any]:
        """Persist a cross-family, value-free future-task replay trajectory.

        The profile stores only capability IDs, family/action semantics and runtime
        value sources such as ``current_task.entity``. It never stores the entity
        value that happened to be used when the trajectory was learned.
        """
        name_n = _norm(name) or "future_task"
        replay_id = f"task-replay-{_stable_id(name_n, '|'.join(sorted(_norm(x) for x in (families or []) if x)))}"
        clean_steps: List[Dict[str, Any]] = []
        inferred_families: List[str] = []
        for raw in steps:
            if not isinstance(raw, Mapping):
                continue
            family = _norm(raw.get("page_family") or "")
            if family and family not in inferred_families:
                inferred_families.append(family)
            row = {
                "type": _norm(raw.get("type") or "action"),
                "page_family": family,
                "capability_id": str(raw.get("capability_id") or ""),
                "action": _norm(raw.get("action") or ""),
                "value_source": str(raw.get("value_source") or ""),
                "scope": str(raw.get("scope") or ""),
                "wait_for": [str(x) for x in (raw.get("wait_for") or []) if str(x)],
                "risk": str(raw.get("risk") or ""),
                "verification": str(raw.get("verification") or ""),
            }
            clean_steps.append(mask_sensitive_data(row))
        family_list = sorted(set(_norm(x) for x in (families or inferred_families) if _norm(x)))
        row = self.data["task_replay_profiles"].setdefault(replay_id, {
            "task_replay_profile_id": replay_id,
            "name": name_n,
            "families": family_list,
            "steps": [],
            "preconditions": [],
            "verified": False,
            "observations": 0,
            "successful_observations": 0,
            "last_seen_at": "",
            "values_stored": False,
        })
        row["families"] = family_list
        row["steps"] = clean_steps
        row["preconditions"] = sorted(set(str(x) for x in (preconditions or []) if str(x)))
        row["verified"] = bool(row.get("verified") or verified)
        row["observations"] = int(row.get("observations") or 0) + 1
        if verified:
            row["successful_observations"] = int(row.get("successful_observations") or 0) + 1
        row["last_seen_at"] = utc_now()
        if evidence:
            row["latest_evidence"] = mask_sensitive_data(dict(evidence))
        if run_id and run_id not in self.data["source_runs"]:
            self.data["source_runs"].append(run_id)
            self.data["source_runs"] = self.data["source_runs"][-100:]
        return row

    def task_replay_profiles(self, *, verified_only: bool = False) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for row in self.data.get("task_replay_profiles", {}).values():
            if verified_only and not bool(row.get("verified")):
                continue
            rows.append(mask_sensitive_data(dict(row)))
        rows.sort(key=lambda x: (-int(x.get("successful_observations") or 0), -int(x.get("observations") or 0), str(x.get("name") or "")))
        return rows

    def page_summary(self, page_family: str) -> Dict[str, Any]:
        family = _norm(page_family)
        page = dict(self.data.get("pages", {}).get(family) or {})
        caps = [self.data["capabilities"][cid] for cid in page.get("capability_ids", []) if cid in self.data["capabilities"]]
        apis = [self.data["api_contracts"][aid] for aid in page.get("api_contract_ids", []) if aid in self.data["api_contracts"]]
        page["capabilities"] = caps
        page["api_contracts"] = apis
        return mask_sensitive_data(page)

    def query_capabilities(self, *, page_family: str = "", text: str = "", risk: str = "") -> List[Dict[str, Any]]:
        family = _norm(page_family)
        needle = _norm(text)
        out: List[Dict[str, Any]] = []
        for cap in self.data.get("capabilities", {}).values():
            if family and cap.get("page_family") != family:
                continue
            if risk and cap.get("risk") != risk:
                continue
            hay = _norm(" ".join([str(cap.get("label") or ""), " ".join(cap.get("roles") or []), " ".join(cap.get("sections") or []), " ".join(cap.get("scopes") or [])]))
            if needle and needle not in hay:
                continue
            out.append(mask_sensitive_data(dict(cap)))
        out.sort(key=lambda x: (-int(x.get("success_observations") or 0), -int(x.get("observations") or 0), str(x.get("label") or "")))
        return out

    def manifest(self) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "path": str(self.path),
            "page_count": len(self.data.get("pages", {})),
            "capability_count": len(self.data.get("capabilities", {})),
            "api_contract_count": len(self.data.get("api_contracts", {})),
            "replay_profile_count": len(self.data.get("replay_profiles", {})),
            "task_replay_profile_count": len(self.data.get("task_replay_profiles", {})),
            "relation_count": len(self.data.get("relations", [])),
            "source_run_count": len(self.data.get("source_runs", [])),
            "bootstrap_source_count": len(self.data.get("bootstrap_sources", {})),
            "values_stored": False,
            "updated_at": self.data.get("updated_at"),
        }

PHASE_TO_CAPABILITY_FAMILY = {
    "data_map": "data_maps",
    "source_document_type": "document_types",
    "target_document_type": "document_types",
    "rule": "rules",
    "source_transport_profile": "transport_profiles",
    "target_transport_profile": "transport_profiles",
    "biz_flow": "bizflows",
}


def promote_validated_phase_to_capability_graph(
    *,
    graph: HIPCapabilityGraph,
    phase: str,
    phase_dir: str | Path,
    trajectory: Mapping[str, Any],
    run_id: str,
) -> Dict[str, Any]:
    """Promote judge-validated form topology/API evidence into HIP capabilities."""
    family = PHASE_TO_CAPABILITY_FAMILY.get(str(phase), _norm(phase))
    phase_dir = Path(phase_dir)
    graph.observe_page(
        page_family=family,
        url=str(trajectory.get("url_template") or ""),
        fingerprint=str(trajectory.get("page_fingerprint") or ""),
        run_id=run_id,
        evidence={"trajectory_fingerprint": trajectory.get("trajectory_fingerprint"), "phase": phase},
    )
    node_caps: Dict[str, str] = {}
    for step in trajectory.get("ordered_steps") or []:
        if not isinstance(step, Mapping):
            continue
        profile = step.get("selector_profile") if isinstance(step.get("selector_profile"), Mapping) else {}
        label = str(profile.get("label") or step.get("field_key") or step.get("node_id") or "form field")
        selector = ""
        for candidate in profile.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            if candidate.get("strategy") in {"framework_key", "name", "role_and_accessible_name", "label_occurrence"} and candidate.get("selector"):
                selector = str(candidate.get("selector")); break
        if not selector:
            for candidate in profile.get("candidates") or []:
                if isinstance(candidate, Mapping) and candidate.get("selector"):
                    selector = str(candidate.get("selector")); break
        cap = graph.observe_capability(
            page_family=family,
            kind="form_field",
            label=label,
            selector=selector,
            role=str(profile.get("role") or ""),
            scope=str(profile.get("row_kind") or ""),
            section=str(profile.get("section") or step.get("section") or ""),
            run_id=run_id,
            evidence={
                "phase": phase,
                "node_id": step.get("node_id"),
                "framework_key": profile.get("framework_key"),
                "interaction_profile": step.get("interaction_profile") or {},
                "dependency_level": step.get("dependency_level"),
                "values_stored": False,
            },
        )
        graph.mark_success(cap["capability_id"])
        node_id = str(step.get("node_id") or "")
        if node_id:
            node_caps[node_id] = cap["capability_id"]
    for step in trajectory.get("ordered_steps") or []:
        if not isinstance(step, Mapping):
            continue
        target = node_caps.get(str(step.get("node_id") or ""))
        if not target:
            continue
        for parent in step.get("parent_node_ids") or []:
            source = node_caps.get(str(parent))
            if source:
                graph.add_relation(source=source, relation="parent_before_child", target=target, evidence={"phase": phase})

    api_files = []
    for pattern in ("*api_transactions*.json", "*payload_response*.json"):
        api_files.extend(phase_dir.rglob(pattern))
    seen_files = set()
    api_count = 0
    for path in api_files:
        key = str(path)
        if key in seen_files:
            continue
        seen_files.add(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = []
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, Mapping):
            for candidate_key in ("transactions", "api_transactions", "records", "items"):
                if isinstance(payload.get(candidate_key), list):
                    rows = payload.get(candidate_key); break
        for row in rows[:2000]:
            if not isinstance(row, Mapping):
                continue
            method = str(row.get("method") or row.get("request_method") or "GET")
            url = str(row.get("url") or row.get("endpoint") or row.get("request_url") or "")
            if not url:
                continue
            graph.observe_api_contract(
                page_family=family,
                method=method,
                url=url,
                request_shape=row.get("request_shape") or row.get("request_payload_shape"),
                response_status=row.get("status") or row.get("response_status"),
                response_shape=row.get("response_shape") or row.get("response_payload_shape"),
                stage=str(row.get("stage") or "validated_form"),
                caused_by_capability_id=str(row.get("caused_by_capability_id") or ""),
                evidence={"phase": phase, "source_file": path.name},
            )
            api_count += 1
    graph.save()
    return {
        "status": "promoted",
        "phase": phase,
        "page_family": family,
        "form_capability_count": len(node_caps),
        "api_observation_count": api_count,
        "graph": graph.manifest(),
        "values_stored": False,
    }
