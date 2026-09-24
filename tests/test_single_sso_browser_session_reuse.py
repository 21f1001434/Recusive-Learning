from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession, browser_session_scope
from hip_id_agent.config import AppConfig
from hip_id_agent import dummy_fill_e2e
from hip_id_agent.datamap_kb import DataMapKBFlow
from hip_id_agent.doctype_kb import DocumentTypeKBFlow
from hip_id_agent.rules_kb import RuleKBFlow
from hip_id_agent.transport_profile_kb import TransportProfileKBFlow
from hip_id_agent.bizflow_kb import BizFlowKBFlow


class _FakePage:
    url = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"


@pytest.mark.asyncio
async def test_borrowed_browser_scope_does_not_close_session(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path / "root")
    session.page = _FakePage()
    session._closed = False
    closed = False

    async def _close():
        nonlocal closed
        closed = True

    session.close = _close  # type: ignore[method-assign]
    async with browser_session_scope(
        session.config,
        tmp_path / "source_document_type",
        existing=session,
        phase_name="source_document_type",
    ) as borrowed:
        assert borrowed is session
        assert borrowed.session_id == session.session_id

    assert closed is False
    assert session._borrow_count == 1
    assert (tmp_path / "source_document_type" / "browser_session_reuse.json").is_file()


def test_all_phase_flows_accept_borrowed_browser_session():
    for cls in [DataMapKBFlow, DocumentTypeKBFlow, RuleKBFlow, TransportProfileKBFlow, BizFlowKBFlow]:
        assert "browser_session" in inspect.signature(cls.run).parameters


def test_full_dummy_flow_uses_one_browser_and_passes_it_to_every_phase():
    source = inspect.getsource(dummy_fill_e2e.FullDummyFillE2EFlow.run)
    assert source.count("shared_browser = BrowserSession(") == 1
    # All phase types now share one helper so mid-run SSO expiry can replay only
    # the interrupted phase without duplicating orchestration code.
    assert source.count("browser_session=shared_browser") == 1
    assert "_execute_phase_once" in source
    assert "await shared_browser.close()" in source
    assert "single_persistent_context" in source


def test_registering_new_phase_reenables_log_flush(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path / "root")
    session.page = _FakePage()
    session._logs_flushed = True
    session.register_borrowed_phase(tmp_path / "rule", "rule")
    assert session._logs_flushed is False


def test_sso_prompt_counter_is_session_scoped():
    source = inspect.getsource(BrowserSession.goto_base_and_complete_sso)
    assert "self._sso_prompt_count += 1" in source
    assert "self._authenticated_once = True" in source
