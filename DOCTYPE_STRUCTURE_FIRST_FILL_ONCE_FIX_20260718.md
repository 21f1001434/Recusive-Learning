# Document Type Structure-First, Fill-Once Runtime Fix

## Supplied run reviewed

Run: `UHAUL-POASN-FULL-DUMMY-20260718-015520`

The Source Document Type browser executor was not failing to fill the form. In each completed attempt, the deterministic target graph reported 29/29 successful exact steps. Version was correctly observed as the portal-generated disabled value `1.0`.

The repeat loop began after exact filling:

- Attempt 1 exact fill passed at `20:34:06 UTC`; post-fill exploration continued until `20:50:20 UTC` (about 16.2 minutes).
- Attempt 2 exact fill passed at `20:53:31 UTC`; post-fill exploration continued until `21:11:36 UTC` (about 18.1 minutes).
- Attempt 3 exact fill passed at `21:15:26 UTC` before the run was stopped.

The old exploration graph contained 18 parent records and 56 branch executions. Twelve parent records represented the same `Derived From` contract on different rows/selectors.

## Root causes

1. **Wrong lifecycle order**
   - Old: fill the exact input branch, explore many alternative branches, reopen the form, recreate rows, fill the exact branch again.
   - This allowed the learning agent to touch a form that was already complete.

2. **Document Identifier row identity bug**
   - Dell renders the section-level Operation control before the repeatable identifier data row.
   - The verifier counted Operation as row 0 and the actual Derived From/Value row as row 1.
   - It falsely reported two identifier rows and could not bind `document_identifier[0].derived_from`.

3. **Version was not contractually protected**
   - Version is portal-generated/read-only. It must never be typed into or used as an exploration parent.

4. **Duplicate exploration targets**
   - Dynamic DDS selectors and repeated row occurrences were treated as separate parent controls.

5. **Judge alias mismatch**
   - Model field names such as `document_identifier_operation` and `attributes_1_expression` did not resolve to deterministic names such as `document_identifier.operation` and `attributes[1].expression`.

6. **Destructive self-heal response**
   - A parser/judge disagreement after exact completion was classified as a form defect and reopened the entire phase.

## Corrected runtime lifecycle

```text
Open Create Document Type
→ create disposable repeatable rows for structure discovery
→ learn semantic controls, parent-child branches and option contracts
→ deduplicate schema-level parent controls
→ discard the learning surface
→ reopen one clean Create Document Type form
→ create exact rows from input.json
→ fill the deterministic input graph once
→ verify Version without typing
→ freeze the completed form
→ capture read-only evidence
→ deterministic judge
→ GPT-OSS-120B text judge
→ Gemma vision judge
→ continue to the next phase
```

No branch exploration or dropdown-catalogue click is allowed after exact target filling.

## Implemented protections

### Structure-first learning

- Exploration runs before business values are entered.
- Exploration is fail-open because the learning surface is disposable.
- A clean form is always rebuilt after learning.
- Validated Portal Brain knowledge can skip exhaustive branch replay while still recapturing the live structure.

### Exact fill once

- The deterministic graph is executed once on the clean form.
- One clean retry is allowed only when the deterministic fill itself fails.
- A text/vision/evidence disagreement cannot invoke that retry.

### Version protection

- `document_type_version` uses action `verify_only`.
- The executor never types into Version, even if Dell unexpectedly exposes it as enabled.
- Version is excluded from exploration parents and post-fill control interactions.

### Correct row semantics

- Operation is section-level and has no repeatable row index.
- Only containers with Derived From/Value controls count as Document Identifier rows.
- Raw Dell row index 1 is normalized to logical data row index 0.

### Parent-contract deduplication

For Document Type repeatable controls, learning identity is:

```text
semantic key + section + row kind + role
```

Dynamic selector spelling and row occurrence are evidence, not separate contracts.

### Read-only completed-phase rejudge

When exact Document Type execution has passed:

- Evidence may be rebuilt once without touching the browser.
- Judges may run again on the rebuilt evidence.
- If disagreement remains, the run stops fail-closed.
- The completed form is never reopened/refilled because of judge disagreement.

## Supplied-run replay with patched code

- Document Identifier row count: **1**
- Attribute row count: **5**
- Deterministic result: **PASS**
- Matched expected facts: **28**
- Missing values: **0**
- Row issues: **0**
- Exact target execution checkpoint: **PASS**

## Safety

The runtime continues to block final mutation actions, including Save, Create, Submit, Delete, Deploy and Publish.

## Verification

- Python compilation: passed
- New focused tests: 6 passed
- Relevant Document Type/exploration/judge tests: 62 passed
- Complete suite: 360/360 passed
- Supplied-run artifact replay: passed
- Authenticated live HIP rerun: not performed in this environment
