# HIP Portal DOM Event and Mutation Learning Fix

## Finding

The previous stateful package captured:

- before/after form-control inventories;
- HTML/text snapshots;
- automation and user click logs;
- MCP snapshots;
- console and network evidence.

It did **not** continuously capture the browser event and mutation lifecycle that occurs between those snapshots. This left an evidence gap for Angular/DDS forms where selecting a parent causes controls to be inserted, removed, enabled, disabled, made required, or replaced.

## Implemented architecture

A browser-init observer is now installed in every page and SSO-created tab. It captures a bounded, redacted sequence of:

- `pointerdown` and `click`;
- `focusin` and `focusout`;
- `input` and `change`;
- `keydown` key identity;
- child controls added or removed through `MutationObserver`;
- changes to `aria-expanded`, `aria-hidden`, `aria-disabled`, `aria-required`;
- changes to `disabled`, `hidden`, `required`, `readonly`, `checked`, `selected`, `value`, `class`, and `style`.

Each record contains semantic evidence such as label, section, role, control type, row signature, visible state, required state, and a current-action selector. Password, token, secret, client-secret, OTP, and authorization values are never stored.

## Stateful graph integration

Before a graph node is executed, the runtime records an event/mutation cursor. After the action it:

1. waits for a committed control event or structural mutation;
2. recaptures the live form controls;
3. extracts added, removed, enabled, disabled, expanded, hidden, and required-state changes;
4. binds the transition to the exact graph node and `input.json` path;
5. attaches the transition evidence to newly learned dependency edges;
6. stores the transition as a candidate contract until the section judge passes.

A judge-approved branch can therefore retain not just:

`Interface=SFTP HAFT -> Account appears`

but also the observed transition contract:

- parent received input/change/focusout;
- child combobox was added;
- child became enabled and required;
- expected section and row were active;
- exact committed value was verified.

## Evidence outputs

Every run now writes:

- `dom_events/dom_events.json`
- `dom_events/dom_mutations.json`
- `dom_events/action_dom_transitions.json`
- `dom_events/<action_id>_transition.json`
- `dom_events/dom_event_summary.json`

Stateful execution attempts also include:

- `dom_transition`
- `dom_events`
- `dom_mutations`

Portal form knowledge includes `dom_event_contracts` linked to graph nodes and input paths.

## Replay behavior

Validated replay still uses semantic locators and exact input paths. DOM event contracts are used to wait for the expected state transition instead of relying only on fixed sleeps. If the expected transition does not occur, the agent recaptures DOM/MCP/network evidence, repairs the current section, and fails closed when the required child cannot be proven.

## Safety and limits

- Event and mutation logs are bounded to 6,000 records each by default.
- Secret-like inputs are redacted in the browser before they reach Python.
- Dynamic DDS IDs remain current-action evidence, never durable selectors.
- Event evidence supplements exact DOM verification; it cannot independently create a PASS.
- Save/Create/Submit/Delete/Deploy and other final mutations remain blocked.

## Local verification

- Python compilation: passed
- Full test suite: 283 passed
- DOM event observer tests: passed
- Mutation-to-child transition summarization tests: passed
- Secret redaction tests: passed
