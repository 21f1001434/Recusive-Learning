# V227 All-Sections In-Page Form Certification — 2026-09-09

## Corrected portal contract

All primary HIP create flows are treated as **same-page / in-page** experiences.

- Data Maps: listing -> top-right `+ Add` -> in-page Create Map form
- Document Types: listing -> top-right `+ Add` -> in-page Create Document Type form
- Rules: listing -> top-right `+ Add` -> in-page Create Rule form
- Transport Profiles: listing -> top-right `+ Add` -> in-page Create Transport Profile wizard/form
- BizFlow: listing -> top-right `+ Add` -> in-page template/card surface -> in-page Create Biz Flow multi-tab form

A query string or hash change is allowed because HIP may encode drawer/wizard state in the SPA URL. A host or pathname change is rejected as a wrong structural opener.

## Runtime hardening implemented

1. `phase_form_entry.py` now automatically enforces the in-page route contract for every primary create phase, even if a caller forgets to pass the flag.
2. Every primary KB/runtime caller explicitly passes `require_same_route=True`.
3. A shared top-right `+ Add` detector rejects nested row/menu/dialog/form-local Add controls and route-changing anchors.
4. Rules and Transport Profiles now use the same top-right Add ranking used by the hardened shared contract.
5. Document Type and BizFlow Add discovery are filtered through the shared same-page validator.
6. BizFlow direct template/card links are rejected when their `href` changes host/path.
7. BizFlow's second hop is route-verified too; a `/create` navigation is not accepted as a valid form even when the form DOM appears.
8. Wrong-route attempts are restored to the canonical listing and retried through the existing bounded ReAct/self-heal flow.

## Verification

Full local suite after the correction:

`1087 passed`

Focused all-phase/form-entry suite before final packaging:

`68 passed`

No Save/Create/Submit/Deploy mutation policy was changed by this patch.
