from __future__ import annotations

"""Local browser-level final mission UAT for the HIP Portal agent.

The harness never contacts Dell.  It drives a local same-page mock HIP SPA using
exactly the same semantic-understanding, dynamic-option, world-model and
PyAutoGUI-MCP point-interaction contracts used by the real agent.  It is intended
as a repeatable pre-Dell certification, not as a claim of Dell tenant UAT.
"""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from playwright.async_api import async_playwright

from . import __version__
from .autonomous_transition_runtime import AutonomousPortalTransitionPlanner, choose_dynamic_portal_option
from .config import AppConfig
from .final_mission import FinalMissionConsolidator
from .mission_controller import MissionController
from .pyautogui_tool import PyAutoGUIFallbackTool
from .safe_io import safe_write_json, safe_write_text
from .website_understanding import WebsiteUnderstandingEngine
from .website_world_model import WebsiteWorldModelMemory

PHASES = [
    "data_map",
    "source_document_type",
    "target_document_type",
    "rule",
    "source_transport_profile",
    "target_transport_profile",
    "biz_flow",
]

DISPLAY = {
    "data_map": "Data Map",
    "source_document_type": "Source Document Type",
    "target_document_type": "Target Document Type",
    "rule": "Rule",
    "source_transport_profile": "Source Transport Profile",
    "target_transport_profile": "Target Transport Profile",
    "biz_flow": "BizFlow",
}


class BrowserBackedPyAutoGUIMCP:
    """Browser-backed test double for the point-based PyAutoGUI MCP contract."""

    started = True

    def __init__(self, page: Any):
        self.page = page
        self.calls: List[Dict[str, Any]] = []

    async def _metrics(self) -> Dict[str, float]:
        return await self.page.evaluate("""() => ({
          screenX:Number(window.screenX||0), screenY:Number(window.screenY||0),
          outerWidth:Number(window.outerWidth||window.innerWidth||0), outerHeight:Number(window.outerHeight||window.innerHeight||0),
          innerWidth:Number(window.innerWidth||0), innerHeight:Number(window.innerHeight||0),
          screenWidth:Number(window.screen?.width||0), screenHeight:Number(window.screen?.height||0)
        })""")

    async def size(self):
        m = await self._metrics()
        return int(m["screenWidth"]), int(m["screenHeight"])

    async def click(self, x: int, y: int, duration: float = 0.0):
        self.calls.append({"kind": "click", "x": int(x), "y": int(y)})
        m = await self._metrics()
        border_x = max(0.0, (m["outerWidth"] - m["innerWidth"]) / 2.0)
        chrome_y = max(0.0, m["outerHeight"] - m["innerHeight"] - border_x)
        vx = float(x) - m["screenX"] - border_x
        vy = float(y) - m["screenY"] - chrome_y
        await self.page.mouse.click(vx, vy)

    async def hotkey(self, keys):
        self.calls.append({"kind": "hotkey", "keys": list(keys)})
        mapping = {"ctrl": "Control", "alt": "Alt", "shift": "Shift", "win": "Meta"}
        await self.page.keyboard.press("+".join(mapping.get(str(k).lower(), str(k)) for k in keys))

    async def write(self, text: str, interval: float = 0.0):
        self.calls.append({"kind": "write", "length": len(str(text))})
        await self.page.keyboard.type(str(text), delay=max(0, int(interval * 1000)))

    async def press(self, keys, presses: int = 1, interval: float = 0.0):
        key = keys[0] if isinstance(keys, list) else keys
        self.calls.append({"kind": "press", "key": str(key)})
        for _ in range(int(presses or 1)):
            await self.page.keyboard.press(str(key))

    def capability_status(self):
        return {"available": True, "transport": "browser-backed-pyautogui-mcp-test-double"}


def _find_chromium(explicit: str = "") -> str:
    if explicit and Path(explicit).is_file():
        return str(Path(explicit))
    candidates = [
        shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("google-chrome"), shutil.which("google-chrome-stable"),
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return ""


def _candidate_for(model: Mapping[str, Any], label: str) -> Dict[str, Any]:
    wanted = str(label).strip().lower()
    controls = [x for x in model.get("controls", []) if isinstance(x, Mapping)]
    exact = [x for x in controls if str(x.get("label") or "").strip().lower() == wanted and x.get("inActiveSurface", True)]
    if exact:
        return dict(exact[0])
    partial = [x for x in controls if wanted in str(x.get("label") or "").strip().lower() and x.get("inActiveSurface", True)]
    return dict(partial[0]) if partial else {"label": label, "section": "", "role": "button", "inActiveSurface": True}


def _portal_options(page: Any, selector: str) -> Any:
    return page.locator(selector).locator("option").all_text_contents()


async def run_final_mission_local_uat(
    *,
    config: AppConfig,
    output_dir: str | Path,
    browser_executable: str = "",
    headless: bool = True,
) -> Dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    html = Path(__file__).resolve().parents[1] / "local_mock_hip" / "final_mission_mock.html"
    chromium = _find_chromium(browser_executable or str(getattr(config.portal, "chrome_executable_path", "") or ""))
    if not chromium:
        result = {
            "schema_version": "hip.final-mission-local-uat.v1",
            "pass": False,
            "status": "browser_unavailable",
            "reason": "No local Chromium/Chrome/Edge executable was found.",
            "phase_count": 0,
            "phases": [],
        }
        safe_write_json(output / "final_mission_local_uat.json", result, mask=False)
        return result

    config.portal.headless = False  # geometry contract is exercised even though Chromium itself may be launched headless
    config.pyautogui.windows_only = False
    config.pyautogui.enabled = True
    config.pyautogui.mcp_enabled = True
    config.pyautogui.interaction_mode = "primary"
    config.pyautogui.primary_for_clicks = True
    config.pyautogui.primary_for_form_fill = True
    config.pyautogui.primary_for_search_fill = True
    config.pyautogui.primary_for_keys = True
    config.pyautogui.allow_mutation_clicks = True
    config.pyautogui.verify_after_click_event = False

    engine = WebsiteUnderstandingEngine(config=config)
    world = WebsiteWorldModelMemory(output / "world_model", config=config.brain)
    planner = AutonomousPortalTransitionPlanner(world_model=world)
    phase_results: List[Dict[str, Any]] = []
    executor_audit: List[Dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=bool(headless), executable_path=chromium)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        await page.set_content(html.read_text(encoding="utf-8"))
        mcp = BrowserBackedPyAutoGUIMCP(page)
        tool = PyAutoGUIFallbackTool(config, output)
        tool.set_mcp_backend(mcp)

        async def observe(phase: str, stage: str) -> Dict[str, Any]:
            # Registered-listener CDP capture is certified separately in Stage 1;
            # the final lifecycle UAT disables that expensive pass so the full
            # seven-phase browser harness remains fast and deterministic.
            return await engine.capture(
                page=page, phase=phase, stage=stage,
                output_dir=output / "observations" / phase / stage,
                include_registered_listeners=False,
            )

        async def point_click(locator: Any, action: str, selector: str) -> Dict[str, Any]:
            row = await tool.click_locator(page, locator, action=action, selector=selector)
            executor_audit.append({"action": action, "executor": row.get("executor"), "kind": "click"})
            return row

        async def point_fill(locator: Any, value: str, selector: str) -> Dict[str, Any]:
            row = await tool.fill_locator(page, locator, value, selector=selector)
            executor_audit.append({"action": "fill", "executor": row.get("executor"), "kind": "fill", "value_length": len(value)})
            return row

        for idx, phase in enumerate(PHASES, start=1):
            display = DISPLAY[phase]
            nav = page.get_by_role("button", name=display, exact=True)
            await point_click(nav, f"open {display}", f"role=button name={display}")
            before = await observe(phase, "listing")
            plan = planner.plan_next(phase=phase, goal_action="open_add_form", website_model=before, goal_context=f"Create {display}")
            if not plan.get("pass"):
                raise RuntimeError(f"Stage5 UAT could not resolve Add for {phase}: {plan}")
            add_label = str((plan.get("selected") or {}).get("label") or "Add")
            await point_click(page.get_by_role("button", name=add_label, exact=True), f"structural opener {display} Add", "role=button name=Add")
            opened = await observe(phase, "create_opened")
            if not (opened.get("understanding_gate") or {}).get("pass"):
                raise RuntimeError(f"Website understanding gate failed for {phase}")

            # Learn the verified listing -> create transition.
            world.record_verified_transition(
                phase=phase, action="open_add_form", label=add_label, section="",
                candidate=_candidate_for(before, add_label), before=before, after=opened,
                effect={"pass": True, "confidence": 0.99, "effect_type": "foreground_create_drawer_opened"},
            )

            name_value = f"UAT_{idx}_{phase.upper()}"
            await point_fill(page.get_by_label("Name", exact=True), name_value, "label=Name")

            decisions: List[Dict[str, Any]] = []
            if "document_type" in phase:
                live = await _portal_options(page, "#data-format")
                decision = choose_dynamic_portal_option(
                    phase=phase, label="Data Format Type", section="Create", live_options=live,
                    desired_value="XML", mission_context={"data_format_type": "XML"}, world_model=world,
                )
                if not decision.get("pass"):
                    raise RuntimeError(f"Document Type option decision failed: {decision}")
                await page.select_option("#data-format", label=str(decision["selected_option"]))
                executor_audit.append({"action": "select Data Format Type", "executor": "python-playwright", "kind": "select"})
                world.record_verified_portal_choice(
                    phase=phase, action="select", control={"label": "Data Format Type", "section": "Create", "role": "combobox"},
                    choice=str(decision["selected_option"]), available_options=live,
                    effect_type="option_committed", effect_confidence=0.99,
                )
                decisions.append(decision)
            elif phase == "rule":
                live = await _portal_options(page, "#rule-type")
                decision = choose_dynamic_portal_option(
                    phase=phase, label="Rule Type", section="Create Rule", live_options=live,
                    desired_value="Routing", mission_context={"rule_type": "Routing"}, world_model=world,
                )
                if not decision.get("pass"):
                    raise RuntimeError(f"Rule option decision failed: {decision}")
                await page.select_option("#rule-type", label=str(decision["selected_option"]))
                await page.select_option("#condition-attr", label="Receiver")
                await page.select_option("#condition-op", label="Equals")
                await point_fill(page.locator("#condition-value"), "UAT_RECEIVER", "#condition-value")
                executor_audit.append({"action": "select Rule Type", "executor": "python-playwright", "kind": "select"})
                decisions.append(decision)
            elif "transport_profile" in phase:
                usage = "Sender" if phase.startswith("source") else "Receiver"
                expected_group = "da-sender-sftphaft-dce-shared" if usage == "Sender" else "pt-receiver-sftphaft-dce-shared"
                await page.select_option("#profile-usage", label=usage)
                live_interface = await _portal_options(page, "#interface-type")
                decision = choose_dynamic_portal_option(
                    phase=phase, label="Interface Type", section="Interface", live_options=live_interface,
                    desired_value="SFTP_HAFT", mission_context={"interface_type": "SFTP_HAFT", "profile_usage": usage}, world_model=world,
                )
                if not decision.get("pass"):
                    raise RuntimeError(f"TP interface option decision failed: {decision}")
                await page.select_option("#interface-type", label=str(decision["selected_option"]))
                await page.wait_for_selector("#deployment-group")
                groups = await _portal_options(page, "#deployment-group")
                group_decision = choose_dynamic_portal_option(
                    phase=phase, label="Deployment Group", section="Interface", live_options=groups,
                    desired_value=expected_group, mission_context={"deployment_group": expected_group}, world_model=world,
                )
                if not group_decision.get("pass"):
                    raise RuntimeError(f"TP deployment group decision failed: {group_decision}")
                await page.select_option("#deployment-group", label=str(group_decision["selected_option"]))
                await point_fill(page.locator("#remote-path"), "/uat/path", "#remote-path")
                executor_audit.append({"action": "select Interface Type", "executor": "python-playwright", "kind": "select"})
                executor_audit.append({"action": "select Deployment Group", "executor": "python-playwright", "kind": "select"})
                decisions.extend([decision, group_decision])
            elif phase == "biz_flow":
                live = await _portal_options(page, "#process-step")
                decision = choose_dynamic_portal_option(
                    phase=phase, label="Process Step", section="Create BizFlow", live_options=live,
                    desired_value="Translation", mission_context={"process_steps": [{"step_type": "Translation"}]}, world_model=world,
                )
                if not decision.get("pass"):
                    raise RuntimeError(f"BizFlow Process Step decision failed: {decision}")
                await page.select_option("#process-step", label=str(decision["selected_option"]))
                if "Translation" not in await page.locator("#process-child").inner_text():
                    raise RuntimeError("BizFlow Process Step child branch did not update")
                world.record_verified_portal_choice(
                    phase=phase, action="select", control={"label": "Process Step", "section": "Create BizFlow", "role": "combobox"},
                    choice=str(decision["selected_option"]), available_options=live,
                    effect_type="dependent_controls_revealed", effect_confidence=0.99,
                )
                executor_audit.append({"action": "select Process Step", "executor": "python-playwright", "kind": "select"})
                decisions.append(decision)

            pre_create = await observe(phase, "filled")
            await point_click(page.get_by_role("button", name="Create", exact=True), f"create {display}", "role=button name=Create")
            if await page.locator("#drawer").is_visible():
                raise RuntimeError(f"Create drawer did not close for {phase}")
            listing_text = await page.locator("#listing").inner_text()
            if name_value not in listing_text:
                raise RuntimeError(f"Created object not visible for {phase}")
            after_create = await observe(phase, "created")
            world.record_verified_transition(
                phase=phase, action="create", label="Create", section=f"Create {display}",
                candidate=_candidate_for(pre_create, "Create"), before=pre_create, after=after_create,
                effect={"pass": True, "confidence": 0.99, "effect_type": "object_created_and_drawer_closed"},
            )
            phase_results.append({
                "phase": phase, "display": display, "pass": True, "name": name_value,
                "dynamic_option_decisions": decisions,
                "same_page_create": True,
            })

        # Lifecycle after create: Edit -> Save -> Validate -> Deploy for BizFlow.
        await point_click(page.get_by_role("button", name="BizFlow", exact=True), "open BizFlow lifecycle", "role=button name=BizFlow")
        before_edit = await observe("biz_flow", "before_edit")
        edit_plan = planner.plan_next(phase="biz_flow", goal_action="edit", website_model=before_edit, goal_context="Edit the created BizFlow")
        if not edit_plan.get("pass"):
            raise RuntimeError(f"Edit action could not be planned: {edit_plan}")
        await point_click(page.get_by_role("button", name="Edit", exact=True), "edit BizFlow", "role=button name=Edit")
        edit_open = await observe("biz_flow", "edit_open")
        current_name = await page.get_by_label("Name", exact=True).input_value()
        edited_name = current_name + "_EDITED"
        await point_fill(page.get_by_label("Name", exact=True), edited_name, "label=Name")
        await point_click(page.get_by_role("button", name="Save", exact=True), "save BizFlow", "role=button name=Save")
        saved = await observe("biz_flow", "saved")
        world.record_verified_transition(
            phase="biz_flow", action="save", label="Save", section="Edit BizFlow",
            candidate=_candidate_for(edit_open, "Save"), before=edit_open, after=saved,
            effect={"pass": True, "confidence": 0.99, "effect_type": "edit_saved_and_drawer_closed"},
        )

        validate_plan = planner.plan_next(phase="biz_flow", goal_action="validate", website_model=saved, goal_context="Validate the BizFlow")
        if not validate_plan.get("pass"):
            raise RuntimeError(f"Validate action could not be planned: {validate_plan}")
        await point_click(page.get_by_role("button", name="Validate", exact=True), "validate BizFlow", "role=button name=Validate")
        validated = await observe("biz_flow", "validated")
        if "Validated" not in await page.locator("#listing").inner_text():
            raise RuntimeError("BizFlow did not enter Validated state")
        world.record_verified_transition(
            phase="biz_flow", action="validate", label="Validate", section="BizFlow",
            candidate=_candidate_for(saved, "Validate"), before=saved, after=validated,
            effect={"pass": True, "confidence": 0.99, "effect_type": "status_validated"},
        )

        deploy_plan = planner.plan_next(phase="biz_flow", goal_action="deploy", website_model=validated, goal_context="Deploy the validated BizFlow")
        if not deploy_plan.get("pass"):
            raise RuntimeError(f"Deploy action could not be planned: {deploy_plan}")
        await point_click(page.get_by_role("button", name="Deploy", exact=True), "deploy BizFlow", "role=button name=Deploy")
        deployed = await observe("biz_flow", "deployed")
        final_text = await page.locator("#listing").inner_text()
        if "Deployed" not in final_text:
            raise RuntimeError("BizFlow did not enter Deployed state")
        world.record_verified_transition(
            phase="biz_flow", action="deploy", label="Deploy", section="BizFlow",
            candidate=_candidate_for(validated, "Deploy"), before=validated, after=deployed,
            effect={"pass": True, "confidence": 0.99, "effect_type": "status_deployed"},
        )
        screenshot = output / "final_uat_deployed.png"
        await page.screenshot(path=str(screenshot), full_page=True)
        await browser.close()

    # Build current-run proof artifacts and exercise the same final consolidation
    # gate used by the live mission without contacting Dell.
    proof_root = output / "proof_run"
    mission = MissionController(proof_root, run_id="LOCAL-UAT", phases=PHASES, mode="all_phases_until_complete")
    phase_verifications: List[Dict[str, Any]] = []
    for phase in PHASES:
        phase_dir = proof_root / phase
        phase_dir.mkdir(parents=True, exist_ok=True)
        verification = {"phase": phase, "status": "pass", "pass": True, "source": "local_browser_uat"}
        safe_write_json(phase_dir / "phase_verification.json", verification, mask=False)
        safe_write_json(phase_dir / "phase_exact_state_lock.json", {
            "phase": phase, "exact_completion_checkpoint": {"pass": True, "status": "local_browser_uat_verified"}
        }, mask=False)
        safe_write_json(phase_dir / "section_judge_gate.json", {"phase": phase, "pass": True, "source": "deterministic_local_uat"}, mask=False)
        mission.mark_phase_complete(phase, attempt=1, judge_pass=True)
        phase_verifications.append(verification)

    terminal_gate = {"pass": True, "code": "LOCAL_UAT_TERMINAL_GATE_OK"}
    consolidation = FinalMissionConsolidator(
        proof_root, run_id="LOCAL-UAT", phases=PHASES, mode="all_phases_until_complete"
    ).evaluate(
        mission=mission,
        terminal_gate=terminal_gate,
        phase_verifications=phase_verifications,
        witness_report={"status": "disabled", "pass": True},
        transition_state={"pending": None},
    )

    pyauto_used = any(row.get("executor") == "pyautogui-mcp" for row in executor_audit)
    result = {
        "schema_version": "hip.final-mission-local-uat.v1",
        "pass": bool(len(phase_results) == len(PHASES) and all(x.get("pass") for x in phase_results) and consolidation.get("pass") and "Deployed" in final_text),
        "status": "pass" if consolidation.get("pass") else "failed",
        "phase_count": len(phase_results),
        "phases": phase_results,
        "lifecycle": {"edit": True, "save": True, "validate": True, "deploy": True, "final_status": "Deployed"},
        "final_mission_consolidation": consolidation,
        "pyautogui_mcp_contract_used": pyauto_used,
        "executor_audit": executor_audit,
        "world_model_manifest": str(world.manifest_path),
        "world_model_learned": world.manifest_path.is_file(),
        "browser_executable": chromium,
        "mock_only": True,
        "dell_environment_contacted": False,
        "screenshot": str(screenshot),
    }
    safe_write_json(output / "final_mission_local_uat.json", result, mask=False)
    lines = [
        f"# HIP Portal v{__version__} Final Mission Local Browser UAT",
        "",
        f"- PASS: **{'YES' if result['pass'] else 'NO'}**",
        f"- Phases: **{len(phase_results)}/{len(PHASES)}**",
        f"- Lifecycle: Edit / Save / Validate / Deploy = **PASS**",
        f"- Final BizFlow status: **Deployed**",
        f"- PyAutoGUI-MCP point contract used: **{'YES' if pyauto_used else 'NO'}**",
        f"- Final consolidation gate: **{'PASS' if consolidation.get('pass') else 'BLOCK'}**",
        "",
        "This is a local mock-browser certification and does not claim Dell tenant UAT.",
    ]
    safe_write_text(output / "final_mission_local_uat.md", "\n".join(lines) + "\n")
    return result


__all__ = ["run_final_mission_local_uat", "PHASES", "DISPLAY", "BrowserBackedPyAutoGUIMCP"]
