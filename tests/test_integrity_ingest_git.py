# CUI // SP-CTI
"""Tests for SIPA git-clone ingest (sipa-ingest-02).

Covers the acceptance criteria for the remote-git path of ``tools/integrity/ingest.py``:

  * a git source is shallow-cloned into quarantine with a FIXED-arg, ``shell=False``
    subprocess and an ``integrity_assessments`` row recorded — exercised against a
    tiny *local* bare repo over ``file://`` so no network is required;
  * a malformed / disallowed URL is rejected with :class:`ingest.IngestRejected`
    *before* any subprocess is launched (monkeypatched ``subprocess.run`` proves the
    process is never spawned);
  * **no** ``shell=True`` appears anywhere under ``tools/integrity/`` (regression);
  * embedded credentials are stripped from anything destined for a log sink.

SQLite-backed via the shared ``icdev_db`` fixture; quarantine is redirected to a
tmp dir so cloning never touches the repo tree.
"""
import os
import subprocess
from pathlib import Path

import pytest

from tools.integrity import ingest

_GIT = ["git"]
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "sipa-test",
    "GIT_AUTHOR_EMAIL": "sipa@test.local",
    "GIT_COMMITTER_NAME": "sipa-test",
    "GIT_COMMITTER_EMAIL": "sipa@test.local",
    "GIT_TERMINAL_PROMPT": "0",
}

_has_git = pytest.mark.skipif(
    not __import__("shutil").which("git"),
    reason="git executable not available on PATH",
)


@pytest.fixture
def staged_env(icdev_db, tmp_path, monkeypatch):
    """Point get_connection() at the temp SQLite db and quarantine at tmp."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    return icdev_db


def _make_local_repo(tmp_path):
    """Create a tiny real git repo on disk and return its ``file://`` URL."""
    work = tmp_path / "src_repo"
    work.mkdir()
    (work / "main.py").write_text("print('from cloned repo')\n", encoding="utf-8")
    (work / "README.md").write_text("# demo repo\n", encoding="utf-8")

    def run(*args):
        subprocess.run(
            [*_GIT, *args], cwd=work, env=_GIT_ENV, check=True, capture_output=True
        )

    run("init", "-q")
    run("add", "-A")
    run("commit", "-q", "-m", "initial commit")
    return work.as_uri()  # file:///.../src_repo (cross-platform)


def _count_assessments():
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM integrity_assessments").fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Happy path — real clone from a local bare repo (no network)
# --------------------------------------------------------------------------- #
@_has_git
def test_stage_git_clones_local_repo_into_quarantine(staged_env, tmp_path):
    url = _make_local_repo(tmp_path)

    result = ingest.stage(url, source_type="git")

    assert result["source_type"] == "git"
    aid = result["assessment_id"]
    assert aid and aid > 0

    staged = Path(result["staged_path"])
    assert staged.is_dir()
    assert staged.name == str(aid)
    # The repo content was cloned (copied), never executed.
    assert (staged / "main.py").read_text(encoding="utf-8") == "print('from cloned repo')\n"
    assert (staged / "README.md").exists()
    # Shallow clone keeps the working tree but not deep history.
    assert (staged / ".git").exists()

    assert _count_assessments() == 1


@_has_git
def test_stage_git_clone_is_shallow(staged_env, tmp_path):
    url = _make_local_repo(tmp_path)
    result = ingest.stage(url, source_type="git")
    staged = Path(result["staged_path"])
    # A --depth 1 clone records exactly one commit in the shallow grafts file.
    rev = subprocess.run(
        [*_GIT, "-C", str(staged), "rev-list", "--count", "HEAD"],
        env=_GIT_ENV, check=True, capture_output=True, text=True,
    )
    assert rev.stdout.strip() == "1"


# --------------------------------------------------------------------------- #
# Validation — disallowed / malformed URLs rejected BEFORE any subprocess
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_subprocess(monkeypatch):
    """Make any ``subprocess.run`` from the ingest module an immediate failure.

    If validation lets a bad URL through to the clone, this fixture turns that into
    a loud test failure instead of a silent (or networked) process launch.
    """
    def _boom(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError(f"subprocess.run was called for a rejected URL: {args!r}")

    monkeypatch.setattr(ingest.subprocess, "run", _boom)
    return monkeypatch


def test_stage_git_rejects_disallowed_host_before_subprocess(staged_env, no_subprocess):
    with pytest.raises(ingest.IngestRejected):
        ingest.stage("https://evil.example.com/org/repo.git", source_type="git")
    assert _count_assessments() == 0


def test_stage_git_rejects_non_https_scheme_before_subprocess(staged_env, no_subprocess):
    # http:// is not in the scheme allowlist — refused at the gate, no clone.
    with pytest.raises(ingest.IngestRejected):
        ingest.stage("http://github.com/org/repo.git", source_type="git")
    assert _count_assessments() == 0


def test_stage_git_rejects_url_without_host_before_subprocess(staged_env, no_subprocess):
    with pytest.raises(ingest.IngestRejected):
        ingest.stage("https:///no-host-here/repo.git", source_type="git")
    assert _count_assessments() == 0


def test_validate_git_url_rejects_ssh_and_git_scheme():
    hosts = {"github.com", "gitlab.com"}
    for bad in (
        "git@github.com:org/repo.git",        # scp-style ssh
        "ssh://git@github.com/org/repo.git",  # ssh
        "git://github.com/org/repo.git",      # unauthenticated git protocol
        "ftp://github.com/org/repo.git",      # ftp
        "/etc/passwd",                        # bare path, no scheme
    ):
        with pytest.raises(ingest.IngestRejected):
            ingest._validate_git_url(bad, hosts)


def test_validate_git_url_accepts_allowlisted_https_and_file():
    hosts = {"github.com", "gitlab.com"}
    assert ingest._validate_git_url("https://github.com/org/repo.git", hosts)
    assert ingest._validate_git_url("https://gitlab.com/org/repo.git", hosts)
    assert ingest._validate_git_url("file:///tmp/local-repo", hosts)


# --------------------------------------------------------------------------- #
# Security regression — no shell=True anywhere in the integrity package
# --------------------------------------------------------------------------- #
def test_no_shell_true_anywhere_in_integrity_package():
    # AST-based, not text-based: a real ``shell=True`` keyword on a *call* is the
    # offence — prose/docstrings that merely mention the string must not trip it.
    import ast

    pkg_root = Path(ingest.__file__).resolve().parent
    offenders = []
    for py in pkg_root.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        offenders.append(f"{py}:{node.lineno}")
    assert offenders == [], f"shell=True found in integrity package: {offenders}"


def test_git_clone_builds_fixed_arg_list_with_shell_false(monkeypatch):
    """The clone must pass a list (not a shell string) and shell=False."""
    captured = {}

    class _Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def _fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(ingest.subprocess, "run", _fake_run)
    ingest._git_clone(
        "https://github.com/org/repo.git",
        Path("dest"),
        hosts={"github.com"},
    )

    args = captured["args"]
    assert isinstance(args, list)            # fixed arg list, never a shell string
    assert captured["kwargs"]["shell"] is False
    assert "clone" in args
    assert "--depth" in args
    assert "--" in args                      # option terminator before the URL
    # URL appears as its own argv element, not interpolated into a larger string.
    assert "https://github.com/org/repo.git" in args


# --------------------------------------------------------------------------- #
# Credentials are stripped from logs
# --------------------------------------------------------------------------- #
def test_redact_url_strips_embedded_credentials():
    redacted = ingest._redact_url("https://alice:ghp_secrettoken@github.com/org/repo.git")
    assert "ghp_secrettoken" not in redacted
    assert "alice" not in redacted
    assert redacted == "https://***@github.com/org/repo.git"


def test_git_clone_failure_message_redacts_credentials(monkeypatch):
    class _Result:
        returncode = 128
        stderr = "fatal: could not read from https://bob:topsecret@github.com/x"
        stdout = ""

    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(ingest.IngestRejected) as excinfo:
        ingest._git_clone(
            "https://bob:topsecret@github.com/org/repo.git",
            Path("dest"),
            hosts={"github.com"},
        )
    assert "topsecret" not in str(excinfo.value)
