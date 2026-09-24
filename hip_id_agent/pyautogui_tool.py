from __future__ import annotations

import asyncio
import platform
from pathlib import Path
from typing import Any, Dict

from .safe_io import safe_write_json
from .security import mask_sensitive_string


class PyAutoGUIUnavailable(RuntimeError):
    pass


class PyAutoGUIFallbackTool:
    """Audited visible-desktop interaction engine for already-proven HIP targets.

    In ``interaction_mode=primary`` the PyAutoGUI MCP transport performs the first
    physical click/type/key action after semantic target proof. Direct in-process
    PyAutoGUI is only a compatibility fallback. Playwright remains attached for
    discovery, geometry and exact effect/value verification.
    """

    def __init__(self, config: Any, run_dir: str | Path):
        self.config = config
        self.run_dir = Path(run_dir)
        self.audit_dir = self.run_dir / "pyautogui"
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self._module: Any = None
        self._mcp_backend: Any = None
        self._sequence = 0
        self._desktop_geometry_unreliable = False

    @property
    def policy(self) -> Any:
        return getattr(self.config, "pyautogui", None)

    def set_mcp_backend(self, backend: Any) -> None:
        self._mcp_backend = backend

    def enabled(self) -> bool:
        cfg = self.policy
        return bool(cfg is not None and getattr(cfg, "enabled", True))

    def mcp_enabled(self) -> bool:
        cfg = self.policy
        return bool(self.enabled() and cfg is not None and getattr(cfg, "mcp_enabled", True))

    def primary_mode(self) -> bool:
        cfg = self.policy
        return bool(self.enabled() and cfg is not None and str(getattr(cfg, "interaction_mode", "fallback")).lower() == "primary")

    def should_primary(self, action_kind: str, *, mutation: bool = False) -> bool:
        if not self.primary_mode():
            return False
        cfg = self.policy
        kind = str(action_kind or "").strip().lower()
        flag = {
            "click": "primary_for_clicks",
            "fill": "primary_for_form_fill",
            "search": "primary_for_search_fill",
            "press": "primary_for_keys",
        }.get(kind)
        if not flag or not bool(getattr(cfg, flag, False)):
            return False
        if mutation and not bool(getattr(cfg, "allow_mutation_clicks", False)):
            return False
        return True

    def _mcp_ready(self) -> bool:
        return bool(self.mcp_enabled() and self._mcp_backend is not None and getattr(self._mcp_backend, "started", False))

    def available(self) -> bool:
        """Non-invasive runtime availability signal used in action provenance."""
        if self._mcp_ready():
            return True
        if not self.enabled() or not self._local_fallback_allowed():
            return False
        if bool(getattr(self.policy, "windows_only", True)) and platform.system().lower() != "windows":
            return False
        if bool(getattr(getattr(self.config, "portal", None), "headless", False)):
            return False
        return True

    def _local_fallback_allowed(self) -> bool:
        cfg = self.policy
        return bool(cfg is not None and getattr(cfg, "allow_local_fallback_if_mcp_unavailable", True))

    def _load(self) -> Any:
        if self._module is not None:
            return self._module
        if not self.enabled():
            raise PyAutoGUIUnavailable("PyAutoGUI fallback is disabled by config.")
        if not self._local_fallback_allowed():
            raise PyAutoGUIUnavailable("Direct PyAutoGUI fallback is disabled; PyAutoGUI MCP is required for desktop fallback.")
        if bool(getattr(self.policy, "windows_only", True)) and platform.system().lower() != "windows":
            raise PyAutoGUIUnavailable("PyAutoGUI fallback is enabled only on Windows in this HIP build.")
        if bool(getattr(getattr(self.config, "portal", None), "headless", False)):
            raise PyAutoGUIUnavailable("PyAutoGUI fallback is unavailable in headless browser mode.")
        try:
            import pyautogui  # type: ignore
        except Exception as exc:  # lazy import: headless/Linux test environments stay unaffected
            raise PyAutoGUIUnavailable(f"PyAutoGUI is not available: {exc}") from exc
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = max(0.0, float(getattr(self.policy, "pause_seconds", 0.05)))
        self._module = pyautogui
        return pyautogui

    def status(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "enabled": self.enabled(),
            "mode": "pyautogui-mcp-primary" if self.primary_mode() else "pyautogui-mcp-fallback",
            "windows_only": bool(getattr(self.policy, "windows_only", True)) if self.policy is not None else True,
            "mcp_enabled": self.mcp_enabled(),
            "mcp_attached": self._mcp_ready(),
            "prefer_mcp": bool(getattr(self.policy, "prefer_mcp", True)) if self.policy is not None else True,
            "local_fallback_allowed": self._local_fallback_allowed(),
            "allow_mutation_clicks": bool(getattr(self.policy, "allow_mutation_clicks", False)) if self.policy is not None else False,
            "primary_mode": self.primary_mode(),
            "primary_clicks": bool(getattr(self.policy, "primary_for_clicks", False)) if self.policy is not None else False,
            "primary_form_fill": bool(getattr(self.policy, "primary_for_form_fill", False)) if self.policy is not None else False,
            "primary_keys": bool(getattr(self.policy, "primary_for_keys", False)) if self.policy is not None else False,
            "available": bool(self._mcp_ready()),
        }
        if not result["enabled"]:
            result["reason"] = "disabled"
            return result
        if self._mcp_ready():
            try:
                result["mcp_capabilities"] = self._mcp_backend.capability_status()
            except Exception:
                pass
            return result
        # Status must remain non-invasive on non-Windows/headless test systems.
        if platform.system().lower() != "windows" and bool(result["windows_only"]):
            result["reason"] = "windows_desktop_required"
            return result
        try:
            module = self._load()
            size = module.size()
            result.update({"available": True, "executor": "local-pyautogui", "screen": {"width": int(size.width), "height": int(size.height)}})
        except Exception as exc:
            result["reason"] = mask_sensitive_string(str(exc))
        return result

    def _is_mutating_action(self, action: str) -> bool:
        text = str(action or "").lower()
        safe_exact = {"add", "+ add", "continue", "next", "back", "cancel", "close", "done", "proceed"}
        if text.strip() in safe_exact:
            return False
        return any(word in text for word in ("save", "create", "submit", "delete", "remove", "deploy", "publish", "update", "enable", "disable", "confirm"))

    async def _screen_size(self) -> tuple[int, int, str]:
        if self._mcp_ready() and bool(getattr(self.policy, "prefer_mcp", True)):
            width, height = await self._mcp_backend.size()
            return int(width), int(height), "pyautogui-mcp"
        module = self._load()
        size = module.size()
        return int(size.width), int(size.height), "local-pyautogui"

    async def _stable_screen_point(self, page: Any, locator: Any) -> Dict[str, Any]:
        if self._desktop_geometry_unreliable:
            raise PyAutoGUIUnavailable("secondary_monitor_geometry_mismatch: desktop coordinates are not reliable for this run; use Playwright semantic execution")
        await page.bring_to_front()
        await locator.first.scroll_into_view_if_needed(timeout=int(getattr(self.policy, "locator_timeout_ms", 5000)))
        first = await locator.first.bounding_box(timeout=int(getattr(self.policy, "locator_timeout_ms", 5000)))
        if not first or first.get("width", 0) <= 1 or first.get("height", 0) <= 1:
            raise PyAutoGUIUnavailable("Resolved HIP control does not have a visible bounding box.")
        await asyncio.sleep(max(0.05, float(getattr(self.policy, "stability_wait_ms", 150)) / 1000.0))
        second = await locator.first.bounding_box(timeout=int(getattr(self.policy, "locator_timeout_ms", 5000)))
        if not second:
            raise PyAutoGUIUnavailable("Resolved HIP control disappeared before desktop fallback.")
        tolerance = float(getattr(self.policy, "bbox_stability_tolerance_px", 3.0))
        for key in ("x", "y", "width", "height"):
            if abs(float(first.get(key, 0)) - float(second.get(key, 0))) > tolerance:
                raise PyAutoGUIUnavailable("Resolved HIP control is moving/rerendering; refusing coordinate fallback.")

        metrics = await page.evaluate("""
() => ({
  screenX:Number(window.screenX||0), screenY:Number(window.screenY||0),
  outerWidth:Number(window.outerWidth||window.innerWidth||0), outerHeight:Number(window.outerHeight||window.innerHeight||0),
  innerWidth:Number(window.innerWidth||0), innerHeight:Number(window.innerHeight||0),
  screenWidth:Number(window.screen?.width||0), screenHeight:Number(window.screen?.height||0),
  vvX:Number(window.visualViewport?.offsetLeft||0), vvY:Number(window.visualViewport?.offsetTop||0)
})
""")
        screen_width, screen_height, executor = await self._screen_size()
        js_sw = max(1.0, float(metrics.get("screenWidth") or screen_width))
        js_sh = max(1.0, float(metrics.get("screenHeight") or screen_height))
        sx = float(screen_width) / js_sw
        sy = float(screen_height) / js_sh
        border_x = max(0.0, (float(metrics.get("outerWidth") or 0) - float(metrics.get("innerWidth") or 0)) / 2.0)
        chrome_y = max(0.0, float(metrics.get("outerHeight") or 0) - float(metrics.get("innerHeight") or 0) - border_x)
        css_x = float(metrics.get("screenX") or 0) + border_x + float(second["x"]) + float(second["width"]) / 2.0 + float(metrics.get("vvX") or 0)
        css_y = float(metrics.get("screenY") or 0) + chrome_y + float(second["y"]) + float(second["height"]) / 2.0 + float(metrics.get("vvY") or 0)
        x, y = int(round(css_x * sx)), int(round(css_y * sy))
        margin = int(getattr(self.policy, "screen_margin_px", 2))
        if not (margin <= x < screen_width - margin and margin <= y < screen_height - margin):
            self._desktop_geometry_unreliable = True
            raise PyAutoGUIUnavailable(f"secondary_monitor_geometry_mismatch: calculated desktop point ({x},{y}) is outside primary screen {screen_width}x{screen_height}; suspend PyAutoGUI for this run and use Playwright semantic execution")
        return {
            "x": x, "y": y,
            "bbox": {k: float(second[k]) for k in ("x", "y", "width", "height")},
            "screen": {"width": screen_width, "height": screen_height},
            "scale": {"x": sx, "y": sy},
            "executor": executor,
        }

    def _write_audit(self, payload: Dict[str, Any]) -> None:
        self._sequence += 1
        safe_write_json(self.audit_dir / f"desktop_action_{self._sequence:05d}.json", payload)

    async def _mcp_or_local_click(self, point: Dict[str, Any]) -> str:
        if self._mcp_ready() and bool(getattr(self.policy, "prefer_mcp", True)):
            await self._mcp_backend.click(
                point["x"], point["y"],
                duration=max(0.0, float(getattr(self.policy, "move_duration_seconds", 0.12))),
            )
            return "pyautogui-mcp"
        module = self._load()
        module.moveTo(point["x"], point["y"], duration=max(0.0, float(getattr(self.policy, "move_duration_seconds", 0.12))))
        module.click()
        return "local-pyautogui"

    async def click_locator(self, page: Any, locator: Any, *, action: str, selector: str = "") -> Dict[str, Any]:
        if self._is_mutating_action(action) and not bool(getattr(self.policy, "allow_mutation_clicks", False)):
            raise PyAutoGUIUnavailable("PyAutoGUI mutation click is disabled by policy. Enable it only with the governed BrowserSession mutation authorization/semantic gate.")
        point = await self._stable_screen_point(page, locator)
        payload = {"kind": "click", "action": str(action), "selector": str(selector), "point": point, "pass": False}
        try:
            payload["executor"] = await self._mcp_or_local_click(point)
            await asyncio.sleep(max(0.05, float(getattr(self.policy, "settle_ms", 300)) / 1000.0))
            payload["pass"] = True
            return payload
        except Exception as exc:
            payload["error"] = mask_sensitive_string(str(exc))
            raise
        finally:
            self._write_audit(payload)

    async def fill_locator(self, page: Any, locator: Any, value: str, *, selector: str = "") -> Dict[str, Any]:
        text = str(value)
        if bool(getattr(self.policy, "ascii_only_text", True)) and any(ord(ch) > 127 for ch in text):
            raise PyAutoGUIUnavailable("PyAutoGUI text fallback is ASCII-only to avoid clipboard leakage and keyboard-layout ambiguity.")
        point = await self._stable_screen_point(page, locator)
        payload = {"kind": "fill", "selector": str(selector), "point": point, "value_length": len(text), "pass": False}
        try:
            if self._mcp_ready() and bool(getattr(self.policy, "prefer_mcp", True)):
                await self._mcp_backend.click(point["x"], point["y"], duration=max(0.0, float(getattr(self.policy, "move_duration_seconds", 0.12))))
                await self._mcp_backend.hotkey(["ctrl", "a"])
                await self._mcp_backend.write(text, interval=max(0.0, float(getattr(self.policy, "key_interval_seconds", 0.01))))
                payload["executor"] = "pyautogui-mcp"
            else:
                module = self._load()
                module.moveTo(point["x"], point["y"], duration=max(0.0, float(getattr(self.policy, "move_duration_seconds", 0.12))))
                module.click()
                module.hotkey("ctrl", "a")
                module.write(text, interval=max(0.0, float(getattr(self.policy, "key_interval_seconds", 0.01))))
                payload["executor"] = "local-pyautogui"
            await asyncio.sleep(max(0.05, float(getattr(self.policy, "settle_ms", 300)) / 1000.0))
            payload["pass"] = True
            return payload
        except Exception as exc:
            payload["error"] = mask_sensitive_string(str(exc))
            raise
        finally:
            self._write_audit(payload)

    async def press_key(self, page: Any, key: str, *, selector: str = "") -> Dict[str, Any]:
        await page.bring_to_front()
        normalized = str(key or "").strip().lower()
        mapping = {"enter": "enter", "tab": "tab", "escape": "esc", "esc": "esc", "arrowdown": "down", "arrowup": "up", "space": "space"}
        resolved = mapping.get(normalized, normalized)
        payload = {"kind": "press", "selector": str(selector), "key": resolved, "pass": False}
        try:
            if self._mcp_ready() and bool(getattr(self.policy, "prefer_mcp", True)):
                await self._mcp_backend.press(resolved)
                payload["executor"] = "pyautogui-mcp"
            else:
                module = self._load()
                module.press(resolved)
                payload["executor"] = "local-pyautogui"
            await asyncio.sleep(max(0.05, float(getattr(self.policy, "settle_ms", 300)) / 1000.0))
            payload["pass"] = True
            return payload
        except Exception as exc:
            payload["error"] = mask_sensitive_string(str(exc))
            raise
        finally:
            self._write_audit(payload)

    async def _viewport_css_to_screen(self, page: Any, x_css: float, y_css: float) -> Dict[str, Any]:
        await page.bring_to_front()
        metrics = await page.evaluate("""
() => ({
  screenX:Number(window.screenX||0), screenY:Number(window.screenY||0),
  outerWidth:Number(window.outerWidth||window.innerWidth||0), outerHeight:Number(window.outerHeight||window.innerHeight||0),
  innerWidth:Number(window.innerWidth||0), innerHeight:Number(window.innerHeight||0),
  screenWidth:Number(window.screen?.width||0), screenHeight:Number(window.screen?.height||0),
  vvX:Number(window.visualViewport?.offsetLeft||0), vvY:Number(window.visualViewport?.offsetTop||0)
})
""")
        screen_width, screen_height, executor = await self._screen_size()
        js_sw = max(1.0, float(metrics.get("screenWidth") or screen_width))
        js_sh = max(1.0, float(metrics.get("screenHeight") or screen_height))
        sx = float(screen_width) / js_sw
        sy = float(screen_height) / js_sh
        border_x = max(0.0, (float(metrics.get("outerWidth") or 0) - float(metrics.get("innerWidth") or 0)) / 2.0)
        chrome_y = max(0.0, float(metrics.get("outerHeight") or 0) - float(metrics.get("innerHeight") or 0) - border_x)
        css_x = float(metrics.get("screenX") or 0) + border_x + float(x_css) + float(metrics.get("vvX") or 0)
        css_y = float(metrics.get("screenY") or 0) + chrome_y + float(y_css) + float(metrics.get("vvY") or 0)
        x, y = int(round(css_x * sx)), int(round(css_y * sy))
        margin = int(getattr(self.policy, "screen_margin_px", 2))
        if not (margin <= x < screen_width - margin and margin <= y < screen_height - margin):
            self._desktop_geometry_unreliable = True
            raise PyAutoGUIUnavailable(f"secondary_monitor_geometry_mismatch: calculated visual point ({x},{y}) is outside primary screen {screen_width}x{screen_height}; use Playwright semantic execution")
        return {"x": x, "y": y, "screen": {"width": screen_width, "height": screen_height}, "executor": executor}

    async def click_viewport_ratio(self, page: Any, *, x_ratio: float, y_ratio: float, action: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """Click a high-confidence visual target expressed as normalized viewport coordinates."""
        if not bool(getattr(self.policy, "allow_native_screen_targets", True)):
            raise PyAutoGUIUnavailable("Visual screen-target recovery is disabled.")
        if self._is_mutating_action(action):
            raise PyAutoGUIUnavailable("Visual screen-target recovery is restricted to structural/non-mutating actions.")
        confidence = float(evidence.get("confidence") or 0.0)
        threshold = float(getattr(self.policy, "visual_target_confidence_threshold", 0.94))
        if confidence < threshold:
            raise PyAutoGUIUnavailable(f"Visual target confidence {confidence:.3f} is below {threshold:.3f}.")
        xr, yr = float(x_ratio), float(y_ratio)
        if not (0.0 <= xr <= 1.0 and 0.0 <= yr <= 1.0):
            raise PyAutoGUIUnavailable("Visual target coordinates must be normalized to the viewport.")
        dims = await page.evaluate("() => ({width:Number(window.innerWidth||0),height:Number(window.innerHeight||0)})")
        width, height = float(dims.get("width") or 0), float(dims.get("height") or 0)
        if width < 20 or height < 20:
            raise PyAutoGUIUnavailable("Browser viewport dimensions are unavailable for visual target conversion.")
        point = await self._viewport_css_to_screen(page, xr * width, yr * height)
        payload = {
            "kind": "visual_structural_click", "action": str(action), "point": point,
            "viewport_ratio": {"x": xr, "y": yr},
            "evidence": {"source": str(evidence.get("source") or "vision"), "confidence": confidence, "reason": str(evidence.get("reason") or "")[:500]},
            "pass": False,
        }
        try:
            if self._mcp_ready() and bool(getattr(self.policy, "prefer_mcp", True)):
                await self._mcp_backend.click(point["x"], point["y"], duration=max(0.0, float(getattr(self.policy, "move_duration_seconds", 0.12))))
                payload["executor"] = "pyautogui-mcp"
            else:
                module = self._load()
                module.moveTo(point["x"], point["y"], duration=max(0.0, float(getattr(self.policy, "move_duration_seconds", 0.12))))
                module.click()
                payload["executor"] = "local-pyautogui"
            await asyncio.sleep(max(0.05, float(getattr(self.policy, "settle_ms", 300)) / 1000.0))
            payload["pass"] = True
            return payload
        except Exception as exc:
            payload["error"] = mask_sensitive_string(str(exc))
            raise
        finally:
            self._write_audit(payload)

    async def click_screen_point(self, *, x: int, y: int, action: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """Optional native/browser-chrome recovery with an explicit evidence gate.

        This capability is never invoked for normal DOM controls. A caller must
        provide high-confidence vision/native-dialog evidence and the requested
        action must be non-mutating.
        """
        if not bool(getattr(self.policy, "allow_native_screen_targets", True)):
            raise PyAutoGUIUnavailable("Native screen-target recovery is disabled.")
        if self._is_mutating_action(action):
            raise PyAutoGUIUnavailable("Native screen-target recovery cannot perform mutating HIP actions.")
        confidence = float(evidence.get("confidence") or 0.0)
        threshold = float(getattr(self.policy, "native_target_confidence_threshold", 0.97))
        source = str(evidence.get("source") or "").strip().lower()
        allowed_sources = set(getattr(self.policy, "native_target_evidence_sources", ["vision", "native_dialog", "browser_chrome"]) or [])
        if confidence < threshold or source not in allowed_sources:
            raise PyAutoGUIUnavailable("Native screen target lacks the required high-confidence evidence provenance.")
        width, height, executor = await self._screen_size()
        margin = int(getattr(self.policy, "screen_margin_px", 2))
        if not (margin <= int(x) < width - margin and margin <= int(y) < height - margin):
            raise PyAutoGUIUnavailable("Native screen target is outside the active display bounds.")
        payload = {
            "kind": "native_click", "action": str(action), "x": int(x), "y": int(y),
            "evidence": {"source": source, "confidence": confidence, "reason": str(evidence.get("reason") or "")[:500]},
            "executor": executor, "pass": False,
        }
        try:
            if self._mcp_ready() and bool(getattr(self.policy, "prefer_mcp", True)):
                await self._mcp_backend.click(int(x), int(y), duration=max(0.0, float(getattr(self.policy, "move_duration_seconds", 0.12))))
                payload["executor"] = "pyautogui-mcp"
            else:
                module = self._load()
                module.moveTo(int(x), int(y), duration=max(0.0, float(getattr(self.policy, "move_duration_seconds", 0.12))))
                module.click()
                payload["executor"] = "local-pyautogui"
            payload["pass"] = True
            return payload
        except Exception as exc:
            payload["error"] = mask_sensitive_string(str(exc))
            raise
        finally:
            self._write_audit(payload)
