# CUI // SP-CTI
"""Failure triage — automatic review and (optional) auto-fix of kanban
task failures.

Bridges the gap between ``self_debug.py`` (which only fires after a task
has looped N times with the same signature) and manual human review.

Pipeline per failure
--------------------
1. Query kanban_tasks for recent failures (``last_failure_reason`` set,
   ``updated_at`` within the window, status in {backlog, failed}).
2. Skip if already triaged this signature (file marker in
   ``.tmp/kanban/triaged/``).
3. Gather evidence via ``self_debug.snapshot``.
4. Diagnose via LLM — routing ``failure_triage_diagnose`` (Claude primary,
   Ollama fallback). Thinking / reasoning step.
5. If ``recommendation == 'patch'`` AND ``confidence >= APPLY_CONFIDENCE``
   AND ``task.task_type`` in whitelist AND signature/files not on
   deny-list AND rate budget available AND ``ICDEV_AUTOFIX_ENABLED=true``:
   generate a patch via LLM routing ``failure_triage_patch`` (Ollama
   primary, Claude fallback). Building / code-gen step.
   Apply the patch in an isolated worktree, run verification, commit, and
   merge back to the task branch. On any verification failure the worktree
   is rolled back and the task falls through to the human-review path.
6. Otherwise: create an Oracle suggested card (the existing
   ``self_debug._create_diagnostic_card`` flow), leaving the decision to
   a human. If the task blocks dependents, the card is escalated —
   critical priority + an explicit title marker + an ``escalation``
   event. The confidence bar is NOT lowered for blockers; see
   ``should_auto_apply`` for that decision and its reasoning.
7. Record a triage marker so the same signature isn't reprocessed.

Defaults are intentionally conservative:
* ``--dry-run`` is the default CLI behavior.
* ``ICDEV_AUTOFIX_ENABLED`` defaults to ``false`` — the auto-apply path
  is opt-in per operator.
* Task-type whitelist, file deny-list, rate limit, and kill switch all
  have to be green before a patch is applied.

Kill switch: set ``ICDEV_AUTOFIX_ENABLED=false`` (or leave it unset) to
force suggested-card-only behavior.

Deny lists — the membership criteria
------------------------------------
Three separate lists gate auto-apply. They exist for different reasons and
are matched against different fields; do not merge them.

``NON_CODE_FAILURE_TOKENS`` — *"there is no code a patch could fix."*
    Matched against ``last_failure_reason`` ONLY, because that column is
    written by the scheduler, not by a human or an LLM. A member belongs
    here when the string identifies a failure class whose cause lives in
    the runtime budget, the scheduler lifecycle, or the executor
    environment rather than in the repository — or is not a failure at
    all, just bookkeeping text the scheduler parked in the column.
    Diagnosing one of these burns an LLM generation to produce a patch
    for a setting.
    Deliberately NOT in ``DENY_SIGNATURE_TOKENS``: that list also matches
    ``description``, and a task legitimately described as "add a timeout
    to the socket call" would then be permanently ineligible for autofix
    even when it later fails with a real ``AttributeError``.

``DENY_SIGNATURE_TOKENS`` — *"this change is too dangerous to make
    unattended."* Matched against the whole blob (failure reason +
    description + LLM root cause + patch hint) precisely because a
    mention anywhere is enough to warrant a human. Membership criterion:
    the token names an operation that is irreversible, destroys history,
    or disables a control.

``DENY_FILE_PREFIXES`` — *"the verification gates are blind to damage
    here."* A path belongs on this list when BOTH hold:
    (a) an incorrect edit would still pass ``_run_independent_gates``
        (the originally-failing test, the CI ruff gate, and the coherence
        diff) — i.e. the harness cannot tell right from wrong for this
        file; AND
    (b) being wrong is not merely a bug: the file either weakens a
        control that constrains the autofixer itself, or produces an
        outward-facing / irreversible artifact that a third party may
        already hold by the time the error is noticed.
    Ordinary application code fails (b) — a wrong edit there is caught by
    the tests, or is a normal bug that a normal fix reverses. Do not add
    a prefix just because the module feels important.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.workflow.git_utils import default_branch

logger = get_logger(__name__)

# Dedicated structured-event logger (WS3.2 / arc-obs-02). Uses a stable
# component name so recovery events land in .logs/failure_triage.ndjson
# regardless of whether this module is imported as ``tools.workflow.*`` or
# ``icdev.tools.workflow.*``. Once the log_ingest/direct-write bridge lands,
# these NDJSON lines flow into ``centralized_logs`` and become visible on
# /logs and queryable via IQE. The .tmp marker write (dedup) is unchanged —
# it stays the source of truth for "already triaged this signature".
_events = get_logger("failure_triage")

# The discrete recovery-pipeline event types emitted via ``_emit_event``.
EVENT_DIAGNOSIS_MADE = "diagnosis_made"
EVENT_GATE_DECISION = "gate_decision"
EVENT_PATCH_GENERATED = "patch_generated"
EVENT_VERIFY_RESULT = "verify_result"
EVENT_APPLY_OUTCOME = "apply_outcome"
EVENT_ESCALATION = "escalation"


def _emit_event(
    event_type: str,
    *,
    task_id: Optional[str],
    signature: Optional[str],
    level: str = "INFO",
    **fields: Any,
) -> None:
    """Emit one structured NDJSON recovery event through ``get_logger``.

    Every event carries ``event_type``, ``task_id`` and ``signature``; the
    common recovery fields (``confidence``, ``recommendation``, ``outcome``)
    are passed through ``fields`` when relevant. ``None`` values are dropped
    to keep the line compact.

    Best-effort: logging must never break the triage pipeline, so any error
    here is swallowed.
    """
    payload: Dict[str, Any] = {
        "event_type": event_type,
        "task_id": task_id,
        "signature": signature,
    }
    payload.update({k: v for k, v in fields.items() if v is not None})
    try:
        lvl = getattr(logging, level.upper(), logging.INFO)
        _events.log(lvl, event_type, extra={"extra": payload})
    except Exception:  # pragma: no cover - logging must not raise into triage
        pass


BASE_DIR = Path(__file__).resolve().parent.parent.parent
TRIAGED_DIR = BASE_DIR / ".tmp" / "kanban" / "triaged"
RATE_FILE = BASE_DIR / ".tmp" / "kanban" / "triage_rate.json"

# Tuning knobs
APPLY_CONFIDENCE = 0.85            # min LLM confidence to auto-apply
SUGGEST_CONFIDENCE = 0.50          # min confidence to bother creating a card
MAX_APPLIES_PER_HOUR = 5           # matches CLAUDE.md self-healing cap
DEFAULT_WINDOW_HOURS = 1           # how far back to scan

# Task-type whitelist for auto-apply — matches feedback_kanban_vv_policy.md
# "fail-open for build/research/test/chore" plus 'fix'. Counts from the
# live kanban_tasks table (2026-04-17): chore=742, build=307, fix=200,
# test=89, research=49. 'deploy' (12) is intentionally excluded — higher
# blast radius, stays on the suggested-card path.
AUTO_APPLY_TASK_TYPES = {"build", "chore", "fix", "research", "test"}

# Failure classes with no code defect behind them. See the "Deny lists"
# section of the module docstring for the membership criterion. Matched
# against ``last_failure_reason`` ONLY (lower-cased substring).
#
# MEASURED 2026-08-08: re-queueing nine sbx-* tasks refreshed their
# ``updated_at`` while they still carried ``last_failure_reason`` from the
# build attempts whose PRs had just been closed. find_recent_failures selects
# on exactly (reason IS NOT NULL AND updated_at > cutoff AND status IN
# (backlog, failed, scheduled, needs_decomposition)), so five tasks entered
# the autofix queue with nothing wrong with them: four carried
# "TIMEOUT after ~3430s (max 3386s)" — and ``max_runtime_seconds`` was NULL on
# every one of them, so the cap was the GLOBAL dispatch budget, not a
# per-task defect — and the fifth carried
# "Task-specific checks passed or not applicable", which is not a failure at
# all. ICDEV_AUTOFIX_ENABLED was true; only the absence of
# ICDEV_AUTOFIX_AUTOMERGE kept the resulting patches off main.
#
# Each entry below is a verbatim prefix of a string some writer puts in the
# column; the writer is named so the pairing stays checkable.
NON_CODE_FAILURE_TOKENS = [
    # --- runtime budget / lifecycle: the cause is a setting or a process,
    #     not a line of code (kanban reflex scheduler) ---
    "timeout after",              # reaper: "TIMEOUT after {n}s (max {budget}s)"
    "task exceeded dispatch budget",
    "stale-reaper:",              # _reap_stale_in_progress
    "zombie reclaim:",            # _reclaim_zombies
    "startup-recovery:",          # scheduler restarted mid-run
    "circuit breaker: failure_count",   # retry cap reached, not a new defect
    # --- executor environment: nothing in the repo is broken ---
    "no executor available",
    # --- not a failure at all: bookkeeping text parked in the column ---
    "task-specific checks passed",      # _verify_task_specific non-failure default
    "decay-promoted:",            # _promote_decayed_suggested
    "auto-revive ",               # _revive_quarantined_suggested
    "dep-chain-unblock:",         # _revive_dep_chain_blocked
    "cascade: parent ",           # _move_task descendant rollback
    "auto-closed: parent ",       # _close_orphaned_rca_children
    "auto-decomposed into",       # phase-gate decomposition marker
    "unclassified (no failure clause)",  # _split_failure_narrative fallback
]

# Signatures and suspect-file substrings that force human review.
# Anything matching stays on the suggested-card path regardless of
# confidence.
DENY_SIGNATURE_TOKENS = [
    "migration", "schema change", "drop table", "alter table",
    "security gate", "auth bypass", "destructive",
    "force-push", "force push", "--force", "--no-verify",
    "delete branch", "reset --hard",
]
DENY_FILE_PREFIXES = [
    "tools/db/migrations/",
    "tools/security/",
    ".claude/hooks/",
    "args/security_gates.yaml",
    "args/llm_config.yaml",  # config edits are blast-radius; human-review
    # tools/compliance/ — decided 2026-08-08 (kax-recover-01), ADDED.
    # Criterion (a): a wrong control id, a dropped component, a mis-scored
    # assessment, or a missing CUI banner is textually valid Python. It
    # compiles, it lints, the tests assert artifact STRUCTURE rather than
    # content truth, and coherence does not model control semantics — so
    # _run_independent_gates cannot tell a correct generator from a subtly
    # wrong one.
    # Criterion (b): the output is outward-facing evidence, and correcting
    # it is not an edit. sbom_generator.py is the single writer for every
    # SBOM ICDEV emits, and a corrected SBOM ships as a SUCCESSOR row via
    # sbom_revision.apply_correction — because a recipient may already hold
    # the document the wrong one described. The same holds for the rest of
    # the package: all 95 modules are assessors, artifact generators
    # (SSP/POA&M/OSCAL/SLSA/model+system cards), evidence collectors, or
    # classification markers. There is no internal-only plumbing in there
    # that would make a narrower prefix meaningfully different.
    # Note this is a DENY, not a ban: these tasks still get a diagnosis and
    # a suggested card with the patch attached — a human presses apply.
    "tools/compliance/",
]

# Env kill switch
AUTOFIX_ENV = "ICDEV_AUTOFIX_ENABLED"


# ---------------------------------------------------------------------------
# DB query
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def find_recent_failures(window_hours: int = DEFAULT_WINDOW_HOURS) -> List[Dict[str, Any]]:
    """Return kanban tasks that failed within ``window_hours``.

    Matches either:
    * ``last_failure_reason`` set and ``status`` in (backlog, failed,
      needs_decomposition), or
    * ``failure_count > 0`` for tasks returned to backlog on cooldown.

    Ordering priority:
    1. Tasks that have downstream dependents blocked on them (chain_blockers)
       are returned first — their failure is amplified because it stalls
       the whole dependent chain.
    2. Then by kanban priority (critical > high > medium > low).
    3. Finally most-recently-updated first.

    Ordering is the ONLY thing blocker status changes. It does not affect the
    auto-apply confidence bar — see ``should_auto_apply`` for why cost and
    correctness are kept separate.
    """
    try:
        from tools.db.storage import get_connection, sql_placeholder
    except Exception as exc:
        logger.warning("failure_triage: storage import failed: %s", exc)
        return []

    rows: List[Dict[str, Any]] = []
    cutoff = (_utcnow() - timedelta(hours=window_hours)).isoformat()
    try:
        with get_connection() as conn:
            ph = sql_placeholder(conn)
            # blocked_dependents_count: number of tasks blocked by this one.
            # A task is blocked if its depends_on_task_id points here AND its
            # own status is not done/decomposed.
            sql = (
                "SELECT kt.id, kt.title, kt.description, kt.task_type, "
                "       kt.priority, kt.status, "
                "       kt.failure_count, kt.last_failure_reason, kt.updated_at, "
                "       (SELECT COUNT(*) FROM kanban_tasks dep "
                "         WHERE dep.depends_on_task_id = kt.id "
                "           AND dep.status NOT IN ('done','decomposed')) "
                "       AS blocked_dependents_count "
                "FROM kanban_tasks kt "
                f"WHERE kt.last_failure_reason IS NOT NULL "
                f"  AND kt.updated_at > {ph} "
                f"  AND kt.status IN ('backlog','failed','scheduled','needs_decomposition') "
                f"ORDER BY "
                f"  CASE WHEN (SELECT COUNT(*) FROM kanban_tasks dep "
                f"              WHERE dep.depends_on_task_id = kt.id "
                f"                AND dep.status NOT IN ('done','decomposed')) > 0 "
                f"       THEN 0 ELSE 1 END, "
                f"  CASE kt.priority "
                f"    WHEN 'critical' THEN 0 "
                f"    WHEN 'high'     THEN 1 "
                f"    WHEN 'medium'   THEN 2 "
                f"    ELSE 3 END, "
                f"  kt.updated_at DESC "
                f"LIMIT 50"
            )
            cur = conn.execute(sql, (cutoff,))
            for r in cur.fetchall():
                rows.append(dict(r))
    except Exception as exc:
        logger.warning("failure_triage: DB query failed: %s", exc)
        return []
    return rows


# ---------------------------------------------------------------------------
# Dedup — don't re-triage the same (task_id, signature)
# ---------------------------------------------------------------------------

def _marker_path(task_id: str, sig: str) -> Path:
    return TRIAGED_DIR / f"{task_id}__{sig}.marker"


def already_triaged(task_id: str, sig: str) -> bool:
    return _marker_path(task_id, sig).exists()


def mark_triaged(task_id: str, sig: str, outcome: Dict[str, Any]) -> None:
    try:
        TRIAGED_DIR.mkdir(parents=True, exist_ok=True)
        _marker_path(task_id, sig).write_text(
            json.dumps(
                {"task_id": task_id, "sig": sig, "ts": _utcnow().isoformat(), "outcome": outcome},
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("failure_triage: mark_triaged failed for %s: %s", task_id, exc)


# ---------------------------------------------------------------------------
# Rate limit — global cap on auto-applies per rolling hour
# ---------------------------------------------------------------------------

def _load_rate_log() -> List[float]:
    if not RATE_FILE.exists():
        return []
    try:
        data = json.loads(RATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [float(x) for x in data]
    except Exception:
        pass
    return []


def _save_rate_log(ts_list: List[float]) -> None:
    try:
        RATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RATE_FILE.write_text(json.dumps(ts_list), encoding="utf-8")
    except Exception as exc:
        logger.warning("failure_triage: rate-log write failed: %s", exc)


def within_rate_budget() -> Tuple[bool, int]:
    """Return (ok, applies_in_last_hour)."""
    now = time.time()
    window_start = now - 3600
    log = [t for t in _load_rate_log() if t >= window_start]
    _save_rate_log(log)
    return (len(log) < MAX_APPLIES_PER_HOUR, len(log))


def record_apply(ts: Optional[float] = None) -> None:
    now = ts or time.time()
    window_start = now - 3600
    log = [t for t in _load_rate_log() if t >= window_start]
    log.append(now)
    _save_rate_log(log)


# ---------------------------------------------------------------------------
# Auto-apply gates
# ---------------------------------------------------------------------------

def autofix_enabled() -> bool:
    return os.environ.get(AUTOFIX_ENV, "false").strip().lower() in ("1", "true", "yes", "on")


def _deny_hit(diag: Dict[str, Any], task: Dict[str, Any]) -> Optional[str]:
    """Return the reason string if any deny rule matches, else None."""
    # Non-code failure classes are checked FIRST and against the scheduler-
    # written column alone. A TIMEOUT is a budget symptom: max_runtime_seconds
    # was NULL on every task that produced one, so the cap is a global
    # setting and there is no code a patch could fix. Same for the
    # bookkeeping strings the scheduler parks in this column on re-queue —
    # they are not failures at all. Scoped to last_failure_reason so a task
    # merely *described* as "add a timeout to X" stays eligible.
    failure_reason = (task.get("last_failure_reason") or "").lower()
    for tok in NON_CODE_FAILURE_TOKENS:
        if tok in failure_reason:
            return f"non-code failure class matched: {tok!r}"

    reason_blob = (
        (task.get("last_failure_reason") or "")
        + " " + (task.get("description") or "")
        + " " + (diag.get("root_cause") or "")
        + " " + (diag.get("patch_hint") or "")
    ).lower()
    for tok in DENY_SIGNATURE_TOKENS:
        if tok in reason_blob:
            return f"deny-token matched: {tok!r}"
    suspects = diag.get("suspect_files") or []
    for sf in suspects:
        # Normalize to forward-slash paths
        norm = str(sf).replace("\\", "/")
        for prefix in DENY_FILE_PREFIXES:
            if prefix in norm:
                return f"deny-path matched: {prefix!r} in {sf!r}"
    return None


def should_auto_apply(task: Dict[str, Any], diag: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (allow, reason). ``reason`` is human-readable even on allow.

    Confidence bar: ``APPLY_CONFIDENCE`` (0.85), for every task. A chain
    blocker and a leaf task face the identical bar.

    Why there is no chain-blocker relaxation (decided 2026-08-08, kax-recover-03)
    ---------------------------------------------------------------------------
    This function used to relax the bar to 0.70 when
    ``blocked_dependents_count > 0``, on the reasoning that "the cost of
    inaction is amplified". That relaxation is REMOVED. It confused two
    different quantities:

    * being a blocker says the failure is EXPENSIVE — it stalls a chain;
    * ``confidence`` is a claim about whether the DIAGNOSIS IS CORRECT.

    Amplified cost is an argument for getting a human onto the failure
    sooner. It is not evidence that the LLM's root-cause analysis is more
    likely to be right, so it cannot buy a discount on correctness. Trading
    certainty for speed on exactly the failures with the largest blast
    radius inverts the intent: a wrong patch on a chain blocker is wrong for
    every dependent too.

    The relaxation also inverted under a stale flag, which is how it was
    found. MEASURED 2026-08-08: re-queueing nine ``sbx`` tasks chained with
    ``depends_on_task_id`` made each of them a blocker while they still
    carried ``last_failure_reason`` from the build attempts whose PRs had
    just been closed. The same chaining that raised their queue priority
    also LOWERED the bar for patching them — for tasks with nothing wrong.
    Four showed ``blocked_dependents_count=1``. ``ICDEV_AUTOFIX_ENABLED``
    was true; only the absence of ``ICDEV_AUTOFIX_AUTOMERGE`` kept the
    patches off main. See PR #1379.

    kax-recover-02 landed the clean re-queue path
    (``tools/kanban/requeue.py``) that clears ``last_failure_reason``, so
    that specific trigger is closed. It is not a reason to keep the
    relaxation: ``requeue.py`` closes the supported path, not a hand-written
    ``UPDATE``, and the relaxation was unsound on a genuinely fresh failure
    too.

    What replaces it: escalation at the SAME bar. A chain blocker whose
    diagnosis does not clear 0.85 gets a critical, explicitly-marked
    suggested card plus an ``escalation`` structured event — see
    ``chain_blocker_escalation``. Blocker status still sorts a task to the
    front of ``find_recent_failures``; it changes WHEN a human looks, never
    HOW SURE the machine has to be before it writes code.
    """
    if not autofix_enabled():
        return (False, f"{AUTOFIX_ENV} is not set to true")

    rec = (diag.get("recommendation") or "").lower()
    if rec != "patch":
        return (False, f"recommendation is {rec!r} (not 'patch')")

    conf = float(diag.get("confidence") or 0.0)
    if conf < APPLY_CONFIDENCE:
        return (False, f"confidence {conf:.2f} < threshold {APPLY_CONFIDENCE}")

    ttype = (task.get("task_type") or "").lower()
    if ttype not in AUTO_APPLY_TASK_TYPES:
        return (False, f"task_type {ttype!r} not in whitelist {sorted(AUTO_APPLY_TASK_TYPES)}")

    deny = _deny_hit(diag, task)
    if deny:
        return (False, deny)

    ok, count = within_rate_budget()
    if not ok:
        return (False, f"rate limit hit ({count}/{MAX_APPLIES_PER_HOUR} in last hour)")

    return (True, f"all gates green; rate {count}/{MAX_APPLIES_PER_HOUR}")


def chain_blocker_escalation(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return escalation metadata for a chain blocker, else ``None``.

    The replacement for the removed confidence relaxation (see
    ``should_auto_apply``). Cost is spent on reaching a human sooner rather
    than on lowering the correctness bar: the suggested card is raised to
    ``critical`` and its title carries an explicit marker, so it sorts above
    the ordinary triage backlog on the board.
    """
    blocked = int(task.get("blocked_dependents_count") or 0)
    if blocked <= 0:
        return None
    return {
        "blocked_dependents_count": blocked,
        "priority": "critical",
        "title_marker": f"[CHAIN-BLOCKER: {blocked} task(s) stalled] ",
    }


# ---------------------------------------------------------------------------
# Diagnose wrapper — reuses self_debug.snapshot + LLM routing 'failure_triage_diagnose'
# ---------------------------------------------------------------------------

def diagnose_task(task: Dict[str, Any], chain_mode: str = "") -> Dict[str, Any]:
    """Run thinking-tier LLM diagnosis. Falls back to self_debug heuristic.

    Args:
        task: Kanban task dict.
        chain_mode: Optional chain mode — "cot" for step-by-step reasoning.
    """
    from tools.workflow import self_debug

    reason = task.get("last_failure_reason") or ""
    # Conventional branch path — we don't persist cwd on the row, and the
    # worktree may have been cleaned up. self_debug.snapshot gracefully
    # handles a missing cwd.
    snap = self_debug.snapshot(task["id"], str(BASE_DIR), reason)

    # Try the dedicated route first. Fall back to self_debug.diagnose on any
    # routing failure (it already has its own LLM + heuristic fallback).
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter, LLMUnavailableError
    except Exception as exc:
        logger.info("failure_triage: llm imports failed (%s); using self_debug.diagnose", exc)
        return self_debug.diagnose(snap, chain_mode=chain_mode)

    prompt = (
        "A kanban task has failed verification. Diagnose the STRUCTURAL root "
        "cause from this evidence. Do not repeat the symptom — explain why "
        "the task failed and point at the code that needs fixing.\n\n"
        f"TASK: {task.get('id')} / {task.get('title','')} (task_type={task.get('task_type','')})\n"
        f"DESCRIPTION: {(task.get('description') or '')[:400]}\n\n"
        f"EVIDENCE (JSON):\n{json.dumps(snap, indent=2, default=str)[:4000]}\n\n"
        + self_debug._DIAGNOSIS_SCHEMA_HINT  # reuse exact schema
    )
    try:
        resp = LLMRouter().invoke(
            "failure_triage_diagnose",
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800, temperature=0.2, effort="high",
                skip_injection_scan=True,
                chain_mode=chain_mode,
            ),
        )
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[-1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError("no JSON object in LLM response")
        diag = json.loads(m.group(0))
        diag["_source"] = "llm_failure_triage_diagnose"
        return diag
    except LLMUnavailableError:
        logger.info("failure_triage: LLM unavailable; using self_debug fallback")
        return self_debug.diagnose(snap, chain_mode=chain_mode)
    except Exception as exc:
        logger.warning("failure_triage: diagnose LLM failed (%s); fallback", exc)
        return self_debug.diagnose(snap, chain_mode=chain_mode)


# ---------------------------------------------------------------------------
# Patch generation — opt-in, off by default, bounded scope
# ---------------------------------------------------------------------------

PATCH_SCHEMA_HINT = """Return a JSON object with these keys only:
{
  "files": [
    {
      "path": "tools/...py",
      "old_string": "<exact text from the file — must be unique>",
      "new_string": "<replacement — must differ from old_string>",
      "rationale": "<one sentence — why this edit fixes the diagnosed cause>"
    }
  ],
  "verification_command": "<single shell command to prove the fix works, e.g. 'python -m pytest tests/test_x.py -v'>"
}
No prose outside the JSON. No markdown fences. Prefer the smallest possible edit.
"""


def generate_patch(task: Dict[str, Any], diag: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Ask the building-tier LLM (Ollama primary) for a concrete patch.

    Returns the parsed patch dict, or None on failure.
    """
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter, LLMUnavailableError
    except Exception as exc:
        logger.info("failure_triage: llm imports failed (%s); no patch", exc)
        return None

    suspect_files = diag.get("suspect_files") or []
    file_contents: List[str] = []
    for sf in suspect_files[:3]:  # cap prompt size
        path_only = str(sf).split(":")[0].replace("\\", "/")
        fp = BASE_DIR / path_only
        if fp.exists() and fp.is_file():
            try:
                txt = fp.read_text(encoding="utf-8", errors="replace")[:6000]
                file_contents.append(f"=== {path_only} ===\n{txt}")
            except Exception:
                continue

    prompt = (
        "Generate a minimal patch to fix the diagnosed root cause. Output "
        "JSON only — no prose.\n\n"
        f"TASK: {task.get('id')} / {task.get('title','')}\n"
        f"FAILURE REASON: {(task.get('last_failure_reason') or '')[:600]}\n\n"
        f"DIAGNOSIS:\n{json.dumps(diag, indent=2, default=str)[:1500]}\n\n"
        f"SUSPECT FILE CONTENTS:\n" + "\n\n".join(file_contents) + "\n\n"
        + PATCH_SCHEMA_HINT
    )
    try:
        resp = LLMRouter().invoke(
            "failure_triage_patch",
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200, temperature=0.1, effort="medium",
                skip_injection_scan=True,
            ),
        )
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[-1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError("no JSON object in LLM response")
        patch = json.loads(m.group(0))
        patch["_source"] = "llm_failure_triage_patch"
        return patch
    except LLMUnavailableError:
        logger.info("failure_triage: patch LLM unavailable")
        return None
    except Exception as exc:
        logger.warning("failure_triage: patch generation failed: %s", exc)
        return None


def _sig(reason: str) -> str:
    """Re-export the same signature scheme self_debug uses."""
    from tools.workflow.self_debug import failure_signature
    return failure_signature(reason)


# ---------------------------------------------------------------------------
# Worktree-apply stage — isolated patch application + verification
# ---------------------------------------------------------------------------

# Second opt-in: even when autofix is enabled and verification passes,
# the autofix branch is NOT merged into main unless this is also true.
AUTOMERGE_ENV = "ICDEV_AUTOFIX_AUTOMERGE"

AUTOFIX_WORKTREE_BASE = BASE_DIR / ".tmp" / "autofix"
AUDIT_DIR = BASE_DIR / ".tmp" / "kanban" / "autofix-audit"

# Only these prefixes are allowed in patch.verification_command. LLM output
# is untrusted — anything else is rejected and we fall through to the
# suggested-card path.
_VERIFICATION_CMD_ALLOWLIST = (
    "python -m pytest",
    "python -m py_compile",
    "python -m ruff",
    "python -m bandit",
    "python tools/",
    "python -m tools",
)


def automerge_enabled() -> bool:
    return os.environ.get(AUTOMERGE_ENV, "false").strip().lower() in ("1", "true", "yes", "on")


def _validate_verification_command(cmd: str) -> Tuple[bool, str]:
    c = (cmd or "").strip()
    if not c:
        return (False, "empty verification_command")
    # Reject shell metacharacters that would enable chained commands
    for bad in (";", "&&", "||", "|", ">", "<", "`", "$(", "--no-verify"):
        if bad in c:
            return (False, f"disallowed token {bad!r}")
    for prefix in _VERIFICATION_CMD_ALLOWLIST:
        if c.startswith(prefix):
            return (True, "ok")
    return (False, f"command prefix not allowlisted: {c.split()[0:3]!r}")


def _validate_patch_files(patch: Dict[str, Any], diag: Dict[str, Any]) -> Tuple[bool, str]:
    """Each patch file must: (a) live under BASE_DIR, (b) match a path
    appearing in diag.suspect_files, (c) pass deny-list, (d) exist on disk,
    (e) have a non-empty, unique old_string distinct from new_string."""
    files = patch.get("files") or []
    if not files:
        return (False, "patch has no files")

    suspect_norm = {
        str(s).split(":")[0].replace("\\", "/").strip()
        for s in (diag.get("suspect_files") or [])
    }

    for f in files:
        path = (f.get("path") or "").replace("\\", "/").strip()
        if not path:
            return (False, "patch file missing path")
        # Reject path traversal / absolute paths
        if path.startswith("/") or ".." in Path(path).parts or Path(path).is_absolute():
            return (False, f"unsafe path: {path!r}")
        # Must be an existing regular file inside BASE_DIR
        full = (BASE_DIR / path).resolve()
        try:
            full.relative_to(BASE_DIR.resolve())
        except ValueError:
            return (False, f"path escapes repo root: {path!r}")
        if not full.is_file():
            return (False, f"path does not exist: {path!r}")
        # Must have been named in the diagnosis — refuse to edit files the
        # thinking-tier LLM didn't flag
        if suspect_norm and path not in suspect_norm:
            return (False, f"path {path!r} not in diag.suspect_files {sorted(suspect_norm)}")
        # Deny-list on the apply side too (belt-and-suspenders vs should_auto_apply)
        for prefix in DENY_FILE_PREFIXES:
            if path.startswith(prefix):
                return (False, f"deny-path (apply-side) matched: {prefix!r}")

        old = f.get("old_string")
        new = f.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return (False, "old_string/new_string must be strings")
        if not old:
            return (False, "old_string is empty")
        if old == new:
            return (False, "old_string == new_string (no-op)")
        # Uniqueness check — Edit-style replace requires exactly one match
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return (False, f"unreadable file {path!r}: {exc}")
        hits = content.count(old)
        if hits == 0:
            return (False, f"old_string not found in {path!r}")
        if hits > 1:
            return (False, f"old_string not unique in {path!r} ({hits} matches)")
    return (True, "ok")


def _run(cmd: List[str], cwd: Path, timeout: int = 60) -> Tuple[int, str]:
    import subprocess as _sp
    try:
        r = _sp.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return (r.returncode, (r.stdout or "") + (r.stderr or ""))
    except Exception as exc:
        return (-1, f"<exception: {exc}>")


def _create_autofix_worktree(task_id: str, sig_short: str) -> Optional[Tuple[Path, str]]:
    """Create .tmp/autofix/<task_id>__<sig>/ on branch autofix/<task_id>-<sig>.

    Distinct from kanban's .tmp/worktrees/ — never collides with an
    in-flight kanban task, never touches main directly.
    """
    AUTOFIX_WORKTREE_BASE.mkdir(parents=True, exist_ok=True)
    wt = AUTOFIX_WORKTREE_BASE / f"{task_id}__{sig_short}"
    branch = f"autofix/{task_id}-{sig_short}"

    # Clean prior leftovers
    if wt.exists():
        _run(["git", "worktree", "remove", "--force", str(wt)], cwd=BASE_DIR, timeout=30)
        import shutil
        shutil.rmtree(wt, ignore_errors=True)
        _run(["git", "worktree", "prune"], cwd=BASE_DIR, timeout=10)
        _run(["git", "branch", "-D", branch], cwd=BASE_DIR, timeout=10)

    rc, out = _run(
        ["git", "worktree", "add", "-b", branch, str(wt)],
        cwd=BASE_DIR, timeout=60,
    )
    if rc != 0 or not (wt / ".git").exists():
        logger.warning("autofix worktree add failed: rc=%s out=%s", rc, out[:400])
        return None
    return (wt, branch)


def _cleanup_autofix_worktree(wt: Path, branch: str, *, keep_branch: bool) -> None:
    _run(["git", "worktree", "remove", "--force", str(wt)], cwd=BASE_DIR, timeout=30)
    _run(["git", "worktree", "prune"], cwd=BASE_DIR, timeout=10)
    import shutil
    shutil.rmtree(wt, ignore_errors=True)
    if not keep_branch:
        _run(["git", "branch", "-D", branch], cwd=BASE_DIR, timeout=10)


def _write_audit(task_id: str, sig: str, record: Dict[str, Any]) -> None:
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        (AUDIT_DIR / f"{task_id}__{sig}.json").write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("failure_triage: audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# Independent verification gates (WS4.2) — close the self-grading gap.
#
# ``verification_command`` is chosen by the same LLM that wrote the patch, so a
# narrow command ("pytest the one test I just made pass") can green-light a
# change that still breaks CI. After that command passes we ALSO re-run the
# checks the LLM did NOT choose:
#   (a) the ORIGINALLY failing test (parsed from the failure reason / evidence)
#       must now pass — proves the patch fixes the real defect,
#   (b) the EXACT CI ruff command (Tier-0 lint gate) must pass,
#   (c) the coherence gate must introduce NO new hard-failing checks vs the
#       unpatched base tree.
# Any failure rolls the whole worktree back, exactly like a failed
# verification_command. The allowlist + metachar rejection on the LLM-supplied
# command are untouched — these gates are additive.
# ---------------------------------------------------------------------------

# Exact Tier-0 lint command from .github/workflows/icdev-ci.yml. Kept in sync
# with CI so "passes here" means "passes the CI lint gate". The base tree is
# kept ruff-clean by that blocking gate, so an absolute exit-0 requirement is
# safe — a failure means THIS patch introduced it.
_RUFF_CI_CMD: Tuple[str, ...] = (
    "python", "-m", "ruff", "check", "tools/", "tests/",
    "--select", "E,F,W",
    "--ignore", "E402,E501,E701,E702,E721,E722,E731,E741,F404",
)

# Coherence is run with --all and diffed against a baseline because the base
# tree legitimately carries advisory/pre-existing hard findings (CI itself runs
# `coherence_checker --all --gate || true`). We only block on NEW failures the
# patch introduces, not on debt it inherited.
_COHERENCE_CMD: Tuple[str, ...] = (
    "python", "tools/workflow/coherence_checker.py", "--all", "--json",
)

# Matches pytest node ids / test file paths inside a failure reason, e.g.
# "tests/test_foo.py::test_bar" or "tests/db/test_x.py".
_TEST_TARGET_RE = re.compile(r"(?:tests|features)[\w./\\-]*\.py(?:::[\w\[\].:+-]+)?")


def _extract_last_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the last complete JSON object out of noisy stdout.

    coherence_checker prints its JSON report last, but module import side
    effects (init_db / blueprint registration) pollute stdout before it. Scan
    every ``{`` with ``raw_decode`` and keep the last dict that parses cleanly.
    """
    dec = json.JSONDecoder()
    best: Optional[Dict[str, Any]] = None
    idx = text.find("{")
    while idx != -1:
        try:
            obj, end = dec.raw_decode(text, idx)
            if isinstance(obj, dict):
                best = obj
            idx = text.find("{", max(end, idx + 1))
        except json.JSONDecodeError:
            idx = text.find("{", idx + 1)
    return best


def _coherence_failing_checks(wt: Path) -> Tuple[Optional[set], int, str]:
    """Run the coherence checker in *wt* and return the set of hard-failing
    ``check_id``s. Returns ``(failing_set | None, rc, tail)``. ``None`` means
    the report could not be parsed — the caller decides how to treat that."""
    rc, out = _run(list(_COHERENCE_CMD), cwd=wt, timeout=300)
    report = _extract_last_json(out)
    if not report or "checks" not in report:
        return (None, rc, out[-1500:])
    failing = {
        c.get("check_id")
        for c in report.get("checks", [])
        if c.get("status") == "fail"
    }
    return (failing, rc, out[-1500:])


def _original_test_targets(reason: str, wt: Path) -> List[str]:
    """Extract re-runnable pytest targets from the failure reason / evidence,
    keeping only those whose file actually exists in the worktree."""
    targets: List[str] = []
    for m in _TEST_TARGET_RE.finditer(reason or ""):
        node = m.group(0).replace("\\", "/")
        file_part = node.split("::", 1)[0]
        if (wt / file_part).is_file() and node not in targets:
            targets.append(node)
        if len(targets) >= 5:
            break
    return targets


def _run_independent_gates(
    task: Dict[str, Any],
    wt: Path,
    baseline_coh_fails: Optional[set],
) -> Dict[str, Any]:
    """Re-run the originally failing test, the CI ruff gate, and the coherence
    gate in *wt*. The LLM's own verification_command has already passed.

    Returns ``{"passed": bool, "first_failure": str|None, "gates": {...}}``.
    """
    gates: Dict[str, Any] = {}

    # (a) ORIGINALLY failing test/gate — must now pass.
    reason = task.get("last_failure_reason") or ""
    targets = _original_test_targets(reason, wt)
    if targets:
        rc, out = _run(
            ["python", "-m", "pytest", *targets, "-p", "no:cacheprovider", "-q"],
            cwd=wt, timeout=600,
        )
        gates["original_test"] = {
            "ran": True, "targets": targets, "rc": rc, "tail": out[-1500:],
        }
        if rc != 0:
            return {"passed": False, "first_failure": "original_test", "gates": gates}
    else:
        # No re-runnable pytest target in the reason — the original failure was
        # a gate (coherence / lint / route), re-validated by (b) and (c).
        gates["original_test"] = {
            "ran": False,
            "reason": "no pytest target in failure reason; original failure was a "
                      "gate, re-validated by ruff + coherence below",
        }

    # (b) EXACT CI ruff command — absolute exit-0 (base tree is ruff-clean).
    rc, out = _run(list(_RUFF_CI_CMD), cwd=wt, timeout=300)
    gates["ruff"] = {"rc": rc, "tail": out[-1500:]}
    if rc != 0:
        return {"passed": False, "first_failure": "ruff", "gates": gates}

    # (c) Coherence gate — no NEW hard-failing checks vs the unpatched base.
    after_fails, coh_rc, coh_tail = _coherence_failing_checks(wt)
    gate_c: Dict[str, Any] = {
        "rc": coh_rc,
        "baseline_fails": sorted(baseline_coh_fails) if baseline_coh_fails is not None else None,
        "after_fails": sorted(after_fails) if after_fails is not None else None,
        "tail": coh_tail,
    }
    gates["coherence"] = gate_c
    if after_fails is None:
        # Post-patch report unparseable — the patch may have broken an import
        # the coherence checker loads. Fail closed.
        gate_c["note"] = "post-patch coherence report unparseable; failing closed"
        return {"passed": False, "first_failure": "coherence_unparsed", "gates": gates}
    if baseline_coh_fails is None:
        # Baseline could not be captured — we cannot diff, so don't block on
        # inherited debt. (a) and (b) still gate.
        gate_c["note"] = "baseline coherence unavailable; skipping coherence diff"
        return {"passed": True, "first_failure": None, "gates": gates}
    new_fails = after_fails - baseline_coh_fails
    if new_fails:
        gate_c["new_fails"] = sorted(new_fails)
        return {"passed": False, "first_failure": "coherence", "gates": gates}

    return {"passed": True, "first_failure": None, "gates": gates}


def apply_patch_in_worktree(
    task: Dict[str, Any],
    diag: Dict[str, Any],
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply an LLM-generated patch in an isolated worktree, verify, and
    conditionally merge.

    Contract:
      * Creates a fresh branch ``autofix/<task_id>-<sig>`` — never touches
        main directly.
      * Runs the LLM-supplied ``verification_command`` (allowlisted prefix
        only). Any non-zero exit → roll back the whole worktree + branch.
      * WS4.2 — then re-runs the checks the LLM did NOT choose via
        ``_run_independent_gates``: the originally failing test, the exact CI
        ruff gate, and the coherence gate (diffed against the unpatched base).
        Any of these failing also rolls the worktree back.
      * On success: commits to the autofix branch. If
        ``ICDEV_AUTOFIX_AUTOMERGE=true`` also fast-forward merges the
        branch into main via ``tools/genesis/reflexes/kanban._merge_worktree_to_main``.
      * Returns a dict that is safe to JSON-serialize and store on the
        triage entry. Increments the global rate counter only when an
        apply is actually attempted (after all pre-apply gates pass).
    """
    sig = _sig(task.get("last_failure_reason") or "")
    sig_short = sig[:8]
    now = _utcnow().isoformat()
    record: Dict[str, Any] = {
        "task_id": task.get("id"),
        "signature": sig,
        "started_at": now,
        "outcome": None,
    }

    # Pre-apply validation — gates the LLM output, not just the diagnosis.
    ok, why = _validate_verification_command(patch.get("verification_command") or "")
    if not ok:
        record["outcome"] = "rejected_bad_verification_command"
        record["reason"] = why
        _write_audit(task["id"], sig, record)
        return {"applied": False, "outcome": record["outcome"], "reason": why}

    ok, why = _validate_patch_files(patch, diag)
    if not ok:
        record["outcome"] = "rejected_bad_patch_files"
        record["reason"] = why
        _write_audit(task["id"], sig, record)
        return {"applied": False, "outcome": record["outcome"], "reason": why}

    # Count this attempt against the rolling hour cap even if verification
    # ultimately fails — it's the *attempt* that consumes budget.
    record_apply()

    created = _create_autofix_worktree(task["id"], sig_short)
    if not created:
        record["outcome"] = "worktree_create_failed"
        _write_audit(task["id"], sig, record)
        return {"applied": False, "outcome": record["outcome"]}
    wt, branch = created
    record["worktree"] = str(wt)
    record["branch"] = branch

    # WS4.2: capture the unpatched coherence baseline BEFORE any edits so the
    # post-patch coherence gate can require "no NEW failing checks" rather than
    # an absolute pass (the base tree carries pre-existing advisory debt that CI
    # itself tolerates with `|| true`).
    baseline_coh_fails, _bl_rc, _bl_tail = _coherence_failing_checks(wt)
    record["baseline_coherence_fails"] = (
        sorted(baseline_coh_fails) if baseline_coh_fails is not None else None
    )

    # 1) apply file edits (Edit-style unique replace, already validated unique)
    applied_files: List[str] = []
    try:
        for f in patch.get("files", []):
            path = f["path"].replace("\\", "/")
            full = wt / path
            text = full.read_text(encoding="utf-8", errors="replace")
            new_text = text.replace(f["old_string"], f["new_string"], 1)
            full.write_text(new_text, encoding="utf-8")
            applied_files.append(path)
    except Exception as exc:
        record["outcome"] = "edit_failed"
        record["reason"] = str(exc)
        _cleanup_autofix_worktree(wt, branch, keep_branch=False)
        _write_audit(task["id"], sig, record)
        return {"applied": False, "outcome": record["outcome"], "reason": str(exc)}

    record["applied_files"] = applied_files

    # 2) verification_command — run in the worktree cwd
    cmd_parts = (patch["verification_command"] or "").split()
    rc, out = _run(cmd_parts, cwd=wt, timeout=600)
    record["verification_rc"] = rc
    record["verification_tail"] = out[-1500:]
    _emit_event(
        EVENT_VERIFY_RESULT,
        task_id=task.get("id"),
        signature=sig,
        level="INFO" if rc == 0 else "ERROR",
        confidence=diag.get("confidence"),
        recommendation=diag.get("recommendation"),
        verification_command=patch.get("verification_command"),
        verification_rc=rc,
        passed=(rc == 0),
        outcome="verify_passed" if rc == 0 else "verify_failed",
    )

    if rc != 0:
        record["outcome"] = "verification_failed"
        _cleanup_autofix_worktree(wt, branch, keep_branch=False)
        _write_audit(task["id"], sig, record)
        return {"applied": False, "outcome": record["outcome"], "verification_rc": rc}

    # 2b) WS4.2 — verification beyond the LLM's own command. Re-run the
    # originally failing test, the CI ruff gate, and the coherence gate. The
    # LLM picked the command above; these it did not. Any failure rolls back.
    gate_result = _run_independent_gates(task, wt, baseline_coh_fails)
    record["independent_gates"] = gate_result["gates"]
    _emit_event(
        EVENT_VERIFY_RESULT,
        task_id=task.get("id"),
        signature=sig,
        level="INFO" if gate_result["passed"] else "ERROR",
        confidence=diag.get("confidence"),
        recommendation=diag.get("recommendation"),
        passed=gate_result["passed"],
        first_failure=gate_result.get("first_failure"),
        outcome="independent_gates_passed" if gate_result["passed"] else "independent_gates_failed",
    )
    if not gate_result["passed"]:
        record["outcome"] = "independent_gate_failed"
        record["failed_gate"] = gate_result.get("first_failure")
        _cleanup_autofix_worktree(wt, branch, keep_branch=False)
        _write_audit(task["id"], sig, record)
        return {
            "applied": False,
            "outcome": record["outcome"],
            "failed_gate": gate_result.get("first_failure"),
        }

    # 3) commit on the autofix branch (never amend, new commit per apply)
    commit_msg = (
        f"autofix: {task.get('title','')[:60]}\n\n"
        f"Auto-fix-source: failure_triage\n"
        f"Source-task: {task.get('id')}\n"
        f"Signature: {sig}\n"
        f"Diagnosis: {(diag.get('root_cause') or '')[:200]}\n"
        f"Confidence: {diag.get('confidence')}\n"
        f"Verification: {patch['verification_command']}\n"
    )
    _run(["git", "add", "--"] + applied_files, cwd=wt, timeout=30)
    rc_c, out_c = _run(["git", "commit", "-m", commit_msg], cwd=wt, timeout=60)
    if rc_c != 0:
        record["outcome"] = "commit_failed"
        record["reason"] = out_c[-500:]
        _cleanup_autofix_worktree(wt, branch, keep_branch=False)
        _write_audit(task["id"], sig, record)
        return {"applied": False, "outcome": record["outcome"]}

    # Capture the new commit sha for the audit
    rc_sha, sha_out = _run(["git", "rev-parse", "HEAD"], cwd=wt, timeout=10)
    record["autofix_commit"] = (sha_out or "").strip() if rc_sha == 0 else None

    # 4) conditional merge into main
    merged = False
    if automerge_enabled():
        try:
            # _merge_worktree_to_main takes task_id and assumes branch
            # kanban/<task_id>. Our branch is autofix/<task_id>-<sig> so
            # perform the merge directly here instead.
            merged = _ff_merge_autofix_branch(branch)
        except Exception as exc:
            logger.warning("failure_triage: automerge failed: %s", exc)
            merged = False
    record["merged_to_main"] = merged

    # 5) cleanup — remove worktree directory, but KEEP the autofix branch
    # so humans (or automerge later) can still see what was applied.
    _cleanup_autofix_worktree(wt, branch, keep_branch=True)

    record["outcome"] = "applied_verified_committed" + ("_merged" if merged else "")
    _write_audit(task["id"], sig, record)

    return {
        "applied": True,
        "outcome": record["outcome"],
        "branch": branch,
        "commit": record.get("autofix_commit"),
        "merged_to_main": merged,
        "applied_files": applied_files,
    }


def _ff_merge_autofix_branch(branch: str) -> bool:
    """Attempt a fast-forward merge of ``branch`` into main in the main
    worktree. Refuses to merge if main has diverged (no 3-way merge).
    """
    # 1. Are we on main? If not, abort — automerge is only for the
    # straightforward case.
    rc, head = _run(["git", "symbolic-ref", "--short", "HEAD"], cwd=BASE_DIR, timeout=10)
    db = default_branch()
    if rc != 0 or head.strip() != db:
        logger.info("automerge skipped — current branch is not %s (%s)", db, head.strip())
        return False
    # 2. Refuse to merge with a dirty tree
    rc, out = _run(["git", "status", "--porcelain"], cwd=BASE_DIR, timeout=10)
    if rc != 0 or out.strip():
        logger.info("automerge skipped — main worktree dirty")
        return False
    # 3. ff-only merge. If main has moved, we do NOT force — leave the
    # branch for human resolution.
    rc, out = _run(["git", "merge", "--ff-only", branch], cwd=BASE_DIR, timeout=60)
    if rc != 0:
        logger.info("automerge ff failed: %s", out[:200])
    return rc == 0


# ---------------------------------------------------------------------------
# Orchestrator — one pass over recent failures
# ---------------------------------------------------------------------------

def triage_once(
    window_hours: int = DEFAULT_WINDOW_HOURS,
    *,
    apply: bool = False,
) -> Dict[str, Any]:
    """One pass. Returns a JSON-serializable summary.

    ``apply=True`` activates the auto-apply gates. Even then, each
    individual failure must pass every ``should_auto_apply`` check.
    """
    started = _utcnow().isoformat()
    failures = find_recent_failures(window_hours=window_hours)
    results: List[Dict[str, Any]] = []

    for task in failures:
        sig = _sig(task.get("last_failure_reason") or "")
        entry: Dict[str, Any] = {
            "task_id": task.get("id"),
            "title": task.get("title"),
            "task_type": task.get("task_type"),
            "signature": sig,
        }
        if already_triaged(task["id"], sig):
            entry["outcome"] = "skipped_already_triaged"
            results.append(entry)
            continue

        diag = diagnose_task(task)
        entry["diagnosis"] = {
            "root_cause": diag.get("root_cause"),
            "recommendation": diag.get("recommendation"),
            "confidence": diag.get("confidence"),
            "source": diag.get("_source"),
        }
        _emit_event(
            EVENT_DIAGNOSIS_MADE,
            task_id=task.get("id"),
            signature=sig,
            confidence=diag.get("confidence"),
            recommendation=diag.get("recommendation"),
            root_cause=(diag.get("root_cause") or "")[:300],
            source=diag.get("_source"),
        )

        allow, allow_reason = should_auto_apply(task, diag)
        entry["autofix_gate"] = {"allow": allow, "reason": allow_reason}
        _emit_event(
            EVENT_GATE_DECISION,
            task_id=task.get("id"),
            signature=sig,
            confidence=diag.get("confidence"),
            recommendation=diag.get("recommendation"),
            allow=allow,
            reason=allow_reason,
        )

        # Chain-blocker escalation — the replacement for the removed
        # confidence relaxation. Fires when a blocker is NOT being
        # auto-applied, i.e. exactly when a human has to look at it.
        esc = chain_blocker_escalation(task)
        if esc and not (allow and apply):
            entry["escalation"] = esc
            _emit_event(
                EVENT_ESCALATION,
                task_id=task.get("id"),
                signature=sig,
                level="WARNING",
                confidence=diag.get("confidence"),
                recommendation=diag.get("recommendation"),
                blocked_dependents_count=esc["blocked_dependents_count"],
                reason=allow_reason,
            )

        if allow and apply:
            patch = generate_patch(task, diag)
            if patch and patch.get("files"):
                entry["patch_preview"] = {
                    "files": [f.get("path") for f in patch.get("files", [])],
                    "verification_command": patch.get("verification_command"),
                }
                _emit_event(
                    EVENT_PATCH_GENERATED,
                    task_id=task.get("id"),
                    signature=sig,
                    confidence=diag.get("confidence"),
                    recommendation=diag.get("recommendation"),
                    generated=True,
                    files=[f.get("path") for f in patch.get("files", [])],
                    verification_command=patch.get("verification_command"),
                )
                apply_result = apply_patch_in_worktree(task, diag, patch)
                entry["apply_result"] = apply_result
                if apply_result.get("applied"):
                    entry["outcome"] = apply_result["outcome"]
                    # Post a card so the human sees the autofix even when it
                    # merged cleanly — keeps an auditable trail on the board.
                    _create_diagnostic_card_with_patch(task, diag, patch)
                else:
                    # Verification failed, validation rejected the patch, or
                    # the worktree failed to build. Fall through to a
                    # suggested card so a human can pick it up.
                    entry["outcome"] = "apply_failed_fell_through_to_card"
                    _create_diagnostic_card_with_patch(task, diag, patch)
            else:
                _emit_event(
                    EVENT_PATCH_GENERATED,
                    task_id=task.get("id"),
                    signature=sig,
                    level="WARNING",
                    confidence=diag.get("confidence"),
                    recommendation=diag.get("recommendation"),
                    generated=False,
                )
                entry["outcome"] = "patch_gen_failed_fell_through_to_card"
                _create_diagnostic_card(task, diag)
        else:
            _create_diagnostic_card(task, diag)
            entry["outcome"] = "suggested_card_created"

        _emit_event(
            EVENT_APPLY_OUTCOME,
            task_id=task.get("id"),
            signature=sig,
            level="WARNING" if "fail" in (entry.get("outcome") or "") else "INFO",
            confidence=diag.get("confidence"),
            recommendation=diag.get("recommendation"),
            outcome=entry.get("outcome"),
        )

        mark_triaged(task["id"], sig, entry)
        results.append(entry)

    return {
        "started_at": started,
        "finished_at": _utcnow().isoformat(),
        "autofix_enabled": autofix_enabled(),
        "window_hours": window_hours,
        "apply_mode": apply,
        "failures_scanned": len(failures),
        "results": results,
    }


def _create_diagnostic_card(task: Dict[str, Any], diag: Dict[str, Any]) -> Optional[str]:
    """Wrap self_debug._create_diagnostic_card so callers don't import a
    private symbol directly. Applies the chain-blocker escalation marker."""
    from tools.workflow.self_debug import _create_diagnostic_card as _cdc, snapshot
    reason = task.get("last_failure_reason") or ""
    snap = snapshot(task["id"], str(BASE_DIR), reason)
    card_id = _cdc(task["id"], reason, snap, diag)
    if card_id:
        _apply_escalation(card_id, chain_blocker_escalation(task))
    return card_id


def _apply_escalation(card_id: str, esc: Optional[Dict[str, Any]]) -> None:
    """Stamp the escalation marker onto an already-created suggested card.

    self_debug already inserts its RCA cards at ``critical``; this adds the
    title marker so an operator scanning the board can see *why* it is
    critical without opening it. Best-effort — escalation must never break
    the triage pass.
    """
    if not esc:
        return
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT title FROM kanban_tasks WHERE id = %s", (card_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            title = (dict(row).get("title") or "")
            marker = esc["title_marker"]
            if marker in title:
                return
            conn.execute(
                "UPDATE kanban_tasks SET title = %s, priority = %s WHERE id = %s",
                (f"{marker}{title}"[:255], esc["priority"], card_id),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("failure_triage: escalation stamp failed for %s: %s", card_id, exc)


def _create_diagnostic_card_with_patch(
    task: Dict[str, Any], diag: Dict[str, Any], patch: Dict[str, Any],
) -> Optional[str]:
    """Same as the plain card, but embeds the generated patch in the body
    so a human can review & apply with one click."""
    from tools.db.storage import get_connection
    import uuid

    esc = chain_blocker_escalation(task)
    new_id = f"diag-{uuid.uuid4().hex[:10]}"
    title = f"Oracle RCA + PATCH READY: {task['id']} stuck in loop"
    if esc:
        title = f"{esc['title_marker']}{title}"
    priority = esc["priority"] if esc else "high"
    body_lines = [
        "AUTO-CREATED by failure_triage reflex — patch attached.",
        "",
        f"## Source task\n{task['id']} — {task.get('title','')}",
        f"\n## Failure reason\n{(task.get('last_failure_reason') or '')[:500]}",
        f"\n## Diagnosis ({diag.get('_source','?')}, conf={diag.get('confidence', 0)})",
        f"- Root cause: {diag.get('root_cause', '?')}",
        f"- Recommendation: {diag.get('recommendation', '?')}",
        f"- Patch hint: {diag.get('patch_hint', '—')}",
        "- Suspect files:",
    ]
    for sf in diag.get("suspect_files", []) or ["(none)"]:
        body_lines.append(f"  - {sf}")
    body_lines.append("\n## Patch (LLM-generated — REVIEW BEFORE APPLY)")
    body_lines.append("```json")
    body_lines.append(json.dumps(patch, indent=2)[:4000])
    body_lines.append("```")
    body_lines.append(f"\n## Verification command\n`{patch.get('verification_command','(none)')}`")
    body = "\n".join(body_lines)

    now = _utcnow().isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (new_id, title, body, "chore", priority, "suggested", now, now),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("failure_triage: suggested-card insert failed: %s", exc)
        return None
    return new_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Triage recent kanban failures via LLM diagnosis + optional patch generation."
    )
    p.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS,
                   help=f"Look-back window in hours (default {DEFAULT_WINDOW_HOURS})")
    p.add_argument("--apply", action="store_true",
                   help="Request patch generation for high-confidence diagnoses "
                        "(still requires ICDEV_AUTOFIX_ENABLED=true). "
                        "Default is diagnose-only.")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Print result as JSON to stdout")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = triage_once(window_hours=args.window_hours, apply=args.apply)
    if args.as_json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"Scanned {summary['failures_scanned']} failures "
              f"(window {summary['window_hours']}h, apply={summary['apply_mode']}, "
              f"autofix_env={summary['autofix_enabled']})")
        for r in summary["results"]:
            print(f"  - {r['task_id']}: {r['outcome']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
