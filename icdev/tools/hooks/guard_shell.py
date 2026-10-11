#!/usr/bin/env python3
# CUI // SP-CTI
"""omx-guard-04 (L2): the guarded shell -- any harness's shell command -> ICDEV's guard.

L1 (``harness_guard`` behind a native pre-tool hook) only guards a harness that
HAS such a hook. This module guards the one thing every coding agent does
through the operating system rather than through its own plugin API: it runs a
shell. The kanban runner puts a directory of shims in front of the real shells
(``PATH`` prefix, and ``SHELL``), so whatever the harness is, a ``bash -c <cmd>``
it spawns lands here first. The command text goes through the SAME bridge L1
uses (``harness_guard.decide`` -> ``run_pre_tool_check`` -> ``shared_checks``);
no check is copied. Then either the real shell is exec'd with the argv
unchanged, or the shim exits ``BLOCKED_EXIT`` with the BLOCKED reason on stderr.

What it reads as "the command": ``-c <text>`` (any short-option cluster holding
``c``: ``-lc``, ``-ic``, ``-euc`` ...), a script path (the file's contents), or
stdin (read, checked, then handed to the real shell). An interactive shell on a
TTY cannot be pre-read; it is passed through and the gap is LOGGED, never silent.

Coverage is a property of the HARNESS, not of this file: a harness that execs
``/bin/bash`` by absolute path never consults ``PATH`` or ``SHELL`` and is not
covered. That is measured per adapter (``--measure``) and recorded in
``args/guard_shell_resolution.yaml``; ``capability_matrix`` reports it as the
``guard_shell`` capability.

OS: POSIX only. On Windows, ``wiring()`` reports ``supported: False`` with the
reason -- CreateProcess finds ``cmd.exe`` in System32 before ``PATH``, and
harnesses spawn ``bash``/``powershell`` as ``.exe``, which a script shim cannot
stand in for. The runner logs that; it is never a silent pass.

Fail OPEN, loudly, like ``harness_guard``: a bridge that cannot decide allows
the command and says so on stderr. Kill switches are the hook's own
(``ICDEV_PRETOOLUSE_ENFORCE``, ``ICDEV_<CHECK>_GUARD``), applied by ``decide``.

Usage::

    python -m tools.hooks.guard_shell --wiring            # env the runner adds
    python -m tools.hooks.guard_shell --measure -- \\
        opencode run "Run exactly this shell command: echo {marker}"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from icdev.core.paths import repo_root
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = repo_root(__file__)

#: The shells a shim is written for (when the real one exists on PATH).
SHELLS = ("bash", "sh", "dash", "zsh")
#: "Found but cannot be executed" -- the shell convention closest to a refusal.
BLOCKED_EXIT = 126
NOT_FOUND_EXIT = 127
#: The harness label the bridge sees; ``to_icdev`` passes ``Bash`` through as is.
HARNESS = "shell"

SHIM_DIR_ENV = "ICDEV_GUARD_SHELL_DIR"
REAL_ENV_PREFIX = "ICDEV_GUARD_SHELL_REAL_"
#: When set, every shim decision is appended to this JSONL file (``--measure``).
LOG_ENV = "ICDEV_GUARD_SHELL_LOG"

VIA_PATH = "path"
VIA_SHELL = "shell_env"
#: ``args/guard_shell_resolution.yaml`` values for how a harness finds its shell.
RESOLUTIONS = (VIA_SHELL, VIA_PATH, "absolute", "unmeasured")
RESOLUTION_PATH = BASE_DIR / "args" / "guard_shell_resolution.yaml"

UNSUPPORTED_WINDOWS = (
    "the L2 guarded shell is unsupported on Windows: CreateProcess resolves "
    "cmd.exe from System32 before PATH, and harnesses spawn bash/powershell as "
    ".exe, which a PATH script shim cannot stand in for -- shell commands of a "
    "harness without a native hook (L1) are NOT guarded on this host"
)

# Options that consume the next argument (bash/dash/zsh share these spellings).
_ARG_OPTS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})


# ── argv -> command text ─────────────────────────────────────────────────────

@dataclass
class Invocation:
    """How a shell was asked to run, and the text it will run."""
    mode: str                      # "c" | "script" | "stdin" | "interactive"
    text: Optional[str] = None
    operand: Optional[str] = None  # the script path, for "script"
    rest: List[str] = field(default_factory=list)


def parse_invocation(args: Sequence[str]) -> Invocation:
    """Classify a POSIX shell argv (without argv[0]); reads nothing."""
    command_mode = stdin_mode = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--", "-"):
            i += 1
            break
        if arg in _ARG_OPTS:
            i += 2
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if len(arg) > 1 and arg[0] in "-+":
            cluster = arg[1:]
            if arg[0] == "-":
                command_mode = command_mode or "c" in cluster
                stdin_mode = stdin_mode or "s" in cluster
            # -o / -O inside a cluster (``-eo pipefail``) take the next argument.
            i += 1 + sum(1 for ch in cluster if ch in "oO")
            continue
        break
    operands = list(args[i:])
    if command_mode:
        return Invocation("c", text=operands[0] if operands else None,
                          rest=operands[1:])
    if operands and not stdin_mode:
        return Invocation("script", operand=operands[0], rest=operands[1:])
    return Invocation("stdin", rest=operands)


def read_command(inv: Invocation) -> Invocation:
    """Fill ``inv.text`` for a script; mark a TTY stdin ``interactive``.

    Stdin is NOT read here: a harness may hold a persistent shell open on a pipe
    and wait for each command's output, so reading to EOF would deadlock it.
    ``_proxy_stdin`` checks and forwards it line by line instead.
    """
    if inv.mode == "script" and inv.operand:
        try:
            inv.text = Path(inv.operand).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("guard_shell: cannot read script %s (%s); the shell "
                           "will report it", inv.operand, exc)
    elif inv.mode == "stdin":
        stream = sys.stdin
        if stream is None or stream.isatty():
            inv.mode = "interactive"
    return inv


# ── decision ────────────────────────────────────────────────────────────────

_EVAL_PREFIXES = (("eval",), ("builtin", "eval"), ("command", "eval"))


def _posix_tokens(segment: str) -> List[str]:
    # POSIX rules on every host: this module only ever fronts POSIX shells, and
    # a non-POSIX lex keeps the quotes and mangles a multi-line payload.
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def split_evals(text: str) -> Tuple[str, List[str]]:
    """``(text minus its eval segments, the text each eval will run)``.

    MEASURED, not hypothetical: Claude Code sends ``bash -c "source <snapshot>
    && eval 'cat .env' < /dev/null && pwd -P >| <tmp>"``. The ``.env`` check
    reads ``'cat .env'`` as ONE quoted token, so the first real run on
    2026-10-11 was ALLOWED; and ``dangerous_rm`` reads the same token as an rm
    with no target, so a plain ``docker rm -f x`` was REFUSED (the fire-rate
    replay: 209 extra refusals). ``eval`` is a builtin and never comes back
    through a PATH shim, so its argument is checked as the shell will run it,
    INSTEAD of the quoted segment. Segmenting is ``shared_checks``' own; this
    is input extraction, not a second check. Only a segment whose COMMAND is
    ``eval`` counts, so ``cat .env eval`` is still checked whole.
    """
    from tools.hooks.shared_checks import command_segments  # noqa: PLC0415

    rest: List[str] = []
    payloads: List[str] = []
    for segment in command_segments(text):
        tokens = _posix_tokens(segment)
        prefix = next((p for p in _EVAL_PREFIXES if tuple(tokens[:len(p)]) == p), None)
        if prefix is None:
            rest.append(segment)
        elif tokens[len(prefix):]:
            payloads.append(" ".join(tokens[len(prefix):]))
    return "\n".join(rest), payloads


def eval_payloads(text: str, depth: int = 0) -> List[str]:
    """Every text an ``eval`` in *text* will run, nested ``eval`` included."""
    payloads = split_evals(text)[1]
    if depth < 3:
        for inner in list(payloads):
            payloads.extend(eval_payloads(inner, depth + 1))
    return payloads


def _decide(text: str, depth: int = 0) -> Dict[str, Any]:
    from tools.hooks import harness_guard  # noqa: PLC0415

    rest, payloads = split_evals(text) if depth < 4 else (text, [])
    if not payloads:
        # No eval: exactly the text L1 would see, so exactly L1's verdict.
        return harness_guard.decide(HARNESS, "Bash", {"command": text})
    verdicts = [harness_guard.decide(HARNESS, "Bash", {"command": rest})] if rest.strip() else []
    verdicts += [_decide(p, depth + 1) for p in payloads]
    return next((v for v in verdicts if not v.get("allowed")), verdicts[0])


def decide_text(text: Optional[str]) -> Dict[str, Any]:
    """The bridge's verdict on one shell command text. Never raises."""
    if not text or not text.strip():
        return {"allowed": True, "advisory": False, "reason": "no command text"}
    # The shell deletes backslash-newline before it parses, so the checks must
    # see the command that way too: `cat \<newline>.env` is `cat .env`.
    text = text.replace("\\\r\n", "").replace("\\\n", "")
    try:
        return _decide(text)
    except Exception as exc:  # noqa: BLE001 -- fail open, like pre_tool_use.main()
        logger.warning("guard_shell: bridge unavailable (%s: %s); FAILING OPEN",
                       type(exc).__name__, exc)
        return {"allowed": True, "advisory": False,
                "reason": f"ICDEV guard bridge error, failed open: "
                          f"{type(exc).__name__}: {exc}"}


def _log(record: Dict[str, Any]) -> None:
    path = os.environ.get(LOG_ENV)
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        logger.warning("guard_shell: cannot append to %s (%s)", path, exc)


# ── the real shell ──────────────────────────────────────────────────────────

def _same_dir(a: str, b: Optional[str]) -> bool:
    if not b:
        return False
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return False


def real_shell(name: str, env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """The real ``name``: the path the wiring recorded, else PATH minus the shims."""
    env = os.environ if env is None else env
    recorded = env.get(REAL_ENV_PREFIX + name.upper())
    if recorded:
        return recorded
    shim_dir = env.get(SHIM_DIR_ENV)
    shims = (shim_dir, os.path.join(shim_dir, "login")) if shim_dir else ()
    parts = [p for p in env.get("PATH", "").split(os.pathsep)
             if p and not any(_same_dir(p, s) for s in shims)]
    return shutil.which(name, path=os.pathsep.join(parts))


def _exit_code(code: int) -> int:
    return 128 - code if code < 0 else code


def _run_real(real: str, args: Sequence[str]) -> int:
    argv = [real, *args]
    sys.stdout.flush()
    if os.name != "nt":
        os.execv(real, argv)  # never returns: same argv, same fds, same exit code
    return _exit_code(subprocess.run(argv, shell=False).returncode)


def _refuse(verdict: Dict[str, Any]) -> None:
    sys.stderr.write(f"ICDEV guard: {verdict.get('reason') or 'BLOCKED'}\n")
    sys.stderr.flush()


def _proxy_stdin(real: str, args: Sequence[str], via: str, name: str) -> int:
    """Feed the real shell its stdin one command line at a time, each one checked.

    A refused line is dropped (the reason goes to stderr) and the next is still
    forwarded, so a persistent shell's completion sentinel keeps working. Lines
    ending in a backslash are joined first, so a continuation cannot split a
    command across two checks. If anything was refused, the exit is
    ``BLOCKED_EXIT`` whatever the shell returned.
    """
    proc = subprocess.Popen([real, *args], stdin=subprocess.PIPE, shell=False)
    refused = False
    pending = b""
    try:
        for line in iter(sys.stdin.buffer.readline, b""):
            pending += line
            if pending.rstrip(b"\r\n").endswith(b"\\") and line.endswith(b"\n"):
                continue
            chunk, pending = pending, b""
            text = chunk.decode("utf-8", errors="replace")
            verdict = decide_text(text)
            _log({"via": via, "shell": name, "mode": "stdin", "text": text,
                  "allowed": verdict.get("allowed"), "reason": verdict.get("reason")})
            if not verdict.get("allowed"):
                refused = True
                _refuse(verdict)
                continue
            proc.stdin.write(chunk)
            proc.stdin.flush()
        if pending:
            verdict = decide_text(pending.decode("utf-8", errors="replace"))
            if verdict.get("allowed"):
                proc.stdin.write(pending)
            else:
                refused = True
                _refuse(verdict)
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
    code = _exit_code(proc.wait())
    return BLOCKED_EXIT if refused else code


def shim_main(via: str, name: str, args: Sequence[str]) -> int:
    """The shim body: check the command, then run the real ``name`` or refuse."""
    inv = read_command(parse_invocation(args))
    real = real_shell(name)
    if not real:
        sys.stderr.write(f"ICDEV guard: no real {name} found outside the shim dir\n")
        return NOT_FOUND_EXIT
    if inv.mode == "stdin":
        return _proxy_stdin(real, args, via, name)
    if inv.mode == "interactive":
        logger.warning("guard_shell: interactive %s on a TTY -- its commands are "
                       "NOT checked by L2", name)
        _log({"via": via, "shell": name, "mode": inv.mode, "text": None,
              "allowed": True, "reason": "interactive shell: not checked by L2"})
        return _run_real(real, args)
    verdict = decide_text(inv.text)
    _log({"via": via, "shell": name, "mode": inv.mode, "text": inv.text,
          "allowed": verdict.get("allowed"), "reason": verdict.get("reason")})
    if not verdict.get("allowed"):
        _refuse(verdict)
        return BLOCKED_EXIT
    if verdict.get("advisory"):
        sys.stderr.write(f"ICDEV guard: {verdict.get('reason')}\n")
    return _run_real(real, args)


# ── wiring (the runner's spawn environment) ─────────────────────────────────

def _module_name() -> str:
    from tools.hooks import harness_guard  # noqa: PLC0415

    return harness_guard.guard_module().replace("harness_guard", "guard_shell")


def _entry_source(root: Path) -> str:
    # The shim dir, not the harness's cwd, is sys.path[0] for this script, so a
    # planted module in the working tree is never imported by the guard.
    return (
        "# omx-guard-04: generated by tools/hooks/guard_shell.py -- do not edit\n"
        "import importlib\nimport sys\n"
        f"sys.path.insert(1, {str(root)!r})\n"
        f"_gs = importlib.import_module({_module_name()!r})\n"
        "sys.exit(_gs.shim_main(sys.argv[1], sys.argv[2], sys.argv[3:]))\n"
    )


def _shim_source(python: str, entry: Path, via: str, name: str) -> str:
    q = shlex.quote
    return (f"#!/bin/sh\n# omx-guard-04 guarded {name} -- generated, do not edit\n"
            f"exec {q(python)} {q(str(entry))} {via} {name} \"$@\"\n")


def default_shim_dir(python: Optional[str] = None, root: Optional[Path] = None) -> Path:
    """Per-user and per-(interpreter, root): never a shared, plantable /tmp path."""
    key = hashlib.sha256(f"{python or sys.executable}\0{root or BASE_DIR}".encode()).hexdigest()
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "icdev" / "guard-shell" / key[:16]


def _write(path: Path, text: str, mode: int) -> None:
    if not (path.is_file() and path.read_text(encoding="utf-8") == text):
        path.write_bytes(text.encode("utf-8"))
    os.chmod(path, mode)


def install_shims(shim_dir: Optional[Path] = None, *, python: Optional[str] = None,
                  root: Optional[Path] = None,
                  login_shell: Optional[str] = None) -> Dict[str, Any]:
    """Write the shims (idempotent). ``login/<name>`` is the ``SHELL`` target.

    A shim is written only for a shell that really exists, so the PATH prefix
    never invents a shell the host does not have.
    """
    python = python or sys.executable
    root = root or BASE_DIR
    shim_dir = Path(shim_dir or default_shim_dir(python, root))
    (shim_dir / "login").mkdir(parents=True, exist_ok=True, mode=0o700)
    entry = shim_dir / "guard_shell_entry.py"
    _write(entry, _entry_source(root), 0o600)
    real: Dict[str, str] = {}
    for name in SHELLS:
        found = real_shell(name, {"PATH": os.environ.get("PATH", ""),
                                  SHIM_DIR_ENV: str(shim_dir)})
        if found:
            real[name] = found
            _write(shim_dir / name, _shim_source(python, entry, VIA_PATH, name), 0o755)
    login = None
    base = os.path.basename(login_shell or "")
    if base in SHELLS and login_shell and os.path.isfile(login_shell):
        real.setdefault(base, login_shell)
        login = base
    elif "bash" in real:
        login = "bash"
    elif "sh" in real:
        login = "sh"
    if login:
        _write(shim_dir / "login" / login,
               _shim_source(python, entry, VIA_SHELL, login), 0o755)
    return {"dir": str(shim_dir), "real": real, "login": login}


def wiring(base_env: Optional[Dict[str, str]] = None,
           shim_dir: Optional[Path] = None) -> Dict[str, Any]:
    """The env the runner adds to a spawned harness, or why it cannot.

    ``{"supported": bool, "reason": str, "env": {...}}`` -- ``env`` is empty
    when unsupported, and the caller MUST log ``reason``.
    """
    base = dict(os.environ if base_env is None else base_env)
    if os.name == "nt":
        return {"supported": False, "reason": UNSUPPORTED_WINDOWS, "env": {}}
    try:
        installed = install_shims(shim_dir, login_shell=base.get("SHELL"))
    except Exception as exc:  # noqa: BLE001 -- reported, never silent
        return {"supported": False, "env": {},
                "reason": f"guarded shell shims could not be installed: "
                          f"{type(exc).__name__}: {exc}"}
    if not installed["login"]:
        return {"supported": False, "env": {},
                "reason": "no POSIX shell (bash/sh) found on PATH to guard"}
    d = installed["dir"]
    env = {
        "PATH": d + os.pathsep + base.get("PATH", ""),
        "SHELL": os.path.join(d, "login", installed["login"]),
        SHIM_DIR_ENV: d,
    }
    env.update({REAL_ENV_PREFIX + n.upper(): p for n, p in installed["real"].items()})
    return {"supported": True, "env": env,
            "reason": f"shell commands pass the shared checks via {d}"}


# ── per-harness measurement and the capability self-test ────────────────────

def load_resolution(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """``args/guard_shell_resolution.yaml``: how each adapter's harness finds its shell."""
    import yaml  # noqa: PLC0415

    try:
        data = yaml.safe_load((path or RESOLUTION_PATH).read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    return {str(k): dict(v or {}) for k, v in (data.get("adapters") or {}).items()}


def measure(argv: Sequence[str], cwd: Optional[str] = None,
            timeout: int = 300) -> Dict[str, Any]:
    """Run a harness with the shims in front and report whether they saw it.

    ``{marker}`` in ``argv`` is replaced by a fresh marker; the harness is meant
    to be asked to run ``echo {marker}``. ``shell_env``/``path`` = the shim saw
    the marker through that route; ``absolute`` = the marker came back in the
    output but no shim saw it; ``inconclusive`` = the marker never ran.
    """
    marker = f"ICDEV_L2_MARK_{uuid.uuid4().hex[:10]}"
    work = Path(cwd or tempfile.mkdtemp(prefix="icdev-l2-measure-"))
    log = work / "guard-shell-hits.jsonl"
    wired = wiring()
    if not wired["supported"]:
        return {"verdict": "unsupported", "reason": wired["reason"]}
    env = {**os.environ, **wired["env"], LOG_ENV: str(log)}
    try:
        proc = subprocess.run([a.replace("{marker}", marker) for a in argv],
                              cwd=str(work), env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, shell=False)
        output, code = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        output, code = f"{type(exc).__name__}: {exc}", None
    hits = []
    if log.is_file():
        hits = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    seen = sorted({h["via"] for h in hits if marker in str(h.get("text") or "")})
    if seen:
        verdict = VIA_SHELL if VIA_SHELL in seen else VIA_PATH
    elif marker in output:
        verdict = "absolute"
    else:
        verdict = "inconclusive"
    return {"verdict": verdict, "marker": marker, "exit_code": code,
            "seen_via": seen, "shim_calls": len(hits), "output_tail": output[-400:]}


PROBE_BENIGN = "echo ICDEV_GUARD_SHELL_PROBE"
#: Harmless if it ever ran: the probe's cwd is an empty temporary directory.
PROBE_BAD = "cat .env"


def self_test(env: Dict[str, str], resolves: str, shell: str = "bash") -> Dict[str, Any]:
    """Is the shell this env hands the harness guarded? Measured, not inferred.

    Resolves the shell the way the harness was measured to (``SHELL`` or a PATH
    lookup), then requires it to pass a benign command unchanged AND refuse a
    known-bad one with ``BLOCKED_EXIT``.
    """
    if resolves == VIA_SHELL:
        target = env.get("SHELL")
    else:
        target = shutil.which(shell, path=env.get("PATH", ""))
    shim_dir = env.get(SHIM_DIR_ENV)
    if not target or not shim_dir or not os.path.realpath(target).startswith(
            os.path.realpath(shim_dir)):
        return {"wired": False,
                "reason": f"the spawn env resolves {shell} to {target!r}, not a guard shim"}
    with tempfile.TemporaryDirectory(prefix="icdev-l2-probe-") as tmp:
        def run(cmd: str) -> subprocess.CompletedProcess:
            return subprocess.run([target, "-c", cmd], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=120,
                                  stdin=subprocess.DEVNULL, shell=False)
        try:
            good, bad = run(PROBE_BENIGN), run(PROBE_BAD)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"wired": False, "reason": f"shim did not run: {type(exc).__name__}: {exc}"}
    if good.returncode != 0 or good.stdout != "ICDEV_GUARD_SHELL_PROBE\n":
        return {"wired": False, "reason": f"shim altered a benign command "
                                          f"(exit {good.returncode}, {good.stderr.strip()[:200]})"}
    if bad.returncode != BLOCKED_EXIT or "BLOCKED" not in bad.stderr:
        return {"wired": False, "reason": f"shim ALLOWED a known-bad command "
                                          f"(exit {bad.returncode}, {bad.stderr.strip()[:200]})"}
    return {"wired": True, "reason": f"{target} refused {PROBE_BAD!r}: {bad.stderr.strip()[:200]}"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Everything after --measure is the harness's argv, verbatim.
    if "--measure" in argv:
        harness = argv[argv.index("--measure") + 1:]
        harness = harness[1:] if harness[:1] == ["--"] else harness
        if not harness:
            print("--measure needs a harness argv", file=sys.stderr)
            return 2
        print(json.dumps(measure(harness), indent=2))
        return 0
    parser = argparse.ArgumentParser(
        prog="python -m tools.hooks.guard_shell",
        description="The L2 guarded shell: wiring, and per-harness measurement "
                    "(--measure -- <harness argv, '{marker}' substituted>).",
    )
    parser.add_argument("--wiring", action="store_true",
                        help="install the shims and print the spawn env (JSON)")
    opts = parser.parse_args(argv)
    if opts.wiring:
        result = wiring()
        print(json.dumps(result, indent=2))
        return 0 if result["supported"] else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
