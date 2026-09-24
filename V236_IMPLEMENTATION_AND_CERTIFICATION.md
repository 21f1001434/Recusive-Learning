# HIP Portal V236 / v2.3.6 — All-Phase Completion & Full Attribute Hardening

## Live failure addressed
The V235 runtime could still misclassify multiple phases as BLOCKED and could pass a form while optional-but-supplied attributes were not filled. V236 corrects both problems across Data Map, Source/Target Document Type, Rule, Source/Target Transport Profile, and BizFlow.

## Implementation changes
- Every nonblank input-owned state-graph node is a completion requirement, even if the live portal marks the control optional.
- An optional portal control with a supplied mission value can no longer be silently treated as successful when absent.
- Semantic binding repair now considers every supplied unresolved attribute, not only `required` controls.
- Exact execution audit includes input-owned expected count, exact count, unresolved node IDs, and coverage percentage.
- Phase success requires 100% exact input-owned node coverage.
- Authoritative mutation provenance remains required for values that actually needed mutation; values already exact may be verification-only.
- Deterministic replay readiness no longer blocks current-form completion. It disables fast-replay learning/promotion and produces a warning.
- Mission assurance now separates current-form completion from learning/replay promotion. Current completion requires exact deterministic UI state, independent judge pass, and complete input/control coverage. MCP/API/trajectory/golden/replay dimensions govern `promotion_allowed` only.
- V235 browser-session ownership, disconnect recovery, safe semantic anchors, V234 visual overlay and adaptive-memory behavior remain intact.

## Certification
- Repository: 1,176 collected; 1,175 passed; 1 skipped; 0 failed.
- V236 focused regression plus foundational all-phase tests: 71 passed; 0 failed.
- Local Chromium final mission: 7/7 phases passed; BizFlow Edit → Save → Validate → Deploy passed.
- Local UAT uses a mock portal and reports `dell_environment_contacted=false`.
- Python compileall passed.
- `webui/app.js` passed Node syntax validation.
