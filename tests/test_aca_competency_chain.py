# CUI // SP-CTI
"""aca-trn-02 — the competency evidence chain, end to end.

fa_mission_ontology, fa_step_ontology and fa_user_competencies all existed and
all three were EMPTY in production. Three independent defects kept them that
way, and every one of them was invisible:

  1. seed_mission_ontology_mappings() ran inside migrate(), which executes
     BEFORE seed_mission_catalog(). fa_missions was empty, nothing was mapped,
     and the "already seeded" count guard then read its own zero-work run as
     proof the job was finished.
  2. _create_kg_competency_edge wrote kg_edges.label. The platform column is
     named `relationship`, so on PostgreSQL every call raised UndefinedColumn
     and ABORTED the transaction — inside `except Exception: pass`.
  3. The platform KG DDL declares kg_nodes.graph_id / kg_edges.graph_id as
     REFERENCES kg_graphs(id), and `icdev-core-ontology` is created only by the
     ontology federation pass. Where that key is materialised and federation
     never ran, a corrected column name still failed on the foreign key.
     (Production PG on 2026-08-02 had the tables but not this key, so there
     defect 2 alone was fatal — the chain must work either way.)

The tests below fail on the code as it stood. Several assert against the REAL
platform DDL rather than a copy of it, because a test that restates the schema
it is checking would have passed happily through defect 2.
"""
from __future__ import annotations

import inspect
import json
import re
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "tools" / "dashboard" / "templates" / "forge_academy"


def fadb_tier_class(tier: int) -> str:
    from apps.forge_academy.ontology import get_tier_competency
    return get_tier_competency(tier)

ACADEMY_SCHEMA = """
CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
  display_name TEXT, tenant_id TEXT);
CREATE TABLE fa_missions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE, title TEXT,
  tier INTEGER NOT NULL DEFAULT 1, topic TEXT, mission_type TEXT DEFAULT 'coding',
  is_active INTEGER DEFAULT 1, status TEXT DEFAULT 'active');
CREATE TABLE fa_mission_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id INTEGER NOT NULL,
  step_num INTEGER NOT NULL, title TEXT, step_type TEXT DEFAULT 'coding');
CREATE TABLE fa_mission_progress (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  mission_id INTEGER NOT NULL, status TEXT DEFAULT 'not_started',
  score INTEGER DEFAULT 0, completed_at TEXT, UNIQUE(user_id, mission_id));
CREATE TABLE fa_step_progress (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  step_id INTEGER NOT NULL, status TEXT DEFAULT 'not_started',
  score INTEGER DEFAULT 0, UNIQUE(user_id, step_id));
CREATE TABLE fa_mission_ontology (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id INTEGER NOT NULL,
  ontology_id TEXT NOT NULL, mission_class TEXT, topic_class TEXT,
  competency_class TEXT, prereq_ontology_paths_json TEXT DEFAULT '[]',
  UNIQUE(mission_id));
CREATE TABLE fa_step_ontology (
  id INTEGER PRIMARY KEY AUTOINCREMENT, step_id INTEGER NOT NULL,
  ontology_id TEXT NOT NULL, step_class TEXT, competency_class TEXT,
  UNIQUE(step_id));
CREATE TABLE fa_user_competencies (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  competency_class TEXT NOT NULL, source_mission_id INTEGER,
  source_step_id INTEGER, demonstrated_at TEXT NOT NULL DEFAULT (datetime('now')),
  evidence_json TEXT DEFAULT '{}',
  UNIQUE(user_id, competency_class, source_mission_id));
"""

# The platform KG tables, WITH the foreign keys the platform declares. Defect 3
# only reproduces when the FKs are enforced, so the fixture must carry them.
PLATFORM_KG_SCHEMA = """
CREATE TABLE kg_graphs (
  id TEXT PRIMARY KEY, project_id TEXT, name TEXT NOT NULL, description TEXT,
  entity_count INTEGER DEFAULT 0, edge_count INTEGER DEFAULT 0,
  metadata TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT);
CREATE TABLE kg_nodes (
  id TEXT PRIMARY KEY, graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
  label TEXT NOT NULL, entity_type TEXT NOT NULL, properties TEXT DEFAULT '{}',
  embedding BLOB, centrality REAL DEFAULT 0.0, created_at TEXT);
CREATE TABLE kg_edges (
  id TEXT PRIMARY KEY, graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
  source_id TEXT NOT NULL REFERENCES kg_nodes(id),
  target_id TEXT NOT NULL REFERENCES kg_nodes(id),
  relationship TEXT NOT NULL, weight REAL DEFAULT 1.0,
  properties TEXT DEFAULT '{}', created_at TEXT);
"""


@pytest.fixture()
def chain_db(monkeypatch):
    """An Academy database wired to enforce the platform's KG foreign keys."""
    from apps.forge_academy import db as fadb

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    # The platform DDL declares the kg_graphs foreign key, so this fixture
    # enforces it. SQLite must be told; PG enforces whatever it materialised —
    # and the production instance checked on 2026-08-02 had NOT materialised
    # this one, which is why the fixture, not production, is the reference here.
    raw.execute("PRAGMA foreign_keys=ON")
    from tools.db.storage import StorageConnection

    conn = StorageConnection(raw, "sqlite")
    conn.executescript(ACADEMY_SCHEMA)
    conn.executescript(PLATFORM_KG_SCHEMA)
    conn.execute("INSERT INTO fa_users (id, username) VALUES (1, 'operator')")
    conn.commit()
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)
    return conn


def _seed_mission(conn, slug="harden-the-boundary", topic="security", tier=2,
                  steps=(("coding", 1), ("watch", 2), ("verify", 3))):
    conn.execute(
        "INSERT INTO fa_missions (slug, title, tier, topic, mission_type) "
        "VALUES (%s, %s, %s, %s, 'coding')",
        (slug, slug.replace("-", " ").title(), tier, topic),
    )
    mid = conn.execute("SELECT id FROM fa_missions WHERE slug=%s", (slug,)).fetchone()["id"]
    for step_type, num in steps:
        conn.execute(
            "INSERT INTO fa_mission_steps (mission_id, step_num, title, step_type) "
            "VALUES (%s, %s, %s, %s)",
            (mid, num, f"Step {num}", step_type),
        )
    conn.commit()
    return mid


# ---------------------------------------------------------------------------
# Defect 2 — the KG edge column name
# ---------------------------------------------------------------------------

def _platform_kg_columns(table: str) -> list[str]:
    """Column names the PLATFORM declares, read from the platform's own DDL."""
    src = (REPO_ROOT / "tools" / "knowledge_graph" / "ingester.py").read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE IF NOT EXISTS %s \((.*?)\n\s*\);" % table, src, re.S)
    assert m, f"platform DDL for {table} not found — this test's premise moved"
    cols = []
    for line in m.group(1).strip().splitlines():
        line = line.strip().rstrip(",")
        if line and not line.upper().startswith(("PRIMARY", "UNIQUE", "FOREIGN", "CHECK")):
            cols.append(line.split()[0])
    return cols


def test_kg_edge_insert_uses_the_column_the_platform_actually_declares():
    """`label` is not a kg_edges column; the platform names it `relationship`.

    Asserted against the platform DDL rather than a literal, so this test cannot
    drift into agreeing with a wrong copy of the schema the way the Academy's own
    CREATE IF NOT EXISTS did.
    """
    from apps.forge_academy import db as fadb

    platform_cols = _platform_kg_columns("kg_edges")
    assert "relationship" in platform_cols and "label" not in platform_cols

    src = inspect.getsource(fadb._create_kg_competency_edge)
    edge_insert = src[src.index("INTO kg_edges"):]
    named = edge_insert[edge_insert.index("(") + 1:edge_insert.index(")")]
    cols = [c.strip() for c in named.split(",") if c.strip()]
    assert cols, "could not parse the kg_edges column list"
    for col in cols:
        assert col in platform_cols, f"kg_edges has no column {col!r}"
    assert "relationship" in cols


def test_academy_local_ddl_matches_the_platform_kg_schema():
    """The local CREATE IF NOT EXISTS is a no-op where the real table exists.

    So it must not describe a DIFFERENT table: a developer reading it would
    otherwise write inserts against a schema production does not have — which is
    precisely how `label` got there.
    """
    from apps.forge_academy import db as fadb

    for table in ("kg_nodes", "kg_edges"):
        m = re.search(r"CREATE TABLE IF NOT EXISTS %s \((.*?)\n\);" % table,
                      fadb._DDL, re.S)
        assert m, f"{table} not declared in the Academy schema"
        local = {line.strip().split()[0]
                 for line in m.group(1).strip().splitlines()
                 if line.strip() and not line.strip().upper().startswith(
                     ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK"))}
        platform = set(_platform_kg_columns(table))
        assert local <= platform, f"{table} declares columns the platform lacks: {local - platform}"


# ---------------------------------------------------------------------------
# Defect 3 — the kg_graphs foreign key
# ---------------------------------------------------------------------------

def test_competency_edge_survives_a_database_where_the_graph_row_is_absent(chain_db):
    """icdev-core-ontology exists only after an ontology federation pass.

    Without it the node insert violates kg_nodes.graph_id -> kg_graphs(id), so
    the edge fails even with the column name fixed. The chain must seed its own
    graph row.
    """
    from apps.forge_academy import db as fadb

    assert chain_db.execute("SELECT COUNT(*) FROM kg_graphs").fetchone()[0] == 0

    row = fadb.record_user_competency(
        user_id=1, competency_class="icdev:SecurityEngineering",
        source_mission_id=None, evidence={"score": 100})

    assert row["kg_edge"] == "ok", f"KG edge failed: {row['kg_edge']}"
    assert chain_db.execute(
        "SELECT COUNT(*) FROM kg_edges WHERE relationship='demonstrates'"
    ).fetchone()[0] == 1
    assert chain_db.execute(
        "SELECT COUNT(*) FROM kg_graphs WHERE id=%s", (fadb.KG_GRAPH_ID,)
    ).fetchone()[0] == 1


def test_a_failed_kg_edge_never_costs_the_learner_the_competency(chain_db, monkeypatch):
    """The KG edge is a secondary index. The training record is the record."""
    from apps.forge_academy import db as fadb

    def boom(*a, **k):
        raise RuntimeError("kg unavailable")

    monkeypatch.setattr(fadb, "_create_kg_competency_edge", boom)
    row = fadb.record_user_competency(user_id=1, competency_class="icdev:Boundary",
                                      source_mission_id=None)

    assert row["kg_edge"].startswith("failed:"), "must report, not swallow"
    assert chain_db.execute(
        "SELECT COUNT(*) FROM fa_user_competencies WHERE competency_class='icdev:Boundary'"
    ).fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Defect 1 — mapping ordering and the count guard
# ---------------------------------------------------------------------------

def test_migrate_does_not_map_the_ontology_before_the_catalog_is_seeded():
    """migrate() runs first; mapping there always saw an empty fa_missions.

    Comments are stripped before the check — migrate() explains the ordering in
    prose, and matching that would assert the opposite of what it says.
    """
    from apps.forge_academy import db as fadb

    code = "\n".join(
        line.split("#")[0] for line in inspect.getsource(fadb.migrate).splitlines())
    assert "seed_mission_ontology_mappings(" not in code


def test_init_maps_the_ontology_after_seeding_the_catalog():
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint._ensure_init)
    assert "seed_mission_catalog()" in src and "seed_mission_ontology_mappings()" in src
    assert src.index("seed_mission_catalog()") < src.index("seed_mission_ontology_mappings()")


def test_mapping_covers_missions_no_builtin_list_knows_about(chain_db):
    """AADC/AIMC/tenant missions were never in BUILTIN_MISSIONS to be mapped.

    The old seeder iterated that list and then bailed on a COUNT guard, so
    catalog content from any other source was declared mapped without ever being
    looked at — 35 of 124 missions in production.
    """
    from apps.forge_academy import db as fadb

    _seed_mission(chain_db, slug="aadc-only-mission", topic="data", tier=3)
    summary = fadb.seed_mission_ontology_mappings()

    assert summary["missions_mapped"] == 1, summary
    assert not summary["errors"], summary
    row = chain_db.execute(
        "SELECT topic_class, competency_class FROM fa_mission_ontology"
    ).fetchone()
    assert row["topic_class"] and row["competency_class"]


def test_mapping_is_idempotent_and_writes_nothing_on_a_mapped_catalog(chain_db):
    from apps.forge_academy import db as fadb

    _seed_mission(chain_db)
    first = fadb.seed_mission_ontology_mappings()
    assert first["missions_mapped"] == 1 and first["steps_mapped"] >= 1

    second = fadb.seed_mission_ontology_mappings()
    assert second == {"missions_mapped": 0, "steps_mapped": 0, "errors": []}


def test_mapping_backfills_step_rows_that_predate_the_competency_column(chain_db):
    """Pre-aca-trn-02 step rows exist but carry competency_class NULL."""
    from apps.forge_academy import db as fadb

    mid = _seed_mission(chain_db)
    step_id = chain_db.execute(
        "SELECT id FROM fa_mission_steps WHERE mission_id=%s AND step_num=1", (mid,)
    ).fetchone()["id"]
    chain_db.execute(
        "INSERT INTO fa_step_ontology (step_id, ontology_id, step_class, competency_class) "
        "VALUES (%s, 'legacy:id', 'icdev:Exercise', NULL)", (step_id,))
    chain_db.commit()

    fadb.seed_mission_ontology_mappings()
    got = chain_db.execute(
        "SELECT competency_class FROM fa_step_ontology WHERE step_id=%s", (step_id,)
    ).fetchone()["competency_class"]
    assert got, "a NULL competency_class must be re-mapped, not treated as done"


# ---------------------------------------------------------------------------
# What a step is evidence OF
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("step_type,expected", [
    ("coding", "topic"),
    ("configure", "topic"),   # the graded activity on the guided role tracks
    ("design", "topic"),
    ("verify", "tier"),
    ("reflect", "tier"),
    ("watch", None),          # being shown a topic is not evidence of it
    ("unrecognised-type", None),
])
def test_step_evidence_rules(step_type, expected):
    from apps.forge_academy.ontology import get_step_competency_class

    got = get_step_competency_class(step_type, "icdev:SecurityEngineering", 2)
    if expected == "topic":
        assert got == "icdev:SecurityEngineering"
    elif expected == "tier":
        assert got and got != "icdev:SecurityEngineering"
    else:
        assert got is None


def test_a_mission_demonstrates_both_its_depth_and_its_subject():
    """Recording only the tier cannot tell a boundary from a prompt."""
    from apps.forge_academy.ontology import build_mission_competency_classes, get_tier_competency

    classes = build_mission_competency_classes("icdev:SecurityEngineering", 3)
    assert get_tier_competency(3) in classes
    assert "icdev:SecurityEngineering" in classes


# ---------------------------------------------------------------------------
# Recording a completion
# ---------------------------------------------------------------------------

def test_completion_records_tier_topic_and_the_steps_actually_passed(chain_db):
    from apps.forge_academy import db as fadb

    mid = _seed_mission(chain_db)
    fadb.seed_mission_ontology_mappings()
    # The learner passed the coding step and the verify step; not the watch step.
    for step_num in (1, 3):
        sid = chain_db.execute(
            "SELECT id FROM fa_mission_steps WHERE mission_id=%s AND step_num=%s",
            (mid, step_num)).fetchone()["id"]
        chain_db.execute(
            "INSERT INTO fa_step_progress (user_id, step_id, status) VALUES (1, %s, 'completed')",
            (sid,))
    chain_db.commit()

    # The topic class is read back from the mapping rather than hardcoded, so
    # this asserts the chain agrees with itself instead of with the vocabulary
    # the test author guessed.
    topic_class = chain_db.execute(
        "SELECT topic_class FROM fa_mission_ontology WHERE mission_id=%s", (mid,)
    ).fetchone()["topic_class"]
    assert topic_class, "the mission must carry a topic class to demonstrate"

    out = fadb.record_mission_competencies(user_id=1, mission_id=mid, score=100)

    assert not out["errors"], out
    assert out["unmapped"] is False
    assert topic_class in out["classes"], "the subject of the work must be claimed"
    assert fadb_tier_class(2) in out["classes"], "the depth of the work must be claimed"
    assert out["recorded"], "a verified completion must record something"

    ev = json.loads(chain_db.execute(
        "SELECT evidence_json FROM fa_user_competencies "
        "WHERE competency_class=%s", (topic_class,)).fetchone()["evidence_json"])
    assert ev["mission_slug"] == "harden-the-boundary"
    assert ev["score"] == 100
    assert ev["verified_step_ids"], "the claim must cite the submissions behind it"


def test_an_unmapped_mission_reports_itself_instead_of_recording_nothing(chain_db):
    """Silence here is what made 35 unmapped missions invisible."""
    from apps.forge_academy import db as fadb

    mid = _seed_mission(chain_db, slug="never-mapped")  # deliberately not mapped
    out = fadb.record_mission_competencies(user_id=1, mission_id=mid)

    assert out["unmapped"] is True
    assert out["recorded"] == []


def test_recording_the_same_mission_twice_does_not_duplicate_the_claim(chain_db):
    from apps.forge_academy import db as fadb

    mid = _seed_mission(chain_db)
    fadb.seed_mission_ontology_mappings()
    fadb.record_mission_competencies(user_id=1, mission_id=mid)
    fadb.record_mission_competencies(user_id=1, mission_id=mid)

    rows = chain_db.execute(
        "SELECT competency_class, COUNT(*) n FROM fa_user_competencies "
        "GROUP BY competency_class").fetchall()
    assert rows and all(r["n"] == 1 for r in rows)


def test_backfill_credits_missions_completed_before_the_chain_worked(chain_db):
    """Every completion on the board predates a working recorder."""
    from apps.forge_academy import db as fadb

    mid = _seed_mission(chain_db)
    fadb.seed_mission_ontology_mappings()
    chain_db.execute(
        "INSERT INTO fa_mission_progress (user_id, mission_id, status, score) "
        "VALUES (1, %s, 'completed', 95)", (mid,))
    chain_db.commit()

    summary = fadb.backfill_user_competencies()
    assert summary["recorded"] > 0 and summary["users"] == 1, summary
    assert not summary["errors"], summary

    # Idempotent: a second pass finds nothing left to do.
    assert fadb.backfill_user_competencies()["missions"] == 0


# ---------------------------------------------------------------------------
# The learner-facing record, and observability
# ---------------------------------------------------------------------------

def test_profile_groups_claims_and_attaches_their_evidence(chain_db):
    from apps.forge_academy import db as fadb

    mid = _seed_mission(chain_db)
    fadb.seed_mission_ontology_mappings()
    fadb.record_mission_competencies(user_id=1, mission_id=mid)

    profile = fadb.get_competency_profile(1)
    assert profile["error"] is None
    assert profile["total_classes"] >= 2  # tier + topic
    for entry in profile["competencies"]:
        assert entry["evidence"], "a competency with no evidence is an assertion"
        assert entry["mission_count"] == len(entry["evidence"])
        assert entry["catalog_missions"] >= 1, "depth must be readable"


def test_profile_reports_a_read_failure_instead_of_an_empty_record(chain_db):
    """'You earned nothing' and 'we could not read it' must not look the same."""
    from apps.forge_academy import db as fadb

    chain_db.execute("DROP TABLE fa_user_competencies")
    chain_db.commit()
    profile = fadb.get_competency_profile(1)
    assert profile["error"], "a broken read must not render as an empty record"


def test_health_flags_a_chain_that_records_nothing_for_completed_missions(chain_db):
    from apps.forge_academy import db as fadb

    mid = _seed_mission(chain_db)
    chain_db.execute(
        "INSERT INTO fa_mission_progress (user_id, mission_id, status) "
        "VALUES (1, %s, 'completed')", (mid,))
    chain_db.commit()

    stalled = fadb.competency_chain_status()
    assert stalled["stalled"] is True, "completions with zero competencies is the broken state"
    assert stalled["missions_unmapped"] == 1

    fadb.seed_mission_ontology_mappings()
    fadb.backfill_user_competencies()
    healthy = fadb.competency_chain_status()
    assert healthy["stalled"] is False
    assert healthy["missions_unmapped"] == 0
    assert healthy["competencies_recorded"] > 0


def test_an_unused_chain_is_not_reported_as_broken(chain_db):
    """No completions yet is not a defect; only completions-without-records is."""
    from apps.forge_academy import db as fadb

    status = fadb.competency_chain_status()
    assert status["stalled"] is False and status["ok"] is True


# ---------------------------------------------------------------------------
# The submit path and the surface
# ---------------------------------------------------------------------------

def test_step_submit_no_longer_swallows_a_failed_recording():
    """`except Exception: pass` is why this was never once observed."""
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_step_submit)
    assert "record_mission_competencies" in src
    assert not re.search(r"except Exception:\s*\n\s*pass", src), "bare swallow is back"
    assert '"competencies"' in src or "resp[\"competencies\"]" in src, \
        "the outcome must reach the client, not only a log"


def test_health_route_surfaces_the_competency_chain():
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_academy_health)
    assert "competency_chain" in src and "stalled" in src


def test_certificates_cite_competencies():
    """aca-int-07: a certificate quotes the training record, not just missions."""
    from apps.forge_academy import db as fadb

    src = inspect.getsource(fadb.collect_cert_evidence)
    assert '"evidence_type": "competency"' in src


def test_profile_page_renders_the_training_record():
    """Backend-only would leave the record real but unfindable."""
    html = (TEMPLATES / "profile.html").read_text(encoding="utf-8")
    assert "Competency Record" in html
    assert "competency_profile" in html
    assert "fa-comp-evidence" in html, "claims must render with their evidence"

    mirror = (REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates"
              / "forge_academy" / "profile.html")
    assert mirror.read_text(encoding="utf-8") == html, "icdev/ mirror is stale"


def test_profile_route_passes_the_record_to_the_template():
    from apps.forge_academy import blueprint

    assert "competency_profile" in inspect.getsource(blueprint.profile)
