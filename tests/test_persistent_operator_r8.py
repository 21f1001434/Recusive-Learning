from pathlib import Path

from hip_id_agent.config import load_config
from hip_id_agent.persistent_operator import (
    extract_inline_patch,
    infer_entity_name,
    infer_family,
    infer_operation,
    normalize_task,
)


def test_arbitrary_document_type_edit_intent():
    task = 'edit Document Type "ABC_DOCTYPE" and change Version to 2.0'
    assert infer_family(task) == 'document_types'
    assert infer_operation(task) == 'edit'
    assert infer_entity_name(task, 'document_types') == 'ABC_DOCTYPE'
    patch = extract_inline_patch(task)
    assert patch.get('Version') == '2.0'
    normalized = normalize_task(task, 'document_types', 'edit', 'ABC_DOCTYPE')
    assert 'Document Types' in normalized
    assert 'search' in normalized.lower()


def test_arbitrary_bizflow_and_rule_patch_values_are_run_local():
    assert infer_family('find BizFlow UHAL and change destination_value to UHI1002') == 'bizflows'
    assert extract_inline_patch('change destination_value to UHI1002').get('destination_value') == 'UHI1002'
    assert infer_family('Rule MAP1 set Operator = Equals') == 'rules'
    assert extract_inline_patch('set Operator = Equals').get('Operator') == 'Equals'


def test_r8_defaults_are_open_ended_and_human_final():
    cfg = load_config('config.yaml')
    assert cfg.persistent_operator.enabled is True
    assert cfg.persistent_operator.max_goal_cycles == 0
    assert cfg.persistent_operator.require_final_human_confirmation is True
    assert cfg.recursive_self_improvement.max_recursive_cycles == 0


def test_universal_executor_supports_borrowed_browser():
    import inspect
    from hip_id_agent.universal_portal_operator import UniversalPortalTaskExecutor
    assert 'browser_override' in inspect.signature(UniversalPortalTaskExecutor.execute).parameters
