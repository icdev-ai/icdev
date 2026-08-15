#!/usr/bin/env python3
# CUI // SP-CTI
"""The inverse question must reach every review surface — trust-disc-06 (D396/D397).

    "Under what condition does this check PASS while the system is BROKEN?"

Every check in this codebase is written by the same process that writes the code,
in the same session, in the environment where the author's mental model holds.
Nothing asked the inverse question, which is exactly what a second,
differently-motivated reader supplies for free.

The task that added it is a PROCESS change and says so, and D394/D397 both record
what happens to an instruction with no enforced artifact. So the question is not
only written into the three prose surfaces (`/review`, the `code_review` skill,
and the ANVIL Critique phase in `goals/build_app.md`) — it is also APPENDED TO
EVERY CRITIC PROMPT at runtime by ``anvil_critique.py::_dispatch_critics``, and
these tests assert the prompt actually carries it. That is the part a reader can
distinguish from a rule nobody follows.

Run:
    pytest tests/test_adversarial_review_question.py -v --tb=short
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.agent.anvil_critique import (  # noqa: E402
    ADVERSARIAL_QUESTION,
    AtlasCritique,
    load_config,
)

QUESTION = "Under what condition does this check PASS while the system is BROKEN?"

# The prose surfaces named by trust-disc-06, plus the packaged copies `icdev init`
# actually scaffolds — a rule that ships to generated projects in a weaker form is
# the bootstrap-drift defect (args/bootstrap_parity.yaml) one file over.
SURFACES = [
    ".claude/commands/review.md",
    ".claude/commands/code_review.md",
    "goals/build_app.md",
    "args/anvil_critique_config.yaml",
    "icdev/data/claude_bootstrap/claude/commands/review.md",
    "icdev/data/claude_bootstrap/claude/commands/code_review.md",
    "icdev/data/goals/build_app.md",
]


def _write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


_MINIMAL_CRITICS = """anvil_critique:
  enabled: true
  max_rounds: 3
{extra}  critics:
    - agent: security-agent
      role: security_reviewer
      focus:
        - security_vulnerability
      prompt_context: |
        Review for security issues.
    - agent: knowledge-agent
      role: patterns_reviewer
      focus:
        - testing_gap
      prompt_context: |
        Review for quality issues.
"""


@pytest.fixture
def captured_prompts(monkeypatch):
    """Record every prompt handed to a critic agent."""
    seen: list[str] = []

    def _fake_call_agent(self, agent, prompt, focus_areas):
        seen.append(prompt)
        return []

    monkeypatch.setattr(AtlasCritique, "_call_agent", _fake_call_agent)
    return seen


# ---------------------------------------------------------------------------
# The wired half: the question reaches the critics
# ---------------------------------------------------------------------------
def test_every_critic_prompt_carries_the_question(tmp_path, captured_prompts):
    """The whole point. Fails against the pre-change tree, which is the proof."""
    cfg = _write_config(tmp_path / "cfg.yaml", _MINIMAL_CRITICS.format(extra=""))
    critique = AtlasCritique(db_path=tmp_path / "t.db", config_path=cfg)

    critique._dispatch_critics("some plan text")

    assert len(captured_prompts) == 2, "both critics must be dispatched"
    for prompt in captured_prompts:
        assert QUESTION in prompt, (
            "a critic was asked to review without being asked the inverse question"
        )


def test_the_question_survives_a_config_that_omits_the_key(tmp_path, captured_prompts):
    """Deleting the YAML key must not silently delete the question.

    A guard whose only home is a config file is a guard someone can switch off by
    tidying — the module constant is the floor.
    """
    cfg = _write_config(tmp_path / "cfg.yaml", _MINIMAL_CRITICS.format(extra=""))
    critique = AtlasCritique(db_path=tmp_path / "t.db", config_path=cfg)
    assert "adversarial_question" not in critique._config

    critique._dispatch_critics("some plan text")

    assert captured_prompts
    for prompt in captured_prompts:
        assert ADVERSARIAL_QUESTION in prompt


def test_a_blank_key_falls_back_rather_than_asking_nothing(tmp_path, captured_prompts):
    """`adversarial_question: ""` must not degrade to an empty line in the prompt."""
    cfg = _write_config(
        tmp_path / "cfg.yaml", _MINIMAL_CRITICS.format(extra='  adversarial_question: ""\n')
    )
    critique = AtlasCritique(db_path=tmp_path / "t.db", config_path=cfg)

    critique._dispatch_critics("some plan text")

    assert captured_prompts
    for prompt in captured_prompts:
        assert ADVERSARIAL_QUESTION in prompt


def test_the_wording_is_actually_read_from_config(tmp_path, captured_prompts):
    """Discrimination check on this test file itself.

    Without this case, hardcoding the string in the prompt would pass every other
    assertion here while the config key did nothing — the exact defect the question
    exists to catch, in the tests written to enforce it.
    """
    cfg = _write_config(
        tmp_path / "cfg.yaml",
        _MINIMAL_CRITICS.format(extra="  adversarial_question: WHEN-DOES-THIS-LIE\n"),
    )
    critique = AtlasCritique(db_path=tmp_path / "t.db", config_path=cfg)

    critique._dispatch_critics("some plan text")

    assert captured_prompts
    for prompt in captured_prompts:
        assert "WHEN-DOES-THIS-LIE" in prompt


def test_a_concrete_answer_is_routed_to_a_finding_type_that_exists(tmp_path, captured_prompts):
    """The instruction tells the critic to file a `testing_gap`.

    `_parse_findings` drops any finding whose type is outside the critic's focus
    list, so naming a type that no critic is focused on would send every answer
    straight to the floor.

    The assertion is scoped to the INSTRUCTION BLOCK, not the whole prompt. The
    first draft asserted ``"testing_gap" in prompt``, which was the only one of
    these 14 tests to pass against the pre-change tree: every prompt already
    enumerates FINDING_TYPES verbatim, so the routing sentence could have named a
    type no critic focuses on — every answer silently floored — and the test would
    still have been green. Found by asking this card's own question of this card's
    own tests.
    """
    cfg = _write_config(tmp_path / "cfg.yaml", _MINIMAL_CRITICS.format(extra=""))
    critique = AtlasCritique(db_path=tmp_path / "t.db", config_path=cfg)

    critique._dispatch_critics("some plan text")

    assert captured_prompts
    for prompt in captured_prompts:
        assert QUESTION in prompt
        instruction_block = prompt.split(QUESTION, 1)[1].split("Plan to review:", 1)[0]
        assert "testing_gap" in instruction_block, (
            "the routing instruction must name the finding type by which a concrete "
            "answer is recorded"
        )

    focus_areas = [
        f for c in critique._config.get("critics", []) for f in (c.get("focus") or [])
    ]
    assert "testing_gap" in focus_areas


def test_the_shipped_config_declares_the_question():
    """The repo's own config, not a synthetic one."""
    cfg = load_config()
    assert QUESTION in str(cfg.get("adversarial_question", ""))


# ---------------------------------------------------------------------------
# The prose half: the surfaces trust-disc-06 names
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rel", SURFACES)
def test_review_surface_carries_the_question(rel):
    path = REPO_ROOT / rel
    assert path.is_file(), f"review surface missing: {rel}"
    assert QUESTION in path.read_text(encoding="utf-8"), (
        f"{rel} does not ask the inverse question"
    )


def test_review_report_schema_requires_the_answer():
    """A question whose answer is never recorded is the `|| true` failure again.

    `/review` returns JSON; the answer has a slot in it, so a review that skipped
    the question is distinguishable from one that asked it and found nothing.
    """
    text = (REPO_ROOT / ".claude/commands/review.md").read_text(encoding="utf-8")
    assert '"pass_while_broken"' in text
    for field in ('"check"', '"condition"', '"test_case"'):
        assert field in text, f"pass_while_broken schema is missing {field}"
