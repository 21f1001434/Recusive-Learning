from __future__ import annotations

import asyncio
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .browser_session import BrowserSession
from .config import AppConfig
from .memory import HipMemory
from .models import ExtractedID, NetworkTabEvent, RunContext, StageResult, utc_now
from .page_explorer import PageExplorer
from .report import ReportWriter
from .knowledge_graph import KnowledgeGraphWriter
from .security import mask_sensitive_data, safe_json_loads
from .summarizer import InventorySummaryAgent




class _InventoryProgress:
    """Lightweight progress reporter for long full-inventory exports.

    The export-all flow can run for a long time because it fans out every Account
    and Domain and then optionally repeats the read-only UI nested walk.  This
    class writes a durable heartbeat to inventory/progress.json and
    inventory/progress_events.jsonl, and also prints a compact progress bar to
    the terminal so the operator can tell whether the run is moving or stuck.
    """

    def __init__(self, run_dir: Path, *, enabled: bool = True, print_interval_seconds: float = 1.0) -> None:
        self.enabled = enabled
        self.inv_dir = Path(run_dir) / "inventory"
        self.inv_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_path = self.inv_dir / "progress.json"
        self.events_path = self.inv_dir / "progress_events.jsonl"
        self.heartbeat_path = self.inv_dir / "progress_heartbeat.txt"
        self.phase = "not_started"
        self.current = 0
        self.total = 0
        self.status = "not_started"
        self.detail = ""
        self.counts: Dict[str, int] = {}
        self.started_epoch = time.time()
        self.last_update_epoch = self.started_epoch
        self._last_print_epoch = 0.0
        self.print_interval_seconds = print_interval_seconds
        self.events_path.write_text("", encoding="utf-8")

    def start(self, phase: str, total: int = 0, *, detail: str = "", counts: Optional[Dict[str, int]] = None) -> None:
        self.phase = phase
        self.current = 0
        self.total = max(0, int(total or 0))
        self.status = "running"
        self.detail = detail
        if counts is not None:
            self.counts = dict(counts)
        self._emit(force=True)

    def update(
        self,
        *,
        current: Optional[int] = None,
        advance: int = 0,
        total: Optional[int] = None,
        phase: Optional[str] = None,
        detail: str = "",
        counts: Optional[Dict[str, int]] = None,
        status: Optional[str] = None,
        force: bool = False,
    ) -> None:
        if phase is not None and phase != self.phase:
            self.phase = phase
            if current is None:
                self.current = 0
        if total is not None:
            self.total = max(0, int(total or 0))
        if current is not None:
            self.current = max(0, int(current))
        elif advance:
            self.current = max(0, self.current + int(advance))
        if self.total and self.current > self.total:
            self.current = self.total
        if detail:
            self.detail = detail
        if counts is not None:
            self.counts = dict(counts)
        if status:
            self.status = status
        self._emit(force=force or (self.total > 0 and self.current >= self.total))

    def finish(self, *, status: str = "success", detail: str = "", counts: Optional[Dict[str, int]] = None) -> None:
        if self.total and self.current < self.total:
            self.current = self.total
        if detail:
            self.detail = detail
        if counts is not None:
            self.counts = dict(counts)
        self.status = status
        self._emit(force=True)
        try:
            sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:
            pass

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        percent = round((self.current / self.total) * 100, 2) if self.total else None
        return {
            "status": self.status,
            "phase": self.phase,
            "current": self.current,
            "total": self.total,
            "percent": percent,
            "detail": self.detail,
            "counts": self.counts,
            "started_epoch": self.started_epoch,
            "last_update_epoch": self.last_update_epoch,
            "elapsed_seconds": round(now - self.started_epoch, 2),
            "seconds_since_last_update": round(now - self.last_update_epoch, 2),
            "heartbeat_file": str(self.heartbeat_path),
            "stuck_rule": "If last_update_epoch / progress_heartbeat.txt does not change for several minutes, the browser is likely waiting on SSO, a Dell gateway response, or a UI blocker.",
        }

    def _bar(self, width: int = 28) -> str:
        if not self.total:
            return "[" + ("░" * width) + "]"
        ratio = min(1.0, max(0.0, self.current / self.total))
        filled = int(ratio * width)
        return "[" + ("█" * filled) + ("░" * (width - filled)) + "]"

    def _emit(self, *, force: bool = False) -> None:
        if not self.enabled:
            return
        self.last_update_epoch = time.time()
        snap = self.snapshot()
        try:
            self.snapshot_path.write_text(json.dumps(mask_sensitive_data(snap), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(mask_sensitive_data(snap), ensure_ascii=False, default=str) + "\n")
            self.heartbeat_path.write_text(str(self.last_update_epoch), encoding="utf-8")
        except Exception:
            # Progress must never break the actual extraction.
            pass

        now = time.time()
        if not force and (now - self._last_print_epoch) < self.print_interval_seconds:
            return
        self._last_print_epoch = now
        pct = f"{snap['percent']:.1f}%" if snap.get("percent") is not None else "--%"
        counts = snap.get("counts") or {}
        count_text = " ".join(f"{k}={v}" for k, v in counts.items() if isinstance(v, int))
        total_text = f"{self.current}/{self.total}" if self.total else f"{self.current}"
        line = f"\r{self._bar()} {total_text} {pct} | {self.phase} | {self.detail[:90]}"
        if count_text:
            line += f" | {count_text}"
        try:
            sys.stdout.write(line)
            sys.stdout.flush()
        except Exception:
            pass

@dataclass
class InventoryEntity:
    object_type: str  # account | partner | domain | system
    object_id: str
    name: Optional[str]
    source_url: str
    endpoint_kind: str
    field_path: str = ""
    parent_account_id: Optional[str] = None
    parent_account_name: Optional[str] = None
    parent_domain_id: Optional[str] = None
    parent_domain_name: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def identity_key(self) -> Tuple[str, str, str, str]:
        # Parent is part of the identity for child objects because the same row can be
        # visible under multiple parents. This preserves relationships in export files.
        return (
            self.object_type.lower(),
            self.object_id.lower(),
            (self.parent_account_id or "").lower(),
            (self.parent_domain_id or "").lower(),
        )


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

INVENTORY_KINDS = ["account", "partner", "domain", "system", "deployment_group"]


def _empty_inventory() -> Dict[str, List[InventoryEntity]]:
    return {kind: [] for kind in INVENTORY_KINDS}


def _canon_key(k: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k or "").lower())


def _looks_like_id(v: Any) -> bool:
    s = str(v or "").strip()
    if not s:
        return False
    if UUID_RE.match(s):
        return True
    if re.fullmatch(r"\d{1,}", s):
        # System ids in /systems-partners/systems can be small numerics like 72/110.
        return True
    if re.fullmatch(r"[A-Z]{1,4}\d{3,}", s, flags=re.I):
        return True
    return False


def _safe_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        return safe_json_loads(value)
    return None


def _path_kind(url: str) -> str:
    """Classify the most-specific BizLink inventory resource from a URL.

    Important exclusions:
    - /users and /user-details are user/admin metadata, not Domain/System inventory.
    - certificate endpoints are certificate inventory, not Partner inventory.
    - deployment-groups are first-class inventory because TP create needs deployment group IDs.
    """
    u = (url or "").lower()
    path = urlparse(u).path.lower()
    if any(x in path for x in ["/users", "/user-details", "/certificate/"]):
        return "unknown"
    if re.search(r"/domain-systems/domains/[^/]+/deployment-groups/?$", path) or path.endswith("/deployment-groups"):
        return "deployment_group"
    if re.search(r"/domain-systems/domains/[^/]+/systems/?$", path):
        return "system"
    if path.endswith("/systems-partners/systems") or re.search(r"/systems(?:/)?$", path):
        return "system"
    if re.search(r"/authz/accounts/[^/]+/partners/?$", path) or path.endswith("/authz/partners") or path.endswith("/partners"):
        return "partner"
    if re.search(r"/authz/accounts/[^/]+/domains/?$", path):
        return "domain"
    if path.endswith("/domain-systems/domains") or re.search(r"/domains(?:/)?$", path):
        return "domain"
    if path.endswith("/authz/accounts") or path.endswith("/accounts"):
        return "account"
    return "unknown"


def _parents_from_url(url: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        parsed = urlparse(url or "")
        path = parsed.path
        m = re.search(r"/accounts/([^/]+)/(?:partners|domains)(?:/|$)", path, flags=re.I)
        if m:
            out["parent_account_id"] = m.group(1)
        m = re.search(r"/domains/([^/]+)/(?:systems|deployment-groups)(?:/|$)", path, flags=re.I)
        if m:
            out["parent_domain_id"] = m.group(1)
        qs = parse_qs(parsed.query)
        for key in ["accountId", "account_id", "parentAccountId", "domainId", "domain_id", "parentDomainId"]:
            for cand in [key, key.lower()]:
                if cand in qs and qs[cand]:
                    out[key] = qs[cand][0]
    except Exception:
        pass
    return out


NAME_KEYS = {
    "account": ["accountName", "account_name", "displayName", "name", "title"],
    "partner": ["name", "partnerName", "partner_name", "partnerIdentifier", "partnerIdentifierValue", "displayName", "title"],
    "domain": ["domainName", "domain_name", "name", "displayName", "title", "description"],
    "system": ["systemName", "system_name", "name", "displayName", "title", "description"],
    "deployment_group": ["deploymentGroupName", "deployment_group_name", "name", "displayName", "title", "description", "type"],
}

ID_KEYS = {
    "account": ["accountId", "account_id", "id", "uuid"],
    "partner": ["partnerId", "partner_id", "partnerID", "id", "uuid"],
    "domain": ["domainId", "domain_id", "domainID", "id", "uuid"],
    # Do not use domainId as the system id. It is parent context only.
    "system": ["systemId", "system_id", "systemID", "id", "uuid"],
    "deployment_group": ["deploymentGroupId", "deployment_group_id", "deploymentGroupID", "id", "uuid"],
}


def _pick(row: Dict[str, Any], keys: Iterable[str]) -> Tuple[Optional[str], Any]:
    wanted = {_canon_key(k) for k in keys}
    for k, v in row.items():
        if _canon_key(k) in wanted and v not in (None, ""):
            return str(k), v
    return None, None


def _get_any(row: Dict[str, Any], keys: Iterable[str]) -> Any:
    _, value = _pick(row, keys)
    return value


class InventoryExtractor:
    """Extract every Account/Partner/Domain/System entity from Network-tab JSON."""

    def from_network_events(self, events: List[NetworkTabEvent]) -> Dict[str, List[InventoryEntity]]:
        out: Dict[str, List[InventoryEntity]] = _empty_inventory()
        for ev in events:
            kind = _path_kind(ev.url)
            if kind not in out:
                continue
            body = _safe_json(ev.response_body_redacted)
            if body is None and ev.response_body_text_redacted:
                body = _safe_json(ev.response_body_text_redacted)
            if body is None:
                continue
            parents = _parents_from_url(ev.url)
            # The live Partner endpoint uses x-account-id instead of putting the account id
            # in the URL. Preserve that parent context when it is present.
            headers = ev.request_headers or {}
            for hk, hv in headers.items():
                if str(hk).lower() == "x-account-id" and hv:
                    parents.setdefault("parent_account_id", str(hv))
            out[kind].extend(self.from_payload(kind, body, ev.url, parents=parents))
        return {k: self._dedupe(v) for k, v in out.items()}

    def from_payload(self, kind: str, body: Any, source_url: str, *, parents: Optional[Dict[str, str]] = None) -> List[InventoryEntity]:
        out: List[InventoryEntity] = []
        if kind not in set(INVENTORY_KINDS):
            return out
        parents = parents or {}
        for row, path in self._collect_rows(body):
            ent = self._entity_from_row(kind, row, path, source_url, parents)
            if ent:
                out.append(ent)
        return self._dedupe(out)

    def _collect_rows(self, body: Any) -> List[Tuple[Dict[str, Any], str]]:
        rows: List[Tuple[Dict[str, Any], str]] = []

        def walk(node: Any, path: str = "response") -> None:
            if isinstance(node, list):
                for i, item in enumerate(node):
                    walk(item, f"{path}[{i}]")
            elif isinstance(node, dict):
                keys = {_canon_key(k) for k in node.keys()}
                has_id = any(k in keys for k in ["id", "uuid", "accountid", "partnerid", "domainid", "systemid", "deploymentgroupid"])
                has_name = any("name" in k or k in {"title", "description", "partneridentifier", "systemname", "domainname", "accountname", "deploymentgroupname", "type"} for k in keys)
                if has_id and (has_name or len(node) <= 80):
                    rows.append((node, path))
                for k, v in node.items():
                    if isinstance(v, (dict, list)):
                        walk(v, f"{path}.{k}")

        walk(body)
        return rows

    def _entity_from_row(self, kind: str, row: Dict[str, Any], path: str, url: str, parents: Dict[str, str]) -> Optional[InventoryEntity]:
        id_key, entity_id = _pick(row, ID_KEYS[kind])
        if not _looks_like_id(entity_id):
            return None
        name_key, name = _pick(row, NAME_KEYS[kind])
        if name is None:
            _, name = _pick(row, ["description", "code", "identifier", "email"])

        parent_account_id = parents.get("parent_account_id") or parents.get("accountId") or parents.get("account_id") or parents.get("parentAccountId")
        parent_domain_id = parents.get("parent_domain_id") or parents.get("domainId") or parents.get("domain_id") or parents.get("parentDomainId")
        parent_account_name = parents.get("parent_account_name")
        parent_domain_name = parents.get("parent_domain_name")

        if kind in {"system", "deployment_group"}:
            pd = _get_any(row, ["domainId", "domain_id", "parentDomainId"])
            pn = _get_any(row, ["domain", "domainName", "parentDomainName", "primaryDomain"])
            if pd:
                parent_domain_id = str(pd)
            if pn:
                parent_domain_name = str(pn)
        if kind == "partner":
            # Useful relationship metadata; these are names, not ids.
            primary_domain = _get_any(row, ["primaryDomain"])
            additional_domains = _get_any(row, ["additionalDomains"])
        else:
            primary_domain = None
            additional_domains = None

        # Keep the full row returned by the live BizLink Network payload.
        # Earlier exports only kept row_keys, which proved the ID was found but
        # did not give the user the same complete detail record that is visible
        # when opening Gmail Account -> Show Partner(s) -> AS2TEST.
        # The writer masks sensitive keys before saving files.
        extra = {
            "id_key": id_key,
            "name_key": name_key,
            "row_keys": list(row.keys())[:100],
            "field_path": path,
            "details": dict(row),
        }
        if kind == "account" and isinstance(row.get("domains"), list):
            extra["account_domain_names"] = row.get("domains")
        if primary_domain:
            extra["primary_domain_name"] = primary_domain
        if additional_domains:
            extra["additional_domain_names"] = additional_domains
        if kind == "deployment_group":
            dg_type = _get_any(row, ["type", "deploymentGroupType", "direction"])
            if dg_type:
                extra["deployment_group_type"] = dg_type
        extra.update({k: v for k, v in parents.items() if v})
        return InventoryEntity(
            object_type=kind,
            object_id=str(entity_id).strip(),
            name=str(name).strip() if name is not None else None,
            source_url=url,
            endpoint_kind=kind,
            field_path=f"{path}.{id_key}" if id_key else path,
            parent_account_id=parent_account_id,
            parent_account_name=parent_account_name,
            parent_domain_id=parent_domain_id,
            parent_domain_name=parent_domain_name,
            extra=extra,
        )

    def _dedupe(self, items: List[InventoryEntity]) -> List[InventoryEntity]:
        best: Dict[Tuple[str, str, str, str], InventoryEntity] = {}
        for item in items:
            key = item.identity_key
            if key not in best:
                best[key] = item
            else:
                cur = best[key]
                score_cur = int(bool(cur.name)) + int(bool(cur.parent_account_name)) + int(bool(cur.parent_domain_name))
                score_new = int(bool(item.name)) + int(bool(item.parent_account_name)) + int(bool(item.parent_domain_name))
                if score_new > score_cur:
                    best[key] = item
        return sorted(best.values(), key=lambda x: ((x.name or "").lower(), x.object_id.lower(), (x.parent_account_id or "").lower(), (x.parent_domain_id or "").lower()))


def _entity_to_extracted(entity: InventoryEntity) -> ExtractedID:
    return ExtractedID(
        object_type=entity.object_type,
        query=entity.name or entity.object_id,
        object_id=entity.object_id,
        name=entity.name,
        source="network_inventory",
        confidence=0.94,
        evidence={
            "source_url": entity.source_url,
            "endpoint_kind": entity.endpoint_kind,
            "field_path": entity.field_path,
            "parent_account_id": entity.parent_account_id,
            "parent_account_name": entity.parent_account_name,
            "parent_domain_id": entity.parent_domain_id,
            "parent_domain_name": entity.parent_domain_name,
            "inventory_export": True,
            **(entity.extra or {}),
        },
    )


class PortalInventoryFlow:
    """Broad read-only inventory export for BizLink Accounts, Partners, Domains and Systems.

    Full inventory cannot rely on typing the child name in the top-level search, and it
    also should not click one visible card at a time. The reliable pattern in the live
    Network tab is:

    - GET /hipAuthService-svc/api/authz/accounts => all parent Accounts
    - GET /hipAuthService-svc/api/authz/partners with header x-account-id=<account> => Partners under that Account
    - GET /hipSystemsAuthService-svc/api/domain-systems/domains => all Domains
    - GET /hipSystemsAuthService-svc/api/domain-systems/domains/<domainId>/systems => Systems under that Domain
    - GET /hipSystemsAuthService-svc/api/systems-partners/systems => broad Systems index
    """

    PARTNER_ORIGIN = "https://developer.dell.com/inaas-gateway/hipAuthService-svc/api"
    SYSTEM_ORIGIN = "https://developer.dell.com/inaas-gateway/hipSystemsAuthService-svc/api"

    def __init__(self, config: AppConfig, memory: HipMemory, *, full_ui_nested_walk: bool = True, enrich_link_details: bool = True, write_heavy_evidence: bool = False):
        self.config = config
        self.memory = memory
        self.explorer = PageExplorer(config)
        self.extractor = InventoryExtractor()
        # For API-readiness the user needs the same nested evidence as the manual UI flow:
        # every Account -> Show Partner(s), and every Domain -> View Domain/System(s).
        # API fan-out remains the fast source, but this read-only UI walk fills/validates any
        # parent whose API response is incomplete/500 and records exact click/network evidence.
        self.full_ui_nested_walk = full_ui_nested_walk
        self.enrich_link_details = enrich_link_details
        self.write_heavy_evidence = write_heavy_evidence
        self.progress: Optional[_InventoryProgress] = None
        # Keep long inventory runs moving even when one Dell gateway call hangs.
        self.inventory_request_timeout_seconds = 25
        self.final_log_flush_timeout_seconds = 90

    def _inventory_counts(self, inventory: Optional[Dict[str, List[InventoryEntity]]] = None, **overrides: int) -> Dict[str, int]:
        counts = {kind: 0 for kind in INVENTORY_KINDS}
        if inventory:
            for kind, rows in inventory.items():
                counts[kind] = len(rows or [])
        for key, value in overrides.items():
            if key in counts:
                counts[key] = int(value or 0)
        return counts

    def _progress_start(self, phase: str, total: int = 0, *, detail: str = "", counts: Optional[Dict[str, int]] = None) -> None:
        if self.progress:
            self.progress.start(phase, total, detail=detail, counts=counts)

    def _progress_update(
        self,
        *,
        current: Optional[int] = None,
        advance: int = 0,
        total: Optional[int] = None,
        phase: Optional[str] = None,
        detail: str = "",
        counts: Optional[Dict[str, int]] = None,
        status: Optional[str] = None,
        force: bool = False,
    ) -> None:
        if self.progress:
            self.progress.update(current=current, advance=advance, total=total, phase=phase, detail=detail, counts=counts, status=status, force=force)

    async def _flush_logs_with_progress(self, session: BrowserSession, *, timeout_seconds: Optional[int] = None) -> None:
        """Flush browser evidence with a visible heartbeat and bounded timeout."""
        timeout_seconds = int(timeout_seconds or self.final_log_flush_timeout_seconds)
        started = time.time()
        task = asyncio.create_task(session.flush_logs())
        try:
            while not task.done():
                elapsed = int(time.time() - started)
                if elapsed >= timeout_seconds:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                    if self.progress:
                        self.progress.update(
                            phase="finalizing",
                            current=1,
                            total=4,
                            detail=f"Log flush exceeded {timeout_seconds}s; continuing with streamed partial logs",
                            status="running",
                            force=True,
                        )
                    return
                if self.progress:
                    self.progress.update(
                        phase="finalizing",
                        current=0,
                        total=4,
                        detail=f"Flushing browser/network logs... {elapsed}s elapsed, events={len(session.network_tab_events)} actions={len(session.action_events)}",
                        force=True,
                    )
                await asyncio.sleep(5)
            await task
            self._progress_update(phase="finalizing", current=1, total=4, detail="Browser/network logs flushed", force=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._progress_update(phase="finalizing", current=1, total=4, detail=f"Log flush warning: {exc}", force=True)

    async def run(self, ctx: RunContext) -> Dict[str, Any]:
        self.progress = _InventoryProgress(ctx.run_dir)
        self._progress_start("initializing", 8, detail="Preparing browser, SSO, Network capture and output folders", counts=self._inventory_counts())
        ctx.registry["inventory_progress_json"] = str(self.progress.snapshot_path)
        ctx.registry["inventory_progress_events_jsonl"] = str(self.progress.events_path)
        ctx.registry["inventory_progress_heartbeat_txt"] = str(self.progress.heartbeat_path)
        self.config.exploration.enabled = True
        self.config.exploration.collect_all_pages = True
        self.config.exploration.search_first = False
        self.config.exploration.prefer_nested_discovery = True
        self.config.exploration.allow_direct_child_search_fallback = False
        async with BrowserSession(self.config, ctx.run_dir) as session:
            await session.goto_base_and_complete_sso()
            self._progress_update(current=1, detail="Browser opened and SSO warm-up completed", force=True)

            # Warm the live application first on the System page. In the Dell BizLink UI,
            # successful gateway calls include x-requester-id. Replaying that same header
            # is required for full API fan-out; otherwise endpoints such as /authz/accounts
            # return 400 and the export loses account/partner counts.
            # Avoid opening Edit pages during full inventory; we only need read-only list actions.
            self.config.exploration.open_edit_pages_for_readonly_capture = False

            self._progress_update(current=2, detail="Navigating to System link", force=True)
            system_nav = await self.explorer.navigate_to_area(session, "system")
            ctx.registry["system_navigation"] = system_nav
            system_inventory = await self._crawl_system_inventory(session)
            self._progress_update(phase="initializing", current=3, total=8, detail="System API inventory crawl completed", counts=self._inventory_counts(system_inventory["inventory"]), force=True)
            ctx.registry["system_api_inventory_crawl"] = system_inventory["summary"]

            # If the API fan-out hits 5xx/4xx for any Domain -> Systems call, fall back
            # to the real UI card/menu flow so the Network tab can reveal alternate
            # endpoints used by the live page. This is still read-only.
            system_ui_fallback = None
            if system_inventory["summary"].get("errors"):
                await self.explorer.navigate_to_area(session, "system")
                system_ui_fallback = await self.explorer.explore_area(session, "system")
                ctx.registry["system_ui_inventory_fallback"] = system_ui_fallback

            self._progress_update(phase="initializing", current=4, total=8, detail="Navigating to Partner link", force=True)
            partner_nav = await self.explorer.navigate_to_area(session, "partner")
            ctx.registry["partner_navigation"] = partner_nav
            partner_inventory = await self._crawl_partner_inventory(session)
            self._progress_update(phase="initializing", current=5, total=8, detail="Partner API inventory crawl completed", counts=self._inventory_counts(partner_inventory["inventory"]), force=True)
            ctx.registry["partner_api_inventory_crawl"] = partner_inventory["summary"]

            partner_ui_fallback = None
            if partner_inventory["summary"].get("errors"):
                await self.explorer.navigate_to_area(session, "partner")
                partner_ui_fallback = await self.explorer.explore_area(session, "partner")
                ctx.registry["partner_ui_inventory_fallback"] = partner_ui_fallback

            full_ui_walk_summary = None
            full_ui_inventory: Dict[str, List[InventoryEntity]] = _empty_inventory()
            if self.full_ui_nested_walk:
                full_ui_walk_summary = {"mode": "exhaustive_parent_card_ui_walk", "partner": {}, "system": {}}
                await self.explorer.navigate_to_area(session, "partner")
                partner_ui_all = await self._ui_collect_children_for_all_parents(
                    session, area="partner", parents=partner_inventory["inventory"].get("account", []), target_kind="partner"
                )
                full_ui_walk_summary["partner"] = partner_ui_all["summary"]
                full_ui_inventory["partner"].extend(partner_ui_all["rows"])

                await self.explorer.navigate_to_area(session, "system")
                system_ui_all = await self._ui_collect_children_for_all_parents(
                    session, area="system", parents=system_inventory["inventory"].get("domain", []), target_kind="system"
                )
                full_ui_walk_summary["system"] = system_ui_all["summary"]
                full_ui_inventory["system"].extend(system_ui_all["rows"])
                ctx.registry["full_ui_nested_walk"] = full_ui_walk_summary
                self._progress_update(phase="initializing", current=6, total=8, detail="Full parent-card UI nested walk completed", counts=self._inventory_counts(full_ui_inventory), force=True)

            self._progress_update(phase="finalizing", current=0, total=4, detail="Flushing browser/network logs", force=True)
            await self._flush_logs_with_progress(session)

            inventory: Dict[str, List[InventoryEntity]] = _empty_inventory()
            for bucket in [partner_inventory["inventory"], system_inventory["inventory"], full_ui_inventory, self.extractor.from_network_events(session.network_tab_events)]:
                for kind, rows in bucket.items():
                    inventory.setdefault(kind, []).extend(rows)
            inventory = {k: self.extractor._dedupe(v) for k, v in inventory.items()}
            if self.enrich_link_details:
                detail_summary = await self._enrich_detail_records(session, inventory)
                ctx.registry["inventory_detail_enrichment"] = detail_summary
                inventory = {k: self.extractor._dedupe(v) for k, v in inventory.items()}

            self._progress_update(phase="finalizing", current=2, total=5, detail="Writing inventory JSON/CSV files", counts=self._inventory_counts(inventory), force=True)
            outputs = self._write_inventory_files(ctx, inventory)
            audit_paths = self._write_inventory_audit_files(
                ctx,
                partner_summary=partner_inventory["summary"],
                system_summary=system_inventory["summary"],
                partner_ui_fallback=partner_ui_fallback,
                system_ui_fallback=system_ui_fallback,
            )
            outputs.update(audit_paths)
            ctx.registry.update(outputs)
            ctx.registry["inventory_counts"] = {k: len(v) for k, v in inventory.items()}

            # Write a small upload/review summary before memory/report/KG work. If the
            # run is interrupted later, the user can still upload UPLOAD_THIS_SUMMARY.zip
            # instead of a multi-GB run folder.
            self._progress_update(phase="finalizing", current=3, total=5, detail="Creating small upload summary zip", counts=self._inventory_counts(inventory), force=True)
            summary_paths = InventorySummaryAgent(ctx.run_dir).generate(create_zip=True)
            ctx.registry.update({f"summary_{k}": v for k, v in summary_paths.items()})

            self._progress_update(phase="finalizing", current=4, total=5, detail="Saving inventory IDs to local memory", counts=self._inventory_counts(inventory), force=True)
            self._save_inventory_to_memory(inventory)
            ctx.registry["network_tab_event_count"] = len(session.network_tab_events)
            ctx.registry["click_event_count"] = len(session.click_events)
            ctx.registry["action_event_count"] = len(session.action_events)
            failed_requests = (partner_inventory["summary"].get("errors") or []) + (system_inventory["summary"].get("errors") or [])
            stage_status = "partial_success" if failed_requests else "success"
            # A full-inventory export can still produce useful complete/partial files even when
            # a few live Dell gateway fan-out calls return 500. Preserve that distinction at
            # the run level instead of letting the report infer "failed" merely because the
            # only stage is partial_success. The detailed unresolved calls remain in
            # inventory/failed_requests.json.
            ctx.registry["run_status"] = stage_status
            ctx.registry["inventory_failed_request_count"] = len(failed_requests)
            warnings = []
            if failed_requests:
                warnings.append(f"{len(failed_requests)} API fan-out request(s) returned non-200; exhaustive UI nested walk was attempted and failed_requests.json was written.")
            if self.full_ui_nested_walk:
                warnings.append("Full read-only UI nested walk was enabled: every discovered Account/Domain parent is searched/opened and its Show Partner(s)/View Domain/System(s) action is attempted.")
            ctx.stage_results.append(StageResult(
                stage="export_all_inventory",
                status=stage_status,
                message="Exported Accounts, Partners, Domains, Systems and Deployment Groups with API fan-out plus exhaustive parent-card UI nested walk evidence.",
                started_at=ctx.started_at,
                finished_at=utc_now(),
                warnings=warnings,
                evidence={"inventory_counts": ctx.registry["inventory_counts"], "failed_request_count": len(failed_requests), "outputs": outputs},
            ))
            if self.write_heavy_evidence:
                graph_paths = KnowledgeGraphWriter(ctx).write(
                    click_events=session.click_events,
                    action_events=session.action_events,
                    network_events=session.network_tab_events,
                    report_paths=None,
                )
                ctx.registry.update(graph_paths)
            else:
                ctx.registry["knowledge_graph_mode"] = "skipped_by_default_for_full_inventory; use --write-heavy-evidence or HIP_WRITE_FULL_KG=1 only when needed"
            report_paths = ReportWriter(ctx).write_all()
            ctx.registry["report_paths"] = report_paths
            if self.write_heavy_evidence:
                graph_paths = KnowledgeGraphWriter(ctx).write(
                    click_events=session.click_events,
                    action_events=session.action_events,
                    network_events=session.network_tab_events,
                    report_paths=report_paths,
                )
                ctx.registry.update(graph_paths)
            # Refresh the small upload zip after final reports exist.
            summary_paths = InventorySummaryAgent(ctx.run_dir).generate(create_zip=True)
            ctx.registry.update({f"summary_{k}": v for k, v in summary_paths.items()})
            final_status = ctx.registry.get("run_status", "success")
            if self.progress:
                self.progress.finish(status=final_status, detail="Inventory export finished; reports and UPLOAD_THIS_SUMMARY.zip written", counts=ctx.registry.get("inventory_counts") or self._inventory_counts(inventory))
            self.memory.record_run(ctx.run_id, ReportWriter(ctx).build_summary())
            return ReportWriter(ctx).build_summary()

    def _requester_from_network_events(self, session: BrowserSession) -> Optional[str]:
        for ev in reversed(session.network_tab_events):
            for key, value in (ev.request_headers or {}).items():
                if str(key).lower() == "x-requester-id" and value:
                    return str(value)
        return None

    async def _discover_requester_id(self, session: BrowserSession) -> Optional[str]:
        configured = (self.config.api.requester_id or "").strip()
        if configured:
            return configured
        from_events = self._requester_from_network_events(session)
        if from_events:
            return from_events
        page = session.page
        if page is None:
            return None
        try:
            raw = await page.evaluate(
                """
() => {
  const values = [];
  const add = (v) => {
    try {
      if (v === null || v === undefined) return;
      if (typeof v === 'string') values.push(v.slice(0, 30000));
      else values.push(JSON.stringify(v).slice(0, 30000));
    } catch (e) {}
  };
  for (const store of [window.localStorage, window.sessionStorage]) {
    try {
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i);
        add(k);
        add(store.getItem(k));
      }
    } catch (e) {}
  }
  try { add(document.body && document.body.innerText); } catch (e) {}
  return values.join('\n');
}
"""
            )
            match = re.search(r"[A-Z0-9._%+-]+@(?:dellteam|dell|emc)\.com", str(raw or ""), flags=re.I)
            if match:
                return match.group(0)
        except Exception:
            return None
        return None

    async def _default_headers(self, session: BrowserSession, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "content-type": "application/json",
        }
        requester = await self._discover_requester_id(session)
        if requester:
            headers["x-requester-id"] = requester
        if extra:
            headers.update({k: v for k, v in extra.items() if v is not None})
        return headers

    def _referer_for_inventory_url(self, url: str) -> str:
        if "hipAuthService-svc" in (url or ""):
            return "https://developer.dell.com/hybrid-integrations/bizlink/partner"
        if "hipSystemsAuthService-svc" in (url or ""):
            return "https://developer.dell.com/hybrid-integrations/bizlink/system"
        return "https://developer.dell.com/"

    def _row_count(self, body: Any) -> int:
        if isinstance(body, list):
            return len(body)
        if isinstance(body, dict):
            for key in ["items", "content", "data", "records", "results", "result"]:
                value = body.get(key)
                if isinstance(value, list):
                    return len(value)
            return 1 if any(k in body for k in ["id", "uuid", "accountId", "partnerId", "domainId", "systemId"]) else 0
        return 0

    async def _fetch_json(
        self,
        session: BrowserSession,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        max_attempts: int = 4,
    ) -> Tuple[Any, Dict[str, Any]]:
        page = session.page
        if page is None:
            return None, {"url": url, "status": "failed", "ok": False, "error": "no page"}
        headers = dict(headers or {})
        headers.setdefault("Accept", "application/json, text/plain, */*")
        headers.setdefault("content-type", "application/json")
        headers.setdefault("Referer", self._referer_for_inventory_url(url))

        async def do_fetch(hdrs: Dict[str, str]) -> Dict[str, Any]:
            timeout_ms = int(self.inventory_request_timeout_seconds * 1000)
            try:
                return await asyncio.wait_for(
                    page.evaluate(
                        """
async ({url, headers, timeoutMs}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || 25000);
  try {
    const res = await fetch(url, {method: 'GET', credentials: 'include', headers: headers || {}, cache: 'no-store', signal: controller.signal});
    const text = await res.text();
    return {ok: res.ok, status: res.status, url: res.url, text};
  } catch (err) {
    return {ok: false, status: 'failed', url, text: '', error: String(err && err.message ? err.message : err)};
  } finally {
    clearTimeout(timer);
  }
}
""",
                        {"url": url, "headers": hdrs, "timeoutMs": timeout_ms},
                    ),
                    timeout=self.inventory_request_timeout_seconds + 5,
                )
            except Exception as exc:
                return {"ok": False, "status": "failed", "url": url, "text": "", "error": f"fetch timeout/error after {self.inventory_request_timeout_seconds}s: {exc}"}

        attempts: List[Dict[str, Any]] = []
        result: Dict[str, Any] = {"ok": False, "status": "failed", "url": url, "text": ""}
        retried_with_requester = False
        retry_statuses = {400, 401, 403, 408, 429, 500, 502, 503, 504}
        try:
            for attempt_no in range(1, max(1, max_attempts) + 1):
                if not any(str(k).lower() == "x-requester-id" for k in headers):
                    requester = await self._discover_requester_id(session)
                    if requester:
                        headers["x-requester-id"] = requester
                        retried_with_requester = attempt_no > 1
                if attempt_no > 1:
                    headers["Cache-Control"] = "no-cache"
                    headers["Pragma"] = "no-cache"
                    headers["x-inventory-retry-attempt"] = str(attempt_no)
                result = await do_fetch(headers)
                status = result.get("status")
                attempts.append({"attempt": attempt_no, "status": status, "ok": bool(result.get("ok")), "error": result.get("error"), "sent_x_requester_id": bool(any(str(k).lower() == "x-requester-id" for k in headers))})
                if result.get("ok"):
                    break
                if (status not in retry_statuses and status != "failed") or attempt_no >= max(1, max_attempts):
                    break
                # Let transient gateway 500/429 responses recover; also gives SSO token refresh hooks time to settle.
                await asyncio.sleep(min(1.5, 0.25 * attempt_no))
            body = _safe_json(result.get("text"))
            row_count = self._row_count(body)
            text = str(result.get("text") or "")
            meta = {
                "url": url,
                "resolved_url": result.get("url"),
                "status": result.get("status"),
                "ok": bool(result.get("ok")),
                "row_count": row_count,
                "sent_x_requester_id": bool(any(str(k).lower() == "x-requester-id" for k in headers)),
                "attempt_count": len(attempts),
                "attempts": attempts,
            }
            if retried_with_requester:
                meta["retried_with_x_requester_id"] = True
            if not result.get("ok"):
                meta["error"] = result.get("error") or (text[:500] if text else f"HTTP {result.get('status')}")
            return body, meta
        except Exception as exc:
            return None, {"url": url, "status": "failed", "ok": False, "error": str(exc), "attempts": attempts, "attempt_count": len(attempts)}

    def _request_failed(self, meta: Dict[str, Any]) -> bool:
        return meta.get("status") == "failed" or meta.get("ok") is False or (isinstance(meta.get("status"), int) and int(meta.get("status")) >= 400)


    def _norm_text(self, value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    async def _ui_collect_children_for_parent(
        self,
        session: BrowserSession,
        *,
        area: str,
        parent: InventoryEntity,
        target_kind: str,
    ) -> Tuple[List[InventoryEntity], Dict[str, Any]]:
        """Open one parent card and collect all child rows from the live UI flow.

        This is the exact pattern the user validated manually:
        Gmail Account -> three dots -> Show Partner(s) -> AS2TEST.  For full
        inventory, use the same read-only nested action for any parent whose API
        fan-out fails or whose details need Network-tab confirmation.  The method
        searches only for the *parent* name on the parent page; it never searches
        for a child Partner/System name in the top-level search box.
        """
        page = session.page
        if page is None:
            return [], {"status": "failed", "reason": "no page", "parent_id": parent.object_id, "parent_name": parent.name}
        if target_kind not in {"partner", "system"}:
            return [], {"status": "skipped", "reason": f"unsupported target_kind {target_kind}"}

        parent_name = parent.name or parent.object_id
        meta: Dict[str, Any] = {
            "status": "not_found",
            "mode": "targeted_parent_ui_fallback",
            "area": area,
            "target_kind": target_kind,
            "parent_id": parent.object_id,
            "parent_name": parent.name,
            "rule": "search/open parent only, then click nested card action",
            "cards_checked": [],
            "actions_checked": [],
        }
        await self.explorer.navigate_to_area(session, area)
        await self.explorer.dismiss_blockers(session)

        # Try parent-name search first. If the live grid does not render a card for
        # long domain/account names, retry with stable identifiers from the parent row
        # such as componentId (PT281680) and finally the parent UUID. This keeps the
        # run parent-scoped while fixing the earlier card_count=0 failures.
        cards: List[Dict[str, Any]] = []
        meta["parent_searches"] = []
        for term in self._parent_search_terms(parent):
            await self.explorer.navigate_to_area(session, area)
            await self.explorer.dismiss_blockers(session)
            search_meta = await self.explorer.search_query(session, term, area)
            meta["parent_searches"].append({"term": term, "result": search_meta})
            await asyncio.sleep(0.5)
            await self.explorer.dismiss_blockers(session)
            cards = await self.explorer._discover_entity_cards(page)
            if cards:
                meta["selected_parent_search_term"] = term
                break

        if not cards and parent_name:
            # Synthetic title fallback: let PageExplorer resolve the actual locator by title.
            # We use a deliberately high index later so _card_locator_by_title is used instead
            # of the first unrelated visible card.
            try:
                loc = await self.explorer._card_locator_by_title(page, parent_name)
                if loc is not None:
                    cards = [{"title": parent_name, "text": parent_name, "synthetic_by_title": True}]
                    meta["synthetic_title_card"] = True
            except Exception as exc:
                meta["synthetic_title_card_error"] = str(exc)

        meta["card_count"] = len(cards)
        parent_norm = self._norm_text(parent_name)
        matched_indices: List[int] = []
        for idx, card in enumerate(cards[: max(1, self.config.exploration.max_cards_per_page)]):
            card_title = card.get("title") or card.get("name") or ""
            card_text = " ".join([str(card_title), str(card.get("text") or "")])
            card_norm = self._norm_text(card_text)
            is_match = bool(parent_norm and (parent_norm in card_norm or card_norm in parent_norm))
            meta["cards_checked"].append({"card_index": idx, "card_title": card_title, "matched_parent": is_match})
            if is_match:
                matched_indices.append(idx)
        if not matched_indices and cards:
            # If filtering left exactly one visible card, try it. This mirrors the human
            # flow after searching for the parent card by name.
            matched_indices = [0]
            meta["single_card_fallback"] = True

        for idx in matched_indices:
            card = cards[idx]
            card_title = card.get("title") or card.get("name") or card.get("text", "")[:80]
            locator_index = 999999 if card.get("synthetic_by_title") or card.get("fallback") else idx
            actions = await self.explorer._open_card_menu_and_collect_actions(session, locator_index, card_title)
            nested_actions = [a for a in actions if self.explorer._is_nested_discovery_action(a.get("label") or "", target_kind)]
            meta["actions_checked"].append({"card_index": idx, "locator_index": locator_index, "card_title": card_title, "actions": actions, "nested_actions": nested_actions})
            for action in nested_actions:
                label = action.get("label") or ""
                before_url = page.url
                before_network = len(session.network_tab_events)
                click_result = await self.explorer._click_menu_action_by_label(session, label, area=area, card_title=card_title, card_index=locator_index)
                await self.explorer.wait_after_row_click(session, query=parent_name, area=area, before_url=before_url, before_network_count=before_network)
                await asyncio.sleep(0.6)
                network_after = session.network_tab_events[before_network:]
                inventory = self.extractor.from_network_events(network_after)
                rows = inventory.get(target_kind, [])
                for row in rows:
                    if target_kind == "partner":
                        row.parent_account_id = row.parent_account_id or parent.object_id
                        row.parent_account_name = row.parent_account_name or parent.name
                        row.extra.setdefault("parent_account_id", parent.object_id)
                        if parent.name:
                            row.extra.setdefault("parent_account_name", parent.name)
                    elif target_kind == "system":
                        row.parent_domain_id = row.parent_domain_id or parent.object_id
                        row.parent_domain_name = row.parent_domain_name or parent.name
                        row.extra.setdefault("parent_domain_id", parent.object_id)
                        if parent.name:
                            row.extra.setdefault("parent_domain_name", parent.name)
                meta.update({
                    "status": "success" if rows else "clicked_no_rows",
                    "clicked_action": label,
                    "click_result": click_result,
                    "network_events_after_click": len(network_after),
                    "rows_collected": len(rows),
                    "child_url": page.url,
                })
                # Reset so the next parent starts cleanly.
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                return rows, meta
        return [], meta

    def _parent_search_terms(self, parent: InventoryEntity) -> List[str]:
        terms: List[str] = []
        details = (parent.extra or {}).get("details") if isinstance(parent.extra, dict) else {}
        for value in [
            parent.name,
            (details or {}).get("componentId") if isinstance(details, dict) else None,
            (details or {}).get("accountName") if isinstance(details, dict) else None,
            (details or {}).get("name") if isinstance(details, dict) else None,
            parent.object_id,
        ]:
            text = str(value or "").strip()
            if text and text not in terms:
                terms.append(text)
        return terms

    async def _ui_collect_children_for_all_parents(
        self,
        session: BrowserSession,
        *,
        area: str,
        parents: List[InventoryEntity],
        target_kind: str,
    ) -> Dict[str, Any]:
        """Exhaustively repeat the human nested flow for every parent.

        Partner: each Account -> Show Partner(s) -> child Partner list.
        System: each Domain -> View Domain/System(s) -> child System list.
        This is intentionally read-only and never searches for a child name in the
        top-level parent search box.
        """
        rows: List[InventoryEntity] = []
        self._progress_start(f"{area}_{target_kind}_ui_parent_walk", len(parents), detail=f"Repeating validated UI flow for every {area} parent", counts={kind: 0 for kind in INVENTORY_KINDS})
        summary: Dict[str, Any] = {
            "area": area,
            "target_kind": target_kind,
            "parent_count": len(parents),
            "parents_attempted": 0,
            "parents_with_rows": 0,
            "rows_collected_before_dedupe": 0,
            "attempts": [],
            "stopped_reason": None,
        }
        max_actions = max(1, self.config.exploration.max_total_actions)
        for idx, parent in enumerate(parents):
            if summary["parents_attempted"] >= max_actions:
                summary["stopped_reason"] = "max_total_actions reached during exhaustive UI parent walk"
                break
            child_rows, meta = await self._ui_collect_children_for_parent(session, area=area, parent=parent, target_kind=target_kind)
            summary["parents_attempted"] += 1
            summary["attempts"].append(meta)
            if child_rows:
                summary["parents_with_rows"] += 1
                rows.extend(child_rows)
                summary["rows_collected_before_dedupe"] += len(child_rows)
            self._progress_update(current=summary["parents_attempted"], detail=f"UI nested walk {area}: {summary['parents_attempted']}/{len(parents)} parent={parent.name or parent.object_id} rows={len(child_rows)}", counts={**{kind: 0 for kind in INVENTORY_KINDS}, target_kind: len(rows)}, force=(summary["parents_attempted"] == len(parents)))
            # Keep the SPA stable during long full-inventory walks.
            if idx % 20 == 19:
                await asyncio.sleep(0.35)
        deduped = self.extractor._dedupe(rows)
        summary["rows_collected_after_dedupe"] = len(deduped)
        return {"rows": deduped, "summary": summary}

    def _detail_candidate_urls(self, entity: InventoryEntity) -> List[str]:
        urls: List[str] = []
        if entity.object_type == "partner":
            if entity.parent_account_id:
                urls.append(f"{self.PARTNER_ORIGIN}/authz/accounts/{entity.parent_account_id}/partners/{entity.object_id}")
            urls.append(f"{self.PARTNER_ORIGIN}/authz/partners/{entity.object_id}")
        elif entity.object_type == "system":
            if entity.parent_domain_id:
                urls.append(f"{self.SYSTEM_ORIGIN}/domain-systems/domains/{entity.parent_domain_id}/systems/{entity.object_id}")
            urls.append(f"{self.SYSTEM_ORIGIN}/domain-systems/systems/{entity.object_id}")
            urls.append(f"{self.SYSTEM_ORIGIN}/systems-partners/systems/{entity.object_id}")
        elif entity.object_type == "account":
            urls.append(f"{self.PARTNER_ORIGIN}/authz/accounts/{entity.object_id}")
        elif entity.object_type == "domain":
            urls.append(f"{self.SYSTEM_ORIGIN}/domain-systems/domains/{entity.object_id}")
        elif entity.object_type == "deployment_group" and entity.parent_domain_id:
            urls.append(f"{self.SYSTEM_ORIGIN}/domain-systems/domains/{entity.parent_domain_id}/deployment-groups/{entity.object_id}")
        # Preserve order and remove duplicates.
        out: List[str] = []
        for u in urls:
            if u not in out:
                out.append(u)
        return out

    async def _enrich_detail_records(self, session: BrowserSession, inventory: Dict[str, List[InventoryEntity]]) -> Dict[str, Any]:
        """Best-effort detail-link fan-out for rows already discovered.

        List payloads already contain many fields. Some APIs expose a per-ID detail link;
        when available, merge that response into extra.details_detail_response without
        failing the run if Dell returns 404/500 for a detail variant.
        """
        summary: Dict[str, Any] = {"attempted": 0, "enriched": 0, "failed": 0, "requests": []}
        # Detail endpoints are best-effort and can be noisy; focus on the objects used by APIs.
        candidates: List[InventoryEntity] = []
        for kind in ["partner", "system", "account", "domain", "deployment_group"]:
            candidates.extend(inventory.get(kind, []))
        self._progress_start("detail_link_enrichment", len(candidates), detail=f"Best-effort detail GET for {len(candidates)} inventory rows", counts=self._inventory_counts(inventory))
        for entity_idx, entity in enumerate(candidates):
            # Do not hammer detail endpoints; a full list row is already saved.
            urls = self._detail_candidate_urls(entity)[:2]
            if not urls:
                continue
            entity_result = {"object_type": entity.object_type, "id": entity.object_id, "name": entity.name, "attempts": []}
            got_detail = False
            for url in urls:
                headers = await self._default_headers(session)
                if entity.object_type == "partner" and entity.parent_account_id:
                    headers["x-account-id"] = entity.parent_account_id
                body, meta = await self._fetch_json(session, url, headers=headers, max_attempts=2)
                summary["attempted"] += 1
                entity_result["attempts"].append({k: meta.get(k) for k in ["url", "status", "ok", "row_count", "error"]})
                if not self._request_failed(meta) and isinstance(body, dict):
                    entity.extra.setdefault("detail_responses", []).append({"url": url, "body": body})
                    # Also make the latest detail easy to see in the JSON export.
                    entity.extra["details_detail_response"] = body
                    summary["enriched"] += 1
                    got_detail = True
                    break
            if not got_detail:
                summary["failed"] += 1
            summary["requests"].append(entity_result)
            self._progress_update(current=entity_idx + 1, detail=f"Detail enrichment: {entity_idx + 1}/{len(candidates)} {entity.object_type}={entity.name or entity.object_id}", counts=self._inventory_counts(inventory), force=(entity_idx == len(candidates) - 1))
            if summary["attempted"] >= 750:
                summary["stopped_reason"] = "detail enrichment cap reached; list-row details are still saved"
                break
        return summary

    async def _crawl_partner_inventory(self, session: BrowserSession) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"mode": "api_fanout", "requests": [], "errors": []}
        inventory: Dict[str, List[InventoryEntity]] = _empty_inventory()
        accounts_url = f"{self.PARTNER_ORIGIN}/authz/accounts"
        body, meta = await self._fetch_json(session, accounts_url, headers=await self._default_headers(session))
        summary["requests"].append(meta)
        accounts = self.extractor.from_payload("account", body, accounts_url) if body is not None else []
        inventory["account"].extend(accounts)
        summary["account_count"] = len(accounts)
        self._progress_start("partner_api_fanout", len(accounts), detail=f"Fetched {len(accounts)} Accounts; collecting Partners for every Account", counts=self._inventory_counts(inventory))

        partners_url = f"{self.PARTNER_ORIGIN}/authz/partners"
        partner_total = 0
        for idx, account in enumerate(accounts):
            headers = await self._default_headers(session, {"x-account-id": account.object_id})
            body, meta = await self._fetch_json(session, partners_url, headers=headers)
            meta["parent_account_id"] = account.object_id
            meta["parent_account_name"] = account.name
            summary["requests"].append(meta)
            if self._request_failed(meta):
                ui_rows, ui_meta = await self._ui_collect_children_for_parent(session, area="partner", parent=account, target_kind="partner")
                meta["targeted_ui_fallback"] = ui_meta
                if ui_rows:
                    inventory["partner"].extend(ui_rows)
                    partner_total += len(ui_rows)
                    self._progress_update(current=idx + 1, detail=f"Partner fan-out UI fallback: {idx + 1}/{len(accounts)} Account={account.name or account.object_id} partners={len(ui_rows)}", counts=self._inventory_counts(inventory), force=(idx == len(accounts) - 1))
                    continue
                summary["errors"].append(meta)
                self._progress_update(current=idx + 1, detail=f"Partner fan-out failed: {idx + 1}/{len(accounts)} Account={account.name or account.object_id}", counts=self._inventory_counts(inventory), force=(idx == len(accounts) - 1))
                continue
            parents = {"parent_account_id": account.object_id, "parent_account_name": account.name or ""}
            rows = self.extractor.from_payload("partner", body, partners_url, parents=parents) if body is not None else []
            inventory["partner"].extend(rows)
            partner_total += len(rows)
            self._progress_update(current=idx + 1, detail=f"Partner fan-out: {idx + 1}/{len(accounts)} Account={account.name or account.object_id} partners_in_account={len(rows)}", counts=self._inventory_counts(inventory), force=(idx == len(accounts) - 1))
            # Keep the live site stable; this is still fast but avoids hammering the gateway.
            if idx % 25 == 24:
                await asyncio.sleep(0.15)
        inventory["partner"] = self.extractor._dedupe(inventory["partner"])
        summary["partner_count"] = len(inventory["partner"])
        summary["partner_rows_before_dedupe"] = partner_total
        summary["request_count"] = len(summary["requests"])
        return {"summary": summary, "inventory": inventory}

    async def _crawl_system_inventory(self, session: BrowserSession) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"mode": "api_fanout", "requests": [], "errors": []}
        inventory: Dict[str, List[InventoryEntity]] = _empty_inventory()

        domains_url = f"{self.SYSTEM_ORIGIN}/domain-systems/domains"
        body, meta = await self._fetch_json(session, domains_url, headers=await self._default_headers(session))
        summary["requests"].append(meta)
        domains = self.extractor.from_payload("domain", body, domains_url) if body is not None else []
        inventory["domain"].extend(domains)
        summary["domain_count"] = len(domains)
        self._progress_start("system_domain_discovery", len(domains), detail=f"Fetched {len(domains)} Domains; collecting Systems and Deployment Groups", counts=self._inventory_counts(inventory))

        systems_index_url = f"{self.SYSTEM_ORIGIN}/systems-partners/systems"
        body, meta = await self._fetch_json(session, systems_index_url, headers=await self._default_headers(session))
        summary["requests"].append(meta)
        broad_systems = self.extractor.from_payload("system", body, systems_index_url) if body is not None else []
        inventory["system"].extend(broad_systems)
        summary["broad_system_count"] = len(broad_systems)
        self._progress_update(current=0, detail=f"Broad Systems index collected {len(broad_systems)} rows; now fanning out each Domain", counts=self._inventory_counts(inventory), force=True)

        system_total = 0
        deployment_group_total = 0
        for idx, domain in enumerate(domains):
            parents = {"parent_domain_id": domain.object_id, "parent_domain_name": domain.name or ""}

            url = f"{self.SYSTEM_ORIGIN}/domain-systems/domains/{domain.object_id}/systems"
            body, meta = await self._fetch_json(session, url, headers=await self._default_headers(session))
            meta["parent_domain_id"] = domain.object_id
            meta["parent_domain_name"] = domain.name
            summary["requests"].append(meta)
            if self._request_failed(meta):
                ui_rows, ui_meta = await self._ui_collect_children_for_parent(session, area="system", parent=domain, target_kind="system")
                meta["targeted_ui_fallback"] = ui_meta
                if ui_rows:
                    inventory["system"].extend(ui_rows)
                    system_total += len(ui_rows)
                else:
                    summary["errors"].append(meta)
            else:
                rows = self.extractor.from_payload("system", body, url, parents=parents) if body is not None else []
                inventory["system"].extend(rows)
                system_total += len(rows)

            dg_url = f"{self.SYSTEM_ORIGIN}/domain-systems/domains/{domain.object_id}/deployment-groups"
            dg_body, dg_meta = await self._fetch_json(session, dg_url, headers=await self._default_headers(session))
            dg_meta["parent_domain_id"] = domain.object_id
            dg_meta["parent_domain_name"] = domain.name
            summary["requests"].append(dg_meta)
            if self._request_failed(dg_meta):
                summary["errors"].append(dg_meta)
            else:
                dg_rows = self.extractor.from_payload("deployment_group", dg_body, dg_url, parents=parents) if dg_body is not None else []
                inventory["deployment_group"].extend(dg_rows)
                deployment_group_total += len(dg_rows)

            self._progress_update(current=idx + 1, detail=f"System fan-out: {idx + 1}/{len(domains)} Domain={domain.name or domain.object_id} systems_total={len(inventory['system'])} deployment_groups={len(inventory['deployment_group'])}", counts=self._inventory_counts(inventory), force=(idx == len(domains) - 1))
            if idx % 10 == 9:
                await asyncio.sleep(0.15)
        inventory["system"] = self.extractor._dedupe(inventory["system"])
        inventory["deployment_group"] = self.extractor._dedupe(inventory["deployment_group"])
        summary["domain_system_count"] = system_total
        summary["deployment_group_count"] = len(inventory["deployment_group"])
        summary["deployment_group_rows_before_dedupe"] = deployment_group_total
        summary["system_count"] = len(inventory["system"])
        summary["request_count"] = len(summary["requests"])
        return {"summary": summary, "inventory": inventory}

    def _save_inventory_to_memory(self, inventory: Dict[str, List[InventoryEntity]]) -> None:
        for object_type, rows in inventory.items():
            for row in rows:
                self.memory.save_entity(object_type, _entity_to_extracted(row))
        self.memory._update_entity_index()

    def _relationships(self, inventory: Dict[str, List[InventoryEntity]]) -> List[Dict[str, Any]]:
        rels: List[Dict[str, Any]] = []
        for p in inventory.get("partner", []):
            if p.parent_account_id:
                rels.append({
                    "relationship": "account_has_partner",
                    "parent_type": "account",
                    "parent_id": p.parent_account_id,
                    "parent_name": p.parent_account_name or "",
                    "child_type": "partner",
                    "child_id": p.object_id,
                    "child_name": p.name or "",
                    "source_url": p.source_url,
                })
            for domain_name in [p.extra.get("primary_domain_name"), *(p.extra.get("additional_domain_names") or [])]:
                if domain_name:
                    rels.append({
                        "relationship": "partner_references_domain_name",
                        "parent_type": "partner",
                        "parent_id": p.object_id,
                        "parent_name": p.name or "",
                        "child_type": "domain_name",
                        "child_id": "",
                        "child_name": str(domain_name),
                        "source_url": p.source_url,
                    })
        for s in inventory.get("system", []):
            if s.parent_domain_id:
                rels.append({
                    "relationship": "domain_has_system",
                    "parent_type": "domain",
                    "parent_id": s.parent_domain_id,
                    "parent_name": s.parent_domain_name or "",
                    "child_type": "system",
                    "child_id": s.object_id,
                    "child_name": s.name or "",
                    "source_url": s.source_url,
                })
        for dg in inventory.get("deployment_group", []):
            if dg.parent_domain_id:
                rels.append({
                    "relationship": "domain_has_deployment_group",
                    "parent_type": "domain",
                    "parent_id": dg.parent_domain_id,
                    "parent_name": dg.parent_domain_name or "",
                    "child_type": "deployment_group",
                    "child_id": dg.object_id,
                    "child_name": dg.name or "",
                    "source_url": dg.source_url,
                })
        for a in inventory.get("account", []):
            domains = a.extra.get("account_domain_names") or a.extra.get("domains") or []
            if isinstance(domains, str):
                domains = [domains]
            for d in domains:
                rels.append({
                    "relationship": "account_references_domain_name",
                    "parent_type": "account",
                    "parent_id": a.object_id,
                    "parent_name": a.name or "",
                    "child_type": "domain_name",
                    "child_id": "",
                    "child_name": str(d),
                    "source_url": a.source_url,
                })
        return rels


    def _write_inventory_audit_files(
        self,
        ctx: RunContext,
        *,
        partner_summary: Dict[str, Any],
        system_summary: Dict[str, Any],
        partner_ui_fallback: Optional[Dict[str, Any]] = None,
        system_ui_fallback: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        inv_dir = ctx.run_dir / "inventory"
        inv_dir.mkdir(parents=True, exist_ok=True)
        failed_requests = []
        for area, summary in [("partner", partner_summary), ("system", system_summary)]:
            for req in summary.get("errors") or []:
                item = dict(req)
                item.setdefault("area", area)
                failed_requests.append(item)
        audit = {
            "partner_api_inventory_crawl": partner_summary,
            "system_api_inventory_crawl": system_summary,
            "partner_ui_inventory_fallback": partner_ui_fallback,
            "system_ui_inventory_fallback": system_ui_fallback,
            "failed_request_count": len(failed_requests),
            "failed_requests": failed_requests,
            "status": "partial_success" if failed_requests else "success",
            "note": "Non-200 inventory requests are captured here instead of being hidden behind a success report. UI fallback is attempted when API fan-out has failures.",
        }
        audit_path = inv_dir / "inventory_request_audit.json"
        audit_path.write_text(json.dumps(mask_sensitive_data(audit), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        failed_path = inv_dir / "failed_requests.json"
        failed_path.write_text(json.dumps(mask_sensitive_data(failed_requests), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {
            "inventory_request_audit_json": str(audit_path),
            "inventory_failed_requests_json": str(failed_path),
        }


    def _entity_detail_record(self, entity: InventoryEntity) -> Dict[str, Any]:
        """Return a user-facing detail record for an entity.

        The important part is ``details``: the full row captured from the same
        Network payload the UI used. For example, an Account detail contains the
        account row fields, and a Partner detail contains all fields returned by
        the Account -> Show Partner(s) network call, including identifier,
        contacts, business/support contacts and domain references.
        """
        rec = asdict(entity)
        details = (entity.extra or {}).get("details")
        if isinstance(details, dict):
            rec["details"] = details
        else:
            rec["details"] = {}
        return rec

    def _write_hierarchy_detail_files(self, ctx: RunContext, inventory: Dict[str, List[InventoryEntity]]) -> Dict[str, str]:
        inv_dir = ctx.run_dir / "inventory"
        inv_dir.mkdir(parents=True, exist_ok=True)
        paths: Dict[str, str] = {}

        partners_by_account: Dict[str, List[InventoryEntity]] = {}
        for partner in inventory.get("partner", []):
            partners_by_account.setdefault(partner.parent_account_id or "", []).append(partner)

        systems_by_domain: Dict[str, List[InventoryEntity]] = {}
        for system in inventory.get("system", []):
            systems_by_domain.setdefault(system.parent_domain_id or "", []).append(system)

        deployment_groups_by_domain: Dict[str, List[InventoryEntity]] = {}
        for dg in inventory.get("deployment_group", []):
            deployment_groups_by_domain.setdefault(dg.parent_domain_id or "", []).append(dg)

        account_tree = []
        for account in inventory.get("account", []):
            children = partners_by_account.get(account.object_id, [])
            rec = self._entity_detail_record(account)
            rec["partner_count"] = len(children)
            rec["partners"] = [self._entity_detail_record(p) for p in children]
            account_tree.append(rec)

        domain_tree = []
        for domain in inventory.get("domain", []):
            children = systems_by_domain.get(domain.object_id, [])
            deployment_groups = deployment_groups_by_domain.get(domain.object_id, [])
            rec = self._entity_detail_record(domain)
            rec["system_count"] = len(children)
            rec["systems"] = [self._entity_detail_record(s) for s in children]
            rec["deployment_group_count"] = len(deployment_groups)
            rec["deployment_groups"] = [self._entity_detail_record(dg) for dg in deployment_groups]
            domain_tree.append(rec)

        unparented_partners = [self._entity_detail_record(p) for p in partners_by_account.get("", [])]
        unparented_systems = [self._entity_detail_record(s) for s in systems_by_domain.get("", [])]
        unparented_deployment_groups = [self._entity_detail_record(dg) for dg in deployment_groups_by_domain.get("", [])]

        account_path = inv_dir / "accounts_with_partners_details.json"
        account_path.write_text(json.dumps(mask_sensitive_data(account_tree), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths["inventory_accounts_with_partners_details_json"] = str(account_path)

        domain_path = inv_dir / "domains_with_systems_details.json"
        domain_path.write_text(json.dumps(mask_sensitive_data(domain_tree), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths["inventory_domains_with_systems_details_json"] = str(domain_path)

        tree = {
            "accounts_with_partners": account_tree,
            "domains_with_systems": domain_tree,
            "unparented_partners": unparented_partners,
            "unparented_systems": unparented_systems,
            "unparented_deployment_groups": unparented_deployment_groups,
            "counts": {k: len(v) for k, v in inventory.items()},
            "note": "Detail export mirrors the live nested UI flow: Account -> Show Partner(s) and Domain -> View Domain/System(s). Each child record includes the full details row captured from Network payloads.",
        }
        tree_path = inv_dir / "complete_inventory_tree.json"
        tree_path.write_text(json.dumps(mask_sensitive_data(tree), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths["inventory_complete_tree_json"] = str(tree_path)
        return paths

    def _candidate_record(self, entity: InventoryEntity, *, required_for: Optional[List[str]] = None) -> Dict[str, Any]:
        details = (entity.extra or {}).get("details") if isinstance(entity.extra, dict) else None
        return {
            "object_type": entity.object_type,
            "id": entity.object_id,
            "name": entity.name or "",
            "parent_account_id": entity.parent_account_id or "",
            "parent_account_name": entity.parent_account_name or "",
            "parent_domain_id": entity.parent_domain_id or "",
            "parent_domain_name": entity.parent_domain_name or "",
            "source_url": entity.source_url,
            "field_path": entity.field_path,
            "required_for": required_for or [],
            "details": details if isinstance(details, dict) else {},
        }

    def _build_required_api_id_catalog(self, inventory: Dict[str, List[InventoryEntity]]) -> Dict[str, Any]:
        accounts = [self._candidate_record(a, required_for=["Partner Create", "account-linked APIs", "x-account-id header"]) for a in inventory.get("account", [])]
        partners = [self._candidate_record(p, required_for=["Partner/System/Account linkage", "Flow/TP reference when partner ID is required"]) for p in inventory.get("partner", [])]
        domains = [self._candidate_record(d, required_for=["TP", "Flow", "Orchestration domainId", "Domain -> System lookup"]) for d in inventory.get("domain", [])]
        systems = [self._candidate_record(s, required_for=["source_system_id", "target_system_id", "TP create systemId", "Flow/Orchestration system reference"]) for s in inventory.get("system", [])]
        deployment_groups = [self._candidate_record(dg, required_for=["source_deployment_group_id", "target_deployment_group_id", "TP create deploymentGroupId"]) for dg in inventory.get("deployment_group", [])]

        def dg_type(row: InventoryEntity) -> str:
            details = (row.extra or {}).get("details") if isinstance(row.extra, dict) else {}
            return str((details or {}).get("type") or (row.extra or {}).get("deployment_group_type") or "").lower()

        sender_dgs = [self._candidate_record(dg, required_for=["source_deployment_group_id", "source_deployment_group_name"]) for dg in inventory.get("deployment_group", []) if "sender" in dg_type(dg)]
        receiver_dgs = [self._candidate_record(dg, required_for=["target_deployment_group_id", "target_deployment_group_name"]) for dg in inventory.get("deployment_group", []) if "receiver" in dg_type(dg)]

        lookup: Dict[str, List[Dict[str, Any]]] = {}
        for kind in INVENTORY_KINDS:
            for ent in inventory.get(kind, []):
                for key in {ent.name or "", ent.object_id}:
                    norm = self._norm_text(key)
                    if not norm:
                        continue
                    lookup.setdefault(norm, []).append({
                        "object_type": ent.object_type,
                        "id": ent.object_id,
                        "name": ent.name or "",
                        "parent_account_id": ent.parent_account_id or "",
                        "parent_account_name": ent.parent_account_name or "",
                        "parent_domain_id": ent.parent_domain_id or "",
                        "parent_domain_name": ent.parent_domain_name or "",
                    })

        not_available = {
            "source_document_type_id": "Not available from Partner/System pages. Needs Document Type API/UI inventory.",
            "target_document_type_id": "Not available from Partner/System pages unless a Flow details endpoint is opened later. Existing known candidate can be recorded manually: 10483.",
            "map_id": "Not available from Partner/System pages. Needs Map API/UI inventory. Existing known candidate can be recorded manually: 1670.",
            "source_transport_profile_id": "Usually not available from Partner/System inventory unless Transport Profile or Flow detail endpoints are opened next.",
            "target_transport_profile_id": "Usually not available from Partner/System inventory unless Transport Profile or Flow detail endpoints are opened next.",
            "rule_id": "Not available from Partner/System pages. Needs Rule/Flow details inventory.",
            "workOrderId": "Generated by Workflow API, not a static Partner/System inventory value.",
            "taskId": "Generated/retrieved through Workflow task API, not a static Partner/System inventory value.",
            "workType IDs": "Workflow catalog/API value, not Partner/System inventory.",
            "flowTemplateName": "Can be seen from Flow definition endpoints, not Partner/System inventory itself.",
            "Map status / active value": "Map API metadata, not Partner/System inventory.",
        }

        return {
            "purpose": "Candidates for the ID/value table needed before TP/Flow/API execution, extracted only from Partner and System inventory.",
            "status_rule": "These are candidates. The final source/target choice must be selected by exact system/partner/deployment group name for the transaction.",
            "available_from_partner_system_inventory": {
                "accountId_or_x_account_id_candidates": accounts,
                "partner_id_candidates": partners,
                "domainId_candidates": domains,
                "source_system_id_candidates": systems,
                "target_system_id_candidates": systems,
                "source_system_name_candidates": systems,
                "target_system_name_candidates": systems,
                "source_deployment_group_id_candidates_sender_type": sender_dgs,
                "target_deployment_group_id_candidates_receiver_type": receiver_dgs,
                "all_deployment_group_candidates": deployment_groups,
            },
            "not_available_from_partner_system_inventory": not_available,
            "lookup_by_normalized_name_or_id": lookup,
            "counts": {k: len(v) for k, v in inventory.items()},
        }

    def _write_required_api_id_files(self, ctx: RunContext, inventory: Dict[str, List[InventoryEntity]]) -> Dict[str, str]:
        inv_dir = ctx.run_dir / "inventory"
        inv_dir.mkdir(parents=True, exist_ok=True)
        catalog = self._build_required_api_id_catalog(inventory)
        catalog_path = inv_dir / "api_required_ids_candidates.json"
        catalog_path.write_text(json.dumps(mask_sensitive_data(catalog), indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        lookup_path = inv_dir / "api_id_lookup_by_name.json"
        lookup_path.write_text(json.dumps(mask_sensitive_data(catalog.get("lookup_by_normalized_name_or_id", {})), indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        csv_path = inv_dir / "api_required_ids_candidates.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["required_value", "object_type", "id", "name", "parent_account_id", "parent_account_name", "parent_domain_id", "parent_domain_name", "source_url"])
            writer.writeheader()
            for required_value, rows in catalog["available_from_partner_system_inventory"].items():
                for row in rows:
                    writer.writerow({
                        "required_value": required_value,
                        "object_type": row.get("object_type", ""),
                        "id": row.get("id", ""),
                        "name": row.get("name", ""),
                        "parent_account_id": row.get("parent_account_id", ""),
                        "parent_account_name": row.get("parent_account_name", ""),
                        "parent_domain_id": row.get("parent_domain_id", ""),
                        "parent_domain_name": row.get("parent_domain_name", ""),
                        "source_url": row.get("source_url", ""),
                    })
        return {
            "inventory_api_required_ids_candidates_json": str(catalog_path),
            "inventory_api_id_lookup_by_name_json": str(lookup_path),
            "inventory_api_required_ids_candidates_csv": str(csv_path),
        }

    def _write_partner_system_api_inventory_files(self, ctx: RunContext, inventory: Dict[str, List[InventoryEntity]]) -> Dict[str, str]:
        """Write a compact API-facing ID inventory focused on Partner/System links."""
        inv_dir = ctx.run_dir / "inventory"
        inv_dir.mkdir(parents=True, exist_ok=True)
        rows: List[Dict[str, Any]] = []
        api_field_by_type = {
            "account": "accountId / x-account-id",
            "partner": "partner_id",
            "domain": "domainId",
            "system": "source_system_id / target_system_id",
            "deployment_group": "source_deployment_group_id / target_deployment_group_id",
        }
        for kind in INVENTORY_KINDS:
            for ent in inventory.get(kind, []):
                details = (ent.extra or {}).get("details") if isinstance(ent.extra, dict) else {}
                row = {
                    "api_field_candidate": api_field_by_type.get(kind, kind),
                    "object_type": ent.object_type,
                    "id": ent.object_id,
                    "name": ent.name or "",
                    "parent_account_id": ent.parent_account_id or "",
                    "parent_account_name": ent.parent_account_name or "",
                    "parent_domain_id": ent.parent_domain_id or "",
                    "parent_domain_name": ent.parent_domain_name or "",
                    "numeric_id": ent.object_id if str(ent.object_id).isdigit() else "",
                    "uuid_id": ent.object_id if UUID_RE.match(str(ent.object_id)) else "",
                    "componentId": (details or {}).get("componentId", "") if isinstance(details, dict) else "",
                    "active": (details or {}).get("active", "") if isinstance(details, dict) else "",
                    "source_url": ent.source_url,
                    "field_path": ent.field_path,
                    "details": details if isinstance(details, dict) else {},
                }
                rows.append(row)

        # Systems often appear in two forms: a numeric id from systems-partners/systems
        # and a UUID from domain-systems/domains/<domainId>/systems. Keep both by name.
        system_crosswalk: Dict[str, Dict[str, Any]] = {}
        for ent in inventory.get("system", []):
            key = self._norm_text(ent.name or ent.object_id)
            rec = system_crosswalk.setdefault(key, {"name": ent.name or "", "numeric_system_ids": [], "domain_system_uuid_ids": [], "domains": [], "all_records": []})
            if str(ent.object_id).isdigit() and ent.object_id not in rec["numeric_system_ids"]:
                rec["numeric_system_ids"].append(ent.object_id)
            elif UUID_RE.match(str(ent.object_id)) and ent.object_id not in rec["domain_system_uuid_ids"]:
                rec["domain_system_uuid_ids"].append(ent.object_id)
            if ent.parent_domain_id or ent.parent_domain_name:
                dom = {"domainId": ent.parent_domain_id or "", "domainName": ent.parent_domain_name or ""}
                if dom not in rec["domains"]:
                    rec["domains"].append(dom)
            rec["all_records"].append({"id": ent.object_id, "parent_domain_id": ent.parent_domain_id or "", "parent_domain_name": ent.parent_domain_name or "", "source_url": ent.source_url})

        payload = {
            "purpose": "Operational IDs from BizLink Partner and System links for API execution readiness.",
            "counts": {k: len(v) for k, v in inventory.items()},
            "rows": rows,
            "system_id_crosswalk_by_normalized_name": system_crosswalk,
        }
        json_path = inv_dir / "partner_system_api_id_inventory.json"
        json_path.write_text(json.dumps(mask_sensitive_data(payload), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        csv_path = inv_dir / "partner_system_api_id_inventory.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            fieldnames = ["api_field_candidate", "object_type", "id", "name", "parent_account_id", "parent_account_name", "parent_domain_id", "parent_domain_name", "numeric_id", "uuid_id", "componentId", "active", "source_url", "field_path"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
        return {
            "inventory_partner_system_api_id_inventory_json": str(json_path),
            "inventory_partner_system_api_id_inventory_csv": str(csv_path),
        }

    def _write_inventory_files(self, ctx: RunContext, inventory: Dict[str, List[InventoryEntity]]) -> Dict[str, str]:
        inv_dir = ctx.run_dir / "inventory"
        inv_dir.mkdir(parents=True, exist_ok=True)
        payload = {k: [asdict(x) for x in rows] for k, rows in inventory.items()}
        paths: Dict[str, str] = {}
        all_path = inv_dir / "all_entities.json"
        all_path.write_text(json.dumps(mask_sensitive_data(payload), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths["inventory_all_entities_json"] = str(all_path)
        for kind, rows in payload.items():
            p = inv_dir / f"{kind}s.json"
            p.write_text(json.dumps(mask_sensitive_data(rows), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            paths[f"inventory_{kind}s_json"] = str(p)
        rels = self._relationships(inventory)
        rel_json = inv_dir / "relationships.json"
        rel_json.write_text(json.dumps(mask_sensitive_data(rels), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths["inventory_relationships_json"] = str(rel_json)

        csv_path = inv_dir / "all_entities.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["object_type", "object_id", "name", "parent_account_id", "parent_account_name", "parent_domain_id", "parent_domain_name", "source_url", "field_path"])
            writer.writeheader()
            for rows in inventory.values():
                for row in rows:
                    writer.writerow({
                        "object_type": row.object_type,
                        "object_id": row.object_id,
                        "name": row.name or "",
                        "parent_account_id": row.parent_account_id or "",
                        "parent_account_name": row.parent_account_name or "",
                        "parent_domain_id": row.parent_domain_id or "",
                        "parent_domain_name": row.parent_domain_name or "",
                        "source_url": row.source_url,
                        "field_path": row.field_path,
                    })
        paths["inventory_all_entities_csv"] = str(csv_path)

        rel_csv = inv_dir / "relationships.csv"
        with rel_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["relationship", "parent_type", "parent_id", "parent_name", "child_type", "child_id", "child_name", "source_url"])
            writer.writeheader()
            for row in rels:
                writer.writerow(row)
        paths["inventory_relationships_csv"] = str(rel_csv)
        paths.update(self._write_hierarchy_detail_files(ctx, inventory))
        paths.update(self._write_required_api_id_files(ctx, inventory))
        paths.update(self._write_partner_system_api_inventory_files(ctx, inventory))
        return paths
