# CUI // SP-CTI
"""ICDEV is a PUBLIC repository. Customer evidence must never land in it.

The BOM Evidence Engine is developed against real customer documents — bills of
materials, briefing decks, architecture diagrams, asset inventories. Those files
carry sensitivity labels, which the engine's own forensics module detects and
reports. The engine belongs here, in the open. The evidence does not.

The failure this guards against is not hypothetical and it is not obvious in
review. Writing a *test* that asserts "the total is $522,304" or a *commit
message* that quotes a customer's meeting notes publishes that content just as
surely as committing the spreadsheet — and it looks, in a diff, like diligent
engineering. The line to hold:

    Publish the TECHNIQUE. Never the DATA.

A formula multiplying a quantity by an empty cell yields zero regardless of what
is being bought. That insight is ours and belongs in the open. The customer's
figure is theirs.

Real corpora are pointed at through an env var and tested in the PRIVATE repo.
See the cncd-corpus-01 task.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Directories the engine and its tests live in. Scanning the whole repo would
# drown in false positives from unrelated fixtures.
#
# `args/` is here because the guard did NOT cover it and should have. A plan config
# is the most customer-specific file the engine has — real people, real committed
# dates, a real task list — and it looked like innocuous YAML right up until it was
# a disclosure. The live config is gitignored and lives with the corpus; only the
# synthetic example ships.
SCANNED = (
    "tools/bom",
    "tools/slides/brand_deck.py",
    "tests/bom",
    "tools/kanban/seed_bom_concord.py",
    "args/bom_plan.example.yaml",
    "args/bom_credibility.yaml",
    "args/bom_columns.yaml",
    "args/bom_xlsx_layout.yaml",
)

# Config that must never be tracked, because a real one is customer material.
UNTRACKED = ("args/bom_plan.yaml",)

# Patterns that indicate customer evidence rather than engineering. Deliberately
# specific: a guard that cries wolf gets switched off, and then it protects
# nothing.
_LEAK_PATTERNS: list[tuple[str, str]] = [
    # Absolute paths into somebody's evidence folder.
    (r"[A-Za-z]:[\\/]Users[\\/][^\s\"']+[\\/]Downloads",
     "an absolute path into a local evidence folder"),
    # A corpus root hardcoded instead of read from an env var.
    (r"BOM_TEST_CORPUS\s*=\s*['\"][A-Za-z]:",
     "a hardcoded corpus path (use an env var, and test it in the private repo)"),
]

# Words that only appear if customer material has been pasted in. Kept as a small
# explicit list rather than a clever heuristic, so a reviewer can see exactly what
# is being refused and add to it.
_FORBIDDEN_TERMS = (
    "peraton",
    "askiris",
)


_SUFFIXES = (".py", ".yaml", ".yml", ".md")


def _iter_files():
    for target in SCANNED:
        p = REPO / target
        if p.is_file():
            yield p
        elif p.is_dir():
            yield from (
                f for f in p.rglob("*")
                if f.is_file()
                and f.suffix in _SUFFIXES
                and "__pycache__" not in f.parts
            )


class TestNoCorpusInTheOpenSourceRepo:
    def test_no_absolute_evidence_paths(self):
        offenders = []
        for f in _iter_files():
            text = f.read_text(encoding="utf-8", errors="replace")
            for pattern, why in _LEAK_PATTERNS:
                for m in re.finditer(pattern, text):
                    line = text[: m.start()].count("\n") + 1
                    offenders.append(f"{f.relative_to(REPO)}:{line} — {why}")
        assert not offenders, (
            "Customer evidence must not be referenced from the public repo:\n  "
            + "\n  ".join(offenders)
        )

    def test_no_customer_identifiers(self):
        offenders = []
        for f in _iter_files():
            text = f.read_text(encoding="utf-8", errors="replace").lower()
            for term in _FORBIDDEN_TERMS:
                if term in text:
                    offenders.append(f"{f.relative_to(REPO)} — contains {term!r}")
        assert not offenders, (
            "Customer identifiers must not appear in the public repo:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_live_plan_config_is_not_tracked(self):
        """A plan config names real people, real committed dates, a real task list.

        The mistake this catches: writing that file into `args/` because that is
        where config lives, and it looks like innocuous YAML in a diff. The engine
        loads it from `$BOM_PLAN_CONFIG` or a gitignored local path; only the
        synthetic example ships.
        """
        try:
            out = subprocess.run(
                ["git", "ls-files", "--", *UNTRACKED],
                cwd=REPO, capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
            pytest.skip("git unavailable")

        tracked = [line for line in out.stdout.splitlines() if line.strip()]
        assert not tracked, (
            f"these must never be committed to a public repo: {tracked}. "
            f"Move the file out of the tree and point $BOM_PLAN_CONFIG at it; "
            f"args/bom_plan.example.yaml is what ships."
        )

    def test_the_example_config_exists_so_a_fresh_clone_runs(self):
        assert (REPO / "args" / "bom_plan.example.yaml").exists()

    def test_fixtures_are_built_not_committed(self):
        """No customer documents checked in under the BOM tests.

        Fixtures are CONSTRUCTED at test time (tests/bom/fixtures.py). That keeps
        CI hermetic and, more importantly, means the suite proves the engine finds
        these defects in documents nobody here has ever seen — a stronger claim
        than passing against the one corpus we happened to have.
        """
        binaries = [
            f for f in (REPO / "tests" / "bom").rglob("*")
            if f.suffix.lower() in (".xlsx", ".xlsm", ".pptx", ".docx", ".pdf", ".drawio")
        ]
        assert not binaries, (
            "Documents are checked in under tests/bom:\n  "
            + "\n  ".join(str(f.relative_to(REPO)) for f in binaries)
            + "\nBuild them in fixtures.py instead."
        )


class TestCommitMessagesAreClean:
    """A commit message is published exactly as surely as the code is.

    This is the one that actually bit: quoting a customer's hidden meeting notes
    and their budget figures into a commit body, on a branch destined for a public
    remote. It reads like careful engineering right up until it is a disclosure.
    """

    def test_no_customer_identifiers_in_branch_commit_messages(self):
        try:
            out = subprocess.run(
                ["git", "log", "--format=%B", "origin/main..HEAD"],
                cwd=REPO, capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
            pytest.skip("git unavailable")

        if out.returncode != 0:
            pytest.skip("no origin/main to compare against")

        body = out.stdout.lower()
        found = [t for t in _FORBIDDEN_TERMS if t in body]
        assert not found, (
            f"Commit messages on this branch contain customer identifiers: {found}. "
            "Rewrite them before pushing — a public remote does not forget."
        )
