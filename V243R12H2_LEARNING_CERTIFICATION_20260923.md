# V243R12H2 Learning-Certified Final

This build keeps the V243R12H1 hardened runtime and adds the final learning observability/config consistency patch.

## Learning contract

1. Every real fill/click/search/navigation action is captured as portal experience.
2. Successful semantically verified actions become candidate structural knowledge immediately.
3. Failed actions are retained as negative evidence and never enter a trusted deterministic route.
4. Trusted promotion requires exact phase verification + judge PASS + explicit human PASS when the strict gate is enabled.
5. Later tasks retrieve capability graph, Portal Brain, replay policy, induced skills and deterministic recipes before rediscovery.
6. Reused actions are rebound/re-proved on the current live page; customer values/selectors/coordinates are not reused as authority.
7. AutoWebGLM action-selection tournaments receive downstream browser reward; model champions/challengers can adapt from actual portal outcomes.

## Final H2 additions

- `learn_from_search: true` is now explicit in all distributed YAML templates.
- Continuous-learning receipts now report one of:
  - `trusted_promoted`
  - `candidate_learned`
  - `negative_evidence_learned`
  - `observed_unverified`
  - `no_actions`
  so operators can distinguish learning from deterministic promotion.
- Added a regression proving candidate learning stays candidate until human PASS, then becomes trusted promotion.

## Verification

- Test collection: 1,277 tests.
- Accounted result using order-stable shards: 1,276 passed, 1 skipped, 0 product failures.
- A bundled shard exposed an existing test-order environment pollution in `test_existing_aia_env_aliases.py`; that file passes 2/2 in isolation and the remaining shard passes 210/210. This is not a runtime learning failure.
- Learning-focused regression: 49 passed, 0 failed.
- Rebuilt wheel import/default smoke: PASS.

Live Dell AIA deployment reachability, HIP tenant state, SSO, and external MCP availability must still be checked on the deployment machine with Production Doctor.
