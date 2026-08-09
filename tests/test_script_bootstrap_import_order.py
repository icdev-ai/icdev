#!/usr/bin/env python3
"""A module that bootstraps sys.path must not import through it first.

Running a file BY PATH (`python tools/kanban/des_audit_logger.py`) puts the
FILE's directory on sys.path, never the repo root. That is why these modules
carry a `sys.path.insert(0, BASE_DIR)` bootstrap. An `import tools...` placed
ABOVE that bootstrap therefore raises ModuleNotFoundError before main() is ever
reached, and the module's own documented CLI is unrunnable — which is exactly
what `test_cli_query_returns_valid_json` was failing on in both files below.

The check is scoped to modules with a REAL CLI, because that is where the defect
has consequences: a module that is only ever imported never executes the failing
line. The wider tree currently has ~396 modules with the same ordering (a
`get_logger` sweep hoisted the import above existing bootstraps); those are
tracked separately rather than swept here, since a 396-file rewrite would
conflict with every open branch.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Modules proven broken by a failing CLI test. Additions welcome; removals are
#: the thing to be suspicious of.
GUARDED = [
    "tools/audit/cross_agency_transfer_logger.py",
    "tools/kanban/des_audit_logger.py",
]

_FIRST_PARTY = re.compile(r"^\s*(?:from|import)\s+(?:tools|icdev)\b")


def _first_party_imports_above_bootstrap(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8", newline="").split("\n")
    boot = next((i for i, ln in enumerate(lines) if "sys.path.insert" in ln), None)
    if boot is None:
        return []
    return [ln.strip() for ln in lines[:boot] if _FIRST_PARTY.match(ln)]


@pytest.mark.parametrize("rel", GUARDED)
@pytest.mark.parametrize("base", ["", "icdev/"])
def test_no_first_party_import_above_the_bootstrap(base, rel):
    path = REPO / (base + rel)
    if not path.exists():
        pytest.skip(f"no mirror at {base + rel}")
    offenders = _first_party_imports_above_bootstrap(path)
    assert not offenders, (
        f"{base + rel} imports first-party modules above its own sys.path "
        f"bootstrap, so `python {rel}` dies on ModuleNotFoundError: {offenders}"
    )


@pytest.mark.parametrize("rel", GUARDED)
def test_the_guarded_modules_still_have_a_bootstrap_to_be_above(rel):
    """If the bootstrap is deleted, the check above silently passes forever."""
    src = (REPO / rel).read_text(encoding="utf-8", newline="")
    assert "sys.path.insert" in src, (
        f"{rel} lost its sys.path bootstrap — the import-order test above becomes "
        "vacuous, and the CLI breaks again for a different reason"
    )
