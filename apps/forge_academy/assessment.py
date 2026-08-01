# CUI // SP-CTI
"""FORGE Academy assessment model — thresholds, attempt policy, item selection.

The INT epic made grading server-authoritative but there was nothing to grade. All
32 reflect steps in ``content_loader.BUILTIN_STEPS`` are free text and none carries a
``question``/``options``/``correct`` key, so ``grading._grade_reflect`` short-circuited
to ``assessed=False, passed=True`` for every one of them; a step was 100 or 0; attempts
were unbounded and uncounted; and the ``assessment_score_min`` declared in
``CERT_TIERS`` was never read by ``check_cert_eligibility``.

This module is the model. Spec:
``docs/features/forge-academy-aca-trn-01-assessment-model.md``.

Three things live here and nowhere else:

  * **Classification** — ``classify_step`` decides whether a step is ``graded``
    (a real score can be computed), ``ungraded`` (an assessed type with nothing to
    grade against) or ``acknowledged`` (real work, but not evidence of a skill).
    Only ``graded`` steps count toward a certificate.
  * **Attempt policy** — ``practice`` is unlimited, ``summative`` is capped. The cap
    is checked BEFORE grading, so a learner cannot burn attempts to enumerate the
    bank. Exhaustion is reversible via ``reset_attempts``, which appends a
    compensating row rather than deleting the attempts it forgives.
  * **Item selection** — ``open_attempt`` draws from the bank, shuffles the draw AND
    each item's options, and records the permutation server-side.
    ``grade_attempt`` maps the client's displayed indices back through it.

The answer key never leaves the server. The browser holds option *text* in a
per-attempt permutation whose mapping exists only in ``fa_step_attempts.served_json``,
so the indices it posts are meaningless without that row — which is what makes
reading the DOM worthless. Randomness is ``secrets.SystemRandom``, not a PRNG seeded
on ``(user_id, step_id, attempt_num)``: the learner knows all three of those.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from secrets import SystemRandom

from . import db as _fadb
from .constants import (
    ASSESSMENT_ITEMS_PER_ATTEMPT,
    ASSESSMENT_MIN_BANK_SIZE,
    ATTEMPT_POLICIES,
    ATTEMPT_POLICY_PRACTICE,
    ATTEMPT_POLICY_SUMMATIVE,
    CERT_ASSESSMENT_THRESHOLD_PCT,
    CODING_PASS_THRESHOLD_PCT,
    MISSION_ASSESSMENT_ATTESTED,
    MISSION_ASSESSMENT_DEMONSTRATED,
    MISSION_ASSESSMENT_INCOMPLETE,
    MISSION_ASSESSMENT_PARTIAL,
    MISSION_ASSESSMENT_THRESHOLD_PCT,
    STEP_CLASS_ACKNOWLEDGED,
    STEP_CLASS_GRADED,
    STEP_CLASS_UNGRADED,
    STEP_PASS_THRESHOLD_PCT,
    STEP_STATUS_COMPLETED,
    SUMMATIVE_MAX_ATTEMPTS,
)

_log = logging.getLogger(__name__)
_rand = SystemRandom()


def _conn():
    """Resolve through the db module, not a bound import.

    Same seam ``grading._conn`` uses: going through the module attribute rather than
    importing the function keeps a single place for tests to substitute an in-memory
    schema.
    """
    return _fadb.get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(row):
    return dict(row) if row is not None and hasattr(row, "keys") else row


# ---------------------------------------------------------------------------
# Item bank
# ---------------------------------------------------------------------------

def get_item_bank(step_id: int, *, active_only: bool = True) -> list[dict]:
    """Every authored item for a step, in authored order.

    Includes ``correct_index``. This is a SERVER-ONLY view — never hand the result
    to a template or a JSON response. ``_client_item`` is the projection that may
    cross the wire.
    """
    try:
        sid = int(step_id)
    except (TypeError, ValueError):
        return []
    sql = (
        "SELECT id, step_id, item_key, prompt, options_json, correct_index, "
        "explanation, difficulty, is_active FROM fa_assessment_items WHERE step_id=%s"
    )
    if active_only:
        sql += " AND is_active=1"
    sql += " ORDER BY id"
    try:
        rows = _conn().execute(sql, (sid,)).fetchall()
    except Exception:
        # A missing table must not take the mission page down with it. Reported
        # honestly as "no bank", which classifies the step as acknowledged rather
        # than pretending it was assessed.
        _log.warning("item bank unavailable for step %s", step_id, exc_info=True)
        return []

    out = []
    for row in rows:
        item = _as_dict(row)
        raw = item.get("options_json") or "[]"
        try:
            options = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            _log.warning("item %s has unparseable options_json", item.get("item_key"))
            continue
        if not isinstance(options, list) or not options:
            continue
        item["options"] = [str(o) for o in options]
        out.append(item)
    return out


def has_item_bank(step_id: int) -> bool:
    return bool(get_item_bank(step_id))


def validate_item_bank(items: list[dict]) -> list[str]:
    """Authoring-time problems with a bank, as human-readable strings.

    Called by the seeder so a malformed bank is refused at seed time rather than
    discovered by a learner mid-assessment. An empty return means the bank is sound.
    """
    problems: list[str] = []
    if len(items) < ASSESSMENT_MIN_BANK_SIZE:
        # A bank no larger than the draw serves the same items every attempt, which
        # makes the randomisation decorative and the memorisation defence a lie.
        problems.append(
            f"bank has {len(items)} items, need >= {ASSESSMENT_MIN_BANK_SIZE} "
            f"(draw is {ASSESSMENT_ITEMS_PER_ATTEMPT})"
        )
    seen = set()
    for item in items:
        key = str(item.get("item_key") or "")
        if not key:
            problems.append("an item has no item_key")
        elif key in seen:
            problems.append(f"duplicate item_key {key!r}")
        seen.add(key)
        options = item.get("options") or []
        if len(options) < 2:
            problems.append(f"item {key!r} has fewer than 2 options")
        idx = item.get("correct_index")
        if not isinstance(idx, int) or not (0 <= idx < len(options)):
            problems.append(f"item {key!r} correct_index {idx!r} is out of range")
        if not str(item.get("prompt") or "").strip():
            problems.append(f"item {key!r} has no prompt")
    return problems


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_step(step: dict) -> str:
    """``graded`` / ``ungraded`` / ``acknowledged`` for a step row.

    An item bank promotes a step to ``graded`` REGARDLESS of its declared type. That
    is deliberate: it lets a `watch` lesson become assessable by authoring items
    against it, without rewriting the catalogue's step types — aca-hon-04 established
    that a step type must describe what the step actually is, and re-labelling a
    lesson 'reflect' to make it gradeable would walk straight back into that.
    """
    if not isinstance(step, dict):
        return STEP_CLASS_ACKNOWLEDGED
    step_id = step.get("id")
    if step_id is not None and has_item_bank(step_id):
        return STEP_CLASS_GRADED

    step_type = (step.get("step_type") or "coding").strip().lower()
    if step_type == "coding":
        # grading._grade_coding already refuses to credit a testless coding step.
        return (STEP_CLASS_GRADED if (step.get("test_code_path") or "").strip()
                else STEP_CLASS_UNGRADED)
    # A reflect step with no bank is a prompt to think. Real work, still earns XP,
    # but not evidence of a demonstrated skill — the distinction aca-int-07
    # introduced as `assessed: False`.
    return STEP_CLASS_ACKNOWLEDGED


def counts_toward_certificate(step: dict) -> bool:
    return classify_step(step) == STEP_CLASS_GRADED


def pass_threshold_for(step: dict) -> int:
    """The percentage this step must reach to pass."""
    policy = step_policy(step)
    override = policy.get("pass_threshold_pct")
    if override is not None:
        return int(override)
    step_type = (step.get("step_type") or "coding").strip().lower()
    if step_type == "coding" and not has_item_bank(step.get("id")):
        return CODING_PASS_THRESHOLD_PCT
    return STEP_PASS_THRESHOLD_PCT


# ---------------------------------------------------------------------------
# Attempt policy
# ---------------------------------------------------------------------------

def _policy_row(step_id) -> dict:
    try:
        sid = int(step_id)
    except (TypeError, ValueError):
        return {}
    try:
        row = _conn().execute(
            "SELECT policy, items_per_attempt, pass_threshold_pct, max_attempts "
            "FROM fa_step_assessment_policy WHERE step_id=%s",
            (sid,),
        ).fetchone()
    except Exception:
        _log.warning("policy table unavailable for step %s", step_id, exc_info=True)
        return {}
    return _as_dict(row) or {}


def step_policy(step: dict) -> dict:
    """Resolved policy for a step. An absent row means practice defaults.

    ``items_per_attempt`` and ``pass_threshold_pct`` stay None when unset rather than
    being materialised: NULL means "use the constant", so raising a threshold moves
    every step that never asked for something different instead of leaving stale
    copies of an old default in the database.
    """
    row = _policy_row(step.get("id")) if isinstance(step, dict) else {}
    policy = str(row.get("policy") or ATTEMPT_POLICY_PRACTICE).strip().lower()
    if policy not in ATTEMPT_POLICIES:
        policy = ATTEMPT_POLICY_PRACTICE

    # A limited number of attempts at clicking "I Understand" is theatre. Downgrade
    # a mis-seeded summative acknowledgement rather than locking a learner out of a
    # button they cannot fail.
    if policy == ATTEMPT_POLICY_SUMMATIVE and classify_step(step) != STEP_CLASS_GRADED:
        _log.warning(
            "step %s is marked summative but is not graded — treating as practice",
            step.get("id"),
        )
        policy = ATTEMPT_POLICY_PRACTICE

    max_attempts = row.get("max_attempts")
    if policy == ATTEMPT_POLICY_SUMMATIVE:
        max_attempts = int(max_attempts or SUMMATIVE_MAX_ATTEMPTS)
    else:
        max_attempts = 0  # 0 == unlimited

    ipa = row.get("items_per_attempt")
    return {
        "policy": policy,
        "max_attempts": max_attempts,
        "items_per_attempt": int(ipa) if ipa else ASSESSMENT_ITEMS_PER_ATTEMPT,
        "pass_threshold_pct": row.get("pass_threshold_pct"),
    }


def set_step_policy(step_id: int, policy: str = ATTEMPT_POLICY_PRACTICE, *,
                    items_per_attempt: int | None = None,
                    pass_threshold_pct: int | None = None,
                    max_attempts: int | None = None) -> bool:
    """Declare a step's assessment policy. Refuses summative on an ungraded step."""
    policy = str(policy or "").strip().lower()
    if policy not in ATTEMPT_POLICIES:
        raise ValueError(f"unknown attempt policy {policy!r}")
    step = _load_step(step_id)
    if step is None:
        return False
    if policy == ATTEMPT_POLICY_SUMMATIVE and classify_step(step) != STEP_CLASS_GRADED:
        raise ValueError(
            f"step {step_id} has nothing to grade, so it cannot be summative — "
            "author an item bank or a verification test first"
        )
    conn = _conn()
    existing = conn.execute(
        "SELECT id FROM fa_step_assessment_policy WHERE step_id=%s", (int(step_id),)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE fa_step_assessment_policy SET policy=%s, items_per_attempt=%s, "
            "pass_threshold_pct=%s, max_attempts=%s, updated_at=%s WHERE step_id=%s",
            (policy, items_per_attempt, pass_threshold_pct, max_attempts, _now(),
             int(step_id)),
        )
    else:
        conn.execute(
            "INSERT INTO fa_step_assessment_policy "
            "(step_id, policy, items_per_attempt, pass_threshold_pct, max_attempts, "
            " updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (int(step_id), policy, items_per_attempt, pass_threshold_pct,
             max_attempts, _now()),
        )
    conn.commit()
    return True


def _last_reset_at(user_id: int, step_id: int) -> str | None:
    row = _conn().execute(
        "SELECT MAX(created_at) FROM fa_step_attempts "
        "WHERE user_id=%s AND step_id=%s AND kind='reset'",
        (int(user_id), int(step_id)),
    ).fetchone()
    return row[0] if row and row[0] else None


def attempts_used(user_id: int, step_id: int) -> int:
    """Graded attempts since the most recent instructor reset.

    Counts forward from the reset marker rather than deleting the rows before it —
    the ledger is append-only and fa_xp_ledger cites it.
    """
    try:
        uid, sid = int(user_id), int(step_id)
    except (TypeError, ValueError):
        return 0
    try:
        since = _last_reset_at(uid, sid)
        if since:
            row = _conn().execute(
                "SELECT COUNT(*) FROM fa_step_attempts WHERE user_id=%s AND step_id=%s "
                "AND kind='attempt' AND created_at > %s",
                (uid, sid, since),
            ).fetchone()
        else:
            row = _conn().execute(
                "SELECT COUNT(*) FROM fa_step_attempts WHERE user_id=%s AND step_id=%s "
                "AND kind='attempt'",
                (uid, sid),
            ).fetchone()
        return int(row[0] or 0)
    except Exception:
        _log.warning("attempt ledger unavailable for step %s", step_id, exc_info=True)
        return 0


def has_passed(user_id: int, step_id: int) -> bool:
    """Whether the learner has ever passed this step.

    Mastery is never withdrawn — the rule record_step_attempt already follows — so a
    passed step is never re-locked by an attempt limit.
    """
    try:
        row = _conn().execute(
            "SELECT COUNT(*) FROM fa_step_attempts WHERE user_id=%s AND step_id=%s "
            "AND kind='attempt' AND passed=1",
            (int(user_id), int(step_id)),
        ).fetchone()
        if int(row[0] or 0):
            return True
    except Exception:
        pass
    progress = _fadb.get_step_progress(user_id, step_id) or {}
    return progress.get("status") == STEP_STATUS_COMPLETED


def attempt_state(user_id: int, step_id: int) -> dict:
    """Everything a caller needs to decide whether another attempt is allowed."""
    step = _load_step(step_id)
    if step is None:
        return {"allowed": False, "reason": "unknown_step", "policy": ATTEMPT_POLICY_PRACTICE,
                "attempts_used": 0, "max_attempts": 0, "attempts_remaining": None}
    policy = step_policy(step)
    used = attempts_used(user_id, step_id)
    maximum = policy["max_attempts"]
    passed_already = has_passed(user_id, step_id)
    if not maximum:
        remaining = None  # unlimited
        allowed = True
    else:
        remaining = max(0, maximum - used)
        allowed = passed_already or remaining > 0
    return {
        "allowed": allowed,
        "reason": "" if allowed else "attempts_exhausted",
        "policy": policy["policy"],
        "attempts_used": used,
        "max_attempts": maximum,
        "attempts_remaining": remaining,
        "passed_already": passed_already,
    }


def reset_attempts(user_id: int, step_id: int, reason: str = "", actor: str = "") -> int:
    """Forgive a learner's attempts on a step by APPENDING a reset marker.

    Never deletes: an attempt ledger that can be edited is not evidence. The
    instructor UI that calls this is aca-trn-04; the function and its audit row ship
    with the limit so that introducing a cap does not introduce a dead end.
    """
    conn = _conn()
    conn.execute(
        "INSERT INTO fa_step_attempts "
        "(user_id, step_id, kind, attempt_num, policy, served_json, reason, actor, "
        " created_at) VALUES (%s,%s,'reset',0,%s,'[]',%s,%s,%s)",
        (int(user_id), int(step_id), ATTEMPT_POLICY_PRACTICE,
         reason or "instructor reset", actor or "system", _now()),
    )
    conn.commit()
    return 0


# ---------------------------------------------------------------------------
# Serving an attempt
# ---------------------------------------------------------------------------

def _load_step(step_id) -> dict | None:
    try:
        sid = int(step_id)
    except (TypeError, ValueError):
        return None
    try:
        row = _conn().execute(
            "SELECT id, mission_id, step_num, title, step_type, test_code_path, "
            "config_schema_json, xp_partial, skill_tag FROM fa_mission_steps WHERE id=%s",
            (sid,),
        ).fetchone()
    except Exception:
        return None
    return _as_dict(row)


def _open_attempt_row(user_id: int, step_id: int) -> dict | None:
    try:
        row = _conn().execute(
            "SELECT id, attempt_num, policy, served_json FROM fa_step_attempts "
            "WHERE user_id=%s AND step_id=%s AND kind='attempt' AND closed_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (int(user_id), int(step_id)),
        ).fetchone()
    except Exception:
        return None
    return _as_dict(row)


def _client_item(served_entry: dict, bank_by_key: dict) -> dict | None:
    """Project one served item down to what the browser may see.

    Option TEXT in the served permutation, and nothing else. No key, no flags, no
    authored index — the mapping back lives only in served_json.
    """
    item = bank_by_key.get(served_entry.get("item_key"))
    if not item:
        return None
    options = item.get("options") or []
    order = served_entry.get("option_order") or []
    try:
        shown = [options[i] for i in order]
    except (IndexError, TypeError):
        return None
    return {
        "item_key": item["item_key"],
        "prompt": item["prompt"],
        "options": shown,
    }


def open_attempt(user_id: int, step_id: int) -> dict | None:
    """The items to serve for this learner's current attempt at this step.

    Returns None when the step has no bank. Returns the EXISTING open attempt when
    there is one, so a page refresh neither consumes an attempt nor rerolls the
    draw — a refresh must not be a way to shop for an easier set of questions.
    """
    bank = get_item_bank(step_id)
    if not bank:
        return None
    step = _load_step(step_id)
    if step is None:
        return None
    policy = step_policy(step)
    bank_by_key = {i["item_key"]: i for i in bank}

    existing = _open_attempt_row(user_id, step_id)
    if existing:
        try:
            served = json.loads(existing.get("served_json") or "[]")
        except (TypeError, ValueError):
            served = []
        items = [ci for e in served if (ci := _client_item(e, bank_by_key))]
        if items:
            state = attempt_state(user_id, step_id)
            return {
                "attempt_id": existing["id"],
                "attempt_num": existing["attempt_num"],
                "items": items,
                "pass_threshold_pct": pass_threshold_for(step),
                "policy": state["policy"],
                "attempts_used": state["attempts_used"],
                "attempts_remaining": state["attempts_remaining"],
                "max_attempts": state["max_attempts"],
            }
        # The bank changed under an open attempt (an item was retired). Close it
        # unscored and draw fresh rather than serving a hole.
        _close_attempt(existing["id"], answers={}, score_pct=None, passed=None,
                       reason="bank_changed")

    draw = min(policy["items_per_attempt"], len(bank))
    chosen = _rand.sample(bank, draw)
    _rand.shuffle(chosen)
    served = []
    for item in chosen:
        order = list(range(len(item["options"])))
        _rand.shuffle(order)
        served.append({"item_key": item["item_key"], "option_order": order})

    used = attempts_used(user_id, step_id)
    conn = _conn()
    conn.execute(
        "INSERT INTO fa_step_attempts "
        "(user_id, step_id, kind, attempt_num, policy, served_json, created_at) "
        "VALUES (%s,%s,'attempt',%s,%s,%s,%s)",
        (int(user_id), int(step_id), used + 1, policy["policy"],
         json.dumps(served), _now()),
    )
    conn.commit()
    row = _open_attempt_row(user_id, step_id)
    state = attempt_state(user_id, step_id)
    return {
        "attempt_id": (row or {}).get("id"),
        "attempt_num": used + 1,
        "items": [ci for e in served if (ci := _client_item(e, bank_by_key))],
        "pass_threshold_pct": pass_threshold_for(step),
        "policy": state["policy"],
        "attempts_used": state["attempts_used"],
        "attempts_remaining": state["attempts_remaining"],
        "max_attempts": state["max_attempts"],
    }


def _close_attempt(attempt_id: int, *, answers: dict, score_pct, passed, reason="") -> None:
    """Write the verdict onto an open attempt.

    The one UPDATE this module performs against an append-only table, and it is the
    intended lifecycle of a row rather than a rewrite of history: an attempt is
    created open (served) and closed once (graded). Nothing that has a closed_at is
    ever touched again — see the WHERE clause.
    """
    conn = _conn()
    conn.execute(
        "UPDATE fa_step_attempts SET answers_json=%s, score_pct=%s, passed=%s, "
        "closed_at=%s, reason=%s WHERE id=%s AND closed_at IS NULL",
        (json.dumps(answers or {}), score_pct,
         None if passed is None else (1 if passed else 0),
         _now(), reason, int(attempt_id)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Grading an attempt
# ---------------------------------------------------------------------------

def grade_attempt(user_id: int, step_id: int, answers: dict) -> dict:
    """Score the learner's open attempt. ``answers`` maps item_key -> DISPLAYED index.

    The displayed index is mapped back through the recorded permutation before it is
    compared with anything. This is the load-bearing line of the whole model: the
    client's indices are meaningless without ``served_json``, so the option order in
    the DOM tells an attacker nothing.
    """
    step = _load_step(step_id)
    if step is None:
        return {"ok": False, "reason": "unknown_step", "passed": False, "assessed": False}
    bank_by_key = {i["item_key"]: i for i in get_item_bank(step_id)}
    if not bank_by_key:
        return {"ok": False, "reason": "no_item_bank", "passed": False, "assessed": False}

    attempt = _open_attempt_row(user_id, step_id)
    if not attempt:
        # Nothing was served, so there is nothing to grade. Refusing here is what
        # stops a POST of bare answers from being scored against a draw the server
        # never made.
        return {"ok": False, "reason": "no_open_attempt", "passed": False,
                "assessed": True}
    try:
        served = json.loads(attempt.get("served_json") or "[]")
    except (TypeError, ValueError):
        served = []
    if not served:
        return {"ok": False, "reason": "no_open_attempt", "passed": False,
                "assessed": True}

    answers = answers if isinstance(answers, dict) else {}
    results, correct = [], 0
    for entry in served:
        key = entry.get("item_key")
        item = bank_by_key.get(key)
        if not item:
            continue
        order = entry.get("option_order") or []
        raw = answers.get(key)
        try:
            shown_idx = int(raw)
        except (TypeError, ValueError):
            shown_idx = -1
        authored_idx = order[shown_idx] if 0 <= shown_idx < len(order) else None
        is_right = authored_idx is not None and authored_idx == item.get("correct_index")
        if is_right:
            correct += 1
        # The explanation and the right answer are released only now, with the
        # attempt closed — before that they would be the answer key.
        try:
            correct_shown = order.index(int(item.get("correct_index") or 0))
        except (ValueError, TypeError):
            correct_shown = None
        results.append({
            "item_key": key,
            "prompt": item.get("prompt"),
            "correct": is_right,
            "answered": shown_idx if shown_idx >= 0 else None,
            "correct_option": correct_shown,
            "explanation": item.get("explanation") or "",
        })

    total = len(results)
    score_pct = int(round(correct / total * 100)) if total else 0
    threshold = pass_threshold_for(step)
    passed = score_pct >= threshold

    _close_attempt(attempt["id"], answers=answers, score_pct=score_pct, passed=passed)
    state = attempt_state(user_id, step_id)
    return {
        "ok": True,
        "assessed": True,
        "passed": passed,
        "score": score_pct,
        "correct": correct,
        "total": total,
        "pass_threshold_pct": threshold,
        "items": results,
        "attempts_used": state["attempts_used"],
        "attempts_remaining": state["attempts_remaining"],
        "policy": state["policy"],
        "reason": "" if passed else "below_threshold",
    }


# ---------------------------------------------------------------------------
# Mission and certificate rollups
# ---------------------------------------------------------------------------

def _mission_steps(mission_id: int) -> list[dict]:
    try:
        rows = _conn().execute(
            "SELECT id, mission_id, step_num, title, step_type, test_code_path "
            "FROM fa_mission_steps WHERE mission_id=%s ORDER BY step_num",
            (int(mission_id),),
        ).fetchall()
    except Exception:
        return []
    return [_as_dict(r) for r in rows]


def mission_assessment_summary(user_id: int, mission_id: int) -> dict:
    """What a completed mission is evidence OF.

    Mission COMPLETION is unchanged and still lives in ``grading.mission_is_complete``
    — every step must have a completed progress row. Since aca-int-05 a failed step is
    filed 'attempted', so that rule already implies "every assessed step passed"; a
    percentage gate on completion would be strictly weaker, which is why this
    classifies rather than gates.
    """
    steps = _mission_steps(mission_id)
    graded = [s for s in steps if classify_step(s) == STEP_CLASS_GRADED]
    passed = [s for s in graded if has_passed(user_id, s["id"])]
    total_graded = len(graded)
    pct = int(round(len(passed) / total_graded * 100)) if total_graded else 0

    from .grading import mission_is_complete
    complete = mission_is_complete(user_id, mission_id)

    if not complete:
        status = MISSION_ASSESSMENT_INCOMPLETE
    elif not total_graded:
        # Completed by turning pages. Real work, but not a demonstrated skill — this
        # is the distinction a certificate needs and never had.
        status = MISSION_ASSESSMENT_ATTESTED
    elif pct >= MISSION_ASSESSMENT_THRESHOLD_PCT:
        status = MISSION_ASSESSMENT_DEMONSTRATED
    else:
        status = MISSION_ASSESSMENT_PARTIAL

    return {
        "mission_id": mission_id,
        "assessment_status": status,
        "graded_steps": total_graded,
        "graded_passed": len(passed),
        "assessment_pct": pct,
        "threshold_pct": MISSION_ASSESSMENT_THRESHOLD_PCT,
        "total_steps": len(steps),
        "complete": complete,
    }


def certificate_assessment_score(user_id: int) -> dict:
    """Mean of the learner's BEST score per graded step, over steps they attempted.

    Best-per-step, not mean-of-all-attempts: practice is unlimited by design, so
    averaging attempts would punish the learner for practising — which is the exact
    behaviour the practice policy exists to encourage.

    A learner who has attempted no graded step scores 0, so the gate cannot be
    satisfied vacuously by a catalogue with nothing graded in it.
    """
    try:
        rows = _conn().execute(
            "SELECT step_id, MAX(score_pct) AS best FROM fa_step_attempts "
            "WHERE user_id=%s AND kind='attempt' AND score_pct IS NOT NULL "
            "GROUP BY step_id",
            (int(user_id),),
        ).fetchall()
    except Exception:
        _log.warning("attempt ledger unavailable for user %s", user_id, exc_info=True)
        rows = []
    scores = [int((_as_dict(r) or {}).get("best") or 0) for r in rows]

    # Coding steps are graded but produce no item score, so credit a passed coding
    # step at its own threshold. Leaving them out would let a learner who has only
    # ever passed coding steps score 0 on a gate they have plainly met.
    try:
        coding = _conn().execute(
            "SELECT s.id FROM fa_mission_steps s "
            "JOIN fa_step_progress sp ON sp.step_id = s.id "
            "WHERE sp.user_id=%s AND sp.status=%s AND s.step_type='coding' "
            "  AND s.test_code_path IS NOT NULL AND s.test_code_path <> ''",
            (int(user_id), STEP_STATUS_COMPLETED),
        ).fetchall()
    except Exception:
        coding = []
    scored_ids = {int((_as_dict(r) or {}).get("step_id") or 0) for r in rows}
    for row in coding:
        sid = int((_as_dict(row) or {}).get("id") or 0)
        if sid and sid not in scored_ids:
            scores.append(CODING_PASS_THRESHOLD_PCT)

    score = int(round(sum(scores) / len(scores))) if scores else 0
    return {
        "score": score,
        "graded_steps": len(scores),
        "threshold": CERT_ASSESSMENT_THRESHOLD_PCT,
        "met": bool(scores) and score >= CERT_ASSESSMENT_THRESHOLD_PCT,
    }


def coverage_report() -> dict:
    """How much of the catalogue is actually graded.

    Exists so the shortfall described in the spec is a NUMBER on an endpoint rather
    than a claim in a document. 94% of steps were passive; this reports what that is
    today instead of letting it be re-discovered by the next audit.
    """
    try:
        steps = [_as_dict(r) for r in _conn().execute(
            "SELECT id, mission_id, step_type, test_code_path FROM fa_mission_steps"
        ).fetchall()]
    except Exception:
        return {"total_steps": 0, "graded": 0, "ungraded": 0, "acknowledged": 0,
                "graded_pct": 0, "by_type": {}}

    counts = {STEP_CLASS_GRADED: 0, STEP_CLASS_UNGRADED: 0, STEP_CLASS_ACKNOWLEDGED: 0}
    by_type: dict[str, dict] = {}
    for step in steps:
        cls = classify_step(step)
        counts[cls] = counts.get(cls, 0) + 1
        st = (step.get("step_type") or "?").strip().lower()
        by_type.setdefault(st, {"total": 0, STEP_CLASS_GRADED: 0})
        by_type[st]["total"] += 1
        if cls == STEP_CLASS_GRADED:
            by_type[st][STEP_CLASS_GRADED] += 1

    total = len(steps)
    return {
        "total_steps": total,
        "graded": counts[STEP_CLASS_GRADED],
        "ungraded": counts[STEP_CLASS_UNGRADED],
        "acknowledged": counts[STEP_CLASS_ACKNOWLEDGED],
        "graded_pct": int(round(counts[STEP_CLASS_GRADED] / total * 100)) if total else 0,
        "by_type": by_type,
    }
