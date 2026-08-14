# CUI // SP-CTI
"""Pins the ctx-reach-03 reach decisions for ``cortex.govern`` / ``cortex.agent``.

Both facades were flagged as having zero production consumers, with a latent
bug alongside: ``args/projects.yaml`` recorded "cortex.agent mode graph. Note the
latent bug - mode is unvalidated."

What this module asserts, and why each assertion is the one that would have
caught the thing that actually went wrong:

1. **``mode`` is validated in the facade, not at the entry points.** The bug was
   never "nobody checks the string" — it was that dispatch was a single
   ``use_team = mode == "team" or (mode == "auto" and roles)`` boolean, so a
   typo, or ``"graph"`` before graph mode existed, silently ran a REAL, BILLED
   single agent instead of erroring. Checking membership in a test that only
   reads ``_AGENT_MODES`` would pass against the buggy dispatch too, so these
   tests stub all three backends to ``pytest.fail`` and assert none is entered.

2. **The MCP tool no longer has an ungoverned second implementation.**
   ``handle_cortex_agent_launch`` probed ``getattr(cortex_api, "agent", None)``
   and fell back to ``_agent_launch_fallback``, which reached ACEController /
   run_agent_loop directly — no TRUST chain — and re-derived the exact
   ``use_team`` boolean above. The facade has existed since ctx-govern-04, so the
   probe could never fail, but the code it guarded was still there. It was
   deleted; these tests keep it deleted.

3. **``agent`` is adopted; ``govern`` is external-only.** Each decision is only
   honest if it is checkable, so the adopted paths are asserted to genuinely
   REACH the facade (declared-but-unconsumed is this platform's signature
   defect), and the external-only one is asserted to have exactly the entry
   point its docstring claims.

4. **The `compliance` domain lens resolves.** It was declared in
   ``CORTEX_DOMAIN_LENSES`` with no ``search.domains.compliance`` block, so
   selecting it in the canvas picker changed nothing at all.
"""
from __future__ import annotations

import contextlib
import importlib
import inspect

import pytest

from tools.cortex import api
from tools.cortex import governance as _gov
from tools.cortex import validators as _validators
from tools.cortex.domains import apply_persona, load_domain_profile
from tools.cortex.schemas import CortexContext, CortexResult, GovernanceReport
from tools.mcp import cortex_server

_REST = importlib.import_module("tools.cortex.rest_v1")
_BLUEPRINT = importlib.import_module("tools.cortex.blueprint")


@pytest.fixture(autouse=True)
def _stub_governance_sinks(monkeypatch):
    """Keep the governed facades fully in-memory — no DB audit/provenance write,
    no gateway call, no anonymizer. The subject here is dispatch, not the gates."""
    monkeypatch.setattr(_gov, "_gate_record_audit", lambda payload: None)
    monkeypatch.setattr(
        _gov, "_gate_register_provenance", lambda text, ctx, op, rid: "scr-test"
    )
    monkeypatch.setattr(
        _gov,
        "_gate_check_text",
        lambda text: {"allowed": True, "warnings": [], "blocked_reason": None},
    )
    monkeypatch.setattr(_gov, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(_gov, "_gate_redact_output", lambda text: (text, []))


@pytest.fixture
def _no_backend_may_run(monkeypatch):
    """Every agent backend fails the test if entered.

    This is the fixture that distinguishes "the mode was rejected" from "the mode
    was accepted and something ran". A membership assertion alone cannot.
    """
    monkeypatch.setattr(
        api, "_run_single_agent", lambda *a, **k: pytest.fail("a single agent ran")
    )
    monkeypatch.setattr(
        api, "_get_ace_controller", lambda: pytest.fail("an ACE team launched")
    )
    monkeypatch.setattr(
        api, "_start_graph_run", lambda *a, **k: pytest.fail("a graph run started")
    )


# --------------------------------------------------------------------------- #
# 1. agent(mode=...) rejects anything outside AGENT_MODES
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_mode",
    [
        "yolo",             # a typo
        "graph_mode",       # a near-miss on a real mode
        "teams",            # plural — the shape a caller actually gets wrong
        "workflow",         # the name graph mode nearly got
        "auto ,team",       # not a list surface: two modes is not a mode
    ],
)
def test_agent_rejects_a_mode_outside_the_accepted_set(bad_mode, _no_backend_may_run):
    with pytest.raises(ValueError, match="unknown agent mode"):
        api.agent("fix the bug", mode=bad_mode)


def test_agent_reject_names_the_accepted_set_in_the_error(_no_backend_may_run):
    """The refusal has to be actionable: a caller must learn what IS accepted."""
    with pytest.raises(ValueError) as excinfo:
        api.agent("fix the bug", mode="nonsense")
    message = str(excinfo.value)
    for mode in sorted(api._AGENT_MODES):
        assert mode in message, f"the error does not name the valid mode {mode!r}"


def test_agent_mode_is_normalised_before_the_membership_check(monkeypatch):
    """`" TEAM "` is the same mode as `"team"` — case/whitespace must not refuse."""
    launched = {}

    class _Controller:
        def launch(self, **kwargs):
            launched.update(kwargs)
            return "inst-1"

    monkeypatch.setattr(api, "_get_ace_controller", lambda: _Controller())
    result = api.agent("triage the CVEs", roles=["security"], mode="  TEAM  ")
    assert result.data["mode"] == "team"
    assert launched["problem_text"] == "triage the CVEs"


def test_agent_empty_mode_defaults_to_auto_rather_than_refusing(monkeypatch):
    """`mode=None`/`""` means "the caller did not choose", not "invalid"."""
    monkeypatch.setattr(api, "_get_router", lambda: object())
    monkeypatch.setattr(
        api,
        "_run_single_agent",
        lambda router, **kw: _fake_loop("no roles -> single"),
    )
    for empty in ("", None):
        result = api.agent("summarise the repo", mode=empty)
        assert result.data["mode"] == "single"


def test_the_validator_and_the_facade_agree_on_the_accepted_set():
    """Two copies of the vocabulary exist by design (validators.py stays
    importable without the ACE / agent-loop seams). Two copies that DISAGREE is
    how a mode gets accepted at the door and refused in the room, or worse."""
    assert set(_validators.AGENT_MODES) == set(api._AGENT_MODES)


def test_the_rest_surface_refuses_a_bad_mode_before_it_reaches_the_facade(
    _no_backend_may_run,
):
    with pytest.raises(_validators.CortexValidationError, match="mode"):
        _validators.validate_agent({"goal": "x", "mode": "yolo"})


def test_the_mcp_tool_surfaces_the_facade_refusal_as_a_tool_error(
    _no_backend_may_run,
):
    """cortex_agent_launch passes `mode` through unvalidated ON PURPOSE — the
    facade owns the vocabulary. Prove the refusal still reaches the caller."""
    result = cortex_server.handle_cortex_agent_launch(
        {"goal": "fix the bug", "mode": "yolo"}
    )
    assert "unknown agent mode" in result.get("error", "")


# --------------------------------------------------------------------------- #
# 2. No ungoverned second implementation of "launch an agent"
# --------------------------------------------------------------------------- #
def test_the_ungoverned_agent_launch_fallback_is_gone():
    assert not hasattr(cortex_server, "_agent_launch_fallback"), (
        "_agent_launch_fallback reached ACEController / run_agent_loop directly, "
        "with no TRUST chain and with the pre-hgx `use_team` dispatch that made "
        "an unrecognised mode silently run a single agent"
    )


def test_the_mcp_handlers_call_the_facades_unconditionally():
    """No `getattr(cortex_api, "agent"/"govern", None)` probe with a fallback
    branch. The facades landed in ctx-govern-04; a probe that can never fail
    guarding an ungoverned path is worse than no probe."""
    for handler in (
        cortex_server.handle_cortex_agent_launch,
        cortex_server.handle_cortex_govern,
    ):
        src = inspect.getsource(handler)
        assert "getattr(" not in src, f"{handler.__name__} probes for its facade"
        assert "run_agent_loop" not in src, f"{handler.__name__} reaches the agent loop"
        assert "ACEController" not in src, f"{handler.__name__} reaches ACE directly"
        assert "GovernancePipeline" not in src, (
            f"{handler.__name__} builds its own pipeline instead of calling the facade"
        )
    assert "from tools.cortex.api import agent" in inspect.getsource(
        cortex_server.handle_cortex_agent_launch
    )
    assert "from tools.cortex.api import govern" in inspect.getsource(
        cortex_server.handle_cortex_govern
    )


def test_mcp_agent_launch_goes_through_the_governed_facade(monkeypatch):
    captured = {}

    def _fake_agent(goal, roles=None, ctx=None, mode="auto", **kw):
        captured.update(goal=goal, roles=roles, mode=mode, tenant=ctx.tenant_id)
        result = CortexResult(text="Launched ACE team run abc123", provider="ace")
        result.data = {"mode": "team", "instance_id": "abc123"}
        return result

    monkeypatch.setattr(api, "agent", _fake_agent)
    out = cortex_server.handle_cortex_agent_launch(
        {"goal": "triage CVEs", "roles": ["security"], "tenant_id": "t9"}
    )
    assert out["data"]["instance_id"] == "abc123"
    assert captured == {
        "goal": "triage CVEs",
        "roles": ["security"],
        "mode": "auto",
        "tenant": "t9",
    }


# --------------------------------------------------------------------------- #
# 3a. govern() — EXTERNAL-ONLY, and its one entry point genuinely reaches it
# --------------------------------------------------------------------------- #
def test_mcp_govern_reaches_the_facade(monkeypatch):
    """The whole external-only decision rests on this call being live. A surface
    declared "for external callers" that no external caller can actually reach is
    the declared-but-unconsumed defect wearing a justification."""
    captured = {}

    def _fake_govern(text, sources=None, ctx=None):
        captured.update(text=text, sources=sources, tenant=ctx.tenant_id)
        return GovernanceReport(gates_run=["pre_check", "provenance"])

    monkeypatch.setattr(api, "govern", _fake_govern)
    out = cortex_server.handle_cortex_govern(
        {"text": "draft [source: 1]", "sources": 2, "tenant_id": "t9"}
    )
    assert out["governance"]["gates_run"] == ["pre_check", "provenance"]
    assert captured == {"text": "draft [source: 1]", "sources": 2, "tenant": "t9"}


def test_govern_runs_the_chain_and_returns_a_report_not_a_result():
    """The contract external adopters code against: a GovernanceReport, because
    the caller already holds its own text. Changing this breaks the surface's
    only reason to exist."""
    report = api.govern("Account management is required [source: 1].", sources=2)
    assert isinstance(report, GovernanceReport)
    assert report.gates_run, "no gate ran"


def test_the_rest_govern_endpoint_deliberately_does_not_call_the_facade():
    """api_v1_govern runs its own identity lambda. That is NOT an oversight to be
    'fixed': GovernanceReport has no field for the governed/redacted text the
    endpoint returns, and the facade fixes `retrieval=True` while the endpoint
    honours the caller's flag. Pinned so a future sweep does not 'adopt' it and
    silently drop `text` from a published response."""
    src = inspect.getsource(_REST.api_v1_govern)
    assert "_governed(" in src, "the endpoint no longer runs its own single pipeline"
    assert 'params["retrieval"]' in src, "the caller's retrieval flag is no longer honoured"
    # The response fields external callers depend on. `text` is the one a
    # GovernanceReport cannot carry, and is why this endpoint is not the facade.
    for field in ('"text"', '"grounded"', '"blocked"', '"governance"'):
        assert field in src
    # ...and rest_v1 must not import `govern` from the facade module, which is
    # how a future sweep would "adopt" it without noticing the dropped field.
    # (`agent` IS imported there — that one is adopted; see below.)
    import_line = next(
        line
        for line in inspect.getsource(_REST).splitlines()
        if line.startswith("from .api import")
    )
    imported = {n.strip() for n in import_line.split("import", 1)[1].split(",")}
    assert "govern" not in imported
    assert "agent" in imported


# --------------------------------------------------------------------------- #
# 3b. agent() — ADOPTED. All three consumers must genuinely reach the facade.
# --------------------------------------------------------------------------- #
def test_the_canvas_confirm_then_launch_path_reaches_the_facade(monkeypatch):
    """tools/cortex/blueprint.py::_launch_confirmed_agent is a first-party
    in-repo Python consumer — the card's "zero production Python consumers"
    premise was wrong, and this is the assertion that shows it."""
    captured = {}

    def _fake_agent(goal, roles=None, ctx=None, mode="auto", **kw):
        captured.update(goal=goal, mode=mode, trigger=kw.get("trigger_source"))
        result = CortexResult(text="Launched ACE team run xyz")
        result.data = {"instance_id": "xyz"}
        return result

    monkeypatch.setattr(api, "agent", _fake_agent)
    out = _BLUEPRINT._launch_confirmed_agent(
        "stand up a threat-hunt review", CortexContext(tenant_id="t1")
    )
    assert captured["goal"] == "stand up a threat-hunt review"
    assert captured["mode"] == "auto"
    assert captured["trigger"] == "cortex.chat"
    assert "/coworker/xyz" in out["answer"]


def test_the_rest_agent_endpoint_reaches_the_facade(monkeypatch):
    captured = {}

    def _fake_agent(goal, ctx=None, **kw):
        captured.update(goal=goal, mode=kw.get("mode"), trigger=kw.get("trigger_source"))
        result = CortexResult(text="launched")
        result.data = {"instance_id": "i1"}
        return result

    monkeypatch.setattr(_REST, "agent", _fake_agent)
    with _rest_request_context({"goal": "run the release pipeline", "mode": "single"}):
        # __wrapped__ is the endpoint body without @_cortex_api's auth/scope
        # shell. The subject here is "does the endpoint reach the facade", and
        # the scope gate is asserted separately (and owned by test_rest_agent).
        out = _REST.api_v1_agent.__wrapped__(
            {"goal": "run the release pipeline", "mode": "single"}
        )
    assert captured["goal"] == "run the release pipeline"
    assert captured["mode"] == "single"
    # Provenance is attributed to the KEY, never to a caller-supplied field.
    assert captured["trigger"] == "cortex.rest_v1"
    assert out["launched"] is True


def test_the_remote_agent_scope_is_still_never_granted_by_default():
    """`cortex:agent` is the one scope that makes the platform ACT. The
    ctx-reach-03 decision keeps it grantable but never granted by default."""
    from tools.cortex import service_keys

    assert "cortex:agent" in service_keys.AGENT_SCOPES
    assert "cortex:agent" in service_keys.ALL_SCOPES, "the scope is not grantable at all"
    assert "cortex:agent" not in service_keys.DEFAULT_SCOPES, (
        "a newly created Cortex key can now launch agents on the platform's budget"
    )


# --------------------------------------------------------------------------- #
# 4. The compliance domain lens is wired, not merely declared
# --------------------------------------------------------------------------- #
def test_every_declared_domain_lens_resolves_to_a_profile():
    """The canvas picker offers each of these. A lens with no config block is a
    picker entry that advertises scoping it does not perform."""
    from tools.cortex.constants import CORTEX_DOMAIN_KEYS, DEFAULT_DOMAIN

    for key in CORTEX_DOMAIN_KEYS:
        if key == DEFAULT_DOMAIN:
            continue  # "general" is the unscoped default: no profile by design
        profile = load_domain_profile(key)
        assert profile is not None, f"domain lens {key!r} is declared but has no profile"
        assert profile.backends, f"domain lens {key!r} narrows no backends"
        assert profile.persona, f"domain lens {key!r} injects no persona"


def test_the_compliance_lens_scopes_and_speaks_in_controls():
    profile = load_domain_profile("compliance")
    assert set(profile.backends) <= {"rag", "graph", "dic", "kb"}
    assert "compliance analyst" in profile.persona
    assert "800-53" in profile.persona
    assert profile.intents
    # No bespoke triage formatter exists for this lens, so declaring triage:true
    # would advertise a summary that summarize() returns None for.
    assert profile.triage is False
    assert "compliance analyst" in apply_persona(CortexContext(domain="compliance"), "")


def test_empty_domain_sources_are_a_decision_and_a_documented_no_op():
    """`sources: []` on document/proposal/network/compliance is deliberate —
    row-level prefix scoping DROPS non-matching hits, and these lenses have no
    closed prefix set. Assert the no-op rather than trusting the comment."""
    from tools.cortex.domains import filter_by_sources

    rows = [_row("dic_documents"), _row("nist_controls"), _row("pvm_predictions")]
    for lens in ("document", "proposal", "network", "compliance"):
        assert load_domain_profile(lens).sources == []
    kept, dropped = filter_by_sources(rows, [])
    assert len(kept) == 3 and dropped == []
    # ...and that `security`, the one lens that DOES scope, still drops. This is
    # the behaviour the empty lists avoid: two of three legitimate hits deleted.
    kept, dropped = filter_by_sources(rows, load_domain_profile("security").sources)
    assert [r.citation.source_table for r in kept] == ["pvm_predictions"]
    assert len(dropped) == 2


# --------------------------------------------------------------------------- #
# 5. Remaining swept surfaces — documented-as-intentional, asserted as such
# --------------------------------------------------------------------------- #
def test_the_standalone_cortex_stdio_server_cannot_drift_from_the_unified_one():
    """.mcp.json launches only icdev-unified; cortex_server.main() is kept as a
    bounded stdio surface. Two entry points for one tool set is only safe while
    neither can host a tool the other does not."""
    from tools.mcp.tool_registry import TOOL_REGISTRY

    names = {t["name"] for t in cortex_server.CORTEX_TOOLS}
    assert len(names) == len(cortex_server.CORTEX_TOOLS), "duplicate tool name"
    missing = sorted(names - set(TOOL_REGISTRY))
    assert not missing, (
        f"{missing} are served ONLY by the standalone cortex server — "
        f".mcp.json does not launch it, so they are unreachable in this repo"
    )
    for name in names:
        assert TOOL_REGISTRY[name]["module"] == "tools.mcp.cortex_server"


def test_the_reason_tools_echo_the_params_they_ignore(monkeypatch):
    """gap_handlers._REASON_IGNORED_PARAMS: four MCP schema params the governed
    cortex.reason facade has no seam for. They stay in the public input_schema
    but must be visibly ignored, never silently."""
    import tools.cortex as cortex_pkg
    from tools.mcp import gap_handlers

    assert gap_handlers._REASON_IGNORED_PARAMS == (
        "max_rounds",
        "self_consistency_runs",
        "num_debaters",
        "debate_rounds",
    )
    monkeypatch.setattr(
        cortex_pkg, "reason", lambda prompt, **kw: CortexResult(text="because")
    )
    out = gap_handlers._cortex_reason_adapter(
        {"prompt": "why?", "num_debaters": 5, "max_rounds": 3}, "debate", "cod_invoke"
    )
    assert set(out.get("ignored_params") or []) == {"num_debaters", "max_rounds"}
    assert out["content"] == "because"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fake_loop(content: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        final_content=content,
        provider="ollama",
        model_id="m",
        total_cost_usd=0.0,
        result_subtype="success",
        turns=1,
        done=True,
        truncated=False,
        session_id="s",
        tool_call_log=[],
    )


@contextlib.contextmanager
def _rest_request_context(payload: dict):
    """A request context with the identity the real auth middleware installs.

    The v1 endpoints derive tenant/user/classification server-side from
    ``g.security_context`` and never from the request body, so a test must
    supply it the same way — otherwise ``_server_context()`` has nothing to read.
    """
    from flask import Flask, g

    app = Flask(__name__)
    with app.test_request_context(json=payload):
        g.current_user = {"id": "u1", "role": "admin", "tenant_id": "t1"}
        g.security_context = {
            "tenant_id": "t1",
            "user_id": "u1",
            "classification": "CUI",
        }
        yield


def _row(source_table: str):
    from tools.cortex.schemas import Citation, CortexSearchResult

    return CortexSearchResult(
        content="x", score=0.5, citation=Citation(source_table=source_table)
    )
