# CUI // SP-CTI
"""SOAR-lite response playbooks — composition layer (card ``crx-sec-02``).

Closes Security-Canvas gap #1 (SOAR-lite / active response) as a **composition**
over layers that already exist — it does NOT re-implement runbook execution or
incident tracking:

* Incident tracking  -> ``tools.sre.incident_commander.create_incident`` (reused).
* Runbook catalog    -> ``tools.sre.runbook_executor`` / infra & ZIG runbooks (referenced).
* HITL approval gate  -> the ACE co-worker pattern (``tools/ace/coworker_thread.py``
  ``HITLGate``): a *pending* audit row is outstanding until a matching *approved*
  (or *rejected*) row is written. Here that lives in the append-only
  ``soar_playbook_audit`` table.

A playbook maps a REAL alert/finding type (CVE triage SLA breach, insider-risk UBA
anomaly, secrets-detection hit) to an ordered list of steps. Each step is either:

* ``enrichment``  — READ-ONLY context / tracking; auto-executes.
* ``destructive`` — a state-changing containment action; the run BLOCKS pending
  HITL approval before the action runs.

Only actions ICDEV can actually perform are wired: revoke a broker-issued service
key, disable a user/service account, quarantine a kanban task source, block an
egress destination. Host-isolation / raw-network actions are intentionally absent.

State lives in ``soar_playbook_runs`` (mutable) and every run event is appended to
``soar_playbook_audit`` (APPEND-ONLY, registered in ``.claude/hooks/pre_tool_use.py``).
Both tables carry ``tenant_id`` + ``classification`` for row-level security.

NIST 800-53: IR-4, IR-4(1), IR-5, AC-2(12), AU-2, AU-6, SI-4.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "args" / "soar_playbooks.yaml"

RUN_STATUSES = ("running", "awaiting_approval", "completed", "aborted")
STEP_KINDS = ("enrichment", "destructive")


# ---------------------------------------------------------------------------
# Config / playbook catalog
# ---------------------------------------------------------------------------

def load_playbooks(path: Optional[Path] = None) -> dict[str, Any]:
    """Load the playbook catalog from YAML, tolerating a missing file."""
    cfg_path = Path(path) if path else _CONFIG_PATH
    default = {"version": 1, "enabled": False, "default_classification": "CUI",
               "default_tenant": "platform", "playbooks": {}}
    try:
        import yaml

        if cfg_path.exists():
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            default.update({k: v for k, v in loaded.items()})
            default["playbooks"] = loaded.get("playbooks", {}) or {}
    except Exception:
        return default
    env = os.environ.get("ICDEV_SOAR_ENABLED")
    if env is not None:
        default["enabled"] = env.strip().lower() in ("1", "true", "yes", "on")
    return default


def is_enabled(config: Optional[dict] = None) -> bool:
    return bool((config or load_playbooks()).get("enabled"))


def match_playbook(finding_type: str, config: Optional[dict] = None) -> Optional[dict[str, Any]]:
    """Return the playbook whose ``finding_type`` matches, else ``None``."""
    config = config or load_playbooks()
    for key, pb in (config.get("playbooks") or {}).items():
        if pb.get("finding_type") == finding_type or key == finding_type:
            out = dict(pb)
            out["id"] = key
            return out
    return None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soar_playbook_runs (
            run_id             TEXT PRIMARY KEY,
            playbook_id        TEXT NOT NULL,
            finding_type       TEXT,
            entity             TEXT,
            severity           TEXT,
            status             TEXT NOT NULL DEFAULT 'running',
            current_step_index INTEGER NOT NULL DEFAULT 0,
            context_json       TEXT DEFAULT '{}',
            results_json       TEXT DEFAULT '[]',
            actor              TEXT,
            tenant_id          TEXT,
            classification     TEXT DEFAULT 'CUI',
            created_at         TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at         TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soar_playbook_audit (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id         TEXT NOT NULL,
            playbook_id    TEXT,
            step_id        TEXT,
            action         TEXT,
            kind           TEXT,
            status         TEXT NOT NULL,
            detail         TEXT,
            actor          TEXT,
            tenant_id      TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_soar_audit_run "
            "ON soar_playbook_audit(run_id, created_at)"
        )
    except Exception:
        pass
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_conn():
    from tools.db.storage import get_connection

    return get_connection()


# ---------------------------------------------------------------------------
# Append-only audit
# ---------------------------------------------------------------------------

def _audit(conn, *, run_id: str, playbook_id: str, step_id: str, action: str,
           kind: str, status: str, detail: str, actor: str,
           tenant_id, classification: str) -> None:
    """Append one event to ``soar_playbook_audit`` (append-only) and best-effort
    mirror the material events to the global audit trail via the shared helper."""
    conn.execute(
        """INSERT INTO soar_playbook_audit
           (run_id, playbook_id, step_id, action, kind, status, detail, actor,
            tenant_id, classification, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (run_id, playbook_id, step_id, action, kind, status, detail, actor,
         tenant_id, classification, _now()),
    )
    conn.commit()
    # Mirror to the global immutable audit trail (never raw-INSERT into audit_trail).
    try:  # pragma: no cover - best-effort, tolerant of DB/path differences
        from tools.audit.audit_logger import atomic_log_event

        atomic_log_event(
            event_type="soar_playbook_step",
            actor=actor or "system",
            action=f"{status}:{action or step_id}",
            details={"run_id": run_id, "playbook_id": playbook_id,
                     "step_id": step_id, "kind": kind, "detail": detail},
            classification=classification or "CUI",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------
# Enrichment handlers are READ-ONLY and always safe to auto-run. Destructive
# handlers run ONLY after HITL approval; each is a thin best-effort wrapper over a
# real ICDEV admin function and degrades to a recorded intent (status
# ``recorded_intent``) when no automated wrapper is wired — it never fabricates a
# host-isolation / network action the platform cannot perform.


def _enrich_cve_context(ctx: dict) -> dict:
    return {"status": "enriched", "cve_id": ctx.get("cve_id"),
            "severity": ctx.get("severity"), "sla_age_days": ctx.get("sla_age_days"),
            "affected_assets": ctx.get("affected_assets", [])}


def _enrich_insider_risk(ctx: dict) -> dict:
    account = ctx.get("account_id") or ctx.get("entity")
    try:
        from tools.security import insider_risk

        summary = insider_risk.get_summary(limit=50)
        match = next((f for f in summary.get("findings", [])
                      if f.get("account_id") == account), None)
        return {"status": "enriched", "account_id": account, "latest_finding": match}
    except Exception as exc:
        return {"status": "enriched", "account_id": account, "note": f"summary unavailable: {exc}"}


def _enrich_secret_finding(ctx: dict) -> dict:
    return {"status": "enriched", "rule": ctx.get("rule"),
            "masked_value": ctx.get("masked_value"), "location": ctx.get("location")}


def _open_incident(ctx: dict) -> dict:
    """Reuse the incident layer — do NOT re-implement incident tracking."""
    try:
        from tools.sre.incident_commander import create_incident

        title = ctx.get("incident_title") or f"SOAR: {ctx.get('finding_type', 'security finding')}"
        sev_map = {"critical": "sev1", "high": "sev2", "medium": "sev3", "low": "sev4"}
        severity = sev_map.get((ctx.get("severity") or "high").lower(), "sev2")
        inc = create_incident(title=title, severity=severity,
                              service=ctx.get("service", "security_canvas"),
                              alert_source="soar_lite")
        return {"status": "incident_opened", "incident": inc}
    except Exception as exc:
        return {"status": "recorded_intent", "note": f"incident layer unavailable: {exc}"}


def _revoke_service_key(ctx: dict) -> dict:
    """Revoke a broker-issued service credential (real admin function)."""
    agent_id = ctx.get("agent_id") or ctx.get("service_account") or ctx.get("entity")
    if not agent_id:
        return {"status": "recorded_intent", "note": "no agent_id/service_account supplied"}
    try:
        from tools.security.credential_broker import CredentialBroker

        result = CredentialBroker().revoke_credentials(agent_id, reason="soar_lite_playbook")
        return {"status": "executed", "action": "revoke_service_key",
                "agent_id": agent_id, "result": result}
    except Exception as exc:
        return {"status": "recorded_intent", "action": "revoke_service_key",
                "agent_id": agent_id, "note": f"broker unavailable: {exc}"}


def _disable_user_account(ctx: dict) -> dict:
    """Disable a user/service account. ICDEV has no single-call user-disable admin
    function today, so this records the intent for the operator rather than
    fabricating an action the platform cannot yet perform."""
    account = ctx.get("account_id") or ctx.get("entity")
    return {"status": "recorded_intent", "action": "disable_user_account",
            "account_id": account,
            "note": "recorded for operator action; no automated user-disable wrapper wired"}


def _quarantine_kanban_task_source(ctx: dict) -> dict:
    """Quarantine the kanban task/source that introduced a finding. Recorded as
    intent (kanban quarantine is a manual/scheduler-driven state today)."""
    task_id = ctx.get("task_id") or ctx.get("source")
    return {"status": "recorded_intent", "action": "quarantine_kanban_task_source",
            "task_id": task_id,
            "note": "recorded for operator action; quarantine handled by kanban scheduler"}


def _block_egress_destination(ctx: dict) -> dict:
    """Block an egress destination. Recorded as intent — egress policy is role-based
    and applied via generated K8s/manifest policy, not a live single-call block."""
    dest = ctx.get("destination") or ctx.get("indicator")
    return {"status": "recorded_intent", "action": "block_egress_destination",
            "destination": dest,
            "note": "recorded for operator action; enforced via egress policy manifest"}


ENRICHMENT_ACTIONS = {
    "enrich_cve_context": _enrich_cve_context,
    "enrich_insider_risk": _enrich_insider_risk,
    "enrich_secret_finding": _enrich_secret_finding,
    "open_incident": _open_incident,
}

DESTRUCTIVE_ACTIONS = {
    "revoke_service_key": _revoke_service_key,
    "disable_user_account": _disable_user_account,
    "quarantine_kanban_task_source": _quarantine_kanban_task_source,
    "block_egress_destination": _block_egress_destination,
}


def _run_action(action: str, kind: str, ctx: dict) -> dict:
    handler = (ENRICHMENT_ACTIONS if kind == "enrichment" else DESTRUCTIVE_ACTIONS).get(action)
    if handler is None:
        return {"status": "error", "note": f"no handler for action '{action}'"}
    try:
        return handler(ctx)
    except Exception as exc:  # pragma: no cover - defensive
        return {"status": "error", "note": str(exc)}


# ---------------------------------------------------------------------------
# Run state helpers
# ---------------------------------------------------------------------------

def _row_get(row, key, idx):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        try:
            return row[idx]
        except (IndexError, KeyError, TypeError):
            return None


def _load_run(conn, run_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT run_id, playbook_id, finding_type, entity, severity, status, "
        "current_step_index, context_json, results_json, actor, tenant_id, "
        "classification FROM soar_playbook_runs WHERE run_id=%s",
        (run_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": _row_get(row, "run_id", 0),
        "playbook_id": _row_get(row, "playbook_id", 1),
        "finding_type": _row_get(row, "finding_type", 2),
        "entity": _row_get(row, "entity", 3),
        "severity": _row_get(row, "severity", 4),
        "status": _row_get(row, "status", 5),
        "current_step_index": int(_row_get(row, "current_step_index", 6) or 0),
        "context": json.loads(_row_get(row, "context_json", 7) or "{}"),
        "results": json.loads(_row_get(row, "results_json", 8) or "[]"),
        "actor": _row_get(row, "actor", 9),
        "tenant_id": _row_get(row, "tenant_id", 10),
        "classification": _row_get(row, "classification", 11) or "CUI",
    }


def _save_run(conn, run: dict) -> None:
    conn.execute(
        "UPDATE soar_playbook_runs SET status=%s, current_step_index=%s, "
        "results_json=%s, context_json=%s, updated_at=%s WHERE run_id=%s",
        (run["status"], run["current_step_index"], json.dumps(run["results"]),
         json.dumps(run["context"]), _now(), run["run_id"]),
    )
    conn.commit()


def _steps_for(run: dict, config: dict) -> list[dict]:
    pb = match_playbook(run["playbook_id"], config) or {}
    return list(pb.get("steps") or [])


def _advance(conn, run: dict, config: dict) -> dict:
    """Run steps from ``current_step_index``: auto-execute enrichment steps and
    BLOCK at the first destructive step pending HITL approval. Persists as it goes.
    """
    steps = _steps_for(run, config)
    i = run["current_step_index"]
    while i < len(steps):
        step = steps[i]
        kind = step.get("kind", "enrichment")
        step_id = step.get("id", f"step_{i}")
        action = step.get("action", "")
        if kind == "destructive":
            # Block: write a pending-approval audit row and stop here.
            run["status"] = "awaiting_approval"
            run["current_step_index"] = i
            _save_run(conn, run)
            _audit(conn, run_id=run["run_id"], playbook_id=run["playbook_id"],
                   step_id=step_id, action=action, kind=kind, status="awaiting_approval",
                   detail=step.get("description", ""), actor=run["actor"],
                   tenant_id=run["tenant_id"], classification=run["classification"])
            return run
        # enrichment — auto-execute
        result = _run_action(action, kind, run["context"])
        run["results"].append({"step_id": step_id, "action": action, "kind": kind,
                               "result": result})
        run["current_step_index"] = i + 1
        _save_run(conn, run)
        _audit(conn, run_id=run["run_id"], playbook_id=run["playbook_id"],
               step_id=step_id, action=action, kind=kind, status=result.get("status", "executed"),
               detail=json.dumps(result)[:500], actor=run["actor"],
               tenant_id=run["tenant_id"], classification=run["classification"])
        i += 1
    # all steps consumed
    run["status"] = "completed"
    run["current_step_index"] = len(steps)
    _save_run(conn, run)
    _audit(conn, run_id=run["run_id"], playbook_id=run["playbook_id"],
           step_id="", action="", kind="", status="completed",
           detail="playbook run completed", actor=run["actor"],
           tenant_id=run["tenant_id"], classification=run["classification"])
    return run


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_run(finding_type: str, entity: str = "", context: Optional[dict] = None,
              actor: str = "system", config: Optional[dict] = None, conn=None) -> dict[str, Any]:
    """Start a playbook run for ``finding_type``. Auto-executes enrichment steps and
    blocks on the first destructive step pending HITL approval.

    Pass ``conn`` to reuse a caller-owned connection (tests); otherwise the main DB
    connection is opened and closed. Returns the run state dict.
    """
    config = config or load_playbooks()
    pb = match_playbook(finding_type, config)
    if pb is None:
        return {"status": "no_playbook", "finding_type": finding_type}

    own = conn is None
    if own:
        conn = _open_conn()
    try:
        _ensure_tables(conn)
        tenant_id = (context or {}).get("tenant_id") or config.get("default_tenant", "platform")
        classification = (context or {}).get("classification") or config.get("default_classification", "CUI")
        run_id = "soar-" + hashlib.sha256(
            f"{pb['id']}:{entity}:{_now()}:{uuid.uuid4()}".encode()
        ).hexdigest()[:16]
        ctx = dict(context or {})
        ctx.setdefault("finding_type", finding_type)
        ctx.setdefault("entity", entity)
        ctx.setdefault("severity", pb.get("severity", "high"))
        conn.execute(
            """INSERT INTO soar_playbook_runs
               (run_id, playbook_id, finding_type, entity, severity, status,
                current_step_index, context_json, results_json, actor, tenant_id,
                classification, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, pb["id"], finding_type, entity, pb.get("severity", "high"),
             "running", 0, json.dumps(ctx), "[]", actor, tenant_id, classification,
             _now(), _now()),
        )
        conn.commit()
        _audit(conn, run_id=run_id, playbook_id=pb["id"], step_id="", action="",
               kind="", status="started", detail=f"finding_type={finding_type} entity={entity}",
               actor=actor, tenant_id=tenant_id, classification=classification)
        run = _load_run(conn, run_id)
        return _advance(conn, run, config)
    finally:
        if own:
            conn.close()


def approve_step(run_id: str, step_id: str, actor: str = "operator",
                 config: Optional[dict] = None, conn=None) -> dict[str, Any]:
    """Approve the pending destructive step, execute it, and continue the run.

    Mirrors the ACE HITLGate.resolve() pattern: writes an ``approved`` audit row
    (the resolution of the outstanding ``awaiting_approval`` row) then runs the
    action and advances.
    """
    config = config or load_playbooks()
    own = conn is None
    if own:
        conn = _open_conn()
    try:
        _ensure_tables(conn)
        run = _load_run(conn, run_id)
        if run is None:
            return {"status": "error", "note": "unknown run_id"}
        if run["status"] != "awaiting_approval":
            return {"status": "error", "note": f"run not awaiting approval (status={run['status']})"}
        steps = _steps_for(run, config)
        i = run["current_step_index"]
        step = steps[i] if i < len(steps) else {}
        if step.get("id") != step_id:
            return {"status": "error", "note": f"pending step is '{step.get('id')}', not '{step_id}'"}
        # Resolve the gate + execute the destructive action.
        _audit(conn, run_id=run_id, playbook_id=run["playbook_id"], step_id=step_id,
               action=step.get("action", ""), kind="destructive", status="approved",
               detail=f"approved by {actor}", actor=actor,
               tenant_id=run["tenant_id"], classification=run["classification"])
        result = _run_action(step.get("action", ""), "destructive", run["context"])
        run["results"].append({"step_id": step_id, "action": step.get("action", ""),
                               "kind": "destructive", "result": result, "approved_by": actor})
        run["current_step_index"] = i + 1
        run["status"] = "running"
        run["actor"] = actor
        _save_run(conn, run)
        _audit(conn, run_id=run_id, playbook_id=run["playbook_id"], step_id=step_id,
               action=step.get("action", ""), kind="destructive",
               status=result.get("status", "executed"), detail=json.dumps(result)[:500],
               actor=actor, tenant_id=run["tenant_id"], classification=run["classification"])
        return _advance(conn, run, config)
    finally:
        if own:
            conn.close()


def reject_step(run_id: str, step_id: str, actor: str = "operator",
                reason: str = "", conn=None) -> dict[str, Any]:
    """Reject the pending destructive step and abort the run."""
    own = conn is None
    if own:
        conn = _open_conn()
    try:
        _ensure_tables(conn)
        run = _load_run(conn, run_id)
        if run is None:
            return {"status": "error", "note": "unknown run_id"}
        if run["status"] != "awaiting_approval":
            return {"status": "error", "note": f"run not awaiting approval (status={run['status']})"}
        run["status"] = "aborted"
        _save_run(conn, run)
        _audit(conn, run_id=run_id, playbook_id=run["playbook_id"], step_id=step_id,
               action="", kind="destructive", status="rejected",
               detail=f"rejected by {actor}: {reason}"[:500], actor=actor,
               tenant_id=run["tenant_id"], classification=run["classification"])
        return run
    finally:
        if own:
            conn.close()


def get_run(run_id: str, conn=None) -> Optional[dict[str, Any]]:
    own = conn is None
    if own:
        conn = _open_conn()
    try:
        _ensure_tables(conn)
        run = _load_run(conn, run_id)
        if run is None:
            return None
        rows = conn.execute(
            "SELECT step_id, action, kind, status, detail, actor, created_at "
            "FROM soar_playbook_audit WHERE run_id=%s ORDER BY id",
            (run_id,),
        ).fetchall()
        run["audit"] = [
            {"step_id": _row_get(r, "step_id", 0), "action": _row_get(r, "action", 1),
             "kind": _row_get(r, "kind", 2), "status": _row_get(r, "status", 3),
             "detail": _row_get(r, "detail", 4), "actor": _row_get(r, "actor", 5),
             "created_at": _row_get(r, "created_at", 6)}
            for r in rows
        ]
        return run
    finally:
        if own:
            conn.close()


def get_summary(conn=None, limit: int = 20) -> dict[str, Any]:
    """Recent playbook runs + status counts — for the security-canvas panel."""
    own = conn is None
    if own:
        conn = _open_conn()
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT run_id, playbook_id, finding_type, entity, status, current_step_index, "
            "created_at FROM soar_playbook_runs ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        runs, counts = [], {}
        for r in rows:
            status = _row_get(r, "status", 4) or "running"
            counts[status] = counts.get(status, 0) + 1
            runs.append({
                "run_id": _row_get(r, "run_id", 0),
                "playbook_id": _row_get(r, "playbook_id", 1),
                "finding_type": _row_get(r, "finding_type", 2),
                "entity": _row_get(r, "entity", 3),
                "status": status,
                "current_step_index": _row_get(r, "current_step_index", 5),
                "created_at": _row_get(r, "created_at", 6),
            })
        return {"runs": runs, "status_counts": counts, "count": len(runs),
                "playbooks_available": list((load_playbooks().get("playbooks") or {}).keys())}
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="SOAR-lite response playbooks (crx-sec-02)")
    ap.add_argument("--list", action="store_true", help="List available playbooks")
    ap.add_argument("--summary", action="store_true", help="Recent runs + status counts")
    ap.add_argument("--start", metavar="FINDING_TYPE", help="Start a run for a finding type")
    ap.add_argument("--entity", default="", help="Entity for --start")
    ap.add_argument("--approve", nargs=2, metavar=("RUN_ID", "STEP_ID"), help="Approve a pending step")
    ap.add_argument("--reject", nargs=2, metavar=("RUN_ID", "STEP_ID"), help="Reject a pending step")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    if args.list:
        cfg = load_playbooks()
        out = {"enabled": cfg.get("enabled"),
               "playbooks": {k: {"finding_type": v.get("finding_type"),
                                 "steps": len(v.get("steps") or [])}
                             for k, v in (cfg.get("playbooks") or {}).items()}}
    elif args.summary:
        out = get_summary()
    elif args.start:
        out = start_run(args.start, entity=args.entity, actor="cli")
    elif args.approve:
        out = approve_step(args.approve[0], args.approve[1], actor="cli")
    elif args.reject:
        out = reject_step(args.reject[0], args.reject[1], actor="cli")
    else:
        out = {"error": "specify --list, --summary, --start, --approve, or --reject"}

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
