"""Listings of every HIP object with the row expander and Edit (V243R30).

``DocTypesPortal`` (R24) mirrors the recorded Document Types listing.  The
other objects' listings follow the same DDS table: "Table search", rows whose
first cell is the expander ``button.dds__td--expandable__button`` ("Expand the
row"), an expanded details row with the environment tabs, a few read-only
details and Edit / Clone / Deploy.  Edit opens the object's form -- the full
create-form replica of that phase (``tests/fixtures/*_dds.html``), filled by
the "portal" from the stored record, with the fields the portal keeps
read-only in Edit disabled:

* Transport Profile and Data Map: an in-page drawer over the listing (the URL
  stays), as the Document Type Edit;
* Business Flow and Rule: their own Edit page (the URL changes), with the
  BizFlow wizard tabs, collapsed process steps and unlabelled rows.

Save posts the form's values; the server keeps them, so a test can reopen the
Edit form, or read the record, to see what was really saved.

V243R31: Clone opens the same form titled "Clone ...", filled from the record,
with the name editable (the server rejects a name that exists).  Migrate opens a
menu of target environments (from DEV: TEST1, TEST2; from TEST1: TEST2; from
TEST2: PROD) and a confirmation.  Deploy has the three shapes seen on portals:
a dialog with a Target Environment field (Transport Profile), a menu of target
environments plus a confirmation (Business Flow), and a plain button whose
confirmation names the next environment (Data Map); a Rule has no Deploy.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from phase_replica_support import FIXTURES, replica_html

TP_SRC = "SFTP_U-HAUL_ASN_PC_SRC_IB"
TP_TGT = "SFTP_U-HAUL_ASN_PC_TGT_OB"
BIZFLOW = "U-HAUL_PC_856_ANS_MAPPING_OB"
DATAMAP = "DELLCoXMLASNXX08C_U-HAUL"
RULE = "DELLCoXMLASNXX08C_U-HAUL_RULE"
SRC_DOC = "XML_DellAutoASN_10_U-HAUL_ANS_IB (1.0)"
TGT_DOC = "XML_SHIPMENT_NOTICE_10_U-HAUL_ANS_OB (1.0)"


def _s(label: str, value: Any, kind: str = "text", index: int = 0) -> Dict[str, Any]:
    return {"label": label, "value": value, "kind": kind, "index": index}


def _tp_steps(*, system_type: str, system: str, name: str, usage: str, group: str, account: str, folder: str, doc: str) -> List[Dict[str, Any]]:
    return [
        _s("System Type *", system_type, "dropdown"), _s("System Name *", system, "dropdown"),
        _s("Profile Name *", name), _s("Profile Usage *", usage, "dropdown"), _s("Deployment Group *", group, "dropdown"),
        _s("Interface Type *", "SFTP HAFT", "dropdown"), _s("Interface Environment *", "UAT", "dropdown"),
        _s("Existing Account *", "Yes", "radio"), _s("Existing Account Name *", account, "dropdown"),
        _s("Use Existing Folder *", "No", "radio"), _s("Subscription Folder *", folder),
        _s("File Filtering Pattern *", ".*\\*."), _s("Post Transfer Action *", "Move To Archive", "dropdown"),
        _s("Is Compression Required", "FALSE", "dropdown"), _s("Key", "BU"), _s("Value", "SCM"),
        _s("Document Type", doc, "dropdown"),
    ]


FAMILIES: Dict[str, Dict[str, Any]] = {
    "transport_profiles": {
        "phases": ("source_transport_profile", "target_transport_profile"),
        "path": "/hybrid-integrations/securelink/transport-profiles", "title": "Transport Profiles", "object": "Transport Profile",
        "fixture": "transport_profile_full_dds.html", "surface": "drawer", "commit_label": "Update",
        # V243R31: Deploy opens a dialog with a Target Environment field.
        "deploy": "dialog", "clone_label": "Create",
        "read_only": ["Profile Name *"], "columns": ["Profile Name", "Profile Usage", "Interface Type", "Available Environments"],
        "details": ["Profile Usage *", "Interface Type *"],
        # Shown in Edit only: the portal's audit fields and the account password.
        "edit_extras": True,
    },
    "bizflows": {
        "phases": ("biz_flow",), "path": "/hybrid-integrations/securelink/bizflows", "title": "Business Flows", "object": "Biz Flow",
        "fixture": "bizflow_wizard_dds.html", "surface": "page", "commit_label": "Save",
        # Deploy opens a menu of target environments, then a confirmation.
        "deploy": "menu", "clone_label": "Save",
        "read_only": ["Business Flow Name *"], "columns": ["Business Flow Name", "Source", "Target", "Available Environments"],
        "details": ["Source Type *", "Target Type *"],
    },
    "datamaps": {
        "phases": ("data_map",), "path": "/hybrid-integrations/securelink/datamaps", "title": "Data Maps", "object": "Map",
        "fixture": "data_map_full_dds.html", "surface": "drawer", "commit_label": "Submit",
        # Deploy is a plain button: the confirmation names the next environment.
        "deploy": "confirm", "clone_label": "Submit",
        "read_only": ["Map Identifier *"], "columns": ["Map Identifier", "Map Name", "Contivo version", "Available Environments"],
        "details": ["Map Name *", "Map Class *"],
    },
    "rules": {
        "phases": ("rule",), "path": "/hybrid-integrations/securelink/rules", "title": "Rules", "object": "Rule",
        "fixture": "rule_full_dds.html", "surface": "page", "commit_label": "Save",
        # No Deploy: an environment is reached with Migrate (as Document Types).
        "deploy": None, "clone_label": "Create",
        "read_only": ["Name *"], "columns": ["Name", "Rule Type", "Document Type", "Available Environments"],
        "details": ["Document Type Name (Version) *", "Description"],
    },
}


def seed_records(family: str) -> List[Dict[str, Any]]:
    def rec(name: str, columns: List[str], steps: List[Dict[str, Any]], envs: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        return {"name": name, "columns": columns, "steps": steps, "envs": envs or {"DEV": ["1.0"]}}

    if family == "transport_profiles":
        src = _tp_steps(system_type="Dell Application", system="AIC - DCE", name=TP_SRC, usage="Sender",
                        group="da-sender-sftphaft-dce-shared", account="haftatap10251108", folder=f"/{TP_SRC}", doc=SRC_DOC)
        tgt = _tp_steps(system_type="Partner", system="dce-test-partner", name=TP_TGT, usage="Receiver",
                        group="pt-receiver-sftphaft-dce-shared", account="haftattp10251114", folder="/Inbound/ASN", doc=TGT_DOC)
        old = copy.deepcopy(src)
        old[2]["value"] = f"{TP_SRC}_OLD"
        return [
            rec(TP_SRC, [TP_SRC, "Sender", "SFTP HAFT"], src, {"DEV": ["1.0"], "TEST1": ["1.0"]}),
            rec(f"{TP_SRC}_OLD", [f"{TP_SRC}_OLD", "Sender", "SFTP HAFT"], old),
            rec(TP_TGT, [TP_TGT, "Receiver", "SFTP HAFT"], tgt),
        ]
    if family == "bizflows":
        steps = [
            _s("Business Flow Name *", BIZFLOW), _s("Flow Description *", "Outbound 856 Ship Notice - DELL to U-HAUL"),
            _s("Source Type *", "Dell Application", "dropdown"), _s("Source Application *", "AIC - DCE", "dropdown"),
            _s("Source Transport Profile *", [TP_SRC], "multi"), _s("Document Type Name *", [SRC_DOC], "multi"),
            _s("Flow Identifier Operator *", "one or more conditions are satisfied", "dropdown"),
            _s("Document Type Name (Version) *", SRC_DOC, "dropdown", 0), _s("Attribute Name *", "Receiver", "dropdown", 0),
            _s("Operator *", "Equals", "dropdown", 0), _s("Value *", "uhaul", "text", 0),
            _s("Document Type Name (Version) *", SRC_DOC, "dropdown", 1), _s("Attribute Name *", "Sender", "dropdown", 1),
            _s("Operator *", "Contains", "dropdown", 1), _s("Value *", "DELL", "text", 1),
            _s("Target Type *", "Partner", "dropdown"), _s("Target Application *", "dce-test-partner", "dropdown"),
            _s("Target Transport Profile *", TP_TGT, "dropdown"),
            # The target tab's own Document Type Name (Version) follows the two identifier rows.
            _s("Document Type Name (Version) *", [TGT_DOC], "multi", 2),
            _s("Step Type", "Mapping Transformer", "dropdown", 0), _s("Step Name", "Mapping-1", "text", 0),
            _s("Action *", "Mapping", "dropdown", 0), _s("Target Document Type (Version) *", [TGT_DOC], "multi"),
            _s("Rule (Version) *", "DELLCoXMLASNXX08C_U-HAUL_RULE (1.0)", "dropdown"),
            _s("Step Type", "Enricher", "dropdown", 1), _s("Step Name", "Enricher-1", "text", 1), _s("Action *", "Enrich", "dropdown", 1),
        ]
        return [rec(BIZFLOW, [BIZFLOW, "AIC - DCE", "dce-test-partner"], steps, {"DEV": ["1.0"], "TEST1": ["1.0"]}),
                rec("U-HAUL_PC_810_INV_OB", ["U-HAUL_PC_810_INV_OB", "AIC - DCE", "dce-test-partner"],
                    [dict(x, value="U-HAUL_PC_810_INV_OB") if x["label"] == "Business Flow Name *" else dict(x) for x in steps])]
    if family == "datamaps":
        steps = [_s("Map Identifier *", DATAMAP), _s("Status", True, "switch"), _s("Map Name *", "DELLCoXMLASNXX08C"),
                 _s("Map Class *", "Transform_DELLCoXMLASNXX08C"), _s("Contivo version *", "6.7", "dropdown"),
                 _s("Map Data *", "Transform_DELLCoXMLASNXX08C.jar", "file_name")]
        return [rec(DATAMAP, [DATAMAP, "DELLCoXMLASNXX08C", "6.7"], steps)]
    if family == "rules":
        steps = [
            _s("Name *", RULE), _s("Document Type Name (Version) *", "XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)", "dropdown"),
            _s("Status", True, "switch"), _s("Execute Always", False, "switch"), _s("Description", RULE),
            _s("Execute Action(s) When *", "one or more conditions are satisfied", "dropdown"),
            _s("Condition Type", "Attributes", "dropdown", 0), _s("Operator", "Equals", "dropdown", 0), _s("Value", "uhaul", "text", 0),
            _s("Attribute Name/Unit", "Receiver", "dropdown", 0),
            _s("Condition Type", "Attributes", "dropdown", 1), _s("Operator", "Contains", "dropdown", 1), _s("Value", "DELL", "text", 1),
            _s("Attribute Name/Unit", "Sender", "dropdown", 1),
            _s("Name *", "DELLCoXMLASNXX08C_U-HAUL_ROUTE", "text", 1), _s("Type *", "Route Document", "dropdown"),
            _s("Mapping Identifier Name (Version) *", "DELLCoXMLASNXX08C_U-HAUL(1.0)", "dropdown"),
        ]
        return [rec(RULE, [RULE, "Mapping", "XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)"], steps)]
    raise KeyError(family)


# In-page "portal": fill a rendered form from a record, read it back for Save.
_PREFILL_JS = r"""
(function () {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase().replace(/\s*[*:]+\s*$/, '');
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const byId = (scope, id) => id && (scope.querySelector('#' + CSS.escape(id)) || document.getElementById(id));
  const ownLabel = (scope, el) => { const l = el.id && scope.querySelector(`label[for="${CSS.escape(el.id)}"]`); return l ? l.textContent : ''; };
  function hits(scope, step) {
    const want = norm(step.label);
    if (step.kind === 'radio') return Array.from(scope.querySelectorAll('.dds__form-group')).filter(g => g.querySelector('[role=radiogroup]') && norm((g.querySelector(':scope > .dds__label') || {}).textContent) === want);
    if (step.kind === 'checkbox') return Array.from(scope.querySelectorAll('.dds__checkbox__label')).filter(l => norm(l.textContent) === want).map(l => byId(scope, l.getAttribute('for')));
    if (step.kind === 'switch') return Array.from(scope.querySelectorAll('.dds__switch')).filter(g => norm((g.querySelector('label') || {}).textContent) === want).map(g => g.querySelector('input'));
    if (step.kind === 'file_name') return Array.from(scope.querySelectorAll('.dds__file-input')).filter(g => norm((g.querySelector('label') || {}).textContent) === want).map(g => g.querySelector('.dds__file-input__name'));
    return Array.from(scope.querySelectorAll('input,textarea')).filter(el => !['radio', 'checkbox', 'file', 'hidden'].includes(el.type))
      .filter(el => { const own = ownLabel(scope, el); return own ? norm(own) === want : norm(el.getAttribute('placeholder')) === want; });
  }
  async function find(scope, step, wait = true) {
    for (let t = 0; t < (wait ? 60 : 1); t++) {
      const found = hits(scope, step)[step.index || 0];
      if (found) return found;
      await sleep(50);
    }
    return null;
  }
  async function choose(dd, input, value) {
    input.click();
    await sleep(20);
    const opt = Array.from(dd.querySelectorAll('[role=option]')).find(o => o.textContent.trim() === String(value));
    if (!opt) { console.warn('replica prefill: no option', value); return false; }
    opt.click();
    return true;
  }
  window.hipPrefill = async function (scope, steps, readOnly) {
    for (const step of steps) {
      const el = await find(scope, step);
      if (!el) { console.warn('replica prefill: no control', step.label, step.index); continue; }
      const v = step.value;
      if (step.kind === 'dropdown') await choose(el.closest('dds-dropdown'), el, v);
      else if (step.kind === 'multi') { const dd = el.closest('dds-dropdown'); for (const x of v) await choose(dd, el, x); dd.__close(); }
      else if (step.kind === 'radio') { const r = Array.from(el.querySelectorAll('input[type=radio]')).find(i => byId(scope, i.id) && (scope.querySelector(`label[for="${CSS.escape(i.id)}"]`) || {}).textContent === v); if (r) r.click(); }
      else if (step.kind === 'checkbox' || step.kind === 'switch') { if (!!el.checked !== !!v) el.click(); }
      else if (step.kind === 'file_name') el.textContent = String(v);
      else { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }
      await sleep(40);
    }
    for (const label of readOnly || []) {
      const el = await find(scope, { label, kind: 'text', index: 0 }, false);
      if (el) el.disabled = true;
    }
  };
  window.hipReadBack = async function (scope, steps) {
    const out = [];
    for (const step of steps) {
      const el = await find(scope, step, false);
      if (!el) continue;
      let value;
      if (step.kind === 'multi') value = Array.from(el.closest('dds-dropdown').querySelectorAll('.dds__tag')).map(t => t.textContent.trim());
      else if (step.kind === 'radio') { const r = el.querySelector('input[type=radio]:checked'); value = r ? (scope.querySelector(`label[for="${CSS.escape(r.id)}"]`) || {}).textContent : ''; }
      else if (step.kind === 'checkbox' || step.kind === 'switch') value = !!el.checked;
      else if (step.kind === 'file_name') value = el.textContent;
      else value = el.value;
      out.push({ label: step.label, index: step.index || 0, value });
    }
    return out;
  };
})();
"""

_EXTRAS_JS = r"""
window.hipEditExtras = function (form) {
  const H = window.HIP;
  const fs = H.fieldset('Audit Details :', H.row(
    H.text({ label: 'Last Modified By', name: 'lastModifiedBy', value: 'svc_hip_portal', disabled: true }),
    H.text({ label: 'Last Modified On', name: 'lastModifiedOn', value: '2026-09-20 10:42', disabled: true })));
  const pw = H.el('<div class="dds__form-group"><label class="dds__label" for="acct-pw">Account Password</label><input type="password" id="acct-pw" name="accountPassword" value="S3cr3t!pass"></div>');
  fs.appendChild(pw);
  form.insertBefore(fs, form.querySelector('.actions'));
};
"""

_LISTING = r"""<!doctype html><html><head><meta charset="utf-8"><title>Dell Technologies Developer</title>
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
  fieldset { border: 1px solid #ccc; margin: 0 0 12px 0; padding: 8px 12px; }
  .row { display: flex; gap: 14px; flex-wrap: wrap; align-items: flex-start; margin: 6px 0; }
  .dds__form-group { display: flex; flex-direction: column; position: relative; }
  .dds__label { font-size: 11px; margin-bottom: 2px; }
  input[type=text], input[type=password], textarea { width: 240px; height: 30px; box-sizing: border-box; }
  textarea { height: 48px; }
  dds-dropdown { display: block; position: relative; width: 240px; }
  .dds__dropdown__popup { position: absolute; left: 0; right: 0; top: 100%; background: #fff; border: 1px solid #777; z-index: 3000; max-height: 260px; overflow: auto; }
  .dds__dropdown__popup--hidden { display: none; }
  .dds__dropdown__list { list-style: none; margin: 0; padding: 0; }
  .dds__dropdown__item-option { display: block; width: 100%; text-align: left; padding: 6px 10px; background: #fff; border: 0; cursor: pointer; }
  .dds__tag { display: inline-block; background: #e1e1e1; border-radius: 10px; padding: 1px 8px; margin: 2px; font-size: 11px; }
  .dds__radio-button-group { display: flex; gap: 12px; }
  .backdrop { position: fixed; inset: 0; background: rgba(40, 40, 40, .35); z-index: 1900; }
  .dds__drawer { position: fixed; top: 0; bottom: 0; right: 0; left: 260px; background: #fff; z-index: 2000; overflow: auto; padding: 16px 24px 0 24px; }
  .actions { position: sticky; bottom: 0; background: #f4f4f4; border-top: 1px solid #ccc; padding: 10px 16px; }
  .toast { background: #dff0d8; padding: 8px; margin: 6px 0; }
  .toast--error { background: #f2dede; padding: 8px; margin: 6px 0; }
</style></head>
<body>
<header class="app-header">DELL Technologies Developer · Hybrid Integrations · SecureLink</header>
<main>
  <h1>__TITLE__</h1>
  <div id="toast"></div>
  <dds-table-ribbon class="ribbon">
    <button type="button" class="dds__button dds__button--tertiary">Filter</button>
    <input type="text" id="tsearch" placeholder="Table search" aria-label="Table search">
    <button type="button" class="dds__button dds__button--tertiary">Manage Columns</button>
    <button type="button" class="dds__button dds__button--tertiary" id="add">+ Add</button>
  </dds-table-ribbon>
  <div role="table" class="dds__table dds__table--compact">
    <dds-table-head><div role="row" class="dds__tr"><div role="columnheader" class="dds__th"></div>__HEADERS__</div></dds-table-head>
    <dds-table-body role="rowgroup" id="rows"></dds-table-body>
  </div>
</main>
<script>__KIT__</script>
<script>__PREFILL__</script>
<script>__EXTRAS__</script>
<script>
const CFG = __CFG__;
window.__buildEditForm = function () {
  const H = window.HIP;
  const page = H.page;
  let built = null;
  H.page = (title, form) => { built = form; return form; };
  try { (function () { __FIXTURE__ })(); } finally { H.page = page; }
  return built;
};
const ENVS = ['DEV', 'TEST1', 'TEST2', 'PROD'];
const esc = v => String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
let RECORDS = [];
let QUERY = '';
function toast(message, ok) {
  document.getElementById('toast').innerHTML = `<div class="${ok ? 'toast' : 'toast--error'}" role="${ok ? 'status' : 'alert'}">${esc(message)}</div>`;
}
async function load() { RECORDS = await (await fetch(CFG.api)).json(); render(); }
function detail(r, label) { const s = r.steps.find(x => x.label === label && !(x.index || 0)); return s ? (Array.isArray(s.value) ? s.value.join(', ') : s.value) : ''; }
function render() {
  const body = document.getElementById('rows');
  body.innerHTML = '';
  RECORDS.filter(r => !QUERY || r.name.toLowerCase().includes(QUERY.toLowerCase())).slice(0, 10).forEach(r => {
    const host = document.createElement('dds-table-body-row');
    const row = document.createElement('div');
    row.setAttribute('role', 'row'); row.className = 'dds__tr';
    const envs = ENVS.filter(e => r.envs[e]);
    row.innerHTML = `<div role="cell" class="dds__td dds__td--expandable"><button type="button" aria-expanded="false" aria-label="Expand the row" class="dds__td--expandable__button"><span aria-hidden="true">&#8964;</span></button></div>`
      + r.columns.map(c => `<div role="cell" class="dds__td">${esc(c)}</div>`).join('')
      + `<div role="cell" class="dds__td">${envs.map(e => `<span class="dds__badge">${e}</span>`).join('')}</div>`;
    host.appendChild(row);
    const btn = row.querySelector('button');
    btn.addEventListener('click', () => {
      const open = btn.getAttribute('aria-expanded') === 'true';
      host.querySelectorAll('.dds__tr--expanded').forEach(x => x.remove());
      btn.setAttribute('aria-expanded', open ? 'false' : 'true');
      if (!open) setTimeout(() => host.appendChild(expanded(r, envs[0])), 250);
    });
    body.appendChild(host);
  });
}
const NEXT = { DEV: ['TEST1', 'TEST2'], TEST1: ['TEST2'], TEST2: ['PROD'], PROD: [] };
let menuSeq = 0;
function expanded(r, env) {
  const H = window.HIP;
  const version = (r.envs[env] || ['1.0']).slice(-1)[0];
  const targets = NEXT[env] || [];
  const deployMenu = `deploy-menu-${++menuSeq}`;
  const migrateMenu = `migrate-menu-${++menuSeq}`;
  const wrap = document.createElement('div');
  wrap.setAttribute('role', 'row');
  wrap.className = 'dds__tr dds__tr--expandable dds__tr--expanded';
  wrap.innerHTML = `<div class="dds__td dds__td--expandable"></div><div class="dds__td dds__td--expandable-content">
    <div role="tablist" aria-label="Environments">${ENVS.map(e => `<button type="button" role="tab" aria-selected="${e === env}"${r.envs[e] ? '' : ' disabled aria-disabled="true"'}>${e}</button>`).join('')}</div>
    <div class="bar"><span>Version : ${esc(version)}</span>
      <app-tab-view-actionbar style="position:relative;display:inline-flex;gap:6px">
      <button type="button" class="dds__button dds__button--secondary dds__button--sm act-edit"><span class="dds__icon dds__icon--pencil" aria-hidden="true"></span>Edit</button>
      <button type="button" class="dds__button dds__button--secondary dds__button--sm act-clone">Clone</button>
      ${CFG.deploy === 'dialog' ? '<button type="button" class="dds__button dds__button--secondary dds__button--sm act-deploy" aria-haspopup="dialog">Deploy</button>' : ''}
      ${CFG.deploy === 'menu' ? `<button type="button" class="dds__button dds__button--secondary dds__button--sm act-deploy" aria-expanded="false" aria-controls="${deployMenu}">Deploy</button><div class="dds__action-menu" id="${deployMenu}" role="menu" hidden>${targets.map(t => `<button type="button" role="menuitem" class="dds__action-menu__option">${t}</button>`).join('')}</div>` : ''}
      ${CFG.deploy === 'confirm' ? '<button type="button" class="dds__button dds__button--secondary dds__button--sm act-deploy">Deploy</button>' : ''}
      <button type="button" class="dds__button dds__button--secondary dds__button--sm act-migrate" aria-expanded="false" aria-controls="${migrateMenu}">Migrate</button>
      <div class="dds__action-menu" id="${migrateMenu}" role="menu" hidden>${targets.map(t => `<button type="button" role="menuitem" class="dds__action-menu__option">${t}</button>`).join('')}</div>
      </app-tab-view-actionbar></div>
    <fieldset><legend>${esc(CFG.object)} Details</legend><div class="row view-fields"></div></fieldset></div>`;
  const view = wrap.querySelector('.view-fields');
  view.appendChild(H.text({ label: 'Name', name: 'view_name', value: r.name, disabled: true }));
  CFG.details.forEach(label => view.appendChild(H.text({ label: label.replace(/\s*\*$/, ''), name: 'view_' + label.replace(/\W+/g, ''), value: detail(r, label), disabled: true })));
  wrap.querySelectorAll('[role=tab]').forEach(tab => tab.addEventListener('click', () => { if (!tab.disabled) setTimeout(() => wrap.replaceWith(expanded(r, tab.textContent.trim())), 150); }));
  wrap.querySelector('.act-edit').onclick = () => {
    if (CFG.surface === 'page') { location.href = CFG.path + '/edit/' + encodeURIComponent(r.name); return; }
    openDrawer(r, 'edit');
  };
  wrap.querySelector('.act-clone').onclick = () => {
    if (CFG.surface === 'page') { location.href = CFG.path + '/clone/' + encodeURIComponent(r.name); return; }
    openDrawer(r, 'clone');
  };
  const menuAction = (btn, action) => {
    const menu = document.getElementById(btn.getAttribute('aria-controls')) || wrap.querySelector('#' + btn.getAttribute('aria-controls'));
    btn.onclick = () => { menu.hidden = !menu.hidden; btn.setAttribute('aria-expanded', String(!menu.hidden)); };
    menu.querySelectorAll('[role=menuitem]').forEach(item => item.addEventListener('click', () => {
      menu.hidden = true; btn.setAttribute('aria-expanded', 'false');
      confirmAction(r, action, env, version, item.textContent.trim());
    }));
  };
  menuAction(wrap.querySelector('.act-migrate'), 'migrate');
  const deploy = wrap.querySelector('.act-deploy');
  if (CFG.deploy === 'menu') menuAction(deploy, 'deploy');
  if (CFG.deploy === 'confirm') deploy.onclick = () => confirmAction(r, 'deploy', env, version, targets[0]);
  if (CFG.deploy === 'dialog') deploy.onclick = () => deployDialog(r, env, version, targets);
  return wrap;
}
function modal(title) {
  const m = document.createElement('div');
  m.className = 'dds__modal'; m.setAttribute('role', 'dialog'); m.setAttribute('aria-label', title);
  m.style.cssText = 'position:fixed;top:25%;left:32%;width:420px;background:#fff;border:1px solid #555;padding:16px;z-index:2600';
  document.body.appendChild(m);
  return m;
}
async function act(action, body, m) {
  const res = await fetch(CFG.api + '/' + action, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const out = await res.json();
  if (m) m.remove();
  toast(out.message, res.ok);
  if (res.ok) load();
}
function confirmAction(r, action, from, version, to) {
  const verb = action === 'deploy' ? 'Deploy' : 'Migrate';
  if (!to) { toast(`${r.name} cannot be ${action}ed from ${from}`, false); return; }
  const m = modal(`${verb} ${CFG.object}`);
  m.innerHTML = `<h3>${verb} ${esc(CFG.object)}</h3><p>${verb} ${esc(r.name)} version ${esc(version)} from ${from} to ${to}?</p>
    <button type="button" class="c-cancel">Cancel</button> <button type="button" class="c-ok">${verb}</button>`;
  m.querySelector('.c-cancel').onclick = () => m.remove();
  m.querySelector('.c-ok').onclick = () => act(action, { name: r.name, from, to, version }, m);
}
function deployDialog(r, from, version, targets) {
  const H = window.HIP;
  const m = modal(`Deploy ${CFG.object}`);
  m.innerHTML = `<h3>Deploy ${esc(CFG.object)}</h3><p>${esc(r.name)} version ${esc(version)} from ${from}</p>`;
  const form = document.createElement('form');
  form.append(H.row(H.dropdown({ label: 'Target Environment *', name: 'targetEnvironment', options: targets })),
              H.row(H.text({ label: 'Comments', name: 'comments', textarea: true })));
  const bar = H.el('<div class="actions"><button type="button" class="c-cancel">Cancel</button> <button type="button" class="c-ok">Deploy</button></div>');
  form.appendChild(bar);
  m.appendChild(form);
  bar.querySelector('.c-cancel').onclick = () => m.remove();
  bar.querySelector('.c-ok').onclick = () => {
    const to = form.querySelector('[formcontrolname=targetEnvironment] input').value;
    if (!to) { toast('Target Environment is required', false); return; }
    act('deploy', { name: r.name, from, to, version, comments: form.querySelector('[name=comments]').value }, m);
  };
}
async function openDrawer(r, mode) {
  const title = `${mode === 'clone' ? 'Clone' : 'Edit'} ${CFG.object}`;
  const backdrop = document.createElement('div'); backdrop.className = 'backdrop';
  const drawer = document.createElement('app-generic-drawer');
  drawer.innerHTML = `<div class="dds__drawer dds__drawer--open" role="dialog" aria-label="${title}"><a href="#" class="back">&#8249; Back</a>
    <h2 class="dds__drawer__title">${title}</h2><div class="loading">Loading...</div></div>`;
  const panel = drawer.firstElementChild;
  document.body.appendChild(backdrop);
  document.body.appendChild(drawer);
  const close = () => { drawer.remove(); backdrop.remove(); };
  panel.querySelector('.back').onclick = (e) => { e.preventDefault(); close(); };
  const form = window.__buildEditForm();
  if (CFG.editExtras) window.hipEditExtras(form);
  // The portal loads the record, fills the form, then shows it.  (Attached while
  // filled: a detached radio / switch fires no change event.)
  form.style.display = 'none';
  panel.appendChild(form);
  // Clone: the name is the one thing to change, so it stays editable.
  await window.hipPrefill(form, r.steps, mode === 'clone' ? CFG.readOnly.slice(1) : CFG.readOnly);
  form.querySelector('#submit').textContent = mode === 'clone' ? CFG.cloneLabel : CFG.commitLabel;
  panel.querySelector('.loading').remove();
  form.style.display = '';
  form.querySelector('#cancel').onclick = close;
  form.querySelector('#submit').onclick = async () => {
    const values = await window.hipReadBack(form, r.steps);
    const res = await fetch(CFG.api + (mode === 'clone' ? '/clone' : '/save'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: r.name, values }) });
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

# Edit page (Business Flow, Rule): the create form's page, filled from the record.
_EDIT_PAGE_HEAD = r"""<style>main { visibility: hidden; }</style>
<script>
window.__EDIT = __EDIT__;
window.addEventListener('DOMContentLoaded', async () => {
  const E = window.__EDIT;
  const main = document.querySelector('main');
  const heading = main.querySelector('h2');
  const clone = E.mode === 'clone';
  if (heading) heading.textContent = `${clone ? 'Clone' : 'Edit'} ${E.object}`;
  const form = main.querySelector('form');
  await window.hipPrefill(form, E.record.steps, clone ? E.readOnly.slice(1) : E.readOnly);
  let actions = form.querySelector(':scope > .actions');
  if (!actions) { actions = document.createElement('div'); actions.className = 'actions'; form.appendChild(actions); }
  actions.innerHTML = `<button type="button" id="cancel">Cancel</button> <button type="button" id="submit">${clone ? E.cloneLabel : E.commitLabel}</button>`;
  actions.querySelector('#cancel').onclick = () => { location.href = E.listing; };
  actions.querySelector('#submit').onclick = async () => {
    const values = await window.hipReadBack(form, E.record.steps);
    const res = await fetch(E.api + (clone ? '/clone' : '/save'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: E.record.name, values }) });
    const out = await res.json();
    const note = document.createElement('div');
    note.setAttribute('role', res.ok ? 'status' : 'alert'); note.className = res.ok ? 'toast' : 'toast--error';
    note.textContent = out.message;
    main.prepend(note);
    if (res.ok) setTimeout(() => { location.href = E.listing; }, 600);
  };
  main.style.visibility = 'visible';
});
</script>"""


def _fixture_script(fixture: str) -> str:
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    match = re.search(r"<!--HIP_DDS_KIT-->\s*<script>(.*?)</script>", html, re.S)
    if not match:
        raise ValueError(f"no replica script in {fixture}")
    return match.group(1)


class PhaseListingPortal:
    """HTTP server for one object family's listing, expander and Edit."""

    def __init__(self, family: str, *, ignore_on_save: tuple = ()) -> None:
        self.family = family
        # Labels the server silently drops on Save (a portal that "saves" but keeps the old value).
        self.ignore_on_save = set(ignore_on_save)
        self.spec = FAMILIES[family]
        self.records: List[Dict[str, Any]] = seed_records(family)
        self.posts: List[Dict[str, Any]] = []
        self.api = f"/api/{family}"
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
                path = unquote(urlparse(self.path).path)
                if path == portal.spec["path"]:
                    self._send(200, portal.listing_html().encode("utf-8"), "text/html; charset=utf-8")
                elif path.startswith(portal.spec["path"] + "/edit/") or path.startswith(portal.spec["path"] + "/clone/"):
                    mode = "clone" if path.startswith(portal.spec["path"] + "/clone/") else "edit"
                    record = portal.find(path.rsplit(f"/{mode}/", 1)[1])
                    if not record:
                        self._send(404, b"not found", "text/plain")
                        return
                    self._send(200, portal.edit_page_html(record, mode).encode("utf-8"), "text/html; charset=utf-8")
                elif path == portal.api:
                    self._send(200, json.dumps(portal.records).encode("utf-8"), "application/json")
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                portal.posts.append({"path": self.path, "body": body})
                action = self.path.rsplit("/", 1)[-1]
                if action in {"deploy", "migrate"}:
                    code, message = portal.promote(body, action)
                elif action == "clone":
                    code, message = portal.clone(body)
                else:
                    code, message = portal.save(body)
                self._send(code, json.dumps({"message": message}).encode("utf-8"), "application/json")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _cfg(self) -> Dict[str, Any]:
        return {"api": self.api, "path": self.spec["path"], "object": self.spec["object"], "surface": self.spec["surface"],
                "commitLabel": self.spec["commit_label"], "readOnly": self.spec["read_only"], "details": self.spec["details"],
                "editExtras": bool(self.spec.get("edit_extras")), "deploy": self.spec.get("deploy"),
                "cloneLabel": self.spec.get("clone_label") or "Save"}

    def listing_html(self) -> str:
        headers = "".join(f'<div role="columnheader" class="dds__th">{h}</div>' for h in self.spec["columns"])
        fixture = _fixture_script(self.spec["fixture"]) if self.spec["surface"] == "drawer" else ""
        return (_LISTING.replace("__TITLE__", self.spec["title"]).replace("__HEADERS__", headers)
                .replace("__KIT__", self.kit).replace("__PREFILL__", _PREFILL_JS).replace("__EXTRAS__", _EXTRAS_JS)
                .replace("__CFG__", json.dumps(self._cfg())).replace("__FIXTURE__", fixture))

    def edit_page_html(self, record: Dict[str, Any], mode: str = "edit") -> str:
        edit = {**self._cfg(), "record": record, "listing": self.spec["path"], "mode": mode}
        head = _EDIT_PAGE_HEAD.replace("__EDIT__", json.dumps(edit))
        html = replica_html(self.spec["fixture"])
        return html.replace("<head>", "<head>" + head + f"<script>{_PREFILL_JS}</script>", 1)

    def find(self, name: str) -> Dict[str, Any]:
        return next((r for r in self.records if r["name"] == name), {})

    def value(self, name: str, label: str, index: int = 0) -> Any:
        step = next((s for s in self.find(name).get("steps") or [] if s["label"] == label and int(s.get("index") or 0) == index), {})
        return step.get("value")

    def save(self, body: Dict[str, Any]) -> tuple:
        record = self.find(str(body.get("name") or ""))
        if not record:
            return 404, f"{self.spec['object']} not found"
        for posted in body.get("values") or []:
            if posted.get("label") in self.ignore_on_save:
                continue
            for step in record["steps"]:
                if step["label"] == posted.get("label") and int(step.get("index") or 0) == int(posted.get("index") or 0):
                    step["value"] = posted.get("value")
        return 200, f"{self.spec['object']} {record['name']} updated successfully"

    def clone(self, body: Dict[str, Any]) -> tuple:
        """A new object from the source's form; its name must not exist yet."""
        source = self.find(str(body.get("name") or ""))
        if not source:
            return 404, f"{self.spec['object']} not found"
        name_label = self.spec["read_only"][0]
        posted = {(v.get("label"), int(v.get("index") or 0)): v.get("value") for v in body.get("values") or []}
        name = str(posted.get((name_label, 0)) or "")
        if not name or self.find(name):
            return 409, f"{self.spec['object']} name already exists"
        steps = copy.deepcopy(source["steps"])
        for step in steps:
            key = (step["label"], int(step.get("index") or 0))
            if key in posted:
                step["value"] = posted[key]
        self.records.append({"name": name, "columns": [name, *source["columns"][1:]], "steps": steps, "envs": {"DEV": ["1.0"]},
                             "cloned_from": source["name"]})
        return 200, f"{self.spec['object']} {name} created successfully"

    def promote(self, body: Dict[str, Any], action: str) -> tuple:
        """Deploy / Migrate a version from one environment to another."""
        record = self.find(str(body.get("name") or ""))
        source, target, version = body.get("from"), body.get("to"), body.get("version")
        if not record or version not in (record["envs"].get(source) or []):
            return 400, "Source version not found"
        if target not in {"DEV": ["TEST1", "TEST2"], "TEST1": ["TEST2"], "TEST2": ["PROD"]}.get(source, []):
            return 400, f"{target} cannot be reached from {source}"
        if version in (record["envs"].get(target) or []):
            return 409, f"Version {version} already exists in {target}"
        record["envs"].setdefault(target, []).append(version)
        verb = "deployed" if action == "deploy" else "migrated"
        return 200, f"{self.spec['object']} {record['name']} {verb} to {target} successfully"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}{self.spec['path']}"

    def __enter__(self) -> "PhaseListingPortal":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
