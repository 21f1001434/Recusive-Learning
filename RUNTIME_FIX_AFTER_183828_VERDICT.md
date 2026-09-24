# Runtime Fix After UHAUL-POASN-FULL-DUMMY-20260709-183828

## Evidence reviewed
The 183828 bundle shows the flow is now reaching much deeper surfaces:

- Data Map: pass
- Source Document Type: pass
- Target Document Type: pass
- Rule: pass with 22 field steps, confirming repeatable Rule conditions are now being filled
- Source Transport Profile: fields filled, screenshot exists in the phase folder, but summary/verifier did not attach screenshot evidence
- Target Transport Profile: fields filled, screenshot exists in the phase folder, but summary/verifier did not attach screenshot evidence
- BizFlow: reached nested Configure Routing rule drawer, but strict verification failed because the action-menu item `Create Biz Flow` was treated as unsafe and two fields failed

## Fixes implemented

### 1. BizFlow `Create Biz Flow` action-menu click is now safe
The template picker uses the exact menu item `Create Biz Flow` to launch the wizard. This is not final Save/Create/Submit. The unsafe-click detector now allows exact `Create Biz Flow` only when it is a menu item/action-menu launch.

### 2. BizFlow route document type now uses the input routing rule document type
Previously `route_document_type` defaulted to the target document type. The input.json routing rule requires:

`XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)`

The patch maps `configure_routing.rule.document_type_name_version` directly to `route_document_type`.

### 3. BizFlow aggregate operator is no longer filled as row operator
The field labelled `Flow Identifier Operator` had options like:

- all conditions are satisfied
- one or more conditions are satisfied

It is not the row operator field. The real row operator is labelled `Operator` and accepts values like `Equals` and `Contains`. The patch skips the aggregate field when the requested value is a row operator.

### 4. BizFlow post-dependency field fill added
The latest screenshot showed `Attribute Name/Unit` became visible only after `Condition Type = Attributes`. The control was not present in the original inventory, so it remained blank.

A live post-dependency scan now finds and fills newly visible fields such as:

- Attribute Name/Unit -> Receiver
- Target / route target -> target transport profile when revealed

### 5. Transport Profile screenshot recovery verified
With the patched verifier, the existing PNGs under the TP phase folders are found correctly and no longer cause a false TP failure.

## Validation

```text
pytest -q
197 passed in 16.51s
```

Offline verification against the 183828 bundle using the patched verifier:

```text
source_transport_profile: pass, screenshots=1
target_transport_profile: pass, screenshots=1
biz_flow: pass_with_warnings on old evidence only because old run still contains old failed attempts; next run should not record those two failed attempts.
```

## Changed files

- `hip_id_agent/dummy_fill_e2e.py`
- `hip_id_agent/bizflow_kb.py`
- `RUNTIME_FIX_AFTER_183828_VERDICT.md`
