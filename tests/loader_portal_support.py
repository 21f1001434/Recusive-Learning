"""A local "portal" whose loading spinner can get stuck (V243R18).

Serves the Document Type replica over HTTP.  Typing Transaction Type starts
a portal request: a full-screen DDS loading overlay appears and the rest of
the form is disabled until the request finishes.  The server counts page
loads; for the first ``stuck_loads`` loads the request never finishes, as on
the live portal where the spinner stays until the page is refreshed or the
browser is restarted.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

FIXTURES = Path(__file__).parent / "fixtures"

_LOADER_JS = r"""
<script>
(function () {
  function hook() {
    const tx = document.querySelector('input[name=transactionType]');
    if (!tx || tx.__hipLoaderHooked) return;
    tx.__hipLoaderHooked = true;
    tx.addEventListener('input', () => {
      if (document.getElementById('hip-loader')) return;
      const host = document.createElement('app-loadingindicator');
      host.id = 'hip-loader';
      host.innerHTML = '<div class="dds__loading-indicator__overlay" style="position:fixed;left:0;top:0;right:0;bottom:0;z-index:5000;background:rgba(255,255,255,.55);display:flex;align-items:center;justify-content:center">'
        + '<div class="dds__loading-indicator" role="progressbar" aria-busy="true">Loading...</div></div>';
      document.body.appendChild(host);
      document.body.classList.add('dds__loading-indicator__overlay--overflow-hidden');
      const locked = Array.from(document.querySelectorAll('input,textarea')).filter((el) => el !== tx && !el.disabled);
      locked.forEach((el) => { el.disabled = true; });
      fetch('/request-state').then((r) => r.text()).then((state) => {
        if (state.trim() === 'stuck') return;  // the portal request never completes
        setTimeout(() => {
          host.remove();
          document.body.classList.remove('dds__loading-indicator__overlay--overflow-hidden');
          locked.forEach((el) => { el.disabled = false; });
        }, 400);
      });
    });
  }
  document.addEventListener('DOMContentLoaded', hook);
  setInterval(hook, 200);
})();
</script>
"""


class LoaderPortal:
    """HTTP server for the stuck-spinner replica; ``loads`` counts page loads."""

    def __init__(self, *, stuck_loads: int, attribute_rows: int = 2):
        self.stuck_loads = int(stuck_loads)
        self.loads = 0
        html = (FIXTURES / "document_type_full_dds.html").read_text(encoding="utf-8")
        html = html.replace("<script>", f"<script>window.__attributeRows = {int(attribute_rows)};</script><script>", 1)
        self.html = html.replace("</body>", _LOADER_JS + "</body>")
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # quiet test output
                return

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                if self.path.startswith("/doctypes"):
                    portal.loads += 1
                    body = portal.html.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path.startswith("/request-state"):
                    body = (b"stuck" if portal.loads <= portal.stuck_loads else b"done")
                    ctype = "text/plain"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/doctypes"

    def __enter__(self) -> "LoaderPortal":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


def real_session_config(tmp: Path, *, loading_seconds: float = 2.0) -> Any:
    """AppConfig for a real, locally launched BrowserSession without MCP servers."""
    from hip_id_agent.config import AppConfig

    cfg = AppConfig()
    cfg.portal.headless = True
    cfg.portal.chromium_channel = None
    cfg.portal.chrome_executable_path = "/opt/pw-browsers/chromium"
    for attr in ("browser_user_data_dir", "chrome_user_data_dir", "chromium_user_data_dir"):
        setattr(cfg.portal, attr, str(tmp / "profile"))
    cfg.portal.loading_watchdog_timeout_seconds = loading_seconds
    cfg.portal.loading_watchdog_poll_seconds = 0.25
    cfg.portal.autonomous_page_health_enabled = False
    cfg.reporting.memory_dir = str(tmp / "memory")
    cfg.mcp.browser_backend = "playwright"
    for name in ("use_playwright_mcp", "use_chrome_devtools_mcp", "use_pyautogui_mcp",
                 "require_playwright_mcp", "require_chrome_devtools_mcp"):
        if hasattr(cfg.mcp, name):
            setattr(cfg.mcp, name, False)
    if getattr(cfg, "browser_use", None) is not None:
        cfg.browser_use.enabled = False
    # Text-only model (gpt-oss-120b): no vision confirmation is available.
    if getattr(cfg, "vision_runtime", None) is not None:
        cfg.vision_runtime.use_for_loading_watchdog = False
    cfg.aia.enabled = False
    cfg.runtime_self_heal.loader_grace_seconds = 2.0
    cfg.runtime_self_heal.max_phase_attempts = 5
    return cfg


def patch_navigation(session: Any) -> Dict[str, int]:
    """Replace Dell SSO/module routing with a plain navigation to the local portal.

    The live MCP evidence servers are not running in tests, so the semantic
    action gate is switched off as in ``phase_replica_support.attach_broker_session``.
    """
    calls = {"goto": 0}
    if getattr(session, "semantic_action_gate", None) is not None:
        session.semantic_action_gate.enabled = False

    async def goto(target_url: str = "") -> None:
        calls["goto"] += 1
        session._active_target_url = target_url
        page = await session._ensure_active_page(target_url)
        await page.goto(target_url, wait_until="domcontentloaded")

    session.goto_base_and_complete_sso = goto
    return calls
