# Changed Files

- `hip_id_agent/runtime_self_heal.py`
  - Added SSO-aware route-resume helper.
  - All-phase preflight now resumes after `sso_required`.
- `hip_id_agent/dummy_fill_e2e.py`
  - Moved agentic preflight inside the bounded phase exception/self-heal boundary.
  - Added precise preflight vs execution failure-stage logging.
- `tests/test_startup_sso_preflight_resume.py`
  - Added first-phase SSO resume and exception-boundary regressions.
- `CHANGELOG.md`
- `STARTUP_SSO_PREFLIGHT_SELF_HEAL_FIX_20260718.md`
- `STARTUP_SSO_PREFLIGHT_SELF_HEAL_RERUN_GUIDE_20260718.md`
