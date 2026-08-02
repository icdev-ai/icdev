#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the ICDEV™ analyzer/responder contract (anz-con-01).

The acceptance criteria this file exists to hold:
  1. a new analyzer is declared entirely in config
  2. its accepted observable types and output taxonomy are machine-readable
  3. an unknown observable type is rejected AT LOAD, not at run
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

from tools.analyzers.contract import (
    ANALYZER_KINDS,
    AnalyzerContract,
    ContractFileError,
    DuplicateAnalyzerKey,
    InvalidDeclaration,
    UnknownObservableType,
    UnknownSandboxPosture,
    UnknownTaxonomyValue,
    check_constraint_sql,
    find_contract_path,
    load_contract,
    observable_types,
    parse_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def contract_path() -> Path:
    return find_contract_path()


@pytest.fixture(scope="module")
def contract(contract_path: Path) -> AnalyzerContract:
    return load_contract(contract_path)


@pytest.fixture()
def raw_contract(contract_path: Path) -> dict:
    """A mutable copy of the shipped contract for negative tests."""
    return yaml.safe_load(contract_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The shipped contract is valid
# ---------------------------------------------------------------------------


def test_shipped_contract_loads(contract: AnalyzerContract):
    assert contract.version == 1
    assert contract.observable_types, "closed vocabulary must not be empty"
    assert contract.analyzers, "contract ships with at least one declaration"


def test_shipped_declarations_reference_known_vocabulary(contract: AnalyzerContract):
    for decl in contract.analyzers:
        assert decl.kind in ANALYZER_KINDS
        assert decl.sandbox in contract.sandbox_postures
        assert decl.taxonomy.namespace in contract.taxonomy_namespaces
        for level in decl.taxonomy.levels:
            assert level in contract.taxonomy_levels
        for obs in decl.accepts:
            assert obs in contract.observable_types


def test_find_contract_path_resolves_repo_args_copy(contract_path: Path):
    """Source checkouts must resolve to args/, never to a packaged duplicate.

    Two copies read by two import namespaces is how llm_config.yaml ended up
    with three drifting copies.
    """
    assert contract_path == REPO_ROOT / "args" / "analyzer_contract.yaml"


# ---------------------------------------------------------------------------
# AC 3 — an unknown observable type is rejected AT LOAD, not at run
# ---------------------------------------------------------------------------


def test_unknown_observable_type_rejected_at_load(raw_contract: dict, contract_path: Path):
    raw_contract["analyzers"].append(
        {
            "key": "bogus_analyzer",
            "kind": "analyzer",
            "display_name": "Bogus",
            "module": "tools.security.secret_detector",
            "entrypoint": "scan",
            "accepts": ["ip", "not_a_real_observable"],
            "taxonomy": {"namespace": "SECURITY", "predicates": ["x"]},
        }
    )
    with pytest.raises(UnknownObservableType) as exc:
        parse_contract(raw_contract, contract_path)

    message = str(exc.value)
    assert "bogus_analyzer" in message, "the error must name the offending analyzer"
    assert "not_a_real_observable" in message, "the error must name the offending value"
    assert "ip" in message, "the error must list the legal values"


def test_unknown_observable_type_is_not_swallowed(raw_contract: dict, contract_path: Path):
    """The citation_type failure mode: a raise nobody sees.

    load_contract() must not degrade to a warning, a None, or an empty contract.
    """
    raw_contract["analyzers"][0]["accepts"] = ["definitely_not_a_type"]
    with pytest.raises(UnknownObservableType):
        parse_contract(raw_contract, contract_path)


def test_lookup_of_unknown_observable_type_raises(contract: AnalyzerContract):
    """A typo at a call site must not read as 'no analyzers apply'."""
    with pytest.raises(UnknownObservableType):
        contract.for_observable("nope_not_a_type")


def test_observable_type_must_be_snake_case(raw_contract: dict, contract_path: Path):
    raw_contract["observable_types"]["Not-Snake-Case"] = {"description": "bad"}
    with pytest.raises(InvalidDeclaration):
        parse_contract(raw_contract, contract_path)


# ---------------------------------------------------------------------------
# The other closed vocabularies are equally closed
# ---------------------------------------------------------------------------


def test_unknown_taxonomy_namespace_rejected(raw_contract: dict, contract_path: Path):
    raw_contract["analyzers"][0]["taxonomy"]["namespace"] = "MADE_UP"
    with pytest.raises(UnknownTaxonomyValue):
        parse_contract(raw_contract, contract_path)


def test_unknown_taxonomy_level_rejected(raw_contract: dict, contract_path: Path):
    raw_contract["analyzers"][0]["taxonomy"]["levels"] = ["info", "catastrophic"]
    with pytest.raises(UnknownTaxonomyValue):
        parse_contract(raw_contract, contract_path)


def test_unknown_sandbox_posture_rejected(raw_contract: dict, contract_path: Path):
    raw_contract["analyzers"][0]["sandbox"] = "yolo"
    with pytest.raises(UnknownSandboxPosture):
        parse_contract(raw_contract, contract_path)


def test_unknown_kind_rejected(raw_contract: dict, contract_path: Path):
    raw_contract["analyzers"][0]["kind"] = "enricher"
    with pytest.raises(InvalidDeclaration):
        parse_contract(raw_contract, contract_path)


def test_duplicate_analyzer_key_rejected(raw_contract: dict, contract_path: Path):
    raw_contract["analyzers"].append(dict(raw_contract["analyzers"][0]))
    with pytest.raises(DuplicateAnalyzerKey):
        parse_contract(raw_contract, contract_path)


def test_unsupported_version_rejected(raw_contract: dict, contract_path: Path):
    raw_contract["version"] = 99
    with pytest.raises(InvalidDeclaration):
        parse_contract(raw_contract, contract_path)


def test_empty_accepts_rejected(raw_contract: dict, contract_path: Path):
    raw_contract["analyzers"][0]["accepts"] = []
    with pytest.raises(InvalidDeclaration):
        parse_contract(raw_contract, contract_path)


def test_non_positive_rate_limit_rejected(raw_contract: dict, contract_path: Path):
    raw_contract["defaults"]["rate_limit"]["max_calls"] = 0
    with pytest.raises(InvalidDeclaration):
        parse_contract(raw_contract, contract_path)


def test_missing_contract_file_raises(tmp_path: Path):
    with pytest.raises(ContractFileError):
        load_contract(tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------
# AC 1 — a new analyzer is declared entirely in config
# ---------------------------------------------------------------------------


def test_new_analyzer_needs_only_a_config_entry(raw_contract: dict, contract_path: Path):
    """No base class, no dispatch table, no blueprint edit — just this block."""
    raw_contract["analyzers"].append(
        {
            "key": "kev_check",
            "kind": "analyzer",
            "display_name": "CISA KEV Check",
            "description": "Checks a CVE against the CISA Known Exploited Vulnerabilities catalog.",
            "module": "tools.strategos.cisa_kev_importer",
            "entrypoint": "run",
            "accepts": ["cve"],
            "taxonomy": {
                "namespace": "STRATEGOS",
                "predicates": ["kev-listed"],
                "levels": ["info", "malicious"],
            },
            "sandbox": "trusted_first_party",
        }
    )
    parsed = parse_contract(raw_contract, contract_path)

    decl = parsed.get("kev_check")
    assert decl is not None
    assert decl.accepts == ("cve",)
    assert decl in parsed.for_observable("cve")


def test_defaults_apply_to_a_minimal_declaration(raw_contract: dict, contract_path: Path):
    """An analyzer that declares nothing optional gets the strictest posture."""
    raw_contract["analyzers"].append(
        {
            "key": "minimal",
            "module": "tools.security.secret_detector",
            "entrypoint": "scan",
            "accepts": ["file_path"],
            "taxonomy": {"namespace": "SECURITY", "predicates": ["p"]},
        }
    )
    decl = parse_contract(raw_contract, contract_path).get("minimal")

    assert decl is not None
    assert decl.kind == "analyzer"
    assert decl.sandbox == raw_contract["defaults"]["sandbox"] == "sandboxed"
    assert decl.rate_limit.max_calls == raw_contract["defaults"]["rate_limit"]["max_calls"]
    assert decl.timeout_seconds == raw_contract["defaults"]["timeout_seconds"]
    assert decl.enabled is True
    # levels omitted -> the analyzer may emit any level in the closed set
    assert set(decl.taxonomy.levels) == set(raw_contract["taxonomy"]["levels"])


def test_disabled_analyzer_excluded_from_dispatch(raw_contract: dict, contract_path: Path):
    raw_contract["analyzers"][0]["enabled"] = False
    parsed = parse_contract(raw_contract, contract_path)
    key = raw_contract["analyzers"][0]["key"]
    obs = raw_contract["analyzers"][0]["accepts"][0]

    assert parsed.get(key).enabled is False
    assert all(d.key != key for d in parsed.for_observable(obs))
    assert any(d.key == key for d in parsed.for_observable(obs, enabled_only=False))


def test_kind_filter_separates_analyzers_from_responders(contract: AnalyzerContract):
    responders = contract.for_observable("ip", kind="responder")
    analyzers = contract.for_observable("ip", kind="analyzer")

    assert responders, "the seed set declares at least one responder accepting ip"
    assert all(d.kind == "responder" for d in responders)
    assert all(d.kind == "analyzer" for d in analyzers)


# ---------------------------------------------------------------------------
# AC 2 — accepted types and output taxonomy are machine-readable
# ---------------------------------------------------------------------------


def test_contract_serializes_to_json(contract: AnalyzerContract):
    payload = json.loads(json.dumps(contract.to_dict()))

    assert set(payload) >= {"version", "observable_types", "taxonomy", "analyzers"}
    for decl in payload["analyzers"]:
        assert isinstance(decl["accepts"], list) and decl["accepts"]
        assert decl["taxonomy"]["namespace"] in payload["taxonomy"]["namespaces"]
        assert isinstance(decl["taxonomy"]["predicates"], list)
        assert isinstance(decl["rate_limit"]["max_calls"], int)
        assert decl["sandbox"] in payload["sandbox_postures"]


def test_check_constraint_sql_matches_the_vocabulary(contract: AnalyzerContract):
    sql = contract.check_constraint_sql("observable_type")

    assert sql.startswith("CHECK (observable_type IN (")
    for obs in contract.observable_types:
        assert f"'{obs}'" in sql
    # module-level helper agrees with the instance method
    assert check_constraint_sql("observable_type") == sql
    assert observable_types() == tuple(sorted(contract.observable_types))


def test_sqlite_check_clause_has_no_spaces_after_commas(contract: AnalyzerContract):
    clause = contract.sqlite_check_clause()
    assert clause.startswith("CHECK(observable_type IN (")
    assert ", " not in clause


# ---------------------------------------------------------------------------
# Declarations must point at code that actually exists
# ---------------------------------------------------------------------------


def _module_path(dotted: str) -> Path:
    return REPO_ROOT / Path(*dotted.split(".")).with_suffix(".py")


def test_declared_modules_and_entrypoints_exist(contract: AnalyzerContract):
    """A declaration naming a module or function that does not exist is a lie.

    Parsed with ast rather than imported: validating a declaration must not
    execute 79 analyzer modules' import side effects.
    """
    missing = []
    for decl in contract.analyzers:
        path = _module_path(decl.module)
        if not path.is_file():
            missing.append(f"{decl.key}: module file {path.relative_to(REPO_ROOT)} not found")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if decl.entrypoint not in names:
            missing.append(f"{decl.key}: {decl.module} defines no {decl.entrypoint!r}")
    assert not missing, "declared entrypoints missing: " + "; ".join(missing)


def test_packaged_copies_have_not_drifted(contract_path: Path):
    """args/ is the source; icdev/ holds the packaged copies. They must agree.

    The loader prefers args/ in a checkout and only reads the packaged copy from
    a wheel, so a drifted copy would be invisible until install time. This is
    the gate that makes the mirror stick.
    """
    pairs = [
        (contract_path, REPO_ROOT / "icdev" / "data" / "args" / "analyzer_contract.yaml"),
        (
            REPO_ROOT / "tools" / "analyzers" / "contract.py",
            REPO_ROOT / "icdev" / "tools" / "analyzers" / "contract.py",
        ),
    ]
    for source, mirror in pairs:
        assert mirror.is_file(), f"missing packaged copy: {mirror.relative_to(REPO_ROOT)}"
        assert source.read_text(encoding="utf-8") == mirror.read_text(encoding="utf-8"), (
            f"{mirror.relative_to(REPO_ROOT)} has drifted from "
            f"{source.relative_to(REPO_ROOT)}"
        )


def test_observable_type_consumers_exist(contract: AnalyzerContract):
    """Every observable type names a real module that handles it today.

    A type with no consumer is dead vocabulary.
    """
    missing = []
    for obs in contract.observable_types.values():
        assert obs.consumers, f"observable type {obs.key!r} declares no consumer"
        for consumer in obs.consumers:
            if not (REPO_ROOT / consumer).is_file():
                missing.append(f"{obs.key}: {consumer}")
    assert not missing, "observable-type consumers not found: " + "; ".join(missing)
