# CUI // SP-CTI
"""Every shipped IQE seed query parses with the real IQE parser.

The seed queries under ``context/iqe/queries/`` and their packaged copies under
``icdev/data/context/iqe/queries/`` are what the dashboard's ``GET /iqe`` lists
and ``POST /iqe/run`` (``tools/dashboard/api/iqe.py``) and ``POST /api/v1/iqe/run``
(``tools/api/v1/blueprint.py``) execute. Both read the file and hand the raw text
straight to ``tools.iqe.parser.parse`` -- no comment stripping, no other dialect --
so that is exactly how this test parses each file. A seed that does not parse is a
canvas query that answers every run with a 400.

The infra pre-apply gate (``tools/infra_canvas/preapply_gate.py``) is the one
consumer that pre-processes (it drops ``#`` lines first), and it reports a file
that will not parse as ``skipped`` rather than failing; it is checked separately so
a gate rule can never silently stop being a rule.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.iqe.ast_nodes import ForeachNode
from tools.iqe.parser import parse

REPO = Path(__file__).resolve().parents[1]
SEED_ROOTS = (
    REPO / "context" / "iqe" / "queries",
    REPO / "icdev" / "data" / "context" / "iqe" / "queries",
)

# Nothing is excluded. (The packaged ai_augmentation/01_top_opportunities.iqe was,
# until PR #2602 removed its merge-conflict markers.) An entry here is a seed that
# is known not to parse and must name its fix; a stale entry fails below.
EXCLUDED: frozenset[str] = frozenset()

# A floor, so a broken glob or a moved directory cannot pass vacuously.
MIN_SEED_FILES = 600


def _seed_files() -> list[Path]:
    files: list[Path] = []
    for root in SEED_ROOTS:
        files.extend(sorted(root.rglob("*.iqe")))
    return files


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


ALL_SEEDS = _seed_files()
CHECKED = [p for p in ALL_SEEDS if _rel(p) not in EXCLUDED]


def test_seed_inventory_is_not_empty():
    assert len(ALL_SEEDS) >= MIN_SEED_FILES, (
        f"found only {len(ALL_SEEDS)} .iqe seed files under {[_rel(r) for r in SEED_ROOTS]}"
    )


def test_exclusions_name_real_files():
    """An exclusion that no longer names a file is stale and must be removed."""
    present = {_rel(p) for p in ALL_SEEDS}
    assert EXCLUDED <= present, f"stale exclusions: {sorted(EXCLUDED - present)}"


@pytest.mark.parametrize("path", CHECKED, ids=_rel)
def test_seed_query_parses(path: Path):
    # Exactly what /iqe/run and /api/v1/iqe/run do: read the file, parse the text.
    ast = parse(path.read_text(encoding="utf-8"))
    assert isinstance(ast, ForeachNode)
    assert ast.select is not None


def test_infra_preapply_gate_skips_no_seed_query():
    """The pre-apply gate loads infra/*.iqe its own way; none may be skipped."""
    from tools.infra_canvas import preapply_gate

    parsed, skipped = preapply_gate._load_iqe_queries()
    assert parsed, "the pre-apply gate loaded no infra seed queries"
    assert skipped == [], f"pre-apply gate skipped seed queries: {skipped}"
