# CUI // SP-CTI
"""The ONE protected-path rule, importable by anything and runnable with no tree.

WHY THIS FILE EXISTS (mfx-mrg-07). `protected_paths` in args/pr_watcher_config.yaml
names the files a CI-green kanban PR may NOT auto-merge over -- above all the
merge ladder itself. Two doors honoured it (the local watcher's task-linked
merge and its unlinked sweep) and one did not: `.github/workflows/pr-watcher.yml`,
the Actions cron that squash-merges any green non-draft `kanban/*` PR as
`github-actions[bot]`. MEASURED 2026-09-07 over the last 84 merged PRs: ten
touched a protected path, EIGHT merged unattended through that workflow, seven
of those eight edited tools/ci/pr_watcher.py -- the first entry in the list
and the reason the list exists. mfx-mrg-04's own PR (#2143), which built the
audited `--protected-ok` door on the premise that a protected PR needs a human,
was merged by the cron at 05:02 on a routine sweep. The door had never been used.

The workflow does no checkout and runs no Python from the tree; it shells `gh`.
So the rule had to become something it can run: THIS module is import-free
(stdlib only, no `tools.*`, no `icdev.*`), so `python3 tools/ci/protected_paths.py`
runs from a sparse checkout of ONE file on a bare runner with nothing installed,
and `tools.ci.merge_readiness.protected_hits` is a RE-EXPORT of the function
below, not a copy -- the same discipline `tools/git/coordination_paths.py`
applies to the sibling-conflict list. A test pins the identity.

THE LIST IS NEVER RESPELLED. The workflow reads `protected_paths` from
args/pr_watcher_config.yaml on the PR's BASE branch and hands the text to
`--config-file`; nothing here, and nothing in the workflow, carries a path
literal from that list. A PR that deletes its own path from the list must not
thereby authorise its own merge -- reading the base branch is what stops it.

THREE EXIT CODES, and the middle one is the finding:
    0   clean -- the PR touches no protected path (or protection is OFF: an
        EMPTY list on the base branch is a decision that already went through a
        door, and it is reported as `protection off`, never as `clean`)
    1   protected -- at least one hit, printed one per line
    2   UNDECIDABLE -- the config could not be read or parsed, or the file list
        is missing / truncated. Fail-closed: the caller must NOT merge. This is
        the same precedent the workflow already applies to an empty
        required-check answer, and it has a COST worth stating: this cron is the
        door that kept the board moving while the local watcher was blind to a
        tripped GraphQL limit (2026-09-06), so a persistent config-read failure
        stalls every kanban merge until a human looks. That is preferred to the
        alternative -- an unreadable config reading as "nothing protected"
        reinstates exactly the hole this module closes.
"""
from __future__ import annotations

import argparse
import sys
from typing import Iterable, List, Optional

EXIT_CLEAN = 0
EXIT_PROTECTED = 1
EXIT_UNDECIDABLE = 2

CONFIG_KEY = "protected_paths"


def protected_hits(
    changed_files: Optional[Iterable[str]], protected_paths: Iterable[str]
) -> Optional[List[str]]:
    """Which protected paths this PR touches, or None when nothing is protected.

    An entry matches a path EXACTLY or as a directory prefix: ``e`` matches ``p``
    when ``p == e`` or ``p.startswith(e + "/")``. Both halves are load-bearing.
    A bare prefix test would make the entry ``tools/ci/pr_watcher.py`` also catch
    ``tools/ci/pr_watcher_helpers.py``, and a control that stops work it was
    never meant to stop gets switched off.

    FAIL-CLOSED, and this is the OPPOSITE default from the sibling-conflict map
    a few lines away in the watcher, so the asymmetry needs saying. That map
    answers "might these two PRs collide?" — degrading it to a warning costs a
    retry. This answers "may this PR edit the merger itself?" — degrading it to
    a warning costs the control entirely, because the case where the file list
    is unavailable is not distinguishable from the case where it is unavailable
    BECAUSE the PR is unusual. So a ``changed_files`` of None, with any path
    protected, is treated as a hit. A merge gate that opens when it cannot see
    is not a gate.

    Returns ``None`` (not an empty list) when no path is configured, so a caller
    can tell "protection is off" from "protection is on and this PR is clean".
    """
    entries = [str(e).strip().replace("\\", "/").strip("/")
               for e in (protected_paths or ()) if str(e or "").strip()]
    if not entries:
        return None
    if changed_files is None:
        return sorted(entries)          # fail closed — see above
    paths = [str(f).strip().replace("\\", "/").lstrip("/")
             for f in changed_files if str(f or "").strip()]
    hits = {e for e in entries
            for f in paths if f == e or f.startswith(e + "/")}
    return sorted(hits)


# ── reading the list out of the config text ────────────────────────────────

class ConfigUnreadable(ValueError):
    """The config text could not be parsed into a `protected_paths` list."""


def _strip_comment(line: str) -> str:
    """Drop a YAML `# comment` that is not inside quotes (good enough for a
    block sequence of bare or quoted path scalars; anything odder refuses)."""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _fallback_block_sequence(text: str) -> List[str]:
    """Read `protected_paths:` as a flat block sequence WITHOUT PyYAML.

    Deliberately narrow: the key at column 0, then `- <scalar>` items, then the
    next column-0 key ends it. A flow sequence (`[a, b]`), a nested mapping, an
    anchor, a multi-line scalar -- anything this reader is not sure of --
    REFUSES rather than guessing, because a guess in the permissive direction
    is a merge the list was written to stop.
    """
    lines = text.splitlines()
    start = None
    for i, raw in enumerate(lines):
        line = _strip_comment(raw)
        if line.startswith(CONFIG_KEY + ":"):
            rest = line[len(CONFIG_KEY) + 1:].strip()
            if rest and rest not in ("[]",):
                raise ConfigUnreadable(
                    f"{CONFIG_KEY} is not a block sequence (got {rest!r})")
            if rest == "[]":
                return []
            start = i + 1
            break
    if start is None:
        raise ConfigUnreadable(f"{CONFIG_KEY} not found at top level")
    items: List[str] = []
    for raw in lines[start:]:
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if not line[0].isspace():
            break                       # next top-level key
        item = line.strip()
        if not item.startswith("- "):
            raise ConfigUnreadable(
                f"unexpected line inside {CONFIG_KEY}: {raw.strip()!r}")
        value = item[2:].strip()
        if not value or value[0] in "[{&*|>!%@`":
            raise ConfigUnreadable(
                f"unsupported {CONFIG_KEY} item: {raw.strip()!r}")
        if value[0] in "'\"":
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigUnreadable(f"unterminated quote: {raw.strip()!r}")
            value = value[1:-1]
        items.append(value)
    return items


def load_protected_paths(text: str) -> List[str]:
    """`protected_paths` out of config TEXT -- PyYAML when present, else the
    strict block-sequence reader. Raises ConfigUnreadable on anything that is
    not a flat list of strings. The KEY may legitimately be absent or empty:
    that is protection OFF and returns []."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return _fallback_block_sequence(text)
    try:
        doc = yaml.safe_load(text)
    except Exception as exc:            # noqa: BLE001 - any parse failure refuses
        raise ConfigUnreadable(f"config is not valid YAML: {exc}") from exc
    if doc is None:
        raise ConfigUnreadable("config is empty")
    if not isinstance(doc, dict):
        raise ConfigUnreadable("config top level is not a mapping")
    raw = doc.get(CONFIG_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(e, str) for e in raw):
        raise ConfigUnreadable(f"{CONFIG_KEY} is not a list of strings")
    return list(raw)


# ── the probe ──────────────────────────────────────────────────────────────

def _read_lines(path: str) -> Optional[List[str]]:
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def probe(config_text: str, files: Optional[List[str]],
          *, expected_count: Optional[int] = None) -> tuple[int, List[str], str]:
    """(exit_code, hits, reason) for one PR. Pure: no I/O, no forge."""
    try:
        paths = load_protected_paths(config_text)
    except ConfigUnreadable as exc:
        return EXIT_UNDECIDABLE, [], f"config unreadable: {exc}"
    if not paths:
        return EXIT_CLEAN, [], "protection off: protected_paths is empty on the base branch"
    if files is None:
        return EXIT_UNDECIDABLE, sorted(protected_hits(None, paths) or []), \
            "file list unavailable"
    if expected_count is not None and expected_count != len(files):
        return EXIT_UNDECIDABLE, sorted(protected_hits(None, paths) or []), (
            f"file list truncated: forge reports {expected_count} changed "
            f"file(s), {len(files)} listed")
    hits = protected_hits(files, paths) or []
    if hits:
        return EXIT_PROTECTED, hits, "touches protected path(s)"
    return EXIT_CLEAN, [], f"clean against {len(paths)} protected path(s)"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Does this file set touch a protected path? 0 clean / 1 "
                    "protected / 2 undecidable (fail-closed).")
    ap.add_argument("--config-file", required=True,
                    help="pr_watcher_config.yaml TEXT as fetched from the BASE branch")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--files-from", help="file holding one changed path per line")
    src.add_argument("--files", nargs="*", help="changed paths")
    ap.add_argument("--expected-count", type=int, default=None,
                    help="the forge's changedFiles; a listing shorter than this "
                         "is truncated and refuses")
    args = ap.parse_args(argv)

    try:
        with open(args.config_file, encoding="utf-8") as fh:
            config_text = fh.read()
    except OSError as exc:
        print(f"UNDECIDABLE: config unreadable: {exc}")
        return EXIT_UNDECIDABLE
    files: Optional[List[str]]
    if args.files_from is not None:
        try:
            files = _read_lines(args.files_from)
        except OSError as exc:
            print(f"UNDECIDABLE: file list unreadable: {exc}")
            return EXIT_UNDECIDABLE
    else:
        files = list(args.files or [])

    code, hits, reason = probe(config_text, files, expected_count=args.expected_count)
    label = {EXIT_CLEAN: "CLEAN", EXIT_PROTECTED: "PROTECTED",
             EXIT_UNDECIDABLE: "UNDECIDABLE"}[code]
    print(f"{label}: {reason}")
    for h in hits:
        print(h)
    return code


if __name__ == "__main__":
    sys.exit(main())
