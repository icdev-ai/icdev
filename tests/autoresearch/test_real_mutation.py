#!/usr/bin/env python3
# CUI // SP-CTI
"""Real mutation for ONE domain, in a worktree, with lanes (xrv-lab-02).

The adapter and the evaluator are MOCKED; git is REAL. A fake git would prove
only that this file can write dicts -- "discard removes the worktree" is a
claim about `git worktree remove`, and the whole reason that door was chosen
over `shutil.rmtree` is that git refuses on a dirty tree. So every test here
runs against a real temporary repository.
"""
from __future__ import annotations

import ast
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.autoresearch import real_mutation as rm  # noqa: E402

MIGRATION_DIR = (
    _ROOT / "tools" / "db" / "migrations"
    / "20260912122759_add_experiment_candidate_lane"
)


# ── fixtures ─────────────────────────────────────────────────────────────────


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL)


@pytest.fixture()
def repo(tmp_path):
    """A real git repository with the shape the module expects.

    `tmp_path` on Windows lands under the user profile, which contains no
    whitespace on CI or on this host -- and if it ever did, `measure()` refuses
    with `project_dir_unsplittable` rather than measuring the wrong tree, which
    is itself asserted below.
    """
    root = tmp_path / "repo"
    (root / "tools" / "autoresearch").mkdir(parents=True)
    (root / "args").mkdir()
    (root / "tools" / "widget.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    # The bounds declaration this module READS rather than respells.
    (root / "args" / "genesis_config.yaml").write_text(
        "reflexes:\n"
        "  evolve:\n"
        "    max_files_per_cycle: 1\n"
        "    allowed_directories:\n"
        "      - tools/\n"
        "      - args/\n"
        "    forbidden_files:\n"
        "      - CLAUDE.md\n"
        "      - tools/db/storage.py\n",
        encoding="utf-8",
    )
    (root / "args" / "autoresearch_config.yaml").write_text(
        "real_mutation:\n"
        "  enabled: true\n"
        "  domains:\n"
        "    - code_quality\n"
        "  patch_timeout_seconds: 30\n"
        "  open_pr: false\n"
        "git:\n"
        "  branch_prefix: autoresearch/\n"
        "  worktree_base: .tmp/autoresearch/worktrees\n",
        encoding="utf-8",
    )

    _git(["init", "-b", "main"], root)
    _git(["config", "user.email", "test@example.invalid"], root)
    _git(["config", "user.name", "xrv-lab-02 test"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-m", "base"], root)
    return root


@pytest.fixture()
def cfg(repo):
    return rm._config(repo)


class FakeAdapter:
    """Writes *files* into the worktree, exactly as a patch would.

    It never touches git, which is the same contract `patch_prompt` states to
    the real adapter: the harness owns the commit so the before/after
    measurement describes a tree a reader can reconstruct.
    """

    def __init__(self, files=None, envelope=None, available=True):
        self.files = files or {}
        self.envelope = envelope
        self.available_flag = available
        self.sessions = []

    def available(self):
        return self.available_flag

    def spawn(self, session, stdout=None, stderr=None):
        self.sessions.append(session)
        work = Path(session.working_dir)
        for rel, text in self.files.items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        if self.envelope is not None and stdout is not None:
            stdout.write(self.envelope)
            stdout.flush()
        return subprocess.Popen([sys.executable, "-c", "pass"], stdout=stdout,
                                stderr=stderr, stdin=subprocess.DEVNULL)


def evaluator(values):
    """An evaluator returning *values* in order; a None entry is a FAILED run."""
    seq = list(values)

    def _evaluate(domain, **kwargs):
        value = seq.pop(0)
        if value is None:
            return {"domain": domain, "metric_name": "maintainability_score",
                    "metric_value": 0.0, "success": False, "error": "analyzer blew up"}
        return {"domain": domain, "metric_name": "maintainability_score",
                "metric_value": value, "success": True}

    return _evaluate


def keeper(decision):
    def _decide(candidate_id, pre_metric=None, post_metric=None, **kwargs):
        return {"success": True, "decision": decision,
                "metric_delta": round((post_metric or 0) - (pre_metric or 0), 6),
                "experiment_id": candidate_id}
    return _decide


# ── the card's four behavioural claims ───────────────────────────────────────


def test_before_and_after_are_both_persisted_and_differ(repo, cfg):
    """A kept run carries TWO measurements of TWO trees, not one twice."""
    result = rm.run_real_experiment(
        "exp-keep01", "Simplify tools/widget.py",
        root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter({"tools/widget.py": "def f():\n    return 2\n"}),
        evaluate_fn=evaluator([0.60, 0.85]),
        decide_fn=keeper("keep"), push=False,
    )

    assert result["outcome"] == rm.OUTCOME_KEPT
    assert result["measured"] is True
    assert result["metric_before"] == 0.60
    assert result["metric_after"] == 0.85
    # THE POINT OF THE CARD: the two numbers are not the same number.
    assert result["metric_before"] != result["metric_after"]
    # `placeholder_metrics` False is reachable ONLY from here.
    assert result["placeholder_metrics"] is False
    assert result["lane"] == rm.LANE_FRONTIER
    assert result["branch"] == "autoresearch/exp-keep01"


def test_the_two_measurements_are_of_two_different_trees(repo, cfg):
    """The BEFORE runs on an unpatched tree and the AFTER on the patched one.

    Asserting only that two numbers differ would pass for an evaluator that
    returns a counter. This reads the FILE at each call, so it fails if the
    patch lands on the wrong side of the measurement -- the defect that would
    make the delta meaningless while every other assertion still passed.
    """
    seen = []

    def _evaluate(domain, project_dir=None, **kwargs):
        seen.append(Path(project_dir).joinpath("widget.py").read_text(encoding="utf-8"))
        return {"domain": domain, "metric_name": "m",
                "metric_value": 0.5 + 0.1 * len(seen), "success": True}

    rm.run_real_experiment(
        "exp-order1", "Rewrite the body", root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter({"tools/widget.py": "def f():\n    return 99\n"}),
        evaluate_fn=_evaluate, decide_fn=keeper("discard"), push=False,
    )

    assert len(seen) == 2
    assert "return 1" in seen[0], "BEFORE was measured on a patched tree"
    assert "return 99" in seen[1], "AFTER was measured on the unpatched tree"


def test_a_failed_evaluator_leaves_the_candidate_in_incubator(repo, cfg):
    """Unmeasurable is its OWN verdict: not a discard, and never metric 0.0.

    `fitness_evaluator.evaluate` reports a failed tool as
    `metric_value: 0.0, success: False`. Reading that 0.0 as a score would make
    a broken analyzer look like a catastrophically bad patch and would move a
    posterior on it.
    """
    result = rm.run_real_experiment(
        "exp-unmeas1", "Something", root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter({"tools/widget.py": "x = 1\n"}),
        evaluate_fn=evaluator([0.60, None]),
        decide_fn=keeper("keep"), push=False,
    )

    assert result["outcome"] == rm.OUTCOME_UNMEASURABLE
    assert result["outcome"] != rm.OUTCOME_DISCARDED
    assert result["lane"] == rm.LANE_INCUBATOR
    assert result["reason"] == "post_unmeasurable"
    assert result["reason"] in rm.REFUSALS
    assert result["measured"] is False
    # NEVER 0.0 -- the number the evaluator actually handed back.
    assert result["metric_after"] is None
    assert result["metric_before"] == 0.60
    assert result["placeholder_metrics"] is None


def test_a_raising_evaluator_is_also_unmeasurable(repo, cfg):
    def _boom(domain, **kwargs):
        raise RuntimeError("analyzer exploded")

    result = rm.run_real_experiment(
        "exp-boom1", "Something", root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter(), evaluate_fn=_boom,
        decide_fn=keeper("keep"), push=False,
    )
    assert result["outcome"] == rm.OUTCOME_UNMEASURABLE
    assert result["reason"] == "base_unmeasurable"
    assert result["metric_before"] is None


def test_discard_removes_the_worktree(repo, cfg):
    """git's own door, and the directory is actually gone afterwards."""
    result = rm.run_real_experiment(
        "exp-disc01", "Make it worse", root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter({"tools/widget.py": "def f():\n    return 0\n"}),
        evaluate_fn=evaluator([0.80, 0.40]),
        decide_fn=keeper("discard"), push=False,
    )

    assert result["outcome"] == rm.OUTCOME_DISCARDED
    assert result["lane"] == rm.LANE_ARCHIVE
    assert result["worktree_removed"] is True
    assert not Path(result["worktree"]).exists()
    # git's own registry agrees, so this is not a directory left dangling.
    listing = _git(["worktree", "list"], repo).stdout
    assert "exp-disc01" not in listing


def test_a_kept_run_commits_and_never_merges(repo, cfg):
    """Commit, then the PR opener -- and nothing merges, at any level."""
    calls = []

    def _open_pr(worktree, branch, **kwargs):
        calls.append({"branch": branch, "title_domain": kwargs.get("domain")})
        return {"opened": True, "url": "https://example.invalid/pr/1",
                "title": f"autoresearch({kwargs.get('domain')}): x"}

    result = rm.run_real_experiment(
        "exp-pr01", "Improve widget", root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter({"tools/widget.py": "def f():\n    return 3\n"}),
        evaluate_fn=evaluator([0.50, 0.90]),
        decide_fn=keeper("keep"), open_pr_fn=_open_pr, push=False,
    )

    assert result["outcome"] == rm.OUTCOME_KEPT
    assert result["commit"]["committed"] is True
    assert result["commit"]["commit"]
    # push=False, so the PR is not attempted and says so rather than claiming one.
    assert calls == []
    assert result["pr"]["opened"] is False
    assert result["pr"]["reason"] == "push_skipped"


def test_a_patch_that_changes_nothing_is_discarded_not_kept(repo, cfg):
    result = rm.run_real_experiment(
        "exp-noop01", "Do nothing", root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter({}), evaluate_fn=evaluator([0.7]),
        decide_fn=keeper("keep"), push=False,
    )
    assert result["outcome"] == rm.OUTCOME_DISCARDED
    assert result["reason"] == "no_patch_produced"
    assert result["lane"] == rm.LANE_ARCHIVE
    assert not Path(result["worktree"]).exists()


def test_a_patch_outside_the_declared_bounds_is_discarded(repo, cfg):
    """The bounds are the evolve reflex's OWN declaration, read not respelled."""
    result = rm.run_real_experiment(
        "exp-oob01", "Touch the root", root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter({"CLAUDE.md": "pwned\n"}),
        evaluate_fn=evaluator([0.7, 0.9]),
        decide_fn=keeper("keep"), push=False,
    )
    assert result["outcome"] == rm.OUTCOME_DISCARDED
    assert result["reason"] == "patch_out_of_bounds"
    assert result["bounds_check"]["out_of_bounds"] == ["CLAUDE.md"]
    assert not Path(result["worktree"]).exists()


def test_a_forbidden_file_under_an_allowed_directory_is_still_refused():
    """`tools/db/storage.py` IS under `tools/`. Forbidden must win."""
    bounds = {"allowed_directories": ["tools/", "args/"],
              "forbidden_files": ["CLAUDE.md", "tools/db/storage.py"]}
    split = rm.classify_paths(
        ["tools/widget.py", "tools/db/storage.py", "CLAUDE.md", "docs/x.md"], bounds)
    assert split["in_bounds"] == ["tools/widget.py"]
    assert set(split["out_of_bounds"]) == {"tools/db/storage.py", "CLAUDE.md", "docs/x.md"}


# ── the gates ────────────────────────────────────────────────────────────────


def test_the_shipped_default_is_off_and_says_which_switch():
    """DEFAULT FALSE on the tree as committed, with a NAMED basis."""
    gate = rm.real_mutation_gate("code_quality", env={})
    assert gate["enabled"] is False
    assert gate["basis"] == "disabled_by_config"
    assert gate["basis"] in rm.REFUSALS


def test_a_domain_outside_the_python_list_is_a_different_verdict():
    """A list and a switch are different facts and are never merged."""
    for domain in ("security", "compliance", "rag_quality"):
        gate = rm.real_mutation_gate(domain, env={})
        assert gate["enabled"] is False
        assert gate["basis"] == "domain_not_enabled"


def test_config_can_narrow_the_domain_list_but_never_widen_it():
    """Widening autonomous code mutation must not be a YAML edit."""
    widened = {"real_mutation": {"enabled": True,
                                 "domains": ["code_quality", "security", "compliance"]}}
    assert rm.mutation_domains(widened) == ("code_quality",)
    assert rm.real_mutation_gate("security", env={}, config=widened)["basis"] == \
        "domain_not_enabled"

    narrowed = {"real_mutation": {"enabled": True, "domains": []}}
    assert rm.mutation_domains(narrowed) == ()
    assert rm.real_mutation_gate("code_quality", env={}, config=narrowed)["basis"] == \
        "domain_not_enabled"


def test_an_unreadable_config_is_fail_closed_and_not_consent(tmp_path):
    empty = tmp_path / "no_such_checkout"
    empty.mkdir()
    gate = rm.real_mutation_gate("code_quality", env={}, root=empty)
    assert gate["enabled"] is False
    assert gate["basis"] == "config_unreadable"
    assert gate["config_enabled"] is None, "declared-off and could-not-tell must differ"


def test_the_env_override_works_in_both_directions():
    cfg_on = {"real_mutation": {"enabled": True, "domains": ["code_quality"]}}
    cfg_off = {"real_mutation": {"enabled": False, "domains": ["code_quality"]}}
    assert rm.real_mutation_gate("code_quality", env={"ICDEV_AUTORESEARCH_REAL_MUTATION": "0"},
                                 config=cfg_on)["basis"] == "disabled_by_env"
    assert rm.real_mutation_gate("code_quality", env={"ICDEV_AUTORESEARCH_REAL_MUTATION": "1"},
                                 config=cfg_off)["enabled"] is True


def test_a_closed_gate_refuses_and_touches_nothing(repo, tmp_path):
    off = {"real_mutation": {"enabled": False, "domains": ["code_quality"]},
           "git": {"worktree_base": ".tmp/autoresearch/worktrees"}}
    result = rm.run_real_experiment("exp-off01", "anything", root=repo, config=off,
                                    base_ref="main", adapter=FakeAdapter(),
                                    evaluate_fn=evaluator([0.5]), push=False)
    assert result["outcome"] == rm.OUTCOME_REFUSED
    assert result["reason"] == "disabled_by_config"
    assert not (repo / ".tmp" / "autoresearch").exists(), "a refused run created a worktree"


def test_unreadable_bounds_refuse_rather_than_defaulting_permissive(tmp_path):
    """A half-read declaration permits exactly the files it was written to protect."""
    assert rm.evolve_bounds(tmp_path)["readable"] is False
    partial = tmp_path / "partial"
    (partial / "args").mkdir(parents=True)
    (partial / "args" / "genesis_config.yaml").write_text(
        "reflexes:\n  evolve:\n    allowed_directories:\n      - tools/\n", encoding="utf-8")
    bounds = rm.evolve_bounds(partial)
    assert bounds["readable"] is False, "an empty forbidden_files must not read as permissive"


def test_the_live_bounds_are_the_evolve_reflexs_own_declaration():
    """Read from args/genesis_config.yaml, never a second copy in this module."""
    bounds = rm.evolve_bounds()
    assert bounds["readable"] is True
    assert "tools/" in bounds["allowed_directories"]
    assert "CLAUDE.md" in bounds["forbidden_files"]
    assert "tools/db/storage.py" in bounds["forbidden_files"]


# ── measurement rails ────────────────────────────────────────────────────────


def test_a_failed_evaluation_is_none_and_never_zero():
    ok = rm.measure("code_quality", _ROOT / "tools",
                    evaluate_fn=lambda d, **k: {"metric_value": 0.0, "success": True,
                                                "metric_name": "m"})
    assert ok["metric"] == 0.0, "a MEASURED zero is a real finding and must survive"
    assert ok["basis"] == "measured"

    bad = rm.measure("code_quality", _ROOT / "tools",
                     evaluate_fn=lambda d, **k: {"metric_value": 0.0, "success": False})
    assert bad["metric"] is None
    assert bad["basis"] == "evaluator_failed"


def test_a_whitespace_project_dir_refuses_rather_than_measuring_another_tree(tmp_path):
    """`fitness_evaluator._run_tool` splits its command on whitespace."""
    spaced = tmp_path / "a b" / "tools"
    spaced.mkdir(parents=True)
    called = []
    out = rm.measure("code_quality", spaced,
                     evaluate_fn=lambda d, **k: called.append(k) or {
                         "metric_value": 0.9, "success": True})
    assert out["metric"] is None
    assert out["basis"] == "project_dir_unsplittable"
    assert called == [], "the evaluator was run against a path it would mis-split"


def test_changed_files_is_none_not_empty_when_git_cannot_answer(tmp_path):
    """'the adapter changed nothing' and 'we could not read the tree' differ."""
    not_a_repo = tmp_path / "bare"
    not_a_repo.mkdir()
    assert rm.changed_files(not_a_repo) is None


# ── lanes ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def candidates_db():
    """A connection factory that CLOSES what it opens.

    `tests/conftest.py` fails a test that leaves a SQLite write transaction
    open, and these tests deliberately never commit -- so teardown, not the
    body, is where the connection is disposed of.
    """
    opened = []

    def _make(with_lane: bool):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        lane_col = ", lane TEXT DEFAULT 'incubator'" if with_lane else ""
        conn.execute(
            "CREATE TABLE experiment_candidates (id TEXT PRIMARY KEY, domain TEXT, "
            f"updated_at TEXT{lane_col})")
        opened.append(conn)
        return conn

    yield _make
    for conn in opened:
        conn.rollback()
        conn.close()


@pytest.fixture()
def sqlite_conn():
    """Same discipline for the migration tests."""
    opened = []

    def _make():
        conn = sqlite3.connect(":memory:")
        opened.append(conn)
        return conn

    yield _make
    for conn in opened:
        conn.rollback()
        conn.close()


def test_lane_census_is_unmeasurable_not_zero_without_the_migration(candidates_db):
    """Three zeroes would assert a board holds no candidates in any lane."""
    conn = candidates_db(with_lane=False)
    conn.execute("INSERT INTO experiment_candidates (id) VALUES ('exp-1')")
    census = rm.lane_census(conn=conn)
    assert census["state"] == "unmeasurable"
    assert census["reason"] == "lane_column_absent"
    assert census["by_lane"] is None
    assert census["total"] is None


def test_lane_write_reports_an_absent_column_rather_than_skipping_silently(candidates_db):
    conn = candidates_db(with_lane=False)
    conn.execute("INSERT INTO experiment_candidates (id) VALUES ('exp-1')")
    out = rm.set_lane("exp-1", rm.LANE_FRONTIER, conn=conn)
    assert out["set"] is False
    assert out["reason"] == "lane_column_absent"


def test_lanes_round_trip_and_an_unknown_lane_is_refused(candidates_db):
    conn = candidates_db(with_lane=True)
    for ident in ("exp-1", "exp-2", "exp-3"):
        conn.execute("INSERT INTO experiment_candidates (id) VALUES (?)", (ident,))
    assert rm.set_lane("exp-2", rm.LANE_FRONTIER, conn=conn)["set"] is True
    assert rm.set_lane("exp-3", rm.LANE_ARCHIVE, conn=conn)["set"] is True
    refused = rm.set_lane("exp-1", "graveyard", conn=conn)
    assert refused["set"] is False and refused["reason"] == "unknown_lane"

    census = rm.lane_census(conn=conn)
    assert census["state"] == "measured"
    assert census["by_lane"] == {"incubator": 1, "frontier": 1, "archive": 1}
    assert census["unassigned"] == 0


def test_a_null_lane_is_unassigned_and_never_counted_as_incubator(candidates_db):
    conn = candidates_db(with_lane=True)
    conn.execute("INSERT INTO experiment_candidates (id, lane) VALUES ('exp-1', NULL)")
    census = rm.lane_census(conn=conn)
    assert census["by_lane"] == {"incubator": 0, "frontier": 0, "archive": 0}
    assert census["unassigned"] == 1


# ── the migration ────────────────────────────────────────────────────────────


def _migration():
    spec = importlib.util.spec_from_file_location("xrv_lab_02_up", MIGRATION_DIR / "up.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_applies_on_sqlite_and_is_idempotent(sqlite_conn):
    module = _migration()
    conn = sqlite_conn()
    conn.execute("CREATE TABLE experiment_candidates (id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO experiment_candidates VALUES ('exp-old', 'discarded')")

    first = module.up(conn)
    assert first["column_added"] is True
    assert first["lanes"] == list(rm.LANES)

    second = module.up(conn)
    assert second["column_added"] is False
    assert second["reason"] == "already_present"

    # A pre-migration candidate reads `incubator`: it was decided against an
    # identity baseline, so no real measurement exists for it.
    assert conn.execute("SELECT lane FROM experiment_candidates WHERE id='exp-old'"
                        ).fetchone()[0] == rm.LANE_INCUBATOR
    conn.execute("INSERT INTO experiment_candidates (id) VALUES ('exp-new')")
    assert conn.execute("SELECT lane FROM experiment_candidates WHERE id='exp-new'"
                        ).fetchone()[0] == rm.LANE_INCUBATOR


def test_migration_check_refuses_a_lane_outside_the_python_tuple(sqlite_conn):
    module = _migration()
    conn = sqlite_conn()
    conn.execute("CREATE TABLE experiment_candidates (id TEXT PRIMARY KEY)")
    module.up(conn)
    for lane in rm.LANES:
        conn.execute("INSERT INTO experiment_candidates (id, lane) VALUES (?, ?)",
                     (f"ok-{lane}", lane))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO experiment_candidates (id, lane) VALUES ('bad', 'nonsense')")


def test_migration_emits_portable_ddl_for_postgresql():
    """The generated statement carries no SQLite-only syntax.

    The PostgreSQL leg was exercised for real against a throwaway table in
    `icdev_e2e` on 2026-09-12 (add, backfill, CHECK, DEFAULT all behaved); this
    keeps the emitted SQL from drifting into a dialect afterwards, on a runner
    that has no PostgreSQL to ask.
    """
    source = (MIGRATION_DIR / "up.py").read_text(encoding="utf-8")
    for sqlite_only in ("AUTOINCREMENT", "PRAGMA", "WITHOUT ROWID", "INSERT OR REPLACE"):
        assert sqlite_only not in source.upper()
    assert "ALTER TABLE experiment_candidates ADD COLUMN lane TEXT " in source
    # The CHECK list is BUILT from LANES; a literal IN list would be a second
    # spelling of the vocabulary.
    assert "IN ({allowed})" in source
    assert "from tools.autoresearch.real_mutation import LANE_INCUBATOR, LANES" in source


def test_the_init_ddl_literal_matches_the_python_tuple():
    """`init_icdev_db.py` cannot import, so its literal is pinned here instead."""
    ddl = (_ROOT / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
    marker = "lane            TEXT DEFAULT 'incubator' CHECK(lane IN ("
    assert marker in ddl
    listed = ddl.split(marker, 1)[1].split("))", 1)[0]
    assert [part.strip().strip("'") for part in listed.split(",") if part.strip()] == \
        list(rm.LANES)


# ── structural: the loop may never merge ─────────────────────────────────────


MERGE_MODULES = (
    Path("tools") / "autoresearch" / "real_mutation.py",
    Path("tools") / "autoresearch" / "experiment_engine.py",
)


def _string_constants(tree):
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


@pytest.mark.parametrize("rel", MERGE_MODULES, ids=lambda p: p.name)
def test_no_merge_verb_anywhere_in_the_loop(rel):
    """AST, not grep: the loop opens a PR and a HUMAN merges it.

    A behavioural test over today's callers would still pass the day somebody
    threads an auto-merge through, which is exactly the edit this refuses.
    """
    tree = ast.parse((_ROOT / rel).read_text(encoding="utf-8"))
    literals = _string_constants(tree)

    for text in literals:
        lowered = text.lower()
        assert "pr merge" not in lowered, f"{rel} names a merge verb: {text[:80]!r}"
        assert "--merge" not in lowered, f"{rel} names --merge: {text[:80]!r}"
        assert "--admin" not in lowered, f"{rel} names --admin: {text[:80]!r}"
        assert "--squash" not in lowered, f"{rel} names --squash: {text[:80]!r}"

    # Also as an argv LIST, which no single literal above would reveal.
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        parts = [e.value for e in node.elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        assert parts[:3] != ["gh", "pr", "merge"], f"{rel} builds a `gh pr merge` argv"
        assert parts[:2] != ["git", "merge"], f"{rel} builds a `git merge` argv"


def test_the_worktree_is_removed_through_git_never_rmtree():
    """`shutil.rmtree` would destroy a dirty tree git deliberately refuses."""
    source = (_ROOT / MERGE_MODULES[0]).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"rmtree", "rmdir"}:
            raise AssertionError(f"real_mutation calls {node.attr}; use git worktree remove")
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names}
    assert "shutil" not in imported
    assert '"worktree", "remove"' in source


def test_a_forced_removal_is_impossible_without_the_no_commits_proof(tmp_path):
    """The force is PROVEN, not blunt: an unreadable history must refuse.

    Structural absence is not available here -- the discard path genuinely
    needs `--force`, because a rejected patch leaves the tree dirty every
    time. What must stay impossible is forcing on a tree whose history nobody
    could read, so this exercises the predicate rather than grepping for it.
    """
    not_a_repo = tmp_path / "bare"
    not_a_repo.mkdir()
    # git cannot answer here, so the proof is None -> refuse, and no directory
    # is destroyed even though force_if_dirty was asked for.
    assert rm._holds_no_commits(not_a_repo, "main") is None
    out = rm.remove_worktree(tmp_path, not_a_repo, base_ref="main", force_if_dirty=True)
    assert out["removed"] is False
    assert out["forced"] is False
    assert out["reason"] == "history_unmeasurable"
    assert not_a_repo.exists()


def test_a_worktree_holding_a_commit_is_never_forced(repo, cfg):
    """Committed work is the one thing the force must not be able to reach."""
    wt = rm.create_worktree(repo, "exp-commit1", base_ref="main", config=cfg)
    assert wt["created"] is True
    path = Path(wt["path"])
    (path / "tools" / "widget.py").write_text("x = 2" + chr(10), encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["commit", "-m", "the worker committed"], path)
    (path / "tools" / "widget.py").write_text("x = 3" + chr(10), encoding="utf-8")

    assert rm._holds_no_commits(path, "main") is False
    out = rm.remove_worktree(repo, path, base_ref="main", force_if_dirty=True)
    assert out["removed"] is False
    assert out["reason"] == "worktree_holds_commits"
    assert path.exists(), "a forced removal destroyed committed work"


def test_a_discarded_candidates_branch_is_deleted_with_lowercase_d(repo, cfg):
    """`git branch -d` refuses an unmerged branch; that refusal is the proof."""
    source = (_ROOT / MERGE_MODULES[0]).read_text(encoding="utf-8")
    assert '"branch", "-D"' not in source, "-D would delete unmerged commits"

    result = rm.run_real_experiment(
        "exp-branch1", "Make it worse", root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter({"tools/widget.py": "y = 0\n"}),
        evaluate_fn=evaluator([0.80, 0.40]),
        decide_fn=keeper("discard"), push=False,
    )
    assert result["outcome"] == rm.OUTCOME_DISCARDED
    assert result["branch_deleted"] is True
    assert "autoresearch/exp-branch1" not in _git(["branch", "--list"], repo).stdout


def test_every_named_reason_is_in_the_closed_refusals_mapping():
    """A reason string outside `REFUSALS` is a silent exit wearing a name."""
    source = (_ROOT / MERGE_MODULES[0]).read_text(encoding="utf-8")
    tree = ast.parse(source)
    named = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        target = getattr(func, "id", None) or getattr(func, "attr", None)
        if target not in {"_refuse", "abandon"}:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                named.add(arg.value)
    assert named, "the AST scan found no refusal call sites — the scanner stopped scanning"
    assert named <= set(rm.REFUSALS), f"unnamed reasons: {sorted(named - set(rm.REFUSALS))}"


def test_the_declared_domain_list_is_exactly_one_and_lives_in_python():
    assert rm.REAL_MUTATION_DOMAINS == ("code_quality",)
    assert rm.LANES == ("incubator", "frontier", "archive")
    assert set(rm.OUTCOMES) == {"kept", "discarded", "unmeasurable", "refused"}


# ── the engine's own honesty flag ────────────────────────────────────────────


def test_the_engine_reports_placeholder_metrics_for_every_other_domain():
    """The other seven domains are untouched and keep reporting unmeasurable."""
    from tools.autoresearch import experiment_engine as engine

    source = (_ROOT / "tools" / "autoresearch" / "experiment_engine.py").read_text(
        encoding="utf-8")
    # `placeholder_metrics: False` is reachable only through `measured_run`,
    # which requires the real path to have measured at least one candidate.
    assert '"placeholder_metrics": not measured_run,' in source
    assert "measured_run = real_enabled and real_measured > 0" in source
    assert engine._PLACEHOLDER_METRICS_NOTE
    assert engine._REAL_MUTATION_UNMEASURABLE_NOTE != engine._REAL_MUTATION_NOTE


def test_the_loop_acceptance_rate_is_none_over_an_empty_denominator():
    source = (_ROOT / "tools" / "autoresearch" / "experiment_engine.py").read_text(
        encoding="utf-8")
    assert "acceptance_rate = (kept_count / judged) if judged else None" in source
    assert "kept_count / max(kept_count + discarded_count, 1)" not in source
def test_a_host_level_refusal_stops_the_run_and_a_hypothesis_one_does_not():
    """`FATAL_REASONS` is about the HOST and must stay a subset of `REFUSALS`.

    A missing CLI cannot become available between candidates, and the BEFORE
    measurement is a full analyzer pass that runs before the adapter is even
    reached -- so retrying it five times costs five analyzer runs to learn the
    same thing five times. A measurement that failed for THIS hypothesis is a
    different fact and must not stop the run.
    """
    assert set(rm.FATAL_REASONS) <= set(rm.REFUSALS)
    for reason in ("adapter_unavailable", "config_unreadable",
                   "worktree_add_failed", "wall_clock_spent"):
        assert rm.is_fatal(reason) is True
    for reason in ("post_unmeasurable", "base_unmeasurable", "no_patch_produced",
                   "patch_out_of_bounds", "patch_timed_out", "push_failed"):
        assert rm.is_fatal(reason) is False, f"{reason} is about one hypothesis"
    assert rm.is_fatal(None) is False


def test_the_circuit_breaker_never_counts_an_infrastructure_outage():
    """It counts REJECTED HYPOTHESES; an outage must not latch a domain closed."""
    source = (_ROOT / "tools" / "autoresearch" / "experiment_engine.py").read_text(
        encoding="utf-8")
    branch = source.split("if _real.is_fatal(", 1)[1].split("continue", 1)[0]
    assert "consecutive_failures" not in branch
    assert "break" in branch


def test_an_unavailable_adapter_is_unmeasurable_not_a_discard(repo, cfg):
    result = rm.run_real_experiment(
        "exp-noadapter", "anything", root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter(available=False), evaluate_fn=evaluator([0.7]),
        decide_fn=keeper("keep"), push=False,
    )
    assert result["outcome"] == rm.OUTCOME_UNMEASURABLE
    assert result["reason"] == "adapter_unavailable"
    assert rm.is_fatal(result["reason"]) is True
    assert result["lane"] == rm.LANE_INCUBATOR
    assert not Path(result["worktree"]).exists()
# ── the scoped reading (the dilution finding) ────────────────────────────────


def test_patch_scope_refuses_anything_that_is_not_one_narrower_directory():
    assert rm.patch_scope(["tools/autoresearch/x.py"]) == "tools/autoresearch"
    # Two directories: there is no single scope to measure.
    assert rm.patch_scope(["tools/a/x.py", "tools/b/y.py"]) is None
    # The evaluator's own root: a "scoped" reading here IS the tree reading,
    # and giving the same number two names is how a diluted delta gets believed.
    assert rm.patch_scope(["tools/x.py"]) is None
    assert rm.patch_scope(["CLAUDE.md"]) is None
    assert rm.patch_scope([]) is None


def test_a_scoped_reading_is_made_from_head_and_decides(repo, cfg):
    """The BEFORE is reconstructed from HEAD, because the scope is not knowable
    until the patch exists."""
    handed = {}

    def _decide(candidate_id, pre_metric=None, post_metric=None, **kwargs):
        handed.update(pre=pre_metric, post=post_metric)
        return {"success": True, "decision": "discard",
                "metric_delta": round((post_metric or 0) - (pre_metric or 0), 6)}

    def _evaluate(domain, project_dir=None, **kwargs):
        files = sorted(pp.name for pp in Path(project_dir).rglob("*.py"))
        # A reading that depends on WHAT IS THERE, so a scoped call that failed
        # to exclude the added file cannot pass by returning a constant.
        return {"domain": domain, "metric_name": "m", "success": True,
                "metric_value": round(1.0 - 0.1 * len(files), 4)}

    result = rm.run_real_experiment(
        "exp-scoped1", "Add a helper under tools/autoresearch",
        root=repo, config=cfg, base_ref="main",
        adapter=FakeAdapter({"tools/autoresearch/helper.py": "z = 1" + chr(10)}),
        evaluate_fn=_evaluate, decide_fn=_decide, push=False,
    )

    assert result["scoped_basis"] == "measured"
    assert result["scope"] == "tools/autoresearch"
    assert result["decision_basis"] == "scoped"
    # The scoped pair, not the tree pair, is what the decision saw.
    assert handed["pre"] == result["scoped_before"]
    assert handed["post"] == result["scoped_after"]
    assert result["metric_before"] == result["scoped_before"]
    # The tree pair is still carried, so the choice hides nothing.
    assert "tree_before" in result and "tree_after" in result
    # The added file is absent from the HEAD reconstruction and present after.
    assert result["scoped_before"] != result["scoped_after"]


def test_an_unscopeable_patch_falls_back_and_NAMES_the_dilution(repo, cfg):
    """A 0.0 delta from a tree-wide average must never read as 'no change'."""
    result = rm.run_real_experiment(
        "exp-diluted1", "Touch the evaluator root", root=repo, config=cfg,
        base_ref="main",
        adapter=FakeAdapter({"tools/widget.py": "def f():" + chr(10) + "    return 7" + chr(10)}),
        evaluate_fn=evaluator([0.9312, 0.9312]),
        decide_fn=keeper("discard"), push=False,
    )
    assert result["decision_basis"] == "tree_wide_diluted"
    assert result["scoped_basis"] == "scope_is_evaluator_root"
    assert result["scoped_before"] is None and result["scoped_after"] is None
    assert result["metrics_note"] == rm.REAL_METRICS_DILUTED_NOTE
    assert rm.REAL_METRICS_DILUTED_NOTE != rm.REAL_METRICS_NOTE
    assert "does NOT mean" in result["metrics_note"]


def test_a_scoped_reading_that_cannot_be_made_leaves_both_metrics_none():
    """Never a zero delta for a reading nobody made."""
    out = rm.scoped_measurement("code_quality", Path("/no/such/tree"),
                                ["tools/autoresearch/x.py"],
                                evaluate_fn=lambda d, **k: {"metric_value": 0.5,
                                                            "success": True})
    assert out["basis"] == "base_unreadable"
    assert out["before"] is None and out["after"] is None and out["delta"] is None
