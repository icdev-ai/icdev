# CUI // SP-CTI
"""A process is stale only if code IT EXECUTES changed (autonomy-id-02).

autonomy-id-01 made each process record the commit it booted from. This asks
whether anything it actually runs has changed since.

THE INVARIANT THAT DECIDES WHETHER THIS IS USABLE AT ALL: staleness is keyed on
the process's own IMPORT CLOSURE, never on the tip. `main` takes several commits
an hour here, so a detector keyed on the tip marks every process stale within
minutes of every merge, and a signal that fires constantly is ignored inside a
day — the way a check earns itself a `|| true`. A change outside the closure
must read CURRENT, and that is the first thing tested below.

THE INVARIANT THAT DECIDES WHETHER IT CAN BE TRUSTED: `unmeasurable` is never
folded into `current`. There are four distinct ways this cannot answer — no
recorded version, no module, an unknown commit, an underivable closure — and
each must stay visible, because "nobody could check" and "checked and fine"
justify opposite actions.

Both fail GREEN if broken: a tip-keyed detector still returns verdicts, and an
unmeasurable smoothed into current still returns a clean report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness import code_staleness as cs  # noqa: E402


def _row(**kw):
    base = {"session_id": "s1", "module": "tools.x", "pid": 1,
            "code_version": "sha-old", "code_dirty": 0}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# 1. Keyed on the CLOSURE, not the tip — the whole design
# --------------------------------------------------------------------------- #
def test_a_change_outside_the_closure_is_not_stale():
    """The invariant that makes the signal worth reading. Without it every
    process reads stale minutes after any merge."""
    result = cs.assess_process(
        _row(), changed={"tools/unrelated/canvas.py"}, closure={"tools/x.py"})
    assert result["verdict"] == cs.CURRENT, (
        "an unrelated file changed and the process was called stale — this "
        "detector would fire on every merge"
    )


def test_a_change_inside_the_closure_is_stale_and_names_the_files():
    """A verdict with no evidence cannot be acted on and gets dismissed."""
    result = cs.assess_process(
        _row(), changed={"tools/x.py", "tools/other.py"},
        closure={"tools/x.py", "tools/y.py"})
    assert result["verdict"] == cs.STALE
    assert result["changed_in_closure"] == ["tools/x.py"]
    assert result["changed_count"] == 1


def test_nothing_changed_at_all_is_current():
    result = cs.assess_process(_row(), changed=set(), closure={"tools/x.py"})
    assert result["verdict"] == cs.CURRENT


# --------------------------------------------------------------------------- #
# 2. The four ways it cannot answer — none becomes `current`
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("row,changed,closure,why", [
    (_row(code_version=None), set(), {"tools/x.py"}, "no recorded version"),
    (_row(module=None), set(), {"tools/x.py"}, "no module"),
    (_row(), None, {"tools/x.py"}, "git could not compare"),
    (_row(), set(), None, "closure underivable"),
])
def test_every_unmeasurable_cause_stays_unmeasurable(row, changed, closure, why):
    result = cs.assess_process(row, changed, closure)
    assert result["verdict"] == cs.UNMEASURABLE, why
    assert result.get("reason"), "an unmeasurable verdict with no reason is unactionable"


def test_an_unknown_commit_is_not_read_as_nothing_changed():
    """`changed_files` returns None for a commit git does not know — a shallow
    clone, an ICDEV_BUILD_ID, a branch never fetched. Reading that as an empty
    set would report CURRENT for a process nobody could place."""
    class _R:
        returncode = 1
        stdout = ""

    assert cs.changed_files("nosuchsha", "origin/main", ROOT,
                            runner=lambda *_a, **_k: _R()) is None


def test_no_diff_output_is_an_empty_set_not_none():
    """Distinct from the above: git answered, and the answer was 'nothing'."""
    calls = {"n": 0}

    class _Ok:
        returncode = 0
        stdout = ""

    def _run(args, _root):
        calls["n"] += 1
        return _Ok()

    assert cs.changed_files("a", "b", ROOT, runner=_run) == set()


# --------------------------------------------------------------------------- #
# 3. Dirtiness is carried, never merged into the verdict
# --------------------------------------------------------------------------- #
def test_a_dirty_tree_does_not_change_the_verdict():
    """The verdict answers "has the recorded commit been superseded". A process
    booted from a modified tree is not running the tree its SHA names, so the
    reader needs BOTH facts — overloading one field would hide whichever the
    reader was not looking for."""
    clean = cs.assess_process(_row(code_dirty=0), set(), {"tools/x.py"})
    dirty = cs.assess_process(_row(code_dirty=1), set(), {"tools/x.py"})
    assert clean["verdict"] == dirty["verdict"] == cs.CURRENT
    assert clean["dirty"] is False and dirty["dirty"] is True


def test_an_unmeasured_dirty_flag_is_none_not_false():
    """`code_dirty` is NULL when the check was declined — reporting False there
    would be a clean tree nobody measured."""
    assert cs.assess_process(_row(code_dirty=None), set(), {"tools/x.py"})["dirty"] is None


# --------------------------------------------------------------------------- #
# 4. The closure walk
# --------------------------------------------------------------------------- #
def _mkmod(root: Path, dotted: str, body: str = "") -> Path:
    path = root.joinpath(*dotted.split(".")).with_suffix(".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_the_closure_is_transitive(tmp_path):
    _mkmod(tmp_path, "tools.a", "import tools.b\n")
    _mkmod(tmp_path, "tools.b", "from tools.c import thing\n")
    _mkmod(tmp_path, "tools.c", "x = 1\n")

    files = cs.import_closure("tools.a", tmp_path)["files"]
    assert files == {"tools/a.py", "tools/b.py", "tools/c.py"}, (
        "a transitively imported file is code the process runs; missing it "
        "shrinks the closure and biases the verdict toward `current`"
    )


def test_a_cycle_terminates(tmp_path):
    _mkmod(tmp_path, "tools.a", "import tools.b\n")
    _mkmod(tmp_path, "tools.b", "import tools.a\n")
    assert cs.import_closure("tools.a", tmp_path)["files"] == {"tools/a.py", "tools/b.py"}


def test_third_party_and_stdlib_are_not_walked(tmp_path):
    _mkmod(tmp_path, "tools.a", "import os\nimport requests\nimport json\n")
    assert cs.import_closure("tools.a", tmp_path)["files"] == {"tools/a.py"}, (
        "a merge to this repository cannot change the stdlib, and following "
        "site-packages turns a bounded walk into a crawl"
    )


def test_an_unparseable_file_still_counts_itself(tmp_path):
    """It is still code the process runs. Dropping it would shrink the closure
    in the direction of a false `current`."""
    _mkmod(tmp_path, "tools.a", "import tools.broken\n")
    _mkmod(tmp_path, "tools.broken", "def (((\n")
    got = cs.import_closure("tools.a", tmp_path)
    assert "tools/broken.py" in got["files"]
    assert got["unparseable"], "an unparseable file was silently dropped"


def test_truncation_is_reported_never_silent(tmp_path):
    """A silently capped closure could miss the changed file and report
    `current` — reintroducing the false reassurance this module refuses."""
    _mkmod(tmp_path, "tools.a", "import tools.b\n")
    _mkmod(tmp_path, "tools.b", "import tools.c\n")
    _mkmod(tmp_path, "tools.c", "x = 1\n")
    got = cs.import_closure("tools.a", tmp_path, max_files=2)
    assert got["truncated"] is True


def test_an_unknown_module_is_unresolved_not_an_empty_closure(tmp_path):
    """An empty closure intersects nothing and would report CURRENT."""
    got = cs.import_closure("tools.nope", tmp_path)
    assert got["unresolved"] is True
    assert cs.assess_process(_row(module="tools.nope"), set(), None)["verdict"] == (
        cs.UNMEASURABLE)


def test_the_walk_parses_and_never_imports():
    """Importing `tools.genesis.daemon` to learn what it imports would start a
    daemon, and importing the Cortex stack is heaviest on exactly the
    deployment where something is broken."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(cs.import_closure)))
    fn = tree.body[0]
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    code = "\n".join(ast.dump(n) for n in body)
    for forbidden in ("import_module", "__import__", "exec_module"):
        assert forbidden not in code, f"the closure walk reached for {forbidden}"


# --------------------------------------------------------------------------- #
# 5. The fleet report passes through what it cannot measure
# --------------------------------------------------------------------------- #
def test_an_unmeasurable_fleet_is_not_a_clean_fleet():
    rep = cs.report(processes_fn=lambda: {"state": "unmeasurable",
                                          "reason": "db down", "processes": []})
    assert rep["state"] == "unmeasurable"
    assert rep["stale"] is None and rep["current"] is None, (
        "an unmeasurable fleet reported counts, which read as measured zeros"
    )


def test_no_live_processes_is_distinct_from_measured_zero():
    rep = cs.report(processes_fn=lambda: {"state": "no_live_processes",
                                          "processes": []})
    assert rep["state"] == "no_live_processes"
    assert rep["stale"] is None


def test_the_detector_does_not_restart_anything():
    """Restarting a stale daemon is autonomy-act-03's enumerated `restore` tier,
    performed by the supervisor with an audit row. A detector that restarts
    things is an unaudited actuator."""
    import inspect

    src = inspect.getsource(cs)
    for forbidden in ("respawn", "execv", "Popen", "kill", "terminate"):
        assert forbidden not in src, f"the detector reached for {forbidden}"
