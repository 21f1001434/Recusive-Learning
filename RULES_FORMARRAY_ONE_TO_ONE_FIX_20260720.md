# Rules Angular FormArray one-to-one fix — 2026-07-20

## Live failure analyzed

The 05:40:50 live run reached Rule filling but ended with `HIP_PHASE_FORM_MODEL_NOT_ONE_TO_ONE`.
The form model showed both input condition nodes bound to the same physical row-0 controls. The previous row counter counted the DDS dropdown host and its nested combobox input as separate rows, so one visible Condition row was incorrectly reported as two.

## Fixes

- Conditions row count now uses direct children of Angular `formarrayname="conditions"`.
- Each row has a unique identity based on its real Condition Type control.
- Repeated Condition fields resolve only inside the requested physical row; no global-label fallback is allowed.
- Conditions `+` must create one distinct new FormArray row identity.
- Final verification compares all four live values in every row with `input.json`.
- Duplicate/reused physical rows fail closed.
- Disabled Rule Version is verification-only and cannot bind to Document Type Name (Version).
- DDS switch labels and names are captured through `aria-labelledby` and the `dds-switch` host, uniquely distinguishing Status from Execute Always.

## Expected U-HAUL result

Row 1: Attributes / Equals / uhaul / Receiver

Row 2: Attributes / Contains / DELL / Sender

## Validation

- Python compilation: passed
- Complete suite: 417/417 passed
- New one-row-reuse rejection regression: passed
- Version/status one-to-one binding regression: passed
