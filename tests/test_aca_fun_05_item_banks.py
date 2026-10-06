# CUI // SP-CTI
"""aicur-fun-05 — m03 (RAG) and m04 (agents) are graded, not acknowledged.

Only m01 shipped an item bank, so every RAG/agent lesson beyond the two step-1 labs
had nothing to grade against. These tests seed the AUTHORED banks onto the steps
discovery actually produces, through the real seeder, and assert the verdict each
step reports.

The step-1 labs are asserted bank-free on purpose: grading.grade_step routes any
step with an active bank to the item path, so a bank there would silently replace
the lab's own test with a quiz.
"""
from __future__ import annotations

import importlib
import re

import pytest

from _academy_conn import academy_conn

MISSIONS = ("m03-rag-basics", "m04-first-agent")
LESSON_STEPS = {2, 3}

SCHEMA = """
CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE);
CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id INTEGER,
                               step_num INTEGER, step_type TEXT, test_code_path TEXT);
CREATE TABLE fa_assessment_items (id INTEGER PRIMARY KEY AUTOINCREMENT, step_id INTEGER,
                                  item_key TEXT, prompt TEXT,
                                  options_json TEXT DEFAULT '[]',
                                  correct_index INTEGER DEFAULT 0, explanation TEXT,
                                  difficulty TEXT DEFAULT 'core',
                                  is_active INTEGER DEFAULT 1,
                                  UNIQUE(step_id, item_key));
"""


@pytest.fixture()
def seeded(monkeypatch):
    """The discovered m03/m04 steps in one in-memory schema, banks seeded."""
    loader = importlib.import_module("apps.forge_academy.content_loader")
    dbmod = importlib.import_module("apps.forge_academy.db")
    assessment = importlib.import_module("apps.forge_academy.assessment")

    conn = academy_conn()
    conn.executescript(SCHEMA)
    discovered = loader.discover_steps()
    steps = {}
    for slug in MISSIONS:
        cur = conn.execute("INSERT INTO fa_missions (slug) VALUES (%s)", (slug,))
        mission_id = cur.lastrowid
        for st in discovered[slug]:
            cur = conn.execute(
                "INSERT INTO fa_mission_steps (mission_id, step_num, step_type, "
                "test_code_path) VALUES (%s,%s,%s,%s)",
                (mission_id, st["step_num"], st["step_type"], st["test_code_path"]),
            )
            steps[(slug, st["step_num"])] = {
                "id": cur.lastrowid, "step_type": st["step_type"],
                "test_code_path": st["test_code_path"],
            }
    conn.commit()
    monkeypatch.setattr(dbmod, "get_connection", lambda *a, **k: conn)
    written = loader.seed_item_banks(conn)
    try:
        yield assessment, steps, written
    finally:
        conn.close()


def test_both_banks_pass_the_seeders_validation():
    from apps.forge_academy.assessment import validate_item_bank
    from apps.forge_academy.content_loader import load_item_banks

    banks = load_item_banks()
    for slug in MISSIONS:
        assert slug in banks, f"{slug} has no item bank"
        assert set(banks[slug]) == LESSON_STEPS
        for step_num, items in banks[slug].items():
            assert not validate_item_bank(items), (slug, step_num, validate_item_bank(items))


def test_every_m03_m04_step_reports_a_graded_verdict(seeded):
    assessment, steps, written = seeded
    assert written > 0, "the seeder refused or skipped the banks"
    for slug in MISSIONS:
        nums = {n for s, n in steps if s == slug}
        assert nums == {1} | LESSON_STEPS, f"{slug} discovered steps {nums}"
        for num in nums:
            step = steps[(slug, num)]
            assert assessment.classify_step(step) == "graded", (slug, num, step)


def test_the_lessons_are_graded_by_their_bank(seeded):
    assessment, steps, _ = seeded
    for slug in MISSIONS:
        for num in LESSON_STEPS:
            step = steps[(slug, num)]
            # A lesson: no lab test, so ONLY the bank can make it graded.
            assert step["step_type"] == "watch" and not step["test_code_path"]
            assert len(assessment.get_item_bank(step["id"])) >= 5


def test_the_step1_labs_keep_their_code_grading(seeded):
    assessment, steps, _ = seeded
    for slug in MISSIONS:
        step = steps[(slug, 1)]
        assert step["step_type"] == "coding" and step["test_code_path"]
        assert not assessment.has_item_bank(step["id"]), (
            f"{slug} step 1 has a bank, which would replace its lab test")


def test_every_lesson_is_dated():
    """Platform invariant: facts are dated in the lesson ("as of <month year>")."""
    from apps.forge_academy.content_loader import discover_steps, CONTENT_ROOT

    found = discover_steps()
    for slug in MISSIONS:
        for st in found[slug]:
            if st["step_num"] in LESSON_STEPS:
                text = (CONTENT_ROOT / st["content_path"]).read_text(encoding="utf-8")
                assert re.search(r"as of [A-Z][a-z]+ \d{4}", text), st["content_path"]
