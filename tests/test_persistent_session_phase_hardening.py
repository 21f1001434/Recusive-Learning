from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession, browser_session_scope
from hip_id_agent.config import AppConfig


class _Keyboard:
    async def press(self, key):
        return None


class _Body:
    def __init__(self, text: str):
        self._text = text

    async def inner_text(self, timeout=0):
        return self._text


class _Locator:
    def __init__(self, count=0, visible=False, text=""):
        self._count = count
        self._visible = visible
        self._text = text
        self.first = self

    def filter(self, **kwargs):
        return self

    async def count(self):
        return self._count

    async def is_visible(self, timeout=0):
        return self._visible

    async def click(self, timeout=0):
        return None

    async def evaluate(self, script):
        return None

    async def inner_text(self, timeout=0):
        return self._text

    def nth(self, index):
        return self


class _Page:
    def __init__(self, url="https://developer.dell.com/hybrid-integrations/securelink/datamaps"):
        self.url = url
        self.keyboard = _Keyboard()
        self.goto_calls = []

    def is_closed(self):
        return False

    def locator(self, selector):
        if selector == "body":
            return _Body("Developer SecureLink Data Map Map Identifier Mapping")
        return _Locator()

    async def evaluate(self, script, *args):
        if "document.readyState" in script:
            return "complete"
        return None

    async def wait_for_timeout(self, ms):
        return None

    async def wait_for_load_state(self, state, timeout=None):
        return None

    async def screenshot(self, *args, **kwargs):
        return b"png"


@pytest.mark.asyncio
async def test_phase_scope_rebinds_evidence_directory_and_keeps_session_open(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path / "root")
    session.page = _Page()
    session._closed = False

    async with browser_session_scope(
        session.config,
        tmp_path / "source_document_type",
        existing=session,
        phase_name="source_document_type",
    ):
        assert session.run_dir == tmp_path / "source_document_type"
        assert session.actions_dir == tmp_path / "source_document_type" / "actions"
        assert session._active_phase_name == "source_document_type"

    assert session._closed is False
    assert session._borrow_count == 1


@pytest.mark.asyncio
async def test_authenticated_same_module_does_not_navigate_or_prompt(tmp_path: Path, monkeypatch):
    session = BrowserSession(AppConfig(), tmp_path)
    session.page = _Page("https://developer.dell.com/hybrid-integrations/securelink/datamaps")
    session._authenticated_once = True

    async def logged_in(page):
        return True

    async def usable(url):
        return True

    async def gate(url, **kwargs):
        return {"pass": True, "attempt_count": 1}

    monkeypatch.setattr(session, "_looks_logged_in", logged_in)
    monkeypatch.setattr(session, "_navigation_page_is_usable", usable)
    monkeypatch.setattr(session, "_verify_dual_mcp_same_surface", gate)

    async def must_not_navigate(url):
        raise AssertionError("authenticated same-module session should not navigate")

    monkeypatch.setattr(session, "navigate", must_not_navigate)
    await session.goto_base_and_complete_sso(
        "https://developer.dell.com/hybrid-integrations/securelink/datamaps"
    )

    assert session._sso_prompt_count == 0
    assert session._authenticated_once is True


def test_all_kb_flows_pass_explicit_target_url_to_single_sso_guard():
    expected = {
        "hip_id_agent/datamap_kb.py": "goto_base_and_complete_sso(self.datamaps_url)",
        "hip_id_agent/doctype_kb.py": "goto_base_and_complete_sso(self.doctypes_url)",
        "hip_id_agent/rules_kb.py": "goto_base_and_complete_sso(self.rules_url)",
        "hip_id_agent/transport_profile_kb.py": "goto_base_and_complete_sso(self.transport_profiles_url)",
        "hip_id_agent/bizflow_kb.py": "goto_base_and_complete_sso(self.bizflows_url)",
    }
    root = Path(__file__).resolve().parents[1]
    for rel, needle in expected.items():
        assert needle in (root / rel).read_text(encoding="utf-8")


def test_custom_dds_combobox_avoids_native_select_option_probe():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "dds_control_driver.py").read_text(encoding="utf-8")
    assert 'if tag_name == "select"' in source
    assert "_broker_press" in source
