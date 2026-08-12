#!/usr/bin/env python3
# CUI // SP-CTI
"""The sensitive-path inventory — one list, three consumers (exa-bench-09).

MEASURED, three holes on three surfaces, all of the same shape: each layer had
its own idea of "a credential", or no idea at all.

``args/file_access_tiers.yaml`` ``zero_access``
    Covered ``.env``, ``**/.ssh/*``, ``**/*.pem``, ``**/*.key`` and
    ``**/credentials.json`` — and therefore missed ``~/.aws/credentials`` (no
    extension, and the pattern is ``credentials.JSON``), ``~/.config/gh/hosts.yml``,
    ``~/.docker/config.json``, ``~/.kube/config`` and ``~/.netrc``. Its Bash
    branch inspected a command for ``rm`` targets and ``>`` redirects only, so a
    plain ``cat`` of a secret was never examined at all.
``tools/agent_runtime/approval_gate.py``
    Had no path concept. ``read_file`` is tier ``reversible`` and :func:`classify`
    rule 0 exempts a reversible tool from content escalation — correctly, because
    a tool that does not execute its arguments cannot be made irreversible by
    them.
``agent_workflow_tools.allowed`` in ``args/security_gates.yaml``
    Names ``read_file`` with no path constraint. A name is a constant; a path is
    an argument, so a NAME allowlist says nothing about reach.

Why one file and not three
--------------------------
Three lists is three lists that drift, and the drift is silent: the surface that
falls behind still returns "allowed" rather than an error, so nothing reports
it. This module is the single inventory; the data lives in
``args/sensitive_paths.yaml`` (FORGE args layer) so the list is an operator edit,
not a code change.

The axis is DISCLOSURE, deliberately
------------------------------------
Reversibility cannot express this and was never going to. A read of ``~/.netrc``
is *perfectly reversible* — nothing changed — and *completely unrecoverable* —
the credential is disclosed and cannot be un-disclosed. Those are two different
questions, and the four reversibility tiers only have a word for the first. So
this is a second dimension consulted **alongside** the tier, never a re-tiering
of ``read_file``: rule 0's escalation exemption stays exactly as it is, because
it is what stops ``read_file("how do I git push safely")`` halting for human
approval, and a gate that prompts on reads teaches operators to approve
reflexively.

Not :mod:`tools.security.secret_detector`
-----------------------------------------
That module detects credential *content* — ``AKIA…``, ``-----BEGIN … PRIVATE
KEY-----``, ``ghp_…`` — inside files it is already allowed to open. This one
names the paths that must not be opened. Checked for a reusable path inventory
before authoring this: it has none, only ``BUILTIN_PATTERNS`` over content and
``SKIP_DIRS``/``SKIP_EXTENSIONS`` over scan scope.

Dependencies
------------
Standard library plus an optional ``yaml``. Nothing first-party, deliberately:
``tools/hooks/shared_checks.py`` loads this module BY PATH from
``.claude/hooks/pre_tool_use.py``, which is a fresh interpreter on every single
tool call, and ``import tools`` alone costs ~92ms there.

CLI::

    python tools/security/sensitive_paths.py --check ~/.aws/credentials --json
    python tools/security/sensitive_paths.py --check-command "cat ~/.netrc" --json
    python tools/security/sensitive_paths.py --list --json
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONFIG_FILENAME = "sensitive_paths.yaml"
CONFIG_ENV = "ICDEV_SENSITIVE_PATHS"

#: Argument keys whose VALUE is a path. Only these are matched for a non-shell
#: tool: a tool that reads a file has its target here, and scanning the whole
#: flattened input instead would escalate ``write_file(content="…~/.netrc…")``,
#: which is prose about a path rather than a read of one.
PATH_ARG_KEYS = frozenset({
    "path", "paths", "file", "files", "file_path", "filepath", "file_paths",
    "filename", "filenames", "notebook_path", "source", "src", "target",
    "destination", "dest", "dir", "directory", "root", "pattern_path",
})

# Fallback inventory, used when args/sensitive_paths.yaml cannot be read.
# Fail-closed in the sense that matters here: a missing config file must never
# be the reason a credential path reads as ordinary. Deliberately the five that
# exa-bench-09 measured as missing plus the material every surface already
# agreed on — not a copy of the YAML, which would be a second list again.
_FALLBACK: Dict[str, Any] = {
    "enabled": True,
    "entries": [
        {
            "label": "fallback_credentials",
            "detail": "credential material (fallback inventory — YAML unreadable)",
            "patterns": [
                ".env", ".env.*",
                "**/.ssh/*", "**/.gnupg/*",
                "**/*.pem", "**/*.key", "**/*.p12", "**/*.pfx", "**/*.jks",
                "**/credentials.json", "**/service-account*.json",
                "**/.aws/credentials", "**/.aws/config",
                "**/.config/gh/hosts.yml",
                "**/.docker/config.json",
                "**/.kube/config",
                "**/.netrc", "**/_netrc",
                "**/.git-credentials",
                "**/secrets.yaml", "**/secrets.yml",
                "**/*.tfstate", "**/*.tfstate.backup",
            ],
        }
    ],
    "exclusions": [".env.sample", ".env.example"],
    "read_commands": [
        "cat", "head", "tail", "more", "less", "strings", "xxd", "od",
        "base64", "grep", "rg", "type", "findstr", "get-content",
    ],
    "disclosure_commands": [],
}


# ---------------------------------------------------------------------------
# Inventory loading
# ---------------------------------------------------------------------------
def _find_config() -> Optional[Path]:
    """Locate ``args/sensitive_paths.yaml``.

    Resolved from ``__file__``, never ``os.getcwd()`` — this module is imported
    from worktrees and from CI runners whose cwd is not the repo root
    (CLAUDE.md, "Notes for agents working from worktrees").
    """
    override = os.environ.get(CONFIG_ENV)
    if override:
        p = Path(override)
        return p if p.exists() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "args" / CONFIG_FILENAME
        if candidate.exists():
            return candidate
    return None


_CACHE: Optional[Dict[str, Any]] = None


def load_inventory(*, refresh: bool = False) -> Dict[str, Any]:
    """Load (and memoize) the inventory. Never raises."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    data = dict(_FALLBACK)
    path = _find_config()
    if path is not None:
        try:
            import yaml

            with open(path, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            section = (loaded or {}).get("sensitive_paths")
            if isinstance(section, dict):
                data = section
        except Exception:  # noqa: BLE001 — fall back, never fail open
            pass
    _CACHE = data
    return data


def enabled() -> bool:
    """Whether the inventory is active. Absent key means enabled."""
    return bool(load_inventory().get("enabled", True))


def entries() -> Tuple[Dict[str, Any], ...]:
    inv = load_inventory()
    return tuple(e for e in (inv.get("entries") or []) if isinstance(e, dict))


def exclusions() -> Tuple[str, ...]:
    return tuple(str(p) for p in (load_inventory().get("exclusions") or []))


def patterns() -> Tuple[str, ...]:
    """Every sensitive glob, plus the exclusions as ``!``-prefixed entries.

    The ``!`` spelling is what ``args/file_access_tiers.yaml`` and
    ``shared_checks._matches_tier`` already use, so a tier can consume this list
    verbatim rather than needing a second exclusion mechanism.
    """
    out: List[str] = []
    for entry in entries():
        out.extend(str(p) for p in (entry.get("patterns") or []))
    out.extend(f"!{p}" for p in exclusions())
    return tuple(out)


def read_commands() -> Tuple[str, ...]:
    return tuple(
        str(c).lower() for c in (load_inventory().get("read_commands") or [])
    )


_DISCLOSURE_CACHE: Optional[Tuple[Tuple[re.Pattern, str], ...]] = None


def disclosure_patterns() -> Tuple[Tuple[re.Pattern, str], ...]:
    """Compiled ``disclosure_commands`` — secret reads with no path at all."""
    global _DISCLOSURE_CACHE
    if _DISCLOSURE_CACHE is not None:
        return _DISCLOSURE_CACHE
    out: List[Tuple[re.Pattern, str]] = []
    for raw in load_inventory().get("disclosure_commands") or []:
        if isinstance(raw, str):
            pattern, detail = raw, "discloses a credential"
        elif isinstance(raw, dict):
            pattern = str(raw.get("pattern", ""))
            detail = str(raw.get("detail", "discloses a credential"))
        else:
            continue
        if not pattern:
            continue
        try:
            out.append((re.compile(pattern, re.IGNORECASE), detail))
        except re.error:
            continue
    _DISCLOSURE_CACHE = tuple(out)
    return _DISCLOSURE_CACHE


def reset_cache() -> None:
    """Drop the memoized inventory. For tests and for ``--refresh`` callers."""
    global _CACHE, _DISCLOSURE_CACHE
    _CACHE = None
    _DISCLOSURE_CACHE = None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SensitiveMatch:
    """One sensitive path, and which inventory entry named it."""

    path: str
    pattern: str
    label: str
    detail: str

    def summary(self) -> str:
        return f"{self.path} matches {self.pattern} ({self.label}: {self.detail})"


def normalize(path: str) -> str:
    """Forward-slash a path and strip the noise a command line adds.

    Not ``Path.resolve()``: the path may name a host this process cannot see
    (a probe, a Windows path evaluated on Linux), and resolving it would either
    raise or invent a cwd-relative answer — the exact cwd sensitivity CLAUDE.md
    warns about.
    """
    if not path:
        return ""
    p = str(path).strip().strip("'\"")
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _excluded(fp: str) -> bool:
    for exc in exclusions():
        if fnmatch(fp, exc) or fnmatch(os.path.basename(fp), exc):
            return True
    return False


def match(path: str) -> Optional[SensitiveMatch]:
    """The inventory entry naming *path*, or ``None`` when it is ordinary."""
    if not enabled():
        return None
    fp = normalize(path)
    if not fp or _excluded(fp):
        return None
    base = os.path.basename(fp)
    for entry in entries():
        for pattern in entry.get("patterns") or []:
            pat = str(pattern)
            # basename as well as full path, so `.env` catches /home/x/.env —
            # the same two-sided test args/file_access_tiers.yaml already uses.
            if fnmatch(fp, pat) or fnmatch(base, pat):
                return SensitiveMatch(
                    path=fp,
                    pattern=pat,
                    label=str(entry.get("label", "sensitive")),
                    detail=str(entry.get("detail", "credential material")),
                )
    return None


def is_sensitive(path: str) -> bool:
    """True when *path* names credential material."""
    return match(path) is not None


def _iter_path_values(tool_input: Any) -> Iterable[str]:
    if not isinstance(tool_input, dict):
        return
    for key, value in tool_input.items():
        if str(key).lower() not in PATH_ARG_KEYS:
            continue
        if isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, str):
                    yield item
        elif isinstance(value, str):
            yield value


def sensitive_args(tool_input: Any) -> List[SensitiveMatch]:
    """Sensitive paths among a tool call's PATH-LIKE arguments.

    Only :data:`PATH_ARG_KEYS` are inspected. Scanning the whole flattened input
    would fire on prose — a ``content`` that mentions ``~/.netrc`` is a document
    about a credential, not a read of one — and a guard that blocks documents is
    a guard operators route around.
    """
    seen: Dict[str, SensitiveMatch] = {}
    for value in _iter_path_values(tool_input):
        hit = match(value)
        if hit is not None and hit.path not in seen:
            seen[hit.path] = hit
    return list(seen.values())


# ---------------------------------------------------------------------------
# Shell commands
# ---------------------------------------------------------------------------
_TOKEN_SPLIT = re.compile(r"[|;&\n]+")
_WS = re.compile(r"\s+")


def _segments(command: str) -> List[List[str]]:
    """Split a command line into per-verb token lists.

    Deliberately not ``shlex.split`` over the whole string: an unbalanced quote
    makes ``shlex`` raise, and a guard that gives up on a malformed command is a
    guard that a malformed command defeats. Splitting on the shell's own
    separators first keeps ``cat a | grep b`` two segments.
    """
    out: List[List[str]] = []
    for part in _TOKEN_SPLIT.split(command or ""):
        toks = [t for t in _WS.split(part.strip()) if t]
        if toks:
            out.append(toks)
    return out


def read_command_targets(command: str) -> List[str]:
    """Paths a command DISCLOSES the contents of.

    A verb from ``read_commands`` plus its non-flag operands. ``touch`` and
    ``mkdir`` are absent on purpose: they write, and a write outside the
    worktree is exa-bench-07's gap, measured by its own probes. Folding it in
    here would silently report that gap as fixed.
    """
    verbs = set(read_commands())
    if not verbs:
        return []
    out: List[str] = []
    for tokens in _segments(command):
        verb = os.path.basename(normalize(tokens[0])).lower()
        # `sudo cat x`, `command cat x` — step past the wrapper.
        idx = 0
        while verb in ("sudo", "command", "env", "nohup", "time") and idx + 1 < len(tokens):
            idx += 1
            verb = os.path.basename(normalize(tokens[idx])).lower()
        if verb not in verbs:
            continue
        for tok in tokens[idx + 1:]:
            is_posix_flag = tok.startswith("-")
            is_cmd_flag = len(tok) == 2 and tok.startswith("/")  # cmd.exe `/s`
            if is_posix_flag or is_cmd_flag or tok in ("<", ">", ">>"):
                continue
            out.append(tok)
    return out


def sensitive_read_targets(command: str) -> List[SensitiveMatch]:
    """Sensitive paths a Bash/PowerShell command would read.

    Covers the two shapes the tier list could not see: ``cat`` of a secret path
    (a read verb, never inspected — it looked for ``rm`` targets and ``>``
    redirects only) and a redirect-free ``<`` input.
    """
    hits: Dict[str, SensitiveMatch] = {}
    for target in read_command_targets(command):
        hit = match(target)
        if hit is not None:
            hits.setdefault(hit.path, hit)
    # `foo < ~/.netrc` — input redirection is a read with no verb of its own.
    for raw in re.findall(r"<\s*([^\s|;&<>]+)", command or ""):
        hit = match(raw)
        if hit is not None:
            hits.setdefault(hit.path, hit)
    return list(hits.values())


def disclosure_match(command: str) -> Optional[str]:
    """Detail of the first ``disclosure_commands`` pattern *command* matches.

    These are credential reads with NO path — ``env | grep -i key`` is the
    measured one. A path inventory structurally cannot see them, so they are
    named separately rather than pretended into the glob list.
    """
    if not enabled() or not command:
        return None
    for rx, detail in disclosure_patterns():
        if rx.search(command):
            return detail
    return None


def command_disclosure(command: str) -> Optional[str]:
    """One reason string when *command* discloses a credential, else ``None``."""
    hits = sensitive_read_targets(command)
    if hits:
        return hits[0].summary()
    return disclosure_match(command)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="The shared sensitive-path inventory (exa-bench-09)."
    )
    parser.add_argument("--check", metavar="PATH", help="classify one path")
    parser.add_argument(
        "--check-command", metavar="CMD", help="classify a shell command"
    )
    parser.add_argument("--list", action="store_true", help="show the inventory")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    if args.list:
        payload = {
            "config_path": str(_find_config() or ""),
            "enabled": enabled(),
            "entry_count": len(entries()),
            "pattern_count": len(patterns()),
            "read_command_count": len(read_commands()),
            "disclosure_pattern_count": len(disclosure_patterns()),
            "patterns": list(patterns()),
        }
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0

    if args.check:
        hit = match(args.check)
        payload = {"path": args.check, "sensitive": hit is not None,
                   "match": asdict(hit) if hit else None}
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 1 if hit else 0

    if args.check_command:
        reason = command_disclosure(args.check_command)
        payload = {"command": args.check_command, "discloses": reason is not None,
                   "reason": reason or ""}
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 1 if reason else 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
