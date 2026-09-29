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
    ("transport_profile", r"\btransport[\s_-]*pr?o?f(?:ile|lie|il|ie)?s?\b"),
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
_ENV_NAME = r"(prod(?:uction)?|uat|dev|qa|sit|test\d*|stag(?:e|ing))"
_ENV = r"\b(?:to|in|into|on|for)\s+(?:the\s+)?" + _ENV_NAME + r"\b"
_TO_ENV = r"\b(?:to|into)\s+(?:the\s+)?" + _ENV_NAME + r"\b"
_FROM_ENV = r"\bfrom\s+(?:the\s+)?" + _ENV_NAME + r"\b"
_IN_ENV = r"\b(?:in|on|of)\s+(?:the\s+)?" + _ENV_NAME + r"\b|\b" + _ENV_NAME + r"\s+(?:version|environment|env|tab)\b"
_VERSION = r"\bversion\s+(\d+(?:\.\d+)?)\b|\bv(\d+(?:\.\d+)?)\b"
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


def _canonical_env(word: str) -> str:
    word = str(word or "").lower()
    return _ENV_CANONICAL.get(word, word.upper())


def _environment(task: str, pattern: str = _ENV) -> str:
    m = re.search(pattern, str(task or ""), re.I)
    if not m:
        return ""
    return _canonical_env(next(g for g in m.groups() if g))


def _panel(task: str, operation: str) -> Dict[str, str]:
    """V243R24: which environment tab and version of the object the action applies to
    (the expanded row's DEV / TEST1 / TEST2 / PROD tabs and Version)."""
    text = str(task or "")
    panel: Dict[str, str] = {}
    source = _environment(text, _FROM_ENV)
    if not source and operation not in {"deploy", "migrate"}:
        source = _environment(text, _IN_ENV)
    if source:
        panel["environment"] = source
    version = re.search(_VERSION, text, re.I)
    if version:
        panel["version"] = next(g for g in version.groups() if g)
    return panel


# V243R30: "open the Transport Profile, expand it, click Edit and capture all the
# values" -- read the Edit form, change nothing, remember the Edit section.
_LEARN_EDIT = r"\b(?:captur\w*|learn\w*|read\w*|view\w*|show\w*|record\w*|remember\w*|get|know\w*|inspect\w*|explor\w*)\b"
# "save it so that it already has knowledge of the Edit section" saves the
# knowledge, not the object: only a change verb turns the request into an Edit.
_CHANGE = r"\b(?:chang\w*|set|sets|setting|updat\w*|modif\w*|replac\w*|renam\w*)\b"
_EVERY_PHASE = r"\b(?:all|every|each)\s+(?:the\s+|of\s+the\s+)?(?:phases?|pashes?|phses?|objects?|forms?|sections?|modules?|pages?)\b"


def _phases_in_order(text: str) -> List[str]:
    """Object families in the order the request names them."""
    hits = []
    for family, pattern in _FAMILIES:
        m = re.search(pattern, str(text or ""), re.I)
        if m:
            hits.append((m.start(), family))
    return [family for _, family in sorted(hits)]


def _phase_for_family(text: str, family: str) -> str:
    if family in {"document_type", "transport_profile"}:
        return f"target_{family}" if re.search(r"\btarget\b", str(text or ""), re.I) else f"source_{family}"
    return family


def _learn_edit_specs(text: str) -> List[Dict[str, Any]]:
    if not (re.search(r"\bedit\b", text, re.I) and re.search(_LEARN_EDIT, text, re.I) and not re.search(_CHANGE, text, re.I)):
        return []
    from .edit_section_learning import ALL_PHASES

    phase = _phase(text)
    named = _phases_in_order(text)
    if re.search(_EVERY_PHASE, text, re.I):
        # "the Transport Profile ... same for the BizFlow and all the phases": the
        # named ones first, in the order asked, then every other phase.
        phases = [p for fam in named for p in ALL_PHASES if p == fam or p.endswith(fam)]
        phases += [p for p in ALL_PHASES if p not in phases]
    elif len(named) > 1:
        phases = [p for fam in named for p in ALL_PHASES if p.endswith(fam) and (p == _phase_for_family(text, fam))]
    elif phase:
        phases = [phase]
        if phase.endswith("transport_profile") and not re.search(r"\b(?:source|target)\b", text, re.I):
            phases = ["source_transport_profile"]
    else:
        return []
    target = _target(text) if len(phases) == 1 else ""
    specs = []
    for p in phases:
        spec: Dict[str, Any] = {"phase": p, "operation": "learn_edit", "source": "task_box", "commit": False}
        if target:
            spec["target"] = target
        panel = _panel(text, "learn_edit")
        if panel:
            spec["panel"] = panel
        specs.append(spec)
    return specs


def task_operation_specs(task: str, input_data: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    """Operation specs for a free-text request, or ``[]`` when it is not one."""
    text = str(task or "")
    learn = _learn_edit_specs(text)
    if learn:
        return learn
    phase = _phase(text)
    if not phase:
        return []
    # V243R30: a verb right after "to" / "as" / "=" is a value ("set Post Transfer
    # Action to Delete"), not a second operation.
    found = sorted(
        (m.start(), op) for op, pattern in _VERBS
        for m in [next((x for x in re.finditer(pattern, text, re.I) if not re.search(r"(?:\bto|\bas|[=:])\s*['\"]?$", text[:x.start()], re.I)), None)]
        if m)
    operations = list(dict.fromkeys(op for _, op in found))
    save = bool(re.search(_SAVE, text, re.I))
    target = _target(text)
    if not operations and save and re.search(_FILL, text, re.I):
        # "fill the document type from input.json and save it"
        operations = ["edit" if target else "create"]
    if not operations:
        return []
    env = _environment(text, _TO_ENV) or _environment(text)
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
        if operation in {"deploy", "migrate"} and env and env != _environment(text, _FROM_ENV):
            spec["values"] = {"target_environment": env}
        panel = _panel(text, operation)
        if panel and operation != "create":
            spec["panel"] = panel
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
        if operation == "learn_edit":
            steps += [
                {"type": "search", "target": spec.get("target") or f"objects.{phase} name, else the first row", "risk": "read"},
                {"type": "open_row_action", "click": ["Expand the row", "Edit"], "exact_row_match": True, "risk": "read"},
                {"type": "capture", "what": "every field and value of the Edit form: every tab, collapsed section and row", "risk": "read"},
                {"type": "close", "click": ["Cancel", "Close", "Back"], "saves": False, "risk": "read"},
                {"type": "remember", "what": f"edit_sections/{phase}.json (structure, value-free); values -> run folder edit_values.json + edit_input.json", "risk": "read"},
            ]
            continue
        if operation == "create":
            steps.append({"type": "open", "click": list(ADD_LABELS), "risk": "read"})
        else:
            steps.append({"type": "search", "target": spec.get("target") or f"objects.{phase} name", "risk": "read"})
            panel = spec.get("panel") or {}
            steps.append({"type": "open_row_action", "click": list(OPENER_LABELS.get(operation) or (operation.title(),)),
                          # V243R24: Document Types keep Edit / Clone / Migrate in the row's
                          # expanded details, behind its chevron.
                          "fallbacks": ["row expander: expanded details" + (f" ({', '.join(f'{k} {v}' for k, v in panel.items())})" if panel else ""),
                                        "More actions menu", "semantic affordance resolver"],
                          "exact_row_match": True, "ambiguous_or_missing_row": "NEEDS_INPUT",
                          "risk": "mutation" if operation not in _FORM_OPERATIONS else "read"})
        if operation in {"deploy", "migrate"}:
            steps.append({"type": "choose_target", "form": form_phase_for(phase, operation, spec),
                          "values": spec.get("values") or f"objects.{phase}_{operation}",
                          "checks": ["offered by the portal, else NEEDS_INPUT", "already holds the version: EXISTING, nothing clicked"],
                          "engine": "action menu choice, or the action's dialog (certified skill)", "risk": "draft"})
        else:
            steps.append({"type": "fill", "form": form_phase_for(phase, operation, spec), "engine": "certified skill (learn, prove by replay, then replay)",
                          "values": spec.get("values") or f"objects.{phase}", "risk": "draft",
                          **({"edit_safety": "snapshot first; change only requested fields; not saved if another field changed"} if operation in {"edit", "clone"} else {})})
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
