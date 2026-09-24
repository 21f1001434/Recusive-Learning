# Portal Brain legacy selector migration fix

## Failure fixed

The live command stopped before opening HIP forms with:

```text
AttributeError: 'str' object has no attribute 'get'
```

The exception occurred in `PortalBrain._preferred_locator()` while deterministic plans were being compiled. One or more persistent Portal Brain phase files contained a historical selector representation such as:

```json
{
  "selectors": {
    "legacy_rule_name": "input[name='ruleName']"
  }
}
```

The current runtime expected:

```json
{
  "selectors": {
    "input[name='ruleName']": {
      "selector": "input[name='ruleName']",
      "dynamic": false,
      "validated_count": 1,
      "candidate_count": 0,
      "failure_count": 0
    }
  }
}
```

## Implementation

`hip_id_agent/portal_brain.py` now automatically migrates selector memory in all known historical forms:

- selector-keyed metadata dictionaries;
- dictionaries whose values are selector strings;
- lists of selector strings or records;
- one direct selector record;
- one plain selector string;
- malformed/non-numeric evidence counters.

The migration is applied while a phase is loaded, before ingestion, canonical/live merging, locator sorting, or deterministic-plan compilation. Existing learned memory is retained. Dynamic DDS selectors remain evidence and are not promoted as durable primary selectors.

## Verification

- Python compilation: passed
- Complete automated suite: **239 passed**
- Exact legacy string-selector regression: passed
- Selector-list migration regression: passed
- Direct selector-record and malformed-count regression: passed
- Unified KB import and canonical locator tests: passed

## Rerun

Use the same command again after replacing the package. Do not delete `data\\hip_memory\\portal_brain`; the patch is designed to preserve and migrate it.
