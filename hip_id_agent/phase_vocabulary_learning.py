from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .capability_graph import HIPCapabilityGraph
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


def _norm(v: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(v or "").lower()).strip("_")


async def learn_complete_form_vocabulary(
    *, page: Any, graph: HIPCapabilityGraph, page_family: str, run_id: str, output_dir: Path,
    max_tabs: int = 20, max_controls_per_surface: int = 300, max_options_per_control: int = 500,
) -> Dict[str, Any]:
    """Learn a value-free vocabulary for a currently open HIP form.

    This is intentionally structural: it learns tabs/sections, labels, roles, framework
    keys and dropdown option labels. It never persists current customer values, never
    clicks Save/Create/Submit/Deploy/Delete/Migrate, and always re-proves controls live.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Any] = {
        "schema_version": "hip.phase-form-vocabulary.v1", "page_family": page_family,
        "tabs": [], "surfaces": [], "controls": [], "dropdowns": [], "errors": [],
        "values_stored": False, "selectors_are_advisory": True, "live_reproof_required": True,
    }

    async def visible_tabs() -> List[Dict[str, Any]]:
        try:
            return await page.evaluate(r"""() => {
              const bad=/save|create|submit|deploy|delete|remove|migrate|publish/i;
              const els=[...document.querySelectorAll('[role="tab"],button[aria-controls],a[role="tab"]')];
              return els.filter(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el); const t=(el.innerText||el.textContent||'').trim(); return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&t&&!bad.test(t)})
                .slice(0,100).map((el,i)=>({label:(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim(), role:el.getAttribute('role')||'', ariaControls:el.getAttribute('aria-controls')||'', index:i}));
            }""")
        except Exception:
            return []

    async def capture_surface(section: str) -> None:
        try:
            rows = await page.evaluate(r"""() => [...document.querySelectorAll('input,select,textarea,button,[role="combobox"],[role="textbox"],[role="checkbox"],[role="radio"],[contenteditable="true"]')]
              .filter(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el); return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'})
              .slice(0,1000).map((el,i)=>{let label=''; const id=el.id; if(id){const l=document.querySelector(`label[for="${CSS.escape(id)}"]`); if(l) label=(l.innerText||l.textContent||'').trim();}
                if(!label){label=el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||el.getAttribute('formcontrolname')||'';}
                return {index:i,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',type:el.getAttribute('type')||'',label:String(label).replace(/\s+/g,' ').trim(),placeholder:el.getAttribute('placeholder')||'',name:el.getAttribute('name')||'',formControlName:el.getAttribute('formcontrolname')||'',ariaExpanded:el.getAttribute('aria-expanded')||'',disabled:!!el.disabled,readonly:!!el.readOnly};})""")
        except Exception as exc:
            summary["errors"].append(mask_sensitive_string(str(exc)))
            return
        surface_controls: List[Dict[str, Any]] = []
        for row in (rows or [])[:max_controls_per_surface]:
            if not isinstance(row, Mapping):
                continue
            label = str(row.get("label") or row.get("formControlName") or row.get("name") or row.get("placeholder") or row.get("role") or row.get("tag") or "control")
            cap = graph.observe_capability(
                page_family=page_family, kind="form_control", label=label, role=str(row.get("role") or ""),
                placeholder=str(row.get("placeholder") or ""), scope="form_surface", section=section,
                run_id=run_id, evidence={
                    "tag": row.get("tag"), "type": row.get("type"), "formControlName": row.get("formControlName"),
                    "name": row.get("name"), "disabled": bool(row.get("disabled")), "readonly": bool(row.get("readonly")),
                    "values_stored": False,
                }, knowledge_source="live_form_vocabulary", trust="observed", verified=False,
            )
            item = {"capability_id": cap.get("capability_id"), "section": section, **{k: row.get(k) for k in ("tag","role","type","label","placeholder","name","formControlName","disabled","readonly")}}
            surface_controls.append(item); summary["controls"].append(item)

        # Open each visible combobox/select by semantic label and inventory option labels only.
        for item in surface_controls:
            if item.get("disabled"):
                continue
            role, tag, typ = str(item.get("role") or ""), str(item.get("tag") or ""), str(item.get("type") or "")
            if not (role == "combobox" or tag == "select" or typ in {"search"}):
                continue
            label = str(item.get("label") or item.get("formControlName") or item.get("name") or "")
            try:
                loc = None
                if label:
                    for candidate in (
                        page.get_by_role("combobox", name=re.compile(re.escape(label), re.I)).first,
                        page.get_by_label(re.compile(re.escape(label), re.I)).first,
                    ):
                        try:
                            if await candidate.count() and await candidate.is_visible(): loc = candidate; break
                        except Exception: pass
                if loc is None and item.get("formControlName"):
                    loc = page.locator(f'[formcontrolname="{item["formControlName"]}"]').first
                if loc is None:
                    continue
                await loc.click(timeout=2500)
                await asyncio.sleep(0.15)
                options = await page.evaluate(r"""() => [...document.querySelectorAll('[role="option"],.dds__dropdown__item-option,option')]
                  .filter(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el); return (el.tagName==='OPTION'||(r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'))})
                  .slice(0,1000).map(el=>String(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim()).filter(Boolean)""")
                try: await page.keyboard.press("Escape")
                except Exception: pass
                clean = list(dict.fromkeys(str(x) for x in (options or []) if str(x).strip()))[:max_options_per_control]
                summary["dropdowns"].append({"section": section, "label": label, "option_count": len(clean), "option_labels": clean, "values_stored": False})
            except Exception as exc:
                summary["errors"].append(f"{label}: {mask_sensitive_string(str(exc))[:500]}")

        summary["surfaces"].append({"section": section, "control_count": len(surface_controls)})

    tabs = await visible_tabs()
    summary["tabs"] = tabs[:max_tabs]
    await capture_surface("default")
    for tab in tabs[:max_tabs]:
        label = str(tab.get("label") or "").strip()
        if not label:
            continue
        try:
            loc = page.get_by_role("tab", name=re.compile(re.escape(label), re.I)).first
            if not (await loc.count() and await loc.is_visible()):
                continue
            await loc.click(timeout=3000)
            await asyncio.sleep(0.25)
            await capture_surface(label)
        except Exception as exc:
            summary["errors"].append(f"tab {label}: {mask_sensitive_string(str(exc))[:500]}")

    graph.save()
    summary["control_count"] = len(summary["controls"])
    summary["dropdown_count"] = len(summary["dropdowns"])
    summary["option_count"] = sum(int(x.get("option_count") or 0) for x in summary["dropdowns"])
    safe_write_json(output_dir / "complete_form_vocabulary.json", mask_sensitive_data(summary))
    return mask_sensitive_data(summary)
