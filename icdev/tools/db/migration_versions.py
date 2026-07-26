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


def discover_versions(migrations_dir: Path | None = None) -> dict[str, list[str]]:
    """Map normalised version -> sorted list of migration entry names."""
    d = migrations_dir or _MIGRATIONS_DIR
    out: dict[str, list[str]] = defaultdict(list)
    if not d.is_dir():
        return {}
    for p in sorted(d.iterdir()):
        m = _VERSION_RE.match(p.name)
        if m:
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


def load_allowlist(path: Path | None = None) -> dict[str, list[str]]:
    """Known pre-existing duplicates, grandfathered so the gate is actionable."""
    p = path or _ALLOWLIST_PATH
    if not p.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    raw = data.get("grandfathered", {}) or {}
    return {_normalise(str(k)): sorted(v or []) for k, v in raw.items()}


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
    return {
        "total_versions": len(discover_versions(migrations_dir)),
        "duplicate_versions": len(dups),
        "grandfathered": len(allowed),
        "new_violations": new,
        "shadowed_count": len(shadowed_migrations(migrations_dir)),
        "passed": not new,
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
