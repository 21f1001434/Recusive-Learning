# Autonomous Mission + Rule Mapping Commit Fix — 2026-07-21

## Live failure addressed

Run `UHAUL-POASN-FULL-DUMMY-20260721-015927` reached the Rule Actions row and found the exact owned Mapping Identifier option:

- Expected: `DELLCoXMLASNXX08C_U-HAUL(1.0)`
- Owned listbox option count after filtering: `1`
- Input value after interaction: exact expected value
- DDS option state after interaction: `aria-selected="true"` / `data-selected="true"`
- Playwright result: `Locator.click` timed out after it had started the click action

The portal had already committed the selection, but the runtime classified the Playwright timeout as a failed selection and repeated the same interaction. The Rule prerequisite gate therefore stayed false and Conditions were never attempted.

## Runtime correction

`hip_id_agent/rules_kb.py` now treats an option-click timeout as a state-verification event rather than automatic failure.

After every owned-option interaction it:

1. Re-probes only the listbox referenced by the Mapping Identifier combobox.
2. Requires both:
   - the exact owned option is selected; and
   - the combobox value exactly matches the expected Mapping Identifier.
3. Rebinds the Angular control after DDS rerender.
4. Emits only safe `change` and `blur` commit events.
5. Closes the owned dropdown without clicking the page shell.
6. Re-verifies the exact value after settle/rebind.
7. Promotes the field as complete and continues to Conditions.

If Playwright times out before selected state appears, one bounded fallback ladder is used:

- owned exact-option DOM `click` dispatch;
- then DDS keyboard `Enter` only when filtering has produced exactly one enabled owned option.

No background Rule inventory option or checkbox is searched or clicked.

Typed text alone is never accepted. The runtime requires selected-option evidence unless the normal Playwright click completed and the freshly rebound committed control independently verifies the value.

## Autonomous mission profile

A new one-switch CLI profile is available:

```text
--autonomous-mission
```

It enables:

- the full Data Map → Source Document Type → Target Document Type → Rule → Source Transport Profile → Target Transport Profile → BizFlow objective;
- unlimited progress-driven safe self-healing;
- exploration/exploitation alternation;
- automatic fail-closed resume of judge-approved completed phases;
- Playwright MCP + Chrome DevTools MCP + HIP Intelligence MCP evidence;
- golden-image feedback;
- deterministic, text and vision judge gates;
- judge-approved trajectory promotion to Portal Brain.

It cannot be combined with `--rules-only` or a partial `--phases` list.

## Safety invariants

The agent still does not click or execute:

- Save
- Create
- Submit
- Delete
- Deploy
- Publish
- Update
- Remove
- Enable / Disable
- Confirm

The autonomous mission completes the requested no-save form-fill and verification objective.
