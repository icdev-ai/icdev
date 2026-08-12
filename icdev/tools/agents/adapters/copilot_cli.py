# CUI // SP-CTI
"""exa-bench-02: GitHub Copilot CLI adapter — a real harness behind the seam.

Until exa-bench-02 this module was a stub, and its ``available()`` was the
single line the EXA card exists to catch::

    return False and (shutil.which("gh") is not None)

``False and …`` short-circuits, so the right-hand side was never evaluated and
the adapter could not report available under any condition. It was DECLARED in
``registry._ensure_loaded`` and listed in the manifest, and it was inert — the
same declared-but-unconsumed shape as the audit chain and the prompt registry.

Both halves of that line were wrong, and the second half is the interesting one.
``shutil.which("gh")`` is not a probe for this harness. As of ``gh`` 2.86
``gh copilot`` is a LAUNCHER: if the Copilot CLI is not installed it DOWNLOADS
it. So the presence of ``gh`` means "this host could go and fetch a harness",
which is not what ``available()`` asks. Treating it as availability would make
``pick_default`` hand back an adapter whose first act is a network install, and
the Protocol is explicit that the check is cheap and local. This module resolves
the ``copilot`` binary itself — including the location ``gh`` downloads it to,
because a CLI that ``gh`` already fetched IS installed — and never shells out to
``gh``.

``claude_cli.py`` and ``codex_cli.py`` (exa-bench-01) are the references for the
problems already solved here, and the same answers are used:

* **PATHEXT-aware discovery** — ``shutil.which`` first (it resolves
  ``.exe``/``.cmd``/``.bat`` through PATHEXT, which is what the npm
  ``@github/copilot`` shim install needs), then secondary probes tried with each
  PATHEXT suffix, because the bare suffix-less name never exists on Windows.
  25 kanban tasks were quarantined as "no executor available" on 2026-08-01
  before that was traced on the claude side.
* **the instruction goes in over stdin from a temp file** — Copilot CLI accepts
  a piped prompt, and a real task prompt on argv trips the Windows 32767-char
  command-line limit (WinError 206). Note ``-p``/``--prompt`` is deliberately
  NOT used: the vendor documents that piped input is IGNORED when ``-p`` is
  also given, so passing both would silently truncate the task to whatever fit.

Where this adapter deliberately differs
---------------------------------------

**No auto-approval by default.** Copilot's ``--allow-all-tools`` removes the
confirmation prompt for every tool. ``claude_cli`` passes the analogous
``--dangerously-skip-permissions`` unconditionally, and exa-bench-04 exists
because that is a question, not a settled answer — so a NEW adapter must not
add a second instance of it. Auto-approval here is opt-in per session
(``metadata['allow_all_tools']``) or per host (``$ICDEV_COPILOT_ALLOW_ALL``),
and the narrower ``--allow-tool`` / ``--deny-tool`` / ``--add-dir`` knobs are
passed through so an operator can grant exactly what a task needs. This is not
the same choice as ``codex_cli``'s ``--sandbox workspace-write`` default: that
is a CONFINEMENT (what the agent may touch), this would be the removal of a
CONFIRMATION (whether anyone is asked first).

**``--no-ask-user`` is on by default.** The prompt occupies stdin, so an agent
that paused for clarification would read EOF and hang until the session
timeout. Opt out with ``metadata['no_ask_user'] = False``.

**``--share-gist`` is not wired.** It publishes the session transcript as a
GitHub gist. An adapter that can be handed a CUI prompt does not get a
one-metadata-key path to publishing it; an operator who wants it can pass it
through ``extra_args`` deliberately.

**``structured`` reports what the harness does not give.** Copilot's
programmatic mode emits plain text — there is no JSON envelope, so no token
counts, no cost, no tool-call list and no diff. Those keys are ABSENT rather
than zero, and ``structured['machine_readable']`` is ``False`` so the
exa-bench-03 capability probe can tell "this harness does not report it" from
"this adapter did not parse it".

**No ``spawn()``.** ``claude_cli.spawn()`` exists because the kanban runner owns
its own poll/kill loop. Nothing dispatches Copilot that way; a second execution
mode with no consumer would be inventing a capability the probe is meant to
MEASURE.

LLM-agnostic
------------
No model id appears in this module. The model is the operator's choice, taken
from ``session.metadata['model_id']`` or ``$ICDEV_COPILOT_MODEL`` and passed as
``--model``; with neither set the CLI uses its own ``COPILOT_MODEL`` /
configured default. ``invoke()`` never raises for a backend failure — an
unknown flag, a refused model, a missing token, a non-zero exit and a timeout
all come back as an ``AgentResult`` with ``completed=False`` and the CLI's own
stderr in ``error``. Only a genuinely absent CLI raises, which is the Protocol's
contract.

Authentication is the CLI's own: ``COPILOT_GITHUB_TOKEN`` then ``GH_TOKEN``
then ``GITHUB_TOKEN``, inherited from the environment. This adapter never reads,
rewrites or logs a token.

OS-agnostic
-----------
``pathlib`` throughout, ``encoding='utf-8'`` and ``newline=''`` on every file
handle so a prompt's bytes survive unchanged on both line-ending conventions,
``shell=False``, and the one platform branch is two-sided and parameterised.

Session metadata keys this adapter understands (all optional):

    model_id            str        -> ``--model=``
    allow_all_tools     bool       -> ``--allow-all-tools``       (default off)
    allow_tool          str|list   -> ``--allow-tool=`` per entry
    deny_tool           str|list   -> ``--deny-tool=`` per entry
    add_dir             str|list   -> ``--add-dir=`` per entry
    secret_env_vars     str|list   -> ``--secret-env-vars=`` per entry
    no_ask_user         bool       -> ``--no-ask-user``            (default on)
    suppress_decoration bool       -> ``-s``                       (default on)
    extra_args          list       -> appended verbatim
    dispatch_source     str        -> sets ICDEV_DISPATCH_SOURCE + _TASK_ID
    env                 dict       -> extra environment variables
    temp_dir            str        -> where the stdin temp file is written
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tools.agents.adapter_base import (
    AgentResult,
    AgentSession,
    NotInstalledError,
)


# The standalone CLI (npm ``@github/copilot``, or downloaded by ``gh copilot``)
# installs a single executable named ``copilot``. $ICDEV_COPILOT_CLI overrides.
_EXECUTABLE_NAMES = ("copilot",)
_ENV_EXECUTABLE = "ICDEV_COPILOT_CLI"
_ENV_MODEL = "ICDEV_COPILOT_MODEL"
_ENV_ALLOW_ALL = "ICDEV_COPILOT_ALLOW_ALL"
_ENV_ALLOW_TOOL = "ICDEV_COPILOT_ALLOW_TOOL"

# ``gh copilot`` downloads the CLI under gh's own per-user data directory.
_GH_WINDOWS_VENDOR_DIR = "GitHub CLI"
_GH_POSIX_DIR = "gh"
_COPILOT_DIR = "copilot"

_TRUTHY = ("1", "true", "yes", "on")

_COMPLETION_MARKERS = (
    "[DONE]",
    "Task completed",
    "done.",
)


def _resolve_platform(is_windows: Optional[bool]) -> bool:
    """The platform being described — this host unless the caller said otherwise.

    An explicit parameter rather than an ``os.name`` read at each call site so
    BOTH branches are reachable from either OS's test run. Forcing the branch by
    patching ``os.name`` instead makes ``pathlib`` hand out a ``WindowsPath`` on
    Linux, which raises on construction.
    """
    return (os.name == "nt") if is_windows is None else is_windows


def _pathext_candidates(
    base: Path, is_windows: Optional[bool] = None
) -> List[Path]:
    """``base`` itself, plus each PATHEXT suffix when targeting Windows.

    PATHEXT is a Windows-only variable and is always semicolon-delimited, so the
    split is on ``;`` rather than ``os.pathsep`` — the host running the check is
    not necessarily the platform being described.
    """
    candidates = [base]
    if _resolve_platform(is_windows):
        for ext in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT;.COM").split(";"):
            ext = ext.strip()
            if ext:
                candidates.append(base.with_name(base.name + ext.lower()))
    return candidates


def _gh_managed_paths(is_windows: Optional[bool] = None) -> List[Path]:
    """Where ``gh copilot`` puts the CLI it downloads.

    A binary ``gh`` already fetched IS installed, so it counts for
    ``available()``. What does NOT count is ``gh`` alone: ``gh copilot`` on a
    host without the CLI downloads it, and a download is not a cheap local
    probe. The returned path may be the executable itself or the directory
    holding it depending on the ``gh`` release, so both are tried.
    """
    if _resolve_platform(is_windows):
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            return []
        return [Path(base) / _GH_WINDOWS_VENDOR_DIR / _COPILOT_DIR]
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return [root / _GH_POSIX_DIR / _COPILOT_DIR]


def resolve_copilot_cli(is_windows: Optional[bool] = None) -> Optional[str]:
    """Absolute path to the Copilot CLI, or None.

    ``$ICDEV_COPILOT_CLI`` wins and may be either an explicit path or a bare
    name. Otherwise the name is tried through ``shutil.which`` (PATHEXT on
    Windows), then as a secondary probe under ``~/.local/bin`` and under the
    directory ``gh`` downloads the CLI to.
    """
    override = (os.environ.get(_ENV_EXECUTABLE) or "").strip()
    names: List[str] = [override] if override else list(_EXECUTABLE_NAMES)

    if override:
        candidate = Path(override)
        if candidate.is_file():
            return str(candidate)

    for name in names:
        found = shutil.which(name)
        if found:
            return found

    managed = _gh_managed_paths(is_windows)
    for directory in [Path.home() / ".local" / "bin"] + managed:
        for name in names:
            for candidate in _pathext_candidates(directory / name, is_windows):
                if candidate.is_file():
                    return str(candidate)

    # Older ``gh`` releases write the executable AT the managed path rather
    # than inside it.
    for base in managed:
        for candidate in _pathext_candidates(base, is_windows):
            if candidate.is_file():
                return str(candidate)
    return None


def _as_list(value: Any, env_value: Optional[str] = None) -> List[str]:
    """Normalise a metadata knob that accepts one entry, many, or a CSV string."""
    if value is None:
        value = env_value
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value)]


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class CopilotCliAdapter:
    """AgentAdapter over the GitHub Copilot CLI's programmatic (piped) mode."""

    name = "copilot_cli"

    # ── discovery ────────────────────────────────────────────────────────────
    def available(self) -> bool:
        """True only when the Copilot CLI is actually resolvable on this host.

        Cheap and local — a filesystem/PATH probe, no subprocess and no network
        call, per the Protocol. Notably NOT ``shutil.which("gh")``: see the
        module docstring.
        """
        try:
            return resolve_copilot_cli() is not None
        except OSError:  # e.g. an unreadable home directory
            return False

    def resolve(self) -> str:
        found = resolve_copilot_cli()
        if not found:
            raise NotInstalledError(
                "copilot CLI not found: tried "
                f"{', '.join(_EXECUTABLE_NAMES)} on PATH, ~/.local/bin, the "
                f"gh-managed install directory, and ${_ENV_EXECUTABLE}. "
                "`gh` being installed is not enough — `gh copilot` would "
                "download the CLI, which is not something availability may do."
            )
        return found

    # ── construction ─────────────────────────────────────────────────────────
    def prepare_prompt(self, session: AgentSession) -> str:
        """One prompt goes in, so a system prompt is prepended.

        Copilot CLI has no ``--system-prompt`` equivalent (its persistent
        instructions live in ``AGENTS.md`` / ``.github/copilot-instructions.md``),
        and inventing one would silently drop the caller's text.
        """
        if not session.system_prompt:
            return session.prompt
        return f"{session.system_prompt}\n\n{session.prompt}"

    def build_argv(self, session: AgentSession) -> List[str]:
        """The command line. The prompt is NOT on it — it arrives over stdin.

        ``-p``/``--prompt`` is deliberately absent: the vendor documents that
        piped input is ignored when ``-p`` is given, so passing both would
        silently truncate a long task to whatever fit on argv.
        """
        meta = session.metadata or {}
        argv = [self.resolve()]

        if meta.get("suppress_decoration", True):
            argv.append("-s")
        if meta.get("no_ask_user", True):
            argv.append("--no-ask-user")

        allow_all = meta.get("allow_all_tools")
        if allow_all is None:
            allow_all = _env_flag(_ENV_ALLOW_ALL)
        if allow_all:
            argv.append("--allow-all-tools")

        for tool in _as_list(meta.get("allow_tool"),
                             os.environ.get(_ENV_ALLOW_TOOL)):
            argv.append(f"--allow-tool={tool}")
        for tool in _as_list(meta.get("deny_tool")):
            argv.append(f"--deny-tool={tool}")
        for directory in _as_list(meta.get("add_dir")):
            argv.append(f"--add-dir={directory}")
        for var in _as_list(meta.get("secret_env_vars")):
            argv.append(f"--secret-env-vars={var}")

        model_id = meta.get("model_id") or os.environ.get(_ENV_MODEL)
        if model_id:
            argv.append(f"--model={model_id}")

        argv += [str(arg) for arg in (meta.get("extra_args") or [])]
        return argv

    def build_env(self, session: AgentSession) -> Dict[str, str]:
        """Inherited environment plus the dispatch tags.

        ``ICDEV_DISPATCH_SOURCE`` is what lets the stop hook attribute this
        session's commits to the scheduler instead of to an interactive user.
        Only set when the caller asked for it, so a review-only session stays
        untagged. The CLI's own credentials (``COPILOT_GITHUB_TOKEN``,
        ``GH_TOKEN``, ``GITHUB_TOKEN``) are inherited untouched.
        """
        meta = session.metadata or {}
        env = dict(os.environ)
        source = meta.get("dispatch_source")
        if source:
            env["ICDEV_DISPATCH_SOURCE"] = str(source)
            env["ICDEV_DISPATCH_TASK_ID"] = str(session.task_id)
        for key, value in (meta.get("env") or {}).items():
            env[str(key)] = str(value)
        return env

    def _write_stdin(self, session: AgentSession) -> str:
        """Write the prompt to a temp file and return its path.

        Piped in over stdin rather than passed on argv: a real task prompt is
        far past the Windows 32767-char command-line limit. ``newline=""``
        keeps the bytes exactly as composed on every OS.
        """
        meta = session.metadata or {}
        temp_dir = meta.get("temp_dir")
        if temp_dir:
            dir_path = Path(temp_dir)
            dir_path.mkdir(parents=True, exist_ok=True)
            temp_dir = str(dir_path)
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix="_copilot_instr.txt", delete=False,
            dir=temp_dir or None,
            encoding="utf-8", newline="", errors="replace",
        )
        try:
            handle.write(self.prepare_prompt(session))
        finally:
            handle.close()
        return handle.name

    # ── execution ────────────────────────────────────────────────────────────
    def invoke(self, session: AgentSession) -> AgentResult:
        """Run one Copilot session to completion and return the result.

        Raises:
            NotInstalledError: the CLI is not present on this host. Every other
                failure — non-zero exit, timeout, refused model, missing token,
                unknown flag — is reported as ``completed=False`` with the
                CLI's own stderr.
        """
        argv = self.build_argv(session)
        env = self.build_env(session)
        instr_path = self._write_stdin(session)

        t0 = time.time()
        try:
            with open(instr_path, "r", encoding="utf-8",
                      newline="", errors="replace") as stdin_fh:
                proc = subprocess.run(
                    argv,
                    cwd=session.working_dir or None,
                    stdin=stdin_fh,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=session.timeout_seconds,
                    shell=False,
                )
            text = proc.stdout or ""
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=(proc.returncode == 0
                           and self.detect_completion(text)),
                exit_code=proc.returncode,
                output=text,
                error=("" if proc.returncode == 0 else (proc.stderr or "")),
                duration_ms=int((time.time() - t0) * 1000),
                # Plain text in, plain text out. No token counts, no cost and
                # no tool-call list are ABSENT rather than zero, and the flag
                # says which of the two it is.
                structured={"machine_readable": False,
                            "stderr": proc.stderr or ""},
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=False,
                exit_code=-1,
                output="",
                error=(f"copilot CLI timed out after "
                       f"{session.timeout_seconds}s"),
                duration_ms=int((time.time() - t0) * 1000),
            )
        except FileNotFoundError as exc:
            # Resolved a moment ago and gone by exec time — still "not
            # installed" from the caller's point of view.
            raise NotInstalledError(f"copilot CLI missing: {exc}") from exc
        finally:
            _unlink_quietly(instr_path)

    # ── protocol tail ────────────────────────────────────────────────────────
    def detect_completion(self, output: str) -> bool:
        """Pure-string heuristic — the only kind available for a text harness.

        Copilot's programmatic mode has no completion event to read, so this is
        the same marker/length heuristic ``claude_cli`` uses and consumers
        holding only the text behave consistently across both.
        """
        if not output:
            return False
        tail = output[-500:]
        return (any(marker in tail for marker in _COMPLETION_MARKERS)
                or len(output.strip()) > 100)

    def parse_response(self, raw: str) -> Dict[str, Any]:
        """Content only — and that is the harness's limit, not a shortcut.

        There is no JSON envelope to mine, so ``tool_calls`` and ``diff`` are
        empty by FACT. Deriving a ``diff`` from a fenced block in prose would
        report a patch that was described rather than applied, which is exactly
        the kind of unearned column the exa-bench-03 comparison must not have.
        """
        return {
            "content": raw or "",
            "tool_calls": [],
            "diff": "",
        }


ADAPTER = CopilotCliAdapter()

__all__ = ["CopilotCliAdapter", "ADAPTER", "resolve_copilot_cli"]
