#!/usr/bin/env python3
"""tools/ ↔ icdev/tools/ content-drift must not grow — CUI // SP-CTI.

`icdev/tools/` is the packaged copy of `tools/`. A file present in BOTH trees
whose bytes differ means a fix landed on one side only — and the packaged side
is what a wheel install and every generated child app actually get.

This is not hypothetical. `tools/db/schema/pg_consolidated.sql` carried the
`dic_chat_memory` turn-schema fix while its `icdev/` twin still shipped the
broken migration-191 shape, so a fresh bootstrap from the wheel recreated the
exact bug the fix removed.

Nothing caught it. `coherence_checker.check_mirror_drift` audits **8 named
packages** out of the **200** that have twins, and compares only `*.py` — so a
drifted `.sql` under `tools/db/schema` was invisible on both counts. A curated
list is how this class of bug survives; `discover_mirrored_paths()` derives
coverage from the tree instead.

Content drift is the low-noise half. "Missing-from-mirror" legitimately spikes
while a subsystem is mid-flight (a sibling session adding `tools/browser/cdp/*`
is not a defect), so this gates only on files that exist on both sides and
disagree.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from tools.dx.mirror_parity import audit_path, discover_mirrored_paths

_BASELINE = pathlib.Path(__file__).resolve().parents[1] / "args" / "mirror_drift_baseline.yaml"


def _baseline() -> dict:
    if not _BASELINE.is_file():
        return {}
    data = yaml.safe_load(_BASELINE.read_text(encoding="utf-8")) or {}
    raw = {str(k): int(v) for k, v in (data.get("content_drift") or {}).items()}
    return {k: v for k, v in raw.items() if k in GATED_PACKAGES}


#: Packages this test sweeps on every CI run.
#:
#: NOT the full 200. `audit_all()` walks ~20k file pairs and takes ~68s on a
#: cold checkout — and CI is always cold — which does not belong in the unit
#: tier. These are the packages carrying grounding, governance, schema and the
#: document pipeline: the ones where a fix landing on one side only is a
#: correctness bug rather than stale convenience code.
#:
#: The full sweep stays a CLI concern:
#:     python tools/dx/mirror_parity.py --all --gate
GATED_PACKAGES = (
    "quality",       # citation/claim grounding — the TRUST core
    "cortex",        # governed facade
    "db",            # schema + migrations (this is where pg_consolidated.sql drifted)
    "document_intelligence",
    "rag",
    "knowledge_graph",
)


@pytest.fixture(scope="module")
def current() -> dict:
    """Content-drift counts for the gated packages only (fast path)."""
    out = {}
    for pkg in GATED_PACKAGES:
        r = audit_path(pkg)
        if r["content_drift"]:
            out[pkg] = len(r["content_drift"])
    return out


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def test_no_new_package_starts_drifting(current):
    baseline = _baseline()
    new = sorted(set(current) - set(baseline))
    assert not new, (
        f"package(s) newly out of mirror parity: {new}. A fix landed in tools/ "
        "and not in icdev/, so the packaged copy still has the old behaviour. "
        "Reconcile with: python tools/dx/mirror_parity.py --paths <pkg> --fix"
    )


def test_drift_does_not_grow(current):
    baseline = _baseline()
    grew = {
        pkg: (baseline[pkg], n)
        for pkg, n in current.items()
        if pkg in baseline and n > baseline[pkg]
    }
    assert not grew, (
        f"content drift grew (package: was -> now): {grew}. Mirror the changed "
        "file rather than raising the baseline."
    )


def test_baseline_has_no_stale_entries(current):
    """A reconciled package must leave the baseline, or the file becomes fiction."""
    baseline = _baseline()
    stale = sorted(set(baseline) - set(current))
    assert not stale, (
        f"package(s) listed as drifting but now clean: {stale}. Remove them from "
        "args/mirror_drift_baseline.yaml so the file keeps meaning something."
    )


# --------------------------------------------------------------------------- #
# The discovery that makes the gate honest
# --------------------------------------------------------------------------- #


def test_discovery_finds_far_more_than_the_curated_list():
    """Guard the premise. If discovery regressed to a handful, the gate is hollow."""
    found = discover_mirrored_paths()
    assert len(found) > 100, f"only {len(found)} mirrored packages discovered"


@pytest.mark.parametrize("pkg", ["quality", "cortex", "db", "document_intelligence"])
def test_trust_critical_packages_are_discovered(pkg):
    """The packages that carry grounding, governance and schema.

    `db` and `document_intelligence` are absent from
    coherence_checker's 8-package list — which is exactly why their drift went
    unnoticed.
    """
    assert pkg in discover_mirrored_paths()


def test_baseline_file_exists_and_parses():
    assert _BASELINE.is_file(), "args/mirror_drift_baseline.yaml missing"
    assert _baseline(), "baseline parsed empty — the gate would be vacuous"
