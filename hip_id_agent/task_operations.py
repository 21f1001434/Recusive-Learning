"""Turn a free-text portal request into portal operations (V243R23).

"Deploy the document type XML_DellAutoASN_10_U-HAUL_ANS_IB to PROD" used to be
planned as: open the Partner page, look for a page-level "Deploy" button -- the
agent did not know that a document type lives on the Document Types listing,
that its Deploy is an action on that object's row (directly or in its "More
actions" menu), or that "to PROD" is a value for the Deploy dialog.

Requests that name a HIP object (document type, data map, rule, transport
profile, business flow) and an object action are now turned into the same
operation specs ``input.json`` "operations" use, and run by
:class:`~hip_id_agent.portal_operations.PortalOperationRunner`:

1. open the object's listing, search for it, open the row action (directly,
   from its "More actions" menu, or through the semantic resolver);
2. fill the form or the action's own dialog with the certified-skill engine
   (learned once, proved by a replay, then replayed);
3. commit -- Save / Submit / Deploy / Migrate ... -- only through the
   three-part mutation gate, clicked once, reconciled, and verified in the
   listing.

A plain "fill ..." request is not an operation and keeps its existing path.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

from .portal_operations import ADD_LABELS, OPENER_LABELS, form_phase_for
from .portal_skills import COMMIT_LABELS

_FAMILIES = (
    ("biz_flow", r"\bbiz\s*flows?\b|\bbizflows?\b|\bbusiness\s+flows?\b"),
    ("document_type", r"\bdoc(?:ument)?[\s_-]*types?\b|\bdoctypes?\b"),
    ("transport_profile", r"\btransport[\s_-]*profiles?\b"),
    ("data_map", r"\bdata[\s_-]*maps?\b|\bdatamaps?\b"),
    ("rule", r"\brules?\b"),
)
_VERBS = (
    ("deploy", r"\bdeploy(?:s|ed|ing|ment)?\b"),
    ("migrate", r"\b(?:migrat(?:e|es|ed|ing|ion)|promot(?:e|es|ed|ing|ion))\b"),
    ("clone", r"\b(?:clon(?:e|es|ed|ing)|cop(?:y|ies|ied)|duplicat(?:e|es|ed|ing))\b"),
    ("merge", r"\bmerg(?:e|es|ed|ing)\b"),
    ("validate", r"\bvalidat(?:e|es|ed|ing|ion)\b"),
    ("delete", r"\b(?:delet(?:e|es|ed|ing)|remov(?:e|es|ed|ing))\b"),
    ("edit", r"\b(?:edit(?:s|ed|ing)?|updat(?:e|es|ed|ing)|modif(?:y|ies|ied))\b"),
    ("create", r"\b(?:creat(?:e|es|ed|ing)|add|new)\b"),
)
_SAVE = r"\b(?:save[sd]?|saving|submit(?:s|ted)?|commit)\b"
_FILL = r"\bfill(?:s|ed|ing)?\b"
_ENV = r"\b(?:to|in|into|on|for)\s+(?:the\s+)?(prod(?:uction)?|uat|dev|qa|sit|test|stag(?:e|ing))\b"
_FORM_OPERATIONS = {"create", "edit", "clone"}
_ENV_CANONICAL = {"production": "PROD", "staging": "STAGE"}


def _phase(task: str) -> str:
    text = str(task or "").lower()
    for family, pattern in _FAMILIES:
        if re.search(pattern, text):
            if family in {"document_type", "transport_profile"}:
                return f"target_{family}" if re.search(r"\btarget\b", text) else f"source_{family}"
            return family
    return ""


def _target(task: str) -> str:
    text = str(task or "")
    quoted = re.search(r'"([^"\n]{2,})"|\'([^\'\n]{2,})\'', text)
    if quoted:
        return (quoted.group(1) or quoted.group(2)).strip()
    named = re.search(r"\b(?:named|called)\s+([^\s,;]+(?:\s*\([^)]*\))?)", text, re.I)
    if named:
        return named.group(1).strip(" .")
    # Portal object names: XML_DellAutoASN_10_U-HAUL_ANS_IB, TP_ALPHA, SFTP_..._IB(1.0)
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.\-]*[_\-][A-Za-z0-9_.\-]*(?:\(\d+(?:\.\d+)*\))?", text)
    tokens = [t.strip(".") for t in tokens if not re.search(r"\.json$|^input\b|^https?", t, re.I) and re.search(r"[A-Za-z]", t)]
    return max(tokens, key=len) if tokens else ""


def _environment(task: str) -> str:
    m = re.search(_ENV, str(task or ""), re.I)
    if not m:
        return ""
    word = m.group(1).lower()
    return _ENV_CANONICAL.get(word, word.upper() if word != "prod" else "PROD")


def task_operation_specs(task: str, input_data: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    """Operation specs for a free-text request, or ``[]`` when it is not one."""
    text = str(task or "")
    phase = _phase(text)
    if not phase:
        return []
    found = sorted((m.start(), op) for op, pattern in _VERBS for m in [re.search(pattern, text, re.I)] if m)
    operations = list(dict.fromkeys(op for _, op in found))
    save = bool(re.search(_SAVE, text, re.I))
    target = _target(text)
    if not operations and save and re.search(_FILL, text, re.I):
        # "fill the document type from input.json and save it"
        operations = ["edit" if target else "create"]
    if not operations:
        return []
    env = _environment(text)
    merge_into = ""
    into = re.search(r"(\S+)\s+into\s+(?:the\s+)?(\S+)", text, re.I)
    if "merge" in operations and into:
        # "merge U-HAUL_POASN into U-HAUL_POASN_V2": the row is the first name,
        # the other one is a value of the Merge dialog.
        target, merge_into = into.group(1).strip(" .'\""), into.group(2).strip(" .'\"")
    specs: List[Dict[str, Any]] = []
    for operation in operations:
        spec: Dict[str, Any] = {"phase": phase, "operation": operation, "source": "task_box"}
        spec["commit"] = save if operation in _FORM_OPERATIONS else True
        if operation != "create" and target:
            spec["target"] = target
        if operation in {"deploy", "migrate"} and env:
            spec["values"] = {"target_environment": env}
        if operation == "merge" and merge_into:
            spec["values"] = {"merge_into": merge_into}
        specs.append(spec)
    return specs


def plan_task_operations(task: str, input_data: Optional[Mapping[str, Any]] = None, *, listing_urls: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """A readable plan (where the agent goes and what it clicks) for the Control Center."""
    specs = task_operation_specs(task, input_data)
    if not specs:
        return {"matched": False}
    if listing_urls is None:
        from .dummy_fill_e2e import PHASE_URLS

        listing_urls = PHASE_URLS
    steps: List[Dict[str, Any]] = []
    for spec in specs:
        phase, operation = spec["phase"], spec["operation"]
        url = str(listing_urls.get(phase) or "")
        steps.append({"type": "navigate", "target": url, "risk": "read", "phase": phase})
        if operation == "create":
            steps.append({"type": "open", "click": list(ADD_LABELS), "risk": "read"})
        else:
            steps.append({"type": "search", "target": spec.get("target") or f"objects.{phase} name", "risk": "read"})
            steps.append({"type": "open_row_action", "click": list(OPENER_LABELS.get(operation) or (operation.title(),)),
                          "fallbacks": ["More actions menu", "semantic affordance resolver"],
                          "risk": "mutation" if operation not in _FORM_OPERATIONS else "read"})
        steps.append({"type": "fill", "form": form_phase_for(phase, operation, spec), "engine": "certified skill (learn, prove by replay, then replay)",
                      "values": spec.get("values") or f"objects.{phase}", "risk": "draft"})
        if spec.get("commit"):
            steps.append({"type": "commit", "click": list(COMMIT_LABELS.get(operation) or ("Save",)),
                          "gate": "--allow-portal-mutation + HIP_ALLOW_PORTAL_MUTATION=YES + 'ALLOW HIP MUTATION'", "risk": "mutation"})
            steps.append({"type": "verify_listing", "target": spec.get("target") or f"objects.{phase} name", "risk": "read"})
    return {
        "matched": True, "pass": True, "execution_mode": "portal_operation", "task": task,
        "operations": specs, "steps": steps,
        "mutation_required": any(s.get("risk") == "mutation" for s in steps),
        "values_stored": False,
    }
