# CUI // SP-CTI
"""fa_step_assessment_policy must be readable inside an authenticated request.

Found 2026-10-06 by a perf sweep: every per-step policy lookup made from the
dashboard raised ``UndefinedColumn: column "classification"``. Inside a Flask
request ``get_connection`` attaches the request's SecurityContext, and the
row-security injector rewrites every SELECT/UPDATE into
``... WHERE (classification IS NULL OR ...)``. ``fa_step_assessment_policy`` is the
ONE fa_* table without that column (measured on the live board: 31 of 32 fa_*
tables carry it), so ``_policy_row`` caught the error, logged a warning, and
returned ``{}`` -- every step silently resolved to practice defaults and any
summative cap, attempt limit or pass-threshold override stored for it was ignored.
``set_step_policy`` raised outright for the same reason.

The fixture carries a SecurityContext exactly as a request does, over the
canonical DDL from ``tools/db/schema/pg_consolidated.sql`` (SQLite-typed).
"""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

from _academy_conn import academy_conn

# Canonical: pg_consolidated.sql ``CREATE TABLE public.fa_mission_steps``.
FA_MISSION_STEPS = """
CREATE TABLE fa_mission_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id integer NOT NULL,
    step_num integer NOT NULL,
    title text NOT NULL,
    step_type text DEFAULT 'coding' NOT NULL,
    content_path text,
    starter_code_path text,
    test_code_path text,
    config_schema_json text DEFAULT '{}',
    xp_partial integer DEFAULT 50 NOT NULL,
    skill_tag text,
    hint_allowed integer DEFAULT 1 NOT NULL,
    estimated_seconds integer DEFAULT 180,
    ontology_id text DEFAULT '',
    domain_classes_json text DEFAULT '[]',
    classification varchar(50) DEFAULT 'CUI'
)
"""

# Canonical: pg_consolidated.sql ``CREATE TABLE public.fa_assessment_items``.
FA_ASSESSMENT_ITEMS = """
CREATE TABLE fa_assessment_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id integer NOT NULL,
    item_key text NOT NULL,
    prompt text NOT NULL,
    options_json text DEFAULT '[]' NOT NULL,
    correct_index integer DEFAULT 0 NOT NULL,
    explanation text,
    difficulty text DEFAULT 'core',
    is_active integer DEFAULT 1 NOT NULL,
    classification text DEFAULT 'CUI',
    tenant_id text,
    created_at text
)
"""

# Canonical: pg_consolidated.sql ``CREATE TABLE public.fa_step_assessment_policy``
# plus its ``fa_step_assessment_policy_step_id_key UNIQUE (step_id)`` constraint.
# NOTE: no classification column -- that absence is the subject of this file.
FA_STEP_ASSESSMENT_POLICY = """
CREATE TABLE fa_step_assessment_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id integer NOT NULL UNIQUE,
    policy text DEFAULT 'practice' NOT NULL,
    items_per_attempt integer,
    pass_threshold_pct integer,
    max_attempts integer,
    updated_at text,
    created_at text
)
"""

STEP_ID = 10

#: What ``g.security_context`` looks like to the injector: a CUI clearance and no
#: tenant (the dashboard's default), which is enough to add the classification
#: predicate to every statement.
REQUEST_CTX = SimpleNamespace(tenant_id=None, classification="CUI",
                              compartments=frozenset(), role="")


@pytest.fixture()
def fa(monkeypatch):
    conn = academy_conn()
    for ddl in (FA_MISSION_STEPS, FA_ASSESSMENT_ITEMS, FA_STEP_ASSESSMENT_POLICY):
        conn.execute(ddl)
    conn.execute(
        "INSERT INTO fa_mission_steps (id, mission_id, step_num, title, step_type) "
        "VALUES (%s, 1, 1, 'Context Window Limits', 'watch')",
        (STEP_ID,),
    )
    # A five-item bank makes the step GRADED, so it may legitimately be summative.
    for n in range(5):
        conn.execute(
            "INSERT INTO fa_assessment_items "
            "(step_id, item_key, prompt, options_json, correct_index) "
            "VALUES (%s,%s,%s,%s,%s)",
            (STEP_ID, f"q{n}", f"Question {n}?", json.dumps(["a", "b", "c"]), 0),
        )
    conn.commit()
    # Attached AFTER seeding, as a request would: every statement from here on is
    # rewritten by the row-security injector.
    conn.set_security_context(REQUEST_CTX)

    dbmod = importlib.import_module("apps.forge_academy.db")
    monkeypatch.setattr(dbmod, "get_connection", lambda *a, **k: conn)
    assessment = importlib.import_module("apps.forge_academy.assessment")
    try:
        yield assessment, conn
    finally:
        conn.close()


def _seed_policy(conn, **cols):
    """Write a policy row with RLS off -- the seeder path, not the code under test."""
    prior = conn._security_context
    conn.set_security_context(None)
    try:
        keys = ", ".join(["step_id", *cols])
        marks = ", ".join(["%s"] * (len(cols) + 1))
        conn.execute(
            f"INSERT INTO fa_step_assessment_policy ({keys}) VALUES ({marks})",
            (STEP_ID, *cols.values()),
        )
        conn.commit()
    finally:
        conn.set_security_context(prior)


def test_stored_summative_policy_is_returned_inside_a_request(fa):
    assessment, conn = fa
    _seed_policy(conn, policy="summative", max_attempts=2, items_per_attempt=4,
                 pass_threshold_pct=85)

    resolved = assessment.step_policy({"id": STEP_ID, "step_type": "watch"})

    assert resolved == {
        "policy": "summative",
        "max_attempts": 2,
        "items_per_attempt": 4,
        "pass_threshold_pct": 85,
    }
    assert assessment.pass_threshold_for({"id": STEP_ID, "step_type": "watch"}) == 85


def test_set_step_policy_writes_and_rewrites_inside_a_request(fa):
    assessment, conn = fa

    assert assessment.set_step_policy(STEP_ID, "summative", max_attempts=3) is True
    assert assessment.set_step_policy(STEP_ID, "summative", max_attempts=5,
                                      pass_threshold_pct=90) is True

    resolved = assessment.step_policy({"id": STEP_ID, "step_type": "watch"})
    assert resolved["policy"] == "summative"
    assert resolved["max_attempts"] == 5
    assert resolved["pass_threshold_pct"] == 90


def test_the_lookup_does_not_strip_row_security_from_the_shared_connection(fa):
    """The bypass is scoped to the policy statement, not leaked to later callers.

    Inside a request the academy hands one pooled connection to successive
    helpers; a policy read that left it with no SecurityContext would turn off
    row security for every fa_* query after it.
    """
    assessment, conn = fa
    _seed_policy(conn, policy="practice", pass_threshold_pct=70)

    assessment.step_policy({"id": STEP_ID, "step_type": "watch"})

    assert conn._security_context is REQUEST_CTX
