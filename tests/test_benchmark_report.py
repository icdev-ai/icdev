#!/usr/bin/env python3
# CUI // SP-CTI
"""Guards for the generated benchmark report (xbm-cmp-01-d4).

``tools/innovation/benchmark_report.py`` renders d1's measurements, d2's join and
d3's verdicts as one markdown document. What these pin, and why each is here:

1. **The checked-in report is what its sources currently produce.** This is the
   CI diff check the task asked for, and it is the whole reason the document is
   worth having: a generated file that nobody regenerates is a stale document
   with extra steps. ``test_the_checked_in_report_is_in_sync`` fails the moment
   an inventory, watchlist or promoter-config edit lands without a regenerate.

1b. **...and adding a module is not one of those edits (kax-conflict-02).** The
   document used to quote exact module counts, which are globs over the tree, so
   two branches that each added a module produced two different files and the
   thing conflicted with itself: three of six blocked PRs on 2026-08-08 were held
   up by this file and nothing else. The counts are no longer committed — the
   classification against the floor is — so the rendering is invariant to adding
   a module and there is nothing to merge. The section below proves that with a
   two-branch git rehearsal rather than by assertion, and proves in the same
   place that crossing the floor, which IS a state change, still fails the gate.

2. **The render is deterministic.** Rendering twice must be byte-identical and
   the body must carry no wall-clock stamp — a timestamp in the document would
   make the check above fail on every run and it would be switched off within a
   week. The as-of date is the git commit date instead.

3. **Offline is a mode, not a failure.** The committed artifact is generated
   without a database so it reproduces anywhere. That must not cost the
   ``not_assessed``/``unfed`` distinction ``benchmark_compare`` rule 3 is built
   on: a database nobody opened must never manufacture a "built but unfed"
   finding, and the prose must not claim the database was unreachable when it
   was simply not asked for.

4. **It does not overwrite the hand-written map.** ``benchmark_compare`` cites
   ``docs/research/external-benchmark-map.md`` as the origin of every declared
   reading. A generator writing over its own cited source produces a document
   whose provenance is itself, and it would delete narrative that lives in no
   config. The paths must stay distinct.

5. **All ten benchmarked subsystems appear, in the map's section order.** The
   acceptance criterion names it, and section order is what makes the generated
   document readable against the hand-written one.

Every test runs offline. The two that need a live connection do not exist —
``--live`` is exercised through its guard rails instead, because a test that
depends on row counts fails on whichever database the runner had lying around.
"""

import doctest
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation import benchmark_report as br  # noqa: E402
from tools.innovation.benchmark_compare import MAP_DOC  # noqa: E402

REPORT_FILE = BASE_DIR / br.REPORT_PATH


@pytest.fixture(scope="module")
def rendered():
    markdown, result = br.generate()
    return markdown, result


# ── 1. the CI diff check ─────────────────────────────────────────────────


def test_the_checked_in_report_is_in_sync(rendered):
    """The gate. If this fails, run `python tools/innovation/benchmark_report.py --write`."""
    markdown, _ = rendered
    report = br.check_report(REPORT_FILE, markdown)
    assert report["in_sync"], (
        f"{br.REPORT_PATH.as_posix()} is stale — {report['reason']}\n{report['diff']}"
    )


def test_the_gates_failure_message_names_the_fix(tmp_path, rendered):
    """Criterion 3: whoever hits this red is usually mid-merge on an unrelated branch."""
    markdown, _ = rendered
    stale = tmp_path / "stale.md"
    stale.write_text(markdown.replace("## Summary", "## Sumary"), encoding="utf-8")
    reason = br.check_report(stale, markdown)["reason"]
    assert br.TOOL in reason
    assert "--write" in reason
    assert "Adding a module no longer changes" in reason


def test_check_reports_drift_with_a_diff(tmp_path, rendered):
    markdown, _ = rendered
    stale = tmp_path / "stale.md"
    stale.write_text(markdown.replace("## Summary", "## Sumary"), encoding="utf-8")
    report = br.check_report(stale, markdown)
    assert report["in_sync"] is False
    assert "Sumary" in report["diff"]


def test_check_on_a_missing_file_is_drift_not_a_crash(tmp_path, rendered):
    markdown, _ = rendered
    report = br.check_report(tmp_path / "nope.md", markdown)
    assert report["in_sync"] is False
    assert "does not exist" in report["reason"]


def test_write_then_check_round_trips(tmp_path, rendered):
    markdown, _ = rendered
    out = tmp_path / "round-trip.md"
    br.write_report(out, markdown)
    assert br.check_report(out, markdown)["in_sync"] is True


def test_write_uses_lf_endings(tmp_path, rendered):
    """Path.write_text would emit CRLF here, and a Linux --check would call it wholly changed."""
    markdown, _ = rendered
    out = tmp_path / "endings.md"
    br.write_report(out, markdown)
    assert b"\r\n" not in out.read_bytes()


# ── 1b. the artifact does not conflict with itself (kax-conflict-02) ─────


_SYNTHETIC_MODULES = 8  # comfortably above every module_floor in the inventory


def _module_dirs():
    """Every directory the inventory's module globs point at, repo-root relative."""
    inventory = br.load_inventory()
    dirs = []
    for spec in inventory.values():
        for pattern in spec.get("modules", []):
            parent = Path(pattern).parent
            if parent not in dirs:
                dirs.append(parent)
    return dirs


def _synthetic_tree(root: Path) -> Path:
    """A tree the module globs can be counted against, with no repo in it.

    The real checkout is not usable for this: writing probe modules into it to
    see what happens is how you get a probe module committed. The counts here do
    not have to match the real ones — what is under test is whether *changing*
    them changes the document.
    """
    for directory in _module_dirs():
        target = root / directory
        target.mkdir(parents=True, exist_ok=True)
        for index in range(_SYNTHETIC_MODULES):
            (target / f"m{index}.py").write_text("", encoding="utf-8")
    return root


def _render_for(root: Path) -> str:
    markdown, _ = br.generate(base_dir=root)
    return markdown


def test_adding_a_module_does_not_change_the_committed_rendering(tmp_path):
    """The root cause, stated as an invariant: this is why there is nothing to merge."""
    root = _synthetic_tree(tmp_path / "tree")
    before = _render_for(root)
    (root / "tools" / "rag" / "brand_new_module.py").write_text("", encoding="utf-8")
    (root / "tools" / "kanban" / "another_new_module.py").write_text("", encoding="utf-8")
    assert _render_for(root) == before


def _subsystem_owning_its_directories():
    """A subsystem whose module directories no other subsystem also counts.

    Several subsystems share a surface, so emptying one directory does not
    necessarily drop anybody below a floor. This picks one where it does.
    """
    inventory = br.load_inventory()
    owners = {}
    for tag, spec in inventory.items():
        for pattern in spec.get("modules", []):
            owners.setdefault(Path(pattern).parent, set()).add(tag)
    for tag, spec in sorted(inventory.items()):
        dirs = [Path(p).parent for p in spec.get("modules", [])]
        if dirs and all(owners[d] == {tag} for d in dirs):
            return tag, dirs
    raise AssertionError("no subsystem owns its module directories exclusively")


def test_dropping_below_the_floor_does_change_it(tmp_path):
    """The invariance is to the count, not to the tree. A state change must still show."""
    root = _synthetic_tree(tmp_path / "tree")
    before = _render_for(root)
    _, dirs = _subsystem_owning_its_directories()
    for directory in dirs:
        for path in (root / directory).glob("*.py"):
            path.unlink()
    (root / dirs[0] / "only_one_left.py").write_text("", encoding="utf-8")
    after = _render_for(root)
    assert after != before
    assert "`thin`" in after


def _git(repo: Path, *args: str):
    """git with an identity supplied inline — the CI runner has none configured.

    Signing is disabled for the same reason: this is a throwaway repository in a
    temp directory, and a GPG prompt here would hang the suite.
    """
    return subprocess.run(
        [
            "git",
            "-c", "user.name=benchmark-rehearsal",
            "-c", "user.email=rehearsal@example.invalid",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=str(repo), capture_output=True, text=True,
    )


def _commit_report(repo: Path, root: Path, message: str) -> None:
    br.write_report(repo / "report.md", _render_for(root))
    assert _git(repo, "add", "-A").returncode == 0
    result = _git(repo, "commit", "-m", message)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_two_branches_that_each_add_a_module_both_merge_cleanly(tmp_path):
    """Acceptance criterion 1, rehearsed rather than asserted.

    Two branches off the same base, each adding a module under a different
    counted surface, each regenerating the report. Both merge into main with no
    human touching the document, and the merged main is still in sync with what
    the merged tree produces.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init").returncode == 0
    assert _git(repo, "symbolic-ref", "HEAD", "refs/heads/main").returncode == 0

    root = _synthetic_tree(repo / "tree")
    _commit_report(repo, root, "base: the report as main has it")

    for branch, module in (
        ("branch-a", Path("tools") / "rag" / "a_new_module.py"),
        ("branch-b", Path("tools") / "observability" / "b_new_module.py"),
    ):
        assert _git(repo, "checkout", "-b", branch, "main").returncode == 0
        (root / module).write_text("", encoding="utf-8")
        # Each branch does exactly what the old workflow demanded of it.
        _commit_report(repo, root, f"{branch}: add a module and regenerate")

    assert _git(repo, "checkout", "main").returncode == 0
    for branch in ("branch-a", "branch-b"):
        merge = _git(repo, "merge", "--no-edit", branch)
        assert merge.returncode == 0, (
            f"{branch} did not merge cleanly:\n{merge.stdout}\n{merge.stderr}"
        )

    committed = (repo / "report.md").read_text(encoding="utf-8")
    assert "<<<<<<<" not in committed, "the merge left conflict markers in the report"
    # main now carries both modules; the gate must be green with nobody having
    # regenerated after the merge.
    assert br.check_report(repo / "report.md", _render_for(root))["in_sync"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_the_rehearsal_would_have_caught_the_old_conflict(tmp_path):
    """The same rehearsal against a rendering that still quotes counts, to show
    the proof above is not vacuous: with the integer in the file, the two
    branches write different values to the same line and git cannot merge them."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init").returncode == 0
    assert _git(repo, "symbolic-ref", "HEAD", "refs/heads/main").returncode == 0

    def counted(n_a: int, n_b: int) -> str:
        return f"# report\n\nrag: **{n_a} modules**\nobservability: **{n_b} modules**\n"

    def commit(message: str, text: str) -> None:
        (repo / "report.md").write_text(text, encoding="utf-8")
        assert _git(repo, "add", "-A").returncode == 0
        assert _git(repo, "commit", "-m", message).returncode == 0

    commit("base", counted(61, 32))
    assert _git(repo, "checkout", "-b", "branch-a", "main").returncode == 0
    commit("branch-a regenerates", counted(62, 32))
    assert _git(repo, "checkout", "-b", "branch-b", "main").returncode == 0
    commit("branch-b regenerates", counted(61, 33))

    assert _git(repo, "checkout", "main").returncode == 0
    assert _git(repo, "merge", "--no-edit", "branch-a").returncode == 0
    conflicted = _git(repo, "merge", "--no-edit", "branch-b")
    assert conflicted.returncode != 0, "expected the count-bearing rendering to conflict"
    assert "<<<<<<<" in (repo / "report.md").read_text(encoding="utf-8")


# ── 2. determinism ───────────────────────────────────────────────────────


def test_rendering_twice_is_byte_identical():
    first, _ = br.generate()
    second, _ = br.generate()
    assert first == second


def test_the_body_carries_no_wall_clock_stamp(rendered):
    """A timestamp would defeat the diff check outright."""
    markdown, result = rendered
    assert result["generated_at"] not in markdown
    for record in result["subsystems"].values():
        measured_at = record["measured"].get("measured_at")
        if measured_at:
            assert measured_at not in markdown


# ── 3. offline is a mode, not a failure ──────────────────────────────────


def test_offline_evidence_opens_no_database():
    evidence = br.offline_evidence()
    assert evidence["subsystems"], "the offline sweep produced no subsystems"
    for tag, record in evidence["subsystems"].items():
        assert record["db_measured"] is False, f"{tag} measured a database in offline mode"
        assert record["table_row_count"] == 0
        assert record["status"] != "inert", f"{tag} reported inert without a measurement"


def test_offline_never_produces_an_unfed_reading(rendered):
    """Rule 3 of benchmark_compare: absence of evidence is not evidence."""
    _, result = rendered
    for tag, record in result["subsystems"].items():
        assert record["data_state"] != "unfed", f"{tag} invented a gap out of an unopened database"


def test_offline_says_not_measured_rather_than_unreachable(rendered):
    """d1 phrases an absent database as "not reachable"; offline, nothing tried and failed."""
    markdown, result = rendered
    assert br.OFFLINE_LIMIT in markdown
    assert br.OFFLINE_REASON not in markdown
    assert "the database was not reachable" not in markdown
    for record in result["subsystems"].values():
        assert any(limit == br.OFFLINE_LIMIT for limit in br._limits(record))


def test_offline_retires_no_declared_finding(rendered):
    """Nothing may be reported retired by a measurement that was never taken."""
    _, result = rendered
    assert not [t for t, r in result["subsystems"].items() if r["finding_retired"]]
    assert "Retired by measurement" not in br.render_markdown(result)


# ── 4. it does not overwrite the hand-written map ────────────────────────


def test_the_generated_report_is_not_the_hand_written_map():
    assert br.REPORT_PATH.as_posix() != MAP_DOC
    assert (BASE_DIR / MAP_DOC).exists(), "the cited source map is missing"


def test_the_hand_written_map_is_still_cited_as_the_source(rendered):
    markdown, _ = rendered
    assert MAP_DOC in markdown
    assert f"`{MAP_DOC}`" in markdown, "the map is not listed among the sources"


def test_the_hand_written_map_points_at_the_generated_one():
    """A reader who opens the map by hand has to be told the live verdicts are generated."""
    text = (BASE_DIR / MAP_DOC).read_text(encoding="utf-8")
    assert br.REPORT_PATH.name in text


# ── 5. structure: the map's ten sections, in order ───────────────────────


def test_all_ten_benchmarked_subsystems_are_present(rendered):
    markdown, result = rendered
    sectioned, _ = br._ordered(result)
    assert [r["benchmark_section"] for r in sectioned] == list(range(1, 11))
    for record in sectioned:
        assert f"## {record['benchmark_section']}. {record['title']}" in markdown


def test_every_declared_subsystem_gets_a_section(rendered):
    markdown, result = rendered
    for record in result["subsystems"].values():
        assert record["title"] in markdown, f"{record['subsystem']} has no section"


def test_sections_appear_in_the_maps_order(rendered):
    markdown, result = rendered
    sectioned, _ = br._ordered(result)
    positions = [markdown.index(f"## {r['benchmark_section']}. {r['title']}") for r in sectioned]
    assert positions == sorted(positions)


def test_unbenchmarked_subsystems_are_separated_from_the_ten(rendered):
    """Listing them together would imply a pass judged them and found them unremarkable."""
    markdown, result = rendered
    _, unsectioned = br._ordered(result)
    assert unsectioned, "the inventory used to carry subsystems the map never covered"
    divider = markdown.index("# Subsystems the benchmark map has not benchmarked")
    for record in unsectioned:
        assert markdown.index(f"## {record['title']} —") > divider


def test_the_summary_table_covers_every_subsystem(rendered):
    markdown, result = rendered
    summary = markdown[markdown.index("## Summary"): markdown.index("## 1. ")]
    for record in result["subsystems"].values():
        assert record["title"] in summary


# ── content the report exists to carry ───────────────────────────────────


def test_no_adaptation_needed_is_stated_not_implied_by_silence(rendered):
    """The fourth verdict is why this is not a gap report."""
    markdown, result = rendered
    assert result["no_adaptation_needed"], "no subsystem reached the fourth verdict"
    assert "No adaptation needed" in markdown
    assert "Nothing outstanding" in markdown


def test_a_lead_with_nothing_to_take_still_reads_as_a_lead(rendered):
    """position and verdict are separate questions; a heading must not collapse them.

    §7 is position=ahead with verdict=no_adaptation_needed. A heading reading only
    "no adaptation needed" would lose that ICDEV leads — the exact choice
    benchmark_compare split the two fields to avoid.
    """
    markdown, result = rendered
    qualified = [
        r
        for r in result["subsystems"].values()
        if r["verdict"] == "no_adaptation_needed" and r["position"] in ("ahead", "parity")
    ]
    assert qualified, "no subsystem exercises the split between position and verdict"
    for record in qualified:
        label = br._heading_label(record)
        assert label != br._verdict_label(record)
        assert br.POSITION_LABELS[record["position"]] in label
        assert f"{record['title']} — **{label}**" in markdown


def test_every_section_states_its_position(rendered):
    markdown, result = rendered
    for record in result["subsystems"].values():
        assert (
            f"position **{br.POSITION_LABELS[record['position']]}**" in markdown
        ), f"{record['subsystem']} does not state its position"


def test_the_three_leads_are_named_out_loud(rendered):
    """The map's first ranked recommendation, and the one a generator can reproduce."""
    markdown, result = rendered
    leads = markdown[markdown.index("## Where ICDEV leads"):]
    ahead = [r for r in result["subsystems"].values() if r["position"] == "ahead"]
    assert ahead
    for record in ahead:
        assert record["title"] in leads


def test_every_outstanding_item_reaches_the_index(rendered):
    markdown, result = rendered
    index = markdown[markdown.index("## Everything outstanding"):]
    for record in result["subsystems"].values():
        for item in record["outstanding"]:
            assert br._cell(item["item"]) in index


def test_measurement_limits_survive_into_the_prose(rendered):
    """A limit reported as a result is the failure this whole chain is built to avoid."""
    markdown, result = rendered
    for record in result["subsystems"].values():
        for limit in br._limits(record):
            assert br._flat(limit) in markdown


def test_a_pipe_in_a_source_string_does_not_break_a_table():
    assert br._cell("a | b") == "a \\| b"
    assert "\n" not in br._cell("folded\nscalar")


def test_watchlist_projects_are_rendered_under_their_subsystem(rendered):
    markdown, result = rendered
    for record in result["subsystems"].values():
        for label in record["external_projects"]:
            assert br._cell(label) in markdown


# ── guard rails ──────────────────────────────────────────────────────────


def _cli(*argv):
    return subprocess.run(
        [sys.executable, str(BASE_DIR / br.TOOL), *argv],
        capture_output=True, text=True, cwd=str(BASE_DIR),
    )


def test_live_cannot_be_checked():
    result = _cli("--live", "--check")
    assert result.returncode != 0
    assert "--live cannot be checked" in result.stderr


def test_live_cannot_be_written_to_the_default_path():
    """A live render committed here would fail --check on every other machine."""
    result = _cli("--live", "--write")
    assert result.returncode != 0
    assert "explicit --out" in result.stderr


def test_the_cli_check_agrees_with_the_library():
    result = _cli("--check")
    assert result.returncode == 0, result.stderr


def test_module_doctests_pass():
    failures, _ = doctest.testmod(br, verbose=False)
    assert failures == 0
