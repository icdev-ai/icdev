"""aicur-fun-01: m01 carries a graded token-cost / context-budget lab (step 6).

What this pins:
  * discovery loads six m01 steps and step 6 is a CODING step with both assets,
  * the step 6 grader PASSES a correct solution and FAILS the untouched starter,
    a solution that bills reasoning tokens at the input rate, and one that drops
    the NEWEST turns instead of the oldest - all through the real sandbox,
  * the m01 item bank still validates after the fact refresh, and the retired
    80%-summarization item is gone from it,
  * a key that leaves a bank is deactivated on re-seed rather than left serving.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from apps.forge_academy import content_loader
from apps.forge_academy.assessment import validate_item_bank
from apps.forge_academy.code_runner import run_code

_STEPS = (
    Path(__file__).resolve().parent.parent
    / "apps" / "forge_academy" / "content" / "tier1" / "m01-llm-fundamentals" / "steps"
)

_SOLUTION = '''

def request_cost(usage, prices):
    n = lambda k: usage.get(k, 0) or 0
    return (n("input_tokens") * prices["input"]
            + n("cached_input_tokens") * prices["cached_input"]
            + (n("output_tokens") + n("reasoning_tokens")) * prices["output"]) / 1_000_000


def fit_to_budget(system_prompt, history, question, context_window, reserved_output):
    budget = context_window - reserved_output
    used = count_tokens(system_prompt) + count_tokens(question)
    if used > budget:
        raise ValueError("prompt cannot fit")
    kept = []
    for turn in reversed(history):
        if used + count_tokens(turn) > budget:
            break
        used += count_tokens(turn)
        kept.append(turn)
    return kept[::-1]
'''


def _starter() -> str:
    return (_STEPS / "step6_starter.py").read_text(encoding="utf-8")


def _grader() -> str:
    return (_STEPS / "step6_test.py").read_text(encoding="utf-8")


def _grade(code: str) -> dict:
    result = run_code(code, test_code=_grader())
    assert result.get("error") != "blocked", result
    return result


def test_m01_discovers_six_steps_with_step6_a_coding_lab():
    steps = content_loader.discover_steps()["m01-llm-fundamentals"]
    assert [s["step_num"] for s in steps] == [1, 2, 3, 4, 5, 6]
    step6 = steps[-1]
    assert step6["step_type"] == "coding"
    assert step6["starter_code_path"].endswith("step6_starter.py")
    assert step6["test_code_path"].endswith("step6_test.py")


def test_step6_correct_solution_passes():
    result = _grade(_starter() + _SOLUTION)
    assert result["passed"] is True, result.get("stderr")
    assert "PASS" in result["stdout"]


def test_step6_untouched_starter_fails():
    assert _grade(_starter())["passed"] is False


def test_step6_reasoning_billed_as_input_fails():
    wrong = _SOLUTION.replace(
        '(n("output_tokens") + n("reasoning_tokens")) * prices["output"]',
        'n("output_tokens") * prices["output"] + n("reasoning_tokens") * prices["input"]',
    )
    assert wrong != _SOLUTION
    assert _grade(_starter() + wrong)["passed"] is False


def test_step6_dropping_newest_turns_fails():
    wrong = _SOLUTION.replace("for turn in reversed(history):", "for turn in history:")
    wrong = wrong.replace("return kept[::-1]", "return kept")
    assert _grade(_starter() + wrong)["passed"] is False


def test_m01_item_bank_validates_and_retired_item_is_gone():
    bank = content_loader.load_item_banks()["m01-llm-fundamentals"]
    for step_num in (2, 4, 5):
        assert validate_item_bank(bank[step_num]) == [], step_num
    assert 6 not in bank, "step 6 is a coding lab; an item bank would pre-empt its grader"
    keys = {i["item_key"] for i in bank[4]}
    assert "ctx-router-threshold" not in keys
    assert {"ctx-floor-window", "ctx-overcount"} <= keys


class _Conn:
    """sqlite3 behind the %s paramstyle seed_item_banks writes."""

    def __init__(self):
        self._c = sqlite3.connect(":memory:")
        self._c.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._c.commit()


def test_seed_retires_a_key_that_left_the_bank(monkeypatch):
    conn = _Conn()
    conn.execute("""CREATE TABLE fa_missions (id INTEGER PRIMARY KEY, slug TEXT NOT NULL,
                    title TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY,
                    mission_id INTEGER NOT NULL, step_num INTEGER NOT NULL,
                    title TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE fa_assessment_items (id INTEGER PRIMARY KEY,
        step_id INTEGER NOT NULL, item_key TEXT NOT NULL, prompt TEXT NOT NULL,
        options_json TEXT NOT NULL DEFAULT '[]', correct_index INTEGER NOT NULL DEFAULT 0,
        explanation TEXT, difficulty TEXT DEFAULT 'core',
        is_active INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("INSERT INTO fa_missions VALUES (1, 'm-x', 'M X')")
    conn.execute("INSERT INTO fa_mission_steps VALUES (10, 1, 1, 'Step 1')")

    def _item(key):
        return {"item_key": key, "prompt": f"q {key}", "options": ["a", "b"],
                "correct_index": 0, "explanation": "", "difficulty": "core"}

    keys = ["k1", "k2", "k3", "k4", "k5", "old"]
    monkeypatch.setattr(content_loader, "load_item_banks",
                        lambda: {"m-x": {1: [_item(k) for k in keys]}})
    content_loader.seed_item_banks(conn)

    keys.remove("old")
    keys.append("new")
    content_loader.seed_item_banks(conn)

    rows = {r["item_key"]: r["is_active"] for r in
            conn.execute("SELECT item_key, is_active FROM fa_assessment_items")}
    assert rows["old"] == 0
    assert rows["new"] == 1
    assert all(rows[k] == 1 for k in ("k1", "k2", "k3", "k4", "k5"))
