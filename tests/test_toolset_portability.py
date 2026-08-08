"""OS-portability gate for the owned build agent's toolset (hgx-port-02).

``tools/genesis/rubric_build_tools.py`` is the toolset the rubric-gated kanban
build loop hands to an autonomous agent. It executes on whatever host the
kanban runner happens to live on — Windows in development, Linux in CI and in
the container images. Anything in it that assumes a POSIX shell is a defect
that only shows up on the *other* OS, i.e. never in a review.

Two invariants are pinned here, and both are checked twice — once statically
against the source (so an unreachable branch cannot hide) and once at runtime
against every handler in the toolset (so a helper added later is covered
without anyone remembering to update a list):

1. **shell=False, list argv.** ``shell=True`` hands the command line to
   ``cmd.exe`` on Windows and ``/bin/sh`` on POSIX. The two disagree about
   quoting, about ``&&``, about globbing and about how a path with a space is
   split, so the same string is a different command on each. A list argv passed
   with ``shell=False`` is the one form that means the same thing everywhere.
2. **No POSIX-only binary.** ``grep``/``sed``/``find``/``sh`` do not exist on a
   stock Windows host (or exist as unrelated programs — ``find.exe`` and
   ``sort.exe`` are Windows utilities with different semantics). A command that
   names one is not portable even with a list argv.

The companion Windows CI job (``.github/workflows/icdev-ci.yml``, job
``test-windows``) is what makes these assertions worth writing: before it,
every OS-portability defect in this module was structurally invisible to the
pipeline, because all nine jobs ran on ubuntu-latest.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.genesis.rubric_build_tools import _BUILD_ALLOWED_PREFIXES, build_worktree_toolset

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Both copies must hold: tools/ is what the kanban reflex imports in a checkout,
# icdev/tools/ is what a pip-installed deployment runs. A gate that only reads
# one of them passes while the shipped copy drifts.
_SOURCES = [
    _REPO_ROOT / "tools" / "genesis" / "rubric_build_tools.py",
    _REPO_ROOT / "icdev" / "tools" / "genesis" / "rubric_build_tools.py",
]

# Executables that are absent from a stock Windows host, or that exist there as
# an unrelated program with different semantics (find, sort). `git` and
# `python` are deliberately NOT here — both are cross-platform and both are
# used by this toolset on purpose.
_POSIX_ONLY = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "csh",
    "grep", "egrep", "fgrep", "rg", "sed", "awk", "gawk", "perl",
    "find", "xargs", "cat", "ls", "rm", "cp", "mv", "ln", "mkdir", "rmdir",
    "chmod", "chown", "touch", "which", "head", "tail", "wc", "cut", "tr",
    "sort", "uniq", "tee", "kill", "killall", "ps", "df", "du", "env",
    "make", "curl", "wget", "tar", "gzip", "diff", "basename", "dirname",
})

# Every tool in the toolset gets driven at runtime. Adding a tool without
# adding an entry here fails `test_every_tool_has_a_portability_input`, which
# is the point: a new handler must be considered, not silently exempted.
_TOOL_INPUTS: dict[str, dict] = {
    "read_file": {"path": "sample.txt"},
    "list_files": {"path": "."},
    "write_file": {"path": "written.txt", "content": "alpha\n"},
    "patch_file": {"path": "sample.txt", "old_string": "alpha", "new_string": "ALPHA"},
    "grep_files": {"pattern": "alpha"},
    "search_files": {"pattern": "**/*.txt"},
    "git_diff": {"stat": True},
    "run_command": {"command": "python -m pytest --version", "timeout": 30},
    "done": {},
}


def _make_tools_dir(root: Path) -> Path:
    """Create the `tools/` directory a `python tools/...` command walks through.

    The allowlist forces every command to start with an allowlisted prefix, so a
    script in the worktree root is reached as `python tools/../script.py`. POSIX
    resolves `tools/..` by actually walking into `tools`, so a missing directory
    is ENOENT; Windows collapses the `..` lexically and never looks. A fixture
    that skips this mkdir passes on Windows and fails on Linux.
    """
    target = root / "tools"
    target.mkdir(exist_ok=True)
    return target


# ── Static analysis of the module source ─────────────────────────────────────


def _subprocess_calls(tree: ast.AST) -> list[ast.Call]:
    """Return every ``subprocess.*`` call node in *tree*."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "subprocess":
                found.append(node)
    return found


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _resolved_argv_literals(tree: ast.AST) -> list[ast.List | ast.Tuple]:
    """Return the argv list literal behind every subprocess call.

    Handles the one indirection the toolset actually uses — ``cmd = ["git",
    "diff"]`` built up over a few lines and then passed by name — by resolving
    a Name argv against the list assignments in its enclosing function.
    """
    resolved: list[ast.List | ast.Tuple] = []
    scopes: list[ast.AST] = [tree] + [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for scope in scopes:
        bound: dict[str, ast.List | ast.Tuple] = {}
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bound[target.id] = node.value
        for call in _subprocess_calls(scope):
            if not call.args:
                continue
            argv = call.args[0]
            if isinstance(argv, (ast.List, ast.Tuple)):
                resolved.append(argv)
            elif isinstance(argv, ast.Name) and argv.id in bound:
                resolved.append(bound[argv.id])
    return resolved


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.parts[-4])
def test_source_never_requests_a_shell(source: Path):
    """`shell=True` is a different command on cmd.exe than on /bin/sh."""
    assert source.is_file(), f"missing toolset copy: {source}"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for call in _subprocess_calls(tree):
        shell = _kwarg(call, "shell")
        if shell is None:
            continue  # subprocess defaults to shell=False
        assert isinstance(shell, ast.Constant) and shell.value is False, (
            f"{source.name}:{call.lineno} passes a non-literal-False shell= — "
            "the build toolset must never hand a command line to a shell"
        )


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.parts[-4])
def test_source_passes_list_argv_never_a_command_string(source: Path):
    """A string argv is shell-parsed on POSIX and CreateProcess-parsed on
    Windows; the two split differently. Only a list means one thing on both."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for call in _subprocess_calls(tree):
        assert call.args, f"{source.name}:{call.lineno} subprocess call with no argv"
        argv = call.args[0]
        # A Name/Attribute is a variable holding a list built above the call
        # (see `cmd = ["git", "diff"]` in _git_diff). A literal string, an
        # f-string, or any concatenation/format is a command STRING and is
        # rejected outright.
        assert not isinstance(argv, (ast.Constant, ast.JoinedStr, ast.BinOp)), (
            f"{source.name}:{call.lineno} passes a command string as argv — "
            "build a list instead"
        )
        assert isinstance(argv, (ast.List, ast.Tuple, ast.Name, ast.Attribute)), (
            f"{source.name}:{call.lineno} argv is {type(argv).__name__}, "
            "expected a list literal or a name bound to one"
        )


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.parts[-4])
def test_source_never_uses_os_system_or_popen(source: Path):
    """os.system/os.popen are shells by definition — no list-argv form exists."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                assert node.func.attr not in ("system", "popen"), (
                    f"{source.name}:{node.lineno} calls os.{node.func.attr} — "
                    "use subprocess with a list argv"
                )


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.parts[-4])
def test_source_names_no_posix_only_executable(source: Path):
    """The executable slot of every argv literal must be portable.

    Only element 0 is checked, because that is the only position that names a
    program. ``["git", "diff"]`` is portable — "diff" there is a git
    subcommand, not /usr/bin/diff — and a rule that flagged it would be a rule
    people learn to work around.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    argv_literals = _resolved_argv_literals(tree)
    assert argv_literals, f"{source.name}: no list argv literal found to check"
    for argv in argv_literals:
        head = argv.elts[0]
        assert isinstance(head, ast.Constant) and isinstance(head.value, str), (
            f"{source.name}:{argv.lineno} argv[0] is not a literal program name"
        )
        assert head.value not in _POSIX_ONLY, (
            f"{source.name}:{argv.lineno} executes {head.value!r}, which does "
            "not exist on Windows"
        )


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.parts[-4])
def test_source_names_no_absolute_posix_path(source: Path):
    """`/bin/sh`, `/usr/bin/env` and friends have no Windows equivalent."""
    text = source.read_text(encoding="utf-8")
    for needle in ("/bin/sh", "/bin/bash", "/usr/bin/", "/dev/null", "/tmp/"):
        assert needle not in text, f"{source.name} hardcodes the POSIX path {needle!r}"


# ── The command allowlist ────────────────────────────────────────────────────


def test_allowlist_is_python_only():
    """Every allowlisted prefix starts a `python` invocation. That is what
    keeps the surface portable: the interpreter is the only executable the
    agent can name, and it exists on both OSes under the same name."""
    assert _BUILD_ALLOWED_PREFIXES, "the build allowlist must not be empty"
    for prefix in _BUILD_ALLOWED_PREFIXES:
        first = prefix.split()[0]
        assert first == "python", f"allowlist prefix {prefix!r} runs {first!r}, not python"
        for token in prefix.split():
            assert token not in _POSIX_ONLY, f"allowlist prefix {prefix!r} names {token!r}"


@pytest.mark.parametrize("binary", sorted(_POSIX_ONLY))
def test_run_command_refuses_every_posix_only_binary(tmp_path, binary):
    _, handlers = build_worktree_toolset(str(tmp_path))
    out = handlers["run_command"]({"command": f"{binary} something"}, None)
    assert out.startswith("refused:"), f"{binary!r} was not refused: {out[:200]}"


def test_shell_metacharacters_are_inert_not_interpreted(tmp_path):
    """A `&&`-chained second command passes the prefix check (the line still
    starts with an allowlisted prefix) and must nevertheless never run.

    With shell=False the `&&` and everything after it are plain argv entries
    handed to the first program, not a second command. This is the whole payoff
    of the list-argv rule, so pin it with a payload that WOULD leave a trace if
    a shell were involved — and prove the first half really executed, so the
    absent trace means "not interpreted" rather than "nothing ran at all".
    """
    _, handlers = build_worktree_toolset(str(tmp_path))
    marker = tmp_path / "should-not-exist.txt"
    _make_tools_dir(tmp_path)
    (tmp_path / "probe.py").write_text("print('probe-ran')\n", encoding="utf-8")
    (tmp_path / "evil.py").write_text(
        "import pathlib; pathlib.Path(__file__).with_name('should-not-exist.txt').touch()\n",
        encoding="utf-8",
    )
    out = handlers["run_command"](
        {"command": "python tools/../probe.py && python tools/../evil.py", "timeout": 120},
        None,
    )
    assert not out.startswith("refused:"), out
    assert "probe-ran" in out, f"the first command did not execute: {out[:400]}"
    assert not marker.exists(), "the `&&` was interpreted — something ran a shell"


def test_dotdot_in_a_command_path_needs_a_real_directory(tmp_path):
    """`tools/../x.py` is not the same path on both OSes.

    Windows normalises `..` lexically, so the intermediate directory need not
    exist. POSIX walks it, so a missing `tools/` is ENOENT. Pinned because the
    allowlist ("commands must start with `python tools/`") pushes every caller
    into exactly this shape, and a fixture that forgets the mkdir is green on
    the development OS and red in CI — a divergence that is invisible until
    someone runs the suite on the other side.
    """
    _, handlers = build_worktree_toolset(str(tmp_path))
    (tmp_path / "script.py").write_text("print('ran')\n", encoding="utf-8")

    missing = handlers["run_command"]({"command": "python tools/../script.py", "timeout": 60}, None)
    _make_tools_dir(tmp_path)
    present = handlers["run_command"]({"command": "python tools/../script.py", "timeout": 60}, None)

    assert "ran" in present, present
    if sys.platform.startswith("win"):
        assert "ran" in missing, "Windows should collapse `..` without the directory"
    else:
        assert "ran" not in missing, "POSIX must not resolve `..` through a missing directory"


# ── Runtime: drive every handler and inspect what it actually executed ───────


class _Spy:
    """Records subprocess invocations instead of performing them."""

    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="", args=args[0] if args else None)


@pytest.fixture
def spy(monkeypatch):
    """Patch the stdlib attribute both modules reach through `subprocess.run`.

    rubric_build_tools calls it directly (git_diff) and tools.skills.invoke
    calls it on the run_command path, so one patch covers both seams.
    """
    s = _Spy()
    monkeypatch.setattr(subprocess, "run", s)
    return s


def test_every_tool_has_a_portability_input(tmp_path):
    """Forcing function: a newly added tool must be driven by this file."""
    tools, handlers = build_worktree_toolset(str(tmp_path))
    declared = {t["function"]["name"] for t in tools}
    assert declared == set(handlers)
    assert declared == set(_TOOL_INPUTS), (
        "the build toolset changed — add the new tool to _TOOL_INPUTS so its "
        "portability is actually checked"
    )


def _drive_all(tmp_path) -> None:
    """Invoke every handler once. Handlers never raise; errors come back as
    strings, which is fine — we are inspecting how they EXECUTE, not what
    they return."""
    (tmp_path / "sample.txt").write_bytes(b"alpha\nbeta\n")
    _, handlers = build_worktree_toolset(str(tmp_path))
    for name, payload in _TOOL_INPUTS.items():
        result = handlers[name](dict(payload), None)
        assert isinstance(result, str), f"{name} returned {type(result).__name__}, not str"


def test_no_handler_ever_requests_a_shell(tmp_path, spy):
    _drive_all(tmp_path)
    assert spy.calls, "no subprocess call was recorded — the spy is not wired"
    for args, kwargs in spy.calls:
        assert kwargs.get("shell") in (None, False), f"shell requested for {args[0]!r}"


def test_every_handler_executes_a_list_argv(tmp_path, spy):
    _drive_all(tmp_path)
    for args, _kwargs in spy.calls:
        assert args, "subprocess called with no positional argv"
        argv = args[0]
        assert isinstance(argv, (list, tuple)), (
            f"argv is {type(argv).__name__} ({argv!r}) — a command string is "
            "split differently by cmd.exe and /bin/sh"
        )
        assert all(isinstance(part, str) for part in argv), f"non-str in argv: {argv!r}"


def test_no_handler_executes_a_posix_only_binary(tmp_path, spy):
    _drive_all(tmp_path)
    for args, _kwargs in spy.calls:
        argv = args[0]
        executable = Path(str(argv[0])).name.lower()
        executable = executable[:-4] if executable.endswith(".exe") else executable
        assert executable not in _POSIX_ONLY, (
            f"handler executed {argv[0]!r}, which does not exist on Windows"
        )


def test_run_command_executes_this_interpreter_not_a_bare_name(tmp_path, spy):
    """A bare `python` resolves to whatever is first on PATH, which on Windows
    is frequently the App Execution Alias stub rather than an interpreter."""
    _, handlers = build_worktree_toolset(str(tmp_path))
    handlers["run_command"]({"command": "python -m pytest --version"}, None)
    assert spy.calls, "run_command did not reach subprocess"
    argv = spy.calls[-1][0][0]
    assert Path(str(argv[0])).name.lower().startswith("python"), argv


def test_run_command_reports_the_real_interpreter(tmp_path):
    """Unspied counterpart to the test above: the command really runs, under a
    real interpreter, on whichever OS this suite is executing on.

    Driven from a script file rather than `python -c`: shlex.split(posix=False)
    keeps the quotes around a -c payload, so the interpreter receives a string
    literal and prints nothing. That is the same on both OSes, but it makes -c
    useless as a probe.
    """
    _, handlers = build_worktree_toolset(str(tmp_path))
    _make_tools_dir(tmp_path)
    (tmp_path / "whoami.py").write_text(
        "import sys, os\nprint(os.path.basename(sys.executable))\n", encoding="utf-8"
    )
    out = handlers["run_command"]({"command": "python tools/../whoami.py", "timeout": 120}, None)
    assert "exit_code=0" in out, out
    assert Path(sys.executable).name in out, out
