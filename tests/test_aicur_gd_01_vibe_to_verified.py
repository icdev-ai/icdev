# CUI // SP-CTI
"""aicur-gd-01 — GameDay pack ``vibe_to_verified`` + the deterministic red-first scorer.

The pack teaches shipping AI-written code safely (topics 1a-1c, 2b, 2c). Its
red-first inject is scored WITHOUT an LLM: the team's test is run against the
buggy module (it must FAIL), against a hidden reference fix (it must PASS), and
the team's own fix is run against hidden acceptance tests. A test that fails
everywhere or passes everywhere does not discriminate and earns nothing.
"""

from __future__ import annotations

import json

import pytest

from tools.ttx import ai_scorer
from tools.ttx.ai_scorer import score_red_first
from tools.ttx.scenario_loader import list_scenario_slugs, load_scenario

SLUG = "vibe_to_verified"
RED_FIRST_INJECT = "v2v-inject-02-red-first-sprint"

GOOD_TEST = """
from chunker import chunk

def test_tail_is_not_dropped():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
"""

GOOD_FIX = """
def chunk(items, size, overlap=0):
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be in [0, size)")
    step = size - overlap
    out = []
    for i in range(0, len(items), step):
        out.append(items[i:i + size])
        if i + size >= len(items):
            break
    return out
"""

# A "fix" that keeps the tail but forgets to validate its arguments: passes the
# team test, fails the hidden acceptance tests.
SLOPPY_FIX = """
def chunk(items, size, overlap=0):
    step = max(size - overlap, 1)
    out = []
    for i in range(0, len(items), step):
        out.append(items[i:i + size])
        if i + size >= len(items):
            break
    return out
"""


def _rubric() -> dict:
    scenario = load_scenario(SLUG)
    inj = next(i for i in scenario["injects"] if i["id"] == RED_FIRST_INJECT)
    return inj["scoring"]["rubric"]


# ---------------------------------------------------------------------------
# Pack loads and validates
# ---------------------------------------------------------------------------

def test_pack_is_auto_discovered():
    assert SLUG in list_scenario_slugs()


def test_pack_loads_with_four_injects_and_resolved_rubrics():
    scenario = load_scenario(SLUG)
    ids = [i["id"] for i in scenario["injects"]]
    assert len(ids) == 4 and len(set(ids)) == 4
    for inj in scenario["injects"]:
        assert inj.get("body_md"), f"{inj['id']} has no body"
        rubric = inj["scoring"]["rubric"]
        assert isinstance(rubric, dict), f"{inj['id']} rubric did not resolve"
        if inj.get("inject_type") == "red_first_sprint":
            continue
        dims = rubric["dimensions"]
        assert isinstance(dims, list) and dims
        assert all({"id", "weight", "prompt"} <= set(d) for d in dims)
        assert sum(d["weight"] for d in dims) == pytest.approx(1.0)


def test_review_inject_carries_its_answer_key():
    scenario = load_scenario(SLUG)
    review = scenario["injects"][0]
    note = review.get("context_note", "")
    for defect in ("hallucinated", "swallowed", "non-discriminating"):
        assert defect in note.lower(), f"answer key misses the {defect} defect"


def test_roles_link_the_ai_engineering_missions():
    scenario = load_scenario(SLUG)
    linked = {m for r in scenario["roles"] for m in r.get("academy_missions", [])}
    for mission in ("m12-model-serving", "m13-benchmarks-evals",
                    "m-aie-01-coding-harnesses", "m-aie-02-verification-harnesses",
                    "m-aie-03-vibe-vs-engineering"):
        assert mission in linked, f"no role links {mission}"
    for role in scenario["roles"]:
        assert role.get("persona_data"), f"{role['id']} persona did not resolve"


def test_red_first_reference_fix_is_not_exposed_in_the_rubric():
    """config_json (which carries the rubric) is sent to the player page."""
    blob = json.dumps(_rubric())
    assert "for i in range(0, len(items), step)" not in blob


# ---------------------------------------------------------------------------
# Deterministic red-first scorer
# ---------------------------------------------------------------------------

def test_discriminating_test_and_correct_fix_score_full_marks():
    out = score_red_first(json.dumps({"test_code": GOOD_TEST, "fix_code": GOOD_FIX}), _rubric())
    assert out["judge_pts"] == 100, out["rationale"]
    assert out["checks"]["red_on_buggy"] and out["checks"]["green_on_reference"]
    assert out["checks"]["fix_passes_hidden"]


def test_discriminating_test_without_fix_scores_the_test_only():
    out = score_red_first(json.dumps({"test_code": GOOD_TEST}), _rubric())
    assert out["judge_pts"] == 70, out["rationale"]
    assert out["checks"]["fix_passes_hidden"] is False


def test_bare_test_code_is_accepted_as_the_submission():
    assert score_red_first(GOOD_TEST, _rubric())["judge_pts"] == 70


def test_always_failing_test_does_not_discriminate():
    bad = "def test_nope():\n    assert False\n"
    out = score_red_first(json.dumps({"test_code": bad, "fix_code": GOOD_FIX}), _rubric())
    assert out["checks"]["red_on_buggy"] and not out["checks"]["green_on_reference"]
    assert out["judge_pts"] == 30, out["rationale"]
    assert "does not discriminate" in out["rationale"]


def test_always_passing_test_does_not_discriminate():
    weak = "def test_returns_a_list():\n    assert isinstance(chunk([1, 2, 3], 2), list)\n"
    out = score_red_first(json.dumps({"test_code": weak}), _rubric())
    assert not out["checks"]["red_on_buggy"]
    assert out["judge_pts"] == 0


def test_no_test_functions_scores_zero():
    out = score_red_first(json.dumps({"test_code": "x = 1\n"}), _rubric())
    assert out["judge_pts"] == 0
    assert "no test_" in out["rationale"].lower()


def test_sloppy_fix_fails_the_hidden_acceptance_tests():
    out = score_red_first(json.dumps({"test_code": GOOD_TEST, "fix_code": SLOPPY_FIX}), _rubric())
    assert out["checks"]["fix_passes_hidden"] is False
    assert out["judge_pts"] == 70


def test_sandbox_escape_in_the_test_is_blocked_not_scored():
    evil = "import subprocess\n\ndef test_x():\n    assert False\n"
    out = score_red_first(json.dumps({"test_code": evil}), _rubric())
    assert out["judge_pts"] == 0


def test_missing_fixture_fails_loud():
    out = score_red_first(GOOD_TEST, {"fixture_dir": "vibe_to_verified/fixtures/nope"})
    assert out["judge_pts"] == 0 and out.get("unscored")


def test_fixture_dir_cannot_escape_the_scenarios_tree():
    out = score_red_first(GOOD_TEST, {"fixture_dir": "../tools/ttx"})
    assert out["judge_pts"] == 0 and out.get("unscored")


class _FakeConn:
    """Records writes; score_response only INSERTs a score and UPDATEs a team."""

    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        return self

    def fetchone(self):
        return None

    def commit(self):
        pass


def test_score_response_routes_red_first_without_the_llm_judge(monkeypatch):
    def _no_llm(*a, **k):
        raise AssertionError("red_first_sprint must not call the LLM judge")

    conn = _FakeConn()
    monkeypatch.setattr(ai_scorer, "judge_response", _no_llm)
    monkeypatch.setattr(ai_scorer, "get_connection", lambda: conn)
    out = ai_scorer.score_response(
        response_id=1, team_id=1, inject_id="inj-1", session_id=1,
        response_text=json.dumps({"test_code": GOOD_TEST, "fix_code": GOOD_FIX}),
        inject_body="", receipts=[], rubric=_rubric(),
        bonus_per_call=50, max_receipt_bonus=150, time_taken_s=None,
        time_bonus_enabled=False, inject_type="red_first_sprint",
    )
    assert out["judge_pts"] == 100 and out["judge_unscored"] is False
    assert out["red_first"]["green_on_reference"] is True
    assert any("INSERT INTO ttx_scores" in q for q in conn.sql)
