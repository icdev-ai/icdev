# CUI // SP-CTI
"""Genesis Reflex — Integrity Monitor (SIPA self-assessment of ICDEV ``tools/``).

Periodically runs the SIPA engine (:func:`tools.integrity.engine.assess`,
provenance-aware / Mode A) over ICDEV's *own* ``tools/`` tree against the platform
RTM. When a **new** high-risk or ``unauthorized_capability`` finding appears versus
the last baseline assessment of the same source, it opens exactly one Kanban
remediation card (``status='suggested'``) so a human reviews the drift.

Why this matters: an AI agent (or a human) can quietly add a capability to ICDEV's
own code that no requirement authorizes — a *semantic backdoor* in the platform
itself. This reflex is ICDEV watching ICDEV.

Dedupe (the ``drift_detector`` idea, so it never re-alerts):
  * **Baseline diff** — a finding only counts as *new* when its stable signature
    (``finding_type :: relative_path :: capability_type``) is absent from every
    PRIOR assessment of the same ``source_ref``. The very first assessment of a
    source establishes the baseline *silently* (no cards) so the initial sweep of
    a large tree never floods the board.
  * **Open-card guard** — even within the new set, a card is only opened when no
    open Kanban card already carries the same signature title.

Static-only and air-gap safe: ``assess`` never executes the target (copy + AST +
isolated scanner subprocesses) and this reflex makes no network calls. Any future
probe MUST use ``127.0.0.1`` (never ``localhost``) per the daemon gotcha.

Daemon contract: ``run(config, trust)`` is dispatched by ``GenesisDaemon`` (the
second positional is the trust kernel, not a DB handle — we open our own RLS-aware
connection). Returns a dict carrying both the daemon keys
(``success`` / ``metric_value`` / ``details``) and the reflex-spec keys
(``scanned`` / ``flagged`` / ``status``).

CLI:
    python tools/genesis/reflexes/integrity_monitor.py            # assess repo tools/
    python tools/genesis/reflexes/integrity_monitor.py --dry-run  # no cards written
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _env_flag(name: str) -> bool:
    """Read a boolean env flag (default OFF)."""
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")


# opx-sipa-02 flags — BOTH default OFF so merging changes NOTHING on the live 6h
# reflex. The operator enables them for one transition run, then drops the
# transition flag (see the reflex docstring / PR go-live note).
#
#   ICDEV_SIPA_RELPATH_DIRS       — when on, _rel_path's marker-absent fallback
#     keeps the normalized relative posix path (directories preserved) instead
#     of collapsing to the basename. Resolves basename ambiguity (posture.py x6,
#     iac_generator.py x24) so distinct files get distinct, still-stable
#     signatures. Measured 0 collisions between distinct files (the only
#     dir-path merges are the same file stored with '/' vs '\').
#   ICDEV_SIPA_RELPATH_TRANSITION — when on, run() treats the pass as a silent
#     baseline-establishing run (no cards) even when prior assessments exist, so
#     the FIRST run under the new rel-path scheme re-baselines every finding
#     WITHOUT opening cards. Belt-and-suspenders against a re-signaturing flood.
_RELPATH_DIRS_ENV = "ICDEV_SIPA_RELPATH_DIRS"
_RELPATH_TRANSITION_ENV = "ICDEV_SIPA_RELPATH_TRANSITION"

# Reflex cadence (hours). Mirrored in args/genesis_config.yaml + reflex_registry.
CADENCE_HOURS = 6

# Repo root: tools/genesis/reflexes/integrity_monitor.py -> repo root is 3 up.
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

# Findings that signal a *new capability* worth a human's attention. We key on the
# capability-drift finding types (not arbitrary SAST noise like dangerous_api /
# vuln_dependency) so the signal stays the "unauthorized / malicious capability"
# the SIPA charter is about. unauthorized_capability is Mode A's semantic-backdoor
# signal; known_bad_signature is a malware/regex hit.
HIGH_RISK_FINDING_TYPES = ("unauthorized_capability", "known_bad_signature")

# Kanban open-status set — a card in any of these already covers a signature.
_OPEN_STATUSES = ("backlog", "scheduled", "in_progress", "suggested")

_SEVERITY_TO_PRIORITY = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "low",
}

# Friendly mode aliases (the task speaks of mode='aware'); map to canonical tokens.
_MODE_ALIASES = {
    "aware": "provenance_aware",
    "blind": "provenance_blind",
    "provenance_aware": "provenance_aware",
    "provenance_blind": "provenance_blind",
    "auto": "auto",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _card_id() -> str:
    return f"task-{uuid.uuid4().hex[:10]}"


def _rel_path(file_path: Optional[str], assessment_id: int) -> str:
    """Normalize a staged finding path to a stable, run-independent relative path.

    ``ingest.stage`` copies the source tree into ``<quarantine>/<assessment_id>/``,
    so a finding's ``file_path`` carries a per-run quarantine prefix that differs
    every cycle. Stripping everything up to and including the ``/<assessment_id>/``
    segment recovers the tree-relative path (e.g. ``net.py`` / ``sub/x.py``) which
    is stable across assessments — the basis for baseline diffing.

    Marker-absent fallback (opx-sipa-02): in practice findings are stored with
    tree-relative paths and the ``/<assessment_id>/`` marker is essentially never
    present, so this fallback drives ~all signatures. The LEGACY behaviour returns
    just the basename, which is ambiguous — 6+ files share ``posture.py`` and 24
    share ``iac_generator.py``, collapsing to ONE signature and causing wrong
    triage. When ``ICDEV_SIPA_RELPATH_DIRS`` is enabled the fallback keeps the
    normalized relative posix path (directories preserved), which is still stable
    across runs (mixed ``/``\\``\\`` separators normalize identically) and unique
    per file. The flag defaults OFF so merging does not change live reflex output
    until the operator runs the one-time baseline transition.
    """
    if not file_path:
        return ""
    posix = Path(file_path).as_posix()
    marker = f"/{assessment_id}/"
    idx = posix.find(marker)
    if idx >= 0:
        return posix[idx + len(marker):]
    if _env_flag(_RELPATH_DIRS_ENV):
        # Directory-preserving, normalized, and relative (strip any leading
        # slash so an absolute stray path can't destabilize the signature).
        return posix.lstrip("/")
    return Path(file_path).name


def _signature(finding_type: str, rel_path: str, capability_type: str) -> str:
    """Stable dedupe key for a finding across assessments of the same source."""
    return f"{finding_type}::{rel_path}::{capability_type}"


def _detail_of(row: Any) -> Dict[str, Any]:
    """Decode a finding's ``detail`` column (JSONB on PG, TEXT/JSON on SQLite)."""
    raw = row["detail"] if "detail" in row.keys() else None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _high_risk_signatures(conn: Any, assessment_id: int) -> Dict[str, Dict[str, Any]]:
    """Return ``{signature: finding-info}`` for the high-risk findings of one assessment."""
    placeholders = ", ".join(["%s"] * len(HIGH_RISK_FINDING_TYPES))
    rows = conn.execute(
        f"SELECT finding_type, severity, file_path, line, detail "
        f"FROM integrity_findings "
        f"WHERE assessment_id = %s AND finding_type IN ({placeholders})",
        (assessment_id, *HIGH_RISK_FINDING_TYPES),
    ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        detail = _detail_of(r)
        cap = detail.get("capability_type") or detail.get("rule") or ""
        rel = _rel_path(r["file_path"], assessment_id)
        sig = _signature(r["finding_type"], rel, cap)
        out[sig] = {
            "finding_type": r["finding_type"],
            "severity": r["severity"],
            "rel_path": rel,
            # Raw, un-normalized finding path. rel_path is often a bare basename
            # (the _rel_path fallback) and 6+ files share names like posture.py,
            # so the card MUST also carry the raw path + assessment_id to be
            # unambiguous during triage (opx-sipa-01). Do NOT rely on rel_path
            # alone for file identification.
            "file_path": r["file_path"],
            "line": r["line"],
            "capability_type": cap,
            "detail": detail,
        }
    return out


def _prior_assessment_ids(conn: Any, source_ref: str, current_id: int) -> List[int]:
    """All assessment ids for this source created before the current one (the baseline)."""
    rows = conn.execute(
        "SELECT id FROM integrity_assessments WHERE source_ref = %s AND id < %s ORDER BY id",
        (source_ref, current_id),
    ).fetchall()
    return [r["id"] for r in rows]


def _baseline_signatures(conn: Any, assessment_ids: List[int]) -> set:
    """Union of high-risk signatures across the given prior assessments."""
    seen: set = set()
    for aid in assessment_ids:
        seen.update(_high_risk_signatures(conn, aid).keys())
    return seen


def _card_title(info: Dict[str, Any]) -> str:
    """Deterministic, signature-stable card title (drives open-card dedupe)."""
    cap = info["capability_type"] or info["finding_type"]
    return f"[SIPA] Unauthorized capability '{cap}' in {info['rel_path']}"


def _open_card_exists(conn: Any, title: str) -> bool:
    placeholders = ", ".join(["%s"] * len(_OPEN_STATUSES))
    try:
        row = conn.execute(
            f"SELECT 1 FROM kanban_tasks WHERE title = %s AND status IN ({placeholders}) LIMIT 1",
            (title, *_OPEN_STATUSES),
        ).fetchone()
        return row is not None
    except Exception as exc:  # noqa: BLE001 — missing table early in bootstrap -> not a dup
        logger.debug("open-card dedupe query failed: %s", exc)
        return False


_ALLOWLIST_KEY: Dict[str, str] = {
    "process_exec": "known_safe_process_exec_modules",
    "filesystem": "known_safe_filesystem_modules",
    "env_secret": "known_safe_env_vars",
    "dynamic_import": "known_safe_dynamic_import_modules",
}

_ALLOWLIST_VERIFY: Dict[str, str] = {
    "process_exec": (
        "python -c \""
        "import yaml; d=yaml.safe_load(open('args/integrity_config.yaml')); "
        "mods=d.get('known_safe_process_exec_modules') or []; "
        "print('AUTHORIZED' if any('{rel_path}' in m for m in mods) else 'NOT AUTHORIZED')"
        "\""
    ),
    "filesystem": (
        "python -c \""
        "import yaml; d=yaml.safe_load(open('args/integrity_config.yaml')); "
        "mods=d.get('known_safe_filesystem_modules') or []; "
        "print('AUTHORIZED' if any('{rel_path}' in m for m in mods) else 'NOT AUTHORIZED')"
        "\""
    ),
    "env_secret": (
        "python -c \""
        "import yaml; d=yaml.safe_load(open('args/integrity_config.yaml')); "
        "keys=d.get('known_safe_env_vars') or []; "
        "print('AUTHORIZED' if '{rel_path}' in keys else 'NOT AUTHORIZED')"
        "\""
    ),
    "dynamic_import": (
        "python -c \""
        "import yaml; d=yaml.safe_load(open('args/integrity_config.yaml')); "
        "mods=d.get('known_safe_dynamic_import_modules') or []; "
        "print('AUTHORIZED' if any('{rel_path}' in m for m in mods) else 'NOT AUTHORIZED')"
        "\""
    ),
}

_DEFAULT_VERIFY = (
    "python -c \""
    "from tools.integrity.intent_reconciler import _load_safe_process_exec_modules, "
    "_load_safe_filesystem_modules; print('check args/integrity_config.yaml')"
    "\""
)


def _card_description(info: Dict[str, Any], assessment_id: int, source_ref: str, verdict: str) -> str:
    detail = info.get("detail") or {}
    cap = info.get("capability_type") or ""
    rel_path = info.get("rel_path") or ""
    allowlist_key = _ALLOWLIST_KEY.get(cap)

    parts = [
        f"AUTO-GENERATED by the Genesis integrity_monitor reflex ({CADENCE_HOURS}h cadence).",
        "",
        "ICDEV self-assessment (SIPA, provenance-aware / Mode A) surfaced a capability "
        "in ICDEV's own tools/ tree that NO platform requirement (RTM) authorizes — the "
        "semantic-backdoor case. Review whether this capability is legitimate; if so, add "
        "an authorizing requirement, otherwise remove/quarantine the code.",
        "",
        f"Source:          {source_ref}",
        f"Assessment ID:   {assessment_id}  (engine verdict: {verdict})",
        f"Finding type:    {info['finding_type']}",
        f"Severity:        {info['severity']}",
        f"Capability:      {cap}",
        f"File (rel):      {rel_path}"
        + (f":{info['line']}" if info.get("line") else "")
        + "   <- may be a bare basename; disambiguate via the raw path below",
        f"File (raw):      {info.get('file_path') or ''}",
        "",
        "Triage — resolve the file unambiguously (rel path above can collide; 6+ "
        "files share names like posture.py). Query the raw findings for THIS "
        "assessment; output is authoritative and must NOT be truncated:",
        "  SELECT finding_type, file_path, line, detail",
        "  FROM integrity_findings",
        f"  WHERE assessment_id = {assessment_id}",
        "  ORDER BY file_path, line;",
    ]
    reason = detail.get("reason")
    if reason:
        parts.append(f"Reason:          {reason}")
    sites = detail.get("sites")
    if sites:
        parts.append("Call sites:")
        for s in sites[:10]:
            parts.append(f"  - {s}")

    # Build capability-specific remediation steps so Claude CLI doesn't have to
    # rediscover the allowlist mechanism from source. Step 3 is a fast config
    # check (< 1s), NOT the full SIPA scan which takes 10–15 min and always times out.
    if allowlist_key:
        verify_cmd = _ALLOWLIST_VERIFY.get(cap, _DEFAULT_VERIFY).replace("{rel_path}", rel_path)
        parts.extend(
            [
                "",
                "Investigation:",
                "  1. git log/blame the file — who/what added this capability and when?",
                f"  2. If legitimate: add the file path to '{allowlist_key}' in "
                f"args/integrity_config.yaml (see existing entries for the pattern). "
                f"Include a comment explaining the authorization rationale.",
                "     If NOT legitimate: remove or refactor the offending call site.",
                f"  3. Verify (fast — no full scan): {verify_cmd}",
                "  4. Mark this task done via: python tools/kanban/cli.py --set-status "
                "<task_id> done",
            ]
        )
    else:
        parts.extend(
            [
                "",
                "Investigation:",
                "  1. git log/blame the file — who/what added this capability and when?",
                "  2. Confirm whether a requirement authorizes it. If yes, add the file to "
                "the appropriate known_safe_* list in args/integrity_config.yaml. "
                "If no, remove/quarantine the code.",
                "  3. Verify the fix: check args/integrity_config.yaml contains the entry, "
                "or confirm the call site is removed.",
                "  4. Mark this task done via: python tools/kanban/cli.py --set-status "
                "<task_id> done",
            ]
        )
    return "\n".join(parts)


def _open_card(
    conn: Any,
    info: Dict[str, Any],
    assessment_id: int,
    source_ref: str,
    verdict: str,
) -> Optional[str]:
    """Insert one suggested Kanban remediation card. Returns task id or None."""
    title = _card_title(info)
    description = _card_description(info, assessment_id, source_ref, verdict)
    priority = _SEVERITY_TO_PRIORITY.get((info.get("severity") or "medium").lower(), "medium")
    task_id = _card_id()
    now = _now_iso()
    try:
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, status, executor_type, "
            " dispatch_source, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                task_id,
                title,
                description,
                "fix",
                priority,
                "suggested",
                "claude_cli",
                "integrity_monitor",
                now,
                now,
            ),
        )
        conn.commit()
        return task_id
    except Exception as exc:  # noqa: BLE001 — never let a card write abort the cycle
        logger.warning("integrity_monitor: card insert failed for %r: %s", title, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def run(config: Optional[Dict[str, Any]] = None, conn: Any = None) -> Dict[str, Any]:
    """Self-assess ICDEV ``tools/`` and open a card per NEW high-risk capability.

    Args:
        config: reflex context. Recognized keys:
            ``target`` (path to assess; default ``<repo>/tools``),
            ``mode`` (``aware`` / ``blind`` / canonical token; default ``aware``),
            ``project_id`` / ``session_id`` (RTM provenance handles),
            ``dry_run`` (compute findings without opening cards).
        conn: the Genesis daemon passes its TrustKernel here (NOT a DB handle); a
            real DB connection is only injected by tests. We use ``conn`` as the DB
            only when it exposes ``.execute`` — otherwise we open our own.

    Returns:
        Dict with daemon keys (``success`` / ``metric_value`` / ``details``) plus
        reflex-spec keys (``scanned`` / ``flagged`` / ``status``).
    """
    ctx = config or {}
    dry_run = bool(ctx.get("dry_run", False))
    target = str(ctx.get("target") or (BASE_DIR / "tools"))
    raw_mode = str(ctx.get("mode") or "aware").strip().lower()
    mode = _MODE_ALIASES.get(raw_mode, "provenance_aware")
    project_id = ctx.get("project_id") or "icdev-tools-rtm"
    session_id = ctx.get("session_id")

    result: Dict[str, Any] = {
        "success": False,
        "metric_value": 0.0,
        "status": "ok",
        "scanned": 0,
        "flagged": 0,
        "cadence_hours": CADENCE_HOURS,
        "details": {
            "target": target,
            "mode": mode,
            "cards": [],
            "deduped": 0,
            "baseline_established": False,
            "baseline_transition": False,
            "errors": [],
        },
    }
    details = result["details"]

    # Use the injected handle as a DB only when it is one (tests); else open our own.
    db_injected = conn is not None and hasattr(conn, "execute")
    db = conn if db_injected else None
    own_conn = not db_injected
    try:
        if own_conn:
            from tools.db.storage import get_connection

            db = get_connection()

        from tools.integrity import engine
        from tools.integrity.db.init_db import init_db as integrity_init

        integrity_init(db)

        # 1. Run the static-only SIPA assessment over ICDEV's own tools/.
        assessment = engine.assess(
            target,
            mode=mode,
            project_id=project_id,
            session_id=session_id,
            conn=db,
        )
        assessment_id = assessment["assessment_id"]
        verdict = assessment.get("verdict", "")
        source_ref = _source_ref_of(db, assessment_id, target)
        details["assessment_id"] = assessment_id
        details["verdict"] = verdict

        # 2. Current high-risk signatures vs. the prior-assessment baseline.
        current = _high_risk_signatures(db, assessment_id)
        result["scanned"] = len(current)
        prior_ids = _prior_assessment_ids(db, source_ref, assessment_id)

        # opx-sipa-02 baseline transition: when the operator flips
        # ICDEV_SIPA_RELPATH_DIRS on, every finding is re-signatured under the
        # new dir-preserving rel path. This transition flag forces the FIRST such
        # run to re-establish the baseline SILENTLY (no cards) even though prior
        # assessments exist, so re-signatured findings never flood the board.
        # The operator drops the flag after one run; subsequent runs diff
        # normally against the re-baselined (dir-path) signatures.
        transition = _env_flag(_RELPATH_TRANSITION_ENV)

        if not prior_ids or transition:
            # First assessment of this source (or a forced transition pass) —
            # establish the baseline silently so the sweep never floods the board.
            details["baseline_established"] = True
            details["baseline_transition"] = bool(transition and prior_ids)
            logger.info(
                "integrity_monitor: baseline %s for %s (%d high-risk capability(ies)) — no cards",
                "transition (re-signatured, forced)" if (transition and prior_ids) else "established",
                source_ref, len(current),
            )
            result["success"] = True
            return result

        baseline = _baseline_signatures(db, prior_ids)
        new_sigs = [sig for sig in current if sig not in baseline]

        # 3. Open exactly one card per genuinely-new signature (open-card guarded).
        for sig in new_sigs:
            info = current[sig]
            title = _card_title(info)
            if _open_card_exists(db, title):
                details["deduped"] += 1
                continue
            if dry_run:
                details["cards"].append({"title": title, "dry_run": True})
                continue
            task_id = _open_card(db, info, assessment_id, source_ref, verdict)
            if task_id is None:
                details["errors"].append(f"card insert failed for {sig}")
                continue
            details["cards"].append(
                {
                    "task_id": task_id,
                    "title": title,
                    "severity": info["severity"],
                    "signature": sig,
                }
            )

        result["flagged"] = len([c for c in details["cards"] if c.get("task_id")]) if not dry_run else len(details["cards"])
        result["metric_value"] = float(result["flagged"])
        result["success"] = not details["errors"]
        logger.info(
            "integrity_monitor: %s -> verdict=%s, %d new / %d scanned, %d card(s), %d deduped",
            source_ref, verdict, len(new_sigs), result["scanned"], result["flagged"], details["deduped"],
        )
    except Exception as exc:  # noqa: BLE001 — surface as a failed cycle, never crash the daemon
        logger.exception("integrity_monitor reflex error: %s", exc)
        result["status"] = "error"
        result["success"] = False
        details["errors"].append(str(exc))
    finally:
        if own_conn and db is not None:
            try:
                db.close()
            except Exception:
                pass
    return result


def _source_ref_of(conn: Any, assessment_id: int, fallback: str) -> str:
    """Read the assessment's stored ``source_ref`` (stable across runs) for baseline keying."""
    try:
        row = conn.execute(
            "SELECT source_ref FROM integrity_assessments WHERE id = %s",
            (assessment_id,),
        ).fetchone()
        if row is not None:
            return row["source_ref"] if "source_ref" in row.keys() else (row[0] or fallback)
    except Exception as exc:  # noqa: BLE001
        logger.debug("source_ref lookup failed: %s", exc)
    return fallback


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        prog="integrity_monitor",
        description="Genesis reflex — SIPA self-assessment of ICDEV tools/ for new unauthorized capabilities",
    )
    parser.add_argument("--target", help="Path to assess (default: repo tools/)")
    parser.add_argument("--mode", default="aware", help="aware | blind | provenance_aware | provenance_blind")
    parser.add_argument("--dry-run", action="store_true", help="Compute findings without opening cards")
    args = parser.parse_args()

    out = run({"target": args.target, "mode": args.mode, "dry_run": args.dry_run})
    print(json.dumps(out, indent=2, ensure_ascii=False))
