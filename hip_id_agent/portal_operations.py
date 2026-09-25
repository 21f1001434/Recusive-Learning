"""Run portal operations from input.json: create, edit, clone, merge, deploy ... (V243R19).

input.json names what to do in an ``operations`` list::

    "operations": [
      {"phase": "source_transport_profile", "operation": "edit",
       "target": "SFTP_U-HAUL_ASN_PC_SRC_IB", "values": {...}, "commit": true},
      {"phase": "source_transport_profile", "operation": "deploy",
       "target": "SFTP_U-HAUL_ASN_PC_SRC_IB", "values": {"target_environment": "UAT"}, "commit": true}
    ]

``values`` defaults to ``objects.<phase>`` of the same file.  For each operation
the runner:

1. opens the surface: the listing, a search for the target, then the row's
   action (directly or from its "More actions" menu); ``create`` opens + Add;
2. fills it with the certified-skill goal engine: a known skill for this
   phase + operation + branch is replayed deterministically; anything new is
   learned adaptively and proved by a replay on a freshly reopened surface
   before it is saved (``portal_skills``);
3. commits (Save / Deploy / Merge ...) only when ``commit`` is true, the fill
   was exact, the skill is certified and the three-part mutation gate is open
   (``--allow-portal-mutation``, ``HIP_ALLOW_PORTAL_MUTATION=YES`` and the phrase
   ``ALLOW HIP MUTATION``).  A commit is clicked at most once, never retried
   blindly;
4. verifies the effect: the surface closes or reports success, and the listing
   shows the object (and any ``expect`` text such as a new status).

Create / edit / clone fill the phase form itself.  Merge, deploy and other
row actions open their own dialog, which is learned as its own form
(``universal_<phase>_<operation>``).
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .portal_skills import COMMIT_LABELS, PortalSkillStore, canonical_operation, operation_specs
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.portal-operation.v1"
MUTATION_CONFIRMATION = "ALLOW HIP MUTATION"
PHASE_FORM_OPERATIONS = {"create", "edit", "clone"}
OPENER_LABELS: Dict[str, Sequence[str]] = {
    "edit": ("Edit", "Update", "Modify"),
    "clone": ("Clone", "Copy", "Duplicate"),
    "merge": ("Merge",),
    "deploy": ("Deploy",),
    "migrate": ("Migrate", "Promote"),
    "validate": ("Validate",),
    "delete": ("Delete", "Remove"),
}
ADD_LABELS = ("+ Add", "Add", "+ Create", "New", "+ New")
NAME_KEYS = (
    "profile_name", "name", "map_name", "map_identifier", "business_flow_name", "rule_name",
    "document_type_name", "flow_name",
)
# The browser safety guard blocks these words unless explicitly authorized.
_GUARDED = re.compile(r"\b(save|create|submit|delete|remove|deploy|publish|update|enable|disable|confirm)\b", re.I)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").split())


def operation_gate(allow_portal_mutation: bool, confirmation: str) -> Dict[str, Any]:
    env_ok = str(os.getenv("HIP_ALLOW_PORTAL_MUTATION", "")).strip().upper() == "YES"
    phrase_ok = str(confirmation or "").strip() == MUTATION_CONFIRMATION
    return {
        "pass": bool(allow_portal_mutation and env_ok and phrase_ok), "explicit_flag": bool(allow_portal_mutation),
        "environment_gate": env_ok, "confirmation_gate": phrase_ok, "required_confirmation": MUTATION_CONFIRMATION,
    }


def _name_in(values: Mapping[str, Any]) -> str:
    for key in NAME_KEYS:
        if isinstance(values.get(key), str) and values.get(key):
            return str(values[key])
    for child in values.values():
        if isinstance(child, Mapping):
            found = _name_in(child)
            if found:
                return found
    return ""


def _verify_name(spec: Mapping[str, Any], operation: str, values: Mapping[str, Any], target: str) -> str:
    """Which listing row proves the commit: the saved object, or a merge's target."""
    if spec.get("verify_name"):
        return str(spec["verify_name"])
    if operation in {"create", "clone", "edit"} and _name_in(values):
        return _name_in(values)
    if operation == "merge":
        # The source may be merged away; the object merged into must show it.
        for key, value in values.items():
            if isinstance(value, str) and value and re.search(r"into|target|destination", str(key), re.I):
                return value
    return target


def form_phase_for(phase: str, operation: str, spec: Mapping[str, Any]) -> str:
    """The form an operation fills: the phase form, or the action's own dialog."""
    form = str(spec.get("form") or "").lower()
    if form == "phase" or (not form and operation in PHASE_FORM_OPERATIONS):
        return phase
    return f"universal_{phase}_{operation}"


_ROW_ACTION_JS = r"""
({target, labels, token}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const textOf = el => norm(el.getAttribute('aria-label') || el.innerText || el.textContent || el.getAttribute('title') || el.value);
  const wanted = labels.map(norm).filter(Boolean);
  const t = norm(target);
  // A row whose own cell is exactly the target beats one that only contains it
  // ("TP_BETA" must not open "TP_BETA_COPY").
  const exact = r => !!t && Array.from(r.querySelectorAll('td,th,[role=cell],[role=gridcell],a,span')).some(c => norm(c.innerText) === t);
  const rows = Array.from(document.querySelectorAll('tr,[role=row],.dds__table__row,[class*=card],li'))
    .filter(visible).filter(r => !t || norm(r.innerText).includes(t))
    .sort((a, b) => (exact(b) - exact(a)) || ((a.innerText || '').length - (b.innerText || '').length));
  const pick = root => {
    const els = Array.from(root.querySelectorAll('button,a,[role=button],[role=menuitem],[role=link]')).filter(visible);
    for (const w of wanted) {
      const hit = els.find(e => textOf(e) === w) || els.find(e => textOf(e).split(/[^a-z0-9+]+/).includes(w));
      if (hit) return hit;
    }
    return null;
  };
  for (const row of rows) {
    const hit = pick(row);
    if (hit) { hit.setAttribute('data-hip-op', token); return {found: 'row_action', label: textOf(hit), rows: rows.length}; }
  }
  for (const row of rows) {
    const els = Array.from(row.querySelectorAll('button,a,[role=button]')).filter(visible);
    const menu = els.find(e => e.getAttribute('aria-haspopup') || /\b(more|actions|options|menu)\b|⋮|…|\.\.\./.test(textOf(e)));
    if (menu) { menu.setAttribute('data-hip-op', token); return {found: 'menu', label: textOf(menu), rows: rows.length}; }
  }
  return {found: '', rows: rows.length};
}
"""

_MENU_ITEM_JS = r"""
({labels, token}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const textOf = el => norm(el.getAttribute('aria-label') || el.innerText || el.textContent);
  const items = Array.from(document.querySelectorAll('[role=menuitem],[role=menu] button,[role=menu] a,.dds__dropdown__item-option')).filter(visible);
  for (const w of labels.map(norm)) {
    const hit = items.find(e => textOf(e) === w);
    if (hit) { hit.setAttribute('data-hip-op', token); return {found: true, label: textOf(hit)}; }
  }
  return {found: false};
}
"""

_PAGE_BUTTON_JS = r"""
({labels, token, surfaceOnly}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.disabled; };
  const textOf = el => norm(el.innerText || el.textContent || el.getAttribute('aria-label') || el.value);
  let scope = document;
  if (surfaceOnly) {
    const surfaces = Array.from(document.querySelectorAll('[role=dialog],dds-drawer,.dds__drawer,.dds__modal,form')).filter(visible);
    if (surfaces.length) scope = surfaces[surfaces.length - 1];
  }
  const els = Array.from(scope.querySelectorAll('button,a,[role=button],input[type=submit]')).filter(visible)
    .filter(e => !e.closest('tr,[role=row],[role=menu]'));
  for (const w of labels.map(norm)) {
    const hit = els.find(e => textOf(e) === w);
    if (hit) { hit.setAttribute('data-hip-op', token); return {found: true, label: textOf(hit)}; }
  }
  return {found: false};
}
"""

_EFFECT_JS = r"""
({token}) => {
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const texts = sel => Array.from(document.querySelectorAll(sel)).filter(visible).map(e => (e.innerText || '').trim()).filter(Boolean);
  const errors = texts('[role=alert],.toast--error,.dds__notification--error,.dds__invalid-feedback,.error-message');
  const notes = texts('[role=status],.toast,.dds__notification,.dds__toast');
  const button = document.querySelector(`[data-hip-op="${token}"]`);
  return {url: location.href, errors, notes, button_visible: visible(button)};
}
"""


class PortalOperationRunner:
    """Opens, fills, commits and verifies input.json operations in one browser."""

    def __init__(self, config: Any, browser: Any, run_dir: Path, *, listing_urls: Optional[Mapping[str, str]] = None):
        self.config = config
        self.browser = browser
        self.run_dir = Path(run_dir)
        self.listing_urls = dict(listing_urls or {})
        ops_cfg = getattr(config, "portal_operations", None)
        self.require_certified = bool(getattr(ops_cfg, "commit_requires_certified_skill", True))
        self.verify_after_commit = bool(getattr(ops_cfg, "verify_after_commit", True))
        self.effect_timeout = float(getattr(ops_cfg, "commit_effect_timeout_seconds", 20.0) or 20.0)

    # ------------------------------------------------------------------ helpers
    def _listing_url(self, phase: str) -> str:
        if phase in self.listing_urls:
            return self.listing_urls[phase]
        from .dummy_fill_e2e import PHASE_URLS

        return str(PHASE_URLS.get(phase) or "")

    async def _page(self, url: str = "") -> Any:
        return await self.browser._ensure_active_page(url or getattr(self.browser, "_active_target_url", "") or "")

    async def _navigate(self, url: str) -> None:
        await self.browser.goto_base_and_complete_sso(url)
        page = await self._page(url)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await self.browser.wait_ready()

    async def _click_token(self, token: str, label: str, *, mutation: bool = False) -> None:
        page = await self._page()
        selector = f'[data-hip-op="{token}"]'
        await self.browser.click_and_wait(action=label, locator=page.locator(selector), selector=selector, mutation_risk=mutation)

    async def _search(self, target: str) -> Dict[str, Any]:
        page = await self._page()
        token = uuid.uuid4().hex[:10]
        found = await page.evaluate(
            r"""(token) => {
              const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
              const box = Array.from(document.querySelectorAll('input[type=search],input[placeholder],input[aria-label]'))
                .filter(visible).find(e => /search|filter/i.test(e.type + ' ' + (e.placeholder || '') + ' ' + (e.getAttribute('aria-label') || '')));
              if (!box) return false;
              box.setAttribute('data-hip-op', token); return true;
            }""", token)
        if not found:
            return {"searched": False}
        selector = f'[data-hip-op="{token}"]'
        locator = page.locator(selector)
        await self.browser.fill_and_log(locator=locator, value=target, selector=selector, action_type="search")
        try:
            await self.browser.press_and_log(locator=locator, key="Enter", selector=selector)
        except Exception:
            pass
        await page.wait_for_timeout(400)
        return {"searched": True}

    async def _wait_for_form(self, form_phase: str, timeout_s: float = 12.0) -> bool:
        from .stateful_form_runtime import capture_stateful_controls

        page = await self._page()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if await capture_stateful_controls(page, form_phase):
                    return True
            except Exception:
                pass
            await page.wait_for_timeout(250)
        return False

    # ------------------------------------------------------------------ open
    async def open_surface(self, *, phase: str, operation: str, target: str, form_phase: str) -> Dict[str, Any]:
        url = self._listing_url(phase)
        audit: Dict[str, Any] = {"listing_url": url, "operation": operation, "path": []}
        await self._navigate(url)
        page = await self._page(url)
        if operation == "create":
            token = uuid.uuid4().hex[:10]
            hit = await page.evaluate(_PAGE_BUTTON_JS, {"labels": list(ADD_LABELS), "token": token, "surfaceOnly": False})
            if not hit.get("found"):
                # Live portal: the semantic affordance resolver knows the Add drawer.
                await self.browser.click_semantic_affordance(
                    intent="open_add_form", aliases=["Add", "Create"], allow_mutation=False,
                    action_label="Open create form", allow_compound_menu=False,
                )
                audit["path"].append("semantic:open_add_form")
            else:
                await self._click_token(token, str(hit.get("label") or "Add"))
                audit["path"].append(str(hit.get("label")))
            if phase == "biz_flow":
                from .bizflow_kb import _click_bizflow_template_link_after_add, _is_bizflow_form_surface

                if not await _is_bizflow_form_surface(page):
                    audit["bizflow_template"] = mask_sensitive_data(await _click_bizflow_template_link_after_add(page))
        else:
            if target:
                audit["search"] = await self._search(target)
            labels = list(OPENER_LABELS.get(operation) or (operation.replace("_", " ").title(),))
            start_net = len(getattr(self.browser, "network_tab_events", []) or [])
            token = uuid.uuid4().hex[:10]
            hit = await page.evaluate(_ROW_ACTION_JS, {"target": target, "labels": labels, "token": token})
            if hit.get("found") == "row_action":
                await self._click_token(token, str(hit.get("label") or labels[0]), mutation=bool(_GUARDED.search(labels[0])))
                audit["path"].append(str(hit.get("label")))
            elif hit.get("found") == "menu":
                await self._click_token(token, str(hit.get("label") or "More actions"))
                audit["path"].append(str(hit.get("label") or "more actions"))
                item_token = uuid.uuid4().hex[:10]
                item = {}
                for _ in range(10):
                    item = await page.evaluate(_MENU_ITEM_JS, {"labels": labels, "token": item_token})
                    if item.get("found"):
                        break
                    await page.wait_for_timeout(150)
                if not item.get("found"):
                    raise RuntimeError(f"HIP_OPERATION_ACTION_NOT_IN_MENU: {operation} for {target!r}")
                await self._click_token(item_token, str(item.get("label") or labels[0]), mutation=bool(_GUARDED.search(labels[0])))
                audit["path"].append(str(item.get("label")))
            else:
                # Icon-only or unusual markup: the live semantic resolver (compound menus).
                await self.browser.click_semantic_affordance(
                    intent=operation, aliases=[labels[0], target], allow_mutation=bool(_GUARDED.search(labels[0])),
                    action_label=labels[0], allow_compound_menu=True,
                )
                audit["path"].append(f"semantic:{operation}")
        audit["form_visible"] = await self._wait_for_form(form_phase)
        if operation != "create":
            opener = (OPENER_LABELS.get(operation) or (operation.title(),))[0]
            if _GUARDED.search(opener):
                audit["opener_outcome"] = await self._reconcile_opener(start_net, form_visible=bool(audit["form_visible"]))
        if audit["form_visible"]:
            # The proven surface's route is now the expected route: the commit
            # guard rejects any drift away from it before Save/Deploy.
            page = await self._page()
            self.browser._active_target_url = str(page.url or url)
            audit["surface_url"] = self.browser._evidence_url(str(page.url or "")) if hasattr(self.browser, "_evidence_url") else ""
        return audit

    # ------------------------------------------------------------------ fill
    async def fill(
        self, *, phase: str, form_phase: str, payload: Dict[str, Any], out_dir: Path, reopen: Any,
    ) -> Dict[str, Any]:
        from .autonomous_form_runtime import execute_autonomous_phase_goal
        from .stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph

        page = await self._page()
        graph = compile_phase_state_graph(payload, form_phase)
        kwargs: Dict[str, Any] = {"executor": execute_document_type_state_graph} if "document_type" in form_phase and not form_phase.startswith("universal_") else {}
        max_cycles = int(getattr(getattr(self.config, "autonomous_form", None), "max_cycles", 4) or 4)
        if form_phase == "biz_flow":
            return await self._fill_bizflow(page, graph, payload, out_dir, max_cycles)
        return await execute_autonomous_phase_goal(
            page=page, graph=graph, phase=form_phase, input_data=payload, config=self.config,
            output_dir=out_dir, max_cycles=max_cycles, repair=True, strict_live_execution=True,
            reopen=reopen, **kwargs,
        )

    async def _fill_bizflow(self, page: Any, graph: Dict[str, Any], payload: Dict[str, Any], out_dir: Path, max_cycles: int) -> Dict[str, Any]:
        """BizFlow tabs: one skill per tab; proved on the next run (a tab cannot be reopened alone)."""
        from .autonomous_form_runtime import execute_autonomous_phase_goal
        from .bizflow_kb import _click_configure_routing_add
        from .dds_control_driver import click_visible_tab

        wizard = [
            ("Flow Details", ["Flow Details", "Basic Details"]),
            ("Configure Source", ["Configure Source", "Source Details"]),
            ("Configure Target(s)", ["Configure Target(s)", "Configure Target", "Target Details"]),
            ("Configure Routing", ["Configure Routing"]),
        ]
        present = {str(n.get("section") or "") for n in graph.get("nodes") or [] if isinstance(n, Mapping)}
        sections: List[Dict[str, Any]] = []
        for name, aliases in wizard:
            if name not in present:
                continue
            await click_visible_tab(page, None, aliases, phase="biz_flow")
            if name == "Configure Routing":
                await _click_configure_routing_add(page)
            result = await execute_autonomous_phase_goal(
                page=page, graph=graph, phase="biz_flow", input_data=payload, config=self.config,
                output_dir=out_dir / re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"), max_cycles=max_cycles,
                repair=True, strict_live_execution=True, section=name,
            )
            sections.append(result)
            if not result.get("pass"):
                break
        passed = bool(sections) and all(r.get("pass") for r in sections)
        statuses = [str(((r.get("skill") or {}).get("outcome") or {}).get("status") or "") for r in sections]
        return {
            "pass": passed, "status": "pass" if passed else "failed_closed", "sections": sections,
            "execution_mode": "+".join(sorted({str(r.get("execution_mode")) for r in sections})),
            "skill": {"outcome": {"status": "certified" if passed and all(s == "certified" for s in statuses) else "candidate"},
                      "sections": [r.get("skill") for r in sections]},
        }

    # ------------------------------------------------------------------ commit
    async def commit(self, *, labels: Sequence[str], gate: Mapping[str, Any], task_id: str, entity: str = "") -> Dict[str, Any]:
        page = await self._page()
        token = uuid.uuid4().hex[:10]
        hit = await page.evaluate(_PAGE_BUTTON_JS, {"labels": list(labels), "token": token, "surfaceOnly": True})
        if not hit.get("found"):
            hit = await page.evaluate(_PAGE_BUTTON_JS, {"labels": list(labels), "token": token, "surfaceOnly": False})
        if not hit.get("found"):
            return {"pass": False, "status": "commit_control_not_found", "labels": list(labels)}
        label = str(hit.get("label") or labels[0])
        before_url = page.url
        start_net = len(getattr(self.browser, "network_tab_events", []) or [])
        self.browser.set_portal_mutation_authorization(enabled=True, allowed_labels=list(labels), task_id=task_id)
        click_error = ""
        try:
            # Clicked once.  An unclear outcome is reported, never retried blindly.
            await self._click_token(token, label, mutation=True)
        except Exception as exc:
            click_error = mask_sensitive_string(str(exc))[:500]
        finally:
            self.browser.clear_portal_mutation_authorization()
        effect = await self._await_effect(token, before_url) if not click_error else {"pass": False, "status": "commit_click_error"}
        effect["reconciliation"] = await self._reconcile(start_net, label=label, entity=entity, effect=effect, click_error=click_error)
        if click_error:
            effect["error"] = click_error
        if effect["reconciliation"].get("classification") == "rejected_verified":
            effect.update({"pass": False, "status": "portal_rejected_commit"})
        elif effect["reconciliation"].get("classification") in {"committed_verified", "committed_verified_after_transport_or_ui_error"}:
            effect.update({"pass": True, "status": "committed"})
        return dict(effect, label=label)

    async def _reconcile_opener(self, start_net: int, *, form_visible: bool) -> Dict[str, Any]:
        """A guarded row action (Deploy ...) either opened its dialog or acted at once."""
        from .certified_future_task_agent import CertifiedHIPFutureTaskExecutor

        dispatch = self.browser.last_click_dispatch_evidence() if hasattr(self.browser, "last_click_dispatch_evidence") else {}
        try:
            rows = CertifiedHIPFutureTaskExecutor._network_rows_since(self.browser, start_net, dispatch=dispatch)
        except Exception:
            rows = []
        writes = [r for r in rows if str(r.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}]
        if form_visible and not writes and hasattr(self.browser, "resolve_mutation_dispatch_guard"):
            return {"classification": "opened_surface_no_write",
                    "mutation_dispatch_guard": self.browser.resolve_mutation_dispatch_guard({"classification": "opened_surface_no_write"})}
        return await self._reconcile(start_net, label="opener", entity="", effect={"pass": False}, click_error="")

    async def _reconcile(self, start_net: int, *, label: str, entity: str, effect: Mapping[str, Any], click_error: str) -> Dict[str, Any]:
        """Classify the commit from write responses and UI signals; release the
        session's mutation guard only for an authoritative outcome."""
        from .certified_future_task_agent import CertifiedHIPFutureTaskExecutor
        from .mutation_outcome import classify_mutation_outcome

        dispatch = self.browser.last_click_dispatch_evidence() if hasattr(self.browser, "last_click_dispatch_evidence") else {}
        rows: List[Dict[str, Any]] = []
        deadline = time.monotonic() + 3.0
        while True:
            try:
                rows = CertifiedHIPFutureTaskExecutor._network_rows_since(self.browser, start_net, dispatch=dispatch)
            except Exception:
                rows = []
            if any(str(r.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"} and r.get("status") is not None for r in rows):
                break
            if time.monotonic() >= deadline or not dispatch.get("dispatch_attempted"):
                break
            await asyncio.sleep(0.25)
        ui = {"success": bool(effect.get("pass")) and bool(effect.get("notes")), "error": effect.get("status") == "portal_reported_error"}
        outcome = classify_mutation_outcome(
            dispatch=dispatch, network_rows=rows, ui_signal=ui,
            structural_change=bool(effect.get("surface_closed")), action_error=click_error,
        )
        if hasattr(self.browser, "resolve_mutation_dispatch_guard"):
            outcome["mutation_dispatch_guard"] = self.browser.resolve_mutation_dispatch_guard(outcome)
        return mask_sensitive_data({k: outcome.get(k) for k in (
            "classification", "pass", "reason", "successful_2xx_write_response_count", "failed_write_response_count",
            "write_request_count", "mutation_dispatch_guard", "automatic_mutation_retry_allowed")})

    async def _await_effect(self, token: str, before_url: str) -> Dict[str, Any]:
        deadline = time.monotonic() + self.effect_timeout
        last: Dict[str, Any] = {}
        success = re.compile(r"success|saved|created|updated|deployed|merged|cloned|completed|submitted", re.I)
        while time.monotonic() < deadline:
            page = await self._page()
            try:
                last = await page.evaluate(_EFFECT_JS, {"token": token})
            except Exception:
                # The page navigated away from the form: the surface closed.
                await asyncio.sleep(0.3)
                continue
            if last.get("errors"):
                return {"pass": False, "status": "portal_reported_error", "errors": [mask_sensitive_string(x)[:300] for x in last["errors"][:3]]}
            if any(success.search(x) for x in last.get("notes") or []) or last.get("url") != before_url or not last.get("button_visible"):
                return {"pass": True, "status": "committed", "notes": [mask_sensitive_string(x)[:200] for x in (last.get("notes") or [])[:3]],
                        "surface_closed": not last.get("button_visible") or last.get("url") != before_url}
            await asyncio.sleep(0.25)
        return {"pass": False, "status": "commit_outcome_unknown", "detail": "no success, error or closed surface within the timeout; not retried"}

    async def verify_listing(self, *, phase: str, name: str, expect: Sequence[str]) -> Dict[str, Any]:
        url = self._listing_url(phase)
        await self._navigate(url)
        if name:
            await self._search(name)
        page = await self._page(url)
        rows = await page.evaluate(
            r"""(name) => {
              const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const want = norm(name);
              const exact = r => Array.from(r.querySelectorAll('td,th,[role=cell],[role=gridcell],a,span')).some(c => norm(c.innerText) === want);
              return Array.from(document.querySelectorAll('tr,[role=row],.dds__table__row,[class*=card]'))
                .filter(r => r.getBoundingClientRect().height > 0)
                .filter(r => !want || norm(r.innerText).includes(want))
                .sort((a, b) => exact(b) - exact(a))
                .map(r => (r.innerText || '').replace(/\s+/g, ' ').trim());
            }""", name)
        row = rows[0] if rows else ""
        missing = [x for x in expect if _norm(x) not in _norm(row)]
        return {"pass": bool(row) and not missing, "row_found": bool(row), "missing": missing, "row_text": mask_sensitive_string(row)[:300]}

    # ------------------------------------------------------------------ run
    async def run(self, input_data: Mapping[str, Any], *, allow_portal_mutation: bool = False, confirmation: str = "") -> Dict[str, Any]:
        specs = operation_specs(input_data)
        gate = operation_gate(allow_portal_mutation, confirmation)
        started = time.monotonic()
        report: Dict[str, Any] = {"schema_version": SCHEMA, "operations": [], "mutation_gate": gate}
        for index, spec in enumerate(specs, start=1):
            try:
                row = await self.run_one(spec, input_data, gate, index=index)
            except Exception as exc:
                from .environment_faults import is_environment_fatal

                row = {"phase": spec.get("phase"), "operation": spec.get("operation"), "pass": False,
                       "status": "error", "error": mask_sensitive_string(str(exc))[:800],
                       "environment_fault": is_environment_fatal(exc)}
            report["operations"].append(row)
            if row.get("environment_fault"):
                break
        report["pass"] = bool(specs) and all(r.get("pass") for r in report["operations"])
        report["seconds"] = round(time.monotonic() - started, 1)
        safe_write_json(self.run_dir / "portal_operations.json", mask_sensitive_data(report))
        return mask_sensitive_data(report)

    async def run_one(self, spec: Mapping[str, Any], input_data: Mapping[str, Any], gate: Mapping[str, Any], *, index: int = 1) -> Dict[str, Any]:
        started = time.monotonic()
        phase = str(spec.get("phase") or "")
        operation = canonical_operation(spec.get("operation"))
        objects = input_data.get("objects") if isinstance(input_data.get("objects"), Mapping) else {}
        values = dict(spec.get("values") or objects.get(phase) or {})
        target = str(spec.get("target") or (_name_in(objects.get(phase) or {}) if operation != "create" else "") or _name_in(values))
        form_phase = form_phase_for(phase, operation, spec)
        commit_wanted = bool(spec.get("commit", False))
        out_dir = self.run_dir / "operations" / f"{index:02d}_{phase}_{operation}"
        row: Dict[str, Any] = {"phase": phase, "operation": operation, "form": form_phase, "target": target,
                               "commit_requested": commit_wanted}
        opener = (OPENER_LABELS.get(operation) or (operation.title(),))[0]
        if operation != "create" and _GUARDED.search(opener) and not gate.get("pass"):
            # The row action itself (Deploy, Delete ...) is a portal mutation.
            row.update({"pass": False, "status": "blocked_mutation_authorization",
                        "reason": f"opening {opener!r} is itself a portal mutation; the three-part gate is closed"})
            return row
        task_id = f"op-{uuid.uuid4().hex[:10]}"
        payload: Dict[str, Any] = {"objects": {form_phase: values}, "_operation": operation}
        for key in ("_upload_assets_dir", "_input_json_path"):
            if key in input_data:
                payload[key] = input_data[key]
        if gate.get("pass") and _GUARDED.search(opener):
            self.browser.set_portal_mutation_authorization(enabled=True, allowed_labels=[opener], task_id=task_id)
        try:
            row["open"] = await self.open_surface(phase=phase, operation=operation, target=target, form_phase=form_phase)
            reopen_count = {"n": 0}

            async def reopen() -> None:
                reopen_count["n"] += 1
                await self.open_surface(phase=phase, operation=operation, target=target, form_phase=form_phase)

            fill = await self.fill(phase=phase, form_phase=form_phase, payload=payload, out_dir=out_dir, reopen=reopen)
        finally:
            if gate.get("pass") and _GUARDED.search(opener):
                self.browser.clear_portal_mutation_authorization()
        skill = fill.get("skill") if isinstance(fill.get("skill"), dict) else {}
        outcome = skill.get("outcome") if isinstance(skill.get("outcome"), dict) else {}
        certified = outcome.get("status") == "certified"
        row["fill"] = {
            "pass": bool(fill.get("pass")), "status": fill.get("status"), "execution_mode": fill.get("execution_mode"),
            "skill_plan": skill.get("plan"), "skill_reason": skill.get("reason"), "skill_status": outcome.get("status") or skill.get("skill_status"),
            "novelty": skill.get("novelty") or [], "learn_seconds": skill.get("learn_seconds"), "replay_seconds": skill.get("replay_seconds"),
            "reopened": reopen_count["n"],
        }
        if not fill.get("pass"):
            row.update({"pass": False, "status": "fill_not_proven", "failure_summary": fill.get("failure_summary") or {}})
            return self._finish(row, started, out_dir)
        if not commit_wanted:
            row.update({"pass": True, "status": "filled_not_committed"})
            return self._finish(row, started, out_dir)
        if not gate.get("pass"):
            row.update({"pass": False, "status": "commit_not_authorized",
                        "reason": "commit requested but the three-part mutation gate is closed; the form was filled and left unsaved"})
            return self._finish(row, started, out_dir)
        if self.require_certified and not certified:
            row.update({"pass": False, "status": "commit_waiting_for_certified_skill",
                        "reason": "the skill is not certified by a deterministic replay yet; nothing was saved"})
            return self._finish(row, started, out_dir)
        skill_id = str(outcome.get("skill_id") or skill.get("skill_id") or "")
        store = PortalSkillStore.for_run(self.config, await self._page())
        learned = store.commit_label(form_phase, skill_id) if store is not None else ""
        labels = list(dict.fromkeys([x for x in [learned, *(spec.get("commit_labels") or []), *COMMIT_LABELS.get(operation, ("Save",))] if x]))
        committed = await self.commit(labels=labels, gate=gate, task_id=task_id, entity=target)
        row["commit"] = committed
        if committed.get("pass") and store is not None and skill_id:
            store.record_commit(form_phase, skill_id, str(committed.get("label") or ""))
        if not committed.get("pass"):
            row.update({"pass": False, "status": committed.get("status") or "commit_failed"})
            return self._finish(row, started, out_dir)
        expect = [str(x) for x in (spec.get("expect") or [])] if isinstance(spec.get("expect"), list) else (
            [str(v) for v in (spec.get("expect") or {}).values()] if isinstance(spec.get("expect"), Mapping) else [])
        name = _verify_name(spec, operation, values, target)
        if self.verify_after_commit:
            row["verification"] = await self.verify_listing(phase=phase, name=name, expect=expect)
            row["pass"] = bool(row["verification"].get("pass"))
            row["status"] = "committed_and_verified" if row["pass"] else "committed_verification_failed"
            guard = (committed.get("reconciliation") or {}).get("mutation_dispatch_guard") or {}
            if row["pass"] and guard.get("retained") and hasattr(self.browser, "resolve_mutation_dispatch_guard"):
                # No write response was captured, but the listing now shows the
                # committed state: that is the authoritative outcome.
                row["reconciliation_by_listing"] = self.browser.resolve_mutation_dispatch_guard(
                    {"classification": "committed_verified", "pass": True})
        else:
            row.update({"pass": True, "status": "committed"})
        return self._finish(row, started, out_dir)

    def _finish(self, row: Dict[str, Any], started: float, out_dir: Path) -> Dict[str, Any]:
        row["seconds"] = round(time.monotonic() - started, 1)
        safe_write_json(out_dir / "operation.json", mask_sensitive_data(row))
        return mask_sensitive_data(row)


async def run_portal_operations(
    config: Any, input_data: Mapping[str, Any], *, run_dir: Path, allow_portal_mutation: bool = False,
    confirmation: str = "", browser: Any = None, listing_urls: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Run every operation of ``input_data`` in one browser session."""
    if browser is not None:
        return await PortalOperationRunner(config, browser, run_dir, listing_urls=listing_urls).run(
            input_data, allow_portal_mutation=allow_portal_mutation, confirmation=confirmation)
    from .browser_session import BrowserSession

    async with BrowserSession(config, run_dir) as session:
        return await PortalOperationRunner(config, session, run_dir, listing_urls=listing_urls).run(
            input_data, allow_portal_mutation=allow_portal_mutation, confirmation=confirmation)
