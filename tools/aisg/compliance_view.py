# CUI // SP-CTI
"""Compliance view data — real ATO / NIST 800-53 / POA&M posture from the DB.

Sources ONLY real compliance tables (no canned control coverage or POA&M rows):
  * control families -> ``compliance_controls`` (catalog) x ``control_narratives``
                        (a control counts as "implemented" once it has a narrative)
  * POA&M items      -> ``poam_items``
  * ATO header       -> ``ato_system_registry``

Any widget without a real backing source renders an explicit empty state
("No data — connect compliance engine") rather than fabricated posture.
"""
from __future__ import annotations

from collections import OrderedDict

from tools.db.storage import get_connection

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.aisg.compliance_view")

# Display labels for NIST 800-53 control-family codes. These are reference
# labels only (not posture data) — the counts/coverage below come from the DB.
_FAMILY_NAMES = {
    "AC": "Access Control", "AT": "Awareness & Training", "AU": "Audit & Accountability",
    "CA": "Assessment, Authorization & Monitoring", "CM": "Configuration Management",
    "CP": "Contingency Planning", "IA": "Identification & Authentication",
    "IR": "Incident Response", "MA": "Maintenance", "MP": "Media Protection",
    "PE": "Physical & Environmental Protection", "PL": "Planning",
    "PM": "Program Management", "PS": "Personnel Security", "PT": "PII Processing & Transparency",
    "RA": "Risk Assessment", "SA": "System & Services Acquisition",
    "SC": "System & Communications Protection", "SI": "System & Information Integrity",
    "SR": "Supply Chain Risk Management",
}


def _norm(control_id) -> str:
    return str(control_id or "").strip().upper()


def _scalar(conn, sql: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception as exc:
        logger.warning("compliance_view: count query failed (%s): %s", sql, exc)
        return 0
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError, IndexError):
        return 0


def _load_control_families(conn) -> list[dict]:
    """Group compliance_controls by family; implemented = has a control narrative."""
    try:
        ctrl_rows = conn.execute(
            "SELECT id, family FROM compliance_controls"
        ).fetchall()
    except Exception as exc:
        logger.warning("compliance_view: compliance_controls query failed: %s", exc)
        return []
    if not ctrl_rows:
        return []

    documented: set[str] = set()
    try:
        narr_rows = conn.execute(
            "SELECT DISTINCT control_id FROM control_narratives"
        ).fetchall()
        documented = {_norm(r["control_id"]) for r in narr_rows}
    except Exception as exc:
        logger.warning("compliance_view: control_narratives query failed: %s", exc)

    families: "OrderedDict[str, dict]" = OrderedDict()
    for r in ctrl_rows:
        family = _norm(r["family"])
        d = families.setdefault(family, {"controls": 0, "implemented": 0})
        d["controls"] += 1
        if _norm(r["id"]) in documented:
            d["implemented"] += 1

    sections: list[dict] = []
    for code, d in sorted(families.items()):
        controls, impl = d["controls"], d["implemented"]
        if impl <= 0:
            status = "gray"
        elif impl >= controls:
            status = "green"
        else:
            status = "yellow"
        name = _FAMILY_NAMES.get(code, code or "Uncategorized")
        sections.append({
            "id": code.lower(),
            "name": f"{name} ({code})" if code else name,
            "controls": controls,
            "implemented": impl,
            "status": status,
        })
    return sections


def _load_poam(conn, limit: int = 50) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT weakness_id, control_id, weakness_description, status, "
            "milestone_date, severity FROM poam_items "
            "ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 "
            "ELSE 2 END, milestone_date LIMIT %s",
            (limit,),
        ).fetchall()
    except Exception as exc:
        logger.warning("compliance_view: poam_items query failed: %s", exc)
        return []

    items: list[dict] = []
    for r in rows:
        d = dict(r)
        due = d.get("milestone_date")
        items.append({
            "id": d.get("weakness_id") or "—",
            "control": d.get("control_id") or "—",
            "weakness": d.get("weakness_description") or "",
            "due": str(due)[:10] if due else "—",
            "status": d.get("status") or "open",
            "severity": d.get("severity"),
        })
    return items


def _load_ato(conn) -> dict:
    try:
        row = conn.execute(
            "SELECT system_name, ato_type, impact_level, ato_expiry "
            "FROM ato_system_registry ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    except Exception as exc:
        logger.warning("compliance_view: ato_system_registry query failed: %s", exc)
        row = None

    if not row:
        return {
            "ato_registered": False,
            "ato_status": None,
            "impact_level": None,
            "ato_expiry": None,
            "system_name": None,
        }
    d = dict(row)
    ato_type = (d.get("ato_type") or "").upper()
    return {
        "ato_registered": True,
        "ato_status": ato_type or "Registered",
        "impact_level": d.get("impact_level"),
        "ato_expiry": d.get("ato_expiry"),
        "system_name": d.get("system_name"),
    }


def get_compliance_data() -> dict:
    """Return ATO status, control-family coverage, and POA&M items (live data only)."""
    result = {
        "ato_sections": [],
        "poam_items": [],
        "compliance_pct": None,
        "total_controls": 0,
        "total_implemented": 0,
        "poam_open": 0,
        "poam_in_progress": 0,
        "ato_registered": False,
        "impact_level": None,
        "ato_status": None,
        "ato_expiry": None,
        "system_name": None,
        "data_source_error": None,
    }

    try:
        conn = get_connection()
    except Exception as exc:
        logger.error("compliance_view: database unavailable: %s", exc)
        result["data_source_error"] = str(exc)
        return result

    try:
        sections = _load_control_families(conn)
        result["ato_sections"] = sections
        total_controls = sum(s["controls"] for s in sections)
        total_implemented = sum(s["implemented"] for s in sections)
        result["total_controls"] = total_controls
        result["total_implemented"] = total_implemented
        result["compliance_pct"] = (
            round(total_implemented / total_controls * 100) if total_controls else None
        )

        result["poam_items"] = _load_poam(conn)
        result["poam_open"] = _scalar(
            conn, "SELECT COUNT(*) FROM poam_items WHERE status = 'open'"
        )
        result["poam_in_progress"] = _scalar(
            conn, "SELECT COUNT(*) FROM poam_items WHERE status = 'in_progress'"
        )

        result.update(_load_ato(conn))
    except Exception as exc:
        logger.exception("compliance_view: unexpected failure building compliance data")
        result["data_source_error"] = str(exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result
