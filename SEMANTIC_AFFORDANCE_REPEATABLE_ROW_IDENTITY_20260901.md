# v1.9.2 Semantic Affordance + Repeatable Row Identity

## Problem addressed
HIP is an Angular/DDS application. Repeatable arrays can be rebuilt and reordered after `+ Add`; icon-only buttons may have no visible text. Physical row index and literal button text are therefore insufficient execution contracts.

## Repeatable row identity
Each live row is represented by a value-free structural fingerprint and committed semantic anchors. Expected `input.json` rows are matched to live rows using committed discriminating values. A completely blank row may use physical order only provisionally. Once an expected value commits, the next capture upgrades the row to semantic identity and later Angular reorder cannot redirect the next input row into it. Conflicting nonblank rows fail closed instead of being rebound by position.

## Semantic affordances
The runtime inventories only visible actionable elements and derives an affordance from: accessible name, visible text, title, ARIA state, SVG/use/icon metadata, classes/test IDs, and local section/row context. Supported intents include Add Row/Open Add Form, Expand/Collapse, More Actions, Edit, Clone, Migrate, Deploy, Delete, Next/Back, Close, Search, Refresh, Filter, Settings, Retry, Upload and Download.

A glyph alone is never enough when ambiguous. In particular a bare `+` is accepted as `add_row` only when local aliases/context match the intended repeatable section. Every repeatable Add must then prove an exact row-count transition `N -> N+1`.

## Execution order
`input.json / task -> expert skill -> AutoWebGLM primary action intent -> AgentQ safety/reward -> semantic affordance binding / deterministic DDS tool -> effect/state verification -> generation rebind -> next node`.

## Governance
Icon understanding does not weaken mutation safety. Clone, Migrate, Deploy, Delete, Save and Create affordances can be discovered, but execution still requires the existing mutation authorization gate.
