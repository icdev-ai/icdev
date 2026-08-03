#!/usr/bin/env python3
# CUI // SP-CTI
"""Analyzer contract input bindings and behavioural parity (anz-mig-01).

anz-mig-01 ports the DSOC and PVM analyzer families onto the contract declared
by anz-con-01. The migration's only success criterion is that nothing about
those analyzers changed, so these tests are mostly about proving a negative.

Three groups:

``TestInputBindingSchema``
    A binding is data, so a bad one has to fail at load with the offending key
    named — the lesson from ``register_citation``, where an unknown value raised
    into a caller that swallowed it and two subsystems wrote zero rows for their
    entire existence.

``TestBindingBehaviour``
    The invocation layer must be transparent: exact keyword arguments in, the
    callable's own result out, its own exceptions propagating unwrapped. Uses a
    fixture module so the success path is exercised, not just the failure path.

``TestRealContractParity``
    Runs against the SHIPPED contract. These are the tests that go red when
    someone changes ``predict_advisory_risk``'s signature or renames a DSOC
    parameter, which is the drift that would otherwise turn a declaration into a
    lie that only fails in production.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest
import yaml

from tools.analyzers import binding as binding_mod
from tools.analyzers import parity as parity_mod
from tools.analyzers.binding import (
    BindingNotDeclared,
    MissingContext,
    ObservableNotAccepted,
    UnknownAnalyzer,
    build_kwargs,
    get_declaration,
    invoke,
    verify_bindings,
)
from tools.analyzers.contract import (
    InvalidInputBinding,
    load_contract,
    parse_contract,
)

FIXTURE_MODULE = "anz_mig_01_fixture_analyzer"


# ---------------------------------------------------------------------------
# Minimal well-formed contract used by the schema and behaviour tests
# ---------------------------------------------------------------------------


def _base_contract(analyzer_extra: dict | None = None) -> dict:
    analyzer = {
        "key": "fixture_analyzer",
        "kind": "analyzer",
        "display_name": "Fixture",
        "module": FIXTURE_MODULE,
        "entrypoint": "analyze",
        "accepts": ["ip"],
        "taxonomy": {"namespace": "ICDEV", "predicates": ["fixture"], "levels": ["info"]},
        "sandbox": "trusted_first_party",
    }
    if analyzer_extra:
        analyzer.update(analyzer_extra)
    return {
        "version": 1,
        "observable_types": {
            "ip": {"description": "An address.", "consumers": ["tests/"]},
            "cve": {"description": "A CVE id.", "consumers": ["tests/"]},
        },
        "taxonomy": {
            "levels": {"info": "Neutral.", "malicious": "Hostile."},
            "namespaces": {"ICDEV": "Platform-wide."},
        },
        "sandbox_postures": {"trusted_first_party": "First-party data only."},
        "defaults": {
            "rate_limit": {"max_calls": 60, "per_seconds": 3600},
            "sandbox": "trusted_first_party",
            "timeout_seconds": 120,
            "enabled": True,
        },
        "analyzers": [analyzer],
    }


def _parse(data: dict, tmp_path):
    return parse_contract(data, tmp_path / "analyzer_contract.yaml")


@pytest.fixture
def fixture_module():
    """A real importable analyzer module, torn down after the test.

    Registered in ``sys.modules`` rather than written to disk: ``importlib``
    checks ``sys.modules`` first, so the binding layer resolves it through
    exactly the same code path it uses for ``tools.network.vuln_predictor``.
    """
    module = types.ModuleType(FIXTURE_MODULE)
    module.calls = []
    module.connections = []

    def analyze(observable, weight=1.0, note=None):
        module.calls.append({"observable": observable, "weight": weight, "note": note})
        return {"observable": observable, "weight": weight, "note": note}

    def analyze_with_conn(conn, observable, note=None):
        module.calls.append({"conn": conn, "observable": observable, "note": note})
        return {"observable": observable, "note": note}

    def explode(observable):
        raise ValueError(f"analyzer refused {observable!r}")

    class _Conn:
        def __init__(self):
            self.committed = 0
            self.closed = 0

        def commit(self):
            self.committed += 1

        def close(self):
            self.closed += 1

    def get_connection():
        conn = _Conn()
        module.connections.append(conn)
        return conn

    module.analyze = analyze
    module.analyze_with_conn = analyze_with_conn
    module.explode = explode
    module.get_connection = get_connection

    sys.modules[FIXTURE_MODULE] = module
    importlib.invalidate_caches()
    try:
        yield module
    finally:
        sys.modules.pop(FIXTURE_MODULE, None)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestInputBindingSchema:
    def test_declaration_without_binding_is_not_dispatchable(self, tmp_path):
        """anz-con-01's declarations stay valid and stay honestly un-wired."""
        contract = _parse(_base_contract(), tmp_path)
        decl = contract.get("fixture_analyzer")
        assert decl.input_binding is None
        assert decl.is_dispatchable is False
        assert contract.dispatchable() == []

    def test_binding_round_trips_through_to_dict(self, tmp_path):
        contract = _parse(
            _base_contract(
                {
                    "input_binding": {
                        "observable_param": "observable",
                        "observable_form": "list",
                        "connection": {
                            "param": "conn",
                            "factory": f"{FIXTURE_MODULE}:get_connection",
                            "commit": True,
                        },
                        "context_params": ["weight"],
                        "optional_context_params": ["note"],
                    }
                }
            ),
            tmp_path,
        )
        decl = contract.get("fixture_analyzer")
        assert decl.is_dispatchable is True
        payload = decl.to_dict()["input_binding"]
        assert payload["observable_form"] == "list"
        assert payload["connection"]["commit"] is True
        assert payload["context_params"] == ["weight"]
        assert payload["optional_context_params"] == ["note"]

    def test_unknown_observable_form_is_rejected_at_load(self, tmp_path):
        with pytest.raises(InvalidInputBinding) as excinfo:
            _parse(
                _base_contract(
                    {
                        "input_binding": {
                            "observable_param": "observable",
                            "observable_form": "tuple",
                        }
                    }
                ),
                tmp_path,
            )
        assert "tuple" in str(excinfo.value)
        assert "fixture_analyzer" in str(excinfo.value)

    def test_parameter_bound_from_two_sources_is_rejected(self, tmp_path):
        """One parameter, one source — otherwise the binding is ambiguous.

        Picking a winner silently is how a port stops being behaviour-preserving.
        """
        with pytest.raises(InvalidInputBinding) as excinfo:
            _parse(
                _base_contract(
                    {
                        "input_binding": {
                            "observable_param": "observable",
                            "context_params": ["observable", "weight"],
                        }
                    }
                ),
                tmp_path,
            )
        assert "observable" in str(excinfo.value)

    def test_malformed_connection_factory_is_rejected(self, tmp_path):
        with pytest.raises(InvalidInputBinding) as excinfo:
            _parse(
                _base_contract(
                    {
                        "input_binding": {
                            "observable_param": "observable",
                            "connection": {
                                "param": "conn",
                                "factory": "tools.db.storage.get_connection",
                            },
                        }
                    }
                ),
                tmp_path,
            )
        assert "dotted.module:callable" in str(excinfo.value)

    def test_non_identifier_observable_param_is_rejected(self, tmp_path):
        with pytest.raises(InvalidInputBinding):
            _parse(
                _base_contract({"input_binding": {"observable_param": "not a param"}}),
                tmp_path,
            )


# ---------------------------------------------------------------------------
# Behaviour of the invocation layer
# ---------------------------------------------------------------------------


class TestBindingBehaviour:
    @staticmethod
    def _contract(tmp_path, binding, **overrides):
        data = _base_contract({"input_binding": binding, **overrides})
        return _parse(data, tmp_path)

    def test_kwargs_match_a_hand_written_call(self, tmp_path, fixture_module):
        contract = self._contract(
            tmp_path,
            {
                "observable_param": "observable",
                "context_params": ["weight"],
                "optional_context_params": ["note"],
            },
        )
        decl = contract.get("fixture_analyzer")
        assert build_kwargs(decl, "ip", "203.0.113.1", context={"weight": 2.5, "note": "n"}) == {
            "observable": "203.0.113.1",
            "weight": 2.5,
            "note": "n",
        }

    def test_optional_context_is_omitted_when_absent(self, tmp_path, fixture_module):
        """Omitted, never passed as None — the callable's own default must win."""
        contract = self._contract(
            tmp_path,
            {"observable_param": "observable", "optional_context_params": ["note"]},
        )
        decl = contract.get("fixture_analyzer")
        assert build_kwargs(decl, "ip", "203.0.113.1", context={}) == {
            "observable": "203.0.113.1"
        }

    def test_list_form_wraps_a_single_observable(self, tmp_path, fixture_module):
        contract = self._contract(
            tmp_path,
            {"observable_param": "observable", "observable_form": "list"},
        )
        decl = contract.get("fixture_analyzer")
        assert build_kwargs(decl, "ip", 42, context={}) == {"observable": [42]}

    def test_missing_required_context_raises_and_names_it(self, tmp_path, fixture_module):
        contract = self._contract(
            tmp_path, {"observable_param": "observable", "context_params": ["weight"]}
        )
        decl = contract.get("fixture_analyzer")
        with pytest.raises(MissingContext) as excinfo:
            build_kwargs(decl, "ip", "203.0.113.1", context={})
        assert "weight" in str(excinfo.value)

    def test_undeclared_context_key_raises_rather_than_being_dropped(
        self, tmp_path, fixture_module
    ):
        """A dropped key reads to the caller as a filter that was applied."""
        contract = self._contract(tmp_path, {"observable_param": "observable"})
        decl = contract.get("fixture_analyzer")
        with pytest.raises(MissingContext) as excinfo:
            build_kwargs(decl, "ip", "203.0.113.1", context={"tenant_id": "acme"})
        assert "tenant_id" in str(excinfo.value)

    def test_undeclared_observable_type_raises(self, tmp_path, fixture_module):
        contract = self._contract(tmp_path, {"observable_param": "observable"})
        decl = contract.get("fixture_analyzer")
        with pytest.raises(ObservableNotAccepted):
            build_kwargs(decl, "cve", "CVE-2026-0001", context={})

    def test_result_is_returned_untouched(self, tmp_path, fixture_module):
        """The parity guarantee: no envelope, no coercion, identity preserved."""
        sentinel = object()
        fixture_module.analyze = lambda observable: sentinel
        contract = self._contract(tmp_path, {"observable_param": "observable"})
        assert invoke("fixture_analyzer", "ip", "203.0.113.1", contract=contract) is sentinel

    def test_analyzer_exceptions_propagate_unwrapped(self, tmp_path, fixture_module):
        """Catching here would kill the MCP handlers' own except clause.

        Every hand-written call site already wraps these analyzers in
        ``except Exception: return {"error": str(exc)}``. If this layer caught
        too, a port would turn every failure path into a different shape.
        """
        contract = self._contract(
            tmp_path, {"observable_param": "observable"}, entrypoint="explode"
        )
        with pytest.raises(ValueError) as excinfo:
            invoke("fixture_analyzer", "ip", "203.0.113.9", contract=contract)
        assert "203.0.113.9" in str(excinfo.value)

    def test_connection_is_opened_committed_and_closed(self, tmp_path, fixture_module):
        contract = self._contract(
            tmp_path,
            {
                "observable_param": "observable",
                "connection": {
                    "param": "conn",
                    "factory": f"{FIXTURE_MODULE}:get_connection",
                    "commit": True,
                },
            },
            entrypoint="analyze_with_conn",
        )
        invoke("fixture_analyzer", "ip", "203.0.113.2", contract=contract)
        conn = fixture_module.connections[-1]
        assert (conn.committed, conn.closed) == (1, 1)

    def test_connection_is_not_committed_when_not_declared(
        self, tmp_path, fixture_module
    ):
        """A read-only analyzer must not acquire write semantics by being ported."""
        contract = self._contract(
            tmp_path,
            {
                "observable_param": "observable",
                "connection": {
                    "param": "conn",
                    "factory": f"{FIXTURE_MODULE}:get_connection",
                },
            },
            entrypoint="analyze_with_conn",
        )
        invoke("fixture_analyzer", "ip", "203.0.113.2", contract=contract)
        conn = fixture_module.connections[-1]
        assert (conn.committed, conn.closed) == (0, 1)

    def test_connection_is_closed_when_the_analyzer_raises(
        self, tmp_path, fixture_module
    ):
        def boom(conn, observable, note=None):
            raise RuntimeError("analyzer failed")

        fixture_module.analyze_with_conn = boom
        contract = self._contract(
            tmp_path,
            {
                "observable_param": "observable",
                "connection": {
                    "param": "conn",
                    "factory": f"{FIXTURE_MODULE}:get_connection",
                    "commit": True,
                },
            },
            entrypoint="analyze_with_conn",
        )
        with pytest.raises(RuntimeError):
            invoke("fixture_analyzer", "ip", "203.0.113.2", contract=contract)
        conn = fixture_module.connections[-1]
        assert conn.closed == 1
        assert conn.committed == 0, "a failed analyzer must not commit"

    def test_declaration_without_binding_refuses_to_dispatch(
        self, tmp_path, fixture_module
    ):
        contract = _parse(_base_contract(), tmp_path)
        with pytest.raises(BindingNotDeclared):
            invoke("fixture_analyzer", "ip", "203.0.113.1", contract=contract)

    def test_disabled_analyzer_refuses_to_dispatch(self, tmp_path, fixture_module):
        contract = self._contract(
            tmp_path, {"observable_param": "observable"}, enabled=False
        )
        with pytest.raises(BindingNotDeclared):
            invoke("fixture_analyzer", "ip", "203.0.113.1", contract=contract)

    def test_unknown_key_names_the_declared_keys(self, tmp_path, fixture_module):
        contract = _parse(_base_contract(), tmp_path)
        with pytest.raises(UnknownAnalyzer) as excinfo:
            get_declaration("no_such_analyzer", contract=contract)
        assert "fixture_analyzer" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The shipped contract
# ---------------------------------------------------------------------------


class TestRealContractParity:
    @pytest.fixture(scope="class")
    def contract(self):
        return load_contract()

    def test_every_binding_matches_its_callable_signature(self, contract):
        """Goes red when a ported analyzer's signature drifts from its binding.

        Without this the declaration would keep validating while the call it
        describes had become impossible — the failure would surface at dispatch,
        in production, as a TypeError.
        """
        report = verify_bindings(contract)
        problems = [r for r in report["results"] if r["status"] == "error"]
        assert not problems, "\n".join(
            f"{r['key']}: {'; '.join(r['errors'])}" for r in problems
        )

    def test_dsoc_and_pvm_families_are_dispatchable(self, contract):
        """The anz-mig-01 acceptance criterion, asserted rather than asserted-to."""
        bound = {d.key for d in contract.dispatchable()}
        assert {"rtbh_blackhole", "bgp_prefix_hijack", "bgp_route_leak"} <= bound
        assert {
            "pvm_risk_prediction",
            "pvm_triage_scoring",
            "pvm_attack_surface",
        } <= bound

    def test_parity_call_shapes_match_the_hand_written_calls(self, contract):
        report = parity_mod.run(live=False, contract=contract)
        failures = [r for r in report["results"] if r["status"] == "FAIL"]
        assert not failures, "\n".join(
            f"{r['analyzer']}/{r['case']}: bound={r['call_shape']['bound_kwargs']} "
            f"direct={r['call_shape']['direct_kwargs']}"
            for r in failures
        )
        assert report["total"] >= 6

    def test_every_bound_analyzer_has_a_parity_case(self, contract):
        """A binding with no fixed input set is an unproven port."""
        cases, _ = parity_mod.load_cases()
        covered = {c["analyzer"] for c in cases}
        bound = {d.key for d in contract.dispatchable()}
        assert bound <= covered, f"bound but unproven: {sorted(bound - covered)}"

    def test_parity_cases_reference_real_declarations(self, contract):
        cases, _ = parity_mod.load_cases()
        for case in cases:
            decl = contract.get(case["analyzer"])
            assert decl is not None, f"parity case names undeclared {case['analyzer']!r}"
            assert decl.accepts_observable(case["observable_type"])

    def test_skipped_live_cases_state_a_reason(self, contract):
        """Coverage gaps have to be legible, not silent.

        A case that is not executed is a real hole in the proof. It is allowed —
        committing an RTBH blackhole twice to satisfy a test would be worse —
        but it must say so out loud.
        """
        cases, _ = parity_mod.load_cases()
        for case in cases:
            if not case.get("live", False):
                assert case.get("live_skip_reason"), (
                    f"{case['analyzer']}/{case['name']} is not executed live "
                    "but gives no reason"
                )

    def test_live_parity_holds_for_executable_cases(self, contract):
        """Real analyzers, run both ways, outcomes diffed.

        The comparison is direct-vs-bound in this process against this database,
        so it holds on an empty worktree DB and on a seeded one alike. On a DB
        without the NDC schema both sides raise the same OperationalError, which
        is still a real parity result: a port that converted a raise into an
        error dict would fail here.
        """
        report = parity_mod.run(live=True, contract=contract)
        executed = [r for r in report["results"] if r["live"].get("ran")]
        assert executed, "no case was executed live; the output half is unproven"
        for result in executed:
            assert result["live"]["match"], (
                f"{result['analyzer']}/{result['case']} diverged\n"
                f"  direct: {result['live']['direct']}\n"
                f"  bound : {result['live']['bound']}"
            )

    def test_parity_harness_detects_a_swapped_observable(self, contract, tmp_path):
        """The harness must be able to go red, or its green means nothing.

        Models the regression that would actually happen when porting
        detect_prefix_hijack: binding the observable to the prefix we OWN rather
        than the one we OBSERVED, which inverts the hijack verdict.
        """
        source = yaml.safe_load(contract.path.read_text(encoding="utf-8"))
        for entry in source["analyzers"]:
            if entry["key"] == "bgp_prefix_hijack":
                entry["input_binding"]["observable_param"] = "our_prefix"
                entry["input_binding"]["context_params"] = [
                    "observed_prefix",
                    "our_asn",
                    "observed_origin_asn",
                ]
        broken_path = tmp_path / "analyzer_contract.yaml"
        broken_path.write_text(yaml.safe_dump(source), encoding="utf-8")

        report = parity_mod.run(
            live=False,
            analyzer="bgp_prefix_hijack",
            contract=load_contract(broken_path),
        )
        assert report["valid"] is False
        assert report["failed"] == 1

    def test_binding_resolution_never_reaches_a_missing_entrypoint(self, contract):
        """Every declaration — bound or not — must name a callable that exists."""
        for decl in contract.analyzers:
            fn = binding_mod.resolve_entrypoint(decl)
            assert callable(fn), f"{decl.key}: {decl.module}:{decl.entrypoint}"
