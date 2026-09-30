"""V243R32 replica: a portal that answers with Spring Boot's "Whitelabel Error Page".

``WhitelabelPortal`` serves the Document Type form replica (the stuck-spinner
replica's page, loader never stuck) at ``/doctypes`` and counts its loads:

* on an *armed* load the page itself navigates to ``/error`` after a number of
  field changes -- the portal replaces the half-filled form with its error page,
  as reported on the live portal;
* an *error* load answers the phase link itself with the error page (HTTP 500);
* every other load is the normal form.

``/error`` is the Whitelabel Error Page, word for word as Spring Boot renders it.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable

from loader_portal_support import FIXTURES, _LOADER_JS

WHITELABEL_HTML = """<!DOCTYPE html><html><head><title>Error</title></head><body>
<h1>Whitelabel Error Page</h1>
<p>This application has no explicit mapping for /error, so you are seeing this as a fallback.</p>
<div id="created">Tue Sep 30 10:12:44 UTC 2026</div>
<div>There was an unexpected error (type=Internal Server Error, status=500).</div>
</body></html>"""

_ARMED_JS = """<script>
(function () {
  if (!window.__whitelabelArmed) return;
  let n = 0;
  const hit = () => {
    n += 1;
    if (n === (window.__whitelabelAfterChanges || 4)) setTimeout(() => { location.href = '/error'; }, 60);
  };
  document.addEventListener('change', hit, true);
})();
</script>"""


class WhitelabelPortal:
    def __init__(self, *, armed_loads: Iterable[int] = (1,), error_loads: Iterable[int] = (), after_changes: int = 4,
                 attribute_rows: int = 1):
        self.loads = 0
        self.error_pages = 0
        self.armed_loads = set(int(x) for x in armed_loads)
        self.error_loads = set(int(x) for x in error_loads)
        html = (FIXTURES / "document_type_full_dds.html").read_text(encoding="utf-8")
        html = html.replace("<script>", f"<script>window.__attributeRows = {int(attribute_rows)};</script><script>", 1)
        self.html = html.replace("</body>", _LOADER_JS + _ARMED_JS + "</body>")
        self.after_changes = int(after_changes)
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def _send(self, status: int, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                if self.path.startswith("/doctypes"):
                    portal.loads += 1
                    if portal.loads in portal.error_loads:
                        portal.error_pages += 1
                        return self._send(500, WHITELABEL_HTML.encode("utf-8"), "text/html; charset=utf-8")
                    html = portal.html
                    if portal.loads in portal.armed_loads:
                        html = html.replace(
                            "<script>", f"<script>window.__whitelabelArmed = true; window.__whitelabelAfterChanges = {portal.after_changes};</script><script>", 1)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                if self.path.startswith("/error"):
                    portal.error_pages += 1
                    return self._send(500, WHITELABEL_HTML.encode("utf-8"), "text/html; charset=utf-8")
                if self.path.startswith("/request-state"):
                    return self._send(200, b"done", "text/plain")
                self._send(404, b"", "text/plain")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/doctypes"

    def __enter__(self) -> "WhitelabelPortal":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
