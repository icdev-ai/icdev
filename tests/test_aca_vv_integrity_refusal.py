# CUI // SP-CTI
"""aca-vv-01 — the Academy must REFUSE to credit work that was not done.

The reverse-direction suite whose absence let the whole integrity epic exist. Every
other academy test asserts what the system accepts; these assert what it declines,
and they do it over the real HTTP routes rather than by calling functions, because
the defect was always at the trust boundary between the browser and the server.

What the audit found, and what each test now pins:

  * /api/academy/step/submit read `passed = bool(data.get("passed", True))` —
    defaulting to True — then completed the step and paid XP with no check, and
    completed the MISSION on the client's own mission_complete flag. One crafted
    POST per mission produced a certificate that /academy/verify/<token> publicly
    attested to.
  * the coding grader used the test body the CLIENT posted back, and passed any
    script exiting 0 — so a step with no stored test was cleared by `print(1)`.
  * a wrong reflect answer submitted passed=true and paid in full.
  * opening a mission page mutated progress (39 rows in_progress, 352 attempts,
    zero step submissions).
  * hints were counted in the browser and zeroed by navigating away.

These are deliberately black-box: they post what an attacker would post and assert
on the response and the database, so a future refactor that preserves the routes
cannot quietly reopen any of it.
"""
from __future__ import annotations

import pytest
from flask import Flask, g

pytestmark = pytest.mark.usefixtures("_seeded_academy")

LEARNER_EMAIL = "vv01@test.local"


@pytest.fixture(scope="module")
def _seeded_academy():
    from apps.forge_academy import content_loader, db

    db.migrate()
    content_loader.seed_mission_catalog()


@pytest.fixture()
def client(monkeypatch):
    """Academy blueprint on a bare app as an authenticated learner.

    render_template is stubbed for the same reason as test_penta_aca_routes: page
    views need the dashboard's global context, which is a property of the dashboard
    app, not of this blueprint. Every view's real Python still runs — which is the
    part these tests care about.
    """
    import apps.forge_academy.blueprint as bp_mod

    monkeypatch.setattr(
        bp_mod, "render_template", lambda name, **ctx: f"<rendered:{name}>"
    )
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.before_request
    def _set_user():
        g.current_user = {"id": "vv01", "role": "admin", "email": LEARNER_EMAIL}

    app.register_blueprint(bp_mod.bp)
    return app.test_client()


# ---------------------------------------------------------------------------
# Helpers — resolve real seeded rows and read state back
# ---------------------------------------------------------------------------

def _conn():
    from tools.db.storage import get_connection

    return get_connection()


def _learner_id():
    from apps.forge_academy.db import get_or_create_user

    return get_or_create_user(LEARNER_EMAIL, display_name="vv01")["id"]


def _xp():
    row = _conn().execute(
        "SELECT xp FROM fa_users WHERE id=?", (_learner_id(),)
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _step_of_type(step_type: str, *, with_test: bool | None = None):
    """A real seeded step id, or None when the catalogue has none of that shape."""
    sql = (
        "SELECT s.id, s.mission_id FROM fa_mission_steps s WHERE s.step_type=?"
    )
    if with_test is True:
        sql += " AND s.test_code_path IS NOT NULL AND s.test_code_path<>''"
    elif with_test is False:
        sql += " AND (s.test_code_path IS NULL OR s.test_code_path='')"
    row = _conn().execute(sql + " LIMIT 1", (step_type,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _step_status(step_id):
    row = _conn().execute(
        "SELECT status, score FROM fa_step_progress WHERE user_id=? AND step_id=?",
        (_learner_id(), step_id),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _mission_status(mission_id):
    row = _conn().execute(
        "SELECT status, attempts FROM fa_mission_progress WHERE user_id=? AND mission_id=?",
        (_learner_id(), mission_id),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


# ---------------------------------------------------------------------------
# The forged verdict
# ---------------------------------------------------------------------------

def _reset_step(step_id):
    """Clear prior progress for a step so the assertion is about THIS submission.

    The seeded database is module-scoped and survives across runs, so a row left by
    an earlier run — including one written when a grader was still vacuous — would
    otherwise be read as the result of the current attempt.
    """
    c = _conn()
    c.execute(
        "DELETE FROM fa_step_progress WHERE user_id=? AND step_id=?",
        (_learner_id(), step_id),
    )
    c.commit()


def test_a_forged_pass_on_a_coding_step_earns_nothing(client):
    step_id, _ = _step_of_type("coding", with_test=True)
    if step_id is None:
        pytest.skip("no graded coding step in the catalogue")
    _reset_step(step_id)
    before = _xp()
    r = client.post("/api/academy/step/submit", json={
        "step_id": step_id,
        "submission": "# I did not solve this",
        "passed": True,          # the forgery
        "score": 100,
        "base_xp": 99999,
        "mission_complete": True,
    })
    assert r.status_code == 200
    assert r.get_json().get("passed") is not True, "a claimed pass was honoured"
    assert _xp() == before, "XP moved on an unverified submission"
    status, score = _step_status(step_id)
    assert status != "completed"


def test_omitting_passed_does_not_default_to_a_pass(client):
    """`data.get("passed", True)` — the default was the whole bug."""
    step_id, _ = _step_of_type("coding", with_test=True)
    if step_id is None:
        pytest.skip("no graded coding step in the catalogue")
    before = _xp()
    r = client.post("/api/academy/step/submit", json={
        "step_id": step_id, "submission": "print('nope')",
    })
    assert r.get_json().get("passed") is not True
    assert _xp() == before


def test_client_supplied_xp_is_ignored(client):
    """base_xp/mission_xp came from the request; they must come from the row."""
    step_id, _ = _step_of_type("watch")
    if step_id is None:
        pytest.skip("no watch step in the catalogue")
    before = _xp()
    client.post("/api/academy/step/submit", json={
        "step_id": step_id, "submission": "watched",
        "base_xp": 100000, "mission_xp": 100000,
    })
    gained = _xp() - before
    assert gained < 1000, f"client-supplied XP was honoured (+{gained})"


def test_a_client_supplied_test_body_is_ignored(client):
    """The graded party used to supply the test it was graded against."""
    step_id, _ = _step_of_type("coding", with_test=True)
    if step_id is None:
        pytest.skip("no graded coding step in the catalogue")
    r = client.post("/api/academy/code/run", json={
        "step_id": step_id,
        "code": "x = 1",
        "test_code": "assert True  # my own test",
    })
    body = r.get_json()
    assert body.get("passed") is not True, "the client's test was used to grade"


def test_a_coding_step_with_no_stored_test_cannot_be_credited(client):
    """`print(1)` exits 0; without a stored test that used to score 100."""
    step_id, _ = _step_of_type("coding", with_test=False)
    if step_id is None:
        pytest.skip("every coding step now has a stored test")
    before = _xp()
    r = client.post("/api/academy/step/submit", json={
        "step_id": step_id, "submission": "print(1)",
    })
    body = r.get_json()
    assert body.get("passed") is not True
    assert body.get("reason") in ("ungraded_no_test", "tier_locked", "test_failed"), body
    assert _xp() == before


# ---------------------------------------------------------------------------
# Knowledge checks
# ---------------------------------------------------------------------------

def test_a_wrong_reflect_answer_is_recorded_as_wrong(client):
    from apps.forge_academy.grading import _load_step, _reflect_schema

    row = _conn().execute(
        "SELECT id FROM fa_mission_steps WHERE step_type='reflect' "
        "AND config_schema_json LIKE '%correct%' LIMIT 1"
    ).fetchone()
    if not row:
        pytest.skip("no reflect step with an answer key")
    step_id = row[0]
    schema = _reflect_schema(_load_step(step_id))
    options = schema.get("options") or []
    wrong = next(
        (i for i, o in enumerate(options) if isinstance(o, dict) and not o.get("correct")),
        None,
    )
    if wrong is None:
        pytest.skip("no wrong option to choose")

    before = _xp()
    r = client.post("/api/academy/step/submit", json={
        "step_id": step_id, "submission": "reflect", "chosen_option": wrong,
    })
    body = r.get_json()
    assert body.get("passed") is False, "a wrong answer was accepted"
    assert _xp() == before, "a wrong answer was paid"


def test_the_answer_key_is_not_served_to_the_browser(client):
    """data-correct in the DOM made the question pointless."""
    from apps.forge_academy.grading import client_safe_steps

    rows = _conn().execute(
        "SELECT config_schema_json FROM fa_mission_steps WHERE step_type='reflect' "
        "AND config_schema_json LIKE '%correct%' LIMIT 1"
    ).fetchone()
    if not rows:
        pytest.skip("no reflect step with an answer key")
    import json as _json

    schema = _json.loads(rows[0])
    safe = client_safe_steps([{"id": 1, "step_type": "reflect", "config_schema": schema}])
    assert "correct" not in _json.dumps(safe)


# ---------------------------------------------------------------------------
# Reading is not progress
# ---------------------------------------------------------------------------

def test_opening_a_mission_page_does_not_touch_progress(client):
    row = _conn().execute(
        "SELECT m.slug, m.id FROM fa_missions m WHERE m.is_active=1 "
        "AND EXISTS (SELECT 1 FROM fa_mission_steps s WHERE s.mission_id=m.id) LIMIT 1"
    ).fetchone()
    if not row:
        pytest.skip("no mission with steps")
    slug, mission_id = row[0], row[1]
    _learner_id()
    before = _mission_status(mission_id)
    for _ in range(3):
        assert client.get(f"/academy/mission/{slug}").status_code in (200, 302)
    assert _mission_status(mission_id) == before, (
        "viewing a mission page changed progress — 352 phantom attempts came from this"
    )


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

def test_a_certificate_cannot_be_issued_without_the_evidence(client):
    from apps.forge_academy.constants import CERT_TIERS

    if not CERT_TIERS:
        pytest.skip("no certificate tiers defined")
    key = CERT_TIERS[0]["key"]
    r = client.post(f"/api/academy/certificate/{key}/issue")
    assert r.status_code in (403, 400), (
        f"a certificate was issued without meeting its gates: {r.get_json()}"
    )
    issued = _conn().execute(
        "SELECT COUNT(*) FROM fa_certificates WHERE user_id=?", (_learner_id(),)
    ).fetchone()[0]
    assert issued == 0


def test_verifying_an_unknown_token_does_not_vouch_for_it(client):
    r = client.get("/academy/verify/not-a-real-token-000")
    assert r.status_code in (200, 404)
    from apps.forge_academy.db import verify_certificate_token

    assert verify_certificate_token("not-a-real-token-000") is None


# ---------------------------------------------------------------------------
# Hints
# ---------------------------------------------------------------------------

def test_hints_are_counted_by_the_server_not_the_request(client):
    step_id, _ = _step_of_type("coding", with_test=True)
    if step_id is None:
        pytest.skip("no coding step")
    uid = _learner_id()
    # One connection for the delete AND its commit. Using _conn() twice takes two
    # connections, and the first holds the SQLite write lock uncommitted while the
    # second blocks on busy_timeout — the same self-deadlock penta-fix-03 hit in
    # issue_certificate. It hangs rather than fails, which is worse.
    _c = _conn()
    _c.execute(
        "DELETE FROM fa_step_progress WHERE user_id=? AND step_id=?", (uid, step_id)
    )
    _c.commit()
    from apps.forge_academy.db import record_hint

    record_hint(uid, step_id)
    record_hint(uid, step_id)
    # Claiming zero hints must not restore the no-hints bonus.
    r = client.post("/api/academy/step/submit", json={
        "step_id": step_id, "submission": "print(1)", "hints_used": 0,
    })
    assert r.status_code == 200
    stored = _conn().execute(
        "SELECT hints_used FROM fa_step_progress WHERE user_id=? AND step_id=?",
        (uid, step_id),
    ).fetchone()
    assert stored and int(stored[0]) >= 2, "the client's hint count overwrote the server's"


# ---------------------------------------------------------------------------
# Aggregate invariants — every graded step in the catalogue, not a sample
# ---------------------------------------------------------------------------

# Graders the sandbox AST allowlist rejects, so the COMBINED learner+grader script
# cannot run and the step can never be completed.
#
# This set was six. All six graders were pytest modules — `def test_*(tmp_path)` plus
# importlib/subprocess to load the starter from disk — which the runner cannot use
# twice over: the imports are blocked, and plain `python script.py` never calls a
# `def test_*` anyway, so unblocking them would only have made them vacuous. They are
# now written in the runner's idiom, asserting against globals() where the learner's
# own definitions already live. All 49 graded steps now reject a non-solution.
#
# The set is empty, and must stay empty: a grader that lands here is one nobody can
# complete.
_GRADERS_BLOCKED_BY_SANDBOX: set[str] = set()

# A different defect, deliberately NOT hidden in the set above: these two STARTERS
# import ICDEV modules the sandbox forbids (tools.db.storage,
# tools.ai_augmentation.agent_readiness.checker). The grader is fine; the exercise
# itself is designed to run against the real codebase rather than in an isolated
# sandbox, so no grader rewrite can rescue it. Recorded here because it is a real
# content-design conflict that needs an authoring decision, not a silent skip.
_STARTERS_REQUIRING_BLOCKED_IMPORTS = {
    "m-sre-xai-01",                  # starter: from tools.db.storage import ...
    "m-readiness-01-eleven-pillars",  # starter: from tools.ai_augmentation... import ...
}

NON_SOLUTION = "# I did not solve this\n"


def _graded_steps():
    return _conn().execute(
        "SELECT s.id, m.slug, s.step_num FROM fa_mission_steps s "
        "JOIN fa_missions m ON m.id=s.mission_id "
        "WHERE s.step_type='coding' AND s.test_code_path IS NOT NULL "
        "  AND s.test_code_path<>'' ORDER BY m.slug, s.step_num"
    ).fetchall()


def test_no_graded_step_accepts_a_non_solution():
    """The property that matters, over every graded step rather than a sample.

    m01-llm-fundamentals step 1 used to pass this — its grader defined its own
    solution and asserted on its own output, so the Academy's very first exercise
    graded nothing at all.
    """
    from apps.forge_academy.grading import grade_step

    steps = _graded_steps()
    assert steps, "fixture guard: expected graded coding steps"
    vacuous = []
    for sid, slug, num in steps:
        verdict = grade_step(sid, NON_SOLUTION)
        if verdict.get("passed"):
            vacuous.append(f"{slug} step {num}")
    assert not vacuous, (
        f"{len(vacuous)} grader(s) pass a non-solution — the exercise verifies "
        f"nothing: {vacuous}"
    )


def test_no_grader_is_rejected_by_the_sandbox_beyond_the_known_set():
    """A grader the sandbox blocks makes its step permanently uncompletable."""
    from apps.forge_academy.grading import grade_step

    blocked = {
        slug for sid, slug, _num in _graded_steps()
        if grade_step(sid, NON_SOLUTION).get("reason") == "blocked"
    }
    new = sorted(blocked - _GRADERS_BLOCKED_BY_SANDBOX)
    fixed = sorted(_GRADERS_BLOCKED_BY_SANDBOX - blocked)
    assert not new, (
        f"new grader(s) blocked by the sandbox allowlist, so their steps can never "
        f"be completed: {new}"
    )
    assert not fixed, (
        f"these graders now run — remove them from _GRADERS_BLOCKED_BY_SANDBOX: {fixed}"
    )


def test_the_starters_needing_blocked_imports_are_still_the_known_two():
    """A separate defect from a blocked GRADER: a blocked STARTER.

    No grader rewrite can fix these — the exercise is written against the real ICDEV
    codebase and the sandbox forbids those imports by design (penta-aca-02). Pinned so
    the list cannot grow silently and so fixing one is noticed.
    """
    from apps.forge_academy.code_runner import _check_code_safety
    from apps.forge_academy.content_loader import CONTENT_ROOT

    offenders = set()
    for sid, slug, _num in _graded_steps():
        row = _conn().execute(
            "SELECT starter_code_path FROM fa_mission_steps WHERE id=?", (sid,)
        ).fetchone()
        rel = row[0] if row else ""
        if not rel:
            continue
        path = CONTENT_ROOT / rel
        if not path.is_file():
            continue
        ok, _reason = _check_code_safety(path.read_text(encoding="utf-8", errors="replace"))
        if not ok:
            offenders.add(slug)

    new = sorted(offenders - _STARTERS_REQUIRING_BLOCKED_IMPORTS)
    fixed = sorted(_STARTERS_REQUIRING_BLOCKED_IMPORTS - offenders)
    assert not new, (
        f"new starter(s) the sandbox rejects, making the exercise impossible: {new}"
    )
    assert not fixed, (
        f"these starters now pass the gate — remove them from "
        f"_STARTERS_REQUIRING_BLOCKED_IMPORTS: {fixed}"
    )


def test_the_front_door_exercise_actually_grades():
    """m01 step 1 specifically: reject a non-solution AND the untouched starter."""
    from pathlib import Path

    from apps.forge_academy.grading import grade_step

    row = _conn().execute(
        "SELECT s.id FROM fa_mission_steps s JOIN fa_missions m ON m.id=s.mission_id "
        "WHERE m.slug='m01-llm-fundamentals' AND s.step_num=1"
    ).fetchone()
    if not row:
        pytest.skip("m01 step 1 not seeded")
    sid = row[0]
    starter = (
        Path("apps/forge_academy/content/tier1/m01-llm-fundamentals/steps")
        / "step1_starter.py"
    ).read_text(encoding="utf-8")

    assert grade_step(sid, NON_SOLUTION).get("passed") is False
    assert grade_step(sid, starter).get("passed") is False, (
        "submitting the starter unchanged must not pass — the TODOs are the exercise"
    )
    solution = starter + (
        "\nresponse = simulate_llm_call(system_prompt, user_message)\n"
        "print(response['content'])\n"
    )
    assert grade_step(sid, solution).get("passed") is True, (
        "a correct solution must pass — a grader that rejects everything is no better"
    )
    cheat = starter + (
        "\nresponse = {'content': 'whatever', 'model': 'x', "
        "'usage': {'input_tokens': 1, 'output_tokens': 2}}\n"
    )
    assert grade_step(sid, cheat).get("passed") is False, (
        "a hardcoded response must not pass"
    )


# ---------------------------------------------------------------------------
# Unknown input
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {},                                   # no step_id
    {"step_id": 99999999},                # unknown step
    {"step_id": "not-an-int"},            # wrong type
])
def test_malformed_submissions_are_refused_not_credited(client, payload):
    before = _xp()
    r = client.post("/api/academy/step/submit", json=payload)
    assert r.status_code in (200, 400, 404)
    assert r.get_json().get("passed") is not True
    assert _xp() == before
