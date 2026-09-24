from hip_id_agent.config import AppConfig
from hip_id_agent.page_explorer import PageExplorer


def test_visible_body_text_fallback_extracts_partner_account_cards():
    explorer = PageExplorer(AppConfig())
    body = '''Developer
Manage Account
Filter
Gmail Account
Description:
Gmail Account
gmail.com
gcbs-test-account
Description:
gcbs-test-account - this is for GCBS Testing purpose
gcbs.com
Items per page
1 - 20 of 235 items
Next
'''
    cards = explorer._fallback_cards_from_body_text(body)
    titles = [c["title"] for c in cards]
    assert "Gmail Account" in titles
    assert "gcbs-test-account" in titles
    gmail = next(c for c in cards if c["title"] == "Gmail Account")
    assert any(a["label"] == "Show Partner(s)" for a in gmail["buttons"])


def test_visible_body_text_fallback_extracts_system_domain_cards():
    explorer = PageExplorer(AppConfig())
    body = '''Developer
Manage Domain
Filter
Customer Experience (CX)
PT281657
Customer and Services IT (CSI)
PT281646
Enterprise Finance Systems
PT281641
Supply Chain
PT281647
Copyright ©2026 Dell Inc.
'''
    cards = explorer._fallback_cards_from_body_text(body)
    titles = [c["title"] for c in cards]
    assert "Customer Experience (CX)" in titles
    assert "Supply Chain" in titles
    cx = next(c for c in cards if c["title"] == "Customer Experience (CX)")
    assert any(a["label"] == "View Domain" for a in cx["buttons"])
