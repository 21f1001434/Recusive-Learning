# V241 — Recursive Self-Improvement + On-Prem Multi-Model Portfolio

## Operating loop

```text
User request + current input.json
        ↓
Replay policy + induced skill lookup
        ↓
Model portfolio chooses champion/challengers
        ↓
Parallel model proposals (NO browser authority)
        ↓
Governed live semantic executor chooses/proves one action
        ↓
Exact effect verification + goal score
        ↓
Replay episode + model outcome reward + skill outcome
        ↓
Bounded recursive dream cycle
        ↓
Updated replay policy + model champions + skill confidence
        ↓
Next compatible run explores less and exploits the fastest proven route
```

## On-Prem model pool

Text/reasoning candidates: `gpt-oss-120b`, `gpt-oss-20b`, `mistral-small-3-1-24b-instruct-2503`, `llama-3-3-70b-instruct`, `gemma-3-27b-it`, `llama-3-2-3b-instruct`.

Vision candidates: `gemma-3-27b-it`, `pixtral-12b-2409`, `florence-2-large-ft`.

Image-embedding specialist: `nomic-embed-vision-v1-5` (786 dimensions). It is not treated as a chat model.

Cloud/GCP/Anthropic entries are not part of the allowed model catalog.

## Model reward

Immediate proposal score combines output-contract quality with historical prior. Downstream model reward is assigned only after the real portal task result is known. The downstream reward is derived from the V240 replay score: goal achievement, 100% runtime-input coverage, exact readback, workflow completion, mutation verification, repeatable-row correctness, efficiency and recovery cost.

A shadow challenger never performs a portal action. If it proposed the same safe choice as the successful champion it can receive a bounded shadow reward. Otherwise it receives only limited proposal-quality credit. This keeps benchmarking safe while still learning which model is useful.

## Fast exploitation

During early exploration the router can evaluate up to three models in parallel, with a hard cap of four. After a model has at least the configured minimum trials and a sufficiently high score, known tasks use only the champion. Drift/failure lowers the model score and reopens the challenger pool.

## Recursive self-improvement

The recursive loop is bounded by `max_recursive_cycles`. Each cycle can re-dream the replay policy and recalculate model champions. Skill confidence is already reinforced/demoted by exact task outcomes before the recursive cycle. The loop stops when the bounded depth is reached or no meaningful improvement/champion change remains.

Runtime source-code self-modification is explicitly disabled. Persistent memory stores no customer values, CSS selectors, XPath, screen/viewport coordinates or bounding boxes.

## Authority hierarchy

```text
Model portfolio proposes.
Replay policy proposes.
Skill memory proposes.
Current input.json supplies business values.
Live portal evidence authorizes controls.
Mutation gate authorizes writes.
Independent verification decides success.
```
