# CUI // SP-CTI
"""An auto-managed file re-freezes the deployment; the restore tier puts it
back — and NEVER a human edit (autonomy-dep-04).

THE INCIDENT. autonomy-dep-03 shipped a REPORTER for this freeze on
2026-08-21, the freeze was cleared by hand, and it recurred within a day:
1,340 refusals of "1 incoming file(s) are locally modified: args/projects.yaml"
between 2026-08-22T10:22Z and 02:41Z the next morning. `kanban_project_sync`
regenerates that TRACKED file after every task creation; every project-card
PR touches it upstream; `pull_if_safe` correctly refuses to pull over a
locally modified incoming file. The local side is re-dirtied continuously and
the incoming side constantly, so the clash cannot clear on its own. What it
cost: PR #1903's fix for GET /coworker/<id> merged and the live dashboard
kept serving the old JSON body, the QA sweep failed on it again and seeded a
duplicate card, and every green signal stayed green.

These tests build a REAL origin + deployment pair with git and reproduce the
freeze exactly: the board adds a project the writer registers locally, a
human registers a different card upstream, and `deployment_freshness` reads
`blocked` on that one file. Then the fourth enumerated restore act is asked.

THE THREE THINGS PINNED:
  1. A regenerable diff is restored, the tree is pulled THROUGH the guard, and
     the board's cards are re-derived on the pulled tree — the human's
     committed project, the upstream PR's project and the board's project all
     survive, the intent row precedes the act, and `deployment_freshness`
     goes blocked -> current with nobody typing git.
  2. A HUMAN EDIT to name/description/briefs/a committed epic is NEVER
     reverted: the proof is False, no audit row is written, the file is
     byte-identical afterwards, and the deployment still reports `blocked`
     (a freeze a human caused is reported, not discarded).
  3. CANNOT TELL refuses: an unreadable board, an empty board, a foreign
     locally-modified incoming file no writer regenerates.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 — git only, fixed argv, shell=False
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness import restore_acts as ra  # noqa: E402
from tools.genesis import deployment_freshness as dfm  # noqa: E402
from tools.project import kanban_project_sync as kps  # noqa: E402

ACT = "restore_auto_managed_file"
REL = kps.TRACKED_RELPATH

COMMITTED = """# CUI // SP-CTI
#
# Auto-managed by tools/project/kanban_project_sync.py
# Human edits to name/description/briefs are preserved.

projects:
- key: alpha
  name: Alpha - a human-named project
  description: 'A human-written description that the old whole-document writer
    would reflow onto a different number of lines without changing a value.'
  task_prefix: alpha-
  default_open: true
  briefs:
  - docs/briefs/alpha.md
  epics:
  - key: core
    title: Core
    priority: high
"""

UPSTREAM_CARD = """- key: beta
  name: Beta - registered by a human PR
  task_prefix: beta-
  briefs: []
  epics:
  - key: ui
    title: UI
    priority: medium
"""

#: What the reflex derives from the board: a project NO human registered.
BOARD = {"gamma-": {"core", "ui"}}


# --------------------------------------------------------------------------- #
# A real origin + deployment pair
# --------------------------------------------------------------------------- #
def _git(root: Path, *args: str, check: bool = True):
    return subprocess.run(  # nosec B603 B607 — fixed argv, shell=False
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, shell=False,
    )


def _commit_all(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "-m", message)


@pytest.fixture
def deployment(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q")
    _git(seed, "symbolic-ref", "HEAD", "refs/heads/main")
    (seed / "args").mkdir()
    (seed / REL).write_text(COMMITTED, encoding="utf-8", newline="\n")
    (seed / "tools").mkdir()
    (seed / "tools" / "x.py").write_text("X = 1\n", encoding="utf-8", newline="\n")
    _commit_all(seed, "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "-u", "origin", "main")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    deploy = tmp_path / "deploy"
    _git(tmp_path, "clone", "-q", str(origin), str(deploy))
    return SimpleNamespace(origin=origin, seed=seed, deploy=deploy)


def _upstream_registers_a_card(seed: Path, *, also_edit_tool: bool = False) -> None:
    """The #1907 / #1908 shape: a human PR appends a card upstream."""
    path = seed / REL
    path.write_text(path.read_text(encoding="utf-8") + UPSTREAM_CARD,
                    encoding="utf-8", newline="\n")
    if also_edit_tool:
        (seed / "tools" / "x.py").write_text("X = 3\n", encoding="utf-8", newline="\n")
    _commit_all(seed, "chore(kanban): register beta card")
    _git(seed, "push", "-q", "origin", "main")


def _regenerate_locally(deploy: Path, board=None) -> dict:
    """The reflex's dirt: the writer, run against the board, in the deployment."""
    return kps.sync_projects(path=deploy / REL, board=BOARD if board is None else board)


def _projects(deploy: Path) -> dict:
    data = yaml.safe_load((deploy / REL).read_text(encoding="utf-8"))
    return {p["key"]: p for p in data["projects"]}


def _deps(board=None):
    return {"board_fn": (lambda: BOARD if board is None else board)}


class Recorder:
    def __init__(self):
        self.rows = []

    def __call__(self, action, details):
        json.dumps(details, default=str)       # the row must be serialisable
        self.rows.append((action, details))
        return len(self.rows)


# --------------------------------------------------------------------------- #
# 0. The incident reproduces
# --------------------------------------------------------------------------- #
def test_the_incident_reproduces_blocked_on_the_one_file(deployment):
    _regenerate_locally(deployment.deploy)
    _upstream_registers_a_card(deployment.seed)
    rep = dfm.freshness(root=str(deployment.deploy))
    assert rep["state"] == dfm.BLOCKED
    assert rep["reason"] == ra.OVERLAP_REASON
    assert rep["conflicts"] == [REL]
    assert rep["behind_by"] == 1


# --------------------------------------------------------------------------- #
# 1. A regenerable diff is restored, pulled and re-derived — nobody types git
# --------------------------------------------------------------------------- #
def test_a_regenerated_file_is_restored_pulled_and_rederived(deployment):
    d = deployment.deploy
    _regenerate_locally(d)
    _upstream_registers_a_card(deployment.seed)
    assert dfm.freshness(root=str(d))["state"] == dfm.BLOCKED

    rec = Recorder()
    r = ra.perform(ACT, REL, audit=rec, root=d, **_deps())

    assert r["outcome"] == ra.APPLIED, r
    assert r["proven"] is True and r["confirmed"] is True
    assert r["applied"]["restored"] == REL
    assert r["applied"]["pull"]["pulled"] is True
    assert r["applied"]["sync"]["written"] is True
    # The intent row was written BEFORE the act, and the outcome after it.
    assert [a for a, _ in rec.rows] == [f"restore.{ACT}.intent", f"restore.{ACT}.applied"]
    assert rec.rows[0][1]["tier"] == "restore"
    assert rec.rows[0][1]["evidence"]["added_projects"] == ["gamma"]

    # The tree IS the branch now, and the registry holds all three: the
    # human's committed project, the upstream PR's, and the board's.
    head = _git(d, "rev-parse", "HEAD").stdout.strip()
    assert head == _git(deployment.seed, "rev-parse", "HEAD").stdout.strip()
    projects = _projects(d)
    assert set(projects) == {"alpha", "beta", "gamma"}
    assert projects["alpha"]["name"] == "Alpha - a human-named project"
    assert projects["beta"]["name"] == "Beta - registered by a human PR"
    assert {e["key"] for e in projects["gamma"]["epics"]} == {"core", "ui"}

    # And the deployment is no longer frozen — MEASURED again, not inferred.
    after = dfm.freshness(root=str(d))
    assert after["state"] == dfm.CURRENT, after
    assert after["behind_by"] == 0


def test_a_board_that_grew_since_the_dirt_is_still_regenerable(deployment):
    """The working copy may hold LESS than the writer would add today; it may
    never hold something the writer would not."""
    d = deployment.deploy
    _regenerate_locally(d, board={"gamma-": {"core"}})
    _upstream_registers_a_card(deployment.seed)
    bigger = {"gamma-": {"core", "ui"}, "delta-": {"l0"}}
    p = ra.prove_auto_managed_file(REL, root=d, board_fn=lambda: bigger)
    assert p.proven is True, p.reason
    assert p.evidence["added_projects"] == ["gamma"]


def test_format_only_drift_from_the_old_writer_is_regenerable(deployment):
    """The pre-dep-03 writer (what the FROZEN dashboard was still running)
    yaml.dump'ed the whole document: a diff with no value changed. Restoring
    it loses nothing."""
    d = deployment.deploy
    data = yaml.safe_load(COMMITTED)
    reflowed = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    (d / REL).write_text(reflowed + "\n# a trailing comment the old writer left\n",
                         encoding="utf-8", newline="\n")
    assert _git(d, "status", "--porcelain").stdout.strip(), "the file must be dirty"
    _upstream_registers_a_card(deployment.seed)
    p = ra.prove_auto_managed_file(REL, root=d, **_deps())
    assert p.proven is True, p.reason
    assert p.evidence["format_only"] is True


def test_backslash_target_spelling_is_the_same_file(deployment):
    d = deployment.deploy
    _regenerate_locally(d)
    _upstream_registers_a_card(deployment.seed)
    p = ra.prove_auto_managed_file(REL.replace("/", "\\"), root=d, **_deps())
    assert p.proven is True, p.reason
    assert p.evidence["path"] == REL


# --------------------------------------------------------------------------- #
# 2. A HUMAN EDIT is never reverted
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label, old, new, word", [
    ("name", "Alpha - a human-named project", "Alpha - renamed by hand", "name"),
    ("description", "A human-written description", "An edited description", "description"),
    ("briefs", "docs/briefs/alpha.md", "docs/briefs/alpha-v2.md", "briefs"),
    ("committed epic", "priority: high", "priority: low", "epics"),
])
def test_a_human_edit_is_never_reverted(deployment, label, old, new, word):
    d = deployment.deploy
    _regenerate_locally(d)                       # the reflex's dirt, AND
    path = d / REL
    edited = path.read_text(encoding="utf-8").replace(old, new)
    assert edited != path.read_text(encoding="utf-8"), f"fixture must edit the {label}"
    path.write_text(edited, encoding="utf-8", newline="\n")
    _upstream_registers_a_card(deployment.seed)
    assert dfm.freshness(root=str(d))["state"] == dfm.BLOCKED

    rec = Recorder()
    r = ra.perform(ACT, REL, audit=rec, root=d, **_deps())

    assert r["outcome"] == ra.REFUSED, r
    assert r["proven"] is False
    assert word in r["reason"], r["reason"]
    assert rec.rows == [], "a refusal writes no row"
    assert path.read_text(encoding="utf-8") == edited, "byte-identical afterwards"
    # Still frozen — and SAYS so. A freeze a human caused is reported, not discarded.
    assert dfm.freshness(root=str(d))["state"] == dfm.BLOCKED


def test_a_removed_committed_project_is_a_human_edit(deployment):
    d = deployment.deploy
    data = yaml.safe_load(COMMITTED)
    data["projects"] = []                        # somebody deleted alpha locally
    (d / REL).write_text(yaml.dump(data, sort_keys=False), encoding="utf-8", newline="\n")
    _upstream_registers_a_card(deployment.seed)
    p = ra.prove_auto_managed_file(REL, root=d, **_deps())
    assert p.proven is False
    assert "missing from the working copy" in p.reason


def test_a_locally_added_project_the_board_does_not_have_is_a_human_edit(deployment):
    """An entry the writer would not register from the board is somebody's
    hand-written card, not dirt."""
    d = deployment.deploy
    path = d / REL
    path.write_text(path.read_text(encoding="utf-8") + UPSTREAM_CARD.replace("beta", "zeta"),
                    encoding="utf-8", newline="\n")
    _upstream_registers_a_card(deployment.seed)
    p = ra.prove_auto_managed_file(REL, root=d, **_deps())
    assert p.proven is False
    assert "zeta" in p.reason and "not one the writer would register" in p.reason


# --------------------------------------------------------------------------- #
# 3. Cannot tell refuses; out of scope refuses
# --------------------------------------------------------------------------- #
def test_a_foreign_locally_modified_incoming_file_refuses(deployment):
    """The guard names TWO files and only one has a writer. Restoring the
    registry would not unblock the pull, and tools/x.py may be a human's work."""
    d = deployment.deploy
    _regenerate_locally(d)
    (d / "tools" / "x.py").write_text("X = 2\n", encoding="utf-8", newline="\n")
    _upstream_registers_a_card(deployment.seed, also_edit_tool=True)
    rep = dfm.freshness(root=str(d))
    assert rep["state"] == dfm.BLOCKED and sorted(rep["conflicts"]) == [REL, "tools/x.py"]

    rec = Recorder()
    r = ra.perform(ACT, REL, audit=rec, root=d, **_deps())
    assert r["outcome"] == ra.REFUSED and r["proven"] is False
    assert "tools/x.py" in r["reason"] and "NO writer" in r["reason"]
    assert rec.rows == []
    assert (d / "tools" / "x.py").read_text(encoding="utf-8") == "X = 2\n"
    assert "gamma" in _projects(d)

    # The plan lists BOTH, the foreign one as a refusal rather than an omission.
    plan = ra.plan(root=d, leases=SimpleNamespace(list_leases=lambda: []),
                   staleness_fn=lambda: {"state": "unmeasurable", "reason": "test"},
                   **_deps())
    mine = {c["target"]: c for c in plan["candidates"] if c["act"] == ACT}
    assert set(mine) == {REL, "tools/x.py"}
    assert mine["tools/x.py"]["proven"] is False
    assert "no enumerated writer" in mine["tools/x.py"]["reason"]


def test_nothing_incoming_is_nothing_to_restore(deployment):
    d = deployment.deploy
    _regenerate_locally(d)                       # dirty, but the branch has not moved
    p = ra.prove_auto_managed_file(REL, root=d, **_deps())
    assert p.proven is False
    assert "nothing to restore" in p.reason
    assert p.evidence["freshness"]["state"] == dfm.CURRENT


def test_an_unreadable_or_empty_board_cannot_prove(deployment):
    d = deployment.deploy
    _regenerate_locally(d)
    _upstream_registers_a_card(deployment.seed)
    before = (d / REL).read_text(encoding="utf-8")

    def boom():
        raise RuntimeError("db down")

    assert ra.prove_auto_managed_file(REL, root=d, board_fn=boom).proven is None
    assert ra.prove_auto_managed_file(REL, root=d, board_fn=lambda: {}).proven is None
    rec = Recorder()
    r = ra.perform(ACT, REL, audit=rec, root=d, board_fn=boom)
    assert r["outcome"] == ra.REFUSED and r["proven"] is None
    assert rec.rows == [] and (d / REL).read_text(encoding="utf-8") == before


def test_an_unmeasurable_deployment_cannot_prove():
    p = ra.prove_auto_managed_file(
        REL, root=Path("."), freshness_fn=lambda _r: {"state": "unmeasurable",
                                                      "reason": "no remote"})
    assert p.proven is None and "unmeasurable" in p.reason


def test_only_the_enumerated_set_is_in_scope():
    assert dict(ra.AUTO_MANAGED_FILES) == {REL: "tools.project.kanban_project_sync"}
    with pytest.raises(TypeError):
        ra.AUTO_MANAGED_FILES["args/anything.txt"] = "x"  # type: ignore[index]
    p = ra.prove_auto_managed_file("tools/x.py", root=Path("."),
                                   freshness_fn=lambda _r: pytest.fail("never asked"))
    assert p.proven is False and "not an enumerated auto-managed file" in p.reason


def test_another_refusal_reason_is_not_this_act(deployment):
    """`not on main`, `merge in progress`, ... are not a regenerable file's
    fault, even when the deployment is behind."""
    p = ra.prove_auto_managed_file(
        REL, root=deployment.deploy,
        freshness_fn=lambda _r: {"state": "blocked", "behind_by": 3,
                                 "reason": "merge in progress", "conflicts": []})
    assert p.proven is False and "another reason" in p.reason


# --------------------------------------------------------------------------- #
# 4. Dry run, plan, confirm
# --------------------------------------------------------------------------- #
def test_dry_run_proves_and_neither_audits_nor_acts(deployment):
    d = deployment.deploy
    _regenerate_locally(d)
    _upstream_registers_a_card(deployment.seed)
    before = (d / REL).read_text(encoding="utf-8")
    rec = Recorder()
    r = ra.perform(ACT, REL, audit=rec, root=d, dry_run=True, **_deps())
    assert r["outcome"] == ra.WOULD_APPLY and r["proven"] is True
    assert rec.rows == []
    assert (d / REL).read_text(encoding="utf-8") == before
    assert dfm.freshness(root=str(d))["state"] == dfm.BLOCKED


def test_the_plan_names_the_blocked_file_and_acts_on_nothing(deployment):
    d = deployment.deploy
    _regenerate_locally(d)
    _upstream_registers_a_card(deployment.seed)
    before = (d / REL).read_text(encoding="utf-8")
    plan = ra.plan(root=d, leases=SimpleNamespace(list_leases=lambda: []),
                   staleness_fn=lambda: {"state": "unmeasurable", "reason": "test"},
                   **_deps())
    assert plan["freshness_state"] == dfm.BLOCKED
    mine = [c for c in plan["candidates"] if c["act"] == ACT]
    assert len(mine) == 1 and mine[0]["target"] == REL and mine[0]["proven"] is True
    assert plan["provable"] == 1
    text = ra.render_plan(plan)
    assert "freshness=blocked" in text and f"READY {ACT}" in text
    assert (d / REL).read_text(encoding="utf-8") == before
    assert dfm.freshness(root=str(d))["state"] == dfm.BLOCKED


def test_confirm_reads_the_guard_not_the_act():
    ok = {"pulled": False, "reason": "already current"}
    still = {"pulled": False, "reason": ra.OVERLAP_REASON, "conflicts": [REL]}
    other = {"pulled": False, "reason": ra.OVERLAP_REASON, "conflicts": ["tools/x.py"]}
    assert ra.confirm_auto_managed_file(REL, root=Path("."), probe_fn=lambda _r: ok) is True
    assert ra.confirm_auto_managed_file(REL, root=Path("."), probe_fn=lambda _r: still) is False
    assert ra.confirm_auto_managed_file(REL, root=Path("."), probe_fn=lambda _r: other) is True
    assert ra.confirm_auto_managed_file(
        REL, root=Path("."), probe_fn=lambda _r: {"reason": "fetch failed"}) is None


# --------------------------------------------------------------------------- #
# 5. The comparison itself, on plain data
# --------------------------------------------------------------------------- #
_HEAD = [{"key": "a", "name": "A", "task_prefix": "a-",
          "epics": [{"key": "x", "title": "X", "priority": "high"}]}]
_Y = {"key": "y", "title": "Y", "priority": "medium"}
_B = {"key": "b", "name": "B Project", "task_prefix": "b-", "briefs": [],
      "epics": [{"key": "z", "title": "Z", "priority": "medium"},
                {"key": "w", "title": "W", "priority": "medium"}]}
_REGEN = [dict(_HEAD[0], epics=_HEAD[0]["epics"] + [_Y]), _B]


def test_regenerable_diff_on_plain_data():
    ok, _why, ev = ra.regenerable_diff(_HEAD, _HEAD, _REGEN)
    assert ok and ev["format_only"] is True
    ok, _why, ev = ra.regenerable_diff(_HEAD, _REGEN[:1], _REGEN)
    assert ok and ev["added_epics"] == ["a/y"] and ev["added_projects"] == []
    fewer = dict(_B, epics=_B["epics"][:1])      # the board grew since the dirt
    ok, _why, ev = ra.regenerable_diff(_HEAD, _HEAD + [fewer], _REGEN)
    assert ok and ev["added_projects"] == ["b"]

    assert ra.regenerable_diff(_HEAD, [], _REGEN)[0] is False
    stray = dict(_HEAD[0], epics=_HEAD[0]["epics"] + [{"key": "q", "title": "Q"}])
    assert "not one the writer would add" in ra.regenerable_diff(_HEAD, [stray], _REGEN)[1]
    renamed = [dict(_HEAD[0], name="A renamed")]
    assert "human edit" in ra.regenerable_diff(_HEAD, renamed, _REGEN)[1]
    unknown = _HEAD + [{"key": "c", "name": "C", "task_prefix": "c-", "epics": []}]
    assert "not one the writer would register" in ra.regenerable_diff(_HEAD, unknown, _REGEN)[1]
    edited_auto = _HEAD + [dict(_B, name="B renamed by hand")]
    assert "auto-registered" in ra.regenerable_diff(_HEAD, edited_auto, _REGEN)[1]
