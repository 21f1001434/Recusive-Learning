# v1.8.6 AutoWebGLM + LangChain Browser Toolkit + Adaptive Context Architecture

## Goal

Improve recovery accuracy without making the HIP form executor less deterministic. Correct customer values still come only from the selected input contract. Browser intelligence can diagnose and recommend an interaction, but it cannot redefine the intended value, bypass phase scope, or bypass mutation governance.

## Context tiers

| Tier | Budget | Loaded when | Contents |
|---|---:|---|---|
| Phase-local planning | 64,000 chars | Normal execution | Selected input branch, selected phase skill, verified capabilities, compact recent trajectory |
| Recovery intelligence | 128,000 chars | Only after a real interaction failure | Browser Use semantic inventory, LangChain read-only toolkit output, AutoWebGLM compact observation/proposal, bounded failure evidence |
| Dell AIA self-heal advisor | 96,000 chars | Recovery decision | Stuck-state probe, browser-intelligence recovery bundle, reward history, recent action signatures |
| AutoWebGLM protocol prompt | up to 110,000 chars | AutoWebGLM recovery proposal | Task, value-free simplified HTML (up to 70k), viewport position, up to 40 previous operations, tabs |

Increasing context is deliberately not implemented as a full raw DOM dump on every action. That would add noise, expose more data than necessary, and make deterministic field filling less predictable. Broad context is activated only after the normal HIP skill fails to make verified progress.

## AutoWebGLM integration

The upstream AutoWebGLM research project represents web interaction as a compact observation/action loop. HIP reuses that protocol on the existing authenticated Chrome page:

1. current task description;
2. simplified, value-free interactive HTML;
3. current viewport/scroll position;
4. bounded previous action history;
5. one proposed browser action.

The original AutoWebGLM/ChatGLM3-6B checkpoint is **not bundled**. `autowebglm.native_model_command` can point to an enterprise-approved external wrapper that returns one AutoWebGLM-style action. By default, the already-configured Dell AIA model predicts the same action protocol so the package does not introduce another heavyweight model/runtime requirement.

AutoWebGLM is recovery-only. Its proposal must pass the local AgentQ reward/safety gate, deterministic phase scope, mutation policy, and exact post-action verification before it can influence execution. The parser does not use `eval`.

## LangChain Playwright Browser Toolkit integration

`langchain-community==0.4.2` is integrated lazily through `PlayWrightBrowserToolkit` and attached over CDP to the same Dell-SSO Chrome session. The bridge obtains the real toolkit but exposes only the configured read-only perception tools:

- `current_webpage`
- `extract_text`
- `get_elements`
- `extract_hyperlinks`

Navigation and click tools are intentionally filtered out because HIP already has deterministic Playwright/MCP form drivers and governed mutation handling. Every toolkit call is bounded by a timeout and fails open back to the existing recovery stack if LangChain is unavailable or incompatible.

## Combined execution ladder

```text
Description + input.json
        -> exact expert skill
        -> 64k phase-local context
        -> deterministic phase-specific form driver
        -> exact stable verification
        -> on genuine failure only:
             Browser Use semantic state
             + LangChain read-only page extraction
             + AutoWebGLM compact observation/proposal
             + AgentQ reward/history
             -> 128k bounded recovery bundle
             -> Dell AIA self-heal advisor (96k)
        -> safe recovery action
        -> Playwright/MCP
        -> PyAutoGUI only as last physical fallback
        -> exact double-read DOM verification
```

## Safety / anti-stall controls

- No second browser is launched by either bridge.
- Customer input values are excluded from AutoWebGLM simplified HTML.
- LangChain browser tools are read-only.
- AutoWebGLM state-changing advice is not executed directly.
- Save/Create/Delete/Deploy remain governed.
- AgentQ suppresses repeatedly negative no-progress actions.
- Recovery/tool calls have explicit timeouts.
- The existing per-phase wall-clock and repeated-state stall guards remain authoritative.
