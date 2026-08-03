# CUI // SP-CTI
"""aca-trn-01 — the Academy had the machinery of assessment and no assessment model.

The INT epic made grading server-authoritative: the browser no longer says whether it
passed, the test comes from the step row, and the answer key is stripped before the
page renders. What it could not fix is that there was nothing to grade.

    grading._verdict          score = 100 if passed else 0
    db.record_step_attempt    score = 100 if passed else 0
    _grade_reflect            all 32 reflect steps are free text, so it returned
                              assessed=False, passed=True for every one of them
    CERT_TIERS['foundation']  assessment_score_min: 70 — never read by
                              check_cert_eligibility, so it fell off the end of the
                              if-chain and the certificate attested to an assessment
                              that did not exist

Every test below is one of the success criteria stated in the spec
(docs/features/forge-academy-aca-trn-01-assessment-model.md §1) before the code was
written. Spelled out rather than parameterised, because each one is a distinct claim
about what the model guarantees.
"""
from __future__ import annotations

import importlib
import json

import pytest

from _academy_conn import academy_conn

SCHEMA = """
CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
                       role TEXT DEFAULT 'unset', xp INTEGER DEFAULT 0,
                       level TEXT DEFAULT 'recruit');
CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
                          title TEXT, tier INTEGER DEFAULT 1, is_active INTEGER DEFAULT 1,
                          role_filter TEXT DEFAULT 'all');
CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id INTEGER,
                               step_num INTEGER, title TEXT, step_type TEXT,
                               test_code_path TEXT, config_schema_json TEXT,
                               xp_partial INTEGER DEFAULT 50, skill_tag TEXT);
CREATE TABLE fa_step_progress (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                               step_id INTEGER, status TEXT, submission TEXT,
                               score INTEGER DEFAULT 0, hints_used INTEGER DEFAULT 0,
                               completed_at TEXT);
CREATE TABLE fa_mission_progress (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                                  mission_id INTEGER, status TEXT, attempts INTEGER DEFAULT 0);
CREATE TABLE fa_assessment_items (id INTEGER PRIMARY KEY AUTOINCREMENT, step_id INTEGER,
                                  item_key TEXT, prompt TEXT,
                                  options_json TEXT DEFAULT '[]',
                                  correct_index INTEGER DEFAULT 0, explanation TEXT,
                                  difficulty TEXT DEFAULT 'core',
                                  is_active INTEGER DEFAULT 1,
                                  classification TEXT, tenant_id TEXT, created_at TEXT,
                                  UNIQUE(step_id, item_key));
CREATE TABLE fa_step_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                               step_id INTEGER, kind TEXT DEFAULT 'attempt',
                               attempt_num INTEGER DEFAULT 1, policy TEXT DEFAULT 'practice',
                               served_json TEXT DEFAULT '[]', answers_json TEXT,
                               score_pct INTEGER, passed INTEGER, closed_at TEXT,
                               reason TEXT, actor TEXT, classification TEXT,
                               tenant_id TEXT, created_at TEXT);
CREATE TABLE fa_step_assessment_policy (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                        step_id INTEGER UNIQUE, policy TEXT DEFAULT 'practice',
                                        items_per_attempt INTEGER, pass_threshold_pct INTEGER,
                                        max_attempts INTEGER, updated_at TEXT, created_at TEXT);
CREATE TABLE fa_certificates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                              cert_key TEXT, issued_at TEXT);

INSERT INTO fa_users (id, username, role) VALUES (1, 'learner', 'engineer');
INSERT INTO fa_missions (id, slug, title, tier) VALUES (1, 'm-assessed', 'Assessed', 1);
INSERT INTO fa_missions (id, slug, title, tier) VALUES (2, 'm-watch-only', 'Pages', 1);

-- The assessed mission: one lesson step carrying an item bank. Declared 'watch' on
-- purpose — the model's claim is that a BANK makes a step graded, whatever its type,
-- so a lesson can become assessable without re-labelling it (aca-hon-04).
INSERT INTO fa_mission_steps (id, mission_id, step_num, title, step_type, test_code_path)
VALUES (10, 1, 1, 'Context Window Limits', 'watch', '');

-- The pages-turned mission: nothing to grade anywhere in it.
INSERT INTO fa_mission_steps (id, mission_id, step_num, title, step_type, test_code_path)
VALUES (20, 2, 1, 'Watch This', 'watch', '');
INSERT INTO fa_mission_steps (id, mission_id, step_num, title, step_type, test_code_path)
VALUES (21, 2, 2, 'Reflect On It', 'reflect', '');
"""

#: Five items so the bank exceeds the three-item draw. The correct index is
#: deliberately different per item, so a test that passed by always answering the
#: same position would be visible as such.
BANK = [
    ("q1", "First?", ["a1", "b1", "c1", "d1"], 0),
    ("q2", "Second?", ["a2", "b2", "c2", "d2"], 1),
    ("q3", "Third?", ["a3", "b3", "c3", "d3"], 2),
    ("q4", "Fourth?", ["a4", "b4", "c4", "d4"], 3),
    ("q5", "Fifth?", ["a5", "b5", "c5", "d5"], 0),
]

STEP_ID = 10
USER_ID = 1


@pytest.fixture()
def fa(monkeypatch):
    """The academy modules bound to one in-memory schema.

    Only ``db.get_connection`` is patched: assessment._conn and grading._conn both
    resolve through the db MODULE attribute rather than a bound import, so patching
    the one seam reaches all three.
    """
    conn = academy_conn()
    conn.executescript(SCHEMA)
    for key, prompt, options, correct in BANK:
        conn.execute(
            "INSERT INTO fa_assessment_items "
            "(step_id, item_key, prompt, options_json, correct_index, explanation) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (STEP_ID, key, prompt, json.dumps(options), correct, f"because {key}"),
        )
    conn.commit()

    dbmod = importlib.import_module("apps.forge_academy.db")
    monkeypatch.setattr(dbmod, "get_connection", lambda *a, **k: conn)
    assessment = importlib.import_module("apps.forge_academy.assessment")
    grading = importlib.import_module("apps.forge_academy.grading")
    try:
        yield assessment, grading, dbmod, conn
    finally:
        conn.close()


def _served(conn, user_id=USER_ID, step_id=STEP_ID):
    """The server's record of the open attempt: which items, in which permutation."""
    row = conn.execute(
        "SELECT served_json FROM fa_step_attempts WHERE user_id=%s AND step_id=%s "
        "AND kind='attempt' AND closed_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id, step_id),
    ).fetchone()
    return json.loads(row["served_json"]) if row else []


def _answers(conn, n_correct, *, user_id=USER_ID, step_id=STEP_ID):
    """Answers for the open attempt, exactly ``n_correct`` of them right.

    Built by reading the server's own served_json — which is the point: a learner
    cannot do this, because that row never leaves the server. The test uses it to
    control the score precisely, so "2 of 3 fails at 70" is asserted rather than
    hoped for.
    """
    correct_by_key = {k: c for k, _, _, c in
                      [(b[0], b[1], b[2], b[3]) for b in BANK]}
    answers, given = {}, 0
    for entry in _served(conn, user_id, step_id):
        key = entry["item_key"]
        order = entry["option_order"]
        authored_correct = correct_by_key[key]
        shown_correct = order.index(authored_correct)
        if given < n_correct:
            answers[key] = shown_correct
            given += 1
        else:
            # Any displayed position that is not the right one.
            answers[key] = next(i for i in range(len(order)) if i != shown_correct)
    return answers


# ---------------------------------------------------------------------------
# Criterion: a 5-item bank served 3 at a time produces a different served
# set/order across attempts, and the correct index never appears in any
# client-facing payload.
# ---------------------------------------------------------------------------

def test_the_draw_varies_across_attempts(fa):
    assessment, _, _, conn = fa
    signatures = set()
    for _ in range(25):
        served = assessment.open_attempt(USER_ID, STEP_ID)
        signatures.add(tuple(i["item_key"] for i in served["items"]))
        assessment.grade_attempt(USER_ID, STEP_ID, {})  # close it so the next redraws
    # 5 choose 3, ordered, is 60 possibilities. Landing on one of them 25 times
    # running would be a fixed draw, not chance.
    assert len(signatures) > 1


def test_each_attempt_serves_exactly_the_configured_draw(fa):
    assessment, _, _, _ = fa
    from apps.forge_academy.constants import ASSESSMENT_ITEMS_PER_ATTEMPT

    served = assessment.open_attempt(USER_ID, STEP_ID)
    assert len(served["items"]) == ASSESSMENT_ITEMS_PER_ATTEMPT
    # No item is served twice in one attempt.
    keys = [i["item_key"] for i in served["items"]]
    assert len(set(keys)) == len(keys)


def test_option_order_is_permuted_per_attempt(fa):
    """The displayed position of the right answer moves.

    This is what makes memorising a position worthless. Memorising the CONTENT is
    learning, which is the point.
    """
    assessment, _, _, conn = fa
    correct_by_key = {b[0]: b[3] for b in BANK}
    positions: dict[str, set] = {}
    for _ in range(25):
        assessment.open_attempt(USER_ID, STEP_ID)
        for entry in _served(conn):
            key = entry["item_key"]
            positions.setdefault(key, set()).add(
                entry["option_order"].index(correct_by_key[key]))
        assessment.grade_attempt(USER_ID, STEP_ID, {})
    # At least one item's correct answer appeared in more than one position.
    assert any(len(seen) > 1 for seen in positions.values()), positions


def test_the_served_payload_carries_no_answer_key(fa):
    """The whole defence: nothing in what crosses the wire identifies the answer."""
    assessment, _, _, _ = fa
    served = assessment.open_attempt(USER_ID, STEP_ID)
    for item in served["items"]:
        assert set(item) == {"item_key", "prompt", "options"}
        assert all(isinstance(o, str) for o in item["options"])
    blob = json.dumps(served)
    for token in ("correct_index", "correct_option", "is_correct", "explanation"):
        assert token not in blob, f"{token} leaked into the served payload"


def test_a_refresh_resumes_the_same_attempt(fa):
    """A reload must not reroll the draw or consume an attempt.

    Otherwise refreshing is a way to shop for an easier set of questions.
    """
    assessment, _, _, conn = fa
    first = assessment.open_attempt(USER_ID, STEP_ID)
    second = assessment.open_attempt(USER_ID, STEP_ID)
    assert first["attempt_id"] == second["attempt_id"]
    assert [i["item_key"] for i in first["items"]] == [i["item_key"] for i in second["items"]]
    assert [i["options"] for i in first["items"]] == [i["options"] for i in second["items"]]
    open_rows = conn.execute(
        "SELECT COUNT(*) FROM fa_step_attempts WHERE user_id=%s AND step_id=%s "
        "AND kind='attempt'", (USER_ID, STEP_ID)).fetchone()[0]
    assert open_rows == 1


# ---------------------------------------------------------------------------
# Criterion: 2 of 3 correct = 66% = fail at a 70% threshold. 3 of 3 = pass.
# The old code could not express this — it was 100 or 0.
# ---------------------------------------------------------------------------

def test_two_of_three_is_a_fail(fa):
    assessment, _, _, conn = fa
    assessment.open_attempt(USER_ID, STEP_ID)
    result = assessment.grade_attempt(USER_ID, STEP_ID, _answers(conn, 2))
    assert result["correct"] == 2 and result["total"] == 3
    assert result["score"] == 67
    assert result["passed"] is False
    assert result["pass_threshold_pct"] == 70


def test_three_of_three_is_a_pass(fa):
    assessment, _, _, conn = fa
    assessment.open_attempt(USER_ID, STEP_ID)
    result = assessment.grade_attempt(USER_ID, STEP_ID, _answers(conn, 3))
    assert result["score"] == 100
    assert result["passed"] is True


def test_the_real_percentage_reaches_step_progress(fa):
    """`score` used to be `100 if passed else 0` unconditionally."""
    assessment, grading, dbmod, conn = fa
    assessment.open_attempt(USER_ID, STEP_ID)
    verdict = grading.grade_step(STEP_ID, answers=_answers(conn, 2), user_id=USER_ID)
    assert verdict["passed"] is False
    assert verdict["score"] == 67
    status = dbmod.record_step_attempt(USER_ID, STEP_ID, passed=verdict["passed"],
                                       score=verdict["score"])
    # aca-int-05: a failure is 'attempted', never 'completed'.
    assert status == "attempted"
    row = conn.execute("SELECT status, score FROM fa_step_progress WHERE user_id=%s "
                       "AND step_id=%s", (USER_ID, STEP_ID)).fetchone()
    assert row["score"] == 67


def test_a_later_weaker_pass_does_not_lower_a_recorded_score(fa):
    """Mastery is never withdrawn — including the number attached to it."""
    _, _, dbmod, conn = fa
    dbmod.record_step_attempt(USER_ID, STEP_ID, passed=True, score=100)
    dbmod.record_step_attempt(USER_ID, STEP_ID, passed=True, score=70)
    row = conn.execute("SELECT score FROM fa_step_progress WHERE user_id=%s AND "
                       "step_id=%s", (USER_ID, STEP_ID)).fetchone()
    assert row["score"] == 100


def test_answers_are_meaningless_without_the_served_row(fa):
    """Posting answers with no open attempt is refused, not scored.

    Grading a bare POST against a draw the server never made is the attack this
    closes: it would let a client choose its own questions.
    """
    assessment, _, _, _ = fa
    result = assessment.grade_attempt(USER_ID, STEP_ID, {"q1": 0, "q2": 0, "q3": 0})
    assert result["ok"] is False
    assert result["reason"] == "no_open_attempt"
    assert result["passed"] is False


# ---------------------------------------------------------------------------
# Criterion: a summative step refuses a 4th attempt and grants no credit for it.
# A practice step accepts an unbounded number of attempts.
# ---------------------------------------------------------------------------

def _burn(assessment, conn, n, correct=0):
    for _ in range(n):
        assessment.open_attempt(USER_ID, STEP_ID)
        assessment.grade_attempt(USER_ID, STEP_ID, _answers(conn, correct))


def test_summative_refuses_a_fourth_attempt(fa):
    assessment, _, _, conn = fa
    from apps.forge_academy.constants import SUMMATIVE_MAX_ATTEMPTS

    assessment.set_step_policy(STEP_ID, "summative")
    _burn(assessment, conn, SUMMATIVE_MAX_ATTEMPTS)
    state = assessment.attempt_state(USER_ID, STEP_ID)
    assert state["attempts_used"] == SUMMATIVE_MAX_ATTEMPTS
    assert state["attempts_remaining"] == 0
    assert state["allowed"] is False
    assert state["reason"] == "attempts_exhausted"


def test_practice_is_unbounded(fa):
    assessment, _, _, conn = fa
    _burn(assessment, conn, 7)
    state = assessment.attempt_state(USER_ID, STEP_ID)
    assert state["policy"] == "practice"
    assert state["attempts_used"] == 7
    assert state["attempts_remaining"] is None
    assert state["allowed"] is True


def test_a_passed_summative_step_is_never_relocked(fa):
    """Exhausting attempts must not withdraw a pass already earned."""
    assessment, _, _, conn = fa
    assessment.set_step_policy(STEP_ID, "summative")
    _burn(assessment, conn, 1, correct=3)   # passed
    _burn(assessment, conn, 2, correct=0)   # then used the rest up
    state = assessment.attempt_state(USER_ID, STEP_ID)
    assert state["attempts_remaining"] == 0
    assert state["passed_already"] is True
    assert state["allowed"] is True


def test_a_reset_forgives_attempts_without_deleting_them(fa):
    """The ledger is append-only: a reset is a compensating row, not a DELETE."""
    assessment, _, _, conn = fa
    assessment.set_step_policy(STEP_ID, "summative")
    _burn(assessment, conn, 3)
    assert assessment.attempt_state(USER_ID, STEP_ID)["allowed"] is False

    assessment.reset_attempts(USER_ID, STEP_ID, reason="instructor override",
                              actor="instructor@example.mil")
    state = assessment.attempt_state(USER_ID, STEP_ID)
    assert state["attempts_used"] == 0
    assert state["allowed"] is True

    # The forgiven attempts are still on the record.
    kept = conn.execute(
        "SELECT COUNT(*) FROM fa_step_attempts WHERE user_id=%s AND step_id=%s "
        "AND kind='attempt'", (USER_ID, STEP_ID)).fetchone()[0]
    assert kept == 3
    marker = conn.execute(
        "SELECT reason, actor FROM fa_step_attempts WHERE kind='reset'").fetchone()
    assert marker["reason"] == "instructor override"
    assert marker["actor"] == "instructor@example.mil"


def test_a_step_with_nothing_to_grade_cannot_be_summative(fa):
    """A limited number of attempts at clicking 'I Understand' is theatre."""
    assessment, _, _, _ = fa
    with pytest.raises(ValueError):
        assessment.set_step_policy(20, "summative")   # a watch step with no bank


def test_a_misseeded_summative_acknowledgement_degrades_to_practice(fa):
    """Rather than locking a learner out of a button they cannot fail."""
    assessment, _, _, conn = fa
    conn.execute(
        "INSERT INTO fa_step_assessment_policy (step_id, policy) VALUES (%s,'summative')",
        (20,),
    )
    conn.commit()
    step = {"id": 20, "step_type": "watch", "test_code_path": ""}
    assert assessment.step_policy(step)["policy"] == "practice"
    assert assessment.step_policy(step)["max_attempts"] == 0


# ---------------------------------------------------------------------------
# Criterion: classification — a bank makes a step graded whatever its type.
# ---------------------------------------------------------------------------

def test_an_item_bank_promotes_a_watch_step_to_graded(fa):
    assessment, _, _, _ = fa
    step = {"id": STEP_ID, "step_type": "watch", "test_code_path": ""}
    assert assessment.classify_step(step) == "graded"
    assert assessment.counts_toward_certificate(step) is True


def test_a_watch_step_with_no_bank_is_acknowledged_not_graded(fa):
    assessment, _, _, _ = fa
    step = {"id": 20, "step_type": "watch", "test_code_path": ""}
    assert assessment.classify_step(step) == "acknowledged"
    assert assessment.counts_toward_certificate(step) is False


def test_a_coding_step_with_no_test_is_ungraded(fa):
    assessment, _, _, _ = fa
    step = {"id": 999, "step_type": "coding", "test_code_path": ""}
    assert assessment.classify_step(step) == "ungraded"
    assert assessment.counts_toward_certificate(step) is False


def test_a_coding_step_keeps_an_all_or_nothing_threshold(fa):
    """Because code_runner reports one boolean for the whole suite."""
    assessment, _, _, _ = fa
    from apps.forge_academy.constants import CODING_PASS_THRESHOLD_PCT

    step = {"id": 999, "step_type": "coding", "test_code_path": "t.py"}
    assert assessment.pass_threshold_for(step) == CODING_PASS_THRESHOLD_PCT == 100


# ---------------------------------------------------------------------------
# Criterion: a mission of only `watch` steps completes but is classified
# `attested`, never `demonstrated`, and contributes nothing to the certificate
# assessment score.
# ---------------------------------------------------------------------------

def _complete(conn, step_ids, user_id=USER_ID):
    for sid in step_ids:
        conn.execute(
            "INSERT INTO fa_step_progress (user_id, step_id, status, score) "
            "VALUES (%s,%s,'completed',100)", (user_id, sid))
    conn.commit()


def test_a_pages_turned_mission_is_attested_never_demonstrated(fa):
    assessment, _, _, conn = fa
    _complete(conn, [20, 21])
    summary = assessment.mission_assessment_summary(USER_ID, 2)
    assert summary["complete"] is True
    assert summary["graded_steps"] == 0
    assert summary["assessment_status"] == "attested"
    assert summary["assessment_status"] != "demonstrated"


def test_an_incomplete_mission_is_incomplete(fa):
    assessment, _, _, conn = fa
    _complete(conn, [20])          # step 21 left undone
    summary = assessment.mission_assessment_summary(USER_ID, 2)
    assert summary["assessment_status"] == "incomplete"


def test_a_passed_graded_mission_is_demonstrated(fa):
    assessment, _, _, conn = fa
    _burn(assessment, conn, 1, correct=3)     # pass the graded step
    _complete(conn, [STEP_ID])
    summary = assessment.mission_assessment_summary(USER_ID, 1)
    assert summary["graded_steps"] == 1
    assert summary["assessment_pct"] == 100
    assert summary["assessment_status"] == "demonstrated"


def test_pages_turned_contributes_nothing_to_the_certificate_score(fa):
    assessment, _, _, conn = fa
    _complete(conn, [20, 21])
    result = assessment.certificate_assessment_score(USER_ID)
    assert result["graded_steps"] == 0
    assert result["score"] == 0
    assert result["met"] is False


# ---------------------------------------------------------------------------
# Criterion: assessment_score_min is enforced — a learner below 70% aggregate is
# not eligible for the Foundation certificate, and the gate appears in gates[]
# with its figure. This requirement was declared in CERT_TIERS from the start and
# check_cert_eligibility never read it.
# ---------------------------------------------------------------------------

def _gate(eligibility):
    for gate in eligibility["gates"]:
        if gate["name"].startswith("Assessment Score"):
            return gate
    return None


def test_the_assessment_gate_is_actually_evaluated(fa):
    _, _, dbmod, _ = fa
    gate = _gate(dbmod.check_cert_eligibility(USER_ID, "foundation"))
    assert gate is not None, "assessment_score_min fell off the if-chain again"
    assert gate["name"] == "Assessment Score >= 70"


def test_the_gate_cannot_be_satisfied_vacuously(fa):
    """A learner who has attempted no graded step scores 0, not 'no opinion'."""
    _, _, dbmod, _ = fa
    gate = _gate(dbmod.check_cert_eligibility(USER_ID, "foundation"))
    assert gate["met"] is False
    assert "No graded steps attempted" in gate["detail"]


def test_a_learner_below_the_threshold_fails_the_gate(fa):
    assessment, _, dbmod, conn = fa
    assessment.open_attempt(USER_ID, STEP_ID)
    assessment.grade_attempt(USER_ID, STEP_ID, _answers(conn, 2))   # 67%
    eligibility = dbmod.check_cert_eligibility(USER_ID, "foundation")
    gate = _gate(eligibility)
    assert gate["met"] is False
    assert "67%" in gate["detail"]
    assert eligibility["eligible"] is False


def test_a_learner_above_the_threshold_meets_the_gate(fa):
    assessment, _, dbmod, conn = fa
    assessment.open_attempt(USER_ID, STEP_ID)
    assessment.grade_attempt(USER_ID, STEP_ID, _answers(conn, 3))   # 100%
    gate = _gate(dbmod.check_cert_eligibility(USER_ID, "foundation"))
    assert gate["met"] is True
    assert "100%" in gate["detail"]
    assert "1 graded steps" in gate["detail"]


def test_the_score_is_the_best_attempt_not_the_mean_of_all(fa):
    """Practice is unlimited by design, so averaging attempts would punish practising."""
    assessment, _, _, conn = fa
    assessment.open_attempt(USER_ID, STEP_ID)
    assessment.grade_attempt(USER_ID, STEP_ID, _answers(conn, 0))    # 0%
    assessment.open_attempt(USER_ID, STEP_ID)
    assessment.grade_attempt(USER_ID, STEP_ID, _answers(conn, 3))    # 100%
    result = assessment.certificate_assessment_score(USER_ID)
    assert result["score"] == 100
    assert result["graded_steps"] == 1


# ---------------------------------------------------------------------------
# Bank authoring rules, and the shipped bank itself.
# ---------------------------------------------------------------------------

def test_validate_rejects_a_bank_no_larger_than_the_draw(fa):
    assessment, _, _, _ = fa
    small = [{"item_key": f"k{i}", "prompt": "p", "options": ["a", "b"],
              "correct_index": 0} for i in range(3)]
    problems = assessment.validate_item_bank(small)
    assert any("need >=" in p for p in problems)


def test_validate_rejects_an_out_of_range_correct_index(fa):
    assessment, _, _, _ = fa
    items = [{"item_key": f"k{i}", "prompt": "p", "options": ["a", "b"],
              "correct_index": 0} for i in range(5)]
    items[2]["correct_index"] = 7
    problems = assessment.validate_item_bank(items)
    assert any("out of range" in p for p in problems)


def test_validate_rejects_duplicate_item_keys(fa):
    assessment, _, _, _ = fa
    items = [{"item_key": "same", "prompt": "p", "options": ["a", "b"],
              "correct_index": 0} for _ in range(5)]
    problems = assessment.validate_item_bank(items)
    assert any("duplicate item_key" in p for p in problems)


def test_the_shipped_tier1_banks_are_well_formed():
    """The authored YAML must satisfy the rules the seeder enforces.

    A malformed bank is refused at seed time, so this failing is the difference
    between "Tier 1 is graded" and "Tier 1 silently went back to being watchable".
    """
    from apps.forge_academy.assessment import validate_item_bank
    from apps.forge_academy.content_loader import load_item_banks

    banks = load_item_banks()
    assert "m01-llm-fundamentals" in banks, "the seeded Tier-1 bank is missing"
    by_step = banks["m01-llm-fundamentals"]
    # The three Tier-1 lesson steps that have no verification test.
    assert set(by_step) == {2, 4, 5}
    for step_num, items in by_step.items():
        assert not validate_item_bank(items), f"step {step_num}: {validate_item_bank(items)}"


def test_the_shipped_banks_never_put_the_answer_first_every_time():
    """A bank whose answer is always option A is graded in name only."""
    from apps.forge_academy.content_loader import load_item_banks

    for by_step in load_item_banks().values():
        for items in by_step.values():
            indices = {i["correct_index"] for i in items}
            assert len(indices) > 1
