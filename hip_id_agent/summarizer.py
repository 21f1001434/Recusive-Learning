from __future__ import annotations

import csv
import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .security import mask_sensitive_data
from .safe_io import safe_write_json, safe_write_csv


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, data: Any) -> None:
    safe_write_json(path, data)


def _norm(value: Any) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _short(value: Any, limit: int = 220) -> str:
    s = str(value or "").replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


class InventorySummaryAgent:
    """Create small review artifacts from a large HIP/BizLink inventory run.

    The full run can contain multi-GB network logs, DOM snapshots and knowledge graph
    files.  This agent keeps the useful operational facts only: counts, IDs, lookup
    tables, failed parent fan-out calls, and a compact upload zip.  It is deterministic
    and local; no external LLM/API is required.
    """

    EXCLUDE_DIRS = {
        "network", "dom_snapshots", "screenshots", "knowledge_graph", "actions", "clicks", "console", "_small_upload", "_summary_upload", "upload_summary",
    }
    EXCLUDE_FILES = {
        "network_events.jsonl", "network_tab_events.json", "flow_knowledge_graph.json", "flow_knowledge_graph.html",
    }

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.inv_dir = self.run_dir / "inventory"
        self.summary_dir = self.run_dir / "upload_summary"
        self.summary_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, *, create_zip: bool = True, max_failed_rows: int = 500, max_lookup_rows: int = 2000) -> Dict[str, str]:
        counts = self._counts()
        failed = self._failed_requests()[:max_failed_rows]
        required = _read_json(self.inv_dir / "api_required_ids_candidates.json", {})
        api_inventory = _read_json(self.inv_dir / "partner_system_api_id_inventory.json", {})
        lookup_hits = self._important_lookup_hits(required, api_inventory)
        audit_summary = self._audit_summary(failed)
        file_manifest = self._file_manifest()
        progress_tail = self._progress_tail(120)
        compact_rows = self._compact_api_rows(api_inventory, max_rows=max_lookup_rows)

        review = {
            "schema_version": "1.0",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "run_id": self.run_dir.name,
            "status": self._run_status(failed),
            "counts": counts,
            "failed_request_count": len(self._failed_requests()),
            "failed_request_summary": audit_summary,
            "important_lookup_hits": lookup_hits,
            "compact_row_count": len(compact_rows),
            "large_files_excluded_from_upload": [
                "network_events.jsonl",
                "network/network_tab_events.json",
                "knowledge_graph/flow_knowledge_graph.json",
                "dom_snapshots/",
                "screenshots/",
            ],
            "files_manifest": file_manifest,
            "progress_tail": progress_tail,
            "note": "This summary intentionally excludes raw network bodies, DOM snapshots and full KG evidence. Use it for ChatGPT review/upload; keep the full run locally for audit.",
        }

        review_json = self.summary_dir / "run_summary_for_review.json"
        _write_json(review_json, review)
        md_path = self.summary_dir / "completion_summary.md"
        md_path.write_text(self._markdown(review, compact_rows), encoding="utf-8")
        failed_csv = self.summary_dir / "failed_requests_summary.csv"
        self._write_failed_csv(failed_csv, failed)
        compact_csv = self.summary_dir / "partner_system_api_id_inventory_compact.csv"
        self._write_compact_csv(compact_csv, compact_rows)
        compact_json = self.summary_dir / "partner_system_api_id_inventory_compact.json"
        _write_json(compact_json, compact_rows)

        paths = {
            "run_summary_for_review_json": str(review_json),
            "completion_summary_md": str(md_path),
            "failed_requests_summary_csv": str(failed_csv),
            "partner_system_api_id_inventory_compact_csv": str(compact_csv),
            "partner_system_api_id_inventory_compact_json": str(compact_json),
        }
        if create_zip:
            zip_path = self.run_dir / "UPLOAD_THIS_SUMMARY.zip"
            self._create_zip(zip_path)
            paths["upload_summary_zip"] = str(zip_path)
        return paths

    def _counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for kind, fname in [
            ("account", "accounts.json"),
            ("partner", "partners.json"),
            ("domain", "domains.json"),
            ("system", "systems.json"),
            ("deployment_group", "deployment_groups.json"),
            ("relationship", "relationships.json"),
        ]:
            data = _read_json(self.inv_dir / fname, [])
            out[kind] = len(data) if isinstance(data, list) else 0
        # Prefer explicit counts from API inventory when available.
        api_inv = _read_json(self.inv_dir / "partner_system_api_id_inventory.json", {})
        if isinstance(api_inv, dict) and isinstance(api_inv.get("counts"), dict):
            for k, v in api_inv["counts"].items():
                try:
                    out[str(k)] = int(v)
                except Exception:
                    pass
        return out

    def _failed_requests(self) -> List[Dict[str, Any]]:
        data = _read_json(self.inv_dir / "failed_requests.json", [])
        return data if isinstance(data, list) else []

    def _run_status(self, failed: List[Dict[str, Any]]) -> str:
        progress = _read_json(self.inv_dir / "progress.json", {})
        if isinstance(progress, dict) and progress.get("status") in {"success", "partial_success", "failed", "running"}:
            if progress.get("status") == "running" and failed:
                return "partial_or_interrupted"
            return str(progress.get("status"))
        return "partial_success" if failed else "success_or_completed"

    def _audit_summary(self, failed: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_area: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        parents: List[Dict[str, Any]] = []
        for item in failed:
            area = str(item.get("area") or ("partner" if "partner" in str(item.get("url", "")).lower() else "system" if "system" in str(item.get("url", "")).lower() else "unknown"))
            status = str(item.get("status") or item.get("error") or "unknown")
            by_area[area] = by_area.get(area, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            parents.append({
                "area": area,
                "status": status,
                "parent_account_id": item.get("parent_account_id", ""),
                "parent_account_name": item.get("parent_account_name", ""),
                "parent_domain_id": item.get("parent_domain_id", ""),
                "parent_domain_name": item.get("parent_domain_name", ""),
                "url": _short(item.get("url"), 260),
                "error": _short(item.get("error"), 260),
            })
        return {"by_area": by_area, "by_status": by_status, "parents": parents[:200]}

    def _important_lookup_hits(self, required: Dict[str, Any], api_inventory: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        terms = [
            "U-HAUL", "U-HAUL_PC", "AS2TEST", "Gmail Account", "AIC - DCE", "AIC-DCE", "ACI-DEC",
            "Customer Experience", "CX", "dce-shared-sender", "dce-shared-receiver", "dce-default-sender", "dce-default-receiver",
        ]
        rows = []
        if isinstance(api_inventory, dict):
            rows = api_inventory.get("rows") or []
        out: Dict[str, List[Dict[str, Any]]] = {t: [] for t in terms}
        for row in rows if isinstance(rows, list) else []:
            hay = _norm(" ".join(str(row.get(k, "")) for k in ["name", "id", "parent_account_name", "parent_domain_name", "componentId", "api_field_candidate"]))
            for term in terms:
                if _norm(term) and _norm(term) in hay:
                    out[term].append({
                        "object_type": row.get("object_type"),
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "parent_account_id": row.get("parent_account_id"),
                        "parent_account_name": row.get("parent_account_name"),
                        "parent_domain_id": row.get("parent_domain_id"),
                        "parent_domain_name": row.get("parent_domain_name"),
                        "numeric_id": row.get("numeric_id"),
                        "uuid_id": row.get("uuid_id"),
                        "api_field_candidate": row.get("api_field_candidate"),
                    })
        return {k: v[:20] for k, v in out.items() if v}

    def _compact_api_rows(self, api_inventory: Dict[str, Any], *, max_rows: int) -> List[Dict[str, Any]]:
        rows = api_inventory.get("rows") if isinstance(api_inventory, dict) else []
        compact: List[Dict[str, Any]] = []
        if not isinstance(rows, list):
            return compact
        for row in rows[:max_rows]:
            compact.append({
                "api_field_candidate": row.get("api_field_candidate", ""),
                "object_type": row.get("object_type", ""),
                "id": row.get("id", ""),
                "name": row.get("name", ""),
                "parent_account_id": row.get("parent_account_id", ""),
                "parent_account_name": row.get("parent_account_name", ""),
                "parent_domain_id": row.get("parent_domain_id", ""),
                "parent_domain_name": row.get("parent_domain_name", ""),
                "numeric_id": row.get("numeric_id", ""),
                "uuid_id": row.get("uuid_id", ""),
                "componentId": row.get("componentId", ""),
                "active": row.get("active", ""),
            })
        return compact

    def _progress_tail(self, n: int) -> List[Any]:
        p = self.inv_dir / "progress_events.jsonl"
        if not p.exists():
            return []
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]
            out = []
            for line in lines:
                try:
                    out.append(json.loads(line))
                except Exception:
                    out.append(line[:500])
            return out
        except Exception:
            return []

    def _file_manifest(self) -> List[Dict[str, Any]]:
        rows = []
        for p in self.run_dir.rglob("*"):
            if p.is_file():
                rel = _safe_rel(p, self.run_dir)
                try:
                    size = p.stat().st_size
                    mtime = datetime.fromtimestamp(p.stat().st_mtime).isoformat()
                except Exception:
                    size, mtime = 0, ""
                rows.append({"path": rel, "size_bytes": size, "modified": mtime, "included_in_upload_summary": rel.startswith("upload_summary/") or rel == "UPLOAD_THIS_SUMMARY.zip"})
        rows.sort(key=lambda r: r["size_bytes"], reverse=True)
        return rows[:500]

    def _write_failed_csv(self, path: Path, rows: List[Dict[str, Any]]) -> None:
        fields = ["area", "status", "parent_account_id", "parent_account_name", "parent_domain_id", "parent_domain_name", "url", "error"]
        safe_write_csv(path, fields, rows)

    def _write_compact_csv(self, path: Path, rows: List[Dict[str, Any]]) -> None:
        fields = ["api_field_candidate", "object_type", "id", "name", "parent_account_id", "parent_account_name", "parent_domain_id", "parent_domain_name", "numeric_id", "uuid_id", "componentId", "active"]
        safe_write_csv(path, fields, rows)

    def _markdown(self, review: Dict[str, Any], compact_rows: List[Dict[str, Any]]) -> str:
        counts = review.get("counts", {})
        failed = review.get("failed_request_summary", {})
        lines = [
            f"# Inventory Review Summary: {review.get('run_id')}",
            "",
            f"Status: `{review.get('status')}`",
            f"Failed request count: `{review.get('failed_request_count')}`",
            "",
            "## Counts",
            "",
            "| Entity | Count |",
            "|---|---:|",
        ]
        for k in ["account", "partner", "domain", "system", "deployment_group", "relationship"]:
            lines.append(f"| {k} | {counts.get(k, 0)} |")
        lines += ["", "## Failed request summary", "", "```json", json.dumps(failed, indent=2, ensure_ascii=False, default=str)[:12000], "```", "", "## Important lookup hits", "", "```json", json.dumps(review.get("important_lookup_hits", {}), indent=2, ensure_ascii=False, default=str)[:20000], "```", "", "## Compact API row preview", "", "```json", json.dumps(compact_rows[:40], indent=2, ensure_ascii=False, default=str), "```", "", "## Upload note", "", "This summary zip excludes raw Network/DOM/KG evidence so it can be uploaded and reviewed quickly. Keep the original run folder locally for audit."]
        return "\n".join(lines) + "\n"

    def _create_zip(self, zip_path: Path) -> None:
        # Include only the summary directory and a few small top-level/inventory files.
        include_candidates = [
            self.summary_dir,
            self.inv_dir / "progress.json",
            self.inv_dir / "progress_heartbeat.txt",
            self.inv_dir / "failed_requests.json",
            self.inv_dir / "inventory_request_audit.json",
            self.inv_dir / "api_required_ids_candidates.csv",
            self.inv_dir / "partner_system_api_id_inventory.csv",
            self.inv_dir / "relationships.csv",
            self.run_dir / "network_summary.json",
            self.run_dir / "final_report.json",
            self.run_dir / "final_report.md",
            self.run_dir / "report.json",
            self.run_dir / "report.md",
        ]
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for item in include_candidates:
                if not item.exists():
                    continue
                if item.is_dir():
                    for p in item.rglob("*"):
                        if p.is_file():
                            zf.write(p, _safe_rel(p, self.run_dir))
                elif item.is_file():
                    zf.write(item, _safe_rel(item, self.run_dir))


def summarize_run(run_dir: Path, *, create_zip: bool = True) -> Dict[str, str]:
    return InventorySummaryAgent(Path(run_dir)).generate(create_zip=create_zip)
