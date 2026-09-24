from __future__ import annotations

import re
from typing import Any, Dict, Iterable

from playwright.async_api import Locator, Page


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def classify_doctype_surface_text(text: str, *, visible_labels: Iterable[str] = (), filters_open: bool = False) -> Dict[str, Any]:
    """Classify a Document Type surface using strict, phase-specific proof.

    Listing/filter controls are deliberately rejected. A create surface is valid
    only when the exact heading, core sections, core labels, and non-mutating
    footer context are simultaneously visible.
    """
    sample = _norm_text(text)
    lower = sample.lower()
    labels = {_norm_text(x).lower() for x in visible_labels if _norm_text(x)}
    required_markers = {
        "heading": "create document type" in lower,
        "details": "document type details" in lower,
        "identifier": "document identifier" in lower,
        "attributes": "attributes to configure" in lower,
        "name": "name" in labels or re.search(r"\bname\b", lower) is not None,
        "transaction_type": "transaction type" in labels or "transaction type" in lower,
        "version": "version" in labels or re.search(r"\bversion\b", lower) is not None,
        "data_format": "data format type" in labels or "data format type" in lower,
        "cancel": "cancel" in lower,
        "submit_present": "submit" in lower,
    }
    filter_surface = filters_open or (
        "filters" in lower
        and "filter by column" in lower
        and "create document type" not in lower
    )
    listing_only = (
        "items per page" in lower
        and "document type name" in lower
        and "create document type" not in lower
    )
    missing = [key for key, present in required_markers.items() if not present]
    passed = not filter_surface and not listing_only and not missing
    reason = "ok" if passed else (
        "filters_or_listing_surface" if filter_surface or listing_only else "missing_create_form_markers"
    )
    return {
        "pass": passed,
        "reason": reason,
        "missing_markers": missing,
        "markers": required_markers,
        "filters_open": bool(filter_surface),
        "listing_only": bool(listing_only),
        "surface_text_sample": sample[:1200],
    }


_DOCTYPE_SURFACE_JS = r"""
() => {
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
    return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||'1')!==0);
  }
  function text(el){ return String((el && (el.innerText||el.textContent)) || '').replace(/\s+/g,' ').trim(); }
  function cssPath(el){
    if(!el || !el.tagName) return '';
    const parts=[]; let node=el;
    while(node && node.nodeType===1 && parts.length<9){
      let part=node.tagName.toLowerCase();
      if(node.id){part+='#'+CSS.escape(node.id); parts.unshift(part); break;}
      const cls=Array.from(node.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,3);
      if(cls.length) part+='.'+cls.map(c=>CSS.escape(c)).join('.');
      const parent=node.parentElement;
      if(parent){ const same=Array.from(parent.children).filter(x=>x.tagName===node.tagName); if(same.length>1) part+=`:nth-of-type(${same.indexOf(node)+1})`; }
      parts.unshift(part); node=parent;
    }
    return parts.join(' > ');
  }
  const visibleFilters=Array.from(document.querySelectorAll('[role=dialog],dds-drawer,.dds__drawer,.dds__modal,app-generic-drawer'))
    .filter(visible).filter(el=>/^filters?\b/i.test(text(el)) || /filter by column/i.test(text(el)));
  const roots=Array.from(document.querySelectorAll('[role=dialog],dds-drawer,.dds__drawer,.dds__modal,.modal-dialog,form,app-document-type-create,app-document-types,main'))
    .filter(visible).map(el=>{
      const t=text(el); const low=t.toLowerCase();
      const labels=Array.from(el.querySelectorAll('label,.dds__label,.dds__form__label')).filter(visible).map(x=>text(x));
      const controls=Array.from(el.querySelectorAll('input:not([type=hidden]),textarea,select,[role=combobox]')).filter(visible).length;
      let score=0;
      for(const marker of ['create document type','document type details','document identifier','attributes to configure']) if(low.includes(marker)) score+=30;
      for(const marker of ['transaction type','data format type','validation type','attribute name']) if(low.includes(marker)) score+=8;
      if(low.includes('cancel')) score+=5;
      if(low.includes('submit')) score+=5;
      if(/items per page|showing \d+ of \d+ columns/i.test(t) && !low.includes('create document type')) score-=80;
      if(/^filters?\b/i.test(t) || /filter by column/i.test(t)) score-=100;
      const r=el.getBoundingClientRect();
      return {selector:cssPath(el), text:t, labels, controls, score, area:Math.max(1,r.width*r.height)};
    }).filter(x=>x.score>0).sort((a,b)=>(b.score-a.score)||(a.area-b.area));
  const root=roots[0] || null;
  const bodyText=text(document.body);
  return {
    root_selector: root ? root.selector : '',
    root_text: root ? root.text : '',
    visible_labels: root ? root.labels : [],
    visible_control_count: root ? root.controls : 0,
    root_score: root ? root.score : 0,
    filters_open: visibleFilters.length>0,
    filter_text: visibleFilters.length ? text(visibleFilters[0]).slice(0,500) : '',
    body_text_sample: bodyText.slice(0,1500),
    url: location.href,
  };
}
"""


async def inspect_doctype_create_surface(page: Page) -> Dict[str, Any]:
    try:
        raw = await page.evaluate(_DOCTYPE_SURFACE_JS)
    except Exception as exc:
        return {"pass": False, "reason": "surface_evaluation_failed", "error": str(exc), "root_selector": ""}
    if not isinstance(raw, dict):
        return {"pass": False, "reason": "surface_evaluation_returned_no_object", "root_selector": ""}
    result = classify_doctype_surface_text(
        str(raw.get("root_text") or ""),
        visible_labels=raw.get("visible_labels") or (),
        filters_open=bool(raw.get("filters_open")),
    )
    result.update(raw)
    if result.get("pass") and int(raw.get("visible_control_count") or 0) < 6:
        result["pass"] = False
        result["reason"] = "insufficient_visible_form_controls"
    return result


async def get_doctype_create_root(page: Page) -> Locator | None:
    info = await inspect_doctype_create_surface(page)
    selector = str(info.get("root_selector") or "")
    if not info.get("pass") or not selector:
        return None
    loc = page.locator(selector).first
    try:
        if await loc.count() and await loc.is_visible(timeout=800):
            return loc
    except Exception:
        return None
    return None
