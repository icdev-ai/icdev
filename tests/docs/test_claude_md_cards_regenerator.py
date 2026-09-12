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


# ── a record that shipped DIRECTLY is still a card ──────────────────────────


def _record(sandbox, stem: str, body: str) -> None:
    cards.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (cards.CARDS_DIR / (stem + ".md")).write_text(body, encoding="utf-8")


def test_an_orphan_record_is_indexed_from_its_own_header(sandbox, monkeypatch):
    """THE DEFECT PR #2277 HIT. Since xrv-docs-02 a new card ships its RECORD
    rather than an inline essay, and the index was built only from what this run
    EXTRACTED -- so the first card to follow the new convention was never
    indexed and `test_every_card_record_is_indexed_from_claude_md` went red on a
    tree where nothing was wrong except the tool's reach.
    """
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, "cmd" + NL))
    _record(sandbox, "aa-01", "# A measured thing (aa-01)" + NL * 2 + "prose" + NL)

    assert cards.survey()["unindexed_records"] == ["aa-01"]
    out = cards.apply()
    assert out["index_lines_added"] == 1
    assert out["unindexable_records"] == []
    assert "- `aa-01`" in cards.CLAUDE_MD.read_text(encoding="utf-8")


def test_nothing_inline_is_not_nothing_to_do(sandbox, monkeypatch):
    """`apply` returned early on an empty fence, so the first run after #2277
    reported `changed: false` over a record it had just reported unindexed."""
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, "cmd" + NL))
    _record(sandbox, "bb-02", "# Another (bb-02)" + NL * 2 + "prose" + NL)
    assert cards.apply()["changed"] is True


def test_a_classification_banner_is_not_a_title(sandbox, monkeypatch):
    """`# CUI // SP-CTI` is a REQUIRED MARKING, not a heading. Taking the first
    non-blank line made every marked record unindexable -- and marked records
    exist, so that is a live population and not a hypothetical one."""
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, "cmd" + NL))
    _record(sandbox, "cc-03",
            "# CUI // SP-CTI" + NL * 2 + "# Marked card (cc-03)" + NL * 2 + "prose" + NL)
    out = cards.apply()
    assert out["unindexable_records"] == []
    assert "- `cc-03`" in cards.CLAUDE_MD.read_text(encoding="utf-8")


def test_the_index_cites_a_command_only_when_the_record_has_one(sandbox, monkeypatch):
    """The index's first backticked token after the id is read as the CARD'S
    COMMAND and must appear in the record. Emitting the record's own PATH there
    asserts the record quotes its own filename, which it does not -- so a record
    with no command block gets NO command rather than an invented one.
    """
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, "cmd" + NL))
    _record(sandbox, "dd-04", "# Prose only (dd-04)" + NL * 2 + "no commands here" + NL)
    _record(sandbox, "ee-05",
            "# With a command (ee-05)" + NL * 2 + "```bash" + NL
            + "python -m tools.thing --json" + NL + "```" + NL)
    cards.apply()
    body = cards.CLAUDE_MD.read_text(encoding="utf-8")

    prose = next(ln for ln in body.splitlines() if ln.startswith("- `dd-04`"))
    assert "`" not in prose.split("`dd-04`", 1)[1], (
        "a record with no command must not have one invented for it")
    assert ".md`" not in prose, "the record's path is not its command"

    withcmd = next(ln for ln in body.splitlines() if ln.startswith("- `ee-05`"))
    assert "`python -m tools.thing --json`" in withcmd


def test_an_unparseable_record_is_reported_not_guessed(sandbox, monkeypatch):
    """A document this tool does not understand is NAMED. Inventing an index
    entry for it would be the fabrication every rail in this repo refuses."""
    monkeypatch.setattr(cards, "CLAUDE_MD", _tree(sandbox, "cmd" + NL))
    _record(sandbox, "ff-06", "no header at all, just prose" + NL)
    out = cards.apply()
    assert out["unindexable_records"] == ["ff-06"]
    assert "- `ff-06`" not in cards.CLAUDE_MD.read_text(encoding="utf-8")
