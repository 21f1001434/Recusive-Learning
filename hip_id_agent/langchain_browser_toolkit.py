from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from .security import mask_sensitive_data, mask_sensitive_string


@dataclass
class LangChainBrowserToolkitStatus:
    enabled: bool
    available: bool = False
    attached: bool = False
    read_only: bool = True
    package: str = "langchain-community"
    version: str = ""
    tools: List[str] | None = None
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "attached": self.attached,
            "read_only": self.read_only,
            "package": self.package,
            "version": self.version,
            "tools": list(self.tools or []),
            "error": self.error,
        }


class LangChainBrowserToolkitBridge:
    """Bounded read-only LangChain Playwright toolkit on the existing HIP Chrome.

    LangChain's PlayWrightBrowserToolkit contains navigation and click tools. HIP does
    not expose those to autonomous recovery because they can navigate arbitrary URLs
    and mutate the page. We initialize the upstream toolkit against the SAME Microsoft Edge/Chromium
    over CDP, but only retain read-only perception tools. Their outputs are advisory
    evidence; the deterministic HIP executor remains action authority.
    """

    def __init__(self, config: Any):
        self.config = config
        self.enabled = bool(getattr(config, "enabled", True))
        self.read_only = bool(getattr(config, "read_only", True))
        self.fail_open = bool(getattr(config, "fail_open_if_unavailable", True))
        self.timeout_seconds = max(1.0, float(getattr(config, "timeout_seconds", 6.0) or 6.0))
        self.max_text_chars = max(1000, int(getattr(config, "max_text_chars", 30000) or 30000))
        self.max_element_chars = max(1000, int(getattr(config, "max_element_chars", 30000) or 30000))
        self.allowed_tools = {str(x) for x in list(getattr(config, "allowed_tools", []) or [])}
        self.browser: Any = None
        self.toolkit: Any = None
        self.tools: Dict[str, Any] = {}
        self.status = LangChainBrowserToolkitStatus(enabled=self.enabled, read_only=self.read_only)

    async def start(self, *, playwright: Any, cdp_url: str) -> Dict[str, Any]:
        if not self.enabled:
            return self.status.as_dict()
        try:
            import importlib.metadata as metadata
            from langchain_community.agent_toolkits.playwright import PlayWrightBrowserToolkit

            try:
                self.status.version = metadata.version("langchain-community")
            except Exception:
                self.status.version = "unknown"
            self.browser = await asyncio.wait_for(playwright.chromium.connect_over_cdp(cdp_url), timeout=15)
            self.toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=self.browser)
            raw_tools = list(self.toolkit.get_tools() or [])
            for tool in raw_tools:
                name = str(getattr(tool, "name", "") or tool.__class__.__name__).strip()
                if name in self.allowed_tools:
                    self.tools[name] = tool
            self.status.available = True
            self.status.attached = True
            self.status.tools = sorted(self.tools)
            return self.status.as_dict()
        except Exception as exc:
            self.status.error = mask_sensitive_string(str(exc))[:1000]
            if not self.fail_open:
                raise RuntimeError(f"LangChain Playwright toolkit failed to attach: {self.status.error}") from exc
            return self.status.as_dict()

    async def stop(self) -> None:
        # Do not call Browser.close() on a CDP-attached object: depending on
        # Playwright/browser version that can affect the shared browser lifecycle.
        # The owning BrowserSession stops Playwright after all bridges are released.
        self.browser = None
        self.toolkit = None
        self.tools = {}
        self.status.attached = False

    async def _invoke(self, name: str, payload: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            return {"available": False, "tool": name, "reason": "tool not exposed by read-only policy"}
        try:
            call = getattr(tool, "ainvoke", None)
            if callable(call):
                result = await asyncio.wait_for(call(dict(payload or {})), timeout=self.timeout_seconds)
            else:
                arun = getattr(tool, "arun", None)
                if not callable(arun):
                    return {"available": False, "tool": name, "reason": "tool has no async invocation method"}
                result = await asyncio.wait_for(arun(dict(payload or {})), timeout=self.timeout_seconds)
            return {"available": True, "tool": name, "result": result}
        except Exception as exc:
            return {"available": False, "tool": name, "error": mask_sensitive_string(str(exc))[:1000]}

    async def recovery_context(self) -> Dict[str, Any]:
        """Use actual LangChain Playwright toolkit tools as bounded perception."""
        if not self.status.attached:
            return {"available": False, "status": self.status.as_dict()}
        current = await self._invoke("current_webpage")
        text = await self._invoke("extract_text")
        elements = await self._invoke(
            "get_elements",
            {
                "selector": "input,textarea,select,button,[role=combobox],[role=button],[role=checkbox],[role=radio]",
                "attributes": ["aria-label", "role", "formcontrolname", "disabled", "aria-expanded", "aria-checked"],
            },
        )
        links = await self._invoke("extract_hyperlinks")

        def bounded(row: Dict[str, Any], limit: int) -> Dict[str, Any]:
            if not row.get("available"):
                return row
            value = str(row.get("result") or "")
            return {**row, "result": value[:limit], "truncated": len(value) > limit}

        return mask_sensitive_data({
            "available": True,
            "status": self.status.as_dict(),
            "current_webpage": bounded(current, 4000),
            "extract_text": bounded(text, self.max_text_chars),
            "get_elements": bounded(elements, self.max_element_chars),
            "extract_hyperlinks": bounded(links, 12000),
            "policy": "LangChain toolkit is read-only recovery perception; navigate/click tools are not exposed",
        })
