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

* **Event dispatch (icx-ace-01)** — ``ace_events`` not processed after
  ``events_stale_minutes``; recommendations (pending ``decl-wft-ace-run-launch``
  steps) undecided past ``recommendation_warn_hours``; auto-launches that failed
  or were refused; and the auto-dispatch grants log (``ace_dispatch_grants``):
  grants at today's ``max_per_day``, grants expiring within
  ``grant_expiry_warn_days``, grants suspended because trust fell.

Threads: ``ace_instance:<id>``, ``ace_audit:<id>``, ``ace_trust:<ledger id>``,
``ace_event:<event id>``, ``ace_grant:<grant id>``.
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
                    "trust": None, "events": None, "results": None, "grants": None,
                    "errors": {}}
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

        self._collect_dispatch(ev, since, limit)

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

    def _collect_dispatch(self, ev: dict, since: str, limit: int) -> None:
        """Events, their results and the grants log (icx-ace-01)."""
        from tools.db.storage import get_canvas_connection, table_exists

        conn = None
        try:
            conn = get_canvas_connection("ICDEV_ACE_DB_URL")
            if not table_exists(conn, "ace_events"):
                ev["errors"]["events"] = "ace_events does not exist"
            else:
                cols = ("id", "topic", "source_canvas", "created_at")
                rows = rows_as_dicts(conn.execute(
                    "SELECT id, topic, source_canvas, created_at FROM ace_events "
                    "WHERE processed = 0 ORDER BY created_at ASC, id ASC LIMIT %s",
                    (limit,)), cols)
                ev["events"] = [{"id": str(r["id"]), "topic": str(r["topic"]),
                                 "source_canvas": str(r["source_canvas"] or ""),
                                 "at": ts_text(r["created_at"])} for r in rows]
            if not table_exists(conn, "ace_event_results"):
                ev["errors"]["results"] = "ace_event_results does not exist"
            else:
                cols = ("id", "event_id", "role_id", "instance_id", "status", "grant_id",
                        "reason", "created_at", "state")
                rows = rows_as_dicts(conn.execute(
                    "SELECT r.id, r.event_id, r.role_id, r.instance_id, r.status, r.grant_id, "
                    "r.reason, r.created_at, i.state FROM ace_event_results r "
                    "LEFT JOIN ace_instances i ON i.id = r.instance_id "
                    "WHERE r.status = 'recommended' OR (r.status IN "
                    "('auto_launched', 'auto_refused') AND r.created_at >= %s) "
                    "ORDER BY r.created_at ASC, r.id ASC LIMIT %s",
                    (since.replace("T", " ")[:19], limit * 4)), cols)
                ev["results"] = [{"id": str(r["id"]), "event_id": str(r["event_id"]),
                                  "role_id": str(r["role_id"]),
                                  "instance_id": str(r["instance_id"] or ""),
                                  "status": str(r["status"]),
                                  "grant_id": str(r["grant_id"] or ""),
                                  "reason": str(r["reason"] or ""),
                                  "instance_state": str(r["state"] or ""),
                                  "at": ts_text(r["created_at"])} for r in rows]
            if not table_exists(conn, "ace_dispatch_grants"):
                ev["errors"]["grants"] = "ace_dispatch_grants does not exist"
            else:
                cols = ("event_id", "grant_id", "event", "role_id", "topic", "expires_at",
                        "max_per_day", "reason", "created_at")
                rows = rows_as_dicts(conn.execute(
                    "SELECT event_id, grant_id, event, role_id, topic, expires_at, max_per_day, "
                    "reason, created_at FROM ace_dispatch_grants "
                    "ORDER BY created_at ASC, event_id ASC LIMIT %s", (limit * 4,)), cols)
                ev["grants"] = [{"event_id": str(r["event_id"]), "grant_id": str(r["grant_id"]),
                                 "event": str(r["event"]), "role_id": str(r["role_id"] or ""),
                                 "topic": str(r["topic"] or ""),
                                 "expires_at": ts_text(r["expires_at"]),
                                 "max_per_day": int(r["max_per_day"] or 0),
                                 "reason": str(r["reason"] or ""),
                                 "at": ts_text(r["created_at"])} for r in rows]
        except Exception as exc:  # noqa: BLE001 — unreadable is unverified
            for key in ("events", "results", "grants"):
                ev["errors"].setdefault(key, str(exc))
        finally:
            close_quietly(conn)

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

        dispatch = self._judge_dispatch(evidence, policy, cap, now, errors, checks, findings)

        summary = {
            **dispatch,
            "live_runs": sum(1 for r in inst or [] if r["state"] in LIVE_STATES),
            "stuck": len(stuck),
            "failed_in_window": len(failed),
            "aborted_in_window": len(aborted),
            "checkpoints_waiting": len(waiting),
            "roles_by_band": {b: sum(1 for v in bands_now.values() if v == b) for b in BANDS},
        }
        return {"checks": checks, "findings": findings, "summary": summary}

    @staticmethod
    def _judge_dispatch(evidence: dict, policy: dict, cap: int, now: str, errors: dict,
                        checks: list, findings: list) -> dict:
        """Event dispatch and auto-dispatch grants (icx-ace-01). Pure."""
        events = evidence.get("events")
        checks.append(check("ace.events", "ACE events processed by the dispatcher",
                            events is not None, errors.get("events") or "ace_events not read"))
        stale_h = float(policy.get("events_stale_minutes", 15)) / 60.0
        stale = [(e, age_hours(e["at"], now)) for e in events or []]
        stale = [(e, h) for e, h in stale if h is not None and h >= stale_h]
        for e, hours in stale[:cap]:
            findings.append(finding(
                "ace.event.unprocessed", "medium",
                f"Event '{e['topic']}' not processed for {hours * 60:.0f} min",
                thread_id=f"ace_event:{e['id']}", detail=f"from {e['source_canvas'] or '?'}",
                recommendation="Is the event dispatcher running (args/ace/auto_dispatch.yaml)?"))

        results = evidence.get("results")
        checks.append(check("ace.recommendations", "Recommendations and auto-launches",
                            results is not None,
                            errors.get("results") or "ace_event_results not read"))
        warn_h = float(policy.get("recommendation_warn_hours", 24))
        undecided = [(r, age_hours(r["at"], now)) for r in results or []
                     if r["status"] == "recommended"]
        undecided = [(r, h) for r, h in undecided if h is not None and h >= warn_h]
        for r, hours in undecided[:cap]:
            findings.append(finding(
                "ace.recommendation.undecided", "medium",
                f"Recommendation for '{r['role_id']}' undecided for {hours:.1f}h",
                thread_id=f"ace_event:{r['event_id']}",
                recommendation="Decide it at /decisions (launch, or let it lapse)."))
        auto_failed = [r for r in results or [] if r["status"] == "auto_refused"
                       or (r["status"] == "auto_launched" and r["instance_state"] == "failed")]
        for r in auto_failed[:cap]:
            findings.append(finding(
                "ace.auto.failed", "high",
                f"Auto-launch of '{r['role_id']}' under grant {r['grant_id']} failed",
                thread_id=f"ace_event:{r['event_id']}",
                detail=r["reason"] or f"instance {r['instance_id']} failed",
                recommendation="Read the run; consider revoking the grant."))

        log = evidence.get("grants")
        checks.append(check("ace.grants", "Auto-dispatch grants", log is not None,
                            errors.get("grants") or "ace_dispatch_grants not read"))
        grants: dict = {}
        for g in log or []:  # oldest first
            if g["grant_id"] == "*":
                continue
            if g["event"] == "granted":
                grants[g["grant_id"]] = dict(g, state="live", closed_at="")
            elif g["grant_id"] in grants and grants[g["grant_id"]]["state"] == "live":
                grants[g["grant_id"]].update(state=g["event"], closed_at=g["at"])
        day = now[:10]
        today: dict = {}
        for r in results or []:
            if r["status"] == "auto_launched" and r["at"][:10] == day:
                today[r["grant_id"]] = today.get(r["grant_id"], 0) + 1
        warn_days = float(policy.get("grant_expiry_warn_days", 3))
        live = 0
        for gid, g in sorted(grants.items()):
            left = age_hours(now, g["expires_at"])
            if g["state"] == "live" and left is not None and left <= 0:
                g["state"] = "expired"
            if g["state"] == "live":
                live += 1
                if today.get(gid, 0) >= g["max_per_day"] > 0:
                    findings.append(finding(
                        "ace.grant.at_limit", "medium",
                        f"Grant {gid} ({g['role_id']}/{g['topic']}) hit max_per_day "
                        f"{g['max_per_day']}",
                        thread_id=f"ace_grant:{gid}",
                        recommendation="Later events today are recommendations; raise the "
                                       "limit only through a new grant."))
                if left is not None and left <= warn_days * 24:
                    findings.append(finding(
                        "ace.grant.expiring", "low",
                        f"Grant {gid} ({g['role_id']}/{g['topic']}) expires in {left:.0f}h",
                        thread_id=f"ace_grant:{gid}",
                        recommendation="Grant it again on /coworker if it should continue."))
            elif g["state"] == "suspended":
                findings.append(finding(
                    "ace.grant.suspended", "medium",
                    f"Grant {gid} ({g['role_id']}/{g['topic']}) suspended: trust fell",
                    thread_id=f"ace_grant:{gid}", detail=f"at {g['closed_at']}",
                    recommendation="Events are recommendations again; a person may grant anew "
                                   "once trust recovers."))
        return {"events_unprocessed": len(events or []),
                "recommendations_pending": sum(1 for r in results or []
                                               if r["status"] == "recommended"),
                "auto_failed": len(auto_failed), "grants_live": live}
