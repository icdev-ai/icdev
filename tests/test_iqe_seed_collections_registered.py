# CUI // SP-CTI
"""Every seed query names a collection the executor can actually read.

"It parses" stood in for "it runs": 23 seeds parsed but named collections no
adapter registered, and the run route answered them with an empty success.
After ``catalog.load_all_adapters()`` (what /iqe/run and /api/v1/iqe/query do),
every collection a seed reads -- including each ``join()``/``union()`` argument
-- must be registered, unless it is in ``catalog.NO_EXECUTOR_SOURCE``: a short,
reasoned list of collections with no data source anywhere, which the listing
withholds and the run route refuses with that reason.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.iqe import catalog
from tools.iqe.executor import list_collections
from tools.iqe.parser import parse

REPO = Path(__file__).resolve().parents[1]
SEED_ROOTS = (
    REPO / "context" / "iqe" / "queries",
    REPO / "icdev" / "data" / "context" / "iqe" / "queries",
)
# Pre-existing packaged-only drift, left alone on purpose: icdev/data still ships
# an ai_augmentation/ seed directory that context/iqe/queries/ no longer has
# (the canvas became aiify). 01-03 read ai_augmentation.opportunities, which no
# adapter registers. Removing or re-syncing that directory is its own change;
# drop this entry when it lands -- the stale check below then fails.
PACKAGED_ONLY_DRIFT = frozenset({
    "icdev/data/context/iqe/queries/ai_augmentation/01_top_opportunities.iqe",
    "icdev/data/context/iqe/queries/ai_augmentation/02_high_value_low_risk.iqe",
    "icdev/data/context/iqe/queries/ai_augmentation/03_agentic_candidates.iqe",
})
ALL_SEEDS = [p for root in SEED_ROOTS for p in sorted(root.rglob("*.iqe"))]
SEEDS = [p for p in ALL_SEEDS if p.relative_to(REPO).as_posix() not in PACKAGED_ONLY_DRIFT]


def _rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


@pytest.fixture(scope="module")
def registered() -> set[str]:
    catalog.load_all_adapters()
    return set(list_collections())


@pytest.mark.parametrize("path", SEEDS, ids=_rel)
def test_seed_collections_are_registered_or_have_a_stated_reason(path, registered):
    ast = parse(path.read_text(encoding="utf-8"))
    for name in catalog.collections_of(ast):
        assert name in registered or name in catalog.NO_EXECUTOR_SOURCE, (
            f"{_rel(path)} reads {name!r}, which no adapter registers"
        )


def test_no_executor_source_entries_are_real_and_current(registered):
    """Each entry is unregistered (else it is stale) and read by some seed (else unused)."""
    used = {n for p in SEEDS for n in catalog.collections_of(parse(p.read_text(encoding="utf-8")))}
    for name, reason in catalog.NO_EXECUTOR_SOURCE.items():
        assert name not in registered, f"{name} is registered now: drop it from NO_EXECUTOR_SOURCE"
        assert name in used, f"{name} is read by no seed: drop it from NO_EXECUTOR_SOURCE"
        assert len(reason) > 40, f"{name} needs a stated reason"


def test_packaged_only_drift_exclusions_are_current(registered):
    """Each excluded file still exists and still reads an unregistered collection."""
    present = {p.relative_to(REPO).as_posix(): p for p in ALL_SEEDS}
    for rel in PACKAGED_ONLY_DRIFT:
        assert rel in present, f"stale exclusion: {rel}"
        names = catalog.collections_of(parse(present[rel].read_text(encoding="utf-8")))
        assert any(n not in registered for n in names), f"{rel} now runs: drop its exclusion"


def test_load_all_adapters_reports_import_failures_instead_of_hiding_them():
    failures = catalog.load_all_adapters()
    assert isinstance(failures, dict)
    for module, why in failures.items():
        assert module.startswith("tools.iqe.adapters.") and why


def test_collections_of_reads_join_and_union_arguments():
    ast = parse('foreach r in join("aiify.roadmaps", "aiify.scans", "scan_id") select r.title')
    assert catalog.collections_of(ast) == ["aiify.roadmaps", "aiify.scans"]
    ast = parse('foreach r in union("a.x", "b.y", "c.z") select r.id')
    assert catalog.collections_of(ast) == ["a.x", "b.y", "c.z"]
    ast = parse("foreach c in compliance.twin_snapshots('FedRAMP High') select c.control_id")
    assert catalog.collections_of(ast) == ["compliance.twin_snapshots"]
