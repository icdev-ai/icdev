# CUI // SP-CTI
"""Reflective reranking is adopted for chat_rag, and ONLY chat_rag (trust-self-02).

``rag.reflective_rerank`` was wired by oss-meas-01 and left ``enabled: false``,
so it was reachable, benchmarkable and consumed by nothing. Turning it on is
the adoption; the tests here are about the two things that make turning it on
safe rather than merely done:

1. **Scope.** The cost is one cheap-tier LLM call PER DOCUMENT. A global
   ``enabled: true`` would charge every drafting, compliance and ingest-side
   retrieval for a behaviour only the chat surface asked for, so ``surfaces``
   scopes it to the ``args/trust_gate.yaml`` profile that adopted it — and a
   caller that names no surface does not silently inherit the bill.
2. **Adoption is not reachability.** ``toggle_harness`` reported ``WIRED``
   throughout the period the toggle did nothing. Its new ``adoption`` field is
   what actually moves when a toggle is switched on, and it is read from the
   COMMITTED config so a benchmark arm cannot report itself as adopted.

No DB, no LLM, no network: the config is written to ``tmp_path`` and the
reflection pass is asserted through the gate helper, not by retrieving.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from tools.rag.retriever import RAGRetriever, reset_rag_config_cache
from tools.rag import toggle_harness as th

REPO_ROOT = Path(__file__).resolve().parents[2]


def _retriever(**reflective) -> RAGRetriever:
    """A retriever whose reflective_rerank block is exactly *reflective*."""
    return RAGRetriever(config={"rag": {"reflective_rerank": reflective}})


# ── surface scoping ─────────────────────────────────────────────────────────


def test_disabled_toggle_never_fires():
    r = _retriever(enabled=False, surfaces=["chat_rag"])
    assert r._reflective_enabled_for("chat_rag") is False
    assert r._reflective_enabled_for(None) is False


def test_enabled_fires_for_a_declared_surface():
    r = _retriever(enabled=True, surfaces=["chat_rag"])
    assert r._reflective_enabled_for("chat_rag") is True


@pytest.mark.parametrize("surface", ["drafting", "compliance_evidence", "agent_output"])
def test_other_trust_gate_surfaces_keep_the_previous_path(surface):
    """The three surfaces that did NOT adopt it must be byte-for-byte unchanged."""
    r = _retriever(enabled=True, surfaces=["chat_rag"])
    assert r._reflective_enabled_for(surface) is False


def test_an_unattributed_caller_does_not_inherit_the_spend():
    """surface=None is not "all surfaces" — it is "nobody said", which pays nothing.

    Defaulting the unattributed caller in is how a scoped toggle quietly becomes
    a global one again: every retrieval site that was never updated would start
    making per-document LLM calls.
    """
    r = _retriever(enabled=True, surfaces=["chat_rag"])
    assert r._reflective_enabled_for(None) is False


def test_empty_surfaces_means_every_surface():
    """Pre-scoping semantics survive: enabled with no scoping is still global."""
    for cfg in ({"enabled": True}, {"enabled": True, "surfaces": []}):
        r = RAGRetriever(config={"rag": {"reflective_rerank": cfg}})
        assert r._reflective_enabled_for("anything") is True
        assert r._reflective_enabled_for(None) is True


def test_search_accepts_a_surface_kwarg():
    """The chat_rag call sites pass surface= through run_rag_search kwargs.

    ``retriever_common.run_rag_search`` forwards search kwargs verbatim, so an
    unsupported name would raise TypeError at every Cortex rag search.
    """
    import inspect

    assert "surface" in inspect.signature(RAGRetriever.search).parameters


# ── committed configuration ─────────────────────────────────────────────────


def test_committed_config_adopts_chat_rag_only():
    """The shipped args/rag_config.yaml is what actually switches this on."""
    block = (th.load_committed_config().get("rag") or {}).get("reflective_rerank") or {}
    assert block.get("enabled") is True
    assert block.get("surfaces") == ["chat_rag"]


def test_committed_max_candidates_is_bounded_by_final_top_k():
    """Reflection runs after the final_top_k truncation, so a larger bound lies.

    max_candidates above final_top_k can never be reached from step 5b, and
    reading "20" invites a reviewer to price the feature at 20 calls a query.
    """
    cfg = th.load_committed_config().get("rag") or {}
    final_top_k = (cfg.get("retrieval") or {}).get("final_top_k", 5)
    assert (cfg.get("reflective_rerank") or {}).get("max_candidates") <= final_top_k


# ── adoption is a separate axis from reachability ───────────────────────────


def test_adoption_moves_off_unadopted():
    """The card's acceptance check: the harness no longer calls this unadopted."""
    probe = th.probe_reachability("reflective_rerank")
    assert probe.adoption == "ADOPTED"
    assert probe.surfaces == ["chat_rag"]
    # ...without disturbing the orthogonal reachability answer.
    assert probe.verdict == "WIRED"


def test_a_toggle_left_off_is_reported_unadopted():
    """WIRED + UNADOPTED is the state this card existed to leave behind.

    ``rerank`` and ``raptor`` are both reachable and both off, and before the
    adoption field there was nothing in the harness that said so.
    """
    for name in ("rerank", "raptor"):
        probe = th.probe_reachability(name)
        assert probe.verdict == "WIRED"
        assert probe.adoption == "UNADOPTED"


def test_adoption_ignores_the_isolated_benchmark_arm(tmp_path, monkeypatch):
    """An isolated_config arm writes every toggle explicitly — that is not adoption.

    Reading adoption through ``$ICDEV_RAG_CONFIG`` would let a sweep that turns
    a toggle on for one arm report it as shipped-on.
    """
    fake = tmp_path / "rag_config.yaml"
    fake.write_text(
        yaml.safe_dump({"rag": {"raptor": {"enabled": True},
                                "reflective_rerank": {"enabled": False}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(th.CONFIG_ENV_VAR, str(fake))
    reset_rag_config_cache()
    try:
        assert th.probe_adoption("raptor")["adoption"] == "UNADOPTED"
        assert th.probe_adoption("reflective_rerank")["adoption"] == "ADOPTED"
    finally:
        reset_rag_config_cache()


def test_probe_dict_carries_adoption():
    payload = th.probe_reachability("reflective_rerank").to_dict()
    assert payload["adoption"] == "ADOPTED"
    assert payload["committed_enabled"] is True
    assert payload["surfaces"] == ["chat_rag"]


# ── the adopting call sites keep naming their surface ───────────────────────
#
# Scoping only works if the three chat_rag call sites actually say so. Dropping
# `surface=` from one of them does not fail anything at runtime — it silently
# opts that surface back out of a feature the config says is on, which is the
# same "declared but not consumed" shape one level further out. These read the
# source rather than the running app: the retrieval sites live inside route
# closures and behind a Flask app singleton, and the invariant being gated is
# syntactic anyway.


def _search_call_surfaces(rel_path: str, func_name: str, attr: bool) -> list:
    """Every `surface=` argument passed to *func_name* in a source file."""
    tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if attr:
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == func_name):
                continue
        elif not (isinstance(node.func, ast.Name) and node.func.id == func_name):
            continue
        kwargs = {kw.arg for kw in node.keywords}
        surface = next(
            (kw.value.value for kw in node.keywords
             if kw.arg == "surface" and isinstance(kw.value, ast.Constant)),
            None,
        )
        found.append((surface, kwargs))
    return found


def test_dashboard_rag_searches_declare_their_surface():
    """Both `tools/dashboard/app.py` retrieval sites are chat_rag."""
    calls = [
        (s, k) for s, k in _search_call_surfaces("tools/dashboard/app.py", "search", attr=True)
        if "top_k" in k                       # the RAGRetriever searches, not re.search
    ]
    assert calls, "no RAGRetriever.search() call found in tools/dashboard/app.py"
    assert all(s == "chat_rag" for s, _ in calls), calls


def test_cortex_rag_adapter_declares_its_surface():
    """`tools/cortex/search_service.py::search_rag` serves Cortex ask/complete."""
    calls = _search_call_surfaces("tools/cortex/search_service.py", "run_rag_search", attr=False)
    assert calls, "no run_rag_search() call found in tools/cortex/search_service.py"
    assert all(s == "chat_rag" for s, _ in calls), calls


# ── the retrieval log can actually record the new mode ──────────────────────
#
# `_log_retrieval`'s INSERT is best-effort inside a try/except, so a retrieval
# mode outside the CHECK constraint does not raise — it drops the row. That is
# how `reflective_reranked` came to be written by oss-meas-01 while no DDL in
# the tree allowed it: every reflectively reranked retrieval went unlogged, on
# the exact telemetry a reviewer would consult to ask whether the feature ran.


def _check_values(text: str, marker: str) -> set:
    """The quoted values of the first retrieval_mode CHECK list after *marker*.

    Paren-balanced from the CHECK keyword, because the three sources spell the
    same constraint three ways: ``CHECK(x IN (...))``,
    ``CHECK ((x = ANY (ARRAY[...])))`` and ``CHECK (x IN (...))``.
    """
    tail = text[text.index(marker):]
    start = tail.index("(", tail.index("CHECK", tail.index("retrieval_mode")))
    depth, end = 0, start
    for i, ch in enumerate(tail[start:], start):
        depth += (ch == "(") - (ch == ")")
        if depth == 0:
            end = i
            break
    return set(re.findall(r"'([a-z0-9_]+)'", tail[start:end]))


def test_every_mode_the_retriever_writes_is_allowed_by_the_constraint():
    from tools.rag.retriever import RETRIEVAL_MODES

    source = (REPO_ROOT / "tools" / "rag" / "retriever.py").read_text(encoding="utf-8")
    written = set(re.findall(r'retrieval_mode = "([a-z_]+)"', source))
    assert written, "no retrieval_mode assignment found — did the pattern change?"
    assert written <= set(RETRIEVAL_MODES), sorted(written - set(RETRIEVAL_MODES))


@pytest.mark.parametrize(
    "rel_path,marker",
    [
        ("tools/db/init_icdev_db.py", "CREATE TABLE IF NOT EXISTS rag_retrieval_log"),
        ("tools/db/schema/pg_consolidated.sql", "rag_retrieval_log_retrieval_mode_check"),
        ("tools/db/migrations/20260815002727_rag_retrieval_log_reflective_modes/up.sql",
         "ADD CONSTRAINT"),
    ],
)
def test_ddl_sources_match_the_python_constant(rel_path, marker):
    from tools.rag.retriever import RETRIEVAL_MODES

    text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert _check_values(text, marker) == set(RETRIEVAL_MODES), rel_path
