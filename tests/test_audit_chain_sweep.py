#!/usr/bin/env python3
# CUI // SP-CTI
"""Whole-chain integrity sweep — exa-audit-04.

The per-row verifier already existed; what did not was a way to ask "has this
table been tampered with?" without calling it 80,000 times. These tests pin the
property that makes the sweep worth having: **a broken link and a pre-cutover row
must never land in the same bucket.** If they do, a real tamper event gets
dismissed as legacy data, which is the failure the whole feature exists to
prevent — so most of what follows is adversarial: edit a row, delete a row, strip
a signature, and check the sweep says the right one of four things.

File-backed SQLite throughout, for the reason ``test_audit_chain_writer.py``
gives: the chain is a property of a whole table, so a test sharing the ambient
``icdev.db`` would be asserting against whatever else the host had written.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from icdev.tools.audit import chain as audit_chain  # noqa: E402
from icdev.tools.audit.chain_sweep import (  # noqa: E402
    REASON_HASH_MISMATCH,
    REASON_LINK_MISMATCH,
    STATUS_BROKEN,
    STATUS_PRE_CUTOVER,
    STATUS_UNCHAINED,
    STATUS_VERIFIED,
    sweep_chain,
)
from tools.audit.audit_logger import log_event  # noqa: E402

# Mirrors the live audit_trail plus migration 149's chain columns and the
# exa-audit-03 cutover marker, exactly as test_audit_chain_writer.py declares it.
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
"""


@pytest.fixture(autouse=True)
def _clear_chain_caches():
    """The writer memoises column presence and cutover per database path.

    Every test builds a fresh temp database and SQLite reuses the path shape, so
    a cached answer from an earlier test would describe a database that is gone.
    """
    audit_chain._COLUMN_CACHE.clear()
    audit_chain._GENESIS_CACHE.clear()
    yield
    audit_chain._COLUMN_CACHE.clear()
    audit_chain._GENESIS_CACHE.clear()


@pytest.fixture
def signing_secret(monkeypatch):
    monkeypatch.setenv("ICDEV_AUDIT_HMAC_SECRET", "exa-audit-04-test-secret")
    monkeypatch.delenv("ICDEV_AUDIT_SIGNING_KEY_PATH", raising=False)
    return "exa-audit-04-test-secret"


def make_db(tmp_path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "audit.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return db


def legacy_rows(db: Path, count: int) -> None:
    """Rows as they were written before the chain writer existed: no hash."""
    conn = sqlite3.connect(str(db))
    for i in range(count):
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action) VALUES (?, ?, ?)",
            ("project_created", "legacy", f"pre-cutover-{i}"),
        )
    conn.commit()
    conn.close()


def sweep(db: Path, **kwargs) -> dict:
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db))
    try:
        return sweep_chain(conn=conn, **kwargs)
    finally:
        conn.close()


def counts(report: dict) -> dict:
    return report["counts"]


# ---------------------------------------------------------------------------
# The baseline: an untouched chain sweeps clean.
# ---------------------------------------------------------------------------
def test_healthy_chain_reports_every_row_verified(tmp_path, signing_secret):
    db = make_db(tmp_path)
    for i in range(5):
        log_event("code_generated", "tester", f"a{i}", db_path=db)

    report = sweep(db)

    assert report["ok"] is True
    assert report["chain_health"] == "healthy"
    assert counts(report)[STATUS_VERIFIED] == 5
    assert counts(report)[STATUS_BROKEN] == 0
    assert report["total"] == 5


# ---------------------------------------------------------------------------
# Tamper detection. A sweep that cannot catch these is decoration.
# ---------------------------------------------------------------------------
def test_edited_row_is_broken_with_hash_mismatch(tmp_path, signing_secret):
    db = make_db(tmp_path)
    ids = [log_event("code_generated", "tester", f"a{i}", db_path=db) for i in range(3)]

    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE audit_trail SET action = ? WHERE id = ?", ("edited", ids[1]))
    conn.commit()
    conn.close()

    report = sweep(db)

    assert report["chain_health"] == "broken"
    assert counts(report)[STATUS_BROKEN] >= 1
    reasons = {s["reason"] for s in report["broken_samples"]}
    assert REASON_HASH_MISMATCH in reasons
    assert ids[1] in {s["id"] for s in report["broken_samples"]}
    # The edited row must NOT be excused as legacy data.
    assert counts(report)[STATUS_PRE_CUTOVER] == 0


def test_deleted_row_breaks_its_successors_link(tmp_path, signing_secret):
    db = make_db(tmp_path)
    ids = [log_event("code_generated", "tester", f"a{i}", db_path=db) for i in range(3)]

    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM audit_trail WHERE id = ?", (ids[1],))
    conn.commit()
    conn.close()

    report = sweep(db)

    assert report["chain_health"] == "broken"
    broken = {s["id"]: s["reason"] for s in report["broken_samples"]}
    # The survivor still carries the removed row's hash, which no longer matches
    # the GENESIS fallback a missing predecessor implies.
    assert broken.get(ids[2]) == REASON_LINK_MISMATCH


def test_rewritten_previous_hash_is_broken(tmp_path, signing_secret):
    """Re-pointing a link is the subtle tamper: content is untouched."""
    db = make_db(tmp_path)
    ids = [log_event("code_generated", "tester", f"a{i}", db_path=db) for i in range(3)]

    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE audit_trail SET previous_hash = ? WHERE id = ?", ("de" * 32, ids[2]))
    conn.commit()
    conn.close()

    report = sweep(db)

    broken = {s["id"]: s["reason"] for s in report["broken_samples"]}
    assert broken.get(ids[2]) == REASON_LINK_MISMATCH


# ---------------------------------------------------------------------------
# The headline separation: pre-cutover is not broken, and broken is not excused.
# ---------------------------------------------------------------------------
def test_pre_cutover_rows_are_not_counted_broken(tmp_path, signing_secret):
    db = make_db(tmp_path)
    legacy_rows(db, 7)
    for i in range(3):
        log_event("code_generated", "tester", f"a{i}", db_path=db)

    report = sweep(db)

    assert counts(report)[STATUS_PRE_CUTOVER] == 7
    assert counts(report)[STATUS_VERIFIED] == 3
    assert counts(report)[STATUS_BROKEN] == 0
    assert report["chain_health"] == "healthy"


def test_tampering_after_legacy_rows_still_reports_broken(tmp_path, signing_secret):
    """The dismissal risk, stated as a test.

    A table that is mostly legacy rows must not drown one tampered row: the
    broken count stays exactly 1 and the legacy rows stay in their own bucket.
    """
    db = make_db(tmp_path)
    legacy_rows(db, 50)
    ids = [log_event("code_generated", "tester", f"a{i}", db_path=db) for i in range(3)]

    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE audit_trail SET details = ? WHERE id = ?", ('{"x":"y"}', ids[0]))
    conn.commit()
    conn.close()

    report = sweep(db)

    assert counts(report)[STATUS_PRE_CUTOVER] == 50
    assert counts(report)[STATUS_BROKEN] == 1
    assert report["chain_health"] == "broken"


def test_post_cutover_unchained_rows_get_their_own_bucket(tmp_path, signing_secret):
    """A direct INSERT after cutover is neither legacy nor tampered.

    156 files under tools/ INSERT into audit_trail without the chain columns.
    Bucketing those as broken would bury a real tamper alarm in noise; bucketing
    them as pre-cutover would misdate them.
    """
    db = make_db(tmp_path)
    legacy_rows(db, 4)
    log_event("code_generated", "tester", "cutover", db_path=db)
    legacy_rows(db, 6)  # written AFTER the chain started, still unhashed

    report = sweep(db)

    assert counts(report)[STATUS_PRE_CUTOVER] == 4
    assert counts(report)[STATUS_UNCHAINED] == 6
    assert counts(report)[STATUS_VERIFIED] == 1
    assert counts(report)[STATUS_BROKEN] == 0
    # An unchained row is a known structural gap, not a tamper signal.
    assert report["chain_health"] == "healthy"


def test_a_gap_restarts_the_chain_without_reporting_a_break(tmp_path, signing_secret):
    """Unchained rows between two chained runs restart at GENESIS by design."""
    db = make_db(tmp_path)
    log_event("code_generated", "tester", "run1", db_path=db)
    legacy_rows(db, 3)
    log_event("code_generated", "tester", "run2", db_path=db)

    report = sweep(db)

    assert counts(report)[STATUS_VERIFIED] == 2
    assert counts(report)[STATUS_BROKEN] == 0
    assert sum(1 for link in report["links"] if link["chain_start"]) == 2


# ---------------------------------------------------------------------------
# Signatures are reported, never a broken determinant.
# ---------------------------------------------------------------------------
def test_unsigned_rows_are_still_verified(tmp_path, monkeypatch):
    """No signing key must not paint a healthy chain red.

    key_manager returns algorithm "none" when nothing is configured, and rows on
    the live deployment already carry exactly that.
    """
    monkeypatch.delenv("ICDEV_AUDIT_HMAC_SECRET", raising=False)
    monkeypatch.delenv("ICDEV_AUDIT_SIGNING_KEY_PATH", raising=False)
    db = make_db(tmp_path)
    for i in range(3):
        log_event("code_generated", "tester", f"a{i}", db_path=db)

    report = sweep(db)

    assert counts(report)[STATUS_BROKEN] == 0
    assert report["chain_health"] == "healthy"


def test_signature_counts_are_reported_separately(tmp_path, signing_secret):
    db = make_db(tmp_path)
    for i in range(2):
        log_event("code_generated", "tester", f"a{i}", db_path=db)

    report = sweep(db, verify_signatures=True)

    assert report["signatures"]["signed"] == 2
    assert report["signatures"]["checked"] is True
    assert report["signatures"]["verified"] == 2


# ---------------------------------------------------------------------------
# Cutover provenance: a derived boundary must not be presented as recorded.
# ---------------------------------------------------------------------------
def test_recorded_marker_is_authoritative(tmp_path, signing_secret):
    db = make_db(tmp_path)
    legacy_rows(db, 2)
    log_event("code_generated", "tester", "cutover", db_path=db)

    report = sweep(db)

    assert report["cutover"]["source"] == "marker"
    assert report["cutover"]["authoritative"] is True


def test_missing_marker_falls_back_to_derived_and_says_so(tmp_path, signing_secret):
    db = make_db(tmp_path)
    legacy_rows(db, 2)
    first = log_event("code_generated", "tester", "cutover", db_path=db)

    # Simulate a database where migration 20260812041301 has not run: the marker
    # table is empty, so the boundary can only be inferred.
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM audit_chain_genesis")
    conn.commit()
    conn.close()

    report = sweep(db)

    assert report["cutover"]["source"] == "derived"
    assert report["cutover"]["authoritative"] is False
    assert report["cutover"]["boundary_id"] == first
    # A derived boundary still buckets correctly — it is only less trustworthy.
    assert counts(report)[STATUS_PRE_CUTOVER] == 2


def test_empty_table_reports_no_chain(tmp_path):
    db = make_db(tmp_path)

    report = sweep(db)

    assert report["ok"] is True
    assert report["chain_health"] == "no_chain"
    assert report["total"] == 0
    assert report["cutover"]["source"] == "none"


# ---------------------------------------------------------------------------
# Links are what the /provenance page renders.
# ---------------------------------------------------------------------------
def test_links_carry_real_hashes_for_the_ui(tmp_path, signing_secret):
    db = make_db(tmp_path)
    for i in range(3):
        log_event("code_generated", "tester", f"a{i}", db_path=db)

    report = sweep(db)
    links = report["links"]

    assert len(links) == 3
    assert all(len(link["hash"]) == 64 for link in links)
    assert all(len(link["previous_hash"]) == 64 for link in links)
    assert links[0]["chain_start"] is True
    # Each later link actually points at its predecessor's digest.
    assert links[1]["previous_hash"] == links[0]["hash"]
    assert links[2]["previous_hash"] == links[1]["hash"]
    assert {link["status"] for link in links} == {STATUS_VERIFIED}


def test_links_are_capped_without_distorting_counts(tmp_path, signing_secret):
    db = make_db(tmp_path)
    for i in range(12):
        log_event("code_generated", "tester", f"a{i}", db_path=db)

    report = sweep(db, max_links=5)

    assert len(report["links"]) == 5
    assert counts(report)[STATUS_VERIFIED] == 12  # counts stay exact


def test_broken_samples_are_capped_and_flagged(tmp_path, signing_secret):
    db = make_db(tmp_path)
    ids = [log_event("code_generated", "tester", f"a{i}", db_path=db) for i in range(6)]

    conn = sqlite3.connect(str(db))
    for entry_id in ids:
        conn.execute("UPDATE audit_trail SET action = ? WHERE id = ?", ("edited", entry_id))
    conn.commit()
    conn.close()

    report = sweep(db, max_broken_samples=2)

    assert counts(report)[STATUS_BROKEN] == 6
    assert len(report["broken_samples"]) == 2
    assert report["broken_samples_truncated"] is True


# ---------------------------------------------------------------------------
# The CLI contract.
# ---------------------------------------------------------------------------
def _run_cli(db: Path, *extra):
    return subprocess.run(
        [sys.executable, "tools/audit/chain_sweep.py", "--db-path", str(db), *extra],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT),
             "ICDEV_STORAGE_BACKEND": "sqlite"},
        timeout=120,
    )


def test_db_path_is_honoured_on_a_postgresql_primary(tmp_path, signing_secret, monkeypatch):
    """--db-path must open the file it names, not the ambient database.

    Regression: `get_connection` reads ICDEV_STORAGE_BACKEND and only honours
    db_path on SQLite. On this repo's PostgreSQL-primary install the sweep
    therefore connected to PostgreSQL and reported a clean chain for a tampered
    file it had never opened. The rest of the suite could not catch it because
    conftest pins the backend to sqlite, which is exactly what masked the bug.
    """
    db = make_db(tmp_path)
    entry_id = log_event("code_generated", "tester", "a", db_path=db)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE audit_trail SET action = ? WHERE id = ?", ("edited", entry_id))
    conn.commit()
    conn.close()

    # Simulate the real deployment: PostgreSQL is the configured primary.
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")

    report = sweep_chain(db_path=db)

    assert counts(report)[STATUS_BROKEN] == 1, f"swept the ambient DB, not --db-path: {report}"
    # And the ambient setting is left exactly as it was found.
    assert __import__("os").environ["ICDEV_STORAGE_BACKEND"] == "postgresql"


def test_cli_json_reports_the_three_required_counts(tmp_path, signing_secret):
    db = make_db(tmp_path)
    legacy_rows(db, 3)
    for i in range(2):
        log_event("code_generated", "tester", f"a{i}", db_path=db)

    result = _run_cli(db, "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout[result.stdout.find("{"):])
    assert payload["counts"][STATUS_VERIFIED] == 2
    assert payload["counts"][STATUS_PRE_CUTOVER] == 3
    assert payload["counts"][STATUS_BROKEN] == 0


def test_cli_gate_exits_nonzero_only_when_broken(tmp_path, signing_secret):
    db = make_db(tmp_path)
    entry_id = log_event("code_generated", "tester", "a", db_path=db)

    assert _run_cli(db, "--gate").returncode == 0

    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE audit_trail SET action = ? WHERE id = ?", ("edited", entry_id))
    conn.commit()
    conn.close()

    assert _run_cli(db, "--gate").returncode == 1
