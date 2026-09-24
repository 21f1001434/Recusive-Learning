# v2.2.4 — Agentic Form Entry + BizFlow ReAct Hardening

Date: 2026-09-09

## Purpose

Make form opening a first-class, verified autonomous transaction for every HIP configuration phase, with BizFlow's additional template-card/link and multi-tab workflow modeled explicitly.

## Standard governed phase entry

For Data Map, Source/Target Document Type, Rule and Source/Target Transport Profile:

1. Authenticate and establish the persistent browser/MCP surface.
2. Navigate to the exact canonical listing page.
3. ReAct-observe the page and locate the page-level top-right `+ Add` using semantic DOM/accessibility evidence and Playwright MCP `browser_find`.
4. Authorize the control as a structural opener through AutoWebGLM + semantic action policy.
5. Execute through Playwright MCP/BrowserSession.
6. Prove that the expected create form surface exists.
7. Only after proof, begin deterministic/semantic field filling.
8. If entry is not proved, re-observe, route back to the canonical listing, dismiss safe transient surfaces, and retry within a bounded recovery loop.
9. If still unproved, raise `HIP_FORM_ENTRY_NOT_OPENED` and let the existing RuntimeSelfHealController handle the recoverable `active_surface_lost` condition.

## BizFlow-specific entry

BizFlow is modeled as a two-stage opener:

`BizFlows listing -> + Add -> template/card picker -> template link/action -> multi-tab create form`.

The launcher first searches the visible template card for a direct semantic link/action such as the template name, Create Biz Flow, Use Template, Select Template, Start, Open or Continue. It rejects unrelated navigation/mutation controls. The historical overflow-menu path remains a governed secondary recovery path. The create form must be proved before tab filling starts.

## BizFlow tab transaction

Before each tab's fields are filled, `_ensure_bizflow_tab_open()` runs a bounded ReAct loop:

`observe selected tab -> browser_find requested tab -> governed click -> read aria-selected/active panel -> prove requested tab active`.

If proof fails, it re-observes and safely dismisses transient overlays before retrying. Failure raises `HIP_BIZFLOW_TAB_NOT_OPENED`; no controls from the previous tab are filled.

## Safety and intelligence

The models may help re-discover a lost opener, but expected intent is deterministic and mutation policy remains fail-closed. Page-level `+ Add` is a structural opener, not a final Save/Create/Deploy mutation. Playwright MCP remains the governed web executor; DevTools and HIP Intelligence remain evidence/witness channels; Gemma is used only when ambiguity requires visual evidence.

## Regression coverage

`tests/test_v224_agentic_form_entry.py` covers standard entry, route-back retry, BizFlow intermediate/card flow, direct template link integration, structural-opener policy, tab retry/proof and fail-closed tab behavior.
