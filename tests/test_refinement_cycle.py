#!/usr/bin/env python3
# CUI // SP-CTI
"""Snapshot + as-a-unit rollback of the supplemental harness state — exa-refine-05.

The thing under test is not "a snapshot exists" but the three claims the card
makes: a refinement cycle spanning prompts, skills and goals can be undone as
ONE unit; every snapshot and every applied refinement leaves a *verifiable*
chained audit row rather than a log line; and the file half genuinely goes
through ``tools/agent_runtime/checkpoints.py`` instead of a second checkpoint
system.

Everything runs against a file-backed SQLite database this module owns, with
``ICDEV_DB_PATH`` pointed at it, because a rollback restores whatever state it
finds — running that against the ambient ``icdev.db`` would archive the host's
real prompt versions.

The repo root that checkpoints.py resolves is likewise redirected into
``tmp_path``, so no test writes into the checkout's ``.tmp/checkpoints`` or
touches ``.agents/skills``.
"""

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Imported through the `tools.` shim, which resolves to the icdev/ package copy.
# The fixtures below monkeypatch these exact module objects, so they have to be
# the ones the code under test imports — see the CLAUDE.md shim-aware patching
# guardrail.
checkpoints = importlib.import_module("tools.agent_runtime.checkpoints")
refinement_cycle = importlib.import_module("tools.agent_runtime.refinement_cycle")
prompt_registry = importlib.import_module("tools.llm.prompt_registry")
provenance_verifier = importlib.import_module("tools.blockchain.provenance_verifier")
audit_logger = importlib.import_module("tools.audit.audit_logger")
audit_chain = importlib.import_module("tools.audit.chain")

PROMPT = "layer/exa-refine-05-test"

# audit_trail with migration 149's chain columns, plus every supplemental store
# a provider reads. Mirrors the DDL the shipped migration and conftest declare.
SCHEMA = """
CREATE TABLE audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    ip_address TEXT,
    session_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hash TEXT,
    previous_hash TEXT,
    signature TEXT
);
CREATE TABLE audit_chain_genesis (
    chain_start_id INTEGER PRIMARY KEY,
    hash_algorithm TEXT NOT NULL DEFAULT 'sha256',
    note TEXT,
    tenant_id TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE source_citation_registry (
    id TEXT PRIMARY KEY,
    source_table TEXT,
    source_record_id TEXT,
    merkle_root TEXT,
    blockchain_tx_id TEXT
);
CREATE TABLE supplemental_state_snapshots (
    id              TEXT PRIMARY KEY,
    cycle_id        TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'open',
    label           TEXT DEFAULT '',
    actor           TEXT DEFAULT 'system',
    checkpoint_id   TEXT,
    state_json      TEXT NOT NULL,
    state_hash      TEXT NOT NULL,
    audit_entry_id  INTEGER,
    classification  TEXT DEFAULT 'CUI',
    tenant_id       TEXT,
    created_at      TEXT NOT NULL
);
CREATE TABLE supplemental_refinements (
    id              TEXT PRIMARY KEY,
    cycle_id        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    action          TEXT NOT NULL,
    target          TEXT DEFAULT '',
    actor           TEXT DEFAULT 'system',
    details         TEXT DEFAULT '{}',
    audit_entry_id  INTEGER,
    classification  TEXT DEFAULT 'CUI',
    tenant_id       TEXT,
    created_at      TEXT NOT NULL
);
CREATE TABLE sag_skill_registry (
    name             TEXT PRIMARY KEY,
    artifact_id      TEXT,
    skill_dir        TEXT,
    session_id       TEXT DEFAULT '',
    model            TEXT DEFAULT '',
    approved_by      TEXT DEFAULT '',
    status           TEXT DEFAULT 'active',
    pinned           INTEGER DEFAULT 0,
    use_count        INTEGER DEFAULT 0,
    last_activity_at TEXT,
    classification   TEXT DEFAULT 'CUI',
    created_at       TEXT,
    updated_at       TEXT
);
CREATE TABLE genesis_generated_goals (
    id              TEXT PRIMARY KEY,
    version         INTEGER NOT NULL DEFAULT 1,
    domain_label    TEXT NOT NULL,
    title           TEXT NOT NULL,
    slug            TEXT NOT NULL,
    novelty_score   REAL NOT NULL DEFAULT 0.0,
    quality_score   REAL NOT NULL DEFAULT 0.0,
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    keywords        TEXT,
    goal_markdown   TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'suggested'
        CHECK(status IN ('suggested','approved','rejected','superseded')),
    gkp_id          TEXT,
    goal_file_path  TEXT,
    rejection_reason TEXT,
    approved_at     TEXT,
    rejected_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """A private database + a private repo root, wired into every reader.

    ``ICDEV_DB_PATH`` covers ``get_connection()``, which is what ``log_event``,
    the providers and this module's own inserts all use. ``provenance_verifier``
    is the exception: it captured ``storage.DB_PATH`` at import time and passes
    it explicitly, so the env var alone would send the verifier at the real
    database and every verdict here would be about somebody else's rows.
    """
    db = tmp_path / "refine.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.setenv("ICDEV_AUDIT_HMAC_SECRET", "exa-refine-05-test-secret")
    monkeypatch.delenv("ICDEV_AUDIT_SIGNING_KEY_PATH", raising=False)
    monkeypatch.setattr(provenance_verifier, "DB_PATH", str(db))

    # checkpoints.py resolves the repo root from its own __file__; redirect it so
    # snapshots land in tmp_path/.tmp/checkpoints and provider paths() enumerate
    # tmp_path/.agents/skills rather than the real checkout.
    root = tmp_path / "root"
    (root / ".agents" / "skills").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(checkpoints, "_REPO_ROOT", root)

    # has_chain_columns / record_chain_start memoise per database identity, and
    # every test builds a fresh one at a reused path shape.
    audit_chain._COLUMN_CACHE.clear()
    audit_chain._GENESIS_CACHE.clear()
    prompt_registry._LAYER_CACHE.clear()
    yield {"db": db, "root": root}
    audit_chain._COLUMN_CACHE.clear()
    audit_chain._GENESIS_CACHE.clear()
    prompt_registry._LAYER_CACHE.clear()


def rows(db: Path, sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    out = conn.execute(sql, params).fetchall()
    conn.close()
    return out


def active_version(db: Path, name: str = PROMPT):
    found = rows(
        db,
        "SELECT version FROM prompt_versions WHERE prompt_name = ? AND status = 'active'",
        (name,),
    )
    return found[0]["version"] if found else None


def status_of(db: Path, name: str, version: int):
    found = rows(
        db,
        "SELECT status FROM prompt_versions WHERE prompt_name = ? AND version = ?",
        (name, version),
    )
    return found[0]["status"] if found else None


def seed_skill(db: Path, name: str, status: str = "active", pinned: int = 0) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO sag_skill_registry (name, status, pinned, skill_dir, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01')",
        (name, status, pinned, f".agents/skills/{name}"),
    )
    conn.commit()
    conn.close()


def seed_goal(db: Path, goal_id: str, status: str = "suggested") -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO genesis_generated_goals "
        "(id, domain_label, title, slug, goal_markdown, sha256, status, created_at, updated_at) "
        "VALUES (?, 'd', 't', 's', '# g', 'abc', ?, '2026-01-01', '2026-01-01')",
        (goal_id, status),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# The headline acceptance: a cycle is undone as a unit.
# ---------------------------------------------------------------------------
def test_a_refinement_cycle_across_three_stores_rolls_back_as_one_unit(harness):
    db = harness["db"]

    prompt_registry.register_prompt(PROMPT, "v one", "code_generation")
    prompt_registry.activate_prompt(PROMPT, 1)
    seed_skill(db, "icdev-auto-before", status="active")
    seed_goal(db, "gl-before", status="suggested")

    cycle = refinement_cycle.open_cycle("gepa pass", actor="tester")

    # A refinement cycle: a new prompt layer, a promoted skill, a learned goal.
    prompt_registry.register_prompt(PROMPT, "v two", "code_generation")
    prompt_registry.activate_prompt(PROMPT, 2)
    seed_skill(db, "icdev-auto-during", status="active")
    seed_goal(db, "gl-during", status="approved")
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE genesis_generated_goals SET status = 'approved' WHERE id = 'gl-before'")
    conn.commit()
    conn.close()
    refinement_cycle.record_refinement(
        cycle["cycle_id"], "prompts", "activated", target=f"{PROMPT} v2", actor="tester"
    )

    assert active_version(db) == 2

    result = refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester")
    assert result["ok"] is True, result

    # prompts: back to v1, and the version the cycle added is archived, not gone.
    assert active_version(db) == 1
    assert status_of(db, PROMPT, 2) == "archived"
    # skills: the promoted one is archived, the pre-existing one untouched.
    skills = {r["name"]: r["status"] for r in rows(db, "SELECT name, status FROM sag_skill_registry")}
    assert skills == {"icdev-auto-before": "active", "icdev-auto-during": "archived"}
    # goals: the flipped status is back, the learned one is superseded.
    goals = {r["id"]: r["status"] for r in rows(db, "SELECT id, status FROM genesis_generated_goals")}
    assert goals == {"gl-before": "suggested", "gl-during": "superseded"}


def test_rollback_is_itself_a_cycle_and_can_be_undone(harness):
    db = harness["db"]
    prompt_registry.register_prompt(PROMPT, "v one", "code_generation")
    prompt_registry.activate_prompt(PROMPT, 1)

    cycle = refinement_cycle.open_cycle("first", actor="tester")
    prompt_registry.register_prompt(PROMPT, "v two", "code_generation")
    prompt_registry.activate_prompt(PROMPT, 2)

    undone = refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester")
    assert active_version(db) == 1

    # Rolling back the undo cycle restores the refinement.
    redone = refinement_cycle.rollback_cycle(undone["undo_cycle_id"], actor="tester")
    assert redone["ok"] is True, redone
    assert active_version(db) == 2
    assert status_of(db, PROMPT, 2) == "active"


def test_rolled_back_status_is_derived_from_an_appended_row(harness):
    db = harness["db"]
    cycle = refinement_cycle.open_cycle("derived", actor="tester")
    assert refinement_cycle.cycle_status(cycle["cycle_id"]) == "open"

    before = rows(db, "SELECT * FROM supplemental_state_snapshots WHERE cycle_id = ?", (cycle["cycle_id"],))
    refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester")
    after = rows(db, "SELECT * FROM supplemental_state_snapshots WHERE cycle_id = ?", (cycle["cycle_id"],))

    assert refinement_cycle.cycle_status(cycle["cycle_id"]) == "rolled_back"
    # Append-only: the opening snapshot row is byte-identical afterwards. The
    # status came from a NEW ('cycle','rolled_back') refinement row.
    assert [dict(r) for r in before] == [dict(r) for r in after]
    markers = rows(
        db,
        "SELECT provider, action FROM supplemental_refinements WHERE cycle_id = ?",
        (cycle["cycle_id"],),
    )
    assert ("cycle", "rolled_back") in [(r["provider"], r["action"]) for r in markers]


def test_an_already_rolled_back_cycle_is_refused_without_force(harness):
    cycle = refinement_cycle.open_cycle("once", actor="tester")
    refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester")

    again = refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester")
    assert again["ok"] is False
    assert "already rolled back" in again["reason"]
    assert refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester", force=True)["ok"] is True


# ---------------------------------------------------------------------------
# Chained audit rows — the difference between auditable and merely logged.
# ---------------------------------------------------------------------------
def test_supplemental_state_is_an_admitted_audit_event_type():
    """The CHECK on audit_trail.event_type is generated from this constant.

    Writing under an unadmitted event type is rejected, ``log_event`` raises
    before the INSERT, and this module's best-effort ``except`` would report
    every self-modification as audited while nothing was written.
    """
    assert refinement_cycle.AUDIT_EVENT_TYPE in audit_logger.VALID_EVENT_TYPES
    assert f"'{refinement_cycle.AUDIT_EVENT_TYPE}'" in audit_logger.event_type_check_sql()


def test_snapshot_and_refinement_rows_carry_verifiable_chained_audit_rows(harness):
    db = harness["db"]
    cycle = refinement_cycle.open_cycle("audited", actor="tester")
    refinement_cycle.record_refinement(
        cycle["cycle_id"], "prompts", "activated", target="layer/x", actor="tester"
    )

    snapshot = rows(
        db, "SELECT audit_entry_id FROM supplemental_state_snapshots WHERE cycle_id = ?",
        (cycle["cycle_id"],),
    )[0]
    refinement = rows(
        db, "SELECT audit_entry_id FROM supplemental_refinements WHERE cycle_id = ?",
        (cycle["cycle_id"],),
    )[0]
    assert snapshot["audit_entry_id"] and refinement["audit_entry_id"]

    for entry_id in (snapshot["audit_entry_id"], refinement["audit_entry_id"]):
        verdict = provenance_verifier.verify_audit_integrity(int(entry_id))
        assert verdict["chain_status"] == "chained"
        assert verdict["hash_valid"] is True
        assert verdict["chain_valid"] is True
        assert verdict["signature_valid"] is True

    report = refinement_cycle.verify_cycle(cycle["cycle_id"])
    assert report["ok"] is True, report
    assert report["verified"] == 2
    assert report["unaudited"] == 0 and report["failed"] == 0


def test_verify_cycle_catches_a_tampered_audit_row(harness):
    db = harness["db"]
    cycle = refinement_cycle.open_cycle("tampered", actor="tester")
    entry_id = rows(
        db, "SELECT audit_entry_id FROM supplemental_state_snapshots WHERE cycle_id = ?",
        (cycle["cycle_id"],),
    )[0]["audit_entry_id"]

    assert refinement_cycle.verify_cycle(cycle["cycle_id"])["ok"] is True

    # Rewrite the actor without recomputing the hash — the exact edit the chain
    # exists to expose.
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE audit_trail SET actor = 'someone-else' WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()

    report = refinement_cycle.verify_cycle(cycle["cycle_id"])
    assert report["ok"] is False
    assert report["failed"] == 1
    assert report["events"][0]["verdict"] == "unverified"


def test_rollback_writes_its_own_chained_audit_row(harness):
    cycle = refinement_cycle.open_cycle("audited-rollback", actor="tester")
    result = refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester")

    assert result["audit_entry_id"]
    verdict = provenance_verifier.verify_audit_integrity(int(result["audit_entry_id"]))
    assert verdict["hash_valid"] is True and verdict["chain_valid"] is True

    entry = rows(
        harness["db"], "SELECT action, details FROM audit_trail WHERE id = ?",
        (result["audit_entry_id"],),
    )[0]
    assert entry["action"] == "cycle_rolled_back"
    assert json.loads(entry["details"])["undo_cycle_id"] == result["undo_cycle_id"]


# ---------------------------------------------------------------------------
# The file half is checkpoints.py, not a second checkpoint system.
# ---------------------------------------------------------------------------
def test_the_file_half_is_delegated_to_checkpoints(harness):
    root = harness["root"]
    skill = root / ".agents" / "skills" / "icdev-auto-demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("original\n", encoding="utf-8")

    cycle = refinement_cycle.open_cycle("files", actor="tester")

    # The cycle is anchored to a real checkpoints.py checkpoint holding the file.
    assert cycle["checkpoint_id"]
    checkpoint = checkpoints.load_checkpoint(cycle["checkpoint_id"])
    assert checkpoint is not None
    assert ".agents/skills/icdev-auto-demo/SKILL.md" in [f.path for f in checkpoint.files]

    (skill / "SKILL.md").write_text("refined\n", encoding="utf-8")
    refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester")

    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "original\n"


def test_a_file_added_during_the_cycle_is_removed_only_once_recoverable(harness):
    root = harness["root"]
    (root / ".agents" / "skills" / "icdev-auto-old").mkdir(parents=True)
    (root / ".agents" / "skills" / "icdev-auto-old" / "SKILL.md").write_text("keep\n", encoding="utf-8")

    cycle = refinement_cycle.open_cycle("adds", actor="tester")

    added = root / ".agents" / "skills" / "icdev-auto-new" / "SKILL.md"
    added.parent.mkdir(parents=True)
    added.write_text("generated mid-cycle\n", encoding="utf-8")

    result = refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester")

    rel = ".agents/skills/icdev-auto-new/SKILL.md"
    assert rel in result["files_removed"]
    assert not added.exists()
    # Pre-existing files are untouched.
    assert (root / ".agents" / "skills" / "icdev-auto-old" / "SKILL.md").exists()

    # Removal is only safe because the undo cycle holds the bytes — rolling the
    # undo cycle back brings the file straight back.
    refinement_cycle.rollback_cycle(result["undo_cycle_id"], actor="tester")
    assert added.exists()
    assert added.read_text(encoding="utf-8") == "generated mid-cycle\n"


def test_an_unrecoverable_added_file_is_reported_not_deleted(harness, monkeypatch):
    """If the undo checkpoint cannot hand the bytes back, the file stays put.

    This is the guard that makes the deletion above safe rather than merely
    convenient, so it is asserted directly: a checkpoint that failed to capture
    the file must produce a ``files_not_removed`` entry, never a silent delete.
    """
    root = harness["root"]
    cycle = refinement_cycle.open_cycle("unrecoverable", actor="tester")

    added = root / ".agents" / "skills" / "icdev-auto-unsaved" / "SKILL.md"
    added.parent.mkdir(parents=True)
    added.write_text("only copy\n", encoding="utf-8")

    monkeypatch.setattr(refinement_cycle, "_recoverable_from", lambda *_a, **_k: False)
    result = refinement_cycle.rollback_cycle(cycle["cycle_id"], actor="tester")

    assert ".agents/skills/icdev-auto-unsaved/SKILL.md" in result["files_not_removed"]
    assert result["files_removed"] == []
    assert added.read_text(encoding="utf-8") == "only copy\n"


# ---------------------------------------------------------------------------
# Capture honesty
# ---------------------------------------------------------------------------
def test_a_missing_store_is_recorded_as_unavailable_not_as_empty(harness):
    """"No table" and "no rows" are different facts and must not render alike.

    A rollback that silently skipped a store whose table it never found would
    report success while leaving that store where the cycle put it.
    """
    conn = sqlite3.connect(str(harness["db"]))
    conn.execute("DROP TABLE genesis_generated_goals")
    conn.commit()
    conn.close()

    state = refinement_cycle.capture_state()
    assert state["providers"]["goals"]["available"] is False
    assert "genesis_generated_goals" in state["providers"]["goals"]["reason"]
    assert state["providers"]["skills"]["available"] is True


def test_describe_rollback_previews_without_changing_anything(harness):
    db = harness["db"]
    prompt_registry.register_prompt(PROMPT, "v one", "code_generation")
    prompt_registry.activate_prompt(PROMPT, 1)
    cycle = refinement_cycle.open_cycle("preview", actor="tester")
    prompt_registry.register_prompt(PROMPT, "v two", "code_generation")
    prompt_registry.activate_prompt(PROMPT, 2)

    preview = refinement_cycle.describe_rollback(cycle["cycle_id"])
    assert preview["ok"] is True
    assert "prompts" in preview["drifted_providers"]
    assert preview["status"] == "open"
    # Nothing applied.
    assert active_version(db) == 2
    assert refinement_cycle.cycle_status(cycle["cycle_id"]) == "open"


def test_list_cycles_reports_status_and_refinement_count(harness):
    first = refinement_cycle.open_cycle("one", actor="tester")
    refinement_cycle.record_refinement(first["cycle_id"], "prompts", "activated", actor="tester")
    second = refinement_cycle.open_cycle("two", actor="tester")
    refinement_cycle.rollback_cycle(second["cycle_id"], actor="tester")

    listed = {c["cycle_id"]: c for c in refinement_cycle.list_cycles()}
    assert listed[first["cycle_id"]]["status"] == "open"
    assert listed[first["cycle_id"]]["refinements"] == 1
    assert listed[second["cycle_id"]]["status"] == "rolled_back"
    # The pre-rollback snapshot is its own cycle, not a second entry for this one.
    assert listed[second["cycle_id"]]["label"] == "two"
