# CUI // SP-CTI
"""Tests for Constitutional AI per-rule critique/revise (agx-verify-02)."""
from __future__ import annotations

import json

from tools.quality.constitutional_ai import (
    Rule,
    RuleResult,
    classify_rule_verdict,
    compose_overall,
    constitutional_review,
    critique_rule,
    load_constitution,
)


class _Resp:
    def __init__(self, content):
        self.content = content
        self.model_id = "fake"


class _ScriptedRouter:
    """Router returning a verdict per rule id, and a revised-text marker on revise.

    ``verdicts`` maps rule_id -> verdict string. The revise prompt (which contains
    'Revise the ARTIFACT') returns text that flips the rule to pass on re-critique
    when ``fix_on_revise`` includes that rule id.
    """

    def __init__(self, verdicts, fix_on_revise=()):
        self.verdicts = verdicts
        self.fix_on_revise = set(fix_on_revise)
        self.calls = []
        self._revised = set()

    def invoke(self, function, request, **kwargs):
        prompt = ""
        for m in request.messages or []:
            if m.get("role") == "user":
                prompt = m["content"]
        self.calls.append((function, prompt))
        if "Revise the ARTIFACT" in prompt:
            # Figure out which rule this revision targets by matching its principle.
            for rid in self.fix_on_revise:
                self._revised.add(rid)
            return _Resp("REVISED ARTIFACT TEXT")
        # critique: pick the verdict for whichever rule principle is in the prompt
        for rid, verdict in self.verdicts.items():
            if rid in self._prompt_rule(prompt):
                v = "pass" if rid in self._revised else verdict
                return _Resp(json.dumps({"verdict": v, "offending_span": "bad", "rationale": "why"}))
        return _Resp(json.dumps({"verdict": "pass", "offending_span": "", "rationale": ""}))

    @staticmethod
    def _prompt_rule(prompt):
        # our test rules embed the rule id token inside the principle text
        return prompt


# ── vocabulary / composition (deterministic-picker) ─────────────────────────

def test_classify_verdict_known():
    assert classify_rule_verdict("pass") == "pass"
    assert classify_rule_verdict(" FAIL ") == "fail"
    assert classify_rule_verdict("not_applicable") == "not_applicable"


def test_classify_verdict_fail_closed_by_severity():
    # unknown token: BLOCK -> fail (fail closed), WARN -> not_applicable
    assert classify_rule_verdict("garbage", severity="block") == "fail"
    assert classify_rule_verdict("garbage", severity="warn") == "not_applicable"
    assert classify_rule_verdict(None, severity="block") == "fail"


def test_compose_any_block_fail_fails():
    results = [
        RuleResult("r1", "block", "pass"),
        RuleResult("r2", "block", "fail"),
        RuleResult("r3", "warn", "pass"),
    ]
    d = compose_overall(results)
    assert d["passed"] is False
    assert d["failed_block_rules"] == ["r2"]


def test_compose_warn_fail_does_not_block():
    results = [
        RuleResult("r1", "block", "pass"),
        RuleResult("r2", "warn", "fail"),
    ]
    d = compose_overall(results)
    assert d["passed"] is True
    assert d["failed_warn_rules"] == ["r2"]


def test_compose_all_pass():
    results = [RuleResult("r1", "block", "pass"), RuleResult("r2", "warn", "pass")]
    assert compose_overall(results)["passed"] is True


# ── per-rule critique is independent (one rule per call) ────────────────────

def test_critique_is_per_rule_not_monolithic():
    rule = Rule(id="r1", severity="block", principle="rule_r1 principle text")
    router = _ScriptedRouter({"r1": "fail"})
    res = critique_rule("some artifact", rule, router=router)
    assert res.verdict == "fail"
    # exactly one call, and the prompt names only this rule's principle
    assert len(router.calls) == 1
    assert "rule_r1 principle text" in router.calls[0][1]
    assert "Do not consider any other rule" in router.calls[0][1]


def test_critique_fails_closed_on_malformed():
    rule = Rule(id="r1", severity="block", principle="rule_r1")

    class _BadRouter:
        def invoke(self, fn, req, **kw):
            return _Resp("not json")
    res = critique_rule("art", rule, router=_BadRouter())
    assert res.verdict == "fail"  # block rule + malformed -> fail closed


# ── config loading (rules as DATA) ──────────────────────────────────────────

def test_load_constitution_from_shipped_config():
    rules = load_constitution()
    ids = {r.id for r in rules}
    # the shipped constitution block encodes existing invariants
    assert "missing_cui_markings_if_required" in ids
    assert "uncited_claim" in ids
    # severities are constrained to block/warn
    assert all(r.severity in ("block", "warn") for r in rules)


def test_load_constitution_applies_to_filter():
    proposal_rules = {r.id for r in load_constitution(artifact_type="proposal")}
    # numeric_contradiction applies to proposal; a rule scoped elsewhere is filtered
    assert "uncited_claim" in proposal_rules
    dic_rules = {r.id for r in load_constitution(artifact_type="dic")}
    assert "numeric_contradiction" not in dic_rules  # scoped to proposal/rfi only


# ── end-to-end review + bounded revision ────────────────────────────────────

def _rules_two_block():
    return [
        Rule(id="ruleA", severity="block", principle="ruleA principle"),
        Rule(id="ruleB", severity="block", principle="ruleB principle"),
    ]


def test_review_revises_failed_block_rule(monkeypatch):
    import tools.quality.constitutional_ai as cai
    monkeypatch.setattr(cai, "load_constitution", lambda *a, **k: _rules_two_block())
    router = _ScriptedRouter({"ruleA": "fail", "ruleB": "pass"}, fix_on_revise={"ruleA"})
    out = constitutional_review("draft", router=router, max_revisions=2, persist_audit=False)
    assert out["passed"] is True
    assert out["revisions_used"] >= 1
    assert out["revised_text"] == "REVISED ARTIFACT TEXT"
    assert out["unresolved_block_rules"] == []


def test_review_bounded_no_silent_giveup(monkeypatch):
    import tools.quality.constitutional_ai as cai
    monkeypatch.setattr(cai, "load_constitution", lambda *a, **k: _rules_two_block())
    # ruleA never fixes -> stays failing; loop is bounded and reports it, not hangs
    router = _ScriptedRouter({"ruleA": "fail", "ruleB": "pass"}, fix_on_revise=set())
    out = constitutional_review("draft", router=router, max_revisions=2, persist_audit=False)
    assert out["passed"] is False
    assert out["unresolved_block_rules"] == ["ruleA"]
    assert out["revisions_used"] == 2  # exhausted the bound, did not give up early


def test_review_records_audit_shape(monkeypatch):
    import tools.quality.constitutional_ai as cai
    monkeypatch.setattr(cai, "load_constitution", lambda *a, **k: _rules_two_block())
    router = _ScriptedRouter({"ruleA": "pass", "ruleB": "pass"})
    out = constitutional_review("draft", router=router, persist_audit=False, tenant_id="t1")
    assert len(out["audit_records"]) == 2
    rec = out["audit_records"][0]
    for key in ("id", "rule_id", "verdict", "severity", "recorded_at", "tenant_id", "classification"):
        assert key in rec
    assert rec["tenant_id"] == "t1"


def test_review_empty_constitution_passes(monkeypatch):
    import tools.quality.constitutional_ai as cai
    monkeypatch.setattr(cai, "load_constitution", lambda *a, **k: [])
    out = constitutional_review("draft", router=_ScriptedRouter({}), persist_audit=False)
    assert out["passed"] is True and out["rule_trace"] == []
