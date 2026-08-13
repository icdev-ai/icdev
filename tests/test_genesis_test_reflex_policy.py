#!/usr/bin/env python3
# CUI // SP-CTI
"""Pin the generated-test policy for tests/genesis_auto/ (#tsg-gen-02).

THE POLICY: a generated test asserts behaviour, never the existence of a private
name. `assert hasattr(mod, "_THRESHOLD")` cannot fail in a way that names a
defect, and it DOES fail on any legal rename — as a hard error rather than a skip,
because the emitted guard catches ImportError but not AssertionError. tsg-gen-01
(PR #1591) removed 96 such assertions across 27 files. Without this test the next
Genesis Test Reflex run silently re-adds all 96 and that cleanup is undone.

The subject under test is the GENERATOR (tools/genesis/reflexes/test.py), not the
checked-in files: these tests generate fresh output and assert the rule holds of
what comes out. See tests/genesis_auto/README.md for the full rationale.
"""

import ast
from pathlib import Path

import pytest

from tools.genesis.reflexes import test as test_reflex
from tools.testing.api_surface_extractor import extract_api_surface

REPO_ROOT = Path(__file__).resolve().parent.parent

# A generated file must never contain this. It is the literal string tsg-gen-01
# grepped for when it removed 96 of them.
FORBIDDEN = 'hasattr(mod, "_'

MODULE_INFO = {"module": "widget/thing.py", "name": "thing"}


def _surface(constants, **extra):
    """Minimal api_surface dict with the given constant names."""
    surface = {
        "functions": [],
        "classes": [],
        "constants": [{"name": n, "type": "int", "value": "1", "line": 1} for n in constants],
        "mock_targets": [],
    }
    surface.update(extra)
    return surface


# --- The rule, on a synthetic surface ---------------------------------------


def test_private_constants_are_not_asserted():
    """A private constant in the surface produces no assertion."""
    code = test_reflex._generate_test_code(MODULE_INFO, _surface(["_MAX_RETRIES", "TIMEOUT_SEC"]))

    assert FORBIDDEN not in code, "generator emitted a private-constant assertion"
    assert "_MAX_RETRIES" not in code, "private constant name leaked into generated code"
    assert 'hasattr(mod, "TIMEOUT_SEC")' in code, "public constant assertion was dropped"


def test_all_private_constants_emits_no_constants_test_at_all():
    """A module whose constants are all private gets no constants test.

    Not an empty test body with a bare `try/except ImportError` — no test.
    """
    code = test_reflex._generate_test_code(MODULE_INFO, _surface(["_A_LIMIT", "_B_LIMIT"]))

    assert FORBIDDEN not in code
    assert "# --- Constants ---" not in code
    assert "def test_thing_constants(" not in code


def test_private_constants_do_not_consume_the_assertion_cap():
    """Filtering happens BEFORE the cap, so public constants keep their budget.

    This is the ordering bug worth pinning: slice-then-filter would emit a
    constants test containing zero assertions whenever a module declares more
    than _MAX_CONSTANTS_ASSERTED private constants ahead of its public ones.
    """
    cap = test_reflex._MAX_CONSTANTS_ASSERTED
    privates = [f"_PRIVATE_{i}" for i in range(cap + 5)]
    code = test_reflex._generate_test_code(MODULE_INFO, _surface(privates + ["PUBLIC_ONE", "PUBLIC_TWO"]))

    assert FORBIDDEN not in code
    assert 'hasattr(mod, "PUBLIC_ONE")' in code
    assert 'hasattr(mod, "PUBLIC_TWO")' in code


def test_dunder_constants_count_as_public():
    """__all__ / __version__ are public API despite the underscores."""
    assert test_reflex._is_public_name("__version__") is True
    assert test_reflex._is_public_name("PUBLIC") is True
    assert test_reflex._is_public_name("_private") is False
    assert test_reflex._is_public_name("_MAX_X") is False


# --- The rule, on a FRESH generation from a real module ----------------------


def test_fresh_generation_from_a_real_module_has_no_private_assertions():
    """End-to-end: real extractor -> real generator -> no private assertions.

    tools/genesis/reflexes/test.py is the fixture because it declares 14 private
    UPPER_CASE constants and exactly one public one (IMPLEMENTATION_STATUS). Note
    the extractor is called the way the reflex calls it — include_private=False —
    which does NOT filter constants (it filters functions and classes only), so
    every private constant really is present in the surface being generated from.
    """
    target = REPO_ROOT / "tools" / "genesis" / "reflexes" / "test.py"
    surface = extract_api_surface(str(target), include_private=False)

    const_names = [c["name"] for c in surface["constants"]]
    assert any(n.startswith("_") for n in const_names), (
        "fixture no longer has private constants — the test proves nothing; pick another module"
    )

    code = test_reflex._generate_test_code({"module": "genesis/reflexes/test.py", "name": "test_reflex"}, surface)

    assert FORBIDDEN not in code
    for name in const_names:
        if name.startswith("_"):
            assert f'"{name}"' not in code, f"private constant {name} asserted in generated code"
    assert 'hasattr(mod, "IMPLEMENTATION_STATUS")' in code, "public constant assertion was dropped"


def test_fresh_generation_is_syntactically_valid():
    """Guard against the filter breaking code emission (e.g. an empty try body)."""
    for constants in (["_ONLY_PRIVATE"], ["PUBLIC_A", "_PRIV"], []):
        code = test_reflex._generate_test_code(MODULE_INFO, _surface(constants))
        ast.parse(code)  # raises SyntaxError on a malformed emission


@pytest.mark.parametrize(
    "module_rel",
    ["tools/genesis/reflexes/test.py", "tools/dashboard/app.py", "tools/db/storage.py"],
)
def test_fresh_generation_across_several_real_modules(module_rel):
    """The rule holds for whatever the reflex happens to pick up."""
    target = REPO_ROOT / module_rel
    if not target.exists():
        pytest.skip(f"module not present: {module_rel}")

    surface = extract_api_surface(str(target), include_private=False)
    if "error" in surface:
        pytest.skip(f"extractor error: {surface['error']}")

    code = test_reflex._generate_test_code(
        {"module": module_rel.replace("tools/", "", 1), "name": target.stem}, surface
    )
    assert FORBIDDEN not in code, f"private-constant assertion generated for {module_rel}"


# --- The rule is documented, and the mirror stays in sync -------------------


def _flat(text: str) -> str:
    """Collapse whitespace so a wrapped sentence still matches."""
    return " ".join(text.split())


def test_policy_is_documented_where_a_regenerator_will_see_it():
    """The rule must be findable before someone regenerates these files."""
    rule = "never the existence of a private name"

    readme = REPO_ROOT / "tests" / "genesis_auto" / "README.md"
    assert readme.exists(), "tests/genesis_auto/README.md is where the policy lives"
    body = _flat(readme.read_text(encoding="utf-8"))
    assert rule in body
    assert "exa-refine-04" in body, "the kept-and-repaired example must be cited"

    # The docstring of the package a regenerator opens first.
    init_doc = _flat((REPO_ROOT / "tests" / "genesis_auto" / "__init__.py").read_text(encoding="utf-8"))
    assert rule in init_doc

    # The manifest shard row for the reflex that writes these files.
    shard = REPO_ROOT / "tools" / "manifest" / "genesis-v2-0-autonomous-research-lab.md"
    assert rule in _flat(shard.read_text(encoding="utf-8"))

    # And the generator's own docstring, for someone reading the code.
    assert rule.upper() in _flat(test_reflex.__doc__).upper()


def test_icdev_mirror_carries_the_same_filter():
    """A fix only in tools/ leaves the packaged generator free to re-add them."""
    root = (REPO_ROOT / "tools" / "genesis" / "reflexes" / "test.py").read_text(encoding="utf-8")
    mirror_path = REPO_ROOT / "icdev" / "tools" / "genesis" / "reflexes" / "test.py"
    assert mirror_path.exists(), "icdev/ mirror of the test reflex is missing"
    mirror = mirror_path.read_text(encoding="utf-8")

    for marker in ("def _is_public_name(", "pub_constants = [c for c in constants if _is_public_name("):
        assert marker in root, f"root generator lost: {marker}"
        assert marker in mirror, f"icdev/ mirror lost: {marker}"
    assert "for const in constants[:" not in mirror, "icdev/ mirror still slices the unfiltered list"
