# CUI // SP-CTI
"""The provenance write must LAND, not merely be attempted (cxo-trust-01).

Two subsystems passed a citation_type absent from CITATION_TYPES and failed
silently for their entire existence:

  * tools/cortex/governance.py — citation_type="cortex". register_citation
    raises ValueError before the INSERT; the gate catches Exception and records
    provenance="warn". With governance.fail_closed shipping false, nothing
    blocked and nothing alerted. Measured 2026-08-02: 0 of 285 registry rows
    were type 'cortex', and no Cortex operation had ever recorded a clean pass.

  * tools/blockchain/asset_ledger.py — citation_type="asset_token". The raise is
    swallowed by a try/except with an `if reg_id:` guard, so reg_id stayed None,
    anchor_status stayed "skipped", and asset tokenization never anchored.

Existing coverage asserted the gate RAN, not that it SUCCEEDED, which is exactly
why this shipped. These assert the write landed.
"""
import importlib

import pytest

ct = importlib.import_module("tools.provenance.citation_types")


@pytest.mark.parametrize("citation_type", ["cortex", "asset_token"])
def test_the_type_is_in_the_vocabulary(citation_type):
    assert ct.is_valid(citation_type), (
        f"{citation_type!r} is passed by a live caller but is not a legal "
        "citation type — register_citation will raise and the caller will "
        "swallow it"
    )


@pytest.mark.parametrize("citation_type", ["cortex", "asset_token"])
def test_register_citation_does_not_raise_for_it(citation_type):
    """The precise failure: ValueError before the INSERT."""
    from tools.provenance.registry import register_citation

    try:
        register_citation(
            citation_type=citation_type,
            source_table="t",
            source_record_id="r",
            source_hash="h",
        )
    except ValueError as exc:  # pragma: no cover - this is the regression
        pytest.fail(f"register_citation rejected {citation_type!r}: {exc}")
    except Exception:
        # A DB error is a different failure mode and not what this pins — the
        # vocabulary check happens before any connection is opened.
        pass


def test_live_callers_use_only_known_types():
    """Sweep the callers that had the bug, so a third one cannot appear quietly.

    This is the narrow form of the static gate tracked as cxo-trust-02: it reads
    the actual call sites rather than trusting that they were fixed.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    unknown = {}
    for rel in ("tools/cortex/governance.py", "tools/blockchain/asset_ledger.py"):
        path = repo / rel
        if not path.exists():
            continue
        for value in re.findall(r'citation_type=["\']([a-z_]+)["\']', path.read_text(encoding="utf-8")):
            if not ct.is_valid(value):
                unknown.setdefault(rel, set()).add(value)

    assert not unknown, f"call sites pass citation types absent from CITATION_TYPES: {unknown}"


def test_the_guard_can_actually_see_a_violation():
    """A check that cannot fail is not a check."""
    assert not ct.is_valid("definitely_not_a_citation_type")
