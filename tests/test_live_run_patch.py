import asyncio
from types import SimpleNamespace
from pathlib import Path

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.page_explorer import PageExplorer


class FailureObject:
    error_text = "net::ERR_ABORTED"


class FakeReq:
    def __init__(self, failure):
        self.failure = failure
        self.url = "https://developer.dell.com/fail"
        self.method = "GET"


def test_request_failure_text_handles_string_dict_and_object(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path)
    assert session._request_failure_text(FakeReq("net::ERR_FAILED")) == "net::ERR_FAILED"
    assert session._request_failure_text(FakeReq({"errorText": "net::ERR_CERT"})) == "net::ERR_CERT"
    assert session._request_failure_text(FakeReq(FailureObject())) == "net::ERR_ABORTED"


def test_record_request_failed_never_raises_for_playwright_158_string_failure(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path)
    session._record_request_failed(FakeReq("net::ERR_FAILED"))
    assert session.network_records[-1].error == "net::ERR_FAILED"


class FakeItem:
    def __init__(self, visible=True, enabled=True, y=0):
        self.visible = visible
        self.enabled = enabled
        self.y = y
    async def is_visible(self, timeout=0):
        return self.visible
    async def is_enabled(self, timeout=0):
        return self.enabled
    async def bounding_box(self):
        return {"x": 10, "y": self.y, "width": 200, "height": 30}


class FakeLocatorCollection:
    def __init__(self, items):
        self.items = items
    async def count(self):
        return len(self.items)
    def nth(self, i):
        return self.items[i]


def test_find_visible_enabled_prefers_page_grid_search_over_header_search():
    # Header search appears first/high in the page; BizLink grid search appears later/lower.
    header = FakeItem(y=70)
    grid = FakeItem(y=300)
    result = asyncio.run(PageExplorer(AppConfig())._first_visible_enabled(FakeLocatorCollection([header, grid])))
    assert result is grid


def test_find_visible_enabled_skips_hidden_and_disabled_items():
    hidden = FakeItem(visible=False, enabled=True, y=500)
    disabled = FakeItem(visible=True, enabled=False, y=400)
    grid = FakeItem(visible=True, enabled=True, y=250)
    result = asyncio.run(PageExplorer(AppConfig())._first_visible_enabled(FakeLocatorCollection([hidden, disabled, grid])))
    assert result is grid

from hip_id_agent.id_extractor import IDExtractor
from hip_id_agent.models import NetworkTabEvent


def test_partner_page_account_endpoint_is_not_partner_lookup():
    events = [NetworkTabEvent(
        request_id="r-account",
        url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts",
        method="GET",
        status=200,
        resource_type="XHR",
        response_body_redacted=[{
            "id": "5dde7ac6-8f74-4e68-8f97-08c3cd7fa6ee",
            "accountName": "U-HAUL_PC",
            "description": "U-HAUL",
            "domains": ["uhaul.com"],
        }],
        page_context="https://developer.dell.com/hybrid-integrations/bizlink/partner",
    )]
    best, debug = IDExtractor().extract(
        object_type="partner",
        query="UHAL",
        url="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert best is None


def test_system_lookup_does_not_accept_partner_account_endpoint_even_with_matching_name():
    events = [NetworkTabEvent(
        request_id="r-account",
        url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts",
        method="GET",
        status=200,
        resource_type="XHR",
        response_body_redacted=[{
            "id": "5dde7ac6-8f74-4e68-8f97-08c3cd7fa6ee",
            "accountName": "U-HAUL_PC",
            "description": "U-HAUL",
        }],
        page_context="https://developer.dell.com/hybrid-integrations/bizlink/partner",
    )]
    best, debug = IDExtractor().extract(
        object_type="system",
        query="UHAL",
        url="https://developer.dell.com/hybrid-integrations/bizlink/system",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert best is None

class FakeEvaluatedItem(FakeItem):
    def __init__(self, fillable=True, **kwargs):
        super().__init__(**kwargs)
        self.fillable = fillable
    async def evaluate(self, script, timeout=0):
        return self.fillable


def test_find_visible_enabled_skips_search_button_and_uses_fillable_input():
    # The live BizLink run resolved get_by_label("Search") to the DDS submit button.
    # That element is visible/enabled and lower on the page, but it is not fillable.
    search_button = FakeEvaluatedItem(fillable=False, y=500)
    page_input = FakeEvaluatedItem(fillable=True, y=260)
    result = asyncio.run(PageExplorer(AppConfig())._first_visible_enabled(FakeLocatorCollection([page_input, search_button])))
    assert result is page_input
