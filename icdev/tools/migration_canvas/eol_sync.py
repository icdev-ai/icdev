# CUI // SP-CTI
"""Vendor EOL database sync — auto-pull hardware end-of-life dates.

Air-gap safe: if sync fails or network is unavailable, falls back to the
static seed in args/eol_data.yaml.  All data is cached in mc_net_eol_data
(migration_canvas.db).

Public functions:
    sync_eol_database(vendors, force)  → dict  (summary of records synced)
    get_eol_date(model)                → dict | None
    flag_eol_devices(inventory)        → list[dict]  (annotated with eol_status)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

logger = get_logger("icdev.migration_canvas.eol_sync")

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_EOL_YAML = _ICDEV_ROOT / "args" / "eol_data.yaml"

_EOL_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS mc_net_eol_data (
    id            TEXT PRIMARY KEY,
    vendor        TEXT NOT NULL,
    model_pattern TEXT NOT NULL,
    eol_date      TEXT,
    eos_date      TEXT,
    eosm_date     TEXT,
    source        TEXT DEFAULT 'static_seed',
    synced_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mc_net_eol ON mc_net_eol_data(vendor, model_pattern);
"""


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _ensure_table():
    with _conn() as db:
        db.executescript(_EOL_TABLE_DDL)
        db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Static seed loader ────────────────────────────────────────────────────────

def _load_yaml_seed() -> list[dict]:
    """Load static EOL entries from args/eol_data.yaml."""
    try:
        import yaml  # optional — fall back to simple line parser
    except ImportError:
        yaml = None  # type: ignore

    if not _EOL_YAML.exists():
        return []

    try:
        if yaml:
            with open(_EOL_YAML, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("eol_entries", [])
        else:
            # Minimal YAML parser for the simple list structure
            entries: list[dict] = []
            current: dict = {}
            with open(_EOL_YAML, encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip()
                    if line.strip().startswith("- vendor:"):
                        if current:
                            entries.append(current)
                        current = {"vendor": line.split("vendor:")[-1].strip()}
                    elif ":" in line and current:
                        key, _, val = line.strip().partition(":")
                        key = key.strip().lstrip("-").strip()
                        val = val.strip().strip('"').strip("'")
                        if key and val:
                            current[key] = val
            if current:
                entries.append(current)
            return entries
    except Exception as exc:
        logger.warning("EOL YAML load failed: %s", exc)
        return []


def _upsert_entries(db, entries: list[dict], source: str = "static_seed"):
    now = _now()
    for e in entries:
        vendor = e.get("vendor", "").lower()
        model = e.get("model_pattern", "")
        if not vendor or not model:
            continue
        db.execute(
            "INSERT INTO mc_net_eol_data "
            "(id, vendor, model_pattern, eol_date, eos_date, eosm_date, source, synced_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(vendor, model_pattern) DO UPDATE SET "
            "eol_date=excluded.eol_date, eos_date=excluded.eos_date, "
            "eosm_date=excluded.eosm_date, source=excluded.source, synced_at=excluded.synced_at",
            (
                str(uuid.uuid4()),
                vendor,
                model,
                e.get("eol_date"),
                e.get("eos_date"),
                e.get("eosm_date"),
                source,
                now,
            ),
        )


# ── Live sync routines (best-effort, offline-safe) ────────────────────────────

def _sync_cisco(db) -> int:
    """Attempt to refresh Cisco EOL data from public Cisco EoX API."""
    import urllib.request
    import urllib.error

    # Cisco API v5 — returns JSON without auth for public product IDs
    # We check a curated set of common platform IDs
    platform_ids = [
        "ASR1001-X", "ASR1002-X", "ASR9001", "ASA5506-X", "ASA5508-X",
        "WS-C2960X-48FPD-L", "WS-C3650-48FQ-S", "WS-C4500X-16SFP+",
        "N9K-C9300-48S", "ISR4321", "ISR4331", "ISR4351",
    ]
    url_base = "https://apix.cisco.com/supporttools/eox/rest/5/EOXByProductID/1/"
    count = 0
    try:
        for pid in platform_ids:
            url = f"{url_base}{pid}?responseencoding=json"
            req = urllib.request.Request(
                url, headers={"Accept": "application/json", "User-Agent": "ICDEV-EolSync/1.0"}
            )
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                eox_records = data.get("EOXRecord", [])
                for rec in eox_records:
                    eol = rec.get("EndOfSWMaintenanceReleases", {}).get("value", "")
                    eos = rec.get("LastDateOfSupport", {}).get("value", "")
                    pid_name = rec.get("EOLProductID", pid)
                    db.execute(
                        "INSERT INTO mc_net_eol_data "
                        "(id, vendor, model_pattern, eol_date, eos_date, source, synced_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT(vendor, model_pattern) DO UPDATE SET "
                        "eol_date=excluded.eol_date, eos_date=excluded.eos_date, "
                        "source=excluded.source, synced_at=excluded.synced_at",
                        (str(uuid.uuid4()), "cisco", pid_name, eos or None, eol or None, "cisco_api", _now()),
                    )
                    count += 1
            except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                logger.warning("_sync_cisco: best-effort INSERT into mc_net_eol_data failed (non-blocking): %s", _exc)
    except Exception as exc:
        logger.debug("Cisco EOL sync skipped: %s", exc)
    return count


# ── Public API ────────────────────────────────────────────────────────────────

def sync_eol_database(vendors: list[str] | None = None, force: bool = False) -> dict:
    """Sync EOL database from static seed + optional live sources.

    Args:
        vendors: Optional list of vendor slugs to sync (default: all).
        force: Re-sync even if data was recently synced.

    Returns:
        Summary dict: {"seeded": int, "live_synced": int, "total": int}
    """
    _ensure_table()
    seeded = 0
    live = 0

    with _conn() as db:
        # Always load static seed
        seed_entries = _load_yaml_seed()
        if vendors:
            seed_entries = [e for e in seed_entries if e.get("vendor", "").lower() in [v.lower() for v in vendors]]
        _upsert_entries(db, seed_entries, "static_seed")
        seeded = len(seed_entries)

        # Best-effort live sync
        if not vendors or "cisco" in [v.lower() for v in (vendors or [])]:
            live += _sync_cisco(db)

        db.commit()
        total = db.execute("SELECT COUNT(*) FROM mc_net_eol_data").fetchone()[0]

    logger.info("EOL sync complete: seeded=%d live=%d total=%d", seeded, live, total)
    return {"seeded": seeded, "live_synced": live, "total": total}


def get_eol_date(model: str) -> dict | None:
    """Look up EOL dates for a device model from the local cache.

    Tries exact match first, then prefix match, then substring match.

    Returns dict with eol_date, eos_date, vendor, source; or None if not found.
    """
    _ensure_table()
    model_lower = model.lower().strip()

    with _conn() as db:
        rows = db.execute(
            "SELECT vendor, model_pattern, eol_date, eos_date, eosm_date, source "
            "FROM mc_net_eol_data ORDER BY LENGTH(model_pattern) DESC"
        ).fetchall()

    for row in rows:
        pattern = (row[1] or "").lower()
        # Exact match
        if pattern == model_lower:
            return dict(zip(["vendor", "model_pattern", "eol_date", "eos_date", "eosm_date", "source"], row))
        # Substring match (pattern in model or model in pattern)
        if pattern in model_lower or model_lower in pattern:
            return dict(zip(["vendor", "model_pattern", "eol_date", "eos_date", "eosm_date", "source"], row))
        # Regex match (pattern may contain spaces that act as wildcards)
        try:
            if re.search(re.escape(pattern).replace(r"\ ", r".*"), model_lower):
                return dict(zip(["vendor", "model_pattern", "eol_date", "eos_date", "eosm_date", "source"], row))
        except re.error:
            pass

    return None


def flag_eol_devices(inventory: list[dict]) -> list[dict]:
    """Annotate each device in inventory with eol_status.

    Adds/updates field 'eol_status': 'eol' | 'eos' | 'approaching_eol' | 'active' | 'unknown'.
    Also adds 'eol_date' and 'eos_date' if found.
    """
    today = date.today()
    result = []
    for device in inventory:
        d = dict(device)
        model = d.get("model") or d.get("tgt_model") or d.get("src_model") or ""
        eol_info = get_eol_date(model) if model else None

        if not eol_info:
            d.setdefault("eol_status", "unknown")
            result.append(d)
            continue

        eol_str = eol_info.get("eol_date") or ""
        eos_str = eol_info.get("eos_date") or ""
        d["eol_date"] = eol_str
        d["eos_date"] = eos_str

        try:
            eol = date.fromisoformat(eol_str[:10]) if eol_str else None
        except ValueError:
            eol = None
        try:
            eos = date.fromisoformat(eos_str[:10]) if eos_str else None
        except ValueError:
            eos = None

        if eol and today >= eol:
            d["eol_status"] = "eol"
        elif eos and today >= eos:
            d["eol_status"] = "eos"
        elif eol and (eol - today).days <= 365:
            d["eol_status"] = "approaching_eol"
        else:
            d["eol_status"] = "active"

        result.append(d)

    return result
