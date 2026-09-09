# CUI // SP-CTI
"""kpr-watch-14: CLAUDE.md is declared, and its GENERATED copy is derived, not unioned.

`CLAUDE.md` was the single largest cause of `pr_watcher.union_refused` -- 35 of
the 63 lifetime `undeclared:` refusals, 55.6% -- and every one of those 35
conflict sets ALSO carried `icdev/data/claude_bootstrap/CLAUDE.md`, so
declaring CLAUDE.md on its own would have resolved none of them.

What is load-bearing, and asserted here:

  (a) both halves of the pair are declared, and the payload declares NO rules --
      it is a verbatim copy, so the only correct resolution is its source's
      bytes. Two independent unions of two files that must be byte-identical is
      how a branch lands out of `bootstrap_parity`;
  (b) the derivation is what `prebuild_bootstrap` does for this entry -- proven
      by running that module's own copier, not by asserting it in prose;
  (c) a derivation REFUSES rather than guessing: an unmerged-and-unresolved
      source, an absent source. `None` is never quietly resolved to something;
  (d) the module stays ALL-OR-NOTHING. Partial resolution was measured and
      REFUSED (see the docstring below), so a mixed set must still write
      nothing at all;
  (e) the survey that licensed the declaration keeps its own honesty: an
      unmeasurable run reports `lost_content: None`, never 0.

WHY THERE IS NO PARTIAL RESOLUTION, measured rather than argued. 10 of the 63
refusals were a DECLARED file refused because an undeclared sibling shared its
set. Resolving the declared subset buys nothing: `rebase_recovery` runs the
whole rebase in a `tempfile.mkdtemp` worktree and calls `git rebase --abort`
followed by `_cleanup` on any outcome other than `resolved`, so a
half-resolution is deleted seconds later and no human ever sees it -- and the
undeclared files stay unmerged, so `rebase --continue` cannot proceed either.
Zero rebases completed, zero human effort saved, in exchange for the invariant
`resolve_index_conflicts` exists to hold.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import claude_md_union_survey as survey_mod  # noqa: E402
from tools.kanban import union_resolver as ur  # noqa: E402

CLAUDE = "CLAUDE.md"
PAYLOAD = "icdev/data/claude_bootstrap/CLAUDE.md"


def _declarations():
    cfg = yaml.safe_load((ROOT / "args" / "pr_watcher_config.yaml").read_text(encoding="utf-8"))
    return ((cfg or {}).get("union_resolver") or {}).get("files") or []


# ── (a) the pair is declared, and only one half carries rules ────────────────
def test_claude_md_is_declared_with_the_append_a_block_rule():
    decl = ur.match_declaration(CLAUDE, _declarations())
    assert decl is not None, "CLAUDE.md is still undeclared -- the 55.6% cause"
    assert "keep_both_blocks" in ur._declared_rules(decl, CLAUDE)


def test_the_generated_payload_is_derived_and_declares_no_rules():
    decl = ur.match_declaration(PAYLOAD, _declarations())
    assert decl is not None, "the bootstrap payload is undeclared; the set still refuses"
    assert ur.derived_source(decl) == CLAUDE
    assert not decl.get("rules"), "a verbatim copy must not be unioned independently"


def test_the_two_declarations_do_not_capture_each_other():
    """`CLAUDE.md` is anchored, so it cannot claim the payload and invert the pair."""
    assert ur.match_declaration(PAYLOAD, _declarations()) is not ur.match_declaration(
        CLAUDE, _declarations())
    assert ur.canonical_path(PAYLOAD) == PAYLOAD, "the payload is not a mirror path"


# ── (b) the derivation IS prebuild_bootstrap's regeneration for this entry ───
def test_prebuild_regenerates_this_payload_with_a_verbatim_copy(tmp_path, monkeypatch):
    """If the payload ever stops being a byte copy, copying is the wrong repair.

    CRLF on purpose: a copier that "helpfully" normalises line endings is not a
    copier, and the parity check would then refuse every rebase.
    """
    from tools.installer import prebuild_bootstrap as pb

    assert (CLAUDE, CLAUDE, "file") in pb.SOURCES
    monkeypatch.setattr(pb, "REPO_ROOT", tmp_path)
    src = tmp_path / "CLAUDE.md"
    src.write_text("# rules\r\nline\n\nblock\n", encoding="utf-8", newline="")
    dst = tmp_path / "out" / "CLAUDE.md"
    pb._copy_file(src, dst)
    assert dst.read_bytes() == src.read_bytes()


# ── real git plumbing ────────────────────────────────────────────────────────
def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


BASE_MD = "# CLAUDE.md\n\nintro\n\n# Existing block (old-01)\nold rule\n"
MAIN_MD = BASE_MD + "\n# A block main appended (main-01)\nmain rule\n"
CARD_MD = BASE_MD + "\n# A block the card appended (card-01)\ncard rule\n"
RULES = {"enabled": True, "files": [
    {"path": CLAUDE, "rules": ["keep_both_blocks", "adjacent_edits"]},
    {"path": PAYLOAD, "derived_from": CLAUDE},
]}


def _conflicted_pair(tmp_path, *, payload_conflicts=True):
    """A repo mid-rebase where both sides appended a block to CLAUDE.md.

    `payload_conflicts` mirrors the measured reality (all 35 sets carried both)
    and, when false, the case where only the source conflicted.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    claude, payload = repo / CLAUDE, repo / PAYLOAD
    payload.parent.mkdir(parents=True)

    def write(text, also_payload):
        claude.write_text(text, encoding="utf-8", newline="")
        if also_payload:
            payload.write_text(text, encoding="utf-8", newline="")

    write(BASE_MD, True)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "base", cwd=repo)
    _git("checkout", "-q", "-b", "kanban/t-01", cwd=repo)
    write(CARD_MD, payload_conflicts)
    _git("commit", "-qam", "card", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)
    write(MAIN_MD, payload_conflicts)
    _git("commit", "-qam", "main", cwd=repo)
    _git("checkout", "-q", "kanban/t-01", cwd=repo)
    assert _git("rebase", "main", cwd=repo).returncode != 0, "the fixture must conflict"
    return repo, claude, payload


def test_the_pair_resolves_together_and_lands_byte_identical(tmp_path):
    repo, claude, payload = _conflicted_pair(tmp_path)
    out = ur.resolve_index_conflicts(str(repo), RULES)
    assert out.outcome == "resolved", out.reason
    assert set(out.files) == {CLAUDE, PAYLOAD}

    text = claude.read_text(encoding="utf-8")
    assert "main rule" in text and "card rule" in text, "a block was lost"
    assert "old rule" in text and "<<<<<<<" not in text
    # THE POINT: parity, not two plausible resolutions.
    assert payload.read_bytes() == claude.read_bytes()
    assert f"{PAYLOAD}:derived_from:{CLAUDE}" in out.rules_used
    assert f"{PAYLOAD}:derivation_parity" in out.verifiers

    cont = _git("-c", "core.editor=true", "rebase", "--continue", cwd=repo)
    assert cont.returncode == 0, cont.stderr
    assert _git("diff", "--name-only", "--diff-filter=U", cwd=repo).stdout.strip() == ""


def test_a_copy_outside_the_conflict_set_that_is_already_stale_REFUSES(tmp_path):
    """The one shape the resolver will not silently repair, and why.

    A branch can only reach this by being out of parity ALREADY -- two sides
    that each regenerated the copy would both have changed it, so it would be
    unmerged too. Writing it here would put a change into the replayed commit
    that neither side made; leaving it would land a resolved CLAUDE.md beside a
    stale copy. So it refuses, and names the repair.
    """
    repo, claude, _payload = _conflicted_pair(tmp_path, payload_conflicts=False)
    out = ur.resolve_index_conflicts(str(repo), RULES)
    assert out.outcome == "refused"
    assert "out of parity" in out.reason and PAYLOAD in out.reason
    # The conflict is PUT BACK -- a human sees the conflict, not the resolution
    # that was written and then judged wrong. (`git checkout --merge` relabels
    # the markers, so this asserts the state, not the bytes.)
    assert "<<<<<<<" in claude.read_text(encoding="utf-8")
    assert CLAUDE in _git("diff", "--name-only", "--diff-filter=U",
                          cwd=repo).stdout.split()


def test_the_orphan_check_fires_only_on_a_copy_left_out_of_the_set():
    """It is a PARITY assertion, so it must not fire when the copy was resolved."""
    decls = RULES["files"]
    assert ur._orphan_derivation_targets(decls, [CLAUDE], [CLAUDE]) == [(PAYLOAD, CLAUDE)]
    assert ur._orphan_derivation_targets(decls, [CLAUDE], [CLAUDE, PAYLOAD]) == []
    assert ur._orphan_derivation_targets(decls, [], []) == []


def test_a_derivation_declaration_must_name_one_literal_path(tmp_path):
    """A copy has exactly one source; a glob would silently claim several."""
    repo, _claude, _payload = _conflicted_pair(tmp_path)
    globbed = {"enabled": True, "files": [
        {"path": CLAUDE, "rules": ["keep_both_blocks"]},
        {"path": "icdev/data/claude_bootstrap/*.md", "derived_from": CLAUDE},
    ]}
    out = ur.resolve_index_conflicts(str(repo), globbed)
    assert out.outcome == "refused" and "literal path" in out.reason


def test_a_derivation_is_never_unioned_on_its_own_terms(tmp_path):
    """The payload's own three-way is DISCARDED -- the source's bytes win.

    Proven by making the payload's own sides disagree with the source's: a
    union of the payload would keep its `payload-only` line, a derivation
    cannot, because that line is in no version of CLAUDE.md.
    """
    repo, claude, payload = _conflicted_pair(tmp_path)
    _git("rebase", "--abort", cwd=repo)
    payload.write_text(CARD_MD + "payload-only drift\n", encoding="utf-8", newline="")
    _git("commit", "-qam", "drift", cwd=repo)
    assert _git("rebase", "main", cwd=repo).returncode != 0
    out = ur.resolve_index_conflicts(str(repo), RULES)
    assert out.outcome == "resolved", out.reason
    assert "payload-only drift" not in payload.read_text(encoding="utf-8")
    assert payload.read_bytes() == claude.read_bytes()


# ── (c) a derivation refuses rather than guessing ────────────────────────────
def test_derivation_refuses_a_source_that_is_unmerged_and_unresolved(tmp_path):
    with pytest.raises(ur.UnionRefused) as exc:
        ur._plan_derivation(PAYLOAD, CLAUDE, plans=[], unmerged=[CLAUDE], cwd=str(tmp_path))
    assert "unmerged" in str(exc.value)


def test_derivation_refuses_an_absent_source(tmp_path):
    with pytest.raises(ur.UnionRefused) as exc:
        ur._plan_derivation(PAYLOAD, CLAUDE, plans=[], unmerged=[], cwd=str(tmp_path))
    assert "not a file" in str(exc.value)


def test_derivation_parity_is_confirmed_by_RE_READING_both_files(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("a\n", encoding="utf-8", newline="")
    (tmp_path / "copy.md").write_text("b\n", encoding="utf-8", newline="")
    with pytest.raises(ur.UnionRefused) as exc:
        ur._verify_derivation("copy.md", CLAUDE, str(tmp_path))
    assert "byte-identical" in str(exc.value)


# ── (d) still all-or-nothing: a mixed set writes nothing ─────────────────────
def test_one_undeclared_sibling_still_refuses_the_whole_set_and_writes_nothing(tmp_path):
    repo, claude, payload = _conflicted_pair(tmp_path)
    stranger = repo / "docs" / "notes.md"
    stranger.parent.mkdir(parents=True, exist_ok=True)
    before = claude.read_text(encoding="utf-8")
    assert "<<<<<<<" in before

    def runner(cmd, **kw):
        # git reports an extra unmerged file no declaration claims.
        if "--diff-filter=U" in cmd:
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0, stdout=f"{CLAUDE}\ndocs/notes.md\n{PAYLOAD}\n",
                                   stderr="")
        return subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    out = ur.resolve_index_conflicts(str(repo), RULES, runner=runner)
    assert out.outcome == "refused"
    assert "undeclared: docs/notes.md" in out.reason
    assert claude.read_text(encoding="utf-8") == before, "a refused set must write nothing"


# ── (e) the survey that licensed the declaration stays honest ────────────────
def test_survey_classifies_a_lost_block_as_the_finding_not_a_reorder():
    landed = survey_mod._compare(["a\n", "b\n"], ["b\n", "a\n"])
    assert landed["verdict"] == "exact" or landed["verdict"] == "blank_lines_only"
    assert survey_mod._compare(["a\n", "b\n"], ["a\n"])["verdict"] == "union_lost_content"
    assert survey_mod._compare(["a\n"], ["a\n", "b\n"])["verdict"] == "union_kept_more"
    assert survey_mod._compare(["a\n", "\n"], ["a\n"])["verdict"] == "blank_lines_only"
    assert survey_mod._compare(["a\n"], ["a\n"])["verdict"] == "exact"


def test_an_unmeasurable_survey_reports_none_and_never_a_clean_zero(tmp_path):
    report = survey_mod.survey("CLAUDE.md", root=tmp_path, ref="refs/heads/nope")
    assert report["measurable"] is False
    assert report["lost_content"] is None, "0 findings over 0 cases is a fabricated clean bill"
    assert report["conflicting"] is None
