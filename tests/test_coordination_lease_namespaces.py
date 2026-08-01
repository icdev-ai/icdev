#!/usr/bin/env python3
"""Kanban leases must be HARD-enforced in both trees — CUI // SP-CTI.

`coordination/leases.py` decides enforcement with `hard = ns in HARD_NAMESPACES`
(leases.py:131). A namespace inside that set is hard-blocked on conflict; one
outside it is advisory.

The packaged copy's set was missing `RES_KANBAN`:

    canonical  HARD_NAMESPACES = {RES_SERVICE, RES_GIT, RES_MIGRATION, RES_KANBAN}
    packaged   HARD_NAMESPACES = {RES_SERVICE, RES_GIT, RES_MIGRATION}

So in a wheel-installed deployment a kanban lease conflict degraded from a hard
block to an advisory warning — two runners could hold the same task. That is
the exact class of failure the lease system exists to prevent, and it is
invisible: the advisory path succeeds.

Found by the mirror-drift sweep (#872), not by anything watching leases.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_COPIES = {
    "canonical": _ROOT / "tools" / "coordination" / "constants.py",
    "packaged": _ROOT / "icdev" / "tools" / "coordination" / "constants.py",
}


def _hard_namespaces(path: pathlib.Path) -> set[str]:
    src = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"HARD_NAMESPACES\s*=\s*\{([^}]*)\}", src)
    assert m, f"HARD_NAMESPACES not found in {path}"
    return {t.strip() for t in m.group(1).split(",") if t.strip()}


@pytest.mark.parametrize("which", sorted(_COPIES), ids=sorted(_COPIES))
def test_kanban_is_hard_enforced(which: str):
    """The regression: RES_KANBAN missing means advisory-only locking."""
    ns = _hard_namespaces(_COPIES[which])
    assert "RES_KANBAN" in ns, (
        f"{which} HARD_NAMESPACES lacks RES_KANBAN, so kanban lease conflicts "
        "degrade to advisory and two runners can hold one task"
    )


@pytest.mark.parametrize("which", sorted(_COPIES), ids=sorted(_COPIES))
def test_the_other_hard_namespaces_survive(which: str):
    ns = _hard_namespaces(_COPIES[which])
    assert {"RES_SERVICE", "RES_GIT", "RES_MIGRATION"} <= ns


def test_both_copies_agree():
    assert _hard_namespaces(_COPIES["canonical"]) == _hard_namespaces(_COPIES["packaged"])


def test_leases_actually_consumes_the_set():
    """Guard the premise — if leases.py stopped using it, this test proves nothing."""
    src = (_ROOT / "tools" / "coordination" / "leases.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "HARD_NAMESPACES" in src
    assert re.search(r"in\s+HARD_NAMESPACES", src), (
        "leases.py no longer tests membership of HARD_NAMESPACES"
    )
