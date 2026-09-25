"""A local Transport Profile "portal" with saved records and row operations (V243R19).

``/tp`` is the listing: a search box, "+ Add", and per row Edit, Clone and a
"More actions" menu with Merge and Deploy.  Edit and Clone open the Create
Transport Profile wizard pre-filled from the stored record; its branches:

* Profile Usage (Sender / Receiver) decides the Deployment Group options;
* Interface Type SFTP HAFT shows Interface Environment, AS2 shows an
  "AS2 Settings" section (AS2 Identifier, Signing Algorithm).

Deploy opens a dialog: Target Environment UAT, or PROD which reveals Change
Ticket and Approver.  Merge opens a dialog: Merge Into, Keep Source Profile.
Save / Create / Deploy / Merge post to the server, which keeps the records,
so a test can check what was really saved.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).parent / "fixtures"
PHASE = "source_transport_profile"

_LISTING = r"""<!doctype html><html><head><meta charset="utf-8"><title>Transport Profiles</title>
<style>body{font-family:Arial;margin:0} header{background:#0e2e5c;color:#fff;padding:18px} main{padding:20px}
table{border-collapse:collapse;margin-top:12px} td,th{border:1px solid #ccc;padding:6px 10px}
[role=menu]{position:absolute;background:#fff;border:1px solid #777;display:flex;flex-direction:column;z-index:10}
[role=menu][hidden]{display:none} .toast{background:#dff0d8;padding:8px;margin:8px 0}</style></head>
<body><header>Hybrid Integration Platform</header><main><h2>Transport Profiles</h2>
<div id="toast"></div>
<input type="search" id="search" placeholder="Search Transport Profiles" aria-label="Search">
<button type="button" id="add">+ Add</button>
<table><thead><tr><th>Name</th><th>Usage</th><th>Interface</th><th>Status</th><th>Actions</th></tr></thead><tbody id="rows"></tbody></table>
</main>
<script>
const RECORDS = __RECORDS__;
const esc = v => String(v == null ? '' : v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
function render(q) {
  const body = document.getElementById('rows');
  body.innerHTML = '';
  RECORDS.filter(r => !q || r.profileName.toLowerCase().includes(q.toLowerCase())).forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${esc(r.profileName)}</td><td>${esc(r.profileUsage)}</td><td>${esc(r.interfaceType)}</td><td>${esc(r.status)}</td>
      <td style="position:relative"><button type="button" class="edit">Edit</button> <button type="button" class="clone">Clone</button>
      <button type="button" class="more" aria-label="More actions" aria-haspopup="menu">&#8942;</button>
      <div role="menu" hidden><button type="button" role="menuitem" class="merge">Merge</button><button type="button" role="menuitem" class="deploy">Deploy</button></div></td>`;
    const id = encodeURIComponent(r.profileName);
    tr.querySelector('.edit').onclick = () => { location.href = `/tp/form?mode=edit&id=${id}`; };
    tr.querySelector('.clone').onclick = () => { location.href = `/tp/form?mode=clone&id=${id}`; };
    tr.querySelector('.more').onclick = () => { const m = tr.querySelector('[role=menu]'); m.hidden = !m.hidden; };
    tr.querySelector('.merge').onclick = () => { location.href = `/tp/merge?id=${id}`; };
    tr.querySelector('.deploy').onclick = () => { location.href = `/tp/deploy?id=${id}`; };
    body.appendChild(tr);
  });
}
const search = document.getElementById('search');
search.addEventListener('input', () => render(search.value.trim()));
search.addEventListener('keydown', e => { if (e.key === 'Enter') render(search.value.trim()); });
document.getElementById('add').onclick = () => { location.href = '/tp/form?mode=create'; };
const msg = new URLSearchParams(location.search).get('msg');
if (msg) document.getElementById('toast').innerHTML = `<div class="toast" role="status">${esc(msg)}</div>`;
render('');
</script></body></html>"""

_FORM = r"""<!doctype html><html><head><meta charset="utf-8"><title>Transport Profile</title></head><body>
<script>__KIT__</script>
<script>
const CFG = __CFG__;
(function () {
  const H = window.HIP;
  const GROUPS = { Sender: ['dce-default-sender', 'da-sender-sftphaft-dce-shared'], Receiver: ['dce-default-receiver', 'pt-receiver-sftphaft-dce-shared'] };
  const r = CFG.record || {};
  const groupHost = document.createElement('div');
  const ifaceHost = document.createElement('div');
  const renderGroup = (usage, value) => H.when(groupHost, () => usage
    ? H.dropdown({ label: 'Deployment Group *', name: 'deploymentGroup', options: GROUPS[usage], value: value || '' }) : []);
  const renderIface = (type, rec) => H.when(ifaceHost, () => {
    if (type === 'SFTP HAFT') return [H.row(H.dropdown({ label: 'Interface Environment *', name: 'interfaceEnvironment', options: ['UAT', 'PROD'], value: rec.interfaceEnvironment || '' }))];
    if (type === 'AS2') return [H.fieldset('AS2 Settings :', H.row(
      H.text({ label: 'AS2 Identifier *', name: 'as2Identifier', placeholder: 'AS2 Identifier', value: rec.as2Identifier || '' }),
      H.dropdown({ label: 'Signing Algorithm *', name: 'signingAlgorithm', options: ['SHA256', 'SHA1'], value: rec.signingAlgorithm || '' })))];
    return [];
  });
  const form = H.form(
    H.fieldset('Basic Details :', H.row(
      H.text({ label: 'Profile Name *', name: 'profileName', placeholder: 'Profile Name', value: CFG.mode === 'clone' ? (r.profileName || '') + '_COPY' : (r.profileName || '') }),
      H.dropdown({ label: 'Profile Usage *', name: 'profileUsage', options: ['Sender', 'Receiver'], value: r.profileUsage || '',
        onChange: (v) => setTimeout(() => renderGroup(v, ''), 150) }),
      groupHost)),
    H.fieldset('Interface Details :', H.fieldset('Primary Interface Detail :',
      H.row(H.dropdown({ label: 'Interface Type *', name: 'interfaceType', options: ['SFTP HAFT', 'AS2'], value: r.interfaceType || '',
        onChange: (v) => setTimeout(() => renderIface(v, {}), 150) })),
      ifaceHost)),
    H.row(H.dropdown({ label: 'Post Transfer Action *', name: 'postTransferAction', options: ['Move To Archive', 'Delete', 'None'], value: r.postTransferAction || '' })),
  );
  renderGroup(r.profileUsage, r.deploymentGroup);
  renderIface(r.interfaceType, r);
  H.page(CFG.title, form);
  // Drawer header: the title belongs to the form surface, as in the portal drawer.
  const head = document.createElement('h3');
  head.className = 'dds__drawer__title';
  head.textContent = CFG.title;
  form.prepend(head);
  const submit = document.getElementById('submit');
  submit.textContent = CFG.commitLabel;
  document.getElementById('cancel').onclick = () => { location.href = '/tp'; };
  submit.onclick = async () => {
    const values = {};
    document.querySelectorAll('dds-dropdown[formcontrolname]').forEach(dd => { values[dd.getAttribute('formcontrolname')] = dd.querySelector('input').value; });
    document.querySelectorAll('input[formcontrolname]').forEach(i => { if (!i.closest('dds-dropdown')) values[i.getAttribute('formcontrolname')] = i.value; });
    document.querySelectorAll('input[type=radio]:checked').forEach(i => { values[i.name] = i.nextElementSibling.textContent.trim(); });
    const res = await fetch(CFG.api, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: CFG.mode, id: CFG.id, values }) });
    const out = await res.json();
    const note = document.createElement('div');
    note.setAttribute('role', res.ok ? 'status' : 'alert');
    note.className = res.ok ? 'toast' : 'toast--error';
    note.textContent = out.message;
    document.querySelector('main').prepend(note);
    if (res.ok) setTimeout(() => { location.href = '/tp?msg=' + encodeURIComponent(out.message); }, 500);
  };
})();
</script></body></html>"""

_DIALOG = r"""<!doctype html><html><head><meta charset="utf-8"><title>Transport Profile</title></head><body>
<script>__KIT__</script>
<script>
const CFG = __CFG__;
(function () {
  const H = window.HIP;
  const extra = document.createElement('div');
  let children;
  if (CFG.kind === 'deploy') {
    children = [
      H.fieldset('Deployment :', H.row(H.radios({ label: 'Target Environment *', name: 'targetEnvironment', options: [['UAT', 'UAT'], ['PROD', 'PROD']],
        onChange: (v) => setTimeout(() => H.when(extra, () => v === 'PROD' ? [H.fieldset('Production Approval :', H.row(
          H.text({ label: 'Change Ticket *', name: 'changeTicket', placeholder: 'Change Ticket' }),
          H.dropdown({ label: 'Approver *', name: 'approver', options: ['b2b-lead', 'release-manager'] })))] : []), 150) })), extra),
    ];
  } else {
    children = [H.fieldset('Merge :', H.row(
      H.dropdown({ label: 'Merge Into *', name: 'mergeInto', options: CFG.others }),
      H.radios({ label: 'Keep Source Profile *', name: 'keepSourceProfile', options: [['Yes', 'true'], ['No', 'false']] })))];
  }
  const form = H.form(...children);
  H.page(CFG.title, form);
  // Drawer header: the title belongs to the form surface, as in the portal drawer.
  const head = document.createElement('h3');
  head.className = 'dds__drawer__title';
  head.textContent = CFG.title;
  form.prepend(head);
  const submit = document.getElementById('submit');
  submit.textContent = CFG.commitLabel;
  document.getElementById('cancel').onclick = () => { location.href = '/tp'; };
  submit.onclick = async () => {
    const values = {};
    document.querySelectorAll('dds-dropdown[formcontrolname]').forEach(dd => { values[dd.getAttribute('formcontrolname')] = dd.querySelector('input').value; });
    document.querySelectorAll('input[formcontrolname]').forEach(i => { if (!i.closest('dds-dropdown')) values[i.getAttribute('formcontrolname')] = i.value; });
    document.querySelectorAll('input[type=radio]:checked').forEach(i => { values[i.name] = i.nextElementSibling.textContent.trim(); });
    const res = await fetch(CFG.api, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: CFG.id, values }) });
    const out = await res.json();
    const note = document.createElement('div');
    note.setAttribute('role', res.ok ? 'status' : 'alert');
    note.textContent = out.message;
    document.querySelector('main').prepend(note);
    if (res.ok) setTimeout(() => { location.href = '/tp?msg=' + encodeURIComponent(out.message); }, 500);
  };
})();
</script></body></html>"""


def seed_records() -> List[Dict[str, Any]]:
    return [
        {"profileName": "TP_ALPHA", "profileUsage": "Sender", "deploymentGroup": "dce-default-sender", "interfaceType": "SFTP HAFT",
         "interfaceEnvironment": "UAT", "postTransferAction": "None", "status": "Draft"},
        {"profileName": "TP_BETA", "profileUsage": "Receiver", "deploymentGroup": "dce-default-receiver", "interfaceType": "SFTP HAFT",
         "interfaceEnvironment": "UAT", "postTransferAction": "Delete", "status": "Draft"},
    ]


class OperationsPortal:
    """HTTP server for the operations replica; ``records`` is what was saved."""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = seed_records()
        self.posts: List[Dict[str, Any]] = []
        self.kit = (FIXTURES / "hip_dds_kit.js").read_text(encoding="utf-8")
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                url = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                if url.path == "/tp":
                    html = _LISTING.replace("__RECORDS__", json.dumps(portal.records))
                elif url.path == "/tp/form":
                    mode = q.get("mode", "create")
                    record = portal.find(q.get("id", "")) if mode != "create" else {}
                    cfg = {"mode": mode, "id": q.get("id", ""), "record": record or {}, "api": "/api/tp/save",
                           "title": {"create": "Create Transport Profile", "edit": "Edit Transport Profile", "clone": "Clone Transport Profile"}[mode],
                           "commitLabel": "Create" if mode == "create" else "Save"}
                    html = _FORM.replace("__KIT__", portal.kit).replace("__CFG__", json.dumps(cfg))
                elif url.path in {"/tp/deploy", "/tp/merge"}:
                    kind = url.path.rsplit("/", 1)[-1]
                    others = [r["profileName"] for r in portal.records if r["profileName"] != q.get("id", "")]
                    cfg = {"kind": kind, "id": q.get("id", ""), "others": others, "api": f"/api/tp/{kind}",
                           "title": f"{kind.title()} Transport Profile", "commitLabel": kind.title()}
                    html = _DIALOG.replace("__KIT__", portal.kit).replace("__CFG__", json.dumps(cfg))
                else:
                    self._send(404, b"not found", "text/plain")
                    return
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                portal.posts.append({"path": self.path, "body": body})
                code, message = portal.apply(self.path, body)
                self._send(code, json.dumps({"message": message}).encode("utf-8"), "application/json")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def find(self, name: str) -> Dict[str, Any]:
        return next((r for r in self.records if r["profileName"] == name), {})

    def apply(self, path: str, body: Dict[str, Any]) -> tuple:
        values = {k: v for k, v in (body.get("values") or {}).items() if v not in (None, "")}
        if path == "/api/tp/save":
            mode = body.get("mode")
            name = values.get("profileName", "")
            if mode in {"create", "clone"}:
                if self.find(name):
                    return 409, "Transport Profile already exists"
                self.records.append(dict(values, status="Draft"))
                return 200, f"Transport Profile {name} created successfully"
            record = self.find(body.get("id", ""))
            if not record:
                return 404, "Transport Profile not found"
            status = record.get("status", "Draft")
            record.clear()
            record.update(values, status=status)
            return 200, f"Transport Profile {name} updated successfully"
        if path == "/api/tp/deploy":
            record = self.find(body.get("id", ""))
            env = values.get("targetEnvironment", "")
            if env == "PROD" and not values.get("changeTicket"):
                return 400, "Change Ticket is required for PROD"
            record["status"] = f"Deployed {env}"
            record["deployment"] = values
            return 200, f"Transport Profile deployed to {env} successfully"
        if path == "/api/tp/merge":
            source = self.find(body.get("id", ""))
            target = self.find(values.get("mergeInto", ""))
            if not target:
                return 400, "Merge target not found"
            target["status"] = "Merged"
            target["merged_from"] = source.get("profileName")
            if values.get("keepSourceProfile") == "No":
                self.records.remove(source)
            return 200, f"Transport Profile merged into {target['profileName']} successfully"
        return 404, "unknown"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/tp"

    def __enter__(self) -> "OperationsPortal":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
