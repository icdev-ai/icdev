# CUI // SP-CTI
"""mfx-sib-03: pr_watcher gains a union rung for DECLARED append-shaped files.

What is load-bearing, and asserted here:

  (a) rules are chosen BY FILE from the declared table, never by content -- an
      undeclared unmerged file refuses, and the quoted-list rule applied to a
      TypeScript object literal (the wrong rule that shipped a broken spec on
      2026-09-03) refuses instead of producing a line;
  (b) each rule does exactly its union: main's tokens plus the card's on a
      quoted list line, both route blocks, both table rows, the other side
      whole when one side is empty, and adjacent edits of different lines;
  (c) the resolution is VERIFIED before it counts, and a failed verifier puts
      the conflict back rather than leaving a half-resolution;
  (d) the rung runs inside `rebase_recovery.rebase_and_push` after the doc
      resolver and before the abort, against a REAL git rebase;
  (e) `pr_watcher._maybe_rebase` writes `union_resolved` / `union_refused`
      audit rows naming the rules, and hands the config table to the rebase
      without breaking an older three-argument stub.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci import pr_watcher as pw  # noqa: E402
from tools.kanban import rebase_recovery as rr  # noqa: E402
from tools.kanban import union_resolver as ur  # noqa: E402
from tests.ci.test_pr_watcher_rebase import _build, _dirty_pr_state  # noqa: E402


def L(text: str):
    return text.splitlines(keepends=True)


# ── (a) by file, never by content ────────────────────────────────────────────
def test_undeclared_unmerged_file_refuses_before_any_write():
    calls = []

    def runner(cmd, **kw):
        calls.append(list(cmd))
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(returncode=0, stdout="tools/kanban/task_factory.py\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    cfg = {"enabled": True, "files": [{"path": "tools/*/blueprint.py", "rules": ["keep_both_blocks"]}]}
    out = ur.resolve_index_conflicts(".", cfg, runner=runner)
    assert out.outcome == "refused"
    assert "undeclared" in out.reason and "task_factory.py" in out.reason
    assert not any(c[1] == "add" for c in calls), "a refused file must never be staged"


def test_quoted_list_rule_refuses_a_typescript_object_literal():
    """The wrong rule of 2026-09-03: two cards migrated ADJACENT rows of a
    `{ label, path }` list. A quoted-list rule has no business there -- the
    hunk is two lines, not one -- so it must refuse, not fabricate a line."""
    base = L("const P = [\n  { label: 'A', path: '/a' },\n  { label: 'B', path: '/b' },\n];\n")
    main = L("const P = [\n  { label: 'A', path: '/x/a' },\n  { label: 'B', path: '/b' },\n];\n")
    card = L("const P = [\n  { label: 'A', path: '/a' },\n  { label: 'B', path: '/x/b' },\n];\n")
    with pytest.raises(ur.UnionRefused):
        ur.merge_three_way(base, main, card, rules=["quoted_list_line"])


def test_unknown_rule_name_in_the_table_refuses():
    def runner(cmd, **kw):
        if cmd[1:3] == ["diff", "--name-only"]:
            return SimpleNamespace(returncode=0, stdout="tools/x/blueprint.py\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    cfg = {"files": [{"path": "tools/*/blueprint.py", "rules": ["guess_from_content"]}]}
    out = ur.resolve_index_conflicts(".", cfg, runner=runner)
    assert out.outcome == "refused" and "unknown rule" in out.reason


def test_mirror_paths_match_the_canonical_declaration():
    decls = [{"path": "tools/*/blueprint.py", "rules": ["keep_both_blocks"]},
             {"path": "tools/dashboard/templates/**/*.html", "rules": ["adjacent_edits"]}]
    assert ur.match_declaration("icdev/tools/security_canvas/blueprint.py", decls) is decls[0]
    assert ur.match_declaration("tools/dashboard/templates/boundary_canvas/compliance_hub.html", decls) is decls[1]
    # `*` stays within one segment: a nested blueprint is NOT the declared one.
    assert ur.match_declaration("tools/a/b/blueprint.py", decls) is None


def test_declared_table_covers_the_measured_sibling_series():
    cfg = ur.load_declared_rules()
    assert cfg.get("enabled") is True
    decls = cfg["files"]
    for path in (
        "tools/boundary_canvas/blueprint.py",
        "icdev/tools/security_canvas/blueprint.py",
        "tools/dashboard/app.py",
        "tools/dashboard/templates/base.html",
        "tools/dashboard/templates/boundary_canvas/compliance_hub.html",
        "tests/e2e/key_pages_smoke.spec.ts",
        "tests/e2e_ui_full_coverage.py",
        ".claude/commands/start.md",
        "docs/features/rmf-ui-compliance-route-migration.md",
    ):
        decl = ur.match_declaration(path, decls)
        assert decl is not None, path
        assert set(decl["rules"]) <= ur.DECLARABLE_RULES, (path, decl["rules"])
    # Not everything is append-shaped. The seeder is a disagreement about behaviour.
    assert ur.match_declaration("tools/kanban/task_factory.py", decls) is None
    assert ur.match_declaration("tools/ci/pr_watcher.py", decls) is None


# ── (b) each rule is exactly its union ───────────────────────────────────────
def test_quoted_list_line_is_mains_tokens_plus_the_cards_new_one():
    base = L("<a class=\"{% if request.path in ['/a','/b','/c'] %}active{% endif %}\">\n")
    main = L("<a class=\"{% if request.path in ['/a','/b','/m','/c'] %}active{% endif %}\">\n")
    card = L("<a class=\"{% if request.path in ['/a','/b','/c','/k'] %}active{% endif %}\">\n")
    merged, notes = ur.merge_three_way(base, main, card, rules=["quoted_list_line"])
    assert merged == L("<a class=\"{% if request.path in ['/a','/b','/m','/c','/k'] %}active{% endif %}\">\n")
    assert notes == ["quoted_list_line@1"]


def test_quoted_list_line_on_the_real_compliance_dropdown_line():
    """The live line: the list sits INSIDE `class="..."`, so under the wrong
    quote kind the whole attribute is one token each side rewrote. The rule
    must find the single-quoted list and union THAT."""
    html = (ROOT / "tools" / "dashboard" / "templates" / "base.html").read_text(encoding="utf-8")
    line = next(ln for ln in html.splitlines(keepends=True)
                if "request.path in [" in ln and "Compliance" in ln)
    anchor = "'/boundary/poam',"
    assert anchor in line
    main = line.replace(anchor, anchor + "'/boundary/main-card',", 1)
    card = line.replace(anchor, anchor + "'/boundary/card-card',", 1)
    merged, notes = ur.merge_three_way([line], [main], [card], rules=["quoted_list_line"])
    assert notes == ["quoted_list_line@1"]
    got = merged[0]
    assert "'/boundary/main-card'" in got and "'/boundary/card-card'" in got
    assert got.count("class=") == 1 and got.count("Compliance") == line.count("Compliance")
    assert len(got) == len(line) + len("'/boundary/main-card',") + len("'/boundary/card-card',")


def test_quoted_list_line_refuses_when_the_text_around_the_list_changed():
    base = L("x = ['a']\n")
    main = L("x = ['a', 'm']\n")
    card = L("y = ['a', 'k']\n")
    with pytest.raises(ur.UnionRefused):
        ur.merge_three_way(base, main, card, rules=["quoted_list_line"])


def test_keep_both_blocks_takes_main_then_card_and_still_parses():
    base = L("def create():\n    a = 1\n\n    return a\n")
    main = L("def create():\n    a = 1\n\n    @bp.route('/m')\n    def m():\n        return 1\n\n    return a\n")
    card = L("def create():\n    a = 1\n\n    @bp.route('/k')\n    def k():\n        return 2\n\n    return a\n")
    merged, notes = ur.merge_three_way(base, main, card, rules=["keep_both_blocks"])
    text = "".join(merged)
    ast.parse(text)
    assert text.index("'/m'") < text.index("'/k'")
    assert notes == ["keep_both_blocks@4"]


def test_keep_both_blocks_refuses_a_rewrite_of_existing_lines():
    base, main, card = L("a\nb\nc\n"), L("a\nb1\nc\n"), L("a\nb2\nc\n")
    with pytest.raises(ur.UnionRefused):
        ur.merge_three_way(base, main, card, rules=["keep_both_blocks"])


def test_table_rows_keeps_both_rows_and_drops_a_duplicate():
    base = L("| id | old |\n| a | 1 |\n\ntext\n")
    main = L("| id | old |\n| a | 1 |\n| m | 2 |\n| both | 9 |\n\ntext\n")
    card = L("| id | old |\n| a | 1 |\n| both | 9 |\n| k | 3 |\n\ntext\n")
    merged, notes = ur.merge_three_way(base, main, card, rules=["table_rows"])
    assert merged == L("| id | old |\n| a | 1 |\n| m | 2 |\n| both | 9 |\n| k | 3 |\n\ntext\n")
    assert notes == ["table_rows@3"]


def test_an_empty_side_resolves_to_the_other_on_any_declared_file():
    base, main, card = L("a\nb\nc\n"), L("a\nc\n"), L("a\nB\nc\n")
    merged, notes = ur.merge_three_way(base, main, card, rules=["keep_both_blocks"])
    assert merged == L("a\nB\nc\n")
    assert notes == ["other_side_when_empty@2"]


def test_adjacent_edits_of_different_lines_merge_only_when_declared():
    base = L("const P = [\n  { label: 'A', path: '/a' },\n  { label: 'B', path: '/b' },\n];\n")
    main = L("const P = [\n  { label: 'A', path: '/x/a' },\n  { label: 'B', path: '/b' },\n];\n")
    card = L("const P = [\n  { label: 'A', path: '/a' },\n  { label: 'B', path: '/x/b' },\n];\n")
    merged, notes = ur.merge_three_way(base, main, card, rules=["adjacent_edits"])
    assert merged == L("const P = [\n  { label: 'A', path: '/x/a' },\n  { label: 'B', path: '/x/b' },\n];\n")
    assert notes == ["adjacent_edits@3"]
    with pytest.raises(ur.UnionRefused):
        ur.merge_three_way(base, main, card, rules=["keep_both_blocks"])


def test_an_insertion_at_the_seam_is_still_a_conflict_under_adjacent_edits():
    """Main rewrote line 2; the card inserted a line right after it. Which
    comes first is a judgement, so adjacent_edits must not claim it."""
    base, main, card = L("a\nb\nc\n"), L("a\nB\nc\n"), L("a\nb\nk\nc\n")
    with pytest.raises(ur.UnionRefused):
        ur.merge_three_way(base, main, card, rules=["adjacent_edits"])


def test_identical_changes_are_taken_once():
    base, main, card = L("a\nc\n"), L("a\nb\nc\n"), L("a\nb\nc\n")
    merged, notes = ur.merge_three_way(base, main, card, rules=["keep_both_blocks"])
    assert merged == L("a\nb\nc\n") and notes == []


def test_delimiter_balance_sees_a_test_cut_mid_body():
    whole = "test('a', async ({ page }) => {\n  const r = await page.get('/x'); // ok }\n  expect(r).toBe('}');\n});\n"
    assert ur.delimiter_balance(whole) is None
    cut = "test('a', async ({ page }) => {\n  const r = await page.get('/x');\n\ntest('b', () => {\n});\n"
    fault = ur.delimiter_balance(cut)
    assert fault and "never closed" in fault


# ── real git plumbing ────────────────────────────────────────────────────────
def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


BASE_BP = "def create_blueprint(bp):\n    bp.marker = 1\n\n    return bp\n"
MAIN_BP = ("def create_blueprint(bp):\n    bp.marker = 1\n\n"
           "    @bp.route('/m')\n    def m_page():\n        return 'm'\n\n    return bp\n")
CARD_BP = ("def create_blueprint(bp):\n    bp.marker = 1\n\n"
           "    @bp.route('/k')\n    def k_page():\n        return 'k'\n\n    return bp\n")
REL = "tools/x/blueprint.py"
RULES = {"enabled": True, "files": [{"path": "tools/*/blueprint.py", "rules": ["keep_both_blocks"]}]}


def _conflicted_repo(tmp_path):
    """A repo mid-rebase with REL unmerged: main appended one block, the card another."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    target = repo / REL
    target.parent.mkdir(parents=True)
    target.write_text(BASE_BP, encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "base", cwd=repo)
    _git("checkout", "-q", "-b", "kanban/t-01", cwd=repo)
    target.write_text(CARD_BP, encoding="utf-8")
    _git("commit", "-qam", "card", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)
    target.write_text(MAIN_BP, encoding="utf-8")
    _git("commit", "-qam", "main", cwd=repo)
    _git("checkout", "-q", "kanban/t-01", cwd=repo)
    reb = _git("rebase", "main", cwd=repo)
    assert reb.returncode != 0, "the fixture must actually conflict"
    assert "<<<<<<<" in target.read_text(encoding="utf-8")
    return repo


def test_resolves_a_real_rebase_conflict_and_the_rebase_continues(tmp_path):
    repo = _conflicted_repo(tmp_path)
    out = ur.resolve_index_conflicts(str(repo), RULES)
    assert out.outcome == "resolved", out.reason
    assert out.files == [REL]
    assert out.rules_used == [f"{REL}:keep_both_blocks@4"]
    assert f"{REL}:py_ast" in out.verifiers and f"{REL}:ruff" in out.verifiers
    assert "diff_check" in out.verifiers
    text = (repo / REL).read_text(encoding="utf-8")
    assert "'/m'" in text and "'/k'" in text and "<<<<<<<" not in text
    ast.parse(text)
    cont = _git("-c", "core.editor=true", "rebase", "--continue", cwd=repo)
    assert cont.returncode == 0, cont.stderr
    assert _git("diff", "--name-only", "--diff-filter=U", cwd=repo).stdout.strip() == ""


def test_dry_run_resolves_in_memory_and_writes_nothing(tmp_path):
    repo = _conflicted_repo(tmp_path)
    out = ur.resolve_index_conflicts(str(repo), RULES, dry_run=True)
    assert out.outcome == "resolved" and out.rules_used
    assert "<<<<<<<" in (repo / REL).read_text(encoding="utf-8")


# ── (c) verify before it counts; a failure puts the conflict back ────────────
def test_a_failed_verifier_refuses_and_restores_the_conflict(tmp_path):
    repo = _conflicted_repo(tmp_path)

    def ruff_says_no(cmd, **kw):
        assert "ruff" in cmd
        return SimpleNamespace(returncode=1, stdout=f"{REL}:1:1: F821 undefined name\n", stderr="")

    out = ur.resolve_index_conflicts(str(repo), RULES, verify_runner=ruff_says_no)
    assert out.outcome == "refused"
    assert "ruff refused" in out.reason and "F821" in out.reason
    # The half-resolution is gone: the file is conflicted again, exactly as a
    # human would find it, and nothing is staged.
    assert "<<<<<<<" in (repo / REL).read_text(encoding="utf-8")
    assert _git("diff", "--name-only", "--diff-filter=U", cwd=repo).stdout.strip() == REL


def test_a_verifier_that_cannot_run_is_unmeasured_and_refuses(tmp_path):
    repo = _conflicted_repo(tmp_path)

    def no_ruff(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="No module named ruff")

    out = ur.resolve_index_conflicts(str(repo), RULES, verify_runner=no_ruff)
    assert out.outcome == "refused" and "unmeasured" in out.reason


# ── (d) inside rebase_and_push: after the doc resolver, before the abort ─────
def _origin_and_clone(tmp_path):
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    _git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    target = work / REL
    target.parent.mkdir(parents=True)
    target.write_text(BASE_BP, encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "base", cwd=work)
    _git("push", "-q", "-u", "origin", "main", cwd=work)
    _git("checkout", "-q", "-b", "kanban/t-01", cwd=work)
    target.write_text(CARD_BP, encoding="utf-8")
    _git("commit", "-qam", "card", cwd=work)
    _git("push", "-q", "-u", "origin", "kanban/t-01", cwd=work)
    _git("checkout", "-q", "main", cwd=work)
    target.write_text(MAIN_BP, encoding="utf-8")
    _git("commit", "-qam", "main", cwd=work)
    _git("push", "-q", "origin", "main", cwd=work)
    return work


def test_rebase_and_push_union_resolves_a_real_conflict_it_used_to_abort_on(tmp_path):
    work = _origin_and_clone(tmp_path)
    # Rung OFF: the same conflict aborts exactly as before this card.
    before = rr.rebase_and_push("t-01", "kanban/t-01", base="main", repo_root=str(work),
                                dry_run=True, union_rules=False)
    assert before["conflict"] is True and before["union"] is None
    assert "hit conflicts" in before["reason"]
    # Rung ON: resolved, verified, and the dry run reports the rules used.
    after = rr.rebase_and_push("t-01", "kanban/t-01", base="main", repo_root=str(work),
                               dry_run=True, union_rules=RULES)
    assert after["conflict"] is False, after["reason"]
    assert after["attempted"] is True and after["pushed"] is False
    assert after["commits_ahead"] == 1
    assert after["union"]["outcome"] == "resolved"
    assert after["union"]["rules_used"] == [f"{REL}:keep_both_blocks@4"]
    assert f"{REL}:ruff" in after["union"]["verifiers"]


def test_rebase_and_push_reports_the_refusal_on_an_undeclared_file(tmp_path):
    work = _origin_and_clone(tmp_path)
    cfg = {"enabled": True, "files": [{"path": "docs/*.md", "rules": ["table_rows"]}]}
    verdict = rr.rebase_and_push("t-01", "kanban/t-01", base="main", repo_root=str(work),
                                 dry_run=True, union_rules=cfg)
    assert verdict["conflict"] is True
    assert verdict["union"]["outcome"] == "refused"
    assert "undeclared" in verdict["union"]["reason"]
    assert "union rung" in verdict["reason"]
    # The remote branch was never touched.
    assert _git("rev-parse", "origin/kanban/t-01", cwd=work).stdout == \
        _git("rev-parse", "kanban/t-01", cwd=work).stdout


def test_rung_order_is_doc_resolver_then_union_then_abort():
    src = pathlib.Path(rr.__file__).read_text(encoding="utf-8")
    body = src[src.index("def rebase_and_push("):]
    doc = body.index("_auto_resolve_conflicts(tmp, runner)")
    union = body.index("_union_resolve(tmp, union_rules, runner)")
    abort = body.index('"rebase", "--abort"', union)
    assert doc < union < abort


# ── (e) the watcher writes union_resolved / union_refused with the rules ─────
def _union_verdict(outcome, **extra):
    union = {"outcome": outcome, "files": [REL], "rules_used": [f"{REL}:keep_both_blocks@4"],
             "verifiers": [f"{REL}:ruff"], "tests": [], "reason": extra.get("reason", "")}
    return {"attempted": True, "pushed": outcome == "resolved", "conflict": outcome != "resolved",
            "reason": "x", "union": union}


def test_maybe_rebase_audits_union_resolved_with_the_rule_names():
    seen = {}

    def stub(task_id, branch, **kw):
        seen.update(kw)
        return _union_verdict("resolved")

    cfg = {"union_resolver": {"enabled": True, "files": [{"path": "tools/*/blueprint.py", "rules": ["keep_both_blocks"]}]}}
    watcher, rows, _log = _build("mfx-sib-03", stub, config=cfg)
    task = {"id": "mfx-sib-03", "executor_url": "https://github.com/o/r/pull/1300"}
    verdict = watcher._maybe_rebase(task, _dirty_pr_state("kanban/mfx-sib-03"))  # noqa: SLF001
    assert verdict["pushed"] is True
    assert seen["union_rules"] == cfg["union_resolver"], "the declared table reaches the rebase"
    actions = [r["action"] for r in rows]
    assert "pr_watcher.union_resolved" in actions
    row = next(r for r in rows if r["action"] == "pr_watcher.union_resolved")
    assert "keep_both_blocks" in row["details"] and REL in row["details"]
    assert "pr_watcher.union_refused" not in actions


def test_maybe_rebase_audits_union_refused_with_the_reason():
    def stub(task_id, branch, **kw):
        return _union_verdict("refused", reason="undeclared: tools/y.py")

    watcher, rows, _log = _build("mfx-sib-03", stub, config={"union_resolver": {"enabled": True, "files": []}})
    task = {"id": "mfx-sib-03", "executor_url": "https://github.com/o/r/pull/1300"}
    verdict = watcher._maybe_rebase(task, _dirty_pr_state("kanban/mfx-sib-03"))  # noqa: SLF001
    assert verdict["pushed"] is False
    row = next(r for r in rows if r["action"] == "pr_watcher.union_refused")
    assert "undeclared: tools/y.py" in row["details"]
    assert "pr_watcher.union_resolved" not in [r["action"] for r in rows]


def test_maybe_rebase_writes_no_union_row_when_the_rung_never_ran():
    def stub(task_id, branch, **kw):
        return {"attempted": True, "pushed": True, "conflict": False, "reason": "clean", "union": None}

    watcher, rows, _log = _build("mfx-sib-03", stub, config={"union_resolver": {"enabled": True, "files": []}})
    task = {"id": "mfx-sib-03", "executor_url": "https://github.com/o/r/pull/1300"}
    watcher._maybe_rebase(task, _dirty_pr_state("kanban/mfx-sib-03"))  # noqa: SLF001
    assert not [r for r in rows if r["action"].startswith("pr_watcher.union_")]


def test_an_older_three_argument_rebase_stub_is_not_broken_by_the_table():
    def old_stub(task_id, branch, base):
        return {"attempted": True, "pushed": True, "conflict": False, "reason": "ok"}

    watcher, rows, _log = _build("mfx-sib-03", old_stub, config={"union_resolver": {"enabled": True, "files": []}})
    task = {"id": "mfx-sib-03", "executor_url": "https://github.com/o/r/pull/1300"}
    verdict = watcher._maybe_rebase(task, _dirty_pr_state("kanban/mfx-sib-03"))  # noqa: SLF001
    assert verdict["pushed"] is True
    assert pw._accepts_kwarg(old_stub, "union_rules") is False  # noqa: SLF001
    assert pw._accepts_kwarg(rr.rebase_and_push, "union_rules") is True  # noqa: SLF001
