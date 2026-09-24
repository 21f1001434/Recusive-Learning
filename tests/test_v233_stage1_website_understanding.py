from __future__ import annotations

from pathlib import Path

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.website_understanding import WebsiteUnderstandingEngine


class _FakeCDP:
    async def send(self, method, params):
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "obj-1"}}
        if method == "DOMDebugger.getEventListeners":
            return {"listeners": [
                {"type": "click", "useCapture": False, "passive": False, "once": False},
                {"type": "change", "useCapture": True, "passive": False, "once": False},
            ]}
        return {}

    async def detach(self):
        return None


class _FakeContext:
    async def new_cdp_session(self, page):
        return _FakeCDP()


class _FakePage:
    context = _FakeContext()

    async def evaluate(self, script, arg=None):
        if isinstance(arg, str):
            return None
        return {
            "url": "https://developer.dell.com/hybrid-integrations/bizexchange/bizflows",
            "title": "Create BizFlow",
            "frameworkHints": {"angular": True, "dds": True, "react": False},
            "viewport": {"width": 1600, "height": 900, "devicePixelRatio": 1.25},
            "activeSurface": {"tag": "dds-drawer", "role": "dialog", "ariaModal": "true", "label": "Create BizFlow", "score": 1800},
            "surfaces": [{"tag": "dds-drawer", "role": "dialog", "ariaModal": "true", "label": "Create BizFlow", "score": 1800}],
            "controls": [
                {
                    "index": 0, "inspectionToken": "hipinspect-x-0", "tag": "input", "role": "combobox",
                    "type": "text", "label": "Process Step", "section": "Configure Routing", "formControlName": "processStep",
                    "visible": True, "inActiveSurface": True, "required": True, "disabled": False, "readOnly": False,
                    "expanded": "true", "checked": False, "hasValue": False, "valueLength": 0,
                    "ownedIds": ["process-step-list"], "ownedSurfaceVisible": True, "optionCount": 3,
                    "options": [
                        {"text": "Translation", "role": "option", "selected": False, "disabled": False, "visible": True},
                        {"text": "Passthrough", "role": "option", "selected": False, "disabled": False, "visible": True},
                        {"text": "Split", "role": "option", "selected": False, "disabled": False, "visible": True},
                    ],
                    "inlineEvents": {}, "attrs": {"aria-controls": "process-step-list"},
                    "layout": {"x": 520, "y": 410, "width": 300, "height": 40, "zIndex": 1100},
                    "semanticPath": [],
                },
                {
                    "index": 1, "inspectionToken": "hipinspect-x-1", "tag": "input", "role": "textbox",
                    "type": "text", "label": "Old Flow Name", "section": "Underlying detail", "formControlName": "flowName",
                    "visible": True, "inActiveSurface": False, "required": False, "disabled": True, "readOnly": True,
                    "expanded": "", "checked": False, "hasValue": True, "valueLength": 10,
                    "ownedIds": [], "ownedSurfaceVisible": False, "optionCount": 0, "options": [],
                    "inlineEvents": {}, "attrs": {}, "layout": {"x": 40, "y": 300, "width": 300, "height": 40, "zIndex": 0}, "semanticPath": [],
                },
            ],
            "popupGraph": [{"id": "process-step-list", "role": "listbox", "visible": True, "zIndex": 1300, "owners": [{"label": "Process Step"}], "optionCount": 3}],
            "tabs": [{"text": "Configure Routing", "selected": True, "visible": True, "inActiveSurface": True}],
            "accordions": [{"text": "Process Step", "expanded": "true", "controls": "process-step-panel", "visible": True, "inActiveSurface": True}],
            "repeated": [], "forms": [{"visible": True, "inActiveSurface": True, "nativeValid": False, "controlCount": 1}], "tables": [],
            "recentEvents": [{"type": "click", "target": {"label": "Process Step"}}],
            "recentMutations": [{"type": "attributes", "attribute": "aria-expanded"}],
        }


@pytest.mark.asyncio
async def test_stage1_builds_foreground_option_and_event_model(tmp_path: Path):
    cfg = AppConfig()
    engine = WebsiteUnderstandingEngine(config=cfg)
    model = await engine.capture(page=_FakePage(), phase="biz_flow", stage="before_action", output_dir=tmp_path)
    assert model["available"] is True
    assert model["active_surface"]["role"] == "dialog"
    assert model["foreground_control_count"] == 1
    assert model["understanding_gate"]["pass"] is True
    process = next(c for c in model["controls"] if c["label"] == "Process Step")
    assert "select_option" in process["affordances"]
    assert {x["type"] for x in process["registeredEventListeners"]} == {"click", "change"}
    catalog = model["option_catalog"][process["semanticControlKey"]]
    assert [o["text"] for o in catalog["options"]] == ["Translation", "Passthrough", "Split"]
    assert (tmp_path / "website_understanding.json").is_file()
    assert (tmp_path / "website_option_catalog.json").is_file()


def test_stage1_config_is_enabled_by_default():
    cfg = AppConfig()
    p = cfg.portal_learning
    assert p.deep_website_understanding_enabled is True
    assert p.capture_registered_event_listener_types is True
    assert p.website_understanding_max_controls >= 3000
    assert p.website_understanding_max_options_per_control >= 500


@pytest.mark.asyncio
async def test_stage1_real_chromium_foreground_drawer_and_detached_dds_options(tmp_path: Path):
    playwright = pytest.importorskip("playwright.async_api")
    from playwright.async_api import async_playwright

    html = """
    <html><body>
      <div id='background'><input aria-label='Existing Rule Name' value='OLD' readonly></div>
      <dds-drawer role='dialog' aria-modal='true' aria-label='Create BizFlow'
        style='position:fixed;left:300px;top:30px;width:900px;height:700px;z-index:1000;background:white'>
        <form>
          <h2>Create BizFlow</h2>
          <fieldset><legend>Configure Routing</legend>
            <label for='process'>Process Step</label>
            <input id='process' role='combobox' aria-expanded='true' aria-controls='ps-list' required>
          </fieldset>
        </form>
      </dds-drawer>
      <div id='ps-list' role='listbox' style='position:fixed;left:500px;top:300px;z-index:1500;background:white'>
        <div role='option'>Translation</div><div role='option'>Passthrough</div><div role='option'>Split</div>
      </div>
      <script>
        document.getElementById('process').addEventListener('change', ()=>{});
        document.getElementById('process').addEventListener('click', ()=>{});
      </script>
    </body></html>
    """
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        await page.set_content(html)
        engine = WebsiteUnderstandingEngine(config=AppConfig())
        model = await engine.capture(page=page, phase="biz_flow", stage="inspect", output_dir=tmp_path)
        await browser.close()
    assert model["understanding_gate"]["pass"] is True
    assert model["active_surface"]["role"] == "dialog"
    assert any(c["label"] == "Process Step" and c["inActiveSurface"] for c in model["controls"])
    assert any(c["label"] == "Existing Rule Name" and not c["inActiveSurface"] for c in model["controls"])
    key = next(c["semanticControlKey"] for c in model["controls"] if c["label"] == "Process Step")
    assert [o["text"] for o in model["option_catalog"][key]["options"]] == ["Translation", "Passthrough", "Split"]
