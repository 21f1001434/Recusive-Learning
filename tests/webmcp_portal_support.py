"""V243R33 replicas: HIP pages that offer WebMCP tools (navigator.modelContext).

* ``tp_page(tmp_path)`` -- the Transport Profile form replica plus the page's own
  WebMCP tools, registered only when ``navigator.modelContext`` exists (as a
  WebMCP-aware page does):
  - ``fill_transport_profile_form`` (form edit): sets every field through the
    page's own components, in dependency order; ``window.__toolBug`` makes it
    put a wrong Profile Name (a buggy page tool);
  - ``get_deployment_groups`` (read-only);
  - ``save_transport_profile`` (mutating): sets ``window.__saved``.
* ``DECLARATIVE_HTML`` -- a ``<form toolname=… tooldescription=…>`` (declarative
  WebMCP) counting its submits.
* ``FAKE_NATIVE_JS`` -- a browser with native WebMCP: ``navigator.modelContext``
  and Chrome's ``navigator.modelContextTesting`` (``listTools`` / ``executeTool``).

Pages are served from files so the browser context's init scripts run first,
as on the live portal.
"""
from __future__ import annotations

from pathlib import Path

from phase_replica_support import replica_html

TP_TOOLS_JS = r"""
<script>
(function () {
  if (!navigator.modelContext) return;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  window.__webmcpCalls = [];
  async function host(name, timeout = 5000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const el = document.querySelector(`[formcontrolname="${name}"]`) || document.querySelector(`input[type=radio][name="${name}"]`);
      if (el) return el;
      await wait(50);
    }
    return null;
  }
  async function put(name, value) {
    const el = await host(name);
    if (!el || value === undefined || value === null || value === '') return false;
    if (el.tagName === 'DDS-DROPDOWN') {
      el.querySelector('input').click();
      await wait(30);
      const opt = Array.from(el.querySelectorAll('[role=option]')).find((o) => o.textContent.trim() === String(value));
      if (!opt) { el.__close && el.__close(); return false; }
      opt.click();
      if (el.getAttribute('selection') === 'multiple' && el.__close) el.__close();
      await wait(250);
      return true;
    }
    if (el.type === 'radio') {
      const radio = Array.from(document.querySelectorAll(`input[type=radio][name="${name}"]`)).find((r) => {
        const lab = document.querySelector(`label[for="${r.id}"]`);
        return (lab && lab.textContent.trim().toLowerCase() === String(value).toLowerCase()) || r.value === String(value);
      });
      if (!radio) return false;
      radio.click();
      await wait(250);
      return true;
    }
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, String(value));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }
  const ORDER = [['systemType', 'systemType'], ['partnerName', 'systemName'], ['profileName', 'profileName'],
    ['profileUsage', 'profileUsage'], ['deploymentGroup', 'deploymentGroup'], ['interfaceType', 'interfaceType'],
    ['interfaceEnvironment', 'interfaceEnvironment'], ['existingAccount', 'existingAccount'],
    ['existingAccountName', 'existingAccountName'], ['useExistingFolder', 'useExistingFolder'],
    ['subscriptionFolder', 'subscriptionFolder'], ['fileFilteringPattern', 'fileFilteringPattern'],
    ['postTransferAction', 'postTransferAction'], ['isCompressionRequired', 'isCompressionRequired'],
    ['documentType', 'documentType']];
  const str = (title) => ({ type: 'string', title });
  navigator.modelContext.registerTool({
    name: 'fill_transport_profile_form',
    description: 'Fills the Create Transport Profile form with the given values. It does not save.',
    inputSchema: { type: 'object', required: ['systemType', 'profileName'], properties: {
      systemType: str('System Type'), partnerName: str('System Name'), profileName: str('Profile Name'),
      profileUsage: { type: 'string', title: 'Profile Usage', enum: ['Sender', 'Receiver'] },
      deploymentGroup: str('Deployment Group'), interfaceType: str('Interface Type'), interfaceEnvironment: str('Interface Environment'),
      existingAccount: { type: 'string', title: 'Existing Account', enum: ['Yes', 'No'] }, existingAccountName: str('Existing Account Name'),
      useExistingFolder: { type: 'string', title: 'Use Existing Folder', enum: ['Yes', 'No'] }, subscriptionFolder: str('Subscription Folder'),
      fileFilteringPattern: str('File Filtering Pattern'),
      postTransferAction: { type: 'string', title: 'Post Transfer Action', enum: ['Move To Archive', 'Delete', 'None'] },
      isCompressionRequired: { type: 'string', title: 'Is Compression Required', enum: ['TRUE', 'FALSE'] },
      documentType: { type: 'string', title: 'Document Type', enum: ['XML_DellAutoASN_10_U-HAUL_ANS_IB (1.0)', 'XML_SHIPMENT_NOTICE_10_U-HAUL_ANS_OB (1.0)'] },
    } },
    async execute(args) {
      window.__webmcpCalls.push({ tool: 'fill_transport_profile_form', keys: Object.keys(args || {}) });
      const done = [];
      for (const [param, control] of ORDER) {
        let value = (args || {})[param];
        if (param === 'profileName' && window.__toolBug) value = 'WRONG_NAME_FROM_BUGGY_TOOL';
        if (await put(control, value)) done.push(param);
      }
      return { content: [{ type: 'text', text: JSON.stringify({ filled: done }) }] };
    },
  });
  navigator.modelContext.registerTool({
    name: 'get_deployment_groups', description: 'Deployment groups offered for a profile usage.',
    annotations: { readOnlyHint: true },
    inputSchema: { type: 'object', properties: { profileUsage: { type: 'string' } } },
    async execute({ profileUsage }) {
      window.__webmcpCalls.push({ tool: 'get_deployment_groups' });
      return { content: [{ type: 'text', text: JSON.stringify(profileUsage === 'Sender' ? ['da-sender-sftphaft-dce-shared'] : ['pt-receiver-sftphaft-dce-shared']) }] };
    },
  });
  navigator.modelContext.registerTool({
    name: 'save_transport_profile', description: 'Saves the Transport Profile to the DEV environment.',
    inputSchema: { type: 'object', properties: {} },
    async execute() { window.__webmcpCalls.push({ tool: 'save_transport_profile' }); window.__saved = true;
      return { content: [{ type: 'text', text: 'saved' }] }; },
  });
})();
</script>
"""

DECLARATIVE_HTML = """<!DOCTYPE html><html><head><title>Create Rule</title></head><body>
<form toolname="fill_rule_draft" tooldescription="Fill the Create Rule form" id="rule">
  <label for="n">Rule Name *</label><input id="n" name="ruleName" required toolparamdescription="Unique rule name">
  <label for="p">Priority</label><select id="p" name="priority"><option value="">Select</option><option>High</option><option>Low</option></select>
  <fieldset><legend>Status</legend>
    <input type="radio" id="s1" name="status" value="Active"><label for="s1">Active</label>
    <input type="radio" id="s2" name="status" value="Inactive"><label for="s2">Inactive</label></fieldset>
  <label for="c">Notify</label><input type="checkbox" id="c" name="notify">
  <button type="submit">Save</button>
</form>
<form toolname="submit_rule" tooldescription="Create the rule" toolautosubmit id="rule2"><input name="ruleName"></form>
<script>
  window.__submits = 0;
  for (const f of document.forms) f.addEventListener('submit', (e) => { e.preventDefault(); window.__submits += 1; });
</script>
</body></html>"""

FAKE_NATIVE_JS = r"""
(() => {
  const tools = new Map();
  const mc = {
    provideContext(c) { for (const t of (c && c.tools) || []) tools.set(t.name, t); },
    registerTool(t) { tools.set(t.name, t); return { unregister() { tools.delete(t.name); } }; },
    unregisterTool(n) { tools.delete(n); }, clearContext() { tools.clear(); },
  };
  Object.defineProperty(navigator, 'modelContext', { value: mc, configurable: true });
  Object.defineProperty(navigator, 'modelContextTesting', { configurable: true, value: {
    async listTools() { window.__nativeListed = (window.__nativeListed || 0) + 1;
      return Array.from(tools.values()).map((t) => ({ name: t.name, description: t.description, inputSchema: JSON.stringify(t.inputSchema || {}) })); },
    async executeTool(name, argsJson) { window.__nativeExecuted = name; const t = tools.get(name);
      const r = await t.execute(JSON.parse(argsJson || '{}'), {}); return r; },
  } });
})();
"""


def tp_page(tmp_path: Path, *, bug: bool = False) -> str:
    html = replica_html("transport_profile_full_dds.html").replace(
        "duplicateMessage: 'Transport Profile already exists in DEV environment.'", "")
    if bug:
        html = html.replace("<script>", "<script>window.__toolBug = true;</script><script>", 1)
    html = html.replace("</body>", TP_TOOLS_JS + "</body>")
    path = Path(tmp_path) / ("tp_webmcp_bug.html" if bug else "tp_webmcp.html")
    path.write_text(html, encoding="utf-8")
    return path.as_uri()


def declarative_page(tmp_path: Path) -> str:
    path = Path(tmp_path) / "rule_declarative.html"
    path.write_text(DECLARATIVE_HTML, encoding="utf-8")
    return path.as_uri()
