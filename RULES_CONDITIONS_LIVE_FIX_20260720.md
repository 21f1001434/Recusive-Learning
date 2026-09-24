# Rules Conditions Live Fix — 2026-07-20

## Problem confirmed from the authenticated run

The Rules phase reported that repeatable Conditions were filled, but the live Angular form still contained only the default condition row. The later exact state-graph transaction then stopped on **Execute Action(s) When** because the binder gave the same score to that DDS combobox, Condition Type, and Operator.

## Correct transaction

For every row in `objects.rule.conditions.rows`:

1. Select **Execute Action(s) When** through the DDS combobox driver.
2. Fill the current condition row in dependency order: Condition Type, Operator, Value, Attribute Name/Unit.
3. Verify all values committed to the current Angular/DDS row.
4. When another input row remains, locate only the small `+` in the **Conditions** legend.
5. Click once without force-click or DOM fallback.
6. Require the live condition row count to change from `N` to exactly `N + 1`.
7. Fill the newly created row and repeat.

This is the same safe repeatable-row behavior required for Actions, but it uses a Conditions-specific locator and the `conditions` Angular form array.

## Fail-closed behavior

The agent does not click Conditions `+` when:

- the current row is incomplete or not exactly committed;
- the active Create Rule surface is lost;
- the Conditions `+` is ambiguous;
- a click creates zero rows or more than one row;
- the final live row count differs from the input row count.

## Regression coverage

- Exact **Execute Action(s) When** label wins over Condition Type and Operator.
- The simulated portal ignores `+` until the current row is valid; the agent fills first, adds exactly one row, and fills the second row.
- Rules DDS comboboxes use `select_dds_combobox`.
- Complete suite: **415/415 passed**.
