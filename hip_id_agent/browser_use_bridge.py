from __future__ import annotations

import asyncio
import json
import weakref
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .security import mask_sensitive_data, mask_sensitive_string

# Page -> bridge registry lets row/form helpers consume Browser-Use state without
# changing every existing deterministic function signature.
_PAGE_BRIDGES: "weakref.WeakKeyDictionary[Any, BrowserUseStateBridge]" = weakref.WeakKeyDictionary()
_PAGE_BRIDGES_BY_ID: Dict[int, "BrowserUseStateBridge"] = {}


def register_page_bridge(page: Any, bridge: "BrowserUseStateBridge") -> None:
    try:
        _PAGE_BRIDGES[page] = bridge
    except Exception:
        _PAGE_BRIDGES_BY_ID[id(page)] = bridge


def get_page_bridge(page: Any) -> Optional["BrowserUseStateBridge"]:
    try:
        hit = _PAGE_BRIDGES.get(page)
        if hit is not None:
            return hit
    except Exception:
        pass
    return _PAGE_BRIDGES_BY_ID.get(id(page))


@dataclass
class BrowserUseBridgeStatus:
    enabled: bool
    available: bool
    attached: bool
    cdp_url: str
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "attached": self.attached,
            "cdp_url": self.cdp_url,
            "error": self.error,
        }


class BrowserUseStateBridge:
    """Attach Browser-Use to the already-authenticated HIP Chrome via CDP.

    This component intentionally exposes page state only. It does not run a free-form
    Browser-Use Agent and it is not authorized to save/create/delete/deploy/migrate.
    Deterministic Playwright + MCP governance remains the action authority.
    """

    def __init__(
        self, *, enabled: bool, cdp_url: str, keep_alive: bool = True,
        max_state_chars: int = 60000, attach_timeout_seconds: float = 8.0,
        snapshot_timeout_seconds: float = 4.0,
    ):
        self.enabled = bool(enabled)
        self.cdp_url = str(cdp_url or "")
        self.keep_alive = bool(keep_alive)
        self.max_state_chars = max(2000, int(max_state_chars or 60000))
        self.attach_timeout_seconds = max(1.0, float(attach_timeout_seconds or 8.0))
        self.snapshot_timeout_seconds = max(1.0, float(snapshot_timeout_seconds or 4.0))
        self.browser: Any = None
        self.status = BrowserUseBridgeStatus(enabled=self.enabled, available=False, attached=False, cdp_url=self.cdp_url)

    async def start(self) -> Dict[str, Any]:
        if not self.enabled:
            return self.status.as_dict()
        if not self.cdp_url:
            self.status.error = "missing CDP URL"
            return self.status.as_dict()
        try:
            # Browser is an alias of BrowserSession in current Browser-Use. Import
            # lazily so config/tests can run before optional/runtime dependencies load.
            from browser_use import Browser
            try:
                self.browser = Browser(cdp_url=self.cdp_url, keep_alive=self.keep_alive)
            except TypeError:
                self.browser = Browser(cdp_url=self.cdp_url)
            await asyncio.wait_for(self.browser.start(), timeout=self.attach_timeout_seconds)
            self.status.available = True
            self.status.attached = True
            return self.status.as_dict()
        except Exception as exc:
            self.browser = None
            self.status.error = mask_sensitive_string(str(exc))[:1000]
            return self.status.as_dict()

    async def stop(self) -> None:
        """Detach Browser-Use without dispatching BrowserStopEvent.

        Browser-Use's public ``stop()`` resets SessionManager state and, across
        versions/keep_alive combinations, can close or invalidate targets owned by
        the same CDP browser. HIP's Playwright context is the lifecycle authority,
        so recovery perception must never call Browser.stop()/close()/kill().
        """
        browser = self.browser
        if browser is None:
            return
        try:
            # Suppress Browser-Use auto-reconnect before closing only its CDP socket.
            try:
                setattr(browser, "_intentional_stop", True)
            except Exception:
                pass
            task = getattr(browser, "_reconnect_task", None)
            if task is not None and not getattr(task, "done", lambda: True)():
                try:
                    task.cancel()
                except Exception:
                    pass
            root = getattr(browser, "_cdp_client_root", None)
            if root is None:
                root = getattr(browser, "cdp_client", None)
            stopper = getattr(root, "stop", None) if root is not None else None
            if callable(stopper):
                await asyncio.wait_for(stopper(), timeout=2.5)
        except Exception:
            # Never escalate an advisory-observer detach into browser failure.
            pass
        self.browser = None
        self.status.attached = False
        self.status.available = False


    async def _bounded_call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.browser is None:
            raise RuntimeError("Browser-Use is not attached")
        method = getattr(self.browser, method_name, None)
        if method is None:
            raise AttributeError(method_name)
        return await asyncio.wait_for(method(*args, **kwargs), timeout=self.snapshot_timeout_seconds)

    @staticmethod
    def _normalise(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): BrowserUseStateBridge._normalise(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [BrowserUseStateBridge._normalise(v) for v in value]
        if hasattr(value, "model_dump"):
            try:
                return BrowserUseStateBridge._normalise(value.model_dump(mode="json"))
            except Exception:
                pass
        if hasattr(value, "dict"):
            try:
                return BrowserUseStateBridge._normalise(value.dict())
            except Exception:
                pass
        return str(value)

    @staticmethod
    def _interactive_excerpt(value: Any, *, limit: int = 120) -> list[dict[str, Any]]:
        """Extract a compact actionable-element inventory from Browser-Use state.

        Browser-Use state shapes evolve between releases.  This recursive reader
        intentionally looks only for common semantic/geometry keys and never calls
        an action.  The deterministic HIP executor can use the result to understand
        a changed surface without spending context on the full accessibility tree.
        """
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        def walk(node: Any, depth: int = 0) -> None:
            if len(rows) >= max(1, int(limit)) or depth > 14:
                return
            if isinstance(node, dict):
                label = str(node.get("label") or node.get("text") or node.get("name") or node.get("aria_label") or node.get("ariaLabel") or "").strip()
                role = str(node.get("role") or node.get("tag_name") or node.get("tag") or "").strip()
                selector = str(node.get("selector") or node.get("css_selector") or node.get("xpath") or "").strip()
                clickable = bool(node.get("clickable") or node.get("is_clickable") or node.get("interactive") or role.lower() in {"button","textbox","combobox","checkbox","radio","option","link"})
                if clickable or selector or role.lower() in {"button","textbox","combobox","checkbox","radio","option"}:
                    key=(label[:180], role[:80], selector[:240])
                    if key not in seen:
                        seen.add(key)
                        rows.append({
                            "label": label[:240], "role": role[:80], "selector": selector[:300],
                            "enabled": node.get("enabled", not bool(node.get("disabled"))),
                            "visible": node.get("visible", node.get("is_visible")),
                            "index": node.get("index") or node.get("highlight_index"),
                        })
                for child in node.values():
                    if isinstance(child, (dict, list, tuple)):
                        walk(child, depth + 1)
            elif isinstance(node, (list, tuple)):
                for child in node:
                    walk(child, depth + 1)
                    if len(rows) >= max(1, int(limit)):
                        break

        walk(value)
        return rows

    async def recovery_context(self, *, max_elements: int = 120) -> Dict[str, Any]:
        """Return compact Browser-Use perception specifically for self-healing.

        This is the concrete Browser-Use contribution to HIP execution: same-CDP
        browser state, tabs and a bounded semantic element inventory.  It never
        clicks or types.  Deterministic scripts remain action authority.
        """
        snapshot = await self.state_snapshot()
        if not snapshot.get("available"):
            return snapshot
        state = snapshot.get("state")
        elements = self._interactive_excerpt(state, limit=max_elements) if state is not None else []
        return mask_sensitive_data({
            "available": True,
            "status": snapshot.get("status", {}),
            "url": snapshot.get("url", ""),
            "title": snapshot.get("title", ""),
            "tabs": snapshot.get("tabs", []),
            "interactive_elements": elements,
            "interactive_element_count": len(elements),
            "policy": "Browser-Use perception only; deterministic HIP skill chooses and verifies actions",
        })

    async def state_snapshot(self) -> Dict[str, Any]:
        """Return bounded structured Browser-Use state for recovery/evidence."""
        if not self.browser or not self.status.attached:
            return {"available": False, "status": self.status.as_dict()}
        result: Dict[str, Any] = {"available": True, "status": self.status.as_dict()}
        try:
            if hasattr(self.browser, "get_current_page_url"):
                result["url"] = await self._bounded_call("get_current_page_url")
            if hasattr(self.browser, "get_current_page_title"):
                result["title"] = await self._bounded_call("get_current_page_title")
            if hasattr(self.browser, "get_tabs"):
                result["tabs"] = self._normalise(await self._bounded_call("get_tabs"))
            state = None
            # API names have evolved; probe known state methods without ever invoking
            # an action. This bridge remains compatible across minor Browser-Use drift.
            for method_name in ("get_browser_state_summary", "get_browser_state"):
                method = getattr(self.browser, method_name, None)
                if method is None:
                    continue
                try:
                    state = await asyncio.wait_for(method(include_screenshot=False), timeout=self.snapshot_timeout_seconds)
                except TypeError:
                    try:
                        state = await asyncio.wait_for(method(), timeout=self.snapshot_timeout_seconds)
                    except Exception:
                        state = None
                except Exception:
                    state = None
                if state is not None:
                    break
            if state is not None:
                result["state"] = self._normalise(state)
        except Exception as exc:
            result["snapshot_error"] = mask_sensitive_string(str(exc))[:1000]
        safe = mask_sensitive_data(result)
        try:
            text = json.dumps(safe, ensure_ascii=False, default=str)
            if len(text) > self.max_state_chars:
                safe = {
                    "available": safe.get("available", True),
                    "status": safe.get("status", {}),
                    "url": safe.get("url", ""),
                    "title": safe.get("title", ""),
                    "truncated": True,
                    "state_excerpt": text[: self.max_state_chars],
                }
        except Exception:
            pass
        return safe
