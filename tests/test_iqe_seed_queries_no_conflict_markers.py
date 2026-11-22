# CUI // SP-CTI
"""No shipped ``.iqe`` seed query carries merge-conflict markers.

A stash pop once left ``<<<<<<< Updated upstream`` in the packaged
``ai_augmentation/01_top_opportunities.iqe``; nothing read the file, so
nothing noticed. This sweeps the source tree and the packaged copy.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ROOTS = (REPO_ROOT / "context" / "iqe", REPO_ROOT / "icdev" / "data" / "context" / "iqe")
MARKER = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.MULTILINE)

SEEDS = sorted(p for root in ROOTS if root.is_dir() for p in root.rglob("*.iqe"))


def test_seed_queries_are_found():
    assert SEEDS, "no .iqe seed queries found under context/iqe"


@pytest.mark.parametrize("path", SEEDS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_seed_query_has_no_conflict_markers(path):
    text = path.read_text(encoding="utf-8")
    assert not MARKER.search(text), f"merge-conflict markers in {path.relative_to(REPO_ROOT)}"

