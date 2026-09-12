# CUI // SP-CTI
"""The CLAUDE.md card regenerator: idempotent, and it never deletes what it has not written.

WHY THE TOOL EXISTS. xrv-docs-02 moved 82 card essays out of `### Essential
Commands` BY HAND and the removal did not survive: the branch's own later commit
carried CLAUDE.md at 371,288 bytes with every essay back inside the fence, while
the 82 extracted records and the `#### Card records` index were still correct.
Every card appends a block to CLAUDE.md, so it conflicts on nearly every merge
and each resolution is another chance to keep the side that still has the
essays. A restructure re-appliable only by hand gets undone by hand.

Every fixture here is a tmp_path CLAUDE.md. Nothing touches the real file.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

cards = importlib.import_module("tools.docs.claude_md_cards")

NL = chr(10)


def _tree(tmp_path: Path, fence_body: str, index: str = "") -> Path:
    """A minimal CLAUDE.md with the two sections the tool reads."""
    md = tmp_path / "CLAUDE.md"
    md.write_text(
        "# Top" + NL * 2
        + "### Essential Commands" + NL
        + "```bash" + NL
        + fence_body.rstrip(NL) + NL
        + "```" + NL * 2
        + "#### Card records" + NL
        + "Records live in `docs/reference/cards/`." + NL
        + (index.rstrip(NL) + NL if index else "")
        + NL + "## Next" + NL + "after" + NL,
        encoding="utf-8",
    )
    return md


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(cards, "CARDS_DIR", tmp_path / "cards")
    return tmp_path


# ── the discriminator ───────────────────────────────────────────────────────


def test_prose_ending_in_parens_is_not_an_essay_header(sandbox, monkeypatch):
    """THE FALSE POSITIVE THAT WOULD HAVE COST AN ESSAY.

    `# stronger posture than the raw-INSERT (219) and undeclared-import (210)`
    is the fourth line of the rem-hyg-13 essay on the live tree and matches the
    header shape exactly. Splitting there truncates that essay and writes a
    record called `210.md`. A real header always opens a block and is preceded
    by a BLANK line; a continuation line never is.
    """
    body = (
        "python tools/x.py" + NL * 2
        + "# A real card (aa-bb-01)" + NL
        + "python tools/aa.py" + NL
        + "# prose about the raw-INSERT (219) and undeclared-import (210)" + NL
        + "# more prose" + NL
    )
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, body))
    _, essays = cards.parse_essays(cards.CLAUDE_MD.read_text(encoding="utf-8").splitlines())

    assert [e["slug"] for e in essays] == ["aa-bb-01"], (
        "the prose line was treated as a second essay header")
    assert any("(210)" in ln for ln in essays[0]["body"]), (
        "the prose line must stay INSIDE its own essay, not start a new one")


def test_a_header_after_a_blank_line_is_an_essay(sandbox, monkeypatch):
    """The control: the discriminator must still find real headers."""
    body = ("cmd" + NL * 2 + "# One (aa-01)" + NL + "a" + NL * 2
            + "# Two (bb-02)" + NL + "b" + NL)
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, body))
    _, essays = cards.parse_essays(cards.CLAUDE_MD.read_text(encoding="utf-8").splitlines())
    assert [e["slug"] for e in essays] == ["aa-01", "bb-02"]


# ── it never deletes what it has not written ────────────────────────────────


def test_the_essay_is_removed_only_after_its_record_is_on_disk(sandbox, monkeypatch):
    body = "cmd" + NL * 2 + "# One (aa-01)" + NL + "# prose" + NL + "python tools/one.py" + NL
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, body))
    out = cards.apply()

    record = cards.CARDS_DIR / "aa-01.md"
    assert record.exists(), "the record must be written"
    assert "prose" in record.read_text(encoding="utf-8")
    assert "python tools/one.py" in record.read_text(encoding="utf-8"), (
        "the card's commands must travel with it")
    assert out["inline_essays_after"] == 0
    assert "cmd" in cards.CLAUDE_MD.read_text(encoding="utf-8"), "the prelude must survive"


def test_a_record_that_cannot_be_written_is_kept_inline(sandbox, monkeypatch):
    """A trim that loses an incident record to save bytes has destroyed the
    thing the file exists for. The essay stays, and the failure is REPORTED."""
    body = "cmd" + NL * 2 + "# One (aa-01)" + NL + "# prose" + NL
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, body))

    real_write = Path.write_text

    def boom(self, *a, **k):
        if self.name == "aa-01.md":
            raise OSError("disk full")
        return real_write(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", boom)
    out = cards.apply()

    assert [k["slug"] for k in out["kept_inline"]] == ["aa-01"]
    assert out["trimmed"] == 0
    assert "# One (aa-01)" in cards.CLAUDE_MD.read_text(encoding="utf-8"), (
        "an essay whose record failed to write must stay inline")


# ── idempotence ─────────────────────────────────────────────────────────────


def test_a_second_run_changes_nothing(sandbox, monkeypatch):
    """What makes it safe in front of a conflict resolution: re-run it and the
    file is correct again, whichever side won."""
    body = "cmd" + NL * 2 + "# One (aa-01)" + NL + "# prose" + NL
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, body))
    first = cards.apply()
    assert first["changed"] is True

    after_first = cards.CLAUDE_MD.read_text(encoding="utf-8")
    second = cards.apply()
    assert second["changed"] is False
    assert cards.CLAUDE_MD.read_text(encoding="utf-8") == after_first, (
        "a second run must not rewrite the file")


def test_check_reports_drift_without_writing(sandbox, monkeypatch):
    body = "cmd" + NL * 2 + "# One (aa-01)" + NL + "# prose" + NL
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, body))
    before = cards.CLAUDE_MD.read_text(encoding="utf-8")

    assert cards.main(["--check", "--json"]) == 1, "drift must exit 1"
    assert cards.CLAUDE_MD.read_text(encoding="utf-8") == before, "--check wrote to the file"
    assert not cards.CARDS_DIR.exists() or not list(cards.CARDS_DIR.glob("*.md"))


def test_a_missing_claude_md_is_unmeasurable_not_zero(sandbox, monkeypatch):
    """Absence is not emptiness -- the rule this repo applies everywhere."""
    monkeypatch.setattr(cards, "CLAUDE_MD", sandbox / "nope.md")
    out = cards.survey()
    assert out["state"] == "unmeasurable"
    assert "bytes" not in out, "a missing file must not report a size"
