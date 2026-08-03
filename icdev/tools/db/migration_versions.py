#!/usr/bin/env python3
"""Migration version uniqueness — detect silently-skipped migrations.

[TEMPLATE: CUI // SP-CTI]

``schema_migrations.version`` is UNIQUE and ``MigrationRunner.get_pending_migrations``
dedupes by version *within a run as well*, keeping the first by sort order. Its
own docstring states the consequence plainly:

    "in steady state get_pending naturally yields only the first because the
     version is already in applied_versions after the first run"

So when two migration files claim the same version number, **only the first is
ever applied — the rest are skipped permanently and silently**. No error, no
warning, no row. The tables and columns they declare simply never exist, and the
first symptom is a runtime failure somewhere far away, usually swallowed by a
broad ``except``.

This is not hypothetical. A 2026-07-26 audit found ~40 tables and 23 columns
declared by migrations but absent from the live database, and a large share of
them sit behind a duplicated version number: ``283_dic_claims.sql`` lost to
``283_soar_playbook_runs.sql``, ``282_docmod_nist_pubs.sql`` to
``282_insider_risk_uba.sql``, ``289_agent_cron_jobs.sql`` to
``289_twin_compat_reports.sql``, and so on.

The 54 collisions already on disk are grandfathered in
``args/migration_duplicate_versions.yaml`` — this module exists to stop the
count growing, and to make the existing damage enumerable rather than invisible.

CLI::

    python tools/db/migration_versions.py --json          # report all duplicates
    python tools/db/migration_versions.py --gate          # exit 1 on NEW duplicates
    python tools/db/migration_versions.py --shadowed --json  # what is being skipped
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _REPO_ROOT / "tools" / "db" / "migrations"
_ALLOWLIST_PATH = _REPO_ROOT / "args" / "migration_duplicate_versions.yaml"

_VERSION_RE = re.compile(r"^(\d+)_")


def _normalise(version: str) -> str:
    """'007' and '7' are the same version to the runner."""
    return version.lstrip("0") or "0"


def _tracked_entries(migrations_dir: Path) -> set[str] | None:
    """Top-level names under *migrations_dir* holding at least one tracked file.

    Returns ``None`` when git cannot answer — no git binary, not a work tree, or
    an empty answer we cannot distinguish from "nothing is tracked here". The
    caller must then treat every entry as real: dropping migrations because git
    was unavailable would be a far worse failure than the one this guards.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(migrations_dir), "ls-files", "-z", "--", "."],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    names = {
        rel.split("/", 1)[0]
        for rel in proc.stdout.decode("utf-8", "replace").split("\0")
        if rel
    }
    return names or None


def stale_directories(migrations_dir: Path | None = None) -> list[dict[str, str]]:
    """Version-shaped directories that git does not track a single file in.

    A migration rename leaves the old directory behind in every checkout that
    predates it, holding nothing but ``__pycache__``. Git reports the tree clean
    — nothing tracked, nothing unignored — so there is no signal at all, and a
    filesystem scan happily reads the corpse as a second migration claiming that
    version. Observed 2026-08-02: ``027_pipeline_snapshots/`` and
    ``028_odc_mitre_coverage/`` produced two phantom duplicate-version failures
    locally while main was green in CI.
    """
    d = migrations_dir or _MIGRATIONS_DIR
    if not d.is_dir():
        return []
    tracked = _tracked_entries(d)
    if tracked is None:
        return []
    out: list[dict[str, str]] = []
    for p in sorted(d.iterdir()):
        m = _VERSION_RE.match(p.name)
        if m and p.is_dir() and p.name not in tracked:
            out.append({"version": _normalise(m.group(1)), "name": p.name, "path": str(p)})
    return out


def stale_directory_message(rows: list[dict[str, str]]) -> str:
    """Guidance for stale directories — deliberately not phrased as a collision."""
    if not rows:
        return ""
    listed = "\n".join(f"  {r['name']}  (v{r['version']})" for r in rows)
    return (
        f"{len(rows)} STALE LOCAL migration director{'y' if len(rows) == 1 else 'ies'} "
        "found — git tracks no file inside them:\n"
        f"{listed}\n\n"
        "These are local build artifacts, not migrations. A migration rename "
        "leaves the old directory behind holding only __pycache__, which git "
        "reports as a clean tree. Your working tree is stale; the versions do "
        "NOT collide and main is not broken.\n"
        "Remove them — one at a time, by the exact path listed above:\n"
        + "\n".join(f"    rm -rf {r['path']}" for r in rows)
        + "\n    # PowerShell: Remove-Item -Recurse -Force <path>\n"
        "Check first with `git clean -xdn tools/db/migrations`. Do NOT reach "
        "for `git clean -xdf` on that directory: it would also delete a "
        "migration you are still authoring but have not `git add`ed yet, and "
        "such a directory is listed above too — if one of these is yours, "
        "`git add` it instead of removing it."
    )


def discover_versions(migrations_dir: Path | None = None) -> dict[str, list[str]]:
    """Map normalised version -> sorted list of migration entry names.

    Directories with no git-tracked file are skipped — see
    :func:`stale_directories`. They are stale local artifacts, not migrations,
    and counting them invents duplicate versions that exist in no other checkout.
    """
    d = migrations_dir or _MIGRATIONS_DIR
    out: dict[str, list[str]] = defaultdict(list)
    if not d.is_dir():
        return {}
    stale = {r["name"] for r in stale_directories(d)}
    for p in sorted(d.iterdir()):
        m = _VERSION_RE.match(p.name)
        if m and p.name not in stale:
            out[_normalise(m.group(1))].append(p.name)
    return {v: sorted(names) for v, names in out.items()}


def find_duplicates(migrations_dir: Path | None = None) -> dict[str, list[str]]:
    """Versions claimed by more than one migration file/directory."""
    return {
        v: names
        for v, names in discover_versions(migrations_dir).items()
        if len(names) > 1
    }


def shadowed_migrations(migrations_dir: Path | None = None) -> list[dict[str, str]]:
    """The migrations that will never run.

    The runner keeps the FIRST entry by sort order for each version; every
    other entry sharing that version is shadowed.
    """
    out: list[dict[str, str]] = []
    for version, names in sorted(find_duplicates(migrations_dir).items(), key=lambda kv: int(kv[0])):
        winner, *losers = names
        for loser in losers:
            out.append({"version": version, "applied": winner, "shadowed": loser})
    return out


def _split_entry(item: Any) -> tuple[str, str]:
    """An allowlist item as (migration name, reason).

    Two accepted shapes, so the file can carry its justification inline:

        - 010_network_intelligence_schema                  # bare name, no reason
        - 010_network_intelligence_schema: why it is safe  # name + reason

    A one-key mapping rather than a parallel ``reasons:`` block, because a
    parallel block drifts the moment an entry is added or renamed and then
    documents the wrong migration — which is worse than documenting none.
    """
    if isinstance(item, dict):
        if len(item) == 1:
            name, reason = next(iter(item.items()))
            return str(name), str(reason or "").strip()
        return str(item), ""
    return str(item), ""


def load_allowlist(path: Path | None = None) -> dict[str, list[str]]:
    """Known pre-existing duplicates, grandfathered so the gate is actionable."""
    return {v: sorted(d) for v, d in load_allowlist_reasons(path).items()}


def load_allowlist_reasons(path: Path | None = None) -> dict[str, dict[str, str]]:
    """version -> {migration name -> why this shadowed entry is safe}.

    The reason is the point of the file. "Grandfathered" was only ever a
    statement that a collision predates the gate, never that it is harmless;
    the mvs-audit-03 audit found six entries whose schema no supported backend
    produced on a fresh install, so the difference is load-bearing. Recording
    the finding next to the entry means the next reader inherits the answer
    instead of re-deriving it from 60 migration files.
    """
    p = path or _ALLOWLIST_PATH
    if not p.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    raw = data.get("grandfathered", {}) or {}
    out: dict[str, dict[str, str]] = {}
    for version, items in raw.items():
        entries: dict[str, str] = {}
        for item in items or []:
            name, reason = _split_entry(item)
            entries[name] = reason
        out[_normalise(str(version))] = entries
    return out


def unexplained_entries(path: Path | None = None) -> list[str]:
    """Allowlisted migrations carrying no reason — ``version/name`` strings."""
    return sorted(
        f"{version}/{name}"
        for version, entries in load_allowlist_reasons(path).items()
        for name, reason in entries.items()
        if not reason
    )


def check(migrations_dir: Path | None = None, allowlist_path: Path | None = None) -> dict[str, Any]:
    """Report duplicates, splitting known (grandfathered) from new.

    A version already in the allowlist is only tolerated with the SAME set of
    files. Adding a third file to an existing collision is a new violation —
    it shadows one more migration.
    """
    dups = find_duplicates(migrations_dir)
    allowed = load_allowlist(allowlist_path)
    new: dict[str, list[str]] = {}
    for version, names in dups.items():
        if allowed.get(version) != names:
            new[version] = names
    # An entry with no recorded reason is a violation too. It is the same defect
    # the allowlist was created to stop — an unexamined collision that reads as
    # approved — just written down instead of left on disk.
    unexplained = unexplained_entries(allowlist_path)
    return {
        "total_versions": len(discover_versions(migrations_dir)),
        "duplicate_versions": len(dups),
        "grandfathered": len(allowed),
        "new_violations": new,
        "unexplained_entries": unexplained,
        "shadowed_count": len(shadowed_migrations(migrations_dir)),
        "stale_directories": stale_directories(migrations_dir),
        "passed": not new and not unexplained,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migration version uniqueness check")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--gate", action="store_true", help="exit 1 on NEW duplicate versions")
    ap.add_argument("--shadowed", action="store_true", help="list migrations that will never run")
    args = ap.parse_args(argv)

    if args.shadowed:
        rows = shadowed_migrations()
        if args.json:
            print(json.dumps({"shadowed": rows, "count": len(rows)}, indent=2))
        else:
            print(f"{len(rows)} migration(s) will NEVER run (shadowed by a same-version sibling):")
            for r in rows:
                print(f"  v{r['version']}: {r['shadowed']}  (shadowed by {r['applied']})")
        return 0

    result = check()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"versions on disk : {result['total_versions']}")
        print(f"duplicated       : {result['duplicate_versions']} "
              f"({result['grandfathered']} grandfathered)")
        print(f"migrations shadowed and never applied: {result['shadowed_count']}")
        if result["stale_directories"]:
            print("\n" + stale_directory_message(result["stale_directories"]))
        if result["new_violations"]:
            print("\nNEW duplicate version(s) — these WILL be silently skipped:")
            for v, names in sorted(result["new_violations"].items(), key=lambda kv: int(kv[0])):
                print(f"  v{v}: {names}")
            print("\nFix: renumber to the next unused version. Do not add to the "
                  "allowlist — it exists to freeze historical damage, not to absorb new.")
        else:
            print("\nOK — no new duplicate versions.")

    if args.gate and not result["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
