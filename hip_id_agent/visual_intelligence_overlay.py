from __future__ import annotations

"""Transient Browser-Use-style visual intelligence overlays for HIP.

The overlay is deliberately an *observability* layer.  It never chooses a target,
never bypasses the semantic gate, and never persists selectors, coordinates, or
customer values.  The currently vetted live locator remains authoritative.
"""

from typing import Any, Dict, Mapping, Sequence

from .security import mask_sensitive_string


OVERLAY_COLORS: Dict[str, str] = {
    "seen": "#2f80ed",          # blue
    "candidate": "#9b51e0",     # violet
    "selected": "#f2c94c",      # yellow
    "acting": "#f2994a",        # orange
    "verified": "#27ae60",      # green
    "revealed": "#18b7b0",      # teal
    "failed": "#eb5757",        # red
    "foreground": "#ff5ca8",    # pink
}

OVERLAY_LEGEND: Dict[str, Dict[str, str]] = {
    "seen": {"label": "SEE", "meaning": "Visible actionable control the agent can currently perceive", "color": OVERLAY_COLORS["seen"]},
    "candidate": {"label": "CANDIDATE", "meaning": "Semantic candidate considered by the live target resolver", "color": OVERLAY_COLORS["candidate"]},
    "selected": {"label": "IDENTIFIED", "meaning": "Vetted target selected by the semantic action gate", "color": OVERLAY_COLORS["selected"]},
    "acting": {"label": "ACTING", "meaning": "Control about to receive the governed physical action", "color": OVERLAY_COLORS["acting"]},
    "verified": {"label": "VERIFIED", "meaning": "Action effect independently verified", "color": OVERLAY_COLORS["verified"]},
    "revealed": {"label": "REVEALED", "meaning": "New actionable control revealed after the action", "color": OVERLAY_COLORS["revealed"]},
    "failed": {"label": "FAILED", "meaning": "Target/action failed independent verification", "color": OVERLAY_COLORS["failed"]},
    "foreground": {"label": "FOREGROUND", "meaning": "Authoritative foreground drawer/dialog/surface", "color": OVERLAY_COLORS["foreground"]},
}


def overlay_legend() -> Dict[str, Dict[str, str]]:
    return {key: dict(value) for key, value in OVERLAY_LEGEND.items()}


class VisualIntelligenceOverlay:
    """Render transient, non-intercepting overlays into the current HIP page.

    The browser page computes geometry at render time.  Returned telemetry contains
    counts and semantic state only; geometry and selectors are kept runtime-local.
    """

    def __init__(self, *, config: Any = None) -> None:
        policy = getattr(config, "portal_learning", None)
        self.enabled = bool(getattr(policy, "visual_overlay_enabled", True))
        self.max_seen = max(10, int(getattr(policy, "visual_overlay_max_seen_controls", 80) or 80))
        self.max_candidates = max(1, int(getattr(policy, "visual_overlay_max_candidates", 8) or 8))
        self.show_labels = bool(getattr(policy, "visual_overlay_show_labels", True))
        self.result_hold_ms = max(0, int(getattr(policy, "visual_overlay_result_hold_ms", 1400) or 1400))
        self.include_foreground = bool(getattr(policy, "visual_overlay_highlight_foreground_surface", True))

    @staticmethod
    async def _box(locator: Any) -> Dict[str, float]:
        if locator is None:
            return {}
        try:
            box = await locator.first.bounding_box(timeout=1200)
        except TypeError:
            try:
                box = await locator.first.bounding_box()
            except Exception:
                box = None
        except Exception:
            box = None
        if not isinstance(box, Mapping):
            return {}
        try:
            width, height = float(box.get("width") or 0), float(box.get("height") or 0)
            if width <= 0 or height <= 0:
                return {}
            return {
                "x": float(box.get("x") or 0), "y": float(box.get("y") or 0),
                "width": width, "height": height,
            }
        except Exception:
            return {}

    @staticmethod
    def _candidate_selectors(resolution: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
        evidence = resolution.get("evidence") if isinstance(resolution.get("evidence"), Mapping) else {}
        ranked = evidence.get("ranked_candidates") if isinstance(evidence, Mapping) else []
        rows: list[dict[str, Any]] = []
        for index, raw in enumerate(ranked or [], start=1):
            if len(rows) >= limit or not isinstance(raw, Mapping):
                break
            selector = str(raw.get("selector") or "").strip()
            if not selector:
                continue
            rows.append({
                "selector": selector,
                "rank": index,
                "label": mask_sensitive_string(str(raw.get("label") or ""))[:120],
                "selected": bool(raw.get("anchored")) and index == 1,
            })
        return rows

    async def clear(self, *, page: Any) -> None:
        if not self.enabled or page is None:
            return
        try:
            await page.evaluate("document.querySelectorAll('[data-hip-visual-intelligence-root]').forEach(x=>x.remove())")
        except Exception:
            pass

    async def render_selection(
        self, *, page: Any, locator: Any, resolution: Mapping[str, Any], label: str,
    ) -> Dict[str, Any]:
        return await self._render(
            page=page,
            stage="selected",
            selected_box=await self._box(locator),
            selected_label=label,
            candidates=self._candidate_selectors(resolution, self.max_candidates),
            previous_seen_keys=[],
            hold_ms=0,
        )

    async def render_acting(self, *, page: Any, locator: Any, label: str) -> Dict[str, Any]:
        return await self._render(
            page=page,
            stage="acting",
            selected_box=await self._box(locator),
            selected_label=label,
            candidates=[],
            previous_seen_keys=[],
            hold_ms=0,
        )

    async def render_result(
        self, *, page: Any, locator: Any, label: str, success: bool,
        previous_seen_keys: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        return await self._render(
            page=page,
            stage="verified" if success else "failed",
            selected_box=await self._box(locator),
            selected_label=label,
            candidates=[],
            previous_seen_keys=list(previous_seen_keys or []),
            hold_ms=self.result_hold_ms,
        )

    async def _render(
        self, *, page: Any, stage: str, selected_box: Mapping[str, Any], selected_label: str,
        candidates: Sequence[Mapping[str, Any]], previous_seen_keys: Sequence[str], hold_ms: int,
    ) -> Dict[str, Any]:
        if not self.enabled or page is None:
            return {
                "enabled": False, "stage": stage, "counts": {}, "legend": overlay_legend(),
                "pointer_events": "none", "selectors_stored": False, "coordinates_stored": False,
            }
        payload = {
            "stage": str(stage or "selected"),
            "selectedBox": dict(selected_box or {}),
            "selectedLabel": mask_sensitive_string(str(selected_label or ""))[:140],
            "candidates": [dict(x) for x in list(candidates or [])[: self.max_candidates]],
            "previousSeenKeys": [str(x)[:240] for x in list(previous_seen_keys or [])[: self.max_seen * 2]],
            "maxSeen": self.max_seen,
            "showLabels": self.show_labels,
            "foreground": self.include_foreground,
            "colors": dict(OVERLAY_COLORS),
            "holdMs": max(0, int(hold_ms or 0)),
        }
        script = r"""
        (payload) => {
          const ROOT_ATTR='data-hip-visual-intelligence-root';
          document.querySelectorAll('['+ROOT_ATTR+']').forEach(x=>x.remove());
          const colors=payload.colors||{};
          const visible=(el)=>{
            if(!el || !el.isConnected) return false;
            const s=getComputedStyle(el), r=el.getBoundingClientRect();
            return s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||1)>0.01 && r.width>2 && r.height>2 && r.bottom>0 && r.right>0 && r.top<innerHeight && r.left<innerWidth;
          };
          const z=(el)=>{ const n=parseInt(getComputedStyle(el).zIndex||'0',10); return Number.isFinite(n)?n:0; };
          const topVisible=(el)=>{
            if(!visible(el)) return false;
            const r=el.getBoundingClientRect(), x=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2)), y=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));
            const top=document.elementFromPoint(x,y);
            return !!top && (top===el || el.contains(top) || top.contains(el));
          };
          const foregroundCandidates=[
            ...document.querySelectorAll('[role="dialog"][aria-modal="true"],[role="alertdialog"],.dds__drawer,.dds__side-drawer,.dds__modal,[data-testid*="drawer"],[class*="drawer"][aria-hidden="false"]')
          ].filter(visible);
          foregroundCandidates.sort((a,b)=>{const dz=z(b)-z(a); if(dz) return dz; const ar=x=>{const r=x.getBoundingClientRect();return r.width*r.height}; return ar(b)-ar(a)});
          const surface=foregroundCandidates[0]||document.body;
          const surfaceZ=surface===document.body?0:z(surface);
          const belongs=(el)=>{
            if(surface===document.body || surface.contains(el)) return true;
            const pop=el.closest('[role="listbox"],[role="menu"],[role="tree"],.dds__popover,.dds__dropdown__menu,.cdk-overlay-pane');
            return !!pop && visible(pop) && z(pop)>=surfaceZ && topVisible(el);
          };
          const root=document.createElement('div'); root.setAttribute(ROOT_ATTR,'1');
          Object.assign(root.style,{position:'fixed',inset:'0',zIndex:'2147483646',pointerEvents:'none',fontFamily:'Segoe UI,Arial,sans-serif'});
          document.documentElement.appendChild(root);
          const safeText=(s)=>String(s||'').replace(/\s+/g,' ').trim().slice(0,90);
          const drawRect=(rect,color,label,kind,extra={})=>{
            if(!rect || rect.width<=1 || rect.height<=1) return false;
            const d=document.createElement('div'); d.setAttribute('data-hip-overlay-kind',kind||'');
            Object.assign(d.style,{position:'fixed',left:Math.round(rect.left??rect.x??0)+'px',top:Math.round(rect.top??rect.y??0)+'px',width:Math.round(rect.width)+'px',height:Math.round(rect.height)+'px',border:`${extra.width||2}px solid ${color}`,boxSizing:'border-box',borderRadius:'4px',pointerEvents:'none',boxShadow:extra.glow?`0 0 0 1px #0008,0 0 14px ${color}88`:'0 0 0 1px #0006'});
            if(extra.fill) d.style.background=extra.fill;
            if(payload.showLabels && label){
              const t=document.createElement('div'); t.textContent=safeText(label);
              Object.assign(t.style,{position:'absolute',left:'0',top:(rect.top??rect.y??0)<28?'0':'-24px',maxWidth:'420px',background:color,color:(kind==='selected'?'#1a1a1a':'#fff'),fontSize:'10px',fontWeight:'800',padding:'3px 6px',borderRadius:'4px',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis',letterSpacing:'.03em'});
              d.appendChild(t);
            }
            root.appendChild(d); return true;
          };
          const selectorToElements=(selector)=>{
            try {
              if(String(selector||'').startsWith('xpath=')) {
                const xp=String(selector).slice(6), out=[]; const it=document.evaluate(xp,document,null,XPathResult.ORDERED_NODE_ITERATOR_TYPE,null); let n;
                while((n=it.iterateNext()) && out.length<5) if(n instanceof Element) out.push(n); return out;
              }
              if(String(selector||'').startsWith('//') || String(selector||'').startsWith('(//')) {
                const out=[]; const it=document.evaluate(String(selector),document,null,XPathResult.ORDERED_NODE_ITERATOR_TYPE,null); let n;
                while((n=it.iterateNext()) && out.length<5) if(n instanceof Element) out.push(n); return out;
              }
              return [...document.querySelectorAll(String(selector||''))].slice(0,5);
            } catch { return []; }
          };
          const seenSelector='button,input,textarea,select,a[href],[role="button"],[role="textbox"],[role="combobox"],[role="checkbox"],[role="radio"],[role="option"],[role="tab"],[role="switch"],[contenteditable="true"],[tabindex]';
          const seen=[]; const seenKeys=[];
          for(const el of [...document.querySelectorAll(seenSelector)]){
            if(seen.length>=payload.maxSeen) break;
            if(!visible(el)||!belongs(el)||!topVisible(el)) continue;
            const role=safeText(el.getAttribute('role')||el.tagName||'').toLowerCase();
            const label=safeText(el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||el.innerText||el.textContent||'');
            const section=safeText(el.closest('fieldset,section,[role="group"],form')?.getAttribute('aria-label')||'');
            const key=(role+'|'+label+'|'+section).toLowerCase();
            seen.push({el,label,role,key}); seenKeys.push(key);
          }
          let foregroundCount=0;
          if(payload.foreground && surface!==document.body){
            const r=surface.getBoundingClientRect(); if(drawRect(r,colors.foreground,'FOREGROUND '+safeText(surface.getAttribute('aria-label')||surface.getAttribute('role')||'surface'),'foreground',{width:3})) foregroundCount=1;
          }
          let seenCount=0;
          for(const row of seen){ const r=row.el.getBoundingClientRect(); if(drawRect(r,colors.seen,'','seen',{width:1})) seenCount++; }
          let candidateCount=0;
          for(const c of (payload.candidates||[])){
            for(const el of selectorToElements(c.selector)){
              if(!visible(el)||!belongs(el)||!topVisible(el)) continue;
              const r=el.getBoundingClientRect();
              if(drawRect(r,colors.candidate,`C${c.rank||'?'} ${safeText(c.label||'candidate')}`,'candidate',{width:2})) candidateCount++;
              break;
            }
          }
          const prev=new Set((payload.previousSeenKeys||[]).map(x=>String(x)));
          let revealedCount=0;
          if(prev.size){
            for(const row of seen){
              if(prev.has(row.key)) continue;
              const r=row.el.getBoundingClientRect();
              if(drawRect(r,colors.revealed,`REVEALED ${row.label||row.role}`,'revealed',{width:2,glow:true})) revealedCount++;
            }
          }
          let selectedCount=0, actingCount=0, verifiedCount=0, failedCount=0;
          const box=payload.selectedBox||{};
          if(Number(box.width||0)>0 && Number(box.height||0)>0){
            const color=colors[payload.stage]||colors.selected;
            const kind=payload.stage||'selected';
            const caption=kind==='selected'?'IDENTIFIED':kind==='acting'?'ACT / FILL':kind==='verified'?'VERIFIED':kind==='failed'?'FAILED':kind.toUpperCase();
            if(drawRect({x:box.x,y:box.y,width:box.width,height:box.height},color,caption+' '+safeText(payload.selectedLabel),kind,{width:4,glow:true,fill:kind==='acting'?color+'18':''})){
              if(kind==='selected') selectedCount=1; else if(kind==='acting') actingCount=1; else if(kind==='verified') verifiedCount=1; else if(kind==='failed') failedCount=1;
            }
          }
          if(payload.holdMs>0){ setTimeout(()=>{ try{root.remove()}catch{} },payload.holdMs); }
          return {
            stage:payload.stage,
            counts:{seen:seenCount,candidate:candidateCount,selected:selectedCount,acting:actingCount,verified:verifiedCount,revealed:revealedCount,failed:failedCount,foreground:foregroundCount},
            seenKeys:seenKeys,
            pointerEvents:'none',
            foregroundOwnedOnly:surface!==document.body,
          };
        }
        """
        try:
            raw = await page.evaluate(script, payload)
        except Exception as exc:
            return {
                "enabled": True, "stage": stage, "counts": {}, "legend": overlay_legend(),
                "pointer_events": "none", "selectors_stored": False, "coordinates_stored": False,
                "render_error": mask_sensitive_string(str(exc))[:600], "_seen_keys": [],
            }
        raw = dict(raw or {}) if isinstance(raw, Mapping) else {}
        seen_keys = [str(x) for x in (raw.pop("seenKeys", []) or [])[: self.max_seen * 2]]
        return {
            "enabled": True,
            "stage": str(raw.get("stage") or stage),
            "counts": dict(raw.get("counts") or {}),
            "legend": overlay_legend(),
            "pointer_events": "none",
            "foreground_owned_only": bool(raw.get("foregroundOwnedOnly")),
            "selectors_stored": False,
            "coordinates_stored": False,
            "_seen_keys": seen_keys,  # runtime-only; caller must not persist it
        }


def public_overlay_telemetry(payload: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Strip runtime-only selectors/geometry before evidence is written to disk."""
    forbidden = {
        "_seen_keys", "seenkeys", "selector", "selectors", "xpath", "css", "css_selector",
        "bounding_box", "bbox", "box", "coordinates", "coordinate", "point",
        "x", "y", "left", "top", "right", "bottom",
    }

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            out: Dict[str, Any] = {}
            for key, item in value.items():
                normalized = str(key).strip().lower()
                if normalized in forbidden or "selector" in normalized or "xpath" in normalized or "coordinate" in normalized or "bounding_box" in normalized:
                    continue
                out[str(key)] = clean(item)
            return out
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value

    raw = clean(dict(payload or {}))
    raw["selectors_stored"] = False
    raw["coordinates_stored"] = False
    return raw
