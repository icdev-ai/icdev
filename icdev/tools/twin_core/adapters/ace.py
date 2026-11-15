# CUI // SP-CTI — ACE governance twin adapter (icx-cnf-03)
"""Governance twin over the ACE Co-Worker Engine (``tools/ace``), canvas key ``ace``.

ACE runs teams of co-workers. Its state is runs, checkpoints and trust, so that
is what this twin observes:

* **Runs** — an instance in ``assembling``/``pending``/``active``/``paused`` not
  updated for ``stuck_after_hours``, with no checkpoint waiting on a person, is
  stuck; an instance that ``failed`` in the window is a failure.
* **Checkpoints waiting on a person** — ``ace_audit_log`` ``hitl_pending`` rows
  with no matching ``hitl_resolved``/``hitl_rejected`` (same instance, co-worker
  and detail: the rule ``ace_api`` and ``HITLGate`` use), aged.
* **Aborts** — instances ``cancelled`` in the window. A person chose each one,
  so they are advisory (low), never a failure.
* **Trust drift (NOVA)** — per role in ``ace_trust_ledger``: a band that fell in
  the window, and a role now ``probationary`` (dispatch paused).

Threads: ``ace_instance:<id>``, ``ace_audit:<id>``, ``ace_trust:<ledger id>``.
ACE tables are canvas tables (``get_canvas_connection``); the trust ledger is in
the platform database, as ``tools/ace/trust_calibrator`` reads it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.twin_core.governance import (
    GovernanceTwinAdapter,
    age_hours,
    check,
    close_quietly,
    finding,
    load_policy,
    now_iso,
    rows_as_dicts,
    ts_text,
)
from tools.twin_core.governance import (
    replay as replay_observation,
)
from tools.twin_core.registry import register_twin

LIVE_STATES = ("assembling", "pending", "active", "paused")
#: NOVA bands, lowest first (tools/ace/trust_calibrator.get_trust_band).
BANDS = ("probationary", "supervised", "trusted", "autonomous")


def _band(score: float) -> str:
    """The calibrator's band for a score, without importing it (kept pure for replay)."""
    if score < 0.3:
        return "probationary"
    if score < 0.6:
        return "supervised"
    if score < 0.8:
        return "trusted"
    return "autonomous"


@register_twin
class ACEGovernanceTwin(GovernanceTwinAdapter):
    canvas_key = "ace"

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    # -- collect -------------------------------------------------------------

    def collect(self) -> dict:
        from tools.db.storage import get_canvas_connection, get_connection, table_exists

        policy = load_policy("ace")
        limit = int(policy.get("scan_limit") or 500)
        since = (datetime.now(timezone.utc) - timedelta(hours=policy["window_hours"])
                 ).isoformat(timespec="seconds")
        ev: dict = {"canvas": self.canvas_key, "target_id": "fleet", "observed_at": now_iso(),
                    "since": since, "policy": policy, "instances": None, "hitl": None,
                    "trust": None, "errors": {}}
        conn = None
        try:
            conn = get_canvas_connection("ICDEV_ACE_DB_URL")
            if not table_exists(conn, "ace_instances"):
                ev["errors"]["instances"] = "ace_instances does not exist"
            else:
                marks = ", ".join(["%s"] * len(LIVE_STATES))
                cols = ("id", "name", "role_id", "state", "created_at", "updated_at")
                rows = rows_as_dicts(conn.execute(
                    f"SELECT {', '.join(cols)} FROM ace_instances "  # nosec B608 — constants
                    f"WHERE state IN ({marks}) OR (state IN ('failed', 'cancelled') "
                    "AND updated_at >= %s) ORDER BY updated_at DESC, id DESC LIMIT %s",
                    (*LIVE_STATES, since.replace("T", " ")[:19], limit)), cols)
                ev["instances"] = sorted(
                    ({"id": str(r["id"]), "name": str(r["name"] or ""),
                      "role_id": str(r["role_id"] or ""), "state": str(r["state"]),
                      "updated_at": ts_text(r["updated_at"] or r["created_at"])}
                     for r in rows), key=lambda r: (r["updated_at"], r["id"]))
            if not table_exists(conn, "ace_audit_log"):
                ev["errors"]["hitl"] = "ace_audit_log does not exist"
            else:
                cols = ("id", "instance_id", "coworker_id", "action", "detail", "created_at")
                rows = rows_as_dicts(conn.execute(
                    "SELECT id, instance_id, coworker_id, action, detail, created_at "
                    "FROM ace_audit_log WHERE action IN "
                    "('hitl_pending', 'hitl_resolved', 'hitl_rejected') "
                    "ORDER BY id DESC LIMIT %s", (limit * 4,)), cols)
                ev["hitl"] = sorted(
                    ({"id": str(r["id"]), "instance_id": str(r["instance_id"] or ""),
                      "coworker_id": str(r["coworker_id"] or ""), "action": str(r["action"]),
                      "detail": str(r["detail"] or ""), "at": ts_text(r["created_at"])}
                     for r in rows), key=lambda r: (r["at"], r["id"]))
        except Exception as exc:  # noqa: BLE001 — unreadable is unverified
            ev["errors"].setdefault("instances", str(exc))
            ev["errors"].setdefault("hitl", str(exc))
        finally:
            close_quietly(conn)

        conn = None
        try:
            conn = get_connection()
            if not table_exists(conn, "ace_trust_ledger"):
                ev["errors"]["trust"] = "ace_trust_ledger does not exist"
            else:
                cols = ("id", "role_id", "delta", "reason", "new_score", "recorded_at")
                rows = rows_as_dicts(conn.execute(
                    "SELECT id, role_id, delta, reason, new_score, recorded_at "
                    "FROM ace_trust_ledger ORDER BY recorded_at DESC, id DESC LIMIT %s",
                    (limit * 4,)), cols)
                ev["trust"] = sorted(
                    ({"id": str(r["id"]), "role_id": str(r["role_id"]),
                      "reason": str(r["reason"] or ""), "new_score": round(float(r["new_score"]), 4),
                      "at": ts_text(r["recorded_at"])} for r in rows),
                    key=lambda r: (r["at"], r["id"]))
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["trust"] = str(exc)
        finally:
            close_quietly(conn)
        return ev

    # -- judge (pure) --------------------------------------------------------

    def judge(self, evidence: dict) -> dict:
        policy = evidence.get("policy") or {}
        cap = int(policy.get("max_findings") or 50)
        now = evidence["observed_at"]
        since = evidence.get("since") or ""
        errors = evidence.get("errors") or {}
        checks, findings = [], []

        # checkpoints waiting on a person ------------------------------------
        hitl = evidence.get("hitl")
        checks.append(check("ace.checkpoints", "Checkpoints waiting on a person",
                            hitl is not None, errors.get("hitl") or "ace_audit_log not read"))
        done = {(h["instance_id"], h["coworker_id"], h["detail"])
                for h in hitl or [] if h["action"] != "hitl_pending"}
        waiting = [h for h in hitl or [] if h["action"] == "hitl_pending"
                   and (h["instance_id"], h["coworker_id"], h["detail"]) not in done]
        waiting_instances = {h["instance_id"] for h in waiting}
        for h in waiting[:cap]:
            hours = age_hours(h["at"], now) or 0.0
            if hours >= float(policy.get("checkpoint_fail_hours", 24)):
                sev = "critical"
            elif hours >= float(policy.get("checkpoint_warn_hours", 4)):
                sev = "high"
            else:
                sev = "low"
            findings.append(finding(
                "ace.checkpoint.waiting", sev,
                f"Checkpoint waiting on a person for {hours:.1f}h",
                thread_id=f"ace_audit:{h['id']}",
                detail=f"instance {h['instance_id']} co-worker {h['coworker_id']}: {h['detail']}",
                recommendation="Approve or reject it on the run's page (/coworker/<id>)."))

        # runs: stuck, failed, aborted ---------------------------------------
        inst = evidence.get("instances")
        checks.append(check("ace.runs", "ACE runs (stuck, failed, aborted)",
                            inst is not None, errors.get("instances") or "ace_instances not read"))
        stuck, failed, aborted = [], [], []
        for r in inst or []:
            hours = age_hours(r["updated_at"], now)
            if r["state"] in LIVE_STATES:
                if (r["id"] not in waiting_instances and hours is not None
                        and hours >= float(policy.get("stuck_after_hours", 4))):
                    stuck.append((r, hours))
            elif r["updated_at"] >= since[:19]:
                (failed if r["state"] == "failed" else aborted).append(r)
        for r, hours in stuck[:cap]:
            findings.append(finding(
                "ace.run.stuck", "high", f"Run '{r['name'] or r['id']}' stuck in {r['state']}",
                thread_id=f"ace_instance:{r['id']}",
                detail=f"no update for {hours:.1f}h, role {r['role_id']}",
                recommendation="Open the run; abort it or find what it waits on."))
        for r in failed[:cap]:
            findings.append(finding(
                "ace.run.failed", "medium", f"Run '{r['name'] or r['id']}' failed",
                thread_id=f"ace_instance:{r['id']}", detail=f"at {r['updated_at']}",
                recommendation="Read the run's audit log for the cause."))
        for r in aborted[:cap]:
            findings.append(finding(
                "ace.run.aborted", "low", f"Run '{r['name'] or r['id']}' was aborted",
                thread_id=f"ace_instance:{r['id']}", detail=f"at {r['updated_at']}",
                recommendation="A person stopped it; nothing to do unless it recurs."))

        # NOVA trust drift ---------------------------------------------------
        ledger = evidence.get("trust")
        checks.append(check("ace.trust", "NOVA trust bands per role", ledger is not None,
                            errors.get("trust") or "ace_trust_ledger not read"))
        by_role: dict = {}
        for row in ledger or []:  # oldest first
            by_role.setdefault(row["role_id"], []).append(row)
        bands_now = {}
        for role, rows in sorted(by_role.items()):
            latest = rows[-1]
            band = _band(latest["new_score"])
            bands_now[role] = band
            before = [r for r in rows if r["at"] < since]
            start = _band(before[-1]["new_score"]) if before else None
            if start and BANDS.index(band) < BANDS.index(start):
                findings.append(finding(
                    "ace.trust.band_drop", "medium", f"Role '{role}' fell from {start} to {band}",
                    thread_id=f"ace_trust:{latest['id']}",
                    detail=f"score {latest['new_score']} after '{latest['reason']}'",
                    recommendation="Read the role's recent failures and escalations."))
            if band == "probationary":
                findings.append(finding(
                    "ace.trust.probationary", "medium",
                    f"Role '{role}' is probationary: its dispatch is paused",
                    thread_id=f"ace_trust:{latest['id']}", detail=f"score {latest['new_score']}",
                    recommendation="Every decision of this role needs a person until trust "
                                   "recovers."))

        summary = {
            "live_runs": sum(1 for r in inst or [] if r["state"] in LIVE_STATES),
            "stuck": len(stuck),
            "failed_in_window": len(failed),
            "aborted_in_window": len(aborted),
            "checkpoints_waiting": len(waiting),
            "roles_by_band": {b: sum(1 for v in bands_now.values() if v == b) for b in BANDS},
        }
        return {"checks": checks, "findings": findings, "summary": summary}
