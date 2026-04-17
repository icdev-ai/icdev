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
   a human.
7. Record a triage marker so the same signature isn't reprocessed.

Defaults are intentionally conservative:
* ``--dry-run`` is the default CLI behavior.
* ``ICDEV_AUTOFIX_ENABLED`` defaults to ``false`` — the auto-apply path
  is opt-in per operator.
* Task-type whitelist, file deny-list, rate limit, and kill switch all
  have to be green before a patch is applied.

Kill switch: set ``ICDEV_AUTOFIX_ENABLED=false`` (or leave it unset) to
force suggested-card-only behavior.
"""

from __future__ import annotations

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

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TRIAGED_DIR = BASE_DIR / ".tmp" / "kanban" / "triaged"
RATE_FILE = BASE_DIR / ".tmp" / "kanban" / "triage_rate.json"

# Tuning knobs
APPLY_CONFIDENCE = 0.85            # min LLM confidence to auto-apply
SUGGEST_CONFIDENCE = 0.50          # min confidence to bother creating a card
MAX_APPLIES_PER_HOUR = 5           # matches CLAUDE.md self-healing cap
DEFAULT_WINDOW_HOURS = 1           # how far back to scan

# Task-type whitelist for auto-apply (match feedback_kanban_vv_policy.md
# "fail-open for build/research/test/chore" — plus 'bug').
AUTO_APPLY_TASK_TYPES = {"build", "bug", "chore", "test", "research"}

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
    * ``last_failure_reason`` set and ``status`` in (backlog, failed), or
    * ``failure_count > 0`` for tasks returned to backlog on cooldown.
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
            sql = (
                "SELECT id, title, description, task_type, priority, status, "
                "       failure_count, last_failure_reason, updated_at "
                "FROM kanban_tasks "
                f"WHERE last_failure_reason IS NOT NULL "
                f"  AND updated_at > {ph} "
                f"  AND status IN ('backlog','failed','scheduled') "
                f"ORDER BY updated_at DESC "
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
    """Return (allow, reason). ``reason`` is human-readable even on allow."""
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


# ---------------------------------------------------------------------------
# Diagnose wrapper — reuses self_debug.snapshot + LLM routing 'failure_triage_diagnose'
# ---------------------------------------------------------------------------

def diagnose_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Run thinking-tier LLM diagnosis. Falls back to self_debug heuristic."""
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
        return self_debug.diagnose(snap)

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
        return self_debug.diagnose(snap)
    except Exception as exc:
        logger.warning("failure_triage: diagnose LLM failed (%s); fallback", exc)
        return self_debug.diagnose(snap)


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

        allow, allow_reason = should_auto_apply(task, diag)
        entry["autofix_gate"] = {"allow": allow, "reason": allow_reason}

        if allow and apply:
            patch = generate_patch(task, diag)
            if patch and patch.get("files"):
                # Defer the actual application to a separate, review-gated
                # PR (see module docstring / README). We stop here in the
                # current build: the patch is recorded on the diagnostic
                # card so a human can apply it with one click, and the
                # rate counter is NOT incremented (no real apply happened).
                entry["patch_preview"] = {
                    "files": [f.get("path") for f in patch.get("files", [])],
                    "verification_command": patch.get("verification_command"),
                }
                entry["outcome"] = "patch_generated_awaiting_review"
                _create_diagnostic_card_with_patch(task, diag, patch)
            else:
                entry["outcome"] = "patch_gen_failed_fell_through_to_card"
                _create_diagnostic_card(task, diag)
        else:
            _create_diagnostic_card(task, diag)
            entry["outcome"] = "suggested_card_created"

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
    private symbol directly."""
    from tools.workflow.self_debug import _create_diagnostic_card as _cdc, snapshot
    reason = task.get("last_failure_reason") or ""
    snap = snapshot(task["id"], str(BASE_DIR), reason)
    return _cdc(task["id"], reason, snap, diag)


def _create_diagnostic_card_with_patch(
    task: Dict[str, Any], diag: Dict[str, Any], patch: Dict[str, Any],
) -> Optional[str]:
    """Same as the plain card, but embeds the generated patch in the body
    so a human can review & apply with one click."""
    from tools.db.storage import get_connection
    import uuid

    new_id = f"diag-{uuid.uuid4().hex[:10]}"
    title = f"Oracle RCA + PATCH READY: {task['id']} stuck in loop"
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
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, title, body, "chore", "high", "suggested", now, now),
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
