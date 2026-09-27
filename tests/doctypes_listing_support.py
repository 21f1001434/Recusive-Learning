"""A local Document Types listing, as recorded on the live portal (V243R24).

``/securelink/doctypes`` mirrors the live page (screenshots and DevTools of
2026-09-27):

* a DDS table: "Table search", Manage Columns, "+ Add"; rows are
  ``div[role=row].dds__tr`` whose first cell holds the row expander
  ``button.dds__td--expandable__button`` (aria-label "Expand the row",
  aria-expanded) -- there is no action button on the row itself;
* expanding renders, a moment later, a sibling ``div[role=row].dds__tr--expanded``
  with ``.dds__td--expandable-content``: "Description: ...", environment tabs
  DEV / TEST1 / TEST2 / PROD (unavailable ones disabled), a "Version" dropdown,
  the Edit / Clone / Migrate buttons (``dds__button--secondary --sm``) and the
  read-only Document Type Details;
* Migrate (aria-expanded, aria-controls) opens a menu of target environments
  (from DEV: TEST1, TEST2; from TEST1: TEST2; from TEST2: PROD);
* Edit / Clone open "Edit Document Type" / "Clone Document Type" as an in-page
  drawer over the listing (the URL does not change), pre-filled from the
  record; Clone keeps the source name, which the server rejects as a
  duplicate.

The server keeps the records, so a test can check what was really saved or
migrated.
"""
from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

FIXTURES = Path(__file__).parent / "fixtures"
PHASE = "source_document_type"

_PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Dell Technologies Developer</title>
<style>
  body { margin: 0; font-family: Arial, sans-serif; font-size: 13px; }
  header.app-header { height: 48px; background: #0672cb; color: #fff; display: flex; align-items: center; padding: 0 20px; }
  main { padding: 16px 24px; }
  .ribbon { display: flex; gap: 12px; align-items: center; margin: 10px 0; }
  .ribbon input { width: 420px; height: 30px; }
  [role=table] { display: table; width: 100%; border-collapse: collapse; }
  dds-table-head, dds-table-body, dds-table-body-row { display: contents; }
  .dds__tr { display: table-row; }
  .dds__td, .dds__th { display: table-cell; border-bottom: 1px solid #ddd; padding: 6px 8px; vertical-align: middle; }
  .dds__tr--expanded { display: table-row; background: #fafafa; }
  .dds__td--expandable-content { padding: 12px 16px; }
  .dds__badge { background: #0e7ac4; color: #fff; border-radius: 8px; padding: 1px 6px; margin-right: 3px; font-size: 11px; }
  [role=tablist] { display: flex; gap: 22px; border-bottom: 1px solid #ccc; margin: 10px 0; }
  [role=tab] { background: none; border: 0; padding: 6px 2px; cursor: pointer; }
  [role=tab][aria-selected=true] { border-bottom: 3px solid #0672cb; }
  [role=tab][disabled] { color: #aaa; cursor: default; }
  .bar { display: flex; align-items: center; gap: 12px; justify-content: space-between; margin: 8px 0; }
  .bar .ver { display: flex; align-items: center; gap: 8px; }
  app-tab-view-actionbar { position: relative; display: flex; gap: 6px; }
  .dds__action-menu { position: absolute; right: 0; top: 34px; background: #fff; border: 1px solid #999; display: flex; flex-direction: column; z-index: 20; min-width: 110px; }
  .dds__action-menu[hidden] { display: none; }
  .dds__action-menu button { text-align: left; background: #fff; border: 0; padding: 7px 10px; cursor: pointer; }
  fieldset { border: 1px solid #ccc; margin: 0 0 12px 0; padding: 8px 12px; }
  .row { display: flex; gap: 14px; flex-wrap: wrap; align-items: flex-start; margin: 6px 0; }
  .dds__form-group { display: flex; flex-direction: column; position: relative; }
  .dds__label { font-size: 11px; margin-bottom: 2px; }
  input[type=text], textarea { width: 240px; height: 30px; box-sizing: border-box; }
  textarea { height: 48px; }
  dds-dropdown { display: block; position: relative; width: 240px; }
  .dds__dropdown__popup { position: absolute; left: 0; right: 0; top: 100%; background: #fff; border: 1px solid #777; z-index: 3000; max-height: 260px; overflow: auto; }
  .dds__dropdown__popup--hidden { display: none; }
  .dds__dropdown__list { list-style: none; margin: 0; padding: 0; }
  .dds__dropdown__item-option { display: block; width: 100%; text-align: left; padding: 6px 10px; background: #fff; border: 0; cursor: pointer; }
  .dds__tag { display: inline-block; background: #e1e1e1; border-radius: 10px; padding: 1px 8px; margin: 2px; font-size: 11px; }
  .backdrop { position: fixed; inset: 0; background: rgba(40, 40, 40, .35); z-index: 1900; }
  .dds__drawer { position: fixed; top: 0; bottom: 0; right: 0; left: 260px; background: #fff; z-index: 2000; overflow: auto; padding: 16px 24px 0 24px; }
  .actions { position: sticky; bottom: 0; background: #f4f4f4; border-top: 1px solid #ccc; padding: 10px 16px; }
  .dds__modal { position: fixed; top: 30%; left: 35%; width: 380px; background: #fff; border: 1px solid #555; padding: 16px; z-index: 2600; }
  .toast { background: #dff0d8; padding: 8px; margin: 6px 0; }
  .toast--error { background: #f2dede; padding: 8px; margin: 6px 0; }
</style></head>
<body>
<header class="app-header">DELL Technologies Developer · Hybrid Integrations · SecureLink</header>
<main>
  <h1>Document Types</h1>
  <p>Document Type is a metadata structure template which allows for consistent properties and settings across various document type.</p>
  <div id="toast"></div>
  <dds-table-ribbon class="ribbon">
    <button type="button" class="dds__button dds__button--tertiary">Filter</button>
    <input type="text" id="tsearch" placeholder="Table search" aria-label="Table search">
    <button type="button" class="dds__button dds__button--tertiary">Manage Columns</button>
    <button type="button" class="dds__button dds__button--tertiary" id="add">+ Add</button>
  </dds-table-ribbon>
  <div role="table" class="dds__table dds__table--compact">
    <dds-table-head><div role="row" class="dds__tr">
      <div role="columnheader" class="dds__th"></div><div role="columnheader" class="dds__th">Document Type Name</div>
      <div role="columnheader" class="dds__th">Data Format Type</div><div role="columnheader" class="dds__th">Transaction Type</div>
      <div role="columnheader" class="dds__th">Latest Version</div><div role="columnheader" class="dds__th">Validation Type</div>
      <div role="columnheader" class="dds__th">Available Environments</div></div></dds-table-head>
    <dds-table-body role="rowgroup" id="rows"></dds-table-body>
  </div>
  <div class="pager">Items per page 10 · <span id="count"></span> <button type="button">Previous</button> <button type="button">Next</button></div>
</main>
<script>__KIT__</script>
<script>
const CFG = __CFG__;
const ENVS = ['DEV', 'TEST1', 'TEST2', 'PROD'];
const NEXT = { DEV: ['TEST1', 'TEST2'], TEST1: ['TEST2'], TEST2: ['PROD'], PROD: [] };
const esc = v => String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
let RECORDS = [];
let QUERY = '';
let seq = 0;

function toast(message, ok) {
  document.getElementById('toast').innerHTML = `<div class="${ok ? 'toast' : 'toast--error'}" role="${ok ? 'status' : 'alert'}">${esc(message)}</div>`;
}

async function load() {
  RECORDS = await (await fetch('/api/doctypes')).json();
  render();
}

function render() {
  const body = document.getElementById('rows');
  body.innerHTML = '';
  const list = RECORDS.filter(r => !QUERY || r.name.toLowerCase().includes(QUERY.toLowerCase()));
  list.slice(0, 10).forEach((r, i) => {
    const host = document.createElement('dds-table-body-row');
    const row = document.createElement('div');
    row.setAttribute('role', 'row'); row.className = 'dds__tr'; row.dataset.row = String(i);
    const envs = ENVS.filter(e => r.envs[e]);
    row.innerHTML = `<div role="cell" class="dds__td dds__td--expandable"><button type="button" aria-expanded="false" aria-label="Expand the row" class="dds__td--expandable__button"><span aria-hidden="true">&#8964;</span></button></div>
      <div role="cell" class="dds__td">${esc(r.name)}</div><div role="cell" class="dds__td">${esc(r.format)}</div>
      <div role="cell" class="dds__td">${esc(r.transactionType)}</div><div role="cell" class="dds__td">${esc(r.latest)}</div>
      <div role="cell" class="dds__td">${esc(r.validation)}</div>
      <div role="cell" class="dds__td">${envs.map(e => `<span class="dds__badge">${e}</span>`).join('')}</div>`;
    host.appendChild(row);
    const btn = row.querySelector('button');
    btn.addEventListener('click', () => {
      const open = btn.getAttribute('aria-expanded') === 'true';
      host.querySelectorAll('.dds__tr--expanded').forEach(x => x.remove());
      btn.setAttribute('aria-expanded', open ? 'false' : 'true');
      // Angular renders the expanded details a moment later.
      if (!open) setTimeout(() => host.appendChild(expanded(r, envs[0])), CFG.expandDelayMs);
    });
    body.appendChild(host);
  });
  document.getElementById('count').textContent = `1 - ${Math.min(10, list.length)} of ${list.length} items`;
}

function expanded(r, env, version) {
  const H = window.HIP;
  const versions = r.envs[env] || [];
  const ver = version || versions[versions.length - 1] || '';
  const menuId = `menu-${++seq}${Math.floor(Math.random() * 1000)}`;
  const wrap = document.createElement('div');
  wrap.setAttribute('role', 'row');
  wrap.className = 'dds__tr dds__tr--expandable dds__tr--expanded';
  wrap.innerHTML = `<div class="dds__td dds__td--expandable"></div><div class="dds__td dds__td--expandable-content"><div class="dds__row fullWidth">
    <div class="desc">Description: ${esc(r.description)}</div>
    <div role="tablist" aria-label="Environments">${ENVS.map(e => `<button type="button" role="tab" aria-selected="${e === env}"${r.envs[e] ? '' : ' disabled aria-disabled="true"'}>${e}</button>`).join('')}</div>
    <div class="bar"><div class="ver"><span class="ver-label">Version :</span><span class="ver-host"></span></div>
      <app-tab-view-actionbar>
        <button type="button" class="dds__button dds__button--secondary dds__button--sm act-edit"><span class="dds__icon dds__icon--pencil" aria-hidden="true"></span>Edit</button>
        <button type="button" class="dds__button dds__button--secondary dds__button--sm act-clone"><span class="dds__icon dds__icon--copy" aria-hidden="true"></span>Clone</button>
        <button type="button" class="dds__button dds__button--secondary dds__button--sm act-migrate" aria-expanded="false" aria-controls="${menuId}"><span class="dds__icon dds__icon--arrow-right" aria-hidden="true"></span>Migrate</button>
        <div class="dds__action-menu" id="${menuId}" role="menu" hidden>${(NEXT[env] || []).map(t => `<button type="button" role="menuitem" class="dds__action-menu__option">${t}</button>`).join('')}</div>
      </app-tab-view-actionbar></div>
    <app-create-doctype class="view"><fieldset><legend>Document Type Details</legend><div class="row view-fields"></div></fieldset></app-create-doctype>
  </div></div>`;
  const verHost = wrap.querySelector('.ver-host');
  verHost.appendChild(H.dropdown({ label: 'Version', options: versions, value: ver, showLabel: false,
    onChange: (v) => setTimeout(() => wrap.replaceWith(expanded(r, env, v)), 120) }));
  const d = (r.details[env] || {})[ver] || r.base;
  const view = wrap.querySelector('.view-fields');
  [['Name', r.name], ['Transaction Type', d.transactionType], ['Version', ver], ['Data Format Type', r.format]].forEach(([label, value]) => {
    view.appendChild(H.text({ label, name: 'view_' + label.replace(/\W+/g, ''), value, disabled: true }));
  });
  view.appendChild(H.text({ label: 'Description', name: 'view_description', value: d.description, disabled: true, textarea: true }));
  wrap.querySelectorAll('[role=tab]').forEach(tab => tab.addEventListener('click', () => {
    if (tab.disabled) return;
    setTimeout(() => wrap.replaceWith(expanded(r, tab.textContent.trim())), 150);
  }));
  wrap.querySelector('.act-edit').onclick = () => openDrawer('edit', r, env, ver);
  wrap.querySelector('.act-clone').onclick = () => openDrawer('clone', r, env, ver);
  const mig = wrap.querySelector('.act-migrate');
  const menu = wrap.querySelector('[role=menu]');
  mig.onclick = () => { menu.hidden = !menu.hidden; mig.setAttribute('aria-expanded', String(!menu.hidden)); };
  menu.querySelectorAll('[role=menuitem]').forEach(item => item.addEventListener('click', () => {
    menu.hidden = true; mig.setAttribute('aria-expanded', 'false');
    const to = item.textContent.trim();
    if (CFG.confirmMigrate) confirmMigrate(r, env, ver, to); else migrate(r, env, ver, to);
  }));
  return wrap;
}

function confirmMigrate(r, from, version, to) {
  const m = document.createElement('div');
  m.className = 'dds__modal'; m.setAttribute('role', 'dialog'); m.setAttribute('aria-label', 'Migrate Document Type');
  m.innerHTML = `<h3>Migrate Document Type</h3><p>Migrate ${esc(r.name)} version ${esc(version)} from ${from} to ${to}?</p>
    <button type="button" class="c-cancel">Cancel</button> <button type="button" class="c-ok">Migrate</button>`;
  m.querySelector('.c-cancel').onclick = () => m.remove();
  m.querySelector('.c-ok').onclick = () => { m.remove(); migrate(r, from, version, to); };
  document.body.appendChild(m);
}

async function migrate(r, from, version, to) {
  const res = await fetch('/api/doctypes/migrate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: r.name, from, to, version }) });
  const out = await res.json();
  toast(out.message, res.ok);
  if (res.ok) load();
}

function openDrawer(mode, r, env, ver) {
  const H = window.HIP;
  const d = (r.details[env] || {})[ver] || r.base;
  const title = mode === 'edit' ? 'Edit Document Type' : 'Clone Document Type';
  const backdrop = document.createElement('div'); backdrop.className = 'backdrop';
  const drawer = document.createElement('app-generic-drawer');
  drawer.innerHTML = `<div class="dds__drawer dds__drawer--open" role="dialog" aria-label="${title}"><a href="#" class="back">&#8249; Back</a>
    <h2 class="dds__drawer__title">${title}</h2><p>${mode === 'edit' ? 'Edit' : 'Clone'} template for any data format. It is a metadata structure template which allows for configuring consistent properties and settings across various document types.</p></div>`;
  const panel = drawer.firstElementChild;
  const attrs = H.fieldset('Attributes to Configure', ...(d.attributes || []).map(a => H.row(
    H.text({ label: 'Attribute Name', name: 'attributeName', value: a.name }),
    H.dropdown({ label: 'Derived From', name: 'attributeDerivedFrom', options: ['ELEMENT_IN_PAYLOAD', 'TRANSACTION_ROOT_ELEMENT'], value: a.derivedFrom }),
    H.text({ label: 'Expression/Value', name: 'expression', value: a.expression }))));
  const form = H.form(
    H.fieldset('Document Type Details',
      H.row(
        H.text({ label: 'Name', name: 'name', value: r.name, disabled: mode === 'edit' }),
        H.text({ label: 'Transaction Type', name: 'transactionType', value: d.transactionType }),
        H.text({ label: 'Version', name: 'version', value: ver, disabled: true }),
        H.dropdown({ label: 'Data Format Type', name: 'dataFormatType', options: ['XML', 'EDIX12', 'JSON'], value: r.format, disabled: mode === 'edit' })),
      H.row(H.switchControl({ label: 'Status', name: 'status', checked: d.enabled }),
        H.text({ label: 'Description', name: 'description', value: d.description, textarea: true }))),
    H.fieldset('Document Identifier',
      H.row(H.dropdown({ label: 'Operation', name: 'operation', options: ['All conditions are satisfied', 'One or more conditions are satisfied'], value: d.operation })),
      H.row(H.dropdown({ label: 'Derived From', name: 'identifierDerivedFrom', options: ['ELEMENT_IN_PAYLOAD', 'TRANSACTION_ROOT_ELEMENT'], value: d.identifierDerivedFrom }),
        H.text({ label: 'Value', name: 'value', value: d.identifierValue }))),
    attrs,
    H.fieldset('Validation', H.row(H.dropdown({ label: 'Validation Type', name: 'validationType', options: ['Structure', 'None'], value: r.validation }))),
  );
  panel.appendChild(form);
  if (CFG.clearDescriptionOnTransactionType) {
    // A portal reaction: a new Transaction Type resets the Description.
    const tx = form.querySelector('[name=transactionType]');
    tx.addEventListener('change', () => { form.querySelector('[name=description]').value = ''; });
  }
  document.body.appendChild(backdrop);
  document.body.appendChild(drawer);
  const close = () => { drawer.remove(); backdrop.remove(); };
  panel.querySelector('.back').onclick = (e) => { e.preventDefault(); close(); };
  form.querySelector('#cancel').onclick = close;
  form.querySelector('#submit').onclick = async () => {
    const values = {};
    panel.querySelectorAll('dds-dropdown[formcontrolname]').forEach(dd => { if (!dd.closest('.row').querySelector('[name=attributeName]')) values[dd.getAttribute('formcontrolname')] = dd.querySelector('input').value; });
    panel.querySelectorAll('input[formcontrolname],textarea[formcontrolname]').forEach(i => {
      if (i.closest('dds-dropdown') || i.getAttribute('name') === 'attributeName' || i.getAttribute('name') === 'expression') return;
      values[i.getAttribute('formcontrolname')] = i.type === 'checkbox' ? i.checked : i.value;
    });
    values.attributes = Array.from(panel.querySelectorAll('input[name=attributeName]')).map(i => {
      const rowEl = i.closest('.row');
      return { name: i.value, derivedFrom: rowEl.querySelector('dds-dropdown input').value, expression: rowEl.querySelector('input[name=expression]').value };
    });
    const res = await fetch('/api/doctypes/save', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, id: r.name, env, version: ver, values }) });
    const out = await res.json();
    const note = document.createElement('div');
    note.setAttribute('role', res.ok ? 'status' : 'alert'); note.className = res.ok ? 'toast' : 'toast--error';
    note.textContent = out.message;
    panel.prepend(note);
    if (res.ok) setTimeout(() => { close(); toast(out.message, true); load(); }, 400);
  };
}

const search = document.getElementById('tsearch');
let pending = null;
search.addEventListener('input', () => { clearTimeout(pending); pending = setTimeout(() => { QUERY = search.value.trim(); render(); }, 250); });
search.addEventListener('keydown', e => { if (e.key === 'Enter') { QUERY = search.value.trim(); render(); } });
load();
</script></body></html>"""


def _details(transaction: str, description: str, *, operation: str = "All conditions are satisfied", derived: str = "TRANSACTION_ROOT_ELEMENT",
             value: str = "PurchaseOrder") -> Dict[str, Any]:
    return {
        "transactionType": transaction, "description": description, "enabled": True, "operation": operation,
        "identifierDerivedFrom": derived, "identifierValue": value,
        "attributes": [
            {"name": "Receiver", "derivedFrom": "ELEMENT_IN_PAYLOAD", "expression": "/PurchaseOrder/OrderHeader/OrderParty/BuyerParty/Party/ListOfIdentifier"},
            {"name": "Sender", "derivedFrom": "ELEMENT_IN_PAYLOAD", "expression": "/PurchaseOrder/OrderHeader/OrderParty/BillToParty/Party/NameAddress"},
        ],
    }


def seed_records() -> List[Dict[str, Any]]:
    def rec(name: str, fmt: str, tx: str, envs: Dict[str, List[str]], description: str) -> Dict[str, Any]:
        base = _details(tx, description)
        details = {env: {v: copy.deepcopy(base) for v in versions} for env, versions in envs.items()}
        latest = max((v for vs in envs.values() for v in vs), key=float)
        return {"name": name, "format": fmt, "transactionType": tx, "latest": latest, "validation": "Structure",
                "description": description, "envs": envs, "details": details, "base": base}

    return [
        rec("a-doc-type-test", "XML", "payment", {"DEV": ["1.0", "2.0", "3.0"], "TEST1": ["3.0"], "TEST2": ["3.0"], "PROD": ["3.0"]}, "a-doc-type-test"),
        rec("Abbvie_997_DocType", "EDIX12", "997", {"DEV": ["1.0"]}, "Abbvie_997_DocType"),
        rec("Abbvie_SRC_DocType", "EDIX12", "850", {"DEV": ["1.0", "2.0", "3.0"]}, "Abbvie_SRC_DocType"),
        rec("Abbvie_SRC_DocType_IN", "EDIX12", "850", {"DEV": ["1.0"], "TEST1": ["1.0"]}, "Abbvie_SRC_DocType(internal)"),
        rec("Abbvie_TRGT_Doctype", "XML", "XML", {"DEV": ["1.0"]}, "Abbvie_TRGT_Doctype"),
        rec("Abbvie_TRGT_Doctype_IN", "XML", "XML", {"DEV": ["1.0"]}, "Abbvie_TRGT_Doctype_IN"),
    ]


class DocTypesPortal:
    """HTTP server for the Document Types listing replica."""

    def __init__(self, *, confirm_migrate: bool = True, expand_delay_ms: int = 300, clear_description_on_transaction_type: bool = False) -> None:
        self.records: List[Dict[str, Any]] = seed_records()
        self.posts: List[Dict[str, Any]] = []
        self.cfg = {"confirmMigrate": bool(confirm_migrate), "expandDelayMs": int(expand_delay_ms),
                    "clearDescriptionOnTransactionType": bool(clear_description_on_transaction_type)}
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
                path = urlparse(self.path).path
                if path == "/securelink/doctypes":
                    html = _PAGE.replace("__KIT__", portal.kit).replace("__CFG__", json.dumps(portal.cfg))
                    self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/api/doctypes":
                    self._send(200, json.dumps(portal.records).encode("utf-8"), "application/json")
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                portal.posts.append({"path": self.path, "body": body})
                code, message = portal.apply(self.path, body)
                self._send(code, json.dumps({"message": message}).encode("utf-8"), "application/json")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def find(self, name: str) -> Dict[str, Any]:
        return next((r for r in self.records if r["name"] == name), {})

    def apply(self, path: str, body: Dict[str, Any]) -> tuple:
        if path == "/api/doctypes/migrate":
            record = self.find(body.get("name", ""))
            source, target, version = body.get("from"), body.get("to"), body.get("version")
            if not record or version not in (record["envs"].get(source) or []):
                return 400, "Source version not found"
            if version in (record["envs"].get(target) or []):
                return 409, f"Version {version} already exists in {target}"
            record["envs"].setdefault(target, []).append(version)
            record["details"].setdefault(target, {})[version] = copy.deepcopy(record["details"][source][version])
            return 200, f"Document Type {record['name']} migrated to {target} successfully"
        if path == "/api/doctypes/save":
            values = body.get("values") or {}
            mode, env, version = body.get("mode"), body.get("env"), body.get("version")
            source = self.find(body.get("id", ""))
            if not source:
                return 404, "Document Type not found"
            detail = {
                "transactionType": values.get("transactionType", ""), "description": values.get("description", ""),
                "enabled": bool(values.get("status")), "operation": values.get("operation", ""),
                "identifierDerivedFrom": values.get("identifierDerivedFrom", ""), "identifierValue": values.get("value", ""),
                "attributes": values.get("attributes") or [],
            }
            if mode == "clone":
                name = str(values.get("name") or "")
                if self.find(name):
                    return 409, "Document Type name already exists"
                self.records.append({"name": name, "format": values.get("dataFormatType") or source["format"], "transactionType": detail["transactionType"],
                                     "latest": "1.0", "validation": values.get("validationType") or "Structure", "description": detail["description"],
                                     "envs": {"DEV": ["1.0"]}, "details": {"DEV": {"1.0": detail}}, "base": detail})
                return 200, f"Document Type {name} created successfully"
            source["details"].setdefault(env, {})[version] = detail
            source["transactionType"] = detail["transactionType"]
            source["description"] = detail["description"]
            return 200, f"Document Type {source['name']} updated successfully"
        return 404, "unknown"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/securelink/doctypes"

    def __enter__(self) -> "DocTypesPortal":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
