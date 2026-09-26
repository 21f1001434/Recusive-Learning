from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .aia_client import AIAClient, extract_json_object
from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.onprem-model-portfolio.v2"
TRIAL_SCHEMA = "hip.onprem-model-trial.v1"

ON_PREM_MODEL_CATALOG: Dict[str, Dict[str, Any]] = {
    "gpt-oss-20b": {"family": "reasoning", "modalities": ["text"], "context": 131000, "roles": ["reasoning", "planning", "action_selection", "judge", "recovery", "text"], "function_calling": True, "chat_completions": True, "rest_api": True},
    "gpt-oss-120b": {"family": "reasoning", "modalities": ["text"], "context": 131000, "roles": ["reasoning", "planning", "action_selection", "judge", "recovery", "text"], "function_calling": True, "chat_completions": True, "rest_api": True},
    "mistral-small-3-1-24b-instruct-2503": {"family": "text_to_text", "modalities": ["text"], "context": 128000, "roles": ["planning", "action_selection", "judge", "recovery", "text"], "function_calling": True, "chat_completions": True, "rest_api": True},
    "llama-3-2-3b-instruct": {"family": "text_to_text", "modalities": ["text"], "context": 128000, "roles": ["fast_classification", "action_selection", "text"], "function_calling": True, "chat_completions": True, "rest_api": True},
    "llama-3-3-70b-instruct": {"family": "text_to_text", "modalities": ["text"], "context": 65000, "roles": ["planning", "action_selection", "judge", "recovery", "text"], "function_calling": True, "chat_completions": True, "rest_api": True},
    "florence-2-large-ft": {"family": "multimodal", "modalities": ["image", "text"], "context": None, "roles": ["vision_grounding", "visual_recovery"], "function_calling": False, "chat_completions": False, "rest_api": True},
    "pixtral-12b-2409": {"family": "multimodal", "modalities": ["image", "text"], "context": 128000, "roles": ["vision_grounding", "visual_judge", "visual_recovery", "text"], "function_calling": False, "chat_completions": True, "rest_api": True},
    "gemma-3-27b-it": {"family": "multimodal_text_to_text", "modalities": ["image", "text"], "context": 128000, "roles": ["vision_grounding", "visual_judge", "visual_recovery", "planning", "judge", "text"], "function_calling": "non_standard", "chat_completions": True, "rest_api": True},
    "nomic-embed-vision-v1-5": {"family": "image_embedding", "modalities": ["image_embedding"], "dimensions": 786, "roles": ["visual_similarity", "golden_reference_similarity"], "function_calling": False, "chat_completions": False, "rest_api": True},
}

# V243R21: relative reasoning strength of each deployment.  A model's own
# "confidence" field is not comparable across models (gpt-oss-20b reports 0.98
# where gpt-oss-120b reports 0.72), so it never outranks a stronger model on its
# own; downstream task evidence still can.
MODEL_CAPABILITY: Dict[str, float] = {
    "gpt-oss-120b": 1.00,
    "llama-3-3-70b-instruct": 0.86,
    "mistral-small-3-1-24b-instruct-2503": 0.78,
    "gemma-3-27b-it": 0.76,
    "gpt-oss-20b": 0.72,
    "pixtral-12b-2409": 0.62,
    "florence-2-large-ft": 0.50,
    "llama-3-2-3b-instruct": 0.40,
    "nomic-embed-vision-v1-5": 0.0,
}

DEFAULT_TEXT_MODELS = ["gpt-oss-120b", "gpt-oss-20b", "mistral-small-3-1-24b-instruct-2503", "llama-3-3-70b-instruct", "gemma-3-27b-it", "llama-3-2-3b-instruct"]
DEFAULT_VISION_MODELS = ["gemma-3-27b-it", "pixtral-12b-2409", "florence-2-large-ft"]
DEFAULT_EMBEDDING_MODELS = ["nomic-embed-vision-v1-5"]


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(mask_sensitive_data(dict(row)), ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _bounded(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except Exception:
        return lo


def _task_key(role: str, task: str) -> str:
    compact = " ".join(str(task or "").lower().split())[:1500]
    digest = hashlib.sha256(compact.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{str(role or 'text').lower()}:{digest}"


class OnPremModelPortfolioRouter:
    """Champion/challenger routing across Dell AIA On-Prem deployments.

    Candidate models only propose text/JSON decisions. They never click the browser.
    Downstream task success is the authoritative reward.
    """

    def __init__(self, root: str | Path, config: Any, *, aia_config: Any, client: Optional[AIAClient] = None) -> None:
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.config = config; self.aia_config = aia_config; self.client = client or AIAClient(aia_config)
        self.state_path = self.root / "model_portfolio.json"
        self.trials_path = self.root / "model_trials.jsonl"
        self.dreams_path = self.root / "model_dream_cycles.jsonl"
        self.usage_path = self.root / "model_usage.jsonl"
        self.availability_path = self.root / "model_availability.json"
        self.state = self._load()

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    def _prefer_strongest(self) -> bool:
        return bool(self._cfg("prefer_strongest_model", True))

    @staticmethod
    def capability(model: str) -> float:
        return float(MODEL_CAPABILITY.get(str(model or ""), 0.5))

    def primary_text_model(self) -> str:
        """The operator's configured text model (``aia.model``), the floor for default calls."""
        explicit = str(self._cfg("primary_text_model", "") or "").strip()
        configured = explicit or str(getattr(self.aia_config, "model", "") or "").strip() or "gpt-oss-120b"
        return configured if configured in ON_PREM_MODEL_CATALOG else "gpt-oss-120b"

    def strongest_available(self, role: str = "", kind: str = "text") -> str:
        models = self.available_models(kind, role=role)
        if not models:
            return ""
        return max(models, key=lambda m: (self.capability(m), -models.index(m)))

    def _champion_key(self, score: float, model: str) -> float:
        weight = float(self._cfg("capability_weight", 0.30) or 0.0) if self._prefer_strongest() else 0.0
        return (1.0 - weight) * float(score) + weight * self.capability(model)

    def _load(self) -> Dict[str, Any]:
        raw = _read_json(self.state_path)
        if raw.get("schema_version") == SCHEMA:
            raw.setdefault("tasks", {})
            raw.setdefault("availability", {})
            return raw
        # Safe migration from V241: preserve evidence counters but discard proposal-only
        # champions because they were not guaranteed to have downstream success proof.
        if raw.get("schema_version") == "hip.onprem-model-portfolio.v1":
            return {"schema_version": SCHEMA, "models": dict(raw.get("models") or {}), "tasks": {}, "availability": {}, "role_champions": {}, "task_champions": {}, "cycle": int(raw.get("cycle") or 0), "updated_at": utc_now(), "on_prem_only": True, "values_stored": False, "selectors_stored": False, "coordinates_stored": False, "migrated_from": "hip.onprem-model-portfolio.v1"}
        return {"schema_version": SCHEMA, "models": {}, "tasks": {}, "availability": {}, "role_champions": {}, "task_champions": {}, "cycle": 0, "updated_at": utc_now(), "on_prem_only": True, "values_stored": False, "selectors_stored": False, "coordinates_stored": False}

    def _save(self) -> None:
        self.state["updated_at"] = utc_now(); safe_write_json(self.state_path, self.state)

    def configured_models(self, kind: str = "text", *, role: str = "") -> List[str]:
        field = {"text": "text_models", "vision": "vision_models", "embedding": "embedding_models"}.get(kind, "text_models")
        defaults = {"text": DEFAULT_TEXT_MODELS, "vision": DEFAULT_VISION_MODELS, "embedding": DEFAULT_EMBEDDING_MODELS}.get(kind, DEFAULT_TEXT_MODELS)
        raw = list(getattr(self.config, field, defaults) or defaults) if self.config is not None else list(defaults)
        wanted_role = str(role or "").strip().lower()
        out: List[str] = []
        for model in raw:
            name = str(model or "").strip(); meta = ON_PREM_MODEL_CATALOG.get(name)
            if not meta:
                continue
            if kind == "text" and "text" not in meta.get("modalities", []):
                continue
            if kind == "vision" and "image" not in meta.get("modalities", []):
                continue
            if kind == "embedding" and "image_embedding" not in meta.get("modalities", []):
                continue
            # Enforce the deployment capability advertised for the reasoning role.
            # This prevents a fast classifier from silently becoming a judge/planner.
            if wanted_role and wanted_role not in {str(x).lower() for x in meta.get("roles", [])}:
                continue
            if name not in out:
                out.append(name)
        return out

    def availability(self) -> Dict[str, Any]:
        return dict(self.state.get("availability") or {})

    def _availability_fresh(self, model: str) -> bool:
        row = (self.state.get("availability") or {}).get(model) or {}
        checked_epoch = float(row.get("checked_epoch") or 0.0)
        ttl = max(1, int(self._cfg("availability_probe_ttl_seconds", 1800) or 1800))
        return bool(checked_epoch and (time.time() - checked_epoch) < ttl)

    def probe_text_models(self, *, force: bool = False, models: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Probe configured Dell AIA text deployments once per TTL.

        A failed probe only removes that model from the current tournament when at
        least one other configured model proves available. This prevents a transient
        Dell AIA outage from erasing the configured catalog.
        """
        configured = [m for m in (models or self.configured_models("text")) if m in ON_PREM_MODEL_CATALOG]
        if not bool(self._cfg("availability_probe_enabled", True)):
            return {"status": "disabled", "configured": configured, "availability": self.availability()}
        pending = [m for m in configured if force or not self._availability_fresh(m)]
        if not pending:
            return {"status": "cached", "configured": configured, "availability": self.availability()}

        def probe(model: str) -> Dict[str, Any]:
            started = time.perf_counter()
            try:
                text = self.client.autogen_reply(
                    "Return only JSON with ok=true and the model name. This is a non-mutating Dell AIA availability probe.",
                    json.dumps({"probe": "hip_model_portfolio", "model": model}),
                    model=model,
                )
                elapsed = (time.perf_counter() - started) * 1000.0
                parsed = extract_json_object(text)
                ok = bool(str(text or "").strip())
                return {"model": model, "available": ok, "latency_ms": round(elapsed, 2), "checked_epoch": time.time(), "checked_at": utc_now(), "response_shape_ok": bool(parsed and set(parsed.keys()) != {"raw"})}
            except Exception as exc:
                return {"model": model, "available": False, "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "checked_epoch": time.time(), "checked_at": utc_now(), "error": mask_sensitive_string(str(exc))[:500]}

        workers = max(1, min(len(pending), int(self._cfg("availability_probe_parallelism", 4) or 4)))
        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hip-model-probe") as pool:
            for fut in as_completed([pool.submit(probe, model) for model in pending]):
                results.append(fut.result())
        availability = self.state.setdefault("availability", {})
        for row in results:
            availability[str(row.get("model") or "")] = row
        self._save()
        safe_write_json(self.availability_path, {"schema_version": "hip.onprem-model-availability.v1", "updated_at": utc_now(), "models": availability})
        return mask_sensitive_data({"status": "probed", "configured": configured, "results": results, "availability": availability})

    def available_models(self, kind: str = "text", *, role: str = "") -> List[str]:
        configured = self.configured_models(kind, role=role)
        if kind != "text":
            return configured
        availability = self.state.get("availability") or {}
        proven = [m for m in configured if bool((availability.get(m) or {}).get("available"))]
        fresh_failures = [m for m in configured if self._availability_fresh(m) and (availability.get(m) or {}).get("available") is False]
        if proven:
            return [m for m in configured if m in proven]
        # No model has proved available yet: retain unprobed/configured models so the
        # first real tournament can still work when probing is disabled/unavailable.
        return [m for m in configured if m not in fresh_failures] or configured

    def _usage(self, row: Mapping[str, Any]) -> None:
        if bool(self._cfg("record_usage_ledger", True)):
            _append_jsonl(self.usage_path, row)

    def role_roster(self) -> Dict[str, List[str]]:
        roles = ["planning", "action_selection", "judge", "recovery", "fast_classification"]
        return {role: self.available_models("text", role=role) for role in roles}

    def recent_usage(self, limit: int = 25) -> List[Dict[str, Any]]:
        if not self.usage_path.is_file():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with self.usage_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            rows.append(row)
                    except Exception:
                        continue
        except Exception:
            return []
        return rows[-max(1, int(limit or 25)):]

    def _model_stats(self, model: str, role: str) -> Dict[str, Any]:
        row = self.state.setdefault("models", {}).setdefault(model, {"roles": {}, "total_trials": 0, "updated_at": utc_now()})
        return row.setdefault("roles", {}).setdefault(str(role or "text"), {"trials": 0, "successes": 0, "reward_sum": 0.0, "latency_ms_sum": 0.0, "quality_sum": 0.0, "last_reward": 0.0, "last_used_at": "", "drift_failures": 0})

    def _task_stats(self, task_key: str, model: str) -> Dict[str, Any]:
        task = self.state.setdefault("tasks", {}).setdefault(task_key, {"models": {}, "updated_at": utc_now()})
        return task.setdefault("models", {}).setdefault(model, {"trials": 0, "successes": 0, "reward_sum": 0.0, "last_reward": 0.0, "last_used_at": "", "drift_failures": 0})

    @staticmethod
    def _prior_score(stats: Mapping[str, Any]) -> float:
        n = int(stats.get("trials") or 0); successes = int(stats.get("successes") or 0)
        beta_mean = (successes + 1.0) / (n + 2.0)
        reward = float(stats.get("reward_sum") or 0.0) / max(1, n)
        quality = float(stats.get("quality_sum") or 0.0) / max(1, n)
        latency = float(stats.get("latency_ms_sum") or 0.0) / max(1, n)
        latency_score = 1.0 / (1.0 + max(0.0, latency) / 12000.0)
        return _bounded(0.42 * beta_mean + 0.38 * reward + 0.12 * quality + 0.08 * latency_score)

    @staticmethod
    def _task_score(stats: Mapping[str, Any]) -> float:
        n = int(stats.get("trials") or 0); successes = int(stats.get("successes") or 0)
        beta_mean = (successes + 1.0) / (n + 2.0)
        reward = float(stats.get("reward_sum") or 0.0) / max(1, n)
        drift = int(stats.get("drift_failures") or 0)
        drift_penalty = min(0.35, 0.07 * drift)
        return _bounded(0.55 * beta_mean + 0.45 * reward - drift_penalty)

    def rank(self, role: str, *, kind: str = "text", task: str = "") -> List[Dict[str, Any]]:
        models = self.available_models(kind, role=role); rows = []
        task_key = _task_key(role, task) if task else ""
        for model in models:
            stats = self._model_stats(model, role)
            role_score = self._prior_score(stats)
            task_stats = self._task_stats(task_key, model) if task_key else {}
            task_trials = int(task_stats.get("trials") or 0) if task_stats else 0
            task_score = self._task_score(task_stats) if task_trials else role_score
            # Task evidence is stronger than generic role evidence once available.
            score = _bounded((0.68 * task_score + 0.32 * role_score) if task_trials else role_score)
            rows.append({"model": model, "score": score, "role_score": role_score, "task_score": task_score, "capability": self.capability(model), "trials": int(stats.get("trials") or 0), "task_trials": task_trials, "stats": dict(stats)})
        rows.sort(key=lambda r: (-float(r["score"]), -int(r["task_trials"]), -int(r["trials"]), models.index(r["model"])))
        return rows

    def select_candidates(self, role: str, *, task: str = "", kind: str = "text", count: Optional[int] = None, exploration: bool = False, learning: bool = False, complex_task: bool = False, force_multi_model: bool = False) -> List[str]:
        requested = int(count or self._cfg("parallel_models", 3) or 3)
        if learning and bool(self._cfg("force_multi_model_during_learning", True)):
            requested = max(requested, int(self._cfg("learning_parallel_models", 4) or 4), int(self._cfg("min_distinct_models_during_learning", 2) or 2))
            force_multi_model = True
        if complex_task and bool(self._cfg("force_multi_model_for_complex_tasks", True)):
            requested = max(requested, int(self._cfg("complex_task_parallel_models", 4) or 4))
            force_multi_model = True
        count = max(1, min(requested, int(self._cfg("max_parallel_models", 6) or 6)))
        ranked = self.rank(role, kind=kind, task=task)
        if not ranked:
            return []
        if (
            not exploration
            and not force_multi_model
            and not (learning and bool(self._cfg("disable_single_model_collapse_during_learning", True)))
            and bool(self._cfg("fast_exploitation_single_model", True))
            and int(ranked[0].get("trials") or 0) >= int(self._cfg("min_champion_trials", 3) or 3)
            and float(ranked[0].get("score") or 0.0) >= float(self._cfg("min_champion_score", 0.78) or 0.78)
        ):
            count = 1
        task_key = _task_key(role, task)
        task_champion = str((self.state.get("task_champions") or {}).get(task_key) or "")
        role_champion = str((self.state.get("role_champions") or {}).get(role) or "")
        ordered: List[str] = []
        valid = {x["model"] for x in ranked}
        if self._prefer_strongest():
            # V243R21: exploration fills the other slots with the least-tried
            # challengers, but never leaves out the strongest available model (or
            # the capability-aware champion proven by downstream evidence).
            anchor = next((c for c in (task_champion, role_champion) if c and c in valid), "") or self.strongest_available(role, kind=kind)
            if anchor and anchor in valid:
                ordered.append(anchor)
        if not exploration:
            for candidate in (task_champion, role_champion):
                if candidate and candidate in valid and candidate not in ordered:
                    ordered.append(candidate)
        source = sorted(ranked, key=lambda r: (int(r["task_trials"]), int(r["trials"]), -float(r["score"]))) if exploration else ranked
        for row in source:
            if row["model"] not in ordered:
                ordered.append(row["model"])
        return ordered[:count]

    def _response_quality(self, text: str, *, expected_json: bool, require_keys: Sequence[str]) -> tuple[float, Dict[str, Any]]:
        if not str(text or "").strip(): return 0.0, {}
        if not expected_json: return 0.72, {}
        parsed = extract_json_object(text)
        if set(parsed.keys()) == {"raw"}: return 0.15, parsed
        required = [str(x) for x in require_keys if str(x)]
        key_ratio = 1.0 if not required else sum(1 for key in required if key in parsed) / max(1, len(required))
        confidence = _bounded(parsed.get("confidence"), 0.0, 1.0) if "confidence" in parsed else 0.65
        return _bounded(0.60 + 0.25 * key_ratio + 0.15 * confidence), parsed

    def tournament_text(self, *, system: str, task: str, role: str = "planning", expected_json: bool = True, require_keys: Sequence[str] = (), parallel_models: Optional[int] = None, exploration: bool = False, learning: bool = False, complex_task: bool = False, force_multi_model: bool = False) -> Dict[str, Any]:
        if not bool(self._cfg("enabled", True)): return {"used": False, "reason": "model_portfolio_disabled"}
        if bool(self._cfg("availability_probe_on_task_start", True)):
            try:
                self.probe_text_models(force=False)
            except Exception:
                pass
        candidates = self.select_candidates(role, task=task, kind="text", count=parallel_models, exploration=exploration, learning=learning, complex_task=complex_task, force_multi_model=force_multi_model)
        if not candidates: return {"used": False, "reason": "no_on_prem_candidates"}
        trace_id = f"model-trace-{uuid.uuid4().hex[:12]}"; results: List[Dict[str, Any]] = []

        def invoke(model: str) -> Dict[str, Any]:
            started = time.perf_counter()
            try:
                text = self.client.autogen_reply(system, task, model=model)
                latency = (time.perf_counter() - started) * 1000.0
                quality, parsed = self._response_quality(text, expected_json=expected_json, require_keys=require_keys)
                prior = self._prior_score(self._model_stats(model, role))
                if self._prefer_strongest():
                    weight = float(self._cfg("capability_weight", 0.30) or 0.0)
                    immediate = _bounded((1.0 - weight) * (0.62 * quality + 0.38 * prior) + weight * self.capability(model))
                else:
                    immediate = _bounded(0.62 * quality + 0.38 * prior)
                proposal_keys = (
                    "candidate_index", "task_family", "confidence", "live_discovery_advisable",
                    "pass", "verdict", "needs_human", "evidence_conflict", "reason_codes",
                )
                proposal = {k: parsed.get(k) for k in proposal_keys if k in parsed}
                return {"model": model, "ok": True, "latency_ms": round(latency, 2), "quality": quality, "prior": prior, "immediate_score": immediate, "text": text, "parsed": parsed, "proposal": proposal}
            except Exception as exc:
                return {"model": model, "ok": False, "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "quality": 0.0, "prior": self._prior_score(self._model_stats(model, role)), "immediate_score": 0.0, "error": mask_sensitive_string(str(exc))[:800]}

        with ThreadPoolExecutor(max_workers=max(1, min(len(candidates), int(self._cfg("max_parallel_models", 4) or 4))), thread_name_prefix="hip-model-portfolio") as pool:
            for future in as_completed([pool.submit(invoke, model) for model in candidates]): results.append(future.result())
        results.sort(key=lambda r: (-float(r.get("immediate_score") or 0.0), float(r.get("latency_ms") or 1e12)))
        winner = next((x for x in results if x.get("ok")), None)
        audit = {"schema_version": TRIAL_SCHEMA, "trace_id": trace_id, "recorded_at": utc_now(), "role": role, "task_key": _task_key(role, task), "candidate_models": candidates, "winner_model": str((winner or {}).get("model") or ""), "results": [{k: v for k, v in x.items() if k not in {"text", "parsed"}} for x in results], "downstream_reward_pending": True, "on_prem_only": True, "learning": bool(learning), "complex_task": bool(complex_task), "force_multi_model": bool(force_multi_model), "distinct_models_used": len(candidates)}
        _append_jsonl(self.trials_path, audit)
        self._usage({"schema_version": "hip.model-usage.v1", "recorded_at": utc_now(), "trace_id": trace_id, "role": role, "candidate_models": candidates, "winner_model": str((winner or {}).get("model") or ""), "learning": bool(learning), "complex_task": bool(complex_task), "force_multi_model": bool(force_multi_model), "distinct_models_used": len(candidates)})
        # The proposal winner is deliberately NOT persisted as a champion here.
        # Durable promotion happens only after the real portal outcome is scored.
        candidate_votes = [
            {
                "model": str(x.get("model") or ""),
                "ok": bool(x.get("ok")),
                "quality": float(x.get("quality") or 0.0),
                "latency_ms": float(x.get("latency_ms") or 0.0),
                "parsed": dict(x.get("parsed") or {}) if isinstance(x.get("parsed"), dict) else {},
                "proposal": dict(x.get("proposal") or {}) if isinstance(x.get("proposal"), dict) else {},
            }
            for x in results
        ]
        return mask_sensitive_data({"used": bool(winner), "trace_id": trace_id, "role": role, "task_key": _task_key(role, task), "winner_model": str((winner or {}).get("model") or ""), "response": str((winner or {}).get("text") or ""), "parsed": dict((winner or {}).get("parsed") or {}), "candidate_results": [{k: v for k, v in x.items() if k not in {"text", "parsed"}} for x in results], "candidate_votes": candidate_votes, "candidate_models": candidates, "distinct_models_used": len(candidates), "learning": bool(learning), "complex_task": bool(complex_task), "force_multi_model": bool(force_multi_model), "on_prem_only": True, "downstream_reward_pending": True})

    def record_downstream_outcome(self, *, trace: Mapping[str, Any], success: bool, reward: float, role: Optional[str] = None, drift: bool = False) -> Dict[str, Any]:
        reward = _bounded(reward); role = str(role or trace.get("role") or "text"); winner = str(trace.get("winner_model") or "")
        task_key = str(trace.get("task_key") or "")
        candidates = list(trace.get("candidate_results") or [])
        winner_row = next((x for x in candidates if str(x.get("model") or "") == winner), {})
        for row in candidates:
            model = str(row.get("model") or "")
            if not model or model not in ON_PREM_MODEL_CATALOG:
                continue
            stats = self._model_stats(model, role); stats["trials"] = int(stats.get("trials") or 0) + 1
            if model == winner:
                model_reward = reward
            else:
                same_proposal = bool(row.get("proposal") and winner_row.get("proposal") and row.get("proposal") == winner_row.get("proposal"))
                base_shadow = reward if (success and same_proposal) else min(reward, _bounded(row.get("quality")))
                model_reward = base_shadow * float(self._cfg("shadow_reward_weight", 0.35) or 0.35)
            if success and model == winner:
                stats["successes"] = int(stats.get("successes") or 0) + 1
            stats["reward_sum"] = float(stats.get("reward_sum") or 0.0) + model_reward
            stats["quality_sum"] = float(stats.get("quality_sum") or 0.0) + _bounded(row.get("quality"))
            stats["latency_ms_sum"] = float(stats.get("latency_ms_sum") or 0.0) + max(0.0, float(row.get("latency_ms") or 0.0))
            stats["last_reward"] = model_reward; stats["last_used_at"] = utc_now()
            if drift and model == winner:
                stats["drift_failures"] = int(stats.get("drift_failures") or 0) + 1
            self.state.setdefault("models", {}).setdefault(model, {})["total_trials"] = int(self.state.get("models", {}).get(model, {}).get("total_trials") or 0) + 1

            if task_key:
                tstats = self._task_stats(task_key, model)
                tstats["trials"] = int(tstats.get("trials") or 0) + 1
                if success and model == winner:
                    tstats["successes"] = int(tstats.get("successes") or 0) + 1
                tstats["reward_sum"] = float(tstats.get("reward_sum") or 0.0) + model_reward
                tstats["last_reward"] = model_reward; tstats["last_used_at"] = utc_now()
                if drift and model == winner:
                    tstats["drift_failures"] = int(tstats.get("drift_failures") or 0) + 1
                self.state.setdefault("tasks", {}).setdefault(task_key, {})["updated_at"] = utc_now()

        # Only real downstream evidence may create/change durable champions.
        if task_key:
            task_models = (self.state.get("tasks", {}).get(task_key, {}).get("models") or {})
            ranked_task = sorted(
                ((m, self._task_score(st), int((st or {}).get("trials") or 0)) for m, st in task_models.items() if m in ON_PREM_MODEL_CATALOG),
                key=lambda x: (-self._champion_key(x[1], x[0]), -x[1], -x[2], x[0]),
            )
            if ranked_task:
                m, score, trials = ranked_task[0]
                if trials >= int(self._cfg("min_champion_trials", 3) or 3) and score >= float(self._cfg("min_champion_score", 0.78) or 0.78):
                    self.state.setdefault("task_champions", {})[task_key] = m
                elif task_key in self.state.setdefault("task_champions", {}):
                    self.state["task_champions"].pop(task_key, None)
        self.dream(reason="downstream_task_outcome")
        self._save()
        return self.manifest()

    def dream(self, *, reason: str = "scheduled") -> Dict[str, Any]:
        previous = dict(self.state.get("role_champions") or {}); roles = set(previous) | {"planning", "action_selection", "judge", "recovery"}
        for model in self.configured_models("text"):
            roles.update((self.state.get("models", {}).get(model, {}).get("roles") or {}).keys())
        changed: Dict[str, Dict[str, str]] = {}
        for role in sorted(roles):
            ranked = self.rank(role, kind="text")
            eligible = [row for row in ranked if int(row.get("trials") or 0) >= int(self._cfg("min_champion_trials", 3) or 3) and float(row.get("score") or 0.0) >= float(self._cfg("min_champion_score", 0.78) or 0.78)]
            old = str(previous.get(role) or "")
            if eligible:
                eligible.sort(key=lambda row: -self._champion_key(float(row.get("score") or 0.0), str(row.get("model") or "")))
                champion = eligible[0]["model"]
                self.state.setdefault("role_champions", {})[role] = champion
                if old != champion:
                    changed[role] = {"from": old, "to": champion}
            else:
                self.state.setdefault("role_champions", {}).pop(role, None)
                if old:
                    changed[role] = {"from": old, "to": ""}
        self.state["cycle"] = int(self.state.get("cycle") or 0) + 1; self._save()
        cycle = {"schema_version": "hip.model-portfolio-dream-cycle.v2", "cycle": self.state["cycle"], "recorded_at": utc_now(), "reason": reason, "champion_changes": changed, "role_champions": dict(self.state.get("role_champions") or {}), "promotion_requires_downstream_evidence": True}
        general = str((self.state.get("role_champions") or {}).get("planning") or (self.state.get("role_champions") or {}).get("action_selection") or "")
        primary = self.primary_text_model()
        if general and self._prefer_strongest() and self.capability(general) < self.capability(primary):
            # V243R21: a weaker champion never replaces the configured model for
            # every default call, unless the configured model is proven down.
            row = (self.state.get("availability") or {}).get(primary) or {}
            if not (self._availability_fresh(primary) and row.get("available") is False):
                general = ""
        if general:
            os.environ["HIP_MODEL_ROUTER_SELECTED_TEXT"] = general
        else:
            os.environ.pop("HIP_MODEL_ROUTER_SELECTED_TEXT", None)
        cycle["default_text_model"] = general or primary
        _append_jsonl(self.dreams_path, cycle)
        return mask_sensitive_data(cycle)

    def manifest(self) -> Dict[str, Any]:
        roles = sorted(set((self.state.get("role_champions") or {}).keys()) | {"planning", "action_selection", "judge", "recovery"})
        return mask_sensitive_data({"enabled": bool(self._cfg("enabled", True)), "on_prem_only": True, "text_models": self.configured_models("text"), "available_text_models": self.available_models("text"), "vision_models": self.configured_models("vision"), "embedding_models": self.configured_models("embedding"), "availability": self.availability(), "role_roster": self.role_roster(), "role_champions": dict(self.state.get("role_champions") or {}), "task_champions": dict(self.state.get("task_champions") or {}), "primary_text_model": self.primary_text_model(), "default_text_model": os.environ.get("HIP_MODEL_ROUTER_SELECTED_TEXT") or self.primary_text_model(), "prefer_strongest_model": self._prefer_strongest(), "model_capability": {m: self.capability(m) for m in self.configured_models("text")}, "cycle": int(self.state.get("cycle") or 0), "rankings": {role: self.rank(role, kind="text")[:5] for role in roles}, "role_eligible_models": {role: self.available_models("text", role=role) for role in roles}, "state_path": str(self.state_path), "trials_path": str(self.trials_path), "dreams_path": str(self.dreams_path), "usage_path": str(self.usage_path), "availability_path": str(self.availability_path), "recent_usage": self.recent_usage(25), "recent_distinct_models": sorted({m for row in self.recent_usage(25) for m in list(row.get("candidate_models") or [])}), "promotion_requires_downstream_evidence": True, "learning_forces_portfolio": bool(self._cfg("force_multi_model_during_learning", True)), "complex_tasks_force_portfolio": bool(self._cfg("force_multi_model_for_complex_tasks", True)), "values_stored": False, "selectors_stored": False, "coordinates_stored": False})


def model_portfolio_from_config(app_config: Any) -> OnPremModelPortfolioRouter:
    root = Path(app_config.reporting.memory_dir) / str(getattr(app_config.model_portfolio, "memory_subdir", "model_portfolio") or "model_portfolio")
    return OnPremModelPortfolioRouter(root, app_config.model_portfolio, aia_config=app_config.aia)
