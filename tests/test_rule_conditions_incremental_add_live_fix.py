import asyncio
import json
from pathlib import Path

from hip_id_agent import rules_kb
from hip_id_agent.stateful_form_runtime import (
    compile_phase_state_graph,
    resolve_stateful_control_diagnostics,
)


def _input_payload():
    return json.loads(Path("examples/uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def test_execute_actions_when_binds_to_exact_dds_label_not_condition_controls():
    graph = compile_phase_state_graph(_input_payload(), "rule")
    node = next(n for n in graph["nodes"] if n["field_key"] == "execute_actions_when")
    assert "Execute Action(s) When" in node["semantic_locator"]["labels"]

    controls = [
        {
            "index": 0,
            "selector": "#execute-actions",
            "semantic_key": "execute_action_s_when",
            "framework_key": "executeActions",
            "label": "Execute Action(s) When",
            "section": "Conditions :",
            "role": "combobox",
            "type": "text",
            "component_tag": "dds-dropdown",
            "interactable": True,
        },
        {
            "index": 1,
            "selector": "#condition-type",
            "semantic_key": "condition_type",
            "framework_key": "conditionType",
            "label": "Condition Type",
            "section": "Conditions :",
            "role": "combobox",
            "type": "text",
            "component_tag": "dds-dropdown",
            "interactable": True,
            "row_kind": "condition",
            "row_index": 0,
        },
        {
            "index": 2,
            "selector": "#operator",
            "semantic_key": "operator",
            "framework_key": "operator",
            "label": "Operator",
            "section": "Conditions :",
            "role": "combobox",
            "type": "text",
            "component_tag": "dds-dropdown",
            "interactable": True,
            "row_kind": "condition",
            "row_index": 0,
        },
    ]

    diagnostic = resolve_stateful_control_diagnostics(controls, node)
    assert diagnostic["resolved"] is True
    assert diagnostic["control"]["selector"] == "#execute-actions"
    assert diagnostic["score_margin"] >= 14


class _FakeLocator:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def scroll_into_view_if_needed(self, timeout=0):
        return None

    async def click(self, timeout=0):
        # Model the real Angular behavior: Conditions + has no effect while the
        # current row or the required base Rule/Action fields are incomplete.
        self.page.add_click_fill_indexes.append(len(self.page.fill_calls))
        required = {
            "rule_name",
            "document_type_name_version",
            "action_name",
            "action_type",
            "mapping_identifier_name_version",
        }
        if (self.page.row_count - 1) in self.page.valid_rows and required.issubset(self.page.filled_fields):
            self.page.row_count += 1
            self.page.row_values.append({})


class _FakePage:
    def __init__(self):
        self.row_count = 1
        self.valid_rows = set()
        self.fill_calls = []
        self.row_values = [{}]
        self.filled_fields = set()
        self.add_click_fill_indexes = []

    def locator(self, selector):
        assert selector == "#conditions-add"
        return _FakeLocator(self)

    async def wait_for_timeout(self, milliseconds):
        return None


async def _run_incremental_add(monkeypatch):
    page = _FakePage()

    async def fake_count(_page):
        return _page.row_count

    async def fake_inspect(_page):
        rows = []
        for idx in range(_page.row_count):
            values = _page.row_values[idx]
            controls = {
                key: {"selector": f"#{key}-{idx}", "id": f"{key}-{idx}", "value": value, "label": key}
                for key, value in values.items()
            }
            rows.append({"row_index": idx, "row_identity": f"condition-row-{idx}", "row_selector": f"#condition-row-{idx}", "controls": controls})
        return rows

    async def fake_find(_page):
        return {
            "ok": True,
            "selector": "#conditions-add",
            "label": "Create Condition",
            "method": "conditions_legend_direct_button",
        }

    async def fake_set(_page, *, section, label, value, occurrence=0, field=""):
        _page.fill_calls.append((section, label, occurrence, field, value))
        if field:
            _page.filled_fields.add(field)
        while len(_page.row_values) <= occurrence:
            _page.row_values.append({})
        key = {
            "Condition Type": "condition_type",
            "Operator": "operator",
            "Value": "value",
            "Attribute Name/Unit": "attribute_name_unit",
        }.get(label)
        if key:
            _page.row_values[occurrence][key] = value
        if field.endswith("attribute_name_unit"):
            _page.valid_rows.add(occurrence)
        return {
            "field": field or label,
            "section": section,
            "label": label,
            "occurrence": occurrence,
            "filled": True,
        }

    monkeypatch.setattr(rules_kb, "_count_rule_condition_rows", fake_count)
    monkeypatch.setattr(rules_kb, "_inspect_rule_condition_rows", fake_inspect)
    monkeypatch.setattr(rules_kb, "_find_rule_condition_add_candidate", fake_find)
    monkeypatch.setattr(rules_kb, "_set_rule_control_by_label", fake_set)

    warnings = []
    audit = await rules_kb._apply_rule_condition_row_adds(page, _input_payload(), warnings=warnings)
    return page, audit, warnings


def test_conditions_plus_is_clicked_only_after_current_row_is_filled(monkeypatch):
    page, audit, warnings = asyncio.run(_run_incremental_add(monkeypatch))

    assert warnings == []
    assert page.row_count == 2
    assert audit["initial_live_row_count"] == 1
    assert audit["final_live_row_count"] == 2
    assert audit["summary"]["clicked"] == 1
    assert audit["summary"]["failed"] == 0
    assert audit["summary"]["exact_row_count_pass"] is True
    assert audit["clicks"][0]["row_count_before"] == 1
    assert audit["clicks"][0]["row_count_after"] == 2
    assert audit["clicks"][0]["exact_plus_one"] is True

    first_row_attribute = next(i for i, call in enumerate(page.fill_calls) if call[3] == "conditions[0].attribute_name_unit")
    second_row_type = next(i for i, call in enumerate(page.fill_calls) if call[3] == "conditions[1].condition_type")
    assert first_row_attribute < second_row_type
    assert any(call[3] == "conditions[1].attribute_name_unit" for call in page.fill_calls)

class _ComboLocator:
    @property
    def first(self):
        return self

    async def evaluate(self, script):
        return {"tag": "input", "role": "combobox", "type": "text", "ddsDropdown": True, "expanded": "false"}


class _ComboPage:
    def locator(self, selector):
        assert selector == "#execute-actions"
        return _ComboLocator()

    async def wait_for_timeout(self, milliseconds):
        return None


def test_rule_label_fill_uses_real_dds_option_selection_for_combobox(monkeypatch):
    calls = []

    async def fake_find(page, *, section, label, occurrence=0):
        return {"selector": "#execute-actions", "label": label, "section": section}

    async def fake_root(page, phase):
        assert phase == "rule"
        return object()

    async def fake_select(page, root, selector, value, *, phase=""):
        calls.append((selector, value, phase))
        return True

    monkeypatch.setattr(rules_kb, "_find_rule_control_selector", fake_find)
    monkeypatch.setattr(rules_kb, "get_active_form_root", fake_root)
    monkeypatch.setattr(rules_kb, "select_dds_combobox", fake_select)

    attempt = asyncio.run(
        rules_kb._set_rule_control_by_label(
            _ComboPage(),
            section="Conditions",
            label="Execute Action(s) When",
            value="one or more conditions are satisfied",
            field="condition_operation",
        )
    )
    assert attempt["filled"] is True
    assert attempt["executor"] == "dds_control_driver.select_dds_combobox"
    assert calls == [("#execute-actions", "one or more conditions are satisfied", "rule")]


def test_exact_live_proof_rejects_one_physical_row_reused_for_two_input_rows():
    expected = rules_kb._extract_rule_condition_rows(_input_payload())
    one_live_row = [{
        "row_index": 0,
        "row_identity": "condition-type-input-0",
        "controls": {
            "condition_type": {"value": "Attributes"},
            "operator": {"value": "Contains"},
            "value": {"value": "DELL"},
            "attribute_name_unit": {"value": "Sender"},
        },
    }]
    proof = rules_kb._rule_condition_rows_exact(one_live_row, expected)
    assert proof["row_count_pass"] is False
    assert proof["pass"] is False


def test_rule_graph_treats_disabled_version_as_verify_only_and_status_switch_binds_uniquely():
    graph = compile_phase_state_graph(_input_payload(), "rule")
    version_node = next(n for n in graph["nodes"] if n["field_key"] == "rule_version")
    status_node = next(n for n in graph["nodes"] if n["field_key"] == "status")
    assert version_node["action"] == "verify_only"

    controls = [
        {
            "index": 0, "selector": "#version", "semantic_key": "version", "framework_key": "version",
            "label": "Version", "section": "Rule :", "role": "", "type": "text", "component_tag": "input",
            "interactable": False, "disabled": True, "value": "1.0",
        },
        {
            "index": 1, "selector": "#document-type", "semantic_key": "document_type_name_version",
            "framework_key": "documentTypeNameVersion", "label": "Document Type Name (Version)",
            "section": "Rule :", "role": "combobox", "type": "text", "component_tag": "dds-dropdown",
            "interactable": True, "value": "XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)",
        },
        {
            "index": 2, "selector": "#status", "semantic_key": "status", "framework_key": "ruleDisabled",
            "label": "Status", "section": "Rule :", "role": "switch", "type": "", "component_tag": "dds-switch",
            "interactable": True, "checked": True, "value": "",
        },
        {
            "index": 3, "selector": "#execute-always", "semantic_key": "execute_always", "framework_key": "executeAlways",
            "label": "Execute Always", "section": "Rule :", "role": "switch", "type": "", "component_tag": "dds-switch",
            "interactable": True, "checked": False, "value": "",
        },
    ]
    version_diag = resolve_stateful_control_diagnostics(controls, version_node)
    status_diag = resolve_stateful_control_diagnostics(controls, status_node)
    assert version_diag["resolved"] is True
    assert version_diag["control"]["selector"] == "#version"
    assert status_diag["resolved"] is True
    assert status_diag["control"]["selector"] == "#status"


class _SwallowedClickLocator:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def scroll_into_view_if_needed(self, timeout=0):
        return None

    async def click(self, timeout=0):
        self.page.click_count += 1
        if self.page.dropdown_pending:
            # Reproduce the live Dell DDS behavior: pointerdown commits/closes the
            # active dropdown and the Create Condition button receives no click.
            self.page.dropdown_pending = False
            return
        self.page.row_count += 1
        self.page.row_values.append({})


class _SwallowedClickPage:
    def __init__(self):
        self.row_count = 1
        self.row_values = [{
            "condition_type": "Attributes",
            "operator": "Equals",
            "value": "uhaul",
            "attribute_name_unit": "Receiver",
        }]
        self.dropdown_pending = True
        self.click_count = 0

    def locator(self, selector):
        assert selector == "#conditions-add"
        return _SwallowedClickLocator(self)

    async def wait_for_timeout(self, milliseconds):
        return None


async def _run_swallowed_click_recovery(monkeypatch):
    page = _SwallowedClickPage()

    async def fake_inspect(_page):
        rows = []
        for idx, values in enumerate(_page.row_values[:_page.row_count]):
            controls = {
                key: {
                    "selector": f"#{key}-{idx}",
                    "id": f"{key}-{idx}",
                    "value": value,
                    "label": key,
                }
                for key, value in values.items()
            }
            rows.append({
                "row_index": idx,
                "row_identity": f"condition-row-{idx}",
                "row_selector": f"#condition-row-{idx}",
                "controls": controls,
            })
        return rows

    async def fake_find(_page):
        return {
            "ok": True,
            "selector": "#conditions-add",
            "label": "Create Condition",
            "method": "conditions_legend_direct_button",
        }

    async def fake_wait(_page, expected, timeout_ms=5000):
        return _page.row_count

    settle_calls = []

    async def fake_settle(_page):
        settle_calls.append(_page.dropdown_pending)
        # First settlement deliberately models a DDS popup that remains pending.
        # The first physical click is swallowed; the bounded second settlement
        # confirms the popup is now gone and permits exactly one retry.
        return {
            "settled": not _page.dropdown_pending,
            "before": {"expanded_count": int(_page.dropdown_pending)},
            "after": {"expanded_count": int(_page.dropdown_pending)},
        }

    monkeypatch.setattr(rules_kb, "_inspect_rule_condition_rows", fake_inspect)
    monkeypatch.setattr(rules_kb, "_find_rule_condition_add_candidate", fake_find)
    monkeypatch.setattr(rules_kb, "_wait_for_rule_condition_row_count", fake_wait)
    monkeypatch.setattr(rules_kb, "_settle_rule_condition_dropdowns", fake_settle)
    monkeypatch.setattr(rules_kb, "_arm_rule_condition_add_click_probe", lambda *args, **kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(rules_kb, "_read_rule_condition_add_click_probe", lambda *args, **kwargs: asyncio.sleep(0, result={"clicked": False}))

    expected = rules_kb._extract_rule_condition_rows(_input_payload())[:1]
    audit = await rules_kb._click_rule_condition_add_exact(
        page,
        expected_rows_prefix=expected,
        max_click_attempts=2,
    )
    return page, audit, settle_calls


def test_conditions_plus_recovers_when_first_click_only_commits_open_dds_dropdown(monkeypatch):
    page, audit, settle_calls = asyncio.run(_run_swallowed_click_recovery(monkeypatch))

    assert page.click_count == 2
    assert page.row_count == 2
    assert audit["exact_plus_one"] is True
    assert audit["clicked"] is True
    assert audit["successful_physical_click_attempt"] == 2
    assert len(audit["physical_click_attempts"]) == 2
    assert audit["physical_click_attempts"][0]["row_count_after"] == 1
    assert audit["physical_click_attempts"][1]["row_count_after"] == 2
    assert settle_calls == [True, False]


def test_rule_runtime_does_not_downgrade_failed_conditions_add_to_warning():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    assert "Repeatable row Add planning skipped" not in source
    assert "Rule Conditions mandatory +Add/fill transaction failed; refusing row reuse" in source
    assert "include_conditions=False" in source


def test_rule_document_type_is_committed_before_condition_attribute_dependency(monkeypatch):
    page, audit, warnings = asyncio.run(_run_incremental_add(monkeypatch))

    fields = [call[3] for call in page.fill_calls]
    doc_index = fields.index("document_type_name_version")
    execute_index = fields.index("condition_operation")
    type_index = fields.index("conditions[0].condition_type")
    attribute_index = fields.index("conditions[0].attribute_name_unit")

    assert warnings == []
    assert doc_index < execute_index < type_index < attribute_index
    doc_attempt = next(a for a in audit["prerequisite_attempts"] if a["field"] == "document_type_name_version")
    assert doc_attempt["filled"] is True


def test_rule_default_action_is_committed_before_conditions_plus(monkeypatch):
    page, audit, warnings = asyncio.run(_run_incremental_add(monkeypatch))

    assert warnings == []
    assert page.add_click_fill_indexes
    first_click_index = page.add_click_fill_indexes[0]
    fields_before_click = {call[3] for call in page.fill_calls[:first_click_index]}
    assert {
        "rule_name",
        "document_type_name_version",
        "action_name",
        "action_type",
        "mapping_identifier_name_version",
        "conditions[0].attribute_name_unit",
    }.issubset(fields_before_click)
    assert audit["prerequisite_result"]["pass"] is True


class _StrictAttributeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def scroll_into_view_if_needed(self, timeout=0):
        return None

    async def click(self, timeout=0):
        self.page.clicked_selectors.append(self.selector)
        if self.selector == "#receiver-option":
            self.page.current_value = "Receiver"

    async def fill(self, value, timeout=0):
        self.page.filled_values.append((self.selector, value))


class _StrictAttributeKeyboard:
    async def press(self, key):
        return None


class _StrictAttributePage:
    def __init__(self, *, option_available=True):
        self.option_available = option_available
        self.clicked_selectors = []
        self.filled_values = []
        self.current_value = ""
        self.keyboard = _StrictAttributeKeyboard()

    def locator(self, selector):
        return _StrictAttributeLocator(self, selector)

    async def wait_for_timeout(self, milliseconds):
        return None

    async def evaluate(self, script, payload=None):
        if self.option_available:
            return {
                "control_found": True,
                "popup_found": True,
                "popup_selector": "#owned-attribute-popup",
                "option_count": 2,
                "no_options": False,
                "selected_option": {"selector": "#receiver-option", "text": "Receiver"},
            }
        return {
            "control_found": True,
            "popup_found": True,
            "popup_selector": "#owned-attribute-popup",
            "popup_text": "No options found",
            "option_count": 0,
            "no_options": True,
            "selected_option": None,
        }


def test_attribute_name_uses_only_owned_dds_popup_and_verifies_value(monkeypatch):
    page = _StrictAttributePage(option_available=True)

    async def fake_read(loc):
        return page.current_value

    async def fake_lock(*args, **kwargs):
        return None

    monkeypatch.setattr(rules_kb, "_read_control_value", fake_read)
    monkeypatch.setattr(rules_kb, "_lock_filled_value", fake_lock)

    attempt = asyncio.run(rules_kb._select_rule_condition_attribute_exact(
        page,
        selector="#attribute-input",
        value="Receiver",
        occurrence=0,
        field="conditions[0].attribute_name_unit",
    ))

    assert attempt["filled"] is True
    assert attempt["executor"] == "rule.conditions.strict_owned_dds_popup"
    assert page.clicked_selectors == ["#attribute-input", "#receiver-option"]
    assert all("checkbox" not in selector for selector in page.clicked_selectors)


def test_attribute_name_no_options_fails_without_global_checkbox_fallback(monkeypatch):
    page = _StrictAttributePage(option_available=False)

    async def fake_close(*args, **kwargs):
        return None

    monkeypatch.setattr(rules_kb, "close_open_dropdown", fake_close)

    attempt = asyncio.run(rules_kb._select_rule_condition_attribute_exact(
        page,
        selector="#attribute-input",
        value="Receiver",
        occurrence=0,
        field="conditions[0].attribute_name_unit",
    ))

    assert attempt["filled"] is False
    assert "owned by Attribute Name/Unit" in attempt["reason"]
    assert set(page.clicked_selectors) == {"#attribute-input"}
    assert all("checkbox" not in selector for selector in page.clicked_selectors)
    assert len(attempt["popup_attempts"]) == 3


def test_condition_row_inspector_anchors_on_visible_nested_combobox_not_host_geometry():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    start = source.index("async def _inspect_rule_condition_rows")
    end = source.index("async def _count_rule_condition_rows", start)
    inspector = source[start:end]

    assert 'dds-dropdown[formcontrolname="conditionType"] input[role="combobox"]' in inspector
    assert "const anchors=Array.from" in inspector
    assert ").filter(visible);" in inspector
    assert "anchor.closest('[cdkdrag],.dds__row')" in inspector
    assert "host geometry is never used" in inspector.lower()
    assert "row-existence" in inspector.lower()
    assert "const hosts=Array.from(array.querySelectorAll" not in inspector


def test_attribute_commit_rebinds_replaced_angular_row_before_success(monkeypatch):
    page = _StrictAttributePage(option_available=True)
    page.current_value = ""  # the pre-click locator remains stale/blank
    lock_calls = []

    async def stale_read(loc):
        return ""

    async def fresh_rows(_page):
        value = "Receiver" if "#receiver-option" in _page.clicked_selectors else ""
        return [{
            "row_index": 0,
            "row_identity": "condition-row-live-0",
            "row_selector": "#condition-row-live-0",
            "controls": {
                "condition_type": {"selector": "#condition-type-live-0", "id": "condition-type-live-0", "value": "Attributes"},
                "attribute_name_unit": {"selector": "#attribute-live-rebound-0", "id": "attribute-live-rebound-0", "value": value},
            },
        }]

    async def fake_lock(_page, selector, value, *, combo=False):
        lock_calls.append((selector, value, combo))

    monkeypatch.setattr(rules_kb, "_read_control_value", stale_read)
    monkeypatch.setattr(rules_kb, "_inspect_rule_condition_rows", fresh_rows)
    monkeypatch.setattr(rules_kb, "_lock_filled_value", fake_lock)

    attempt = asyncio.run(rules_kb._select_rule_condition_attribute_exact(
        page,
        selector="#attribute-input-stale",
        value="Receiver",
        occurrence=0,
        field="conditions[0].attribute_name_unit",
    ))

    assert attempt["filled"] is True
    assert attempt["popup_attempts"][0]["fresh_row_state"]["row_identity"] == "condition-row-live-0"
    assert attempt["popup_attempts"][0]["actual_after_fresh_rebind"] == "Receiver"
    assert lock_calls == [("#attribute-live-rebound-0", "Receiver", True)]


class _SettleKeyboard:
    def __init__(self):
        self.keys = []

    async def press(self, key):
        self.keys.append(key)


class _SettlePage:
    def __init__(self):
        self.keyboard = _SettleKeyboard()
        self.evaluate_calls = 0

    async def evaluate(self, script, payload=None):
        self.evaluate_calls += 1
        return True

    async def wait_for_timeout(self, milliseconds):
        return None


def test_settle_does_not_send_escape_to_collapsed_focused_attribute(monkeypatch):
    page = _SettlePage()
    states = iter([
        {
            "surface_ok": True,
            "expanded_count": 0,
            "visible_popup_count": 0,
            "active_label": "Attribute Name/Unit",
            "active_tag": "input",
            "active_role": "combobox",
        },
        {
            "surface_ok": True,
            "expanded_count": 0,
            "visible_popup_count": 0,
            "active_label": "",
            "active_tag": "body",
            "active_role": "",
        },
    ])

    async def fake_state(_page):
        return next(states)

    async def fake_close(_page, phase):
        assert phase == "rule"
        return None

    monkeypatch.setattr(rules_kb, "_rule_condition_popup_state", fake_state)
    monkeypatch.setattr(rules_kb, "close_open_dropdown", fake_close)

    result = asyncio.run(rules_kb._settle_rule_condition_dropdowns(page))

    assert result["popup_was_open"] is False
    assert result["keyboard_escape_used"] is False
    assert page.keyboard.keys == []
    assert result["settled"] is True


def test_condition_add_refuses_known_invalid_foreground_rule_form(monkeypatch):
    page = _SwallowedClickPage()
    page.dropdown_pending = False

    async def fake_inspect(_page):
        values = _page.row_values[0]
        return [{
            "row_index": 0,
            "row_identity": "condition-row-0",
            "row_selector": "#condition-row-0",
            "controls": {
                key: {"selector": f"#{key}-0", "id": f"{key}-0", "value": value, "label": key}
                for key, value in values.items()
            },
        }]

    async def fake_validity(_page):
        return {
            "known": True,
            "valid": False,
            "angularValid": False,
            "classes": "dds__mt-3 ng-invalid ng-touched",
            "invalidControls": [{"label": "Action Name", "name": "actionName"}],
        }

    monkeypatch.setattr(rules_kb, "_inspect_rule_condition_rows", fake_inspect)
    monkeypatch.setattr(rules_kb, "_inspect_rule_create_form_validity", fake_validity)
    monkeypatch.setattr(
        rules_kb,
        "_settle_rule_condition_dropdowns",
        lambda *args, **kwargs: asyncio.sleep(0, result={"settled": True}),
    )

    expected = rules_kb._extract_rule_condition_rows(_input_payload())[:1]
    audit = asyncio.run(
        rules_kb._click_rule_condition_add_exact(
            page,
            expected_rows_prefix=expected,
            max_click_attempts=2,
        )
    )

    assert page.click_count == 0
    assert audit["exact_plus_one"] is False
    assert "form is still invalid" in audit["reason"]
    assert audit["form_validity_before_click"]["invalidControls"][0]["label"] == "Action Name"


def test_condition_add_click_probe_captures_nested_icon_activation():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    assert "root.addEventListener('click', handler, true)" in source
    assert "live.contains(target)" in source
    assert "targetTag" in source


class _OwnedMapLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def scroll_into_view_if_needed(self, timeout=0):
        self.page.scrolled.append(self.selector)

    async def click(self, timeout=0):
        self.page.clicked.append(self.selector)
        if self.selector == "#owned-map-option":
            self.page.current_value = "DELLCoXMLASNXX08C_U-HAUL(1.0)"

    async def fill(self, value, timeout=0):
        self.page.filled.append((self.selector, value))


class _OwnedMapPage:
    def __init__(self):
        self.clicked = []
        self.filled = []
        self.scrolled = []
        self.current_value = ""
        self.probe_calls = 0

    def locator(self, selector):
        return _OwnedMapLocator(self, selector)

    async def evaluate(self, script, payload=None):
        self.probe_calls += 1
        if self.probe_calls < 3:
            return {
                "control_found": True,
                "list_found": True,
                "list_id": "owned-map-list",
                "loading": True,
                "no_options": False,
                "option_count": 0,
                "option_selector": "",
            }
        return {
            "control_found": True,
            "list_found": True,
            "list_id": "owned-map-list",
            "loading": False,
            "no_options": False,
            "option_count": 1,
            "exact_option": {"text": "DELLCoXMLASNXX08C_U-HAUL(1.0)", "pos": "241"},
            "option_selector": "#owned-map-option",
        }

    async def wait_for_timeout(self, milliseconds):
        return None


def test_mapping_identifier_waits_for_owned_async_dds_option_and_rebinds(monkeypatch):
    page = _OwnedMapPage()
    lock_calls = []

    async def fake_find(_page, *, section, label, occurrence=0):
        assert section == "Actions"
        assert label == "Mapping Identifier Name (Version)"
        return {"selector": "#mapping-input-fresh"}

    async def fake_read(loc):
        return page.current_value

    async def fake_lock(_page, selector, value, *, combo=False):
        lock_calls.append((selector, value, combo))

    async def fake_close(*args, **kwargs):
        return None

    monkeypatch.setattr(rules_kb, "_find_rule_control_selector", fake_find)
    monkeypatch.setattr(rules_kb, "_read_control_value", fake_read)
    monkeypatch.setattr(rules_kb, "_lock_filled_value", fake_lock)
    monkeypatch.setattr(rules_kb, "close_open_dropdown", fake_close)

    attempt = asyncio.run(
        rules_kb._select_rule_mapping_identifier_exact(
            page,
            selector="#mapping-input",
            value="DELLCoXMLASNXX08C_U-HAUL(1.0)",
            timeout_ms=2000,
        )
    )

    assert attempt["filled"] is True
    assert attempt["executor"] == "rule.actions.strict_owned_async_dds_popup"
    assert page.probe_calls >= 3
    assert page.clicked == ["#mapping-input", "#owned-map-option"]
    assert all("checkbox" not in selector for selector in page.clicked)
    assert lock_calls == [
        ("#mapping-input-fresh", "DELLCoXMLASNXX08C_U-HAUL(1.0)", True)
    ]


class _PrerequisitePage:
    async def wait_for_timeout(self, milliseconds):
        return None


def test_prerequisites_use_temporary_unsaved_rule_name_when_exact_name_exists(monkeypatch):
    page = _PrerequisitePage()
    calls = []
    duplicate_states = iter([
        {
            "known": True,
            "duplicate": True,
            "value": "DELLCoXMLASNXX08C_U-HAUL_RULE",
            "message": "Rule Name already exists",
        },
        {
            "known": True,
            "duplicate": False,
            "value": "DELLCoXMLASNXX08C_U-HAUL_RULE__PROBE_FIXED",
            "message": "",
        },
    ])

    async def fake_set(_page, *, section, label, value, occurrence=0, field=""):
        calls.append((section, label, value, field))
        return {
            "field": field or label,
            "section": section,
            "label": label,
            "filled": True,
        }

    async def fake_duplicate(_page):
        return next(duplicate_states)

    async def fake_validity(_page):
        return {"known": True, "valid": False, "invalidControls": [{"label": "Condition Type"}]}

    monkeypatch.setattr(rules_kb, "_set_rule_control_by_label", fake_set)
    monkeypatch.setattr(rules_kb, "_rule_name_duplicate_state", fake_duplicate)
    monkeypatch.setattr(
        rules_kb,
        "_temporary_rule_name_for_structure",
        lambda original: "DELLCoXMLASNXX08C_U-HAUL_RULE__PROBE_FIXED",
    )
    monkeypatch.setattr(rules_kb, "_inspect_rule_create_form_validity", fake_validity)

    result = asyncio.run(
        rules_kb._prepare_rule_condition_add_prerequisites(page, _input_payload())
    )

    assert result["pass"] is True
    assert result["missing_or_failed"] == []
    assert result["transient_rule_name"]["used"] is True
    assert result["required_status"]["rule_name"] is True
    assert any(call[3] == "rule_name_structure_probe" for call in calls)
    assert any(call[3] == "mapping_identifier_name_version" for call in calls)


def test_rule_existing_name_validation_is_nonblocking_when_inventory_resolves_exact_rule():
    from hip_id_agent.dummy_fill_e2e import _classify_validation_messages

    messages = [{
        "message": "Rule Name already exists",
        "source": "rule_add_form_after_dummy_fill_no_save.html",
        "element": "small",
        "aria_invalid": True,
    }]
    summary = {
        "existing_object_resolution": {
            "found": True,
            "mode": "reuse_existing",
            "object_type": "rule",
            "rule_name": "DELLCoXMLASNXX08C_U-HAUL_RULE",
        }
    }
    result = _classify_validation_messages("rule", messages, summary)
    assert result["blocking"] == []
    assert result["accepted_nonblocking"][0]["classification"] == "existing_object_reuse"


def test_condition_transaction_restores_exact_rule_name_after_structural_add(monkeypatch):
    page = _FakePage()
    page.rule_name_value = "TEMP_PROBE_NAME"

    async def fake_count(_page):
        return _page.row_count

    async def fake_inspect(_page):
        rows = []
        for idx in range(_page.row_count):
            values = _page.row_values[idx]
            controls = {
                key: {"selector": f"#{key}-{idx}", "id": f"{key}-{idx}", "value": value, "label": key}
                for key, value in values.items()
            }
            rows.append({
                "row_index": idx,
                "row_identity": f"condition-row-{idx}",
                "row_selector": f"#condition-row-{idx}",
                "controls": controls,
            })
        return rows

    async def fake_find(_page):
        return {"ok": True, "selector": "#conditions-add", "label": "Create Condition"}

    async def fake_prepare(_page, input_data):
        required = {
            "rule_name",
            "document_type_name_version",
            "action_name",
            "action_type",
            "mapping_identifier_name_version",
        }
        _page.filled_fields.update(required)
        return {
            "pass": True,
            "attempts": [],
            "missing_or_failed": [],
            "transient_rule_name": {
                "used": True,
                "exact_rule_name": "DELLCoXMLASNXX08C_U-HAUL_RULE",
                "temporary_rule_name": "TEMP_PROBE_NAME",
            },
        }

    async def fake_set(_page, *, section, label, value, occurrence=0, field=""):
        _page.fill_calls.append((section, label, occurrence, field, value))
        if field:
            _page.filled_fields.add(field)
        if field == "rule_name":
            _page.rule_name_value = value
        while len(_page.row_values) <= occurrence:
            _page.row_values.append({})
        key = {
            "Condition Type": "condition_type",
            "Operator": "operator",
            "Value": "value",
            "Attribute Name/Unit": "attribute_name_unit",
        }.get(label)
        if key:
            _page.row_values[occurrence][key] = value
        if field.endswith("attribute_name_unit"):
            _page.valid_rows.add(occurrence)
        return {"field": field or label, "section": section, "label": label, "filled": True}

    async def fake_duplicate(_page):
        return {
            "known": True,
            "duplicate": True,
            "value": _page.rule_name_value,
            "message": "Rule Name already exists",
        }

    monkeypatch.setattr(rules_kb, "_count_rule_condition_rows", fake_count)
    monkeypatch.setattr(rules_kb, "_inspect_rule_condition_rows", fake_inspect)
    monkeypatch.setattr(rules_kb, "_find_rule_condition_add_candidate", fake_find)
    monkeypatch.setattr(rules_kb, "_prepare_rule_condition_add_prerequisites", fake_prepare)
    monkeypatch.setattr(rules_kb, "_set_rule_control_by_label", fake_set)
    monkeypatch.setattr(rules_kb, "_rule_name_duplicate_state", fake_duplicate)

    audit = asyncio.run(
        rules_kb._apply_rule_condition_row_adds(page, _input_payload(), warnings=[])
    )

    assert audit["summary"]["exact_input_pass"] is True
    assert audit["summary"]["exact_rule_name_restored"] is True
    assert page.rule_name_value == "DELLCoXMLASNXX08C_U-HAUL_RULE"
    assert audit["rule_name_restore_attempt"]["transient_structure_restore"] is True

class _ConditionalControlWaitPage:
    async def wait_for_timeout(self, milliseconds):
        return None


def test_mapping_control_waits_until_angular_mounts_target(monkeypatch):
    calls = {"n": 0}

    async def fake_find(_page, *, section, label, occurrence=0):
        calls["n"] += 1
        if calls["n"] < 4:
            return {"reason": "not found", "matches": []}
        return {
            "selector": "input#mapping-mounted-late",
            "label": "Mapping Identifier Name (Version)",
            "section": "actions",
        }

    monkeypatch.setattr(rules_kb, "_find_rule_control_selector", fake_find)
    found = asyncio.run(
        rules_kb._wait_for_rule_control_selector(
            _ConditionalControlWaitPage(),
            section="Actions",
            label="Mapping Identifier Name (Version)",
            timeout_ms=2000,
        )
    )
    assert found["selector"] == "input#mapping-mounted-late"
    assert found["waited_for_conditional_control"] is True
    assert calls["n"] == 4


def test_prerequisites_fall_back_to_long_mapping_wait_after_one_shot_not_found(monkeypatch):
    page = _PrerequisitePage()
    calls = []

    async def fake_set(_page, *, section, label, value, occurrence=0, field=""):
        calls.append((section, label, field))
        if field == "mapping_identifier_name_version":
            return {"field": field, "section": section, "label": label, "filled": False, "reason": "not found"}
        return {"field": field or label, "section": section, "label": label, "filled": True}

    async def fake_duplicate_wait(_page, *, timeout_ms=0):
        return {"known": True, "duplicate": False, "value": "DELLCoXMLASNXX08C_U-HAUL_RULE", "settled": True}

    async def fake_wait_control(_page, *, section, label, occurrence=0, timeout_ms=0):
        return {"selector": "#late-mapping", "method": "actions_formcontrol_target_direct"}

    async def fake_select_mapping(_page, *, selector, value, field="", timeout_ms=0):
        assert selector == "#late-mapping"
        assert value == "DELLCoXMLASNXX08C_U-HAUL(1.0)"
        return {"field": field, "selector": selector, "filled": True, "executor": "strict-owned"}

    async def fake_validity(_page):
        return {"known": True, "valid": False, "invalidControls": [{"label": "Condition Type"}]}

    monkeypatch.setattr(rules_kb, "_set_rule_control_by_label", fake_set)
    monkeypatch.setattr(rules_kb, "_wait_for_rule_name_duplicate_state", fake_duplicate_wait)
    monkeypatch.setattr(rules_kb, "_wait_for_rule_control_selector", fake_wait_control)
    monkeypatch.setattr(rules_kb, "_select_rule_mapping_identifier_exact", fake_select_mapping)
    monkeypatch.setattr(rules_kb, "_inspect_rule_create_form_validity", fake_validity)

    result = asyncio.run(rules_kb._prepare_rule_condition_add_prerequisites(page, _input_payload()))
    assert result["pass"] is True
    mapping = next(row for row in result["attempts"] if row.get("field") == "mapping_identifier_name_version")
    assert mapping["filled"] is True
    assert mapping["selector"] == "#late-mapping"
    assert mapping["conditional_control_wait"]["method"] == "actions_formcontrol_target_direct"

class _TimeoutSelectedLocator(_OwnedMapLocator):
    async def click(self, timeout=0):
        self.page.clicked.append(self.selector)
        if self.selector == "#owned-map-option":
            self.page.current_value = "DELLCoXMLASNXX08C_U-HAUL(1.0)"
            self.page.selected = True
            raise RuntimeError("Locator.click: Timeout 4000ms exceeded after performing click action")

    async def dispatch_event(self, event_name):
        self.page.dispatched.append((self.selector, event_name))
        if self.selector == "#owned-map-option" and event_name == "click":
            self.page.current_value = "DELLCoXMLASNXX08C_U-HAUL(1.0)"
            self.page.selected = True


class _TimeoutSelectedPage(_OwnedMapPage):
    def __init__(self):
        super().__init__()
        self.selected = False
        self.dispatched = []

    def locator(self, selector):
        return _TimeoutSelectedLocator(self, selector)

    async def evaluate(self, script, payload=None):
        self.probe_calls += 1
        return {
            "control_found": True,
            "control_value": self.current_value,
            "expanded": True,
            "list_found": True,
            "list_id": "owned-map-list",
            "list_visible": True,
            "loading": False,
            "global_blocking_loading": False,
            "disabled": False,
            "no_options": False,
            "option_count": 1,
            "exact_option": {
                "text": "DELLCoXMLASNXX08C_U-HAUL(1.0)",
                "pos": "1",
                "selected": self.selected,
                "disabled": False,
            },
            "option_selector": "#owned-map-option",
        }


def test_mapping_identifier_accepts_selected_state_after_playwright_click_timeout(monkeypatch):
    page = _TimeoutSelectedPage()
    locks = []

    async def fake_find(_page, *, section, label, occurrence=0):
        return {"selector": "#mapping-input-fresh"}

    async def fake_read(_loc):
        return page.current_value

    async def fake_lock(_page, selector, value, *, combo=False):
        locks.append((selector, value, combo))

    async def fake_close(*args, **kwargs):
        return None

    monkeypatch.setattr(rules_kb, "_find_rule_control_selector", fake_find)
    monkeypatch.setattr(rules_kb, "_read_control_value", fake_read)
    monkeypatch.setattr(rules_kb, "_lock_filled_value", fake_lock)
    monkeypatch.setattr(rules_kb, "close_open_dropdown", fake_close)

    result = asyncio.run(
        rules_kb._select_rule_mapping_identifier_exact(
            page,
            selector="#mapping-input",
            value="DELLCoXMLASNXX08C_U-HAUL(1.0)",
            timeout_ms=1000,
        )
    )

    assert result["filled"] is True
    assert result["commit_recovered_from"] == "post_click_state"
    assert page.clicked.count("#owned-map-option") == 1
    assert not any(selector == "#owned-map-option" and event == "click" for selector, event in page.dispatched)
    assert locks == [("#mapping-input-fresh", "DELLCoXMLASNXX08C_U-HAUL(1.0)", True)]


class _DispatchRecoveryLocator(_TimeoutSelectedLocator):
    async def click(self, timeout=0):
        self.page.clicked.append(self.selector)
        if self.selector == "#owned-map-option":
            raise RuntimeError("Locator.click: Timeout 4000ms exceeded before selection state")


class _DispatchRecoveryPage(_TimeoutSelectedPage):
    def locator(self, selector):
        return _DispatchRecoveryLocator(self, selector)


def test_mapping_identifier_uses_one_owned_dom_dispatch_after_timeout(monkeypatch):
    page = _DispatchRecoveryPage()

    async def fake_find(_page, *, section, label, occurrence=0):
        return {"selector": "#mapping-input-fresh"}

    async def fake_read(_loc):
        return page.current_value

    async def fake_close(*args, **kwargs):
        return None

    monkeypatch.setattr(rules_kb, "_find_rule_control_selector", fake_find)
    monkeypatch.setattr(rules_kb, "_read_control_value", fake_read)
    monkeypatch.setattr(rules_kb, "_lock_filled_value", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(rules_kb, "close_open_dropdown", fake_close)

    result = asyncio.run(
        rules_kb._select_rule_mapping_identifier_exact(
            page,
            selector="#mapping-input",
            value="DELLCoXMLASNXX08C_U-HAUL(1.0)",
            timeout_ms=1000,
        )
    )

    assert result["filled"] is True
    assert result["commit_recovered_from"] == "post_dispatch_state"
    assert ("#owned-map-option", "click") in page.dispatched
    assert all("checkbox" not in selector for selector in page.clicked)


class _TypedOnlyPage(_TimeoutSelectedPage):
    async def evaluate(self, script, payload=None):
        self.probe_calls += 1
        self.current_value = "DELLCoXMLASNXX08C_U-HAUL(1.0)"
        return {
            "control_found": True,
            "control_value": self.current_value,
            "expanded": True,
            "list_found": True,
            "list_id": "owned-map-list",
            "list_visible": True,
            "loading": False,
            "global_blocking_loading": False,
            "disabled": False,
            "no_options": False,
            "option_count": 1,
            "exact_option": {
                "text": "DELLCoXMLASNXX08C_U-HAUL(1.0)",
                "pos": "1",
                "selected": False,
                "disabled": False,
            },
            "option_selector": "#owned-map-option",
        }

    def locator(self, selector):
        class Locator(_TimeoutSelectedLocator):
            async def click(inner_self, timeout=0):
                inner_self.page.clicked.append(inner_self.selector)
                if inner_self.selector == "#owned-map-option":
                    raise RuntimeError("click failed without selecting")
            async def dispatch_event(inner_self, event_name):
                inner_self.page.dispatched.append((inner_self.selector, event_name))
        return Locator(self, selector)


def test_mapping_identifier_does_not_accept_typed_text_without_selected_option(monkeypatch):
    page = _TypedOnlyPage()

    async def fake_find(_page, *, section, label, occurrence=0):
        return {"selector": "#mapping-input-fresh"}

    async def fake_read(_loc):
        return page.current_value

    async def fake_close(*args, **kwargs):
        return None

    monkeypatch.setattr(rules_kb, "_find_rule_control_selector", fake_find)
    monkeypatch.setattr(rules_kb, "_read_control_value", fake_read)
    monkeypatch.setattr(rules_kb, "close_open_dropdown", fake_close)

    result = asyncio.run(
        rules_kb._select_rule_mapping_identifier_exact(
            page,
            selector="#mapping-input",
            value="DELLCoXMLASNXX08C_U-HAUL(1.0)",
            timeout_ms=300,
        )
    )

    assert result["filled"] is False
    assert "not committed" in result["reason"]
