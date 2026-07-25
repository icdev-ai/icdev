# CUI // SP-CTI
"""Post-migration validation checklist — per workload (crx-mig-01, gap #3).

This module COMPOSES existing ICDEV engines into a per-workload validation
checklist run after a workload is migrated in a wave.  It does not reimplement
any engine — each check adapter calls an existing tool and normalises the
result to a ``pass | fail | skip | error`` status:

    * health_probe        → post_migration_validator.check_service_health
    * security_scan        → security.code_pattern_scanner.CodePatternScanner
    * stig_readiness       → aiify.agent_readiness.pillars.stig_compliance.PILLAR
    * twin_resource_diff   → optional IDC twin before/after snapshot diff

Validation status is recorded per (session, wave, workload) in
``mc_workload_validations``.  A wave cannot be closed while any workload has a
failing validation, unless a human supplies an audited override — mirroring the
existing ``force_*`` guard-override pattern.  Every override is written to the
append-only ``mc_wave_close_overrides`` log (NIST AU).

All canvas DB access goes through the canvas connection (RLS disabled); the
``mc_*`` tables carry no tenant_id/classification columns.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.migration_canvas.workload_validator")

# Ordered checklist of composed checks.  Every check runs for a workload; a
# check that has no applicable target for the workload records ``skip``.
CHECK_TYPES = ("health_probe", "security_scan", "stig_readiness", "twin_resource_diff")
VALID_STATUSES = ("pass", "fail", "skip", "error", "pending")

_VALIDATIONS_DDL = """
CREATE TABLE IF NOT EXISTS mc_workload_validations (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    wave_id        TEXT NOT NULL,
    workload_id    TEXT NOT NULL,
    workload_name  TEXT,
    check_type     TEXT NOT NULL,
    status         TEXT NOT NULL CHECK(status IN ('pass','fail','skip','error','pending')),
    detail         TEXT,
    run_at         TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_wlval_unique
    ON mc_workload_validations(session_id, wave_id, workload_id, check_type);
CREATE INDEX IF NOT EXISTS idx_mc_wlval_wave
    ON mc_workload_validations(session_id, wave_id);
"""

# Append-only HITL override audit (NIST AU) — see APPEND_ONLY_TABLES.
_OVERRIDES_DDL = """
CREATE TABLE IF NOT EXISTS mc_wave_close_overrides (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    wave_id         TEXT NOT NULL,
    override_user   TEXT NOT NULL,
    reason          TEXT NOT NULL,
    failing_json    TEXT DEFAULT '[]',
    created_at      TEXT NOT NULL,
    classification  TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_mc_wave_close_ovr_wave
    ON mc_wave_close_overrides(session_id, wave_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _ensure_tables() -> None:
    conn = _conn()
    try:
        conn.executescript(_VALIDATIONS_DDL)
        conn.executescript(_OVERRIDES_DDL)
        conn.commit()
    finally:
        conn.close()


def _result(check_type: str, status: str, detail: str) -> dict:
    return {"check_type": check_type, "status": status, "detail": detail}


# ── Composed check adapters ───────────────────────────────────────────────────
#
# Each adapter CALLS an existing engine.  When the workload provides no target
# for that engine the check is a benign ``skip`` (not a failure).

def _run_health_probe(workload: dict) -> dict:
    """Compose post_migration_validator.check_service_health for a workload URL."""
    url = workload.get("health_url")
    if not url:
        return _result("health_probe", "skip", "No health_url provided for workload.")
    try:
        from tools.migration_canvas.post_migration_validator import check_service_health
        r = check_service_health(url, workload.get("expected_status", 200))
        return _result("health_probe", r.get("status", "error"), r.get("detail", ""))
    except Exception as exc:  # noqa: BLE001
        return _result("health_probe", "error", f"health probe failed: {exc}")


def _run_security_scan(workload: dict) -> dict:
    """Compose the code-pattern security scanner over the workload's repo path."""
    repo = workload.get("repo_path")
    if not repo or not pathlib.Path(repo).is_dir():
        return _result("security_scan", "skip", "No repo_path provided for workload.")
    try:
        from tools.security.code_pattern_scanner import CodePatternScanner
        res = CodePatternScanner().scan_directory(repo)
        if "error" in res:
            return _result("security_scan", "error", res["error"])
        crit = res.get("unallowed_critical", 0)
        high = res.get("unallowed_high", 0)
        if crit or high:
            return _result(
                "security_scan", "fail",
                f"{crit} critical / {high} high unallowed pattern finding(s).",
            )
        return _result("security_scan", "pass", "No critical/high security patterns found.")
    except Exception as exc:  # noqa: BLE001
        return _result("security_scan", "error", f"security scan failed: {exc}")


def _run_stig_readiness(workload: dict) -> dict:
    """Compose the STIG-compliance readiness pillar over the workload's repo path."""
    repo = workload.get("repo_path")
    if not repo or not pathlib.Path(repo).is_dir():
        return _result("stig_readiness", "skip", "No repo_path provided for workload.")
    try:
        from tools.aiify.agent_readiness.pillars.stig_compliance import PILLAR
        results = PILLAR.run(pathlib.Path(repo))
        score = PILLAR.score(results)
        pct = score.get("percentage", 0.0)
        threshold = float(workload.get("stig_min_pct", 0.5))
        if pct >= threshold:
            return _result("stig_readiness", "pass",
                           f"STIG readiness {pct:.0%} (>= {threshold:.0%}).")
        return _result("stig_readiness", "fail",
                       f"STIG readiness {pct:.0%} below required {threshold:.0%}.")
    except Exception as exc:  # noqa: BLE001
        return _result("stig_readiness", "error", f"STIG readiness check failed: {exc}")


def _resource_diff(before: dict, after: dict) -> list[str]:
    keys = sorted(set(before) | set(after))
    changes = []
    for k in keys:
        b, a = before.get(k), after.get(k)
        if b != a:
            changes.append(f"{k}: {b} -> {a}")
    return changes


def _run_twin_diff(workload: dict, snapshot_loader=None) -> dict:
    """Before/after resource diff from an IDC twin snapshot, where one exists.

    A twin snapshot is supplied either directly on the workload
    (``twin_before`` / ``twin_after`` resource dicts) or via an injectable
    ``snapshot_loader(workload_id) -> (before, after) | None`` so a real IDC
    twin importer can be wired in without changing the gate.  Absent a
    snapshot the check records ``skip``.
    """
    before = workload.get("twin_before")
    after = workload.get("twin_after")
    if (before is None or after is None) and snapshot_loader is not None:
        try:
            loaded = snapshot_loader(workload.get("id") or workload.get("workload_id"))
            if loaded:
                before, after = loaded
        except Exception as exc:  # noqa: BLE001
            return _result("twin_resource_diff", "error", f"twin loader failed: {exc}")
    if before is None or after is None:
        return _result("twin_resource_diff", "skip", "No IDC twin snapshot for workload.")
    changes = _resource_diff(before, after)
    if not changes:
        return _result("twin_resource_diff", "pass", "No resource drift vs. pre-migration twin.")
    return _result("twin_resource_diff", "pass",
                   "Resource diff: " + "; ".join(changes))


# ── Runner + persistence ──────────────────────────────────────────────────────

def run_workload_validation(
    session_id: str,
    wave_id: str,
    workload: dict,
    snapshot_loader=None,
) -> dict:
    """Run the full composed checklist for one workload and persist each result.

    ``workload`` fields (all optional except an id):
        id / workload_id, name, health_url, expected_status,
        repo_path, stig_min_pct, twin_before, twin_after

    Returns ``{workload_id, run_at, results: [...], summary: {...}}``.
    """
    _ensure_tables()
    workload_id = workload.get("id") or workload.get("workload_id") or "workload"
    workload_name = workload.get("name", workload_id)
    run_at = _now()

    results = [
        _run_health_probe(workload),
        _run_security_scan(workload),
        _run_stig_readiness(workload),
        _run_twin_diff(workload, snapshot_loader=snapshot_loader),
    ]

    conn = _conn()
    try:
        for r in results:
            existing = conn.execute(
                "SELECT id FROM mc_workload_validations "
                "WHERE session_id=%s AND wave_id=%s AND workload_id=%s AND check_type=%s",
                (session_id, wave_id, workload_id, r["check_type"]),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE mc_workload_validations SET status=%s, detail=%s, run_at=%s, "
                    "workload_name=%s WHERE id=%s",
                    (r["status"], r["detail"], run_at, workload_name, existing[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO mc_workload_validations "
                    "(id, session_id, wave_id, workload_id, workload_name, check_type, "
                    " status, detail, run_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), session_id, wave_id, workload_id, workload_name,
                     r["check_type"], r["status"], r["detail"], run_at),
                )
        conn.commit()
    finally:
        conn.close()

    summary: dict[str, int] = {s: 0 for s in ("pass", "fail", "skip", "error")}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return {
        "workload_id": workload_id,
        "run_at": run_at,
        "results": results,
        "summary": summary,
    }


def get_workload_validations(session_id: str, wave_id: str) -> list[dict]:
    """Return all recorded validation rows for a wave."""
    _ensure_tables()
    conn = _conn()
    try:
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM mc_workload_validations LIMIT 0"
        ).description]
        rows = conn.execute(
            "SELECT * FROM mc_workload_validations WHERE session_id=%s AND wave_id=%s "
            "ORDER BY workload_id, check_type",
            (session_id, wave_id),
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def wave_validation_status(session_id: str, wave_id: str) -> dict:
    """Aggregate validation state for a wave.

    Returns ``{all_pass, failing: [...], counts: {...}, total}`` where
    ``failing`` lists each ``{workload_id, workload_name, check_type, detail}``
    whose status is ``fail`` or ``error``.
    """
    rows = get_workload_validations(session_id, wave_id)
    failing = [
        {
            "workload_id": r["workload_id"],
            "workload_name": r.get("workload_name"),
            "check_type": r["check_type"],
            "detail": r.get("detail"),
        }
        for r in rows
        if r["status"] in ("fail", "error")
    ]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "all_pass": len(failing) == 0,
        "failing": failing,
        "counts": counts,
        "total": len(rows),
    }


def can_close_wave(session_id: str, wave_id: str) -> tuple[bool, list[dict]]:
    """Return ``(closeable, failing_checks)``.

    A wave with no recorded validations is closeable (nothing has failed); a
    wave with any ``fail``/``error`` validation is not.
    """
    status = wave_validation_status(session_id, wave_id)
    return status["all_pass"], status["failing"]


def _log_override(session_id: str, wave_id: str, user: str, reason: str,
                  failing: list[dict]) -> str:
    ovr_id = "ovr-" + uuid.uuid4().hex[:10]
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO mc_wave_close_overrides "
            "(id, session_id, wave_id, override_user, reason, failing_json, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (ovr_id, session_id, wave_id, user, reason, json.dumps(failing), _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return ovr_id


def get_close_overrides(session_id: str, wave_id: str) -> list[dict]:
    """Return the append-only override audit trail for a wave."""
    _ensure_tables()
    conn = _conn()
    try:
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM mc_wave_close_overrides LIMIT 0"
        ).description]
        rows = conn.execute(
            "SELECT * FROM mc_wave_close_overrides WHERE session_id=%s AND wave_id=%s "
            "ORDER BY created_at",
            (session_id, wave_id),
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def close_wave(
    session_id: str,
    wave_id: str,
    user: str = "",
    force: bool = False,
    override_reason: str = "",
) -> dict:
    """Close a wave, gating on post-migration validation status.

    When validations pass the wave is set to ``complete``.  When any workload
    validation is failing the close is REFUSED unless ``force=True`` with a
    non-empty ``override_reason`` — in which case the override is written to the
    append-only ``mc_wave_close_overrides`` audit log (mirroring the existing
    ``force_*`` guard-override pattern) and the wave is closed.

    Returns ``{ok, status, failing?, override_id?, reason?}``.
    """
    _ensure_tables()
    closeable, failing = can_close_wave(session_id, wave_id)

    if not closeable:
        if not force:
            return {
                "ok": False,
                "status": "blocked",
                "reason": "Post-migration validations are failing for this wave.",
                "failing": failing,
            }
        if not (override_reason or "").strip():
            return {
                "ok": False,
                "status": "override_reason_required",
                "reason": "A non-empty override_reason is required to force-close a "
                          "wave with failing validations.",
                "failing": failing,
            }
        override_id = _log_override(session_id, wave_id, user or "unknown",
                                    override_reason.strip(), failing)
    else:
        override_id = None

    # Set the wave status to complete.
    conn = _conn()
    try:
        conn.execute(
            "UPDATE mc_migration_waves SET status='complete' "
            "WHERE id=%s AND session_id=%s",
            (wave_id, session_id),
        )
        conn.commit()
    finally:
        conn.close()

    out = {"ok": True, "status": "complete"}
    if override_id:
        out["override_id"] = override_id
        out["forced"] = True
        out["failing"] = failing
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="MCE Workload Validator — post-migration validation + wave-close gate")
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--wave-id", required=True)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--status", action="store_true", help="Show wave validation status")
    grp.add_argument("--can-close", action="store_true", help="Check if the wave is closeable")
    grp.add_argument("--list", action="store_true", help="List recorded validations")
    ap.add_argument("--output-json", action="store_true")
    args = ap.parse_args()

    if args.status:
        result = wave_validation_status(args.session_id, args.wave_id)
    elif args.can_close:
        ok, failing = can_close_wave(args.session_id, args.wave_id)
        result = {"closeable": ok, "failing": failing}
    else:
        result = get_workload_validations(args.session_id, args.wave_id)

    if args.output_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"[workload_validator] {result}")


if __name__ == "__main__":
    main()
