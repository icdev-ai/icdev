# CUI // SP-CTI
"""oss2-fix-03 (D3) — the four AutoGen skill cards are marked reference-only.

`.claude/commands/{code_review_agent,test_orchestrator_agent,security_researcher,
senior_software_engineer}.md` embed AutoGen agent JSON (system_message,
human_input_mode, max_consecutive_auto_reply) seeded from SkillHub, but AutoGen is
not imported anywhere, so they were inert data blobs presented as capabilities —
same class as a phantom capability claim. This pins the honest banner and the
pointer to ICDEV's real equivalent.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CARDS = [
    "code_review_agent",
    "test_orchestrator_agent",
    "security_researcher",
    "senior_software_engineer",
]


def test_every_autogen_card_carries_the_reference_only_banner():
    for card in CARDS:
        raw = (REPO / ".claude" / "commands" / f"{card}.md").read_text(encoding="utf-8")
        # normalise the markdown blockquote wrapping (`\n> `) so the banner sentence
        # is matched regardless of where it line-wraps.
        text = raw.replace("\n> ", " ").replace("\n>", " ")
        assert "NOT an executable capability" in text, f"{card} missing the D3 banner"
        assert "AutoGen is not an ICDEV dependency and nothing executes it" in text
        assert "oss2-fix-03" in text


def test_banner_points_at_equivalents_that_exist():
    # Each banner must direct the reader to a real, present ICDEV implementation —
    # a dangling pointer would just be a new false claim.
    equivalents = {
        "code_review_agent": [".claude/commands/review.md", "tools/quality/review_loop.py"],
        "test_orchestrator_agent": [".claude/commands/test.md", "tools/testing/test_orchestrator.py"],
        "security_researcher": [".claude/commands/security_audit.md", "tools/anvil/secure.py"],
        "senior_software_engineer": [".claude/commands/feature.md"],
    }
    for card, paths in equivalents.items():
        text = (REPO / ".claude" / "commands" / f"{card}.md").read_text(encoding="utf-8")
        for p in paths:
            assert p in text, f"{card} banner should reference {p}"
            assert (REPO / p).exists(), f"referenced equivalent {p} must exist"
