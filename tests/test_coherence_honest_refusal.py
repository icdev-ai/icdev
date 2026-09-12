# CUI // SP-CTI
"""Two coherence checks must not report a defect for an HONEST REFUSAL (rem-hyg-20).

MEASURED 2026-09-09, ``coherence_checker.py --all`` against the live PG board:
64 checks, 9 non-pass, and BOTH small findings are false positives of the same
shape -- a predicate that matches the SHAPE of the defect while missing its
SUBSTANCE.

  api_wiring           flags ``api_macro_intelligence``, which returns HTTP 503
                       with every badge NULL and a stated reason because the
                       macro feed left with the trading domain (xit-rm-02).
                       nav-plat-04 built it that way so "a data outage must NOT
                       masquerade as a benign NEUTRAL regime". Returning NEUTRAL
                       would be the fabrication; the check calls the refusal
                       "likely placeholder code".

  capability_liveness  FAILS on ``approval_park_is_whole``, a claim that IS
                       dispatched by claim_verifier_reflex and honestly returns
                       ``unmeasurable`` (reported [] / derived [] -- this board
                       holds no approval-gate rows). Nothing is un-wired; there
                       is no DATA.

The repo has answered this twice already -- perfect_score_census requires a
RATIO and not merely the literal 100.0; undeclared_import_census requires a
SWALLOWING handler and not merely an undeclared import. Both fixes here are the
same move: narrow the PREDICATE, never add an exemption entry.

Every test below asserts BOTH directions. A scanner that stopped scanning also
reports clean, so each fix ships with the finding it must still catch.
"""
from __future__ import annotations

import ast


from tools.workflow import coherence_checker as cc


# ─────────────────────────────────────────────────────────────── api_wiring
REFUSAL_503 = '''
@app.route("/api/macro/intelligence")
def api_macro_intelligence():
    """The feed moved; say so rather than inventing a neutral regime."""
    detail = "macro intelligence feed moved to ICDEV[FT]; no provider here"
    return jsonify({
        "status": "error",
        "detail": detail,
        "qeqt_phase": None,
        "credit_stress": None,
    }), 503
'''

PLACEHOLDER_200 = '''
@app.route("/api/things")
def api_things():
    """A real placeholder: plausible data, success status, no storage."""
    return jsonify({
        "things": [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
        "total": 2,
    })
'''

REFUSAL_ABORT = '''
@app.route("/api/gone")
def api_gone():
    """A refusal expressed as a status constant."""
    payload = {"status": "error", "detail": "moved", "value": None}
    return jsonify(payload), 410
'''


def _func(src: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef):
            return node
    raise AssertionError("no function in fixture")


def test_a_non_2xx_return_is_a_refusal_not_a_placeholder():
    assert cc._returns_error_status(_func(REFUSAL_503)) is True


def test_a_410_refusal_is_also_a_refusal():
    assert cc._returns_error_status(_func(REFUSAL_ABORT)) is True


def test_a_plain_200_literal_is_still_a_placeholder():
    """The control. Narrowing must not switch the check off."""
    assert cc._returns_error_status(_func(PLACEHOLDER_200)) is False


def test_the_live_macro_handler_is_no_longer_flagged():
    """The real finding, re-derived from the tree rather than a fixture."""
    check = cc.check_api_wiring()
    hits = [x for x in (check.extra or []) if "api_macro_intelligence" in x]
    assert hits == [], hits


def test_api_wiring_still_reports_a_real_placeholder(tmp_path, monkeypatch):
    """A genuinely hardcoded 200 handler must still be found."""
    src = tmp_path / "app.py"
    src.write_text("from flask import jsonify\n" + PLACEHOLDER_200, encoding="utf-8")
    node = _func(src.read_text(encoding="utf-8"))
    assert cc._returns_error_status(node) is False


# ────────────────────────────────────────────────────── capability_liveness
#
# THE LIST IS TRUNCATED AND THE COUNT IS NOT. `_evaluate_capability_liveness`
# already warns in its own docstring that everything is decided on COUNTS,
# "never on the ``inert_units`` name lists: those are truncated to
# ``max_listed_units`` per class, so a set difference over them would silently
# under-report the 466-unit classes". `attempted_never_measured` is truncated
# the SAME way (capability_consumption.py:1385, `[:max_listed]`) and carried no
# count at all -- so subtracting len(list) would under-subtract on exactly the
# large classes the warning is about. The count is what the gate reads.

GATE = {"evidence_anchor": {"table": "audit_trail", "min_rows": 1000},
        "grandfathered": {"verified_claim": 0}}


def _report(*, inert, attempted_count, listed=None):
    """One class, shaped as the gate reads it."""
    extra = {"attempted_never_measured_count": attempted_count}
    if listed is not None:
        extra["attempted_never_measured"] = list(listed)
    return {"classes": [{
        "capability_class": "verified_claim",
        "declared": 15,
        "inert": inert,
        "telemetry_available": True,
        "telemetry_table": "genesis_audit",
        "extra": extra,
    }]}


def _evaluate(*, inert, attempted_count, listed=None):
    return cc._evaluate_capability_liveness(
        window_report=_report(inert=inert, attempted_count=attempted_count,
                              listed=listed),
        lifetime_report=_report(inert=inert, attempted_count=attempted_count,
                                listed=listed),
        corpus_rows=50_000,
        gate=GATE,
    )


def test_an_attempted_but_unmeasurable_unit_is_not_never_consumed():
    """It HAS a consumer; what it lacks is a SUBSTRATE.

    `approval_park_is_whole` is dispatched by claim_verifier_reflex and returns
    unmeasurable because this board holds no approval-gate rows. Counting that
    as never-consumed conflates "nothing calls it" -- the defect this gate
    exists for -- with "it ran and honestly could not measure".
    """
    ev = _evaluate(inert=1, attempted_count=1,
                   listed=["approval_park_is_whole"])
    cls = ev["classes"][0]
    assert cls["never_consumed"] == 0
    assert cls["attempted_never_measured"] == 1
    assert ev["over_budget"] == []


def test_a_unit_nothing_ever_ran_still_breaches_the_budget():
    """The control: the defect the gate exists for is still caught."""
    ev = _evaluate(inert=1, attempted_count=0, listed=[])
    cls = ev["classes"][0]
    assert cls["never_consumed"] == 1
    assert [c["capability_class"] for c in ev["over_budget"]] == ["verified_claim"]


def test_the_count_is_read_and_not_the_truncated_list():
    """THE TRAP. 40 units were attempted; the list carries the first 3.

    Reading len(list) subtracts 3 of 40 and the class still breaches by 37 --
    a gate that fires for a reason that is not true.
    """
    ev = _evaluate(inert=40, attempted_count=40, listed=["a", "b", "c"])
    cls = ev["classes"][0]
    assert cls["attempted_never_measured"] == 40, "the gate read the truncated list"
    assert cls["never_consumed"] == 0
    assert ev["over_budget"] == []


def test_a_missing_count_is_treated_as_zero_not_guessed_from_the_list():
    """An older report has no count. FAIL CLOSED: subtract nothing.

    Guessing from the truncated list would under-subtract silently; treating an
    absent count as 'all of them' would switch the gate off for every class.
    """
    ev = _evaluate(inert=1, attempted_count=None, listed=["approval_park_is_whole"])
    cls = ev["classes"][0]
    assert cls["never_consumed"] == 1
    assert cls["attempted_never_measured"] == 0


def test_the_subtraction_never_goes_negative():
    """Two passes taken moments apart must not manufacture a negative count."""
    ev = _evaluate(inert=1, attempted_count=5)
    assert ev["classes"][0]["never_consumed"] == 0


def test_the_live_gate_budget_was_not_raised():
    """verified_claim is absent from the gate file, i.e. the default 0."""
    import yaml

    gate = yaml.safe_load(
        (cc.PROJECT_ROOT / "args" / "liveness_gate.yaml").read_text(encoding="utf-8"))
    grandfathered = (gate or {}).get("grandfathered") or {}
    assert int(grandfathered.get("verified_claim", 0)) == 0, (
        "verified_claim was grandfathered to get this through -- forbidden")
    # A RATCHET, NOT A PIN (xrv-cost-05). This was `== 467`, written by a card
    # that wanted to say "I did not touch it" — but equality also refuses the
    # one direction args/liveness_gate.yaml asks for ("Lower a count when you
    # wire a capability up. NEVER raise one"). xrv-cost-05 wired the MCP
    # servers' own dispatch audit and drained 468 inert -> 460, and the equality
    # failed it for draining the backlog.
    #
    # THE CEILING IS THE CURRENT VALUE, NOT THE OLD ONE, and that is what makes
    # this a ratchet rather than a relaxation. `<= 467` would have permitted a
    # silent regrowth from 460 back to 467 — a backlog rebuilding itself behind
    # a green gate, which is the exact failure the census discipline exists to
    # stop. Pinning the ceiling at what we actually drained to is the same rule
    # `backlog_max`, `skip_max` and `self_root_max` already carry: it may only
    # ever be LOWERED, by the card that does the wiring.
    #
    # It is also what makes this change DISCRIMINATING. `<= 467` passes against
    # the merge base unchanged (467 <= 467), so it asserts current behaviour and
    # the red-first gate refuses it — correctly, because a test relaxed to admit
    # a change can never have gone red for it. `<= 460` fails at the merge base
    # (467 > 460) and passes here, so the RED is recorded.
    assert int(grandfathered.get("mcp_dispatch_tool", 10**9)) <= 460, (
        "mcp_dispatch_tool budget was RAISED above the 460 this card drained it "
        "to; grandfathering a capability to get a commit through is forbidden — "
        "wire it up or do not declare it. Lower this ceiling when you drain it "
        "further; never raise it.")


def test_the_consumption_report_emits_the_count():
    """The producer half: a truncated list must ship a count beside it."""
    from tools.awareness import capability_consumption as cco

    src = (cc.PROJECT_ROOT / "tools" / "awareness"
           / "capability_consumption.py").read_text(encoding="utf-8")
    assert "attempted_never_measured_count" in src, (
        "the count the gate reads is not produced")
    assert hasattr(cco, "PROBES")
