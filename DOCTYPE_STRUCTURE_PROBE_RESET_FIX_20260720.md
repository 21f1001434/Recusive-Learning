# Document Type Structure-Probe Reset Fix — 20 July 2026

## Live failure reviewed

Run: `UHAUL-POASN-FULL-DUMMY-20260720-163756`

The Source Document Type phase successfully opened and learned the Create Document Type structure. It then failed at the clean-form rebuild step with:

```text
RuntimeError: Create Document Type form could not be rebuilt after structure learning
```

The captured reset evidence showed that the Create Document Type drawer was still active and valid, but the reset path navigated to the same Angular SPA URL before closing the drawer. The portal subsequently exposed a blank loading surface and the toolbar `+ Add` could not be resolved.

## Root cause

`_ensure_doctype_create_surface(..., force_reopen=True)` previously did this:

1. Confirmed the Create Document Type drawer was open.
2. Navigated to the Document Types URL.
3. Looked for the toolbar `+ Add`.

Navigating to the same route is not a reliable drawer-close operation in the HIP Angular application. The drawer can remain mounted or the SPA can enter a transient blank state. The subsequent toolbar lookup then fails because the listing surface was never proven restored.

## Corrected reset transaction

The structure-first lifecycle now performs an explicit safe discard transaction:

1. Confirm the active Create Document Type surface.
2. Settle/close any active DDS dropdown.
3. Rebind the Back/Close control inside the active Create Document Type drawer.
4. Click only that safe close control.
5. If an unsaved-change dialog appears, click only a safe `Discard`, `Leave`, `Continue without saving`, or `Yes` action.
6. Prove that the Create drawer is no longer active.
7. Prove that the Document Types listing toolbar `+ Add` is visible.
8. Reopen `+ Add` and recapture the clean Create surface.
9. Create the exact repeatable rows from `input.json` and perform the single authoritative fill.

Same-route navigation is allowed only after the active drawer has been proven closed. It is no longer used as the primary drawer-reset mechanism.

## Safety behavior

The reset never clicks:

- Save
- Create
- Submit
- Delete
- Remove
- Deploy
- Update
- Enable or Disable

The close transaction has a maximum of two attempts. The second attempt exists only for the known DDS behavior where an open dropdown consumes the first pointer interaction. The phase stops before entering customer values when the listing cannot be proven restored.

## Changed files

- `hip_id_agent/doctype_kb.py`
- `tests/test_doctype_structure_probe_reset_live_fix.py`
- `DOCTYPE_STRUCTURE_PROBE_RESET_FIX_20260720.md`
- `LIVE_TESTING_README_DOCTYPE_RESET_20260720.md`

## Verification

- Python compilation: passed
- New targeted reset tests: 3 passed
- Complete source-tree suite: 422/422 passed
- Clean extracted package suite: 422/422 passed
- Secret scan: passed
- ZIP integrity: passed
