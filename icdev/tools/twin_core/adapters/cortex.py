# CUI // SP-CTI — Cortex governance twin adapter (icx-cnf-03)
"""Governance twin over ICDEV Cortex (``tools/cortex``), canvas key ``cortex``.

Cortex has no design graph to freeze (twx-cov-02 parked it for that reason).
What it has is a governed call path, and that is what this twin observes:

* **TRUST-gate outcomes** — per gate, how many governed calls passed, warned,
  failed or skipped in the window; how many were BLOCKED and at which gate; how
  many redactions ran. A mandatory gate (``output_redaction``, ``provenance``)
  that failed or never ran on a call that was not blocked is a hole; a gate that
  failed while the call went on is a fail-open; an unblocked call with no
  provenance record cannot be traced.
* **cortex_audit chain health** — the table is declared append-only at the agent
  hook (``APPEND_ONLY_TABLES``). This twin checks whether the DATABASE also
  refuses UPDATE/DELETE (a trigger), and whether rows carry a hash chain that
  could prove nothing was altered. It reports what is there; it does not invent
  a chain the table does not have.
* **Unadjudicated findings** — every conflict in ``cortex_entity_findings`` is
  one a person must adjudicate (the store keeps no winner and no adjudication
  state yet), aged from first seen.
* **Backend liveness** — ``capability_consumption.probe_cortex_backend``: a rung
  that died, or one the router asked that never answered.

Each finding's ``thread_id`` names its record: ``cortex_audit:<id>`` or
``cortex_finding:<id>``, which :func:`tools.twin_core.governance.resolve_thread`
follows to the row and its ``source_citation_registry`` provenance.
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

AUDIT_TABLE = "cortex_audit"
FINDINGS_TABLE = "cortex_entity_findings"
#: Gates no governance profile may omit (tools/cortex/governance.MANDATORY_GATES,
#: minus ``operation``, which is the call itself).
MANDATORY_GATES = ("output_redaction", "provenance")


def _json(value) -> dict:
    import json

    if isinstance(value, dict):
        return value
    try:
        out = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _hook_declares_append_only(table: str) -> bool | None:
    """Is ``table`` in APPEND_ONLY_TABLES (.claude/hooks/pre_tool_use.py)? Read as text."""
    from tools.twin_core.governance import BASE_DIR

    try:
        text = (BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
    except OSError:
        return None
    return f'"{table}"' in text


def _db_triggers(conn, table: str) -> list[str]:
    """Names of the database triggers on ``table`` (UPDATE/DELETE guards)."""
    from tools.db.storage import is_pg

    if is_pg(conn):
        rows = conn.execute(
            "SELECT trigger_name, event_manipulation FROM information_schema.triggers "
            "WHERE event_object_table = %s", (table,)).fetchall()
        return sorted({f"{r[0]}:{r[1]}".lower() for r in rows})
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = %s",
        (table,)).fetchall()
    out = []
    for r in rows:
        sql = str(r[1] or "").upper()
        event = "update" if "UPDATE ON" in sql else "delete" if "DELETE ON" in sql else "other"
        out.append(f"{str(r[0]).lower()}:{event}")
    return sorted(out)


@register_twin
class CortexGovernanceTwin(GovernanceTwinAdapter):
    canvas_key = "cortex"

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    # -- collect (reads; the only impure half) -------------------------------

    def collect(self) -> dict:
        from tools.db.storage import column_exists, get_connection, table_exists

        policy = load_policy("cortex")
        observed_at = now_iso()
        since = (datetime.now(timezone.utc) - timedelta(hours=policy["window_hours"])
                 ).isoformat(timespec="seconds")
        ev: dict = {"canvas": self.canvas_key, "target_id": "fleet",
                    "observed_at": observed_at, "since": since, "policy": policy,
                    "audit": None, "chain": None, "findings": None, "backends": None,
                    "errors": {}}
        conn = None
        try:
            conn = get_connection()
            if table_exists(conn, AUDIT_TABLE):
                rows = rows_as_dicts(conn.execute(
                    f"SELECT id, outcome, blocked, provenance_id, gates_json, created_at "  # nosec B608
                    f"FROM {AUDIT_TABLE} WHERE created_at >= %s "
                    "ORDER BY created_at DESC, id DESC LIMIT %s",
                    (since, int(policy["audit_scan_limit"]) + 1)),
                    ("id", "outcome", "blocked", "provenance_id", "gates_json", "created_at"))
                truncated = len(rows) > int(policy["audit_scan_limit"])
                calls = []
                for r in rows[: int(policy["audit_scan_limit"])]:
                    g = _json(r["gates_json"])
                    backends = g.get("backends") if isinstance(g.get("backends"), dict) else {}
                    calls.append({
                        "id": str(r["id"]),
                        "at": ts_text(r["created_at"]),
                        "outcome": str(r["outcome"] or ""),
                        "blocked": bool(r["blocked"]),
                        "blocked_gate": str(g.get("blocked_gate") or ""),
                        "provenance_id": str(r["provenance_id"] or ""),
                        "profile": str(g.get("profile") or "default"),
                        "gates_run": sorted(str(x) for x in (g.get("gates_run") or [])),
                        "outcomes": {str(k): str(v) for k, v in
                                     sorted((g.get("outcomes") or {}).items())},
                        "redactions": int(g.get("redactions_applied") or 0),
                        "backends_failed": sorted(str(x) for x in
                                                  (backends.get("failed") or []) if x),
                    })
                ev["audit"] = {"rows": calls, "truncated": truncated}
                ev["chain"] = {
                    "hook_append_only": _hook_declares_append_only(AUDIT_TABLE),
                    "db_triggers": _db_triggers(conn, AUDIT_TABLE),
                    "hash_columns": [c for c in ("hash", "previous_hash", "row_hash", "prev_hash")
                                     if column_exists(conn, AUDIT_TABLE, c)],
                    "latest_id": "",
                }
                newest = conn.execute(
                    f"SELECT id FROM {AUDIT_TABLE} ORDER BY created_at DESC, id DESC LIMIT 1"  # nosec B608
                ).fetchone()
                if newest:
                    ev["chain"]["latest_id"] = str(newest[0])
            else:
                ev["errors"]["audit"] = f"{AUDIT_TABLE} does not exist"
        except Exception as exc:  # noqa: BLE001 — unreadable is unverified, never a pass
            ev["errors"]["audit"] = str(exc)
        finally:
            close_quietly(conn)

        try:
            from tools.cortex.finding_store import finding_stats, list_findings

            stats = finding_stats()
            ev["findings"] = {"state": stats["state"], "detail": stats.get("detail") or "",
                              "conflicts": []}
            if stats["state"] not in ("disabled", "unmeasured"):
                ev["findings"]["conflicts"] = sorted(
                    ({"id": f["finding_id"], "entity": f.get("entity_label") or f["entity_key"],
                      "kind": f.get("conflict_kind") or "",
                      "first_seen_at": ts_text(f.get("first_seen_at")),
                      "seen_count": int(f.get("seen_count") or 0)}
                     for f in list_findings(finding_type="conflict", limit=1000)),
                    key=lambda c: (c["first_seen_at"], c["id"]))
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["findings"] = str(exc)

        conn = None
        try:
            from tools.awareness.capability_consumption import probe_cortex_backend

            conn = get_connection()
            res = probe_cortex_backend(conn, datetime.fromisoformat(since), 0, 40).to_dict()
            ev["backends"] = {k: res.get(k) for k in (
                "telemetry_available", "unmeasured_reason", "declared", "consumed", "events")}
            extra = res.get("extra") or {}
            ev["backends"]["failed_events"] = dict(sorted(
                (extra.get("failed_events") or {}).items()))
            ev["backends"]["consulted_never_used"] = sorted(
                extra.get("consulted_never_used") or [])
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["backends"] = str(exc)
        finally:
            close_quietly(conn)
        return ev

    # -- judge (pure) --------------------------------------------------------

    def judge(self, evidence: dict) -> dict:
        policy = evidence.get("policy") or {}
        cap = int(policy.get("max_findings") or 50)
        errors = evidence.get("errors") or {}
        audit = evidence.get("audit")
        rows = (audit or {}).get("rows") or []
        checks, findings = [], []

        # TRUST-gate outcomes ------------------------------------------------
        gate_counts: dict = {}
        blocked_by_gate: dict = {}
        redactions = 0
        for r in rows:
            for gate, outcome in r["outcomes"].items():
                gate_counts.setdefault(gate, {}).setdefault(outcome, 0)
                gate_counts[gate][outcome] += 1
            if r["blocked"]:
                gate = r["blocked_gate"] or "unknown"
                blocked_by_gate[gate] = blocked_by_gate.get(gate, 0) + 1
            redactions += r["redactions"]
        checks.append(check(
            "cortex.trust_gates", "TRUST-gate outcomes on governed calls",
            audit is not None and bool(rows),
            errors.get("audit") or "no governed Cortex call in the window: unmeasured"))

        holes, fail_open, untraced = [], [], []
        for r in rows:
            if r["blocked"]:
                continue  # a block stops the chain; later gates rightly did not run
            if r["outcomes"].get("operation") != "pass":
                continue  # the call itself failed: no output, nothing to redact or cite
            for gate in MANDATORY_GATES:
                if r["outcomes"].get(gate) not in ("pass", "warn"):
                    holes.append((r, gate))
            failed = sorted(g for g, o in r["outcomes"].items()
                            if o == "fail" and g not in MANDATORY_GATES + ("operation",))
            if failed:
                fail_open.append((r, failed))
            if not r["provenance_id"]:
                untraced.append(r)
        for r, gate in holes[:cap]:
            findings.append(finding(
                "cortex.gate.mandatory", "critical",
                f"Mandatory gate '{gate}' did not pass on an unblocked call",
                thread_id=f"cortex_audit:{r['id']}",
                detail=f"{r['at']} outcome={r['outcomes'].get(gate, 'absent')} "
                       f"profile={r['profile']}",
                recommendation="output_redaction and provenance run on every call; find why "
                               "this one went out without them.", category="security"))
        for r, failed in fail_open[:cap]:
            findings.append(finding(
                "cortex.gate.fail_open", "medium",
                f"Gate failed and the call went on: {', '.join(failed)}",
                thread_id=f"cortex_audit:{r['id']}", detail=f"{r['at']} outcome={r['outcome']}",
                recommendation="Fail-open is the configured default; set fail_closed for "
                               "callers that must not proceed on a failed gate."))
        if untraced:
            newest = untraced[0]
            findings.append(finding(
                "cortex.audit.no_provenance", "medium",
                f"{len(untraced)} unblocked governed call(s) have no provenance record",
                thread_id=f"cortex_audit:{newest['id']}",
                detail="newest " + newest["at"],
                recommendation="The provenance gate writes a source_citation_registry row; "
                               "check its citation_type and the registry write."))

        # cortex_audit chain health ------------------------------------------
        chain = evidence.get("chain")
        checks.append(check("cortex.audit_chain", "cortex_audit append-only and chain",
                            chain is not None, errors.get("audit") or "cortex_audit not read"))
        if chain is not None:
            thread = (f"cortex_audit:{chain['latest_id']}" if chain["latest_id"]
                      else self.obs_thread(evidence, "cortex.audit_chain"))
            guards = {t.split(":")[-1] for t in chain["db_triggers"]}
            if chain["hook_append_only"] is False:
                findings.append(finding(
                    "cortex.audit.hook", "high", "cortex_audit is not in APPEND_ONLY_TABLES",
                    thread_id=thread, recommendation="Add it to .claude/hooks/pre_tool_use.py."))
            if not {"update", "delete"} <= guards:
                findings.append(finding(
                    "cortex.audit.db_mutable", "medium",
                    "The database accepts UPDATE/DELETE on cortex_audit",
                    thread_id=thread,
                    detail="append-only is enforced only at the agent hook; triggers found: "
                           + (", ".join(chain["db_triggers"]) or "none"),
                    recommendation="Install BEFORE UPDATE/DELETE triggers, as aisg_audit has."))
            if not chain["hash_columns"]:
                findings.append(finding(
                    "cortex.audit.unchained", "medium",
                    "cortex_audit rows carry no hash chain, so tampering cannot be detected",
                    thread_id=thread,
                    recommendation="Chain the rows (hash, previous_hash) as audit_trail is, "
                                   "then verify the chain here."))
            else:
                findings.append(finding(
                    "cortex.audit.chain_unverified", "low",
                    "cortex_audit has chain columns but no verifier reads them",
                    thread_id=thread, detail=", ".join(chain["hash_columns"])))

        # unadjudicated findings ---------------------------------------------
        fs = evidence.get("findings")
        measured = fs is not None and fs["state"] not in ("disabled", "unmeasured")
        checks.append(check("cortex.findings", "Resolution conflicts awaiting a person",
                            measured, errors.get("findings")
                            or (fs or {}).get("detail") or "finding store not read"))
        aged = []
        for c in (fs or {}).get("conflicts") or []:
            hours = age_hours(c["first_seen_at"], evidence["observed_at"])
            if hours is None:
                continue
            days = hours / 24.0
            if days >= float(policy.get("finding_fail_days", 30)):
                aged.append((c, "critical", days))
            elif days >= float(policy.get("finding_warn_days", 7)):
                aged.append((c, "high", days))
        for c, sev, days in aged[:cap]:
            findings.append(finding(
                "cortex.finding.unadjudicated", sev,
                f"Conflict on '{c['entity']}' unadjudicated for {days:.1f} days",
                thread_id=f"cortex_finding:{c['id']}",
                detail=f"kind={c['kind']} seen {c['seen_count']}x since {c['first_seen_at']}",
                recommendation="A person adjudicates a conflict; open it in the "
                               "Document Intelligence explorer."))
        if len(aged) > cap:
            findings.append(finding(
                "cortex.finding.more", "medium", f"{len(aged) - cap} more aged conflict(s)",
                thread_id=f"cortex_finding:{aged[cap][0]['id']}"))

        # backend liveness ---------------------------------------------------
        be = evidence.get("backends")
        checks.append(check("cortex.backends", "Cortex backend liveness",
                            be is not None and bool(be.get("telemetry_available")),
                            errors.get("backends") or (be or {}).get("unmeasured_reason")
                            or "backend probe not run"))
        if be and be.get("telemetry_available"):
            newest_fail = {}
            for r in rows:  # rows are newest first
                for name in r["backends_failed"]:
                    newest_fail.setdefault(name, r["id"])
            for name, count in sorted((be.get("failed_events") or {}).items()):
                thread = (f"cortex_audit:{newest_fail[name]}" if name in newest_fail
                          else self.obs_thread(evidence, "cortex.backends"))
                findings.append(finding(
                    "cortex.backend.failed", "high", f"Backend '{name}' failed {count} time(s)",
                    thread_id=thread, category="service_parity",
                    recommendation="A rung that dies degrades every resolution routed to it."))
            for name in be.get("consulted_never_used") or []:
                findings.append(finding(
                    "cortex.backend.silent", "medium",
                    f"Backend '{name}' was asked and never answered",
                    thread_id=self.obs_thread(evidence, "cortex.backends"),
                    category="service_parity",
                    recommendation="Usually an empty corpus or a dead adapter."))

        summary = {
            "governed_calls": len(rows),
            "truncated": bool((audit or {}).get("truncated")),
            "blocked": sum(1 for r in rows if r["blocked"]),
            "blocked_by_gate": dict(sorted(blocked_by_gate.items())),
            "redactions": redactions,
            "gate_outcomes": {g: dict(sorted(v.items())) for g, v in sorted(gate_counts.items())},
            "conflicts_open": len((fs or {}).get("conflicts") or []),
            "finding_state": (fs or {}).get("state"),
            "backends_declared": (be or {}).get("declared"),
            "backends_answering": (be or {}).get("consumed"),
        }
        return {"checks": checks, "findings": findings, "summary": summary}
