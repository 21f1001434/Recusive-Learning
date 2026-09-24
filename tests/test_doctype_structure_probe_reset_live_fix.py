from __future__ import annotations

import asyncio
from pathlib import Path

import hip_id_agent.doctype_kb as doctype_kb


class _Browser:
    def __init__(self):
        self.logged = []

    async def log_automation_click(self, **kwargs):
        self.logged.append(kwargs)


class _Page:
    def __init__(self, *, form_open: bool = True):
        self.form_open = form_open
        self.waits = []

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)


class _Add:
    pass


def test_force_reopen_discards_active_drawer_before_any_same_route_navigation(monkeypatch, tmp_path: Path):
    page = _Page(form_open=True)
    browser = _Browser()
    calls = {"discard": 0, "navigate": 0, "open": 0}

    async def inspect(_page):
        return {
            "pass": bool(page.form_open),
            "reason": "ok" if page.form_open else "listing",
            "root_selector": "#drawer" if page.form_open else "",
        }

    async def dismiss(_page):
        return {"attempted": False, "dismissed": False}

    async def discard(*_args, **_kwargs):
        calls["discard"] += 1
        page.form_open = False
        return {"pass": True, "reason": "drawer_discarded_and_listing_restored"}

    async def find_add(_page):
        return _Add() if not page.form_open else None

    async def navigate(*_args, **_kwargs):
        calls["navigate"] += 1
        raise AssertionError("same-route navigation must not run while/resetting an active drawer")

    async def click_add(*_args, **_kwargs):
        calls["open"] += 1
        page.form_open = True
        return True

    monkeypatch.setattr(doctype_kb, "inspect_doctype_create_surface", inspect)
    monkeypatch.setattr(doctype_kb, "_dismiss_safe_doctype_overlays", dismiss)
    monkeypatch.setattr(doctype_kb, "_discard_doctype_structure_probe_surface", discard)
    monkeypatch.setattr(doctype_kb, "_find_add_button", find_add)
    monkeypatch.setattr(doctype_kb, "_force_open_doctypes_listing", navigate)
    monkeypatch.setattr(doctype_kb, "_click_add_doctype_with_overlay_recovery", click_add)

    result = asyncio.run(
        doctype_kb._ensure_doctype_create_surface(
            page,
            browser,
            doctypes_url=doctype_kb.DOCTYPES_URL,
            kb_dir=tmp_path,
            warnings=[],
            stage="structure_learned_clean_target_form",
            force_reopen=True,
        )
    )

    assert result["pass"] is True
    assert result["reopened"] is True
    assert calls == {"discard": 1, "navigate": 0, "open": 1}


def test_structure_probe_discard_retries_when_first_close_is_swallowed(monkeypatch, tmp_path: Path):
    page = _Page(form_open=True)
    browser = _Browser()
    close_calls = {"count": 0}
    dropdown_settles = []

    class CloseLocator:
        async def is_visible(self, timeout=0):
            return True

        async def is_enabled(self, timeout=0):
            return True

        async def click(self, timeout=0):
            close_calls["count"] += 1
            # Live Dell behavior: the first pointer interaction can be consumed by
            # a still-active DDS dropdown. The rebound second click closes drawer.
            if close_calls["count"] >= 2:
                page.form_open = False

    class Candidates:
        async def count(self):
            return 1

        def nth(self, _index):
            return CloseLocator()

    class Root:
        def locator(self, _selector):
            return Candidates()

    async def inspect(_page):
        return {
            "pass": bool(page.form_open),
            "reason": "ok" if page.form_open else "listing",
            "root_selector": "#drawer" if page.form_open else "",
        }

    async def get_root(_page):
        return Root() if page.form_open else None

    async def find_add(_page):
        return _Add() if not page.form_open else None

    async def settle(_page, phase):
        dropdown_settles.append(phase)
        return {"closed": True}

    async def confirm(_page):
        return {"detected": False, "confirmed": False}

    async def save(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(doctype_kb, "inspect_doctype_create_surface", inspect)
    monkeypatch.setattr(doctype_kb, "get_doctype_create_root", get_root)
    monkeypatch.setattr(doctype_kb, "_find_add_button", find_add)
    monkeypatch.setattr(doctype_kb, "close_open_dropdown", settle)
    monkeypatch.setattr(doctype_kb, "_confirm_doctype_discard_dialog", confirm)
    monkeypatch.setattr(doctype_kb, "_save_doctype_surface_evidence", save)

    warnings = []
    result = asyncio.run(
        doctype_kb._discard_doctype_structure_probe_surface(
            page,
            browser,
            kb_dir=tmp_path,
            warnings=warnings,
            stage="structure_learned_clean_target_form",
        )
    )

    assert result["pass"] is True
    assert result["reason"] == "drawer_discarded_and_listing_restored"
    assert close_calls["count"] == 2
    assert dropdown_settles == ["source_document_type", "source_document_type"]
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["surface_after_close"] is True
    assert result["attempts"][1]["listing_toolbar_visible"] is True
    assert warnings == []
    assert len(browser.logged) == 2


def test_structure_probe_discard_is_fail_closed_when_listing_never_returns(monkeypatch, tmp_path: Path):
    page = _Page(form_open=True)
    browser = _Browser()

    class CloseLocator:
        async def is_visible(self, timeout=0):
            return True

        async def is_enabled(self, timeout=0):
            return True

        async def click(self, timeout=0):
            return None

    class Candidates:
        async def count(self):
            return 1

        def nth(self, _index):
            return CloseLocator()

    class Root:
        def locator(self, _selector):
            return Candidates()

    async def inspect(_page):
        return {"pass": True, "reason": "ok", "root_selector": "#drawer"}

    async def get_root(_page):
        return Root()

    async def find_add(_page):
        return None

    async def settle(*_args, **_kwargs):
        return {}

    async def confirm(_page):
        return {"detected": False, "confirmed": False}

    async def save(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(doctype_kb, "inspect_doctype_create_surface", inspect)
    monkeypatch.setattr(doctype_kb, "get_doctype_create_root", get_root)
    monkeypatch.setattr(doctype_kb, "_find_add_button", find_add)
    monkeypatch.setattr(doctype_kb, "close_open_dropdown", settle)
    monkeypatch.setattr(doctype_kb, "_confirm_doctype_discard_dialog", confirm)
    monkeypatch.setattr(doctype_kb, "_save_doctype_surface_evidence", save)

    warnings = []
    result = asyncio.run(
        doctype_kb._discard_doctype_structure_probe_surface(
            page,
            browser,
            kb_dir=tmp_path,
            warnings=warnings,
            stage="structure_learned_clean_target_form",
        )
    )

    assert result["pass"] is False
    assert result["reason"] == "drawer_discard_not_committed"
    assert len(result["attempts"]) == 2
    assert any("stopped before entering customer values" in warning for warning in warnings)
