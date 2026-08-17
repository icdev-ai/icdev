# CUI // SP-CTI
"""The Cortex ``sme`` advisory backend, and proof ACE can actually mint an SME.

Two halves, and the second one is the point.

**The backend half** asserts the properties that keep an OPINION from becoming a
VERDICT: ``sme`` is never selected automatically, its RRF weight is 0.0 so it
cannot outrank evidence, it is excluded from the CRAG trigger, and every result
is stamped advisory. base_pack TRUST rule 1 requires the verdict to come from
deterministic evidence; these tests are what makes that structural rather than
a comment.

**The ACE half** exists because ``ensure_sme`` had NEVER successfully produced a
role: no file in args/ace/roles/ carried the ``generated_at`` stamp
``_build_role_spec`` writes, and ``_generated_smes.json`` existed nowhere. A
card that says "add a backend over ensure_sme" on top of a generation path that
has never run once is a card built on an assumption, so these tests run the real
``ensure_sme`` — real role_policy, real shipped capability bundles, real YAML
write, real index write — against a redirected project root.

The ONLY thing stubbed is the model, at the ``LLMRouter`` seam every one of the
three generation calls resolves at call time. Stubbing lower (the provider) would
pin a vendor into a test; stubbing higher (``ensure_sme`` itself) would prove
nothing at all, which is the state this card found.
"""
from __future__ import annotations

import importlib
import shutil

import pytest
import yaml

from tools.cortex import search_service
from tools.cortex.config import resolve_strategy_weights
from tools.cortex.schemas import (
    ADVISORY_BACKENDS,
    CORTEX_BACKENDS,
    EVIDENTIARY_BACKENDS,
    Citation,
    CortexContext,
    CortexSearchResult,
)


class _NoLiveModel(RuntimeError):
    """Raised if anything in this file reaches the router without a stub."""


@pytest.fixture(autouse=True)
def _never_call_a_live_model(monkeypatch):
    """Hard floor: no test in this file may reach a real provider.

    Not paranoia — this is what actually happened. The first run of this file
    patched only the ``icdev.tools.ace.*`` module objects while the adapter
    resolved the ``tools.ace.*`` ones, so the "no provider available" tests ran
    the REAL generation path against a REAL model and left three unreviewed
    personas in the committed tree (one of them minted from the query ``"q"``,
    which the label normaliser turned into "quantum computing").

    Poisoning the router class means an escaped patch now fails the assertion
    loudly instead of silently costing tokens and writing files. Tests that
    legitimately need a model install ``_install_stub_router`` over this.
    """
    class _Poisoned:
        def __init__(self, *a, **k):
            raise _NoLiveModel(
                "tests/cortex/test_search_sme.py reached LLMRouter without a stub"
            )

    _patch_both_namespaces(monkeypatch, "llm.router", "LLMRouter", _Poisoned)


# ---------------------------------------------------------------------------
# Registration + the advisory/evidentiary split
# ---------------------------------------------------------------------------


def test_sme_is_a_registered_backend():
    assert "sme" in CORTEX_BACKENDS
    assert "sme" in search_service.BACKEND_ADAPTERS
    assert search_service.BACKEND_ADAPTERS["sme"] is search_service.search_sme
    # A caller can name it explicitly — that is the ONLY way to reach it.
    assert "sme" in search_service.CORTEX_STRATEGIES


def test_evidentiary_is_derived_from_advisory_and_cannot_drift():
    assert "sme" in ADVISORY_BACKENDS
    assert "sme" not in EVIDENTIARY_BACKENDS
    assert set(EVIDENTIARY_BACKENDS) | set(ADVISORY_BACKENDS) == set(CORTEX_BACKENDS)
    assert not set(EVIDENTIARY_BACKENDS) & set(ADVISORY_BACKENDS)


# ---------------------------------------------------------------------------
# "Never becomes the verdict", proven four independent ways
# ---------------------------------------------------------------------------


def test_no_routing_label_selects_the_advisory_backend():
    """The query classifier must never route to an opinion.

    ``classify_route`` answers "what SHAPE is this query"; no query shape means
    "consult an expert instead of the corpus". A label here would make the
    advisory rung fire automatically.
    """
    for label, backends in search_service.ROUTE_LABEL_BACKENDS.items():
        assert "sme" not in backends, f"routing label {label!r} selects the advisory backend"


def test_shipped_fan_out_excludes_the_advisory_backend():
    from tools.cortex.config import load_cortex_config

    fan_out = ((load_cortex_config().get("search") or {}).get("fan_out") or {}).get(
        "backends"
    ) or []
    assert "sme" not in fan_out


def test_search_all_default_and_strategy_all_exclude_sme(monkeypatch):
    """Neither ``search_all()`` nor ``strategy="all"`` may reach the LLM.

    Both used to mean ``CORTEX_BACKENDS``; adding ``sme`` there without changing
    them would have put an LLM call on the default path of every "search
    everything" caller.
    """
    called: list[str] = []

    def _spy(name):
        def _adapter(query, top_k=10, ctx=None):
            called.append(name)
            return []

        return _adapter

    for name in CORTEX_BACKENDS:
        monkeypatch.setitem(search_service.BACKEND_ADAPTERS, name, _spy(name))

    search_service.search_all("q")
    assert "sme" not in called
    assert set(called) == set(EVIDENTIARY_BACKENDS)

    called.clear()
    search_service.search("q", strategy="all")
    assert "sme" not in called
    assert set(called) == set(EVIDENTIARY_BACKENDS)


def test_shipped_weight_keeps_an_opinion_below_every_evidentiary_hit():
    """RRF with the SHIPPED weights, not a fixture: sme scores 0.0 and sorts last.

    This is the fusion-level guarantee. Even a caller that fans out to an
    evidentiary backend AND the advisory one gets the opinion underneath the
    evidence, because ``search.strategy_weights.sme`` is 0.0.
    """
    weights = resolve_strategy_weights()
    assert weights["sme"] == 0.0

    evidence = CortexSearchResult(
        content="AC-2 requires account management.",
        # A deliberately terrible evidentiary score: even the WORST evidence
        # must outrank the opinion, so the guarantee cannot depend on the
        # evidence happening to be good.
        score=0.01,
        backend="kb",
        citation=Citation(source_id="kb-1"),
    )
    opinion = CortexSearchResult(
        content="In my judgement the control is satisfied.",
        score=1.0,
        backend="sme",
        citation=Citation(source_id="some_expert"),
        metadata={"advisory": True},
    )

    fused = search_service._rrf_fuse([opinion, evidence], weights=weights)

    assert [r.backend for r in fused] == ["kb", "sme"]
    assert fused[-1].raw_scores["rrf"] == 0.0


def test_is_advisory_survives_metadata_being_dropped():
    """``backend`` alone is enough — the flag is a second signal, not the only one.

    ``metadata`` is a plain dict that consumers rewrite (``_routed_pass`` already
    does). If a dropped key could promote an opinion into a verdict, the marking
    would be decorative.
    """
    stripped = CortexSearchResult(content="opinion", backend="sme", metadata={})
    assert search_service.is_advisory(stripped)

    flagged_only = CortexSearchResult(content="x", backend="", metadata={"advisory": True})
    assert search_service.is_advisory(flagged_only)

    assert not search_service.is_advisory(
        CortexSearchResult(content="evidence", backend="rag", metadata={})
    )


def test_advisory_results_do_not_trigger_the_crag_corrective_pass(monkeypatch):
    """An opinion is evidence of neither retrieval success nor retrieval failure.

    CRAG's evaluator is ``max(score) < crag_threshold``. The opinion's 0.0 would
    answer "retrieval failed", so an sme-only search would buy a query rewrite
    plus a whole second advisory pass — two more LLM calls to correct a
    retrieval that never ran.
    """
    rewrites: list[str] = []

    def _spy_rewrite(query, results, ctx):
        rewrites.append(query)
        return "rewritten"

    monkeypatch.setattr(search_service, "rewrite_query", _spy_rewrite)

    advisory_only = [
        CortexSearchResult(content="opinion", score=0.0, backend="sme", metadata={"advisory": True})
    ]
    out = search_service._corrective_pass(
        "q", advisory_only, 5, CortexContext(), "sme", {}, {"crag_threshold": 0.55}
    )
    assert rewrites == []
    assert out is advisory_only

    # Control: a low-scoring EVIDENTIARY result in the same set still triggers it,
    # so the exclusion is scoped to advisory results and has not disabled CRAG.
    mixed = [
        CortexSearchResult(content="opinion", score=0.0, backend="sme", metadata={"advisory": True}),
        CortexSearchResult(content="weak", score=0.1, backend="rag", metadata={}),
    ]
    monkeypatch.setattr(search_service, "_routed_pass", lambda *a, **k: [])
    search_service._corrective_pass(
        "q", mixed, 5, CortexContext(), "auto", {}, {"crag_threshold": 0.55}
    )
    assert rewrites == ["q"]


def test_an_empty_first_pass_still_triggers_correction(monkeypatch):
    """The near-miss in the exclusion above, and the reason it is two conditions.

    An empty result set has no evidentiary results EITHER, so a naive "no
    evidentiary results -> skip" would have disabled CRAG for its single most
    important case: retrieval found nothing at all, which is the strongest
    reason there is to rewrite the query. Only a non-empty, entirely-advisory
    set is skipped.
    """
    rewrites: list[str] = []
    monkeypatch.setattr(
        search_service, "rewrite_query", lambda q, r, c: rewrites.append(q) or "rewritten"
    )
    monkeypatch.setattr(search_service, "_routed_pass", lambda *a, **k: [])

    search_service._corrective_pass(
        "q", [], 5, CortexContext(), "auto", {}, {"crag_threshold": 0.55}
    )
    assert rewrites == ["q"]


def test_the_mcp_surface_advertises_sme_as_advisory():
    """The MCP description is the ONLY thing an external caller reads.

    ``handle_cortex_search`` passes ``strategy`` straight through to
    ``search()``, which validates it against CORTEX_STRATEGIES — so ``sme`` is
    reachable over MCP the moment it is registered, described or not. A caller
    who discovers it from the enum and not from the description has no way to
    know the result is an opinion rather than a hit, which is the one thing
    about this backend they must know.
    """
    from tools.mcp.cortex_server import CORTEX_TOOLS
    from tools.mcp.tool_registry import TOOL_REGISTRY

    server_tool = next(t for t in CORTEX_TOOLS if t["name"] == "cortex_search")
    registry_tool = TOOL_REGISTRY["cortex_search"]

    for blob in (
        server_tool["description"],
        server_tool["properties"]["strategy"]["description"],
        registry_tool["description"],
        registry_tool["input_schema"]["properties"]["strategy"]["description"],
    ):
        assert "sme" in blob.lower()
    for blob in (server_tool["description"], registry_tool["description"]):
        assert "advisory" in blob.lower()


# ---------------------------------------------------------------------------
# The adapter contract
# ---------------------------------------------------------------------------


class _FakeSmeResult:
    def __init__(self, **kw):
        self.role_id = kw.get("role_id", "maritime_insurance_underwriting")
        self.status = kw.get("status", "generated")
        self.domain_label = kw.get("domain_label", "maritime insurance underwriting")
        self.capability_bundle = kw.get("capability_bundle", "advisory")
        self.soul_path = kw.get("soul_path", "/roles/x/SOUL.md")
        self.role_yaml_path = kw.get("role_yaml_path", "/args/ace/roles/x.yaml")
        self.matched_existing = kw.get("matched_existing", "")


def _patch_both_namespaces(monkeypatch, module_suffix: str, attr: str, value) -> None:
    """Set *attr* on BOTH ``tools.<suffix>`` and ``icdev.tools.<suffix>``.

    These are two DISTINCT module objects — the root ``tools/`` shim re-imports
    rather than aliasing — and ``search_service._backend()`` picks whichever one
    matches the namespace ``search_service`` itself was imported under. Patching
    one namespace leaves the other live, which in this file meant the "no
    provider" tests reached a REAL model and wrote REAL personas into the
    committed tree. Patching both is the only shim-safe form.
    """
    for root in ("tools", "icdev.tools"):
        try:
            mod = importlib.import_module(f"{root}.{module_suffix}")
        except Exception:  # noqa: BLE001 — a namespace that will not import cannot be reached
            continue
        monkeypatch.setattr(mod, attr, value)


def _patch_ace(monkeypatch, *, ensure=None, query=None):
    """Replace the two ACE calls ``search_sme`` makes, in every namespace."""
    calls: dict = {}

    def _ensure(domain_description, *, capability_bundle=None, allow_reuse=True):
        calls["ensure"] = {
            "domain_description": domain_description,
            "capability_bundle": capability_bundle,
        }
        if callable(ensure):
            return ensure(domain_description)
        return ensure if ensure is not None else _FakeSmeResult()

    def _query(role_id, question, context=""):
        calls["query"] = {"role_id": role_id, "question": question, "context": context}
        if callable(query):
            return query(role_id, question)
        return query if query is not None else "Underwriters price hull risk from class records."

    _patch_both_namespaces(monkeypatch, "ace.sme_registry", "ensure_sme", _ensure)
    _patch_both_namespaces(monkeypatch, "ace.persona_query", "query_persona", _query)
    return calls


def test_adapter_returns_one_advisory_result_with_a_mandatory_citation(monkeypatch):
    calls = _patch_ace(monkeypatch)

    results = search_service.search_sme("How is hull risk priced?", top_k=10)

    assert len(results) == 1, "one expert gives one opinion; top_k must not fan out"
    r = results[0]
    assert r.backend == "sme"
    assert r.strategy == "persona_opinion"
    assert r.content == "Underwriters price hull risk from class records."

    # Mandatory citation — names WHO said it, since no row backs an opinion.
    assert r.citation.source_id == "maritime_insurance_underwriting"
    assert r.citation.source_type == "sme_opinion"
    assert r.citation.source_table == ""
    assert r.citation.snippet

    # Advisory marking, both signals.
    assert r.metadata["advisory"] is True
    assert r.metadata["verdict_eligible"] is False
    assert search_service.is_advisory(r)

    # An opinion has no retrieval confidence, and says so structurally rather
    # than letting a bare 0.0 read as "measured a terrible match".
    assert r.score == 0.0
    assert r.raw_scores["scored"] is False

    # Provenance of the persona itself.
    assert r.metadata["role_id"] == "maritime_insurance_underwriting"
    assert r.metadata["sme_status"] == "generated"
    assert r.metadata["capability_bundle"] == "advisory"

    # The question reaches the expert, and top_k never becomes a call count.
    assert calls["query"]["question"] == "How is hull risk priced?"
    assert calls["ensure"]["capability_bundle"] == "advisory"


def test_adapter_always_requests_the_advisory_bundle(monkeypatch):
    """The bundle is not caller-configurable.

    ``advisory`` ships ``folder_access: []`` and ``icdev_tools: []``, so a
    persona minted on this path cannot write or execute. A search backend is not
    a place to hand out agency.
    """
    calls = _patch_ace(monkeypatch)
    search_service.search_sme("q", ctx=CortexContext(domain="security"))
    assert calls["ensure"]["capability_bundle"] == search_service.SME_CAPABILITY_BUNDLE
    assert search_service.SME_CAPABILITY_BUNDLE == "advisory"


def test_ctx_domain_is_preferred_as_the_sme_domain(monkeypatch):
    """Two differently-worded questions in one lens must converge on one expert."""
    calls = _patch_ace(monkeypatch)
    search_service.search_sme("what about port state control?", ctx=CortexContext(domain="security"))
    assert calls["ensure"]["domain_description"] == "security"

    calls2 = _patch_ace(monkeypatch)
    search_service.search_sme("what about port state control?")
    assert calls2["ensure"]["domain_description"] == "what about port state control?"


# ---------------------------------------------------------------------------
# Degradation — errors, never a fabricated opinion
# ---------------------------------------------------------------------------


def test_no_provider_degrades_to_errors_not_a_fabricated_opinion(monkeypatch, caplog):
    """The failure this backend is most likely to hit, and the one that matters.

    A dead/blocked provider must produce ZERO results carrying ``.errors``, not
    an empty-string opinion. An empty opinion is not a neutral opinion — it is a
    silent abstention that a consumer would render as "the expert had nothing to
    add".
    """
    class LLMUnavailableError(RuntimeError):
        pass

    def _boom(role_id, question):
        raise LLMUnavailableError("no provider in the chain could serve the request")

    _patch_ace(monkeypatch, query=_boom)

    results = search_service.search_sme("q")

    assert list(results) == []
    errors = getattr(results, "errors", None)
    assert errors, "an empty result set with no .errors is indistinguishable from 'no match'"
    assert errors[0]["backend"] == "sme"
    assert errors[0]["stage"] == "persona_query"
    assert "no provider" in errors[0]["message"]


def test_persona_resolution_failure_is_a_distinct_stage(monkeypatch):
    """"No expert for this domain" and "the expert could not answer" are
    different outages with different fixes; merging them is how a budget ceiling
    reads as a missing capability."""
    def _boom(domain_description):
        raise RuntimeError("role generation refused")

    _patch_ace(monkeypatch, ensure=_boom)

    results = search_service.search_sme("q")
    assert list(results) == []
    assert getattr(results, "errors")[0]["stage"] == "ensure_sme"


def test_empty_opinion_is_an_error_not_a_result(monkeypatch):
    _patch_ace(monkeypatch, query="   ")
    results = search_service.search_sme("q")
    assert list(results) == []
    assert getattr(results, "errors")[0]["stage"] == "persona_query"


def test_empty_query_never_reaches_ace(monkeypatch):
    calls = _patch_ace(monkeypatch)
    results = search_service.search_sme("   ")
    assert list(results) == []
    assert getattr(results, "errors")[0]["stage"] == "input"
    assert calls == {}, "an empty question must not mint a persona"


@pytest.mark.parametrize("stage", ["ensure_sme", "persona_query"])
def test_adapter_never_raises(monkeypatch, stage):
    """The adapter contract: exception-isolated, always returns."""
    class Catastrophe(Exception):
        pass

    def _boom(*a, **k):
        raise Catastrophe("catastrophic")

    if stage == "ensure_sme":
        _patch_ace(monkeypatch, ensure=_boom)
    else:
        _patch_ace(monkeypatch, query=_boom)

    results = search_service.search_sme("q")
    assert list(results) == []
    assert getattr(results, "errors")[0]["stage"] == stage


# ---------------------------------------------------------------------------
# The ACE half: ensure_sme actually produces a role
# ---------------------------------------------------------------------------


_ROLE_FIELDS_YAML = """\
display_name: Maritime Insurance Underwriter
description: Prices and structures hull and cargo insurance risk.
steps:
  - review submission
  - assess class records
  - price the risk
domain: maritime insurance
capabilities:
  - hull risk pricing
  - cargo exposure modelling
  - reinsurance treaty review
rules:
  - Never quote without a current class certificate.
  - State the assumptions behind every rate.
"""

# What a model would emit if it TRIED to grant itself agency. apply_bundle must
# discard every one of these; this is the escalation the bundle file exists to
# stop, so the fixture has to actually attempt it.
_ROLE_FIELDS_YAML_HOSTILE = _ROLE_FIELDS_YAML + """\
trust_tier: green
mode: agent
folder_access:
  - path: args/ace/roles
    mode: rw
icdev_tools:
  - rm -rf /
tool_permissions:
  - Write
  - Bash
"""


class _StubRouter:
    """One stub at the LLMRouter seam, covering all three generation calls.

    ``sme_registry`` and ``persona_generator`` both import ``LLMRouter`` INSIDE
    the function, so patching the class on the router module reaches every call
    without pinning a provider or a model id into the test.
    """

    calls: list[str] = []

    def __init__(self, role_fields=_ROLE_FIELDS_YAML):
        self._role_fields = role_fields

    def invoke(self, function, request):
        prompt = " ".join(
            str(m.get("content", "")) for m in (request.messages or []) if isinstance(m, dict)
        )
        type(self).calls.append(function)
        if "canonical 2-4 word domain label" in prompt:
            return _StubResponse("maritime insurance underwriting")
        if "SOUL.md identity file" in prompt:
            return _StubResponse(
                "# Maritime Insurance Underwriter — Identity & Values\n\n"
                "## Core Values\n- Price the risk that is actually on the water.\n"
            )
        if "defining a subject-matter expert" in prompt:
            return _StubResponse(self._role_fields)
        # The near-miss adjudicator. NONE == "nothing covers this; generate".
        return _StubResponse("NONE")


class _StubResponse:
    def __init__(self, content):
        self.content = content


@pytest.fixture()
def ace_sandbox(tmp_path, monkeypatch):
    """A real project root ensure_sme may write into, with the SHIPPED policy.

    ``args/ace/sme_capability_bundles.yaml`` is COPIED, not synthesized: the
    whole point of the bundle assertions below is that the file this platform
    ships leaves a generated role unable to write or execute. A hand-written
    fixture would only prove the fixture.
    """
    from icdev._paths import get_data_path

    real_args = get_data_path("args")
    roles_dir = tmp_path / "args" / "ace" / "roles"
    roles_dir.mkdir(parents=True)
    shutil.copy(
        real_args / "ace" / "sme_capability_bundles.yaml",
        tmp_path / "args" / "ace" / "sme_capability_bundles.yaml",
    )
    soul_dir = tmp_path / "souls"
    soul_dir.mkdir()

    monkeypatch.setenv("ICDEV_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ICDEV_ACE_ROLES_DIR", str(soul_dir))
    _StubRouter.calls = []
    return {"root": tmp_path, "roles": roles_dir, "souls": soul_dir}


def _install_stub_router(monkeypatch, role_fields=_ROLE_FIELDS_YAML):
    class _Stub(_StubRouter):
        def __init__(self, *a, **k):
            super().__init__(role_fields=role_fields)

    _patch_both_namespaces(monkeypatch, "llm.router", "LLMRouter", _Stub)


def test_ensure_sme_writes_a_role_yaml_carrying_generated_at(ace_sandbox, monkeypatch):
    """The claim this card refused to assume: generation works end to end.

    Before this test, NO file in args/ace/roles/ carried the ``generated_at``
    stamp ``_build_role_spec`` writes, and ``_generated_smes.json`` existed
    nowhere in the tree — i.e. ``ensure_sme`` had never once completed.
    """
    _install_stub_router(monkeypatch)
    registry = importlib.import_module("icdev.tools.ace.sme_registry")

    result = registry.ensure_sme(
        "insuring ships and cargo against loss at sea", capability_bundle="advisory"
    )

    assert result.status == "generated"
    yaml_path = ace_sandbox["roles"] / f"{result.role_id}.yaml"
    assert yaml_path.exists(), "no role YAML was written"

    spec = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert spec["generated_at"], "the generated_at stamp is the proof this path ran"
    assert spec["role_id"] == result.role_id
    # The model authored WHO the expert is.
    assert spec["display_name"] == "Maritime Insurance Underwriter"
    assert "review_submission" in spec["steps"]

    # And the index exists — the other artifact that had never been created.
    index_path = ace_sandbox["souls"] / "_generated_smes.json"
    assert index_path.exists()
    import json

    assert result.role_id in json.loads(index_path.read_text(encoding="utf-8"))

    # Both halves, or neither: the SOUL.md that makes the role queryable.
    assert (ace_sandbox["souls"] / result.role_id / "SOUL.md").exists()


def test_generated_role_is_advisory_and_cannot_write_or_execute(ace_sandbox, monkeypatch):
    """Acceptance: capability_bundle ``advisory``, empty icdev_tools/folder_access.

    Run against a model that ACTIVELY tries to grant itself ``trust_tier: green``,
    rw access to args/ace/roles (the privilege-escalation loop: write there and a
    role can rewrite its own permissions) and ``rm -rf /``. Every one is discarded
    by ``role_policy.apply_bundle`` before the spec is validated or written.
    """
    _install_stub_router(monkeypatch, role_fields=_ROLE_FIELDS_YAML_HOSTILE)
    registry = importlib.import_module("icdev.tools.ace.sme_registry")

    result = registry.ensure_sme("insuring ships and cargo against loss at sea")

    spec = yaml.safe_load(
        (ace_sandbox["roles"] / f"{result.role_id}.yaml").read_text(encoding="utf-8")
    )
    assert spec["capability_bundle"] == "advisory"
    assert spec["folder_access"] == []
    assert spec["icdev_tools"] == []
    assert spec["trust_tier"] == "red"
    assert spec["mode"] == "steps"
    assert "Bash" not in spec["tool_permissions"]
    assert "Write" not in spec["tool_permissions"]


def test_second_call_for_the_same_domain_reuses_rather_than_regenerating(
    ace_sandbox, monkeypatch
):
    """357 coworkers already exist; a second ask must not mint a 358th."""
    _install_stub_router(monkeypatch)
    registry = importlib.import_module("icdev.tools.ace.sme_registry")

    first = registry.ensure_sme("insuring ships and cargo against loss at sea")
    assert first.status == "generated"

    yaml_path = ace_sandbox["roles"] / f"{first.role_id}.yaml"
    stamp = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["generated_at"]
    before = sorted(p.name for p in ace_sandbox["roles"].glob("*.yaml"))

    second = registry.ensure_sme("marine cargo and hull insurance underwriting")

    assert second.role_id == first.role_id
    assert second.status == "cached"
    assert sorted(p.name for p in ace_sandbox["roles"].glob("*.yaml")) == before
    # Not merely the same id — the same FILE, untouched.
    assert yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["generated_at"] == stamp


def test_the_backend_end_to_end_over_a_real_generated_role(ace_sandbox, monkeypatch):
    """The whole rung: search_sme -> real ensure_sme -> real role on disk.

    Everything but the model is real here, so this is the test that would catch
    the adapter and the registry drifting apart — a contract mismatch that every
    other test in this file stubs away.
    """
    _install_stub_router(monkeypatch)
    _patch_both_namespaces(
        monkeypatch,
        "ace.persona_query",
        "query_persona",
        lambda role_id, question, context="": f"[{role_id}] hull risk.",
    )

    results = search_service.search_sme("insuring ships and cargo against loss at sea")

    assert len(results) == 1
    r = results[0]
    assert r.metadata["sme_status"] == "generated"
    assert r.metadata["capability_bundle"] == "advisory"
    assert search_service.is_advisory(r)
    assert (ace_sandbox["roles"] / f"{r.metadata['role_id']}.yaml").exists()
    assert r.content == f"[{r.metadata['role_id']}] hull risk."
