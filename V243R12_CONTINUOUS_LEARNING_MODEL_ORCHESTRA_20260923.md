# V243R12 — Continuous Portal Learning + Dell On-Prem Model Orchestra

Date: 2026-09-23  
Compatibility package version: `2.4.3`

## Objective

V243R12 changes the HIP agent from a form-completion agent into a persistent portal-learning operator. Filling, clicking, searching and navigating are treated as experience. The live browser result remains authoritative; successful experience is compiled into value-free structural memory that later complex HIP tasks can exploit.

The second major change is model orchestration. Earlier releases contained a Dell On-Prem champion/challenger portfolio, but a hidden live AutoWebGLM action path could still call the default Dell AIA client directly, making the runtime appear to use only `gpt-oss-120b`. R12 routes live browser action proposals through the same outcome-driven portfolio during learning and complex tasks.

## Learn by doing

Each phase records the browser action range belonging to that phase. After exact browser verification, judge reconciliation and required human acceptance, the phase experience is promoted into:

- Capability Graph semantic controls/actions and `followed_by` relations;
- Portal Brain value-free state-transition memory;
- trajectory memory;
- Replay/Dreaming policy episodes;
- deterministic recipe / skill promotion through the existing supervised gates.

Failed actions remain negative/candidate evidence. They may help avoid a bad route later, but they do not become trusted deterministic truth.

Persistent experience deliberately excludes:

- customer values;
- passwords/tokens;
- CSS/XPath selectors as action authority;
- DDS transient ids/tokens;
- screen coordinates or bounding boxes.

The current `input.json` remains the business-value authority on every new run.

## Dell On-Prem model orchestra

Configured text deployments:

- `gpt-oss-120b`
- `gpt-oss-20b`
- `mistral-small-3-1-24b-instruct-2503`
- `llama-3-3-70b-instruct`
- `gemma-3-27b-it`
- `llama-3-2-3b-instruct`

Configured vision specialists:

- `gemma-3-27b-it`
- `pixtral-12b-2409`
- `florence-2-large-ft`

Visual embedding specialist:

- `nomic-embed-vision-v1-5`

Actual participation depends on what Dell AIA proves reachable in the current environment.

### Role-aware execution

- planning: multi-model tournament while learning or solving a complex/new task;
- action selection: AutoWebGLM live browser proposal uses the portfolio instead of a fixed default model;
- judge: multi-model panel resolves disagreement while exact browser proof remains authoritative;
- recovery: trace self-repair uses challenger models;
- vision: the existing multimodal preflight/vision judge uses proved Dell vision deployments;
- exploitation: after repeated downstream-proven success, the runtime may use the best champion for speed.

Learning mode cannot collapse to a single text champion when at least the configured minimum number of eligible models is available.

Defaults:

```yaml
model_portfolio:
  parallel_models: 3
  max_parallel_models: 6
  learning_parallel_models: 4
  complex_task_parallel_models: 4
  min_distinct_models_during_learning: 2
  force_multi_model_during_learning: true
  force_multi_model_for_complex_tasks: true
  disable_single_model_collapse_during_learning: true
  availability_probe_enabled: true
  availability_probe_on_task_start: true
```

## Runtime availability and usage evidence

The persistent model-portfolio directory now contains:

- `model_availability.json` — TTL-cached Dell AIA availability/latency evidence;
- `model_usage.jsonl` — which candidates actually participated per role/tournament;
- `model_trials.jsonl` — proposal audit without raw customer task values;
- `model_portfolio.json` — downstream reward/champion state.

The Control Center model panel can probe availability and show configured, available and recently used distinct models. Production Doctor adds a multi-model-learning readiness row.

This distinguishes three different facts that used to be easy to confuse:

1. model exists in configuration;
2. model is reachable in Dell AIA now;
3. model actually participated in the current/recent task.

## Complex task lifecycle

```text
User goal
  -> retrieve Portal Brain / Capability Graph / taught paths / recipes
  -> multi-model planning tournament for new/complex work
  -> deterministic semantic recipe when proven
  -> AutoWebGLM multi-model action selection when learning/ambiguous
  -> governed BrowserSession execution
  -> exact live effect proof
  -> continuous interaction learning
  -> judge + vision + model consensus
  -> human final supervision when configured
  -> downstream reward to model/skill/replay/RSI
  -> deterministic promotion after trusted success
```

A later request such as "find this Rule, open Edit, change one condition and verify it" can therefore reuse navigation and form knowledge learned while earlier create/fill missions were performed.

## RSI relationship

R12 does not treat model self-confidence as improvement. Recursive improvement is driven by downstream portal outcomes. Successful or failed cycles update:

- replay policy;
- task/role model scores;
- skill confidence;
- deterministic recipe evidence;
- recovery knowledge.

The persistent operator remains open-ended at the goal level (`max_goal_cycles: 0`). Source-code self-modification remains disabled.

## In-place upgrade

The R12 in-place package is designed for the existing project folder. It preserves:

- `.env`
- `config.yaml`
- `input.json`
- `runs/`
- `data/hip_memory/`
- `.backend_runtime/`
- `.hip_runtime/`

The updater backs up replaced code, copies the R12 source/WebUI/tests and force-reinstalls the exact bundled wheel. New R12 config fields use code defaults even when an older preserved `config.yaml` does not contain them.

## Verification

Source suite:

```text
1,272 collected
1,271 passed
1 skipped
0 failed
```

Focused R12 continuous-learning/multi-model tests: `5 passed`.

Static checks:

- Python `compileall`: PASS
- JavaScript syntax: PASS
- clean wheel install/import: PASS

## Live Dell boundary

Local/package certification cannot prove which Dell AIA deployments are enabled for the user's tenant at a particular moment. R12 therefore probes and records actual runtime availability instead of claiming that every configured model is always usable. Real Dell SSO, tenant-specific Angular/DDS behavior and authorized Save/Edit/Deploy/Migrate effects remain live-environment checks.
