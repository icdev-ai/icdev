# CUI // SP-CTI
"""Is this deployment running the schema that MERGED? (autonomy-dep-01)

THE DEFECT, live when this was written. `code_staleness` reported every process
on the board as ``no recorded code version`` — not because the code was missing,
but because migration ``20260821024132_agent_sessions_code_identity`` had never
been APPLIED here, so ``agent_sessions`` had no identity columns. The code of
autonomy-id-01 was on main, its tests were green, and it produced nothing.

Measured 2026-08-21: 76 timestamped migrations in the tree, 74 applied, TWO
pending — that one and ``20260821045946_restore_act_audit_event_type`` from
autonomy-act-03. Two of the last three cards to ship a migration were inert on
the very deployment that merged them.

NOTHING DETECTED IT. ``substrate_liveness`` asks whether a declared table has
ROWS, which is a different question: it answers ``absent`` without knowing that a
migration for that table is sitting on the default branch unapplied. Migrations
are applied by ``tools/db/migrate.py`` and ``tools/db/init_icdev_db.py``, wired
to NO startup path and NO reflex. So a capability can merge, pass CI, and sit
inert in production indefinitely — the platform's signature defect, one layer
below the code.

COMPARE SETS, NEVER A MAXIMUM, and this is not a stylistic preference.
``schema_migrations.version`` holds BOTH the closed legacy ``NNN`` sequence and
14-digit timestamps, and a lexicographic ``ORDER BY`` puts ``'343'`` AFTER
``'20260821...'`` because ``'3' > '2'``. Scoping this card, that exact mistake
produced the conclusion "no timestamped migration has ever been applied", which
was false by a factor of 37. A set difference cannot make that error.

THREE STATES, and only one is the finding:

    current       every migration on the branch is applied here
    pending       on the branch, NOT applied here — the migrations are NAMED
    unmeasurable  the branch could not be read, `schema_migrations` was
                  unreachable, or this database has no migration history at all

`unmeasurable` is never folded into `current`. A fresh database has applied
nothing, and reporting that as "0 pending" would call an empty deployment fully
migrated.

REPORT ONLY. Applying a migration writes schema on a live database; that is a
deployment act with its own blast radius and it is deliberately not offered here
— see autonomy-dep-02, whose answer to "may the restore tier apply one" is no.

Usage:
    python tools/db/migration_drift.py
    python tools/db/migration_drift.py --json
    python tools/db/migration_drift.py --ref origin/main --gate
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 — git only, fixed argv, shell=False
import sys
from typing import Any, Dict, List, Optional, Set

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from tools.db.migration_runner import _VERSION_DIR_RE  # noqa: E402

# ── States ──────────────────────────────────────────────────────────────────
CURRENT = "current"
PENDING = "pending"
UNMEASURABLE = "unmeasurable"

DEFAULT_REF = "origin/main"
MIGRATIONS_DIR = "tools/db/migrations"
GIT_TIMEOUT_SECONDS = 20


def _run_git(args: List[str], root: Optional[str] = None):
    return subprocess.run(  # nosec B603 B607 — fixed argv, shell=False, git only
        ["git", "-C", root or _BASE, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT_SECONDS, check=False, shell=False,
    )


def branch_migrations(ref: str = DEFAULT_REF, root: Optional[str] = None,
                      runner=None) -> Optional[Dict[str, str]]:
    """``{version: directory_name}`` for every migration on *ref*.

    Read from git rather than the filesystem: the working tree is whatever this
    checkout happens to hold, which for a worktree or a mid-rebase checkout is
    not what merged. The question is about the DEFAULT BRANCH.

    Returns None — never an empty dict — when the ref cannot be read. A shallow
    clone with no remote must report `unmeasurable`, not "nothing on the branch",
    which would make every deployment look perfectly current.
    """
    run = runner or _run_git
    try:
        result = run(["ls-tree", "--name-only", ref, f"{MIGRATIONS_DIR}/"], root)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0:
        return None

    out: Dict[str, str] = {}
    for line in (getattr(result, "stdout", "") or "").splitlines():
        entry = line.strip().rstrip("/")
        if not entry:
            continue
        name = entry.split("/")[-1]
        match = re.match(_VERSION_DIR_RE, name)
        if match:
            # The SAME regex the runner parses directory names with, so "what
            # counts as a migration" cannot drift between applying and auditing.
            out[match.group(1)] = name
    return out


def applied_versions(conn=None) -> Optional[Set[str]]:
    """Versions recorded as applied here, excluding rolled-back ones.

    None when the table cannot be read. A rolled-back migration is deliberately
    NOT applied: its schema change has been undone, so counting it would report
    a deployment as current while the column it added is gone.
    """
    close = False
    if conn is None:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            close = True
        except Exception:  # noqa: BLE001
            return None
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations WHERE rolled_back_at IS NULL"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    finally:
        if close:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    out = set()
    for row in rows or []:
        value = dict(row).get("version")
        if value is not None:
            out.add(str(value).strip())
    return out


def drift(ref: str = DEFAULT_REF, root: Optional[str] = None,
          conn=None, runner=None,
          on_branch: Optional[Dict[str, str]] = None,
          applied: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Compare the branch against this deployment. Never raises."""
    if on_branch is None:
        on_branch = branch_migrations(ref, root, runner)
    if on_branch is None:
        return {"state": UNMEASURABLE, "ref": ref,
                "reason": f"migrations on {ref} could not be read",
                "pending": None, "pending_count": None,
                "on_branch_count": None, "applied_count": None}

    if applied is None:
        applied = applied_versions(conn)
    if applied is None:
        return {"state": UNMEASURABLE, "ref": ref,
                "reason": "schema_migrations could not be read",
                "pending": None, "pending_count": None,
                "on_branch_count": len(on_branch), "applied_count": None}

    if not applied:
        # No migration history AT ALL. Every branch migration would show as
        # pending, which is technically true and useless — this is a fresh or
        # bootstrapping database, not a drifted one.
        return {"state": UNMEASURABLE, "ref": ref,
                "reason": ("this database has no migration history — a fresh or "
                           "bootstrapping deployment, not a drifted one"),
                "pending": None, "pending_count": None,
                "on_branch_count": len(on_branch), "applied_count": 0}

    # SET DIFFERENCE. Never a max, never a sort — see the module docstring.
    pending = sorted(v for v in on_branch if v not in applied)
    # Applied here but absent from the branch: a migration that came from a
    # branch which never merged, or one deleted since. Reported for context,
    # deliberately NOT a finding — it says nothing about whether this deployment
    # is missing something.
    extra = sorted(v for v in applied if v not in on_branch)

    return {
        "state": PENDING if pending else CURRENT,
        "ref": ref,
        "on_branch_count": len(on_branch),
        "applied_count": len(applied),
        "pending_count": len(pending),
        "pending": [{"version": v, "name": on_branch[v]} for v in pending],
        "applied_not_on_branch_count": len(extra),
        "applied_not_on_branch": extra[:20],
    }


def render(report: Dict[str, Any]) -> str:
    state = report["state"]
    out = [f"Migration drift vs {report['ref']} — {state}"]
    if report.get("reason"):
        out.append(f"  {report['reason']}")
    if state == UNMEASURABLE:
        out.append("  (unmeasurable is NOT current — nobody could check)")
        return "\n".join(out)

    out.append(f"  on branch {report['on_branch_count']} · "
               f"applied here {report['applied_count']} · "
               f"PENDING {report['pending_count']}")
    for item in report.get("pending") or []:
        out.append(f"    pending: {item['name']}")
    extra = report.get("applied_not_on_branch_count") or 0
    if extra:
        out.append(f"  {extra} applied here but not on {report['ref']} "
                   f"(from an unmerged branch, or deleted since — not a finding)")
    if state == CURRENT:
        out.append("  Every migration on the branch is applied here.")
    else:
        out.append("  A merged capability whose migration is pending is INERT here: "
                   "its code is present, its schema is not.")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--root", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 when a migration is pending")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = drift(ref=args.ref, root=args.root)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))

    if args.gate and report["state"] == PENDING:
        return 1
    # UNMEASURABLE exits 2 even under --gate: a check that could not run is not
    # a check that found nothing.
    return 2 if report["state"] == UNMEASURABLE else 0


if __name__ == "__main__":
    raise SystemExit(main())
