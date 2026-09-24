# Rules KB True Deep Learning Patch Acceptance Results

```text
132 passed
```

Regression coverage added:

- `/api/rule/summary` is not treated as a reusable deep profile.
- Real `/api/rule/{id}/details` payloads with conditions/actions are treated as deep profiles.
- Rules KB JavaScript no longer references the undefined `rule` global.
