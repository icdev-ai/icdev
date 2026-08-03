# CUI // SP-CTI
"""anz-mig-01 — declaring a DB handle and a batch shape, instead of coding them.

Two capabilities the contract could not express, both required by the DSOC and
PVM analyzer families:

``binding.connection``
    Every ``tools/dsoc_canvas/`` analyzer takes ``conn`` first and WRITES its
    finding. ``context_args`` could already route a caller-supplied connection
    into that parameter, but that pushes the open/close boilerplate onto every
    caller and leaves the lifecycle unowned — nothing in the declaration says
    who commits. Naming a ``module:callable`` factory lets the dispatcher own
    it end to end.

``binding.observable_form: list``
    ``score_advisories(advisory_ids)`` is batch-shaped. Handed a bare scalar it
    iterates a string character by character and reports having scored nothing:
    a wrong answer that looks like a clean run, which is the failure class this
    whole contract exists to remove.

The negative cases carry the weight. A connection that is opened but never
committed, or committed after the call raised, is exactly the silent-no-op
shape ``commit`` was added to prevent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.analyzers import dispatch as D  # noqa: E402
from tools.analyzers.contract import (  # noqa: E402
    ArgumentBinding,
    ConnectionBinding,
    InvalidDeclaration,
    load_contract,
)


# --------------------------------------------------------------------------- #
# Declaration parsing
# --------------------------------------------------------------------------- #

def _binding(**block):
    from tools.analyzers.contract import _parse_binding

    return _parse_binding(block, analyzer_key="t")


def test_a_connection_block_parses_into_the_declaration():
    b = _binding(
        observable_arg="prefix",
        connection={
            "param": "conn",
            "factory": "tools.dsoc_canvas.db.init_db:get_connection",
            "commit": True,
        },
    )
    assert b.connection == ConnectionBinding(
        param="conn",
        factory="tools.dsoc_canvas.db.init_db:get_connection",
        commit=True,
    )
    assert b.connection.factory_module == "tools.dsoc_canvas.db.init_db"
    assert b.connection.factory_attr == "get_connection"


def test_commit_defaults_to_false_because_writing_is_the_exception():
    b = _binding(connection={"param": "conn", "factory": "m:f"})
    assert b.connection.commit is False


@pytest.mark.parametrize("factory", ["no_colon", ":f", "m:", "m:f:g", "m f:g", ""])
def test_a_malformed_factory_reference_is_refused_at_load(factory):
    with pytest.raises(InvalidDeclaration):
        _binding(connection={"param": "conn", "factory": factory})


def test_an_unknown_connection_key_is_refused():
    """A typo must not be silently ignored — `commmit: true` would never write."""
    with pytest.raises(InvalidDeclaration) as exc:
        _binding(connection={"param": "conn", "factory": "m:f", "commmit": True})
    assert "commmit" in str(exc.value)


def test_a_non_boolean_commit_is_refused():
    with pytest.raises(InvalidDeclaration):
        _binding(connection={"param": "conn", "factory": "m:f", "commit": "yes"})


def test_one_parameter_cannot_have_two_sources():
    """Connection vs context_args: refuse to guess, same rule the others follow."""
    with pytest.raises(InvalidDeclaration) as exc:
        _binding(
            connection={"param": "conn", "factory": "m:f"},
            context_args={"conn": "conn"},
        )
    assert "conn" in str(exc.value)


def test_the_connection_parameter_cannot_also_receive_the_observable():
    with pytest.raises(InvalidDeclaration):
        _binding(
            observable_arg="conn",
            connection={"param": "conn", "factory": "m:f"},
        )


@pytest.mark.parametrize("form", ["scalar", "list"])
def test_observable_form_accepts_the_closed_set(form):
    assert _binding(observable_arg="x", observable_form=form).observable_form == form


def test_an_unknown_observable_form_is_refused():
    with pytest.raises(InvalidDeclaration) as exc:
        _binding(observable_arg="x", observable_form="tuple")
    assert "observable_form" in str(exc.value)


def test_a_binding_carrying_only_a_connection_still_counts_as_declared():
    assert _binding(connection={"param": "conn", "factory": "m:f"}).declared
    assert _binding(observable_arg="x", observable_form="list").declared
    assert not ArgumentBinding().declared


# --------------------------------------------------------------------------- #
# Call assembly
# --------------------------------------------------------------------------- #

class _Obs:
    def __init__(self, value):
        self.value = value


def _decl(binding, entrypoint="fn", module="m"):
    import types

    return types.SimpleNamespace(
        key="t", module=module, entrypoint=entrypoint, binding=binding
    )


def test_the_observable_is_wrapped_for_a_batch_shaped_callable():
    def fn(advisory_ids=None):
        return advisory_ids

    kwargs = D.build_call(
        fn,
        _decl(_binding(observable_arg="advisory_ids", observable_form="list")),
        _Obs(7),
        {},
    )
    assert kwargs == {"advisory_ids": [7]}, "a bare 7 would be iterated, not scored"


def test_a_scalar_binding_is_left_alone():
    def fn(advisory_id):
        return advisory_id

    kwargs = D.build_call(
        fn, _decl(_binding(observable_arg="advisory_id")), _Obs(7), {}
    )
    assert kwargs == {"advisory_id": 7}


def test_the_connection_parameter_is_not_reported_as_unsourced():
    """Before anz-mig-01 this raised BindingError and the analyzer never ran."""
    def fn(conn, prefix):
        return conn, prefix

    kwargs = D.build_call(
        fn,
        _decl(_binding(
            observable_arg="prefix",
            connection={"param": "conn", "factory": "m:f"},
        )),
        _Obs("10.0.0.0/8"),
        {},
    )
    assert kwargs == {"prefix": "10.0.0.0/8"}, "conn is supplied around the call"


def test_a_connection_param_the_callable_does_not_accept_is_a_binding_error():
    def fn(prefix):
        return prefix

    with pytest.raises(D.BindingError) as exc:
        D.build_call(
            fn,
            _decl(_binding(
                observable_arg="prefix",
                connection={"param": "conn", "factory": "m:f"},
            )),
            _Obs("x"),
            {},
        )
    assert "conn" in str(exc.value)


def test_an_undeclared_observable_would_land_in_the_connection_slot():
    """The precise bug main's ArgumentBinding docstring warns about."""
    def fn(conn, prefix=None):
        return conn, prefix

    with pytest.raises(D.BindingError) as exc:
        D.build_call(
            fn,
            _decl(_binding(connection={"param": "conn", "factory": "m:f"})),
            _Obs("10.0.0.0/8"),
            {},
        )
    assert "observable_arg" in str(exc.value)


# --------------------------------------------------------------------------- #
# Connection lifecycle — the part that writes
# --------------------------------------------------------------------------- #

class _FakeConn:
    def __init__(self):
        self.committed = False
        self.closed = False

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _with_factory(monkeypatch, conn, *, commit):
    """Point a declaration's factory at *conn* without importing a canvas."""
    import types

    module = types.ModuleType("fake_canvas_db")
    module.get_connection = lambda: conn
    monkeypatch.setitem(sys.modules, "fake_canvas_db", module)
    return _decl(_binding(
        observable_arg="prefix",
        connection={
            "param": "conn",
            "factory": "fake_canvas_db:get_connection",
            "commit": commit,
        },
    ))


def test_the_connection_is_injected_committed_and_closed(monkeypatch):
    conn = _FakeConn()
    seen = {}

    def fn(conn, prefix):
        seen["conn"] = conn
        seen["prefix"] = prefix
        return {"ok": True}

    decl = _with_factory(monkeypatch, conn, commit=True)
    out = D._call_with_connection(fn, decl, {"prefix": "10.0.0.0/8"})

    assert out == {"ok": True}
    assert seen["conn"] is conn, "the analyzer got the handle the factory made"
    assert conn.committed, "commit: true and the call succeeded — the write must land"
    assert conn.closed


def test_commit_false_does_not_commit(monkeypatch):
    conn = _FakeConn()
    decl = _with_factory(monkeypatch, conn, commit=False)
    D._call_with_connection(lambda conn, prefix: None, decl, {"prefix": "x"})
    assert not conn.committed
    assert conn.closed


def test_a_raising_analyzer_is_not_committed_but_is_closed(monkeypatch):
    """A half-finished analysis committed is worse than nothing written."""
    conn = _FakeConn()

    def fn(conn, prefix):
        raise RuntimeError("detector blew up")

    decl = _with_factory(monkeypatch, conn, commit=True)
    with pytest.raises(RuntimeError):
        D._call_with_connection(fn, decl, {"prefix": "x"})
    assert not conn.committed, "commit must be reachable only on success"
    assert conn.closed, "the handle must not leak when the analyzer raises"


def test_a_close_failure_does_not_mask_the_result(monkeypatch):
    class _BadClose(_FakeConn):
        def close(self):
            raise OSError("socket already gone")

    conn = _BadClose()
    decl = _with_factory(monkeypatch, conn, commit=False)
    assert D._call_with_connection(lambda conn, prefix: "value", decl, {"prefix": "x"}) == "value"


def test_an_unresolvable_factory_is_reported_against_the_analyzer(monkeypatch):
    decl = _decl(_binding(
        observable_arg="prefix",
        connection={"param": "conn", "factory": "nonexistent_module_xyz:get_connection"},
    ))
    with pytest.raises(D.BindingError) as exc:
        D._call_with_connection(lambda conn, prefix: None, decl, {"prefix": "x"})
    assert "nonexistent_module_xyz" in str(exc.value)


# --------------------------------------------------------------------------- #
# The shipped contract
# --------------------------------------------------------------------------- #

def test_the_reported_families_are_declared_and_resolvable():
    """The point of the card: five analyzers reachable through the contract."""
    contract = load_contract()
    keys = {d.key for d in contract.analyzers}
    assert {
        "bgp_prefix_hijack", "bgp_route_leak",
        "pvm_risk_prediction", "pvm_triage_scoring", "pvm_attack_surface",
    } <= keys

    for key in ("bgp_prefix_hijack", "bgp_route_leak"):
        decl = next(d for d in contract.analyzers if d.key == key)
        assert decl.binding.connection is not None
        assert decl.binding.connection.commit is True, (
            "these analyzers write; an uncommitted detection is a silent no-op"
        )

    triage = next(d for d in contract.analyzers if d.key == "pvm_triage_scoring")
    assert triage.binding.observable_form == "list"


def test_every_declared_binding_matches_its_real_signature():
    """A declaration that no longer fits its callable is caught here, not at dispatch."""
    contract = load_contract()
    for decl in contract.analyzers:
        if not decl.binding.declared:
            continue
        try:
            func = D.resolve_entrypoint(decl)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"{decl.key}: entrypoint unavailable in this env ({exc})")
        names = {p.name for p in D._positional_parameters(func)}
        if decl.binding.observable_arg:
            assert decl.binding.observable_arg.split(".")[0] in names, decl.key
        if decl.binding.connection:
            assert decl.binding.connection.param in names, decl.key
        for param, _ in decl.binding.context_args:
            assert param in names, f"{decl.key}: {param}"
