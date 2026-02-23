# [TEMPLATE: CUI // SP-CTI]
# ICDEV VCS Abstraction — Unified GitHub + GitLab interface
# Adapted from ADW github.py with GitLab support added

"""
Unified Version Control System abstraction for GitHub and GitLab.

Detects which platform is in use from git remote URL, then provides
a common API for issues, comments, merge/pull requests using the
appropriate CLI tool (gh for GitHub, glab for GitLab).

Usage:
    from tools.ci.modules.vcs import VCS
    vcs = VCS()  # Auto-detects platform from git remote
    issue = vcs.fetch_issue(123)
    vcs.comment_on_issue(123, "Build complete")
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Safe environment for subprocesses
def _get_env() -> Dict[str, str]:
    # Start with full inherited environment so gh/glab can access OS keyring,
    # APPDATA (Windows credential store), and other platform-specific paths.
    env = os.environ.copy()
    # Overlay explicit token overrides if set
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


def _run(cmd: List[str], cwd: str = None, timeout: int = 30) -> Tuple[str, str, int]:
    """Run a CLI command and return (stdout, stderr, returncode)."""
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=cwd or str(PROJECT_ROOT),
        env=_get_env(), timeout=timeout,
    )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


class VCS:
    """Unified VCS abstraction for GitHub and GitLab.

    Auto-detects platform from git remote URL:
    - github.com → GitHub (uses `gh` CLI)
    - gitlab.com or custom GitLab → GitLab (uses `glab` CLI)
    """

    PLATFORM_GITHUB = "github"
    PLATFORM_GITLAB = "gitlab"

    def __init__(self, platform: str = None, repo_path: str = None):
        """Initialize VCS with auto-detection or explicit platform.

        Args:
            platform: Force "github" or "gitlab" (auto-detect if None)
            repo_path: Override repo path (e.g., "owner/repo")
        """
        self.repo_path = repo_path
        if platform:
            self.platform = platform
        else:
            self.platform, self.repo_path = self._detect_platform()

    def _detect_platform(self) -> Tuple[str, str]:
        """Detect GitHub or GitLab from git remote URL."""
        stdout, stderr, rc = _run(["git", "remote", "get-url", "origin"])
        if rc != 0:
            raise ValueError(f"No git remote 'origin': {stderr}")

        url = stdout.strip()

        # Extract repo path from URL
        # SSH: git@github.com:owner/repo.git
        # HTTPS: https://github.com/owner/repo.git
        ssh_match = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url)
        https_match = re.match(r"https?://([^/]+)/(.+?)(?:\.git)?$", url)

        if ssh_match:
            host, path = ssh_match.group(1), ssh_match.group(2)
        elif https_match:
            host, path = https_match.group(1), https_match.group(2)
        else:
            raise ValueError(f"Cannot parse remote URL: {url}")

        if "github" in host.lower():
            return self.PLATFORM_GITHUB, path
        else:
            # Default to GitLab for non-GitHub hosts
            return self.PLATFORM_GITLAB, path

    @property
    def cli(self) -> str:
        """Return the CLI tool name for this platform."""
        return "gh" if self.platform == self.PLATFORM_GITHUB else "glab"

    @property
    def is_github(self) -> bool:
        return self.platform == self.PLATFORM_GITHUB

    @property
    def is_gitlab(self) -> bool:
        return self.platform == self.PLATFORM_GITLAB

    # --- Issues ---

    def fetch_issue(self, issue_number: int) -> Dict[str, Any]:
        """Fetch issue details from GitHub or GitLab."""
        if self.is_github:
            fields = "number,title,body,state,author,labels,comments,createdAt,updatedAt,url"
            stdout, stderr, rc = _run([
                "gh", "issue", "view", str(issue_number),
                "-R", self.repo_path,
                "--json", fields,
            ])
        else:
            stdout, stderr, rc = _run([
                "glab", "issue", "view", str(issue_number),
                "--output", "json",
            ])

        if rc != 0:
            raise RuntimeError(f"Failed to fetch issue #{issue_number}: {stderr}")

        return json.loads(stdout)

    def list_open_issues(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List open issues."""
        if self.is_github:
            stdout, stderr, rc = _run([
                "gh", "issue", "list",
                "--repo", self.repo_path,
                "--state", "open",
                "--json", "number,title,body,labels,createdAt,updatedAt",
                "--limit", str(limit),
            ])
        else:
            stdout, stderr, rc = _run([
                "glab", "issue", "list",
                "--opened",
                "--output", "json",
                "--per-page", str(limit),
            ])

        if rc != 0:
            return []

        return json.loads(stdout) if stdout else []

    def comment_on_issue(self, issue_number: int, body: str) -> bool:
        """Post a comment on an issue/MR."""
        if self.is_github:
            _, stderr, rc = _run([
                "gh", "issue", "comment", str(issue_number),
                "-R", self.repo_path,
                "--body", body,
            ])
        else:
            _, stderr, rc = _run([
                "glab", "issue", "note", str(issue_number),
                "--message", body,
            ])

        return rc == 0

    def fetch_issue_comments(self, issue_number: int) -> List[Dict[str, Any]]:
        """Fetch comments on an issue."""
        if self.is_github:
            stdout, stderr, rc = _run([
                "gh", "issue", "view", str(issue_number),
                "--repo", self.repo_path,
                "--json", "comments",
            ])
            if rc == 0 and stdout:
                data = json.loads(stdout)
                return data.get("comments", [])
        else:
            stdout, stderr, rc = _run([
                "glab", "api", f"projects/:id/issues/{issue_number}/notes",
                "--paginate",
            ])
            if rc == 0 and stdout:
                return json.loads(stdout)

        return []

    # --- Pull Requests / Merge Requests ---

    def create_pr(self, title: str, body: str, base: str = "main",
                  head: str = None) -> Optional[str]:
        """Create a pull request (GitHub) or merge request (GitLab).

        Returns the PR/MR URL on success, None on failure.
        """
        if self.is_github:
            cmd = [
                "gh", "pr", "create",
                "--repo", self.repo_path,
                "--title", title,
                "--body", body,
                "--base", base,
            ]
            if head:
                cmd.extend(["--head", head])
            stdout, stderr, rc = _run(cmd, timeout=60)
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
            stdout, stderr, rc = _run(cmd, timeout=60)

        if rc == 0:
            # Extract URL from output
            for line in (stdout + "\n" + stderr).splitlines():
                if "http" in line:
                    url_match = re.search(r'(https?://\S+)', line)
                    if url_match:
                        return url_match.group(1)
            return stdout.strip() or "created"

        return None

    def check_pr_exists(self, branch_name: str) -> Optional[str]:
        """Check if a PR/MR already exists for a branch. Returns URL or None."""
        if self.is_github:
            stdout, stderr, rc = _run([
                "gh", "pr", "list",
                "--repo", self.repo_path,
                "--head", branch_name,
                "--json", "url",
            ])
            if rc == 0 and stdout:
                prs = json.loads(stdout)
                if prs:
                    return prs[0].get("url")
        else:
            stdout, stderr, rc = _run([
                "glab", "mr", "list",
                "--source-branch", branch_name,
                "--output", "json",
            ])
            if rc == 0 and stdout:
                mrs = json.loads(stdout)
                if mrs:
                    return mrs[0].get("web_url") or mrs[0].get("url")

        return None

    def comment_on_pr(self, pr_number: int, body: str) -> bool:
        """Post a comment on a PR/MR."""
        if self.is_github:
            _, _, rc = _run([
                "gh", "pr", "comment", str(pr_number),
                "-R", self.repo_path,
                "--body", body,
            ])
        else:
            _, _, rc = _run([
                "glab", "mr", "note", str(pr_number),
                "--message", body,
            ])
        return rc == 0

    # --- Utility ---

    def get_remote_url(self) -> str:
        """Get the git remote URL."""
        stdout, _, _ = _run(["git", "remote", "get-url", "origin"])
        return stdout

    def whoami(self) -> str:
        """Get the authenticated user."""
        if self.is_github:
            stdout, _, rc = _run(["gh", "auth", "status"])
            return stdout
        else:
            stdout, _, rc = _run(["glab", "auth", "status"])
            return stdout

    def __repr__(self) -> str:
        return f"VCS(platform={self.platform}, repo={self.repo_path})"
