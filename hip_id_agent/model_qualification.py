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

The most accurate model is selected and locked (V243R36: on equal accuracy
the more capable model -- gpt-oss-120b over gpt-oss-20b -- and only between
equally capable models the faster one; before R36 latency broke every tie, so
gpt-oss-20b won whenever both answered the same).  Every
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
# V243R29: version 2 adds completion-judgment questions (does the live row hold
# exactly the expected record?) graded against the page.  A lock from an older
# version is re-validated once: every model is asked again and the champion is
# chosen anew; the old champion stays in use until then.
# V243R36: version 3 ranks a tie on accuracy by capability (not latency), asks a
# model that failed to answer (error, timeout, an answer that is not JSON) once
# more, and reads a reasoning model's answer when its final text came back empty.
# A lock from an older version is re-validated once.
QUALIFICATION_VERSION = 3
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
  // V243R29: the table itself (headers and each row's cells) for the judgment questions.
  const headers = Array.from(document.querySelectorAll('[role=columnheader],th')).filter(visible).map(h => norm(h.innerText).slice(0, 60)).slice(0, 20);
  const rows = [];
  for (const row of document.querySelectorAll('[role=row],tr')) {
    if (rows.length >= 12) break;
    if (!visible(row) || row.querySelector('[role=columnheader],th')) continue;
    const cells = Array.from(row.querySelectorAll('[role=cell],[role=gridcell],td')).map(c => norm(c.innerText).slice(0, 80));
    if (cells.filter(c => /[a-z0-9]/i.test(c)).length >= 3) rows.push({name: rowName(row), cells});
  }
  return {url: location.pathname, heading, controls, headers, rows};
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


def build_judge_questions(screen: Mapping[str, Any], *, max_questions: int = 4) -> List[Dict[str, Any]]:
    """Completion-judgment questions whose answer is read from the live table (V243R29).

    The agent's judges decide whether a filled form holds exactly what input.json
    asks for.  Each question gives an expected record for one live table row:
    half of them are exact copies of the row, the other half have one value
    changed to another row's value.  The right answer is known from the page:
    ``{"match": true}`` or ``{"match": false, "fields": [<the changed column>]}``.
    """
    headers = [str(h or "") for h in (screen.get("headers") or [])]
    rows = [r for r in (screen.get("rows") or []) if isinstance(r, Mapping) and len(r.get("cells") or []) == len(headers)]
    usable = []
    for row in rows:
        fields = [(h, str(v)) for h, v in zip(headers, row.get("cells") or []) if h.strip() and re.search(r"[a-z0-9]", str(v), re.I)]
        if len(fields) >= 3:
            usable.append((str(row.get("name") or fields[0][1]), fields))
    questions: List[Dict[str, Any]] = []
    for index, (name, fields) in enumerate(usable[: max(0, int(max_questions))]):
        record = dict(fields)
        changed = ""
        if index % 2 == 1:
            # Change one value (not the name column) to what another row holds there.
            rest = fields[1:]
            start = (index // 2) % len(rest)  # a different column each time
            for column, value in rest[start:] + rest[:start]:
                other = next((dict(f).get(column) for n, f in usable if n != name and dict(f).get(column) not in (None, value)), None)
                if other:
                    record[column] = other
                    changed = column
                    break
        questions.append({
            "id": f"j{len(questions) + 1}", "kind": "completion_judgment", "row": name, "record": record,
            "question": (f"input.json expects the object '{name}' to hold exactly the values in 'expected'. "
                         "Does the live table row hold exactly these values?"),
            "expected": {"match": not changed, "fields": [changed] if changed else []},
        })
    return questions


def qualification_questions(screen: Mapping[str, Any], *, max_questions: int = 8, judge_questions: int = 4) -> List[Dict[str, Any]]:
    """The navigation questions (R24) and the completion-judgment questions (R29)."""
    return build_questions(screen, max_questions=max_questions) + build_judge_questions(screen, max_questions=judge_questions)


def _prompt(screen: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    system = (
        "You operate a web portal for a user. You get the visible controls of the current page (id, role, "
        "accessible name, form label, placeholder, the table row the control belongs to, aria-expanded) and "
        "questions. Answer every question with the id of the one control to use. "
        "Questions j1, j2, ... are judgments: compare the 'expected' record with the live table row of the same "
        'object and answer {"match": true} or {"match": false, "fields": [<the column names that differ>]}. '
        'Return only JSON: {"answers": {"q1": <id>, "q2": <id>, ..., "j1": {"match": ..., "fields": [...]}}}.'
    )
    keys = ("id", "role", "name", "label", "placeholder", "row", "expanded")
    controls = [{k: c.get(k) for k in keys if c.get(k) not in (None, "", False)} for c in screen.get("controls") or []]
    payload: Dict[str, Any] = {
        "page": {"url": screen.get("url"), "heading": screen.get("heading")},
        "controls": controls,
        "questions": [{"id": q["id"], "question": q["question"], **({"expected": q["record"]} if q.get("record") else {})}
                      for q in questions],
    }
    if any(q.get("kind") == "completion_judgment" for q in questions):
        payload["table"] = {"headers": list(screen.get("headers") or []),
                            "rows": [{"object": r.get("name"), "cells": r.get("cells")} for r in screen.get("rows") or []]}
    task = json.dumps(payload, ensure_ascii=False)
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
        raw = (answers or {}).get(q["id"]) if isinstance(answers, Mapping) else None
        if q.get("kind") == "completion_judgment":
            verdict = _judgment(raw)
            want = q["expected"]
            named = {_snake(f) for f in verdict.get("fields") or []}
            ok = verdict.get("match") is want["match"] and (want["match"] or bool(named & {_snake(f) for f in want["fields"]}))
            per.append({"id": q["id"], "kind": q["kind"], "answer": verdict, "correct": bool(ok)})
            continue
        got = _answer_id(raw)
        per.append({"id": q["id"], "kind": q["kind"], "answer": got, "correct": got in set(q["expected"])})
    correct = sum(1 for p in per if p["correct"])
    judge = [p for p in per if p["kind"] == "completion_judgment"]
    return {"json_ok": json_ok, "correct": correct, "total": len(per), "accuracy": round(correct / max(1, len(per)), 4),
            "judge_correct": sum(1 for p in judge if p["correct"]), "judge_total": len(judge),
            "judge_accuracy": round(sum(1 for p in judge if p["correct"]) / len(judge), 4) if judge else None,
            "answers": per}


def _judgment(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        match = value.get("match", value.get("pass"))
        fields = value.get("fields") or value.get("mismatched_fields") or []
    else:
        match, fields = value, []
    text = str(match).strip().lower()
    verdict = True if match is True or text in {"true", "yes", "match"} else False if match is False or text in {"false", "no"} else None
    return {"match": verdict, "fields": [str(f) for f in fields] if isinstance(fields, (list, tuple)) else [str(fields)]}


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
    models: Optional[Sequence[str]] = None, judge_questions: int = 4, min_judge_accuracy: float = 0.5,
    retries: int = 1,
) -> Dict[str, Any]:
    """Give every available text model the same questions about this page and rank them."""
    questions = qualification_questions(screen, max_questions=max_questions, judge_questions=judge_questions)
    base = {"schema_version": SCHEMA, "qualification_version": QUALIFICATION_VERSION, "source": source, "qualified_at": utc_now(),
            "page": {"url": screen.get("url"), "heading": screen.get("heading"), "control_count": len(screen.get("controls") or []),
                     "table_rows": len(screen.get("rows") or [])},
            # Judgment questions are stored without their record's values.
            "questions": [{k: q[k] for k in ("id", "kind", "question", "expected") if k in q} | ({"row": q["row"]} if q.get("row") else {})
                          for q in questions],
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

    def ask(model: str) -> Dict[str, Any]:
        text = router.client.autogen_reply(system, task, model=model)
        row = score_answers(text, questions)
        if not row["json_ok"] and callable(getattr(router.client, "chat_rest", None)):
            # A reasoning model can return its answer only in the reasoning channel
            # (the final text empty); the REST reader takes it from there.
            try:
                rest = score_answers(router.client.chat_rest(
                    [{"role": "system", "content": system}, {"role": "user", "content": task}], model=model), questions)
                if rest["json_ok"]:
                    row = dict(rest, answer_read_from="rest_reasoning_fallback")
            except Exception:
                pass
        return row

    def run(model: str) -> Dict[str, Any]:
        started = time.perf_counter()
        failed: List[str] = []
        row: Dict[str, Any] = {}
        # A model that fails to answer (error, timeout, unreadable answer) is asked
        # once more: one transient failure must not decide the champion.
        for _ in range(1 + max(0, int(retries))):
            try:
                row = ask(model)
                row.update(model=model, ok=True)
                if row["json_ok"]:
                    break
                failed.append("answer was not JSON")
            except Exception as exc:
                row = {"model": model, "ok": False, "json_ok": False, "correct": 0, "total": len(questions), "accuracy": 0.0,
                       "error": mask_sensitive_string(str(exc))[:400]}
                failed.append(row["error"][:160])
        row["asked"] = len(failed) + (1 if row.get("json_ok") else 0)
        if failed:
            row["failed_answers"] = failed
        row["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        row["capability"] = float(router.capability(model))
        return row

    ranking: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(candidates), 6)), thread_name_prefix="hip-model-qualify") as pool:
        for fut in as_completed([pool.submit(run, m) for m in candidates]):
            ranking.append(fut.result())
    # Accuracy on the live page decides; on a tie the more capable model wins and
    # latency decides only between equally capable models.
    ranking.sort(key=lambda r: (-float(r["accuracy"]), -float(r.get("judge_accuracy") or 0.0), -int(bool(r.get("json_ok"))),
                                -float(r["capability"]), float(r["latency_ms"])))
    for row in ranking:
        # A champion must also judge a filled form correctly (it answers the judges).
        judge_ok = row.get("judge_accuracy") is None or float(row.get("judge_accuracy") or 0.0) >= float(min_judge_accuracy)
        row["qualified"] = bool(row.get("ok")) and float(row["accuracy"]) >= float(min_accuracy) and judge_ok
    winner = next((r for r in ranking if r["qualified"]), None)
    for row in ranking:
        row["why"] = _why(row, winner, min_accuracy=min_accuracy, min_judge_accuracy=min_judge_accuracy)
    result = {**base, "ranking": ranking, "min_accuracy": float(min_accuracy), "min_judge_accuracy": float(min_judge_accuracy),
              "candidate_models": list(candidates),
              "tie_rule": "equal accuracy -> the more capable model; equal capability -> the faster model"}
    if not winner:
        return {**result, "status": "no_model_met_threshold", "locked": False,
                "reason": (f"no model answered at least {min_accuracy:.0%} of the page questions correctly"
                           f" and at least {min_judge_accuracy:.0%} of the completion judgments")}
    return {**result, "status": "selected", "locked": True, "one_time": True, "selected_model": winner["model"],
            "accuracy": winner["accuracy"], "correct": winner["correct"], "total": winner["total"],
            "judge_accuracy": winner.get("judge_accuracy"), "judge_correct": winner.get("judge_correct"),
            "judge_total": winner.get("judge_total"),
            "selection_reason": winner["why"],
            "fallback_order": [r["model"] for r in ranking if r["qualified"] and r["model"] != winner["model"]]}


def _why(row: Mapping[str, Any], winner: Optional[Mapping[str, Any]], *, min_accuracy: float, min_judge_accuracy: float) -> str:
    """In plain words, why a model is (or is not) the champion."""
    score = f"{int(row.get('correct') or 0)}/{int(row.get('total') or 0)}"
    if not row.get("ok"):
        return f"did not answer ({str(row.get('error') or 'error')[:120]})"
    if not row.get("json_ok"):
        return "its answer could not be read (not JSON), also when asked again"
    if not row.get("qualified"):
        if float(row.get("accuracy") or 0.0) < float(min_accuracy):
            return f"answered {score} correctly, below the {min_accuracy:.0%} needed"
        return (f"answered {score} correctly but judged only {int(row.get('judge_correct') or 0)}/"
                f"{int(row.get('judge_total') or 0)} filled records right (at least {min_judge_accuracy:.0%} needed)")
    if winner is None:
        return f"answered {score} correctly"
    best = f"{int(winner.get('correct') or 0)}/{int(winner.get('total') or 0)}"
    if row.get("model") == winner.get("model"):
        return f"answered {score} questions about the live page correctly -- the most accurate (a tie goes to the more capable model)"
    if float(row.get("accuracy") or 0.0) < float(winner.get("accuracy") or 0.0) or \
            float(row.get("judge_accuracy") or 0.0) < float(winner.get("judge_accuracy") or 0.0):
        return f"answered {score} correctly; the champion answered {best}"
    if float(row.get("capability") or 0.0) < float(winner.get("capability") or 0.0):
        return f"same score ({score}); the champion is the more capable model"
    return f"same score and capability ({score}); the champion answered faster"


def revalidation_reason(selection: Mapping[str, Any], config: Any = None) -> str:
    """Why a locked selection must be re-validated now ("" when it is current)."""
    if not selection.get("locked"):
        return ""
    if int(selection.get("qualification_version") or 1) < QUALIFICATION_VERSION:
        return "qualification_version_upgrade"
    if selection.get("revalidation_due"):
        return str(selection.get("revalidation_due"))
    max_age = float(_qualification_cfg(config, "qualification_max_age_days", 30) or 0) if config is not None else 0.0
    if max_age > 0:
        try:
            from datetime import datetime, timezone

            then = datetime.fromisoformat(str(selection.get("qualified_at")).replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - then).total_seconds() > max_age * 86400:
                return f"older_than_{int(max_age)}_days"
        except Exception:
            pass
    return ""


def record_live_judge_truth(config: Any, judge_result: Mapping[str, Any], *, truth: bool, phase: str = "") -> Dict[str, Any]:
    """Score the champion's own judge verdict against the live form (V243R29).

    The live input.json proof is the truth.  When the champion keeps judging
    against it (at least ``qualification_revalidate_after_judge_errors`` of its
    last five verdicts wrong), the selection is marked for re-validation: the
    next certification or live mission asks every model again.
    """
    from .input_json_authority import model_judge_verdicts

    selection = load_selection(config)
    if not selection.get("locked"):
        return {"recorded": False, "reason": "no locked selection"}
    champion = str(selection.get("selected_model") or "")
    verdicts = [v for v in model_judge_verdicts(judge_result) if v["judge"] == "text" and (not v["model"] or v["model"] == champion)]
    if not verdicts:
        return {"recorded": False, "reason": "no text-judge verdict of the champion"}
    record = dict(selection.get("live_judge_record") or {})
    recent = list(record.get("recent") or [])
    for verdict in verdicts:
        right = verdict["pass"] == bool(truth)
        record["correct"] = int(record.get("correct") or 0) + (1 if right else 0)
        record["wrong"] = int(record.get("wrong") or 0) + (0 if right else 1)
        recent.append({"phase": phase, "right": right, "model_said_pass": verdict["pass"], "live_form_exact": bool(truth), "at": utc_now()})
    record["recent"] = recent[-10:]
    selection["live_judge_record"] = record
    limit = int(_qualification_cfg(config, "qualification_revalidate_after_judge_errors", 3) or 0)
    wrong_recent = sum(1 for r in record["recent"][-5:] if not r["right"])
    if limit > 0 and wrong_recent >= limit and not selection.get("revalidation_due"):
        selection["revalidation_due"] = f"champion_judged_against_the_live_form_{wrong_recent}_of_last_5"
    safe_write_json(selection_path(config), mask_sensitive_data(selection))
    return {"recorded": True, "champion": champion, "correct": record["correct"], "wrong": record["wrong"],
            "revalidation_due": selection.get("revalidation_due") or ""}


async def ensure_model_qualification(
    config: Any, page: Any, *, source: str, run_dir: Optional[Path] = None, force: bool = False,
    router: Any = None, wait_seconds: float = 15.0,
) -> Dict[str, Any]:
    """Qualify the models on this live page once; afterwards return the locked selection."""
    existing = load_selection(config)
    revalidate = revalidation_reason(existing, config)
    if existing.get("locked") and existing.get("selected_model") and not force and not revalidate:
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
        judge_questions=int(_qualification_cfg(config, "qualification_judge_questions", 4) or 0),
        min_judge_accuracy=float(_qualification_cfg(config, "qualification_min_judge_accuracy", 0.5) or 0.0),
        retries=int(_qualification_cfg(config, "qualification_retries", 1) or 0),
    )
    result["ran_now"] = True
    if revalidate or force:
        result["revalidation"] = {"reason": revalidate or "requested", "previous_model": existing.get("selected_model") or "",
                                  "previous_version": int(existing.get("qualification_version") or 1) if existing else None}
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
    if not result.get("locked") and existing.get("locked"):
        # A re-validation that could not finish never drops the model in use.
        return {**existing, "status": "kept_previous_selection", "locked": True, "ran_now": False,
                "requalify_error": f"{result.get('status')}: {result.get('reason') or ''}".strip(": ")[:500],
                "revalidation_pending": revalidate or "requested"}
    return masked


def qualification_summary(selection: Mapping[str, Any]) -> Dict[str, Any]:
    if not selection.get("locked"):
        return {"selected_model": "", "locked": False}
    return {k: selection.get(k) for k in (
        "selected_model", "locked", "qualified_at", "source", "accuracy", "correct", "total", "fallback_order",
        "judge_accuracy", "judge_correct", "judge_total", "revalidation_due", "selection_reason", "tie_rule")} | {
        "qualification_version": int(selection.get("qualification_version") or 1),
        "needs_revalidation": revalidation_reason(selection),
        "live_judge_record": {k: (selection.get("live_judge_record") or {}).get(k) for k in ("correct", "wrong")},
        "ranking": [{k: r.get(k) for k in ("model", "accuracy", "correct", "total", "judge_accuracy", "latency_ms", "qualified", "error", "capability", "why")}
                    for r in selection.get("ranking") or []]}
