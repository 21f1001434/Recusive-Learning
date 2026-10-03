"""A local copy of the HIP portal for whole-mission runs (V243R38).

The real ``FullDummyFillE2EFlow`` (the code path of a Control Center mission)
runs against this server instead of ``developer.dell.com``.  Chromium is told
to resolve ``developer.dell.com`` to the local port
(``--host-resolver-rules``), so every URL, the SSO check, the route checks and
the phase handoffs see the real portal addresses.

Every module (Data Maps, Document Types, Rules, Transport Profiles, Business
Flows) is served at its real path as the portal's app shell: a fixed header, a
left navigation that names every module, and a listing with "+ Add".  "+ Add"
opens that module's full create-form replica (``tests/fixtures``) on the same
URL, as on the live portal.

V243R39: every module's listing is the R30/R31 listing replica
(``phase_listing_support`` / ``doctypes_listing_support``) at the module's real
path -- rows with the expander, environment tabs and Edit / Clone / Deploy /
Migrate, records kept by the server -- so a mission can learn (and an
authorized run can perform) each operation on the same portal it creates on.
Business Flow's "+ Add" opens the flow-template picker; the template's link
opens the Create Biz Flow wizard (tabs ahead are locked; Next moves forward).

Faults, per module and page load (``fail``):

* ``loader``: the shell and navigation render, but the module never does --
  a DDS loading indicator spins forever (the live "infinite loading");
* ``home``: the page sends the browser to the portal home (a non-target
  surface that is neither the module nor the SSO page);
* ``blank``: an empty page.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

FIXTURES = Path(__file__).parent / "fixtures"
HOST = "developer.dell.com"

# Each module's listing replica: (support module, family).
LISTING_FAMILY = {"datamaps": "datamaps", "rules": "rules", "transportprofiles": "transport_profiles",
                  "bizflows": "bizflows", "doctypes": "doctypes"}

MODULES: Dict[str, Dict[str, Any]] = {
    "datamaps": {
        "path": "/hybrid-integrations/securelink/datamaps", "title": "Data Maps", "nav": "Data Maps",
        "fixture": "data_map_full_dds.html", "surface": "drawer", "columns": ["Map Identifier", "Map Name", "Contivo version"],
        "rows": [["DELLCoXMLASNXX07C_ACME", "DELLCoXMLASNXX07C", "6.0"], ["DELLCoXMLPOXX03_ACME", "DELLCoXMLPOXX03", "6.0"]],
    },
    "doctypes": {
        "path": "/hybrid-integrations/securelink/doctypes", "title": "Document Types", "nav": "Document Types",
        "fixture": "document_type_full_dds.html", "surface": "drawer", "columns": ["Name", "Data Format", "Transaction Type", "Version"],
        "rows": [["XML_DellAutoPO_10_ACME_IB", "XML", "850", "1.0"], ["XML_INVOICE_10_ACME_OB", "XML", "810", "1.0"]],
    },
    "rules": {
        "path": "/hybrid-integrations/securelink/rules", "title": "Rules", "nav": "Rules",
        "fixture": "rule_full_dds.html", "surface": "page", "columns": ["Name", "Rule Type", "Document Type"],
        "rows": [["DELLCoXMLPOXX03_ACME_RULE", "Mapping", "XML_DellAutoPO_10_ACME_IB(1.0)"]],
    },
    "transportprofiles": {
        "path": "/hybrid-integrations/securelink/transportprofiles", "title": "Transport Profiles", "nav": "Transport Profiles",
        "fixture": "transport_profile_full_dds.html", "surface": "drawer", "columns": ["Profile Name", "Profile Usage", "Interface Type"],
        "rows": [["SFTP_ACME_PO_SRC_IB", "Sender", "SFTP HAFT"], ["SFTP_ACME_INV_TGT_OB", "Receiver", "SFTP HAFT"]],
    },
    "bizflows": {
        # V243R39 (golden BizFlow-Deployed / FD / CR): "Manage Biz Flow"; + Add opens
        # the flow-template picker, the template's link (or its ⋮ > Create Biz Flow)
        # opens the Create Biz Flow wizard, whose tabs advance with Next.
        "path": "/hybrid-integrations/bizexchange/bizflows", "title": "Manage Biz Flow", "nav": "Biz Flows",
        "fixture": "bizflow_wizard_dds.html", "surface": "page", "picker": True,
        "columns": ["Flow Name", "Flow Type", "Primary Domains", "Source System", "Target Systems"],
        "rows": [["ACME_PC_850_PO_MAPPING_IB", "Inbound", "Customer Experience (CX)", "AIC - DCE", "dce-test-partner"]],
    },
}
HOME_PATH = "/hybrid-integrations/home"

_SHELL = r"""<!doctype html><html><head><meta charset="utf-8"><title>Dell Technologies Developer</title>
__HEAD__
<style>
  html { scroll-behavior: smooth; }
  body { margin: 0; font-family: Arial, sans-serif; font-size: 13px; }
  .app-header { position: fixed; top: 0; left: 0; right: 0; height: 56px; background: #0e2e5c; color: #fff;
                display: flex; align-items: center; padding: 0 20px; z-index: 1000; }
  nav.side { position: fixed; top: 56px; left: 0; bottom: 0; width: 190px; background: #f2f2f2; padding: 12px; }
  nav.side a { display: block; padding: 6px 4px; color: #0672cb; text-decoration: none; }
  #app { margin: 56px 0 0 214px; padding: 16px 24px; }
  .ribbon { display: flex; gap: 10px; align-items: center; margin: 10px 0; }
  .dds__table { display: table; border-collapse: collapse; }
  .dds__tr { display: table-row; }
  .dds__th, .dds__td { display: table-cell; border-bottom: 1px solid #ddd; padding: 6px 10px; }
  .dds__loading-indicator { margin: 80px auto; width: 60px; height: 60px; border: 6px solid #ccc;
                            border-top-color: #0672cb; border-radius: 50%; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  app-generic-drawer .dds__drawer { position: fixed; top: 56px; right: 0; bottom: 0; width: 72%; background: #fff;
                                    box-shadow: -4px 0 12px rgba(0,0,0,.25); overflow: auto; z-index: 1500; }
  .dds__drawer__header { padding: 12px 20px; border-bottom: 1px solid #ddd; }
  .dds__drawer__body { padding: 12px 20px 0 20px; }
  .dds__action-menu:not([hidden]) { display: flex; }
  .dds__action-menu[hidden] { display: none; }
</style></head>
<body>
<header class="app-header">DELL Technologies Developer &nbsp;&middot;&nbsp; Hybrid Integrations</header>
<nav class="side" aria-label="Hybrid Integrations">__NAV__</nav>
<div id="app">__APP__</div>
<script>window.__HIP_SIM = __STATE__;</script>
__SCRIPTS__
</body></html>"""

_LOADING = ('<app-loadingindicator><div class="dds__loading-indicator__overlay" aria-busy="true">'
            '<div class="dds__loading-indicator" role="progressbar" aria-label="Loading"></div></div></app-loadingindicator>')

# "+ Add" renders the module's create form on the same URL.  Kit fixtures build
# the form with HIP.page (which replaces the page body, as the SPA swaps its
# view); the static Document Type fixture is mounted from its markup.
_ADD_JS = r"""
<script>
(function () {
  const CFG = window.__HIP_SIM;
  // Data Map, Document Type and Transport Profile open "Create ..." as a modal
  // drawer over the listing (app-generic-drawer, role=dialog, aria-modal) -- the
  // URL stays; Rule and Biz Flow replace the listing with the form page.
  function drawer(title) {
    const host = document.createElement('app-generic-drawer');
    host.setAttribute('data-sim-create', '');
    host.innerHTML = '<div class="dds__drawer dds__drawer--open" role="dialog" aria-modal="true" aria-label="' + title + '">'
      + '<div class="dds__drawer__header"><h3 class="dds__drawer__title">' + title + '</h3></div>'
      + '<div class="dds__drawer__body"></div></div>';
    document.body.appendChild(host);
    return host.querySelector('.dds__drawer__body');
  }
  function mountStatic() {
    const tpl = document.getElementById('hip-sim-form');
    const code = document.getElementById('hip-sim-form-script').textContent;
    const style = document.createElement('style');
    style.textContent = tpl.dataset.style || '';
    document.head.appendChild(style);
    const holder = document.createElement('div');
    holder.innerHTML = tpl.innerHTML;
    const title = (holder.querySelector('h2') || {}).textContent || 'Create';
    const body = drawer(title);
    holder.querySelectorAll('main > *:not(h2)').forEach((n) => body.appendChild(n));
    (new Function(code))();
  }
  function mountKit() {
    const H = window.HIP;
    const text = H.text, page = H.page;
    H.text = (o) => text(Object.assign({}, o, { duplicateMessage: '' }));
    if (CFG.surface === 'drawer') {
      H.page = (title, form) => {
        const keep = Array.from(document.body.childNodes);
        page(title, form);            // adds the kit's form styles
        document.body.innerHTML = '';
        keep.forEach((n) => document.body.appendChild(n));
        drawer(title).appendChild(form);
        return form;
      };
    }
    try { (new Function(document.getElementById('hip-sim-form-script').textContent))(); }
    finally { H.text = text; H.page = page; }
    if (CFG.surface !== 'drawer') document.body.setAttribute('data-sim-create', '');
  }
  // V243R39: Biz Flow's + Add first shows the flow templates (golden: the
  // B2B-Flow-PubSub-Template card).  The template's name is a link; its ⋮ menu
  // also holds "Create Biz Flow".  Either opens the Create Biz Flow wizard.
  function picker() {
    const app = document.getElementById('app') || document.querySelector('main');
    app.innerHTML = '<a class="dds__link" id="picker-back" href="javascript:void(0)">&#8249; Back</a>'
      + '<h1>Create Biz Flow</h1><p>Select a flow template to create a Biz Flow.</p>'
      + '<input type="text" placeholder="Search flow templates" aria-label="Search flow templates" style="width:360px">'
      + '<div class="template-grid" style="display:flex;gap:16px;margin-top:14px">'
      + card('B2B-Flow-PubSub-Template', 'Create B2B flow tailored for Inbound/Outbound integration.', 'tpl-pubsub')
      + card('A2A-Flow-Passthrough-Template', 'Pass documents between applications without transformation.', 'tpl-a2a')
      + '</div>';
    app.querySelectorAll('.template-card').forEach((c) => {
      const link = c.querySelector('a.template-link');
      link.addEventListener('click', (e) => { e.preventDefault(); launch(c.dataset.template); });
      const more = c.querySelector('.template-more');
      const menu = c.querySelector('[role=menu]');
      more.addEventListener('click', (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; more.setAttribute('aria-expanded', String(!menu.hidden)); });
      menu.querySelector('.template-create').addEventListener('click', () => launch(c.dataset.template));
    });
    document.getElementById('picker-back').addEventListener('click', () => location.assign(location.pathname));
  }
  function card(name, text, id) {
    return '<div class="dds__card template-card" data-template="' + name + '" style="border:1px solid #ccc;padding:12px;width:300px;position:relative">'
      + '<div class="dds__card__header" style="display:flex;justify-content:space-between">'
      + '<a class="dds__link template-link" href="javascript:void(0)">' + name + '</a>'
      + '<button type="button" class="dds__action-menu__trigger template-more" aria-haspopup="true" aria-expanded="false" aria-label="More Actions">&#8942;</button>'
      + '<div class="dds__action-menu" role="menu" hidden style="position:absolute;right:8px;top:36px;background:#fff;border:1px solid #999;flex-direction:column">'
      + '<button type="button" role="menuitem" class="template-create">Create Biz Flow</button>'
      + '<button type="button" role="menuitem">View Template</button></div></div>'
      + '<p>' + text + '</p>'
      + '<span class="dds__tag disable-tag-click">Inbound</span> <span class="dds__tag disable-tag-click">Outbound</span></div>';
  }
  function launch(template) {
    window.__hipTemplateChosen = template;
    if (template !== 'B2B-Flow-PubSub-Template') {
      (document.getElementById('app') || document.querySelector('main')).innerHTML = '<h1>Create Biz Flow</h1><p>' + template + ' is not available for this partner.</p>';
      return;
    }
    setTimeout(() => mountKit(), CFG.addDelayMs || 150);
  }
  function open() {
    if (document.querySelector('app-generic-drawer')) return;
    if (CFG.picker) { setTimeout(picker, CFG.addDelayMs || 150); return; }
    setTimeout(() => (CFG.static ? mountStatic() : mountKit()), CFG.addDelayMs || 150);
  }
  document.addEventListener('click', (e) => {
    const b = e.target.closest('#add');
    if (b) { e.preventDefault(); open(); return; }
    // Cancel discards the unsaved form: the drawer closes, a form page returns
    // to the listing (as the portal's Cancel does).  Submit is never simulated.
    // (Only the create form's Cancel: a listing's Edit / Clone drawer closes itself.)
    const c = e.target.closest('#cancel');
    if (c && c.closest('[data-sim-create]')) {
      e.preventDefault();
      const host = document.querySelector('app-generic-drawer');
      if (host) host.remove(); else location.assign(location.pathname);
    }
  });
})();
</script>"""


def _fixture_parts(fixture: str) -> Dict[str, str]:
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    kit_match = re.search(r"<!--HIP_DDS_KIT-->\s*<script>(.*?)</script>", html, re.S)
    if kit_match:
        return {"kind": "kit", "script": kit_match.group(1)}
    style = re.search(r"<style>(.*?)</style>", html, re.S)
    body = re.search(r"<body>(.*?)<script>", html, re.S)
    script = re.search(r"<script>(.*)</script>", html.split("</form>", 1)[-1], re.S)
    return {"kind": "static", "style": style.group(1) if style else "", "body": body.group(1) if body else "",
            "script": script.group(1) if script else ""}


def make_certificate(directory: Path) -> Dict[str, str]:
    """A self-signed certificate for developer.dell.com (Chromium ignores the issuer)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - _dt.timedelta(days=1))
            .not_valid_after(now + _dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    return {"cert": str(cert_path), "key": str(key_path)}


class HipPortalSim:
    """HTTPS server for every HIP module at its real path, with injectable faults."""

    def __init__(self, cert_dir: Path, *, attribute_rows: int = 5, live_plus: bool = True, live_wizard: bool = True,
                 listings: bool = True) -> None:
        self.loads: Dict[str, int] = {key: 0 for key in MODULES}
        self.served: List[Dict[str, Any]] = []
        self.posts: List[Dict[str, Any]] = []
        self.faults: Dict[str, List[Dict[str, Any]]] = {key: [] for key in MODULES}
        self.attribute_rows = int(attribute_rows)
        self.live_plus = bool(live_plus)
        self.live_wizard = bool(live_wizard)
        self.kit = (FIXTURES / "hip_dds_kit.js").read_text(encoding="utf-8")
        self.parts = {key: _fixture_parts(spec["fixture"]) for key, spec in MODULES.items()}
        self.listings = self._listing_stores() if listings else {}
        self.lock = threading.Lock()
        sim = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # quiet test output
                return

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path == "/favicon.ico":
                    self._send(204, b"", "image/x-icon")
                    return
                if path.startswith("/api/") or "/api/" in path:
                    self._send(200, json.dumps(sim.api_records(path)).encode("utf-8"), "application/json")
                    return
                module = sim.module_for(path)
                if module:
                    html = sim.module_page(module, path)
                elif path in {"/", "/hybrid-integrations", HOME_PATH}:
                    html = sim.home_page()
                else:
                    self._send(404, b"not found", "text/plain")
                    return
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                code, message = sim.api_post(urlparse(self.path).path, body)
                self._send(code, json.dumps({"message": message}).encode("utf-8"), "application/json")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        paths = make_certificate(Path(cert_dir))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(paths["cert"], paths["key"])
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # ---- addresses -----------------------------------------------------------------
    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def launch_args(self) -> List[str]:
        return [f"--host-resolver-rules=MAP {HOST} 127.0.0.1:{self.port}", "--ignore-certificate-errors", "--no-proxy-server"]

    @staticmethod
    def url(module: str) -> str:
        return f"https://{HOST}{MODULES[module]['path']}"

    @staticmethod
    def module_for(path: str) -> str:
        for key, spec in MODULES.items():
            if path == spec["path"] or path.startswith(spec["path"] + "/"):
                return key
        return ""

    # ---- faults --------------------------------------------------------------------
    def fail(self, module: str, kind: str, *, loads: int = 1, after: int = 0) -> None:
        """Serve ``kind`` for ``loads`` page loads of ``module`` once ``after`` loads were served normally."""
        if kind not in {"loader", "home", "blank"}:
            raise ValueError(kind)
        with self.lock:
            start = self.loads[module] + int(after) + 1
            self.faults[module].append({"kind": kind, "from": start, "to": start + int(loads) - 1})

    def _fault(self, module: str, load: int) -> str:
        for fault in self.faults[module]:
            if fault["from"] <= load <= fault["to"]:
                return fault["kind"]
        return ""

    # ---- pages ---------------------------------------------------------------------
    def _nav(self) -> str:
        links = "".join(f'<a href="{spec["path"]}">{spec["nav"]}</a>' for spec in MODULES.values())
        return f'<a href="{HOME_PATH}">Home</a>{links}'

    def _shell(self, app: str, state: Dict[str, Any], scripts: str = "", head: str = "") -> str:
        # Hidden form sources live in <head>: page detectors read the body's HTML.
        return (_SHELL.replace("__HEAD__", head).replace("__NAV__", self._nav()).replace("__APP__", app)
                .replace("__STATE__", json.dumps(state)).replace("__SCRIPTS__", scripts))

    def home_page(self) -> str:
        app = ('<h1>Hybrid Integrations</h1><p>Welcome to the Dell Hybrid Integration Platform. '
               'Choose a module in the menu to continue.</p>')
        return self._shell(app, {"module": "", "fault": ""})

    # ---- listings (V243R39) ---------------------------------------------------------
    def _listing_stores(self) -> Dict[str, Any]:
        from doctypes_listing_support import DocTypesPortal
        from phase_listing_support import FAMILIES, PhaseListingPortal

        stores: Dict[str, Any] = {}
        for module, family in LISTING_FAMILY.items():
            spec = MODULES[module]
            if family == "doctypes":
                store = DocTypesPortal(live_path=True, expand_delay_ms=150)
            else:
                store = PhaseListingPortal(family)
                store.spec = {**FAMILIES[family], "path": spec["path"], "title": spec["title"]}
            store.server.server_close()  # the records and pages are served here
            stores[module] = store
        return stores

    def api_records(self, path: str) -> Any:
        for module, store in self.listings.items():
            api = "/api/doctypes" if LISTING_FAMILY[module] == "doctypes" else store.api
            if path.rstrip("/") == api:
                return store.records
        return []

    def api_post(self, path: str, body: Dict[str, Any]) -> tuple:
        with self.lock:
            self.posts.append({"path": path, "body": body})
        for module, store in self.listings.items():
            if LISTING_FAMILY[module] == "doctypes":
                if path.startswith("/api/doctypes/"):
                    return store.apply(path, body)
                continue
            if path.startswith(store.api + "/"):
                action = path.rsplit("/", 1)[-1]
                if action in {"deploy", "migrate"}:
                    return store.promote(body, action)
                if action == "clone":
                    return store.clone(body)
                return store.save(body)
        return 200, "ok"

    def _listing_page(self, module: str, sub: str, head: str, scripts: str, state: Dict[str, Any]) -> str:
        from doctypes_listing_support import _PAGE

        store = self.listings[module]
        if LISTING_FAMILY[module] == "doctypes":
            html = _PAGE.replace("__KIT__", self.kit).replace("__CFG__", json.dumps(store.cfg))
        elif sub.startswith(("/edit/", "/clone/")):
            from urllib.parse import unquote

            mode = "clone" if sub.startswith("/clone/") else "edit"
            record = store.find(unquote(sub.split("/", 2)[2]))
            if not record:
                return "<!doctype html><html><body><h1>Not found</h1></body></html>"
            return store.edit_page_html(record, mode)
        else:
            html = store.listing_html()
        nav = (f'<nav class="side" aria-label="Hybrid Integrations" style="display:flex;gap:14px;padding:6px 20px;background:#f2f2f2">'
               f'{self._nav()}</nav>')
        state_js = f"<script>window.__HIP_SIM = {json.dumps(state)};</script>"
        html = html.replace("</head>", head + "</head>", 1)
        html = html.replace("</header>", "</header>" + nav, 1)
        # The kit is on the listing page already; add the sim's state, setup and "+ Add".
        scripts = scripts.replace(f"<script>{self.kit}</script>", "")
        return html.replace("</body>", state_js + scripts + "</body>", 1)

    def module_page(self, module: str, path: str = "") -> str:
        with self.lock:
            self.loads[module] += 1
            load = self.loads[module]
            fault = self._fault(module, load)
            self.served.append({"module": module, "load": load, "fault": fault})
        spec = MODULES[module]
        if fault == "blank":
            return "<!doctype html><html><head><title></title></head><body></body></html>"
        if fault == "home":
            return self._shell(_LOADING, {"module": module, "fault": fault},
                               f'<script>setTimeout(() => location.replace("{HOME_PATH}"), 200);</script>')
        if fault == "loader":
            return self._shell(_LOADING, {"module": module, "fault": fault})
        parts = self.parts[module]
        head = "".join(f'<div role="columnheader" class="dds__th">{c}</div>' for c in spec["columns"])
        rows = "".join('<div role="row" class="dds__tr">' + "".join(f'<div role="cell" class="dds__td">{c}</div>' for c in row)
                       + "</div>" for row in spec["rows"])
        app = (f'<h1>{spec["title"]}</h1><div class="ribbon">'
               '<button type="button" class="dds__button dds__button--tertiary">Filter</button>'
               '<input type="text" placeholder="Table search" aria-label="Table search">'
               '<button type="button" class="dds__button dds__button--tertiary" id="add">+ Add</button></div>'
               f'<div role="table" class="dds__table"><div role="row" class="dds__tr">{head}</div>{rows}</div>')
        state = {"module": module, "fault": "", "load": load, "static": parts["kind"] == "static",
                 "surface": spec.get("surface", "page"), "picker": bool(spec.get("picker"))}
        # Live-faithful: every Document Type section is on the form from the start
        # (its lookups list values once Data Format Type is chosen), and rows are
        # added with the DDS "+" (R20/R25 recordings).
        setup = (f"window.__attributeRows = {self.attribute_rows}; window.__liveOptionsAfterFormat = true;"
                 + ("window.__livePlus = true;" if self.live_plus else "")
                 + ("window.__liveWizard = true;" if self.live_wizard else ""))
        head = f'<script type="text/plain" id="hip-sim-form-script">{parts["script"]}</script>'
        if parts["kind"] == "static":
            style = parts["style"].replace('"', "&quot;")
            head += f'<template id="hip-sim-form" data-style="{style}">{parts["body"]}</template>'
        scripts = f"<script>{setup}</script><script>{self.kit}</script>{_ADD_JS}"
        if self.listings:
            sub = path[len(spec["path"]):] if path.startswith(spec["path"]) else ""
            return self._listing_page(module, sub, head, scripts, state)
        return self._shell(app, state, scripts, head)

    # ---- lifecycle -----------------------------------------------------------------
    def __enter__(self) -> "HipPortalSim":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


def sim_session_config(tmp: Path, sim: HipPortalSim, *, loading_seconds: float = 2.0) -> Any:
    """A real BrowserSession config (no MCP servers) whose Chromium reaches the simulator."""
    from loader_portal_support import real_session_config

    cfg = real_session_config(tmp, loading_seconds=loading_seconds)
    args = list(getattr(cfg.portal, "launch_args", None) or [])
    cfg.portal.launch_args = [*args, *sim.launch_args]
    cfg.reporting.runs_dir = str(tmp / "runs")
    # As ``hip-agent run-full-dummy-fill --allow-executor-fallback``: the MCP
    # servers are attempted, but missing MCP evidence cannot block a safe action.
    cfg.mcp.strict_runtime_required = False
    cfg.mcp.hip_intelligence_mcp_required = False
    cfg.semantic_understanding.strict_external_evidence = False
    return cfg


def mission_input(tmp: Path, phases: Optional[List[str]] = None) -> Path:
    """The U-Haul example input.json with a local Data Map upload, narrowed to ``phases``."""
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    uploads = tmp / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    jar = uploads / "Transform_DELLCoXMLASNXX08C.jar"
    jar.write_bytes(b"PK\x03\x04replica")
    objects = payload.get("objects") or {}
    if isinstance(objects.get("data_map"), dict):
        objects["data_map"]["map_data_file"] = str(jar)
    path = tmp / "input.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
