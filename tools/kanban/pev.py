#!/usr/bin/env python3
# CUI // SP-CTI
"""Plan-Execute-Verify (PEV) step verification for the kanban runner (agx-verify-03).

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan). Pattern only; no upstream code vendored.

ICDEV's most expensive recurring failure is a task marked ``done`` that shipped
nothing (the ACE "done LIED" cards, the done-artifact audit, the manual-gate
integrity incident). PR #180 hardened the TERMINAL done-gate (done now requires a
merge to origin/main). PEV is the ADDITIVE inside-the-loop half: after a step
executes, verify that step's CLAIMED EFFECT with a step-type-appropriate check
before proceeding, so a step that produced nothing is caught immediately — not
after N further steps built on a phantom.

Design decisions (deliberately narrow, to avoid rebuilding what exists):
  * Reuse, don't rebuild. File/commit/route checks already exist in the runner
    and ``tools/kanban_verify.py``; PEV adds the three-valued VERDICT + the
    Python-composed continue/replan/halt POLICY on top, and records to the
    EXISTING append-only ``kanban_verifications`` table (no new table).
  * Deterministic-picker: each step yields an ENUM verdict
    ({verified, unverified, contradicted}); pure Python (:func:`compose_step_policy`)
    composes the next action. No LLM emits the decision.
  * FAIL-CLOSED. "unverified" (could not gather evidence) never counts as
    "verified" — a verification step that silently passes is worse than none.
    This is asserted directly in the tests ("verify the verifier").
  * cwd-safe. Path/route checks take an explicit ``base_dir`` so a worktree-local
    artifact is not judged against the MAIN checkout (the documented route-verifier
    trap). Never rely on os.getcwd().

The terminal done-gate is NOT weakened: :func:`record_completion_pev` records the
three-valued verdict alongside the runner's boolean result and never changes it.

LLM-agnostic: PEV is pure deterministic checking — it performs no inference and
imports no provider. (Optional LLM-assisted replanning is the caller's job and
would route through LLMRouter; PEV only composes the policy.)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

# ── Verdict + policy vocabularies (deterministic-picker) ────────────────────
VERIFIED = "verified"
UNVERIFIED = "unverified"
CONTRADICTED = "contradicted"
_VERDICTS = (VERIFIED, UNVERIFIED, CONTRADICTED)

CONTINUE = "continue"
REPLAN = "replan"
HALT = "halt"

VOCABULARY_VERSION = "pev-1.0"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_verdict(token: Any) -> str:
    """Coerce a verdict token to the vocabulary; unknown -> unverified (fail-closed)."""
    t = str(token or "").strip().lower()
    return t if t in _VERDICTS else UNVERIFIED


def compose_step_policy(
    verdict: str,
    *,
    replans_used: int = 0,
    max_replans: int = 2,
) -> str:
    """Compose the next action from a step verdict. Pure, documented policy.

    Policy:
      * ``verified``     -> ``continue`` (evidence confirms the claimed effect).
      * ``contradicted`` -> ``replan`` while the bounded replan budget remains,
                            else ``halt`` (evidence disproves the claimed effect —
                            e.g. a claimed file is absent, a claimed test failed).
      * ``unverified``   -> ``halt`` (no evidence either way). FAIL-CLOSED: never
                            build further steps on an unconfirmed effect.

    ``halt`` on an exhausted replan budget prevents the unbounded self-debug
    loops the recovery guard (kanban-recovery-guard) exists to stop.
    """
    v = normalize_verdict(verdict)
    if v == VERIFIED:
        return CONTINUE
    if v == CONTRADICTED:
        return REPLAN if replans_used < max_replans else HALT
    return HALT  # unverified


# ── Step-type-appropriate checks (reuse existing tools; cwd-safe) ────────────
def verify_file_exists(target: str, *, base_dir: Optional[str] = None) -> Tuple[str, str]:
    """A claimed file must exist on the working tree. Absent -> contradicted.

    ``base_dir`` is the worktree the step ran in (defaults to the repo root);
    resolving against it — not os.getcwd() — is what dodges the documented
    "route verifier reads the MAIN checkout" trap.
    """
    if not target:
        return UNVERIFIED, "no path supplied"
    root = Path(base_dir) if base_dir else _BASE_DIR
    path = (root / target) if not Path(target).is_absolute() else Path(target)
    if path.exists():
        return VERIFIED, f"exists: {path}"
    return CONTRADICTED, f"claimed file absent: {path}"


def verify_test_passed(
    target: str,
    *,
    base_dir: Optional[str] = None,
    timeout: int = 300,
    runner: Optional[Callable[[List[str], str], Tuple[int, str]]] = None,
) -> Tuple[str, str]:
    """Run pytest on ``target`` and confirm it ACTUALLY RAN and passed.

    The load-bearing anti-silent-pass rule: a run that collected ZERO tests is
    ``unverified``, NOT ``verified`` — "no tests ran" must never read as success.
    A non-zero exit with failures is ``contradicted``. ``runner`` is injectable
    ``(cmd, cwd) -> (returncode, output)`` so tests need not spawn real pytest.
    """
    if not target:
        return UNVERIFIED, "no test target supplied"
    root = str(base_dir) if base_dir else str(_BASE_DIR)
    cmd = [sys.executable, "-m", "pytest", target, "-q", "--no-header"]
    if runner is not None:
        try:
            code, out = runner(cmd, root)
        except Exception as exc:  # noqa: BLE001 — a broken runner is not a pass
            return UNVERIFIED, f"test runner failed: {exc}"
    else:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                cwd=root, stdin=subprocess.DEVNULL,
                encoding="utf-8", errors="replace",
            )
            code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            return UNVERIFIED, f"pytest timed out after {timeout}s"
        except Exception as exc:  # noqa: BLE001 — a broken runner is not a pass
            return UNVERIFIED, f"pytest could not run: {exc}"
    return _classify_pytest(code, out)


_ZERO_COLLECTED_MARKERS = ("no tests ran", "collected 0 items", "no tests were run")


def _classify_pytest(code: int, output: str) -> Tuple[str, str]:
    low = (output or "").lower()
    if any(m in low for m in _ZERO_COLLECTED_MARKERS):
        # Ran but collected nothing — cannot claim the effect was verified.
        return UNVERIFIED, "pytest collected 0 tests (no evidence)"
    if code == 0 and ("passed" in low or "ok" in low):
        return VERIFIED, "pytest passed"
    if code == 0:
        # Exit 0 but no 'passed' token and not zero-collected — treat as no evidence.
        return UNVERIFIED, "pytest exit 0 but no pass signal"
    return CONTRADICTED, f"pytest failed (exit {code})"


def verify_route_responds(
    target: str,
    *,
    base_url: Optional[str] = None,
    prober: Optional[Callable[[str], int]] = None,
) -> Tuple[str, str]:
    """Probe a route and confirm it responds < 400. No network by default.

    A route probe requires a live server, which is not guaranteed in CI/air-gap,
    so without an injected ``prober`` this returns ``unverified`` (no evidence) —
    it never fabricates a pass. ``prober`` is ``(url) -> status_code``.
    """
    if not target:
        return UNVERIFIED, "no route supplied"
    if prober is None:
        return UNVERIFIED, f"no prober available for {target} (server not probed)"
    url = target if target.startswith("http") else f"{(base_url or '').rstrip('/')}{target}"
    try:
        status = int(prober(url))
    except Exception as exc:  # noqa: BLE001
        return CONTRADICTED, f"route probe error: {exc}"
    if 200 <= status < 400:
        return VERIFIED, f"route {url} -> {status}"
    return CONTRADICTED, f"route {url} -> {status}"


def verify_migration_applied(
    target: str,
    *,
    base_dir: Optional[str] = None,
) -> Tuple[str, str]:
    """Confirm a migration directory/file exists in the tree. Absent -> contradicted.

    Existence-on-branch is the cwd-safe, air-gap-safe check; whether it has been
    APPLIED to a live DB is environment-specific and left to the DB layer, so a
    present-but-unapplied migration is ``verified`` for the "did the step produce
    the migration?" question this hook answers.
    """
    if not target:
        return UNVERIFIED, "no migration id supplied"
    root = Path(base_dir) if base_dir else _BASE_DIR
    mig_root = root / "tools" / "db" / "migrations"
    if (mig_root / target).exists() or any(mig_root.glob(f"{target}*")):
        return VERIFIED, f"migration present: {target}"
    return CONTRADICTED, f"claimed migration absent: {target}"


_STEP_CHECKS: Dict[str, Callable[..., Tuple[str, str]]] = {
    "file": verify_file_exists,
    "test": verify_test_passed,
    "route": verify_route_responds,
    "migration": verify_migration_applied,
}


def verify_step(step: Dict[str, Any], *, base_dir: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Verify one plan step's claimed effect.

    ``step`` = ``{"name": str, "type": file|test|route|migration, "target": str}``.
    An unknown step type is ``unverified`` (fail-closed), never ``verified``.
    Returns ``{name, type, target, verdict, reason, vocabulary_version}``.
    """
    step_type = str(step.get("type", "")).strip().lower()
    target = step.get("target", "")
    name = step.get("name") or f"{step_type}:{target}"
    check = _STEP_CHECKS.get(step_type)
    if check is None:
        verdict, reason = UNVERIFIED, f"unknown step type {step_type!r}"
    else:
        call_kwargs = {"base_dir": base_dir} if step_type != "route" else {}
        # route uses base_url, not base_dir
        if step_type == "route":
            call_kwargs = {"base_url": kwargs.get("base_url"), "prober": kwargs.get("prober")}
        elif step_type == "test":
            call_kwargs["runner"] = kwargs.get("runner")
        verdict, reason = check(target, **call_kwargs)
    return {
        "name": name,
        "type": step_type,
        "target": target,
        "verdict": normalize_verdict(verdict),
        "reason": reason,
        "vocabulary_version": VOCABULARY_VERSION,
    }


def run_plan(
    task_id: str,
    steps: List[Dict[str, Any]],
    *,
    base_dir: Optional[str] = None,
    max_replans: int = 2,
    record: bool = True,
    conn: Any = None,
    **kwargs,
) -> Dict[str, Any]:
    """Execute the PEV loop over an ordered list of already-executed steps.

    Verifies each step; composes the policy; halts on the first ``halt`` and
    counts ``replan`` outcomes against ``max_replans``. Returns a summary with
    the per-step trace and the final action, and (by default) appends each step
    verdict to the ``kanban_verifications`` trail.
    """
    trace: List[Dict[str, Any]] = []
    replans_used = 0
    final_action = CONTINUE
    for step in steps:
        result = verify_step(step, base_dir=base_dir, **kwargs)
        action = compose_step_policy(
            result["verdict"], replans_used=replans_used, max_replans=max_replans
        )
        result["action"] = action
        trace.append(result)
        if record:
            record_step_verification(task_id, result, conn=conn)
        if action == REPLAN:
            replans_used += 1
            final_action = REPLAN
            break
        if action == HALT:
            final_action = HALT
            break
    else:
        final_action = CONTINUE
    passed = final_action == CONTINUE and all(s["verdict"] == VERIFIED for s in trace)
    return {
        "task_id": task_id,
        "passed": passed,
        "final_action": final_action,
        "replans_used": replans_used,
        "steps": trace,
        "vocabulary_version": VOCABULARY_VERSION,
    }


# ── Append-only trail (reuse existing kanban_verifications table) ────────────
_VERDICT_TO_RESULT = {VERIFIED: "passed", CONTRADICTED: "failed", UNVERIFIED: "unverified"}


def record_step_verification(
    task_id: str,
    step_result: Dict[str, Any],
    *,
    conn: Any = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Append one step verdict to the append-only ``kanban_verifications`` table.

    Reuses the existing table (no new table): the three-valued verdict + step
    detail ride in ``result``/``reason``/``specific_checks``. Never raises —
    a trail write must not break the runner.
    """
    row = {
        "id": f"pev-{uuid.uuid4().hex[:12]}",
        "task_id": task_id,
        "verified_at": _utcnow(),
        "result": _VERDICT_TO_RESULT.get(step_result.get("verdict"), "unverified"),
        "reason": str(step_result.get("reason", ""))[:1000],
        "specific_checks": json.dumps({
            "pev": True,
            "step": step_result.get("name"),
            "type": step_result.get("type"),
            "verdict": step_result.get("verdict"),
            "action": step_result.get("action"),
            "vocabulary_version": VOCABULARY_VERSION,
        }),
    }
    if dry_run:
        return {"dry_run": True, "row": row}
    try:
        if conn is None:
            from tools.db.storage import get_connection
            conn = get_connection()
        conn.execute(
            "INSERT INTO kanban_verifications "
            "(id, task_id, verified_at, result, reason, specific_checks) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (row["id"], row["task_id"], row["verified_at"],
             row["result"], row["reason"], row["specific_checks"]),
        )
        conn.commit()
        return {"written": True, "row": row}
    except Exception as exc:  # noqa: BLE001 — trail write is best-effort
        return {"written": False, "error": str(exc), "row": row}


def record_completion_pev(
    task_id: str,
    *,
    verified: bool,
    reason: str,
    metrics: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Opt-in hook the runner calls after its terminal gate. Additive only.

    No-op unless ``ICDEV_KANBAN_PEV`` is truthy, so default runner behavior is
    unchanged (safe for CI). Records the runner's boolean result as a three-valued
    PEV verdict alongside the existing log — it NEVER changes the runner's
    ``verified`` decision, so the terminal done-gate is unweakened.
    """
    import os
    if str(os.environ.get("ICDEV_KANBAN_PEV", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    # A phantom completion (claimed done, nothing on disk) is a CONTRADICTION,
    # not merely unverified — mirror the runner's PHANTOM signal.
    if verified:
        verdict = VERIFIED
    elif "PHANTOM" in (reason or "").upper():
        verdict = CONTRADICTED
    else:
        verdict = UNVERIFIED
    return record_step_verification(
        task_id,
        {"name": "task_completion", "type": "completion", "verdict": verdict,
         "reason": reason, "action": compose_step_policy(verdict)},
    )


# ── CLI (headless / air-gap) ─────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="PEV step verification (agx-verify-03)")
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--steps", help="JSON list of {name,type,target} steps")
    ap.add_argument("--base-dir", default=None)
    ap.add_argument("--max-replans", type=int, default=2)
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    steps = json.loads(args.steps) if args.steps else []
    result = run_plan(
        args.task_id, steps, base_dir=args.base_dir,
        max_replans=args.max_replans, record=not args.no_record,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[pev] {args.task_id}: {result['final_action']} "
              f"(passed={result['passed']}, replans={result['replans_used']})")
        for s in result["steps"]:
            print(f"  - {s['name']}: {s['verdict']} -> {s['action']} ({s['reason']})")


if __name__ == "__main__":
    main()
