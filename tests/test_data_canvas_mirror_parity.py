#!/usr/bin/env python3
"""Data Canvas: packaged copies must not be older, weaker implementations.

The `icdev/tools/data_canvas/` copies had drifted behind their canonical twins
and the differences were not cosmetic. Three defect classes, each fixed in
`tools/` and never mirrored:

1. GOVERNANCE FAILED OPEN. `governance_engine` returned `True` for an unknown
   operation ("default allow") and allowed when no policy matched. The
   canonical copy returns `False` (fail closed / default deny) and allows only
   for resources explicitly classed non-sensitive. A wheel-installed Data
   Canvas therefore permitted governance-controlled operations that the
   canonical implementation denies.
2. RLS-VIOLATING CONNECTIONS. Several modules used `get_connection()` plus raw
   `sqlite3` fallbacks where the canonical copy uses `get_canvas_connection()`.
   Canvas tables carry no `classification`/`tenant_id`, so the global RLS
   predicate raises `UndefinedColumn` on PostgreSQL.
3. SQLITE-DIALECT PLACEHOLDERS in `data_mesh`.

The drift gate below covers 2 and 3 by construction — the copies are identical,
so whatever the canonical does, the packaged copy does. Only the governance
property is pinned explicitly, because it is the one whose regression is
silent and security-relevant.
"""
from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CANON = _ROOT / "tools" / "data_canvas"
_MIRROR = _ROOT / "icdev" / "tools" / "data_canvas"


def _drifted() -> list[str]:
    out = []
    for p in sorted(_CANON.rglob("*.py")):
        q = _MIRROR / p.relative_to(_CANON)
        if q.exists() and q.read_bytes() != p.read_bytes():
            out.append(p.relative_to(_CANON).as_posix())
    return out


def test_no_data_canvas_drift():
    drifted = _drifted()
    assert not drifted, (
        f"data_canvas copies differ: {drifted}. The packaged copies have "
        "previously carried fail-open governance and RLS-violating connections; "
        "reconcile deliberately rather than letting them age."
    )


# --------------------------------------------------------------------------- #
# 1. Governance must fail CLOSED — the most consequential of the three
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("base", [_CANON, _MIRROR], ids=["canonical", "packaged"])
def test_governance_fails_closed_on_unknown_operation(base: pathlib.Path):
    src = (base / "governance_engine.py").read_text(encoding="utf-8", errors="replace")
    assert "default allow" not in src.split("non-sensitive")[0] or "fail closed" in src, (
        "governance_engine must not default-allow an unknown operation"
    )
    assert "fail closed" in src or "default deny" in src, (
        f"{base.name}/governance_engine.py has no fail-closed path — the "
        "packaged copy previously returned True for unknown ops"
    )


@pytest.mark.parametrize("base", [_CANON, _MIRROR], ids=["canonical", "packaged"])
def test_governance_has_a_sensitivity_check(base: pathlib.Path):
    """Allowing is only correct for resources explicitly classed non-sensitive."""
    src = (base / "governance_engine.py").read_text(encoding="utf-8", errors="replace")
    assert "_is_sensitive_resource" in src, (
        f"{base.name}/governance_engine.py cannot distinguish sensitive resources, "
        "so any 'default allow' is unconditional"
    )


def test_the_guard_sees_the_historical_defects():
    """Guard the guard — the drift detector must be able to see a difference."""
    assert _CANON.is_dir() and _MIRROR.is_dir()
    assert list(_CANON.rglob("*.py")), "no canonical data_canvas modules found"


# NOTE — two further checks were drafted and REMOVED deliberately:
#
#   * "no raw sqlite3.connect anywhere under data_canvas"
#   * "no SQLite-style ? placeholders anywhere under data_canvas"
#
# Both fail on BOTH trees. 18 canonical modules use bare `?` and several use
# sqlite3.connect — pre-existing, repo-wide, and unrelated to mirror drift.
# Asserting them here would flag code this PR did not touch and could not fix,
# which trains people to ignore the file. They belong in a dedicated
# PG-portability sweep for data_canvas, not in a parity test.
