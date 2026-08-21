# CUI // SP-CTI
"""Can this deployment still update itself, and if not, WHY? (autonomy-dep-03)

THE INCIDENT THIS EXISTS FOR, measured on the live deployment 2026-08-21.
`C:/AI/ICDev` was 22 commits behind origin/main and had stopped updating
entirely. 170 files were incoming, 11 were locally modified, and EXACTLY ONE
overlapped:

    args/projects.yaml

`code_reload.pull_if_safe` refuses when an incoming file is also locally
modified — "local changes would be lost" — which is correct: pulling over a
modified file destroys work, on a machine where several sessions share one
checkout. The refusal is right. The problem is that NOTHING READS IT.

WHY THAT ONE FILE MADE IT PERMANENT, which is the part worth understanding.
`args/projects.yaml` is AUTO-MANAGED — its own header says so — and
`kanban_project_sync.py` rewrites it in the working tree (measured: 2,109
insertions / 1,511 deletions, a full regeneration). It is ALSO the file every
project-card registration edits upstream. A reflex dirties the local side
continuously and merges touch the incoming side constantly, so once the clash
starts it never clears. A transient, correct refusal becomes a permanent freeze.

WHAT A FROZEN DEPLOYMENT COSTS, all of it measured rather than argued:
  * autonomy-id-01 recorded nothing. Its migration was applied and its columns
    existed, and still nothing was written — because the RUNNING code had zero
    references to `boot_identity`. Proven side by side against the SAME
    database: `register()` from the frozen checkout persisted a NULL identity;
    the same call from an up-to-date tree persisted `code_version=3094c4e44`.
  * `code_staleness` (autonomy-id-02) cannot report it, because it needs an
    identity row that only current code writes. The detector is disabled by the
    exact drift it exists to detect.
  * every other merged fix is equally absent from the running services, which
    execute from the checkout.
And every board, PR and CI signal stayed green throughout.

IT ASKS `pull_if_safe`, IT DOES NOT RE-DERIVE IT. The refusal ladder lives in
one function and this calls it with `dry_run=True`, which performs no merge and
does not consume the pull throttle. A reporter with its own copy of the
predicate describes an updater the deployment does not have — the defect
`deps.py` names after six enforcement sites each grew a copy.

FOUR STATES, and the two failure shapes are kept apart because they need
different repairs:

    current      nothing incoming; the deployment is on the branch
    updatable    behind, and the guard WOULD pull — a normal window between
                 poll cycles, not a finding
    blocked      behind, and the guard refuses. THE finding. Carries the reason
                 and, for the overlap case, the offending files BY NAME
    unmeasurable git could not answer

`unmeasurable` never reads as `current`. A checkout whose remote cannot be
reached is not a checkout that is up to date.

REPORT ONLY, AND IT MUST STAY THAT WAY. Do not "fix" a blocked deployment by
force-pulling or discarding local modifications: that trades a stalled update
for silent data loss on a shared checkout. The repair is to stop dirtying the
file, or to commit what dirtied it — never to overrule the guard.

Usage:
    python tools/genesis/deployment_freshness.py
    python tools/genesis/deployment_freshness.py --json
    python tools/genesis/deployment_freshness.py --root /opt/icdev --gate
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — git only, fixed argv, shell=False
import sys
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

# ── States ──────────────────────────────────────────────────────────────────
CURRENT = "current"
UPDATABLE = "updatable"
BLOCKED = "blocked"
UNMEASURABLE = "unmeasurable"

#: Refusal reasons that clear on their own. A deployment reporting one of these
#: is not frozen — it is between cycles, or being worked on deliberately.
#: `throttled` cannot occur through this module (a dry run does not throttle),
#: but is listed so a caller passing its own runner still classifies correctly.
TRANSIENT_REASONS = frozenset({"throttled", "already current", "would pull"})

GIT_TIMEOUT_SECONDS = 20


def _run_git(args: List[str], root: Optional[str] = None):
    return subprocess.run(  # nosec B603 B607 — fixed argv, shell=False, git only
        ["git", "-C", root or _BASE, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT_SECONDS, check=False, shell=False,
    )


def behind_by(ref: str = "origin/main", root: Optional[str] = None,
              runner=None) -> Optional[int]:
    """How many commits this checkout is behind *ref*, or None if unknowable.

    None, never 0: a checkout whose remote cannot be read is not a checkout
    that is up to date, and reporting 0 there is the reassurance this module
    exists to refuse.
    """
    run = runner or _run_git
    try:
        result = run(["rev-list", "--count", f"HEAD..{ref}"], root)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    text = (getattr(result, "stdout", "") or "").strip()
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def freshness(root: Optional[str] = None, ref: str = "origin/main",
              runner=None, probe=None) -> Dict[str, Any]:
    """Ask the real guard what it would do. Never raises, never mutates."""
    if probe is None:
        try:
            from pathlib import Path

            from tools.genesis.code_reload import pull_if_safe

            def probe(_root):  # noqa: ANN001
                return pull_if_safe(Path(_root) if _root else None, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            return {"state": UNMEASURABLE, "behind_by": None,
                    "reason": f"code_reload unavailable: {exc}",
                    "conflicts": [], "root": root or _BASE, "ref": ref}

    count = behind_by(ref, root, runner)

    try:
        verdict = probe(root or _BASE)
    except Exception as exc:  # noqa: BLE001
        return {"state": UNMEASURABLE, "behind_by": count,
                "reason": f"the update guard could not be asked: {exc}",
                "conflicts": [], "root": root or _BASE, "ref": ref}

    reason = str(verdict.get("reason") or "")
    conflicts = list(verdict.get("conflicts") or [])

    if reason == "already current":
        # Authoritative: the guard itself computed that nothing is incoming.
        return {"state": CURRENT, "behind_by": count or 0, "reason": reason,
                "conflicts": [], "root": root or _BASE, "ref": ref}

    if reason in TRANSIENT_REASONS:
        return {"state": UPDATABLE, "behind_by": count, "reason": reason,
                "conflicts": [], "root": root or _BASE, "ref": ref,
                "incoming": verdict.get("incoming")}

    if count is None:
        # The guard refused AND we could not measure how far behind this is.
        # That is not a freeze — it is not knowing. Reporting `blocked` here
        # raises a false alarm about a stopped deployment whenever a ref is
        # unreachable, which is precisely the confidently-wrong shape this
        # module exists to refuse.
        return {"state": UNMEASURABLE, "behind_by": None,
                "reason": f"{reason} (and {ref} could not be measured)",
                "conflicts": conflicts, "root": root or _BASE, "ref": ref}

    if count == 0:
        # Refusing, but nothing is waiting. Not a freeze — a checkout on another
        # branch, say, which is somebody working deliberately.
        return {"state": CURRENT, "behind_by": 0, "reason": reason,
                "conflicts": conflicts, "root": root or _BASE, "ref": ref}

    return {"state": BLOCKED, "behind_by": count, "reason": reason,
            "conflicts": conflicts, "root": root or _BASE, "ref": ref}


def render(report: Dict[str, Any]) -> str:
    state = report["state"]
    behind = report.get("behind_by")
    behind_text = "?" if behind is None else str(behind)
    out = [f"Deployment freshness — {state}",
           f"  {report['root']} vs {report['ref']}: {behind_text} commit(s) behind"]
    if report.get("reason"):
        out.append(f"  guard says: {report['reason']}")
    for path in report.get("conflicts") or []:
        out.append(f"    blocked by locally-modified: {path}")
    if state == BLOCKED:
        out.append("")
        out.append("  This deployment has STOPPED updating. Merged fixes are on the")
        out.append("  branch and absent from the running services.")
        out.append("  Do NOT force-pull: the guard is right that local work would be")
        out.append("  lost. Commit or revert what dirtied the file(s) above.")
    elif state == UNMEASURABLE:
        out.append("  (unmeasurable is NOT current — nobody could check)")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 when the deployment is blocked")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = freshness(root=args.root, ref=args.ref)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))

    if args.gate and report["state"] == BLOCKED:
        return 1
    return 2 if report["state"] == UNMEASURABLE else 0


if __name__ == "__main__":
    raise SystemExit(main())
