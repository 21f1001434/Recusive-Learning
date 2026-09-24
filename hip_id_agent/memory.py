from __future__ import annotations

import json
import os
import time
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import ExtractedID, utc_now
from .security import mask_sensitive_data


class JsonFileStore:
    def __init__(self, path: str | Path, default: Any = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.default = {} if default is None else default
        if not self.path.exists():
            self.write(self.default)

    def read(self) -> Any:
        with self._lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8") or json.dumps(self.default))
            except json.JSONDecodeError:
                backup = self.path.with_suffix(self.path.suffix + ".corrupt")
                self.path.rename(backup)
                self.write(self.default)
                return {"_warning": f"Corrupt file moved to {backup}"}

    def write(self, data: Any) -> None:
        """Write JSON robustly on Windows/OneDrive managed folders.

        The live Dell laptop run showed intermittent ``PermissionError`` /
        ``[WinError 5] Access is denied`` during ``Path.replace`` for files
        under OneDrive-synced project folders. That should not fail the whole
        portal extraction after IDs were already discovered. We still prefer an
        atomic temp-file replace, but retry and fall back to direct overwrite
        when the filesystem blocks the rename.
        """
        payload = json.dumps(mask_sensitive_data(data), indent=2, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            last_error: Exception | None = None
            for attempt in range(6):
                try:
                    tmp.write_text(payload, encoding="utf-8")
                    os.replace(str(tmp), str(self.path))
                    return
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.10 * (attempt + 1))
                except OSError as exc:
                    last_error = exc
                    time.sleep(0.05 * (attempt + 1))
            # Final fallback: direct overwrite. This is less atomic, but it keeps
            # the run from being marked failed solely because OneDrive/AV blocked
            # the temp-file rename. If even this fails, raise the original context.
            try:
                self.path.write_text(payload, encoding="utf-8")
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                return
            except Exception as direct_exc:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                raise RuntimeError(f"Could not write JSON store {self.path}: {last_error or direct_exc}") from direct_exc


class HipMemory:
    """Local memory. No custom MCP server. Data is JSON so it is inspectable and portable."""

    def __init__(self, memory_dir: str | Path):
        self.root = Path(memory_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects = JsonFileStore(self.root / "object_registry.json", default={})
        self.entity_index = JsonFileStore(self.root / "entity_registry_index.json", default={})
        self.partners = JsonFileStore(self.root / "partners.json", default={"items": []})
        self.systems = JsonFileStore(self.root / "systems.json", default={"items": []})
        self.accounts = JsonFileStore(self.root / "accounts.json", default={"items": []})
        self.domains = JsonFileStore(self.root / "domains.json", default={"items": []})
        self.deployment_groups = JsonFileStore(self.root / "deployment_groups.json", default={"items": []})
        self.selectors = JsonFileStore(self.root / "selectors.json", default={})
        self.execution_history = JsonFileStore(self.root / "execution_history.json", default={})
        self.successful_runs = JsonFileStore(self.root / "successful_runs.json", default={})
        self.stage_schemas = JsonFileStore(self.root / "stage_schemas.json", default={})
        self.error_patterns = JsonFileStore(self.root / "error_patterns.json", default={})
        self.knowledge_graph = JsonFileStore(self.root / "knowledge_graph.json", default={"runs": {}, "updated_at": None})

    def save_extracted_id(self, customer: str, item: ExtractedID) -> None:
        data = self.objects.read()
        customer_key = customer.upper().strip()
        object_type = item.object_type.lower().strip()
        query_key = item.query.upper().strip()
        data.setdefault(customer_key, {})
        data[customer_key].setdefault(object_type, {})
        data[customer_key][object_type][query_key] = {
            "id": item.object_id,
            "name": item.name,
            "source": item.source,
            "confidence": item.confidence,
            "last_verified_at": utc_now(),
            "evidence": item.evidence,
        }
        self.objects.write(data)
        self.save_entity(item.object_type, item)

    def save_entity(self, object_type: str, item: ExtractedID) -> None:
        store = self._store_for_type(object_type)
        if not store:
            return
        data = store.read() or {"items": []}
        items: List[Dict[str, Any]] = data.get("items", [])
        key = f"{item.object_id}::{item.name or item.query}".lower()
        by_key = {f"{r.get('id','')}::{r.get('name') or r.get('query','')}".lower(): r for r in items}
        row = {
            "id": item.object_id,
            "name": item.name,
            "query": item.query,
            "source": item.source,
            "confidence": item.confidence,
            "last_verified_at": utc_now(),
            "evidence": item.evidence,
        }
        by_key[key] = {**by_key.get(key, {}), **row}
        data["items"] = list(by_key.values())
        data["updated_at"] = utc_now()
        store.write(data)
        self._update_entity_index()

    def save_many_entities(self, object_type: str, rows: List[ExtractedID]) -> Dict[str, int]:
        store = self._store_for_type(object_type)
        before = len(store.read().get("items", [])) if store else 0
        for row in rows:
            self.save_entity(object_type, row)
        after = len(store.read().get("items", [])) if store else 0
        return {"found": len(rows), "new_or_updated": len(rows), "total_before": before, "total_after": after}

    def get_id(self, customer: str, object_type: str, query: str) -> Optional[str]:
        data = self.objects.read()
        customer_key = customer.upper().strip()
        object_key = object_type.lower().strip()
        query_key = query.upper().strip()
        customer_bucket = data.get(customer_key, {}) or {}
        # Current nested shape: CUSTOMER -> object_type -> QUERY -> {id, ...}
        item = customer_bucket.get(object_key, {}).get(query_key)
        if item and item.get("id"):
            return str(item["id"])
        # Backward compatibility: old shape accidentally stored CUSTOMER -> QUERY -> {id, ...}.
        # Safety rule: legacy rows are usable only when they are explicitly typed and the
        # stored type exactly matches the requested object type. Untyped legacy rows are
        # intentionally ignored so a Partner ID can never be returned for a System lookup
        # or vice versa. Object-specific partners.json/systems.json fallback can still run.
        legacy = customer_bucket.get(query_key)
        if legacy and legacy.get("id"):
            legacy_type = str(legacy.get("object_type") or legacy.get("type") or "").lower().strip()
            if legacy_type and legacy_type == object_key:
                return str(legacy["id"])
        hit = self.find_entity(object_key, query)
        return str(hit["id"]) if hit and hit.get("id") else None

    def find_entity(self, object_type: str, name_or_query: str) -> Optional[Dict[str, Any]]:
        store = self._store_for_type(object_type)
        if not store:
            return None
        items = (store.read() or {}).get("items", [])
        target = name_or_query.strip().lower()
        if not target:
            return None
        for row in items:
            if str(row.get("name", "")).strip().lower() == target or str(row.get("query", "")).strip().lower() == target:
                return row
        for row in items:
            hay = " ".join(str(row.get(k, "")) for k in ["name", "query", "id"]).lower()
            if target in hay:
                return row
        return None

    def _store_for_type(self, object_type: str):
        object_type = (object_type or "").lower().strip()
        if object_type == "partner":
            return self.partners
        if object_type == "system":
            return self.systems
        if object_type == "account":
            return self.accounts
        if object_type == "domain":
            return self.domains
        if object_type in {"deployment_group", "deploymentgroup"}:
            return self.deployment_groups
        return None

    def record_run(self, run_id: str, summary: Dict[str, Any]) -> None:
        data = self.execution_history.read()
        data[run_id] = summary
        self.execution_history.write(data)

    def record_success(self, run_id: str, summary: Dict[str, Any]) -> None:
        data = self.successful_runs.read()
        data[run_id] = summary
        self.successful_runs.write(data)

    def record_knowledge_graph(self, run_id: str, graph_paths: Dict[str, str], graph_summary: Optional[Dict[str, Any]] = None) -> None:
        data = self.knowledge_graph.read() or {"runs": {}}
        data.setdefault("runs", {})
        data["runs"][run_id] = {
            "paths": graph_paths,
            "summary": graph_summary or {},
            "updated_at": utc_now(),
        }
        data["updated_at"] = utc_now()
        self.knowledge_graph.write(data)

    def _update_entity_index(self) -> None:
        idx = {
            "partner": {"count": len((self.partners.read() or {}).get("items", [])), "updated_at": utc_now()},
            "system": {"count": len((self.systems.read() or {}).get("items", [])), "updated_at": utc_now()},
            "account": {"count": len((self.accounts.read() or {}).get("items", [])), "updated_at": utc_now()},
            "domain": {"count": len((self.domains.read() or {}).get("items", [])), "updated_at": utc_now()},
        }
        self.entity_index.write(idx)
