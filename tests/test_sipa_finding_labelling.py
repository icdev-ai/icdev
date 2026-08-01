# CUI // SP-CTI
"""SIPA card labelling and the inert dynamic_import allowlist.

Two symptoms, one cause. ``_high_risk_signatures`` extracted a finding's
capability with::

    detail.get("capability_type") or detail.get("rule") or ""

but a semgrep finding's detail carries ``category`` and ``rule_id`` — neither of
those keys. So ``cap`` came back empty for every signature finding, and:

1. ``_card_title`` fell back to the finding TYPE, producing cards that read
   ``Unauthorized capability 'known_bad_signature'`` — the type in the slot
   where a capability name belongs, naming nothing present in the file. Two such
   cards were opened against files that do not contain that string anywhere.

2. ``_ALLOWLIST_KEY`` is keyed by capability type and already had a
   ``dynamic_import`` entry backed by ``known_safe_dynamic_import_modules`` in
   args/integrity_config.yaml. With ``cap`` empty the lookup never matched, so
   that allowlist could never suppress anything.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.genesis.reflexes.integrity_monitor import (
    _ALLOWLIST_KEY,
    _card_title,
    _signature,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class _Row(dict):
    """Minimal stand-in for a DB row supporting ``row["k"]`` and ``.keys()``."""


def _extract_cap(detail: dict) -> str:
    """Mirror of the extraction under test, kept in one place."""
    return (
        detail.get("capability_type")
        or detail.get("category")
        or detail.get("rule")
        or detail.get("rule_id")
        or ""
    )


# ---------------------------------------------------------------------------
# Capability extraction
# ---------------------------------------------------------------------------

def test_semgrep_detail_yields_its_category_not_an_empty_string():
    """The real shape of a semgrep finding, taken from integrity_findings."""
    detail = {
        "engine": "semgrep",
        "message": "Dynamic import of a non-literal module name...",
        "rule_id": "context.integrity.semgrep_rules.sipa-dynamic-import-py",
        "category": "dynamic_import",
    }
    assert _extract_cap(detail) == "dynamic_import"


def test_extracted_category_matches_an_allowlist_key():
    """This is what makes known_safe_dynamic_import_modules able to fire."""
    detail = {"engine": "semgrep", "category": "dynamic_import"}
    assert _extract_cap(detail) in _ALLOWLIST_KEY
    assert _ALLOWLIST_KEY[_extract_cap(detail)] == "known_safe_dynamic_import_modules"


def test_a_real_capability_type_still_wins():
    detail = {"capability_type": "process_exec", "category": "dynamic_import"}
    assert _extract_cap(detail) == "process_exec"


def test_rule_id_is_the_last_resort_before_empty():
    assert _extract_cap({"rule_id": "some.rule"}) == "some.rule"
    assert _extract_cap({}) == ""


def test_extraction_mirror_matches_the_real_function():
    """_extract_cap above restates the production expression.

    A mirror that drifts silently stops testing anything, so assert the real
    source still reads the keys a semgrep detail actually carries.
    """
    import inspect

    from tools.genesis.reflexes import integrity_monitor

    src = inspect.getsource(integrity_monitor._high_risk_signatures)
    for key in ("capability_type", "category", "rule_id"):
        assert f'detail.get("{key}")' in src, (
            f"_high_risk_signatures no longer reads {key!r}; the mirror in this "
            f"test is stale"
        )


# ---------------------------------------------------------------------------
# Card titles
# ---------------------------------------------------------------------------

def test_signature_finding_is_not_described_as_a_capability():
    """'known_bad_signature' is a scanner verdict, not something code can do."""
    title = _card_title({
        "capability_type": "dynamic_import",
        "finding_type": "known_bad_signature",
        "rel_path": "workflow/coherence_checker.py",
    })
    assert "Unauthorized capability" not in title
    assert "Signature match" in title
    assert "dynamic_import" in title


def test_capability_finding_keeps_its_wording():
    title = _card_title({
        "capability_type": "process_exec",
        "finding_type": "unauthorized_capability",
        "rel_path": "tools/x.py",
    })
    assert title == "[SIPA] Unauthorized capability 'process_exec' in tools/x.py"


def test_no_card_can_name_the_finding_type_as_a_capability():
    """The exact regression: the type must never occupy the capability slot."""
    title = _card_title({
        "capability_type": "",
        "finding_type": "known_bad_signature",
        "rel_path": "rag/toggle_harness.py",
    })
    assert "Unauthorized capability 'known_bad_signature'" not in title


def test_titles_stay_signature_stable_for_dedupe():
    info = {
        "capability_type": "dynamic_import",
        "finding_type": "known_bad_signature",
        "rel_path": "a/b.py",
    }
    assert _card_title(info) == _card_title(dict(info))


def test_signature_key_separates_distinct_categories():
    """An empty cap collapsed every signature finding in a file into one key."""
    a = _signature("known_bad_signature", "a/b.py", "dynamic_import")
    b = _signature("known_bad_signature", "a/b.py", "process_exec")
    assert a != b


# ---------------------------------------------------------------------------
# The authorizations themselves
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def allowlist() -> list:
    cfg = yaml.safe_load(
        (REPO_ROOT / "args" / "integrity_config.yaml").read_text(encoding="utf-8")
    )
    return cfg.get("known_safe_dynamic_import_modules") or []


@pytest.mark.parametrize(
    "path",
    [
        "tools/workflow/coherence_checker.py",
        "workflow/coherence_checker.py",
        "tools/rag/toggle_harness.py",
        "rag/toggle_harness.py",
    ],
)
def test_triaged_modules_are_authorized(path, allowlist):
    """Both path forms: the self-scan reports paths relative to tools/."""
    assert path in allowlist


def test_authorized_modules_actually_perform_a_dynamic_import(allowlist):
    """Guard against authorizing a file that never needed it.

    An allowlist entry for a module with no dynamic import is dead config that
    silently widens the exemption surface if that file later grows one.
    """
    for rel in ("tools/workflow/coherence_checker.py", "tools/rag/toggle_harness.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        assert "import_module" in text or "__import__" in text, (
            f"{rel} is allowlisted for dynamic_import but performs none"
        )
