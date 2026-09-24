# HIP Portal v2.1.7 Build Certification

Date: 2026-09-05

## Source promotion status

The v2.1.7 source tree implements the HIP Semantic Control MCP / Website Understanding architecture requested for Layer 11.

### Runtime changes certified

- browser-find-first accessibility narrowing and ephemeral Playwright ref resolution;
- structured Playwright MCP `browser_fill_form` for verified ordinary text fields;
- complete HIP Intelligence MCP website-specific `hip_*` semantic tool surface;
- HIP Intelligence MCP required by default in all shipped configs;
- Playwright MCP 0.0.79 pinned consistently in Python config, YAML configs and package scripts;
- Live GO/NO-GO requires `browser_find`, `browser_fill_form`, and the complete HIP semantic MCP inventory;
- confidence tiers: 0.90 execute, 0.75 re-observe, 0.55 rediscover/self-heal, otherwise blocked;
- semantic fingerprints and rerender revalidation remain fail-closed;
- SAFE / CONDITIONAL / DANGEROUS action classification integrated with the mutation authorization evidence path;
- MutationObserver structural intelligence for dialog/drawer/listbox/row/spinner, accordion, field-state and route events;
- Chrome DevTools MCP remains an independent DOM/network/console witness;
- Gemma vision remains ambiguity-only and never supplies coordinate clicks;
- specialized DDS/Material controls remain on the existing semantic DDS path;
- no Selenium/Puppeteer/TypeScript/second generic browser executor MCP was introduced.

### Automated source-tree verification

- v2.1.7 architecture regression: **25 / 25 PASS**
- complete repository regression: **915 / 915 PASS**
- Python AST: **226 files / 0 errors**
- shipped JavaScript syntax: **3 files / 0 errors**
- FastAPI import: **42 routes**
- package version: **2.1.7**

### Distribution

Wheel: `dist/hip_portal_id_agent-2.1.7-py3-none-any.whl`

Wheel SHA-256:

`dae262a79594ada2bc117f6da0e8c48134a64e498fdc75e72703bc2cea6d1f14`

The exact final ZIP is separately clean-extracted and re-tested after packaging; those results are recorded in the external final release certificate so the certificate does not change the already-tested archive bytes.
