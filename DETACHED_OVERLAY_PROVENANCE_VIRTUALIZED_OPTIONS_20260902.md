# HIP Portal Agent v1.9.4 — Detached Overlay Provenance + Virtualized Options

## Problem fixed

In Angular/CDK/DDS portals, a correctly scoped row control such as `UHAL -> More Actions` may open a menu under `document.body` or an overlay container. The detached menu no longer has UHAL/row ancestry, so requiring the target (`Edit`, `Clone`, `Migrate`, `Deploy`, `Delete`, `Download`) to contain the same alias can reject the correct action. Removing the alias requirement globally would be unsafe because an unrelated global action could match.

Long menus can also be virtualized: the desired option may not exist in the DOM until the menu itself is scrolled.

## v1.9.4 execution model

1. Resolve the opener using v1.9.3 local semantic scope.
2. Re-resolve the opener immediately before click.
3. Snapshot active actionable surfaces.
4. Click the opener and prove a menu/popover surface appeared.
5. Bind the new surface by `aria-controls` / `aria-owns` when available; otherwise bind only a single unambiguous new actionable surface.
6. Resolve the requested target **inside that proven surface only**.
7. If not visible, scroll only that surface in bounded monotonic steps and re-inventory.
8. Re-resolve the target inside the same proven surface immediately before click.
9. Apply the existing mutation-governance gate and structural effect verification.

## Safety properties

- No global target fallback after provenance has been established.
- Equal-confidence multiple overlays fail closed.
- Scrolling performs no click or mutation.
- Virtualized discovery is bounded.
- Mutation governance remains unchanged.
- AutoWebGLM remains the primary browser decision layer because the final click still executes through BrowserSession.click_and_wait.

## Verification

- New v1.9.4 focused tests: 4 / 4 PASS.
- v1.9.3 + v1.9.4 focused tests: 8 / 8 PASS.
- Full suite after implementation: 745 / 745 PASS.
