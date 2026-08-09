#!/usr/bin/env python3
# CUI // SP-CTI
"""Oracle Triage Reflex — auto-triage Oracle-suggested kanban tasks.

For each ``status='suggested'`` task with an ``oracle_lens``, applies a
deterministic verifier to decide:

  promote → move to 'backlog'  (real gap confirmed by file check / grep)
  dismiss → move to 'done'     (false positive, bypasses guard-22 with audit reason)
  skip    → leave as suggested (ambiguous; requires human judgment)

Lens-specific verifiers
-----------------------
tool_not_in_manifest
    Path.exists() on the subject file path.  File exists → promote (real but
    unregistered tool).  File DNE → dismiss (Oracle found a reference to a
    planned/deleted file that was never created).

route_not_listed
    Grep app.py and blueprint files for ``@*route(*subject*)`` decorator.
    Route found in Flask code → promote (real route missing from start.md).
    Route absent → dismiss (in seed plans / manifest docs only, not built).

orphan_db_table
    Grep ``migrations/`` for ``CREATE TABLE … <table>``.  If found → dismiss
    (migration exists, Oracle is wrong).  If absent, count code refs in
    ``tools/``.  refs ≥ 1 → promote (active code, missing migration).
    refs = 0 → dismiss (dead reference, no real gap).

No-lens heuristics (tasks created by self_debug / V&V scripts)
    ``Oracle RCA:`` prefix or ``diag-`` id prefix → promote.
    ``V&V`` anywhere in title → promote.
    ``[FR]`` prefix → backlog (feature request needs human scoping).
    Everything else → skip.

Batch cards (``[Batch] <lens>:`` prefix)
    Parse subjects from description, apply per-subject verifier, take the
    majority vote.  All dismiss → dismiss.  Any promote → promote.

CLI
---
    python tools/genesis/reflexes/oracle_triage.py --run --json
    python tools/genesis/reflexes/oracle_triage.py --run --dry-run --json

Genesis daemon
--------------
    Called as ``module.run(config, trust)`` returning ``{"success": bool, ...}``.
    Registered under the ``awareness`` cycle (runs every 3 h alongside
    gap detection so fresh Oracle cards are triaged in the same cycle).
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Sentinel used by the NLP subject cache to distinguish "not yet cached" from
# "cached as None" (i.e. extraction failed).
_NLP_CACHE_MISS = object()

# Per-process result cache for _nlp_extract_subject keyed by (title, lens).
# Avoids repeated LLM calls for the same title/lens pair within a triage run.
_NLP_SUBJECT_CACHE: Dict[Tuple[str, str], Optional[str]] = {}

# Per-process cache for _nlp_extract_batch_lens keyed by title.
_NLP_BATCH_LENS_CACHE: Dict[str, Optional[str]] = {}

# Per-process cache for _nlp_extract_batch_subjects keyed by (lens, description[:600]).
_NLP_BATCH_SUBJECTS_CACHE: Dict[Tuple[str, str], List[str]] = {}

# Env var that controls which model the NLP extractor uses.
# Defaults to claude-haiku-4-5 for low latency / low cost extraction tasks.
_ORACLE_NLP_MODEL_ENV = "ICDEV_ORACLE_NLP_MODEL"
_ORACLE_NLP_MODEL_DEFAULT = "claude-haiku-4-5-20251001"


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
LOG = get_logger("oracle_triage")

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

try:
    from tools.genesis.harness.llm_triage import llm_triage_task as _llm_triage_task
    from tools.genesis.harness.eval_harness import record_decision as _harness_record_decision
except ImportError:
    _llm_triage_task = None  # type: ignore[assignment]
    _harness_record_decision = None  # type: ignore[assignment]

# Dynamic heuristics loaded once per process from args/oracle_heuristics.yaml
_DYNAMIC_HEURISTICS: list[dict] | None = None

# Triage config loaded once per process from args/oracle_triage_config.yaml
_TRIAGE_CONFIG: dict | None = None


def _load_dynamic_heuristics() -> list[dict]:
    global _DYNAMIC_HEURISTICS
    if _DYNAMIC_HEURISTICS is not None:
        return _DYNAMIC_HEURISTICS
    try:
        import yaml
        heuristics_path = BASE_DIR / "args" / "oracle_heuristics.yaml"
        if heuristics_path.exists():
            data = yaml.safe_load(heuristics_path.read_text(encoding="utf-8"))
            _DYNAMIC_HEURISTICS = data.get("heuristics", []) if isinstance(data, dict) else []
        else:
            _DYNAMIC_HEURISTICS = []
    except Exception as exc:
        LOG.warning("[oracle_triage] failed to load oracle_heuristics.yaml: %s", exc)
        _DYNAMIC_HEURISTICS = []
    return _DYNAMIC_HEURISTICS


def _load_triage_config() -> dict:
    """Load oracle_triage_config.yaml once per process; return defaults on failure."""
    global _TRIAGE_CONFIG
    if _TRIAGE_CONFIG is not None:
        return _TRIAGE_CONFIG
    defaults: dict = {
        "orphan_min_refs": 1,
        "anomaly_detection": {
            "low_confidence_threshold": 0.3,
            "orphan_min_refs_low_confidence": 3,
            "high_confidence_threshold": 0.85,
        },
    }
    try:
        import yaml
        config_path = BASE_DIR / "args" / "oracle_triage_config.yaml"
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _TRIAGE_CONFIG = {**defaults, **data}
                ad_defaults = defaults["anomaly_detection"]
                ad_loaded = data.get("anomaly_detection", {}) or {}
                _TRIAGE_CONFIG["anomaly_detection"] = {**ad_defaults, **ad_loaded}
                return _TRIAGE_CONFIG
    except Exception as exc:
        LOG.warning("[oracle_triage] failed to load oracle_triage_config.yaml: %s", exc)
    _TRIAGE_CONFIG = defaults
    return _TRIAGE_CONFIG


def _apply_dynamic_heuristics(task: dict) -> tuple[str, str] | None:
    """Check task against dynamic heuristics from args/oracle_heuristics.yaml.

    Returns (action, reason) on first match, or None if no heuristic matched.
    """
    title: str = task.get("title", "")
    title_lower = title.lower()
    task_id: str = task.get("id", "")

    for h in _load_dynamic_heuristics():
        action = h.get("action", "skip")
        reason = h.get("reason", h.get("name", "dynamic heuristic"))

        if "match_title_prefix" in h and title.startswith(h["match_title_prefix"]):
            return action, reason
        if "match_title_contains" in h and h["match_title_contains"].lower() in title_lower:
            return action, reason
        if "match_id_prefix" in h and task_id.startswith(h["match_id_prefix"]):
            return action, reason

    return None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = BASE_DIR / "migrations"
_TOOLS_DIR = BASE_DIR / "tools"
_DASHBOARD_DIR = _TOOLS_DIR / "dashboard"
_DEFAULT_API_BASE = "http://localhost:5050"

# oracle_lens values this reflex handles.
_HANDLED_LENSES = {"tool_not_in_manifest", "route_not_listed", "orphan_db_table"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _fetch_suggested_tasks() -> List[Dict[str, Any]]:
    """Return all kanban_tasks with status='suggested', joined to oracle_predictions."""
    if get_connection is None:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT kt.id, kt.title, kt.description, kt.priority, kt.task_type,
                   op.lens_name      AS oracle_lens,
                   op.confidence     AS oracle_confidence,
                   op.prediction_type AS oracle_prediction_type
            FROM kanban_tasks kt
            LEFT JOIN oracle_predictions op ON kt.source_prediction_id = op.id
            WHERE kt.status = 'suggested'
            ORDER BY op.confidence DESC NULLS LAST, kt.created_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Kanban move via local REST API
# ---------------------------------------------------------------------------


def _api_move(
    task_id: str,
    new_status: str,
    api_base: str,
    dry_run: bool,
    bypass_reason: Optional[str] = None,
) -> Tuple[bool, str]:
    """POST /api/kanban/tasks/<id>/move.

    Returns (success, message).
    If bypass_reason is set, adds bypass_verification=true to the payload
    (required by guard-22 for moves to 'done' without a verification row).
    """
    if dry_run:
        return True, f"[dry-run] would move {task_id} → {new_status}"

    payload: Dict[str, Any] = {"status": new_status}
    if bypass_reason:
        payload["bypass_verification"] = True
        payload["bypass_reason"] = bypass_reason

    body = json.dumps(payload).encode()
    url = f"{api_base}/api/kanban/tasks/{task_id}/move"
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return True, result.get("status", new_status)
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        try:
            err = json.loads(body_bytes).get("error", str(body_bytes[:120]))
        except Exception:
            err = str(body_bytes[:120])
        return False, f"HTTP {exc.code}: {err}"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Verifier: tool_not_in_manifest
# ---------------------------------------------------------------------------


def _verify_tool_not_in_manifest(subject: str) -> Tuple[str, str]:
    """Check whether the subject file path exists on disk.

    subject  — e.g. "tools/ttx/engine.py"

    Returns (action, reason):
      "promote", "File exists — real tool missing manifest entry"
      "dismiss", "File does not exist — Oracle false positive (planned/deleted file)"
    """
    path = BASE_DIR / subject.replace("\\", "/")
    if path.exists():
        return "promote", f"File exists ({path.stat().st_size} bytes) — needs manifest entry"
    return "dismiss", f"File does not exist at {subject} — Oracle false positive (planned/deleted)"


# ---------------------------------------------------------------------------
# Verifier: route_not_listed
# ---------------------------------------------------------------------------

_ROUTE_DECORATOR_RE = re.compile(
    r"""@\w+\.route\(\s*["']([^"']+)["']""",
    re.MULTILINE,
)


def _collect_flask_routes() -> set:
    """Return set of all route paths defined in app.py and blueprint files."""
    routes: set = set()
    for py_file in [_DASHBOARD_DIR / "app.py"] + list(_DASHBOARD_DIR.glob("*.py")):
        if not py_file.exists():
            continue
        try:
            src = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _ROUTE_DECORATOR_RE.finditer(src):
            routes.add(m.group(1).split("<")[0].rstrip("/") or "/")
    return routes


_flask_routes_cache: Optional[set] = None


def _verify_route_not_listed(subject: str) -> Tuple[str, str]:
    """Verify whether the route actually exists as a Flask decorator.

    subject — e.g. "/studio/narrate"

    The gap_detector fires when a Flask route is missing from start.md.
    If the route ISN'T in Flask code at all, the Oracle found a planning
    reference (seed script, manifest doc) — dismiss as a false positive.
    If the route IS in Flask code, the gap is real — promote.
    """
    global _flask_routes_cache
    if _flask_routes_cache is None:
        _flask_routes_cache = _collect_flask_routes()

    # Normalise the subject: strip trailing slash, collapse <param> segments
    needle = re.sub(r"<[^>]+>", "<param>", subject.rstrip("/")) or "/"

    # Exact match
    if needle in _flask_routes_cache:
        return "promote", f"Route {subject} exists in Flask code — add to start.md"

    # Prefix match (route may have <param> suffix in app.py)
    base = needle.split("<")[0].rstrip("/")
    for r in _flask_routes_cache:
        if r == base or r.startswith(base + "/") or r.startswith(base + "<"):
            return "promote", f"Route {subject} matched by Flask route {r!r} — add to start.md"

    return "dismiss", (
        f"Route {subject} has no Flask @route decorator in dashboard — "
        "only referenced in seed plans/docs (not yet built)"
    )


# ---------------------------------------------------------------------------
# Verifier: orphan_db_table
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(\w+)",
    re.IGNORECASE,
)


def _migration_has_create(table: str) -> bool:
    """Return True if any migration file defines CREATE TABLE <table>."""
    if not _MIGRATIONS_DIR.exists():
        return False
    for f in _MIGRATIONS_DIR.rglob("*"):
        if f.suffix not in {".sql", ".py"}:
            continue
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _CREATE_TABLE_RE.finditer(src):
            if m.group(1).lower() == table.lower():
                return True
    return False


def _count_code_refs(table: str) -> int:
    """Count how many lines in tools/ Python files reference <table>."""
    pattern = re.compile(r"\b" + re.escape(table) + r"\b", re.IGNORECASE)
    count = 0
    for py_file in _TOOLS_DIR.rglob("*.py"):
        if any(p in py_file.parts for p in {".git", ".tmp", "__pycache__"}):
            continue
        try:
            src = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        count += len(pattern.findall(src))
    return count


def _get_active_anomaly_thresholds() -> Dict[str, Any]:
    """Return the anomaly-detection thresholds from static config.

    There used to be an "adaptive" path here that recomputed the high/low
    confidence bands as mean ± 1 std over the recent oracle_predictions
    confidence values. It was removed because the statistic is meaningless: in
    the awareness lens `confidence` is not a per-prediction estimate, it is a
    per-rule constant copied out of awareness_config.yaml (gap::route_no_e2e is
    always 0.70, gap::orphan_db_table always 0.85, and so on). So the "history"
    it averaged was a bag of ~7 constants weighted by how often each rule
    happened to fire — a popularity contest among unrelated rules, not a
    distribution. Live, it drove low_confidence_threshold from 0.30 up to 0.825
    purely because high-confidence rules had fired a lot recently, which would
    have set gap::route_no_e2e's evidence bar from the firing rate of
    gap::tool_not_in_manifest.

    It also never actually changed an outcome. Its only consumer is
    `_verify_orphan_db_table`, which only ever sees orphan_db_table predictions
    at confidence 0.85; both adaptive and static clamp high_threshold to exactly
    0.85, so 0.85 >= high_threshold returns 0 (skip the refs check) in either
    mode and the inflated low bar is never read. Removing it is behaviour-
    preserving cleanup.

    A real per-rule evidence bar wants observed OUTCOME accuracy, not claimed
    confidence — that signal now lives in
    tools/genesis/harness/calibration_report.py and is the correct future input.
    """
    return dict(_load_triage_config().get("anomaly_detection", {}))


def _get_orphan_min_refs(oracle_confidence: Optional[float] = None) -> int:
    """Return the minimum code-reference count for the orphan_db_table verifier.

    Applies anomaly detection: low-confidence oracle predictions require more
    code references before a gap is considered real (reduces false promotions
    from noisy / uncertain predictions).  High-confidence predictions bypass
    the refs check entirely.

    Args:
        oracle_confidence: oracle prediction confidence in [0, 1], or None.

    Returns:
        Required min refs, or 0 when confidence is high enough to skip the check.
    """
    cfg = _load_triage_config()
    base_min_refs: int = int(cfg.get("orphan_min_refs", 1))

    if oracle_confidence is not None:
        ad = _get_active_anomaly_thresholds()
        high_threshold: float = float(ad.get("high_confidence_threshold", 0.85))
        low_threshold: float = float(ad.get("low_confidence_threshold", 0.3))
        if oracle_confidence >= high_threshold:
            # High confidence — trust Oracle, skip the refs check
            return 0
        if oracle_confidence < low_threshold:
            # Low confidence — anomalous prediction, require more evidence
            return int(ad.get("orphan_min_refs_low_confidence", 3))

    return base_min_refs


def _verify_orphan_db_table(
    table: str,
    oracle_confidence: Optional[float] = None,
) -> Tuple[str, str]:
    """Verify an orphan table gap.

    If a CREATE TABLE migration already exists → dismiss (gap_detector missed it).
    If no migration and code refs >= min_refs threshold → promote (real gap).
    If no migration and refs < min_refs → dismiss (dead/weak reference).

    The min_refs threshold is read from args/oracle_triage_config.yaml and
    adjusted by anomaly detection: low-confidence oracle predictions require
    more code references; high-confidence predictions skip the check entirely.
    """
    if _migration_has_create(table):
        return "dismiss", f"CREATE TABLE {table} found in migrations — gap_detector false positive"

    min_refs = _get_orphan_min_refs(oracle_confidence)
    refs = _count_code_refs(table)

    if min_refs == 0:
        # High-confidence oracle — trust the prediction even with few refs
        return "promote", (
            "No CREATE TABLE migration; Oracle high-confidence — write migration"
            + (f" ({refs} code ref(s))" if refs else " (0 code refs)")
        )

    if refs >= min_refs:
        return "promote", f"No CREATE TABLE migration; {refs} code reference(s) — write migration"

    return "dismiss", (
        f"No CREATE TABLE migration and {refs} code reference(s) "
        f"(< min_refs={min_refs}) — dead/weak reference, dismiss"
    )


# ---------------------------------------------------------------------------
# No-lens heuristics
# ---------------------------------------------------------------------------


def _verify_no_lens(task: Dict[str, Any]) -> Tuple[str, str]:
    """Heuristic triage for tasks that the Oracle created without a gap lens.

    Checks dynamic heuristics from args/oracle_heuristics.yaml first (includes
    baseline rules + any LLM-proposed amendments), then falls back to the LLM
    fallback for cases nothing matched.
    """
    match = _apply_dynamic_heuristics(task)
    if match is not None:
        return match

    if _llm_triage_task is not None:
        _a, _r, _c = _llm_triage_task(task)
        if _a != "skip":
            return _a, _r
    return "skip", "No heuristic matched — leaving for human judgment"


# ---------------------------------------------------------------------------
# Batch card helper
# ---------------------------------------------------------------------------


def _parse_batch_subjects(description: str) -> List[str]:
    """Extract the subjects list from a [Batch] card description."""
    subjects: List[str] = []
    in_subjects = False
    for line in description.splitlines():
        stripped = line.strip()
        if stripped == "Subjects:":
            in_subjects = True
            continue
        if in_subjects:
            if stripped.startswith("- "):
                subjects.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("-"):
                break  # end of subjects block
    return subjects


def _verify_batch(task: Dict[str, Any]) -> Tuple[str, str]:
    """For [Batch] cards, derive the lens from the title and apply
    per-subject verification.  Majority rules: any promote → promote.
    """
    title: str = task.get("title", "")
    desc: str = task.get("description", "") or ""

    # Detect lens from title: "[Batch] <lens>: N gap findings"
    lens_match = re.match(r"\[Batch\]\s+(\w+):", title)
    if not lens_match:
        nlp_lens = _nlp_extract_batch_lens(title)
        if not nlp_lens:
            return "skip", "Batch card with unrecognised lens format"
        lens = nlp_lens
    else:
        lens = lens_match.group(1)

    subjects = _parse_batch_subjects(desc)
    if not subjects:
        subjects = _nlp_extract_batch_subjects(lens, desc)
    if not subjects:
        return "skip", "Batch card — could not parse subjects from description"

    batch_confidence: Optional[float] = task.get("oracle_confidence")
    promotions = 0
    dismissals = 0
    reasons: List[str] = []
    for subj in subjects:
        if lens == "tool_not_in_manifest":
            action, reason = _verify_tool_not_in_manifest(subj)
        elif lens == "route_not_listed":
            action, reason = _verify_route_not_listed(subj)
        elif lens == "orphan_db_table":
            action, reason = _verify_orphan_db_table(subj, oracle_confidence=batch_confidence)
        else:
            action, reason = "skip", f"Unknown lens {lens!r}"

        reasons.append(f"{subj}: {action} — {reason}")
        if action == "promote":
            promotions += 1
        elif action == "dismiss":
            dismissals += 1

    summary = f"{promotions} promote / {dismissals} dismiss / {len(subjects)-promotions-dismissals} skip"
    detail = "; ".join(reasons[:3]) + (f" ... (+{len(reasons)-3} more)" if len(reasons) > 3 else "")

    if promotions > 0:
        return "promote", f"Batch: {summary}. {detail}"
    if dismissals == len(subjects):
        return "dismiss", f"Batch: all {dismissals} subjects are false positives. {detail}"
    return "skip", f"Batch: {summary}. {detail}"


# ---------------------------------------------------------------------------
# NLP subject extractor (fallback for non-standard task titles)
# ---------------------------------------------------------------------------

# Canonical prefixes the gap_detector stamps on titles for each lens.
_LENS_PREFIX_RE: Dict[str, re.Pattern] = {
    "tool_not_in_manifest": re.compile(r"^tool_not_in_manifest gap:\s*"),
    "route_not_listed": re.compile(r"^route_not_listed gap:\s*"),
    "orphan_db_table": re.compile(r"^orphan_db_table gap:\s*"),
}

_LENS_SUBJECT_HINTS: Dict[str, str] = {
    "tool_not_in_manifest": "a file path (e.g. tools/foo/bar.py)",
    "route_not_listed": "a URL route path (e.g. /api/foo or /canvas/page)",
    "orphan_db_table": "a database table name (e.g. foo_events)",
}

# Per-lens validators — extracted subjects must match to be accepted.
_LENS_VALIDATORS: Dict[str, re.Pattern] = {
    "tool_not_in_manifest": re.compile(
        r"^[\w/\\.-]+\.(py|yaml|yml|json|md|sh|txt|toml|cfg|ini)$"
    ),
    "route_not_listed": re.compile(r"^/[\w/.<>_-]*$"),
    "orphan_db_table": re.compile(r"^\w{2,64}$"),
}

# Lens-specific few-shot examples for the LLM extractor.
_LENS_EXTRACT_EXAMPLES: Dict[str, str] = {
    "tool_not_in_manifest": (
        "Examples:\n"
        "  'tool_not_in_manifest gap: tools/ttx/engine.py' → tools/ttx/engine.py\n"
        "  'Missing manifest entry for tools/audit/scanner.py' → tools/audit/scanner.py\n"
        "  'New tool at icdev/tools/reporting/pdf.py not registered' → icdev/tools/reporting/pdf.py"
    ),
    "route_not_listed": (
        "Examples:\n"
        "  'route_not_listed gap: /api/agents/status' → /api/agents/status\n"
        "  'Flask route /studio/narrate missing from start.md' → /studio/narrate\n"
        "  'Undocumented route /canvas/zig/report' → /canvas/zig/report"
    ),
    "orphan_db_table": (
        "Examples:\n"
        "  'orphan_db_table gap: harness_eval' → harness_eval\n"
        "  'Table oracle_predictions has no migration' → oracle_predictions\n"
        "  'Missing CREATE TABLE for ai_sessions' → ai_sessions"
    ),
}

_NLP_EXTRACTOR_SYSTEM = """\
You are an entity extractor for an automated DevSecOps triage system.
Given a kanban task title and optional context, extract the single specific artifact.

Rules:
- For file paths: return the relative path as-is (e.g., tools/foo/bar.py)
- For URL routes: return the path starting with "/" (e.g., /api/canvas/report)
- For database tables: return just the snake_case table name (e.g., kanban_tasks)
- Return ONLY the extracted value — no explanation, no quotes, no surrounding text
- If you cannot identify a specific artifact, return the empty string
"""

_NLP_BATCH_SUBJECTS_SYSTEM = """\
You are a subject-list extractor for an automated DevSecOps triage system.
Given a batch kanban task description and lens type, extract all artifact subjects.

Rules:
- For "tool_not_in_manifest": extract relative file paths (e.g., tools/foo/bar.py)
- For "route_not_listed": extract URL route paths starting with "/" (e.g., /api/canvas/report)
- For "orphan_db_table": extract snake_case table names (e.g., kanban_tasks)
- Return one subject per line — no bullets, no numbering, no explanation, no blank lines
- If you cannot identify any subjects, return an empty response
"""

_NLP_LENS_INFER_SYSTEM = """\
You are a DevSecOps triage classifier for an automated kanban system.
Given a task title, classify it into the most appropriate gap lens and extract the artifact subject.

Gap lenses:
- tool_not_in_manifest: A Python/config file exists but is not registered in the manifest
- route_not_listed: A Flask URL route exists in code but is missing from documentation
- orphan_db_table: A database table has code references but no CREATE TABLE migration
- none: The task does not clearly match any of the above lenses

Respond with exactly two lines:
Line 1: the lens name (tool_not_in_manifest, route_not_listed, orphan_db_table, or none)
Line 2: the extracted artifact (file path, URL route, or table name), or empty if lens is none

Examples:
  "Missing manifest entry for tools/audit/scanner.py" → tool_not_in_manifest\ntools/audit/scanner.py
  "Flask route /studio/narrate not in start.md" → route_not_listed\n/studio/narrate
  "No migration for oracle_predictions table" → orphan_db_table\noracle_predictions
  "Oracle RCA: config loading error in reflex" → none\n
"""


def _nlp_infer_lens_and_subject(
    title: str, description: str = ""
) -> tuple[Optional[str], Optional[str]]:
    """Use LLM to infer the gap lens and extract the artifact subject for lens-less tasks.

    Gated by ICDEV_ORACLE_NLP_EXTRACTOR=true (same gate as _nlp_extract_subject).
    Returns (lens, subject) when confident; (None, None) if gate is off, LLM
    unavailable, or the inference fails lens-specific validation.
    """
    if os.getenv("ICDEV_ORACLE_NLP_EXTRACTOR", "").lower() not in ("true", "1"):
        return None, None

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        model_id = os.getenv(_ORACLE_NLP_MODEL_ENV, _ORACLE_NLP_MODEL_DEFAULT)
        user_content = f"Task title: {title}"
        if description:
            user_content += f"\nContext: {description[:300]}"

        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_NLP_LENS_INFER_SYSTEM,
            model=model_id,
            max_tokens=64,
            temperature=0.0,
            skip_injection_scan=True,
        )
        router = LLMRouter()
        response = router.invoke("oracle_triage_nlp_infer", request)
        if not response or not response.content:
            return None, None

        lines = [ln.strip() for ln in response.content.strip().splitlines() if ln.strip()]
        if not lines:
            return None, None

        inferred_lens: Optional[str] = lines[0] if lines[0] in _HANDLED_LENSES else None
        inferred_subject: Optional[str] = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None

        if inferred_lens and inferred_subject and _validate_extracted_subject(inferred_subject, inferred_lens):
            return inferred_lens, inferred_subject

        return inferred_lens, None
    except Exception as exc:
        LOG.debug("[oracle_triage] nlp_infer_lens_and_subject failed: %s", exc)
        return None, None


def _nlp_extract_batch_lens(title: str) -> Optional[str]:
    """Use LLM to extract the oracle lens type from a non-standard batch card title.

    Wraps _nlp_infer_lens_and_subject with per-title caching.
    Returns a valid lens name from _HANDLED_LENSES, or None on failure / gate off.
    """
    if title in _NLP_BATCH_LENS_CACHE:
        return _NLP_BATCH_LENS_CACHE[title]

    lens, _ = _nlp_infer_lens_and_subject(title)
    result = lens if (lens and lens in _HANDLED_LENSES) else None
    _NLP_BATCH_LENS_CACHE[title] = result
    return result


def _validate_extracted_subject(subject: str, lens: str) -> bool:
    """Return True if the extracted subject matches expected format for the lens."""
    if not subject or not subject.strip():
        return False
    validator = _LENS_VALIDATORS.get(lens)
    if validator is None:
        return True
    return bool(validator.match(subject.strip()))


def _nlp_extract_subject(title: str, lens: str, description: str = "") -> Optional[str]:
    """Use an LLM to extract the triage subject from a non-standard task title.

    Gated by ICDEV_ORACLE_NLP_EXTRACTOR=true in .env (default off).
    Results are cached per (title, lens) to avoid redundant LLM calls within a
    single triage run.  Model can be overridden via ICDEV_ORACLE_NLP_MODEL.

    Returns the validated extracted subject, or None if gate is off /
    LLM unavailable / result fails lens validation.
    """
    if os.getenv("ICDEV_ORACLE_NLP_EXTRACTOR", "").lower() not in ("true", "1"):
        return None

    cache_key: Tuple[str, str] = (title, lens)
    cached = _NLP_SUBJECT_CACHE.get(cache_key, _NLP_CACHE_MISS)
    if cached is not _NLP_CACHE_MISS:
        return cached  # type: ignore[return-value]

    result: Optional[str] = None
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        hint = _LENS_SUBJECT_HINTS.get(lens, "the relevant artifact name")
        examples = _LENS_EXTRACT_EXAMPLES.get(lens, "")
        user_content = (
            f"Extract {hint} from this task title.\n"
            f"{examples}\n"
            f"Title: {title}"
        )
        if description:
            user_content += f"\nContext: {description[:200]}"

        model_id = os.getenv(_ORACLE_NLP_MODEL_ENV, _ORACLE_NLP_MODEL_DEFAULT)
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_NLP_EXTRACTOR_SYSTEM,
            model=model_id,
            max_tokens=64,
            temperature=0.0,
            skip_injection_scan=True,
        )
        router = LLMRouter()
        response = router.invoke("oracle_triage_nlp_extract", request)
        if response and response.content:
            extracted = response.content.strip().strip('"').strip("'").strip()
            if _validate_extracted_subject(extracted, lens):
                result = extracted
            else:
                LOG.debug(
                    "[oracle_triage] nlp_extract returned invalid subject %r for lens %s",
                    extracted, lens,
                )
    except Exception as exc:
        LOG.debug("[oracle_triage] nlp_extract_subject failed: %s", exc)

    _NLP_SUBJECT_CACHE[cache_key] = result
    return result


def _extract_subject(title: str, lens: str, description: str = "") -> str:
    """Extract the triage subject from a task title.

    Priority:
    1. Canonical prefix regex — fast deterministic path; validates result.
    2. NLP extraction via LLM (gated by ICDEV_ORACLE_NLP_EXTRACTOR=true).
    3. Raw title as last resort.
    """
    prefix_re = _LENS_PREFIX_RE.get(lens)
    if prefix_re:
        m = prefix_re.match(title)
        if m:
            candidate = prefix_re.sub("", title).strip()
            if _validate_extracted_subject(candidate, lens):
                return candidate
            # Regex matched but result fails lens validation — try NLP.
            nlp_result = _nlp_extract_subject(title, lens, description)
            return nlp_result if nlp_result else candidate
    nlp_result = _nlp_extract_subject(title, lens, description)
    return nlp_result if nlp_result else title


def _nlp_extract_batch_subjects(lens: str, description: str) -> List[str]:
    """Use an LLM to extract batch subjects from a non-standard description.

    Gated by ICDEV_ORACLE_NLP_EXTRACTOR=true (same gate as _nlp_extract_subject).
    Results are cached per (lens, description[:600]) to avoid redundant LLM calls.
    Returns a list of validated subjects, or [] if gate is off / LLM unavailable.
    """
    if os.getenv("ICDEV_ORACLE_NLP_EXTRACTOR", "").lower() not in ("true", "1"):
        return []

    cache_key: Tuple[str, str] = (lens, description[:600])
    cached = _NLP_BATCH_SUBJECTS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result: List[str] = []
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        model_id = os.getenv(_ORACLE_NLP_MODEL_ENV, _ORACLE_NLP_MODEL_DEFAULT)
        hint = _LENS_SUBJECT_HINTS.get(lens, "artifact names")
        user_content = (
            f"Lens: {lens}\n"
            f"Extract all {hint} subjects from this batch task description.\n"
            f"Description:\n{description[:600]}"
        )

        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_NLP_BATCH_SUBJECTS_SYSTEM,
            model=model_id,
            max_tokens=256,
            temperature=0.0,
            skip_injection_scan=True,
        )
        router = LLMRouter()
        response = router.invoke("oracle_triage_nlp_extract", request)
        if response and response.content:
            lines = [ln.strip() for ln in response.content.splitlines() if ln.strip()]
            result = [s for s in lines if _validate_extracted_subject(s, lens)]
    except Exception as exc:
        LOG.debug("[oracle_triage] nlp_extract_batch_subjects failed: %s", exc)

    _NLP_BATCH_SUBJECTS_CACHE[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Core triage engine
# ---------------------------------------------------------------------------


def _triage_one(task: Dict[str, Any]) -> Tuple[str, str]:
    """Return (action, reason) for a single suggested task.

    action ∈ {'promote', 'dismiss', 'backlog', 'skip'}
    """
    title: str = task.get("title", "")
    lens: Optional[str] = task.get("oracle_lens")
    desc: str = task.get("description", "") or ""

    # [Batch] cards have a compound title; treat them specially
    if title.startswith("[Batch]"):
        return _verify_batch(task)

    confidence: Optional[float] = task.get("oracle_confidence")

    if lens == "tool_not_in_manifest":
        return _verify_tool_not_in_manifest(_extract_subject(title, lens, desc))

    if lens == "route_not_listed":
        return _verify_route_not_listed(_extract_subject(title, lens, desc))

    if lens == "orphan_db_table":
        return _verify_orphan_db_table(_extract_subject(title, lens, desc), oracle_confidence=confidence)

    if lens is None:
        # NLP lens inference: attempt to classify the title into a known lens before
        # falling back to the heuristic path (dynamic rules → LLM triage).
        inferred_lens, inferred_subject = _nlp_infer_lens_and_subject(title, desc)
        if inferred_lens and inferred_subject:
            LOG.debug(
                "[oracle_triage] NLP inferred lens=%s subject=%s for %s",
                inferred_lens, inferred_subject, task.get("id"),
            )
            if inferred_lens == "tool_not_in_manifest":
                return _verify_tool_not_in_manifest(inferred_subject)
            if inferred_lens == "route_not_listed":
                return _verify_route_not_listed(inferred_subject)
            if inferred_lens == "orphan_db_table":
                return _verify_orphan_db_table(inferred_subject, oracle_confidence=confidence)
        return _verify_no_lens(task)

    # Unknown lens — try LLM fallback before giving up
    if _llm_triage_task is not None:
        _a, _r, _c = _llm_triage_task(task)
        if _a != "skip":
            return _a, _r
    return "skip", f"Unrecognised oracle_lens={lens!r} — no verifier registered"


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------


def _notify(summary: Dict[str, Any], api_base: str, dry_run: bool) -> None:
    """Write a notification row to the notifications table via the dashboard API."""
    if dry_run:
        return
    counts = summary.get("counts", {})
    msg = (
        f"Oracle Triage: {counts.get('promoted', 0)} promoted, "
        f"{counts.get('dismissed', 0)} dismissed, "
        f"{counts.get('skipped', 0)} skipped "
        f"(dry_run={dry_run})"
    )
    try:
        payload = json.dumps({
            "source": "genesis.oracle_triage",
            "title": "Oracle Triage complete",
            "message": msg,
            "severity": "info",
        }).encode()
        req = urllib.request.Request(
            f"{api_base}/api/notifications",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # notification failure is non-fatal


# ---------------------------------------------------------------------------
# Main triage loop
# ---------------------------------------------------------------------------


def triage(
    api_base: str = _DEFAULT_API_BASE,
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Run the triage loop over all suggested tasks.

    Returns a summary dict with counts and per-task decisions.
    """
    global _flask_routes_cache
    _flask_routes_cache = None          # reset per-run caches

    tasks = _fetch_suggested_tasks()
    if not tasks:
        return {"success": True, "counts": {"total": 0}, "decisions": []}

    decisions: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {"total": len(tasks), "promoted": 0, "dismissed": 0, "skipped": 0, "errors": 0}

    for task in tasks:
        task_id = task["id"]
        title = task["title"]
        action, reason = _triage_one(task)

        if verbose:
            LOG.info("[%s] %s → %s (%s)", task_id, title[:60], action, reason[:80])

        entry: Dict[str, Any] = {
            "id": task_id,
            "title": title,
            "lens": task.get("oracle_lens"),
            "action": action,
            "reason": reason,
            "api_success": None,
            "api_message": None,
        }

        if action == "promote":
            ok, msg = _api_move(task_id, "backlog", api_base, dry_run)
            entry["api_success"] = ok
            entry["api_message"] = msg
            if ok:
                counts["promoted"] += 1
            else:
                counts["errors"] += 1

        elif action == "dismiss":
            bypass_reason = f"oracle_triage_reflex: {reason[:200]}"
            ok, msg = _api_move(task_id, "done", api_base, dry_run, bypass_reason=bypass_reason)
            entry["api_success"] = ok
            entry["api_message"] = msg
            if ok:
                counts["dismissed"] += 1
            else:
                counts["errors"] += 1

        elif action == "backlog":
            # FR tasks: promote to backlog (human scoping needed)
            ok, msg = _api_move(task_id, "backlog", api_base, dry_run)
            entry["api_success"] = ok
            entry["api_message"] = msg
            if ok:
                counts["promoted"] += 1  # count as promoted for summary
            else:
                counts["errors"] += 1

        else:  # skip
            counts["skipped"] += 1
            entry["api_success"] = True
            entry["api_message"] = "no action taken"

        decisions.append(entry)

        if _harness_record_decision is not None:
            _harness_record_decision(
                task_id, "oracle_triage", action,
                confidence=task.get("oracle_confidence"),
                metadata={
                    "lens": task.get("oracle_lens"),
                    "reason": reason[:200],
                    "source": "llm_fallback" if reason.startswith("[llm]") else "deterministic",
                },
            )

    summary: Dict[str, Any] = {
        "success": counts["errors"] == 0,
        "dry_run": dry_run,
        "counts": counts,
        "decisions": decisions,
    }
    return summary


# ---------------------------------------------------------------------------
# Genesis daemon entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:  # noqa: ARG001
    """Genesis reflex entry point — called by daemon every 3 h."""
    api_base = config.get("api_base", _DEFAULT_API_BASE)
    dry_run = bool(config.get("dry_run", False))
    verbose = bool(config.get("verbose", False))

    LOG.info("[oracle_triage] starting triage cycle")
    result = triage(api_base=api_base, dry_run=dry_run, verbose=verbose)
    counts = result.get("counts", {})
    LOG.info(
        "[oracle_triage] done — promoted=%d dismissed=%d skipped=%d errors=%d",
        counts.get("promoted", 0),
        counts.get("dismissed", 0),
        counts.get("skipped", 0),
        counts.get("errors", 0),
    )
    _notify(result, api_base, dry_run)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Oracle Triage Reflex — auto-triage suggested kanban tasks")
    p.add_argument("--run", action="store_true", help="Execute the triage loop")
    p.add_argument("--dry-run", action="store_true", help="Analyse but do not move any tasks")
    p.add_argument("--json", action="store_true", help="Output result as JSON")
    p.add_argument("--verbose", action="store_true", help="Log each decision")
    p.add_argument("--api-base", default=_DEFAULT_API_BASE, help=f"Dashboard API base URL (default: {_DEFAULT_API_BASE})")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)

    if not args.run:
        print("Use --run to execute the triage loop.  Add --dry-run to preview.")
        return 0

    result = triage(
        api_base=args.api_base,
        dry_run=args.dry_run,
        verbose=args.verbose or not args.json,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        counts = result.get("counts", {})
        print(f"\nOracle Triage {'[DRY RUN] ' if args.dry_run else ''}complete")
        print(f"  Promoted:  {counts.get('promoted', 0)}")
        print(f"  Dismissed: {counts.get('dismissed', 0)}")
        print(f"  Skipped:   {counts.get('skipped', 0)}")
        print(f"  Errors:    {counts.get('errors', 0)}")
        if not args.json:
            for d in result.get("decisions", []):
                if d["action"] != "skip":
                    status = "OK" if d.get("api_success") else "FAIL"
                    print(f"  [{status}] {d['action']:8} {d['id']:30} {d['reason'][:70]}")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    sys.exit(main())
