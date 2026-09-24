from pathlib import Path

from hip_id_agent.doctype_kb import (
    DOCTYPES_URL,
    DocumentTypeKBFlow,
    _detail_query_params,
    build_dummy_fill_values,
    build_doctype_api_flow_knowledge_graph,
    extract_document_type_seed,
    extract_doctype_records_from_payload,
    guess_field_key,
    _filter_doctype_dropdown_options,
)


def test_doctype_url_constant():
    assert DOCTYPES_URL.endswith('/securelink/doctypes')


def test_extract_doctype_records_from_summary_payload():
    payload = {
        'content': [
            {
                'documentTypeId': 10483,
                'documentTypeName': 'XML_SHIPMENT_NOTICE_10_U-HAUL_ANS_OB',
                'documentTypeVersion': '1',
                'status': 'Enable',
                'documentIdentifier': 'ShipmentNotice',
                'rootElement': 'ShipmentNotice',
                'dataFormatType': 'XML',
                'transactionType': '856',
                'validationType': 'Structure',
                'availableEnvironments': ['DEV'],
            }
        ]
    }
    rows = extract_doctype_records_from_payload(payload, source_url='https://developer.dell.com/inaas-gateway/hipService-svc/api/document-type/summary')
    assert len(rows) == 1
    assert rows[0]['document_type_id'] == '10483'
    assert rows[0]['document_type_name'] == 'XML_SHIPMENT_NOTICE_10_U-HAUL_ANS_OB'
    assert rows[0]['document_type_version'] == '1'
    assert rows[0]['root_element'] == 'ShipmentNotice'
    assert rows[0]['format'] == 'XML'
    assert rows[0]['transaction_type'] == '856'
    assert rows[0]['validation_type'] == 'Structure'


def test_guess_field_key_for_required_document_type_fields():
    assert guess_field_key('Document Type Name', {}) == 'document_type_name'
    assert guess_field_key('Document Type Version', {}) == 'document_type_version'
    assert guess_field_key('Document Identifier', {}) == 'document_identifier'
    assert guess_field_key('Root Element', {}) == 'root_element'
    assert guess_field_key('Document schema file', {}) == 'schema_file'
    assert guess_field_key('Transaction Type', {}) == 'transaction_type'
    assert guess_field_key('Data Format Type', {}) == 'format'
    assert guess_field_key('Validation Type', {}) == 'validation_type'
    assert guess_field_key('Attribute Name', {}) == 'attribute_name'
    assert guess_field_key('Usage', {}) == 'attribute_usage'


def test_dummy_values_are_safe_prefixed():
    vals = build_dummy_fill_values({'document_type_name': 'XML_SHIPMENT_NOTICE_10_U-HAUL_ANS_OB'})
    assert vals['document_type_name'].startswith('DUMMY_')


def test_doctype_seed_extracts_nested_document_identifier_and_attributes():
    data = {
        'objects': {
            'source_document_type': {
                'name': 'XML_DellAutoASN_10_U-HAUL_ANS_IB',
                'version': '1',
                'transaction_type': '856',
                'data_format_type': 'XML',
                'validation_type': 'Structure',
                'document_identifier': {
                    'operation': 'All conditions are satisfied',
                    'rows': [{'derived_from': 'TRANSACTION_ROOT_ELEMENT', 'value': 'DellAutoASN'}],
                },
                'attributes_to_configure': [
                    {'attribute_name': 'Receiver', 'derived_from': 'ELEMENT_IN_PAYLOAD', 'usage': 'Logging, Routing', 'expression': '/DellAutoASN/Header/To'}
                ],
            }
        }
    }
    seed = extract_document_type_seed(data)
    assert seed['document_type_name'] == 'XML_DellAutoASN_10_U-HAUL_ANS_IB'
    assert seed['document_identifier'] == 'DellAutoASN'
    assert seed['document_identifier_operation'] == 'All conditions are satisfied'
    assert seed['document_identifier_derived_from'] == 'TRANSACTION_ROOT_ELEMENT'
    assert seed['transaction_type'] == '856'
    assert seed['format'] == 'XML'
    assert seed['validation_type'] == 'Structure'
    assert seed['attribute_name'] == 'Receiver'
    vals = build_dummy_fill_values(seed)
    assert vals['document_identifier'].startswith('DUMMY_DellAutoASN')
    assert '{' not in vals['document_identifier']


def test_detail_query_params_normalizes_environment_list_strings():
    params = _detail_query_params({
        'document_type_name': 'TEST_DOC',
        'document_type_version': '1.0',
        'available_environments': "['DEV', 'TEST1']",
    })
    assert params['environment'] == 'DEV'


def test_doctype_kg_has_core_nodes():
    kb = {
        'url': DOCTYPES_URL,
        'doctype_api_interactions': [
            {'method': 'GET', 'url': '/api/document-type/summary', 'status': 200, 'doctype_rows_extracted': 1}
        ],
        'old_doctypes_inventory': [
            {'document_type_id': '10483', 'document_type_name': 'XML_SHIPMENT_NOTICE_10-UHAUL', 'document_type_version': '1'}
        ],
        'form_controls': [{'label': 'Document Type Name', 'selector': '#name', 'mapped_document_type_key': 'document_type_name'}],
        'required_fields': [{'label': 'Document Type Name'}],
        'dropdowns': [],
        'dummy_fill_attempts': [],
    }
    graph = build_doctype_api_flow_knowledge_graph(kb)
    labels = {n['type'] for n in graph['nodes']}
    assert 'DOCTYPE_RECORD' in labels
    assert 'ENDPOINT' in labels
    assert graph['summary']['old_doctypes'] == 1


def test_doctype_flow_default_enriches_all_discovered_rows():
    flow = DocumentTypeKBFlow(config=None)
    assert flow.max_api_pages == 250
    assert flow.max_detail_rows is None


def test_click_add_doctype_overlay_recovery_fails_closed_without_dom_fallback():
    import asyncio
    from hip_id_agent.doctype_kb import _click_add_doctype_with_overlay_recovery

    class FakePage:
        def __init__(self):
            self.form_open = False
        async def evaluate(self, script):
            if 'let changed = 0' in script:
                return 1
            if 'hasDocTypeName' in script:
                return self.form_open
            return None
        async def wait_for_timeout(self, _ms):
            return None

    class FakeAdd:
        def __init__(self, page):
            self.page = page
            self.first = self
        async def evaluate(self, _script):
            self.page.form_open = True

    class FakeBrowser:
        def __init__(self):
            self.clicks = 0
            self.logged = []
        async def wait_for_blocking_overlays_gone(self, timeout_ms=0):
            return False
        async def click_and_wait(self, **_kwargs):
            self.clicks += 1
            raise TimeoutError('loading-indicator overlay intercepts pointer events')
        async def save_dom_snapshot(self, _name):
            return None
        async def log_automation_click(self, **kwargs):
            self.logged.append(kwargs)
        async def wait_ready(self):
            return None
        async def collect_dom_click_log(self):
            return None
        async def screenshot(self, *_args, **_kwargs):
            return None

    page = FakePage()
    warnings = []
    ok = asyncio.run(_click_add_doctype_with_overlay_recovery(
        page, FakeBrowser(), FakeAdd(page), kb_dir=Path('.'), warnings=warnings
    ))
    assert ok is False
    assert page.form_open is False
    assert any('disabled pointer-events' in w for w in warnings)
    assert any('Could not open + Add Document Type form' in w for w in warnings)


def test_doctype_dropdown_filter_removes_portal_navigation_noise():
    opts = [
        {'text': 'XML'}, {'text': 'JSON'}, {'text': 'EDIFACT'}, {'text': 'EDIX12'}, {'text': 'CSV'}, {'text': 'FLAT'},
        {'text': 'Home'}, {'text': 'BizLink'}, {'text': 'SecureLink'}, {'text': 'Items per page'},
    ]
    cleaned = _filter_doctype_dropdown_options(
        'Data Format Type',
        opts,
        ['home', 'bizlink', 'securelink', 'items per page'],
        {'data format type': {'xml', 'json', 'edifact', 'edix12', 'csv', 'flat'}},
    )
    assert [x['text'] for x in cleaned] == ['XML', 'JSON', 'EDIFACT', 'EDIX12', 'CSV', 'FLAT']


def test_doctype_dropdown_filter_keeps_real_non_allowlisted_values_and_dedupes():
    opts = [
        {'text': 'All conditions are satisfied'},
        {'text': 'Any condition is satisfied'},
        {'text': 'Home'},
        {'text': 'All conditions are satisfied'},
    ]
    cleaned = _filter_doctype_dropdown_options('Operation', opts, ['home', 'bizlink'], {})
    assert [x['text'] for x in cleaned] == ['All conditions are satisfied', 'Any condition is satisfied']


def test_deep_profile_parser_extracts_details_endpoint_payload():
    from hip_id_agent.doctype_kb import normalize_doctype_deep_profile

    payload = {
        'documentTypeDetail': {
            'documentTypeId': 10483,
            'documentTypeName': 'XML_SHIPMENT_NOTICE_10_UHAUL_OB',
            'documentTypeVersion': '1.0',
            'transactionType': '856',
            'dataFormatType': 'XML',
            'validationType': 'Structure',
            'description': 'ASN document type',
            'documentIdentifier': {
                'operation': 'All conditions are satisfied',
                'rows': [{'derivedFrom': 'TRANSACTION_ROOT_ELEMENT', 'value': 'ShipmentNotice'}],
            },
            'rootElement': 'ShipmentNotice',
            'documentVersion': '1.0',
            'schemaFileName': 'ShipmentNotice.xsd',
            'attributes': [
                {'attributeName': 'Receiver', 'derivedFrom': 'ELEMENT_IN_PAYLOAD', 'usage': ['Logging', 'Routing'], 'expression': '/ShipmentNotice/Header/Receiver'}
            ],
        },
        'flowDetail': [{'flowId': 1}],
        'ruleDetail': [{'ruleId': 2}],
        'tpDetail': [],
    }
    profile = normalize_doctype_deep_profile(payload, source_url='https://developer.dell.com/inaas-gateway/hipService-svc/api/document-type/10483/details')
    assert profile['document_type_id'] == '10483'
    assert profile['document_identifier'] == 'ShipmentNotice'
    assert profile['document_identifier_operation'] == 'All conditions are satisfied'
    assert profile['root_element'] == 'ShipmentNotice'
    assert profile['schema_file'] == 'ShipmentNotice.xsd'
    assert profile['attribute_count'] == 1
    assert profile['attributes'][0]['attribute_name'] == 'Receiver'
    assert profile['related_usage']['flowDetail']['count'] == 1


def test_deep_profile_applied_to_inventory_record():
    from hip_id_agent.doctype_kb import _apply_deep_profile_to_record

    row = {'document_type_id': '10483', 'document_type_name': 'XML_SHIPMENT_NOTICE_10_UHAUL_OB', 'document_type_version': '1.0'}
    profile = {
        'document_type_id': '10483',
        'document_type_name': 'XML_SHIPMENT_NOTICE_10_UHAUL_OB',
        'document_identifier': 'ShipmentNotice',
        'root_element': 'ShipmentNotice',
        'schema_file': 'ShipmentNotice.xsd',
        'attributes': [{'attribute_name': 'Receiver'}],
        'document_identifier_rows': [{'derived_from': 'TRANSACTION_ROOT_ELEMENT', 'value': 'ShipmentNotice'}],
        'source_url': '/api/document-type/10483/details',
        'deep_profile_completeness_score': 10,
    }
    out = _apply_deep_profile_to_record(row, profile)
    assert out['deep_profile_status'] == 'captured'
    assert out['attribute_count'] == 1
    assert out['document_identifier'] == 'ShipmentNotice'
    assert out['deep_profile']['schema_file'] == 'ShipmentNotice.xsd'


def test_document_identifier_attribute_list_is_normalized():
    from hip_id_agent.doctype_kb import normalize_doctype_deep_profile

    payload = {
        'id': 88,
        'name': 'Abbvie_SRC_DocType',
        'dataFormatType': 'EDIX12',
        'transactionType': '850',
        'version': 3.0,
        'validationType': 'Structure',
        'documentIdentifier': {
            'attributeList': [
                {'derivedFrom': 'ELEMENT_IN_PAYLOAD', 'expression': 'ISA*06', 'value': 'ABBVIE'},
                {'derivedFrom': 'ELEMENT_IN_PAYLOAD', 'expression': 'ISA*08', 'value': '114315195DMC'},
            ],
            'operator': 'ONE',
        },
        'attributes': [],
    }
    profile = normalize_doctype_deep_profile(payload, source_url='/api/document-type/88/details')
    assert profile['document_identifier'] == 'ABBVIE'
    assert profile['document_identifier_operation'] == 'ONE'
    assert profile['document_identifier_derived_from'] == 'ELEMENT_IN_PAYLOAD'
    assert len(profile['document_identifier_rows']) == 2
    assert profile['document_identifier_rows'][0]['expression'] == 'ISA*06'
