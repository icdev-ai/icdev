# CUI // SP-CTI
"""Resolve only the conflicts that are provably not disagreements.

Of roughly ten conflicts resolved by hand on 2026-08-09, two shapes accounted for
six, and both were resolved the same way every time — keep both sides. Everything
else is judgement and belongs to a person: a wrong merge is discovered much later
than an unresolved one.
"""
from __future__ import annotations

from tools.kanban import conflict_resolvers as cr


def _conflict(ours, theirs):
    return f"before\n<<<<<<< HEAD\n{ours}\n=======\n{theirs}\n>>>>>>> origin/main\nafter\n"


# ── scope ───────────────────────────────────────────────────────────────────
def test_only_allowlisted_files_are_touched():
    """'Looks like markdown' is not evidence that keeping both sides is right."""
    body = _conflict("- a new line", "- another new line")
    assert cr.resolve_text("docs/reference/commands.md", body) is not None
    assert cr.resolve_text("README.md", body) is None
    assert cr.resolve_text("docs/architecture/overview.md", body) is None


def test_code_is_never_resolved():
    """A conflict in Python is a disagreement about behaviour."""
    body = _conflict("    x = 1", "    y = 2")
    assert cr.resolve_text("tools/ci/pr_watcher.py", body) is None
    assert cr.resolve_text("tests/test_thing.py", body) is None


# ── the additive case ───────────────────────────────────────────────────────
def test_two_independent_blocks_are_both_kept():
    """commands.md gained a --merge block and a --requeue block on one day."""
    out = cr.resolve_text(
        "docs/reference/commands.md",
        _conflict("python tools/kanban/cli.py --merge", "python tools/kanban/cli.py --requeue"))
    assert out is not None
    text, notes = out
    assert "--merge" in text and "--requeue" in text
    assert "<<<<<<<" not in text and ">>>>>>>" not in text
    assert notes and "kept both sides" in notes[0]


def test_the_base_side_is_ordered_first():
    """Their side is already on the base, so the file reads in landing order."""
    text, _ = cr.resolve_text(
        "docs/reference/commands.md", _conflict("OURS-LINE", "THEIRS-LINE"))
    assert text.index("THEIRS-LINE") < text.index("OURS-LINE")


# ── the refusals ────────────────────────────────────────────────────────────
def test_a_side_that_deleted_something_is_never_auto_resolved():
    """One side empty means the other's block was removed, not added — someone
    made an editorial decision and it is not ours to re-make."""
    assert cr.resolve_text("docs/reference/commands.md", _conflict("", "kept")) is None
    assert cr.resolve_text("docs/reference/commands.md", _conflict("kept", "")) is None


def test_a_rewrite_of_the_same_line_is_not_an_addition():
    """A shared line means both sides edited the SAME text."""
    body = _conflict("shared line\nours extra", "shared line\ntheirs extra")
    assert cr.resolve_text("docs/reference/commands.md", body) is None


def test_a_file_with_no_conflict_is_left_alone():
    assert cr.resolve_text("docs/reference/commands.md", "no markers here\n") is None


# ── allocation numbers ──────────────────────────────────────────────────────
def test_a_duplicate_allocation_number_is_moved_up():
    body = ("### Gap 55 — first\n\nbody\n\n"
            + _conflict("### Gap 56 — ours\n\nours body",
                        "### Gap 56 — theirs\n\ntheirs body"))
    text, notes = cr.resolve_text("docs/security/sandbox-coverage.md", body)
    heads = [ln for ln in text.splitlines() if ln.startswith("### Gap")]
    assert heads == ["### Gap 55 — first", "### Gap 56 — theirs", "### Gap 57 — ours"]
    assert any("Gap 56 -> Gap 57" in n for n in notes)


def test_the_first_occurrence_keeps_its_number():
    """Something already references it — a merged PR, a cross-link."""
    body = _conflict("### Gap 10 — ours", "### Gap 10 — theirs")
    text, _ = cr.resolve_text("docs/security/sandbox-coverage.md", body)
    assert "### Gap 10 — theirs" in text, "the side already on base keeps the number"


def test_the_notes_say_what_was_done():
    """An automatic resolution that does not explain itself is indistinguishable
    from a bad merge when someone reads the history later."""
    _, notes = cr.resolve_text(
        "docs/reference/commands.md", _conflict("a", "b"))
    assert notes and all(isinstance(n, str) and n for n in notes)


# ── end to end, against a real git rebase ───────────────────────────────────
def test_a_real_rebase_conflict_is_resolved_and_continues(tmp_path):
    """The unit tests prove the text transform; this proves the git plumbing.

    Builds a throwaway repo where two branches each append their own block to
    docs/reference/commands.md, rebases one onto the other, and asserts the
    conflict is resolved in place and `rebase --continue` succeeds — the exact
    sequence that cost a human a worktree round-trip six times in one day.
    """
    import subprocess

    from tools.kanban import rebase_recovery as rr

    def git(*args, cwd=tmp_path):
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    doc = tmp_path / "docs" / "reference"
    doc.mkdir(parents=True)
    target = doc / "commands.md"
    target.write_text("# Commands\n\nbase line\n", encoding="utf-8", newline="")
    git("add", "-A"); git("commit", "-qm", "base")

    git("checkout", "-q", "-b", "feature")
    target.write_text("# Commands\n\nbase line\n\nOURS BLOCK\n", encoding="utf-8", newline="")
    git("add", "-A"); git("commit", "-qm", "ours")

    git("checkout", "-q", "main")
    target.write_text("# Commands\n\nbase line\n\nTHEIRS BLOCK\n", encoding="utf-8", newline="")
    git("add", "-A"); git("commit", "-qm", "theirs")

    git("checkout", "-q", "feature")
    reb = git("rebase", "main")
    assert reb.returncode != 0, "expected a real conflict to resolve"

    notes = rr._auto_resolve_conflicts(str(tmp_path), None)
    assert notes, "the resolver should have handled an additive doc conflict"

    cont = git("-c", "core.editor=true", "rebase", "--continue")
    assert cont.returncode == 0, cont.stderr

    final = target.read_text(encoding="utf-8")
    assert "OURS BLOCK" in final and "THEIRS BLOCK" in final
    assert "<<<<<<<" not in final


# ── from the real conflict on docs/security/sandbox-coverage.md (2026-08-10) ──
_TEMPLATE = (
    "- **Decision:** **bypass-documented**\n"
    "- **Guardrails:**\n"
)


def _doc(*entries: str) -> str:
    """A document with a fixed per-entry template, like the real one."""
    return "".join(f"### Gap {i} — Entry {i}\n{_TEMPLATE}\n" for i in range(1, 6)) + "".join(entries)


def test_shared_TEMPLATE_lines_do_not_block_a_resolution():
    """The real conflict was two independently-added `### Gap 57` blocks that
    shared exactly two lines — both boilerplate every entry in the file carries.
    Treating those as evidence of a rewrite meant the one document this module
    was written for was the one document it could never resolve."""
    text = _doc(
        "<<<<<<< HEAD\n### Gap 57 — Mine\n" + _TEMPLATE +
        "=======\n### Gap 57 — Theirs\n" + _TEMPLATE +
        ">>>>>>> branch\n")
    out = cr.resolve_text("docs/security/sandbox-coverage.md", text)
    assert out is not None, "template-only overlap must not block"
    merged, notes = out
    assert "Mine" in merged and "Theirs" in merged
    assert "<<<<<<<" not in merged


def test_a_hunk_of_PURE_boilerplate_is_still_declined():
    """If nothing in the hunk is distinguishable content there is no evidence
    the two sides are independent additions rather than one rewrite."""
    text = _doc("<<<<<<< HEAD\n" + _TEMPLATE + "=======\n" + _TEMPLATE + ">>>>>>> b\n")
    assert cr.resolve_text("docs/security/sandbox-coverage.md", text) is None


def test_a_genuinely_shared_CONTENT_line_still_blocks():
    """The safety property. Boilerplate is excused because the format demands it;
    a substantive line appearing on both sides is still two people editing one
    thing, and that belongs to a human."""
    text = _doc(
        "<<<<<<< HEAD\n### Gap 57 — Mine\nThe module validates every path.\n"
        "=======\n### Gap 57 — Theirs\nThe module validates every path.\n>>>>>>> b\n")
    assert cr.resolve_text("docs/security/sandbox-coverage.md", text) is None


def test_renumbering_NEVER_touches_a_heading_outside_the_merge():
    """An unrestricted pass rewrote TWELVE headings on the real document,
    turning `### Gap 13 — DataBridge Secret Resolvers` into Gap 59 six hundred
    lines from anything being merged. Pre-existing duplicates are not this
    resolution's business — renumbering them breaks cross-references silently."""
    text = (
        "### Gap 3 — Pre-existing dupe A\n" + _TEMPLATE + "\n"
        "### Gap 3 — Pre-existing dupe B\n" + _TEMPLATE + "\n"
        + _doc(
            "<<<<<<< HEAD\n### Gap 9 — Mine\n" + _TEMPLATE +
            "=======\n### Gap 9 — Theirs\n" + _TEMPLATE + ">>>>>>> b\n"))
    out = cr.resolve_text("docs/security/sandbox-coverage.md", text)
    assert out is not None
    merged, notes = out
    assert merged.count("### Gap 3 — Pre-existing dupe A") == 1
    assert merged.count("### Gap 3 — Pre-existing dupe B") == 1
    assert not any("Gap 3 ->" in n for n in notes), \
        f"a pre-existing duplicate was renumbered: {notes}"
    # Gap 9 is claimed by neither side beforehand, so the first occurrence keeps
    # it and only the second moves — exactly what happened on the real file.
    assert sum("Gap 9 ->" in n for n in notes) == 1, notes
