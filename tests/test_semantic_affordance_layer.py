from __future__ import annotations

from hip_id_agent.semantic_affordance import canonical_intent, rank_affordance_candidate, status


def test_bare_plus_is_add_only_when_section_context_matches():
    scoped = rank_affordance_candidate(
        {"text": "+", "context": "Rule Conditions Attribute Name Operator Value", "visible": True, "enabled": True, "width": 28, "height": 28},
        intent="add_row", aliases=["rule conditions", "condition"]
    )
    assert scoped["accepted"] is True
    assert "scoped-plus" in scoped["evidence"]

    global_plus = rank_affordance_candidate(
        {"text": "+", "context": "HIP Configuration dashboard toolbar", "visible": True, "enabled": True, "width": 28, "height": 28},
        intent="add_row", aliases=["rule conditions", "condition"]
    )
    assert global_plus["accepted"] is False


def test_icon_only_expand_and_overflow_are_understood():
    expand = rank_affordance_candidate(
        {"text": "", "aria_expanded": "false", "icon_signature": "dds-icon chevron-down", "context": "UHAL Translation row", "visible": True, "enabled": True, "width": 24, "height": 24},
        intent="expand"
    )
    assert expand["accepted"] is True
    assert expand["score"] >= 100

    more = rank_affordance_candidate(
        {"text": "", "icon_signature": "more_vert kebab icon", "context": "UHAL Translation Actions", "visible": True, "enabled": True, "width": 24, "height": 24},
        intent="more_actions"
    )
    assert more["accepted"] is True


def test_edit_clone_migrate_deploy_semantics_and_governance():
    edit = rank_affordance_candidate(
        {"text": "", "icon_signature": "pencil edit", "context": "Data Map Details", "visible": True, "enabled": True, "width": 24, "height": 24},
        intent="edit"
    )
    assert edit["accepted"] is True

    for intent, icon in [("clone", "content-copy"), ("migrate", "transfer migrate"), ("deploy", "rocket deploy")]:
        blocked = rank_affordance_candidate(
            {"text": "", "icon_signature": icon, "context": "row actions", "visible": True, "enabled": True, "width": 24, "height": 24},
            intent=intent, allow_mutation=False
        )
        assert blocked["accepted"] is False
        allowed = rank_affordance_candidate(
            {"text": "", "icon_signature": icon, "context": "row actions", "visible": True, "enabled": True, "width": 24, "height": 24},
            intent=intent, allow_mutation=True
        )
        assert allowed["accepted"] is True


def test_intent_aliases_and_status_cover_general_portal_options():
    assert canonical_intent("plus") == "add_row"
    assert canonical_intent("open menu") == "more_actions"
    assert canonical_intent("open_details") == "expand"
    runtime = status()
    assert runtime["icon_only_controls"] is True
    assert runtime["effect_verification_required"] is True
    assert {"add_row", "expand", "more_actions", "edit", "clone", "migrate", "deploy"}.issubset(set(runtime["intents"]))
