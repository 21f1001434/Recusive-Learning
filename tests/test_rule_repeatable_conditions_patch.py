from pathlib import Path
import json

from hip_id_agent.rules_kb import (
    _extract_rule_action_row,
    _extract_rule_condition_rows,
    extract_rule_seed,
)


def _uhaulp_input():
    return json.loads(Path('examples/uhaul_poasn_full_dummy_input.json').read_text(encoding='utf-8'))


def test_uhaul_rule_conditions_extracts_both_input_rows_in_order():
    rows = _extract_rule_condition_rows(_uhaulp_input())
    assert len(rows) == 2
    assert rows[0]['attribute_name_unit'] == 'Receiver'
    assert rows[0]['operator'] == 'Equals'
    assert rows[0]['value'] == 'uhaul'
    assert rows[1]['attribute_name_unit'] == 'Sender'
    assert rows[1]['operator'] == 'Contains'
    assert rows[1]['value'] == 'DELL'


def test_rule_seed_uses_input_json_condition_operation_and_action_mapping():
    seed = extract_rule_seed(_uhaulp_input())
    assert seed['condition_operation'] == 'one or more conditions are satisfied'
    assert seed['document_type_name_version'] == 'XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)'
    assert seed['condition_rows'][1]['attribute_name_unit'] == 'Sender'
    assert seed['mapping_identifier_name_version'] == 'DELLCoXMLASNXX08C_U-HAUL(1.0)'


def test_rules_kb_has_dedicated_conditions_add_and_section_aware_fill():
    source = Path('hip_id_agent/rules_kb.py').read_text(encoding='utf-8')
    assert '_apply_rule_condition_row_adds' in source
    assert '_find_rule_condition_add_candidate' in source
    assert 'small row-level +Add next to Conditions' in source
    assert '_fill_rule_exact_input_rows' in source
    assert 'conditions[{idx}].attribute_name_unit' in source
    assert 'generic_rule_repeatable_plan_only' in source
