# CUI // SP-CTI
"""Cross-fabric posture roll-up (rmf-fab-02).

Rolls five measures up across the fabrics ``tools.fabric.registry`` declares
(rmf-fab-01), consuming BOTH cATO modules in the tree and labelling each with
the SCOPE it actually measures.

WHY THERE IS NO SCORE IN THIS OUTPUT
------------------------------------
Every existing surface answering "how is our authorization doing" reduces to a
single number. ``boundary_canvas/cato_readiness.py`` weights four components
into one 0-100 composite; ``security_canvas/continuous_authorization.py``
weights six signals into one ``posture_score``. Both are defensible *within
their own scope* and neither survives being carried across fabrics, because
the two are not measuring the same thing:

  * ``compliance/cato_monitor.py`` is SYSTEM scope -- one registered project is
    one authorization boundary, and its numbers are counts of controls and
    evidence items.
  * ``security_canvas/continuous_authorization.py`` is APPLICATION scope -- one
    row per deployed application, and its numbers are weighted live signals.

An average of "62% of this boundary's controls carry fresh evidence" and "this
application's six signals weight to 0.89" is a number with no denominator, and
a number with no denominator cannot be wrong, which is why it is worse than no
number at all. So this module emits FIVE measures, each carrying its OWN
numerator, denominator and a plain-words statement of what the denominator IS,
and TWO sources, each carrying its scope label. Nothing here blends them, and
``assert_no_blended_score`` is the executable statement of that rule.

THREE STATES PER MEASURE, NEVER MERGED
--------------------------------------
  measured             the numbers were read. ``value`` is a real answer, and a
                       measured 0 (no open CAT I findings) is a real answer too.
  not_assessed         the source is reachable and holds NOTHING for this
                       fabric. ``value`` is None -- never 0, never 100.
  source_unavailable   the table/module could not be reached at all. A migration
                       that never ran and a writer that never ran send you to
                       different fixes, so they are never folded together.

``compute_cato_readiness`` returns ``readiness_pct: 0.0`` when a project holds
no evidence at all -- a zero indistinguishable from a measured failure. Its
COUNTS are consumed here and that ratio is deliberately re-derived, so an
unassessed fabric can never inherit it.

READ-ONLY, BY CONSTRUCTION
--------------------------
``evaluate_authorization`` INSERTs a ``zig_continuous_ato`` row on every call,
and ``check_evidence_freshness`` UPDATEs evidence status. A roll-up that called
either would manufacture the evidence it then reports. This module reads the
rows those writers have already produced and calls neither -- pinned by an AST
test over this file, not by a comment.

NIST 800-53: CA-2, CA-5, CA-6, CA-7, PM-31
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# kax-conflict-05: run by path, sys.path[0] is this file's own directory.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

STATE_MEASURED = "measured"
STATE_NOT_ASSESSED = "not_assessed"
STATE_SOURCE_UNAVAILABLE = "source_unavailable"

MEASURE_STATES = (STATE_MEASURED, STATE_NOT_ASSESSED, STATE_SOURCE_UNAVAILABLE)

MEASURE_KEYS = (
    "control_coverage",
    "evidence_freshness",
    "open_cat1",
    "poam_age",
    "isa_expiry",
)

# Scope labels. These are the whole point of consuming both modules: the two
# answer different questions over different populations and must never be read
# as two readings of one quantity.
SCOPE_SYSTEM = "system"
SCOPE_APPLICATION = "application"

SCOPE_LABELS = {
    SCOPE_SYSTEM: (
        "System-level - one registered project is one authorization boundary; "
        "counts of controls and evidence items"
    ),
    SCOPE_APPLICATION: (
        "Per-application - one row per deployed application; each carries its "
        "own live authorization state"
    ),
}

# Any of these keys appearing in the output would be a number blending measures
# whose denominators differ. `assert_no_blended_score` sweeps for them.
FORBIDDEN_BLEND_KEYS = frozenset({
    "score",
    "readiness_score",
    "overall_score",
    "composite",
    "composite_score",
    "blended_score",
    "posture_score",
    "weighted_score",
    "band",
    "grade",
    "weights",
})

# STIG severity spellings that mean CAT I. `stig_checker.py` writes "CAT1";
# the CKL/CKLB emitter (rmf-oscal-01) and imported checklists use "high".
_CAT1_SEVERITIES = frozenset({"cat1", "cat i", "cat_i", "cati", "cat-i", "high", "1"})
_STIG_OPEN_STATUSES = frozenset({"open"})
_POAM_OPEN_STATUSES = frozenset({"open", "ongoing", "delayed"})

_REGISTRY_LOADERS = ("load_registry", "load_fabrics", "list_fabrics")


# ---------------------------------------------------------------------------
# Measure construction
# ---------------------------------------------------------------------------

def _measure(
    state: str,
    *,
    source: str,
    denominator_of: str,
    value: Optional[float] = None,
    numerator: Optional[float] = None,
    denominator: Optional[int] = None,
    reason: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Build one measure.

    ``value`` is carried ONLY when the state is ``measured``. Everywhere else it
    is None, so a fabric nobody assessed can never render as 0 or as 100.
    """
    if state != STATE_MEASURED:
        value = None
        numerator = None
    out: Dict[str, Any] = {
        "state": state,
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "denominator_of": denominator_of,
        "reason": reason,
        "source": source,
    }
    out.update(extra)
    return out


def _unavailable(source: str, denominator_of: str, reason: str) -> Dict[str, Any]:
    return _measure(
        STATE_SOURCE_UNAVAILABLE, source=source, denominator_of=denominator_of, reason=reason
    )


def _not_assessed(source: str, denominator_of: str, reason: str) -> Dict[str, Any]:
    return _measure(
        STATE_NOT_ASSESSED, source=source, denominator_of=denominator_of, reason=reason
    )


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 / 'YYYY-MM-DD HH:MM:SS' timestamp to aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _rows_as_dicts(rows) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        try:
            out.append(dict(r))
        except (TypeError, ValueError):
            out.append({k: r[k] for k in r.keys()})
    return out


# ---------------------------------------------------------------------------
# Fabric source (rmf-fab-01 registry seam)
# ---------------------------------------------------------------------------

def load_fabrics() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load declared fabrics through ``tools.fabric.registry`` (rmf-fab-01).

    That module is a sibling card and may not have landed. When it has not, this
    returns no fabrics and a registry state of ``absent`` - which is REPORTED,
    never rendered as "zero fabrics, all healthy". The loader probes the three
    plausible entry-point names rather than pinning one, so whichever rmf-fab-01
    settles on is consumed without an edit here.
    """
    meta: Dict[str, Any] = {
        "module": "tools.fabric.registry",
        "state": "absent",
        "entry_point": None,
        "overlay_env": "ICDEV_FABRIC_REGISTRY_PATH",
        "overlay_active": bool(os.environ.get("ICDEV_FABRIC_REGISTRY_PATH")),
        "reason": None,
    }
    try:
        from tools.fabric import registry as _registry  # type: ignore
    except ImportError as exc:
        meta["reason"] = f"registry_not_importable: {exc}"
        return [], meta

    for name in _REGISTRY_LOADERS:
        loader = getattr(_registry, name, None)
        if not callable(loader):
            continue
        try:
            raw = loader()
        except Exception as exc:  # noqa: BLE001 - a broken registry is reported, not raised
            meta["reason"] = f"{name}_failed: {exc}"
            meta["state"] = "unreadable"
            return [], meta
        fabrics = _coerce_fabrics(raw)
        meta["entry_point"] = name
        meta["state"] = "loaded" if fabrics else "declared_empty"
        # rmf-fab-01 excludes SYNTHETIC fixture fabrics from this seam by default
        # and says how many; carry that through so "no fabrics declared" is never
        # read as "the registry is empty" on a deployment that has not set its
        # private overlay.
        if isinstance(raw, dict):
            for key in ("reason", "synthetic_excluded", "fabric_count_declared", "source"):
                if raw.get(key) not in (None, "", 0):
                    meta[key] = raw[key]
        return fabrics, meta

    meta["reason"] = f"no entry point among {list(_REGISTRY_LOADERS)}"
    meta["state"] = "unreadable"
    return [], meta


def _coerce_fabrics(raw: Any) -> List[Dict[str, Any]]:
    """Accept the shapes a registry may plausibly return; keep only mappings."""
    if isinstance(raw, dict):
        inner = raw.get("fabrics", raw)
        if isinstance(inner, dict):
            items: List[Any] = [
                {**v, "key": v.get("key", k)}
                for k, v in inner.items()
                if isinstance(v, dict)
            ]
        else:
            items = list(inner or [])
    else:
        items = list(raw or [])
    return [f for f in items if isinstance(f, dict) and f.get("key")]


# ---------------------------------------------------------------------------
# Source 1 - SYSTEM scope (tools/compliance/cato_monitor.py)
# ---------------------------------------------------------------------------

_SYSTEM_MODULE = "tools/compliance/cato_monitor.py"


def system_cato(project_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Read the system-scope cATO source for one project.

    ``compute_cato_readiness`` is SELECT-only. Its ``readiness_pct`` /
    ``automated_pct`` are deliberately NOT carried: both fall back to 0.0 over
    an empty denominator, which is the defect this card exists to refuse. The
    COUNTS are carried and the ratios are re-derived by the measures.

    ``db_path`` exists because ``cato_monitor._get_connection`` requires a
    SQLite FILE to be present before it will open anything, whatever
    ``ICDEV_STORAGE_BACKEND`` says. From a worktree — or on a PostgreSQL
    deployment with no local ``data/icdev.db`` — that raises, and this source
    correctly reports ``source_unavailable`` carrying the reason VERBATIM
    rather than a zero. That is a pre-existing defect in ``cato_monitor``, not
    one this roll-up may paper over; the parameter lets a caller that knows
    where its evidence lives point at it.
    """
    out: Dict[str, Any] = {
        "module": _SYSTEM_MODULE,
        "scope": SCOPE_SYSTEM,
        "scope_label": SCOPE_LABELS[SCOPE_SYSTEM],
        "state": STATE_SOURCE_UNAVAILABLE,
        "project_id": project_id,
        "reason": None,
        "counts": {},
    }
    try:
        from tools.compliance.cato_monitor import compute_cato_readiness
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"module_unavailable: {exc}"
        return out

    try:
        raw = compute_cato_readiness(project_id, db_path=db_path)
    except ValueError as exc:
        # _verify_project: the project is not registered. Reachable source,
        # nothing recorded for this fabric - not_assessed, not unavailable.
        out["state"] = STATE_NOT_ASSESSED
        out["reason"] = f"project_not_registered: {exc}"
        return out
    except Exception as exc:  # noqa: BLE001 - missing table / unreachable DB
        out["reason"] = f"read_failed: {exc}"
        return out

    counts = {
        "total_controls": int(raw.get("total_controls") or 0),
        "controls_with_evidence": int(raw.get("controls_with_evidence") or 0),
        "controls_with_fresh_evidence": int(raw.get("controls_with_fresh_evidence") or 0),
        "total_evidence_items": int(raw.get("total_evidence_items") or 0),
        "by_frequency": raw.get("by_frequency") or {},
    }
    out["counts"] = counts
    if counts["total_evidence_items"] == 0 and counts["total_controls"] == 0:
        out["state"] = STATE_NOT_ASSESSED
        out["reason"] = "no_evidence_and_no_mapped_controls"
    else:
        out["state"] = STATE_MEASURED
    return out


# ---------------------------------------------------------------------------
# Source 2 - APPLICATION scope (security_canvas/continuous_authorization.py)
# ---------------------------------------------------------------------------

_APPLICATION_MODULE = "tools/security_canvas/continuous_authorization.py"


def application_cato(applications: Optional[List[str]] = None) -> Dict[str, Any]:
    """Read the per-application cATO source: latest stored row per application.

    ``evaluate_authorization`` is NOT called - it INSERTs, and a roll-up that
    evaluated on read would report evidence it had just manufactured. The
    ``posture_score`` those rows carry is a weighted blend of six signals and is
    deliberately not surfaced (see the module docstring); the recorded
    ``ato_state`` is.
    """
    wanted = [a for a in (applications or []) if a]
    out: Dict[str, Any] = {
        "module": _APPLICATION_MODULE,
        "scope": SCOPE_APPLICATION,
        "scope_label": SCOPE_LABELS[SCOPE_APPLICATION],
        "state": STATE_SOURCE_UNAVAILABLE,
        "reason": None,
        "declared_applications": wanted,
        "applications": [],
        "by_ato_state": {},
        "evaluated_count": 0,
        "never_evaluated": list(wanted),
    }
    try:
        from tools.security_canvas.db.init_db import get_connection as _sc_conn
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"module_unavailable: {exc}"
        return out

    conn = None
    try:
        conn = _sc_conn()
        rows = conn.execute(
            "SELECT application, ato_state, degraded_signals, evaluated_at "
            "FROM zig_continuous_ato ORDER BY evaluated_at"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - table is created lazily by the writer
        out["reason"] = f"zig_continuous_ato_unavailable: {exc}"
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    # Latest row per application, filtered in Python (dialect-agnostic).
    latest: Dict[str, Dict[str, Any]] = {}
    for row in _rows_as_dicts(rows):
        app = row.get("application")
        if not app:
            continue
        if wanted and app not in wanted:
            continue
        prev = latest.get(app)
        if prev is None or str(row.get("evaluated_at") or "") >= str(prev.get("evaluated_at") or ""):
            latest[app] = row

    if not latest:
        out["state"] = STATE_NOT_ASSESSED
        out["reason"] = (
            "declared_applications_never_evaluated" if wanted else "no_application_evaluated"
        )
        return out

    apps: List[Dict[str, Any]] = []
    by_state: Dict[str, int] = {}
    for app, row in sorted(latest.items()):
        state = row.get("ato_state") or "unknown"
        by_state[state] = by_state.get(state, 0) + 1
        degraded = row.get("degraded_signals")
        if isinstance(degraded, str):
            try:
                degraded = json.loads(degraded)
            except (ValueError, TypeError):
                degraded = None
        apps.append({
            "application": app,
            "ato_state": state,
            "degraded_signals": degraded if isinstance(degraded, list) else [],
            "evaluated_at": row.get("evaluated_at"),
        })

    out["state"] = STATE_MEASURED
    out["applications"] = apps
    out["by_ato_state"] = by_state
    out["evaluated_count"] = len(apps)
    out["never_evaluated"] = sorted(set(wanted) - set(latest)) if wanted else []
    return out


# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------

_BDC_SOURCE = "tools/boundary_canvas/cato_readiness.py"


def _bdc_components(design_id: str, project_id: str, *, conn, canvas_conn) -> Dict[str, Any]:
    """Per-component detail from the BDC scorer, with its composite DISCARDED.

    ``compute_readiness`` returns ``score`` / ``readiness_score`` / ``band`` /
    ``weights`` alongside ``components``. Only ``components`` is taken; the
    weighted composite is exactly the blended number this roll-up refuses to
    carry across fabrics.
    """
    try:
        from tools.boundary_canvas.cato_readiness import compute_readiness
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"module_unavailable: {exc}"}
    try:
        result = compute_readiness(design_id, project_id, conn=conn, canvas_conn=canvas_conn)
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"compute_failed: {exc}"}
    components = result.get("components")
    return components if isinstance(components, dict) else {"_error": "no_components"}


def measure_control_coverage(components: Dict[str, Any]) -> Dict[str, Any]:
    """Mean control satisfaction of the latest compliance snapshot.

    Denominator: the SCORED controls in that snapshot. Rows marked
    not_applicable/not_assessed carry a NULL score and are excluded upstream,
    so this is never diluted by controls nobody looked at.
    """
    denom_of = "controls carrying a score in the latest compliance snapshot"
    if "_error" in components:
        return _unavailable(_BDC_SOURCE, denom_of, components["_error"])
    detail = components.get("control_coverage") or {}
    scored = detail.get("scored_controls")
    total = detail.get("total_controls")
    if detail.get("score") is None or not scored:
        return _not_assessed(_BDC_SOURCE, denom_of, detail.get("reason") or "no_snapshot")
    return _measure(
        STATE_MEASURED,
        source=_BDC_SOURCE,
        denominator_of=denom_of,
        value=round(float(detail["score"]), 1),
        numerator=detail.get("satisfied_controls"),
        denominator=int(scored),
        unit="percent_mean_satisfaction",
        snapshot_id=detail.get("snapshot_id"),
        controls_in_snapshot=total,
    )


def measure_evidence_freshness(system_source: Dict[str, Any]) -> Dict[str, Any]:
    """Share of evidence-bearing controls whose evidence is current AND fresh.

    Denominator: controls that HAVE evidence - never the full control catalogue,
    which would report a fabric with three fresh controls out of a 300-control
    baseline as 1% fresh when what is true is that 297 were never collected.
    That distinction is what ``control_coverage`` above is for.
    """
    denom_of = "controls that have at least one cATO evidence item"
    state = system_source.get("state")
    if state == STATE_SOURCE_UNAVAILABLE:
        return _unavailable(_SYSTEM_MODULE, denom_of, system_source.get("reason") or "unavailable")
    counts = system_source.get("counts") or {}
    with_evidence = int(counts.get("controls_with_evidence") or 0)
    if state != STATE_MEASURED or with_evidence == 0:
        return _not_assessed(
            _SYSTEM_MODULE, denom_of, system_source.get("reason") or "no_evidence_collected"
        )
    fresh = int(counts.get("controls_with_fresh_evidence") or 0)
    return _measure(
        STATE_MEASURED,
        source=_SYSTEM_MODULE,
        denominator_of=denom_of,
        value=round(fresh / with_evidence * 100.0, 1),
        numerator=fresh,
        denominator=with_evidence,
        unit="percent_fresh",
        total_evidence_items=counts.get("total_evidence_items"),
    )


def measure_open_cat1(conn, project_id: str) -> Dict[str, Any]:
    """Open CAT I STIG findings.

    Denominator: CAT I findings RECORDED for this project. A measured 0 (every
    CAT I finding closed) is a real answer and stays a real answer; a project
    with no CAT I findings recorded at all is ``not_assessed``, because "no scan
    has run" and "the scan found nothing open" are opposite facts and the STIG
    gate treats one of them as a pass.
    """
    denom_of = "CAT I STIG findings recorded for this project"
    source = "stig_findings"
    if conn is None:
        return _unavailable(source, denom_of, "main_db_unavailable")
    try:
        rows = conn.execute(
            "SELECT severity, status FROM stig_findings WHERE project_id = %s",
            (project_id,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - missing table
        return _unavailable(source, denom_of, f"stig_findings_unavailable: {exc}")

    cat1 = [
        r for r in _rows_as_dicts(rows)
        if str(r.get("severity") or "").strip().lower() in _CAT1_SEVERITIES
    ]
    if not cat1:
        return _not_assessed(source, denom_of, "no_cat1_findings_recorded")
    open_count = sum(
        1 for r in cat1 if str(r.get("status") or "").strip().lower() in _STIG_OPEN_STATUSES
    )
    return _measure(
        STATE_MEASURED,
        source=source,
        denominator_of=denom_of,
        value=open_count,
        numerator=open_count,
        denominator=len(cat1),
        unit="count_open",
        gate_note="the STIG gate passes at 0 open CAT I findings",
    )


def measure_poam_age(conn, project_id: str) -> Dict[str, Any]:
    """Age of the OPEN POA&M items.

    Denominator: open POA&M items. ``value`` is the OLDEST open item's age in
    days - the one an assessor asks about - with the median carried beside it so
    a single ancient item cannot be read as a systemic backlog.
    """
    denom_of = "open POA&M items for this project"
    source = "poam_items"
    if conn is None:
        return _unavailable(source, denom_of, "main_db_unavailable")
    try:
        rows = conn.execute(
            "SELECT status, created_at, milestone_date FROM poam_items WHERE project_id = %s",
            (project_id,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(source, denom_of, f"poam_items_unavailable: {exc}")

    now = datetime.now(timezone.utc)
    ages: List[float] = []
    open_items = 0
    undated = 0
    overdue = 0
    for row in _rows_as_dicts(rows):
        if str(row.get("status") or "").strip().lower() not in _POAM_OPEN_STATUSES:
            continue
        open_items += 1
        created = _parse_ts(row.get("created_at"))
        if created is None:
            undated += 1
        else:
            ages.append(max(0.0, (now - created).total_seconds() / 86400.0))
        milestone = _parse_ts(row.get("milestone_date"))
        if milestone is not None and milestone < now:
            overdue += 1

    if open_items == 0:
        return _not_assessed(source, denom_of, "no_open_poam_items")
    if not ages:
        return _measure(
            STATE_NOT_ASSESSED,
            source=source,
            denominator_of=denom_of,
            reason="open_items_carry_no_created_at",
            denominator=open_items,
            undated_items=undated,
        )
    ordered = sorted(ages)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    return _measure(
        STATE_MEASURED,
        source=source,
        denominator_of=denom_of,
        value=round(max(ages), 1),
        numerator=len(ages),
        denominator=open_items,
        unit="days_oldest_open",
        median_age_days=round(median, 1),
        overdue_milestones=overdue,
        undated_items=undated,
    )


def measure_isa_expiry(components: Dict[str, Any]) -> Dict[str, Any]:
    """Interconnection Security Agreements at or near expiry.

    Denominator: ISAs on this fabric's design that are neither terminated nor
    already marked expired - the ones an ISA still governs.
    """
    denom_of = "live ISAs on this fabric's boundary design"
    if "_error" in components:
        return _unavailable(_BDC_SOURCE, denom_of, components["_error"])
    detail = components.get("isa_expiry") or {}
    total = detail.get("total_isas")
    if detail.get("score") is None or not total:
        return _not_assessed(_BDC_SOURCE, denom_of, detail.get("reason") or "no_isas")
    expired = int(detail.get("expired") or 0)
    soon = int(detail.get("expiring_soon") or 0)
    return _measure(
        STATE_MEASURED,
        source=_BDC_SOURCE,
        denominator_of=denom_of,
        value=expired + soon,
        numerator=expired + soon,
        denominator=int(total),
        unit="count_expired_or_expiring",
        expired=expired,
        expiring_soon=soon,
        warn_days=detail.get("warn_days"),
    )


# ---------------------------------------------------------------------------
# Per-fabric roll-up
# ---------------------------------------------------------------------------

def fabric_posture(
    fabric: Dict[str, Any],
    *,
    conn=None,
    canvas_conn=None,
    system_db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Posture for ONE fabric: five measures and two labelled cATO sources.

    A fabric declares its bindings (``project_id``, ``design_id``,
    ``applications``); each defaults to the fabric key, mirroring
    ``compute_readiness``'s own ``project_id or design_id`` default. A binding
    that resolves to nothing produces ``not_assessed`` measures, never zeros.
    """
    key = str(fabric.get("key") or "")
    design_id = str(fabric.get("design_id") or key)
    project_id = str(fabric.get("project_id") or key)
    applications = list(fabric.get("applications") or [])

    components = _bdc_components(design_id, project_id, conn=conn, canvas_conn=canvas_conn)
    sys_source = system_cato(project_id, db_path=system_db_path)
    app_source = application_cato(applications)

    measures = {
        "control_coverage": measure_control_coverage(components),
        "evidence_freshness": measure_evidence_freshness(sys_source),
        "open_cat1": measure_open_cat1(conn, project_id),
        "poam_age": measure_poam_age(conn, project_id),
        "isa_expiry": measure_isa_expiry(components),
    }
    tallies: Dict[str, int] = {s: 0 for s in MEASURE_STATES}
    for m in measures.values():
        tallies[m["state"]] = tallies.get(m["state"], 0) + 1

    # A fabric is `not_assessed` when NOTHING was measured about it. That is
    # deliberately distinct from `partially_assessed` - a fabric with one
    # measured number out of five must not read as fully assessed.
    if tallies[STATE_MEASURED] == len(MEASURE_KEYS):
        fabric_state = "assessed"
    elif tallies[STATE_MEASURED] > 0:
        fabric_state = "partially_assessed"
    elif tallies[STATE_SOURCE_UNAVAILABLE] == len(MEASURE_KEYS):
        fabric_state = STATE_SOURCE_UNAVAILABLE
    else:
        fabric_state = STATE_NOT_ASSESSED

    return {
        "key": key,
        "display_name": fabric.get("display_name") or key,
        # A LABEL from args/classification_profiles.yaml, never a banner.
        "classification": fabric.get("classification"),
        "impact_level": fabric.get("impact_level"),
        "bindings": {
            "design_id": design_id,
            "project_id": project_id,
            "applications": applications,
        },
        "fabric_state": fabric_state,
        "measures": measures,
        "measures_by_state": tallies,
        "cato_sources": {
            "system": sys_source,
            "application": app_source,
        },
    }


def roll_up(
    fabrics: Optional[List[Dict[str, Any]]] = None,
    *,
    system_db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Cross-fabric posture roll-up.

    ``fabrics`` defaults to the rmf-fab-01 registry. Nothing here reduces the
    fabrics to a number: the roll-up level carries COUNTS of fabrics by state
    and counts of measures by state, and the caller reads the per-fabric,
    per-measure numbers beside their own denominators.
    """
    registry_meta: Dict[str, Any] = {"state": "supplied_by_caller"}
    if fabrics is None:
        fabrics, registry_meta = load_fabrics()

    conn = None
    canvas_conn = None
    try:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
        except Exception as exc:  # noqa: BLE001
            logger.warning("main DB connection failed: %s", exc)
        try:
            from tools.boundary_canvas.db.init_db import get_connection as _bdc_conn
            canvas_conn = _bdc_conn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("canvas DB connection failed: %s", exc)

        rows = [
            fabric_posture(
                f, conn=conn, canvas_conn=canvas_conn, system_db_path=system_db_path
            )
            for f in (fabrics or [])
        ]
    finally:
        for c in (conn, canvas_conn):
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass

    by_fabric_state: Dict[str, int] = {}
    by_measure_state: Dict[str, int] = {s: 0 for s in MEASURE_STATES}
    for row in rows:
        by_fabric_state[row["fabric_state"]] = by_fabric_state.get(row["fabric_state"], 0) + 1
        for state, n in row["measures_by_state"].items():
            by_measure_state[state] = by_measure_state.get(state, 0) + n

    result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry": registry_meta,
        "fabric_count": len(rows),
        "fabrics": rows,
        "fabrics_by_state": by_fabric_state,
        "measures_by_state": by_measure_state,
        "measure_keys": list(MEASURE_KEYS),
        "cato_scopes": {
            SCOPE_SYSTEM: {
                "module": _SYSTEM_MODULE,
                "label": SCOPE_LABELS[SCOPE_SYSTEM],
            },
            SCOPE_APPLICATION: {
                "module": _APPLICATION_MODULE,
                "label": SCOPE_LABELS[SCOPE_APPLICATION],
            },
        },
        "scoring": {
            "blended": False,
            "note": (
                "No composite exists in this output. The two cATO sources measure "
                "different populations (system boundaries vs deployed applications) "
                "and the five measures carry different denominators; a single number "
                "over them would have no denominator at all."
            ),
        },
    }
    if not rows:
        result["unmeasurable"] = True
        result["unmeasurable_reason"] = registry_meta.get("reason") or "no fabrics declared"
    return result


# ---------------------------------------------------------------------------
# The rule, executable
# ---------------------------------------------------------------------------

def assert_no_blended_score(payload: Any, _path: str = "$") -> None:
    """Raise if any blended-score key appears anywhere in ``payload``.

    A rule stated as a comment is a rule nobody re-derives. This walks the whole
    structure so a key reintroduced by a future passthrough of
    ``compute_readiness`` or of a ``zig_continuous_ato`` row fails loudly.
    """
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in FORBIDDEN_BLEND_KEYS:
                raise AssertionError(
                    f"blended score key {k!r} present at {_path} - the cross-fabric "
                    "roll-up must carry no composite (rmf-fab-02)"
                )
            assert_no_blended_score(v, f"{_path}.{k}")
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            assert_no_blended_score(v, f"{_path}[{i}]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(result: Dict[str, Any]) -> str:
    lines = ["Cross-fabric posture roll-up", "=" * 60]
    reg = result.get("registry", {})
    reason = f" ({reg['reason']})" if reg.get("reason") else ""
    lines.append(f"registry: {reg.get('state')}{reason}")
    lines.append(f"fabrics: {result['fabric_count']}")
    if result.get("unmeasurable"):
        lines.append(f"UNMEASURABLE - {result['unmeasurable_reason']}")
        lines.append("(this is not a clean bill of health: nothing was assessed)")
        return "\n".join(lines)
    for f in result["fabrics"]:
        lines.append("")
        lines.append(f"[{f['key']}] {f['display_name']} - {f['fabric_state']}")
        lines.append(
            f"  classification={f.get('classification')} impact_level={f.get('impact_level')}"
        )
        for key in MEASURE_KEYS:
            m = f["measures"][key]
            if m["state"] == STATE_MEASURED:
                val = f"{m['value']} {m.get('unit', '')}".strip()
                lines.append(
                    f"  {key:20s} {val}  (of {m['denominator']} {m['denominator_of']})"
                )
            else:
                lines.append(f"  {key:20s} {m['state'].upper()} - {m.get('reason')}")
        for scope in (SCOPE_SYSTEM, SCOPE_APPLICATION):
            s = f["cato_sources"][scope]
            lines.append(f"  cATO/{scope:11s} {s['state']} - {s['scope_label']}")
    lines.append("")
    lines.append("No blended score is emitted. " + result["scoring"]["note"])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-fabric posture roll-up (rmf-fab-02)"
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--fabric", help="Limit the report to one fabric key")
    args = parser.parse_args(argv)

    result = roll_up()
    if args.fabric:
        result["fabrics"] = [f for f in result["fabrics"] if f["key"] == args.fabric]
        result["fabric_count"] = len(result["fabrics"])
    assert_no_blended_score(result)
    print(json.dumps(result, indent=2, default=str) if args.json else _format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
