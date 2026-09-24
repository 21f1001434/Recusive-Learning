# Transport Profiles Deep Capability Learning Milestone — 2026-08-25

## Scope
This milestone extends the HIP Browser Intelligence Platform with deep learning for the Transport Profiles page family while preserving the existing Data Maps, Document Types, Rules, seven-phase configuration mission, AutoGen 0.7.5 requirement, MCP contracts, API evidence and mutation safety.

## Deep-learning coverage
The learner now studies listing search, filters and filter option sets, pagination, exact-row expansion, revealed row actions, safe draft surfaces, safe mutation prerequisites, and the complete Source and Target unsaved Create Transport Profile form.

## Parent/child hierarchy
The state graph was hardened so Transport Profiles are not treated as a flat form. The verified order includes System Type before System/Partner/Application, Interface Type before interface-specific children, Existing Account before Account, Account before Use Existing Folder, and Use Existing Folder before the Subscription Folder branch. Source and Target objects remain isolated and each receives its own replay profile.

## Reuse of production runtime
The deep learner reuses the existing shared stateful form runtime plus the hardened Transport Profile wizard expansion logic. It does not create a second simplified filler. The wizard may be safely expanded with the current phase values so hidden controls mount, after which the dependency runtime verifies or repairs all exact current-input values.

## API evidence
Every form exercise captures request/response transactions and writes an action-to-API causal trace based on exact request IDs recorded by BrowserSession action events. API contracts are promoted structurally; current account, customer and profile values are not stored in persistent capability memory.

## Safety
Edit/Clone/View/Details/History/Audit may be inspected and closed without saving. Migrate/Deploy/Delete can be probed only behind a Playwright route-abort barrier. POST, PUT, PATCH and DELETE requests are aborted before backend delivery during safe discovery. Save/Create/Submit are never executed by this mission.

## Persistent memory
HIP Capability Graph schema is now `hip.capability-graph.v6`. Replay profiles store input paths, semantic controls, dependencies, verification and wait contracts, but never current customer values.
