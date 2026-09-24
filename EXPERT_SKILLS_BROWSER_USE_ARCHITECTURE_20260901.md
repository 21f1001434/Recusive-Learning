# HIP v1.8.4 — Expert Skills + Browser Use Architecture

## Operating model

```text
User description / input.json
        |
        v
Deterministic Description Trigger
        |
        v
Exact HIP phase skill selection
        |
        +--> Expert skill vetting
        |      - implementation exists
        |      - selected input preflight passes
        |      - verification contract exists
        |
        +--> Phase-local context budget
        |      - selected input branches only
        |      - bounded verified capabilities
        |      - broad context only on recovery
        |
        v
Deterministic phase-specific executor
        |
        +--> Playwright MCP
        +--> Python Playwright / DDS drivers
        +--> Browser Use compact recovery context
        +--> PyAutoGUI last resort on exact locator
        |
        v
Exact stable DOM verification + section judges
```

## Why Browser Use is useful here

The upstream Browser Use/WebUI pattern is valuable for reusing an authenticated browser, persistent sessions, state/history and browser-agent perception. HIP needs stronger deterministic and governance guarantees than a generic free-form web agent, so v1.8.4 uses Browser Use as a same-CDP perception/recovery layer rather than giving it independent mutation authority.

The bridge returns a bounded recovery payload with URL, title, tabs and a semantic actionable-element inventory. It never clicks or types. The HIP executor chooses the action and verifies it using deterministic logic.

## Description trigger examples

- `Run full end-to-end HIP configuration` → all seven phases.
- `Transport Profile only` → Source + Target Transport Profile.
- `Source Transport Profile only` → Source TP only.
- `Data Map and Rule only` → exact custom subset `data_map,rule`.
- `BizFlow only` → BizFlow only.

Descriptions that do not deterministically match a supported HIP section are rejected instead of guessed by an LLM.

## Context policy

Only selected input branches and a bounded set of verified capabilities are assembled into the normal planning context. The full portal state/KB is not injected into every step. Browser Use full-state capture remains available for evidence, while recovery uses the smaller semantic context by default.

## Safety

Browser Use and PyAutoGUI cannot replace an unvetted deterministic phase skill. PyAutoGUI does not discover targets and is disabled for final mutating clicks by default. Governed change execution remains separate from the no-save form-fill mission.
