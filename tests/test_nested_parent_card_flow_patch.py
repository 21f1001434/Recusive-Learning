from hip_id_agent.config import AppConfig
from hip_id_agent.id_extractor import StrictIDValidator
from hip_id_agent.models import IDCandidate
from hip_id_agent.page_explorer import PageExplorer


def test_card_discovery_selectors_include_dell_dds_cards_and_custom_cards():
    explorer = PageExplorer(AppConfig())
    # Read implementation strings as a regression guard for the live Dell UI shown in evidence.
    source = explorer._discover_entity_cards.__code__.co_consts
    joined = "\n".join(str(x) for x in source)
    assert "dds-card" in joined
    assert ".customCard" in joined
    assert ".existing_domain" in joined


def test_nested_discovery_is_preferred_when_exploration_enabled_by_config():
    cfg = AppConfig()
    assert cfg.exploration.prefer_nested_discovery is True


def test_validator_does_not_accept_system_row_only_matching_aic_prefix():
    validator = StrictIDValidator()
    candidate = IDCandidate(
        target_type="system",
        candidate_id="108",
        candidate_name="AIC - GSCM",
        source_type="network",
        source_url="https://developer.dell.com/hybrid-integrations/bizlink/system",
        endpoint_url="https://developer.dell.com/inaas-gateway/hipSystemsAuthService-svc/api/systems-partners/systems",
        field_path="response[4].id",
        surrounding_text='{"id":108,"systemName":"AIC - GSCM","domainId":null}',
        matched_query="AIC-DCE",
        confidence=0.35,
        evidence_reasons=[],
        rejection_reasons=[],
        raw_excerpt={},
    )
    result = validator.validate_candidate(candidate, query="AIC-DCE", target_type="system")
    assert not result.accepted
    assert result.confidence < 0.90


def test_validator_accepts_exact_nested_domain_response_for_system():
    validator = StrictIDValidator()
    candidate = IDCandidate(
        target_type="system",
        candidate_id="dce-domain-id-001",
        candidate_name="AIC-DCE",
        source_type="network",
        source_url="https://developer.dell.com/hybrid-integrations/bizlink/system",
        endpoint_url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts/customer-experience/domains",
        field_path="response[0].domainId",
        surrounding_text='{"domainId":"dce-domain-id-001","domainName":"AIC-DCE"}',
        matched_query="AIC-DCE",
        confidence=0.35,
        evidence_reasons=[],
        rejection_reasons=[],
        raw_excerpt={},
    )
    result = validator.validate_candidate(candidate, query="AIC-DCE", target_type="system")
    assert result.accepted
    assert result.id_value == "dce-domain-id-001"
