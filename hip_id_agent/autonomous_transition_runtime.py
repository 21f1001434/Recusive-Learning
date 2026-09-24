from __future__ import annotations

"""Operational semantic transition planning for HIP Portal.

Stage 4 turns the value-free website world model into an execution prior.  The
planner never emits durable selectors/coordinates and never authorizes a browser
mutation by itself.  It proposes a semantic transition or a portal-owned dropdown
choice; the caller must rediscover the current target and pass normal governance,
semantic-action and post-effect verification gates before execution.
"""

import json
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .capability_graph import classify_risk
from .security import mask_sensitive_data
from .semantic_affordance import canonical_intent
from .website_world_model import WebsiteWorldModelMemory, scrub_world_model_payload


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _tokens(value: Any) -> set[str]:
    return {x for x in _norm(value).split() if x}


def _semantic_similarity(a: Any, b: Any) -> float:
    aa, bb = _norm(a), _norm(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        # A containment match is useful but cannot beat an exact option.
        return 0.88
    ta, tb = _tokens(aa), _tokens(bb)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    containment = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return round(0.62 * jaccard + 0.38 * containment, 4)


ACTION_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "open_add_form": ("add", "new", "create new", "open add form"),
    "create": ("create", "submit"),
    "edit": ("edit", "modify"),
    "save": ("save", "update", "submit"),
    "validate": ("validate", "validation", "check", "verify"),
    "clone": ("clone", "copy", "duplicate"),
    "migrate": ("migrate", "migration", "move"),
    "deploy": ("deploy", "deployment"),
    "add_row": ("add row", "add condition", "add action", "add attribute", "add step", "add process step"),
    "next": ("next", "continue"),
    "back": ("back", "previous"),
    "expand": ("expand", "details", "show"),
    "more_actions": ("more", "actions", "options", "menu"),
}


@dataclass
class DynamicOptionDecision:
    pass_: bool
    status: str
    selected_option: str = ""
    confidence: float = 0.0
    reason: str = ""
    source: str = ""
    candidates: Optional[List[Dict[str, Any]]] = None
    requires_live_reproof: bool = True

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["pass"] = data.pop("pass_")
        return mask_sensitive_data(data)


def _live_option_rows(options: Iterable[Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in options or []:
        if isinstance(raw, Mapping):
            text = str(raw.get("text") or raw.get("label") or raw.get("value") or "").strip()
            visible = bool(raw.get("visible", True))
            disabled = bool(raw.get("disabled", False))
        else:
            text = str(raw or "").strip()
            visible, disabled = True, False
        key = _norm(text)
        if not text or not key or key in seen or disabled or not visible:
            continue
        seen.add(key)
        rows.append({"text": text, "normalized": key})
    return rows


def choose_dynamic_portal_option(
    *,
    phase: str,
    label: str,
    section: str = "",
    live_options: Iterable[Any],
    desired_value: Any = "",
    mission_context: Any = "",
    world_model: Optional[WebsiteWorldModelMemory] = None,
    allow_validated_memory_fallback: bool = True,
    min_confidence: float = 0.86,
    min_margin: float = 0.10,
) -> Dict[str, Any]:
    """Choose one *currently mounted* portal option without guessing.

    Priority is explicit desired value -> unique mission-context mention -> one
    validated learned branch that is still present.  Memory cannot select an
    option that is absent from the current DOM/listbox.
    """
    rows = _live_option_rows(live_options)
    if not rows:
        return DynamicOptionDecision(False, "needs_live_options", reason="no enabled visible portal options are mounted").as_dict()

    candidates: List[Dict[str, Any]] = []
    desired = str(desired_value or "").strip()
    if desired:
        for row in rows:
            score = _semantic_similarity(desired, row["text"])
            candidates.append({"option": row["text"], "score": score, "source": "mission_expected_value"})
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[0]
        second = candidates[1]["score"] if len(candidates) > 1 else 0.0
        if top["score"] >= min_confidence and (top["score"] - second >= min_margin or top["score"] >= 0.999):
            return DynamicOptionDecision(
                True, "resolved", selected_option=top["option"], confidence=float(top["score"]),
                reason="current portal option uniquely matches the mission value",
                source="mission_expected_value", candidates=candidates[:12],
            ).as_dict()
        return DynamicOptionDecision(
            False, "ambiguous_or_missing_option", confidence=float(top["score"]),
            reason="mission value did not map uniquely to one current portal option",
            source="mission_expected_value", candidates=candidates[:12],
        ).as_dict()

    # No explicit field value: permit only an exact/unique live option mention in
    # the structured mission context.  This is useful when a new dropdown field is
    # introduced but the requested branch name already exists elsewhere in input.
    try:
        context_text = json.dumps(mission_context, ensure_ascii=False, default=str) if not isinstance(mission_context, str) else mission_context
    except Exception:
        context_text = str(mission_context or "")
    context_norm = f" {_norm(context_text)} "
    mentioned = []
    for row in rows:
        opt = row["normalized"]
        if opt and re.search(rf"(?:^|\s){re.escape(opt)}(?:\s|$)", context_norm):
            mentioned.append(row["text"])
    if len(mentioned) == 1:
        return DynamicOptionDecision(
            True, "resolved", selected_option=mentioned[0], confidence=0.94,
            reason="exactly one currently mounted portal option is explicitly present in the mission context",
            source="mission_context_exact_option", candidates=[{"option": x, "score": 0.94 if x == mentioned[0] else 0.0} for x in mentioned],
        ).as_dict()
    if len(mentioned) > 1:
        return DynamicOptionDecision(
            False, "ambiguous_mission_context", reason="multiple current portal options are mentioned by the mission; field-specific value is required",
            source="mission_context_exact_option", candidates=[{"option": x, "score": 0.94} for x in mentioned],
        ).as_dict()

    # Finally allow a validated world-model branch only when it is unique and
    # still exists in the current list.  Candidate memory alone never auto-selects.
    memory_rows: List[Dict[str, Any]] = []
    if allow_validated_memory_fallback and world_model is not None:
        try:
            hints = world_model.portal_choice_hints(phase=phase, label=label, section=section, limit=30)
        except Exception:
            hints = []
        live_map = {_norm(x["text"]): x["text"] for x in rows}
        for hint in hints:
            if not isinstance(hint, Mapping) or hint.get("trust") != "validated":
                continue
            choice = str(hint.get("portal_choice") or "")
            key = _norm(choice)
            if key in live_map:
                memory_rows.append({
                    "option": live_map[key],
                    "score": float(hint.get("confidence") or 0.0),
                    "success_count": int(hint.get("success_count") or 0),
                    "source": "validated_world_model_branch",
                })
    memory_rows.sort(key=lambda x: (x["score"], x["success_count"]), reverse=True)
    if memory_rows:
        top = memory_rows[0]
        competing = [r for r in memory_rows[1:] if r["option"] != top["option"] and r["score"] >= max(0.80, top["score"] - min_margin)]
        if top["score"] >= max(0.72, min_confidence - 0.14) and not competing:
            return DynamicOptionDecision(
                True, "resolved", selected_option=top["option"], confidence=float(top["score"]),
                reason="one validated semantic branch is still present in the current portal option list",
                source="validated_world_model_branch", candidates=memory_rows[:12],
            ).as_dict()

    return DynamicOptionDecision(
        False, "needs_input", reason="no explicit mission value or unique validated live branch justifies an automatic selection",
        source="fail_closed", candidates=(memory_rows or [{"option": r["text"], "score": 0.0} for r in rows[:12]]),
    ).as_dict()


def _action_score(action: str, label: str, affordances: Sequence[str], goal_text: str) -> float:
    canonical = canonical_intent(action)
    label_n = _norm(label)
    score = 0.0
    if canonical in {canonical_intent(a) for a in affordances or []}:
        score += 0.48
    synonyms = ACTION_SYNONYMS.get(canonical, (canonical,))
    if any(_norm(s) == label_n for s in synonyms if _norm(s)):
        score += 0.44
    elif any(_norm(s) and (_norm(s) in label_n or label_n in _norm(s)) for s in synonyms if label_n):
        score += 0.28
    if any(_norm(s) in _norm(goal_text) for s in synonyms):
        score += 0.08
    return min(1.0, score)


class AutonomousPortalTransitionPlanner:
    """Choose the next semantic website transition from live evidence + memory."""

    def __init__(self, *, world_model: Optional[WebsiteWorldModelMemory] = None) -> None:
        self.world_model = world_model

    def plan_next(
        self,
        *,
        phase: str,
        goal_action: str,
        website_model: Mapping[str, Any],
        goal_context: Any = "",
        entity: str = "",
    ) -> Dict[str, Any]:
        canonical = canonical_intent(goal_action)
        actions = [x for x in (website_model.get("action_catalog") or []) if isinstance(x, Mapping)]
        ranked: List[Dict[str, Any]] = []
        goal_text = str(goal_context or "")
        for row in actions:
            score = _action_score(canonical, str(row.get("label") or ""), list(row.get("affordances") or []), goal_text)
            if entity and entity.lower() in str(row.get("section") or "").lower():
                score += 0.04
            memory_hint = {"matched": False, "score": 0.0}
            if self.world_model is not None:
                pseudo = {
                    "label": row.get("label"), "section": row.get("section"), "role": row.get("role"),
                    "type": row.get("role"), "required": row.get("required"), "inActiveSurface": True,
                }
                try:
                    memory_hint = self.world_model.control_hint(
                        phase=phase, action=canonical, label=str(row.get("label") or ""),
                        section=str(row.get("section") or ""), candidate=pseudo,
                    )
                    # Bounded prior only; live semantics dominate.
                    score += max(-0.12, min(0.12, float(memory_hint.get("score") or 0.0) * 0.12))
                except Exception:
                    pass
            ranked.append({
                "semantic_control_key": row.get("semantic_control_key"),
                "label": row.get("label"), "section": row.get("section"), "role": row.get("role"),
                "affordances": list(row.get("affordances") or []),
                "score": round(score, 4), "world_model": memory_hint,
            })
        ranked.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
        top = ranked[0] if ranked else None
        second = float(ranked[1].get("score") or 0.0) if len(ranked) > 1 else 0.0

        memory_recommendations: List[Dict[str, Any]] = []
        if self.world_model is not None:
            try:
                memory_recommendations = self.world_model.recommend_actions(
                    phase=phase, current_state=website_model, limit=12,
                )
            except Exception:
                memory_recommendations = []

        resolved = bool(top and float(top.get("score") or 0.0) >= 0.72 and (float(top.get("score") or 0.0) - second >= 0.08 or float(top.get("score") or 0.0) >= 0.96))
        risk = classify_risk(canonical)
        return mask_sensitive_data({
            "schema_version": "hip.autonomous-transition-plan.v1",
            "phase": phase,
            "goal_action": canonical,
            "status": "resolved" if resolved else "needs_live_reproof_or_exploration",
            "pass": resolved,
            "selected": top if resolved else None,
            "candidate_ranking": ranked[:20],
            "world_model_recommendations": memory_recommendations,
            "risk": risk,
            "mutation": risk == "mutation",
            "requires_live_reproof": True,
            "requires_governance_for_mutation": risk == "mutation",
            "selectors_emitted": False,
            "coordinates_emitted": False,
            "memory_is_advisory": True,
        })


def operational_world_model_contract() -> Dict[str, Any]:
    return {
        "schema_version": "hip.stage4-operational-world-model.v1",
        "supported_goal_actions": sorted(ACTION_SYNONYMS),
        "dynamic_dropdown_policy": [
            "explicit mission value maps to one current mounted option",
            "otherwise one exact current option may be inferred from structured mission context",
            "otherwise one validated learned branch may be reused only if still present live",
            "ambiguous/no evidence -> NEEDS_INPUT",
        ],
        "memory_proposes_live_evidence_authorizes": True,
        "mutation_governance_preserved": True,
        "customer_values_persisted": False,
        "selectors_persisted": False,
        "coordinates_persisted": False,
    }
