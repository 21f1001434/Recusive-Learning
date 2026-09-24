# Rules Physical Row Detection and Attribute Rebind Fix — 2026-07-20

## Live run reviewed

`UHAUL-POASN-FULL-DUMMY-20260720-191952`

## Failure

The Rules phase stopped safely before clicking **Create Condition**. The audit reported:

- `row_count_before: 0`
- a focused **Attribute Name/Unit** combobox
- an owned DDS popup containing `Receiver`
- no committed Attribute value
- no second physical Conditions row

The visible Rule form did contain the default first condition row. The row detector lost it only after Angular revealed the dependent Attribute control and DDS interaction was active.

## Root causes

### 1. Zero-sized Angular wrapper was treated as a missing row

The previous inspector used geometry on the outer FormArray child and the `dds-dropdown` custom-element host. During the dependent Attribute rerender, either wrapper could temporarily report a zero-sized bounding box while its nested Condition Type input remained visible. The real row was therefore miscounted as zero.

### 2. Attribute verification used the pre-click input

Selecting a DDS Attribute option can rerender the Angular row and replace the original input id. Verifying only through the pre-click locator could report an empty value even when a new row input held the committed selection.

### 3. Escape raced with a collapsed DDS control

The live evidence showed `expanded_count=0` before settlement, but pressing Escape was followed by an expanded popup containing `Receiver`. Escape is now sent only when an expanded combobox or visible popup is already proven.

## Fix

### Physical row identity

The Conditions inspector now:

1. Locates the visible nested Condition Type `input[role=combobox]` inside `formarrayname="conditions"`.
2. Walks up to its nearest `[cdkdrag]` or `.dds__row` physical row.
3. Deduplicates physical row elements.
4. Uses the nested Condition Type input id as the stable row identity.
5. Never uses custom-element host geometry as the row-existence signal.

### Fresh Attribute commit proof

After clicking the owned `Receiver` or `Sender` option, the runtime:

1. Sends one native Tab commit/blur signal.
2. Re-inspects the Conditions FormArray.
3. Rebinds the exact requested row.
4. Reads the newly rendered Attribute control.
5. Accepts success only when the fresh live value exactly matches input.json.
6. Locks the newly rebound selector, not the stale selector.

### Popup settlement

- Escape is used only when `aria-expanded=true` or a visible owned popup is proven.
- A focused but collapsed combobox is settled through change/focusout/blur only.
- No overlay, page shell, background table or destructive control is clicked.

## Expected U-HAUL result

| Row | Condition Type | Operator | Value | Attribute Name/Unit |
|---|---|---|---|---|
| 1 | Attributes | Equals | uhaul | Receiver |
| 2 | Attributes | Contains | DELL | Sender |

The Conditions `+` remains fail-closed and is clicked only after row 1 is freshly verified as exact.

## Validation

- Python compilation: passed
- Targeted Rules tests: 13 passed
- Complete source-tree suite: 428/428 passed
- Clean extracted ZIP suite: 428/428 passed
- Nested-combobox physical-row regression: passed
- Angular row-replacement Attribute rebind regression: passed
- Collapsed-combobox Escape race regression: passed
- Existing Conditions +Add, FormArray, owned-popup and dependency regressions: passed
