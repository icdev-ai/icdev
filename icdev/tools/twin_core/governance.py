# CUI // SP-CTI — Twin Core governance twin: record, replay, thread (icx-cnf-03)
"""Governance twins: observe a governed subsystem, record the evidence, replay it.

The canvas twins in ``tools/twin_core/adapters`` freeze a design and simulate a
delta. A governance twin has no design to freeze. It observes how a governed
subsystem behaved — Cortex's TRUST chain, ACE's runs and checkpoints, the
Co-Workers roster, the conformance audit — and judges it.

Every governance adapter splits the work in two:

* ``collect()`` reads the subsystem and returns EVIDENCE: plain JSON, the clock
  reading it was taken at, and the policy (thresholds) it will be judged by.
* ``judge(evidence)`` is pure. Same evidence in, same verdict out. It reads no
  clock, no database and no config; ages are computed against the evidence's own
  ``observed_at``.

:func:`observe` runs both, records the evidence and the verdict's digest in
``twin_governance_observations`` (append-only), and returns the canonical
envelope from :mod:`tools.twin_core.schema`. :func:`replay` reloads the recorded
evidence, checks its digest, re-judges it and compares the verdict digest. A
replay that does not match is a finding in its own right.

Three rules hold everywhere:

1. **Nothing observed is never a pass.** A source that cannot be read is an
   UNVERIFIED check: it lowers ``coverage_score`` and appears as a low-severity
   violation saying what was not read. A verdict is ``pass`` only when every
   check was verified and nothing was found; with no finding and any check
   unverified it is ``unknown``.
2. **Every violation carries a thread.** ``thread_id`` names the record the
   finding rests on (``cortex_audit:<id>``, ``ace_audit:<id>``, ...).
   :func:`resolve_thread` follows it to that record and, where the record names
   one, on to its ``source_citation_registry`` provenance row.
3. **The schema module is not touched.** :func:`tools.twin_core.schema.twin_verdict`
   builds the envelope; ``thread_id`` is added to each canonical violation after,
   in order, because the vendored schema keeps only its own fields.

CLI::

    python -m tools.twin_core.governance --observe [--canvas cortex] --json
    python -m tools.twin_core.governance --list [--canvas ace] --json
    python -m tools.twin_core.governance --replay <observation_id> --json
    python -m tools.twin_core.governance --thread cortex_audit:cgov-... --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from icdev.core.paths import repo_root

from tools.logging.icdev_logger import get_logger
from tools.twin_core.registry import TwinAdapter, TwinRegistry
from tools.twin_core.schema import normalize_severity

BASE_DIR = repo_root(__file__)

logger = get_logger("icdev.twin_core.governance")

TABLE = "twin_governance_observations"
CONFIG_PATH = BASE_DIR / "args" / "twin_governance.yaml"

#: Bumped when a judge changes what it concludes from the same evidence. A
#: replay under a newer judge version is reported as such, never as tampering.
JUDGE_VERSION = "1"

CHECK_VERIFIED = "verified"
CHECK_UNVERIFIED = "unverified"

#: Severities that move a verdict. ``low`` is advisory (an abort a person chose,
#: a check that could not be read) and never turns a verdict to warn or fail.
_FAIL = ("blocker", "critical")
_WARN = ("high", "medium")

# PostgreSQL DDL is canonical; SQLite reads the same text (TEXT/REAL/INTEGER).
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id               TEXT PRIMARY KEY,
    canvas           TEXT NOT NULL,
    target_id        TEXT NOT NULL DEFAULT 'fleet',
    verdict          TEXT NOT NULL
        CHECK (verdict IN ('pass', 'warn', 'fail', 'unknown')),
    coverage_score   REAL,
    violation_count  INTEGER NOT NULL DEFAULT 0,
    counts_json      TEXT NOT NULL DEFAULT '{{}}',
    evidence_json    TEXT NOT NULL,
    evidence_digest  TEXT NOT NULL,
    verdict_digest   TEXT NOT NULL,
    judge_version    TEXT NOT NULL,
    observed_at      TEXT NOT NULL,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_twin_gov_obs_canvas ON {TABLE}(canvas, observed_at);
"""

SQLITE_TRIGGERS = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update
    BEFORE UPDATE ON {TABLE}
    BEGIN
        SELECT RAISE(ABORT, 'Twin observations are immutable — NIST AU-9');
    END;
CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete
    BEFORE DELETE ON {TABLE}
    BEGIN
        SELECT RAISE(ABORT, 'Twin observations cannot be deleted — NIST AU-9');
    END;
"""

# One statement each: executescript's ';' split would cut the $$ body in two.
PG_TRIGGER_STATEMENTS = (
    f"""CREATE OR REPLACE FUNCTION {TABLE}_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Twin observations are immutable — NIST AU-9';
END;
$$ LANGUAGE plpgsql""",
    f"DROP TRIGGER IF EXISTS {TABLE}_no_update ON {TABLE}",
    f"""CREATE TRIGGER {TABLE}_no_update
    BEFORE UPDATE ON {TABLE}
    FOR EACH ROW EXECUTE FUNCTION {TABLE}_immutable()""",
    f"DROP TRIGGER IF EXISTS {TABLE}_no_delete ON {TABLE}",
    f"""CREATE TRIGGER {TABLE}_no_delete
    BEFORE DELETE ON {TABLE}
    FOR EACH ROW EXECUTE FUNCTION {TABLE}_immutable()""",
)

_SCHEMA_ENSURED = False


# ── small helpers shared by the adapters ─────────────────────────────────────


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: Any) -> Optional[datetime]:
    """Parse a DB timestamp (ISO text, SQLite ``YYYY-MM-DD HH:MM:SS`` or datetime)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ts_text(value: Any) -> str:
    """A timestamp as stable ISO text for evidence (``""`` when absent)."""
    dt = parse_ts(value)
    return dt.isoformat(timespec="seconds") if dt else ""


def age_hours(then: Any, observed_at: str) -> Optional[float]:
    """Hours from ``then`` to the evidence's own clock reading (never ``now``)."""
    start, end = parse_ts(then), parse_ts(observed_at)
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 3600.0, 2)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def rows_as_dicts(cursor, columns: Iterable[str]) -> list[dict]:
    """Rows as dicts whether the driver returns tuples or mappings."""
    cols = tuple(columns)
    out = []
    for row in cursor.fetchall():
        if hasattr(row, "keys"):
            out.append({c: row[c] for c in cols})
        else:
            out.append(dict(zip(cols, row)))
    return out


def close_quietly(conn) -> None:
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def check(check_id: str, label: str, verified: bool, reason: str = "") -> dict:
    """One evidence check: ``verified`` (it was read) or ``unverified`` (and why)."""
    return {
        "id": check_id,
        "label": label,
        "status": CHECK_VERIFIED if verified else CHECK_UNVERIFIED,
        "reason": "" if verified else (reason or "not read"),
    }


def finding(rule_id: str, severity: str, title: str, *, thread_id: str,
            detail: str = "", recommendation: str = "",
            category: str = "compliance") -> dict:
    """One violation, with the thread it rests on. ``thread_id`` is mandatory."""
    if not thread_id:
        raise ValueError(f"finding {rule_id} has no thread_id")
    return {
        "rule_id": rule_id,
        "severity": normalize_severity(severity),
        "category": category,
        "title": title,
        "detail": detail,
        "recommendation": recommendation or title,
        "thread_id": thread_id,
    }


def unverified_findings(canvas: str, checks: list[dict], evidence_digest: str) -> list[dict]:
    """Each unverified check, stated as a low-severity violation.

    Its thread is the observation itself (``twin_obs:<id>#<check>``): the record
    of what was attempted and why it could not be read.
    """
    obs_id = observation_id(canvas, evidence_digest)
    return [
        finding(f"{c['id']}.unverified", "low", f"Not verified: {c['label']}",
                thread_id=f"twin_obs:{obs_id}#{c['id']}", detail=c["reason"],
                recommendation="Make the source readable; until then this check counts "
                               "for nothing (coverage_score falls).")
        for c in checks if c["status"] == CHECK_UNVERIFIED
    ]


def conclude(checks: list[dict], findings: list[dict]) -> tuple[str, float]:
    """Verdict and coverage from checks and findings. Never ``pass`` on nothing."""
    verified = sum(1 for c in checks if c["status"] == CHECK_VERIFIED)
    coverage = round(verified / len(checks), 4) if checks else 0.0
    severities = {f["severity"] for f in findings}
    if severities & set(_FAIL):
        return "fail", coverage
    if severities & set(_WARN):
        return "warn", coverage
    if not checks or verified < len(checks):
        return "unknown", coverage
    return "pass", coverage


def observation_id(canvas: str, evidence_digest: str) -> str:
    """Deterministic: the same canvas and evidence name the same observation."""
    return "tgo-" + hashlib.sha256(f"{canvas}|{evidence_digest}".encode()).hexdigest()[:20]


def load_policy(section: str, path: Optional[Path] = None) -> dict:
    """``args/twin_governance.yaml`` section (thresholds). Copied into evidence."""
    import yaml

    try:
        data = yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        logger.warning("twin_governance config unreadable: %s", exc)
        data = {}
    block = data.get(section) or {}
    out = {"window_hours": int(data.get("window_hours") or 168),
           "max_findings": int(data.get("max_findings") or 50)}
    out.update(block if isinstance(block, dict) else {})
    return out


# ── the adapter base ─────────────────────────────────────────────────────────


class GovernanceTwinAdapter(TwinAdapter):
    """Base for governance twins: ``collect`` + pure ``judge``, recorded and replayable.

    Subclasses set ``canvas_key`` and implement ``collect()`` and ``judge()``,
    and define ``replay()`` (delegating to :func:`replay`), so the conformance
    audit, which reads each adapter class without importing it, sees it there.
    """

    method = "governance-observed"
    supports_snapshots = True
    supports_simulation = False
    #: Observations are this twin's snapshots; the observer reads freshness here.
    snapshot_table = TABLE
    snapshot_time_col = "observed_at"
    #: Feature keys this twin observes (the conformance audit reads it).
    observes: tuple[str, ...] = ()

    def collect(self) -> dict:  # pragma: no cover — abstract
        raise NotImplementedError

    def judge(self, evidence: dict) -> dict:  # pragma: no cover — abstract
        """Return ``{"checks", "findings", "summary"}``. Pure."""
        raise NotImplementedError

    def obs_thread(self, evidence: dict, check_id: str) -> str:
        """Thread to this observation's own record of a check (``twin_obs:<id>#<check>``)."""
        return f"twin_obs:{observation_id(self.canvas_key, digest(evidence))}#{check_id}"

    # -- envelope ------------------------------------------------------------

    def envelope(self, evidence: dict, *, observation: Optional[str] = None) -> dict:
        """Judge ``evidence`` and wrap it in the canonical schema envelope."""
        ev_digest = digest(evidence)
        judged = self.judge(evidence)
        checks = judged["checks"]
        findings = list(judged["findings"]) + unverified_findings(
            self.canvas_key, checks, ev_digest)
        verdict, coverage = conclude(checks, findings)
        core = {"verdict": verdict, "coverage_score": coverage, "checks": checks,
                "findings": findings, "summary": judged.get("summary") or {}}
        obs_id = observation or observation_id(self.canvas_key, ev_digest)
        env = self._wrap(
            evidence.get("target_id") or "fleet", verdict, findings,
            snapshot_id=obs_id, coverage_score=coverage,
            extra={
                "observation_id": obs_id,
                "observed_at": evidence.get("observed_at"),
                "evidence_digest": ev_digest,
                "verdict_digest": digest(core),
                "judge_version": JUDGE_VERSION,
                "checks": checks,
                "summary": core["summary"],
                "observes": list(self.observes),
            },
        )
        # twin_verdict keeps only the schema's own fields; carry each finding's
        # thread (and nothing else) onto its canonical violation, in order.
        for violation, src in zip(env["violations"], findings):
            violation["thread_id"] = src["thread_id"]
        return env

    # -- TwinAdapter surface -------------------------------------------------

    def take_snapshot(self, target_id: str = "fleet", label: str | None = None, **kwargs) -> dict:
        return observe(self, record=bool(kwargs.get("record", True)))

    def list_snapshots(self, target_id: str = "fleet", **kwargs) -> list[dict]:
        return list_observations(self.canvas_key, limit=int(kwargs.get("limit", 20)))

    def latest_status(self, target_id: str = "fleet", **kwargs) -> dict:
        rows = list_observations(self.canvas_key, limit=1)
        latest = rows[0] if rows else None
        return {
            "canvas": self.canvas_key,
            "target_id": target_id,
            # No recorded observation: nothing observed, so not a pass.
            "verdict": latest["verdict"] if latest else "unknown",
            "snapshot_count": len(rows),
            "latest_snapshot": latest,
            "method": self.method,
        }

    def fleet_health(self, window_hours: int = 24) -> dict:
        """Observation freshness, verdicts in the window, the latest violation counts."""
        from datetime import timedelta

        out = {"available": False, "snapshot_count": None, "latest_snapshot_at": None,
               "verdicts": {}, "violation_counts": {}}
        conn = None
        try:
            conn = _connection()
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat(
                timespec="seconds")
            row = conn.execute(
                f"SELECT COUNT(*), MAX(observed_at) FROM {TABLE} WHERE canvas = %s",  # nosec B608
                (self.canvas_key,),
            ).fetchone()
            out["available"] = True
            out["snapshot_count"] = int(row[0] or 0)
            out["latest_snapshot_at"] = row[1]
            rows = conn.execute(
                f"SELECT verdict, COUNT(*) FROM {TABLE} "  # nosec B608 — constant table
                "WHERE canvas = %s AND observed_at >= %s GROUP BY verdict",
                (self.canvas_key, cutoff),
            ).fetchall()
            out["verdicts"] = {str(r[0]): int(r[1] or 0) for r in rows}
            latest = list_observations(self.canvas_key, limit=1, conn=conn)
            if latest:
                out["violation_counts"] = dict(latest[0]["counts"])
        except Exception as exc:  # noqa: BLE001 — a fresh DB has no observations yet
            logger.debug("governance fleet_health %s: %s", self.canvas_key, exc)
        finally:
            close_quietly(conn)
        return out

    def describe(self) -> dict:
        out = super().describe()
        out["governance"] = True
        out["observes"] = list(self.observes)
        return out


# ── storage ──────────────────────────────────────────────────────────────────


def _connection(create: bool = False):
    """Platform database, RLS off: this table has no tenant columns (a canvas table).

    Only a write creates the table; a read of a database that never recorded an
    observation finds nothing, and says so.
    """
    from tools.db.storage import get_canvas_connection

    conn = get_canvas_connection()
    if create:
        ensure_schema(conn)
    return conn


def ensure_schema(conn) -> None:
    """Create the observation table and its immutability triggers (idempotent)."""
    global _SCHEMA_ENSURED
    if _SCHEMA_ENSURED and getattr(conn, "_backend", "sqlite") == "postgresql":
        return
    conn.executescript(SCHEMA)
    if getattr(conn, "_backend", "sqlite") == "postgresql":
        try:
            for stmt in PG_TRIGGER_STATEMENTS:
                conn.execute(stmt)
        except Exception as exc:  # noqa: BLE001 — never block a read on trigger DDL
            conn.rollback()
            logger.warning("%s immutability triggers not installed: %s", TABLE, exc)
    else:
        conn.executescript(SQLITE_TRIGGERS)
    conn.commit()
    _SCHEMA_ENSURED = True


def record_observation(env: dict, evidence: dict, canvas: str, conn=None) -> str:
    """Append one observation. Idempotent on its deterministic id."""
    extra = env["extra"]
    own = conn is None
    conn = conn or _connection(create=True)
    try:
        exists = conn.execute(
            f"SELECT 1 FROM {TABLE} WHERE id = %s", (extra["observation_id"],)  # nosec B608
        ).fetchone()
        if not exists:
            conn.execute(
                f"INSERT INTO {TABLE} (id, canvas, target_id, verdict, coverage_score, "  # nosec B608
                "violation_count, counts_json, evidence_json, evidence_digest, verdict_digest, "
                "judge_version, observed_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (extra["observation_id"], canvas, env["target_id"], env["verdict"],
                 env["coverage_score"], len(env["violations"]), canonical_json(env["counts"]),
                 canonical_json(evidence),
                 extra["evidence_digest"], extra["verdict_digest"], extra["judge_version"],
                 extra["observed_at"]),
            )
            conn.commit()
        return extra["observation_id"]
    finally:
        if own:
            close_quietly(conn)


_LIST_COLS = ("id", "canvas", "target_id", "verdict", "coverage_score", "violation_count",
              "counts_json", "evidence_digest", "verdict_digest", "judge_version", "observed_at")


def list_observations(canvas: Optional[str] = None, limit: int = 20, conn=None) -> list[dict]:
    """Recorded observations, newest first, without their evidence blobs."""
    own = conn is None
    try:
        conn = conn or _connection()
        sql = f"SELECT {', '.join(_LIST_COLS)} FROM {TABLE}"  # nosec B608 — constants
        params: list = []
        if canvas:
            sql += " WHERE canvas = %s"
            params.append(canvas)
        sql += " ORDER BY observed_at DESC, id DESC LIMIT %s"
        params.append(max(1, min(int(limit), 500)))
        rows = rows_as_dicts(conn.execute(sql, tuple(params)), _LIST_COLS)
    except Exception as exc:  # noqa: BLE001
        logger.debug("list_observations: %s", exc)
        return []
    finally:
        if own:
            close_quietly(conn)
    for r in rows:
        try:
            r["counts"] = json.loads(r.pop("counts_json") or "{}")
        except ValueError:
            r["counts"] = {}
    return rows


def load_observation(obs_id: str, conn=None) -> Optional[dict]:
    own = conn is None
    cols = _LIST_COLS + ("evidence_json",)
    try:
        conn = conn or _connection()
        rows = rows_as_dicts(conn.execute(
            f"SELECT {', '.join(cols)} FROM {TABLE} WHERE id = %s", (obs_id,)),  # nosec B608
            cols)
    except Exception as exc:  # noqa: BLE001 — no table yet: no such observation
        logger.debug("load_observation %s: %s", obs_id, exc)
        return None
    finally:
        if own:
            close_quietly(conn)
    return rows[0] if rows else None


# ── observe / replay ─────────────────────────────────────────────────────────


def adapter_for(canvas: str) -> GovernanceTwinAdapter:
    adapter = TwinRegistry.get(canvas)
    if not isinstance(adapter, GovernanceTwinAdapter):
        raise LookupError(f"no governance twin registered for {canvas!r}")
    return adapter


def governance_adapters() -> dict[str, GovernanceTwinAdapter]:
    return {k: a for k, a in sorted(TwinRegistry.all().items())
            if isinstance(a, GovernanceTwinAdapter)}


def observe(adapter: GovernanceTwinAdapter, *, record: bool = True) -> dict:
    """Collect, judge and (by default) record one observation. Returns the envelope."""
    evidence = adapter.collect()
    env = adapter.envelope(evidence)
    env["extra"]["recorded"] = False
    if record:
        try:
            record_observation(env, evidence, adapter.canvas_key)
            env["extra"]["recorded"] = True
        except Exception as exc:  # noqa: BLE001 — an unrecordable observation says so
            logger.warning("governance twin %s: record failed: %s", adapter.canvas_key, exc)
            env["extra"]["record_error"] = str(exc)
    return env


def replay(adapter: GovernanceTwinAdapter, obs_id: str) -> dict:
    """Re-judge a recorded observation from its evidence and compare digests.

    ``match`` is true only when the evidence still hashes to its recorded digest
    AND the recomputed verdict digest equals the recorded one. A different
    judge version is reported, not hidden.
    """
    row = load_observation(obs_id)
    if row is None:
        return {"observation_id": obs_id, "found": False, "match": False,
                "reason": "no such observation"}
    if row["canvas"] != adapter.canvas_key:
        return {"observation_id": obs_id, "found": True, "match": False,
                "reason": f"observation belongs to {row['canvas']!r}"}
    evidence = json.loads(row["evidence_json"])
    intact = digest(evidence) == row["evidence_digest"]
    env = adapter.envelope(evidence, observation=obs_id)
    same_judge = row["judge_version"] == JUDGE_VERSION
    match = intact and env["extra"]["verdict_digest"] == row["verdict_digest"]
    reason = ""
    if not intact:
        reason = "recorded evidence no longer hashes to its digest"
    elif not match:
        reason = ("judge version changed since recording" if not same_judge
                  else "recomputed verdict differs from the recorded one")
    return {
        "observation_id": obs_id,
        "found": True,
        "canvas": row["canvas"],
        "observed_at": row["observed_at"],
        "evidence_intact": intact,
        "judge_version": {"recorded": row["judge_version"], "current": JUDGE_VERSION},
        "recorded": {"verdict": row["verdict"], "verdict_digest": row["verdict_digest"]},
        "recomputed": {"verdict": env["verdict"],
                       "verdict_digest": env["extra"]["verdict_digest"]},
        "match": match,
        "reason": reason,
        "envelope": env,
    }


def replay_any(obs_id: str) -> dict:
    """Replay by id alone: find the canvas, then its adapter."""
    row = load_observation(obs_id)
    if row is None:
        return {"observation_id": obs_id, "found": False, "match": False,
                "reason": "no such observation"}
    return replay(adapter_for(row["canvas"]), obs_id)


def latest_envelopes() -> list[dict]:
    """For each governance twin, its latest recorded observation re-judged (or none)."""
    out = []
    for key, adapter in governance_adapters().items():
        rows = list_observations(key, limit=1)
        item = {"canvas": key, "display_name": adapter.display_name,
                "observes": list(adapter.observes), "observation": None, "envelope": None}
        if rows:
            item["observation"] = rows[0]
            result = replay(adapter, rows[0]["id"])
            item["envelope"] = result.get("envelope")
            item["replay"] = {k: result.get(k) for k in ("match", "reason", "evidence_intact")}
        out.append(item)
    return out


# ── threads ──────────────────────────────────────────────────────────────────

# kind -> (connection kind, table, id column, columns)
_THREAD_TABLES = {
    "cortex_audit": ("rls", "cortex_audit", "id",
                     ("id", "session_id", "function", "agent_id", "outcome", "blocked",
                      "provenance_id", "created_at")),
    "cortex_finding": ("rls", "cortex_entity_findings", "finding_id",
                       ("finding_id", "finding_type", "entity_label", "conflict_kind",
                        "provenance_id", "first_seen_at", "last_seen_at", "seen_count")),
    "ace_audit": ("ace", "ace_audit_log", "id",
                  ("id", "instance_id", "coworker_id", "action", "detail", "actor",
                   "created_at")),
    "ace_instance": ("ace", "ace_instances", "id",
                     ("id", "name", "role_id", "state", "created_at", "updated_at",
                      "completed_at")),
    "ace_trust": ("rls", "ace_trust_ledger", "id",
                  ("id", "role_id", "delta", "reason", "new_score", "source_task_id",
                   "recorded_at")),
}


def _thread_conn(kind: str):
    from tools.db.storage import get_canvas_connection, get_connection

    if kind == "ace":
        return get_canvas_connection("ICDEV_ACE_DB_URL")
    return get_connection()


def _provenance(provenance_id: str) -> Optional[dict]:
    """The ``source_citation_registry`` row a record names (tools/provenance)."""
    if not provenance_id:
        return None
    from tools.db.storage import get_connection

    cols = ("id", "citation_type", "source_table", "source_record_id", "source_hash",
            "classification", "created_at")
    conn = None
    try:
        conn = get_connection()
        rows = rows_as_dicts(conn.execute(
            f"SELECT {', '.join(cols)} FROM source_citation_registry WHERE id = %s",
            (provenance_id,)), cols)
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("provenance lookup %s: %s", provenance_id, exc)
        return None
    finally:
        close_quietly(conn)


def resolve_thread(thread_id: str) -> dict:
    """Follow a violation's thread to the record it rests on.

    ``resolved`` is false when the record is gone or unreadable; that is itself
    worth seeing, and never hidden behind an empty 200.
    """
    kind, _, ref = str(thread_id or "").partition(":")
    out: dict[str, Any] = {"thread_id": thread_id, "kind": kind, "resolved": False,
                           "record": None, "provenance": None}
    if not ref:
        out["reason"] = "malformed thread id (want <kind>:<ref>)"
        return out
    if kind in _THREAD_TABLES:
        conn_kind, table, id_col, cols = _THREAD_TABLES[kind]
        conn = None
        try:
            conn = _thread_conn(conn_kind)
            rows = rows_as_dicts(conn.execute(
                f"SELECT {', '.join(cols)} FROM {table} WHERE {id_col} = %s",  # nosec B608
                (int(ref) if kind == "ace_audit" and ref.isdigit() else ref,)), cols)
        except Exception as exc:  # noqa: BLE001
            out["reason"] = f"{table} unreadable: {exc}"
            return out
        finally:
            close_quietly(conn)
        if not rows:
            out["reason"] = f"no {table} row {ref}"
            return out
        out.update(resolved=True, record=rows[0], source=table)
        out["provenance"] = _provenance(str(rows[0].get("provenance_id") or ""))
        return out
    if kind == "twin_obs":
        obs_id, _, check_id = ref.partition("#")
        row = load_observation(obs_id)
        if row is None:
            out["reason"] = f"no observation {obs_id}"
            return out
        evidence = json.loads(row.pop("evidence_json"))
        try:
            checks = adapter_for(row["canvas"]).envelope(evidence)["extra"]["checks"]
        except LookupError:
            checks = []
        out.update(resolved=True, source=TABLE, record=row,
                   check=next((c for c in checks if c["id"] == check_id), None))
        return out
    if kind == "config":
        rel, _, want = ref.partition("@")
        path = BASE_DIR / rel
        try:
            have = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            out["reason"] = f"{rel} unreadable: {exc}"
            return out
        out.update(resolved=True, source="file", record={"path": rel, "sha256": have},
                   unchanged=have.startswith(want) if want else None)
        return out
    if kind == "conformance":
        try:
            from tools.conformance.feature_audit import load_snapshot

            report = load_snapshot()
        except Exception as exc:  # noqa: BLE001
            out["reason"] = f"conformance snapshot unreadable: {exc}"
            return out
        match = [f for f in report.get("features", []) if f.get("key") == ref]
        if ref == "census":
            out.update(resolved=True, source="args/feature_conformance_census.txt",
                       record={"summary": report.get("summary")})
        elif match:
            out.update(resolved=True, source=report["source"]["snapshot"], record=match[0])
        else:
            out["reason"] = f"no feature {ref} in the snapshot"
        return out
    out["reason"] = f"unknown thread kind {kind!r}"
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Governance twins: observe, list, replay, thread.")
    ap.add_argument("--observe", action="store_true", help="Observe and record (all or --canvas)")
    ap.add_argument("--no-record", action="store_true", help="With --observe: do not record")
    ap.add_argument("--list", action="store_true", help="List recorded observations")
    ap.add_argument("--replay", metavar="OBSERVATION_ID", help="Re-judge a recorded observation")
    ap.add_argument("--thread", metavar="THREAD_ID", help="Resolve a violation's thread")
    ap.add_argument("--canvas", help="Restrict to one governance twin (cortex, ace, cwk, ...)")
    ap.add_argument("--json", action="store_true", help="JSON output (the default)")
    args = ap.parse_args(argv)

    if args.observe:
        keys = [args.canvas] if args.canvas else list(governance_adapters())
        out: Any = [observe(adapter_for(k), record=not args.no_record) for k in keys]
    elif args.list:
        out = list_observations(args.canvas, limit=50)
    elif args.replay:
        out = replay_any(args.replay)
        out.pop("envelope", None)
    elif args.thread:
        out = resolve_thread(args.thread)
    else:
        out = [a.describe() for a in governance_adapters().values()]
    print(json.dumps(out, indent=2, default=str))
    if args.replay and not out.get("match"):
        return 1
    return 0


if __name__ == "__main__":
    # Run the imported module's main: the adapters subclass ITS base class, and
    # under ``-m`` this file is ``__main__``, a second copy they never saw.
    from tools.twin_core import governance as _governance

    raise SystemExit(_governance.main())
