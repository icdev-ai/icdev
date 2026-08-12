# CUI // SP-CTI
"""exa-bench-02: an ENABLED agent adapter may not be an inert stub.

The EXA card's thesis is that ICDEV's signature bug is the declared-but-
unconsumed capability, and ``copilot_cli`` was a perfect specimen: registered in
``registry._ensure_loaded``, listed in the manifest, and unable to report
available under any input because its check was ::

    return False and (shutil.which("gh") is not None)

``False and …`` short-circuits. exa-bench-02's acceptance criteria include "no
new always-unavailable stubs are introduced", and a criterion with no gate
behind it is itself a declared-but-unconsumed capability — so this is the gate.

The invariant is deliberately scoped to adapters listed in
``enabled_adapters``. That is the set an operator can actually select, it needs
no grandfather list, and it grows on its own: an adapter joins the gate the
moment someone enables it. A module that is still openly a stub can stay in the
tree commented out of the config, which is what ``codex_cli`` did before
exa-bench-01 implemented it.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import List

import pytest
import yaml

from tools.agents import registry


def _enabled_adapter_names() -> List[str]:
    path = Path(registry._CONFIG_PATH)  # noqa: SLF001
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return sorted(config.get("enabled_adapters") or [])


ENABLED = _enabled_adapter_names()


def _adapter_ast(name: str) -> ast.Module:
    adapter = registry.get_adapter(name)
    source_file = inspect.getsourcefile(type(adapter))
    assert source_file, f"{name} has no resolvable source file"
    return ast.parse(Path(source_file).read_text(encoding="utf-8"))


def _method(tree: ast.Module, name: str) -> ast.FunctionDef:
    found = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert found, f"no {name}() defined"
    return found[0]


def test_the_gate_covers_something():
    """A parametrised suite over an empty list passes while testing nothing.

    Same failure mode the capability-consumption probe has: a broken reader
    returns the same zero a genuinely empty config does.
    """
    assert len(ENABLED) >= 3, ENABLED
    assert "copilot_cli" in ENABLED
    assert "goose_cli" in ENABLED


@pytest.mark.parametrize("name", ENABLED)
def test_available_is_not_hardcoded(name: str):
    """``available()`` must depend on the host, not on a constant.

    Checked on the AST because behaviour cannot tell "correctly reports absent"
    from "hardcoded to absent" on a host without the CLI — which is every CI
    runner, and is exactly why the copilot stub survived as long as it did.
    """
    available = _method(_adapter_ast(name), "available")

    short_circuits = [
        ast.dump(operand)
        for node in ast.walk(available)
        if isinstance(node, ast.BoolOp)
        for operand in node.values
        if isinstance(operand, ast.Constant)
    ]
    assert short_circuits == [], (
        f"{name}.available() has a constant inside a boolean operator, which "
        f"short-circuits the real check away: {short_circuits}"
    )

    returns = [
        node for node in ast.walk(available)
        if isinstance(node, ast.Return) and node.value is not None
    ]
    assert returns, f"{name}.available() returns nothing"

    # Returning a bare ``False`` is fine when it is one guarded branch among
    # several — ``local_agent`` returns False three ways and True once, and
    # every one of them depends on the host. What is NOT fine is a function
    # whose every path yields the SAME constant: that answer is baked in.
    computed = [n for n in returns if not isinstance(n.value, ast.Constant)]
    constants = {n.value.value for n in returns
                 if isinstance(n.value, ast.Constant)}
    assert computed or len(constants) > 1, (
        f"{name}.available() can only ever return {constants} — it reports "
        f"the same answer on every host, so the adapter is inert"
    )


@pytest.mark.parametrize("name", ENABLED)
def test_invoke_is_not_an_unconditional_raise(name: str):
    """The stub shape: ``invoke()`` whose whole body is ``raise NotInstalled``.

    A real adapter may still raise — ``NotInstalledError`` when the backend is
    genuinely absent is the Protocol's contract — but it must do so on a
    condition, not as its only behaviour.
    """
    invoke = _method(_adapter_ast(name), "invoke")

    body = list(invoke.body)
    if body and isinstance(body[0], ast.Expr) and \
            isinstance(body[0].value, ast.Constant) and \
            isinstance(body[0].value.value, str):
        body = body[1:]  # drop the docstring

    assert body, f"{name}.invoke() has an empty body"
    assert not (len(body) == 1 and isinstance(body[0], ast.Raise)), (
        f"{name}.invoke() is a stub: its entire body is a raise, so the "
        f"adapter is declared but can never run"
    )


# --- positive controls: the gate must REJECT the shapes it exists to catch ---

_HISTORICAL_STUB = '''
class CopilotCliAdapter:
    def available(self) -> bool:
        return False and (shutil.which("gh") is not None)

    def invoke(self, session):
        """Docstring, then nothing but a raise."""
        raise NotInstalledError("copilot_cli adapter is a stub")
'''


def _rule_short_circuit(fn: ast.FunctionDef) -> bool:
    return not any(
        isinstance(operand, ast.Constant)
        for node in ast.walk(fn) if isinstance(node, ast.BoolOp)
        for operand in node.values
    )


def _rule_answers_differ(fn: ast.FunctionDef) -> bool:
    returns = [n for n in ast.walk(fn)
               if isinstance(n, ast.Return) and n.value is not None]
    computed = [n for n in returns if not isinstance(n.value, ast.Constant)]
    constants = {n.value.value for n in returns
                 if isinstance(n.value, ast.Constant)}
    return bool(computed) or len(constants) > 1


def test_the_gate_rejects_the_stub_it_was_written_for():
    """Without this, a rule that silently passes everything looks like a gate.

    The two rules catch different shapes and neither is redundant. The
    historical stub is caught by the short-circuit rule ONLY: ``False and (…)``
    is a ``BoolOp``, so to the second rule it looks like a computed answer —
    which is precisely how it read to every human who skimmed past it.
    """
    available = _method(ast.parse(_HISTORICAL_STUB), "available")
    assert _rule_short_circuit(available) is False
    assert _rule_answers_differ(available) is True


def test_the_gate_rejects_a_bare_constant_probe():
    """The other stub shape — no boolean operator to notice, same inertness."""
    available = _method(
        ast.parse("def available(self):\n    return False\n"), "available")
    assert _rule_short_circuit(available) is True
    assert _rule_answers_differ(available) is False


def test_the_gate_accepts_a_branch_guarded_constant_return():
    """``local_agent`` returns only literals, and every one is host-dependent."""
    real = ast.parse(
        "def available(self):\n"
        "    try:\n"
        "        if probe():\n"
        "            return False\n"
        "        return True\n"
        "    except Exception:\n"
        "        return False\n"
    )
    available = _method(real, "available")
    assert _rule_short_circuit(available) is True
    assert _rule_answers_differ(available) is True


@pytest.mark.parametrize("name", ENABLED)
def test_enabled_adapter_implements_the_whole_protocol(name: str):
    """An enabled adapter the registry cannot import is worse than a stub."""
    adapter = registry.get_adapter(name)
    for method in ("available", "prepare_prompt", "invoke",
                   "detect_completion", "parse_response"):
        assert callable(getattr(adapter, method, None)), f"{name}.{method}"
