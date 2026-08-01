# CUI // SP-CTI
"""Conformance Review gate — "did we build what we were told to build?"

Distinct from V&V (does it work): this compares a kanban task's IMPLEMENTATION
(the branch diff) against its stated REQUIREMENT (title + description +
acceptance_criteria) and flags scope drift / missing requirements. Governed
Delivery Pipeline Phase 2.

Design: an LLM judge (reuses the existing acceptance-judge pattern) routed via
the ``conformance_review`` function in args/llm_config.yaml (local/qwen3 first
for air-gap, small cloud judges as fallback — never a hardcoded model id).

DEGRADES GRACEFULLY: no acceptance criteria, no diff, LLM unavailable, or any
parse/exception → status "not_run" (never raises, never blocks completion on
its own — enforcement lives in the runner behind KANBAN_PIPELINE_ENFORCE).

Primary entry point:
    review_conformance(task_id, *, changed_files=None, timeout=60, llm_caller=None) -> dict
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIFF_CHAR_CAP = 8000
_GIT_TIMEOUT = 15


def _default_branch() -> str:
    """Best-effort default branch name; falls back to 'main'."""
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=5,
        )
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip().split("/")[-1]
    except Exception:
        pass
    return "main"


def _branch_diff(task_id: str) -> str:
    """Truncated unified diff of kanban/<task_id> vs the default branch.

    Returns '' if the branch/diff is unavailable (fail-open — the caller then
    falls back to the changed-file list or records not_run)."""
    branch = f"kanban/{task_id}"
    base = _default_branch()
    try:
        p = subprocess.run(
            ["git", "diff", f"{base}...{branch}"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT,
        )
        if p.returncode != 0:
            return ""
        diff = p.stdout or ""
        if len(diff) > _DIFF_CHAR_CAP:
            diff = diff[:_DIFF_CHAR_CAP] + "\n... [diff truncated] ..."
        return diff
    except Exception as exc:
        logger.debug("conformance: branch diff unavailable for %s: %s", task_id, exc)
        return ""


def _load_task(task_id: str) -> Optional[Dict[str, Any]]:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT title, description, acceptance_criteria "
                "FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("conformance: task load failed for %s: %s", task_id, exc)
        return None


def _default_llm_caller(prompt: str, timeout: int) -> Optional[str]:
    """Real LLM path — routed via the 'conformance_review' function (no hardcoded
    model id). Returns raw text, or None if unavailable."""
    from tools.llm.router import LLMRouter
    from tools.llm.provider import LLMRequest

    req = LLMRequest(
        system_prompt="You are a strict software conformance reviewer. Return valid JSON only.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.0,
        effort="low",
        skip_injection_scan=True,   # trusted internal pipeline call
        classification="CUI",
    )
    resp = LLMRouter().invoke("conformance_review", req)
    return resp.content if (resp and resp.content) else None


def _build_prompt(title: str, description: str, criteria: str, changes: str) -> str:
    return (
        "Judge whether the IMPLEMENTATION conforms to what the task ASKED FOR. "
        "This is a requirements-conformance review (did we build the RIGHT thing), "
        "NOT a correctness/quality review. Flag scope drift, missing acceptance "
        "criteria, or building something different than requested.\n\n"
        f"TASK TITLE:\n{title}\n\n"
        f"TASK DESCRIPTION:\n{(description or '')[:3000]}\n\n"
        f"ACCEPTANCE CRITERIA:\n{criteria}\n\n"
        f"IMPLEMENTATION (branch diff / changed files):\n{changes[:_DIFF_CHAR_CAP]}\n\n"
        'Return ONLY valid JSON: '
        '{"pass": true/false, "gap_findings": [{"criterion": "...", "met": true/false, "note": "..."}]}'
    )


def _parse_verdict(raw: str) -> Optional[Dict[str, Any]]:
    try:
        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.MULTILINE
        ).strip()
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            return None
        findings = data.get("gap_findings") or []
        norm = [
            {
                "criterion": str(f.get("criterion", ""))[:300],
                "met": bool(f.get("met", True)),
                "note": str(f.get("note", ""))[:300],
            }
            for f in findings if isinstance(f, dict)
        ]
        return {"pass": bool(data.get("pass", True)), "findings": norm}
    except Exception:
        return None


def review_conformance(
    task_id: str,
    *,
    changed_files: Optional[List[str]] = None,
    timeout: int = 60,
    llm_caller: Optional[Callable[[str, int], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Return {status, review_passed, findings, reason}.

    status ∈ {"pass", "fail", "not_run"}. Never raises.
    """
    result = {"status": "not_run", "review_passed": None, "findings": [], "reason": ""}

    task = _load_task(task_id)
    if not task:
        result["reason"] = "task not found"
        return result
    criteria = (task.get("acceptance_criteria") or "").strip()
    if not criteria:
        result["reason"] = "no acceptance criteria — nothing to verify against"
        return result

    # Prefer the real diff; fall back to the changed-file list; else can't judge.
    changes = _branch_diff(task_id)
    if not changes and changed_files:
        changes = "Changed files:\n" + "\n".join(changed_files[:200])
    if not changes:
        result["reason"] = "no diff / changed files available — cannot verify conformance"
        return result

    caller = llm_caller or _default_llm_caller
    try:
        raw = caller(
            _build_prompt(
                task.get("title", ""), task.get("description", ""), criteria, changes
            ),
            timeout,
        )
    except Exception as exc:
        result["reason"] = f"reviewer llm error: {exc}"
        return result
    if not raw:
        result["reason"] = "reviewer llm unavailable"
        return result

    verdict = _parse_verdict(raw)
    if verdict is None:
        result["reason"] = "reviewer returned unparseable output"
        return result

    result["review_passed"] = verdict["pass"]
    result["findings"] = verdict["findings"]
    result["status"] = "pass" if verdict["pass"] else "fail"
    unmet = [f for f in verdict["findings"] if not f.get("met")]
    result["reason"] = (
        "conforms to acceptance criteria" if verdict["pass"]
        else f"{len(unmet)} unmet/drifted criteria"
    )
    return result
