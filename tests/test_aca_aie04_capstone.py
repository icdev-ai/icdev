# CUI // SP-CTI
"""m-aie-04-capstone (aicur-eng-04): catalogued, discovered, GRADED — and recommended.

The AI-Assisted Engineering capstone hands the learner a vibe-coded module with three
planted defects. This pins it end to end:

  * it is catalogued after m-aie-03, and discovery finds a lesson and two labs;
  * the brief carries a valid item bank, every item answerable from the brief;
  * step 2 is graded in the code_runner sandbox, offline: the shipped starter FAILS,
    a fix with a suite that lets any planted defect survive FAILS, and a real fix with
    discriminating tests PASSES;
  * step 3 grades an instruction file structurally: the shipped wish-list FAILS,
    a checkable file PASSES, and `|| true` or a missing defect class FAILS;
  * the six AI-curriculum missions are catalogued with resolvable prereqs, and
    /api/academy/learning-path ranks an unlocked mission ahead of a locked one and
    never recommends a mission with no steps.
"""
from __future__ import annotations

import pytest

from apps.forge_academy.assessment import validate_item_bank
from apps.forge_academy.code_runner import run_code
from apps.forge_academy.content_loader import (
    BUILTIN_MISSIONS,
    CONTENT_ROOT,
    discover_steps,
    load_item_banks,
)

SLUG = "m-aie-04-capstone"
STEPS_DIR = CONTENT_ROOT / "tier1" / SLUG / "steps"
REPO_ROOT = CONTENT_ROOT.parents[2]


@pytest.fixture(scope="module")
def steps() -> list:
    return discover_steps().get(SLUG) or []


def _read(path) -> str:
    return (CONTENT_ROOT / path).read_text(encoding="utf-8")


def _asset(name: str) -> str:
    return (STEPS_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Catalogue + discovery
# ---------------------------------------------------------------------------

def test_capstone_is_catalogued_after_m_aie_03():
    by_slug = {m["slug"]: m for m in BUILTIN_MISSIONS}
    mission = by_slug[SLUG]
    assert mission["tier"] == 1
    assert mission["prereqs"] == ["m-aie-03-vibe-vs-engineering"]


@pytest.mark.parametrize("slug", [
    "m-aie-01-coding-harnesses", "m-aie-02-verification-harnesses",
    "m-aie-03-vibe-vs-engineering", "m-aie-04-capstone",
    "m12-model-serving", "m13-benchmarks-evals",
])
def test_curriculum_mission_is_catalogued_with_roles_and_resolvable_prereqs(slug):
    by_slug = {m["slug"]: m for m in BUILTIN_MISSIONS}
    mission = by_slug[slug]
    assert mission.get("role_filter"), f"{slug} declares no role_filter"
    assert set(mission["prereqs"]) <= set(by_slug), f"{slug} names an uncatalogued prereq"
    assert discover_steps().get(slug), f"{slug} has no authored steps"


def test_discovery_finds_lesson_then_two_labs(steps):
    assert [s["step_num"] for s in steps] == [1, 2, 3]
    assert [s["step_type"] for s in steps] == ["watch", "coding", "coding"]
    for lab in steps[1:]:
        n = lab["step_num"]
        assert lab["starter_code_path"].endswith(f"step{n}_starter.py")
        assert lab["test_code_path"].endswith(f"step{n}_test.py")


def test_the_brief_is_dated_and_cites_real_files(steps):
    brief = _read(steps[0]["content_path"])
    assert "as of October 2026" in brief
    for rel in ("tools/ci/red_first_gate.py", "args/claude_md_budget.yaml"):
        assert rel in brief
        assert (REPO_ROOT / rel).is_file(), f"brief cites missing {rel}"


# ---------------------------------------------------------------------------
# Item bank — the brief is graded
# ---------------------------------------------------------------------------

_ANSWER_TERMS = {
    "cap-why-it-shipped": "none of them crashes on the happy path",
    "cap-mutant-survived": "a test is missing",
    "cap-tests-first": "might pass whatever the implementation does",
    "cap-red-test-response": "the defect is in the code",
    "cap-checkable-rule": "**checkable**",
    "cap-done-evidence": "test output from running the exact command",
    "cap-instruction-budget": "paid for on every session",
}


def test_the_brief_has_a_valid_bank_and_the_labs_have_none(steps):
    banks = load_item_banks().get(SLUG) or {}
    assert set(banks) == {1}
    items = banks[1]
    assert not validate_item_bank(items), validate_item_bank(items)
    assert len({i["correct_index"] for i in items}) > 1, "answer is always the same option"


def test_every_item_is_answerable_from_the_brief(steps):
    body = " ".join(_read(steps[0]["content_path"]).split())  # ignore line wrapping
    items = load_item_banks()[SLUG][1]
    assert {i["item_key"] for i in items} == set(_ANSWER_TERMS), "item keys drifted"
    for key, term in _ANSWER_TERMS.items():
        assert term in body, f"{key}: the brief no longer states {term!r}"


# ---------------------------------------------------------------------------
# Step 2 lab — kill the mutants, then fix the module
# ---------------------------------------------------------------------------

_AGENT_TEST = '''def test_charge():
    ledger = {}
    assert charge(ledger, "ana", 100) == 100
'''

_BUGGY_BODY = '''    try:
        if tokens < 1:
            raise ValueError("tokens must be at least 1")
        ledger[user] = ledger.get(user, 0) + tokens
        if ledger[user] >= limit:
            raise BudgetExceeded(f"{user} is over budget")
    except Exception:
        pass
    return ledger.get(user, 0)
'''

_FIXED_BODY = '''    if tokens < 1:
        raise ValueError("tokens must be at least 1")
    new_total = ledger.get(user, 0) + tokens
    if new_total > limit:
        raise BudgetExceeded(f"{user} is over budget")
    ledger[user] = new_total
    return new_total
'''

_DISCRIMINATING = '''def test_charges_accumulate():
    ledger = {}
    assert charge(ledger, "ana", 100) == 100
    assert charge(ledger, "ana", 200) == 300


def test_exactly_up_to_the_limit_is_allowed():
    ledger = {"ana": 900}
    assert charge(ledger, "ana", 100) == 1000


def test_over_the_limit_raises_and_leaves_ledger_unchanged():
    ledger = {"ana": 900}
    try:
        charge(ledger, "ana", 101)
    except BudgetExceeded:
        assert ledger == {"ana": 900}
        return
    raise AssertionError("expected BudgetExceeded")


def test_zero_tokens_raises():
    try:
        charge({}, "ana", 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
'''

# Misses the partial-write mutant: it never inspects the ledger after a refusal.
_MISSES_PARTIAL_WRITE = '''def test_exactly_up_to_the_limit_is_allowed():
    assert charge({"ana": 900}, "ana", 100) == 1000


def test_over_the_limit_raises():
    try:
        charge({"ana": 900}, "ana", 101)
    except BudgetExceeded:
        return
    raise AssertionError("expected BudgetExceeded")
'''


def _step2(tests: str = _AGENT_TEST, fixed: bool = False) -> str:
    src = _asset("step2_starter.py")
    assert _AGENT_TEST in src and _BUGGY_BODY in src, "the step 2 starter drifted"
    if fixed:
        src = src.replace(_BUGGY_BODY, _FIXED_BODY)
    return src.replace(_AGENT_TEST, tests)


def _grade2(src: str) -> dict:
    result = run_code(src, _asset("step2_test.py"))
    assert result.get("error") != "blocked", result.get("stderr")
    return result


def test_step2_starter_runs_green_on_its_own():
    """The agent's own test passes as shipped — that is the trap."""
    result = run_code(_asset("step2_starter.py"))
    assert result["passed"], result["stderr"]
    assert "PASS test_charge" in result["stdout"]


def test_step2_starter_fails_the_grader_on_the_spec():
    result = _grade2(_step2())
    assert not result["passed"]
    assert "does not meet the spec" in result["stderr"]


def test_step2_tests_without_a_fix_fail_the_grader():
    result = _grade2(_step2(_DISCRIMINATING, fixed=False))
    assert not result["passed"]
    assert "does not meet the spec" in result["stderr"]


def test_step2_fix_with_discriminating_tests_passes():
    result = _grade2(_step2(_DISCRIMINATING, fixed=True))
    assert result["passed"], result["stderr"]
    assert "all 3 mutants killed" in result["stdout"]


def test_step2_fix_with_the_agents_test_lets_mutants_survive():
    result = _grade2(_step2(_AGENT_TEST, fixed=True))
    assert not result["passed"]
    assert "SURVIVED" in result["stderr"]
    assert "boundary" in result["stderr"]


def test_step2_names_the_one_surviving_defect_class():
    result = _grade2(_step2(_MISSES_PARTIAL_WRITE, fixed=True))
    assert not result["passed"]
    assert "partial write" in result["stderr"]
    assert "boundary (" not in result["stderr"]
    assert "swallowed exception (" not in result["stderr"]


def test_step2_always_red_test_fails_the_grader():
    result = _grade2(_step2("def test_always_red():\n    assert False\n", fixed=True))
    assert not result["passed"]
    assert "your fixed charge" in result["stderr"]


# ---------------------------------------------------------------------------
# Step 3 lab — the instruction file
# ---------------------------------------------------------------------------

_GOOD_INSTRUCTIONS = '''INSTRUCTIONS = """\\
# AGENTS.md - token ledger

## Commands
- Test: python -m pytest tests/test_ledger.py -q

## Rules
- Never wrap charge() in `except Exception`; let BudgetExceeded and ValueError propagate.
- Always test a charge landing exactly on the limit and one token over it.
- Must leave the ledger unchanged when a charge is refused: check before you write.

## Done means
- python -m pytest tests/test_ledger.py -q exits 0, and you paste its output.
"""
'''


def _grade3(instructions_src: str) -> dict:
    result = run_code(instructions_src, _asset("step3_test.py"))
    assert result.get("error") != "blocked", result.get("stderr")
    return result


def test_step3_starter_fails_the_grader():
    result = _grade3(_asset("step3_starter.py"))
    assert not result["passed"]
    assert "uncheckable phrase" in result["stderr"]
    assert "missing the '## Commands' section" in result["stderr"]


def test_step3_checkable_instruction_file_passes():
    result = _grade3(_GOOD_INSTRUCTIONS)
    assert result["passed"], result["stderr"]


def test_step3_or_true_on_the_test_command_fails():
    src = _GOOD_INSTRUCTIONS.replace(
        "- Test: python -m pytest tests/test_ledger.py -q\n",
        "- Test: python -m pytest tests/test_ledger.py -q || true\n",
    )
    result = _grade3(src)
    assert not result["passed"]
    assert "|| true" in result["stderr"]


def test_step3_missing_defect_class_fails():
    src = _GOOD_INSTRUCTIONS.replace(
        "- Must leave the ledger unchanged when a charge is refused: check before you write.\n",
        "- Must keep functions small.\n",
    )
    result = _grade3(src)
    assert not result["passed"]
    assert "partial write on failure" in result["stderr"]


def test_step3_non_imperative_rule_fails():
    src = _GOOD_INSTRUCTIONS.replace(
        "- Always test a charge",
        "- Try to test a charge",
    )
    result = _grade3(src)
    assert not result["passed"]
    assert "does not start with" in result["stderr"]


def test_step3_overlong_file_fails():
    padding = "".join(f"Note {i}.\\n" for i in range(40))
    src = _GOOD_INSTRUCTIONS.replace("## Commands", padding + "## Commands")
    result = _grade3(src)
    assert not result["passed"]
    assert "non-blank lines" in result["stderr"]


# ---------------------------------------------------------------------------
# Learning path — unlocked missions first, dead ends never
# ---------------------------------------------------------------------------

class _NoRows:
    def execute(self, *_a, **_k):
        return self

    def fetchall(self):
        return []


def test_learning_path_ranks_unlocked_ahead_of_locked_and_skips_dead_ends(monkeypatch):
    import tools.db.storage as storage
    from apps.forge_academy import blueprint

    missions = [
        {"id": 1, "slug": "a-locked", "title": "A Locked", "tier": 1, "order_idx": 1,
         "is_available": True},
        {"id": 2, "slug": "m-aie-04-capstone", "title": "Z Capstone", "tier": 1,
         "order_idx": 15, "is_available": True},
        {"id": 3, "slug": "b-empty", "title": "B Empty", "tier": 1, "order_idx": 2,
         "is_available": False},
    ]
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _NoRows())
    monkeypatch.setattr(blueprint, "list_missions", lambda **_k: list(missions))
    monkeypatch.setattr(
        blueprint, "mission_prereq_state",
        lambda _uid, ms: {m["id"]: {"ready": m["id"] == 2} for m in ms},
    )

    recs = blueprint._recommend_next_missions(7, "swe_arch", limit=5)
    assert [m["slug"] for m in recs] == ["m-aie-04-capstone", "a-locked"]


# ---------------------------------------------------------------------------
# Feature doc — the nine-topic coverage matrix names real missions
# ---------------------------------------------------------------------------

def test_feature_doc_maps_every_topic_to_a_catalogued_mission():
    doc = (REPO_ROOT / "docs" / "features" / "phase-aicur-ai-curriculum.md").read_text(
        encoding="utf-8")
    catalogued = {m["slug"] for m in BUILTIN_MISSIONS}
    rows = [ln for ln in doc.splitlines() if ln.startswith("| ") and "`m" in ln]
    assert len(rows) >= 9, "the coverage matrix must have a row per topic"
    for slug in ("m-aie-01-coding-harnesses", "m-aie-02-verification-harnesses",
                 "m-aie-03-vibe-vs-engineering", "m-aie-04-capstone",
                 "m12-model-serving", "m13-benchmarks-evals"):
        assert f"`{slug}`" in doc, f"coverage matrix omits {slug}"
    import re
    for slug in re.findall(r"`(m[\w-]+)`", "\n".join(rows)):
        assert slug in catalogued, f"coverage matrix cites uncatalogued mission {slug}"
