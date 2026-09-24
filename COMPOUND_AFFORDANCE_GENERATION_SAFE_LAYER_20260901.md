# HIP Portal Agent v1.9.3 — Compound Affordance + Generation-Safe Execution

This layer extends v1.9.2 for portal actions that are not directly visible in the DOM.

## Added
- Scoped compound action traversal: `More Actions / ⋮ / … -> Edit|Clone|Migrate|Deploy|Delete|Download`.
- Overflow menus must have positive entity/section alias evidence; global kebab menus are rejected.
- Affordances are re-resolved immediately before each click to survive Angular/DDS rerenders.
- DDS/Angular-style generated DOM IDs are treated as volatile and are avoided when building semantic selectors.
- Effect verification no longer accepts action text that already existed before the click.
- Future-task execution uses the compound semantic traversal when a learned row action is hidden behind an overflow menu.
- Mutation governance remains enforced for Clone/Migrate/Deploy/Delete and other state-changing actions.

## Why
A portal can expose many controls as icons or nested option menus. Recognizing a pencil/rocket is not enough when the icon only appears after opening `⋮`, and selectors captured before Angular rerenders can become stale. v1.9.3 treats the sequence itself as a semantic capability and proves each structural transition.
