# CUI // SP-CTI
"""cam_seed_sops.py — Seed migration SOPs from context/migration/sop_catalog/*.json.

Routes each SOP to the correct canvas table based on the `sop_target_canvas` field:
  mc  → mc_sops   (migration canvas)
  ddc → ddc_sops  (data canvas)
  idc → idc_sops  (infra canvas)
  ndc → ndc_sops  (network canvas)

Usage:
  python tools/migration_canvas/cam_seed_sops.py
  python tools/migration_canvas/cam_seed_sops.py --reset
  python tools/migration_canvas/cam_seed_sops.py --json
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
from pathlib import Path

logger = get_logger("icdev.cam_seed_sops")

_CATALOG_DIR = Path(__file__).resolve().parent.parent.parent / "context" / "migration" / "sop_catalog"

# IDC category CHECK constraint values — map free-form category strings to valid values
_IDC_CATEGORY_MAP: dict[str, str] = {
    "infra-migration": "infra_change_management",
    "infra_migration": "infra_change_management",
    "infra_change": "infra_change_management",
    "infra_change_management": "infra_change_management",
    "capacity": "capacity_planning",
    "capacity_planning": "capacity_planning",
    "onboarding": "cloud_account_onboarding",
    "cloud_account_onboarding": "cloud_account_onboarding",
    "patch": "patch_management",
    "patch_management": "patch_management",
    "disaster_recovery": "disaster_recovery",
    "dr": "disaster_recovery",
    "access": "access_management",
    "access_management": "access_management",
    "security-migration": "access_management",
    "security_migration": "access_management",
    "security": "access_management",
    "iam": "access_management",
    "rbac": "access_management",
}


def _mc_conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _canvas_conn(canvas: str):
    if canvas == "ddc":
        from tools.data_canvas.db.init_db import get_connection
    elif canvas == "idc":
        from tools.infra_canvas.db.init_db import get_connection
    elif canvas == "ndc":
        from tools.network.db.init_db import get_connection
    else:
        return _mc_conn()
    return get_connection()


def _insert_mc_sop(conn, sop: dict) -> bool:
    existing = conn.execute("SELECT id FROM mc_sops WHERE id=?", (sop["id"],)).fetchone()
    if existing:
        return False
    conn.execute(
        "INSERT OR IGNORE INTO mc_sops "
        "(id, title, sop_type, description, purpose, scope, steps, approval_status, classification) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            sop["id"],
            sop["title"],
            sop.get("category", "custom"),
            sop.get("description", ""),
            sop.get("purpose", sop.get("description", "")),
            sop.get("scope", ""),
            json.dumps(sop.get("steps", [])),
            "approved",
            "CUI // SP-CTI",
        ),
    )
    conn.commit()
    return True


def _insert_ddc_sop(conn, sop: dict) -> bool:
    existing = conn.execute("SELECT id FROM ddc_sops WHERE id=?", (sop["id"],)).fetchone()
    if existing:
        return False
    conn.execute(
        "INSERT OR IGNORE INTO ddc_sops "
        "(id, title, category, description, purpose, scope, steps_json, status, classification) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            sop["id"],
            sop["title"],
            sop.get("category", "general"),
            sop.get("description", ""),
            sop.get("purpose", sop.get("description", "")),
            sop.get("scope", ""),
            json.dumps(sop.get("steps", [])),
            "approved",
            "CUI // SP-CTI",
        ),
    )
    conn.commit()
    return True


def _insert_idc_sop(conn, sop: dict) -> bool:
    existing = conn.execute("SELECT id FROM idc_sops WHERE id=?", (sop["id"],)).fetchone()
    if existing:
        return False
    raw_cat = sop.get("category", "general")
    category = _IDC_CATEGORY_MAP.get(raw_cat, "general")
    conn.execute(
        "INSERT OR IGNORE INTO idc_sops "
        "(id, title, category, description, purpose, scope, steps, status, classification) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            sop["id"],
            sop["title"],
            category,
            sop.get("description", ""),
            sop.get("purpose", sop.get("description", "")),
            sop.get("scope", ""),
            json.dumps(sop.get("steps", [])),
            "approved",
            "CUI",
        ),
    )
    conn.commit()
    return True


def _insert_ndc_sop(conn, sop: dict) -> bool:
    existing = conn.execute("SELECT sop_id FROM ndc_sops WHERE sop_id=?", (sop["id"],)).fetchone()
    if existing:
        return False
    conn.execute(
        "INSERT OR IGNORE INTO ndc_sops "
        "(sop_id, title, category, description, steps, status, classification) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            sop["id"],
            sop["title"],
            sop.get("category", "change_window"),
            sop.get("description", ""),
            json.dumps(sop.get("steps", [])),
            "approved",
            "CUI",
        ),
    )
    conn.commit()
    return True


_INSERTERS = {
    "mc":  _insert_mc_sop,
    "ddc": _insert_ddc_sop,
    "idc": _insert_idc_sop,
    "ndc": _insert_ndc_sop,
}


def _reset_canvas_sops(canvas: str) -> None:
    c = _canvas_conn(canvas)
    try:
        if canvas == "mc":
            c.execute("DELETE FROM mc_sops WHERE id LIKE 'mc-sop-%'")
        elif canvas == "ddc":
            c.execute("DELETE FROM ddc_sops WHERE id LIKE 'mc-sop-%' OR id LIKE 'ddc-sop-%'")
        elif canvas == "idc":
            c.execute("DELETE FROM idc_sops WHERE id LIKE 'idc-sop-%'")
        elif canvas == "ndc":
            c.execute("DELETE FROM ndc_sops WHERE sop_id LIKE 'mc-sop-%' OR sop_id LIKE 'ndc-sop-%'")
        c.commit()
    finally:
        c.close()


def seed(reset: bool = False) -> dict[str, int]:
    """Discover and seed all SOPs from context/migration/sop_catalog/.

    Returns dict of {canvas: count_inserted}.
    """
    if not _CATALOG_DIR.exists():
        logger.warning("SOP catalog directory not found: %s", _CATALOG_DIR)
        return {}

    if reset:
        for canvas in ("mc", "ddc", "idc", "ndc"):
            _reset_canvas_sops(canvas)

    counts: dict[str, int] = {"mc": 0, "ddc": 0, "idc": 0, "ndc": 0}
    errors: list[str] = []

    sop_files = sorted(_CATALOG_DIR.glob("*.json"))
    if not sop_files:
        logger.warning("No SOP JSON files found in %s", _CATALOG_DIR)
        return counts

    # Group by canvas to open each DB connection once
    by_canvas: dict[str, list[dict]] = {"mc": [], "ddc": [], "idc": [], "ndc": []}
    for fpath in sop_files:
        try:
            sop = json.loads(fpath.read_text(encoding="utf-8"))
            canvas = sop.get("sop_target_canvas", "mc")
            if canvas not in by_canvas:
                logger.warning("Unknown canvas '%s' in %s — skipping", canvas, fpath.name)
                continue
            by_canvas[canvas].append(sop)
        except Exception as exc:
            errors.append(f"{fpath.name}: {exc}")
            logger.error("Failed to parse %s: %s", fpath.name, exc)

    for canvas, sops in by_canvas.items():
        if not sops:
            continue
        inserter = _INSERTERS[canvas]
        conn = _canvas_conn(canvas)
        try:
            for sop in sops:
                try:
                    if inserter(conn, sop):
                        counts[canvas] += 1
                except Exception as exc:
                    errors.append(f"{sop.get('id', '?')} → {canvas}: {exc}")
                    logger.error("Failed to insert SOP %s into %s: %s", sop.get("id"), canvas, exc)
        finally:
            conn.close()

    total = sum(counts.values())
    logger.info("Seeded %d SOPs: %s", total, counts)
    if errors:
        logger.warning("%d errors during seed: %s", len(errors), errors)
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Seed migration SOPs from context/migration/sop_catalog/")
    p.add_argument("--reset", action="store_true", help="Delete existing seeded SOPs before inserting")
    p.add_argument("--json", dest="as_json", action="store_true", help="Output results as JSON")
    args = p.parse_args()

    counts = seed(reset=args.reset)
    total = sum(counts.values())

    if args.as_json:
        print(json.dumps({"status": "ok", "total": total, "by_canvas": counts}))
    else:
        print(f"SOPs seeded: {total}")
        for canvas, n in counts.items():
            if n:
                print(f"  {canvas}: {n}")


if __name__ == "__main__":
    main()
