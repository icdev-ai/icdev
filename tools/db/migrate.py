#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Database Migration CLI.

D150: Lightweight migration runner — apply, rollback, validate, scaffold.

Usage:
    python tools/db/migrate.py --status [--json]
    python tools/db/migrate.py --up [--target 005] [--dry-run]
    python tools/db/migrate.py --down [--target 003]
    python tools/db/migrate.py --validate [--json]
    python tools/db/migrate.py --create "add_feature_table"
    python tools/db/migrate.py --mark-applied 001
    python tools/db/migrate.py --up --all-tenants
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.migration_runner import MigrationRunner  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"


def _db_label(engine: str, db_path) -> str:
    """Name the database a run ACTUALLY used, never the one it was handed.

    On PostgreSQL ``db_path`` is ignored by ``_get_connection()`` — the
    connection comes from ``ICDEV_DATABASE_URL`` or the ``ICDEV_PG_*`` vars.
    Printing the path anyway names a database the run never opened (measured
    2026-09-05: ``migrate.py --status`` reported a ``data/icdev.db`` that does
    not exist on disk, above an ``Applied: 432 | Pending: 0`` read from
    PostgreSQL). An operator applying a migration must be able to see WHERE the
    irreversible act landed, so the label follows the engine.
    """
    import os

    if str(engine).lower() not in ("postgresql", "postgres", "pg"):
        return str(db_path)

    db_url = os.environ.get("ICDEV_DATABASE_URL")
    if db_url:
        # A DSN carries credentials — name the source, never echo the value.
        return "postgresql (ICDEV_DATABASE_URL)"
    host = os.environ.get("ICDEV_PG_HOST", "localhost")
    port = os.environ.get("ICDEV_PG_PORT", "5432")
    name = os.environ.get("ICDEV_PG_DATABASE", "icdev")
    return f"postgresql://{host}:{port}/{name}"


BEHIND = "behind"
CHECKOUT_CURRENT = "current"
CHECKOUT_UNMEASURABLE = "unmeasurable"


def checkout_drift(runner, ref: str = "origin/main", root=None) -> dict:
    """Does *ref* hold migrations THIS FILESYSTEM does not contain?

    ``--status`` and ``--up`` both read the filesystem, so a checkout behind the
    default branch reports ``Pending: 0`` and applies nothing — which reads as
    "this deployment is fully migrated" while merged migrations sit unapplied.
    Measured 2026-09-12 from a worktree 7 commits behind main: against the SAME
    live PostgreSQL, in the same minute, ``migration_drift.py`` reported
    ``pending`` naming ``20260912122759_add_experiment_candidate_lane`` while
    ``--up --dry-run`` printed ``No pending migrations.`` The tool an operator
    ACTS on was the one that read as "nothing to do".

    The consumer turned that into a certification, not just confusing output.
    ``production_audit.check_migration_status`` (PRF-001) reads the
    ``pending_count`` below and reports ``pass`` / "All migrations applied";
    ``production_remediate`` then auto-fixes PRF-001 by running ``--up``, which
    applies nothing and exits 0. The audit that exists to catch an unmigrated
    deployment certified one — autonomy-dep-01's own defect, one layer up, in
    the tool prescribed to fix it.

    THE COMPARISON IS AGAINST DIRECTORY NAMES, NOT ``discover_migrations()``.
    That method drops a directory carrying neither ``up.sql`` nor ``up.py``
    (17 exist), so comparing against it would report a present-but-unrunnable
    migration as "your checkout is behind" — a different defect with a different
    fix. The same ``_VERSION_DIR_RE`` the runner parses names with is used here,
    so "what counts as a migration" cannot drift between applying and auditing.

    Three states, and only one of them is silence:

        current       the branch holds nothing this checkout lacks
        behind        named versions are on the branch and absent here — merge
                      origin/main before believing ``Pending: 0``
        unmeasurable  the ref could not be read (no remote, shallow clone)

    ``unmeasurable`` is never folded into ``current``, for the reason
    ``migration_drift`` states: a check that could not run is not a check that
    found nothing.
    """
    try:
        # Reuse the detector's git read — the question "what is on the branch"
        # must have exactly one answer in this tree.
        from tools.db.migration_drift import branch_migrations
    except Exception as exc:  # noqa: BLE001
        return {"state": CHECKOUT_UNMEASURABLE, "ref": ref,
                "reason": f"migration_drift could not be imported: {str(exc)[:120]}",
                "missing_here": None, "missing_count": None}

    on_branch = branch_migrations(ref, root)
    if on_branch is None:
        return {"state": CHECKOUT_UNMEASURABLE, "ref": ref,
                "reason": f"migrations on {ref} could not be read",
                "missing_here": None, "missing_count": None}

    import re

    from tools.db.migration_runner import _VERSION_DIR_RE, _VERSION_FILE_RE

    here = set()
    migrations_dir = getattr(runner, "migrations_dir", None)
    if migrations_dir is not None and migrations_dir.exists():
        for entry in migrations_dir.iterdir():
            pattern = _VERSION_FILE_RE if entry.is_file() else _VERSION_DIR_RE
            match = re.match(pattern, entry.name)
            if match:
                here.add(match.group(1))

    missing = sorted(v for v in on_branch if v not in here)
    return {
        "state": BEHIND if missing else CHECKOUT_CURRENT,
        "ref": ref,
        "missing_count": len(missing),
        "missing_here": [{"version": v, "name": on_branch[v]} for v in missing],
    }


def _format_checkout_drift(drift: dict) -> list:
    """Lines warning that ``Pending: 0`` does not mean current. Never silent."""
    state = (drift or {}).get("state")
    if state == CHECKOUT_UNMEASURABLE:
        return ["",
                f"Checkout vs {drift.get('ref')}: unmeasurable — "
                f"{drift.get('reason')}",
                "  (unmeasurable is NOT current — nobody could check whether a "
                "merged migration is missing from this checkout)"]
    if state != BEHIND:
        return []
    lines = ["",
             f"THIS CHECKOUT IS BEHIND {drift.get('ref')} — "
             f"{drift['missing_count']} migration(s) merged and absent here:"]
    for item in drift.get("missing_here") or []:
        lines.append(f"    absent here: {item['name']}")
    lines.append("  A pending count read from this filesystem CANNOT see them, so "
                 "'Pending: 0' does not mean this deployment is current.")
    lines.append("  Merge the default branch into this checkout, then re-run. "
                 "Cross-check with: python tools/db/migration_drift.py")
    return lines


def _format_status(status: dict) -> str:
    """Format migration status for human-readable output."""
    lines = [
        f"Database: {_db_label(status['engine'], status['db_path'])}",
        f"Engine: {status['engine']}",
        f"Migrations table: {'exists' if status['has_migrations_table'] else 'missing'}",
        f"Current version: {status['current_version'] or 'none'}",
        f"Applied: {status['applied_count']}  |  Pending: {status['pending_count']}",
    ]

    if status["applied"]:
        lines.append("\nApplied migrations:")
        for m in status["applied"]:
            lines.append(f"  [{m['version']}] {m['name']} (applied {m['applied_at']})")

    if status["pending"]:
        lines.append("\nPending migrations:")
        for m in status["pending"]:
            lines.append(f"  [{m['version']}] {m['name']}")

    if status["issues"]:
        lines.append("\nIssues:")
        for issue in status["issues"]:
            lines.append(f"  [{issue['version']}] {issue['issue']}: {issue['detail']}")

    # Printed LAST and unconditionally when it has something to say: the
    # pending count above is filesystem-scoped and cannot see a merged
    # migration this checkout does not hold.
    lines.extend(_format_checkout_drift(status.get("checkout_drift")))

    return "\n".join(lines)


def _get_tenant_db_paths() -> list:
    """Discover tenant database files."""
    tenant_dir = BASE_DIR / "data" / "tenants"
    if not tenant_dir.exists():
        return []
    return sorted(tenant_dir.glob("*.db"))


def main():
    # xit-decl-01: never migrate another parent's database from this checkout.
    from icdev.core.context import assert_identity

    assert_identity(anchor=__file__)
    parser = argparse.ArgumentParser(description="ICDEV™ Database Migration Tool")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="Database file path")
    parser.add_argument("--status", action="store_true", help="Show migration status")
    parser.add_argument("--up", action="store_true", help="Apply pending migrations")
    parser.add_argument("--down", action="store_true", help="Roll back last migration")
    parser.add_argument("--target", help="Target version (for --up or --down)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without applying")
    parser.add_argument("--converge", action="store_true",
                        help="Apply in repeated passes until fixpoint (tolerates out-of-order migrations)")
    parser.add_argument("--validate", action="store_true", help="Validate migration checksums")
    parser.add_argument("--create", metavar="NAME",
                        help="Create new migration scaffold (allocates a "
                             "YYYYMMDDHHMMSS version — always use this rather "
                             "than hand-numbering)")
    parser.add_argument("--mark-applied", metavar="VERSION", help="Mark version as applied")
    parser.add_argument("--all-tenants", action="store_true", help="Apply to all tenant DBs too")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    import os

    _backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
    # For PostgreSQL, db_path is ignored by _get_connection() — get_connection() uses PG env vars.
    runner = MigrationRunner(db_path=args.db_path, engine=_backend)

    # ---- Status ----
    if args.status:
        status = runner.get_status()
        # A filesystem-scoped pending count is not an answer to "is this
        # deployment migrated" unless the checkout holds what merged.
        status["checkout_drift"] = checkout_drift(runner)
        if args.json:
            print(json.dumps(status, indent=2, default=str))
        else:
            print(_format_status(status))
        return

    # ---- Validate ----
    if args.validate:
        runner.ensure_migrations_table()
        issues = runner.validate_checksums()
        if args.json:
            print(json.dumps({"issues": issues, "valid": len(issues) == 0}, indent=2))
        elif issues:
            print("Validation FAILED:")
            for issue in issues:
                print(f"  [{issue['version']}] {issue['issue']}: {issue['detail']}")
            sys.exit(1)
        else:
            print("All migration checksums valid.")
        return

    # ---- Create ----
    if args.create:
        path = runner.create_migration(args.create)
        result = {"created": path}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Created migration: {path}")
        return

    # ---- Mark Applied ----
    if args.mark_applied:
        runner.mark_applied(args.mark_applied)
        if args.json:
            print(json.dumps({"marked_applied": args.mark_applied}))
        else:
            print(f"Marked migration {args.mark_applied} as applied.")
        return

    # ---- Migrate Up ----
    if args.up:
        db_paths = [args.db_path]
        if args.all_tenants:
            db_paths.extend(_get_tenant_db_paths())

        # ---- Converge mode: repeated passes until fixpoint ----
        if args.converge:
            all_conv = {}
            for db_path in db_paths:
                r = MigrationRunner(db_path=db_path, engine=_backend)
                all_conv[str(db_path)] = r.migrate_up_converge(target=args.target)
            if args.json:
                print(json.dumps(all_conv, indent=2, default=str))
            else:
                for db_path, conv in all_conv.items():
                    print(f"[{_db_label(_backend, db_path)}] converged in {len(conv['passes'])} pass(es); "
                          f"applied {conv['applied_total']} migration(s)")
                    for p in conv["passes"]:
                        print(f"  pass {p['pass']}: applied={p['applied']} failed={p['failed']}")
                    if conv["remaining_failures"]:
                        print("  Unresolved after convergence:")
                        for f in conv["remaining_failures"]:
                            print(f"    [{f['version']}] {f['name']}: {f['error']}")
            for conv in all_conv.values():
                if conv["remaining_failures"]:
                    sys.exit(1)
            return

        all_results = {}
        for db_path in db_paths:
            r = MigrationRunner(db_path=db_path, engine=_backend)
            results = r.migrate_up(target=args.target, dry_run=args.dry_run)
            all_results[str(db_path)] = results

        if args.json:
            print(json.dumps(all_results, indent=2, default=str))
        else:
            for db_path, results in all_results.items():
                label = _db_label(_backend, db_path)
                if not results:
                    print(f"[{label}] No pending migrations.")
                    # "Nothing to apply" and "nothing to apply THAT THIS
                    # CHECKOUT CAN SEE" are different claims, and only the
                    # second one is true from a checkout behind the branch.
                    for line in _format_checkout_drift(checkout_drift(runner)):
                        print(line)
                    continue
                print(f"[{label}]")
                for r in results:
                    status = "OK" if r.get("success") else f"FAILED: {r.get('error')}"
                    ms = r.get("execution_time_ms", "")
                    dry = " (dry run)" if r.get("dry_run") else ""
                    print(f"  [{r['version']}] {r['name']} — {status} {ms}ms{dry}")

            # Exit with error if any failed
            for results in all_results.values():
                if any(not r.get("success") for r in results):
                    sys.exit(1)
        return

    # ---- Migrate Down ----
    if args.down:
        results = runner.migrate_down(target=args.target)
        if args.json:
            print(json.dumps(results, indent=2, default=str))
        elif not results:
            print("Nothing to roll back.")
        else:
            for r in results:
                status = "OK" if r.get("success") else f"FAILED: {r.get('error')}"
                print(f"  [{r['version']}] {r['name']} — rolled back {status}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
