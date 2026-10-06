# CUI // SP-CTI
"""aca-presenter-preview: demo a locked tier without unlocking it, and without credit.

aca-ux-04 made a locked mission readable and runnable but refused every submit with
``status: "locked"`` — so a presenter who had not cleared Tier 1 could not walk a
Tier 2/3 mission past its first graded step. The org-leadership tier (the same
admin/pm/isso predicate as ``require_org_intel``) now gets the REAL verdict with
``status: "preview"`` and ``recorded: false``, and nothing is written: no step or
mission progress, no XP ledger row, no achievement, and not even the closing of an
item-bank attempt.

Everyone else still gets ``locked``, and an unlocked tier still records normally.

Black-box over the real routes, against a throwaway SQLite database built by the
academy's own ``migrate()`` + ``seed_mission_catalog()`` — no fixture DDL, so the
tables are the ones production creates.
"""
from __future__ import annotations

import json

import pytest
from flask import Flask, g

PRESENTER = "presenter@test.local"
LEARNER = "learner@test.local"
CORRECT = 1  # authored index of the right option on the reflect steps below


@pytest.fixture(scope="module")
def academy_db(tmp_path_factory):
    """Seed a private academy database once; tests point the env at it."""
    path = tmp_path_factory.mktemp("aca-presenter-preview") / "academy.db"
    mp = pytest.MonkeyPatch()
    mp.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    mp.setenv("ICDEV_DB_PATH", str(path))
    try:
        from apps.forge_academy import content_loader, db
        from tools.db.storage import get_connection

        db.migrate()
        content_loader.seed_mission_catalog()

        conn = get_connection()
        ids = {}
        for tier in (1, 2):
            row = conn.execute(
                "SELECT m.id, m.slug FROM fa_missions m WHERE m.tier=? AND m.is_active=1 "
                "AND EXISTS (SELECT 1 FROM fa_mission_steps s WHERE s.mission_id=m.id) "
                "ORDER BY m.id LIMIT 1",
                (tier,),
            ).fetchone()
            assert row, f"seeded catalogue has no tier-{tier} mission"
            mission_id, slug = row[0], row[1]
            schema = json.dumps({
                "question": "Which option is right?",
                "options": [
                    {"text": "wrong"}, {"text": "right", "correct": True},
                    {"text": "also wrong"},
                ],
                "explanation": "because right is right",
            })
            cur = conn.execute(
                "INSERT INTO fa_mission_steps (mission_id, step_num, title, step_type, "
                "config_schema_json, xp_partial) VALUES (?,?,?,?,?,?)",
                (mission_id, 900 + tier, f"preview reflect t{tier}", "reflect",
                 schema, 50),
            )
            ids[f"t{tier}_reflect"] = cur.lastrowid
            ids[f"t{tier}_slug"] = slug

        # A tier-2 step graded against an item bank, to prove the preview leaves
        # the attempt OPEN rather than spending it.
        t2_mission = conn.execute(
            "SELECT mission_id FROM fa_mission_steps WHERE id=?", (ids["t2_reflect"],)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO fa_mission_steps (mission_id, step_num, title, step_type, "
            "xp_partial) VALUES (?,?,?,?,?)",
            (t2_mission, 950, "preview item bank", "watch", 50),
        )
        ids["t2_items"] = cur.lastrowid
        for key in ("q1", "q2"):
            conn.execute(
                "INSERT INTO fa_assessment_items "
                "(step_id, item_key, prompt, options_json, correct_index, explanation) "
                "VALUES (?,?,?,?,?,?)",
                (ids["t2_items"], key, f"prompt {key}", json.dumps(["a", "b", "c"]),
                 2, f"because {key}"),
            )
        conn.commit()
        conn.close()
        yield {"path": str(path), **ids}
    finally:
        mp.undo()


def _client(monkeypatch, academy_db, *, role, email, captured=None):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", academy_db["path"])
    import apps.forge_academy.blueprint as bp_mod

    def _render(name, **ctx):
        if captured is not None:
            captured.update(ctx)
        return f"<rendered:{name}>"

    # Page views need the dashboard's global template context; the route's own
    # Python still runs, which is what these tests assert on.
    monkeypatch.setattr(bp_mod, "render_template", _render)
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _set_user():
        g.current_user = {"id": email, "role": role, "email": email}

    app.register_blueprint(bp_mod.bp)
    return app.test_client()


def _user_id(email):
    from apps.forge_academy.db import get_or_create_user

    return get_or_create_user(email, display_name=email.split("@")[0])["id"]


def _counts(email):
    """Every row the credit path writes for this learner."""
    from tools.db.storage import get_connection

    uid = _user_id(email)
    conn = get_connection()
    try:
        out = {}
        for table in ("fa_step_progress", "fa_mission_progress", "fa_xp_ledger",
                      "fa_user_achievements"):
            out[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (uid,)
            ).fetchone()[0]
        out["xp"] = conn.execute(
            "SELECT xp FROM fa_users WHERE id=?", (uid,)
        ).fetchone()[0] or 0
        out["closed_attempts"] = conn.execute(
            "SELECT COUNT(*) FROM fa_step_attempts WHERE user_id=? "
            "AND closed_at IS NOT NULL", (uid,)
        ).fetchone()[0]
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The shared predicate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role,expected", [
    ("admin", True), ("pm", True), ("isso", True),
    ("developer", False), ("", False),
])
def test_preview_uses_the_org_intel_predicate(role, expected):
    from apps.forge_academy.auth import is_org_intel_user

    assert is_org_intel_user({"role": role}) is expected


# ---------------------------------------------------------------------------
# Presenter on a locked tier: real verdict, nothing recorded
# ---------------------------------------------------------------------------

def test_presenter_gets_the_real_verdict_and_nothing_is_recorded(monkeypatch, academy_db):
    client = _client(monkeypatch, academy_db, role="admin", email=PRESENTER)
    step = academy_db["t2_reflect"]
    before = _counts(PRESENTER)

    right = client.post("/api/academy/step/submit",
                        json={"step_id": step, "chosen_option": CORRECT}).get_json()
    assert right["status"] == "preview"
    assert right["recorded"] is False
    assert right["passed"] is True
    assert right["correct_option"] == CORRECT
    assert right["explanation"] == "because right is right"
    assert "xp_event" not in right and "mission_xp" not in right

    wrong = client.post("/api/academy/step/submit",
                        json={"step_id": step, "chosen_option": 0}).get_json()
    assert wrong["status"] == "preview"
    assert wrong["passed"] is False, "the preview must report the REAL verdict"
    assert wrong["correct_option"] == CORRECT

    assert _counts(PRESENTER) == before, "a preview wrote progress, XP or achievements"


def test_presenter_preview_does_not_spend_an_item_bank_attempt(monkeypatch, academy_db):
    from apps.forge_academy.assessment import open_attempt

    client = _client(monkeypatch, academy_db, role="pm", email=PRESENTER)
    step = academy_db["t2_items"]
    served = open_attempt(_user_id(PRESENTER), step)  # what the page render does
    assert served and served["items"]
    # The DISPLAYED index of authored option 2 ("c") for each served item.
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT served_json FROM fa_step_attempts WHERE user_id=? AND step_id=? "
            "AND closed_at IS NULL", (_user_id(PRESENTER), step),
        ).fetchone()
    finally:
        conn.close()
    answers = {e["item_key"]: e["option_order"].index(2) for e in json.loads(row[0])}
    before = _counts(PRESENTER)

    d = client.post("/api/academy/step/submit",
                    json={"step_id": step, "answers": answers}).get_json()
    assert d["status"] == "preview" and d["recorded"] is False
    assert d["passed"] is True
    assert d["correct"] == d["total"] == len(answers)
    assert _counts(PRESENTER) == before, "the preview closed (spent) an attempt"


def test_presenter_sees_the_preview_flag_on_a_locked_mission(monkeypatch, academy_db):
    captured = {}
    client = _client(monkeypatch, academy_db, role="isso", email=PRESENTER,
                     captured=captured)
    r = client.get(f"/academy/mission/{academy_db['t2_slug']}")
    assert r.status_code == 200
    assert captured["tier_locked"] is True
    assert captured["presenter_preview"] is True


# ---------------------------------------------------------------------------
# Everyone else: unchanged
# ---------------------------------------------------------------------------

def test_non_privileged_learner_is_still_locked(monkeypatch, academy_db):
    client = _client(monkeypatch, academy_db, role="developer", email=LEARNER)
    before = _counts(LEARNER)
    d = client.post("/api/academy/step/submit",
                    json={"step_id": academy_db["t2_reflect"],
                          "chosen_option": CORRECT}).get_json()
    assert d["status"] == "locked"
    assert d["reason"] == "tier_locked"
    assert d["passed"] is False
    assert "recorded" not in d
    assert _counts(LEARNER) == before


def test_non_privileged_learner_gets_no_preview_flag(monkeypatch, academy_db):
    captured = {}
    client = _client(monkeypatch, academy_db, role="developer", email=LEARNER,
                     captured=captured)
    client.get(f"/academy/mission/{academy_db['t2_slug']}")
    assert captured["tier_locked"] is True
    assert captured["presenter_preview"] is False


def test_presenter_on_an_unlocked_tier_records_normally(monkeypatch, academy_db):
    client = _client(monkeypatch, academy_db, role="admin", email=PRESENTER)
    before = _counts(PRESENTER)
    d = client.post("/api/academy/step/submit",
                    json={"step_id": academy_db["t1_reflect"],
                          "chosen_option": CORRECT}).get_json()
    assert d["status"] != "preview" and "recorded" not in d
    assert d["passed"] is True
    assert d.get("xp_event"), "an unlocked pass must still pay XP"
    after = _counts(PRESENTER)
    assert after["fa_step_progress"] == before["fa_step_progress"] + 1
    assert after["fa_xp_ledger"] > before["fa_xp_ledger"]
    assert after["xp"] > before["xp"]


def test_the_page_words_the_preview_and_every_caller_handles_it():
    """The template must say what the preview is and must never claim XP for it."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    base = root / "tools" / "dashboard" / "templates" / "forge_academy"
    html = (base / "mission.html").read_text(encoding="utf-8")
    assert "presenter_preview|default(false)" in html and "FA_PREVIEW" in html
    assert ("answers are\n      graded and shown, nothing is recorded and no XP is "
            "awarded") in html
    assert "d.status === 'preview'" in html
    configure = (base / "partials" / "_step_configure.html").read_text(encoding="utf-8")
    assert "preview" in configure
