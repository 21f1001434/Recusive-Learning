from __future__ import annotations

"""Semantic website understanding and multi-evidence action gate for HIP Portal.

The deterministic phase drivers still decide *what* business field/action is required.
This layer proves *which live control* corresponds to that intent before any physical
browser action is allowed.  It fuses value-free evidence from:

* the exact Python Playwright locator already selected by a reviewed HIP skill,
* live DOM/control semantics and DOM-generation state,
* Playwright MCP accessibility-tree search/snapshot evidence,
* Chrome DevTools MCP DOM evidence,
* previously successful value-free control fingerprints,
* optional Gemma/vision observation only when ambiguity remains.

No customer-entered field value is persisted in fingerprints or the learned state
graph.  The layer is an action gate, not a second autonomous clicker.
"""

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .safe_io import safe_mkdir, safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .website_world_model import WebsiteWorldModelMemory
from .semantic_affordance import selector_looks_generation_volatile


def _norm(value: Any) -> str:
    text = re.sub(r"[_\-/]+", " ", str(value or "").strip().lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    return {t for t in _norm(value).split() if len(t) > 1}


def _similarity(a: Any, b: Any) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        shorter, longer = min(len(na), len(nb)), max(len(na), len(nb))
        return min(0.94, 0.72 + 0.22 * (shorter / max(1, longer)))
    ta, tb = _tokens(na), _tokens(nb)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _safe_phase(value: Any) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", str(value or "standalone").strip().lower()).strip("_") or "standalone"


def _action_family(action: Any) -> str:
    value = _norm(action)
    if any(x in value for x in ("upload", "attach file", "set input files", "file input")):
        return "upload"
    if any(x in value for x in ("fill", "type", "search", "enter text")):
        return "fill"
    if any(x in value for x in ("select", "choose", "dropdown", "combobox", "radio", "checkbox", "toggle")):
        return "select"
    if any(x in value for x in ("press", "key")):
        return "press"
    return "click"


def _role_compatibility(action: str, candidate: Mapping[str, Any]) -> float:
    family = _action_family(action)
    role = _norm(candidate.get("role") or candidate.get("tag") or candidate.get("type"))
    tag = _norm(candidate.get("tag"))
    typ = _norm(candidate.get("type"))
    if family == "upload":
        if tag == "input" and typ == "file":
            return 1.0
        return 0.05
    if family == "fill":
        if role in {"textbox", "searchbox", "combobox", "input", "textarea"} or tag in {"input", "textarea"}:
            return 1.0
        return 0.15
    if family == "select":
        if role in {"combobox", "option", "radio", "checkbox", "listbox"} or tag == "select" or typ in {"radio", "checkbox"}:
            return 1.0
        return 0.25
    if family == "press":
        return 0.9 if role in {"textbox", "searchbox", "combobox", "button", "input", "textarea"} else 0.45
    if role in {"button", "menuitem", "link", "tab", "option", "checkbox", "radio"} or tag in {"button", "a"}:
        return 1.0
    return 0.45


def control_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Stable, value-free identity for a HIP control."""
    payload = {
        "label": _norm(candidate.get("label") or candidate.get("aria_label") or candidate.get("placeholder")),
        "role": _norm(candidate.get("role") or candidate.get("tag")),
        "type": _norm(candidate.get("type")),
        "section": _norm(candidate.get("section")),
        "framework_key": _norm(candidate.get("framework_key")),
        "name": _norm(candidate.get("name")),
        "testid": _norm(candidate.get("testid")),
        "row_kind": _norm(candidate.get("row_kind")),
        "selection_mode": _norm(candidate.get("selection_mode")),
        "aria_haspopup": _norm(candidate.get("aria_haspopup")),
        "accept": _norm(candidate.get("accept")),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def semantic_control_id(candidate: Mapping[str, Any]) -> str:
    return "SC-" + control_fingerprint(candidate)[:12].upper()


def current_generation_row_binding(candidate: Mapping[str, Any], dom_generation: Any) -> str:
    """Value-free, ephemeral binding for duplicate controls in one DOM generation.

    Row position is never promoted into the stable control fingerprint.  It may only
    disambiguate two otherwise-identical controls while the DOM generation is
    unchanged; after any Angular/DDS rerender the binding is intentionally invalid.
    """
    row_index = candidate.get("row_index")
    if row_index is None:
        return ""
    payload = {
        "dom_generation": int(dom_generation or 0),
        "row_kind": _norm(candidate.get("row_kind")),
        "row_index": int(row_index),
        "label": _norm(candidate.get("label") or candidate.get("aria_label")),
        "section": _norm(candidate.get("section")),
        "framework_key": _norm(candidate.get("framework_key")),
        "name": _norm(candidate.get("name")),
    }
    return "GRB-" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16].upper()


def _state_fingerprint(state: Mapping[str, Any]) -> str:
    keep = {
        "url_path": state.get("url_path"),
        "dom_generation": state.get("dom_generation"),
        "control_count": state.get("control_count"),
        "visible_dialog_count": state.get("visible_dialog_count"),
        "visible_listbox_count": state.get("visible_listbox_count"),
        "visible_drawer_count": state.get("visible_drawer_count"),
        "target": state.get("target"),
    }
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode("utf-8")).hexdigest()


INVENTORY_JS = r"""
({anchor}) => {
  const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
  const visible=el=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)>0};
  const stableId=id=>{id=String(id||'');if(!id||id.length>128)return false;if(/(?:^|[-_:])(dds|ng|mat|cdk|react|ember|generated)[-_:]?[a-z0-9_-]*\d{2,}/i.test(id))return false;if(/^[0-9a-f]{8}-[0-9a-f-]{20,}$/i.test(id))return false;if(/^[a-z_-]*\d{6,}$/i.test(id))return false;return true};
  const escAttr=s=>String(s||'').replace(/\\/g,'\\\\').replace(/"/g,'\\"');
  const unique=sel=>{try{return document.querySelectorAll(sel).length===1}catch(_){return false}};
  const stableSelector=el=>{
    if(stableId(el.id)){const s=`#${CSS.escape(el.id)}`;if(unique(s))return s;}
    const fc=el.getAttribute('formcontrolname'); if(fc){const s=`[formcontrolname="${escAttr(fc)}"]`;if(unique(s))return s;}
    const name=el.getAttribute('name'); if(name){const s=`${el.tagName.toLowerCase()}[name="${escAttr(name)}"]`;if(unique(s))return s;}
    const testid=el.getAttribute('data-testid'); if(testid){const s=`[data-testid="${escAttr(testid)}"]`;if(unique(s))return s;}
    const aria=el.getAttribute('aria-label'); if(aria){const s=`${el.tagName.toLowerCase()}[aria-label="${escAttr(aria)}"]`;if(unique(s))return s;}
    const role=el.getAttribute('role'); if(role&&aria){const s=`[role="${escAttr(role)}"][aria-label="${escAttr(aria)}"]`;if(unique(s))return s;}
    const out=[];let n=el;for(let d=0;n&&d<8&&n.nodeType===1;d++,n=n.parentElement){let p=n.tagName.toLowerCase();const par=n.parentElement;if(par){const same=Array.from(par.children).filter(x=>x.tagName===n.tagName);if(same.length>1)p+=`:nth-of-type(${same.indexOf(n)+1})`;}out.unshift(p);if(n.matches('form,[role=dialog],main,section,dds-drawer,.dds__drawer'))break;}return out.join(' > ');
  };
  const labelFor=el=>{
    const id=el.id; if(id){try{const l=document.querySelector(`label[for="${CSS.escape(id)}"]`);if(l&&norm(l.textContent))return norm(l.textContent)}catch(_){}}
    const own=el.closest('label'); if(own&&norm(own.textContent))return norm(own.textContent);
    const group=el.closest('fieldset,.dds__form-group,.dds__input-text__container,app-generic-dropdown,dds-dropdown,[class*=form-field],[class*=field-container],[role=row],tr');
    if(group){const l=group.querySelector('label,legend,.dds__label');if(l&&norm(l.textContent))return norm(l.textContent)}
    return norm(el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('title')||el.textContent||'').slice(0,280);
  };
  const sectionFor=el=>{let n=el;while(n&&n!==document.body){const h=n.querySelector&&n.querySelector(':scope > h1,:scope > h2,:scope > h3,:scope > h4,:scope > legend,:scope > [role=heading],:scope > [role=tab][aria-selected=true]');if(h&&norm(h.textContent))return norm(h.textContent).slice(0,240);n=n.parentElement;}return ''};
  const rowFor=el=>{const r=el.closest('tr,[role=row],.dds__row,[class*=condition-row],[class*=attribute-row],[class*=form-row]');if(!r)return {kind:'',index:null};const parent=r.parentElement;const peers=parent?Array.from(parent.children).filter(x=>x.tagName===r.tagName):[];return {kind:(r.getAttribute('role')||r.tagName||'row').toLowerCase(),index:peers.length?peers.indexOf(r):null};};
  const q='[data-hip-semantic-anchor],input,textarea,select,button,a,[role=button],[role=link],[role=combobox],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=option],[contenteditable=true],dds-dropdown,dds-checkbox,dds-radio-button,dds-button';
  const nodes=Array.from(document.querySelectorAll(q)).filter(el=>visible(el)||(!!anchor&&el.getAttribute('data-hip-semantic-anchor')===anchor)).slice(0,1200);
  const controls=nodes.map(el=>{
    const row=rowFor(el); const role=el.getAttribute('role')||((el.tagName||'').toLowerCase()==='input'?(el.type==='checkbox'?'checkbox':el.type==='radio'?'radio':'textbox'):(el.tagName||'').toLowerCase());
    const selected=el.tagName&&el.tagName.toLowerCase()==='select'?Array.from(el.selectedOptions||[]).length:0;
    const owner=el.getAttribute('aria-controls')||el.getAttribute('aria-owns')||''; let ownerVisible=false, ownerRole='';
    if(owner){const o=document.getElementById(owner);ownerVisible=visible(o);ownerRole=o?String(o.getAttribute('role')||o.tagName||'').toLowerCase():'';}
    return {
      anchor_match: !!anchor && el.getAttribute('data-hip-semantic-anchor')===anchor,
      selector:stableSelector(el), selector_unique:true,
      label:labelFor(el), aria_label:norm(el.getAttribute('aria-label')), placeholder:norm(el.getAttribute('placeholder')), title:norm(el.getAttribute('title')),
      tag:(el.tagName||'').toLowerCase(), role:String(role||'').toLowerCase(), type:String(el.getAttribute('type')||'').toLowerCase(),
      section:sectionFor(el), framework_key:norm(el.getAttribute('formcontrolname')||el.getAttribute('ng-reflect-name')), name:norm(el.getAttribute('name')), testid:norm(el.getAttribute('data-testid')),
      visible:visible(el), enabled:!(el.disabled||el.getAttribute('aria-disabled')==='true'), readonly:!!(el.readOnly||el.getAttribute('aria-readonly')==='true'), required:!!(el.required||el.getAttribute('aria-required')==='true'), accept:norm(el.getAttribute('accept')),
      checked:!!el.checked, selected:!!el.selected, selected_count:selected, expanded:el.getAttribute('aria-expanded'), aria_haspopup:el.getAttribute('aria-haspopup')||'', aria_controls:el.getAttribute('aria-controls')||'', aria_owns:el.getAttribute('aria-owns')||'',
      owned_surface_visible:ownerVisible, owned_surface_role:ownerRole, has_value:!!(String(el.value||'').trim()||selected||el.checked), row_kind:row.kind,row_index:row.index,
    };
  });
  let path='';try{path=location.pathname}catch(_){path=''}
  return {
    url_path:path,
    dom_generation:Number(window.__HIP_DOM_MUTATION_SEQ||0),
    control_count:controls.length,
    visible_dialog_count:Array.from(document.querySelectorAll('[role=dialog],dialog')).filter(visible).length,
    visible_listbox_count:Array.from(document.querySelectorAll('[role=listbox]')).filter(visible).length,
    visible_drawer_count:Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,app-generic-drawer')).filter(visible).length,
    controls
  };
}
"""


@dataclass
class SemanticGateResult:
    pass_: bool
    status: str
    confidence: float
    margin: float
    candidate: Dict[str, Any]
    evidence: Dict[str, Any]
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return mask_sensitive_data({
            "schema_version": "hip.semantic-action-gate.v1",
            "pass": bool(self.pass_),
            "status": self.status,
            "confidence": round(float(self.confidence), 4),
            "margin": round(float(self.margin), 4),
            "semantic_control_id": semantic_control_id(self.candidate) if self.candidate else "",
            "control_fingerprint": control_fingerprint(self.candidate) if self.candidate else "",
            "candidate": self.candidate,
            "evidence": self.evidence,
            "reason": self.reason,
            "values_stored": False,
        })


class SemanticControlMemory:
    """Value-free successful control fingerprints and state transitions."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        safe_mkdir(self.root, parents=True, exist_ok=True)
        safe_mkdir(self.root / "controls", parents=True, exist_ok=True)
        safe_mkdir(self.root / "state_graph", parents=True, exist_ok=True)

    @staticmethod
    def intent_key(*, phase: str, action: str, label: str, section: str = "") -> str:
        return "|".join((_safe_phase(phase), _action_family(action), _norm(label), _norm(section)))

    def _control_path(self, phase: str, key: str, fingerprint: str) -> Path:
        bucket = self.root / "controls" / _safe_phase(phase)
        safe_mkdir(bucket, parents=True, exist_ok=True)
        digest = hashlib.sha256(f"{key}|{fingerprint}".encode("utf-8")).hexdigest()[:24]
        return bucket / f"{digest}.json"

    def match_score(self, *, phase: str, action: str, label: str, section: str, candidate: Mapping[str, Any]) -> Dict[str, Any]:
        key = self.intent_key(phase=phase, action=action, label=label, section=section)
        fp = control_fingerprint(candidate)
        path = self._control_path(phase, key, fp)
        if not path.exists():
            return {"matched": False, "score": 0.0}
        try:
            row = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"matched": False, "score": 0.0}
        confirmations = max(1, int(row.get("confirmations") or 1))
        return {"matched": True, "score": min(1.0, 0.70 + 0.06 * confirmations), "confirmations": confirmations, "path": str(path)}

    def promote(self, *, phase: str, action: str, label: str, section: str, candidate: Mapping[str, Any], effect: Mapping[str, Any]) -> Dict[str, Any]:
        key = self.intent_key(phase=phase, action=action, label=label, section=section)
        fp = control_fingerprint(candidate)
        path = self._control_path(phase, key, fp)
        current: Dict[str, Any] = {}
        try:
            current = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        except Exception:
            current = {}
        row = {
            "schema_version": "hip.semantic-control-memory.v1",
            "intent_key": key,
            "phase": _safe_phase(phase),
            "action_family": _action_family(action),
            "label": _norm(label),
            "section": _norm(section),
            "semantic_control_id": semantic_control_id(candidate),
            "control_fingerprint": fp,
            "fingerprint_basis": {
                "label": _norm(candidate.get("label") or candidate.get("aria_label")),
                "role": _norm(candidate.get("role") or candidate.get("tag")),
                "type": _norm(candidate.get("type")),
                "section": _norm(candidate.get("section")),
                "framework_key": _norm(candidate.get("framework_key")),
                "name": _norm(candidate.get("name")),
                "testid": _norm(candidate.get("testid")),
                "row_kind": _norm(candidate.get("row_kind")),
                "selection_mode": _norm(candidate.get("selection_mode")),
            },
            "confirmations": int(current.get("confirmations") or 0) + 1,
            "last_effect_type": str(effect.get("effect_type") or ""),
            "last_effect_confidence": float(effect.get("confidence") or 0.0),
            "values_stored": False,
        }
        safe_write_json(path, row)
        return row

    def record_transition(self, *, phase: str, action: str, semantic_id: str, before: Mapping[str, Any], after: Mapping[str, Any], effect: Mapping[str, Any]) -> Dict[str, Any]:
        from_fp = str(before.get("state_fingerprint") or _state_fingerprint(before))
        to_fp = str(after.get("state_fingerprint") or _state_fingerprint(after))
        phase_dir = self.root / "state_graph" / _safe_phase(phase)
        safe_mkdir(phase_dir, parents=True, exist_ok=True)
        edge_key = hashlib.sha256(f"{from_fp}|{_action_family(action)}|{semantic_id}|{to_fp}".encode("utf-8")).hexdigest()[:28]
        path = phase_dir / f"{edge_key}.json"
        current: Dict[str, Any] = {}
        try:
            current = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        except Exception:
            current = {}
        row = {
            "schema_version": "hip.semantic-state-edge.v1",
            "phase": _safe_phase(phase),
            "from_state": from_fp,
            "action_family": _action_family(action),
            "semantic_control_id": semantic_id,
            "to_state": to_fp,
            "effect_type": str(effect.get("effect_type") or ""),
            "effect_confidence": float(effect.get("confidence") or 0.0),
            "success_count": int(current.get("success_count") or 0) + 1,
            "values_stored": False,
        }
        safe_write_json(path, row)
        return row


async def capture_semantic_state(page: Any, *, locator: Any = None, anchor: str = "") -> Dict[str, Any]:
    if page is None:
        return {"available": False, "reason": "page unavailable", "controls": []}
    marker = anchor or ("hip-" + uuid.uuid4().hex[:14])
    marked = False
    if locator is not None:
        try:
            if await locator.first.count():
                await locator.first.evaluate("(el,a)=>el.setAttribute('data-hip-semantic-anchor',a)", marker)
                marked = True
        except Exception:
            marked = False
    try:
        payload = await page.evaluate(INVENTORY_JS, {"anchor": marker if marked else ""})
        if not isinstance(payload, dict):
            payload = {"controls": []}
    except Exception as exc:
        payload = {"available": False, "reason": mask_sensitive_string(str(exc))[:1000], "controls": []}
    finally:
        if marked and locator is not None:
            try:
                await locator.first.evaluate("el=>el.removeAttribute('data-hip-semantic-anchor')")
            except Exception:
                pass
    controls = [dict(c) for c in payload.get("controls", []) if isinstance(c, Mapping)]
    dom_generation = payload.get("dom_generation")
    for c in controls:
        c["control_fingerprint"] = control_fingerprint(c)
        c["semantic_control_id"] = semantic_control_id(c)
        c["selector_generation_volatile"] = selector_looks_generation_volatile(str(c.get("selector") or ""))
        c["current_generation_row_binding"] = current_generation_row_binding(c, dom_generation)
    payload["controls"] = controls
    payload["available"] = payload.get("available", True)
    payload["state_fingerprint"] = _state_fingerprint(payload)
    return mask_sensitive_data(payload)


def _candidate_base_score(candidate: Mapping[str, Any], *, expected_label: str, expected_section: str, action: str, anchored: bool) -> tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []
    if anchored:
        score += 0.38; reasons.append("exact_playwright_locator_anchor")
    label = candidate.get("label") or candidate.get("aria_label") or candidate.get("placeholder") or candidate.get("title")
    ls = _similarity(expected_label, label)
    score += 0.20 * ls
    if ls >= 0.95: reasons.append("label_exact")
    elif ls >= 0.55: reasons.append("label_semantic_match")
    if expected_section:
        ss = _similarity(expected_section, candidate.get("section"))
        score += 0.10 * ss
        if ss >= 0.75: reasons.append("section_match")
    role_score = _role_compatibility(action, candidate)
    score += 0.10 * role_score
    if role_score >= 0.9: reasons.append("control_type_matches_action")
    is_upload_target = _action_family(action) == "upload" and _norm(candidate.get("tag")) == "input" and _norm(candidate.get("type")) == "file"
    if bool(candidate.get("enabled", True)) and (bool(candidate.get("visible", True)) or is_upload_target):
        score += 0.08; reasons.append("visible_enabled" if candidate.get("visible", True) else "hidden_file_input_allowed")
    if str(candidate.get("selector") or "") and not bool(candidate.get("selector_generation_volatile")):
        score += 0.06; reasons.append("stable_semantic_selector")
    if candidate.get("framework_key") or candidate.get("name") or candidate.get("testid"):
        score += 0.04; reasons.append("framework_identity")
    if bool(candidate.get("owned_surface_visible")):
        score += 0.02; reasons.append("owned_surface_visible")
    return score, reasons


def _contains_semantic(text: str, label: str, section: str = "") -> bool:
    """Return True only when evidence names the intended control itself.

    A surrounding section heading is useful context for local candidate ranking, but
    it is not independent proof that a specific field/button exists.
    """
    hay = _norm(text)
    if not hay:
        return False
    label_norm = _norm(label)
    if not label_norm:
        return False
    if label_norm in hay:
        return True
    toks = _tokens(label_norm)
    return bool(toks and len(toks & _tokens(hay)) >= max(1, math.ceil(len(toks) * 0.7)))


def rank_semantic_candidates(
    *,
    candidates: Sequence[Mapping[str, Any]],
    action: str,
    expected_label: str,
    expected_section: str = "",
    accessibility_text: str = "",
    devtools_text: str = "",
    memory: Optional[SemanticControlMemory] = None,
    world_model: Optional[WebsiteWorldModelMemory] = None,
    phase: str = "",
) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    for raw in candidates or []:
        candidate = dict(raw)
        anchored = bool(candidate.get("anchor_match"))
        score, reasons = _candidate_base_score(
            candidate, expected_label=expected_label, expected_section=expected_section, action=action, anchored=anchored
        )
        own_label = str(candidate.get("label") or candidate.get("aria_label") or expected_label or "")
        pw_confirmed = bool(accessibility_text and _contains_semantic(accessibility_text, own_label, str(candidate.get("section") or "")))
        dt_confirmed = bool(devtools_text and _contains_semantic(devtools_text, own_label, str(candidate.get("section") or "")))
        if pw_confirmed:
            score += 0.08; reasons.append("playwright_mcp_accessibility_confirmed")
        if dt_confirmed:
            score += 0.05; reasons.append("devtools_dom_confirmed")
        mem: Dict[str, Any] = {"matched": False, "score": 0.0}
        if memory is not None:
            mem = memory.match_score(
                phase=phase, action=action, label=expected_label or own_label,
                section=expected_section or str(candidate.get("section") or ""), candidate=candidate,
            )
            if mem.get("matched"):
                score += 0.07 * float(mem.get("score") or 0.0); reasons.append("validated_fingerprint_memory")
        world: Dict[str, Any] = {"matched": False, "score": 0.0, "trust": "unknown"}
        if world_model is not None:
            world = world_model.control_hint(
                phase=phase, action=action, label=expected_label or own_label,
                section=expected_section or str(candidate.get("section") or ""), candidate=candidate,
            )
            if world.get("matched"):
                world_score = float(world.get("score") or 0.0)
                # The world model is a bounded prior. Live DOM/accessibility evidence
                # remains authoritative and a negative memory can only modestly penalize.
                score += max(-0.10, min(0.08, 0.08 * world_score))
                if world.get("trust") == "validated": reasons.append("validated_world_model_memory")
                elif world.get("trust") == "negative": reasons.append("negative_world_model_evidence")
                else: reasons.append("candidate_world_model_memory")
        # Penalize same-label ambiguity unless the deterministic locator anchored this exact node.
        same_label = sum(1 for other in candidates if _similarity(own_label, other.get("label") or other.get("aria_label")) >= 0.96)
        if same_label > 1 and not anchored:
            score -= min(0.18, 0.05 * (same_label - 1)); reasons.append("duplicate_semantic_label_penalty")
        local_label_score = _similarity(expected_label, own_label)
        local_role_score = _role_compatibility(action, candidate)
        local_is_upload_target = _action_family(action) == "upload" and _norm(candidate.get("tag")) == "input" and _norm(candidate.get("type")) == "file"
        local_semantic_score = _clamp(
            (0.45 if anchored else 0.0)
            + 0.25 * local_label_score
            + 0.15 * local_role_score
            + (0.15 if bool(candidate.get("enabled", True)) and (bool(candidate.get("visible", True)) or local_is_upload_target) else 0.0)
        )
        source_scores = {
            "playwright_accessibility": 1.0 if pw_confirmed else 0.0,
            "devtools_dom": 1.0 if dt_confirmed else 0.0,
            "hip_historical_memory": round(float(mem.get("score") or 0.0), 4) if mem.get("matched") else 0.0,
            "website_world_model": round(float(world.get("score") or 0.0), 4) if world.get("matched") else 0.0,
            "local_dom_semantics": round(local_semantic_score, 4),
        }
        ranked.append({
            "candidate": candidate,
            "score": round(_clamp(score), 4),
            "anchored": anchored,
            "reasons": reasons,
            "memory": mem,
            "world_model": world,
            "source_scores": source_scores,
        })
    ranked.sort(key=lambda x: (float(x.get("score") or 0.0), bool(x.get("anchored"))), reverse=True)
    return ranked


def verify_semantic_effect(*, before: Mapping[str, Any], after: Mapping[str, Any], action: str, target_before: Mapping[str, Any] | None = None, target_after: Mapping[str, Any] | None = None, exact_value_verified: bool = False) -> Dict[str, Any]:
    """Verify that the action produced a structural/semantic effect.

    The proof deliberately uses booleans/counts/route/generation rather than raw
    customer values. Exact text-field value equality is supplied as a boolean by the
    existing deterministic fill verifier.
    """
    family = _action_family(action)
    evidence: List[str] = []
    confidence = 0.0
    if exact_value_verified:
        confidence = max(confidence, 1.0); evidence.append("exact_value_commit_verified")
    if str(before.get("url_path") or "") != str(after.get("url_path") or ""):
        confidence = max(confidence, 0.98); evidence.append("route_changed")
    try:
        if int(after.get("dom_generation") or 0) > int(before.get("dom_generation") or 0):
            # Observation only: Angular may rerender for unrelated reasons.  A
            # generation increment must be accompanied by a target/surface/route
            # effect before the action is accepted.
            evidence.append("dom_generation_advanced_observation")
    except Exception:
        pass
    for key in ("visible_dialog_count", "visible_listbox_count", "visible_drawer_count", "control_count"):
        if before.get(key) != after.get(key):
            confidence = max(confidence, 0.90 if key != "control_count" else 0.82); evidence.append(f"{key}_changed")
    tb, ta = dict(target_before or {}), dict(target_after or {})
    for key in ("expanded", "checked", "selected", "selected_count", "enabled", "readonly", "owned_surface_visible"):
        if tb.get(key) != ta.get(key):
            confidence = max(confidence, 0.94); evidence.append(f"target_{key}_changed")
    if family == "fill" and bool(ta.get("has_value")):
        confidence = max(confidence, 0.86); evidence.append("target_has_value")
    passed = bool(confidence >= (0.84 if family == "fill" else 0.80))
    return {
        "schema_version": "hip.semantic-action-effect.v1",
        "pass": passed,
        "effect_type": evidence[0] if evidence else "no_proven_semantic_effect",
        "confidence": round(confidence, 4),
        "evidence": evidence,
        "before_state_fingerprint": str(before.get("state_fingerprint") or ""),
        "after_state_fingerprint": str(after.get("state_fingerprint") or ""),
        "values_stored": False,
    }


class SemanticActionGate:
    def __init__(self, config: Any, *, run_dir: str | Path, memory_root: str | Path, world_model_config: Any = None) -> None:
        self.config = config
        self.enabled = bool(getattr(config, "enabled", True)) if config is not None else True
        self.execute_threshold = float(getattr(config, "execute_confidence_threshold", 0.90) or 0.90) if config is not None else 0.90
        self.anchored_execute_threshold = float(getattr(config, "anchored_execute_confidence_threshold", 0.64) or 0.64) if config is not None else 0.64
        self.structural_execute_threshold = float(getattr(config, "structural_opener_confidence_threshold", 0.52) or 0.52) if config is not None else 0.52
        self.anchored_ambiguity_margin = float(getattr(config, "anchored_ambiguity_margin", 0.02) or 0.02) if config is not None else 0.02
        self.prefer_vetted_locator_anchor = bool(getattr(config, "prefer_vetted_locator_anchor", True)) if config is not None else True
        self.reobserve_threshold = float(getattr(config, "reobserve_confidence_threshold", 0.75) or 0.75) if config is not None else 0.75
        self.self_heal_threshold = float(getattr(config, "self_heal_confidence_threshold", 0.55) or 0.55) if config is not None else 0.55
        self.ambiguity_margin = float(getattr(config, "ambiguity_margin", 0.08) or 0.08) if config is not None else 0.08
        self.require_playwright_mcp = bool(getattr(config, "require_playwright_mcp_evidence", True)) if config is not None else True
        self.require_devtools = bool(getattr(config, "require_devtools_evidence", True)) if config is not None else True
        self.use_hip_mcp = bool(getattr(config, "use_hip_intelligence_mcp_consensus", True)) if config is not None else True
        self.require_hip_mcp = bool(getattr(config, "require_hip_intelligence_mcp_evidence", True)) if config is not None else True
        self.strict_external_evidence = bool(getattr(config, "strict_external_evidence", False)) if config is not None else False
        self.use_vision = bool(getattr(config, "use_vision_for_ambiguity", True)) if config is not None else True
        self.vision_boost = float(getattr(config, "vision_confirmation_boost", 0.08) or 0.08) if config is not None else 0.08
        self.fail_closed = bool(getattr(config, "fail_closed", True)) if config is not None else True
        self.require_effect = bool(getattr(config, "require_post_action_effect", True)) if config is not None else True
        self.run_dir = Path(run_dir)
        self.evidence_dir = self.run_dir / "browser_intelligence" / "semantic_action_gate"
        safe_mkdir(self.evidence_dir, parents=True, exist_ok=True)
        self.memory = SemanticControlMemory(Path(memory_root) / "semantic_website_understanding")
        brain_dir = str(getattr(world_model_config, "directory", "portal_brain") or "portal_brain")
        self.world_model = WebsiteWorldModelMemory(
            Path(memory_root) / brain_dir / "website_world_model",
            config=world_model_config,
        )
        self._sequence = 0

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "schema_version": "hip.semantic-action-gate.v1",
            "role": "multi-evidence target proof before Playwright MCP execution",
            "execute_confidence_threshold": self.execute_threshold,
            "anchored_execute_confidence_threshold": self.anchored_execute_threshold,
            "structural_opener_confidence_threshold": self.structural_execute_threshold,
            "anchored_ambiguity_margin": self.anchored_ambiguity_margin,
            "prefer_vetted_locator_anchor": self.prefer_vetted_locator_anchor,
            "reobserve_confidence_threshold": self.reobserve_threshold,
            "self_heal_confidence_threshold": self.self_heal_threshold,
            "ambiguity_margin": self.ambiguity_margin,
            "require_playwright_mcp_evidence": self.require_playwright_mcp,
            "require_devtools_evidence": self.require_devtools,
            "use_hip_intelligence_mcp_consensus": self.use_hip_mcp,
            "require_hip_intelligence_mcp_evidence": self.require_hip_mcp,
            "strict_external_evidence": self.strict_external_evidence,
            "external_evidence_policy": "strict-all-required" if self.strict_external_evidence else "adaptive-available-witnesses",
            "use_vision_for_ambiguity": self.use_vision,
            "fail_closed": self.fail_closed,
            "require_post_action_effect": self.require_effect,
            "values_stored": False,
            "learned_control_fingerprints": True,
            "learned_state_graph": True,
            "persistent_website_world_model": bool(getattr(self.world_model, "enabled", False)),
            "world_model_reused_for_candidate_scoring": bool(getattr(self.world_model, "use_for_candidate_scoring", False)),
            "world_model_reused_for_planning": bool(getattr(self.world_model, "use_for_planning", False)),
            "owned_popup_awareness": True,
            "dom_generation_tracking": True,
        }

    async def _external_evidence(self, *, expected_label: str, playwright_mcp: Any = None, devtools_mcp: Any = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"playwright_mcp": {"available": False}, "devtools_mcp": {"available": False}}
        if playwright_mcp is not None:
            try:
                found = await playwright_mcp.find(text=expected_label) if expected_label else await playwright_mcp.snapshot(boxes=False, depth=8)
                text = str((found or {}).get("text") or "")
                out["playwright_mcp"] = {"available": True, "text": text[:16000], "matched": _contains_semantic(text, expected_label)}
            except Exception as exc:
                out["playwright_mcp"] = {"available": False, "error": mask_sensitive_string(str(exc))[:900]}
        if devtools_mcp is not None:
            try:
                snap = await devtools_mcp.get_dom_snapshot()
                text = str((snap or {}).get("snapshot_text") or (snap or {}).get("text") or "")
                out["devtools_mcp"] = {"available": True, "text": text[:16000], "matched": _contains_semantic(text, expected_label)}
            except Exception as exc:
                out["devtools_mcp"] = {"available": False, "error": mask_sensitive_string(str(exc))[:900]}
        return out

    @staticmethod
    def _candidate_by_semantic_id(state: Mapping[str, Any], semantic_id: str) -> Dict[str, Any]:
        for c in state.get("controls", []) if isinstance(state, Mapping) else []:
            if isinstance(c, Mapping) and str(c.get("semantic_control_id") or "") == semantic_id:
                return dict(c)
        return {}

    @staticmethod
    def _safe_structural_action(action: str, label: str) -> bool:
        text = _norm(f"{action} {label}")
        # Non-committing navigation/structure actions may trust the exact reviewed
        # Playwright locator at a lower confidence threshold. Final mutations never
        # enter this lane.
        blocked = ("save", "submit", "delete", "remove", "deploy", "publish", "update", "confirm", "migrate")
        if any(word in text for word in blocked):
            return False
        return any(word in text for word in (
            "structural opener", "navigation", "open ", "expand", "collapse",
            "tab", "next", "continue", "back", "cancel", "close", "+ add", "add row"
        ))

    def _execution_policy(self, *, action: str, label: str, anchored: bool) -> Dict[str, Any]:
        structural = bool(anchored and self._safe_structural_action(action, label))
        if structural:
            return {"threshold": self.structural_execute_threshold, "margin": 0.0, "structural": True, "anchored": True}
        if anchored:
            return {"threshold": self.anchored_execute_threshold, "margin": self.anchored_ambiguity_margin, "structural": False, "anchored": True}
        return {"threshold": self.execute_threshold, "margin": self.ambiguity_margin, "structural": False, "anchored": False}

    async def resolve(
        self,
        *,
        page: Any,
        locator: Any,
        action: str,
        selector: str,
        label: str,
        phase: str,
        playwright_mcp: Any = None,
        devtools_mcp: Any = None,
        hip_intelligence_mcp: Any = None,
        vision_runtime: Any = None,
        expected_section: str = "",
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"pass": True, "status": "disabled", "confidence": 1.0, "candidate": {}, "values_stored": False}
        before = await capture_semantic_state(page, locator=locator)
        controls = list(before.get("controls") or [])
        anchored = next((dict(c) for c in controls if isinstance(c, Mapping) and c.get("anchor_match")), {})
        derived_label = str(anchored.get("label") or anchored.get("aria_label") or anchored.get("placeholder") or label or selector or action)
        derived_section = str(expected_section or anchored.get("section") or "")
        evidence = await self._external_evidence(expected_label=derived_label, playwright_mcp=playwright_mcp, devtools_mcp=devtools_mcp)
        ranked = rank_semantic_candidates(
            candidates=controls,
            action=action,
            expected_label=derived_label,
            expected_section=derived_section,
            accessibility_text=str((evidence.get("playwright_mcp") or {}).get("text") or ""),
            devtools_text=str((evidence.get("devtools_mcp") or {}).get("text") or ""),
            memory=self.memory,
            world_model=self.world_model,
            phase=phase,
        )
        top = ranked[0] if ranked else {"candidate": {}, "score": 0.0, "reasons": []}
        # The deterministic HIP skill already selected one concrete live locator.
        # If that locator was successfully anchored into the current DOM, evaluate
        # that exact node instead of allowing an unrelated semantic look-alike to
        # outrank it. This is still fail-closed when no anchor can be proven.
        anchored_rows = [row for row in ranked if bool(row.get("anchored"))]
        if self.prefer_vetted_locator_anchor and anchored_rows:
            top = max(anchored_rows, key=lambda row: float(row.get("score") or 0.0))
        competitor_scores = [float(row.get("score") or 0.0) for row in ranked if row is not top]
        second_score = max(competitor_scores) if competitor_scores else 0.0
        confidence = float(top.get("score") or 0.0)
        margin = confidence - second_score
        candidate = dict(top.get("candidate") or {})
        anchored_ok = bool(candidate.get("anchor_match"))
        execution_policy = self._execution_policy(action=action, label=derived_label, anchored=anchored_ok)
        effective_execute_threshold = float(execution_policy["threshold"])
        effective_ambiguity_margin = float(execution_policy["margin"])

        hip_mcp: Dict[str, Any] = {"used": False, "available": False}
        if self.use_hip_mcp and hip_intelligence_mcp is not None:
            try:
                semantic_payload = {
                    "phase": phase,
                    "action": action,
                    "field": derived_label,
                    "expected_label": derived_label,
                    "execution_policy": execution_policy,
                    "effective_execute_threshold": round(effective_execute_threshold, 4),
                    "effective_ambiguity_margin": round(effective_ambiguity_margin, 4),
                    "section": derived_section,
                    "expected_section": derived_section,
                    "surface": before,
                    "candidates": controls,
                    "accessibility_text": str((evidence.get("playwright_mcp") or {}).get("text") or "")[:24000],
                    "devtools_text": str((evidence.get("devtools_mcp") or {}).get("text") or "")[:24000],
                }
                if hasattr(hip_intelligence_mcp, "find_control"):
                    remote = await hip_intelligence_mcp.find_control(semantic_payload)
                    hip_tool_used = "hip_find_control"
                else:
                    remote = await hip_intelligence_mcp.resolve_semantic_control(semantic_payload)
                    hip_tool_used = "resolve_semantic_control"
                remote_top = dict((remote or {}).get("candidate") or {}) if isinstance(remote, Mapping) else {}
                remote_sid = str((remote or {}).get("semantic_control_id") or "") if isinstance(remote, Mapping) else ""
                local_sid = semantic_control_id(candidate) if candidate else ""
                agreed = bool(remote_top and remote_sid and local_sid and remote_sid == local_sid)
                hip_mcp = {
                    "used": True,
                    "available": True,
                    "agreed": agreed,
                    "semantic_control_id": remote_sid,
                    "confidence": (remote or {}).get("confidence") if isinstance(remote, Mapping) else None,
                    "margin": (remote or {}).get("margin") if isinstance(remote, Mapping) else None,
                    "tool": hip_tool_used,
                    "status": (remote or {}).get("status") if isinstance(remote, Mapping) else None,
                }
                if agreed:
                    confidence = _clamp(confidence + 0.06)
                elif remote_top:
                    # In adaptive mode an independently-ranked MCP candidate may
                    # disagree on a generic safe opener. Do not let that erase a
                    # unique exact Playwright anchor; strict mode still penalizes.
                    if (not anchored_ok) or self.strict_external_evidence:
                        confidence = _clamp(confidence - 0.12)
                        margin = min(margin, 0.0)
            except Exception as exc:
                hip_mcp = {"used": True, "available": False, "error": mask_sensitive_string(str(exc))[:800]}
        elif self.use_hip_mcp:
            hip_mcp = {"used": True, "available": False, "reason": "HIP Intelligence MCP backend unavailable"}

        # Vision is a tie-breaker only. It never supplies coordinates/selectors and
        # never authorizes mutation controls.
        vision: Dict[str, Any] = {"used": False}
        needs_more = confidence < self.execute_threshold or margin < self.ambiguity_margin
        if anchored_ok:
            needs_more = confidence < effective_execute_threshold or margin < effective_ambiguity_margin
        if needs_more and self.use_vision and vision_runtime is not None and derived_label:
            try:
                v = await vision_runtime.recovery_context(
                    page=page,
                    task=(
                        f"Semantic target ambiguity check only. Expected control label: {derived_label}. "
                        f"Expected section: {derived_section or 'unspecified'}. Action family: {_action_family(action)}. "
                        "Report visible likely_interactable_labels only; do not propose a click or any customer value."
                    ),
                )
                labels = list(v.get("likely_interactable_labels") or []) if isinstance(v, Mapping) else []
                confirmed = any(_similarity(derived_label, x) >= 0.78 for x in labels)
                vision = {"used": True, "available": bool(v.get("available", True)) if isinstance(v, Mapping) else False, "confirmed": confirmed, "confidence": v.get("confidence") if isinstance(v, Mapping) else None, "labels": labels[:20]}
                if confirmed:
                    confidence = _clamp(confidence + self.vision_boost)
            except Exception as exc:
                vision = {"used": True, "available": False, "error": mask_sensitive_string(str(exc))[:800]}

        pw_evidence = evidence.get("playwright_mcp") or {}
        dt_evidence = evidence.get("devtools_mcp") or {}
        # V232 adaptive evidence quorum. The exact current Playwright locator anchor
        # remains mandatory. External MCP witnesses strengthen the proof when healthy,
        # but their *unavailability* blocks only the explicit strict-MCP profile.
        # A healthy witness that positively disagrees still lowers/blocks strict proof.
        mcp_observed = bool(pw_evidence.get("available") and pw_evidence.get("matched"))
        devtools_observed = bool(dt_evidence.get("available") and dt_evidence.get("matched"))
        hip_available = bool(hip_mcp.get("available"))
        mcp_ok = mcp_observed or not self.require_playwright_mcp or not self.strict_external_evidence
        devtools_ok = devtools_observed or not self.require_devtools or not self.strict_external_evidence
        hip_mcp_ok = hip_available or not self.require_hip_mcp or not self.strict_external_evidence
        hip_mcp_consensus_ok = (not hip_available) or bool(hip_mcp.get("agreed")) or not self.strict_external_evidence
        ambiguity_ok = (
            margin >= effective_ambiguity_margin
            or bool(execution_policy.get("structural"))
            or (anchored_ok and confidence >= effective_execute_threshold + 0.04)
        )
        passed = bool(candidate and anchored_ok and confidence >= effective_execute_threshold and ambiguity_ok and mcp_ok and devtools_ok and hip_mcp_ok and hip_mcp_consensus_ok)
        if passed:
            status = "approved"
        elif candidate and confidence >= self.reobserve_threshold:
            status = "reobserve"
        elif candidate and confidence >= self.self_heal_threshold:
            status = "rediscover"
        else:
            status = "blocked"
        reason_parts: List[str] = []
        if not candidate: reason_parts.append("no semantic control candidate")
        if candidate and not anchored_ok: reason_parts.append("top semantic candidate is not the vetted Playwright locator")
        if confidence < effective_execute_threshold: reason_parts.append(f"confidence {confidence:.3f} below {effective_execute_threshold:.3f}")
        if not ambiguity_ok: reason_parts.append(f"candidate margin {margin:.3f} below {effective_ambiguity_margin:.3f}")
        if not mcp_ok: reason_parts.append("Playwright MCP evidence did not prove the intended control")
        if not devtools_ok: reason_parts.append("Chrome DevTools MCP evidence did not prove the intended control")
        if not hip_mcp_ok: reason_parts.append("HIP Intelligence MCP semantic evidence unavailable")
        if hip_mcp.get("available") and not hip_mcp_consensus_ok: reason_parts.append("HIP Intelligence MCP disagreed with the local semantic target")

        fused_source_scores = dict(top.get("source_scores") or {})
        fused_source_scores["hip_intelligence_consensus"] = 1.0 if hip_mcp.get("available") and hip_mcp.get("agreed") else 0.0
        fused_source_scores["vision"] = 1.0 if vision.get("used") and vision.get("confirmed") else (0.0 if vision.get("used") else None)
        result = SemanticGateResult(
            pass_=passed,
            status=status,
            confidence=confidence,
            margin=margin,
            candidate=candidate,
            evidence={
                "expected_label": derived_label,
                "expected_section": derived_section,
                "action_family": _action_family(action),
                "execution_policy": execution_policy,
                "effective_execute_threshold": round(effective_execute_threshold, 4),
                "effective_ambiguity_margin": round(effective_ambiguity_margin, 4),
                "playwright_mcp": {k: v for k, v in (evidence.get("playwright_mcp") or {}).items() if k != "text"},
                "devtools_mcp": {k: v for k, v in (evidence.get("devtools_mcp") or {}).items() if k != "text"},
                "hip_intelligence_mcp": hip_mcp,
                "vision": vision,
                "ranked_candidates": [
                    {"score": r.get("score"), "anchored": r.get("anchored"), "reasons": r.get("reasons"), "source_scores": r.get("source_scores") or {}, "semantic_control_id": semantic_control_id(r.get("candidate") or {}), "label": (r.get("candidate") or {}).get("label"), "section": (r.get("candidate") or {}).get("section"), "role": (r.get("candidate") or {}).get("role"), "selector": (r.get("candidate") or {}).get("selector")}
                    for r in ranked[:6]
                ],
                "before_state_fingerprint": before.get("state_fingerprint"),
                "dom_generation": before.get("dom_generation"),
                "owned_surface_visible": candidate.get("owned_surface_visible") if candidate else None,
                "source_scores": fused_source_scores,
                "target_confidence": round(confidence, 4),
            },
            reason="; ".join(reason_parts),
        ).as_dict()
        result["before_state"] = {k: v for k, v in before.items() if k != "controls"}
        result["candidate_before"] = candidate
        self._sequence += 1
        safe_write_json(self.evidence_dir / f"{self._sequence:05d}_pre_{_action_family(action)}.json", result)
        if not passed and self.fail_closed:
            return result
        if not passed and not self.fail_closed:
            result["pass"] = True
            result["status"] = "warning_fail_open"
        return result

    async def revalidate(self, *, page: Any, resolution: Mapping[str, Any]) -> Dict[str, Any]:
        """Re-prove the same semantic control immediately before dispatch.

        If Angular/DDS rerendered, a stable semantic selector may change. We search
        by fingerprint instead of DOM index and return the freshly resolved selector.
        """
        sid = str(resolution.get("semantic_control_id") or "")
        fp = str(resolution.get("control_fingerprint") or "")
        state = await capture_semantic_state(page)
        matches = [dict(c) for c in state.get("controls", []) if isinstance(c, Mapping) and (str(c.get("semantic_control_id") or "") == sid or str(c.get("control_fingerprint") or "") == fp)]
        candidate: Dict[str, Any] = {}
        status = "missing"
        if len(matches) == 1:
            candidate = matches[0]
            status = "stable"
        elif len(matches) > 1:
            before_generation = int(((resolution.get("evidence") or {}).get("dom_generation") or 0))
            current_generation = int(state.get("dom_generation") or 0)
            before_candidate = dict(resolution.get("candidate_before") or resolution.get("candidate") or {})
            binding = str(before_candidate.get("current_generation_row_binding") or "")
            if binding and current_generation == before_generation:
                bound = [c for c in matches if str(c.get("current_generation_row_binding") or "") == binding]
                if len(bound) == 1:
                    candidate = bound[0]
                    status = "stable_generation_bound"
                else:
                    status = "ambiguous_same_generation"
            else:
                status = "ambiguous_after_rerender" if current_generation != before_generation else "ambiguous"
        unique = bool(candidate)
        action_family = str(((resolution.get("evidence") or {}).get("action_family") or ""))
        hidden_upload_ok = action_family == "upload" and _norm(candidate.get("tag")) == "input" and _norm(candidate.get("type")) == "file"
        return mask_sensitive_data({
            "pass": unique and bool(candidate.get("enabled", True)) and (bool(candidate.get("visible", True)) or hidden_upload_ok),
            "status": status,
            "match_count": len(matches),
            "candidate": candidate,
            "hidden_upload_ok": bool(hidden_upload_ok),
            "semantic_control_id": sid,
            "control_fingerprint": fp,
            "dom_generation": state.get("dom_generation"),
            "state_fingerprint": state.get("state_fingerprint"),
        })

    async def verify_and_learn(
        self,
        *,
        page: Any,
        resolution: Mapping[str, Any],
        action: str,
        phase: str,
        exact_value_verified: bool = False,
    ) -> Dict[str, Any]:
        after = await capture_semantic_state(page)
        sid = str(resolution.get("semantic_control_id") or "")
        before_state = dict(resolution.get("before_state") or {})
        candidate_before = dict(resolution.get("candidate_before") or resolution.get("candidate") or {})
        candidate_after = self._candidate_by_semantic_id(after, sid)
        effect = verify_semantic_effect(
            before=before_state,
            after=after,
            action=action,
            target_before=candidate_before,
            target_after=candidate_after,
            exact_value_verified=exact_value_verified,
        )
        effect["semantic_control_id"] = sid
        effect["control_fingerprint"] = str(resolution.get("control_fingerprint") or "")
        effect["after_state_fingerprint"] = after.get("state_fingerprint")
        self._sequence += 1
        safe_write_json(self.evidence_dir / f"{self._sequence:05d}_post_{_action_family(action)}.json", effect)
        label = str((resolution.get("evidence") or {}).get("expected_label") or candidate_before.get("label") or "")
        section = str((resolution.get("evidence") or {}).get("expected_section") or candidate_before.get("section") or "")
        if effect.get("pass"):
            self.memory.promote(phase=phase, action=action, label=label, section=section, candidate=candidate_before, effect=effect)
            self.memory.record_transition(phase=phase, action=action, semantic_id=sid, before=before_state, after=after, effect=effect)
            world_update = self.world_model.record_verified_transition(
                phase=phase, action=action, label=label, section=section, candidate=candidate_before,
                before=before_state, after=after, effect=effect,
            )
        else:
            world_update = self.world_model.record_failure(
                phase=phase, action=action, label=label, section=section, candidate=candidate_before,
                before=before_state, after=after, effect=effect,
            )
        effect["world_model_update"] = world_update
        effect["world_model_summary"] = self.world_model.summary(phase=phase)
        return effect


def semantic_control_mcp_status() -> Dict[str, Any]:
    return {
        "schema_version": "hip.semantic-control-capabilities.v1",
        "tools": [
            "resolve_semantic_control",
            "rank_semantic_candidates",
            "verify_semantic_action_effect",
            "get_semantic_control_fingerprint",
            "get_semantic_control_capabilities",
            "hip_get_current_surface",
            "hip_get_form_schema",
            "hip_find_control",
            "hip_find_owned_popup",
            "hip_get_repeatable_rows",
            "hip_get_required_fields",
            "hip_get_current_values",
            "hip_compare_expected_actual",
            "hip_get_safe_actions",
            "hip_verify_action_effect",
            "hip_get_route_identity",
            "hip_get_form_generation",
        ],
        "browser_owned": False,
        "values_stored": False,
        "purpose": "HIP-specific semantic website brain: surface/form/control/popup/row/required-field/safe-action/route/generation/effect intelligence; PyAutoGUI MCP is the primary physical browser executor; Playwright MCP is deterministic fallback/verification",
        "executor": "none",
        "devtools_role": "independent_observer",
        "vision_role": "ambiguity_only",
    }
