# CUI // SP-CTI
"""Tests for SIPA quarantine-first ingest (tools/integrity/ingest.py).

Covers the two acceptance criteria from sipa-ingest-01 — a local directory is
staged into quarantine with an ``integrity_assessments`` row created, and a
disallowed scheme is rejected with no row written — plus mode auto-detection,
``file://`` resolution, missing-path refusal, and remote-fetch deferral.

SQLite-backed via the shared ``icdev_db`` fixture; quarantine is redirected to a
tmp dir so staging never touches the repo tree.
"""
from pathlib import Path

import pytest

from tools.integrity import ingest


@pytest.fixture
def staged_env(icdev_db, tmp_path, monkeypatch):
    """Point get_connection() at the temp SQLite db and quarantine at tmp."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    return icdev_db


def _make_target(tmp_path):
    src = tmp_path / "target_pkg"
    src.mkdir()
    (src / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (src / "README.md").write_text("# demo\n", encoding="utf-8")
    return src


def _fetch_assessment(aid):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT source_type, status, mode, source_ref, tenant_id, classification "
            "FROM integrity_assessments WHERE id = ?",
            (aid,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    cols = ["source_type", "status", "mode", "source_ref", "tenant_id", "classification"]
    return row if hasattr(row, "keys") else dict(zip(cols, row))


def _count_assessments():
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM integrity_assessments").fetchone()[0]
    finally:
        conn.close()


def test_stage_local_dir_creates_row_and_quarantine(staged_env, tmp_path):
    src = _make_target(tmp_path)

    result = ingest.stage(str(src))

    assert result["source_type"] == "local"
    aid = result["assessment_id"]
    assert aid and aid > 0

    # Files copied into quarantine (never executed — only copied).
    staged = Path(result["staged_path"])
    assert staged.is_dir()
    assert staged.name == str(aid)
    assert (staged / "main.py").read_text(encoding="utf-8") == "print('hello')\n"
    assert (staged / "README.md").exists()

    # Assessment row recorded in quarantine status, provenance_blind, CUI defaults.
    row = _fetch_assessment(aid)
    assert row is not None
    assert row["source_type"] == "local"
    assert row["status"] == "quarantine"
    assert row["mode"] == "provenance_blind"
    assert row["tenant_id"] == "default"
    assert row["classification"] == "CUI"


def test_stage_rejects_disallowed_scheme(staged_env):
    with pytest.raises(ingest.IngestRejected):
        ingest.stage("ftp://evil.example.com/payload.tar.gz")
    # Rejected before any row is created.
    assert _count_assessments() == 0


def test_stage_provenance_aware_with_provenance_handle(staged_env, tmp_path):
    src = _make_target(tmp_path)
    result = ingest.stage(str(src), project_id="sipa")
    row = _fetch_assessment(result["assessment_id"])
    assert row["mode"] == "provenance_aware"


def test_stage_file_uri_resolves_to_local(staged_env, tmp_path):
    src = _make_target(tmp_path)
    result = ingest.stage(src.as_uri())  # file:///... cross-platform
    assert result["source_type"] == "local"
    assert (Path(result["staged_path"]) / "main.py").exists()


def test_stage_missing_path_rejected(staged_env, tmp_path):
    with pytest.raises(ingest.IngestRejected):
        ingest.stage(str(tmp_path / "does_not_exist"))
    assert _count_assessments() == 0


def test_stage_remote_git_routes_to_clone(staged_env, monkeypatch):
    # sipa-ingest-02: an allowlisted github URL is now cloned (not deferred). Stub the
    # subprocess clone so the test never touches the network; assert it was invoked
    # with the validated URL and a row was recorded in quarantine.
    seen = {}

    def _fake_clone(url, dest, **kwargs):
        seen["url"] = url
        seen["dest"] = dest
        # A real `git clone` materializes the tree; mirror that so the post-clone
        # SHA-256 tamper-digest baseline (sipa-ingest-03) has a directory to hash.
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "main.py").write_text("print('cloned')\n", encoding="utf-8")

    monkeypatch.setattr(ingest, "_git_clone", _fake_clone)
    result = ingest.stage("https://github.com/org/repo.git")
    assert result["source_type"] == "git"
    assert seen["url"] == "https://github.com/org/repo.git"
    assert result["dir_digest"] and len(result["dir_digest"]) == 64
    assert _count_assessments() == 1
