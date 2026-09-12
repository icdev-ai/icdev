# CUI // SP-CTI
"""tools/analyzers/ghidra_headless.py -- an OPTIONAL backend that reports its absence (xrv-bin-02).

THE FAKE GHIDRA IS A REAL EXECUTABLE, not a patched ``subprocess``. A test that
monkeypatches ``Popen`` proves the code calls ``Popen``, which was never in
doubt; it cannot catch a malformed ``-postScript`` argument order, a path that
needed quoting, or a launcher spelled ``.bat`` on Windows. So the fixture writes
an actual ``support/analyzeHeadless`` (or ``.bat``) that re-execs this
interpreter on a helper, and the helper PARSES THE REAL ARGV to find where the
export JSON should go. Everything between ``decompile`` and the JSON on disk is
therefore executed for real.

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


def _install_fake_ghidra(home: Path, mode: str) -> Path:
    """Write a runnable ``<home>/support/analyzeHeadless`` for this platform."""
    support = home / "support"
    support.mkdir(parents=True, exist_ok=True)
    helper = home / "helper.py"
    helper.write_text(_HELPER, encoding="utf-8")

    if os.name == "nt":
        launcher = support / "analyzeHeadless.bat"
        launcher.write_text(
            '@echo off\r\n"%s" "%s" %s %%*\r\n' % (sys.executable, helper, mode),
            encoding="utf-8",
        )
    else:
        launcher = support / "analyzeHeadless"
        launcher.write_text(
            '#!/bin/sh\nexec "%s" "%s" %s "$@"\n' % (sys.executable, helper, mode),
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return launcher


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
        "headless", Path("proj"), Path("t.bin"), Path("o.json"),
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
    """It runs under Jython 2.7 inside Ghidra: no pip, no site-packages.

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


def test_the_live_backend_matches_this_hosts_actual_ghidra_state(artifact):
    """Opt-in by ``ICDEV_GHIDRA_HOME``, but asserting either way.

    A ``pytest.skip`` here would assert nothing on every host in CI and read as
    a pass. Instead: with no Ghidra declared, the reported absence IS the
    assertion; with Ghidra declared, a real ``analyzeHeadless`` runs.
    """
    home = os.environ.get(gh.ENV_GHIDRA_HOME)
    report = gh.decompile(str(artifact), timeout_s=900)

    assert report["status"] in gh.STATUSES

    if not home:
        # The state on this host and in CI, and it must be legible as such.
        assert report["status"] == gh.STATUS_UNAVAILABLE
        assert report["functions"] is None
        assert gh.ENV_GHIDRA_HOME in (report["reason_detail"] or "")
        return

    # A real Ghidra. `unavailable` is now the one answer that would be wrong --
    # the operator named an install, so it must have been found.
    assert report["status"] != gh.STATUS_UNAVAILABLE, report["reason_detail"]
    assert report["headless"], "a declared ICDEV_GHIDRA_HOME must resolve a launcher"
    if report["status"] in (gh.STATUS_OK, gh.STATUS_TRUNCATED):
        assert report["functions"] is not None
        assert report["ghidra_version"], "a real install must report its version"


def test_the_cli_produces_a_report_and_exits_zero(capsys, artifact):
    """`unavailable` IS a report: exit 0. Exit 2 is reserved for "no report
    could be produced at all"."""
    assert gh.main([str(artifact), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] in gh.STATUSES
    assert "limits" in payload

    assert gh.main([str(artifact)]) == 0
    assert "ghidra headless:" in capsys.readouterr().out
