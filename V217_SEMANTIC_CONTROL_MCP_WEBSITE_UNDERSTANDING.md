# v2.1.7 — HIP Semantic Control MCP / Website Understanding

## Final implementation verdict

The requested Layer-11 architecture is implemented as runtime code, not only documentation.
The existing HIP Intelligence MCP was expanded; no additional generic browser executor MCP was introduced.

## Execution architecture

```text
canonical input / reviewed configuration
              ↓
          AutoWebGLM
              ↓
 HIP Intelligence MCP / Semantic Brain
              ↓
   evidence fusion and confidence gate
      ↙          ↓           ↘
Playwright     DevTools      Gemma
AX/find        DOM/CDP       ambiguity only
      ↘          ↓           ↙
       exact semantic target
              ↓
      Playwright MCP executor
              ↓
       before/after verification
              ↓
       AgentQ reward / retry
```

## Implemented requirements

1. **`browser_find` first discovery** — semantic actions use Playwright MCP accessibility evidence and ref narrowing; ephemeral refs are never promoted to long-term identity.
2. **`browser_fill_form` structured filling** — independently verified ordinary text controls are grouped by section and filled in one MCP operation, followed by exact local and semantic effect checks. DDS/Material selects remain on specialized selection logic.
3. **Multi-source locator consensus** — reports Playwright accessibility, DevTools DOM, HIP historical fingerprint memory, local DOM semantics, HIP MCP consensus, and ambiguity-only vision evidence.
4. **Confidence policy** — >=0.90 execute, 0.75–0.89 re-observe, 0.55–0.74 explicit browser-find rediscovery/self-heal, <0.55 blocked.
5. **Semantic fingerprints** — role/label/section/framework identity; generated positional selectors are volatile and do not survive rerender as trusted identity.
6. **Before/after verification** — exact field commit and semantic effect proof are mandatory before advancing.
7. **MutationObserver intelligence** — records DOM generation plus dialog, drawer, listbox, row, spinner, accordion, field-state and SPA route events.
8. **HIP Intelligence MCP tools** — all requested `hip_*` website tools are exposed and required by Live GO/NO-GO.
9. **Chrome DevTools MCP role** — independent DOM/network/console witness, not a competing operational executor.
10. **Accessibility** — Playwright accessibility evidence is primary; no permanent axe/accessibility executor MCP was added. This remains an optional diagnostic enhancement.
11. **Vision** — Gemma is used only when semantic evidence remains ambiguous; it does not supply coordinates or independently authorize actions.
12. **Negative-action understanding** — controls are classified SAFE / CONDITIONAL / DANGEROUS and the classification is attached to the existing governed mutation authorization flow.
13. **Website state graph** — Document Type, Data Map, Rules, Transport Profile and BizFlow state-graph compilers remain integrated with learned verified transitions.
14. **No duplicate automation stack** — no Selenium MCP, Puppeteer MCP, second browser automation MCP, TypeScript MCP, or coordinate-only primary executor was added.

## New HIP Intelligence MCP tool surface

- `hip_get_current_surface`
- `hip_get_form_schema`
- `hip_find_control`
- `hip_find_owned_popup`
- `hip_get_repeatable_rows`
- `hip_get_required_fields`
- `hip_get_current_values`
- `hip_compare_expected_actual`
- `hip_get_safe_actions`
- `hip_verify_action_effect`
- `hip_get_route_identity`
- `hip_get_form_generation`

## Governed form-fill contract

For an ordinary text-field batch:

```text
canonical attribute/value
       ↓
semantic target proof per field
       ↓
AutoWebGLM intent alignment per field
       ↓
browser_find exact ref + section proof
       ↓
browser_fill_form (one structured call)
       ↓
exact local commit proof per field
       ↓
semantic post-action effect verification
       ↓
continue
```

If any governed precondition or postcondition fails, the batch fails closed. It does not silently revert to raw `locator.fill()` or JavaScript setters.

## Automated certification at source promotion

- v2.1.7 architecture suite: 25 / 25 PASS
- complete repository: 915 / 915 PASS

Live Dell SSO, HIP tenant, Dell AIA and live MCP process availability are external-environment dependencies and remain protected by the fail-closed Live GO/NO-GO gate.
