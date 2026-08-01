# CUI // SP-CTI
"""FORGE Academy server-side grading â€” the single source of a step's verdict.

Before this module the browser decided whether it had passed. `api_step_submit`
read ``passed = bool(data.get("passed", True))`` â€” defaulting to True â€” and then
completed the step and paid XP with no check, so one crafted POST per mission
produced a certificate that ``/academy/verify/<token>`` publicly attested to. The
coding path was worse: the runner graded against the test body the *client*
posted back, and any script that exits 0 passed, so a step with no stored test
was cleared by ``print(1)``.

Everything authoritative now lives here:

  * ``grade_step`` takes a step id and a submission and returns the verdict. It
    never accepts a test â€” it loads the step's own ``test_code_path`` from the
    database. There is deliberately no ``test_code`` parameter to pass.
  * A ``coding`` step with no stored test is **ungraded**, not passed. It cannot
    be credited; reclassify it or author the test (aca-hon-04/05).
  * A ``reflect`` step is graded against the stored ``config_schema_json``. The
    answer key never reaches the browser â€” ``client_safe_steps`` strips it.
  * ``watch``/``configure``/``verify``/``deploy`` steps are acknowledgements, not
    assessments. They stay completable â€” reading is real work â€” but they report
    ``assessed: False`` so a certificate can tell demonstrated skill from pages
    turned (aca-int-07).
  * XP amounts come from ``fa_mission_steps.xp_partial`` and
    ``fa_missions.xp_reward``, never from the request.
  * ``mission_is_complete`` is derived from recorded step progress, not from the
    client's "this was the last step".

The sandbox in ``code_runner`` is unchanged: its AST allowlist, scrubbed env,
isolated interpreter and rlimits are penta-aca-02 work and remain the boundary
that actually contains learner code. Note the gate inspects the *combined*
learner + test script, so a stored test must satisfy the allowlist too.
"""

from __future__ import annotations

import json
import logging

from . import db as _fadb
from .code_runner import run_code
from .content_loader import load_test_code

_log = logging.getLogger(__name__)


def _conn():
    """Resolve the connection through the db module, not a bound import.

    Every other academy module reaches the database via
    ``apps.forge_academy.db.get_connection``; going through the module attribute
    (rather than importing the function) keeps that single seam intact so tests
    can substitute an in-memory schema.
    """
    return _fadb.get_connection()

# Step types for which the server can compute a real pass/fail. Everything else
# is an acknowledgement â€” completable, but never described as an assessment.
ASSESSED_STEP_TYPES = frozenset({"coding", "reflect"})

# Keys that must never be serialised into the page: the answer and anything that
# pre-empts the question.
_ANSWER_KEYS = frozenset({"correct", "is_correct", "answer", "explanation"})

# Step-level fields that must never reach the browser.
_SERVER_ONLY_STEP_KEYS = frozenset({"test_code", "test_code_path"})


# ---------------------------------------------------------------------------
# Step / mission lookup
# ---------------------------------------------------------------------------

def _load_step(step_id: int | str) -> dict | None:
    """Fetch a step row by id, or None. Never trusts anything but the id."""
    try:
        sid = int(step_id)
    except (TypeError, ValueError):
        return None
    row = _conn().execute(
        "SELECT id, mission_id, step_num, title, step_type, test_code_path, "
        "config_schema_json, xp_partial, skill_tag FROM fa_mission_steps WHERE id=%s",
        (sid,),
    ).fetchone()
    return dict(row) if row else None


def step_xp_base(step: dict) -> int:
    """The step's own XP value. The request does not get a say."""
    try:
        return int(step.get("xp_partial") or 0)
    except (TypeError, ValueError):
        return 0


def mission_xp_reward(mission_id: int) -> int:
    """The mission's own XP reward, read from the catalogue."""
    row = _conn().execute(
        "SELECT xp_reward FROM fa_missions WHERE id=%s", (mission_id,)
    ).fetchone()
    if not row:
        return 0
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError):
        return 0


def mission_is_complete(user_id: int, mission_id: int) -> bool:
    """True when every step of the mission has a completed progress row.

    Replaces the client's `mission_complete: isLast`. A mission with no steps is
    never complete â€” that would silently credit the 'Coming soon' missions.
    """
    conn = _conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM fa_mission_steps WHERE mission_id=%s", (mission_id,)
    ).fetchone()[0]
    if not total:
        return False
    done = conn.execute(
        "SELECT COUNT(*) FROM fa_step_progress sp "
        "JOIN fa_mission_steps s ON s.id=sp.step_id "
        "WHERE sp.user_id=%s AND s.mission_id=%s AND sp.status='completed'",
        (user_id, mission_id),
    ).fetchone()[0]
    return int(done) >= int(total)


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def _verdict(passed: bool, *, assessed: bool, reason: str = "", step: dict | None = None,
             **extra) -> dict:
    out = {
        "passed": bool(passed),
        "assessed": bool(assessed),
        "score": 100 if passed else 0,
        "reason": reason,
        "step": step,
        "xp_base": step_xp_base(step) if step else 0,
    }
    out.update(extra)
    return out


def _grade_coding(step: dict, submission: str) -> dict:
    """Run the learner's code against the step's OWN stored test."""
    path = (step.get("test_code_path") or "").strip()
    if not path:
        # Exit-code-zero is not evidence of anything. Refuse to credit.
        return _verdict(
            False, assessed=True, reason="ungraded_no_test", step=step,
            stdout="", stderr=(
                "This step has no verification test, so it cannot be graded. "
                "Nothing you submit will be credited until one is authored."
            ),
        )
    test_code = load_test_code(path)
    if not (test_code or "").strip():
        return _verdict(
            False, assessed=True, reason="ungraded_no_test", step=step,
            stdout="", stderr=(
                f"The verification test for this step ({path}) is missing or empty, "
                "so it cannot be graded."
            ),
        )
    result = run_code(submission or "", test_code=test_code)
    return _verdict(
        bool(result.get("passed")), assessed=True,
        reason="" if result.get("passed") else (result.get("error") or "test_failed"),
        step=step,
        stdout=result.get("stdout", ""),
        stderr=result.get("stderr", ""),
        exit_code=result.get("exit_code"),
    )


def _reflect_schema(step: dict) -> dict:
    raw = step.get("config_schema_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        _log.warning("step %s has unparseable config_schema_json", step.get("id"))
        return {}


def _grade_reflect(step: dict, chosen_option) -> dict:
    """Compare the chosen option index against the stored key."""
    schema = _reflect_schema(step)
    options = schema.get("options") or []
    if not schema.get("question") or not options:
        # A reflect step with no question is a prompt to think, not a test.
        return _verdict(True, assessed=False, reason="acknowledged", step=step)

    correct_idx = next(
        (i for i, o in enumerate(options) if isinstance(o, dict) and o.get("correct")),
        None,
    )
    explanation = schema.get("explanation") or ""
    if correct_idx is None:
        _log.warning("reflect step %s has options but no correct answer", step.get("id"))
        return _verdict(
            False, assessed=True, reason="misconfigured_no_key", step=step,
            explanation=explanation, correct_option=None,
        )

    try:
        chosen = int(chosen_option)
    except (TypeError, ValueError):
        return _verdict(
            False, assessed=True, reason="no_answer", step=step,
            explanation=explanation, correct_option=correct_idx,
        )

    passed = chosen == correct_idx
    return _verdict(
        passed, assessed=True, reason="" if passed else "wrong_answer", step=step,
        explanation=explanation, correct_option=correct_idx,
    )


def _grade_items(user_id: int, step: dict, answers: dict) -> dict:
    """Score a step against its item bank (aca-trn-01).

    Takes precedence over the step's declared type, because an item bank is what
    makes a step gradeable: it is how a `watch` lesson becomes assessable without
    re-labelling it as something it is not (aca-hon-04).
    """
    from .assessment import grade_attempt

    result = grade_attempt(user_id, step["id"], answers)
    if not result.get("ok"):
        return _verdict(
            False, assessed=result.get("assessed", True),
            reason=result.get("reason", "not_graded"), step=step,
            stderr=(
                "No questions were served for this attempt - reload the step and "
                "answer the questions shown."
                if result.get("reason") == "no_open_attempt" else ""
            ),
        )
    verdict = _verdict(
        result["passed"], assessed=True,
        reason=result.get("reason", ""), step=step,
        items=result.get("items", []),
        correct=result.get("correct"),
        total=result.get("total"),
        pass_threshold_pct=result.get("pass_threshold_pct"),
        attempts_used=result.get("attempts_used"),
        attempts_remaining=result.get("attempts_remaining"),
    )
    # The real percentage, not the 100/0 `_verdict` defaults to. This is the whole
    # point of the model: 2 of 3 correct is 67, and 67 is a fail at a 70 threshold.
    verdict["score"] = result["score"]
    return verdict


def grade_step(step_id: int | str, submission: str = "", *, chosen_option=None,
               answers: dict | None = None, user_id: int | None = None) -> dict:
    """Authoritative verdict for a submission.

    There is intentionally no ``test_code`` parameter â€” a caller cannot supply the
    test it is graded against. Returns a dict with at least ``passed``,
    ``assessed``, ``score``, ``reason``, ``xp_base`` and the ``step`` row.
    """
    step = _load_step(step_id)
    if step is None:
        return _verdict(False, assessed=False, reason="unknown_step")

    # aca-trn-01: a step with an active item bank is graded against it, whatever its
    # declared type. user_id is required for that path because the served draw and
    # its option permutation are per-learner, per-attempt state.
    if user_id is not None:
        from .assessment import has_item_bank

        if has_item_bank(step["id"]):
            return _grade_items(user_id, step, answers or {})

    step_type = (step.get("step_type") or "coding").strip().lower()
    if step_type == "coding":
        return _grade_coding(step, submission)
    if step_type == "reflect":
        return _grade_reflect(step, chosen_option)
    # watch / configure / verify / deploy / design â€” acknowledgements.
    return _verdict(True, assessed=False, reason="acknowledged", step=step)


def run_step_code(step_id: int | str, code: str) -> dict:
    """Advisory 'run my code' for the editor, using the step's own stored test.

    The result is the same computation ``grade_step`` performs, so the panel the
    learner sees cannot disagree with what gets recorded. When the step has no
    stored test the response says so instead of reporting a pass.
    """
    verdict = grade_step(step_id, code)
    return {
        "stdout": verdict.get("stdout", ""),
        "stderr": verdict.get("stderr", ""),
        "passed": verdict.get("passed", False),
        "graded": verdict.get("assessed", False),
        "reason": verdict.get("reason", ""),
        "exit_code": verdict.get("exit_code"),
    }


# ---------------------------------------------------------------------------
# Client payload sanitisation
# ---------------------------------------------------------------------------

def _safe_config_schema(schema):
    """Drop the answer key and anything that pre-empts the question."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in _ANSWER_KEYS:
            continue
        if key == "options" and isinstance(value, list):
            out[key] = [
                {k: v for k, v in opt.items() if k not in _ANSWER_KEYS}
                if isinstance(opt, dict) else opt
                for opt in value
            ]
        else:
            out[key] = value
    return out


def client_safe_steps(steps, user_id: int | None = None) -> list[dict]:
    """Project step rows down to what the browser may see.

    ``mission.html`` serialises the step list into JavaScript. Unfiltered that
    shipped both the grading test and the multiple-choice answer key to the
    person being graded.
    """
    safe = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        item = {k: v for k, v in step.items() if k not in _SERVER_ONLY_STEP_KEYS}
        if "config_schema" in item:
            item["config_schema"] = _safe_config_schema(item["config_schema"])
        # The runner only needs to know whether a Run button can grade anything.
        item["has_test"] = bool((step.get("test_code_path") or "").strip())

        # aca-trn-01: open (or resume) the learner's attempt and attach the items
        # drawn for it. Only option TEXT in the served permutation crosses the wire;
        # the mapping back to the authored order - and therefore the answer - lives
        # only in fa_step_attempts.served_json.
        if user_id is not None:
            try:
                from .assessment import classify_step, open_attempt, step_policy

                attempt = open_attempt(user_id, step.get("id"))
                if attempt:
                    item["assessment"] = attempt
                item["assessment_class"] = classify_step(step)
                item["attempt_policy"] = step_policy(step)["policy"]
            except Exception:
                # A broken bank must not take the mission page down. The step falls
                # back to its declared type, which grades honestly (an acknowledgement
                # says assessed: False) rather than pretending to have been assessed.
                _log.warning("assessment attach failed for step %s",
                             step.get("id"), exc_info=True)
        safe.append(item)
    return safe
