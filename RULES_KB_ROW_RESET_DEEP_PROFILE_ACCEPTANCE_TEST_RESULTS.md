# Rules KB Row Reset + Deep Profile Acceptance Results

Generated on: 2026-07-07

## Validation

```text
134 passed
```

## Coverage Added

- Retries Rules UI row-action learning after a clean listing reset when search/row click fails.
- Treats an already-expanded Rules row (`Collapse the row`) as read-only evidence instead of clicking it closed.
- Adds periodic listing cleanup during long Rules runs to avoid alternating search failures.
- Keeps safety unchanged: no Save/Create/Submit/Delete.
