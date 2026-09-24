from __future__ import annotations

"""Close the recursive self-improvement loop after a full HIP mission.

Portal tasks (task box / production E2E / persistent operator) already ran
recursive self-improvement after every execution; the full phase mission only
recorded a replay episode. This runs the same bounded RSI cycle for missions:
replay-policy dreaming, model-portfolio champion dreaming over the downstream
outcomes the mission recorded, and skill-library review. Policies/models/skills
improve; source code never does, and live-page proof stays authoritative.
"""

from typing import Any, Dict

from .model_portfolio import model_portfolio_from_config
from .recursive_self_improvement import recursive_improvement_from_config
from .security import mask_sensitive_data
from .skill_induction import skill_library_from_config


def close_mission_learning_loop(
    config: Any, *, replay_policy: Any, reward: float, success: bool,
    reason: str = "full_hip_mission_completed",
) -> Dict[str, Any]:
    rsi_cfg = getattr(config, "recursive_self_improvement", None)
    if rsi_cfg is not None and not bool(getattr(rsi_cfg, "enabled", True)):
        return {"status": "disabled"}
    portfolio = model_portfolio_from_config(config)
    skills = skill_library_from_config(config) if bool(getattr(config.skill_induction, "enabled", True)) else None
    engine = recursive_improvement_from_config(
        config, replay_policy=replay_policy, model_portfolio=portfolio, skill_library=skills,
    )
    # The mission records model outcomes as they happen (judge panel, AutoWebGLM
    # actions); RSI must not count them twice.
    result = engine.improve(
        run_reward=reward, success=success, reason=reason,
        skill_feedback={"outcome_already_recorded": True},
    )
    return mask_sensitive_data({
        "status": result.get("status"),
        "cycle_count": len(result.get("cycles") or []),
        "cycles": result.get("cycles") or [],
        "state": result.get("state") or {},
        "role_champions": dict(portfolio.state.get("role_champions") or {}),
        "skill_review": result.get("skill_review") or {},
        "state_path": str(engine.state_path),
        "code_self_modification": False,
        "live_page_still_authoritative": True,
    })
