# V240 Replay Policy / Dreaming Engine

## Runtime loop

1. **Explore online** using the current safe policy and live portal evidence.
2. **Store a value-free replay episode** with goal score, input coverage, repeatable-row evidence, verification, recovery cost and value-free workflow structure.
3. **Dream offline** by replay-scoring multiple policy candidates against the accumulated history. No browser action is executed during dreaming.
4. **Promote the best policy** into the disk cache.
5. **Exploit when justified**: use validated induced skills or the best replay workflow as a fast path, re-proving every step live.
6. **Fall back to exploration on drift** and store the result, creating a continuously improving replay world.

## Score objective

The episode score rewards: requested-goal success, 100% input-owned exact readback, correct repeatable rows, completed user steps, verified mutations and lower recovery cost. Missing input leaves or row failures reduce the score and prevent a weak trajectory from becoming the preferred exploitation policy.

## Disk artifacts

Under `<memory_dir>/<portal_brain>/replay_policy/`:

- `replay_episodes.jsonl` — value-free historical episodes.
- `policy_cache.json` — current learned policies and fast workflows.
- `dream_cycles.jsonl` — offline policy-improvement rounds and candidate scores.
- `old_run_index.json` — imported-run index so old runs are not re-ingested repeatedly.

## Safety

Replay memory is advisory. It stores no customer values, selectors, XPath or coordinates. Current `input.json` supplies values, current portal evidence authorizes actions, and mutation authorization cannot be inherited from an old run or skill.
