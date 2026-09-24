# V242 / v2.4.2 — Final Release Closure

V242 closes the remaining verification gaps found during the clean-room audit of V241.

## Correctness fixes

- **Downstream-proven champion promotion**: a model that wins a proposal tournament is no longer persisted as a durable champion before the real browser outcome is known.
- **Role/task identity propagation**: tournament traces now carry `role` and `task_key` through execution so downstream reward is assigned to the correct planning/action-selection/judge/recovery bucket instead of falling back to generic `text`.
- **Task-specific evidence**: the portfolio keeps task-local trial/success/reward statistics and can promote a task champion only after sufficient downstream evidence.
- **Role-capability filtering**: models must advertise the requested role in the On-Prem catalog in addition to matching modality. A fast classifier therefore cannot silently enter a planning/judging tournament.
- **Exploration separation**: exploration prioritizes least-tried eligible challengers instead of forcing historical champions to the front.
- **Safe V241 migration**: V241 model evidence counters are preserved, but proposal-only champion labels are discarded because they were not guaranteed to have downstream success proof.
- **Recursive configuration enforcement**: replay-policy, model-portfolio and skill-update switches are honored. Skill outcomes already recorded by live execution are not double-counted during recursive improvement.
- **Release smoke closure**: the standalone wheel import path now verifies the actual MLflow, replay, skill, model-portfolio, recursive-improvement and universal-operator module paths.
- **Version fallback**: universal task MLflow metadata no longer contains a hardcoded release fallback; it uses the package `__version__` when package metadata is unavailable.

## Invariants retained

- Only approved On-Prem model names from the supplied Dell AIA reference are in the model portfolio catalog.
- Challenger models are proposal-only. Browser mutations are executed only by the single governed executor.
- Current `input.json` is the value authority.
- Current live page evidence is the action authority.
- Mutation actions require explicit mutation authorization.
- Replay/skill/model memory does not intentionally persist customer values, CSS selectors, XPath, coordinates or bounding boxes.
- Recursive improvement changes behavioral policy/model/skill state only; runtime source-code self-modification remains disabled.
- MLflow remains asynchronous/fail-open observability only.

## Local certification limitation

The included final-mission browser UAT uses the local mock HIP portal and explicitly reports `dell_environment_contacted=false`. It validates execution architecture and regressions, but it does not claim live Dell-tenant mutation coverage.
