# CUI // SP-CTI
"""aca-int-07 part 2 — a certificate must cite the work it was issued against.

fa_certificates stored a user, a tier, a label, a token and a timestamp.
check_cert_eligibility computed the gates in full, issue_certificate read only its
boolean, and every detail of which missions and which verified steps satisfied them
was discarded — so /academy/verify/<token> could do nothing but repeat the label
back to whoever was checking it.

The evidence is snapshotted at issue time rather than recomputed on the verify page.
A certificate is a statement about a moment: recomputing lets the claim drift with
the data underneath it, so retiring a mission or re-seeding a step would make a
certificate issued last year quietly describe something else.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest

from _academy_conn import academy_conn

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = REPO_ROOT / "tools" / "db" / "migrations" / "317_fa_certificate_evidence.sql"
TPL = (REPO_ROOT / "tools" / "dashboard" / "templates" / "forge_academy"
       / "cert_verify.html")

SCHEMA = """
CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
    display_name TEXT, role TEXT DEFAULT 'developer', xp INTEGER DEFAULT 0,
    level TEXT DEFAULT 'recruit');
CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT,
    title TEXT, tier INTEGER, is_active INTEGER DEFAULT 1, role_filter TEXT);
CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id INTEGER, step_num INTEGER, title TEXT, step_type TEXT,
    xp_partial INTEGER DEFAULT 50);
CREATE TABLE fa_mission_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, mission_id INTEGER, status TEXT, score INTEGER,
    completed_at TEXT);
CREATE TABLE fa_step_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, step_id INTEGER, status TEXT, score INTEGER,
    hints_used INTEGER DEFAULT 0, completed_at TEXT);
CREATE TABLE fa_certificates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
    cert_tier TEXT, cert_label TEXT, token TEXT, issued_at TEXT);
CREATE TABLE fa_certificate_evidence (id INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_id INTEGER, user_id INTEGER, evidence_type TEXT, ref_id INTEGER,
    label TEXT, detail TEXT, demonstrated_at TEXT, score INTEGER,
    classification TEXT, tenant_id TEXT, created_at TEXT);
CREATE TABLE fa_xp_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
    xp_delta INTEGER, reason TEXT, source_type TEXT, source_id INTEGER,
    is_attendance INTEGER DEFAULT 0, verified INTEGER DEFAULT 1, note TEXT,
    created_at TEXT, classification TEXT, tenant_id TEXT);
INSERT INTO fa_users (id, username, display_name) VALUES (1, 'l@x', 'Learner');
INSERT INTO fa_missions (id, slug, title, tier) VALUES (10, 'm-a', 'LLM Fundamentals', 1);
INSERT INTO fa_mission_steps (id, mission_id, step_num, title, step_type)
    VALUES (91, 10, 1, 'What Is an LLM', 'coding');
INSERT INTO fa_mission_progress (user_id, mission_id, status, score, completed_at)
    VALUES (1, 10, 'completed', 100, '2026-03-04T10:00:00Z');
INSERT INTO fa_step_progress (user_id, step_id, status, score, hints_used, completed_at)
    VALUES (1, 91, 'completed', 100, 0, '2026-03-04T09:55:00Z');
"""

ELIGIBLE = {
    "eligible": True,
    "gates": [{"name": "Tier 1 Complete", "met": True,
               "detail": "1/1 Tier 1 missions completed"}],
}


@pytest.fixture()
def fadb(monkeypatch):
    conn = academy_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    mod = importlib.import_module("apps.forge_academy.db")
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: conn)
    return mod, conn


# --------------------------------------------------------------------------
# what gets collected
# --------------------------------------------------------------------------

def test_the_gates_are_captured_with_their_figures(fadb):
    mod, conn = fadb
    ev = mod.collect_cert_evidence(1, ELIGIBLE, conn)
    gates = [e for e in ev if e["evidence_type"] == "gate"]
    assert len(gates) == 1
    assert gates[0]["label"] == "Tier 1 Complete"
    assert "1/1" in gates[0]["detail"], (
        "a gate without its figures is the same assertion in smaller print"
    )


def test_completed_missions_are_named(fadb):
    mod, conn = fadb
    ev = mod.collect_cert_evidence(1, ELIGIBLE, conn)
    missions = [e for e in ev if e["evidence_type"] == "mission"]
    assert [m["label"] for m in missions] == ["LLM Fundamentals"]
    assert missions[0]["ref_id"] == 10


def test_verified_steps_are_named_under_their_mission(fadb):
    mod, conn = fadb
    ev = mod.collect_cert_evidence(1, ELIGIBLE, conn)
    steps = [e for e in ev if e["evidence_type"] == "step"]
    assert len(steps) == 1
    assert "LLM Fundamentals" in steps[0]["label"]
    assert "What Is an LLM" in steps[0]["label"]
    assert steps[0]["score"] == 100


def test_evidence_carries_when_the_work_happened(fadb):
    """A mission completed in March, cited by a July certificate, is March work."""
    mod, conn = fadb
    ev = mod.collect_cert_evidence(1, ELIGIBLE, conn)
    mission = next(e for e in ev if e["evidence_type"] == "mission")
    assert mission["demonstrated_at"].startswith("2026-03-04")


def test_incomplete_work_is_not_cited(fadb):
    mod, conn = fadb
    conn.execute("UPDATE fa_mission_progress SET status='in_progress'")
    conn.execute("UPDATE fa_step_progress SET status='in_progress'")
    conn.commit()
    ev = mod.collect_cert_evidence(1, ELIGIBLE, conn)
    assert not [e for e in ev if e["evidence_type"] in ("mission", "step")]


# --------------------------------------------------------------------------
# issuance writes it, verification reads it back
# --------------------------------------------------------------------------

def _issue(mod, monkeypatch):
    monkeypatch.setattr(mod, "check_cert_eligibility", lambda uid, key: ELIGIBLE)
    import apps.forge_academy.constants as consts
    monkeypatch.setattr(consts, "CERT_BY_KEY",
                        {"tier1": {"label": "Foundation", "xp_bonus": 0}},
                        raising=False)
    return mod.issue_certificate(1, "tier1")


def test_issuing_records_the_evidence(fadb, monkeypatch):
    mod, conn = fadb
    assert _issue(mod, monkeypatch) is not None
    rows = conn.execute(
        "SELECT evidence_type, COUNT(*) n FROM fa_certificate_evidence "
        "GROUP BY evidence_type").fetchall()
    got = {r["evidence_type"]: r["n"] for r in rows}
    assert got == {"gate": 1, "mission": 1, "step": 1}


def test_the_evidence_is_bound_to_the_certificate_it_justifies(fadb, monkeypatch):
    mod, conn = fadb
    _issue(mod, monkeypatch)
    cert_id = conn.execute("SELECT id FROM fa_certificates").fetchone()["id"]
    orphans = conn.execute(
        "SELECT COUNT(*) n FROM fa_certificate_evidence WHERE cert_id != %s",
        (cert_id,)).fetchone()["n"]
    assert orphans == 0


def test_verifying_a_token_returns_the_evidence(fadb, monkeypatch):
    mod, conn = fadb
    _issue(mod, monkeypatch)
    token = conn.execute("SELECT token FROM fa_certificates").fetchone()["token"]
    result = mod.verify_certificate_token(token)
    assert result is not None
    assert len(result["evidence"]) == 3
    assert {e["evidence_type"] for e in result["evidence"]} == {"gate", "mission", "step"}


def test_verification_reads_the_snapshot_not_the_current_data(fadb, monkeypatch):
    """The point of snapshotting. Retiring a mission must not rewrite a certificate.

    Without this, a certificate issued last year silently starts describing something
    else the moment the catalogue changes underneath it.
    """
    mod, conn = fadb
    _issue(mod, monkeypatch)
    conn.execute("DELETE FROM fa_mission_progress")
    conn.execute("UPDATE fa_missions SET title='Renamed', is_active=0")
    conn.commit()
    token = conn.execute("SELECT token FROM fa_certificates").fetchone()["token"]
    result = mod.verify_certificate_token(token)
    missions = [e for e in result["evidence"] if e["evidence_type"] == "mission"]
    assert [m["label"] for m in missions] == ["LLM Fundamentals"]


def test_an_ineligible_learner_gets_neither_cert_nor_evidence(fadb, monkeypatch):
    mod, conn = fadb
    monkeypatch.setattr(mod, "check_cert_eligibility",
                        lambda uid, key: {"eligible": False, "gates": []})
    assert mod.issue_certificate(1, "tier1") is None
    assert conn.execute(
        "SELECT COUNT(*) n FROM fa_certificate_evidence").fetchone()["n"] == 0


def test_reissuing_does_not_duplicate_the_evidence(fadb, monkeypatch):
    """Issuance is idempotent; the evidence must not accumulate on every call."""
    mod, conn = fadb
    _issue(mod, monkeypatch)
    _issue(mod, monkeypatch)
    assert conn.execute(
        "SELECT COUNT(*) n FROM fa_certificate_evidence").fetchone()["n"] == 3


# --------------------------------------------------------------------------
# the page, the schema, the registration
# --------------------------------------------------------------------------

def test_the_verify_page_renders_the_evidence():
    html = TPL.read_text(encoding="utf-8")
    assert "result.evidence" in html
    for kind in ("gate", "mission", "step"):
        assert f"'{kind}'" in html, f"{kind} evidence is collected but never shown"


def test_a_pre_evidence_certificate_says_so_rather_than_showing_nothing():
    """An empty section reads as 'nothing was required'."""
    html = TPL.read_text(encoding="utf-8")
    assert "issued before evidence was recorded" in html


def test_the_migration_declares_the_table_and_ships_no_backfill():
    """There is no honest way to reconstruct what a past issuance relied on."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS fa_certificate_evidence" in sql
    body = "\n".join(ln for ln in sql.splitlines()
                     if not ln.strip().startswith("--"))
    assert "INSERT" not in body.upper()
    assert not [s for s in body.split("'")[1::2] if ";" in s], (
        "a semicolon inside a literal breaks non-string-aware splitters"
    )


HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"


def _append_only_tables() -> set:
    """The table names `APPEND_ONLY_TABLES` binds in .claude/hooks/pre_tool_use.py.

    Read with `ast`, never with a character window. The first version of this test
    took the 8,000 chars after the FIRST mention of the name, which is the hook's
    module DOCSTRING and not the list, and went red the day the hook's preamble
    grew past that window (d77361d15, 2026-08-12) while the table sat registered
    the whole time (task-det-920b4f1072; same defect, second site). A list read off the AST has no window.
    """
    import ast
    hook = HOOK_PATH.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(hook)):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "APPEND_ONLY_TABLES"
                        for t in node.targets)
                and isinstance(node.value, ast.List)):
            return {e.value for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    raise AssertionError("pre_tool_use.py binds no APPEND_ONLY_TABLES list literal")


def test_the_table_is_registered_append_only():
    assert "fa_certificate_evidence" in _append_only_tables()


def test_the_table_is_in_the_shared_test_schema():
    conftest = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS fa_certificate_evidence" in conftest
