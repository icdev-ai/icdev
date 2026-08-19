# CUI // SP-CTI
"""Persist and browse what ``cortex.resolve()`` DETECTED (cef-ui-02).

cef-rsv-02 made a cross-source disagreement computable and cef-rsv-03 gave it
citations. Both travel on the :class:`CortexResolution` the caller already
holds and NOWHERE ELSE, so until now the only reader of a finding was the code
that happened to trigger the resolution — a docmod sweep, a DocDrift draft
screen, an MCP verb. A conflict is a finding a HUMAN adjudicates and a gap is a
data-quality ticket, and neither is actionable if it dies with the request that
produced it.

This module is the durable projection of those two lists, and the read side the
Explorer browses. It is a PROJECTION, not an audit table: one row per
(tenant, entity, finding), upserted on re-observation. A conflict observed on
forty resolutions is ONE disagreement, and forty rows would render as forty
findings; ``seen_count`` keeps the recurrence without inflating the list.

IT STORES NO WINNER
-------------------
There is no ``resolved_value``, no ``winning_side``, no ``consensus`` and no
score in :data:`FINDING_COLUMNS`. cef-rsv-02 refuses to pick a side and the
store must not quietly supply one on the way to the screen — a persistence
layer that collapsed two claims into "the answer" would delete exactly the
finding this whole chain exists to surface. Every side is kept whole, with its
own provenance, and the reader adjudicates.

AN EMPTY TABLE HAS FOUR CAUSES AND ONLY ONE IS "NO CONFLICTS"
-------------------------------------------------------------
:func:`finding_stats` reports which, structurally, because the platform's
signature rendering bug is a zero whose cause is a tooltip:

``disabled``     ``resolve.persist_findings`` is off, so nothing was ever
                 recorded. Says nothing at all about the corpus.
``unmeasured``   recording is on and no resolution has been recorded yet — a
                 fresh worktree, an ephemeral CI database, a deployment where
                 ``resolve()`` has not been called.
``clean``        resolutions WERE recorded and every source that made a claim
                 made a compatible one. The only one of the four that is a
                 statement about the data.
``findings``     rows exist.

``conflicts``/``gaps`` are ``None`` — never ``0`` — for the first two, so a
template cannot render "0 conflicts" for a surface that never looked.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.cortex.finding_store")

FINDINGS_TABLE = "cortex_entity_findings"
RUNS_TABLE = "cortex_finding_runs"

#: The two finding kinds this store carries. A conflict is "my sources
#: disagree"; a gap is "nothing answered". Different fixes, never merged.
FINDING_CONFLICT = "conflict"
FINDING_GAP = "gap"
FINDING_TYPES = (FINDING_CONFLICT, FINDING_GAP)

#: Measurement states for an empty result set — see the module docstring.
STATE_DISABLED = "disabled"
STATE_UNMEASURED = "unmeasured"
STATE_CLEAN = "clean"
STATE_FINDINGS = "findings"

#: Every column read back. Named once so the read, the write and the tests
#: cannot drift; the absence of a winner column here is the invariant.
FINDING_COLUMNS = (
    "finding_id", "tenant_id", "classification", "finding_type",
    "entity_key", "entity_label", "entity_type", "conflict_kind",
    "reasons_json", "values_json", "sides_json", "backends_json",
    "backends_failed_json", "cross_backend", "citations_json",
    "uncited_sides_json", "citation_basis", "subject_entity",
    "subject_verdict", "provenance_id", "seen_count",
    "first_seen_at", "last_seen_at",
)

_JSON_FIELDS = {
    "reasons_json": "reasons",
    "values_json": "values",
    "sides_json": "sides",
    "backends_json": "backends",
    "backends_failed_json": "backends_failed",
    "citations_json": "citations",
    "uncited_sides_json": "uncited_sides",
}


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def finding_ident(tenant_id: str, finding_type: str, entity_key: str,
                  discriminator: str = "") -> str:
    """Deterministic id for one finding.

    Deterministic so re-observing the SAME disagreement updates one row rather
    than minting a fresh id nothing can correlate. The discriminator is the
    conflict's kind plus its claimed values, or the gap's reasons: a
    disagreement that changes what is claimed is a DIFFERENT finding, not the
    same one mutated, because what a human adjudicated is no longer what is on
    the table.
    """
    raw = "|".join([
        str(tenant_id or "default"), str(finding_type or ""),
        str(entity_key or ""), str(discriminator or ""),
    ])
    return "cef-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value) -> str:
    try:
        return json.dumps(value if value is not None else [], default=str)
    except Exception:  # noqa: BLE001 — a payload must never break a resolution
        return "[]"


def _loads(value):
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value or "[]")
    except Exception:  # noqa: BLE001
        return []


def persistence_enabled(config: Optional[dict] = None) -> bool:
    """``resolve.persist_findings`` — default ON.

    Default on because the surface this feeds renders nothing without it, and
    an empty Explorer that LOOKS like a clean bill of health is the defect the
    card exists to prevent. Turning it off is still honest: the read side
    reports :data:`STATE_DISABLED` rather than "no conflicts".
    """
    if config is None:
        try:
            from .config import load_cortex_config
            config = load_cortex_config()
        except Exception:  # noqa: BLE001
            config = {}
    resolve_cfg = (config or {}).get("resolve") or {}
    return bool(resolve_cfg.get("persist_findings", True))


# ---------------------------------------------------------------------------
# Row shaping — pure, so it is testable without a database
# ---------------------------------------------------------------------------
def conflict_row(conflict: dict, tenant_id: str, classification: str,
                 subject_entity: str, subject_verdict: str,
                 provenance_id: str = "") -> dict:
    """One ``EntityConflict`` dict -> one row. Every side kept whole."""
    values = list(conflict.get("values") or [])
    kind = str(conflict.get("kind") or "status")
    entity_key = str(conflict.get("entity_key") or "")
    return {
        "finding_id": finding_ident(
            tenant_id, FINDING_CONFLICT, entity_key,
            kind + ":" + ",".join(sorted(str(v) for v in values)),
        ),
        "tenant_id": tenant_id,
        "classification": classification,
        "finding_type": FINDING_CONFLICT,
        "entity_key": entity_key,
        "entity_label": str(conflict.get("entity_label") or ""),
        "entity_type": _first_side_type(conflict),
        "conflict_kind": kind,
        "reasons_json": "[]",
        "values_json": _dumps(values),
        # Whole, with each side's own provenance. Nothing is reduced here.
        "sides_json": _dumps(conflict.get("sides")),
        "backends_json": _dumps(conflict.get("backends")),
        "backends_failed_json": "[]",
        "cross_backend": 1 if conflict.get("cross_backend") else 0,
        "citations_json": _dumps(conflict.get("citations")),
        "uncited_sides_json": _dumps(conflict.get("uncited_sides")),
        "citation_basis": "",
        "subject_entity": subject_entity,
        "subject_verdict": subject_verdict,
        "provenance_id": provenance_id,
    }


def gap_row(gap: dict, tenant_id: str, classification: str,
            subject_entity: str, subject_verdict: str,
            provenance_id: str = "") -> dict:
    """One gap dict -> one row.

    Handles BOTH gap shapes without merging their vocabularies:
    ``resolver._gaps`` answers "why is the SUBJECT's verdict unknown" and
    carries no ``entity_key``, while ``entity_resolution``'s answers "did
    anything answer for this entity" and does. The key is derived from the
    label when absent, through the resolver's own ``entity_ident``, so the two
    are browsable in one list and a derived key JOINS the stored ones. The
    reasons are stored verbatim either way.
    """
    reasons = [str(r) for r in (gap.get("reasons") or [])]
    label = str(gap.get("entity") or gap.get("entity_label") or "")
    entity_key = str(gap.get("entity_key") or "") or _ident(label)
    return {
        "finding_id": finding_ident(
            tenant_id, FINDING_GAP, entity_key, ",".join(sorted(reasons)),
        ),
        "tenant_id": tenant_id,
        "classification": classification,
        "finding_type": FINDING_GAP,
        "entity_key": entity_key,
        "entity_label": label,
        "entity_type": str(gap.get("entity_type") or ""),
        "conflict_kind": "",
        "reasons_json": _dumps(reasons),
        "values_json": "[]",
        "sides_json": "[]",
        "backends_json": _dumps(gap.get("backends_consulted")),
        # Its own column, never folded into reasons: a partial outage is
        # CONTEXT for a gap, not the gap's cause.
        "backends_failed_json": _dumps(gap.get("backends_failed")),
        "cross_backend": 0,
        "citations_json": _dumps(gap.get("citations")),
        "uncited_sides_json": "[]",
        "citation_basis": str(gap.get("citation_basis") or ""),
        "subject_entity": subject_entity,
        "subject_verdict": subject_verdict,
        "provenance_id": provenance_id,
    }


def _first_side_type(conflict: dict) -> str:
    for side in conflict.get("sides") or []:
        value = str((side or {}).get("entity_type") or "")
        if value:
            return value
    return ""


def _ident(label: str) -> str:
    """The resolver's own entity key, so a derived key JOINS the stored ones."""
    try:
        from .entity_resolution import entity_ident
        return entity_ident(label)
    except Exception:  # noqa: BLE001
        return str(label or "").strip().lower()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def record_findings(result, ctx=None, config: Optional[dict] = None) -> dict:
    """Persist a resolution's conflicts and gaps. NEVER raises.

    Runs on every resolution, INCLUDING the ones with nothing to report — that
    write is the DENOMINATOR, and without it an empty findings table cannot be
    told apart from a surface nothing ever looked at.

    Returns ``{recorded, conflicts, gaps, status, detail}``.
    """
    record = {"recorded": 0, "conflicts": 0, "gaps": 0,
              "status": "skipped", "detail": ""}
    if not persistence_enabled(config):
        record["detail"] = "resolve.persist_findings is off"
        return record

    tenant_id = str(getattr(ctx, "tenant_id", "") or "default") or "default"
    classification = str(getattr(ctx, "classification", "") or "CUI") or "CUI"
    subject = str(getattr(result, "entity", "") or "")
    verdict = str(getattr(result, "verdict", "") or "")
    provenance_id = _first_provenance_id(result)

    rows = [
        conflict_row(c, tenant_id, classification, subject, verdict, provenance_id)
        for c in (getattr(result, "conflicts", None) or ()) if isinstance(c, dict)
    ] + [
        gap_row(g, tenant_id, classification, subject, verdict, provenance_id)
        for g in (getattr(result, "gaps", None) or ()) if isinstance(g, dict)
    ]

    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            for row in rows:
                _upsert(conn, row)
                if row["finding_type"] == FINDING_CONFLICT:
                    record["conflicts"] += 1
                else:
                    record["gaps"] += 1
            _bump_run(conn, tenant_id, classification,
                      record["conflicts"], record["gaps"])
            try:
                conn.commit()
            except Exception:  # noqa: BLE001 — autocommit backends
                pass
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — a projection never breaks a resolution
        record["status"] = "error"
        record["detail"] = str(exc)
        logger.warning("cortex finding store: write failed: %s", exc)
        return record

    record["recorded"] = len(rows)
    record["status"] = "ok"
    return record


def _first_provenance_id(result) -> str:
    """The registry id cef-rsv-03 stamped onto the citations, if it wrote one."""
    try:
        for citation in list(getattr(result, "citations", None) or ()):
            value = (citation.get("provenance_id") if isinstance(citation, dict)
                     else getattr(citation, "provenance_id", ""))
            if value:
                return str(value)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _upsert(conn, row: dict) -> None:
    now = _now()
    columns = [c for c in FINDING_COLUMNS
               if c not in ("first_seen_at", "last_seen_at", "seen_count")]
    placeholders = ", ".join(["%s"] * (len(columns) + 3))
    sql = (
        "INSERT INTO " + FINDINGS_TABLE + " (" + ", ".join(columns) + ", "
        "seen_count, first_seen_at, last_seen_at) "
        "VALUES (" + placeholders + ") "
        "ON CONFLICT (finding_id) DO UPDATE SET "
        "seen_count = " + FINDINGS_TABLE + ".seen_count + 1, "
        "last_seen_at = EXCLUDED.last_seen_at, "
        "subject_entity = EXCLUDED.subject_entity, "
        "subject_verdict = EXCLUDED.subject_verdict, "
        "citations_json = EXCLUDED.citations_json, "
        "sides_json = EXCLUDED.sides_json, "
        "backends_json = EXCLUDED.backends_json, "
        "backends_failed_json = EXCLUDED.backends_failed_json, "
        "provenance_id = EXCLUDED.provenance_id"
    )
    conn.execute(sql, tuple([row[c] for c in columns] + [1, now, now]))


def _bump_run(conn, tenant_id: str, classification: str,
              conflicts: int, gaps: int) -> None:
    """The denominator. One row per tenant, upserted on every resolution."""
    clean = 1 if not conflicts and not gaps else 0
    conn.execute(
        "INSERT INTO " + RUNS_TABLE + " (tenant_id, classification, resolutions, "
        "conflicts_seen, gaps_seen, clean_resolutions, last_run_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (tenant_id) DO UPDATE SET "
        "resolutions = " + RUNS_TABLE + ".resolutions + 1, "
        "conflicts_seen = " + RUNS_TABLE + ".conflicts_seen + EXCLUDED.conflicts_seen, "
        "gaps_seen = " + RUNS_TABLE + ".gaps_seen + EXCLUDED.gaps_seen, "
        "clean_resolutions = " + RUNS_TABLE + ".clean_resolutions "
        "+ EXCLUDED.clean_resolutions, "
        "last_run_at = EXCLUDED.last_run_at",
        (tenant_id, classification, 1, conflicts, gaps, clean, _now()),
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def list_findings(tenant_id: str = "default", finding_type: Optional[str] = None,
                  entity: str = "", reason: str = "", backend: str = "",
                  cross_backend_only: bool = False, limit: int = 200,
                  conn=None) -> list:
    """Findings for the browse surface, newest observation first.

    Filters a SQL WHERE can express are pushed down; ``reason`` and ``backend``
    live inside JSON payloads and are filtered in PYTHON, per the repository
    rule against SQLite-dialect JSON SQL at a runtime call site.
    """
    sql = (
        "SELECT " + ", ".join(FINDING_COLUMNS) + " FROM " + FINDINGS_TABLE + " "
        "WHERE tenant_id = %s"
    )
    params: list = [tenant_id]
    if finding_type in FINDING_TYPES:
        sql += " AND finding_type = %s"
        params.append(finding_type)
    if entity:
        sql += " AND (LOWER(entity_label) LIKE %s OR LOWER(entity_key) LIKE %s)"
        needle = "%" + str(entity).lower() + "%"
        params.extend([needle, needle])
    if cross_backend_only:
        sql += " AND cross_backend = 1"
    sql += " ORDER BY last_seen_at DESC LIMIT %s"
    params.append(max(1, min(int(limit or 200), 1000)))

    own = conn is None
    if own:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
        except Exception as exc:  # noqa: BLE001
            logger.warning("cortex finding store: no connection: %s", exc)
            return []
    try:
        cur = conn.execute(sql, tuple(params))
        rows = [dict(zip(FINDING_COLUMNS, r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 — an unmigrated table is not a crash
        logger.warning("cortex finding store: read failed: %s", exc)
        return []
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    out = [_inflate(r) for r in rows]
    if reason:
        out = [r for r in out if reason in (r.get("reasons") or [])]
    if backend:
        out = [r for r in out if _mentions_backend(r, backend)]
    return out


def _mentions_backend(row: dict, backend: str) -> bool:
    if backend in (row.get("backends") or []):
        return True
    for side in row.get("sides") or []:
        if backend == str(side.get("backend") or ""):
            return True
        if backend in (side.get("backends") or []):
            return True
    return False


def _inflate(row: dict) -> dict:
    out = dict(row)
    for column, key in _JSON_FIELDS.items():
        out[key] = _loads(out.pop(column, "[]"))
    out["cross_backend"] = bool(out.get("cross_backend"))
    for key in ("first_seen_at", "last_seen_at"):
        value = out.get(key)
        out[key] = value.isoformat() if hasattr(value, "isoformat") else str(value or "")
    return out


def finding_stats(tenant_id: str = "default", config: Optional[dict] = None,
                  conn=None) -> dict:
    """Counts AND the reason an empty set is empty. See the module docstring.

    ``conflicts`` and ``gaps`` are ``None`` — never ``0`` — when the surface
    was never measured, so a template physically cannot render "0 conflicts"
    for a deployment that has not looked.
    """
    stats = {
        "state": STATE_UNMEASURED, "conflicts": None, "gaps": None,
        "resolutions": 0, "clean_resolutions": 0, "cross_backend": None,
        "last_run_at": "", "detail": "",
    }
    if not persistence_enabled(config):
        stats["state"] = STATE_DISABLED
        stats["detail"] = (
            "resolve.persist_findings is off in args/cortex_config.yaml — "
            "nothing has been recorded, which is not a claim about the corpus."
        )
        return stats

    own = conn is None
    if own:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
        except Exception as exc:  # noqa: BLE001
            stats["detail"] = str(exc)
            return stats
    try:
        row = conn.execute(
            "SELECT resolutions, clean_resolutions, last_run_at FROM " + RUNS_TABLE
            + " WHERE tenant_id = %s", (tenant_id,),
        ).fetchone()
        if row:
            stats["resolutions"] = int(row[0] or 0)
            stats["clean_resolutions"] = int(row[1] or 0)
            last = row[2]
            stats["last_run_at"] = (
                last.isoformat() if hasattr(last, "isoformat") else str(last or "")
            )
        counts = conn.execute(
            "SELECT finding_type, COUNT(*), SUM(cross_backend) FROM "
            + FINDINGS_TABLE + " WHERE tenant_id = %s GROUP BY finding_type",
            (tenant_id,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        stats["detail"] = str(exc)
        return stats
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    by_type = {str(r[0]): (int(r[1] or 0), int(r[2] or 0)) for r in counts}
    if not stats["resolutions"] and not by_type:
        stats["detail"] = (
            "No resolution has been recorded on this deployment yet — "
            "UNMEASURED, not a clean bill of health."
        )
        return stats

    stats["conflicts"] = by_type.get(FINDING_CONFLICT, (0, 0))[0]
    stats["gaps"] = by_type.get(FINDING_GAP, (0, 0))[0]
    stats["cross_backend"] = by_type.get(FINDING_CONFLICT, (0, 0))[1]
    stats["state"] = (STATE_FINDINGS if (stats["conflicts"] or stats["gaps"])
                      else STATE_CLEAN)
    if stats["state"] == STATE_CLEAN:
        stats["detail"] = (
            str(stats["resolutions"]) + " resolution(s) recorded and every source "
            "that made a claim made a compatible one."
        )
    return stats


def filter_options(findings: list) -> dict:
    """The distinct reasons, backends and kinds actually present.

    Derived from the rows on screen rather than from the vocabulary constants,
    so a filter chip can never offer a value that matches nothing.
    """
    reasons: set = set()
    backends: set = set()
    kinds: set = set()
    for row in findings or []:
        reasons.update(str(r) for r in (row.get("reasons") or []))
        backends.update(str(b) for b in (row.get("backends") or []))
        for side in row.get("sides") or []:
            if side.get("backend"):
                backends.add(str(side["backend"]))
            backends.update(str(b) for b in (side.get("backends") or []))
        if row.get("conflict_kind"):
            kinds.add(str(row["conflict_kind"]))
    return {
        "reasons": sorted(reasons),
        "backends": sorted(b for b in backends if b),
        "kinds": sorted(kinds),
    }
