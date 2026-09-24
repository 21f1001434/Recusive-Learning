# Document Type DDS exact-commit layer — v1.8.9

## Why this layer was necessary

The Edge/CDP hotfix made the authenticated browser session stable, but a separate form-control problem remained possible: HIP DDS often renders the dropdown listbox in a portal outside the `dds-dropdown` component. The old single-select routine could search visible options globally and primarily read the inner search textbox to decide whether a value was committed. That is unsafe on Document Type because:

1. two different dropdowns can expose the same visible option text;
2. the search textbox can be blank after a correct DDS selection;
3. input JSON uses stable enum values such as `ELEMENT_IN_PAYLOAD` while DDS displays `Element In Payload`;
4. pressing Enter without one exact owned candidate can commit the wrong option;
5. the state verifier previously compared the enum and human label literally and could reject a correct click, creating a retry loop.

## v1.8.9 behavior

For every Document Type single-select the runtime now:

1. resolves the exact live combobox;
2. follows `aria-controls` / `aria-owns` to the owned listbox, even when portalled elsewhere in the DOM;
3. normalizes stable enum/display punctuation for exact semantic comparison;
4. chooses one unique exact option inside that owned listbox;
5. clicks that option using a current-run id/token;
6. proves the commit from input value, explicit selected option, or selected chip/label;
7. blurs/settles the dropdown;
8. recaptures the entire Document Type form;
9. requires the value to remain exact across two stable reads;
10. records a `single_select_driver_audit` in the field transaction proof.

If two options are equally valid (for example two versions with the same base name), the driver fails closed. It does not use page-global text, arbitrary first-match selection, blind Enter, or direct JavaScript value assignment to pretend a DDS selection succeeded.

## Execution hierarchy

- deterministic Python Playwright + owned DDS listbox first;
- Playwright MCP only as a bounded interaction fallback;
- AgentQ/Browser Use/AutoWebGLM/Vision only after a proven deterministic failure;
- PyAutoGUI remains the final supervised physical fallback;
- exact DOM verification is mandatory regardless of executor.

## Live evidence to inspect

After running `Document Types only`, inspect:

- `source_document_type/doctype_kb/doctype_target_branch_execution.json`
- `source_document_type/doctype_kb/doctype_form_state_model.json`
- `target_document_type/doctype_kb/doctype_target_branch_execution.json`

For each `select_single` attempt, `transaction_proof.single_select_driver_audit` should show the owned `list_id`, selected candidate, executor stage, and final commit snapshot.
