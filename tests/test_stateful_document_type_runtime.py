from pathlib import Path

from hip_id_agent.stateful_form_runtime import compile_document_type_state_graph, split_multi_value
from hip_id_agent.form_knowledge_plan import compile_phase_plan
from hip_id_agent.section_judge import DualModelSectionJudge, build_phase_expectation
from hip_id_agent.dummy_fill_e2e import _artifact_actual_state


def payload():
    return {
        "objects": {
            "source_document_type": {
                "name": "XML_DellAutoASN_10_U-HAUL_ANS_IB",
                "transaction_type": "856",
                "version": "1",
                "data_format_type": "XML",
                "status": "Enable",
                "description": "source document",
                "validation_type": "Structure",
                "document_identifier": {
                    "operation": "All conditions are satisfied",
                    "rows": [{"derived_from": "TRANSACTION_ROOT_ELEMENT", "value": "DellAutoASN"}],
                },
                "attributes_to_configure": [
                    {
                        "attribute_name": "Receiver",
                        "derived_from": "ELEMENT_IN_PAYLOAD",
                        "usage": "Flow Identifier Expression, Logging, Mapping, Routing",
                        "expression": "/DellAutoASN/Header/To/PartnerIdentifier",
                    },
                    {
                        "attribute_name": "Transaction Type",
                        "derived_from": "FILENAME",
                        "usage": "Logging, Mapping",
                        "expression": "",
                    },
                ],
            },
            "rule": {"name": "WRONG_RULE_NAME", "description": "must never leak"},
            "biz_flow": {"name": "WRONG_BIZFLOW_NAME"},
        }
    }


def test_graph_is_phase_local_and_contains_conditional_children():
    graph = compile_document_type_state_graph(payload(), "source_document_type")
    nodes = graph["nodes"]
    assert nodes
    assert all(str(n["input_path"]).startswith("$.objects.source_document_type") for n in nodes)
    assert not any("WRONG_RULE_NAME" in str(n) or "WRONG_BIZFLOW_NAME" in str(n) for n in nodes)
    assert any(n["field_key"] == "document_identifier_value" and n["expected_value"] == "DellAutoASN" for n in nodes)
    assert any(n["field_key"] == "attribute_expression" and n["row_index"] == 0 for n in nodes)
    usage = next(n for n in nodes if n["field_key"] == "attribute_usage" and n["row_index"] == 0)
    assert usage["action"] == "select_multi"
    assert usage["expected_value"] == ["Flow Identifier Expression", "Logging", "Mapping", "Routing"]
    expression = next(n for n in nodes if n["field_key"] == "attribute_expression" and n["row_index"] == 0)
    derived = next(n for n in nodes if n["field_key"] == "attribute_derived_from" and n["row_index"] == 0)
    assert derived["node_id"] in expression["depends_on"]


def test_deterministic_plan_uses_state_graph_values_not_fuzzy_other_objects():
    learned = {
        "verification_status": "pass",
        "field_steps": [
            {"key": "name", "label": "Name", "selector": "input#stale", "value": "WRONG_RULE_NAME"},
            {"key": "attribute_usage", "label": "Usage", "selector": "input#stale-usage"},
        ],
    }
    plan = compile_phase_plan(
        phase="source_document_type", payload=payload(), learned_blueprint=learned, blueprint_path=Path("old.json")
    )
    values = [a.get("expected_value") for a in plan["actions"]]
    assert "XML_DellAutoASN_10_U-HAUL_ANS_IB" in values
    assert "WRONG_RULE_NAME" not in values
    assert plan["state_graph"]["strategy"] == "target-branch-first-then-safe-exploration"
    assert any(a.get("operation") == "select_multi" and a.get("row_index") == 0 for a in plan["actions"])


def test_expectation_is_row_scoped_and_exact():
    expected = build_phase_expectation(payload(), "source_document_type")
    assert expected["row_counts"] == {"document_identifier_rows": 1, "attribute_rows": 2}
    expr = next(f for f in expected["facts"] if f["field"] == "attributes[0].expression")
    assert expr["row_kind"] == "attribute" and expr["row_index"] == 0
    usage = next(f for f in expected["facts"] if f["field"] == "attributes[0].usage")
    assert usage["match_mode"] == "set"


def test_artifact_multi_select_ignores_synthetic_lock_and_reads_selected_options(tmp_path: Path):
    d = tmp_path / "dom_snapshots"
    d.mkdir()
    (d / "source_document_type_after_dummy_fill_no_save.html").write_text(
        '''<html><body><fieldset><legend>Attributes To Configure</legend>
        <div class="dds__d-flex dds__justify-content-start"><dds-dropdown selection="multiple">
        <input id="usage" name="usage" placeholder="Usage" data-hip-locked-value="Flow Identifier Expression, Logging, Mapping, Routing">
        <div role="option" aria-selected="true">Flow Identifier Expression</div>
        <div role="option" aria-selected="true">Logging</div>
        <div role="option" aria-selected="true">Mapping</div>
        <div role="option" aria-selected="true">Routing</div>
        </dds-dropdown></div></fieldset></body></html>''',
        encoding="utf-8",
    )
    state = _artifact_actual_state(tmp_path, [])
    usage = next(c for c in state["controls"] if c["selection_mode"] == "multiple")
    assert usage["value"] == ["Flow Identifier Expression", "Logging", "Mapping", "Routing"]
    assert usage["row_kind"] == "attribute" and usage["row_index"] == 0


def test_judge_requires_same_repeatable_row_and_selected_set():
    judge = DualModelSectionJudge()
    expected = build_phase_expectation(payload(), "source_document_type")
    actual = {
        "controls": [],
        "visible_text": "",
        "row_counts": {"document_identifier_rows": 1, "attribute_rows": 2},
    }
    for fact in expected["facts"]:
        actual["controls"].append({
            "label": (fact.get("aliases") or [fact["field"]])[0],
            "value": fact["value"],
            "selected_values": fact["value"] if fact.get("match_mode") == "set" else [],
            "section": fact.get("section"),
            "row_kind": fact.get("row_kind"),
            "row_index": fact.get("row_index"),
            "evidence": "test",
        })
    result = judge.deterministic_judge(expected=expected, actual_state=actual, attempts=[])
    assert result["pass"] is True
    # Moving row 0 expression to row 1 must fail even though the same text exists.
    expression = next(c for c in actual["controls"] if c["label"] == "expression value")
    expression["row_index"] = 1
    result = judge.deterministic_judge(expected=expected, actual_state=actual, attempts=[])
    assert result["pass"] is False
    assert any(x["field"] == "attributes[0].expression" for x in result["missing_values"])


def test_split_multi_value_preserves_individual_dds_options():
    assert split_multi_value("Flow Identifier Expression, Logging, Mapping, Routing") == [
        "Flow Identifier Expression", "Logging", "Mapping", "Routing"
    ]


def test_every_uhaul_phase_compiles_a_phase_local_state_graph():
    sample = {
        "data_map": {"map_identifier": "M", "map_name": "N", "map_class": "C", "contivo_version": "6.7", "map_data_file": "m.jar"},
        "rule": {"name": "R", "document_type_name_version": "D(1.0)", "rule_type": "Mapping", "rule_scope": "GLOBAL", "conditions": {"execute_actions_when": "one or more conditions are satisfied", "rows": [{"condition_type": "Attributes", "operator": "Equals", "value": "x", "attribute_name_unit": "Receiver"}]}, "actions": {"action_name": "A", "action_type": "Route Document", "mapping_identifier_name_version": "M(1.0)"}},
        "source_transport_profile": {"system_type": "Dell Application", "partner_name": "AIC - DCE", "profile_name": "TP", "profile_usage": "Sender", "deployment_group": "dce-shared-sender", "interface_type": "SFTP HAFT", "interface_environment": "UAT", "existing_account": "Yes", "existing_account_name": "acct", "use_existing_folder": "No", "subscription_folder": "/x", "document_type": "D(1.0)"},
        "biz_flow": {"flow_details": {"business_flow_name": "BF"}, "configure_source": {"source_type": "Dell Application", "source_application": "AIC - DCE", "source_transport_profile": "TP", "document_type_name_version": "D(1.0)"}, "flow_identifiers": {"operator": "all conditions are satisfied", "conditions": [{"attribute_name": "Receiver", "operator": "Equals", "value": "x"}]}, "configure_targets": {"target_type": "Partner", "target_application": "P", "target_transport_profile": "T", "document_type_name_version": "TD(1.0)"}, "process_steps": [{"step_type": "Mapping Transformer", "step_name": "Mapping-1", "configuration": {"action": "Mapping", "target_document_type_version": "TD(1.0)", "rule_version": "R(1.0)"}}], "configure_routing": {"conditions": {"execute_actions_when": "all conditions are satisfied", "rows": [{"condition_type": "Attributes", "operator": "Equals", "value": "x", "attribute_name": "Receiver"}]}, "actions": {"name": "RA", "type": "Route Document", "target": "T"}}},
    }
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
    for phase, obj in sample.items():
        graph = compile_phase_state_graph({"objects": {phase: obj}}, phase)
        assert graph["nodes"], phase
        assert all(str(n["input_path"]).startswith(f"$.objects.{phase}") for n in graph["nodes"]), phase
        assert graph["strategy"] == "target-branch-first-then-safe-exploration"
    rule_graph = compile_phase_state_graph({"objects": {"rule": sample["rule"]}}, "rule")
    assert rule_graph["repeatable_rows"]["condition"] == 1
    assert any(e["relation"] in {"parent_value_reveals_child", "precedes_or_enables"} for e in rule_graph["dependency_edges"])


def test_transport_profile_boolean_branches_use_radio_actions():
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
    graph = compile_phase_state_graph({"objects": {"source_transport_profile": {
        "system_type": "Dell Application", "partner_name": "AIC - DCE",
        "profile_name": "TP", "profile_usage": "Sender", "deployment_group": "dce-shared-sender",
        "interface_type": "SFTP HAFT", "interface_environment": "UAT",
        "existing_account": "Yes", "existing_account_name": "acct",
        "use_existing_folder": "No", "subscription_folder": "/in", "document_type": "D(1.0)",
    }}}, "source_transport_profile")
    actions = {n["field_key"]: n["action"] for n in graph["nodes"]}
    assert actions["existing_account"] == "select_radio"
    assert actions["use_existing_folder"] == "select_radio"


def test_bizflow_tab_aliases_map_to_state_graph_sections():
    from hip_id_agent.bizflow_kb import bizflow_graph_section_for_tab
    assert bizflow_graph_section_for_tab("Basic Details") == "Flow Details"
    assert bizflow_graph_section_for_tab("Source Details") == "Configure Source"
    assert bizflow_graph_section_for_tab("Target Details") == "Configure Target(s)"
    assert bizflow_graph_section_for_tab("Configure Routing + Add") == "Configure Routing"


def test_generic_target_knowledge_preserves_phase_repeatable_groups():
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, build_target_branch_knowledge
    source = {"objects": {"rule": {
        "name": "R", "document_type_name_version": "D(1.0)", "rule_type": "Mapping", "rule_scope": "GLOBAL",
        "conditions": {"execute_actions_when": "one or more conditions are satisfied", "rows": [
            {"condition_type": "Attributes", "operator": "Equals", "value": "uhaul", "attribute_name_unit": "Receiver"},
            {"condition_type": "Attributes", "operator": "Contains", "value": "DELL", "attribute_name_unit": "Sender"},
        ]},
        "actions": {"action_name": "A", "action_type": "Route Document", "mapping_identifier_name_version": "M(1.0)"},
    }}}
    graph = compile_phase_state_graph(source, "rule")
    execution = {"pass": True, "section": "all", "attempts": [], "failed_attempts": [], "final_controls": [], "observed_dependency_edges": []}
    knowledge = build_target_branch_knowledge(graph, execution)
    assert knowledge["phase"] == "rule"
    assert knowledge["section"] == "all"
    assert knowledge["repeatable_rows"] == [{"section": "all", "row_kind": "condition", "row_count": 2}]


def test_generic_resolver_accepts_bizflow_tab_alias_and_row_occurrence():
    from hip_id_agent.stateful_form_runtime import resolve_stateful_control
    controls = [
        {"index": 1, "section": "Source Details", "label": "Value", "name": "value", "row_index": 0, "label_occurrence": 0},
        {"index": 2, "section": "Source Details", "label": "Value", "name": "value", "row_index": 1, "label_occurrence": 1},
    ]
    node = {
        "section": "Configure Source", "row_index": 1, "action": "fill_text", "expected_value": "DELL",
        "semantic_locator": {"labels": ["Value"], "names": ["value"], "placeholders": []},
    }
    resolved = resolve_stateful_control(controls, node)
    assert resolved is not None and resolved["index"] == 2


def test_generic_resolver_uses_expected_radio_value():
    from hip_id_agent.stateful_form_runtime import resolve_stateful_control
    controls = [
        {"index": 1, "section": "Create Transport Profile", "label": "Yes", "value": "Yes", "checked": False},
        {"index": 2, "section": "Create Transport Profile", "label": "No", "value": "No", "checked": True},
    ]
    node = {
        "section": "Create Transport Profile", "action": "select_radio", "expected_value": "No",
        "semantic_locator": {"labels": ["Use Existing Folder", "Existing Folder"], "names": [], "placeholders": []},
    }
    resolved = resolve_stateful_control(controls, node)
    assert resolved is not None and resolved["label"] == "No"


def test_flash_blueprint_loads_state_graph_execution_for_non_doctype(tmp_path: Path):
    from hip_id_agent.dummy_fill_e2e import _extract_state_graph_execution
    kb = tmp_path / "rule_kb"
    kb.mkdir()
    expected = {"phase": "rule", "pass": True, "graph_id": "g1"}
    import json
    (kb / "rule_target_branch_execution.json").write_text(json.dumps(expected), encoding="utf-8")
    assert _extract_state_graph_execution(tmp_path, "rule") == expected


def test_dds_multiselect_option_selector_uses_unique_aria_position_not_descendant_nth_type():
    from hip_id_agent.dds_control_driver import _find_multiselect_option, _unique_multiselect_option_selector
    snapshot = {
        "list_id": "dropdown-popup-list-594167474",
        "options": [
            {"text": "Select all", "aria_posinset": "1", "selected": False, "token": "a"},
            {"text": "Flow Identifier Expression", "aria_posinset": "4", "selected": False, "token": "b"},
            {"text": "Logging", "aria_posinset": "5", "selected": False, "token": "c"},
        ],
    }
    option = _find_multiselect_option(snapshot, "Flow Identifier Expression")
    selector = _unique_multiselect_option_selector(snapshot, option)
    assert selector == '[id="dropdown-popup-list-594167474"] [role="option"][aria-posinset="4"]'
    assert "nth-of-type" not in selector


def test_dds_multiselect_delta_preserves_existing_values_and_selects_all_missing():
    from hip_id_agent.dds_control_driver import _multiselect_delta
    wanted = ["Flow Identifier Expression", "Logging", "Mapping", "Routing"]
    assert _multiselect_delta(wanted, ["Flow Identifier Expression"]) == {
        "missing": ["Logging", "Mapping", "Routing"],
        "extra": [],
    }
    assert _multiselect_delta(wanted, wanted) == {"missing": [], "extra": []}


def test_dds_multiselect_snapshot_reads_aria_checked_and_selected_labels():
    from hip_id_agent.dds_control_driver import _multiselect_selected_values_from_snapshot
    snapshot = {
        "options": [
            {"text": "Select all", "selected": True},
            {"text": "Flow Identifier Expression", "selected": True},
            {"text": "Logging", "selected": True},
            {"text": "Mapping", "selected": True},
            {"text": "Routing", "selected": True},
        ],
        "selected_labels": ["4 selected"],
    }
    assert _multiselect_selected_values_from_snapshot(snapshot) == [
        "Flow Identifier Expression", "Logging", "Mapping", "Routing"
    ]


def test_artifact_multiselect_reads_aria_checked_options(tmp_path: Path):
    d = tmp_path / "dom_snapshots"
    d.mkdir()
    (d / "source_document_type_after_dummy_fill_no_save.html").write_text(
        '''<html><body><fieldset><legend>Attributes To Configure</legend>
        <div class="dds__d-flex dds__justify-content-start"><dds-dropdown selection="multiple">
        <input id="usage" name="usage" placeholder="Usage">
        <button role="option" aria-checked="true">Flow Identifier Expression</button>
        <button role="option" aria-checked="true">Logging</button>
        <button role="option" aria-checked="true">Mapping</button>
        <button role="option" aria-checked="true">Routing</button>
        </dds-dropdown></div></fieldset></body></html>''',
        encoding="utf-8",
    )
    state = _artifact_actual_state(tmp_path, [])
    usage = next(c for c in state["controls"] if c["selection_mode"] == "multiple")
    assert set(usage["value"]) == {
        "Flow Identifier Expression", "Logging", "Mapping", "Routing"
    }


def test_dds_multiselect_runtime_selects_four_values_additively_without_delete_keys():
    import asyncio
    import re as _re
    from hip_id_agent.dds_control_driver import select_dds_multiselect

    class FakeLocator:
        def __init__(self, page):
            self.page = page
        @property
        def first(self):
            return self
        async def count(self):
            return 1
        async def click(self, timeout=None):
            self.page.expanded = True
        async def is_visible(self, timeout=None):
            return True
        async def input_value(self, timeout=None):
            return self.page.search_value
        async def fill(self, value):
            self.page.search_value = value

    class FakeBackend:
        def __init__(self, page):
            self.page = page
            self.click_targets = []
            self.press_calls = []
        async def click(self, target, *, element=""):
            self.click_targets.append(target)
            if target == "input#usage":
                self.page.expanded = True
                return
            match = _re.search(r'aria-posinset="(\d+)"', target)
            assert match, target
            pos = match.group(1)
            option = next(x for x in self.page.options if x["aria_posinset"] == pos)
            option["selected"] = not option["selected"]
        async def fill(self, target, value, *, element="", slowly=False):
            self.page.search_value = value
        async def press(self, target, key, *, element=""):
            self.press_calls.append((target, key))

    class FakePage:
        def __init__(self):
            self.expanded = False
            self.search_value = ""
            labels = [
                ("1", "Select all"),
                ("2", "Aggregate"),
                ("3", "Dedup"),
                ("4", "Flow Identifier Expression"),
                ("5", "Logging"),
                ("6", "Mapping"),
                ("7", "Routing"),
                ("8", "Strict Order"),
                ("9", "Target File Name"),
            ]
            self.options = [
                {"text": text, "selected": False, "disabled": False, "id": "", "aria_posinset": pos,
                 "aria_setsize": "9", "token": f"tok-{pos}"}
                for pos, text in labels
            ]
            self._hip_playwright_mcp_backend = FakeBackend(self)
        def locator(self, selector):
            return FakeLocator(self)
        async def wait_for_timeout(self, ms):
            return None
        async def evaluate(self, script, arg=None):
            if isinstance(arg, str) and "const input=document.querySelector(selector)" in script:
                return {
                    "found": True,
                    "list_id": "dropdown-popup-list-usage",
                    "expanded": self.expanded,
                    "search_value": self.search_value,
                    "options": [dict(x) for x in self.options],
                    "selected_labels": [],
                    "summary_text": "",
                }
            return True

    page = FakePage()
    wanted = ["Flow Identifier Expression", "Logging", "Mapping", "Routing"]
    result = asyncio.run(select_dds_multiselect(page, None, "input#usage", wanted, phase="source_document_type"))
    assert result is True
    selected = [x["text"] for x in page.options if x["selected"]]
    assert selected == wanted
    assert page._hip_playwright_mcp_backend.press_calls == []
    option_targets = [x for x in page._hip_playwright_mcp_backend.click_targets if "aria-posinset" in x]
    assert option_targets == [
        '[id="dropdown-popup-list-usage"] [role="option"][aria-posinset="4"]',
        '[id="dropdown-popup-list-usage"] [role="option"][aria-posinset="5"]',
        '[id="dropdown-popup-list-usage"] [role="option"][aria-posinset="6"]',
        '[id="dropdown-popup-list-usage"] [role="option"][aria-posinset="7"]',
    ]


def test_multiselect_exact_verifier_ignores_cosmetic_selected_count():
    from hip_id_agent.stateful_form_runtime import _clean_selected_values, _value_equal
    node = {
        "action": "select_multi",
        "expected_value": ["Flow Identifier Expression", "Logging", "Mapping", "Routing"],
    }
    control = {
        "selected_values": [
            "Flow Identifier Expression", "Logging", "Mapping", "Routing", "4 selected"
        ]
    }
    assert _clean_selected_values(control["selected_values"]) == [
        "Flow Identifier Expression", "Logging", "Mapping", "Routing"
    ]
    assert _value_equal(node, control) is True


def test_repeatable_usage_resolver_never_selects_attribute_name_input():
    from hip_id_agent.stateful_form_runtime import resolve_control
    controls = [
        {
            "index": 10,
            "section": "Attributes To Configure",
            "row_kind": "attribute",
            "row_index": 1,
            "semantic_key": "attribute_name",
            "label": "Attribute Name",
            "selector": "input#attribute-name-row-1",
        },
        {
            "index": 11,
            "section": "Attributes To Configure",
            "row_kind": "attribute",
            "row_index": 1,
            "semantic_key": "attribute_usage",
            "label": "Usage",
            "selector": "input#usage-row-1",
            "selection_mode": "multiple",
            "selected_values": ["Flow Identifier Expression", "Logging", "Mapping", "Routing"],
        },
    ]
    node = {
        "field_key": "attribute_usage",
        "section": "Attributes To Configure",
        "row_kind": "attribute",
        "row_index": 1,
        "action": "select_multi",
        "semantic_locator": {"labels": ["Usage"], "placeholders": ["Usage"], "names": []},
    }
    resolved = resolve_control(controls, node)
    assert resolved is not None
    assert resolved["selector"] == "input#usage-row-1"


def test_repeatable_usage_resolver_fails_closed_without_usage_semantic_control():
    from hip_id_agent.stateful_form_runtime import resolve_control
    controls = [{
        "index": 10,
        "section": "Attributes To Configure",
        "row_kind": "attribute",
        "row_index": 3,
        "semantic_key": "attribute_name",
        "label": "Attribute Name",
        "selector": "input#attribute-name-row-3",
    }]
    node = {
        "field_key": "attribute_usage",
        "section": "Attributes To Configure",
        "row_kind": "attribute",
        "row_index": 3,
        "action": "select_multi",
        "semantic_locator": {"labels": ["Usage"], "placeholders": ["Usage"], "names": []},
    }
    assert resolve_control(controls, node) is None
