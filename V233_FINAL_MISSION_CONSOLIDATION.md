# V233 Final Mission Consolidation

## Scope

V233 Final consolidates the staged website-understanding, live-observability, persistent semantic world-model, operational transition-planning, and hybrid browser-execution work into one mission-level runtime.

The authoritative mission sequence is:

1. Data Map
2. Source Document Type
3. Target Document Type
4. Rule
5. Source Transport Profile
6. Target Transport Profile
7. BizFlow
8. Lifecycle operations such as Edit, Save, Validate and Deploy when requested

## Runtime architecture

The live control loop is:

`mission/input -> live website understanding -> semantic target/transition planning -> PyAutoGUI MCP preferred -> Playwright MCP fallback when same-tab proof is valid -> Python Playwright governed fallback -> exact read-back/effect verification -> semantic world-model update -> re-observe -> continue`

Memory is a bounded prior only. Current foreground DOM/accessibility/vision evidence remains authoritative. The runtime does not persist brittle CSS/XPath selectors, generated DDS/Angular ids, screen coordinates, bounding boxes, or customer-entered values as portal knowledge.

## Final mission gate

`FinalMissionConsolidator` runs before application completion is reported. It fails closed unless every selected phase has current-run proof, the exact-state checkpoint passes, the independent section judge passes, persisted/runtime verification is present, assured-mode evidence passes when required, the terminal transition gate passes, and no unacknowledged transition remains.

## Dynamic dropdowns and Process Step

Dynamic choices are resolved from the currently mounted portal option list. Explicit mission/input values win. Verified semantic memory may help only if the remembered option still exists live. Ambiguous choices return `NEEDS_INPUT`; the agent does not guess.

## Transport Profile deployment-group policy

For SFTP-HAFT:

- Sender / Dell / Source: `da-sender-sftphaft-dce-shared`
- Partner / Receiver / Target: `pt-receiver-sftphaft-dce-shared`

These defaults are interface- and role-specific and do not silently override an explicit valid non-legacy value.

## Local final mission UAT

Run:

```powershell
python -m hip_id_agent.cli certify-final-mission --config config.yaml --output-dir C:\hip_final_uat
```

This is a mock HIP browser certification and does not contact Dell. It exercises all seven phases plus BizFlow Edit/Save/Validate/Deploy, dynamic option resolution, role-aware SFTP-HAFT deployment groups, PyAutoGUI-MCP point interaction, world-model learning, and the final mission consolidation gate.

## Dell live execution

The local UAT is not a substitute for Dell tenant UAT. Run the same final package against the authenticated HIP environment using the normal adaptive `config.yaml` first. The strict Windows MCP profile remains available for certification/debug scenarios that intentionally require all witness channels.
