"""One-time model qualification on a live portal task (V243R24).

The model that answers the agent's decisions used to be chosen by a static
capability table and by champion evidence collected during missions.  Now,
once, during the live GO/NO-GO run (or the first live mission page when that
never ran), every available Dell AIA text model is given the *same* task on
the real HIP page and scored on what it answered:

* the page is read (read-only): its visible controls, their accessible names,
  form labels and the table row each belongs to;
* questions whose right answer is known from that page are asked -- "the
  task is to migrate the document type <row>: which control do you click
  first?" (the row's expander), "which control finds an object by name?"
  (the table search), "which control opens the create form?" (+ Add),
  "which control receives input.json value 'transaction_type'?" ...;
* each model answers every question with a control id; answers are checked
  against the page, never against another model or a model's own confidence.

The most accurate model (latency breaks ties) is selected and locked.  Every
later call -- default calls, action selection, judges -- uses it, with the other
models that also passed as its fall-back order when it is down.  It runs again
only when asked (``--requalify-models`` / ``qualify-models --force``).
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .aia_client import extract_json_object
from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.model-qualification.v1"
SELECTION_FILE = "model_selection.json"
RUNS_FILE = "model_qualification_runs.jsonl"

SCREEN_JS = r"""
() => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const fieldTags = new Set(['input', 'select', 'textarea']);
  const labelFor = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return norm(l.innerText); }
    const by = el.getAttribute('aria-labelledby');
    if (by) { const l = document.getElementById(by.split(' ')[0]); if (l) return norm(l.innerText); }
    const wrap = el.closest('label'); if (wrap) return norm(wrap.innerText);
    const group = el.closest('.dds__form-group,.form-group,dds-dropdown');
    const l = group && group.querySelector('label,.dds__label');
    return l ? norm(l.innerText) : '';
  };
  const rowName = row => {
    // The row's name: its first cell with letters or digits (not the expander's icon).
    for (const c of row.querySelectorAll('[role=cell],[role=gridcell],td')) { const t = norm(c.innerText); if (/[a-z0-9]/i.test(t)) return t.slice(0, 120); }
    return '';
  };
  const heading = norm((document.querySelector('h1,h2,[role=heading]') || {}).innerText || document.title).slice(0, 120);
  const sel = 'button,a[href],input:not([type=hidden]),select,textarea,[role=button],[role=tab],[role=menuitem],[role=combobox],[role=checkbox],[role=switch],[role=link]';
  const controls = [];
  for (const el of document.querySelectorAll(sel)) {
    if (controls.length >= 160) break;
    if (!visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = String(el.getAttribute('type') || '').toLowerCase();
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'select' ? 'combobox' : tag === 'textarea' ? 'textbox'
      : tag === 'input' ? (type === 'checkbox' ? 'checkbox' : type === 'radio' ? 'radio' : type === 'search' ? 'searchbox' : 'textbox') : 'button');
    const field = fieldTags.has(tag);
    const row = el.closest('[role=row],tr');
    controls.push({
      id: controls.length, role,
      name: norm(el.getAttribute('aria-label') || (field ? '' : el.innerText) || el.getAttribute('title') || '').slice(0, 80),
      label: field ? labelFor(el).replace(/\s*\*\s*$/, '').slice(0, 80) : '',
      placeholder: norm(el.getAttribute('placeholder')).slice(0, 60),
      row: row ? rowName(row) : '',
      expanded: el.getAttribute('aria-expanded'),
      popup: !!el.getAttribute('aria-haspopup') || !!el.getAttribute('aria-controls'),
      expander: /expand/i.test(String(el.className || '') + ' ' + (el.getAttribute('aria-label') || '')),
    });
  }
  return {url: location.pathname, heading, controls};
}
"""

_MENU_NAME = re.compile(r"^(more actions?|actions|options|menu|⋮|…)$", re.I)
_SEARCH = re.compile(r"search|filter", re.I)
_ADD = re.compile(r"^\+?\s*(add|create|new)\b", re.I)
_NEXT = re.compile(r"^next\b|next page", re.I)
_VERBS = ("edit", "migrate", "clone")


def _snake(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_")


def build_questions(screen: Mapping[str, Any], *, max_questions: int = 8) -> List[Dict[str, Any]]:
    """Questions about this page whose right answers are read from the page itself."""
    controls = [c for c in (screen.get("controls") or []) if isinstance(c, Mapping)]
    questions: List[Dict[str, Any]] = []

    def add(kind: str, text: str, expected: Sequence[int]) -> None:
        ids = sorted({int(x) for x in expected})
        if ids and len(questions) < max_questions:
            questions.append({"id": f"q{len(questions) + 1}", "kind": kind, "question": text, "expected": ids})

    rows: List[str] = []
    for c in controls:
        name = str(c.get("row") or "")
        if name and name not in rows:
            rows.append(name)
    picks = [rows[i] for i in sorted({0, len(rows) // 2, len(rows) - 1})] if rows else []
    for verb, row in zip(_VERBS, picks[:3]):
        in_row = [c for c in controls if c.get("row") == row]
        expanders = [c["id"] for c in in_row if c.get("expander") or (c.get("expanded") is not None and not c.get("popup"))]
        direct = [c["id"] for c in in_row if str(c.get("name") or "").strip().lower() == verb]
        menus = [c["id"] for c in in_row if c.get("popup") or _MENU_NAME.match(str(c.get("name") or "").strip())]
        # The row's own button, else its expander (Document Types), else its menu.
        expected = direct or expanders or menus
        add("row_action_entry", f"The task is to {verb} the object named '{row}'. Which control do you click first to reach its {verb.title()} action?", expected)
    top = [c for c in controls if not c.get("row")]
    add("search", "Which control do you use to find an object by its name?",
        [c["id"] for c in top if c.get("role") in {"searchbox", "textbox"} and _SEARCH.search(" ".join(str(c.get(k) or "") for k in ("name", "label", "placeholder")))])
    add("create", "Which control opens the form to create a new object?",
        [c["id"] for c in top if c.get("role") in {"button", "link"} and _ADD.match(str(c.get("name") or "").strip())])
    add("next_page", "Which control shows the next page of results?",
        [c["id"] for c in top if c.get("role") in {"button", "link"} and _NEXT.search(str(c.get("name") or ""))])
    seen = set()
    for c in top:
        label = str(c.get("label") or "").strip()
        key = _snake(label)
        if not key or key in seen or c.get("role") not in {"textbox", "combobox", "checkbox", "switch"}:
            continue
        seen.add(key)
        add("field_mapping", f"Which control receives the input.json value '{key}'?",
            [x["id"] for x in top if _snake(str(x.get("label") or "")) == key])
        if len([q for q in questions if q["kind"] == "field_mapping"]) >= 2:
            break
    return questions


def _prompt(screen: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    system = (
        "You operate a web portal for a user. You get the visible controls of the current page (id, role, "
        "accessible name, form label, placeholder, the table row the control belongs to, aria-expanded) and "
        "questions. Answer every question with the id of the one control to use. "
        'Return only JSON: {"answers": {"q1": <id>, "q2": <id>, ...}}.'
    )
    keys = ("id", "role", "name", "label", "placeholder", "row", "expanded")
    controls = [{k: c.get(k) for k in keys if c.get(k) not in (None, "", False)} for c in screen.get("controls") or []]
    task = json.dumps({
        "page": {"url": screen.get("url"), "heading": screen.get("heading")},
        "controls": controls,
        "questions": [{"id": q["id"], "question": q["question"]} for q in questions],
    }, ensure_ascii=False)
    return system, task


def _answer_id(value: Any) -> Optional[int]:
    if isinstance(value, Mapping):
        value = value.get("id", value.get("control"))
    match = re.search(r"-?\d+", str(value if value is not None else ""))
    return int(match.group(0)) if match else None


def score_answers(text: str, questions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    parsed = extract_json_object(text or "")
    answers = parsed.get("answers") if isinstance(parsed.get("answers"), Mapping) else parsed
    json_ok = bool(parsed) and set(parsed.keys()) != {"raw"}
    per: List[Dict[str, Any]] = []
    for q in questions:
        got = _answer_id((answers or {}).get(q["id"])) if isinstance(answers, Mapping) else None
        per.append({"id": q["id"], "kind": q["kind"], "answer": got, "correct": got in set(q["expected"])})
    correct = sum(1 for p in per if p["correct"])
    return {"json_ok": json_ok, "correct": correct, "total": len(per), "accuracy": round(correct / max(1, len(per)), 4), "answers": per}


def selection_path(config: Any) -> Path:
    sub = str(getattr(config.model_portfolio, "memory_subdir", "model_portfolio") or "model_portfolio")
    return Path(config.reporting.memory_dir) / sub / SELECTION_FILE


def load_selection(config: Any) -> Dict[str, Any]:
    try:
        data = json.loads(selection_path(config).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) and data.get("schema_version") == SCHEMA else {}


def _qualification_cfg(config: Any, name: str, default: Any) -> Any:
    return getattr(getattr(config, "model_portfolio", None), name, default)


async def capture_screen(page: Any, *, wait_seconds: float = 15.0) -> Dict[str, Any]:
    """The page's visible controls; waits for a listing's rows or a form's fields to render."""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    screen: Dict[str, Any] = {}
    while True:
        try:
            screen = await page.evaluate(SCREEN_JS)
        except Exception:
            screen = {}
        controls = screen.get("controls") or []
        if any(c.get("row") for c in controls) or sum(1 for c in controls if c.get("label")) >= 2 or time.monotonic() >= deadline:
            return screen
        await page.wait_for_timeout(500)


def qualify_models(
    router: Any, screen: Mapping[str, Any], *, source: str, min_accuracy: float = 0.6, max_questions: int = 8,
    models: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Give every available text model the same questions about this page and rank them."""
    questions = build_questions(screen, max_questions=max_questions)
    base = {"schema_version": SCHEMA, "source": source, "qualified_at": utc_now(),
            "page": {"url": screen.get("url"), "heading": screen.get("heading"), "control_count": len(screen.get("controls") or [])},
            "questions": [{k: q[k] for k in ("id", "kind", "question", "expected")} for q in questions],
            "values_stored": False}
    if len(questions) < 3:
        return {**base, "status": "insufficient_page_evidence", "locked": False,
                "reason": f"only {len(questions)} verifiable questions on this page (3 needed); qualification waits for a richer page"}
    try:
        router.probe_text_models(force=False)
    except Exception:
        pass
    candidates = [m for m in (models or router.available_models("text")) if m]
    if not candidates:
        return {**base, "status": "no_models_available", "locked": False}
    system, task = _prompt(screen, questions)

    def run(model: str) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            text = router.client.autogen_reply(system, task, model=model)
            row = score_answers(text, questions)
            row.update(model=model, ok=True)
        except Exception as exc:
            row = {"model": model, "ok": False, "json_ok": False, "correct": 0, "total": len(questions), "accuracy": 0.0,
                   "error": mask_sensitive_string(str(exc))[:400]}
        row["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        row["capability"] = float(router.capability(model))
        return row

    ranking: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(candidates), 6)), thread_name_prefix="hip-model-qualify") as pool:
        for fut in as_completed([pool.submit(run, m) for m in candidates]):
            ranking.append(fut.result())
    ranking.sort(key=lambda r: (-float(r["accuracy"]), -int(bool(r.get("json_ok"))), float(r["latency_ms"]), -float(r["capability"])))
    for row in ranking:
        row["qualified"] = bool(row.get("ok")) and float(row["accuracy"]) >= float(min_accuracy)
    winner = next((r for r in ranking if r["qualified"]), None)
    result = {**base, "ranking": ranking, "min_accuracy": float(min_accuracy), "candidate_models": list(candidates)}
    if not winner:
        return {**result, "status": "no_model_met_threshold", "locked": False,
                "reason": f"no model answered at least {min_accuracy:.0%} of the page questions correctly"}
    return {**result, "status": "selected", "locked": True, "one_time": True, "selected_model": winner["model"],
            "accuracy": winner["accuracy"], "correct": winner["correct"], "total": winner["total"],
            "fallback_order": [r["model"] for r in ranking if r["qualified"] and r["model"] != winner["model"]]}


async def ensure_model_qualification(
    config: Any, page: Any, *, source: str, run_dir: Optional[Path] = None, force: bool = False,
    router: Any = None, wait_seconds: float = 15.0,
) -> Dict[str, Any]:
    """Qualify the models on this live page once; afterwards return the locked selection."""
    existing = load_selection(config)
    if existing.get("locked") and existing.get("selected_model") and not force:
        return {**existing, "status": "already_qualified", "ran_now": False}
    if not bool(_qualification_cfg(config, "qualification_enabled", True)):
        return {"schema_version": SCHEMA, "status": "disabled", "locked": False}
    if not bool(getattr(getattr(config, "aia", None), "enabled", False)) and router is None:
        return {"schema_version": SCHEMA, "status": "skipped_aia_disabled", "locked": False,
                "reason": "Dell AIA is not enabled; no model can be asked"}
    if router is None:
        from .model_portfolio import model_portfolio_from_config

        router = model_portfolio_from_config(config)
    screen = await capture_screen(page, wait_seconds=wait_seconds)
    result = qualify_models(
        router, screen, source=source,
        min_accuracy=float(_qualification_cfg(config, "qualification_min_accuracy", 0.6) or 0.6),
        max_questions=int(_qualification_cfg(config, "qualification_max_questions", 8) or 8),
    )
    result["ran_now"] = True
    masked = mask_sensitive_data(result)
    path = selection_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / RUNS_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(masked, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    if result.get("locked"):
        safe_write_json(path, masked)
        os.environ["HIP_MODEL_ROUTER_SELECTED_TEXT"] = str(result["selected_model"])
    if run_dir is not None:
        safe_write_json(Path(run_dir) / "model_qualification.json", masked)
        safe_write_json(Path(run_dir) / "model_qualification_screen.json", mask_sensitive_data(dict(screen)))
    return masked


def qualification_summary(selection: Mapping[str, Any]) -> Dict[str, Any]:
    if not selection.get("locked"):
        return {"selected_model": "", "locked": False}
    return {k: selection.get(k) for k in (
        "selected_model", "locked", "qualified_at", "source", "accuracy", "correct", "total", "fallback_order")} | {
        "ranking": [{k: r.get(k) for k in ("model", "accuracy", "correct", "total", "latency_ms", "qualified", "error")}
                    for r in selection.get("ranking") or []]}
