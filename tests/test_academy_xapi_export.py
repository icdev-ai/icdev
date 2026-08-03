# CUI // SP-CTI
"""aca-trn-05 — Academy completions leave the platform as xAPI, or not at all.

The export exists so Academy results can count as training of record. That makes
the interesting claims not "does it emit JSON" but "what does it refuse to emit":
a completion with no provenance row is exactly the thing that must not reach a
system of record wearing the same shape as a graded one.

Each test below is one of those claims.
"""
from __future__ import annotations

import importlib

import pytest

from _academy_conn import academy_conn

SCHEMA = """
CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
                       display_name TEXT, email TEXT, tenant_id TEXT);
CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
                          title TEXT, tagline TEXT, tier INTEGER DEFAULT 1);
CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id INTEGER,
                               step_num INTEGER, title TEXT, step_type TEXT,
                               skill_tag TEXT);
CREATE TABLE fa_step_progress (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                               step_id INTEGER, status TEXT, score INTEGER DEFAULT 0,
                               hints_used INTEGER DEFAULT 0, started_at TEXT,
                               completed_at TEXT);
CREATE TABLE fa_mission_progress (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                                  mission_id INTEGER, status TEXT, score INTEGER DEFAULT 0,
                                  xp_earned INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
                                  started_at TEXT, completed_at TEXT);
CREATE TABLE fa_xp_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                           xp_delta INTEGER, reason TEXT, source_type TEXT,
                           source_id INTEGER, is_attendance INTEGER DEFAULT 0,
                           verified INTEGER DEFAULT 1, note TEXT, created_at TEXT);
CREATE TABLE fa_certificates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                              cert_tier TEXT, cert_label TEXT, token TEXT,
                              issued_at TEXT, expires_at TEXT);
CREATE TABLE fa_certificate_evidence (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                      cert_id INTEGER, user_id INTEGER,
                                      evidence_type TEXT, ref_id INTEGER, label TEXT,
                                      demonstrated_at TEXT, score INTEGER);

INSERT INTO fa_users (id, username, display_name, email)
VALUES (1, 'learner', 'A Learner', 'learner@example.mil');

INSERT INTO fa_missions (id, slug, title, tagline, tier)
VALUES (1, 'm-t1-01-prompting', 'Prompt Engineering', 'Say it precisely', 1);

INSERT INTO fa_mission_steps (id, mission_id, step_num, title, step_type, skill_tag)
VALUES (10, 1, 1, 'Write a graded prompt', 'coding', 'prompting');
-- Second step exists so the "unverified" case is a DIFFERENT record rather than
-- the same one toggled: the export must be able to emit one and withhold the other
-- in a single pass.
INSERT INTO fa_mission_steps (id, mission_id, step_num, title, step_type, skill_tag)
VALUES (11, 1, 2, 'Reconstructed from pre-ledger history', 'coding', 'prompting');

-- Graded server-side after aca-int-01: ledger row, verified.
INSERT INTO fa_step_progress (user_id, step_id, status, score, hints_used, started_at, completed_at)
VALUES (1, 10, 'completed', 90, 1, '2026-03-01 10:00:00', '2026-03-01 10:02:00');
INSERT INTO fa_xp_ledger (id, user_id, xp_delta, reason, source_type, source_id, verified)
VALUES (100, 1, 50, 'step_pass', 'step', 10, 1);

-- The migration-315 backfill shape: a real completion whose award amount could
-- only be reconstructed, so verified=0.
INSERT INTO fa_step_progress (user_id, step_id, status, score, completed_at)
VALUES (1, 11, 'completed', 100, '2026-03-02 10:00:00');
INSERT INTO fa_xp_ledger (id, user_id, xp_delta, reason, source_type, source_id, verified)
VALUES (101, 1, 50, 'step_pass', 'step', 11, 0);

INSERT INTO fa_mission_progress (user_id, mission_id, status, score, xp_earned, attempts,
                                 started_at, completed_at)
VALUES (1, 1, 'completed', 95, 200, 2, '2026-03-01 09:00:00', '2026-03-02 10:05:00');
INSERT INTO fa_xp_ledger (id, user_id, xp_delta, reason, source_type, source_id, verified)
VALUES (102, 1, 200, 'mission_complete', 'mission', 1, 1);

-- Two certificates: one with evidence behind it, one without.
INSERT INTO fa_certificates (id, user_id, cert_tier, cert_label, token, issued_at)
VALUES (1, 1, 'foundation', 'FORGE Foundation', 'tok-evidenced', '2026-03-03 12:00:00');
INSERT INTO fa_certificate_evidence (cert_id, user_id, evidence_type, ref_id, label,
                                     demonstrated_at, score)
VALUES (1, 1, 'mission', 1, 'Prompt Engineering', '2026-03-02 10:05:00', 95);
INSERT INTO fa_certificates (id, user_id, cert_tier, cert_label, token, issued_at)
VALUES (2, 1, 'practitioner', 'FORGE Practitioner', 'tok-bare', '2026-03-04 12:00:00');
"""


@pytest.fixture()
def xapi(monkeypatch):
    conn = academy_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    mod = importlib.import_module("apps.forge_academy.xapi")
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: conn)
    monkeypatch.setenv("ICDEV_XAPI_ACTIVITY_BASE", "https://icdev.test/xapi/academy")
    try:
        yield mod, conn
    finally:
        conn.close()


def _by_verb(result, verb_suffix):
    return [s for s in result["statements"] if s["verb"]["id"].endswith(verb_suffix)]


def test_verified_step_becomes_a_passed_statement(xapi):
    mod, _ = xapi
    passed = _by_verb(mod.build_statements(), "/passed")

    assert len(passed) == 1, "only the ledger-backed step may be exported"
    stmt = passed[0]
    assert stmt["object"]["id"] == "https://icdev.test/xapi/academy/mission/m-t1-01-prompting/step/1"
    assert stmt["object"]["definition"]["type"] == mod.ACTIVITY_ASSESSMENT
    assert stmt["actor"]["mbox"] == "mailto:learner@example.mil"
    assert stmt["result"]["success"] is True
    assert stmt["result"]["score"] == {"raw": 90.0, "min": 0.0, "max": 100.0, "scaled": 0.9}
    # 10:00:00 → 10:02:00 is two minutes of time on task, not a guess.
    assert stmt["result"]["duration"] == "PT120S"
    # The statement is timestamped when the work was FINISHED, not when it began.
    assert stmt["timestamp"] == "2026-03-01T10:02:00Z"
    assert stmt["version"] == "1.0.3"
    parent = stmt["context"]["contextActivities"]["parent"][0]
    assert parent["id"] == "https://icdev.test/xapi/academy/mission/m-t1-01-prompting"


def test_unverified_step_is_withheld_and_counted(xapi):
    mod, _ = xapi
    result = mod.build_statements()

    ids = [s["object"]["id"] for s in result["statements"]]
    assert not any(i.endswith("/step/2") for i in ids)
    assert result["excluded"]["unverified_step"] == 1
    assert result["counts"]["step"] == 1


def test_include_unverified_emits_it_flagged_rather_than_silently(xapi):
    mod, _ = xapi
    result = mod.build_statements(include_unverified=True)

    step2 = [s for s in result["statements"] if s["object"]["id"].endswith("/step/2")]
    assert len(step2) == 1
    ext = step2[0]["context"]["extensions"][
        "https://icdev.test/xapi/academy/extensions/provenance"
    ]
    assert ext["verified"] is False
    assert ext["source"] == "fa_xp_ledger"
    assert result["excluded"]["unverified_step"] == 0


def test_certificate_without_evidence_is_not_exported(xapi):
    mod, _ = xapi
    result = mod.build_statements()

    earned = _by_verb(result, "/earned")
    assert len(earned) == 1
    ext = earned[0]["context"]["extensions"][
        "https://icdev.test/xapi/academy/extensions/provenance"
    ]
    assert ext["certificate_token"] == "tok-evidenced"
    assert ext["evidence_rows"] == 1
    assert result["excluded"]["unverified_certificate"] == 1


def test_mission_completion_is_a_completed_statement(xapi):
    mod, _ = xapi
    completed = _by_verb(mod.build_statements(), "/completed")

    assert len(completed) == 1
    assert completed[0]["object"]["definition"]["type"] == mod.ACTIVITY_COURSE
    assert completed[0]["result"]["score"]["raw"] == 95.0


def test_statement_ids_are_stable_so_re_export_is_idempotent(xapi):
    mod, _ = xapi
    first = {s["id"] for s in mod.build_statements()["statements"]}
    second = {s["id"] for s in mod.build_statements()["statements"]}

    assert first == second
    assert len(first) == 3, "ids must also be unique within one export"


def test_statements_share_one_registration_per_mission(xapi):
    mod, _ = xapi
    regs = {
        s["context"]["registration"]
        for s in mod.build_statements()["statements"]
        if "registration" in s.get("context", {})
    }
    assert len(regs) == 1, "the step and its mission belong to the same run"


def test_since_filters_by_completion_time(xapi):
    mod, _ = xapi
    result = mod.build_statements(since="2026-03-03T00:00:00Z")

    assert result["counts"] == {"step": 0, "mission": 0, "certificate": 1, "learners": 1}


def test_learner_without_email_gets_an_account_not_a_fabricated_mbox(xapi):
    mod, conn = xapi
    conn.execute(
        "INSERT INTO fa_users (id, username, display_name, email) VALUES (2, 'nomail', 'No Mail', NULL)"
    )
    conn.execute(
        "INSERT INTO fa_step_progress (user_id, step_id, status, score, completed_at) "
        "VALUES (2, 10, 'completed', 80, '2026-03-05 10:00:00')"
    )
    conn.execute(
        "INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, source_type, source_id, verified) "
        "VALUES (2, 50, 'step_pass', 'step', 10, 1)"
    )
    conn.commit()

    actors = [s["actor"] for s in mod.build_statements(user_id=2)["statements"]]
    assert actors, "the learner has a verified completion and must be exported"
    for actor in actors:
        assert "mbox" not in actor
        assert actor["account"] == {
            "homePage": "https://icdev.test/xapi/academy",
            "name": "nomail",
        }


def test_learner_with_no_identifier_at_all_is_excluded_not_anonymised(xapi):
    mod, conn = xapi
    conn.execute("INSERT INTO fa_users (id, username, display_name, email) "
                 "VALUES (3, NULL, NULL, NULL)")
    conn.commit()

    result = mod.build_statements(user_id=3)
    assert result["statements"] == []
    assert result["excluded"]["unidentifiable_actor"] == 1
