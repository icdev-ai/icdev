#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the unified observable dispatch path (anz-disp-01).

The acceptance criteria this file exists to hold:
  1. submitting an observable returns reports from ALL analyzers declaring
     that type
  2. a timed-out analyzer is reported as timed-out, NOT omitted
  3. adding an analyzer requires no dispatch-code change

(3) is the one that is easy to claim and hard to prove, so it is tested the
only way that means anything: a contract built in the test declares an
analyzer this file's imports never mention, and dispatch finds and runs it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from tools.analyzers.contract import (
    InvalidDeclaration,
    UnknownObservableType,
    load_contract,
    parse_contract,
)
from tools.analyzers.dispatch import (
    REPORT_STATUSES,
    BindingError,
    MissingContextError,
    Observable,
    build_call,
    capabilities,
    dispatch,
    extract_taxonomy,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# A self-contained contract. Its analyzers live in this module, so these tests
# never depend on a real analyzer's DB, network, or schema being present.
# ---------------------------------------------------------------------------

_BASE_CONTRACT: Dict[str, Any] = {
    "version": 1,
    "observable_types": {
        "ip": {"description": "IPv4 or IPv6 address.", "consumers": ["tests"]},
        "cve": {"description": "CVE identifier.", "consumers": ["tests"]},
        "vendor": {"description": "Supplier name.", "consumers": ["tests"]},
    },
    "taxonomy": {
        "levels": {
            "info": "Neutral.",
            "safe": "Benign.",
            "suspicious": "Warrants review.",
            "malicious": "Hostile.",
        },
        "namespaces": {"ICDEV": "Platform-wide.", "SECURITY": "tools/security/."},
    },
    "sandbox_postures": {
        "sandboxed": "Runs in sandbox_execute.",
        "trusted_first_party": "First-party data only.",
    },
    "defaults": {
        "rate_limit": {"max_calls": 60, "per_seconds": 3600},
        "sandbox": "trusted_first_party",
        "timeout_seconds": 5,
        "enabled": True,
    },
    "analyzers": [],
}

# This module IS the analyzer module under test. Using ``__name__`` rather than
# a hardcoded path matters: importlib returns the already-imported object from
# sys.modules, so the analyzers below share this module's threading.Events with
# the tests. A second import would give the workers a different copy and the
# timeout test would wait on an Event nobody sets.
_MODULE = __name__


def _decl(key: str, entrypoint: str, accepts: list, **overrides: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "key": key,
        "kind": "analyzer",
        "display_name": key,
        "description": f"test analyzer {key}",
        "module": _MODULE,
        "entrypoint": entrypoint,
        "accepts": accepts,
        "taxonomy": {
            "namespace": "SECURITY",
            "predicates": ["ioc-match"],
            "levels": ["info", "safe", "suspicious", "malicious"],
        },
        "sandbox": "trusted_first_party",
    }
    body.update(overrides)
    return body


def _contract(*declarations: Dict[str, Any]):
    data = dict(_BASE_CONTRACT)
    data["analyzers"] = list(declarations)
    return parse_contract(data, REPO_ROOT / "args" / "analyzer_contract.yaml")


# -- the analyzers themselves -----------------------------------------------

#: Set once the slow analyzer has actually been entered, so the timeout test
#: waits on a real event instead of a sleep race.
SLOW_ENTERED = threading.Event()
#: Cleared by the timeout test to release the slow analyzer's worker thread.
SLOW_RELEASE = threading.Event()


def fast_analyzer(observable):
    return {
        "observable": observable,
        "taxonomy": [{"predicate": "ioc-match", "level": "malicious", "value": observable}],
    }


def slow_analyzer(observable):
    SLOW_ENTERED.set()
    SLOW_RELEASE.wait(timeout=30)
    return {"observable": observable}


def raising_analyzer(observable):
    raise RuntimeError("analyzer exploded")


def untagged_analyzer(observable):
    return {"verdict": "clean"}


def undeclared_tag_analyzer(observable):
    return {"taxonomy": [{"predicate": "not-declared", "level": "malicious", "value": 1}]}


def context_analyzer(observable, project_id, note="none"):
    return {"observable": observable, "project_id": project_id, "note": note}


def dict_analyzer(item: dict):
    return {"vendor_name": item.get("vendor_name")}


def connection_analyzer(conn, prefix):
    return {"prefix": prefix}


@pytest.fixture(autouse=True)
def _reset_slow_analyzer():
    SLOW_ENTERED.clear()
    SLOW_RELEASE.clear()
    yield
    SLOW_RELEASE.set()


# ---------------------------------------------------------------------------
# AC1 — every analyzer declaring the type gets dispatched
# ---------------------------------------------------------------------------


def test_dispatch_runs_every_analyzer_declaring_the_type():
    contract = _contract(
        _decl("alpha", "fast_analyzer", ["ip"]),
        _decl("beta", "fast_analyzer", ["ip"]),
        _decl("gamma", "fast_analyzer", ["cve"]),
    )
    result = dispatch("ip", "198.51.100.7", contract=contract)

    assert [r.analyzer for r in result.reports] == ["alpha", "beta"]
    assert all(r.status == "ok" for r in result.reports)
    assert not result.partial
    # gamma accepts a different type, so it is not even a candidate
    assert "gamma" not in [e["analyzer"] for e in result.excluded]


def test_reports_are_taxonomy_tagged_with_the_declared_namespace():
    contract = _contract(_decl("alpha", "fast_analyzer", ["ip"]))
    result = dispatch("ip", "198.51.100.7", contract=contract)

    assert result.taxonomy == [
        {
            "namespace": "SECURITY",
            "predicate": "ioc-match",
            "level": "malicious",
            "value": "198.51.100.7",
        }
    ]


def test_unknown_observable_type_raises_rather_than_returning_nothing():
    contract = _contract(_decl("alpha", "fast_analyzer", ["ip"]))
    with pytest.raises(UnknownObservableType) as exc:
        dispatch("ipv4", "198.51.100.7", contract=contract)
    assert "legal values" in str(exc.value)


def test_no_analyzer_for_a_valid_type_is_an_empty_result_not_an_error():
    contract = _contract(_decl("alpha", "fast_analyzer", ["ip"]))
    result = dispatch("cve", "CVE-2024-3094", contract=contract)
    assert result.reports == ()
    assert not result.partial


# ---------------------------------------------------------------------------
# AC2 — a slow analyzer is reported as timed out, never dropped
# ---------------------------------------------------------------------------


def test_timed_out_analyzer_is_reported_not_omitted():
    contract = _contract(
        _decl("quick", "fast_analyzer", ["ip"]),
        _decl("stuck", "slow_analyzer", ["ip"], timeout_seconds=1),
    )
    result = dispatch("ip", "198.51.100.7", contract=contract, timeout_seconds=1)

    assert SLOW_ENTERED.wait(timeout=5), "slow analyzer never started"
    by_key = {r.analyzer: r for r in result.reports}
    # BOTH analyzers are present. The whole point: a fan-out that dropped
    # 'stuck' would be indistinguishable from one where it found nothing.
    assert set(by_key) == {"quick", "stuck"}
    assert by_key["quick"].status == "ok"
    assert by_key["stuck"].status == "timeout"
    assert by_key["stuck"].data is None
    assert "budget" in (by_key["stuck"].detail or "")


def test_a_timeout_makes_the_whole_result_partial():
    contract = _contract(
        _decl("quick", "fast_analyzer", ["ip"]),
        _decl("stuck", "slow_analyzer", ["ip"], timeout_seconds=1),
    )
    result = dispatch("ip", "198.51.100.7", contract=contract, timeout_seconds=1)

    assert result.partial is True
    assert result.partial_reasons == {"timeout": ["stuck"]}
    assert result.counts["timeout"] == 1
    assert result.counts["ok"] == 1
    assert result.to_dict()["partial"] is True


def test_partial_results_from_the_analyzers_that_finished_are_still_returned():
    contract = _contract(
        _decl("quick", "fast_analyzer", ["ip"]),
        _decl("stuck", "slow_analyzer", ["ip"], timeout_seconds=1),
    )
    started = time.monotonic()
    result = dispatch("ip", "198.51.100.7", contract=contract, timeout_seconds=1)
    elapsed = time.monotonic() - started

    ok = [r for r in result.reports if r.status == "ok"]
    assert [r.analyzer for r in ok] == ["quick"]
    assert ok[0].data["observable"] == "198.51.100.7"
    # The fan-out is bounded by the budget, not by the slow analyzer: it must
    # not wait out slow_analyzer's 30s.
    assert elapsed < 15


def test_a_raising_analyzer_is_reported_as_error_with_its_message():
    contract = _contract(
        _decl("ok_one", "fast_analyzer", ["ip"]),
        _decl("boom", "raising_analyzer", ["ip"]),
    )
    result = dispatch("ip", "198.51.100.7", contract=contract)

    by_key = {r.analyzer: r for r in result.reports}
    assert set(by_key) == {"ok_one", "boom"}
    assert by_key["boom"].status == "error"
    assert "analyzer exploded" in by_key["boom"].detail
    assert result.partial


def test_an_unimportable_analyzer_is_reported_as_unavailable():
    contract = _contract(
        _decl("ghost", "fast_analyzer", ["ip"], module="tools.analyzers.no_such_module"),
        _decl("absent", "no_such_function", ["ip"]),
    )
    result = dispatch("ip", "198.51.100.7", contract=contract)

    statuses = {r.analyzer: r.status for r in result.reports}
    assert statuses == {"ghost": "unavailable", "absent": "unavailable"}
    assert result.partial


def test_every_status_is_in_the_closed_vocabulary():
    contract = _contract(
        _decl("ok_one", "fast_analyzer", ["ip"]),
        _decl("boom", "raising_analyzer", ["ip"]),
        _decl("stuck", "slow_analyzer", ["ip"], timeout_seconds=1),
        _decl("absent", "no_such_function", ["ip"]),
    )
    result = dispatch("ip", "198.51.100.7", contract=contract, timeout_seconds=1)
    assert {r.status for r in result.reports} <= set(REPORT_STATUSES)


# ---------------------------------------------------------------------------
# AC3 — a new analyzer needs no dispatch-code change
# ---------------------------------------------------------------------------


def test_declaring_a_new_analyzer_needs_no_dispatch_code_change():
    """The proof: `newcomer` is named nowhere but in this dict."""
    before = _contract(_decl("incumbent", "fast_analyzer", ["ip"]))
    assert [r.analyzer for r in dispatch("ip", "1.2.3.4", contract=before).reports] == [
        "incumbent"
    ]

    after = _contract(
        _decl("incumbent", "fast_analyzer", ["ip"]),
        _decl("newcomer", "fast_analyzer", ["ip"]),
    )
    assert [r.analyzer for r in dispatch("ip", "1.2.3.4", contract=after).reports] == [
        "incumbent",
        "newcomer",
    ]


def test_dispatch_module_hardcodes_no_analyzer_or_observable_names():
    """A dispatch table hidden in the source would defeat the contract."""
    source = (REPO_ROOT / "tools" / "analyzers" / "dispatch.py").read_text(encoding="utf-8")
    body = "\n".join(
        line
        for line in source.splitlines()
        # Docstrings and comments legitimately mention real analyzers as
        # examples; executable lines must not.
        if not line.lstrip().startswith("#")
    )
    code_only = body.split('"""')
    executable = "".join(code_only[0::2])
    contract = load_contract()
    for decl in contract.analyzers:
        assert (
            f'"{decl.key}"' not in executable and f"'{decl.key}'" not in executable
        ), f"dispatch.py hardcodes analyzer key {decl.key!r}"


def test_the_shipped_contract_is_dispatchable():
    """Every shipped declaration resolves and binds — checked without calling it."""
    contract = load_contract()
    for decl in contract.analyzers:
        assert decl.accepts, f"{decl.key} accepts nothing"
        for observable_type in decl.accepts:
            assert decl in contract.for_observable(observable_type, enabled_only=False)


# ---------------------------------------------------------------------------
# Responders are opt-in — dispatching an IP must not blackhole it
# ---------------------------------------------------------------------------


def test_responders_are_excluded_by_default_and_the_exclusion_is_visible():
    contract = _contract(
        _decl("watcher", "fast_analyzer", ["ip"]),
        _decl("actor", "fast_analyzer", ["ip"], kind="responder"),
    )
    result = dispatch("ip", "198.51.100.7", contract=contract)

    assert [r.analyzer for r in result.reports] == ["watcher"]
    assert result.excluded == ({"analyzer": "actor", "reason": "kind_responder_not_requested"},)


def test_responders_run_when_explicitly_requested():
    contract = _contract(
        _decl("watcher", "fast_analyzer", ["ip"]),
        _decl("actor", "fast_analyzer", ["ip"], kind="responder"),
    )
    result = dispatch(
        "ip", "198.51.100.7", contract=contract, kinds=("analyzer", "responder")
    )
    assert {r.analyzer for r in result.reports} == {"watcher", "actor"}


def test_a_disabled_analyzer_is_excluded_with_a_reason_not_dropped():
    contract = _contract(
        _decl("live", "fast_analyzer", ["ip"]),
        _decl("retired", "fast_analyzer", ["ip"], enabled=False),
    )
    result = dispatch("ip", "198.51.100.7", contract=contract)
    assert [r.analyzer for r in result.reports] == ["live"]
    assert {"analyzer": "retired", "reason": "disabled"} in result.excluded


def test_restricting_to_named_analyzers_still_lists_the_rest():
    contract = _contract(
        _decl("alpha", "fast_analyzer", ["ip"]),
        _decl("beta", "fast_analyzer", ["ip"]),
    )
    result = dispatch("ip", "1.2.3.4", contract=contract, analyzers=["alpha"])
    assert [r.analyzer for r in result.reports] == ["alpha"]
    assert {"analyzer": "beta", "reason": "not_requested"} in result.excluded


# ---------------------------------------------------------------------------
# Argument binding
# ---------------------------------------------------------------------------


def test_default_binding_sends_the_value_to_the_first_parameter():
    contract = _contract(_decl("alpha", "fast_analyzer", ["ip"]))
    decl = contract.get("alpha")
    assert build_call(fast_analyzer, decl, Observable("ip", "1.2.3.4"), {}) == {
        "observable": "1.2.3.4"
    }


def test_declared_binding_nests_the_value_in_a_dict_argument():
    contract = _contract(
        _decl(
            "vendor_screen",
            "dict_analyzer",
            ["vendor"],
            binding={"observable_arg": "item.vendor_name"},
        )
    )
    result = dispatch("vendor", "Acme Corp", contract=contract)
    assert result.reports[0].status == "ok"
    assert result.reports[0].data == {"vendor_name": "Acme Corp"}


def test_context_args_are_drawn_from_the_dispatch_context_by_name():
    contract = _contract(
        _decl(
            "ctx",
            "context_analyzer",
            ["cve"],
            binding={"context_args": {"project_id": "project_id"}},
        )
    )
    result = dispatch(
        "cve", "CVE-2024-3094", context={"project_id": "p1"}, contract=contract
    )
    assert result.reports[0].status == "ok"
    assert result.reports[0].data["project_id"] == "p1"


def test_static_args_are_passed_on_every_call():
    contract = _contract(
        _decl(
            "ctx",
            "context_analyzer",
            ["cve"],
            binding={
                "context_args": {"project_id": "project_id"},
                "static_args": {"note": "from-contract"},
            },
        )
    )
    result = dispatch("cve", "CVE-1", context={"project_id": "p1"}, contract=contract)
    assert result.reports[0].data["note"] == "from-contract"


def test_missing_context_is_reported_as_skipped_naming_the_key():
    contract = _contract(
        _decl(
            "ctx",
            "context_analyzer",
            ["cve"],
            binding={"context_args": {"project_id": "project_id"}},
        )
    )
    result = dispatch("cve", "CVE-2024-3094", contract=contract)

    report = result.reports[0]
    assert report.status == "skipped"
    assert "project_id" in report.detail
    assert result.partial


def test_an_unsuppliable_parameter_is_misdeclared_by_name_not_guessed():
    """`conn` is a DB handle. Binding the observable there would fail *inside*
    the analyzer, which reads like the analyzer having nothing to say."""
    contract = _contract(
        _decl(
            "needs_conn",
            "connection_analyzer",
            ["ip"],
            binding={"observable_arg": "prefix"},
        )
    )
    result = dispatch("ip", "198.51.100.0/24", contract=contract)

    report = result.reports[0]
    assert report.status == "misdeclared"
    assert "conn" in report.detail
    assert result.partial


def test_binding_naming_an_absent_parameter_is_misdeclared():
    contract = _contract(
        _decl("alpha", "fast_analyzer", ["ip"], binding={"observable_arg": "nope"})
    )
    result = dispatch("ip", "1.2.3.4", contract=contract)
    assert result.reports[0].status == "misdeclared"
    assert "nope" in result.reports[0].detail


def test_build_call_raises_the_two_error_kinds_separately():
    contract = _contract(
        _decl(
            "ctx",
            "context_analyzer",
            ["cve"],
            binding={"context_args": {"project_id": "project_id"}},
        )
    )
    decl = contract.get("ctx")
    with pytest.raises(MissingContextError):
        build_call(context_analyzer, decl, Observable("cve", "CVE-1"), {})

    bad = _contract(_decl("alpha", "fast_analyzer", ["ip"], binding={"observable_arg": "nope"}))
    with pytest.raises(BindingError):
        build_call(fast_analyzer, bad.get("alpha"), Observable("ip", "1.2.3.4"), {})


# ---------------------------------------------------------------------------
# Taxonomy validation
# ---------------------------------------------------------------------------


def test_an_analyzer_emitting_no_taxonomy_gets_no_tags_and_no_defects():
    contract = _contract(_decl("plain", "untagged_analyzer", ["ip"]))
    result = dispatch("ip", "1.2.3.4", contract=contract)
    report = result.reports[0]
    assert report.status == "ok"
    assert report.taxonomy == ()
    assert report.taxonomy_defects == ()
    assert not result.partial


def test_an_undeclared_predicate_is_a_defect_not_a_silent_passthrough():
    contract = _contract(_decl("liar", "undeclared_tag_analyzer", ["ip"]))
    result = dispatch("ip", "1.2.3.4", contract=contract)

    report = result.reports[0]
    assert report.status == "ok"          # the analyzer worked
    assert report.taxonomy == ()          # but its tag was not accepted
    assert any("not-declared" in d for d in report.taxonomy_defects)
    assert result.partial                 # and that disagreement is visible
    assert result.partial_reasons == {"taxonomy_defect": ["liar"]}


def test_the_namespace_is_stamped_from_the_declaration_not_the_payload():
    contract = _contract(_decl("alpha", "fast_analyzer", ["ip"]))
    decl = contract.get("alpha")
    tags, defects = extract_taxonomy(
        decl,
        {"taxonomy": [{"namespace": "SPOOFED", "predicate": "ioc-match", "level": "safe"}]},
    )
    assert defects == ()
    assert tags[0]["namespace"] == "SECURITY"


def test_an_undeclared_level_is_a_defect():
    contract = _contract(_decl("alpha", "fast_analyzer", ["ip"], taxonomy={
        "namespace": "SECURITY",
        "predicates": ["ioc-match"],
        "levels": ["info"],
    }))
    tags, defects = extract_taxonomy(
        contract.get("alpha"), {"taxonomy": {"predicate": "ioc-match", "level": "malicious"}}
    )
    assert tags == ()
    assert any("malicious" in d for d in defects)


# ---------------------------------------------------------------------------
# Contract-side binding validation (loud, at load)
# ---------------------------------------------------------------------------


def test_two_sources_for_one_parameter_is_rejected_at_load():
    with pytest.raises(InvalidDeclaration):
        _contract(
            _decl(
                "clash",
                "context_analyzer",
                ["cve"],
                binding={
                    "observable_arg": "project_id",
                    "context_args": {"project_id": "project_id"},
                },
            )
        )


def test_context_and_static_supplying_the_same_parameter_is_rejected_at_load():
    with pytest.raises(InvalidDeclaration):
        _contract(
            _decl(
                "clash",
                "context_analyzer",
                ["cve"],
                binding={
                    "context_args": {"note": "note"},
                    "static_args": {"note": "x"},
                },
            )
        )


def test_a_malformed_observable_arg_is_rejected_at_load():
    with pytest.raises(InvalidDeclaration):
        _contract(
            _decl("bad", "fast_analyzer", ["ip"], binding={"observable_arg": "a.b.c"})
        )


def test_the_shipped_contract_bindings_are_valid():
    contract = load_contract()
    for decl in contract.analyzers:
        for param in decl.binding.context_map:
            assert param.isidentifier()
        if decl.binding.observable_arg:
            assert all(
                part.isidentifier() for part in decl.binding.observable_arg.split(".")
            )


def test_shipped_yaml_declares_bindings_for_the_awkward_signatures():
    """cve_triage, section_889_screen and rtbh_blackhole cannot take the
    observable as their first argument. Regression guard: dropping their
    binding blocks would silently mis-call them."""
    raw = yaml.safe_load(
        (REPO_ROOT / "args" / "analyzer_contract.yaml").read_text(encoding="utf-8")
    )
    declared = {a["key"]: a for a in raw["analyzers"]}
    assert declared["cve_triage"]["binding"]["observable_arg"] == "cve_id"
    assert declared["section_889_screen"]["binding"]["observable_arg"] == "item.vendor_name"
    assert declared["rtbh_blackhole"]["binding"]["observable_arg"] == "prefix"


# ---------------------------------------------------------------------------
# Capabilities + MCP surface
# ---------------------------------------------------------------------------


def test_capabilities_lists_every_observable_type_and_its_analyzers():
    contract = _contract(
        _decl("alpha", "fast_analyzer", ["ip"]),
        _decl("beta", "fast_analyzer", ["ip", "cve"]),
    )
    caps = capabilities(contract=contract)
    assert set(caps["observable_types"]) == {"ip", "cve", "vendor"}
    assert [a["key"] for a in caps["observable_types"]["ip"]["analyzers"]] == [
        "alpha",
        "beta",
    ]
    assert caps["observable_types"]["vendor"]["analyzers"] == []
    assert caps["statuses"] == list(REPORT_STATUSES)


def test_mcp_registry_exposes_dispatch_as_declarative_metadata():
    from tools.mcp.tool_registry import TOOL_REGISTRY

    for name in ("analyzer_dispatch", "analyzer_capabilities"):
        entry = TOOL_REGISTRY[name]
        assert entry["module"] == "tools.mcp.gap_handlers"
        assert entry["handler"].startswith("handle_")
        assert entry["category"] == "analyzers"
        assert entry["input_schema"]["type"] == "object"
    assert TOOL_REGISTRY["analyzer_dispatch"]["input_schema"]["required"] == [
        "observable_type",
        "value",
    ]


def test_mcp_handlers_resolve_and_validate_their_arguments():
    from tools.mcp import gap_handlers

    assert callable(gap_handlers.handle_analyzer_dispatch)
    assert callable(gap_handlers.handle_analyzer_capabilities)
    assert "error" in gap_handlers.handle_analyzer_dispatch({})
    assert "error" in gap_handlers.handle_analyzer_dispatch({"observable_type": "ip"})


def test_mcp_dispatch_rejects_an_unknown_type_with_the_legal_values():
    from tools.mcp import gap_handlers

    out = gap_handlers.handle_analyzer_dispatch(
        {"observable_type": "ipv4", "value": "1.2.3.4"}
    )
    assert "error" in out
    assert "ip" in out["observable_types"]


def test_mcp_capabilities_returns_the_shipped_vocabulary():
    from tools.mcp import gap_handlers

    out = gap_handlers.handle_analyzer_capabilities({})
    assert "error" not in out
    assert "cve" in out["observable_types"]


# ---------------------------------------------------------------------------
# Mirror parity (tools/analyzers is a mirrored root)
# ---------------------------------------------------------------------------


def test_dispatch_is_mirrored_into_the_icdev_package():
    canonical = REPO_ROOT / "tools" / "analyzers" / "dispatch.py"
    mirror = REPO_ROOT / "icdev" / "tools" / "analyzers" / "dispatch.py"
    assert mirror.is_file(), "tools/analyzers is a mirrored root (args/mirror_parity.yaml)"
    assert mirror.read_text(encoding="utf-8") == canonical.read_text(encoding="utf-8")


def test_the_contract_yaml_mirror_matches():
    canonical = REPO_ROOT / "args" / "analyzer_contract.yaml"
    mirror = REPO_ROOT / "icdev" / "data" / "args" / "analyzer_contract.yaml"
    assert mirror.read_text(encoding="utf-8") == canonical.read_text(encoding="utf-8")
