# v1.8.5 — Document Type stall, persistent learning, sibling memory and AgentQ reward

## Live defect addressed
A live Document Type run could remain active for hours even though the input data and form contracts were already known. The issue was not missing business data. The recovery policy allowed `until_complete` to bypass repeated-failure limits, while Document Type still performed structure/dropdown discovery before deterministic execution. A repeated control failure could therefore be rediscovered and retried without measurable progress.

## Runtime changes
- Document Type is now **deterministic-first** when the reviewed unified KB is available. Exhaustive pre-fill dropdown/structure discovery is not performed on the normal path.
- After a judge-approved Source Document Type, Target Document Type applies the same-family Flow Pattern Memory before deciding its execution profile. Target rebinds the validated structural interaction pattern to the current form and performs **no new discovery**.
- `until_complete` is now progress-driven, never unbounded. `max_no_progress_repeats` and `max_phase_wall_seconds` are mandatory anti-stagnation guards. A phase is also wrapped by an async wall-clock timeout so one inner call cannot monopolize Chrome indefinitely.
- Failure evidence includes a value-free semantic DOM probe plus screenshot from the **existing authenticated page**. This adopts the useful DOM+screenshot idea from the supplied L2 web-agent example without launching a second browser or persisting field values.

## Learning visibility
The Capability Graph is bootstrapped from `HIP_Unified_Deep_KB.json` so Capabilities and API Contracts are non-empty before the first successful live run. Entries are marked `canonical`/not-live-verified. Each attempted phase then records candidate form capabilities immediately, and observed network API contracts are written as candidate/live observations even when the phase later fails. Judge-approved success promotes trust to validated live knowledge.

## AgentQ reward
Trajectory memory already stored success/failure rewards and DPO-style preference pairs. v1.8.5 makes that reward operational in action selection:
- candidate recovery actions receive reward/success-rate UCB scoring;
- an action with at least three matching failures, zero successes and negative average reward is suppressed;
- remote MCP/AgentQ advice is advisory and cannot override the local reward/safe-action gate;
- a successful state transition receives higher reward than a non-progress transition.

## Safety
No customer field values are written to long-term capability/flow/trajectory memory. Final Save/Create/Delete/Deploy/Submit actions remain governed and are not unlocked by learning, Browser Use, MCP or PyAutoGUI.
