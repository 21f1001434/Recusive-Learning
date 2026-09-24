from hip_id_agent.bizflow_kb import (
    BIZFLOWS_URL,
    extract_bizflow_record,
    extract_bizflow_records_from_payload,
    guess_field_key,
    _parse_page_count,
    build_dummy_fill_values,
    _is_bizflow_header_text,
)


def test_bizflows_url_is_bizexchange_page():
    assert BIZFLOWS_URL.endswith('/bizexchange/bizflows')


def test_extract_bizflow_record_common_api_shape():
    row = {
        'bizFlowId': 123,
        'bizFlowName': 'UHAL_POASN_BIZFLOW',
        'version': '1',
        'sourceDocumentType': 'XML_U-HAUL_ANS_IB',
        'targetDocumentType': 'XML_DellAutoASN_10_U-HAUL_ANS_IB',
        'ruleName': 'UHAL_POASN_RULE',
        'mapIdentifier': 'DELLCoXMLASNXX08C_UHAUL',
    }
    rec = extract_bizflow_record(row, source_url='https://developer.dell.com/api/bizflows')
    assert rec is not None
    assert rec['bizflow_id'] == '123'
    assert rec['flow_name'] == 'UHAL_POASN_BIZFLOW'
    assert rec['source_document_type'] == 'XML_U-HAUL_ANS_IB'
    assert rec['mapping_identifier'] == 'DELLCoXMLASNXX08C_UHAUL'


def test_extract_bizflow_records_from_wrapped_payload():
    payload = {'content': [{'flowId': 77, 'flowName': 'A'}, {'flowId': 78, 'flowName': 'B'}]}
    rows = extract_bizflow_records_from_payload(payload, source_url='https://x/api/bizflows/search')
    assert len(rows) == 2
    assert rows[0]['bizflow_id'] == '77'


def test_parse_bizflow_pagination_text():
    assert _parse_page_count('Items per page 10 1 - 10 of 666 items Next') == (1, 10, 666)


def test_guess_bizflow_field_keys():
    assert guess_field_key('Source Document Type', {}) == 'source_document_type'
    assert guess_field_key('Target Transport Profile', {}) == 'target_transport_profile'
    assert guess_field_key('Deployment Group', {}) == 'deployment_group'


def test_build_dummy_values_from_input():
    vals = build_dummy_fill_values({'objects': {'biz_flow': {'flow_name': 'ABC'}}})
    assert vals['flow_name'].startswith('DUMMY_')
    assert vals['environment'] == 'DEV'


def test_extract_bizflow_gateway_definition_list_shape():
    payload = {
        'content': [{
            'flowId': 400797,
            'flowName': 'SWS_PC_850_PREMIER_MAPPING_IB',
            'flowType': 'Inbound',
            'primaryDomains': 'Customer Experience (CX)',
            'sourceSystem': 'SWS',
            'targetSystems': ['AIC - DCE'],
            'sourceTransportProfiles': ['SFTP_SWS_PC_850_SRC_IB'],
            'targetTransportProfiles': ['SFTP_SWS_PC_850_TGT_OB'],
            'sourceDocumentTypeNames': ['EDF_EDIFACT_ORDERS_10_SWS_IB'],
            'targetDocumentTypeNames': ['XML_850_10_SWS_OB'],
            'latestFlowVersion': 3,
            'availableEnvironments': {'DEV': {'health': 'UP', 'flowVersion': 3.0}, 'PROD': {}},
        }]
    }
    rows = extract_bizflow_records_from_payload(payload, source_url='https://developer.dell.com/inaas-gateway/hipService-svc/api/flows/definition/list')
    assert len(rows) == 1
    row = rows[0]
    assert row['bizflow_id'] == '400797'
    assert row['flow_name'] == 'SWS_PC_850_PREMIER_MAPPING_IB'
    assert row['flow_type'] == 'Inbound'
    assert row['source_system'] == 'SWS'
    assert row['target_systems'] == 'AIC - DCE'
    assert row['source_transport_profile'] == 'SFTP_SWS_PC_850_SRC_IB'
    assert row['source_document_type'] == 'EDF_EDIFACT_ORDERS_10_SWS_IB'
    assert 'DEV' in row['environment']


def test_bizflow_header_text_detection_for_ui_filter():
    assert _is_bizflow_header_text('Flow Name Flow Type Primary Domains Source System Target Systems Available Environments')
    assert not _is_bizflow_header_text('SWS_PC_850_PREMIER_MAPPING_IB Inbound Customer Experience (CX) SWS AIC - DCE DEV')


def test_bizflow_multitab_plan_includes_four_tabs_and_nested_routing():
    from hip_id_agent.bizflow_kb import bizflow_multitab_plan
    plan = bizflow_multitab_plan()
    assert [p['tab'] for p in plan] == ['Basic Details', 'Source Details', 'Target Details', 'Configure Routing']
    assert plan[-1]['nested_add'] is True
    assert 'mapping_identifier' in plan[-1]['required_keys']


def test_bizflow_tab_label_normalization():
    from hip_id_agent.bizflow_kb import normalize_bizflow_tab_label
    assert normalize_bizflow_tab_label('Source Configuration') == 'Source Details'
    assert normalize_bizflow_tab_label('Configure Routing') == 'Configure Routing'
    assert normalize_bizflow_tab_label('General Details') == 'Basic Details'


def test_bizflow_guess_field_keys_for_multitab_fields():
    assert guess_field_key('Primary Domain', {}) == 'primary_domain'
    assert guess_field_key('Source System', {}) == 'source_system'
    assert guess_field_key('Target System', {}) == 'target_system'
    assert guess_field_key('Route Name', {}) == 'route_name'
    assert guess_field_key('Routing Condition', {}) == 'routing_condition'


def test_bizflow_dummy_values_include_multitab_defaults():
    vals = build_dummy_fill_values({})
    assert vals['template_name'] == 'Translation'
    assert vals['source_system'] == 'UHAUL'
    assert vals['target_system'] == 'AIC - DCE'
    assert vals['route_name'].startswith('DUMMY_')


def test_bizflow_truncated_gateway_list_salvage_extracts_complete_prefix_objects():
    from hip_id_agent.bizflow_kb import salvage_bizflow_records_from_truncated_body
    body = '{"content":[{"flowId":1,"flowName":"A","flowType":"Inbound","availableEnvironments":{"DEV":{"health":"UP"}}},{"flowId":2,"flowName":"B","sourceSystem":"SYS"},{"flowId":3,"flowName"'
    rows = salvage_bizflow_records_from_truncated_body(body, source_url='https://developer.dell.com/inaas-gateway/hipService-svc/api/flows/definition/list')
    assert [r['bizflow_id'] for r in rows] == ['1', '2']
    assert rows[0]['flow_name'] == 'A'
    assert rows[1]['source_system'] == 'SYS'


def test_bizflow_cookie_overlay_controls_are_filtered_before_form_kb():
    from hip_id_agent.bizflow_kb import _filter_bizflow_controls
    controls = [
        {'label': 'Marketing', 'selector': 'input#ot-group-id-C0008'},
        {'label': 'Cookie list search', 'selector': 'input#vendor-search-handler'},
        {'label': 'Business Flow Name *', 'selector': 'input#flowName'},
    ]
    filtered = _filter_bizflow_controls(controls)
    assert len(filtered) == 1
    assert filtered[0]['label'] == 'Business Flow Name *'


def test_bizflow_aliases_include_real_create_tabs_from_reference_images():
    from hip_id_agent.bizflow_kb import normalize_bizflow_tab_label
    assert normalize_bizflow_tab_label('Flow Details') == 'Basic Details'
    assert normalize_bizflow_tab_label('Configure Source') == 'Source Details'
    assert normalize_bizflow_tab_label('Configure Target(s)') == 'Target Details'
    assert normalize_bizflow_tab_label('Configure Routing') == 'Configure Routing'


def test_bizflow_dedupe_merges_api_and_ui_rows_by_flow_name():
    from hip_id_agent.bizflow_kb import _dedupe_bizflow_records
    api = {'bizflow_id': '400797', 'flow_name': 'SWS_PC_850_PREMIER_MAPPING_IB', 'source': 'api'}
    ui = {'bizflow_id': '', 'flow_name': 'SWS_PC_850_PREMIER_MAPPING_IB', 'flow_type': 'Inbound', 'source': 'ui'}
    rows = _dedupe_bizflow_records([ui, api])
    assert len(rows) == 1
    assert rows[0]['bizflow_id'] == '400797'
    assert rows[0]['flow_type'] == 'Inbound'


def test_bizflow_template_candidate_rejects_side_nav_home():
    from hip_id_agent.bizflow_kb import _safe_bizflow_template_candidate
    assert not _safe_bizflow_template_candidate('Home', 'side navigation')
    assert not _safe_bizflow_template_candidate('Transport Profiles', 'side navigation')
    assert _safe_bizflow_template_candidate('B2B-Flow-PubSub-Template', 'Create Biz Flow Search Flow Templates')
    assert _safe_bizflow_template_candidate('Select', 'B2B-Flow-PubSub-Template Create Biz Flow')


def test_bizflow_template_click_source_excludes_side_nav_links():
    import inspect
    import hip_id_agent.bizflow_kb as kb
    src = inspect.getsource(kb._click_bizflow_template_link_after_add)
    assert 'section.dds__side-nav' in src
    assert 'home|dashboard' in src.lower()
    assert 'B2B-Flow-PubSub-Template' in src


def test_bizflow_runtime_parser_extracts_screenshot_fields():
    from hip_id_agent.bizflow_kb import extract_runtime_key_values_from_text, summarize_runtime_snapshot
    text = """
    Basic Details
    Deployment Status ACTIVE
    Deployment Date 2026-07-07 20:13:01.0
    Deployed By adheesh.srivastava@dell.com
    Vulnerability false
    Latest Version 118
    TP-ROUTING
    Type TP-ROUTING
    Service Name trsvc
    Service Health HEALTHY
    Deployment Group Name dce-shared-sender
    Service Manager Name rt-dce-shared-sender-1
    Service Manager Health HEALTHY
    Kubernetes Namespace Name dev-hip-np236157-q
    Data Center AIT-NONPROD
    Image Name harbor.dell.com/hip/hip-service
    No Of Instance 2
    Uptime 11 Days 15 Hours
    FLOW-ROUTING
    Service Name frsvc
    Service Health UNKNOWN
    """
    values = extract_runtime_key_values_from_text(text)
    assert values['deployment_status'] == 'ACTIVE'
    assert values['deployment_date'].startswith('2026-07-07')
    assert values['service_health'] in {'HEALTHY', 'UNKNOWN'}
    snapshot = {'body_text': text, 'sections': [{'keyword': 'TP-ROUTING', 'text': text}], 'links': [{'text':'Deployment Log','href':'https://x/log'}]}
    summary = summarize_runtime_snapshot(snapshot)
    assert summary['basic_runtime_fields']['deployment_status'] == 'ACTIVE'
    assert summary['routing_services']
    assert summary['log_links'][0]['text'] == 'Deployment Log'


def test_bizflow_zip_includes_runtime_outputs():
    import inspect
    import hip_id_agent.bizflow_kb as kb
    src = inspect.getsource(kb._zip_summary)
    assert 'bizflow_runtime_deployment_profiles.json' in src
    assert 'bizflow_runtime_deployment_report.json' in src


def test_bizflow_form_dropdown_collector_harvests_options_source_present():
    import inspect
    import hip_id_agent.bizflow_kb as kb
    src = inspect.getsource(kb._collect_bizflow_dropdowns_with_options)
    assert 'safe_open_read_options_no_select' in src
    assert 'option_count' in src
    assert 'no_select' in src


def test_bizflow_dropdown_options_enrich_from_inventory_runtime_fallback():
    from hip_id_agent.bizflow_kb import _enrich_bizflow_dropdown_options
    rows = [{
        'source_system': 'SWS',
        'target_systems': 'AIC - DCE',
        'source_transport_profile': 'SFTP_SWS_SRC',
        'target_transport_profile': 'SFTP_SWS_TGT',
        'source_document_type': 'EDI_850_SRC',
        'target_document_type': 'XML_850_TGT',
        'raw_row_compact': {'availableEnvironments': {'DEV': {}, 'PROD': {}}},
    }]
    dropdowns = [
        {'label': 'Source Application', 'bizflow_tab': 'Source Details', 'options': []},
        {'label': 'Target Transport Profile', 'bizflow_tab': 'Target Details', 'options': []},
        {'label': 'Flow Identifier Operator', 'bizflow_tab': 'Source Details', 'options': []},
    ]
    enriched = _enrich_bizflow_dropdown_options(dropdowns, old_bizflows=rows, deep_profiles=[], runtime_profiles=[])
    assert enriched[0]['option_count'] >= 1
    assert 'SWS' in enriched[0]['options']
    assert 'SFTP_SWS_TGT' in enriched[1]['options']
    assert 'Equals' in enriched[2]['options']
    assert enriched[0]['option_capture_mode'].startswith('fallback_from_captured')


def test_bizflow_continue_clicker_has_scoped_js_fallback():
    import inspect
    import hip_id_agent.bizflow_kb as kb
    src = inspect.getsource(kb._click_bizflow_continue)
    assert 'scoped_js_fallback' in src
    assert 'create biz flow' in src.lower()


def test_bizflow_nested_row_plan_requires_exact_section_adds_from_uhaul_input():
    from hip_id_agent.bizflow_kb import build_bizflow_nested_row_plan
    data = {
        "objects": {
            "biz_flow": {
                "flow_identifiers": {
                    "conditions": [
                        {"attribute_name": "Receiver", "operator": "Equals", "value": "uhaul"},
                        {"attribute_name": "Sender", "operator": "Contains", "value": "DELL"},
                    ]
                },
                "process_steps": [
                    {"step_type": "Mapping Transformer", "step_name": "Mapping-1", "configuration": {"action": "Mapping"}},
                    {"step_type": "Enricher", "step_name": "Enricher-1", "configuration": {"action": "Enrich"}},
                ],
                "configure_routing": {
                    "conditions": {
                        "rows": [
                            {"condition_type": "Attributes", "attribute_name": "Receiver", "operator": "Equals", "value": "uhaul"},
                            {"condition_type": "Attributes", "attribute_name": "Sender", "operator": "Contains", "value": "DELL"},
                        ]
                    },
                    "actions": {"name": "FLOWACTION_U-HAUL_PC_856_ANS_XMLASN_OB_ROUTE", "type": "Route Document", "target": "SFTP_U-HAUL_ASN_PC_TGT_OB"},
                },
            }
        }
    }
    plan = build_bizflow_nested_row_plan(data)
    sections = {p["section"]: p for p in plan}
    assert sections["Configure Source Attribute Rows"]["add_clicks_needed"] == 1
    assert sections["Configure Target Process Step Rows"]["add_clicks_needed"] == 2
    assert sections["Configure Routing Condition Rows"]["add_clicks_needed"] == 1
    assert sections["Configure Routing Action Rows"]["add_clicks_needed"] == 1
    assert "attribute" in sections["Configure Source Attribute Rows"]["aliases"]
    assert "process step" in sections["Configure Target Process Step Rows"]["aliases"]
    assert "actions" in sections["Configure Routing Action Rows"]["aliases"]


def test_bizflow_capture_uses_dedicated_nested_adds_not_generic_repeatable_rows():
    import inspect
    import hip_id_agent.bizflow_kb as kb
    src = inspect.getsource(kb.capture_and_fill_bizflow_multitab_form)
    assert "_apply_bizflow_nested_row_adds" in src
    assert "_fill_bizflow_source_attribute_rows" in src
    assert "_fill_bizflow_process_step_rows" in src
    assert "_fill_bizflow_routing_action_rows" in src
    assert "apply_repeatable_row_adds(page, input_data or {}, \"biz_flow\"" not in src


def test_bizflow_nested_add_rejects_dropdown_chevrons_and_learns_state():
    import inspect
    import hip_id_agent.bizflow_kb as kb
    src = inspect.getsource(kb._click_bizflow_section_add)
    assert "dropdown__chevron" in src
    assert "inPicker" in src
    assert "state_delta" in src
    assert "_capture_bizflow_form_state" in src


def test_bizflow_row_fills_record_before_after_state_delta():
    import inspect
    import hip_id_agent.bizflow_kb as kb
    src = inspect.getsource(kb._fill_bizflow_selector)
    assert "before fill" in src
    assert "after fill" in src
    assert "state_delta" in src
    assert "row-specific control not visible" in src
