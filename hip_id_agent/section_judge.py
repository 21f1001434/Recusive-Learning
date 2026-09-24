from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

from .aia_client import AIAClient, build_chat_payload_variants, extract_aia_response_text, extract_json_object, resolve_output_token_limit
from .config import AIAConfig
from .security import mask_sensitive_data, mask_sensitive_string
from .active_surface import classify_doctype_surface_text

_TRUE = {"1", "true", "yes", "on", "y"}


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in _TRUE


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[^a-z0-9._/\\*+()-]+", " ", text).strip()


def _value_variants(value: Any) -> set[str]:
    raw = _norm(value)
    if not raw:
        return set()
    out = {raw}
    out.add(raw.replace(" (", "(").replace(" )", ")"))
    out.add(re.sub(r"\((\d+)\.0\)", r"(\1)", raw))
    out.add(re.sub(r"\((\d+)\)", r"(\1.0)", raw))
    out.add(raw.replace("enable", "enabled"))
    out.add(raw.replace("enabled", "enable"))
    out.add(raw.replace("dell application", "application"))
    return {v.strip() for v in out if v.strip()}


def _values_equal(expected: Any, actual: Any) -> bool:
    """Exact committed-value comparison with only portal display equivalences."""
    if isinstance(expected, (list, tuple, set)) or isinstance(actual, (list, tuple, set)):
        def as_set(value: Any) -> set[str]:
            rows = value if isinstance(value, (list, tuple, set)) else re.split(r"\s*,\s*", str(value or ""))
            return {_norm(x) for x in rows if _norm(x)}
        return as_set(expected) == as_set(actual)
    ev = _value_variants(expected)
    av = _value_variants(actual)
    if ev & av:
        return True
    er, ar = _norm(expected), _norm(actual)
    try:
        return float(er) == float(ar)
    except Exception:
        return False


def _flatten_strings(value: Any) -> List[str]:
    out: List[str] = []
    if isinstance(value, dict):
        for v in value.values():
            out.extend(_flatten_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_flatten_strings(v))
    elif isinstance(value, (str, int, float)) and str(value).strip():
        out.append(str(value).strip())
    return out


def _image_data_url(path: str | Path) -> Optional[str]:
    p = Path(path)
    if not p.is_file():
        return None
    suffix = p.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg" if suffix in {".jpg", ".jpeg"} else "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"




def _canonical_field_key(value: Any) -> str:
    """Canonical semantic identity for model field aliases.

    Models may emit document_identifier_operation while deterministic evidence
    uses document_identifier.operation, or attributes[1].expression versus an
    input-path spelling. Punctuation is not semantic, so compare the compact
    alphanumeric identity while keeping the original names in audit output.
    """
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

def _matched_field_index(deterministic: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in deterministic.get("matched_values") or []:
        if not isinstance(row, dict):
            continue
        for name in (row.get("field"), row.get("input_path"), row.get("key"), row.get("label")):
            key = _canonical_field_key(name)
            if key:
                out.setdefault(key, row)
    return out


def _accepted_validation_text(deterministic: Dict[str, Any]) -> str:
    gate = deterministic.get("validation_gate") if isinstance(deterministic.get("validation_gate"), dict) else {}
    return _norm(" ".join(str(x.get("message") or "") for x in gate.get("accepted_nonblocking") or [] if isinstance(x, dict)))


def _verification_status_is_success(status: Any) -> bool:
    """Return True for completed verification states with no fatal evidence.

    ``pass_with_warnings`` is intentionally a successful deterministic state: the
    warnings are advisory evidence-quality notes (for example, not every DDS
    option list was enriched) and the verification builder separately marks any
    unsafe click, missing screenshot, failed exact fill, blocking validation, or
    lost active surface as ``failed``. Treating advisory warnings as failure
    disables model-contradiction reconciliation and can block an exact completed
    phase even when every input-bound value was proven.
    """
    return str(status or "").strip().lower() in {"pass", "pass_with_warnings"}


def _reconcile_text_judge(result: Dict[str, Any], deterministic: Dict[str, Any]) -> Dict[str, Any]:
    """Reject unsupported LLM contradictions against exact DOM evidence.

    The text model remains required and must return structured JSON, but it may
    not veto a field that the deterministic judge proved with a field-bound exact
    committed value. Unsupported hallucinated issues are retained for audit.
    """
    if not isinstance(result, dict) or result.get("status") != "ok":
        return result
    matched = _matched_field_index(deterministic)
    supported, unsupported = [], []
    for issue in result.get("missing_or_wrong") or []:
        if not isinstance(issue, dict):
            continue
        field = str(issue.get("field") or "")
        proof = matched.get(_canonical_field_key(field))
        issue_text = _norm(" ".join(str(issue.get(k) or "") for k in ("field", "expected", "actual", "reason")))
        accepted_text = _accepted_validation_text(deterministic)
        if proof and _values_equal(proof.get("expected"), proof.get("actual")):
            unsupported.append({**issue, "deterministic_proof": proof})
        elif accepted_text and "already exists" in issue_text and "already exists" in accepted_text:
            unsupported.append({**issue, "accepted_validation_evidence": accepted_text})
        else:
            supported.append(issue)
    out = dict(result)
    out["missing_or_wrong"] = supported
    if unsupported:
        out["unsupported_model_issues"] = unsupported
    if deterministic.get("pass") and not supported and result.get("pass") is False:
        out["original_pass"] = False
        out["pass"] = True
        out["status"] = "reconciled"
        out["summary"] = "Text judge contradiction removed because exact field-bound DOM evidence proves every reported field."
        out["repair_steps"] = []
        out["reconciliation_reason"] = "deterministic exact-value evidence is authoritative over unsupported model claims"
    return out


_GENERIC_VISUAL_FIELD_NAMES = {
    "checkbox", "check box", "radio", "radio button", "dropdown", "select",
    "combobox", "input", "field", "toggle", "switch", "button", "control",
}


def _vision_issue_has_concrete_field_binding(issue: Dict[str, Any], deterministic: Dict[str, Any]) -> bool:
    """Return True only when a visual issue names an input-bound semantic field.

    Screenshots can contain unrelated listing/filter checkboxes behind or beside a
    drawer. A model saying merely "checkbox not selected" is not actionable and
    must not veto an exact deterministic pass. Concrete field names, input paths,
    selectors, or row-scoped deterministic failures remain blocking.
    """
    field = _norm(issue.get("field"))
    if not field or field in _GENERIC_VISUAL_FIELD_NAMES:
        return False
    if any(issue.get(k) not in (None, "") for k in ("input_path", "selector", "row_index", "row_kind", "section")):
        return True
    deterministic_fields = set()
    for key in ("matched_values", "missing_values", "failed_attempts", "failed_attempts_from_phase", "row_issues"):
        for row in deterministic.get(key) or []:
            if not isinstance(row, dict):
                continue
            for name_key in ("field", "key", "label", "input_path"):
                value = _canonical_field_key(row.get(name_key))
                if value:
                    deterministic_fields.add(value)
    return _canonical_field_key(issue.get("field")) in deterministic_fields


def _reconcile_vision_judge(result: Dict[str, Any], deterministic: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile vision findings against exact, field-bound DOM evidence.

    Vision is required, but an unbound generic control claim cannot override a
    deterministic pass. This prevents background listing/filter controls from
    being mistaken for required fields in the active HIP form.
    """
    if not isinstance(result, dict) or result.get("status") != "ok":
        return result
    supported, unsupported = [], []
    for issue in result.get("visible_issues") or []:
        if not isinstance(issue, dict):
            continue
        expected, observed = issue.get("expected"), issue.get("observed")
        issue_text = _norm(" ".join(str(issue.get(k) or "") for k in ("field", "expected", "observed", "reason")))
        accepted_text = _accepted_validation_text(deterministic)
        matched_index = _matched_field_index(deterministic)
        deterministic_match = matched_index.get(_canonical_field_key(issue.get("field")))
        if deterministic_match is None:
            deterministic_match = next((m for m in deterministic.get("matched_values", []) if isinstance(m, dict) and _values_equal(expected, m.get("actual"))), None)
        if observed not in (None, "") and _values_equal(expected, observed):
            unsupported.append(issue)
        elif deterministic_match is not None:
            unsupported.append({**issue, "exact_dom_evidence": deterministic_match, "reason": "visual issue is offscreen/ambiguous but exact row-scoped DOM proves the value"})
        elif deterministic.get("pass") and not _vision_issue_has_concrete_field_binding(issue, deterministic):
            unsupported.append({**issue, "reason": "generic visual control claim is not bound to an expected input field, row, section, selector, or input path"})
        elif accepted_text and "already exists" in issue_text and "already exists" in accepted_text:
            unsupported.append({**issue, "accepted_validation_evidence": accepted_text})
        else:
            supported.append(issue)
    out = dict(result)
    out["visible_issues"] = supported
    if unsupported:
        out["unsupported_model_issues"] = unsupported
    if deterministic.get("pass") and not supported and result.get("pass") is False:
        out["original_pass"] = False
        out["pass"] = True
        out["status"] = "reconciled"
        out["summary"] = "Unsupported visual findings were removed because they were unbound, internally inconsistent, or contradicted by exact DOM evidence."
        out["reconciliation_reason"] = "only concrete input-bound visual mismatches may veto an exact deterministic pass"
    return out


@dataclass
class SectionJudgePolicy:
    enabled: bool = True
    require_text_model: bool = True
    require_vision_model: bool = True
    max_repairs: int = 2
    fail_closed: bool = True

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]] = None) -> "SectionJudgePolicy":
        p = payload if isinstance(payload, dict) else {}
        return cls(
            enabled=_truthy(p.get("enabled"), _truthy(os.getenv("HIP_SECTION_JUDGE"), True)),
            require_text_model=_truthy(p.get("require_text_model"), _truthy(os.getenv("HIP_REQUIRE_TEXT_JUDGE"), True)),
            require_vision_model=_truthy(p.get("require_vision_model"), _truthy(os.getenv("HIP_REQUIRE_VISION_JUDGE"), True)),
            max_repairs=max(0, int(p.get("max_repairs") or os.getenv("HIP_SECTION_JUDGE_MAX_REPAIRS") or 2)),
            fail_closed=_truthy(p.get("fail_closed"), _truthy(os.getenv("HIP_SECTION_JUDGE_FAIL_CLOSED"), True)),
        )


TEXT_JUDGE_SYSTEM = """
You are the strict text/state judge for a Dell HIP Portal deterministic form executor.
Return ONLY strict JSON, never markdown.
The deterministic DOM comparison is authoritative. Never mark pass when deterministic_pass is false,
when required expected values are missing, when a required field is blank/Select, or when a fill attempt failed.
Do not authorize Save/Create/Submit/Delete/Deploy.
Return:
{
  "pass": true|false,
  "summary": "...",
  "missing_or_wrong": [{"field":"...","expected":"...","actual":"...","reason":"..."}],
  "repair_steps": [{"field":"...","action":"refill|reselect|expand|add_row|rescan","value":"...","reason":"..."}],
  "confidence": 0.0
}
"""

VISION_JUDGE_SYSTEM = """
You are the strict visual judge for a Dell HIP Portal form section.
Return ONLY strict JSON, never markdown.
Inspect the current screenshot and, when supplied, golden screenshots. Verify visible values, row count,
selected dropdown values, radio states and that required controls are not blank or showing Select.
Report an issue only for a concrete field present in expected_facts/row_counts. Use its exact semantic field name;
never report generic names such as checkbox, radio, dropdown, input, switch, or control. Ignore unrelated
listing/filter/search-result controls outside the active form or drawer. Never infer that every visible checkbox
must be selected. Never mark pass when the active form visibly contains missing required values or wrong rows.
Do not authorize Save/Create/Submit/Delete/Deploy.
Return:
{
  "pass": true|false,
  "summary": "...",
  "visible_issues": [{"field":"...","expected":"...","observed":"..."}],
  "confidence": 0.0
}
"""


class DualModelSectionJudge:
    """Fail-closed section gate: deterministic DOM + Dell AIA text + Dell AIA vision.

    The judge never fills or clicks. It only evaluates evidence. The caller owns the
    deterministic repair callback and may retry the exact section before moving on.
    """

    def __init__(self, policy: Optional[SectionJudgePolicy] = None, aia_config: Optional[AIAConfig] = None):
        self.policy = policy or SectionJudgePolicy.from_payload()
        cfg = aia_config or AIAConfig(enabled=True)
        cfg.enabled = True
        self.aia = AIAClient(cfg)
        self._selected_vision_model: str = ""
        self._vision_preflight_result: Dict[str, Any] = {}

    async def capture_live_state(self, page: Any, *, root_selector: str = "body") -> Dict[str, Any]:
        js = r"""
({rootSelector}) => {
  const clean = s => String(s || '').replace(/\s+/g,' ').trim();
  const visible = el => {
    if (!el) return false;
    const st = getComputedStyle(el), r = el.getBoundingClientRect();
    return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || 1) > 0 && r.width > 1 && r.height > 1;
  };
  const path = el => {
    if (el.id) return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;
    const parts=[]; let cur=el;
    while(cur && cur.nodeType===1 && parts.length<6){
      let p=cur.tagName.toLowerCase();
      const cls=Array.from(cur.classList||[]).filter(x=>x && !/active|focus|open|selected/i.test(x)).slice(0,2);
      if(cls.length) p += '.'+cls.map(CSS.escape).join('.');
      const par=cur.parentElement;
      if(par){ const same=Array.from(par.children).filter(x=>x.tagName===cur.tagName); if(same.length>1) p+=`:nth-of-type(${same.indexOf(cur)+1})`; }
      parts.unshift(p); cur=par;
    }
    return parts.join(' > ');
  };
  const labelFor = el => {
    const id=el.id;
    if(id){ const lab=document.querySelector(`label[for="${CSS.escape(id)}"]`); if(lab) return clean(lab.innerText||lab.textContent); }
    const own=el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||'';
    const wrap=el.closest('.dds__form__field,.dds__form-field,.dds__field,fieldset,.dds__row,app-process-step,app-generic-drawer');
    const near=wrap ? clean(wrap.innerText||wrap.textContent).slice(0,700) : '';
    return clean([own,near].filter(Boolean).join(' '));
  };
  const root=document.querySelector(rootSelector)||document.body;
  const controls=Array.from(root.querySelectorAll('input,textarea,select,[role=combobox],[role=radio],[role=checkbox]'))
    .filter(visible).map(el=>{
      let value='';
      if(el.tagName==='SELECT') value=clean(el.selectedOptions?.[0]?.textContent||el.value);
      else value=clean(el.value||el.getAttribute('aria-valuetext')||el.getAttribute('data-value')||'');
      if((el.type==='radio'||el.type==='checkbox') && el.checked) value=value||'checked';
      const r=el.getBoundingClientRect();
      return {selector:path(el),label:labelFor(el),value,role:el.getAttribute('role')||'',type:el.type||'',required:!!el.required||el.getAttribute('aria-required')==='true',disabled:!!el.disabled||el.getAttribute('aria-disabled')==='true',x:r.x,y:r.y,w:r.width,h:r.height};
    });
  const text=clean(root.innerText||root.textContent).slice(0,50000);
  return {
    url: location.href,
    title: document.title,
    root_selector: rootSelector,
    controls,
    visible_text: text,
    row_counts: {
      process_steps: Array.from(root.querySelectorAll('app-process-step,app-workflow-process-step,[class*="process-step"]')).filter(visible).length,
      accordions: Array.from(root.querySelectorAll('dds-accordion-item,.dds__accordion__item')).filter(visible).length,
      condition_controls: controls.filter(c=>/condition type/i.test(c.label)).length,
      action_controls: controls.filter(c=>/(^|\s)type(\s|$)/i.test(c.label) && /action|route document|name/i.test(c.label)).length
    }
  };
}
"""
        try:
            return mask_sensitive_data(await page.evaluate(js, {"rootSelector": root_selector}))
        except Exception as exc:
            return {"controls": [], "visible_text": "", "capture_error": mask_sensitive_string(str(exc)), "root_selector": root_selector}

    def deterministic_judge(
        self,
        *,
        expected: Dict[str, Any],
        actual_state: Dict[str, Any],
        attempts: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        facts = expected.get("facts") if isinstance(expected.get("facts"), list) else []
        controls_raw = actual_state.get("controls") if isinstance(actual_state.get("controls"), list) else []
        controls = []
        for c in controls_raw:
            if not isinstance(c, dict):
                continue
            if c.get("trusted_for_exact_judge") is False:
                continue
            ctype = str(c.get("type") or c.get("role") or "").lower()
            value_norm = _norm(c.get("value"))
            # Background filter drawers contribute hundreds of unchecked
            # checkboxes with value=false. They are never evidence for text or
            # combobox fields and must not pollute exact-value candidates.
            if ctype in {"checkbox", "radio"} and value_norm in {"", "false", "0", "off", "unchecked"}:
                continue
            controls.append(c)
        actual_values = [_norm(c.get("value")) for c in controls if _norm(c.get("value"))]
        actual_text = _norm(actual_state.get("visible_text"))
        missing: List[Dict[str, Any]] = []
        if expected.get("expectation_error"):
            missing.append({
                "field": "phase_semantic_expectation",
                "expected": "phase-local state graph",
                "actual_candidates": [],
                "reason": str(expected.get("expectation_error")),
            })
        matched: List[Dict[str, Any]] = []
        for fact in facts:
            if not isinstance(fact, dict) or fact.get("required", True) is False:
                continue
            value = fact.get("value")
            if value is None or str(value).strip() == "":
                continue
            if str(value).lower().startswith("disabled(") or str(value).lower() in {"not enabled", "false"}:
                continue
            candidates = []
            aliases = [_norm(a) for a in fact.get("aliases", []) if _norm(a)]
            fact_section = _norm(fact.get("section"))
            fact_sections = [x for x in [fact_section, *[_norm(v) for v in fact.get("section_aliases", [])]] if x]
            fact_row_kind = _norm(fact.get("row_kind"))
            fact_row_index = fact.get("row_index")
            for c in controls:
                lab = _norm(c.get("label"))
                c_section = _norm(c.get("section"))
                c_row_kind = _norm(c.get("row_kind"))
                if fact_sections and c_section and not any(fs == c_section or fs in c_section or c_section in fs for fs in fact_sections):
                    continue
                if fact_row_kind and c_row_kind != fact_row_kind:
                    continue
                if fact_row_index is not None and c.get("row_index") != fact_row_index:
                    continue
                if not aliases or any(a in lab or lab in a for a in aliases if lab):
                    candidates.append(c)
            found = False
            actual_seen = []
            for c in candidates:
                actual_value = c.get("selected_values") if fact.get("match_mode") == "set" and c.get("selected_values") is not None else c.get("value")
                if actual_value not in (None, "", []):
                    actual_seen.append(actual_value)
                if _values_equal(value, actual_value):
                    found = True
                    matched.append({
                        "field": fact.get("field"), "input_path": fact.get("input_path"), "expected": value, "actual": actual_value,
                        "selector": c.get("selector"), "evidence": c.get("evidence"),
                        "section": c.get("section"), "row_kind": c.get("row_kind"), "row_index": c.get("row_index"),
                    })
                    break
            if not found:
                missing.append({"field": fact.get("field"), "expected": value, "actual_candidates": actual_seen[:12], "reason": "exact committed value was not found in a field-bound control"})

        row_issues: List[Dict[str, Any]] = []
        expected_rows = expected.get("row_counts") if isinstance(expected.get("row_counts"), dict) else {}
        actual_rows = actual_state.get("row_counts") if isinstance(actual_state.get("row_counts"), dict) else {}
        for key, wanted in expected_rows.items():
            try:
                got = int(actual_rows.get(key) or 0)
                wanted_i = int(wanted or 0)
            except Exception:
                continue
            if got != wanted_i:
                row_issues.append({"field": key, "expected": wanted_i, "actual": got, "reason": "repeatable row count must equal the deterministic plan exactly"})

        # Only the latest attempt for a semantic field/row is authoritative.
        # A failed first attempt must not permanently poison a section after a
        # successful deterministic repair/refill.
        latest_attempts: Dict[tuple, Dict[str, Any]] = {}
        for a in attempts or []:
            if not isinstance(a, dict) or a.get("planner_only") or a.get("restore_pass"):
                continue
            ident = (
                str(a.get("key") or a.get("field") or a.get("label") or ""),
                str(a.get("row_kind") or ""),
                a.get("row_index"),
                str(a.get("section") or a.get("bizflow_tab") or ""),
            )
            latest_attempts[ident] = a
        failed_attempts = []
        for a in latest_attempts.values():
            if a.get("success") is False and not a.get("skipped") and "disabled" not in str(a.get("reason") or "").lower():
                failed_attempts.append({k: a.get(k) for k in ("label", "key", "field", "expected_value", "actual_value", "reason", "row_kind", "row_index", "section", "bizflow_tab")})

        return {
            "pass": not missing and not row_issues and not failed_attempts,
            "missing_values": missing,
            "row_issues": row_issues,
            "failed_attempts": failed_attempts,
            "matched_values": matched,
            "actual_control_count": len(controls),
        }

    def _text_judge(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            decision = self.aia.json_decision(TEXT_JUDGE_SYSTEM, json.dumps(mask_sensitive_data(payload), ensure_ascii=False, default=str)[:50000])
            if not isinstance(decision, dict):
                return {"status": "error", "pass": False, "error": "non-object text judge response"}
            return {"status": "ok", **decision}
        except Exception as exc:
            return {"status": "error", "pass": False, "error": mask_sensitive_string(str(exc))}

    @staticmethod
    def _split_model_candidates(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            raw = []
            for item in value:
                raw.extend(DualModelSectionJudge._split_model_candidates(item))
            return list(dict.fromkeys(raw))
        parts = re.split(r"[,;\n]+", str(value))
        return list(dict.fromkeys(p.strip() for p in parts if p and p.strip()))

    def _explicit_vision_candidates(self) -> List[str]:
        """Return user/config supplied multimodal model candidates.

        MODEL_NAME is deliberately excluded because it is the text deployment.
        Extra aliases preserve compatibility with older Dell AIA/Gemma projects.
        """
        names = (
            "HIP_VISION_MODEL",
            "HIP_VISION_MODELS",
            "AIA_VISION_MODEL",
            "AIA_VISION_MODELS",
            "VISION_MODEL_NAME",
            "VISION_MODEL",
            "VISION_MODELS",
            "GEMMA_MODEL_NAME",
            "GEMMA_MODEL",
        )
        out: List[str] = []
        for name in names:
            out.extend(self._split_model_candidates(os.getenv(name)))
        return list(dict.fromkeys(out))

    def _auto_vision_candidates(self) -> List[str]:
        """Bounded, probe-only discovery candidates.

        These names come from the reviewed HIP Unified KB. A candidate is never
        accepted merely by name: the preflight must prove image understanding.
        Disable with HIP_VISION_AUTO_DISCOVERY=false.
        """
        if not _truthy(os.getenv("HIP_VISION_AUTO_DISCOVERY"), True):
            return []
        configured = self._split_model_candidates(os.getenv("HIP_VISION_AUTO_CANDIDATES"))
        defaults = ["gemma-3-27b-it", "pixtral-12b-2409"]
        return list(dict.fromkeys([*configured, *defaults]))

    def _vision_model_candidates(self) -> List[str]:
        explicit = self._explicit_vision_candidates()
        return explicit if explicit else self._auto_vision_candidates()

    def _vision_model(self) -> str:
        # A text deployment must never be silently treated as multimodal.
        if self._selected_vision_model:
            return self._selected_vision_model
        explicit = self._explicit_vision_candidates()
        return explicit[0] if explicit else ""

    def _vision_endpoint(self) -> Optional[str]:
        direct = (
            os.getenv("HIP_VISION_ENDPOINT")
            or os.getenv("AIA_VISION_ENDPOINT")
            or os.getenv("VISION_ENDPOINT")
        )
        if direct and str(direct).strip():
            value = str(direct).strip().rstrip("/")
            return value if value.endswith("/chat/completions") else value + "/chat/completions"
        return self.aia._endpoint()

    def _vision_token(self) -> Optional[str]:
        direct = os.getenv("HIP_VISION_TOKEN") or os.getenv("AIA_VISION_TOKEN") or os.getenv("VISION_TOKEN")
        return str(direct).strip() if direct and str(direct).strip() else self.aia.token_provider.get_token()

    @staticmethod
    def _vision_probe_payload(model: str) -> Dict[str, Any]:
        # 64x32 deterministic image: left half red, right half blue. The former
        # 2-pixel probe was vulnerable to multimodal resize/averaging.
        probe = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAAAgCAIAAAAt/+nTAAAAQklEQVR4nO3PQREAIBADMcC/Z1Bx5LMR0M7uu2btNftwRtc/KEArQCtAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAe+LxAj8B7QRwAAAAAElFTkSuQmCC"
        content = [
            {"type": "text", "text": "Inspect the image. State the LEFT-half color and RIGHT-half color. Include the words red and blue."},
            {"type": "image_url", "image_url": {"url": probe}},
        ]
        return {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0}

    def vision_preflight(self) -> Dict[str, Any]:
        """Discover and prove a Dell AIA deployment actually reads images.

        Resolution order:
        1. explicit HIP/AIA/VISION/GEMMA environment aliases;
        2. bounded auto candidates from the reviewed Unified KB;
        3. fail closed with attempted-model diagnostics and exact setup commands.
        """
        candidates = self._vision_model_candidates()
        explicit = bool(self._explicit_vision_candidates())
        if not candidates:
            result = {
                "status": "error",
                "pass": False,
                "error": "No multimodal model candidate configured and auto-discovery is disabled.",
                "accepted_aliases": ["HIP_VISION_MODEL", "AIA_VISION_MODEL", "VISION_MODEL_NAME", "VISION_MODEL", "GEMMA_MODEL_NAME"],
                "powershell": '$env:HIP_VISION_MODEL="<Dell-AIA-multimodal-model>"',
            }
            self._vision_preflight_result = result
            return result
        endpoint = self._vision_endpoint()
        token = self._vision_token()
        if not endpoint or not token:
            result = {
                "status": "error",
                "pass": False,
                "candidates": candidates,
                "error": "Dell AIA endpoint/token unavailable for multimodal vision preflight. BASE_URL plus existing Dell auth are supported; HIP_VISION_ENDPOINT/HIP_VISION_TOKEN are optional overrides.",
            }
            self._vision_preflight_result = result
            return result
        if _truthy(os.getenv("HIP_SKIP_VISION_CAPABILITY_PROBE"), False):
            model = candidates[0]
            self._selected_vision_model = model
            os.environ["HIP_VISION_MODEL_SELECTED"] = model
            result = {
                "status": "configured_probe_skipped",
                "pass": True,
                "model": model,
                "selection_source": "explicit" if explicit else "auto_candidate",
                "endpoint_configured": True,
                "warning": "Capability probe was skipped; use only for controlled offline testing.",
            }
            self._vision_preflight_result = result
            return result

        attempts: List[Dict[str, Any]] = []
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "accept": "application/json"}
        for model in candidates:
            token_limit = resolve_output_token_limit(None, vision=True)
            payloads = build_chat_payload_variants(self._vision_probe_payload(model), output_token_limit=token_limit)
            model_error = ""
            for payload in payloads:
                payload = {k: v for k, v in payload.items() if v is not None}
                try:
                    resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)
                    if resp.status_code >= 400:
                        model_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                        continue
                    data = resp.json()
                    raw = extract_aia_response_text(data)
                    norm = _norm(raw)
                    ok = "red" in norm and "blue" in norm
                    attempts.append({"model": model, "http_status": resp.status_code, "image_understanding": ok, "response": raw[:300]})
                    if ok:
                        self._selected_vision_model = model
                        # Share the proven deployment with the optional aggregate
                        # vision verifier later in the same process.
                        os.environ["HIP_VISION_MODEL_SELECTED"] = model
                        result = {
                            "status": "ok",
                            "pass": True,
                            "model": model,
                            "selection_source": "explicit" if explicit else "auto_discovery_probe",
                            "endpoint_configured": True,
                            "attempts": attempts,
                        }
                        self._vision_preflight_result = result
                        return result
                    model_error = "deployment responded but did not prove red/blue image understanding"
                    break
                except Exception as exc:
                    model_error = mask_sensitive_string(str(exc))
            attempts.append({"model": model, "image_understanding": False, "error": mask_sensitive_string(model_error or "probe failed")})

        result = {
            "status": "error",
            "pass": False,
            "error": "No tested Dell AIA deployment proved multimodal image understanding.",
            "selection_source": "explicit" if explicit else "auto_discovery_probe",
            "attempts": attempts,
            "powershell": '$env:HIP_VISION_MODEL="<working Dell-AIA multimodal deployment>"',
            "note": "The portal was not opened because strict vision judging is fail-closed.",
        }
        self._vision_preflight_result = result
        return result

    def _vision_judge(self, *, screenshot: str, golden_screenshots: Sequence[str], expected: Dict[str, Any]) -> Dict[str, Any]:
        model = self._vision_model()
        if not model:
            return {"status": "error", "pass": False, "error": "Explicit multimodal Dell AIA model is not configured"}
        data_url = _image_data_url(screenshot)
        if not data_url:
            return {"status": "error", "pass": False, "error": "section screenshot missing", "screenshot": screenshot}
        endpoint = self._vision_endpoint()
        token = self._vision_token()
        if not endpoint or not token:
            return {"status": "not_configured", "pass": False, "error": "Dell AIA endpoint/token unavailable for vision judge"}
        content: List[Dict[str, Any]] = [
            {"type": "text", "text": json.dumps({
                "section": expected.get("section"),
                "expected_facts": expected.get("facts"),
                "row_counts": expected.get("row_counts"),
                "object_resolution": expected.get("object_resolution"),
                "accepted_nonblocking_validation": expected.get("accepted_nonblocking_validation"),
                "instruction": "Report only true visible mismatches. Do not list a field as an issue when observed equals expected. Existing-object reuse may legitimately display a duplicate identifier notice in an unsaved create-form exploration.",
            }, ensure_ascii=False, default=str)[:18000]},
            {"type": "text", "text": "Current section screenshot:"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        for path in list(golden_screenshots)[:3]:
            g = _image_data_url(path)
            if g:
                content.extend([
                    {"type": "text", "text": f"Golden reference: {Path(path).name}"},
                    {"type": "image_url", "image_url": {"url": g}},
                ])
        base_payload = {"model": model, "messages": [{"role": "system", "content": VISION_JUDGE_SYSTEM}, {"role": "user", "content": content}], "temperature": 0}
        payloads = build_chat_payload_variants(base_payload, output_token_limit=resolve_output_token_limit(None, vision=True))
        last = ""
        for payload in payloads:
            try:
                resp = requests.post(endpoint, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "accept": "application/json"}, json=payload, timeout=120)
                if resp.status_code >= 400:
                    last = f"HTTP {resp.status_code}: {resp.text[:800]}"
                    continue
                data = resp.json()
                raw = extract_aia_response_text(data)
                if not raw:
                    last = "Dell AIA vision returned HTTP success but no usable assistant output"
                    continue
                parsed = extract_json_object(raw)
                if set(parsed.keys()) == {"raw"}:
                    last = "Dell AIA vision returned malformed JSON: " + mask_sensitive_string(raw)[:500]
                    continue
                return {"status": "ok", "model": model, "response_preview": mask_sensitive_string(raw)[:800], **parsed}
            except Exception as exc:
                last = str(exc)
        return {"status": "error", "pass": False, "model": model, "error": mask_sensitive_string(last or "vision judge request failed")}

    def diagnose_visual_state(
        self,
        *,
        screenshot: str,
        golden_screenshots: Sequence[str],
        expected: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return non-authoritative golden-image feedback during self-heal.

        This uses the same configured multimodal Dell AIA deployment as the
        final judge, but it is diagnostic only.  It may guide the next safe
        exploration/exploitation iteration; deterministic DOM proof and the
        final independent judge remain authoritative.
        """
        return mask_sensitive_data(
            self._vision_judge(
                screenshot=str(screenshot),
                golden_screenshots=list(golden_screenshots or []),
                expected=expected if isinstance(expected, dict) else {},
            )
        )

    async def judge_live_section(
        self,
        *,
        page: Any,
        phase: str,
        section: str,
        expected: Dict[str, Any],
        attempts: Optional[Sequence[Dict[str, Any]]],
        screenshot_path: str | Path,
        golden_screenshots: Optional[Sequence[str]] = None,
        root_selector: str = "body",
        attempt_number: int = 1,
    ) -> Dict[str, Any]:
        if not self.policy.enabled:
            return {"pass": True, "status": "disabled", "phase": phase, "section": section}
        actual = await self.capture_live_state(page, root_selector=root_selector)
        shot = Path(screenshot_path)
        shot.parent.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(shot), full_page=True)
        except Exception as exc:
            actual["screenshot_error"] = mask_sensitive_string(str(exc))

        # Independent structured evidence from Microsoft's official Playwright MCP.
        # BrowserSession attaches the backend to the same Page instance after CDP
        # connection, so this snapshot represents the exact live HIP tab.
        mcp_backend = getattr(page, "_hip_playwright_mcp_backend", None)
        mcp_snapshot: Dict[str, Any] = {"required": bool(mcp_backend), "available": False, "text": ""}
        if mcp_backend is not None:
            try:
                snap = await mcp_backend.snapshot(target=root_selector if root_selector != "body" else None, boxes=True, depth=14)
                mcp_snapshot = {
                    "required": True,
                    "available": True,
                    "text": str(snap.get("text") or "")[:50000],
                }
                try:
                    mcp_shot = await mcp_backend.screenshot(f"playwright_mcp_{shot.name}", full_page=True)
                    mcp_snapshot["screenshot"] = mcp_shot
                except Exception as shot_exc:
                    mcp_snapshot["screenshot_error"] = mask_sensitive_string(str(shot_exc))
            except Exception as exc:
                mcp_snapshot = {"required": True, "available": False, "text": "", "error": mask_sensitive_string(str(exc))}
        actual["playwright_mcp_accessibility"] = mcp_snapshot

        deterministic = self.deterministic_judge(expected=expected, actual_state=actual, attempts=attempts)
        deterministic["playwright_mcp_snapshot_required"] = bool(mcp_snapshot.get("required"))
        deterministic["playwright_mcp_snapshot_available"] = bool(mcp_snapshot.get("available"))
        if mcp_snapshot.get("required") and not mcp_snapshot.get("available"):
            deterministic["pass"] = False
            deterministic.setdefault("missing_or_wrong", []).append({
                "field": "Playwright MCP accessibility snapshot",
                "expected": "available for the current HIP section",
                "actual": mcp_snapshot.get("error") or "not available",
                "reason": "dual-MCP section judge cannot approve without Playwright MCP evidence",
            })
        text_payload = {
            "phase": phase,
            "section": section,
            "expected": expected,
            "deterministic_pass": deterministic.get("pass"),
            "deterministic_findings": deterministic,
            "actual_state": {
                "controls": actual.get("controls", [])[:120],
                "visible_text": str(actual.get("visible_text") or "")[:20000],
                "row_counts": actual.get("row_counts", {}),
                "playwright_mcp_accessibility_snapshot": str(mcp_snapshot.get("text") or "")[:20000],
            },
        }
        text = self._text_judge(text_payload) if self.policy.require_text_model else {"status": "not_required", "pass": True}
        text = _reconcile_text_judge(text, deterministic)
        vision = self._vision_judge(screenshot=str(shot), golden_screenshots=golden_screenshots or [], expected=expected) if self.policy.require_vision_model else {"status": "not_required", "pass": True}
        vision = _reconcile_vision_judge(vision, deterministic)
        overall = bool(deterministic.get("pass")) and bool(text.get("pass")) and bool(vision.get("pass"))
        if not self.policy.fail_closed:
            overall = bool(deterministic.get("pass")) and (bool(text.get("pass")) if text.get("status") == "ok" else True) and (bool(vision.get("pass")) if vision.get("status") == "ok" else True)
        return mask_sensitive_data({
            "phase": phase,
            "section": section,
            "attempt_number": attempt_number,
            "pass": overall,
            "status": "pass" if overall else "blocked",
            "policy": self.policy.__dict__,
            "expected": expected,
            "deterministic_judge": deterministic,
            "text_model_judge": text,
            "vision_model_judge": vision,
            "screenshot": str(shot),
            "actual_state": actual,
        })



    def judge_artifact_section(
        self,
        *,
        phase: str,
        section: str,
        expected_input: Dict[str, Any],
        verification: Dict[str, Any],
        screenshot: str,
        golden_screenshots: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Gate a completed phase using saved post-fill DOM evidence.

        The old implementation sent only counts and attempts to the text model,
        which produced the July 16 false-negative Data Map block.
        """
        expected = build_phase_expectation(expected_input, phase)
        actual_state = verification.get("actual_state") if isinstance(verification.get("actual_state"), dict) else {"controls": [], "visible_text": "", "row_counts": {}}
        attempts = verification.get("all_attempts") if isinstance(verification.get("all_attempts"), list) else verification.get("dummy_fill_attempts_sample", [])
        surface_gate: Dict[str, Any] = {"pass": True, "reason": "not_applicable"}
        if phase in {"source_document_type", "target_document_type", "document_type"}:
            labels = [str(c.get("label") or "") for c in actual_state.get("controls", []) if isinstance(c, dict)]
            surface_gate = classify_doctype_surface_text(str(actual_state.get("visible_text") or ""), visible_labels=labels)
        exact = self.deterministic_judge(expected=expected, actual_state=actual_state, attempts=attempts)
        if not surface_gate.get("pass"):
            exact["pass"] = False
            exact["active_surface_issue"] = {"reason": "active_surface_lost", **surface_gate}
        failed = verification.get("failed_attempts") if isinstance(verification.get("failed_attempts"), list) else []
        validation_gate = verification.get("validation_gate") if isinstance(verification.get("validation_gate"), dict) else {}
        blocking_validation = validation_gate.get("blocking") if isinstance(validation_gate.get("blocking"), list) else []
        deterministic = {
            **exact,
            "pass": bool(exact.get("pass")) and _verification_status_is_success(verification.get("status")) and bool(screenshot) and Path(str(screenshot)).is_file() and not failed and not blocking_validation,
            "verification_status": verification.get("status"),
            "failed_attempts_from_phase": failed,
            "warnings": verification.get("warnings", []),
            "screenshot_exists": bool(screenshot) and Path(str(screenshot)).is_file(),
            "counts": verification.get("counts", {}),
            "actual_state_source": actual_state.get("source"),
            "validation_gate": validation_gate,
            "object_resolution": verification.get("object_resolution", {}),
            "active_surface_gate": surface_gate,
        }
        expected["object_resolution"] = verification.get("object_resolution", {})
        expected["accepted_nonblocking_validation"] = validation_gate.get("accepted_nonblocking", [])
        payload = {
            "phase": phase,
            "section": section,
            "expected": expected,
            "actual_state": actual_state,
            "deterministic_pass": deterministic["pass"],
            "deterministic_findings": deterministic,
            "canonical_actual_bindings": deterministic.get("matched_values", []),
            "successful_attempts": [a for a in attempts if isinstance(a, dict) and (a.get("success") is True or a.get("filled") is True)][:80],
        }
        text = self._text_judge(payload) if self.policy.require_text_model else {"status": "not_required", "pass": True}
        text = _reconcile_text_judge(text, deterministic)
        vision = self._vision_judge(screenshot=screenshot, golden_screenshots=golden_screenshots or [], expected=expected) if self.policy.require_vision_model else {"status": "not_required", "pass": True}
        vision = _reconcile_vision_judge(vision, deterministic)
        overall = bool(deterministic["pass"]) and bool(text.get("pass")) and bool(vision.get("pass"))
        if not self.policy.fail_closed:
            overall = bool(deterministic["pass"]) and (bool(text.get("pass")) if text.get("status") == "ok" else True) and (bool(vision.get("pass")) if vision.get("status") == "ok" else True)
        return mask_sensitive_data({
            "phase": phase, "section": section, "pass": overall, "status": "pass" if overall else "blocked",
            "policy": self.policy.__dict__, "deterministic_judge": deterministic, "text_model_judge": text,
            "vision_model_judge": vision, "screenshot": screenshot, "golden_screenshots": list(golden_screenshots or []),
        })


def _fact(field: str, value: Any, *aliases: str, required: bool = True, section: str = "", section_aliases: Sequence[str] = (), row_kind: str = "", row_index: Any = None, match_mode: str = "exact", input_path: str = "") -> Dict[str, Any]:
    return {"field": field, "value": value, "aliases": list(aliases), "required": required, "section": section, "section_aliases": list(section_aliases), "row_kind": row_kind, "row_index": row_index, "match_mode": match_mode, "input_path": input_path}


def build_bizflow_section_expectation(input_data: Dict[str, Any], section: str) -> Dict[str, Any]:
    obj = input_data.get("objects") if isinstance(input_data.get("objects"), dict) else {}
    bf = obj.get("biz_flow") if isinstance(obj.get("biz_flow"), dict) else {}
    sec = section.lower()
    facts: List[Dict[str, Any]] = []
    row_counts: Dict[str, int] = {}
    if "basic" in sec or "flow details" in sec:
        fd = bf.get("flow_details") if isinstance(bf.get("flow_details"), dict) else {}
        facts.extend([
            _fact("business_flow_name", fd.get("business_flow_name"), "business flow name", "flow name"),
            _fact("flow_description", fd.get("flow_description"), "flow description", "description"),
        ])
    elif "source" in sec:
        src = bf.get("configure_source") if isinstance(bf.get("configure_source"), dict) else {}
        fi = bf.get("flow_identifiers") if isinstance(bf.get("flow_identifiers"), dict) else {}
        facts.extend([
            _fact("source_type", src.get("source_type"), "source type"),
            _fact("source_application", src.get("source_application"), "source application"),
            _fact("source_transport_profile", src.get("source_transport_profile"), "source transport profile"),
            _fact("source_document_type", src.get("document_type_name_version"), "document type name", "document type name version"),
            _fact("flow_identifier_operator", fi.get("operator"), "flow identifier operator"),
        ])
        rows = fi.get("conditions") if isinstance(fi.get("conditions"), list) else []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            facts.extend([
                _fact(f"flow_identifiers[{idx}].document_type", row.get("document_type_name_version"), "document type name version"),
                _fact(f"flow_identifiers[{idx}].attribute", row.get("attribute_name"), "attribute name"),
                _fact(f"flow_identifiers[{idx}].operator", row.get("operator"), "operator"),
                _fact(f"flow_identifiers[{idx}].value", row.get("value"), "value"),
            ])
    elif "target" in sec:
        tgt = bf.get("configure_targets") if isinstance(bf.get("configure_targets"), dict) else {}
        facts.extend([
            _fact("target_type", tgt.get("target_type"), "target type"),
            _fact("target_application", tgt.get("target_application"), "target application"),
            _fact("target_transport_profile", tgt.get("target_transport_profile"), "target transport profile"),
            _fact("target_document_type", tgt.get("document_type_name_version"), "document type name version", "target document type"),
        ])
        steps = bf.get("process_steps") if isinstance(bf.get("process_steps"), list) else []
        row_counts["process_steps"] = len(steps)
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            cfg = step.get("configuration") if isinstance(step.get("configuration"), dict) else {}
            facts.extend([
                _fact(f"process_steps[{idx}].type", step.get("step_type"), "step type", "process step type"),
                _fact(f"process_steps[{idx}].name", step.get("step_name"), "step name", "process step name"),
                _fact(f"process_steps[{idx}].action", cfg.get("action"), "action"),
                _fact(f"process_steps[{idx}].target_document_type", cfg.get("target_document_type_version"), "target document type"),
                _fact(f"process_steps[{idx}].rule", cfg.get("rule_version"), "rule", required=bool(cfg.get("rule_version"))),
            ])
            for pidx, part in enumerate(cfg.get("file_name_parts") if isinstance(cfg.get("file_name_parts"), list) else []):
                if isinstance(part, dict):
                    facts.extend([
                        _fact(f"process_steps[{idx}].file_name_parts[{pidx}].derived_from", part.get("derived_from"), "derived from"),
                        _fact(f"process_steps[{idx}].file_name_parts[{pidx}].value", part.get("value"), "value"),
                    ])
    elif "routing" in sec:
        routing = bf.get("configure_routing") if isinstance(bf.get("configure_routing"), dict) else {}
        rule = routing.get("rule") if isinstance(routing.get("rule"), dict) else {}
        cond = routing.get("conditions") if isinstance(routing.get("conditions"), dict) else {}
        action = routing.get("actions") if isinstance(routing.get("actions"), dict) else {}
        facts.extend([
            _fact("routing.rule.name", rule.get("name"), "name", "rule name"),
            _fact("routing.rule.version", rule.get("version"), "version"),
            _fact("routing.rule.document_type", rule.get("document_type_name_version"), "document type name version"),
            _fact("routing.rule.type", rule.get("rule_type"), "rule type"),
            _fact("routing.rule.scope", rule.get("rule_scope"), "rule scope"),
            _fact("routing.conditions.execute_when", cond.get("execute_actions_when"), "execute action s when", "execute actions when"),
        ])
        rows = cond.get("rows") if isinstance(cond.get("rows"), list) else []
        for idx, row in enumerate(rows):
            if isinstance(row, dict):
                facts.extend([
                    _fact(f"routing.conditions[{idx}].type", row.get("condition_type"), "condition type"),
                    _fact(f"routing.conditions[{idx}].operator", row.get("operator"), "operator"),
                    _fact(f"routing.conditions[{idx}].value", row.get("value"), "value"),
                    _fact(f"routing.conditions[{idx}].attribute", row.get("attribute_name") or row.get("attribute_name_unit"), "attribute name", "attribute name unit"),
                ])
        facts.extend([
            _fact("routing.action.name", action.get("name"), "actions", "name"),
            _fact("routing.action.type", action.get("type"), "type"),
            _fact("routing.action.target", action.get("target"), "target"),
        ])
    return {"section": section, "facts": [f for f in facts if f.get("value") not in {None, ""}], "row_counts": row_counts}


def _document_type_phase_expectation(phase_input: Dict[str, Any], phase: str) -> Dict[str, Any]:
    objects = phase_input.get("objects") if isinstance(phase_input.get("objects"), dict) else {}
    target = objects.get(phase) if isinstance(objects.get(phase), dict) else objects.get("document_type") if isinstance(objects.get("document_type"), dict) else {}
    facts: List[Dict[str, Any]] = []
    details = "Document Type Details"
    facts.extend([
        _fact("document_type_name", target.get("name"), "name", section=details),
        _fact("transaction_type", target.get("transaction_type"), "transaction type", section=details, required=bool(target.get("transaction_type"))),
        _fact("document_type_version", target.get("version"), "version", section=details, required=bool(target.get("version"))),
        _fact("data_format_type", target.get("data_format_type"), "data format type", section=details),
        _fact("description", target.get("description"), "description", section=details, required=bool(target.get("description"))),
    ])
    identifier = target.get("document_identifier") if isinstance(target.get("document_identifier"), dict) else {}
    facts.append(_fact("document_identifier.operation", identifier.get("operation"), "operation", section="Document Identifier"))
    id_rows = identifier.get("rows") if isinstance(identifier.get("rows"), list) else []
    for idx, row in enumerate(id_rows):
        if not isinstance(row, dict):
            continue
        facts.extend([
            _fact(f"document_identifier[{idx}].derived_from", row.get("derived_from"), "derived from", section="Document Identifier", row_kind="document_identifier", row_index=idx),
            _fact(f"document_identifier[{idx}].value", row.get("value"), "value", section="Document Identifier", row_kind="document_identifier", row_index=idx, required=bool(row.get("value"))),
        ])
    attrs = target.get("attributes_to_configure") if isinstance(target.get("attributes_to_configure"), list) else []
    for idx, row in enumerate(attrs):
        if not isinstance(row, dict):
            continue
        usage = row.get("usage")
        usage_values = [x.strip() for x in re.split(r"\s*,\s*", str(usage or "")) if x.strip()]
        facts.extend([
            _fact(f"attributes[{idx}].name", row.get("attribute_name"), "attribute name", section="Attributes To Configure", row_kind="attribute", row_index=idx),
            _fact(f"attributes[{idx}].derived_from", row.get("derived_from"), "derived from", section="Attributes To Configure", row_kind="attribute", row_index=idx),
            _fact(f"attributes[{idx}].usage", usage_values, "usage", section="Attributes To Configure", row_kind="attribute", row_index=idx, match_mode="set", required=bool(usage_values)),
            _fact(f"attributes[{idx}].expression", row.get("expression"), "expression value", "expression", section="Attributes To Configure", row_kind="attribute", row_index=idx, required=bool(row.get("expression"))),
        ])
    facts.append(_fact("validation_type", target.get("validation_type"), "validation type", section="Validation"))
    return {
        "section": phase,
        "facts": [f for f in facts if f.get("value") not in (None, "", [])],
        "row_counts": {"document_identifier_rows": len(id_rows), "attribute_rows": len(attrs)},
        "expectation_source": f"$.objects.{phase}" if isinstance(objects.get(phase), dict) else "$.objects.document_type",
        "stateful": True,
    }


def _graph_judge_field_identity(node: Dict[str, Any], phase: str) -> str:
    """Return a stable, row-aware judge field id for a state-graph node.

    The state graph's ``field_key`` is intentionally reusable across repeatable
    rows (for example ``attribute_expression``).  Judge reports and historical
    evidence need a stable identity that also carries the physical row.  Preserve
    the established Document Type names while keeping the graph as the source of
    truth for section/row/input-path/value semantics.
    """
    key = str(node.get("field_key") or node.get("node_id") or "field")
    row_kind = str(node.get("row_kind") or "")
    row_index = node.get("row_index")
    if "document_type" in str(phase or ""):
        if key == "document_identifier_operation":
            return "document_identifier.operation"
        if row_kind == "document_identifier" and row_index is not None:
            suffix = {
                "document_identifier_derived_from": "derived_from",
                "document_identifier_value": "value",
            }.get(key)
            if suffix:
                return f"document_identifier[{int(row_index)}].{suffix}"
        if row_kind == "attribute" and row_index is not None:
            suffix = {
                "attribute_name": "name",
                "attribute_derived_from": "derived_from",
                "attribute_usage": "usage",
                "attribute_expression": "expression",
            }.get(key)
            if suffix:
                return f"attributes[{int(row_index)}].{suffix}"
    return key


def _graph_legacy_aliases(node: Dict[str, Any], phase: str) -> List[str]:
    """Compatibility aliases that remain semantic and value-free."""
    key = str(node.get("field_key") or "")
    aliases: List[str] = []
    if "document_type" in str(phase or ""):
        aliases.extend({
            "attribute_name": ["attribute name"],
            "attribute_derived_from": ["derived from"],
            "attribute_usage": ["usage"],
            "attribute_expression": ["expression value", "expression"],
            "document_identifier_derived_from": ["derived from"],
            "document_identifier_value": ["value"],
            "document_identifier_operation": ["operation"],
        }.get(key, []))
    return aliases


def build_phase_expectation(phase_input: Dict[str, Any], phase: str) -> Dict[str, Any]:
    """Compile a phase-local exact judge contract from the canonical state graph.

    The old fallback flattened every string in Rule/TP/BizFlow/Data Map payloads,
    producing values with no field/section identity and causing systematic false
    section-judge blocks.  The state graph is already the executor's authoritative
    input-to-control map, so the judge now consumes that same semantic contract.
    """
    try:
        from .stateful_form_runtime import compile_phase_state_graph
        graph = compile_phase_state_graph(phase_input, phase)
    except Exception:
        graph = {}

    nodes = graph.get("nodes") if isinstance(graph, dict) and isinstance(graph.get("nodes"), list) else []
    if nodes:
        facts: List[Dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            value = node.get("expected_value")
            if value in (None, "", []):
                continue
            loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
            aliases: List[str] = []
            judge_field = _graph_judge_field_identity(node, phase)
            for candidate in [
                *_graph_legacy_aliases(node, phase),
                judge_field,
                node.get("field_key"),
                *(loc.get("labels") or []),
                *(loc.get("names") or []),
                *(loc.get("placeholders") or []),
            ]:
                text = str(candidate or "").strip()
                if text and _norm(text) not in {_norm(x) for x in aliases}:
                    aliases.append(text)
            action = str(node.get("action") or "")
            facts.append(_fact(
                judge_field,
                value,
                *aliases,
                # Every value supplied in the phase input must be proven exactly.
                # `node.required` describes portal validation, not whether the
                # user's configured value is optional to the replication mission.
                required=True,
                section=str(node.get("section") or ""),
                section_aliases=tuple(loc.get("section_aliases") or ()),
                row_kind=str(node.get("row_kind") or loc.get("row_kind") or ""),
                row_index=node.get("row_index") if node.get("row_index") is not None else loc.get("row_index"),
                match_mode="set" if action == "select_multi" else "exact",
                input_path=str(node.get("input_path") or ""),
            ))
        repeatable = graph.get("repeatable_rows") if isinstance(graph.get("repeatable_rows"), dict) else {}
        document_type_row_counts: Dict[str, int] = {}
        if "document_type" in str(phase or ""):
            document_type_row_counts = {
                "document_identifier_rows": int(repeatable.get("document_identifier") or 0),
                "attribute_rows": int(repeatable.get("attribute") or 0),
            }
        return {
            "section": phase,
            "facts": facts,
            # Document Type has stable, explicitly modeled repeatable containers and
            # keeps its historical row-count contract.  Other custom HIP phases use
            # row-scoped facts as authoritative because their outer containers can
            # be virtualized/detached from saved HTML.
            "row_counts": document_type_row_counts,
            "planned_repeatable_rows": {f"{k}_rows": int(v or 0) for k, v in repeatable.items()},
            "expectation_source": "stateful_phase_graph",
            "graph_id": graph.get("graph_id"),
            "stateful": True,
        }

    # Backward-compatible Document Type builder for old/minimal fixtures that do
    # not compile a state graph.
    if "document_type" in str(phase or ""):
        return _document_type_phase_expectation(phase_input, phase)

    # Fail closed with no invented field bindings.  Generic string flattening is
    # intentionally prohibited because it cannot prove which control a value owns.
    return {
        "section": phase, "facts": [], "row_counts": {},
        "expectation_source": "state_graph_unavailable", "stateful": False,
        "expectation_error": "No phase-local semantic state graph could be compiled.",
    }
