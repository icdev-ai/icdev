# CUI // SP-CTI
"""Regression: ``known_safe_persistence_modules`` is actually consulted (task-1b49742e56).

The allowlist and its loader ``scanners._load_safe_persistence_modules`` were
added on 2026-07-28 (task-e74c6d806f). ``run_signature_scan`` only ever consulted
the *dynamic_import* allowlist, so the persistence loader had ZERO call sites:
every entry suppressed nothing, and each 6h ``integrity_monitor`` sweep re-raised
the same reviewed false positives as fresh kanban cards. Assessment 274 re-reported
``cli/provision_db.py`` and ``network/seed_sops.py`` sixteen days after they were
allowlisted, and raised this task for a third file.

Deliberately a separate module from ``test_integrity_scanners.py``: these two
tests are pure — no scanner shell-out, no optional third-party tool — so they are
gateable in CI, whereas that file's end-to-end ``secret_detector`` test depends on
whether ``detect-secrets`` happens to be installed on the runner.
"""
from pathlib import Path

import pytest

from tools.integrity import scanners


@pytest.fixture
def staged_env(icdev_db, tmp_path, monkeypatch):
    """Point get_connection() at the temp SQLite db and quarantine at tmp."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    return icdev_db


def _new_assessment() -> int:
    """Insert a bare quarantine assessment row and return its id (FK target)."""
    from tools.db.storage import get_connection
    from tools.integrity.db.init_db import init_db

    conn = get_connection()
    try:
        init_db(conn)
        cur = conn.execute(
            "INSERT INTO integrity_assessments "
            "(source_type, source_ref, mode, status) VALUES (?, ?, ?, ?)",
            ("local", "fixture", "provenance_blind", "quarantine"),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _findings(aid):
    """(file_path, line) for every persisted finding, path normalized to posix."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT file_path, line FROM integrity_findings "
            "WHERE assessment_id = ? ORDER BY id",
            (aid,),
        ).fetchall()
    finally:
        conn.close()
    # file_path is persisted OS-native (os.path.relpath), hence "hooks\guard.py"
    # on Windows — which is why the matcher normalizes with Path.as_posix().
    return {(Path(r["file_path"]).as_posix(), r["line"]) for r in rows}


def test_known_safe_persistence_module_is_suppressed(staged_env, tmp_path, monkeypatch):
    """A persistence hit in an allowlisted module is dropped — and only that.

    The same hit in an unlisted module, and a non-persistence hit in the
    allowlisted one, are both kept: the allowlist excuses one capability
    category in one file, never the file.
    """
    aid = _new_assessment()
    staged = tmp_path / "x"
    monkeypatch.setattr(
        scanners, "_load_safe_persistence_modules", lambda: frozenset({"hooks/guard.py"})
    )
    canned = [
        {
            "rule_id": "sipa-persistence-generic",
            "category": "persistence",
            "file": str(staged / "hooks" / "guard.py"),
            "line": 10,
            "message": "persistence",
        },
        {
            "rule_id": "sipa-persistence-generic",
            "category": "persistence",
            "file": str(staged / "dropper.py"),
            "line": 20,
            "message": "persistence",
        },
        {
            "rule_id": "sipa-decode-then-exec-py",
            "category": "decode_then_exec",
            "file": str(staged / "hooks" / "guard.py"),
            "line": 30,
            "message": "decode-then-exec",
        },
    ]
    monkeypatch.setattr(scanners, "_detect_signatures", lambda s: canned)

    result = scanners.run_signature_scan(aid, staged_path=str(staged))
    assert result["success"] is True

    kept = _findings(aid)
    assert ("hooks/guard.py", 10) not in kept, (
        "allowlisted persistence false positive was persisted — "
        "known_safe_persistence_modules is not consulted"
    )
    assert ("dropper.py", 20) in kept
    assert ("hooks/guard.py", 30) in kept


def test_dynamic_import_allowlist_still_suppresses(staged_env, tmp_path, monkeypatch):
    """The pre-existing dynamic_import suppression survived the refactor.

    Both categories now route through one ``_suppressed`` predicate; this pins the
    half that already worked so consolidating them cannot silently drop it.
    """
    aid = _new_assessment()
    staged = tmp_path / "y"
    monkeypatch.setattr(
        scanners, "_load_safe_dynamic_import_modules", lambda: frozenset({"llm/loader.py"})
    )
    canned = [
        {
            "rule_id": "sipa-dynamic-import-py",
            "category": "dynamic_import",
            "file": str(staged / "llm" / "loader.py"),
            "line": 11,
            "message": "dynamic import",
        },
        {
            "rule_id": "sipa-dynamic-import-py",
            "category": "dynamic_import",
            "file": str(staged / "evil.py"),
            "line": 22,
            "message": "dynamic import",
        },
    ]
    monkeypatch.setattr(scanners, "_detect_signatures", lambda s: canned)

    scanners.run_signature_scan(aid, staged_path=str(staged))
    kept = _findings(aid)
    assert ("llm/loader.py", 11) not in kept
    assert ("evil.py", 22) in kept


def test_shared_checks_is_allowlisted_for_persistence():
    """The reviewed false positive of task-1b49742e56 is in the shipped config.

    ``tools/hooks/shared_checks.py`` names ``/etc/cron.d/pwn`` five times — in a
    section comment, two docstrings and the BLOCKED message of the
    write-containment guard that REFUSES writes to exactly that path.
    ``sipa-persistence-generic`` is a ``languages: [generic]`` regex and cannot
    tell prose from code.

    Both the repo-relative and the ``tools/``-root-relative forms must be listed:
    the matcher is ``str.endswith``, and the Genesis self-scan stages ``tools/``
    as its root, so it reports the finding as ``hooks/shared_checks.py``.
    """
    safe = scanners._load_safe_persistence_modules()
    assert "tools/hooks/shared_checks.py" in safe
    assert "hooks/shared_checks.py" in safe
    for form in ("tools/hooks/shared_checks.py", "hooks/shared_checks.py"):
        assert scanners._is_safe_persistence_module(form, safe)
