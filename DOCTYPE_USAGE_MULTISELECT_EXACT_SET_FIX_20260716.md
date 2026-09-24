# Document Type Usage Multi-Select Exact-Set Fix

## Failure observed

Run `UHAUL-POASN-FULL-DUMMY-20260716-194004` completed Data Map and entered Source Document Type. All five `Usage` controls expected the set:

- Flow Identifier Expression
- Logging
- Mapping
- Routing

The run failed because none of the five rows retained the exact four-value set.

## Root cause

The live DDS listbox exposed nine options. The previous driver built selectors like:

```text
#dropdown-popup-list-989121544 button:nth-of-type(1)
```

Each option is wrapped separately, so that selector matched all nine first buttons and Playwright MCP rejected it in strict mode. Direct Playwright fallback could temporarily click an option, but the next iteration sent Delete while the search input was empty. DDS treats Delete/Backspace on an empty multiple-selection input as removal of the most recent selected chip. The result was the visible “selected, then unselected” behavior.

Selection evidence was also incomplete because the component uses `aria-checked=true`, `data-selected=true`, and `.dds__dropdown__item-selected`; the collector primarily expected `aria-selected=true`.

## Implemented runtime contract

1. Open the exact row-scoped Usage control.
2. Read the associated listbox ID from `aria-controls`.
3. Read every option and its `aria-posinset`.
4. Build one unique selector per option:

```text
[id="<listbox-id>"] [role="option"][aria-posinset="<position>"]
```

5. Remove only explicit extra selections.
6. Select each missing value without clearing the empty search input.
7. After each click, verify that the new value is selected and every previous selection remains selected.
8. Reopen the listbox for authoritative verification.
9. Compare the exact selected set against the current phase-local `input.json` array/string.
10. Continue only when the sets are equal.

The shared driver is used by both Source and Target Document Type phases.

## Evidence handling

Selected options are now recognized through:

- `aria-selected=true`
- `data-selected=true`
- `aria-checked=true`
- `.dds__dropdown__item-selected`
- selected chips/labels when available

Collapsed text such as `4 selected` is treated as a count hint only, never as proof of which four options were selected.

## Safety

The patch does not click Save, Create, Submit, Delete, Deploy, Publish, Update, or Confirm. “Delete” in this report refers only to the keyboard key that was incorrectly used inside the multi-select search control; that destructive key behavior has been removed for empty inputs.
