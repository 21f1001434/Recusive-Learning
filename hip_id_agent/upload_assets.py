from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .security import mask_sensitive_string, mask_sensitive_data

FILE_EXTENSIONS = {".jar", ".xbm", ".xml", ".xsd", ".xlsx", ".xls", ".csv", ".txt", ".json", ".edi", ".zip"}


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())



def parse_accept_extensions(value: Any) -> List[str]:
    """Normalize an HTML file-input ``accept`` attribute to lowercase suffixes.

    MIME wildcards are intentionally not guessed into extensions. When the portal
    provides explicit suffixes (as HIP Data Map does), those are authoritative.
    """
    out: List[str] = []
    for raw in re.split(r"[,;\s]+", str(value or "")):
        item = raw.strip().lower()
        if not item:
            continue
        if item.startswith("."):
            out.append(item)
    return list(dict.fromkeys(out))


def asset_matches_accept(asset: Optional[Dict[str, Any]], accepted_extensions: Sequence[str] | None) -> bool:
    if not asset:
        return False
    accepted = {str(x).lower() for x in (accepted_extensions or []) if str(x).strip()}
    if not accepted:
        return True
    return str(asset.get("suffix") or Path(str(asset.get("file") or "")).suffix).lower() in accepted


def _asset_roots(input_data: Dict[str, Any]) -> List[Path]:
    roots: List[Path] = []
    for key in ["_upload_assets_dir", "upload_assets_dir", "upload_dir", "uploads_dir"]:
        val = input_data.get(key) if isinstance(input_data, dict) else None
        if val:
            roots.append(Path(str(val)))
    # Package default: repo-root/uploads when command is launched from project root.
    roots.append(Path("uploads"))
    # Also support examples/../uploads when input_json is in examples.
    src = input_data.get("_input_json_path") if isinstance(input_data, dict) else None
    if src:
        try:
            roots.append(Path(str(src)).resolve().parent.parent / "uploads")
            roots.append(Path(str(src)).resolve().parent / "uploads")
        except Exception:
            pass
    out: List[Path] = []
    seen = set()
    for p in roots:
        try:
            r = p if p.is_absolute() else (Path.cwd() / p)
            r = r.resolve()
        except Exception:
            r = p
        if str(r).lower() not in seen:
            seen.add(str(r).lower())
            out.append(r)
    return out


def list_upload_assets(input_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for root in _asset_roots(input_data or {}):
        if not root.exists() or not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in FILE_EXTENSIONS:
                rows.append({
                    "file": p.name,
                    "path": str(p),
                    "relative_path": str(p.relative_to(root)) if p.is_relative_to(root) else p.name,
                    "suffix": p.suffix.lower(),
                    "bytes": p.stat().st_size,
                    "root": str(root),
                })
    # De-dupe exact resolved path.
    seen = set()
    out = []
    for r in rows:
        k = str(r.get("path") or "").lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _preferred_exts(field_key: str = "", label: str = "", phase: str = "") -> List[str]:
    text = f"{field_key} {label} {phase}".lower()
    # Be specific for Data Map uploads: mapData is the transform JAR, schema
    # controls must not accidentally receive the JAR.  The user's UHAUL assets
    # contain XML samples for schema validation evidence.
    if any(x in text for x in ["inputschema", "input schema", "input_schema", "source schema"]):
        return [".xsd", ".json", ".edi", ".txt"]
    if any(x in text for x in ["outputschema", "output schema", "output_schema", "target schema"]):
        return [".xsd", ".json", ".edi", ".txt"]
    if any(x in text for x in ["mapdata", "map data", "map_data", "jar", "contivo", "transform"]):
        return [".jar", ".xbm", ".zip"]
    if any(x in text for x in ["schema", "xsd"]):
        return [".xsd", ".json", ".edi", ".txt"]
    if any(x in text for x in ["payload", "sample", "xml", "document"]):
        return [".xml", ".xsd"]
    if any(x in text for x in ["manual", "configuration"]):
        return [".xlsx", ".xls"]
    return [".jar", ".xbm", ".xsd", ".xml", ".xlsx"]


def find_upload_asset(input_data: Dict[str, Any], desired_value: Any = None, *, field_key: str = "", label: str = "", phase: str = "", accepted_extensions: Sequence[str] | None = None, require_explicit: bool = False) -> Optional[Dict[str, Any]]:
    # V243R7: an explicit path in input.json is authoritative. Older builds only
    # searched the uploads directory by basename, so a valid absolute/relative
    # user path could be reported as missing and the Data Map upload was skipped.
    desired = str(desired_value or "").strip().strip('"\'')
    direct_candidates: List[Path] = []
    if desired:
        direct = Path(desired)
        direct_candidates.append(direct)
        if not direct.is_absolute():
            direct_candidates.append(Path.cwd() / direct)
            src = input_data.get("_input_json_path") if isinstance(input_data, dict) else None
            if src:
                try:
                    direct_candidates.append(Path(str(src)).resolve().parent / direct)
                except Exception:
                    pass
        seen_direct = set()
        for candidate in direct_candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except Exception:
                resolved = candidate
            key = str(resolved).lower()
            if key in seen_direct:
                continue
            seen_direct.add(key)
            if resolved.is_file():
                suffix = resolved.suffix.lower()
                accepted = {str(x).lower() for x in (accepted_extensions or []) if str(x).strip()}
                if accepted and suffix not in accepted:
                    return None
                return {
                    "file": resolved.name, "path": str(resolved), "relative_path": resolved.name,
                    "suffix": suffix, "bytes": resolved.stat().st_size, "root": str(resolved.parent),
                    "source": "explicit_input_path",
                }

    assets = list_upload_assets(input_data or {})
    if not assets:
        return None
    accepted = {str(x).lower() for x in (accepted_extensions or []) if str(x).strip()}
    if accepted:
        assets = [a for a in assets if asset_matches_accept(a, accepted)]
        if not assets:
            return None
    desired_name = Path(desired).name if desired else ""
    desired_norm = _norm(desired_name)
    # 1) Exact basename from input.json, e.g. Transform_DELLCoXMLASNXX08C.jar.
    if desired_norm:
        for a in assets:
            if _norm(a.get("file")) == desired_norm:
                return a
        for a in assets:
            if desired_norm in _norm(a.get("file")) or _norm(a.get("file")) in desired_norm:
                return a
    if require_explicit and not desired_norm:
        return None
    # 2) Semantic filename preference for UHAUL sample assets.
    semantic = f"{field_key} {label} {phase}".lower()
    if "output" in semantic:
        output_matches = [a for a in assets if re.search(r"output|target|shipment|asn", str(a.get("file") or ""), re.I) and str(a.get("suffix") or "").lower() in {".xml", ".xsd", ".json", ".txt", ".edi"}]
        if output_matches:
            output_matches.sort(key=lambda a: (0 if re.search(r"output", str(a.get("file") or ""), re.I) else 1, str(a.get("file") or "")))
            return output_matches[0]
    if "input" in semantic or "source" in semantic:
        input_matches = [a for a in assets if re.search(r"^o\.|uhaul|source|input|asn", str(a.get("file") or ""), re.I) and str(a.get("suffix") or "").lower() in {".xml", ".xsd", ".json", ".txt", ".edi", ".xbm"}]
        if input_matches:
            input_matches.sort(key=lambda a: (0 if re.search(r"^o\.", str(a.get("file") or ""), re.I) else 1, str(a.get("file") or "")))
            return input_matches[0]
    # 3) Field/phase extension preference.
    for ext in _preferred_exts(field_key, label, phase):
        matches = [a for a in assets if str(a.get("suffix") or "").lower() == ext]
        if matches:
            # Prefer UHAUL/DELL transform names when available.
            matches.sort(key=lambda a: (0 if re.search(r"u.?haul|dell|transform", str(a.get("file") or ""), re.I) else 1, str(a.get("file") or "")))
            return matches[0]
    return assets[0]


async def _resolve_file_input_selector(page: Any, selector: str, *, field_key: str = "", label: str = "") -> Optional[str]:
    """Resolve a stable file input selector at upload time.

    DDS regenerates ids such as ``file-input-control-*`` after Angular re-renders,
    so a selector captured before fill can become stale.  Prefer stable name
    attributes (mapData/inputSchema/outputSchema), then visible label proximity,
    then ordered file inputs in the active drawer/form.
    """
    key = (field_key or "").strip().lower()
    lab = (label or "").strip().lower()
    # Stable name hints for Data Map file controls.
    name_hints = []
    if "map" in key or "mapdata" in lab or "map data" in lab:
        name_hints += ["mapData", "map_data", "map"]
    if "input" in key or "inputschema" in lab or "input schema" in lab:
        name_hints += ["inputSchema", "input_schema", "sourceSchema", "source_schema"]
    if "output" in key or "outputschema" in lab or "output schema" in lab:
        name_hints += ["outputSchema", "output_schema", "targetSchema", "target_schema"]

    # Try the exact selector only if it is still present.
    try:
        if selector:
            present = await page.locator(selector).count()
            if present:
                return selector
    except Exception:
        pass

    # Try stable name attributes.
    for name in name_hints:
        for sel in [f'input[type="file"][name="{name}"]', f'input[type="file"][id*="{name}" i]']:
            try:
                if await page.locator(sel).count():
                    return sel
            except Exception:
                pass

    js = r"""
({sel, fieldKey, label}) => {
  function visible(el){ const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width||r.height||el.offsetParent||el.files) && s.display !== 'none' && s.visibility !== 'hidden'; }
  function cssPath(el){
    if (!el) return null;
    if (el.id) return '#'+CSS.escape(el.id);
    if (el.getAttribute('name')) return el.tagName.toLowerCase()+'[name="'+CSS.escape(el.getAttribute('name'))+'"]';
    const parts=[]; let node=el;
    while(node && node.nodeType===1 && parts.length<6){
      let part=node.tagName.toLowerCase();
      if(node.className && typeof node.className === 'string'){
        const cls=node.className.trim().split(/\s+/).filter(Boolean).slice(0,2).map(c=>'.'+CSS.escape(c)).join('');
        part += cls;
      }
      const parent=node.parentElement;
      if(parent){
        const same=Array.from(parent.children).filter(x=>x.tagName===node.tagName);
        if(same.length>1) part += `:nth-of-type(${same.indexOf(node)+1})`;
      }
      parts.unshift(part); node=parent;
    }
    return parts.join(' > ');
  }
  const lowerKey=(fieldKey||'').toLowerCase();
  const lowerLabel=(label||'').toLowerCase();
  const files=Array.from(document.querySelectorAll('input[type="file"]'));
  const activeRoots=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,[role="dialog"],.dds__modal,form,main')).filter(visible);
  const activeFiles=files.filter(f => activeRoots.some(r => r.contains(f)) || visible(f));
  function score(f, idx){
    const name=(f.getAttribute('name')||'').toLowerCase();
    const id=(f.id||'').toLowerCase();
    const aria=(f.getAttribute('aria-label')||'').toLowerCase();
    const txt=(name+' '+id+' '+aria).toLowerCase();
    let s=0;
    if ((lowerKey.includes('map') || lowerLabel.includes('map')) && /mapdata|map[_-]?data|map/.test(txt)) s+=80;
    if ((lowerKey.includes('input') || lowerLabel.includes('input')) && /inputschema|input[_-]?schema|source/.test(txt)) s+=80;
    if ((lowerKey.includes('output') || lowerLabel.includes('output')) && /outputschema|output[_-]?schema|target/.test(txt)) s+=80;
    if (lowerLabel && txt.includes(lowerLabel.replace(/\s+/g,''))) s+=40;
    s += Math.max(0, 10-idx);
    return s;
  }
  const candidates=(activeFiles.length?activeFiles:files).map((f,i)=>({f,s:score(f,i)})).sort((a,b)=>b.s-a.s);
  if(candidates.length && candidates[0].s>0) return cssPath(candidates[0].f);
  // Ordered fallback for 3 Data Map controls.
  const ordered = activeFiles.length ? activeFiles : files;
  if (ordered.length >= 3) {
    if (lowerKey.includes('map')) return cssPath(ordered[0]);
    if (lowerKey.includes('input')) return cssPath(ordered[1]);
    if (lowerKey.includes('output')) return cssPath(ordered[2]);
  }
  if (ordered.length === 1) return cssPath(ordered[0]);
  return null;
}
"""
    try:
        resolved = await page.evaluate(js, {"sel": selector or "", "fieldKey": field_key or "", "label": label or ""})
        return str(resolved) if resolved else None
    except Exception:
        return None


async def _file_upload_validation_state(page: Any, selector: str, filename: str) -> Dict[str, Any]:
    """Return field-scoped upload acceptance, not filename visibility alone."""
    if not selector:
        return {"blocking": True, "reason": "file input selector missing"}
    js = r"""
({selector, filename}) => {
  const el=document.querySelector(selector);
  if(!el) return {blocking:true, reason:'file input not found after upload', messages:[]};
  const wrap=el.closest('.dds__file-input,.dds__form__field,.dds__form-field,fieldset,div') || el.parentElement;
  const text=String((wrap && (wrap.innerText||wrap.textContent)) || '').replace(/\s+/g,' ').trim();
  const ariaInvalid=el.getAttribute('aria-invalid')==='true' || !!(wrap && wrap.querySelector('[aria-invalid="true"]'));
  const errorNode=wrap && wrap.querySelector('.dds__file-input__item--error,.dds__error-text,[class*="error"]');
  const errorText=String((errorNode && (errorNode.innerText||errorNode.textContent)) || '').replace(/\s+/g,' ').trim();
  const bad=/not allowed|not supported|invalid|failed|error:/i.test(text+' '+errorText);
  const files=Array.from(el.files||[]).map(f=>f.name);
  return {blocking:!!(ariaInvalid||bad), aria_invalid:ariaInvalid, messages:[errorText,text].filter(Boolean).slice(0,4), files, filename_present:files.includes(filename)};
}
"""
    try:
        state = await page.evaluate(js, {"selector": selector, "filename": filename})
        if not isinstance(state, dict):
            return {"blocking": True, "reason": "upload validation returned no structured state"}
        if state.get("blocking"):
            state["reason"] = "portal rejected the selected upload: " + "; ".join(str(x) for x in state.get("messages") or [] if x)[:800]
        elif not state.get("filename_present"):
            state["blocking"] = True
            state["reason"] = "selected file was not committed to the target input"
        return state
    except Exception as exc:
        return {"blocking": True, "reason": mask_sensitive_string(str(exc))}




async def _semantic_upload_prepare(page: Any, *, selector: str, label: str, phase: str) -> tuple[Dict[str, Any], Any, str, Dict[str, Any]]:
    """Prove and rebind a file input before ``set_input_files``.

    File inputs are frequently visually hidden behind a DDS upload button.  Layer 11
    therefore permits a hidden *input[type=file]* only when that exact input is the
    vetted locator anchor; it never authorizes a generic hidden control.
    """
    loc = page.locator(selector).first
    session = getattr(page, "_hip_browser_session", None)
    if session is None or not hasattr(session, "_semantic_action_preflight"):
        return ({"pass": True, "status": "session_unavailable", "confidence": 1.0}, loc, selector, {"pass": True, "status": "not_required"})
    previous_phase = getattr(session, "_active_phase_name", "")
    if phase and not previous_phase:
        session._active_phase_name = phase
    try:
        resolution = await session._semantic_action_preflight(
            action="upload", locator=loc, selector=selector, label=label or "HIP file upload"
        )
        fresh_loc, fresh_selector, revalidation = await session._semantic_dispatch_target(
            resolution=resolution, locator=loc, selector=selector
        )
        return resolution, fresh_loc, fresh_selector, revalidation
    finally:
        if phase and not previous_phase:
            session._active_phase_name = previous_phase


async def _semantic_upload_commit(page: Any, *, resolution: Dict[str, Any], phase: str, exact_commit: bool) -> Dict[str, Any]:
    session = getattr(page, "_hip_browser_session", None)
    if session is None or not resolution.get("semantic_control_id") or not hasattr(session, "_semantic_post_action_verify"):
        return {"pass": True, "status": "not_required", "confidence": 1.0}
    previous_phase = getattr(session, "_active_phase_name", "")
    if phase and not previous_phase:
        session._active_phase_name = phase
    try:
        return await session._semantic_post_action_verify(
            resolution=resolution, action="upload", exact_value_verified=bool(exact_commit)
        )
    finally:
        if phase and not previous_phase:
            session._active_phase_name = previous_phase

async def attempt_upload_for_control(page: Any, control: Dict[str, Any], input_data: Dict[str, Any], *, phase: str, field_key: str, desired_value: Any = None) -> Dict[str, Any]:
    """Upload a local asset into a file input without clicking Save/Create.

    The desired file comes from input.json when possible; otherwise a best-effort
    match is chosen from --upload-assets-dir / ./uploads.  The portal object is
    still not saved; this only attaches the file in the currently open form.
    """
    label = str(control.get("label") or control.get("ariaLabel") or control.get("placeholder") or "")
    selector = str(control.get("selector") or "")
    file_selector = await _resolve_file_input_selector(page, selector, field_key=field_key, label=label)
    accepted_extensions: List[str] = parse_accept_extensions(control.get("accept"))
    required = bool(control.get("required"))
    if file_selector:
        try:
            live_accept = await page.locator(file_selector).first.get_attribute("accept")
            accepted_extensions = parse_accept_extensions(live_accept) or accepted_extensions
            required = required or bool(await page.locator(file_selector).first.get_attribute("required"))
        except Exception:
            pass
    explicit_value = str(desired_value or "").strip()
    # Optional file controls must never be populated by guessing. This is the
    # exact failure seen in the 2026-07-16 Data Map run: XML payload samples were
    # injected into schema controls that only accept xsd/json/edi/txt.
    if not explicit_value and not required:
        return {
            "field": field_key,
            "label": label,
            "selector": selector,
            "file_input_selector": file_selector,
            "accepted_extensions": accepted_extensions,
            "filled": False,
            "success": True,
            "skipped": True,
            "upload_attempted": False,
            "reason": "optional file input omitted because input.json did not specify an asset",
            "phase": phase,
        }
    asset = find_upload_asset(
        input_data or {}, desired_value, field_key=field_key or "", label=label, phase=phase,
        accepted_extensions=accepted_extensions, require_explicit=not required,
    )
    audit: Dict[str, Any] = {
        "field": field_key,
        "label": label,
        "selector": selector,
        "value_redacted": mask_sensitive_string(str(desired_value or "")),
        "filled": False,
        "upload_attempted": True,
        "phase": phase,
        "asset": mask_sensitive_data(asset) if asset else None,
        "accepted_extensions": accepted_extensions,
        "dom_events": ["file_input_change"],
    }
    if not asset:
        audit["reason"] = "no upload asset found; set --upload-assets-dir or place files in ./uploads"
        return audit
    p = Path(str(asset.get("path") or ""))
    if not p.exists():
        audit["reason"] = "matched upload asset path does not exist"
        return audit
    audit["file_input_selector"] = file_selector
    if not file_selector:
        audit["reason"] = "no input[type=file] found near mapped control"
        return audit
    try:
        semantic_resolution, loc, file_selector, semantic_revalidation = await _semantic_upload_prepare(
            page, selector=file_selector, label=label or field_key or "HIP file upload", phase=phase
        )
        audit["semantic_gate"] = {
            "semantic_control_id": semantic_resolution.get("semantic_control_id") or "",
            "confidence": semantic_resolution.get("confidence"),
            "margin": semantic_resolution.get("margin"),
            "status": semantic_resolution.get("status") or "",
            "revalidation": semantic_revalidation.get("status") or "",
        }
        await loc.set_input_files(str(p), timeout=12000)
        await page.wait_for_timeout(350)
        filename = p.name
        try:
            visible_name = await page.evaluate("""
(name) => {
  const body=(document.body.innerText||document.body.textContent||'');
  const inputs=Array.from(document.querySelectorAll('input[type=file]')).map(i => Array.from(i.files||[]).map(f=>f.name).join(' ')).join(' ');
  return body.includes(name) || inputs.includes(name);
}
""", filename)
        except Exception:
            visible_name = False
        validation = await _file_upload_validation_state(page, file_selector, filename)
        audit["uploaded_file_name"] = filename
        audit["filename_visible_after_upload"] = bool(visible_name)
        audit["validation"] = validation
        audit["filled"] = bool(visible_name) and not bool(validation.get("blocking"))
        audit["success"] = audit["filled"]
        audit["reason"] = "upload accepted by active file control" if audit["filled"] else (validation.get("reason") or "upload was selected but not accepted")
        if audit["filled"]:
            semantic_effect = await _semantic_upload_commit(
                page, resolution=semantic_resolution, phase=phase, exact_commit=True
            )
            audit["semantic_effect"] = {
                "pass": bool(semantic_effect.get("pass")),
                "effect_type": semantic_effect.get("effect_type") or "",
                "confidence": semantic_effect.get("confidence"),
            }
        return audit
    except Exception as exc:
        # Last-resort retry using all stable selectors; captured DDS ids can disappear after re-render.
        retry_errors = [mask_sensitive_string(str(exc))]
        retry_selectors = []
        fk = (field_key or "").lower()
        if "map" in fk:
            retry_selectors += ['input[type="file"][name="mapData"]', 'input[type="file"]']
        elif "input" in fk:
            retry_selectors += ['input[type="file"][name="inputSchema"]', 'input[type="file"]']
        elif "output" in fk:
            retry_selectors += ['input[type="file"][name="outputSchema"]', 'input[type="file"]']
        else:
            retry_selectors += ['input[type="file"]']
        for rsel in retry_selectors:
            try:
                rloc = page.locator(rsel).first
                if await rloc.count():
                    retry_resolution, rloc, rebound_sel, retry_revalidation = await _semantic_upload_prepare(
                        page, selector=rsel, label=label or field_key or "HIP file upload", phase=phase
                    )
                    await rloc.set_input_files(str(p), timeout=12000)
                    await page.wait_for_timeout(350)
                    filename = p.name
                    validation = await _file_upload_validation_state(page, rebound_sel, filename)
                    exact_commit = not bool(validation.get("blocking")) and bool(validation.get("filename_present"))
                    if not exact_commit:
                        raise RuntimeError(validation.get("reason") or "retry upload did not commit to the semantically resolved file input")
                    effect = await _semantic_upload_commit(page, resolution=retry_resolution, phase=phase, exact_commit=True)
                    audit["filled"] = True
                    audit["success"] = True
                    audit["uploaded_file_name"] = filename
                    audit["file_input_selector"] = rebound_sel
                    audit["filename_visible_after_upload"] = True
                    audit["semantic_gate"] = {
                        "semantic_control_id": retry_resolution.get("semantic_control_id") or "",
                        "confidence": retry_resolution.get("confidence"),
                        "margin": retry_resolution.get("margin"),
                        "status": retry_resolution.get("status") or "",
                        "revalidation": retry_revalidation.get("status") or "",
                    }
                    audit["semantic_effect"] = {"pass": bool(effect.get("pass")), "effect_type": effect.get("effect_type") or "", "confidence": effect.get("confidence")}
                    audit["reason"] = "uploaded file into semantically re-resolved input[type=file]; no save/create was clicked"
                    return audit
            except Exception as exc2:
                retry_errors.append(mask_sensitive_string(str(exc2)))
        audit["reason"] = " | ".join(retry_errors[-3:])
        return audit
