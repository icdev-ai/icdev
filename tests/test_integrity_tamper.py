# CUI // SP-CTI
"""Tests for SIPA tamper digest baseline + re-verify (sipa-ingest-03).

Covers the acceptance criteria: after staging, a SHA-256 recursive directory
digest of the quarantined tree is recorded in ``integrity_assessments.dir_digest``;
the digest is stable for identical input and changes when a file is modified; and
``reverify()`` flags a ``tamper_mismatch`` finding when the staged tree changes
between stage and assess.

SQLite-backed via the shared ``icdev_db`` fixture; quarantine is redirected to a
tmp dir so staging never touches the repo tree.
"""
from pathlib import Path

import pytest

from tools.integrity import ingest

_HEX = set("0123456789abcdef")


@pytest.fixture
def staged_env(icdev_db, tmp_path, monkeypatch):
    """Point get_connection() at the temp SQLite db and quarantine at tmp."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    return icdev_db


def _make_target(root, body="print('hello')\n"):
    src = Path(root)
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.py").write_text(body, encoding="utf-8")
    (src / "README.md").write_text("# demo\n", encoding="utf-8")
    return src


def _stored_digest(aid):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT dir_digest FROM integrity_assessments WHERE id = ?", (aid,)
        ).fetchone()
    finally:
        conn.close()
    return row["dir_digest"] if row else None


def _findings(aid):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT source_scanner, finding_type, severity, detail "
            "FROM integrity_findings WHERE assessment_id = ?",
            (aid,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Baseline digest is recorded on stage
# --------------------------------------------------------------------------- #
def test_stage_records_dir_digest(staged_env, tmp_path):
    src = _make_target(tmp_path / "target_pkg")
    result = ingest.stage(str(src))

    digest = result["dir_digest"]
    assert isinstance(digest, str)
    assert len(digest) == 64 and set(digest) <= _HEX  # SHA-256 hex

    # Persisted onto the assessment row, identical to the returned value.
    assert _stored_digest(result["assessment_id"]) == digest


# --------------------------------------------------------------------------- #
# Digest stability vs. sensitivity
# --------------------------------------------------------------------------- #
def test_digest_stable_for_identical_input(staged_env, tmp_path):
    a = _make_target(tmp_path / "pkg_a")
    b = _make_target(tmp_path / "pkg_b")  # same relative paths + bytes

    da = ingest.stage(str(a))["dir_digest"]
    db = ingest.stage(str(b))["dir_digest"]

    assert da == db  # digest is over rel_path + content, not absolute location


def test_dir_digest_changes_when_file_modified(staged_env, tmp_path):
    src = _make_target(tmp_path / "pkg")
    before = ingest._dir_digest(src)

    (src / "main.py").write_text("print('tampered')\n", encoding="utf-8")
    after = ingest._dir_digest(src)

    assert before != after


# --------------------------------------------------------------------------- #
# reverify() — clean tree
# --------------------------------------------------------------------------- #
def test_reverify_clean_returns_no_tamper(staged_env, tmp_path):
    src = _make_target(tmp_path / "pkg")
    result = ingest.stage(str(src))
    aid = result["assessment_id"]

    verdict = ingest.reverify(aid)

    assert verdict["tampered"] is False
    assert verdict["finding_id"] is None
    assert verdict["baseline_digest"] == verdict["current_digest"] == result["dir_digest"]
    assert _findings(aid) == []  # no tamper_mismatch written on a clean tree


# --------------------------------------------------------------------------- #
# reverify() — tampered tree
# --------------------------------------------------------------------------- #
def test_reverify_detects_modified_file(staged_env, tmp_path):
    src = _make_target(tmp_path / "pkg")
    result = ingest.stage(str(src))
    aid = result["assessment_id"]

    # Mutate the QUARANTINED copy between stage and assess (simulated tamper).
    staged_main = Path(result["staged_path"]) / "main.py"
    staged_main.write_text("import os; os.system('rm -rf /')\n", encoding="utf-8")

    verdict = ingest.reverify(aid)

    assert verdict["tampered"] is True
    assert verdict["finding_id"]
    assert verdict["baseline_digest"] == result["dir_digest"]
    assert verdict["current_digest"] != result["dir_digest"]

    findings = _findings(aid)
    assert len(findings) == 1
    f = findings[0]
    assert f["source_scanner"] == "tamper"
    assert f["finding_type"] == "tamper_mismatch"
    assert f["severity"] == "critical"
    assert result["dir_digest"] in f["detail"]  # baseline recorded in the finding detail


def test_reverify_detects_added_file(staged_env, tmp_path):
    src = _make_target(tmp_path / "pkg")
    result = ingest.stage(str(src))
    aid = result["assessment_id"]

    (Path(result["staged_path"]) / "backdoor.py").write_text(
        "exec(__import__('base64').b64decode('cHJpbnQoMSk='))\n", encoding="utf-8"
    )

    verdict = ingest.reverify(aid)
    assert verdict["tampered"] is True
    assert [f["finding_type"] for f in _findings(aid)] == ["tamper_mismatch"]


def test_reverify_missing_baseline_is_not_tamper(staged_env, tmp_path):
    # An assessment row with no recorded dir_digest cannot be compared -> not tamper.
    from tools.db.storage import get_connection
    from tools.integrity.db.init_db import init_db

    conn = get_connection()
    try:
        init_db(conn)
        cur = conn.execute(
            "INSERT INTO integrity_assessments "
            "(source_type, source_ref, mode, status) VALUES (?, ?, ?, ?)",
            ("local", "x", "provenance_blind", "quarantine"),
        )
        conn.commit()
        aid = int(cur.lastrowid)
    finally:
        conn.close()

    verdict = ingest.reverify(aid)
    assert verdict["tampered"] is False
    assert verdict["finding_id"] is None
    assert _findings(aid) == []
