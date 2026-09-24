# BizFlow Deep Capability Learning Milestone — 2026-08-25

## Goal
Deep-learn the HIP BizFlows surface as a reusable browser-intelligence capability rather than a one-off form fill.

## Implemented
- Listing/open/search/filter/filter-option/pagination learning.
- Exact entity-row expansion and revealed action inventory.
- Safe draft inspection for Edit/Clone/View/Details/History/Audit surfaces.
- Safe Migrate/Deploy/Delete prerequisite probes with a request-abort barrier; no backend mutation is delivered.
- Full unsaved Create Biz Flow learning through the production `capture_and_fill_bizflow_multitab_form` runtime.
- Value-free dependency blueprint and deterministic replay promotion.
- UI action -> exact request-ID -> API transaction causal trace.
- CLI, FastAPI, Streamlit, and PowerShell launcher integration.

## Production BizFlow runtime reused
The deep learner intentionally reuses the hardened multi-tab BizFlow runtime rather than implementing a second simplified form filler. This preserves:
1. Flow Details.
2. Configure Source.
3. Flow Identifier repeatable rows.
4. Configure Target(s).
5. Process Step accordion creation/rebinding.
6. Mapping Transformer conditional fields.
7. Enricher target filename config and filename-part rows.
8. Configure Routing top-level `+ Add`.
9. Routing Condition rows.
10. Routing Action row.
11. Section-level deterministic/text/vision judge repair flow.
12. Target-first branch restoration after exploration.

## Current bundled input structural contract
- 58 executable/verified BizFlow nodes.
- 913 scheduler dependency edges after dependency-contract enrichment.
- 2 Flow Identifier rows.
- 2 Process Steps.
- 2 Routing Conditions.
- 1 Routing Action.
- 3 structural/read-only accounted leaves: Current Flow Version and two Process Step numbers.
- 0 dependency cycles.

## Persistence policy
Capability memory stores field/input paths, sections, row kinds/indices, dependency IDs, semantic selectors, wait contracts, verification strategy, capability/API relations, and successful replay ordering. Current customer values are not persisted as reusable replay values.

## Safety
Discovery never executes Save/Create/Submit. Mutation-grade row actions are probed only while POST/PUT/PATCH/DELETE and suspicious mutation-token GET paths are aborted before backend delivery.

## Validation
590/590 tests passed on the source tree before packaging.
