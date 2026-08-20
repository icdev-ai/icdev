# CUI // SP-CTI
"""The perfect-score-for-no-data gate (rem-hyg-13).

Two halves are asserted here and they fail in opposite directions:

  * the PREDICATE — a 100.0 fallback over a ratio is a finding, and the three
    things the survey found legitimate are not. Each of those three would be
    matched by the `else 100.0` grep the card measured with, so a scanner that
    merely re-implements the grep passes nothing below.
  * the RATCHET — the census is enumerated, the ceiling only goes down, and the
    tree is currently clean of the shape at every site the card named.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.ci import perfect_score_census as psc

REPO = Path(__file__).resolve().parents[2]


# ── the predicate ──────────────────────────────────────────────────────────
class TestFindingPredicate:
    """A finding is the CONJUNCTION of a 100.0 fallback AND a ratio body."""

    def test_the_shape_the_card_named_is_a_finding(self):
        src = (
            "def sla():\n"
            "    pct = round(within / total_relevant * 100, 1)"
            " if total_relevant > 0 else 100.0\n"
        )
        sites = psc.scan_source(src, "tools/x.py")
        assert len(sites) == 1
        assert sites[0]["qualname"] == "sla"
        assert sites[0]["key"] == "tools/x.py::sla#0"

    def test_a_ratio_nested_inside_round_is_still_found(self):
        # The common spelling in this tree wraps the division several layers
        # deep. A predicate that only inspected the top-level node would miss
        # every one of the twelve real sites.
        src = (
            "def des():\n"
            "    s = round(100.0 * (a + b * 0.5) / scoreable, 1)"
            " if scoreable > 0 else 100.0\n"
        )
        assert len(psc.scan_source(src, "tools/x.py")) == 1

    @pytest.mark.parametrize(
        "src, why",
        [
            (
                "def quote():\n    price = bars[-1].close if bars else 100.0\n",
                "a synthetic bar price is a dollar figure, not a percentage",
            ),
            (
                "def macro():\n"
                "    dxy = round(float(d['Close'].iloc[-1]), 2) if len(d) > 0 else 100.0\n",
                "the US Dollar Index has a BASE of 100 -- neutral, not perfect",
            ),
        ],
    )
    def test_a_non_ratio_fallback_is_not_a_finding(self, src, why):
        # Both of these are real lines from tools/trading/, both match the
        # `else 100.0` grep the card measured with, and neither is a score.
        # They are excluded by the PREDICATE and hold no exemption entry --
        # asserting that is what proves the exclusion is structural.
        assert psc.scan_source(src, "tools/trading/x.py") == [], why

    def test_the_bare_int_100_is_out_of_scope(self):
        # tools/trading/dashboard/app.py's RSI. An RSI of 100 with no down
        # moves is the definition of the indicator. This one DOES divide, so
        # only the exact-float rule keeps it out.
        src = (
            "def rsi():\n"
            "    out = 100 - (100 / (1 + rs)) if avg_loss > 0 else 100\n"
        )
        assert psc.scan_source(src, "tools/trading/dashboard/app.py") == []

    def test_a_comment_can_never_be_a_finding(self):
        # tools/canvas_compliance/posture.py:260 is a COMMENT inside the
        # rem-hyg-09 fix explaining this very defect. A grep-based census would
        # have registered the previous fix's own explanation of itself as its
        # first entry.
        src = (
            "def posture():\n"
            "    # the old `else 100.0` rendered a canvas nobody had assessed\n"
            "    score = round(closed / total * 100, 1) if total > 0 else None\n"
        )
        assert psc.scan_source(src, "tools/canvas_compliance/posture.py") == []

    def test_a_string_containing_the_shape_is_not_a_finding(self):
        src = 'ADVICE = "pct = a / b if b else 100.0 -- do not do this"\n'
        assert psc.scan_source(src, "tools/x.py") == []

    def test_the_corrected_form_is_not_a_finding(self):
        src = "def sla():\n    pct = round(a / b * 100, 1) if b > 0 else None\n"
        assert psc.scan_source(src, "tools/x.py") == []

    def test_true_is_not_mistaken_for_the_perfect_constant(self):
        # isinstance(True, int) is True and 100.0 == 100, so an equality test
        # without an exact type check accepts surprising things.
        assert not psc.is_perfect_constant(ast.parse("True", mode="eval").body)
        assert not psc.is_perfect_constant(ast.parse("100", mode="eval").body)
        assert psc.is_perfect_constant(ast.parse("100.0", mode="eval").body)


class TestSiteKeys:
    """Per SITE, not per file, and never carrying a line number."""

    def test_two_sites_in_one_function_get_distinct_keys(self):
        src = (
            "def two():\n"
            "    a = x / y if y else 100.0\n"
            "    b = p / q if q else 100.0\n"
        )
        keys = [s["key"] for s in psc.scan_source(src, "tools/x.py")]
        assert keys == ["tools/x.py::two#0", "tools/x.py::two#1"]

    def test_a_key_carries_no_line_number(self):
        # Line numbers churn on every edit above the site, which would make the
        # census a merge-conflict generator and every unrelated PR a census edit.
        src = "def one():\n    a = x / y if y else 100.0\n"
        padded = "\n" * 40 + src
        assert (
            psc.scan_source(src, "tools/x.py")[0]["key"]
            == psc.scan_source(padded, "tools/x.py")[0]["key"]
        )


# ── the ratchet ────────────────────────────────────────────────────────────
class TestRatchet:
    def test_ceiling_is_zero_and_census_is_empty(self):
        cfg = psc.load_gate()
        census = psc.load_census(REPO, cfg)
        assert cfg["perfect_score_max"] == 0, "the ceiling may only go DOWN"
        assert census == set(), (
            "nothing is grandfathered; a new entry breaches the ceiling"
        )

    def test_every_exclusion_states_a_reason(self):
        cfg = psc.load_gate()
        minimum = cfg["min_reason_chars"]
        for entry in cfg["exclude"]:
            reason = (entry.get("reason") or "").strip()
            assert len(reason) >= minimum, f"{entry['path']} has no written reason"

    def test_the_two_trading_sites_hold_no_exemption(self):
        # They are excluded by the predicate. If somebody later "fixes" the
        # predicate by exempting them here instead, this fails -- an exemption
        # is a claim a reviewer must check and a predicate is one the scanner
        # re-derives every run.
        excluded = {e["path"] for e in psc.load_gate()["exclude"]}
        assert not any("trading" in path for path in excluded)


# ── the sites the card named ───────────────────────────────────────────────
_FIXED_SITES = [
    "tools/dashboard/api/compliance_debt.py",
    "tools/compliance/fips200_validator.py",
    "tools/agentic_ai_canvas/safety_redundancy.py",
    "tools/aiml_canvas/governance_assessor.py",
    "tools/document_intelligence/style_engine.py",
    "tools/il5/il5_display_service.py",
    "tools/il5/ingestion.py",
    "tools/infra_canvas/infra_engine.py",
    "tools/maintenance/maintenance_auditor.py",
    "tools/mbse/des_assessor.py",
    "tools/modernization/compliance_bridge.py",
    "tools/qdc_canvas/qdc_engine.py",
]


@pytest.mark.parametrize("rel", _FIXED_SITES)
@pytest.mark.parametrize("tree", ["", "icdev/"])
def test_named_site_is_clean_in_both_trees(rel, tree):
    """Every site the card enumerated, in tools/ AND in the icdev/ mirror.

    Asserted per file rather than by a whole-tree scan: an rglob over ~4,200
    modules costs minutes on a loaded runner, and this is the claim that
    actually matters.
    """
    path = REPO / (tree + rel)
    assert path.exists(), f"{path} is missing"
    assert psc.scan_file(path, REPO) == []
