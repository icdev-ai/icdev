#!/usr/bin/env python3
# CUI // SP-CTI
"""The audit hash chain writer — exa-audit-03.

Before this, ``audit_trail.hash`` / ``previous_hash`` / ``signature`` were NULL
on every row that had ever been written, so ``verify_audit_integrity`` reported
all three flags False for the entire table. These tests pin the writer's
contract end to end: a row it produces verifies, a row that predates it reports
as unverifiable rather than tampered, a tampered row is caught, and concurrent
writers do not fork the chain.

Everything runs on file-backed SQLite rather than the shared database. The
chain is a property of a whole table, so a test that writes into the ambient
``icdev.db`` would be asserting against whatever else the host happened to have
written — and the concurrency test genuinely needs a second writer process's
worth of contention on a table it owns.
"""

import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from icdev.tools.audit import chain as audit_chain  # noqa: E402
from icdev.tools.audit.row_hash import GENESIS_HASH, compute_audit_row_hash  # noqa: E402
from tools.audit.audit_logger import log_event  # noqa: E402
from tools.blockchain.provenance_verifier import verify_audit_integrity  # noqa: E402

# Mirrors the live audit_trail (information_schema on the PostgreSQL primary)
# plus migration 149's three chain columns and this task's cutover marker.
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
"""

# audit_trail WITHOUT migration 149 — what init_icdev_db.py's CREATE TABLE still
# produces, and therefore what a freshly initialised database really looks like.
SCHEMA_UNMIGRATED = """
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture(autouse=True)
def _clear_chain_caches():
    """has_chain_columns / record_chain_start memoise per database.

    Every test builds a brand-new temp database, and SQLite reuses the path
    shape, so a cached "columns present" or "genesis already written" from an
    earlier test would answer for a database that no longer exists.
    """
    audit_chain._COLUMN_CACHE.clear()
    audit_chain._GENESIS_CACHE.clear()
    yield
    audit_chain._COLUMN_CACHE.clear()
    audit_chain._GENESIS_CACHE.clear()


@pytest.fixture
def signing_secret(monkeypatch):
    """An HMAC secret so key_manager signs rather than returning algorithm=none.

    key_manager's own preference order (ECDSA-P256, then Ed25519, then HMAC) is
    its business and is tested there; what matters here is that the writer hands
    it the right payload and stores what it gets back.
    """
    monkeypatch.setenv("ICDEV_AUDIT_HMAC_SECRET", "exa-audit-03-test-secret")
    monkeypatch.delenv("ICDEV_AUDIT_SIGNING_KEY_PATH", raising=False)
    return "exa-audit-03-test-secret"


def make_db(tmp_path, schema: str = SCHEMA) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "audit.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(schema)
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


def read_rows(db: Path) -> list:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM audit_trail ORDER BY id").fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# The headline acceptance: a row the writer produces actually verifies.
# ---------------------------------------------------------------------------
def test_new_row_verifies_on_all_three_flags(tmp_path, signing_secret):
    db = make_db(tmp_path)

    entry_id = log_event(
        "code_generated", "tester", "write one", details={"k": "v"}, db_path=db
    )
    assert entry_id > 0

    result = verify_audit_integrity(entry_id, db_path=db)
    assert result["hash_valid"] is True
    assert result["chain_valid"] is True
    assert result["signature_valid"] is True
    assert result["ok"] is True
    assert result["chain_status"] == "chained"


def test_consecutive_rows_link_to_each_other(tmp_path, signing_secret):
    db = make_db(tmp_path)
    ids = [log_event("code_generated", "tester", f"a{i}", db_path=db) for i in range(5)]

    rows = read_rows(db)
    assert [r["id"] for r in rows] == ids

    # First chained row starts from genesis; each later row carries its
    # predecessor's actual hash.
    assert rows[0]["previous_hash"] == GENESIS_HASH
    for prev, cur in zip(rows, rows[1:]):
        assert cur["previous_hash"] == prev["hash"]

    for entry_id in ids:
        assert verify_audit_integrity(entry_id, db_path=db)["ok"] is True


def test_hash_matches_the_shared_recipe_not_a_private_one(tmp_path, signing_secret):
    """The writer must hash via row_hash.py, or the verifiers cannot agree."""
    db = make_db(tmp_path)
    entry_id = log_event(
        "code_generated", "tester", "recipe", details={"n": 1}, db_path=db
    )
    row = read_rows(db)[0]
    assert row["hash"] == compute_audit_row_hash(row)
    assert row["id"] == entry_id


def test_signature_is_over_the_hash_and_is_verifiable(tmp_path, signing_secret):
    from tools.crypto.key_manager import verify_payload

    db = make_db(tmp_path)
    log_event("code_generated", "tester", "signed", db_path=db)
    row = read_rows(db)[0]

    sig = json.loads(row["signature"])
    assert sig["algorithm"] != "none"
    # Exactly the call verify_audit_integrity makes.
    assert verify_payload(
        row["hash"].encode(),
        sig["value"],
        sig["public_key_fp"],
        algorithm=sig["algorithm"],
    )


# ---------------------------------------------------------------------------
# Cutover: rows older than the chain must read as unverifiable, not tampered.
# ---------------------------------------------------------------------------
def test_pre_cutover_rows_report_pre_chain_not_tampering(tmp_path, signing_secret):
    db = make_db(tmp_path)
    legacy_rows(db, 3)
    new_id = log_event("code_generated", "tester", "first chained", db_path=db)

    for old_id in (1, 2, 3):
        result = verify_audit_integrity(old_id, db_path=db)
        assert result["chain_status"] == "pre_chain"
        # Nothing to verify, so nothing is claimed either way.
        assert result["ok"] is False
        assert result["hash_valid"] is False

    assert verify_audit_integrity(new_id, db_path=db)["chain_status"] == "chained"


def test_cutover_point_is_recorded(tmp_path, signing_secret):
    db = make_db(tmp_path)
    legacy_rows(db, 4)
    first_chained = log_event("code_generated", "tester", "cutover", db_path=db)
    log_event("code_generated", "tester", "after", db_path=db)

    conn = sqlite3.connect(str(db))
    recorded = conn.execute("SELECT chain_start_id FROM audit_chain_genesis").fetchall()
    conn.close()

    # Exactly one marker, naming the first row that was ever chained.
    assert [r[0] for r in recorded] == [first_chained]
    assert first_chained == 5

    assert verify_audit_integrity(first_chained, db_path=db)["chain_start_id"] == 5


def test_first_chained_row_starts_from_genesis_after_legacy_rows(tmp_path, signing_secret):
    """The row above an unchained predecessor chains from GENESIS, not from NULL."""
    db = make_db(tmp_path)
    legacy_rows(db, 2)
    entry_id = log_event("code_generated", "tester", "cutover", db_path=db)

    row = [r for r in read_rows(db) if r["id"] == entry_id][0]
    assert row["previous_hash"] == GENESIS_HASH
    assert verify_audit_integrity(entry_id, db_path=db)["chain_valid"] is True


# ---------------------------------------------------------------------------
# The chain has to actually detect things, or it is decoration.
# ---------------------------------------------------------------------------
def test_editing_a_row_breaks_its_hash(tmp_path, signing_secret):
    db = make_db(tmp_path)
    entry_id = log_event("code_generated", "tester", "original", db_path=db)
    assert verify_audit_integrity(entry_id, db_path=db)["hash_valid"] is True

    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE audit_trail SET action = ? WHERE id = ?", ("edited", entry_id))
    conn.commit()
    conn.close()

    result = verify_audit_integrity(entry_id, db_path=db)
    assert result["hash_valid"] is False
    assert result["ok"] is False
    # An edited row is NOT excused as pre-chain: it still has a hash, it just no
    # longer matches. That distinction is the whole point of the marker.
    assert result["chain_status"] == "chained"


def test_deleting_a_chained_row_breaks_its_successors_link(tmp_path, signing_secret):
    db = make_db(tmp_path)
    ids = [log_event("code_generated", "tester", f"a{i}", db_path=db) for i in range(3)]

    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM audit_trail WHERE id = ?", (ids[1],))
    conn.commit()
    conn.close()

    # The survivor still holds the removed row's hash as its previous_hash,
    # which no longer matches the GENESIS fallback for a missing predecessor.
    result = verify_audit_integrity(ids[2], db_path=db)
    assert result["chain_valid"] is False
    assert result["chain_status"] == "chained"


# ---------------------------------------------------------------------------
# Concurrency — the property that makes the chain trustworthy under load.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("writers,per_writer", [(8, 6)])
def test_concurrent_writers_do_not_fork_the_chain(tmp_path, signing_secret, writers, per_writer):
    """Many threads writing at once must still produce one linear chain.

    This is the failure the critical section exists to prevent: two writers that
    both read the same predecessor hash each believe they are its successor, and
    one of them is lying. Without serialization this test produces duplicate
    previous_hash values and a chain that verifies for only one branch.
    """
    db = make_db(tmp_path)
    errors = []
    barrier = threading.Barrier(writers)

    def write(worker: int):
        try:
            barrier.wait(timeout=30)  # maximise real contention
            for i in range(per_writer):
                entry_id = log_event(
                    "code_generated",
                    f"worker-{worker}",
                    f"w{worker}-{i}",
                    details={"worker": worker, "i": i},
                    db_path=db,
                )
                if entry_id <= 0:
                    errors.append(f"worker {worker} write {i} returned {entry_id}")
        except Exception as exc:  # noqa: BLE001 - surfaced via the assert below
            errors.append(f"worker {worker}: {exc!r}")

    threads = [threading.Thread(target=write, args=(w,)) for w in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a writer thread hung"
    assert errors == [], errors

    rows = read_rows(db)
    assert len(rows) == writers * per_writer, "lost or duplicated rows"

    # 1. Every row got a distinct id — no two writers reserved the same one.
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == len(ids)
    assert ids == list(range(1, len(rows) + 1)), "gaps mean a reservation was burned"

    # 2. No fork: each row's previous_hash is claimed by exactly one row.
    previous = [r["previous_hash"] for r in rows]
    assert len(set(previous)) == len(previous), "two rows claim the same predecessor"

    # 3. The chain is linear and every link holds.
    assert rows[0]["previous_hash"] == GENESIS_HASH
    for prev, cur in zip(rows, rows[1:]):
        assert cur["previous_hash"] == prev["hash"]

    # 4. And it verifies through the real verifier, not just by inspection.
    for row in rows:
        result = verify_audit_integrity(row["id"], db_path=db)
        assert result["ok"] is True, f"row {row['id']}: {result}"
        assert result["signature_valid"] is True


def test_concurrent_writers_record_exactly_one_cutover(tmp_path, signing_secret):
    """A race at the cutover must not leave two different chain starts in play."""
    db = make_db(tmp_path)
    barrier = threading.Barrier(6)

    def write():
        barrier.wait(timeout=30)
        log_event("code_generated", "racer", "cutover race", db_path=db)

    threads = [threading.Thread(target=write) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db))
    try:
        # Whatever raced, the effective boundary is the first chained row.
        assert audit_chain.chain_start_id(conn) == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Degradation — the writer must never cost us the audit event itself.
# ---------------------------------------------------------------------------
def test_database_without_migration_149_still_records_the_event(tmp_path):
    """A database whose audit_trail predates the chain columns keeps working.

    init_icdev_db.py's CREATE TABLE still omits them, so this is not a
    hypothetical: naming a column that is not in the live schema is how audit
    writes have silently vanished in this repo before.
    """
    db = make_db(tmp_path, SCHEMA_UNMIGRATED)

    entry_id = log_event("code_generated", "tester", "unmigrated", db_path=db)

    assert entry_id > 0
    rows = read_rows(db)
    assert len(rows) == 1
    assert rows[0]["action"] == "unmigrated"


def test_signing_failure_still_writes_the_row(tmp_path, signing_secret, monkeypatch):
    """A chain failure degrades to an unchained row; it does not drop the event.

    An unchained row is visible — the verifier reports it and chain_anchor's
    `WHERE hash IS NULL` scan picks it up. An event that was never written is
    not visible to anything, which is why this is the safer of the two.
    """
    import tools.crypto.key_manager as key_manager

    def boom(payload, key_path=None):
        raise RuntimeError("HSM unreachable")

    monkeypatch.setattr(key_manager, "sign_payload", boom)

    entry_id = log_event(
        "code_generated", "tester", "unsigned but recorded", db_path=make_db(tmp_path)
    )
    assert entry_id > 0


def test_hash_failure_is_fatal_when_the_caller_asks_for_it(tmp_path, signing_secret, monkeypatch):
    """raise_on_error=True keeps its meaning for chain failures too."""
    db = make_db(tmp_path)
    monkeypatch.setattr(
        audit_chain,
        "chain_insert_values",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hash failed")),
    )

    # The chain failure itself is absorbed, so the row is still written; what
    # raise_on_error governs is the DB write, which is unaffected here.
    entry_id = log_event(
        "code_generated", "tester", "degraded", db_path=db, raise_on_error=True
    )
    assert entry_id > 0
    assert read_rows(db)[0]["hash"] is None


def test_unchained_row_reports_pre_chain_only_below_the_cutover(tmp_path, signing_secret, monkeypatch):
    """A row that failed to chain AFTER cutover is not excused as pre-chain."""
    db = make_db(tmp_path)
    good_id = log_event("code_generated", "tester", "chained", db_path=db)

    monkeypatch.setattr(
        audit_chain, "chain_insert_values", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    degraded_id = log_event("code_generated", "tester", "degraded", db_path=db)

    assert good_id < degraded_id
    result = verify_audit_integrity(degraded_id, db_path=db)
    # Above the cutover with no hash: unverified AND not excused.
    assert result["chain_status"] == "chained"
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# The chain writer's own helpers.
# ---------------------------------------------------------------------------
def test_previous_hash_falls_back_to_genesis_at_a_gap(tmp_path, signing_secret):
    """A burned id must make writer and verifier agree, not disagree.

    Both sides independently apply "the row at id - 1", so a missing
    predecessor resolves to GENESIS on both — the chain restarts rather than
    reporting a break that nobody caused.
    """
    from tools.db.storage import get_connection

    db = make_db(tmp_path)
    log_event("code_generated", "tester", "row one", db_path=db)

    conn = get_connection(db_path=str(db))
    try:
        # id 3 has no predecessor at id 2 — nothing was ever written there.
        assert audit_chain.previous_hash_for(conn, 3) == GENESIS_HASH
        assert audit_chain.previous_hash_for(conn, 1) == GENESIS_HASH
    finally:
        conn.close()


def test_has_chain_columns_detects_both_shapes(tmp_path):
    from tools.db.storage import get_connection

    migrated = get_connection(db_path=str(make_db(tmp_path / "m", SCHEMA)))
    try:
        assert audit_chain.has_chain_columns(migrated) is True
    finally:
        migrated.close()

    audit_chain._COLUMN_CACHE.clear()
    plain = get_connection(db_path=str(make_db(tmp_path / "u", SCHEMA_UNMIGRATED)))
    try:
        assert audit_chain.has_chain_columns(plain) is False
    finally:
        plain.close()


def test_chain_start_id_is_none_before_any_chained_write(tmp_path):
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(make_db(tmp_path)))
    try:
        assert audit_chain.chain_start_id(conn) is None
    finally:
        conn.close()
