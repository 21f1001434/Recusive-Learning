from pathlib import Path

from hip_id_agent.knowledge_graph import KnowledgeGraphWriter
from hip_id_agent.models import ClickEvent, ExtractedID, NetworkTabEvent, RunContext, StageResult, utc_now


def test_knowledge_graph_writer_outputs_flow_and_clicks(tmp_path: Path):
    ctx = RunContext(
        run_id="UHAL-TEST",
        customer="UHAL",
        partner_query="UHAL",
        system_query="UHAL-SFTP",
        run_dir=tmp_path,
        screenshots_dir=tmp_path / "screenshots",
    )
    ctx.stage_results.append(StageResult(
        stage="extract_partner_id",
        status="success",
        message="Extracted partner ID P123",
        started_at=utc_now(),
        finished_at=utc_now(),
        screenshots=[str(tmp_path / "screenshots" / "partner.png")],
        extracted_ids=[ExtractedID("partner", "UHAL", "P123", "UHAL", "network_tab_json", 0.95)],
        evidence={"snapshot_url": "https://developer.dell.com/hybrid-integrations/bizlink/partner"},
    ))
    clicks = [ClickEvent(
        timestamp=utc_now(),
        source="automation",
        url="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        action="open_row_detail",
        selector="row a:first",
        text="UHAL",
        tag="a",
        extra={"stage": "extract_partner_id", "after_url": "https://developer.dell.com/detail/P123"},
    )]
    network = [NetworkTabEvent(
        request_id="1",
        url="https://developer.dell.com/api/partner/search",
        method="GET",
        status=200,
        resource_type="XHR",
        stage="extract_partner_id",
        response_body_redacted={"items": [{"partnerName": "UHAL", "partnerId": "P123"}]},
    )]
    paths = KnowledgeGraphWriter(ctx).write(click_events=clicks, network_events=network)
    assert Path(paths["knowledge_graph_json"]).exists()
    assert Path(paths["knowledge_graph_html"]).exists()
    assert Path(paths["click_sequence_json"]).exists()
    text = Path(paths["knowledge_graph_json"]).read_text(encoding="utf-8")
    assert "EXTRACTED_ID" in text
    assert "PERFORMED_CLICK" in text
    assert "OBSERVED_NETWORK_EVENT" in text
