from pathlib import Path
from hip_id_agent.transport_profile_kb import (
    TRANSPORT_PROFILES_URL,
    extract_transport_profile_records_from_payload,
    extract_transport_profile_deep_profiles_from_payload,
    _candidate_transport_profile_deep_profile_urls,
    _is_transport_profile_summary_endpoint,
    _is_full_transport_profile_deep_profile,
)
from hip_id_agent.cli import app


def test_transport_profile_url_and_cli_import():
    assert TRANSPORT_PROFILES_URL.endswith('/transportprofiles')
    names = {cmd.name for cmd in app.registered_commands}
    assert 'discover-transport-profile-kb' in names


def test_transport_profile_summary_endpoint_variants():
    assert _is_transport_profile_summary_endpoint('/inaas-gateway/hipService-svc/api/transport-profile/summary')
    assert _is_transport_profile_summary_endpoint('/inaas-gateway/hipService-svc/api/transportprofiles/summary')
    assert _is_transport_profile_summary_endpoint('/inaas-gateway/hipService-svc/api/transport_profile/summary?page=1')


def test_extract_transport_profile_record_from_summary_payload():
    payload = {
        'content': [{
            'transportProfileId': 123,
            'transportProfileName': 'UHAUL_SFTP_TP',
            'transportProfileVersion': '1',
            'interfaceType': 'SFTP',
            'protocol': 'SFTP-HAFT',
            'documentTypeName': 'XML_DellAutoASN_10_U-HAUL_ANS_IB',
            'environment': ['DEV', 'TEST1']
        }]
    }
    rows = extract_transport_profile_records_from_payload(payload, source_url='/api/transport-profile/summary')
    assert len(rows) == 1
    row = rows[0]
    assert row['transport_profile_id'] == '123'
    assert row['transport_profile_name'] == 'UHAUL_SFTP_TP'
    assert row['interface_type'] == 'SFTP'
    assert row['transport_protocol'] == 'SFTP-HAFT'


def test_deep_profile_requires_transport_profile_details_not_summary_only():
    shallow = {'transport_profile_name': 'ONLY_NAME'}
    assert not _is_full_transport_profile_deep_profile(shallow)
    deep = {
        'transport_profile_id': '123',
        'transport_profile_name': 'UHAUL_SFTP_TP',
        'interface_type': 'SFTP',
        'transport_protocol': 'SFTP-HAFT',
        'parameters': [{'name': 'host', 'value': 'dummy'}],
        'parameter_count': 1,
    }
    assert _is_full_transport_profile_deep_profile(deep)


def test_extract_transport_profile_deep_profile_from_details_payload():
    payload = {
        'transportProfileDetails': {
            'transportProfileId': 123,
            'transportProfileName': 'UHAUL_SFTP_TP',
            'transportProfileVersion': '2',
            'status': 'Enable',
            'interfaceDetails': {
                'interfaceType': 'SFTP',
                'protocol': 'SFTP-HAFT',
                'parameters': [
                    {'name': 'host', 'value': 'sftp.example.com'},
                    {'name': 'port', 'value': '22'},
                ]
            },
            'documentTypeDetails': {
                'documentTypeId': 10483,
                'documentTypeName': 'XML_DellAutoASN_10_U-HAUL_ANS_IB',
                'documentTypeVersion': '1'
            }
        }
    }
    profiles = extract_transport_profile_deep_profiles_from_payload(payload, source_url='/api/transport-profile/123/details')
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile['transport_profile_id'] == '123'
    assert profile['parameter_count'] >= 2
    assert profile['interface_details']
    assert profile['document_type_details']
    assert _is_full_transport_profile_deep_profile(profile)


def test_detail_candidate_urls_include_expected_variants():
    urls = _candidate_transport_profile_deep_profile_urls({'transport_profile_id': '123', 'transport_profile_name': 'UHAUL_SFTP_TP', 'transport_profile_version': '1'}, [])
    joined = '\n'.join(urls)
    assert '/api/transport-profile/123/details' in joined
    assert 'transportProfileId=123' in joined
    name_urls = _candidate_transport_profile_deep_profile_urls({'transport_profile_name': 'UHAUL_SFTP_TP', 'transport_profile_version': '1'}, [])
    assert 'transportProfileName=UHAUL_SFTP_TP' in '\n'.join(name_urls)


def test_transport_profile_ui_rows_normalize_visible_grid_fallback():
    from hip_id_agent.transport_profile_kb import _transport_profile_records_from_ui_rows
    ui_rows = [{
        'transport_profile_name': 'a-as2-receiver',
        'usage': 'Sender',
        'interface_type': 'HTTPS-AS2',
        'system_name': 'dce-test',
        'text': 'a-as2-receiver Sender HTTPS-AS2 dce-test',
        'selector': 'table tbody tr:nth-of-type(1)',
        'cells': ['a-as2-receiver', 'Sender', 'HTTPS-AS2', 'dce-test']
    }]
    records = _transport_profile_records_from_ui_rows(ui_rows)
    assert len(records) == 1
    rec = records[0]
    assert rec['transport_profile_name'] == 'a-as2-receiver'
    assert rec['usage'] == 'Sender'
    assert rec['interface_type'] == 'HTTPS-AS2'
    assert rec['source'] == 'ui_visible_grid_fallback'


def test_transport_profile_ui_rows_strip_expand_prefix():
    from hip_id_agent.transport_profile_kb import _transport_profile_records_from_ui_rows
    records = _transport_profile_records_from_ui_rows([{
        'transport_profile_name': 'Expand row DELL_PREMIER_B2B_ORDER_REQUEST',
        'usage': 'Sender',
        'interface_type': 'HTTPS',
        'text': 'Expand row DELL_PREMIER_B2B_ORDER_REQUEST Sender HTTPS dce-test',
        'cells': ['Expand row DELL_PREMIER_B2B_ORDER_REQUEST', 'Sender', 'HTTPS']
    }])
    assert records[0]['transport_profile_name'] == 'DELL_PREMIER_B2B_ORDER_REQUEST'


def test_transport_profile_pagination_text_parser_for_full_inventory():
    from hip_id_agent.transport_profile_kb import _parse_transport_profile_pagination_info
    info = _parse_transport_profile_pagination_info('Items per page 10 20 40 60 80 100 1 - 10 of 659 items Previous Page of 66 Next')
    assert info['start'] == 1
    assert info['end'] == 10
    assert info['total'] == 659
    assert info['page_size'] == 10
    assert info['total_pages'] == 66


def test_transport_profile_add_form_detector_js_name_fixed():
    # Regression guard: the generated JS used to contain invalid variable name
    # `hasTransport ProfileName`, causing Add-form detection to always fail.
    import inspect
    from hip_id_agent.transport_profile_kb import _looks_like_transport_profile_add_form
    source = inspect.getsource(_looks_like_transport_profile_add_form)
    assert 'hasTransport ProfileName' not in source
    assert 'hasTransportProfileName' in source


def test_transport_profile_candidates_include_real_details_in_order_endpoint():
    urls = _candidate_transport_profile_deep_profile_urls({
        'transport_profile_name': 'SFTP_U-HAUL_ASN_PC_TGT_OB',
        'transport_profile_version': '1.0',
        'available_environments': ['DEV']
    }, [])
    joined = '\n'.join(urls)
    assert '/api/transport-profiles/details-in-order?' in joined
    assert 'transportProfileName=SFTP_U-HAUL_ASN_PC_TGT_OB' in joined
    assert 'hipEnvironment=DEV' in joined


def test_transport_profile_api_url_like_matches_hyphen_plural():
    from hip_id_agent.transport_profile_kb import _is_transport_profile_api_url_like
    assert _is_transport_profile_api_url_like('/inaas-gateway/hipService-svc/api/transport-profiles/details-in-order')
    assert _is_transport_profile_api_url_like('/inaas-gateway/hipService-svc/api/transport_profile/123/details')


def test_transport_profile_form_only_plans_partner_name_before_environment():
    import inspect
    from hip_id_agent import transport_profile_kb
    source = inspect.getsource(transport_profile_kb._advance_transport_profile_add_wizard)
    assert '("partner", "name")' in source
    assert 'dce-test-partner' in source
    assert source.index('("system", "type")') < source.index('("partner", "name")') < source.index('("available", "environment")')


def test_transport_profile_form_only_has_scoped_progression_helper():
    import inspect
    from hip_id_agent import transport_profile_kb
    source = inspect.getsource(transport_profile_kb._click_transport_profile_wizard_progression)
    assert 'Create\\s+Transport\\s+Profile' in source
    assert 'save|create|submit|delete' in source
    assert 'app-generic-drawer' in source
    assert 'querySelectorAll' in source


def test_transport_profile_guess_field_key_maps_partner_name():
    from hip_id_agent.transport_profile_kb import guess_field_key, build_dummy_fill_values
    assert guess_field_key('Partner Name', {}) == 'partner_name'
    vals = build_dummy_fill_values({})
    assert vals['partner_name'] == 'dce-test-partner'

def test_transport_profile_combobox_select_does_not_direct_set_fallback():
    import inspect
    from hip_id_agent import transport_profile_kb
    source = inspect.getsource(transport_profile_kb._select_transport_profile_combobox_option)
    assert 'do not fall back to directly setting arbitrary combobox text' in source
    assert 'return await _set_control_value(page, selector, option_text)' not in source
    assert "dds-pagination" in source
    assert "dds-table" in source


def test_transport_profile_dummy_fill_refinds_controls_after_rerender():
    source = Path('hip_id_agent/transport_profile_kb.py').read_text(encoding='utf-8')
    assert '_find_fresh_control(fresh_rows' in source
    assert 'preserve_combobox_keys' in source
    assert 'Transport Profile Add form closed during dummy fill' in source
    assert 'SFTP HAFT' in source


def test_transport_profile_combobox_uses_real_playwright_option_click_not_dom_click():
    from pathlib import Path
    source = Path("hip_id_agent/transport_profile_kb.py").read_text(encoding="utf-8")
    assert "Use real pointer events" in source
    assert "button[role=option]:has-text" in source
    assert "HTMLElement.click" in source
    assert "state.get(\"selected\")" in source


def test_transport_profile_wizard_retries_when_system_type_does_not_reveal_partner_name():
    from pathlib import Path
    source = Path("hip_id_agent/transport_profile_kb.py").read_text(encoding="utf-8")
    assert "critical_reveal" in source
    assert "treat it as unselected and retry" in source
    assert "seen_selectors.add(attempt_key)" in source


def test_transport_profile_add_form_detector_accepts_expanded_field_surface():
    import inspect
    from hip_id_agent.transport_profile_kb import _looks_like_transport_profile_add_form
    source = inspect.getsource(_looks_like_transport_profile_add_form)
    assert "Profile\\\\s+Usage" in source or "Profile\\s+Usage" in source
    assert "Deployment\\\\s+Group" in source or "Deployment\\s+Group" in source
    assert "File\\\\s+Filtering\\\\s+Pattern" in source or "File\\s+Filtering\\s+Pattern" in source
    assert "tpFieldScore >= 3" in source


def test_transport_profile_dummy_fill_probes_controls_before_declaring_closed():
    source = Path("hip_id_agent/transport_profile_kb.py").read_text(encoding="utf-8")
    assert "probe_rows = await _evaluate_controls(page)" in source
    assert "_controls_look_like_transport_profile_add_form_controls(probe_rows)" in source
    assert "final_form_visible" in source


def test_transport_profile_combobox_does_not_accept_wrong_default_value():
    source = Path("hip_id_agent/transport_profile_kb.py").read_text(encoding="utf-8")
    assert "must not satisfy a requested DEV selection" in source
    assert "selected: !!(valueMatch || optionSelected)" in source
    assert "!expanded && value" not in source

def test_transport_profile_dropdown_collection_noninvasive_before_dummy_fill():
    from pathlib import Path
    source = Path("hip_id_agent/transport_profile_kb.py").read_text(encoding="utf-8")
    assert "_collect_dropdown_options_noninvasive" in source
    assert "no-click-before-dummy-fill" in source
    assert "dropdown probing + Escape can close the drawer" in source
    assert "dropdowns = _collect_dropdown_options_noninvasive(controls)" in source


def test_transport_profile_invasive_dropdown_scan_runs_after_no_save_snapshot():
    from pathlib import Path
    source = Path("hip_id_agent/transport_profile_kb.py").read_text(encoding="utf-8")
    assert "after-fill no-save snapshot has already" in source
    assert "post_fill_dropdowns = await _collect_dropdown_options(page, post_fill_controls)" in source
    assert source.index("transport_profile_add_form_after_dummy_fill_no_save") < source.index("post_fill_dropdowns = await _collect_dropdown_options")
