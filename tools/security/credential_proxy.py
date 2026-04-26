# CUI // SP-CTI
"""Zerobox credential proxy for child-process spawns (OPT-58).

Implements the Zerobox pattern: before spawning any child process that does
not explicitly need production secrets, sanitize the environment to strip all
credential-like vars and pass only a safe allowlist.

This prevents accidental secret leakage to child processes that may log their
environment, dump core, or be exploited through subprocess injection.

Usage:
    from tools.security.credential_proxy import CredentialProxy

    proxy = CredentialProxy()
    result = proxy.spawn(["python", "tools/my_tool.py", "--json"])

    # Or build the sanitized env dict yourself:
    env = proxy.sanitized_env(extra_allowed={"MY_SAFE_VAR"})
    subprocess.run(cmd, env=env)
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Optional, Set

# Variables that are safe for all child processes. These carry no credentials.
_ALWAYS_ALLOWED: Set[str] = {
    "PATH",
    "HOME",
    "HOMEPATH",
    "HOMEDRIVE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONIOENCODING",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "COLORTERM",
    "SHELL",
}

# Prefixes that indicate a credential-bearing var — strip these unconditionally.
_SECRET_PREFIXES = (
    "SECRET",
    "API_KEY",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "AWS_",
    "AZURE_",
    "GCP_",
    "GOOGLE_",
    "ANTHROPIC_",
    "OPENAI_",
    "STRIPE_",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
)


class CredentialProxy:
    """Sanitizes the process environment before spawning child processes.

    By default, child processes inherit only the vars in _ALWAYS_ALLOWED.
    Callers may pass extra_allowed to whitelist additional safe vars for a
    specific spawn (e.g. ICDEV_DASHBOARD_PORT for a health-check subprocess).
    """

    def sanitized_env(self, extra_allowed: Optional[Set[str]] = None) -> Dict[str, str]:
        """Return a sanitized copy of os.environ.

        Only vars in _ALWAYS_ALLOWED union extra_allowed are retained.
        Any var whose name starts with a _SECRET_PREFIXES entry is stripped
        even if it was in extra_allowed — secrets cannot be whitelisted.
        """
        allowed = _ALWAYS_ALLOWED | (extra_allowed or set())
        env: Dict[str, str] = {}
        for key, val in os.environ.items():
            upper = key.upper()
            if any(upper.startswith(p) for p in _SECRET_PREFIXES):
                continue
            if upper in allowed or key in allowed:
                env[key] = val
        return env

    def spawn(
        self,
        cmd: Any,
        extra_allowed: Optional[Set[str]] = None,
        **subprocess_kwargs: Any,
    ) -> subprocess.CompletedProcess:
        """Run *cmd* in a sanitized environment.

        Equivalent to subprocess.run(cmd, env=self.sanitized_env(extra_allowed),
        **subprocess_kwargs).

        Parameters
        ----------
        cmd:
            Command list (or string with shell=True).
        extra_allowed:
            Additional env-var names to pass through (beyond _ALWAYS_ALLOWED).
            Secret-prefixed vars are still stripped even if listed here.
        **subprocess_kwargs:
            Forwarded to subprocess.run().
        """
        env = self.sanitized_env(extra_allowed)
        return subprocess.run(cmd, env=env, **subprocess_kwargs)


# Module-level singleton for convenience.
_proxy = CredentialProxy()
