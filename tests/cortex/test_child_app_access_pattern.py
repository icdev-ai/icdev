# CUI // SP-CTI
"""Cortex is reached over REST, never inherited (ctx-reach-04).

docs/features/cortex-child-app-access-pattern.md states an architecture that
until now lived only in the ABSENCE of code: `tools/cortex` appears nowhere in
the child-app generator, so no generated descendant inherits Cortex. An absence
is exactly the kind of invariant that gets undone by a well-meaning one-line
addition, with nothing going red — so assert it.

Also pins the cross-references the doc depends on. A doc nobody can find from
the code is the state ctx-reach-04 existed to fix; if the pointer is deleted the
doc quietly reverts to being unreachable.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

DOC = REPO_ROOT / "docs" / "features" / "cortex-child-app-access-pattern.md"
GENERATOR = REPO_ROOT / "tools" / "builder" / "child_app_generator.py"
CLIENT = REPO_ROOT / "tools" / "cortex" / "client.py"
CLIENT_MIRROR = REPO_ROOT / "icdev" / "tools" / "cortex" / "client.py"
GENERATOR_DOC = REPO_ROOT / "docs" / "features" / "phase-19-agentic-generation.md"

DOC_REF = "docs/features/cortex-child-app-access-pattern.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_doc_exists():
    assert DOC.is_file(), f"{DOC_REF} is missing — the access pattern is undocumented again"


def test_generator_does_not_mention_cortex():
    """The measurement the doc cites: zero occurrences, case-insensitive.

    DIRECTORY_TREE is an ALLOWLIST, so absence is what excludes Cortex — it
    needs no PARENT_ONLY_DIRS entry. Adding `tools/cortex` to any of the four
    structures would ship a descendant carrying the SHAPE of the TRUST chain
    without the audit tables, RLS predicates or revocable key that make it real.
    """
    assert GENERATOR.is_file(), "child_app_generator.py moved — update this test and the doc"
    assert "cortex" not in _read(GENERATOR).lower(), (
        "tools/builder/child_app_generator.py now mentions cortex. Cortex is a "
        "parent-hosted service reached over REST with an icdev_ctx_ service key, "
        f"never copied into a descendant — see {DOC_REF}."
    )


@pytest.mark.parametrize("path", [CLIENT, CLIENT_MIRROR], ids=["canonical", "icdev_mirror"])
def test_client_points_at_the_doc(path: Path):
    """`tools/cortex/client.py` is where a new consumer starts reading."""
    assert DOC_REF in _read(path), (
        f"{path} lost its pointer to {DOC_REF}; a consumer wiring Cortex will not "
        "find the degradation contract or the vendoring rule."
    )


def test_generator_doc_points_at_the_doc():
    """The generator source is deliberately comment-free on this (see above), so
    its DOC carries the pointer for a reader sitting in that file."""
    assert DOC_REF.rsplit("/", 1)[-1] in _read(GENERATOR_DOC), (
        "phase-19-agentic-generation.md lost its reference to the access-pattern doc"
    )


def test_client_is_stdlib_only():
    """The vendoring contract: ZERO first-party imports.

    The consumers are standalone apps in SEPARATE repos that copy this file
    verbatim. One icdev import turns a vendorable client into a partial fork of
    the platform, which is the decay this whole pattern exists to prevent.
    """
    import ast

    tree = ast.parse(_read(CLIENT))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names
                          if a.name.split(".")[0] in {"tools", "icdev"}]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level or mod.split(".")[0] in {"tools", "icdev"}:
                offenders.append(mod or ".")
    assert not offenders, (
        f"tools/cortex/client.py must stay stdlib-only; found {offenders}. "
        f"See {DOC_REF} for why."
    )


def test_client_never_raises_on_an_unreachable_host():
    """The degradation contract's third row, asserted rather than described.

    None means UNREACHABLE (degrade silently); a 4xx body means Cortex ANSWERED
    with a refusal (surface it). Conflating them is the mistake the doc exists
    to prevent, so the `None` half is pinned here.
    """
    from tools.cortex.client import CortexClient

    # Port 1 refuses immediately; no network egress, no fixture server.
    dead = CortexClient(base_url="http://127.0.0.1:1", api_key="icdev_ctx_x", timeout=2)
    assert dead.ask("anything") is None
    assert dead.search("anything") is None
    assert dead.is_available() is False

    # Unconfigured / disabled are the same contract, short-circuited.
    assert CortexClient(base_url="", api_key="k").ask("q") is None
    assert CortexClient(base_url="http://127.0.0.1:1", api_key="k",
                        enabled=False).ask("q") is None
