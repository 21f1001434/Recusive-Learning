# AutoWebGLM Primary Framework Layer — v1.9.0

## Purpose

AutoWebGLM is now the primary browser-decision framework rather than a recovery-only advisor.
The official AutoWebGLM pattern is preserved: task + simplified HTML + viewport + action history -> one browser action.

## Runtime hierarchy

1. Expert-skill contract selects the exact HIP phase and vetted field intent.
2. AutoWebGLM observes the current Microsoft Edge page and proposes one action in the official action space.
3. Intent alignment requires the action kind, target element id and requested value to match the vetted HIP intent.
4. AgentQ/reward and mutation-governance gates remain active.
5. Existing deterministic HIP/DDS routines execute the approved action as verified tool adapters.
6. Exact DOM/DDS state is read twice before success is recorded.
7. Browser Use, LangChain, vision and PyAutoGUI expand recovery only after a proven interaction failure.

## Why deterministic tool adapters remain

AutoWebGLM is the policy/orchestration framework; deterministic scripts are tools.  This is intentional for enterprise form automation because a model should decide *what action to take* but should not be trusted to declare a DDS/Angular commit successful without exact portal-state evidence.

## Model-unavailable behavior

The same AutoWebGLM protocol remains primary.  If the native AutoWebGLM wrapper or Dell AIA planner is unavailable, the vetted skill intent is emitted as an AutoWebGLM function call and passed to the same verified tool adapter.  The runtime does not silently switch to a different orchestration framework.

## Security

Credentials/password/token-like values are not sent to the model.  For those controls the primary framework emits the vetted action locally in AutoWebGLM protocol form and lets the deterministic adapter enter the value.  Final mutations remain governance-controlled.
