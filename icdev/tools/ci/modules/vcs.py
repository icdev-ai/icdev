# CUI // SP-CTI
"""ICDEV™ unified VCS abstraction for GitHub and GitLab.

Auto-detects which platform is in use from the local repo's
``origin`` remote URL and routes every API call through the
appropriate CLI tool (``gh`` for GitHub, ``glab`` for GitLab). The
caller works with one method surface and never has to know which
backend it's talking to.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/modules/vcs.md`` (OPT-75 Phase 3
clean-room rewrite).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]


_DEFAULT_TIMEOUT_SECONDS: int = 30
_PR_CREATE_TIMEOUT_SECONDS: int = 60

_RE_SSH_REMOTE = re.compile(r"^git@([^:]+):(.+?)(?:\.git)?$")
_RE_HTTPS_REMOTE = re.compile(r"^https?://([^/]+)/(.+?)(?:\.git)?$")
_RE_FIRST_URL = re.compile(r"https?://\S+")


# ────────────────────────────────────────────────────────────────────────────
# Subprocess helpers
# ────────────────────────────────────────────────────────────────────────────


def _get_env() -> Dict[str, str]:
    """Return a child-process env that inherits everything and overlays
    explicit token overrides for ``gh`` and ``glab``."""
    env = os.environ.copy()

    gh_token = os.getenv("GITHUB_PAT") or os.getenv("GH_TOKEN")
    if gh_token:
        env["GH_TOKEN"] = gh_token

    gl_token = os.getenv("GITLAB_TOKEN") or os.getenv("GLAB_TOKEN")
    if gl_token:
        env["GITLAB_TOKEN"] = gl_token

    gl_url = os.getenv("GITLAB_URL")
    if gl_url:
        env["GITLAB_URL"] = gl_url

    return env


def _run(
    cmd: List[str],
    cwd: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT_SECONDS,
) -> Tuple[str, str, int]:
    """Run a CLI subprocess and return ``(stdout, stderr, returncode)``."""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd or str(PROJECT_ROOT),
        env=_get_env(),
        timeout=timeout,
    )
    return (
        (proc.stdout or "").strip(),
        (proc.stderr or "").strip(),
        proc.returncode,
    )


def _extract_first_url(*streams: str) -> Optional[str]:
    """Find the first ``https?://...`` URL across one or more text
    streams. Used to pluck the PR/MR URL out of CLI output."""
    for stream in streams:
        if not stream:
            continue
        match = _RE_FIRST_URL.search(stream)
        if match:
            return match.group(0).rstrip(".,)")
    return None


# ────────────────────────────────────────────────────────────────────────────
# VCS class
# ────────────────────────────────────────────────────────────────────────────


class VCS:
    """Unified VCS abstraction.

    Auto-detects GitHub vs. GitLab from ``git remote get-url origin``
    on construction. Pass ``platform=`` and/or ``repo_path=`` to bypass
    detection.
    """

    PLATFORM_GITHUB = "github"
    PLATFORM_GITLAB = "gitlab"

    def __init__(
        self,
        platform: Optional[str] = None,
        repo_path: Optional[str] = None,
    ) -> None:
        self.repo_path = repo_path
        if platform:
            self.platform = platform
            if self.repo_path is None:
                # Caller forced the platform but not the path; try to
                # parse a path out of the remote anyway. Failures are
                # tolerated — most CLI calls accept the implicit repo.
                try:
                    _, parsed_path = self._detect_platform()
                    self.repo_path = parsed_path
                except Exception:
                    self.repo_path = None
        else:
            self.platform, detected_path = self._detect_platform()
            if self.repo_path is None:
                self.repo_path = detected_path

    # ── detection ────────────────────────────────────────────────────

    def _detect_platform(self) -> Tuple[str, str]:
        stdout, stderr, rc = _run(["git", "remote", "get-url", "origin"])
        if rc != 0:
            raise ValueError(f"No git remote 'origin': {stderr}")

        url = (stdout or "").strip()
        ssh = _RE_SSH_REMOTE.match(url)
        https = _RE_HTTPS_REMOTE.match(url)
        if ssh:
            host, path = ssh.group(1), ssh.group(2)
        elif https:
            host, path = https.group(1), https.group(2)
        else:
            raise ValueError(f"Cannot parse remote URL: {url}")

        if "github" in host.lower():
            return self.PLATFORM_GITHUB, path
        return self.PLATFORM_GITLAB, path

    # ── properties ────────────────────────────────────────────────────

    @property
    def cli(self) -> str:
        return "gh" if self.platform == self.PLATFORM_GITHUB else "glab"

    @property
    def is_github(self) -> bool:
        return self.platform == self.PLATFORM_GITHUB

    @property
    def is_gitlab(self) -> bool:
        return self.platform == self.PLATFORM_GITLAB

    # ── issues ────────────────────────────────────────────────────────

    def fetch_issue(self, issue_number: int) -> Dict[str, Any]:
        if self.is_github:
            fields = (
                "number,title,body,state,author,labels,comments,"
                "createdAt,updatedAt,url"
            )
            cmd = [
                "gh", "issue", "view", str(issue_number),
                "-R", self.repo_path or "",
                "--json", fields,
            ]
        else:
            cmd = [
                "glab", "issue", "view", str(issue_number),
                "--output", "json",
            ]
        stdout, stderr, rc = _run(cmd)
        if rc != 0:
            raise RuntimeError(
                f"Failed to fetch issue #{issue_number}: {stderr}"
            )
        try:
            return json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Issue payload for #{issue_number} is not valid JSON: {exc}"
            ) from exc

    def list_open_issues(self, limit: int = 50) -> List[Dict[str, Any]]:
        if self.is_github:
            cmd = [
                "gh", "issue", "list",
                "--repo", self.repo_path or "",
                "--state", "open",
                "--json", "number,title,body,labels,createdAt,updatedAt",
                "--limit", str(limit),
            ]
        else:
            cmd = [
                "glab", "issue", "list",
                "--opened",
                "--output", "json",
                "--per-page", str(limit),
            ]
        stdout, _stderr, rc = _run(cmd)
        if rc != 0 or not stdout:
            return []
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return []

    def comment_on_issue(self, issue_number: int, body: str) -> bool:
        if self.is_github:
            cmd = [
                "gh", "issue", "comment", str(issue_number),
                "-R", self.repo_path or "",
                "--body", body,
            ]
        else:
            cmd = [
                "glab", "issue", "note", str(issue_number),
                "--message", body,
            ]
        _stdout, _stderr, rc = _run(cmd)
        return rc == 0

    def fetch_issue_comments(self, issue_number: int) -> List[Dict[str, Any]]:
        if self.is_github:
            cmd = [
                "gh", "issue", "view", str(issue_number),
                "--repo", self.repo_path or "",
                "--json", "comments",
            ]
            stdout, _stderr, rc = _run(cmd)
            if rc != 0 or not stdout:
                return []
            try:
                return (json.loads(stdout) or {}).get("comments") or []
            except json.JSONDecodeError:
                return []
        else:
            cmd = [
                "glab", "api",
                f"projects/:id/issues/{issue_number}/notes",
                "--paginate",
            ]
            stdout, _stderr, rc = _run(cmd)
            if rc != 0 or not stdout:
                return []
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return []

    # ── PR / MR ───────────────────────────────────────────────────────

    def create_pr(
        self,
        title: str,
        body: str,
        base: str = "main",
        head: Optional[str] = None,
    ) -> Optional[str]:
        if self.is_github:
            cmd = [
                "gh", "pr", "create",
                "--repo", self.repo_path or "",
                "--title", title,
                "--body", body,
                "--base", base,
            ]
            if head:
                cmd.extend(["--head", head])
        else:
            cmd = [
                "glab", "mr", "create",
                "--title", title,
                "--description", body,
                "--target-branch", base,
                "--remove-source-branch",
                "--yes",
            ]
            if head:
                cmd.extend(["--source-branch", head])

        stdout, stderr, rc = _run(cmd, timeout=_PR_CREATE_TIMEOUT_SECONDS)
        if rc != 0:
            return None

        url = _extract_first_url(stdout, stderr)
        if url:
            return url
        return stdout.strip() or "created"

    def check_pr_exists(self, branch_name: str) -> Optional[str]:
        if self.is_github:
            cmd = [
                "gh", "pr", "list",
                "--repo", self.repo_path or "",
                "--head", branch_name,
                "--json", "url",
            ]
            stdout, _stderr, rc = _run(cmd)
            if rc != 0 or not stdout:
                return None
            try:
                prs = json.loads(stdout)
            except json.JSONDecodeError:
                return None
            if prs:
                return prs[0].get("url")
            return None

        cmd = [
            "glab", "mr", "list",
            "--source-branch", branch_name,
            "--output", "json",
        ]
        stdout, _stderr, rc = _run(cmd)
        if rc != 0 or not stdout:
            return None
        try:
            mrs = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        if mrs:
            return mrs[0].get("web_url") or mrs[0].get("url")
        return None

    def comment_on_pr(self, pr_number: int, body: str) -> bool:
        if self.is_github:
            cmd = [
                "gh", "pr", "comment", str(pr_number),
                "-R", self.repo_path or "",
                "--body", body,
            ]
        else:
            cmd = [
                "glab", "mr", "note", str(pr_number),
                "--message", body,
            ]
        _stdout, _stderr, rc = _run(cmd)
        return rc == 0

    # ── utility ───────────────────────────────────────────────────────

    def get_remote_url(self) -> str:
        stdout, _stderr, _rc = _run(["git", "remote", "get-url", "origin"])
        return stdout

    def whoami(self) -> str:
        if self.is_github:
            stdout, _stderr, _rc = _run(["gh", "auth", "status"])
        else:
            stdout, _stderr, _rc = _run(["glab", "auth", "status"])
        return stdout

    def __repr__(self) -> str:
        return f"VCS(platform={self.platform}, repo={self.repo_path})"
