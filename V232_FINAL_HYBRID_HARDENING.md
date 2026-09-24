# V232 Final Hybrid Hardening

## Purpose

V232 is the final hardening layer on top of V231 all-phase autonomy. It is designed for the real HIP constraint that a single browser automation channel can be intermittently unavailable or unable to act on a Dell DDS/Angular control. The mission therefore reasons about the goal first and treats executors as replaceable workers, while live state verification remains authoritative.

## All-phase autonomous contract

The same goal runtime owns completion for Data Map, Source Document Type, Target Document Type, Rule, Source Transport Profile, Target Transport Profile and BizFlow. Phase-specific modules retain business semantics such as repeatable Rule conditions, Document Type parent/child dependencies, interface-dependent Transport Profile fields and BizFlow tab/routing structure; they are not allowed to bypass final autonomous verification.

The loop is: observe current HIP surface -> AutoWebGLM/semantic planning -> discover current controls -> map only trustworthy mission values -> act -> verify exact effect -> re-observe after DOM change -> self-heal/rebind -> continue until the phase target is proven, `NEEDS_INPUT` is justified, or bounded no-progress/wall-clock policy fails closed.

## Hybrid executor quorum

Normal runtime uses `config.yaml` and does not require every MCP to be present. PyAutoGUI MCP is preferred for trustworthy physical targets. Playwright MCP is an immediate deterministic alternative only after it proves that its active tab matches the authenticated Python Playwright HIP surface. Python Playwright remains the governed compatibility fallback where the action policy allows it. Chrome DevTools MCP and HIP Intelligence MCP are used as additional witnesses/intelligence when healthy.

Selecting `--require-mcp` or `config.mcp-required.windows.yaml` restores the strict three-MCP contract.

## Fail-closed completion

For strict live execution, the autonomous runtime requires explicit `exact_execution_verified=True` and `authoritative_execution_verified=True` from the execution stage. Missing keys are false, not true. A phase cannot pass because expected text happens to be visible, because an inner helper returned `pass=True`, or because navigation to the next route succeeded.

## Adaptive learning policy

Durable knowledge is semantic. Before replay artifacts are saved, V232 removes transient selectors, XPath, bounding boxes, screen coordinates, viewport ratios and equivalent current-generation locator material. The next run rediscovers the live control. Labels/roles/sections/dependencies and judge-verified semantic relationships may be retained.

## MCP same-tab quarantine

A running Playwright MCP process is not enough to authorize form actions. V232 compares the MCP current URL to the Python Playwright current HIP URL after navigation/SSO settling. If they disagree in adaptive mode, the MCP remains an optional degraded witness and is marked ineligible for form dispatch; PyAutoGUI/Python Playwright can continue. In strict mode the same disagreement is a blocker.

## Independent section execution

`run-section` now defaults to adaptive executor fallback and forwards `--allow-executor-fallback` to the full form engine. This makes isolated Data Map/Document Type/Rule/Transport Profile/BizFlow runs consistent with the Control Center and the complete seven-phase mission.

## Safety boundaries

Autonomy does not fabricate missing business data. A newly discovered required field without a trustworthy input value produces `NEEDS_INPUT`. Mutation outcome reconciliation and duplicate-dispatch quarantine remain in place for Save/Create/Submit/Deploy-type actions. Transient selectors/coordinates are not treated as learned truth.

## Validation

The final source collected 1,115 tests. Four non-overlapping batches passed: 279 + 279 + 279 + 278 = 1,115. Additional validation covers Python compilation, configuration parsing, CLI contracts, JavaScript syntax and wheel import. This validates the implementation architecture; real Dell HIP authenticated execution remains environment UAT and is intentionally not represented as having been executed from this build environment.

## Final packaged-copy smoke result

The clean ZIP was re-extracted before release. From that extracted copy, version/config/autonomous-phase assertions passed and the focused release regression set covering in-page contracts, Data Map autonomy, all-phase autonomy, V232 hybrid hardening and independent section execution passed **54/54**.
