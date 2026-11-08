# CUI // SP-CTI
"""The GitHub token for the engines' REST calls: ``GITHUB_TOKEN`` / ``GH_TOKEN``, else the ``gh`` CLI's login.

Unauthenticated REST calls are capped at 60 an hour and answer 403 past it, which a scan of a few dozen
repositories reaches at once. ``github_token()`` returns None when neither source has a token; callers
then send no Authorization header and live with the unauthenticated limit.
"""

from __future__ import annotations

import os
import shutil
import subprocess

_CACHE: list = []


def github_token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    if not _CACHE:
        found = None
        gh = shutil.which("gh")
        if gh:
            try:
                out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10, check=False)
                found = (out.stdout.strip() or None) if out.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                found = None
        _CACHE.append(found)
    return _CACHE[0]


def clear() -> None:
    _CACHE.clear()
