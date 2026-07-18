# CUI // SP-CTI
"""Cross-canvas compliance health cards for the /canvas-compliance dashboard.

Each get_*_card() function queries the relevant DB(s) and returns a dict::

    {
        "key":     str,        # canvas abbreviation (NDC, SDC, …)
        "name":    str,        # human-readable name
        "path":    str,        # URL to canvas dashboard
        "color":   str,        # accent hex color
        "badge":   str,        # "green" | "yellow" | "red"
        "metrics": [{"label": str, "value": str | int | float}],
        "available": bool,     # False when backing DB is absent
    }
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlite3

from tools.db.storage import get_canvas_connection
from tools.canvas_compliance.constants import (
    NDC_POAM_YELLOW, NDC_POAM_RED,
    SDC_AGE_YELLOW, SDC_AGE_RED, SDC_PATHS_RED,
    PDC_SCORE_GREEN, PDC_SCORE_YELLOW,
    BDC_EXPIRY_GREEN, BDC_EXPIRY_YELLOW,
    DDC_PII_GREEN, DDC_PII_YELLOW,
    ODC_COV_GREEN, ODC_COV_YELLOW,
    IDC_DRIFT_GREEN, IDC_DRIFT_YELLOW,
)

logger = get_logger("icdev.canvas_compliance")

_BASE = Path(__file__).resolve().parent.parent.parent
_DATA = _BASE / "data"
_ICDEV_DB = _DATA / "icdev.db"


def _open(path: Path) -> Any | None:
    """Open a DB connection.

    For icdev.db: use get_canvas_connection() (cnr-cc-01). The global ICDEV
    tables read here — poam_items, cato_evidence, threat_models,
    cf_provision_log — carry classification/tenant_id RLS columns. This
    aggregate runs on a *context-less* route (no ``g.tenant_id``), so a normal
    get_connection() would attach the global RLS predicate and, under
    PostgreSQL, filter every row out (or error). PostgreSQL also has no SQLite
    ``.db`` file, so the old ``path.exists()`` gate left the icdev-backed cards
    permanently ``available=False``. get_canvas_connection() disables RLS and,
    on PG, reads the shared icdev database — the correct whole-platform posture
    read. For canvas-specific SQLite files, open directly via sqlite3 so they
    are not misrouted to the PostgreSQL backend.
    """
    if path.name == "icdev.db":
        try:
            return get_canvas_connection()
        except Exception as exc:
            logger.warning("canvas_compliance: cannot open %s: %s", path.name, exc)
            return None
    # Canvas-specific SQLite files — always open directly.
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:
        logger.warning("canvas_compliance: cannot open %s: %s", path.name, exc)
        return None


def _days_ago(ts: str) -> int | None:
    """Return number of days since an ISO-8601 timestamp string, or None."""
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(ts[:19], fmt).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).days
        except ValueError:
            continue
    return None


def _days_until(date_str: str) -> int | None:
    """Return days until a future date string (YYYY-MM-DD or ISO), or None."""
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (d - datetime.now(timezone.utc)).days
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# NDC — open POA&M count + cATO status
# ---------------------------------------------------------------------------

def get_ndc_card() -> dict:
    conn = _open(_ICDEV_DB)
    open_poams = 0
    cato_status = "no data"
    badge = "yellow"
    available = conn is not None

    if conn:
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM poam_items"
                " WHERE status IN ('open','in_progress')"
            ).fetchone()
            open_poams = (row["cnt"] if row else 0) or 0

            ev_rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM cato_evidence GROUP BY status"
            ).fetchall()
            ev = {r["status"]: r["cnt"] for r in ev_rows} if ev_rows else {}
            total_ev = sum(ev.values())

            if total_ev == 0:
                cato_status = "no data"
                badge = "yellow"
            elif ev.get("expired", 0) > 0:
                cato_status = "expired"
                badge = "red"
            elif ev.get("stale", 0) > 0:
                cato_status = "stale"
                badge = "yellow"
            else:
                cato_status = "current"
                badge = "green"

            if open_poams >= NDC_POAM_RED:
                badge = "red"
            elif open_poams >= NDC_POAM_YELLOW and badge != "red":
                badge = "yellow"
        except Exception as exc:
            logger.warning("NDC card error: %s", exc)

    return {
        "key": "NDC", "name": "Network Canvas", "path": "/network/",
        "color": "#e91e63",
        "badge": badge, "available": available,
        "metrics": [
            {"label": "Open POA&Ms", "value": open_poams},
            {"label": "cATO Status", "value": cato_status},
        ],
    }


# ---------------------------------------------------------------------------
# SDC — attack paths + threat model age
# ---------------------------------------------------------------------------

def get_sdc_card() -> dict:
    # PG-primary: read through the SDC canvas connection (honors SC_STORAGE_BACKEND
    # via get_canvas_connection under PostgreSQL) rather than opening the raw
    # data/security_canvas.db file directly. On a PG deployment that file is
    # absent, so the old sqlite3.connect() path left the card permanently
    # available=False.
    attack_paths = 0
    model_age = "n/a"
    badge = "yellow"
    available = False
    age_days = None

    conn = None
    try:
        from tools.security_canvas.db.init_db import (
            get_connection as _sdc_get_connection,
        )
        conn = _sdc_get_connection()
    except Exception as exc:
        logger.warning("SDC card: cannot open canvas connection: %s", exc)
        conn = None

    if conn is not None:
        try:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM sdc_attack_snapshots"
                ).fetchone()
                available = True  # table exists / query succeeded
                attack_paths = (row["cnt"] if row else 0) or 0
            except Exception as exc:
                logger.debug("SDC attack_snapshots unavailable: %s", exc)

            try:
                row2 = conn.execute(
                    "SELECT ran_at FROM sc_assessments ORDER BY ran_at DESC LIMIT 1"
                ).fetchone()
                available = True  # table exists / query succeeded
                age_days = _days_ago(row2["ran_at"] if row2 else None)
            except Exception as exc:
                logger.debug("SDC sc_assessments unavailable: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # Fallback: icdev.db threat_models age when the SDC canvas has none.
    if age_days is None:
        icdev_conn = _open(_ICDEV_DB)
        if icdev_conn:
            try:
                row3 = icdev_conn.execute(
                    "SELECT updated_at FROM threat_models ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
                age_days = _days_ago(row3["updated_at"] if row3 else None)
            except Exception as exc:
                logger.debug("SDC card (icdev_conn) unavailable: %s", exc)

    if age_days is not None:
        model_age = f"{age_days}d ago" if age_days >= 0 else "future"

    if age_days is None:
        badge = "yellow"
    elif age_days >= SDC_AGE_RED or attack_paths >= SDC_PATHS_RED:
        badge = "red"
    elif age_days >= SDC_AGE_YELLOW or attack_paths > 0:
        badge = "yellow"
    else:
        badge = "green"

    return {
        "key": "SDC", "name": "Security Canvas", "path": "/security/",
        "color": "#ff5722",
        "badge": badge, "available": available,
        "metrics": [
            {"label": "Attack Paths", "value": attack_paths},
            {"label": "Threat Model Age", "value": model_age},
        ],
    }


# ---------------------------------------------------------------------------
# PDC — pipeline compliance score
# ---------------------------------------------------------------------------

def get_pdc_card() -> dict:
    # PG-primary: read through the PDC (pipeline) canvas connection (honors
    # PC_STORAGE_BACKEND / PC_PG_DATABASE) rather than opening the raw
    # data/pipeline_canvas.db file directly — absent on PG deployments.
    score: float | None = None
    badge = "yellow"
    available = False

    conn = None
    try:
        from tools.pipeline.db.init_db import get_connection as _pdc_get_connection
        conn = _pdc_get_connection()
    except Exception as exc:
        logger.warning("PDC card: cannot open canvas connection: %s", exc)
        conn = None

    if conn is not None:
        try:
            try:
                row = conn.execute(
                    "SELECT passed, failed, ran_at FROM pc_compliance_checks"
                    " ORDER BY ran_at DESC LIMIT 1"
                ).fetchone()
                available = True  # table exists / query succeeded
                if row:
                    passed = row["passed"] or 0
                    failed = row["failed"] or 0
                    total = passed + failed
                    if total > 0:
                        score = round(passed / total * 100, 1)
            except Exception as exc:
                logger.debug("PDC compliance_checks unavailable: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    if score is None:
        badge = "yellow"
        display = "n/a"
    elif score >= PDC_SCORE_GREEN:
        badge = "green"
        display = f"{score}%"
    elif score >= PDC_SCORE_YELLOW:
        badge = "yellow"
        display = f"{score}%"
    else:
        badge = "red"
        display = f"{score}%"

    return {
        "key": "PDC", "name": "Pipeline Canvas", "path": "/devops/",
        "color": "#f44336",
        "badge": badge, "available": available,
        "metrics": [
            {"label": "Compliance Score", "value": display},
        ],
    }


# ---------------------------------------------------------------------------
# BDC — ISA count + days-to-expiry
# ---------------------------------------------------------------------------

def get_bdc_card() -> dict:
    # PG-primary: read through the BDC (boundary) canvas connection (honors
    # BDC_STORAGE_BACKEND / BDC_PG_DATABASE) rather than opening the raw
    # data/boundary_canvas.db file directly — absent on PG deployments.
    isa_count = 0
    min_days: int | None = None
    expired_count = 0
    badge = "yellow"
    available = False

    conn = None
    try:
        from tools.boundary_canvas.db.init_db import (
            get_connection as _bdc_get_connection,
        )
        conn = _bdc_get_connection()
    except Exception as exc:
        logger.warning("BDC card: cannot open canvas connection: %s", exc)
        conn = None

    if conn is not None:
        try:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM bd_isa_tracker"
                    " WHERE status IN ('active','expiring','signed')"
                ).fetchone()
                available = True  # table exists / query succeeded
                isa_count = (row["cnt"] if row else 0) or 0

                rows = conn.execute(
                    "SELECT expiry_date FROM bd_isa_tracker"
                    " WHERE status NOT IN ('draft','revoked','expired')"
                    "   AND expiry_date IS NOT NULL AND expiry_date != ''"
                ).fetchall()
                days_list = [_days_until(r["expiry_date"]) for r in rows]
                days_list = [d for d in days_list if d is not None]
                min_days = min(days_list) if days_list else None

                expired_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM bd_isa_tracker WHERE status = 'expired'"
                ).fetchone()
                expired_count = (expired_row["cnt"] if expired_row else 0) or 0
            except Exception as exc:
                logger.debug("BDC bd_isa_tracker unavailable: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    if available:
        if expired_count > 0 or (min_days is not None and min_days <= BDC_EXPIRY_YELLOW):
            badge = "red"
        elif min_days is not None and min_days <= BDC_EXPIRY_GREEN:
            badge = "yellow"
        elif isa_count > 0:
            badge = "green"
        else:
            badge = "yellow"  # no ISAs tracked yet

    expiry_display = f"{min_days}d" if min_days is not None else "n/a"

    return {
        "key": "BDC", "name": "Boundary Canvas", "path": "/boundary/",
        "color": "#9c27b0",
        "badge": badge, "available": available,
        "metrics": [
            {"label": "Active ISAs", "value": isa_count},
            {"label": "Min Days to Expiry", "value": expiry_display},
        ],
    }


# ---------------------------------------------------------------------------
# DDC — PII exposure count
# ---------------------------------------------------------------------------

def get_ddc_card() -> dict:
    # PG-primary: read through the DDC (data) canvas connection (honors
    # DDC_STORAGE_BACKEND via get_canvas_connection) rather than opening the raw
    # data/data_canvas.db file directly — absent on PG deployments.
    pii_count = 0
    badge = "yellow"
    available = False

    conn = None
    try:
        from tools.data_canvas.db.init_db import get_connection as _ddc_get_connection
        conn = _ddc_get_connection()
    except Exception as exc:
        logger.warning("DDC card: cannot open canvas connection: %s", exc)
        conn = None

    if conn is not None:
        try:
            try:
                rows = conn.execute(
                    "SELECT findings_json FROM dd_assessments"
                    " ORDER BY created_at DESC LIMIT 5"
                ).fetchall()
                available = True  # table exists / query succeeded
                seen: set[str] = set()
                for r in rows:
                    try:
                        findings = json.loads(r["findings_json"] or "[]")
                    except (json.JSONDecodeError, TypeError):
                        findings = []
                    for f in findings:
                        if not isinstance(f, dict):
                            continue
                        title = (f.get("title") or "").lower()
                        rule = (f.get("rule_id") or "").lower()
                        if "pii" in title or "pii" in rule:
                            key = f"{f.get('rule_id','')}|{f.get('title','')}"
                            if key not in seen:
                                seen.add(key)
                                pii_count += 1
            except Exception as exc:
                logger.debug("DDC dd_assessments unavailable: %s", exc)

            try:
                node_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM data_nodes"
                    " WHERE node_type LIKE '%pii%' OR label LIKE '%(PII)%'"
                ).fetchone()
                available = True  # table exists / query succeeded
                pii_count += (node_row["cnt"] if node_row else 0) or 0
            except Exception as exc:
                logger.debug("DDC data_nodes unavailable: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    if pii_count == DDC_PII_GREEN:
        badge = "green"
    elif pii_count <= DDC_PII_YELLOW:
        badge = "yellow"
    else:
        badge = "red"

    return {
        "key": "DDC", "name": "Data Canvas", "path": "/data/",
        "color": "#ff9800",
        "badge": badge, "available": available,
        "metrics": [
            {"label": "PII Exposures", "value": pii_count},
        ],
    }


# ---------------------------------------------------------------------------
# ODC — MITRE coverage % + Sigma needed
# ---------------------------------------------------------------------------

def get_odc_card() -> dict:
    # PG-primary: read through the ODC canvas connection (honors OC_STORAGE_BACKEND
    # via get_canvas_connection under PostgreSQL) rather than opening the raw
    # SQLite file directly. On a PG deployment the .db file is absent, so the old
    # sqlite3.connect() path left the card permanently available=False.
    coverage_pct: float | None = None
    sigma_needed = 0
    badge = "yellow"
    available = False

    conn = None
    try:
        from tools.observability_canvas.db.init_db import (
            get_connection as _odc_get_connection,
        )
        conn = _odc_get_connection()
    except Exception as exc:
        logger.warning("ODC card: cannot open canvas connection: %s", exc)
        conn = None

    if conn is not None:
        try:
            # Primary source: latest odc_gap_scores assessment.
            try:
                row = conn.execute(
                    "SELECT total_techniques, covered_count, gap_count"
                    " FROM odc_gap_scores ORDER BY assessed_at DESC LIMIT 1"
                ).fetchone()
                available = True  # table exists / query succeeded
                if row and (row["total_techniques"] or 0) > 0:
                    coverage_pct = round(
                        row["covered_count"] / row["total_techniques"] * 100, 1
                    )
                    sigma_needed = row["gap_count"] or 0
            except Exception as exc:
                # Missing table / empty DB — degrade gracefully, no raise.
                logger.debug("ODC gap_scores unavailable: %s", exc)

            # Fallback: od_ttp_coverage state counts.
            if coverage_pct is None:
                try:
                    total = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM od_ttp_coverage"
                    ).fetchone()
                    covered = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM od_ttp_coverage WHERE state='full'"
                    ).fetchone()
                    available = True  # table exists / query succeeded
                    t = (total["cnt"] if total else 0) or 0
                    c = (covered["cnt"] if covered else 0) or 0
                    if t > 0:
                        coverage_pct = round(c / t * 100, 1)
                        sigma_needed = t - c
                except Exception as exc:
                    logger.debug("ODC ttp_coverage fallback unavailable: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    if coverage_pct is None:
        badge = "yellow"
        cov_display = "n/a"
    elif coverage_pct >= ODC_COV_GREEN:
        badge = "green"
        cov_display = f"{coverage_pct}%"
    elif coverage_pct >= ODC_COV_YELLOW:
        badge = "yellow"
        cov_display = f"{coverage_pct}%"
    else:
        badge = "red"
        cov_display = f"{coverage_pct}%"

    return {
        "key": "ODC", "name": "Observability Canvas", "path": "/observability/",
        "color": "#4caf50",
        "badge": badge, "available": available,
        "metrics": [
            {"label": "MITRE Coverage", "value": cov_display},
            {"label": "Sigma Needed", "value": sigma_needed},
        ],
    }


# ---------------------------------------------------------------------------
# IDC — IaC drift count
# ---------------------------------------------------------------------------

def get_idc_card() -> dict:
    drift_count = 0
    badge = "yellow"
    available = False

    # Shared icdev.db source: latest cf_provision_log drift_check.
    icdev_conn = _open(_ICDEV_DB)
    if icdev_conn:
        try:
            row = icdev_conn.execute(
                "SELECT resources_affected FROM cf_provision_log"
                " WHERE action = 'drift_check'"
                " ORDER BY id DESC LIMIT 1"
            ).fetchone()
            available = True
            if row:
                drift_count = row["resources_affected"] or 0
        except Exception as exc:
            logger.debug("IDC drift (cf_provision_log) unavailable: %s", exc)

    # PG-primary: read idc_assessments through the IDC (infra) canvas connection
    # (honors IDC_STORAGE_BACKEND / IDC_PG_DATABASE) rather than opening the raw
    # data/infra_canvas.db file directly — absent on PG deployments.
    conn = None
    try:
        from tools.infra_canvas.db.init_db import get_connection as _idc_get_connection
        conn = _idc_get_connection()
    except Exception as exc:
        logger.warning("IDC card: cannot open canvas connection: %s", exc)
        conn = None

    if conn is not None:
        try:
            try:
                rows = conn.execute(
                    "SELECT findings_json FROM idc_assessments"
                    " ORDER BY created_at DESC LIMIT 3"
                ).fetchall()
                available = True  # table exists / query succeeded
                if drift_count == 0:
                    seen: set[str] = set()
                    for r in rows:
                        try:
                            findings = json.loads(r["findings_json"] or "[]")
                        except (json.JSONDecodeError, TypeError):
                            findings = []
                        for f in findings:
                            if not isinstance(f, dict):
                                continue
                            title = (f.get("title") or "").lower()
                            if "drift" in title:
                                key = f"{f.get('rule_id','')}|{title}"
                                if key not in seen:
                                    seen.add(key)
                                    drift_count += 1
            except Exception as exc:
                logger.debug("IDC drift (idc_assessments) unavailable: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    if drift_count == IDC_DRIFT_GREEN:
        badge = "green"
    elif drift_count <= IDC_DRIFT_YELLOW:
        badge = "yellow"
    else:
        badge = "red"

    return {
        "key": "IDC", "name": "Infra Canvas", "path": "/infra/",
        "color": "#00bcd4",
        "badge": badge, "available": available,
        "metrics": [
            {"label": "IaC Drift Count", "value": drift_count},
        ],
    }


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

# Bespoke compliance probes, keyed by the component-registry canvas key.
# These carry hand-written per-canvas logic (POA&M counts, ISA expiry, MITRE
# coverage, …) and are NOT registry-derived — but the FULL card list is
# (cnr-cc-02(b)): any other enabled canvas in the registry gets a generic
# "no compliance signal yet" card, so NOCC/PMC/CCC/DSOC/OHC/mission appear
# automatically as they gain compliance probes, instead of the old hardcoded
# 7-canvas list.
_BESPOKE_CARDS = {
    "network": get_ndc_card,
    "security": get_sdc_card,
    "pipeline": get_pdc_card,
    "boundary": get_bdc_card,
    "data": get_ddc_card,
    "observability": get_odc_card,
    "infra": get_idc_card,
}

# Back-compat alias — some callers/tests referenced the flat list.
_CARD_FNS = list(_BESPOKE_CARDS.values())


def _generic_card(comp) -> dict:
    """Placeholder card for a registered canvas without a bespoke probe."""
    prefix = (getattr(comp, "url_prefix", "") or f"/{comp.key}").rstrip("/")
    return {
        "key": comp.key.upper(),
        "name": getattr(comp, "display_name", None) or comp.key.replace("_", " ").title(),
        "path": (prefix + "/") if prefix else "/",
        "color": "#607d8b",
        "badge": "gray",
        "available": False,
        "metrics": [{"label": "Compliance Signal", "value": "not yet reported"}],
    }


def get_all_cards() -> list[dict]:
    """Return compliance card dicts for every enabled design canvas.

    The card list is driven by the component registry (cnr-cc-02): canvases with
    a bespoke probe in ``_BESPOKE_CARDS`` render their hand-written card; every
    other enabled canvas renders a generic placeholder card. Individual query
    failures are caught per-card so one unavailable canvas never blocks the rest.
    """
    cards: list[dict] = []
    covered: set[str] = set()

    # 1. Bespoke cards (preserve existing order + logic). Dedup is keyed off the
    #    card's own "key" (NDC/SDC/…), lowercased, because a bespoke card's
    #    registry canvas key is the short-code (ndc/sdc/…), not the dict key
    #    (network/security/…) used to look up its probe function.
    for _dict_key, fn in _BESPOKE_CARDS.items():
        try:
            card = fn()
            cards.append(card)
            covered.add(str(card.get("key", "")).lower())
        except Exception as exc:
            logger.error("canvas_compliance: %s failed: %s", fn.__name__, exc)

    # 2. Registry-driven: generic cards for other enabled canvases.
    try:
        from tools.config.component_registry import get_registry
        for comp in get_registry().iter_enabled(kind="canvas"):
            if comp.key.lower() in covered:
                continue
            covered.add(comp.key.lower())
            try:
                cards.append(_generic_card(comp))
            except Exception as exc:
                logger.warning("canvas_compliance: generic card for %s failed: %s", comp.key, exc)
    except Exception as exc:
        logger.warning("canvas_compliance: registry-driven cards unavailable: %s", exc)

    return cards
