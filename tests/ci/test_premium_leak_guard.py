# CUI // SP-CTI
"""Open-sourcing leak guard — premium product code must never enter this repo.

ICDEV is the open-source core; compass and idea_lab (C:/AI/standalone) are
private premium products layered on top via the network seams (Cortex REST,
DataBridge feeds, MCP). Their code was originally vendored FROM icdev, so the
risk direction to guard is the reverse: premium identifiers leaking INTO the
open repo through a careless copy-back.

Implemented as one `git grep` over the tracked tree (a Python file-read loop
is too slow on Windows CI). Runs inside the normal pytest job — no workflow
changes needed. If this test fires, move the flagged code to the private
repo; do not weaken the pattern list without a deliberate open-sourcing
decision.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Identifiers that only exist in the private premium products. Kept specific
# on purpose — generic words ("premium", "license") appear legitimately across
# compliance/profile configs. Joined into one ERE alternation for git grep.
_PREMIUM_PATTERNS = (
    r"\bcompass_premium\b",
    r"\bidea_lab_premium\b",
    r"\bpm_msr_(reports|sections)\b",
    r"\bCOMPASS_LICENSE\b",
    r"\bIDEALAB_LICENSE\b",
    r"\bmake_license\.py\b",
)

# Source trees that ship in the open repo.
_SCAN_DIRS = ("tools", "icdev/tools", "args", "goals", "hardprompts", ".claude")

# This guard (and its potential icdev mirror) names the patterns it hunts.
_SELF_SUFFIX = "test_premium_leak_guard.py"


# `git grep` across six trees can legitimately outlast the global 30s budget
# from pyproject's `timeout = 30`. Raise THIS test's budget rather than
# shortening the scan: the subprocess timeout below (120s) must stay inside it,
# or pytest-timeout fires first and — using the thread method, the only one
# available on Windows — kills the whole session instead of failing one test.
@pytest.mark.timeout(180)
def test_no_premium_identifiers_in_open_repo():
    pattern = "|".join(_PREMIUM_PATTERNS)
    try:
        proc = subprocess.run(
            ["git", "grep", "-l", "-E", pattern, "--", *_SCAN_DIRS],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable — leak guard only runs in a git checkout")

    # git grep exits 1 when nothing matches (success for us), >1 on error.
    if proc.returncode > 1:
        pytest.skip(f"git grep failed: {proc.stderr.strip()[:200]}")

    hits = [
        line for line in proc.stdout.splitlines()
        if line.strip() and not line.replace("\\", "/").endswith(_SELF_SUFFIX)
    ]
    assert not hits, (
        "Premium product identifiers found in the open-source repo — move this "
        "code to the private premium repo:\n  " + "\n  ".join(sorted(hits))
    )
