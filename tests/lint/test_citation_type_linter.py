# CUI // SP-CTI
"""The gate that makes an unknown citation_type unshippable (cxo-trust-02).

Two subsystems shipped the same silent failure — Cortex governance with
``"cortex"`` and GovChain asset tokenization with ``"asset_token"`` — because
``register_citation()`` raises on an unknown vocabulary value and every caller
swallows it. Zero rows were written by either, for their entire existence, with
no error anywhere.

The decisive tests here are the two that reconstruct those exact call sites and
assert the linter catches them. A gate justified by a bug it could not have
caught is theatre.
"""
import importlib

import pytest

linter = importlib.import_module("tools.lint.citation_type_linter")


@pytest.fixture()
def tree(tmp_path):
    """A miniature repo: <root>/tools/<pkg>/<file>.py"""
    def _make(rel: str, body: str):
        p = tmp_path / "tools" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p
    return tmp_path, _make


# --------------------------------------------------------------------------- #
# It would have caught the two bugs that motivated it
# --------------------------------------------------------------------------- #


def test_catches_the_cortex_governance_bug(tree):
    """Reconstructs tools/cortex/governance.py as it shipped."""
    root, make = tree
    make("cortex/governance.py", '''
def _gate_register_provenance(output_text, ctx, operation, record_id):
    return _mod("provenance.registry").register_citation(
        citation_type="kortex",
        source_table="cortex_governance",
        source_record_id=record_id,
    )
''')
    hits = linter.scan_tree(root)
    assert len(hits) == 1
    assert hits[0]["citation_type"] == "kortex"
    assert hits[0]["file"] == "tools/cortex/governance.py"


def test_catches_the_govchain_asset_token_bug(tree):
    """Reconstructs tools/blockchain/asset_ledger.py as it shipped."""
    root, make = tree
    make("blockchain/asset_ledger.py", '''
    reg_id = register_citation(
        citation_type="asset_tokens",
        source_table="govchain_assets",
        source_record_id=asset_id,
    )
    if reg_id:
        anchor()
''')
    hits = linter.scan_tree(root)
    assert [h["citation_type"] for h in hits] == ["asset_tokens"]


def test_the_real_values_now_pass(tree):
    """'cortex' and 'asset_token' are in the vocabulary as of cxo-trust-01."""
    root, make = tree
    make("cortex/governance.py", 'x = register_citation(citation_type="cortex")\n')
    make("blockchain/asset_ledger.py", 'y = register_citation(citation_type="asset_token")\n')
    assert linter.scan_tree(root) == []


# --------------------------------------------------------------------------- #
# Vocabulary source
# --------------------------------------------------------------------------- #


def test_vocabulary_is_imported_not_copied():
    """A linter holding its own copy of the list is a second source of truth."""
    from tools.provenance.citation_types import CITATION_TYPES

    assert linter.known_types() == tuple(CITATION_TYPES)


def test_every_known_type_is_accepted(tree):
    root, make = tree
    body = "\n".join(
        f'v{i} = register_citation(citation_type="{t}")'
        for i, t in enumerate(linter.known_types())
    )
    make("thing/mod.py", body + "\n")
    assert linter.scan_tree(root) == []


# --------------------------------------------------------------------------- #
# Exemptions must be narrow
# --------------------------------------------------------------------------- #


def test_inline_exemption_comment_is_honoured(tree):
    root, make = tree
    make("thing/mod.py", 'x = register_citation(citation_type="bogus")  # citation-type-ok\n')
    assert linter.scan_tree(root) == []


def test_tests_directory_is_exempt(tree):
    """A test asserting a bogus type is REJECTED must not itself be flagged."""
    root, make = tree
    make("thing/tests/test_x.py", 'register_citation(citation_type="definitely_bogus")\n')
    assert linter.scan_tree(root) == []


def test_dynamic_values_are_not_flagged(tree):
    """A variable cannot be checked statically; flagging it would be noise."""
    root, make = tree
    make("thing/mod.py", "register_citation(citation_type=some_var)\n")
    assert linter.scan_tree(root) == []


def test_the_linter_does_not_report_itself():
    """It contains the pattern it searches for, in its docstring and PATTERN.

    Self-reporting is the fastest way to get a gate switched off.
    """
    from pathlib import Path

    root = Path(linter.REPO_ROOT)
    assert linter._is_exempt(root / "tools/lint/citation_type_linter.py", root)


# --------------------------------------------------------------------------- #
# The gate behaves as a gate
# --------------------------------------------------------------------------- #


def test_gate_flag_exits_nonzero_on_violation(tree, capsys):
    root, make = tree
    make("thing/mod.py", 'register_citation(citation_type="nope")\n')
    assert linter.main(["--path", str(root), "--gate", "--json"]) == 1


def test_without_gate_it_reports_but_does_not_block(tree):
    root, make = tree
    make("thing/mod.py", 'register_citation(citation_type="nope")\n')
    assert linter.main(["--path", str(root), "--json"]) == 0


def test_clean_tree_exits_zero(tree):
    root, make = tree
    make("thing/mod.py", 'register_citation(citation_type="rag")\n')
    assert linter.main(["--path", str(root), "--gate", "--json"]) == 0


def test_the_repo_is_currently_clean():
    """The live gate must pass on main, or it will be ignored."""
    from pathlib import Path

    hits = linter.scan_tree(Path(linter.REPO_ROOT))
    assert hits == [], f"unknown citation_type values in tools/: {hits}"
