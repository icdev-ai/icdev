# CUI // SP-CTI
"""Append-only store for detection findings (agov-det-05).

A finding is one observation: at a point in time, rule X matched this ordered
list of events in this session. Writes land in ``agent_findings`` (migration
20260809201320, registered in ``APPEND_ONLY_TABLES``), falling back to the
``hook_events`` trail when that table is absent — a checkout whose database
predates the migration must still leave a record rather than losing the finding.

Two properties this module owes its callers, because agov-det-06 calls it from
``.claude/hooks/pre_tool_use.py``, which runs before *every* tool call:

1. **It never raises.** A detection store that can break the hook is a worse
   problem than the detections it stores. Every failure path degrades and
   returns, and an unrecorded finding is logged at ERROR.
2. **It is bounded.** One indexed primary-key lookup and one INSERT per
   finding, no scans.

Re-observation is idempotent by construction. ``finding_id`` is a SHA-256 over
(rule_id, rule_version, session_id, event_ids), so a sequence rule that keeps
matching the same chain as the window slides forward writes the row once instead
of once per subsequent tool call. This is not a workaround for the append-only
invariant — appending the *identical* observation a second time records no new
fact. A genuinely new match cites different events and gets a different id.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

TABLE = "agent_findings"

#: Rule severities. The single source for this vocabulary — the migration
#: deliberately carries no CHECK constraint that would drift from it, and the
#: rule loader (agov-det-03) validates `severity:` against this same tuple.
SEVERITIES = ("info", "low", "medium", "high", "critical")

#: What happened to the tool call. ``observed`` = recorded and allowed (the
#: monitor-only default, and every offline scan); ``denied`` = the call was
#: blocked and the caller received the rule's deny_message.
DECISIONS = ("observed", "denied")

#: Columns written by :func:`record_finding`, in INSERT order. Kept adjacent to
#: the statement below so the two cannot drift; asserted against the live schema
#: by tests/test_agov_agent_findings.py and by coherence check 20.
COLUMNS = (
    "finding_id",
    "rule_id",
    "rule_version",
    "severity",
    "title",
    "session_id",
    "actor",
    "project_id",
    "event_ids",
    "tags",
    "enforced",
    "decision",
    "classification",
    "created_at",
)


def current_session_id() -> str:
    """The session this process belongs to, or ``unknown``."""
    for key in ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID"):
        val = os.environ.get(key)
        if val:
            return val
    return "unknown"


def _json_array(values: Optional[Iterable[Any]]) -> str:
    """Serialise an ordered list to a JSON array TEXT column.

    Stored as JSON TEXT and read back with ``json.loads`` in Python. Never
    queried with ``json_extract``/``json_each``, which are SQLite-only dialect
    and would break on PostgreSQL (CLAUDE.md runtime-SQL guardrail).
    """
    if not values:
        return "[]"
    try:
        return json.dumps([str(v) for v in values])
    except Exception:  # noqa: BLE001 — a malformed tag list must not lose the finding
        return "[]"


def compute_finding_id(
    rule_id: str,
    rule_version: str,
    session_id: str,
    event_ids: Sequence[str],
) -> str:
    """Deterministic id for one (rule, version, session, ordered events) match.

    Order matters: a chain that fired A→B is a different observation from one
    that fired B→A, so the ids differ.
    """
    payload = "\x1f".join(
        [
            str(rule_id),
            str(rule_version),
            str(session_id),
            *[str(e) for e in event_ids],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def build_finding(
    *,
    rule_id: str,
    rule_version: str = "1",
    severity: str = "info",
    title: str = "",
    session_id: str = "",
    actor: str = "",
    project_id: str = "",
    event_ids: Optional[Sequence[str]] = None,
    tags: Optional[Sequence[str]] = None,
    enforced: bool = False,
    decision: str = "observed",
    classification: str = "CUI",
) -> Dict[str, Any]:
    """Assemble one finding row. Pure — no I/O, so it is safe to unit test.

    An unrecognised severity or decision is normalised rather than rejected: the
    caller is a detection path, and dropping the finding because a rule author
    typed ``"HIGH"`` would lose the signal the rule exists to surface. The rule
    loader is where a bad vocabulary value gets reported.
    """
    sev = str(severity or "").strip().lower()
    if sev not in SEVERITIES:
        sev = "info"
    dec = str(decision or "").strip().lower()
    if dec not in DECISIONS:
        dec = "observed"

    ordered_events = [str(e) for e in (event_ids or [])]
    sid = session_id or current_session_id()

    return {
        "finding_id": compute_finding_id(rule_id, rule_version, sid, ordered_events),
        "rule_id": str(rule_id),
        "rule_version": str(rule_version),
        "severity": sev,
        "title": str(title or rule_id),
        "session_id": sid,
        "actor": str(actor or ""),
        "project_id": str(project_id or ""),
        "event_ids": _json_array(ordered_events),
        "tags": _json_array(tags),
        "enforced": bool(enforced),
        "decision": dec,
        "classification": str(classification or "CUI"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _already_recorded(conn, finding_id: str) -> bool:
    """One primary-key lookup. Any failure answers 'no' and lets the INSERT decide."""
    try:
        cur = conn.execute(
            "SELECT 1 FROM agent_findings WHERE finding_id = %s LIMIT 1",
            (finding_id,),
        )
        return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def record_finding(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Append one finding. Returns ``{finding_id, persisted, sink, duplicate}``.

    Never raises. ``sink`` is ``agent_findings``, ``hook_events`` (the fallback
    trail) or ``none`` when the finding could not be recorded at all.
    """
    result = {
        "finding_id": finding.get("finding_id", ""),
        "persisted": False,
        "sink": "none",
        "duplicate": False,
    }

    try:
        from tools.db.storage import get_connection, table_exists

        conn = get_connection()
        try:
            if table_exists(conn, TABLE):
                if _already_recorded(conn, finding["finding_id"]):
                    result.update(persisted=True, sink=TABLE, duplicate=True)
                    return result
                # Static column list on purpose: coherence check 20
                # (check_insert_schema_parity) only validates INSERTs it can read
                # statically, and this table's whole job is to be trustworthy.
                conn.execute(
                    "INSERT INTO agent_findings ("
                    "finding_id, rule_id, rule_version, severity, title, "
                    "session_id, actor, project_id, event_ids, tags, "
                    "enforced, decision, classification, created_at"
                    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    tuple(finding[c] for c in COLUMNS),
                )
                conn.commit()
                result.update(persisted=True, sink=TABLE)
                return result
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 — fall through to hook_events
        logger.warning("agent_detect.findings: %s write failed: %s", TABLE, exc)

    try:
        from tools.airgap.hook_compat import store_event

        store_event(
            finding.get("session_id") or current_session_id(),
            "agent_finding",
            finding.get("rule_id", "unknown"),
            finding,
        )
        result.update(persisted=True, sink="hook_events")
        return result
    except Exception as exc:  # noqa: BLE001 — detection must not break the caller
        logger.error(
            "agent_detect.findings: FINDING UNRECORDED for rule %s: %s",
            finding.get("rule_id", "unknown"),
            exc,
        )
    return result


def record(**kwargs: Any) -> Dict[str, Any]:
    """Convenience: :func:`build_finding` then :func:`record_finding`."""
    return record_finding(build_finding(**kwargs))


#: The four reads, spelled out. Composing this SQL at runtime — even from a
#: constant column list and an integer limit — is a B608 finding and, more to
#: the point, makes a reviewer prove the safety of a string builder instead of
#: reading a statement. Four literals is the cheaper thing to audit.
#:
#: The cost is four copies of the column list, which is real drift risk — rows
#: are zipped against ``COLUMNS`` positionally, so a query whose order differed
#: would mislabel every field it returned. All four are held to ``COLUMNS`` by
#: ``test_every_select_matches_the_columns_constant``.
_QUERIES = {
    (False, False): (
        "SELECT finding_id, rule_id, rule_version, severity, title, session_id, "
        "actor, project_id, event_ids, tags, enforced, decision, classification, "
        "created_at FROM agent_findings ORDER BY created_at DESC LIMIT %s"
    ),
    (True, False): (
        "SELECT finding_id, rule_id, rule_version, severity, title, session_id, "
        "actor, project_id, event_ids, tags, enforced, decision, classification, "
        "created_at FROM agent_findings WHERE session_id = %s "
        "ORDER BY created_at DESC LIMIT %s"
    ),
    (False, True): (
        "SELECT finding_id, rule_id, rule_version, severity, title, session_id, "
        "actor, project_id, event_ids, tags, enforced, decision, classification, "
        "created_at FROM agent_findings WHERE rule_id = %s "
        "ORDER BY created_at DESC LIMIT %s"
    ),
    (True, True): (
        "SELECT finding_id, rule_id, rule_version, severity, title, session_id, "
        "actor, project_id, event_ids, tags, enforced, decision, classification, "
        "created_at FROM agent_findings WHERE session_id = %s AND rule_id = %s "
        "ORDER BY created_at DESC LIMIT %s"
    ),
}


def list_findings(
    session_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Read findings back, newest first. Returns ``[]`` rather than raising.

    Bounded by ``limit`` and served by the (session_id, created_at) and
    (rule_id, created_at) indexes the migration creates.
    """
    limit = max(1, min(int(limit or 100), 1000))
    params: List[Any] = []
    if session_id:
        params.append(session_id)
    if rule_id:
        params.append(rule_id)
    params.append(limit)
    sql = _QUERIES[(bool(session_id), bool(rule_id))]

    try:
        from tools.db.storage import get_connection, table_exists

        conn = get_connection()
        try:
            if not table_exists(conn, TABLE):
                return []
            cur = conn.execute(sql, tuple(params))
            rows = cur.fetchall() or []
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_detect.findings: read failed: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for row in rows:
        rec = dict(zip(COLUMNS, tuple(row)))
        # JSON columns are parsed here, in Python, not in SQL.
        for col in ("event_ids", "tags"):
            try:
                rec[col] = json.loads(rec.get(col) or "[]")
            except Exception:  # noqa: BLE001
                rec[col] = []
        rec["enforced"] = bool(rec.get("enforced"))
        out.append(rec)
    return out


def new_correlation_id() -> str:
    """An opaque id for grouping findings emitted by one evaluation pass."""
    return uuid.uuid4().hex
