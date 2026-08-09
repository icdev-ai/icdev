# CUI // SP-CTI
"""Kanban self-debug reflex — diagnose recurring failures instead of looping.

When a kanban task fails verification N times with the same signature, this
module captures the state, asks an LLM for a root-cause analysis, creates a
diagnostic kanban card with the evidence, and quarantines the looping task
so the scheduler stops burning retries on a structural problem.

Zero schema changes: per-task signature history is stored in
``.tmp/kanban/<task_id>.signatures.json``.

Public entry point: ``check_and_diagnose(task_id, reason, cwd)``.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger
from tools.workflow.git_utils import default_branch

import hashlib
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
KANBAN_DIR = BASE_DIR / ".tmp" / "kanban"
RECURRENCE_THRESHOLD = 3  # same signature this many times → diagnose
SIG_HISTORY_MAX = 20       # cap per-task history size


# ---------------------------------------------------------------------------
# Signature — turn noisy reason text into a stable recurrence key
# ---------------------------------------------------------------------------

_NORMALIZE_PATTERNS = [
    (re.compile(r"\| REMEDIATION=.*$"), ""),          # strip retry annotations
    # Strip AUTO-REMEDIATED prefix so the underlying failure keeps the same
    # signature across remediation attempts — prevents inflating the recurrence
    # count with what is really the same root cause.
    (re.compile(r"^auto-remediated \([^)]+\):[^|]*\|\s*", re.IGNORECASE), ""),
    (re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.+-]+"), "<TS>"),  # timestamps
    (re.compile(r"\b\d{3,}\b"), "<N>"),               # counts, PIDs, sizes
    (re.compile(r"\s+"), " "),
]


def failure_signature(reason: str) -> str:
    """Normalize + hash a failure reason to a stable 12-char signature."""
    s = reason or ""
    for pat, repl in _NORMALIZE_PATTERNS:
        s = pat.sub(repl, s)
    s = s.strip().lower()
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]  # nosec B324 -- id, not crypto


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _task_is_done(task_id: str) -> bool:
    """Return True if the task already has status='done' in the kanban DB.

    Called before recording a failure signature so we never quarantine or
    create diagnostic cards for tasks that self-reported completion — the
    worktree may have been cleaned up by the completion path, which leaves
    cwd_exists=false and triggers false coherence failures through the
    verification gate even though the work is finished.
    """
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM kanban_tasks WHERE id = %s", (task_id,)
            ).fetchone()
        if row and dict(row).get("status") == "done":
            return True
    except Exception as exc:
        logger.warning("self_debug: DB status check failed for %s: %s", task_id, exc)
    return False


def _task_exists(task_id: str) -> bool:
    """Return True if a row for ``task_id`` exists in the kanban DB.

    Guards the reflex against synthetic / test-fixture task ids that drive
    ``_verify_task_completed`` directly without ever being seeded on the board
    — most notably the ``task-real`` fixture in
    tests/genesis/test_kanban_phantom_guard.py. That test exercises the real
    verification path on its "no git commits" branch, which calls this reflex
    with a fixture id and a pytest ``tmp_path`` cwd. Because the signature
    history lives under the real repo ``.tmp/kanban/`` (this module's BASE_DIR,
    not the test's monkeypatched one), it accumulates across test-suite runs
    and, on the 3rd run, spawns a phantom Oracle RCA card for a task that does
    not exist — a loop that has produced dozens of dead ``diag-*`` cards.

    A recurring *real* task loop cannot exist for a task that is not on the
    board, so a missing row means the failure is a false positive: skip it.
    On a DB error we fail open (return True) so a transient blip never
    suppresses diagnosis of a genuine task.
    """
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM kanban_tasks WHERE id = %s", (task_id,)
            ).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("self_debug: DB existence check failed for %s: %s", task_id, exc)
        return True  # fail open — never suppress real diagnosis on a DB blip


# ---------------------------------------------------------------------------
# Signature history (file-backed)
# ---------------------------------------------------------------------------

def _history_path(task_id: str) -> Path:
    return KANBAN_DIR / f"{task_id}.signatures.json"


def record_failure(task_id: str, reason: str) -> int:
    """Append to per-task signature history; return count for this signature."""
    sig = failure_signature(reason)
    path = _history_path(task_id)
    now = datetime.now(timezone.utc).isoformat()
    history: List[Dict[str, Any]] = []
    if path.exists():
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history.append({"sig": sig, "reason": reason[:500], "ts": now})
    history = history[-SIG_HISTORY_MAX:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(history, indent=2), encoding="utf-8", newline="")
    except Exception as exc:
        logger.warning("self_debug: failed to write signature history: %s", exc)
    return sum(1 for h in history if h.get("sig") == sig)


# ---------------------------------------------------------------------------
# Snapshot — structured evidence for the diagnoser
# ---------------------------------------------------------------------------

def _safe_run(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 10) -> str:
    try:
        r = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return f"<error: {exc}>"


def _git_ref_exists(ref: str, cwd: Path) -> bool:
    """True only if git's exit code confirms the ref exists.

    Unlike ``_safe_run``, which swallows git's own non-zero exit (fatal:
    messages are just text in the returned string), this checks the actual
    return code so a missing ref is never mistaken for a present one.
    """
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=str(cwd), capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _suspect_code_refs(reason: str) -> List[str]:
    """Find distinctive phrases in the reason and grep the codebase for them.

    Returns up to 5 ``file:line`` hits. Heuristic — helps the LLM localize
    the code path that produced the error string.
    """
    hits: List[str] = []
    # Pull quoted strings and error-like phrases from the reason
    phrases = set()
    for m in re.finditer(r'"([^"]{8,80})"', reason or ""):
        phrases.add(m.group(1))
    for m in re.finditer(r":\s*([A-Z][A-Za-z0-9_ ]{10,60})", reason or ""):
        phrases.add(m.group(1).strip())
    # Also grep for distinctive "not in worktree"-style fragments
    for frag in ["not in worktree", "main passes, cwd fails", "broken by cwd",
                 "phantom completion", "no git commits"]:
        if frag in (reason or "").lower():
            phrases.add(frag)
    for p in list(phrases)[:3]:
        out = _safe_run(["git", "grep", "-n", "--", p, "tools/"], cwd=BASE_DIR, timeout=5)
        for line in out.splitlines()[:3]:
            if line.strip():
                hits.append(line.strip())
        if len(hits) >= 5:
            break
    return hits[:5]


def snapshot(task_id: str, cwd: str, reason: str) -> Dict[str, Any]:
    """Collect diagnostic evidence for an LLM root-cause analysis."""
    cwd_path = Path(cwd)
    snap: Dict[str, Any] = {
        "task_id": task_id,
        "cwd": cwd,
        "cwd_exists": cwd_path.exists(),
        "is_worktree_path": ".tmp" in cwd_path.parts and "worktrees" in cwd_path.parts,
    }
    if cwd_path.exists():
        try:
            top = [p.name for p in cwd_path.iterdir()][:15]
            snap["cwd_top_entries"] = top
            snap["cwd_entry_count"] = len(list(cwd_path.iterdir()))
        except Exception:
            snap["cwd_top_entries"] = []
            snap["cwd_entry_count"] = 0
        snap["has_tools_manifest"] = (cwd_path / "tools" / "manifest.md").exists()
        snap["has_dot_git"] = (cwd_path / ".git").exists()
    # Git worktree registration
    wt_list = _safe_run(["git", "worktree", "list", "--porcelain"], cwd=BASE_DIR)
    snap["worktree_registered"] = str(cwd_path).replace("\\", "/") in wt_list.replace("\\", "/")
    # Branch state
    branch = f"kanban/{task_id}"
    snap["branch_exists"] = _git_ref_exists(branch, BASE_DIR)
    if snap["branch_exists"]:
        db = default_branch(str(BASE_DIR))
        snap["branch_commits_ahead"] = _safe_run(
            ["git", "rev-list", "--count", f"{db}..{branch}"], cwd=BASE_DIR).strip()
    else:
        snap["branch_commits_ahead"] = "n/a: branch does not exist"
    # Last task log tail
    log_path = KANBAN_DIR / f"{task_id}.log"
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            snap["log_tail"] = lines[-20:]
        except Exception:
            snap["log_tail"] = []
    snap["suspect_code"] = _suspect_code_refs(reason)
    snap["reason"] = reason[:2000]
    return snap


# ---------------------------------------------------------------------------
# Diagnose — LLM root-cause analysis with deterministic fallback
# ---------------------------------------------------------------------------

_DIAGNOSIS_SCHEMA_HINT = """Return a JSON object with these keys only:
{
  "root_cause": "<one sentence — structural cause, not symptom>",
  "suspect_files": ["tools/...py:LINE", ...],
  "recommendation": "patch" | "quarantine" | "rebuild_worktree",
  "patch_hint": "<one sentence — if recommendation=patch, what to change>",
  "confidence": 0.0..1.0
}
No prose outside the JSON. No markdown fences."""


def diagnose(snap: Dict[str, Any], chain_mode: str = "") -> Dict[str, Any]:
    """LLM RCA; falls back to heuristic when no LLM is available.

    Args:
        snap: Failure snapshot dict.
        chain_mode: Optional chain mode — "cot" for step-by-step reasoning.
    """
    try:
        from tools.llm.router import LLMRouter, LLMUnavailableError
        from tools.llm.provider import LLMRequest
    except Exception as exc:
        logger.warning("self_debug: LLM router import failed: %s", exc)
        return _heuristic_diagnosis(snap)

    prompt = (
        "A kanban task has failed verification repeatedly with the same "
        "signature. Diagnose the STRUCTURAL root cause from this evidence. "
        "Do not repeat the symptom — explain why the loop keeps happening "
        "and point at the code that needs fixing.\n\n"
        f"EVIDENCE (JSON):\n{json.dumps(snap, indent=2, default=str)}\n\n"
        f"{_DIAGNOSIS_SCHEMA_HINT}"
    )
    try:
        router = LLMRouter()
        resp = router.invoke(
            "code_generation",
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800, temperature=0.2, effort="medium",
                skip_injection_scan=True,
                chain_mode=chain_mode,
            ),
        )
        text = resp.content.strip()
        # Tolerate accidental code fences
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[-1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError("no JSON object in LLM response")
        diag = json.loads(m.group(0))
        diag["_source"] = "llm"
        return diag
    except LLMUnavailableError:
        logger.info("self_debug: LLM unavailable, using heuristic diagnosis")
        return _heuristic_diagnosis(snap)
    except Exception as exc:
        logger.warning("self_debug: LLM diagnosis failed (%s); using heuristic", exc)
        return _heuristic_diagnosis(snap)


# Exception class → (recommendation, confidence). Confidence is deliberately
# kept in the 0.55–0.65 band: high enough to beat the conf-0.30 "manual review"
# floor and route the failure to a real card, but well below the 0.85 auto-apply
# bar so the patch is never applied without human/LLM review.
_PATCH_EXCEPTIONS = {
    "AssertionError", "ImportError", "ModuleNotFoundError", "NameError",
    "AttributeError", "TypeError", "ValueError", "KeyError", "IndexError",
    "SyntaxError", "IndentationError", "TabError", "RuntimeError",
    "UnboundLocalError", "NotImplementedError", "ZeroDivisionError",
    "FileNotFoundError", "LookupError", "ArithmeticError",
}
# Environmental / flaky failures — patching the suspect line rarely helps; the
# right move is to quarantine and let a human decide (raise budget, retry, etc).
_QUARANTINE_EXCEPTIONS = {
    "TimeoutError", "ConnectionError", "ConnectionRefusedError",
    "ConnectionResetError", "BrokenPipeError", "OSError", "IOError",
    "MemoryError", "socket.timeout", "concurrent.futures.TimeoutError",
}


def _classify_exception(exc_type: str) -> tuple:
    """Map an exception class to (recommendation, confidence).

    Patchable in-repo exceptions → patch @0.62; environmental/flaky →
    quarantine @0.58; anything else → patch @0.55 (an in-repo suspect frame is
    still the best deterministic lead, just less certain).
    """
    if exc_type in _QUARANTINE_EXCEPTIONS:
        return "quarantine", 0.58
    if exc_type in _PATCH_EXCEPTIONS:
        return "patch", 0.62
    return "patch", 0.55


def _patch_hint_for_exception(exc_type: str, suspect: str) -> str:
    """One-sentence, exception-aware remediation hint pointing at the suspect."""
    if exc_type in ("ImportError", "ModuleNotFoundError"):
        return (f"Fix the failing import at {suspect} — correct the module path "
                f"or add the missing dependency to requirements.txt.")
    if exc_type in _QUARANTINE_EXCEPTIONS:
        return (f"{exc_type} at {suspect} looks environmental/flaky — quarantine "
                f"for human review (raise the budget or retry) rather than patch.")
    if exc_type == "AssertionError":
        return (f"An assertion failed at {suspect} — reconcile expected vs actual; "
                f"fix the implementation or the test.")
    if exc_type in ("SyntaxError", "IndentationError", "TabError"):
        return f"Syntax/indentation error at {suspect} — fix the offending line."
    return (f"Inspect {suspect} where {exc_type} was raised and correct the "
            f"offending code.")


def _traceback_diagnosis(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Deterministic diagnosis from a parsed traceback, or None.

    Fires only when ``traceback_analyzer`` extracts BOTH an exception class and
    a primary *in-repo* suspect frame. Produces a real root_cause naming the
    exception + frame, suspect_files=[primary suspect], an exception-class-based
    recommendation, and a calibrated confidence (0.55–0.65). Returns None when
    there is no exception or no first-party suspect, so the generic fallback
    still applies.
    """
    try:
        from tools.workflow.traceback_analyzer import parse_traceback
    except Exception as exc:  # analyzer optional — never break the fallback
        logger.debug("self_debug: traceback_analyzer import failed: %s", exc)
        return None

    parts: List[str] = []
    raw_reason = snap.get("reason")
    if raw_reason:
        parts.append(str(raw_reason))
    log_tail = snap.get("log_tail")
    if isinstance(log_tail, list) and log_tail:
        parts.append("\n".join(str(x) for x in log_tail))
    text = "\n".join(parts)
    if not text.strip():
        return None

    tb = parse_traceback(text)
    if not tb.exception_type:
        return None
    # Deepest in-repo frame is the primary suspect; require a first-party frame.
    suspect_frame = next((fr for fr in reversed(tb.frames) if fr.in_repo), None)
    if suspect_frame is None:
        return None

    suspect = f"{suspect_frame.file}:{suspect_frame.line}"
    exc_type = tb.exception_type
    recommendation, confidence = _classify_exception(exc_type)
    func = (suspect_frame.function or "").strip()
    msg = (tb.exception_message or "").strip()
    root_cause = (
        f"{exc_type} raised at {suspect}"
        + (f" (in {func})" if func else "")
        + (f": {msg[:160]}" if msg else "")
        + " — deterministic traceback localization."
    )
    return {
        "root_cause": root_cause,
        "suspect_files": [suspect],
        "recommendation": recommendation,
        "patch_hint": _patch_hint_for_exception(exc_type, suspect),
        "confidence": confidence,
        "_source": "heuristic_traceback",
    }


def _heuristic_diagnosis(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Rule-based fallback when no LLM is available."""
    # cwd_exists=false fires before any reason-string parsing — the directory
    # is gone so subprocess calls swallow FileNotFoundError and return
    # misleading pass/skip results.  Skip all string checks and rebuild.
    if snap.get("cwd_exists") is False:
        return {
            "root_cause": (
                "Worktree directory does not exist on disk (cwd_exists=false). "
                "A prior reset's rmtree likely failed on Windows file locks, "
                "leaving git state stale. Validation subprocess calls swallow "
                "FileNotFoundError, causing repeated coherence failures."
            ),
            "suspect_files": [
                "tools/workflow/validated_commit.py:validate_working_tree",
                "tools/workflow/auto_remediate.py:_reset_broken_worktree",
            ],
            "recommendation": "rebuild_worktree",
            "patch_hint": (
                "Check cwd_exists before any git/file ops; if false, "
                "return FAILURE_WORKTREE_MISSING immediately so remediate() can "
                "prune git state and let the next dispatch create a clean worktree."
            ),
            "confidence": 0.92,
            "_source": "heuristic",
        }

    reason = (snap.get("reason") or "").lower()

    if snap.get("is_worktree_path") and not snap.get("worktree_registered"):
        return {
            "root_cause": "Worktree dir exists but is not registered with git — orphan left by a failed `git worktree remove`.",
            "suspect_files": ["tools/genesis/reflexes/kanban.py:_create_worktree"],
            "recommendation": "rebuild_worktree",
            "patch_hint": "Validate via `git worktree list` before reusing an existing path.",
            "confidence": 0.85,
            "_source": "heuristic",
        }
    if "main passes, cwd fails" in reason or "not in worktree" in reason:
        return {
            "root_cause": "Verifier ran in a worktree missing essential files (structural break, not task-level).",
            "suspect_files": ["tools/workflow/validated_commit.py:_run_coherence"],
            "recommendation": "rebuild_worktree",
            "patch_hint": "Treat missing worktree files as structural, not task failure.",
            "confidence": 0.70,
            "_source": "heuristic",
        }
    if "timeout" in reason and "exceeded dispatch budget" in reason:
        return {
            "root_cause": "Task scope exceeds MAX_EXECUTION_SECONDS (900s). Either contains too many sequential heavyweight commands or is genuinely long-running.",
            "suspect_files": ["tools/genesis/reflexes/kanban.py:MAX_EXECUTION_SECONDS"],
            "recommendation": "quarantine",
            "patch_hint": "Decompose into per-command sub-tasks, raise the budget for this task_type, or run checks in parallel.",
            "confidence": 0.75,
            "_source": "heuristic",
        }
    if "phantom" in reason:
        return {
            "root_cause": "Agent claimed output but produced no real file changes.",
            "suspect_files": [],
            "recommendation": "quarantine",
            "patch_hint": "",
            "confidence": 0.60,
            "_source": "heuristic",
        }
    # Generic deterministic localization: when a Python traceback is present and
    # points at a first-party file, return a real diagnosis instead of the
    # conf-0.30 "manual review" floor. Runs AFTER the 5 infra rules above.
    tb_diag = _traceback_diagnosis(snap)
    if tb_diag is not None:
        return tb_diag

    return {
        "root_cause": "Unknown recurring failure; manual review required.",
        "suspect_files": [],
        "recommendation": "quarantine",
        "patch_hint": "",
        "confidence": 0.30,
        "_source": "heuristic",
    }


# ---------------------------------------------------------------------------
# Act — quarantine source task, create diagnostic card, notify
# ---------------------------------------------------------------------------

def _create_diagnostic_card(source_task_id: str, reason: str,
                            snap: Dict[str, Any], diag: Dict[str, Any]) -> Optional[str]:
    """Insert a new kanban card with the RCA evidence. Returns new task_id."""
    try:
        from tools.db.storage import get_connection
    except Exception as exc:
        logger.warning("self_debug: storage import failed: %s", exc)
        return None

    new_id = f"diag-{uuid.uuid4().hex[:10]}"
    title = f"Oracle RCA: {source_task_id} stuck in loop"
    body = (
        f"AUTO-CREATED by self_debug reflex.\n\n"
        f"## Source task\n{source_task_id}\n\n"
        f"## Signature recurrence\n{reason[:400]}\n\n"
        f"## LLM diagnosis ({diag.get('_source', '?')}, conf={diag.get('confidence', 0)})\n"
        f"- Root cause: {diag.get('root_cause', '?')}\n"
        f"- Recommendation: {diag.get('recommendation', '?')}\n"
        f"- Patch hint: {diag.get('patch_hint', '—')}\n"
        f"- Suspect files:\n" + "".join(f"  - {f}\n" for f in diag.get("suspect_files", []) or ["(none)"]) +
        f"\n## Evidence snapshot\n```json\n{json.dumps(snap, indent=2, default=str)[:3500]}\n```\n"
    )
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection() as conn:
            # status='suggested' — visible on the kanban board for human /
            # Oracle review, but NOT auto-dispatched. Prevents the scheduler
            # from trying to "run" an RCA card as a code task (which would
            # fail with "no commits" since there's no implementation to do).
            conn.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                " executor_type, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (new_id, title, body, "chore", "critical",
                 "suggested", "claude_cli", now, now),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("self_debug: failed to insert diagnostic card: %s", exc)
        return None
    return new_id


QUARANTINE_PREFIX = "QUARANTINED by self_debug"


def _quarantine_task(task_id: str, diag_id: Optional[str], diag: Dict[str, Any]) -> None:
    """Move the looping task to status='suggested' so the scheduler stops
    dispatching it. Also annotates last_failure_reason so operators can see
    why.

    'suggested' is chosen because: (a) it's in the existing status CHECK
    constraint — no schema change; (b) the scheduler's get_due_tasks
    query only picks up 'backlog' + 'scheduled', so 'suggested' tasks
    stay visible on the board but aren't auto-dispatched; (c) unlike
    annotation-only quarantines, it survives the state-machine paths
    that reset failure_count / last_failure_reason.
    """
    try:
        from tools.db.storage import get_connection
    except Exception:
        return
    annotation = (
        f"{QUARANTINE_PREFIX} — recurring failure. "
        f"Root cause: {diag.get('root_cause', '?')} "
        f"See diagnosis card {diag_id or '(none)'}"
    )
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE kanban_tasks SET status = %s, "
                "last_failure_reason = %s, updated_at = %s WHERE id = %s",
                ("suggested", annotation, now, task_id),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("self_debug: failed to quarantine %s: %s", task_id, exc)


def _persist_lesson(task_id: str, reason: str, diag: Dict[str, Any]) -> Optional[int]:
    """Write a one-paragraph lesson to the memory DB so future sessions know
    about this recurrence pattern without re-discovering it.

    Stored as type='insight'. Dedup is automatic (SHA-256 on content) — if
    the same lesson has already been recorded, this is a no-op.
    """
    try:
        from tools.memory.memory_write import write_to_db
    except Exception as exc:
        logger.warning("self_debug: memory_write import failed: %s", exc)
        return None
    sig = failure_signature(reason)
    suspects = ", ".join(diag.get("suspect_files") or []) or "(none identified)"
    lesson = (
        f"[self_debug lesson, sig={sig}] Task {task_id} hit a recurring "
        f"failure. Root cause: {diag.get('root_cause', '?')} "
        f"Recommendation: {diag.get('recommendation', '?')}. "
        f"Suspect code: {suspects}. "
        f"Patch hint: {diag.get('patch_hint', '')}. "
        f"Normalized signature above can be matched against future failures "
        f"to recognize the same structural pattern."
    )
    try:
        result = write_to_db(
            content=lesson,
            entry_type="insight",
            importance=8,
            source="auto",
        )
        if result["status"] == "duplicate_merged":
            logger.info("self_debug: lesson already recorded (entry %s)", result["id"])
        else:
            logger.info("self_debug: persisted lesson as memory entry %s", result["id"])
        return result["id"]
    except Exception as exc:
        logger.warning("self_debug: failed to persist lesson: %s", exc)
        return None


def _notify(task_id: str, diag_id: Optional[str], diag: Dict[str, Any]) -> None:
    try:
        from tools.notifications.adapters.telegram import send
        send(
            title=f"Kanban loop → RCA: {task_id}",
            message=f"{diag.get('root_cause', '?')[:160]} (card {diag_id})",
            severity="warning",
        )
    except Exception:
        pass  # notifier optional


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_and_diagnose(task_id: str, reason: str, cwd: str,
                       threshold: int = RECURRENCE_THRESHOLD) -> Optional[Dict[str, Any]]:
    """Record a failure; if the signature recurs ≥ threshold, run RCA.

    Returns the diagnosis dict if triggered (and side-effects fired),
    or None if below threshold.
    """
    # Guard: synthetic / test-fixture ids (e.g. the ``task-real`` fixture) reach
    # this reflex through _verify_task_completed but are never seeded on the
    # board. Diagnosing them spawns phantom Oracle RCA cards in an endless loop
    # (root cause of diag-9312c5edb5). If there is no kanban_tasks row, there is
    # no real loop to break — skip before recording a signature so no history is
    # written to .tmp/kanban/ either.
    if not _task_exists(task_id):
        logger.info(
            "self_debug: %s has no kanban_tasks row — skipping diagnosis "
            "(synthetic/test id, not a real board task)",
            task_id,
        )
        return None

    # Guard: if the task already self-reported done, the worktree was cleaned up
    # by the completion path and any subsequent coherence/verification failure is
    # a false positive.  Never quarantine or create diagnostic cards for a done task.
    if _task_is_done(task_id):
        logger.info(
            "self_debug: %s is already done — skipping failure recording and diagnosis",
            task_id,
        )
        return None

    count = record_failure(task_id, reason)
    if count < threshold:
        logger.debug("self_debug: %s sig count=%d < %d, no action",
                     task_id, count, threshold)
        return None
    logger.warning("self_debug: %s signature recurred %dx — diagnosing", task_id, count)
    snap = snapshot(task_id, cwd, reason)
    snap["signature_count"] = count
    diag = diagnose(snap)
    diag_id = _create_diagnostic_card(task_id, reason, snap, diag)
    _quarantine_task(task_id, diag_id, diag)
    lesson_id = _persist_lesson(task_id, reason, diag)
    # ── LESSONS LEARNED: self-debug quarantine ─────────────────────────────
    try:
        from tools.workflow.lesson_learned import analyze_task, write_lesson
        lesson = analyze_task(task_id, outcome="quarantined")
        write_lesson(lesson)
    except Exception as _ll_exc:
        logger.warning("lesson_learned hook failed: %s", _ll_exc)
    _notify(task_id, diag_id, diag)
    diag["lesson_entry_id"] = lesson_id
    # Clear history so a future re-queue starts fresh (after human/Oracle acts)
    try:
        _history_path(task_id).unlink(missing_ok=True)
    except Exception:
        pass
    diag["diagnosis_card_id"] = diag_id
    return diag


if __name__ == "__main__":
    import argparse, sys  # noqa: E401
    ap = argparse.ArgumentParser(description="Self-debug reflex (manual trigger)")
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--cwd", default=str(BASE_DIR))
    ap.add_argument("--threshold", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = check_and_diagnose(args.task_id, args.reason, args.cwd, args.threshold)
    if args.json:
        print(json.dumps(out or {"skipped": True}, indent=2, default=str))
    else:
        print(out or "below threshold — nothing to do")
    sys.exit(0 if out else 0)
