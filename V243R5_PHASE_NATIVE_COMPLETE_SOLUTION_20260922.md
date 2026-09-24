# HIP Portal V243R5 — Phase-Native Complete Solution

## Why V243R4 could still appear to learn Data Maps but not fill later phases
V243R4 contained dedicated deep learners and dedicated phase runtimes, but production execution still entered through the generic Universal Portal executor. Data Maps happened to benefit from strong listing/search/type discovery while complex Document Type, Rules, Transport Profile and BizFlow forms could reach the generic form compiler before their family-specific knowledge was made authoritative.

## V243R5 correction
Production now recognizes canonical HIP input and routes known HIP configuration work through `NativeHIPPhaseMissionCoordinator`.

1. Inspect the persistent capability graph.
2. Certify Data Maps, Document Types, Rules, Transport Profiles and BizFlow independently.
3. Deep-learn only the missing/uncertified families.
4. For complex Add/Create surfaces, learn the complete value-free form vocabulary: tabs/sections, visible controls, Angular/DDS semantic keys and dropdown option labels.
5. Execute through the native phase runtime:
   - `DataMapKBFlow`
   - `DocumentTypeKBFlow`
   - `RuleKBFlow`
   - `TransportProfileKBFlow`
   - `BizFlowKBFlow`
6. Keep one authenticated browser context through the all-phase mission.
7. Use exact readback + section judges + runtime self-heal until the current phase is complete.
8. Promote verified structure into Portal Brain/capability graph/replay memory.
9. On later runs, exploit the learned phase knowledge and re-prove the live portal rather than crawling everything again.
10. The universal agent remains the fallback for unknown/future portal capabilities or drift.

## Complete-form vocabulary learning
`phase_vocabulary_learning.py` inventories the currently open form before the deep fill. It is deliberately value-free and non-mutating. It learns:
- tabs and sections;
- labels, roles, input types and `formControlName` keys;
- visible custom DDS/Angular comboboxes;
- option labels exposed by each dropdown;
- disabled/readonly state;
- structural relationships needed for future binding.

It never persists customer field values and never clicks Save/Create/Submit/Deploy/Delete/Migrate during learning.

## Learn once, exploit later
Family certification is fail-closed. If a family is not operationally ready, V243R5 runs its deep learner. Once the family has verified replay profiles and capability evidence, later production runs skip the expensive family-wide deep crawl and use the dedicated live phase runtime with current-DOM verification.

## Production routing
For recognized HIP phase input without an authorized mutation, phase-native execution is authoritative. For explicitly governed mutations, phase-native qualification must first pass; only then can the governed mutation path proceed.

This prevents mutation attempts on an incompletely filled configuration.

## Verification
- 1,240 tests collected
- 1,239 passed
- 1 skipped
- 0 failed
- focused phase-native/deep-learning/production regression: 82 passed
