from hip_id_agent.config import AppConfig
from hip_id_agent.page_explorer import PageExplorer


def test_system_canonical_url_is_exact_bizlink_system():
    explorer = PageExplorer(AppConfig())
    assert explorer._canonical_url_for_area("system") == "https://developer.dell.com/hybrid-integrations/bizlink/system"
    assert explorer._canonical_url_for_area("partner") == "https://developer.dell.com/hybrid-integrations/bizlink/partner"


def test_relative_candidate_resolves_from_developer_origin_not_partner_page():
    explorer = PageExplorer(AppConfig())
    assert explorer._resolve_candidate_url("/hybrid-integrations/bizlink/system") == "https://developer.dell.com/hybrid-integrations/bizlink/system"
    assert explorer._resolve_candidate_url("/system") == "https://developer.dell.com/system"
    assert "/bizlink/partner/system" not in explorer._resolve_candidate_url("/system")


def test_canonical_system_url_match_is_authoritative_even_if_text_shared():
    explorer = PageExplorer(AppConfig())
    assert explorer._url_matches_area("https://developer.dell.com/hybrid-integrations/bizlink/system", "system") is True
    assert explorer._url_matches_area("https://developer.dell.com/hybrid-integrations/bizlink/partner", "system") is False
    assert explorer._url_matches_area("https://developer.dell.com/hybrid-integrations/bizlink/partner/system", "system") is False
    assert explorer._url_matches_area("https://developer.dell.com/404", "system") is False


def test_unique_direct_paths_puts_canonical_system_first():
    explorer = PageExplorer(AppConfig())
    paths = explorer._unique_direct_paths("system", ["/system", "https://developer.dell.com/hybrid-integrations/bizlink/system"])
    assert paths[0] == "https://developer.dell.com/hybrid-integrations/bizlink/system"
    assert "https://developer.dell.com/hybrid-integrations/bizlink/partner/system" not in paths
