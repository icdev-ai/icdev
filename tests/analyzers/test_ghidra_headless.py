# CUI // SP-CTI
"""tools/analyzers/ghidra_headless.py -- an OPTIONAL backend that reports its absence (xrv-bin-02).

THE FAKE GHIDRA IS A REAL EXECUTABLE, not a patched ``subprocess``. A test that
monkeypatches ``Popen`` proves the code calls ``Popen``, which was never in
doubt; it cannot catch a malformed ``-postScript`` argument order, a path that
needed quoting, or a launcher spelled ``.bat`` on Windows. So the fixture writes
an actual ``support/analyzeHeadless`` (or ``.bat``), a
``Ghidra/application.properties`` naming a 12.x version, and a fake PyGhidra
INTERPRETER (``ICDEV_GHIDRA_PYTHON``) that re-execs this interpreter on a
helper. The helper answers the ``-c "import pyghidra"`` probe and, on the run,
PARSES THE REAL ARGV to find where the export JSON should go. Everything
between ``decompile`` and the JSON on disk is therefore executed for real
(rea-eng-02: the run is ``<python> -m pyghidra.ghidra_launch ...``).

THE ASSERTIONS THAT MATTER ARE THE ONES ABOUT ABSENCE. ``unavailable`` must
never be confused with an empty result, and several tests here exist only to
prove that ``functions``/``imports``/``strings`` are ``None`` rather than
``[]`` on every path where nothing was measured.

THERE IS NO ``pytest.skip`` IN THIS FILE, and that is deliberate. The card
offered two doors for the opt-in live test -- an entry in
``args/ci_skip_census.txt``, or a second ungated file -- and both are worse
than a third: a gated test that SKIPS asserts nothing (CLAUDE.md: "A gated test
that SKIPS is an UNMEASURED test, not a passing one"), and a new ungated file
would need an ``args/ci_test_backlog.txt`` entry, a census that may only
shrink. ``test_the_live_backend_matches_this_hosts_actual_ghidra_state`` is
instead written so it ALWAYS runs and always asserts something real: with
``ICDEV_GHIDRA_HOME`` unset it asserts the reported absence, with it set it
asserts a real run. One test, no skip, no census entry.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tools.analyzers import ghidra_headless as gh

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The real package-manager roots, captured before the autouse fixture empties
#: them, so the live test can probe the host the way production does.
REAL_WELL_KNOWN_HOMES = gh.WELL_KNOWN_HOMES


# ---------------------------------------------------------------------------
# Fixture builders -- a real launcher on disk
# ---------------------------------------------------------------------------

#: Parses the REAL argv this module builds and does what the fixture asked.
#: Kept as source rather than importing anything, because it runs in a child
#: interpreter with an unknown working directory.
_HELPER = '''
import json, os, sys, time

argv = sys.argv[1:]
mode = argv[0]
rest = argv[1:]
here = os.path.dirname(os.path.abspath(sys.argv[0]))

# The `<python> -c "import pyghidra"` probe. Answered before anything else so
# it never writes the pid / argv records the run assertions read.
if rest[:1] == ["-c"]:
    sys.exit(1 if mode == "no_pyghidra" else 0)

open(os.path.join(here, "argv.json"), "w").write(json.dumps(rest))

# Own pid, recorded BEFORE anything blocks. On Windows the launcher is a .bat
# under cmd.exe and this is a grandchild, which is the process a parent-only
# kill would leave running.
try:
    handle = open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "helper.pid"), "w")
    handle.write(str(os.getpid()))
    handle.close()
except Exception:
    pass

# The export path is the argument straight after the script name, exactly
# where build_argv puts it. Finding it this way is what makes the test fail
# if that order ever changes.
out = None
maxf = maxs = None
for i, item in enumerate(rest):
    if item == "icdev_export.py":
        out = rest[i + 1]
        maxf = int(rest[i + 2])
        maxs = int(rest[i + 3])
        break

if mode == "prompt":
    input("Do you wish to install PyGhidra (y/n)? ")   # EOF under DEVNULL
    sys.exit(0)
if mode == "hang":
    time.sleep(120)
    sys.exit(0)
if mode == "nonzero":
    sys.stderr.write("fake ghidra: loader refused the artifact\\n")
    sys.exit(3)
if mode == "silent":
    sys.exit(0)            # exit 0 and write NOTHING
if mode == "garbage":
    open(out, "w").write("{not json at all")
    sys.exit(0)

payload = {
    "program": {"name": "fixture", "format": "Portable Executable"},
    "functions": [{"name": "entry", "address": "0x401000", "size": 42}],
    "functions_truncated": mode == "truncated",
    "imports": ["KERNEL32.DLL!CreateFileW"],
    "strings": [],                 # a MEASURED empty
    "strings_truncated": False,
    "entry_decompiled": "int entry(void) { return 0; }",
    "entry_basis": "parsed",
    "error": "strings: boom" if mode == "script_error" else None,
}
if mode == "script_error":
    payload["strings"] = None      # the section that failed stays null
open(out, "w").write(json.dumps(payload))
sys.exit(0)
'''


def _write_exec(path: Path, helper: Path, mode: str) -> Path:
    """A launcher that re-execs this interpreter on ``helper`` with ``mode``."""
    if os.name == "nt":
        path = path.with_suffix(".bat")
        path.write_text(
            '@echo off\r\n"%s" "%s" %s %%*\r\n' % (sys.executable, helper, mode),
            encoding="utf-8",
        )
    else:
        path.write_text(
            '#!/bin/sh\nexec "%s" "%s" %s "$@"\n' % (sys.executable, helper, mode),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return path


def _install_fake_ghidra(home: Path, mode: str, version: str | None = "12.1.4") -> Path:
    """A Ghidra install on disk, and a fake PyGhidra interpreter for it.

    Writes ``<home>/support/analyzeHeadless`` (what ``locate_ghidra`` finds),
    ``<home>/Ghidra/application.properties`` (what the version pin reads; None
    leaves it out) and ``<home>/pyghidra-python`` -- the interpreter
    ``decompile`` launches, named through ``ICDEV_GHIDRA_PYTHON``. The autouse
    fixture restores that variable after the test. Returns the launcher.
    """
    support = home / "support"
    support.mkdir(parents=True, exist_ok=True)
    helper = home / "helper.py"
    helper.write_text(_HELPER, encoding="utf-8")
    if version is not None:
        props = home / "Ghidra" / "application.properties"
        props.parent.mkdir(parents=True, exist_ok=True)
        props.write_text(
            "application.name=Ghidra\napplication.version=%s\n" % version, encoding="utf-8"
        )
    python = _write_exec(home / "pyghidra-python", helper, mode)
    os.environ[gh.ENV_PYTHON] = str(python)
    return _write_exec(support / gh.HEADLESS_STEM, helper, mode)


@pytest.fixture()
def artifact(tmp_path: Path) -> Path:
    """Any real file. The fake launcher never opens it -- what is under test is
    this module's handling, not Ghidra's parsing."""
    target = tmp_path / "sample.bin"
    target.write_bytes(b"MZ" + b"\x00" * 128)
    return target


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No ambient Ghidra configuration leaks into a test.

    ``ICDEV_GHIDRA_HOME`` is deliberately NOT cleared: the live test below
    reads it, and every other test passes ``ghidra_home=`` explicitly, which
    outranks it.
    """
    for name in (gh.ENV_ENABLED, gh.ENV_TIMEOUT, gh.ENV_MAX_CPU,
                 gh.ENV_MAX_FUNCTIONS, gh.ENV_MAX_STRINGS):
        monkeypatch.delenv(name, raising=False)
    # setenv RECORDS the prior value, so the fixture builder's direct write to
    # os.environ is undone at teardown too. "" reads as unset.
    monkeypatch.setenv(gh.ENV_PYTHON, "")
    # Every fake run is an authorized one unless a test says otherwise.
    monkeypatch.setenv(gh.ENV_AUTHORIZATION, "owned")
    # A host with a real /opt/ghidra must not answer a test about absence.
    monkeypatch.setattr(gh, "WELL_KNOWN_HOMES", ())


# ---------------------------------------------------------------------------
# AC1 -- it runs, and reports `ok`
# ---------------------------------------------------------------------------


def test_a_real_launcher_that_writes_the_export_reports_ok(tmp_path, artifact):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_OK
    assert report["exit_code"] == 0
    assert report["functions"] == [{"name": "entry", "address": "0x401000", "size": 42}]
    assert report["imports"] == ["KERNEL32.DLL!CreateFileW"]
    assert "int entry(void)" in report["entry_decompiled"]
    assert report["functions_basis"] == gh.BASIS_PARSED


def test_an_empty_section_the_script_walked_stays_an_empty_list(tmp_path, artifact):
    """`[]` from the script is a MEASURED empty and must survive as one.

    This is the other half of the None-vs-[] rule: the module must not "helpfully"
    turn a measured empty into None either, or a statically linked binary that
    genuinely imports nothing reads as unmeasured.
    """
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["strings"] == []
    assert report["strings_basis"] == gh.BASIS_PARSED


def test_the_launcher_receives_the_bounds_it_was_given(tmp_path, artifact):
    """The helper reads max_functions/max_strings out of the real argv, so a
    report at all proves the contract's argument order held."""
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")

    report = gh.decompile(
        str(artifact), ghidra_home=str(home), timeout_s=120,
        max_functions=7, max_strings=9,
    )

    assert report["status"] == gh.STATUS_OK
    assert report["limits"]["max_functions"] == 7
    assert report["limits"]["max_strings"] == 9


def test_a_hit_bound_is_truncated_and_says_which(tmp_path, artifact):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "truncated")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_TRUNCATED
    assert report["truncation"] == {"functions": True, "strings": False}
    assert "functions" in report["reason_detail"]
    # A truncated run still REPORTS what it got -- a hit bound is not a failure.
    assert report["functions"]


# ---------------------------------------------------------------------------
# AC2 -- no Ghidra: `unavailable`, naming the variable, measuring nothing
# ---------------------------------------------------------------------------


def test_no_ghidra_anywhere_is_unavailable_and_names_the_env_var(monkeypatch, artifact):
    monkeypatch.delenv(gh.ENV_GHIDRA_HOME, raising=False)
    monkeypatch.setattr(gh.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr("tools.dx.tool_index.shutil.which", lambda *a, **k: None)

    report = gh.decompile(str(artifact))

    assert report["status"] == gh.STATUS_UNAVAILABLE
    assert report["reason"] == gh.REASON_NOT_FOUND
    assert gh.ENV_GHIDRA_HOME in report["reason_detail"]


def test_unavailable_never_reports_an_empty_list(monkeypatch, artifact):
    """THE CENTRAL ASSERTION OF THIS MODULE.

    `[]` here would read as "Ghidra looked and found no functions" -- a claim
    about the artifact from a run that never opened it.
    """
    monkeypatch.delenv(gh.ENV_GHIDRA_HOME, raising=False)
    monkeypatch.setattr(gh.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr("tools.dx.tool_index.shutil.which", lambda *a, **k: None)

    report = gh.decompile(str(artifact))

    for key in ("functions", "imports", "strings", "entry_decompiled"):
        assert report[key] is None, f"{key} must be None, not {report[key]!r}"
        basis = report[f"{key}_basis"]
        assert basis == gh.BASIS_GHIDRA_UNAVAILABLE, f"{key}_basis is {basis!r}"
    assert report["truncation"] == {"functions": None, "strings": None}, (
        "a run that never happened cannot report that no bound was hit"
    )


def test_a_set_but_wrong_ghidra_home_is_its_own_reason(tmp_path, artifact):
    """Not merged into `not_found`: an operator who SET the variable and got the
    path wrong needs a different sentence from one who never set it."""
    report = gh.decompile(str(artifact), ghidra_home=str(tmp_path / "nowhere"))

    assert report["status"] == gh.STATUS_UNAVAILABLE
    assert report["reason"] == gh.REASON_HOME_INVALID
    assert "nowhere" in report["reason_detail"]


def test_a_wrong_ghidra_home_never_falls_through_to_path(tmp_path, artifact, monkeypatch):
    """A PATH hit would answer with a DIFFERENT install than the operator named."""
    other = tmp_path / "other"
    launcher = _install_fake_ghidra(other, "ok")
    monkeypatch.setattr(gh.shutil, "which", lambda *a, **k: str(launcher))

    report = gh.decompile(str(artifact), ghidra_home=str(tmp_path / "nowhere"))

    assert report["status"] == gh.STATUS_UNAVAILABLE
    assert report["headless"] is None


def test_the_backend_can_be_switched_off_and_off_is_reported(tmp_path, artifact, monkeypatch):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")
    monkeypatch.setenv(gh.ENV_ENABLED, "0")

    report = gh.decompile(str(artifact), ghidra_home=str(home))

    assert report["status"] == gh.STATUS_UNAVAILABLE
    assert report["reason"] == gh.REASON_DISABLED
    assert report["functions_basis"] == gh.BASIS_DISABLED


def test_an_absent_backend_outranks_an_absent_artifact(monkeypatch, tmp_path):
    """With no Ghidra the answer is `unavailable` whatever the path says.

    Reporting `source_unreadable` there sends a reader to look at their file
    when the missing thing is the decompiler.
    """
    monkeypatch.delenv(gh.ENV_GHIDRA_HOME, raising=False)
    monkeypatch.setattr(gh.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr("tools.dx.tool_index.shutil.which", lambda *a, **k: None)

    report = gh.decompile(str(tmp_path / "no-such-file.bin"))

    assert report["status"] == gh.STATUS_UNAVAILABLE


def test_an_unreadable_artifact_with_a_backend_present_is_an_error(tmp_path):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")

    report = gh.decompile(str(tmp_path / "no-such-file.bin"), ghidra_home=str(home))

    assert report["status"] == gh.STATUS_ERROR
    assert report["reason"] == gh.BASIS_SOURCE_UNREADABLE
    assert report["functions"] is None


# ---------------------------------------------------------------------------
# AC3 -- a hanging backend times out inside its budget, and the TREE dies
# ---------------------------------------------------------------------------


def test_a_hanging_launcher_times_out_within_its_budget(tmp_path, artifact):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "hang")

    started = time.monotonic()
    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=3)
    elapsed = time.monotonic() - started

    assert report["status"] == gh.STATUS_TIMEOUT
    # The fake sleeps 120s. Anything near that means the kill did not take.
    assert elapsed < 60, f"decompile did not return promptly: {elapsed:.1f}s"
    assert report["kill_method"], "a timeout must say HOW the tree was killed"
    assert report["functions"] is None
    assert report["functions_basis"] == gh.BASIS_TIMEOUT


def _pid_alive(pid: int) -> bool:
    """Is this pid still running? No third-party dependency, both platforms.

    Deliberately NOT ``wmic``: it is removed from Windows 11 build 26100+ (this
    host is 26200), so a probe written with it reports "no such process" for
    every pid and the assertion below would pass for a kill that never
    happened -- a test that cannot fail.
    """
    if os.name == "nt":
        probe = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
            capture_output=True, text=True, timeout=60,
        )
        return str(pid) in (probe.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_the_timeout_kills_the_grandchild_not_just_the_wrapper(tmp_path, artifact):
    """The launcher is a shell script / ``.bat`` that runs another process.

    ``proc.kill()`` alone reaps the WRAPPER and leaves that process running --
    the exact defect ``_kill_process_tree`` exists for, and on Windows it also
    keeps ``communicate()`` blocked on the pipes it inherited. The helper
    records its OWN pid before sleeping, so this asserts on the process that
    would survive rather than on the one we hold a handle to.
    """
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "hang")
    pid_file = home / "helper.pid"

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=5)
    assert report["status"] == gh.STATUS_TIMEOUT

    assert pid_file.is_file(), "the fake never started; this test proved nothing"
    pid = int(pid_file.read_text(encoding="utf-8").strip())

    # A handle can outlive its process briefly on Windows; the sleep is 120s, so
    # anything still alive after this grace is genuinely not dead.
    deadline = time.monotonic() + 15
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.5)
    assert not _pid_alive(pid), "the grandchild (pid %d) survived the tree kill" % pid


def test_the_analysis_timeout_is_set_below_the_wall_budget():
    """Ghidra's own budget must expire FIRST, or a slow analysis is killed with
    no export to label instead of yielding a partial one."""
    argv = gh.build_argv(
        "python", "home", Path("proj"), Path("t.bin"), Path("o.json"),
        analysis_timeout=540, max_cpu=2, max_functions=10, max_strings=10,
    )
    index = argv.index("-analysisTimeoutPerFile")
    assert int(argv[index + 1]) == 540
    assert 540 < gh.DEFAULT_TIMEOUT_SECONDS

    # And the arithmetic that produces it, at the default.
    expected = gh.DEFAULT_TIMEOUT_SECONDS - gh.GHIDRA_STARTUP_OVERHEAD_SECONDS
    assert expected == 540
    # A tiny wall budget still leaves Ghidra a workable floor rather than 0.
    assert gh.MIN_ANALYSIS_TIMEOUT_SECONDS > 0


# ---------------------------------------------------------------------------
# Failure shapes that must never read as "it ran and found nothing"
# ---------------------------------------------------------------------------


def test_a_nonzero_exit_is_an_error_carrying_the_code_and_the_stderr(tmp_path, artifact):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "nonzero")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_ERROR
    assert report["reason"] == "nonzero_exit"
    assert report["exit_code"] == 3
    assert "loader refused" in report["stderr_tail"]
    assert report["functions"] is None


def test_exit_zero_with_no_export_is_an_error_not_an_empty_result(tmp_path, artifact):
    """Ghidra's launcher exits 0 for several things that are NOT a successful
    analysis, so this can never be read as "there was nothing to report"."""
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "silent")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_ERROR
    assert report["reason"] == "export_absent"
    assert report["exit_code"] == 0
    assert report["functions"] is None
    assert report["functions_basis"] == gh.BASIS_EXPORT_ABSENT


def test_unparseable_json_is_an_error_of_its_own(tmp_path, artifact):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "garbage")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_ERROR
    assert report["reason"] == "export_malformed"
    assert report["functions_basis"] == gh.BASIS_EXPORT_MALFORMED


def test_a_partial_script_failure_keeps_what_it_did_measure(tmp_path, artifact):
    """Discarding a real function list because the string extractor raised
    would throw away measured evidence."""
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "script_error")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["reason"] == "script_error"
    assert report["functions"], "a measured section must survive a sibling's failure"
    assert report["strings"] is None
    assert report["strings_basis"] == gh.BASIS_SCRIPT_ERROR
    # It measured something, so it is not a whole-run error.
    assert report["status"] != gh.STATUS_ERROR


# ---------------------------------------------------------------------------
# Report shape -- a caller must never have to ask whether a key exists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["ok", "nonzero", "silent", "garbage", "truncated"])
def test_every_status_carries_every_key(tmp_path, artifact, mode):
    """A missing key is how `report.get("functions") or []` gets written, which
    puts the empty list back in by the side door."""
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, mode)

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    for key in (
        "path", "status", "reason", "reason_detail", "functions", "functions_basis",
        "imports", "imports_basis", "strings", "strings_basis", "entry_decompiled",
        "entry_decompiled_basis", "ghidra_version", "ghidra_home", "headless",
        "headless_source", "truncation", "exit_code", "duration_seconds",
        "kill_method", "stderr_tail", "limits", "generated_at",
        "runtime", "provenance", "evidence_basis", "entry_decompiled_confidence",
    ):
        assert key in report, f"{mode}: missing {key}"
    assert report["status"] in gh.STATUSES


def test_a_malformed_env_bound_is_ignored_and_the_value_used_is_reported(
    monkeypatch, tmp_path, artifact
):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")
    monkeypatch.setenv(gh.ENV_MAX_FUNCTIONS, "not-a-number")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["limits"]["max_functions"] == gh.DEFAULT_MAX_FUNCTIONS


def test_the_version_is_read_from_application_properties_not_from_a_jvm(tmp_path):
    home = tmp_path / "ghidra"
    props = home / "Ghidra" / "application.properties"
    props.parent.mkdir(parents=True)
    props.write_text("application.name=Ghidra\napplication.version=11.1.2\n", encoding="utf-8")

    assert gh.ghidra_version(str(home)) == "11.1.2"
    # No install, no guess -- None, never a placeholder.
    assert gh.ghidra_version(str(tmp_path / "nope")) is None
    assert gh.ghidra_version(None) is None


def test_the_temporary_project_directory_does_not_survive_the_run(tmp_path, artifact):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")
    scratch = Path(tempfile.gettempdir())

    before = set(scratch.glob("icdev-ghidra-*"))
    gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)
    after = set(scratch.glob("icdev-ghidra-*"))

    # A SUBSET, not equality: a concurrent session on this host may hold one of
    # its own, and failing on somebody else's directory would make this test
    # flake for a reason that has nothing to do with the code under test.
    assert after <= before, f"a project directory leaked: {sorted(after - before)}"


# ---------------------------------------------------------------------------
# The declaration, and the Ghidra-side script
# ---------------------------------------------------------------------------


def test_the_export_script_ships_beside_the_module():
    """``-scriptPath`` points at a directory that must exist in BOTH trees, or
    the packaged copy runs with no script to post."""
    assert (gh.SCRIPT_DIR / gh.EXPORT_SCRIPT_NAME).is_file()
    packaged = REPO_ROOT / "icdev" / "tools" / "analyzers" / "ghidra_scripts" / gh.EXPORT_SCRIPT_NAME
    assert packaged.is_file(), "the icdev/ mirror has no export script"


def test_the_export_script_imports_nothing_third_party_but_ghidra():
    """It runs inside Ghidra under whichever CPython carries ``pyghidra``,
    and nothing else can be assumed installed there.

    Read from the AST, because a behavioural test cannot run this file at all
    on a host with no Ghidra -- which is every host this suite runs on.
    """
    import ast

    source = (gh.SCRIPT_DIR / gh.EXPORT_SCRIPT_NAME).read_text(encoding="utf-8")
    allowed = {"json", "sys", "traceback", "ghidra"}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] in allowed, node.module


def test_the_analyzer_is_declared_and_binds_only_the_observable():
    from tools.analyzers.contract import load_contract

    declared = {d.key: d for d in load_contract().analyzers}
    assert "ghidra_decompile" in declared
    entry = declared["ghidra_decompile"]
    assert entry.module == "tools.analyzers.ghidra_headless"
    assert entry.entrypoint == "decompile"
    assert entry.accepts == ("binary",)
    assert entry.sandbox == "sandboxed"
    assert entry.binding.observable_arg == "path"


def test_analyze_headless_is_declared_in_the_tool_index():
    """It was `excluded` with the reason "declaring it now is a capability
    declared before its consumer exists". This module is that consumer."""
    from tools.dx import tool_index

    index = tool_index.load_index()
    names = {e["name"] for e in tool_index.entries(index)}
    assert "analyzeHeadless" in names
    excluded = {e.get("name") for e in (index.get("excluded") or [])}
    assert "analyzeHeadless" not in excluded, "declared and excluded at once"
    # which() must therefore ANSWER rather than raise KeyError.
    tool_index.which("analyzeHeadless")


def test_the_kill_helper_is_imported_and_never_re_implemented():
    """Two spellings of "kill the tree" is how one policy's two halves come to
    disagree. Read from the AST: a behavioural test over today's code would
    still pass for a future edit that inlines a second copy.
    """
    import ast

    source = Path(gh.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "tools.genesis.reflexes.kanban"
        and any(a.name == "_kill_process_tree" for a in node.names)
        for node in ast.walk(tree)
    )
    assert imported, "the tree-kill helper must be imported, not re-implemented"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            assert name not in ("killpg", "taskkill"), "a second tree-killer was inlined"


# ---------------------------------------------------------------------------
# The live backend -- ALWAYS runs, never skips
# ---------------------------------------------------------------------------


def test_the_live_backend_matches_this_hosts_actual_ghidra_state(artifact, monkeypatch):
    """Probes THIS host the way production does, and asserts either way.

    A ``pytest.skip`` here would assert nothing on every host in CI and read as
    a pass. Instead: with no Ghidra on the host, the reported absence IS the
    assertion; with a runnable Ghidra 12 + pyghidra, a real run happens.
    """
    monkeypatch.setattr(gh, "WELL_KNOWN_HOMES", REAL_WELL_KNOWN_HOMES)
    monkeypatch.delenv(gh.ENV_PYTHON, raising=False)
    located = gh.locate_ghidra()
    report = gh.decompile(str(artifact), timeout_s=900)

    assert report["status"] in gh.STATUSES

    if not located["headless"]:
        # The state in CI, and it must be legible as such.
        assert report["status"] == gh.STATUS_UNAVAILABLE
        assert report["functions"] is None
        assert gh.ENV_GHIDRA_HOME in (report["reason_detail"] or "")
        return

    assert report["headless"] == located["headless"]
    if report["status"] == gh.STATUS_UNAVAILABLE:
        # Found but not runnable: it must say WHICH install step is missing.
        assert report["reason"] in (
            gh.REASON_VERSION_UNDETERMINED,
            gh.REASON_VERSION_UNSUPPORTED,
            gh.REASON_PYGHIDRA_MISSING,
            gh.REASON_PYTHON_INVALID,
        ), report["reason_detail"]
        assert report["functions"] is None
        return
    assert report["ghidra_version"], "a runnable install must report its version"
    if report["status"] in (gh.STATUS_OK, gh.STATUS_TRUNCATED):
        assert report["functions"] is not None
        assert report["provenance"]["authorization_basis"] == "owned"


def test_the_cli_produces_a_report_and_exits_zero(capsys, artifact):
    """`unavailable` IS a report: exit 0. Exit 2 is reserved for "no report
    could be produced at all"."""
    assert gh.main([str(artifact), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] in gh.STATUSES
    assert "limits" in payload

    assert gh.main([str(artifact)]) == 0
    assert "ghidra headless:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# rea-eng-02 -- Ghidra 12 / PyGhidra, the version pin, the authorization rail
# ---------------------------------------------------------------------------


def test_the_run_is_a_pyghidra_launch_of_analyze_headless(tmp_path, artifact):
    """Ghidra 12 runs a ``.py`` postScript only under PyGhidra. The helper
    records the argv it was REALLY given, after the interpreter."""
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_OK
    argv = json.loads((home / "argv.json").read_text(encoding="utf-8"))
    assert argv[:5] == [
        "-m", gh.PYGHIDRA_LAUNCH_MODULE, "--install-dir", str(home), gh.HEADLESS_CLASS,
    ]
    assert gh.EXPORT_SCRIPT_NAME in argv and "-deleteProject" in argv
    assert report["runtime"]["engine"] == "pyghidra"
    assert report["runtime"]["python_source"] == gh.ENV_PYTHON


@pytest.mark.parametrize("version", ["11.4.2", "10.1"])
def test_a_ghidra_older_than_the_pin_is_unavailable_and_runs_nothing(
    tmp_path, artifact, version
):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok", version=version)

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_UNAVAILABLE
    assert report["reason"] == gh.REASON_VERSION_UNSUPPORTED
    assert version in report["reason_detail"]
    assert report["functions"] is None
    assert not (home / "argv.json").exists(), "a pre-12 Ghidra was launched"


def test_an_unreadable_version_is_its_own_reason(tmp_path, artifact):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok", version=None)

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_UNAVAILABLE
    assert report["reason"] == gh.REASON_VERSION_UNDETERMINED


def test_twelve_point_anything_passes_the_pin():
    assert gh._version_tuple("12.0") >= gh.MIN_GHIDRA_VERSION
    assert gh._version_tuple("12.1.4") == (12, 1, 4)
    assert gh._version_tuple("12.1_DEV") == (12, 1)
    assert gh._version_tuple("11.4.2")[:2] < gh.MIN_GHIDRA_VERSION
    assert gh._version_tuple("") is None and gh._version_tuple("abc") is None


def test_a_declared_python_without_pyghidra_never_falls_through(tmp_path, artifact):
    """Like ICDEV_GHIDRA_HOME: the interpreter the operator NAMED is the answer."""
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "no_pyghidra")

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_UNAVAILABLE
    assert report["reason"] == gh.REASON_PYTHON_INVALID
    assert report["runtime"]["python"] is None
    assert report["functions"] is None


def test_no_interpreter_with_pyghidra_names_the_bundled_wheel(tmp_path, artifact, monkeypatch):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")
    monkeypatch.setenv(gh.ENV_PYTHON, "")
    monkeypatch.setattr(gh, "_probe_pyghidra", lambda python: (False, "No module named pyghidra"))

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_UNAVAILABLE
    assert report["reason"] == gh.REASON_PYGHIDRA_MISSING
    assert "pypkg/dist" in report["reason_detail"]
    assert report["functions_basis"] == gh.BASIS_GHIDRA_UNAVAILABLE


def test_a_launcher_that_prompts_gets_eof_not_the_wall_budget(tmp_path, artifact):
    """``pyghidraRun`` asks on first run whether to pip-install. A child that
    reads stdin must meet EOF at once -- never hold the run until timeout."""
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "prompt")

    started = time.monotonic()
    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=60)

    assert time.monotonic() - started < 45
    assert report["status"] == gh.STATUS_ERROR
    assert report["reason"] == "nonzero_exit"
    assert "EOFError" in (report["stderr_tail"] or "")


def test_a_package_install_root_is_found_without_any_configuration(
    tmp_path, artifact, monkeypatch
):
    """Arch's ``ghidra`` lives in /opt/ghidra with no analyzeHeadless on PATH."""
    home = tmp_path / "opt-ghidra"
    _install_fake_ghidra(home, "ok")
    monkeypatch.delenv(gh.ENV_GHIDRA_HOME, raising=False)
    monkeypatch.setattr(gh.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr("tools.dx.tool_index.shutil.which", lambda *a, **k: None)
    monkeypatch.setattr(gh, "WELL_KNOWN_HOMES", (str(home),))

    report = gh.decompile(str(artifact), timeout_s=120)

    assert report["headless_source"] == "well_known_home"
    assert report["ghidra_home"] == str(home)
    assert report["status"] == gh.STATUS_OK


def test_a_run_without_an_authorization_basis_is_refused_before_the_jvm(
    tmp_path, artifact, monkeypatch
):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")
    monkeypatch.delenv(gh.ENV_AUTHORIZATION, raising=False)

    report = gh.decompile(str(artifact), ghidra_home=str(home), timeout_s=120)

    assert report["status"] == gh.STATUS_ERROR
    assert report["reason"] == gh.BASIS_AUTHORIZATION_MISSING
    assert report["functions"] is None
    assert report["functions_basis"] == gh.BASIS_AUTHORIZATION_MISSING
    # The refusal still names WHICH artifact it refused.
    assert report["provenance"]["target_sha256"] == hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()
    assert not (home / "argv.json").exists(), "the JVM was started without a basis"


def test_an_unknown_authorization_basis_is_no_basis(tmp_path, artifact):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")

    report = gh.decompile(
        str(artifact), ghidra_home=str(home), timeout_s=120, authorization_basis="trust me"
    )

    assert report["reason"] == gh.BASIS_AUTHORIZATION_MISSING
    assert report["provenance"]["authorization_basis"] is None


def test_a_run_records_provenance_and_marks_decompiled_output(tmp_path, artifact):
    home = tmp_path / "ghidra"
    _install_fake_ghidra(home, "ok")

    report = gh.decompile(
        str(artifact), ghidra_home=str(home), timeout_s=120,
        authorization_basis="customer-authorized",
    )

    assert report["status"] == gh.STATUS_OK
    prov = report["provenance"]
    assert prov["target_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert prov["authorization_basis"] == "customer-authorized"
    assert prov["analysis"] == "local"
    assert report["evidence_basis"]["entry_decompiled"] == "decompiled"
    assert report["evidence_basis"]["functions"] == "observed"
    assert report["entry_decompiled_confidence"] == gh.DECOMPILED_CONFIDENCE
    rendered = gh._render(report)
    assert "DECOMPILED" in rendered and "not recovered source" in rendered


def test_the_export_script_declares_pyghidra_and_never_dict_gets_the_host():
    """``PyGhidraScript`` resolves ``currentProgram`` in ``__missing__``, which
    ``dict.get`` bypasses: ``_HOST.get("currentProgram")`` is None on a run
    that HAS a program. Read from the AST -- no host here can run the file."""
    import ast

    source = (gh.SCRIPT_DIR / gh.EXPORT_SCRIPT_NAME).read_text(encoding="utf-8")
    assert "# @runtime PyGhidra" in source.splitlines()[:5]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            target = node.func.value
            if isinstance(target, ast.Name) and target.id == "_HOST":
                assert node.func.attr != "get", "host globals must be subscripted"
