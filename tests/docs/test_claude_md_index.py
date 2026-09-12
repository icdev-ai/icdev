#!/usr/bin/env python3
# CUI // SP-CTI
"""CLAUDE.md is an INDEX of the card records, not the records (xrv-docs-02).

WHY THIS FILE EXISTS. Claude Code loads CLAUDE.md into EVERY session and
`icdev init` copies the packaged twin into every scaffolded project. Measured
2026-09-12 the file was 367,462 bytes (~91,674 tokens at len/4), of which
297,635 -- 81.0% -- was 82 per-card incident essays inline, so every session
paid for all 82 whether or not it touched one. The essays moved VERBATIM to
docs/reference/cards/ and the block became a one-line-per-card index.

The failure mode this guards is the essays coming BACK, one card at a time,
which is exactly how the block grew in the first place: every card appended its
own record and no single append looked like a problem.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CLAUDE_MD = REPO / "CLAUDE.md"
CARDS_DIR = REPO / "docs" / "reference" / "cards"
COMMANDS_MD = REPO / "docs" / "reference" / "commands.md"
BUDGET_CONFIG = REPO / "args" / "claude_md_budget.yaml"

NL = chr(10)
EMDASH = chr(8212)

#: The index sits under this heading, inside `### Essential Commands`.
INDEX_HEADING = "#### Card records"

#: ``- `<ids>` -- <title> -- `<command>` `` -- the command is optional, because a
#: few cards are libraries with no CLI and are indexed by title alone.
_INDEX_LINE = re.compile(r"^- `(?P<ids>[^`]+)`\s+" + EMDASH + r"\s+(?P<rest>.+)$")

#: A card essay header inside the commands block: ``# <title> (<card-id>)``.
_ESSAY_HEADER = re.compile(
    r"^# .+ \((?:#?[a-z0-9][a-z0-9/_.-]*)(?:\s*,\s*#?[a-z0-9][a-z0-9/_.-]*)*\)\s*$"
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _index_block() -> list[str]:
    """The card index, from its heading to the next `#`/`##`/`###` heading."""
    lines = _text(CLAUDE_MD).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(INDEX_HEADING))
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^#{1,3} ", lines[j]):
            end = j
            break
    return lines[start:end]


def _indexed_ids() -> dict[str, str]:
    """``first id -> the whole index line``, one entry per card."""
    out: dict[str, str] = {}
    for line in _index_block():
        match = _INDEX_LINE.match(line)
        if not match:
            continue
        first = match.group("ids").split(",")[0].strip()
        out[first.replace("/", "-")] = line
    return out


def _card_files() -> list[Path]:
    return sorted(CARDS_DIR.glob("*.md"))


def _essential_commands_fence() -> list[str]:
    """The fenced block under `### Essential Commands`."""
    lines = _text(CLAUDE_MD).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "### Essential Commands")
    opened = next(i for i in range(start, len(lines)) if lines[i].startswith("```"))
    closed = next(i for i in range(opened + 1, len(lines)) if lines[i].startswith("```"))
    return lines[opened + 1:closed]


# ---------------------------------------------------------------------------
# The index and the records are a bijection
# ---------------------------------------------------------------------------

def test_the_cards_directory_exists_and_is_not_empty():
    """An empty directory would make every bijection assertion below vacuous."""
    assert CARDS_DIR.is_dir(), (
        "docs/reference/cards/ is missing -- the card records live there since "
        "xrv-docs-02, and CLAUDE.md's index points at it"
    )
    assert len(_card_files()) >= 80, (
        "only %d card record(s) found; 82 moved out of CLAUDE.md on 2026-09-12 and "
        "a record is deleted only when its card is retired" % len(_card_files())
    )


def test_every_card_record_is_indexed_from_claude_md():
    """A record nothing points at is a record nobody reads."""
    indexed = _indexed_ids()
    orphans = sorted(p.stem for p in _card_files() if p.stem not in indexed)
    assert not orphans, (
        "%d record(s) under docs/reference/cards/ have no line in CLAUDE.md's "
        "'%s' index: %s. Add one line per card." % (len(orphans), INDEX_HEADING, orphans)
    )


def test_every_index_line_resolves_to_a_card_record():
    """The path is DERIVED from the id, so a typo is a dangling pointer."""
    missing = sorted(
        cid for cid in _indexed_ids() if not (CARDS_DIR / (cid + ".md")).is_file()
    )
    assert not missing, (
        "%d index line(s) name a card with no record at docs/reference/cards/<id>.md: "
        "%s" % (len(missing), missing)
    )


def test_the_index_heading_states_where_the_records_live():
    """The per-line path was dropped for size; the rule is stated once instead.

    Without this the index is a list of ids a reader cannot turn into a path,
    which is the one thing the move must not cost.
    """
    block = NL.join(_index_block())
    assert "docs/reference/cards/<id>.md" in block, (
        "the index heading no longer states the id -> path rule, so a reader "
        "cannot find a record from its index line"
    )


def test_every_indexed_command_appears_in_its_own_record():
    """The index carries ONE entry point; the record must actually contain it.

    An index command that is in no record is a command whose context was lost in
    the move -- the failure the verbatim requirement exists to prevent.
    """
    stray: list[str] = []
    for cid, line in _indexed_ids().items():
        quoted = re.findall(r"`([^`]+)`", line)[1:]  # [0] is the id
        if not quoted:
            continue
        if quoted[0] not in _text(CARDS_DIR / (cid + ".md")):
            stray.append("%s: %s" % (cid, quoted[0]))
    assert not stray, (
        "%d index command(s) do not appear in their own record: %s" % (len(stray), stray)
    )


# ---------------------------------------------------------------------------
# The essays must not come back
# ---------------------------------------------------------------------------

def test_no_card_essay_header_remains_in_the_essential_commands_block():
    """The block holds the genuine essential commands and nothing else.

    82 essays were removed on 2026-09-12. A `# <title> (<card-id>)` header back
    inside the fence means one was re-inlined; its home is
    docs/reference/cards/<id>.md with one index line here.
    """
    found = [ln for ln in _essential_commands_fence() if _ESSAY_HEADER.match(ln)]
    assert not found, (
        "%d card essay header(s) are back inside `### Essential Commands`: %s. Move "
        "the essay to docs/reference/cards/<id>.md and leave one index line "
        "(xrv-docs-02)." % (len(found), found[:5])
    )


def test_the_prelude_is_still_a_command_block_and_not_prose():
    """A sanity floor: removing the essays must not have emptied the block."""
    commands = [ln for ln in _essential_commands_fence() if ln.strip() and not ln.startswith("#")]
    assert len(commands) >= 30, (
        "only %d command line(s) left under `### Essential Commands` -- the genuine "
        "essential commands were removed along with the essays" % len(commands)
    )


# ---------------------------------------------------------------------------
# The budget check
# ---------------------------------------------------------------------------

def test_the_budget_config_declares_a_usable_max_bytes():
    raw = yaml.safe_load(_text(BUDGET_CONFIG))
    assert isinstance(raw, dict)
    assert isinstance(raw.get("max_bytes"), int) and raw["max_bytes"] > 0, (
        "args/claude_md_budget.yaml must declare a positive integer max_bytes, or "
        "the check reports UNMEASURABLE rather than judging the file"
    )


def test_the_budget_check_passes_on_the_tree():
    from tools.workflow import coherence_checker as cc

    result = cc.check_claude_md_budget()
    assert result.status == "pass", result.message


def test_the_budget_check_warns_on_a_file_over_budget(tmp_path, monkeypatch):
    """The positive control: a check that never fires is not a check.

    A scanner that stopped scanning also reports clean, so the fixture proves the
    comparison is live and not a constant.
    """
    from tools.workflow import coherence_checker as cc

    root = tmp_path / "repo"
    (root / "args").mkdir(parents=True)
    (root / "CLAUDE.md").write_text(
        "# CLAUDE.md" + NL + NL + "## Guardrails" + NL + ("x" * 5000), encoding="utf-8"
    )
    (root / "args" / "claude_md_budget.yaml").write_text(
        "max_bytes: 100" + NL + "report_sections:" + NL + '  - "## Guardrails"' + NL,
        encoding="utf-8",
    )
    monkeypatch.setattr(cc, "PROJECT_ROOT", root)
    monkeypatch.setattr(cc, "_CLAUDE_MD_BUDGET_CONFIG", root / "args" / "claude_md_budget.yaml")

    result = cc.check_claude_md_budget()
    assert result.status == "warn", result.message
    assert "OVER" in result.message
    assert any("## Guardrails" in line for line in result.extra), (
        "an over-budget warning must NAME where the bytes went, not only that there "
        "are too many: %r" % (result.extra,)
    )


def test_an_absent_budget_is_unmeasurable_and_never_a_pass(tmp_path, monkeypatch):
    """No denominator, no percentage. `unmeasurable` is not a clean bill of health."""
    from tools.workflow import coherence_checker as cc

    root = tmp_path / "repo"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# CLAUDE.md" + NL, encoding="utf-8")
    monkeypatch.setattr(cc, "PROJECT_ROOT", root)
    monkeypatch.setattr(cc, "_CLAUDE_MD_BUDGET_CONFIG", root / "args" / "nope.yaml")

    result = cc.check_claude_md_budget()
    assert result.status == "warn"
    assert "unmeasurable" in result.message
    assert not any("%" in line for line in result.actual), (
        "a percentage was reported with no budget to divide by: %r" % (result.actual,)
    )


def test_a_missing_claude_md_is_unmeasurable_not_zero_bytes(tmp_path, monkeypatch):
    """A missing file is not a small one."""
    from tools.workflow import coherence_checker as cc

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(cc, "PROJECT_ROOT", root)

    result = cc.check_claude_md_budget()
    assert result.status == "warn"
    assert "unmeasurable" in result.message


# ---------------------------------------------------------------------------
# The surfaces that carry the records onward
# ---------------------------------------------------------------------------

def test_the_card_records_are_under_the_documented_command_gate():
    """Every command in the essays was gated inside CLAUDE.md; relocating them
    must not take them out of the gate (args/doc_command_gate.yaml)."""
    raw = yaml.safe_load(_text(REPO / "args" / "doc_command_gate.yaml")) or {}
    docs = [str(d) for d in (raw.get("docs") or [])]
    assert "docs/reference/cards/*.md" in docs, (
        "args/doc_command_gate.yaml no longer covers docs/reference/cards/ -- the 82 "
        "records' commands would leave check_doc_command_paths: %s" % docs
    )


def test_commands_md_lists_every_card():
    """The command reference stays complete after the move."""
    body = _text(COMMANDS_MD)
    missing = sorted(p.stem for p in _card_files() if ("cards/" + p.stem + ".md") not in body)
    assert not missing, (
        "%d card(s) are absent from docs/reference/commands.md's Cards section: %s"
        % (len(missing), missing)
    )


def test_every_card_record_carries_its_title_and_a_body():
    """A record reduced to a stub is the move failing quietly."""
    thin: list[str] = []
    for path in _card_files():
        body = _text(path)
        if not body.startswith("# "):
            thin.append(path.name + ": no title heading")
        elif len(body) < 200:
            thin.append("%s: %d bytes" % (path.name, len(body)))
    assert not thin, "%d record(s) look empty or malformed: %s" % (len(thin), thin)
