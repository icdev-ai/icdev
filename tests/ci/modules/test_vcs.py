# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/modules/vcs.py."""
from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from tools.ci.modules import vcs as vcs_mod  # noqa: E402


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_run(side_effect):
    return patch.object(vcs_mod.subprocess, "run", side_effect=side_effect)


# ────────────────────────────────────────────────────────────────────────────
# _get_env
# ────────────────────────────────────────────────────────────────────────────


def test_get_env_overlays_github_token(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "ghp_xxx")
    env = vcs_mod._get_env()
    assert env["GH_TOKEN"] == "ghp_xxx"


def test_get_env_overlays_gitlab_token(monkeypatch):
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.setenv("GLAB_TOKEN", "glab_yyy")
    env = vcs_mod._get_env()
    assert env["GITLAB_TOKEN"] == "glab_yyy"


# ────────────────────────────────────────────────────────────────────────────
# _extract_first_url
# ────────────────────────────────────────────────────────────────────────────


def test_extract_first_url_finds_in_stdout():
    out = vcs_mod._extract_first_url(
        "Created PR\nhttps://github.com/o/r/pull/9\nDone", "",
    )
    assert out == "https://github.com/o/r/pull/9"


def test_extract_first_url_falls_back_to_stderr():
    out = vcs_mod._extract_first_url(
        "", "warning: https://gitlab.example/o/r/-/merge_requests/3",
    )
    assert "merge_requests/3" in out


def test_extract_first_url_returns_none_when_absent():
    assert vcs_mod._extract_first_url("no url here", "") is None


# ────────────────────────────────────────────────────────────────────────────
# Detection
# ────────────────────────────────────────────────────────────────────────────


def test_detect_github_ssh():
    def fake(args, **kw):
        return _Proc(stdout="git@github.com:icdev-ai/icdev-ai.git\n")

    with _patch_run(fake):
        v = vcs_mod.VCS()
    assert v.platform == vcs_mod.VCS.PLATFORM_GITHUB
    assert v.repo_path == "icdev-ai/icdev-ai"
    assert v.is_github is True
    assert v.is_gitlab is False
    assert v.cli == "gh"


def test_detect_github_https():
    def fake(args, **kw):
        return _Proc(stdout="https://github.com/o/r.git")

    with _patch_run(fake):
        v = vcs_mod.VCS()
    assert v.platform == vcs_mod.VCS.PLATFORM_GITHUB
    assert v.repo_path == "o/r"


def test_detect_gitlab_default_for_other_hosts():
    def fake(args, **kw):
        return _Proc(stdout="https://gitlab.example.com/team/project.git")

    with _patch_run(fake):
        v = vcs_mod.VCS()
    assert v.platform == vcs_mod.VCS.PLATFORM_GITLAB
    assert v.repo_path == "team/project"
    assert v.cli == "glab"


def test_detect_remote_failure_raises_value_error():
    def fake(args, **kw):
        return _Proc(stdout="", stderr="not a git repo", returncode=128)

    with _patch_run(fake), pytest.raises(ValueError):
        vcs_mod.VCS()


def test_detect_unparseable_url_raises():
    def fake(args, **kw):
        return _Proc(stdout="ftp://oddball")

    with _patch_run(fake), pytest.raises(ValueError):
        vcs_mod.VCS()


def test_explicit_platform_skips_detection():
    v = vcs_mod.VCS(platform="github", repo_path="o/r")
    assert v.platform == "github"
    assert v.repo_path == "o/r"


# ────────────────────────────────────────────────────────────────────────────
# fetch_issue
# ────────────────────────────────────────────────────────────────────────────


def test_fetch_issue_github_returns_parsed_json():
    payload = {"number": 9, "title": "Bug"}

    def fake(args, **kw):
        return _Proc(stdout=json.dumps(payload))

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        out = v.fetch_issue(9)
    assert out == payload


def test_fetch_issue_failure_raises_runtime_error():
    def fake(args, **kw):
        return _Proc(stderr="not found", returncode=1)

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        with pytest.raises(RuntimeError):
            v.fetch_issue(404)


def test_fetch_issue_invalid_json_raises_runtime_error():
    def fake(args, **kw):
        return _Proc(stdout="<html>not json</html>")

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        with pytest.raises(RuntimeError):
            v.fetch_issue(1)


# ────────────────────────────────────────────────────────────────────────────
# list_open_issues
# ────────────────────────────────────────────────────────────────────────────


def test_list_open_issues_github_returns_list():
    payload = [{"number": 1, "title": "x"}]

    def fake(args, **kw):
        return _Proc(stdout=json.dumps(payload))

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        assert v.list_open_issues(limit=10) == payload


def test_list_open_issues_returns_empty_on_failure():
    def fake(args, **kw):
        return _Proc(stderr="boom", returncode=1)

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        assert v.list_open_issues() == []


# ────────────────────────────────────────────────────────────────────────────
# comment_on_issue
# ────────────────────────────────────────────────────────────────────────────


def test_comment_on_issue_returns_true_on_success():
    def fake(args, **kw):
        return _Proc(stdout="ok")

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        assert v.comment_on_issue(9, "hi") is True


def test_comment_on_issue_returns_false_on_failure():
    def fake(args, **kw):
        return _Proc(stderr="rate limit", returncode=1)

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        assert v.comment_on_issue(9, "hi") is False


# ────────────────────────────────────────────────────────────────────────────
# fetch_issue_comments
# ────────────────────────────────────────────────────────────────────────────


def test_fetch_issue_comments_github_extracts_comments_field():
    def fake(args, **kw):
        return _Proc(stdout=json.dumps({"comments": [{"body": "hi"}]}))

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        out = v.fetch_issue_comments(9)
    assert out == [{"body": "hi"}]


def test_fetch_issue_comments_returns_empty_on_failure():
    def fake(args, **kw):
        return _Proc(returncode=1)

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        assert v.fetch_issue_comments(9) == []


# ────────────────────────────────────────────────────────────────────────────
# create_pr
# ────────────────────────────────────────────────────────────────────────────


def test_create_pr_returns_url_from_stdout():
    def fake(args, **kw):
        return _Proc(stdout="Created\nhttps://github.com/o/r/pull/9\nDone")

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        url = v.create_pr("title", "body", head="feat-1")
    assert url == "https://github.com/o/r/pull/9"


def test_create_pr_returns_none_on_failure():
    def fake(args, **kw):
        return _Proc(stderr="oops", returncode=1)

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        assert v.create_pr("t", "b") is None


def test_create_pr_returns_created_when_no_url():
    def fake(args, **kw):
        return _Proc(stdout="(no url here)")

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        out = v.create_pr("t", "b")
    assert out == "(no url here)"


# ────────────────────────────────────────────────────────────────────────────
# check_pr_exists
# ────────────────────────────────────────────────────────────────────────────


def test_check_pr_exists_github_returns_url():
    def fake(args, **kw):
        return _Proc(
            stdout=json.dumps([{"url": "https://github.com/o/r/pull/3"}])
        )

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        assert v.check_pr_exists("feat-1") == "https://github.com/o/r/pull/3"


def test_check_pr_exists_returns_none_when_no_results():
    def fake(args, **kw):
        return _Proc(stdout="[]")

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        assert v.check_pr_exists("feat-1") is None


def test_check_pr_exists_gitlab_prefers_web_url():
    def fake(args, **kw):
        return _Proc(stdout=json.dumps(
            [{"web_url": "https://gitlab.example/x/-/merge_requests/2",
              "url": "https://api.gitlab.example/projects/1/mr/2"}]
        ))

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="gitlab", repo_path="x")
        out = v.check_pr_exists("feat-2")
    assert "merge_requests/2" in out


# ────────────────────────────────────────────────────────────────────────────
# comment_on_pr / utility
# ────────────────────────────────────────────────────────────────────────────


def test_comment_on_pr_returns_true_on_success():
    def fake(args, **kw):
        return _Proc()

    with _patch_run(fake):
        v = vcs_mod.VCS(platform="github", repo_path="o/r")
        assert v.comment_on_pr(9, "hi") is True


def test_repr_includes_platform_and_repo():
    v = vcs_mod.VCS(platform="github", repo_path="o/r")
    assert "github" in repr(v)
    assert "o/r" in repr(v)
