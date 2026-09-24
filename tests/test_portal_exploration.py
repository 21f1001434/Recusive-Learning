from hip_id_agent.config import AppConfig
from hip_id_agent.id_extractor import IDExtractor
from hip_id_agent.models import NetworkTabEvent
from hip_id_agent.page_explorer import PageExplorer


def test_exploration_safe_action_filter_skips_destructive_actions():
    cfg = AppConfig()
    explorer = PageExplorer(cfg)
    assert explorer._is_safe_exploration_action("View Partner") is True
    assert explorer._is_safe_exploration_action("Show Partners") is True
    assert explorer._is_safe_exploration_action("Edit Partner") is True  # opened read-only; Update/Save skipped
    assert explorer._is_safe_exploration_action("Delete Partner") is False
    assert explorer._is_safe_exploration_action("Update") is False
    assert explorer._is_safe_exploration_action("Add Partner") is False


def test_exploration_config_defaults_are_read_only():
    cfg = AppConfig()
    assert cfg.exploration.enabled is False
    assert cfg.exploration.allow_unsafe_clicks is False
    assert cfg.exploration.max_total_actions > 0


def test_bulk_discovery_saves_generic_id_from_partner_endpoint_with_name():
    ev = NetworkTabEvent(
        request_id="r-partners",
        url="https://developer.dell.com/authz/accounts/partners",
        method="GET",
        status=200,
        response_body_redacted=[{"id": "ff0341d2-018b-4489-8d23-669000000001", "name": "AS2TEST", "partnerIdentifier": "DUNS Number", "partnerIdentifierValue": "AMAZONJP"}],
    )
    rows = IDExtractor().extract_all_from_network("partner", [ev])
    assert rows
    assert rows[0].object_id.startswith("ff0341d2")
    assert rows[0].name == "AS2TEST"


def test_bulk_discovery_saves_generic_id_from_system_endpoint_with_system_name():
    ev = NetworkTabEvent(
        request_id="r-systems",
        url="https://developer.dell.com/authz/domains/systems",
        method="GET",
        status=200,
        response_body_redacted=[{"id": "7f8fd919-c696-4b01-9ad8-395000000001", "systemName": "AIC-DCE", "businessContactName": "Sangeeta"}],
    )
    rows = IDExtractor().extract_all_from_network("system", [ev])
    assert rows
    assert rows[0].object_id.startswith("7f8fd919")
    assert rows[0].name == "AIC-DCE"


def test_nested_discovery_action_filter_targets_child_lists():
    cfg = AppConfig()
    explorer = PageExplorer(cfg)
    assert explorer._is_nested_discovery_action("Show Partner(s)", "partner") is True
    assert explorer._is_nested_discovery_action("Show Partners", "partner") is True
    assert explorer._is_nested_discovery_action("View Domain", "system") is True
    assert explorer._is_nested_discovery_action("View Domains", "system") is True
    assert explorer._is_nested_discovery_action("Delete Partner", "partner") is False
    assert explorer._is_nested_discovery_action("View Domain", "partner") is False
    assert explorer._is_nested_discovery_action("Show Partners", "system") is False
