#!/usr/bin/env python3
# CUI // SP-CTI
"""CISA Known Exploited Vulnerabilities (KEV) importer for Strategos.

Fetches the CISA KEV catalog (public JSON, no auth) and merges it into
sg_cve_feed via two operations per entry:
  1. UPSERT — insert a minimal row if the CVE is not yet in sg_cve_feed
  2. UPDATE — set is_kev=1 and kev_due_date on any matching row

Data source: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json

Usage
-----
  python tools/strategos/cisa_kev_importer.py --sync           # fetch + merge
  python tools/strategos/cisa_kev_importer.py --file kev.json  # local file
  python tools/strategos/cisa_kev_importer.py --json           # JSON summary output
  python tools/strategos/cisa_kev_importer.py --sync --json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection, is_pg  # noqa: E402

logger = get_logger(__name__)

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
DOWNLOAD_TIMEOUT = 60  # seconds


# ---------------------------------------------------------------------------
# Fetch / load
# ---------------------------------------------------------------------------

def _fetch_kev_json() -> dict:
    """Download KEV catalog from CISA. Raises on network error."""
    parsed = urlparse(KEV_URL)
    if parsed.scheme != "https":
        raise ValueError(f"Unexpected scheme in KEV_URL: {parsed.scheme!r} — only https is permitted")
    try:
        req = urllib.request.Request(KEV_URL, headers={"User-Agent": "ICDEV-Strategos/1.0"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:  # nosec B310 — scheme validated above
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch KEV catalog: {exc}") from exc


def _load_local(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Merge into sg_cve_feed
# ---------------------------------------------------------------------------

def _merge(conn, vulnerabilities: list[dict]) -> tuple[int, int]:
    """Merge KEV entries; returns (kev_flagged, new_rows_inserted).

    Two queries per CVE (no exception-based flow for PG compatibility):
      1. INSERT ... ON CONFLICT DO NOTHING  — adds row only if absent
      2. UPDATE ... SET is_kev=1            — always flags the row
    """
    kev_flagged = 0
    new_inserted = 0
    now_ts = datetime.now(timezone.utc).isoformat()
    pg = is_pg()
    ph = "%s" if pg else "?"

    for entry in vulnerabilities:
        cve_id: str = (entry.get("cveID") or "").strip()
        if not cve_id:
            continue

        description: str = (entry.get("vulnerabilityName") or "").strip()
        date_added: str = (entry.get("dateAdded") or "").strip()
        due_date: str | None = (entry.get("dueDate") or "").strip() or None

        created_at = date_added if date_added else now_ts
        row_id = str(uuid.uuid4())

        # Step 1: flag any existing row — no exception risk.
        update_sql = (
            f"UPDATE sg_cve_feed "  # nosec B608
            f"   SET is_kev = 1, kev_due_date = {ph}, updated_at = {ph} "
            f" WHERE cve_id = {ph}"
        )
        cur = conn.execute(update_sql, (due_date, now_ts, cve_id))
        updated_count = getattr(cur, "rowcount", None)

        if updated_count == 0:
            # Step 2: CVE not in table yet — insert a minimal row with KEV flags set.
            insert_sql = (
                f"INSERT INTO sg_cve_feed "  # nosec B608
                f"    (id, cve_id, title, description, status,"
                f"     created_at, updated_at, is_kev, kev_due_date) "
                f"VALUES ({ph},{ph},{ph},{ph},'new',{ph},{ph},1,{ph})"
            )
            conn.execute(
                insert_sql,
                (row_id, cve_id, description, description, created_at, now_ts, due_date),
            )
            new_inserted += 1

        kev_flagged += 1

    return kev_flagged, new_inserted


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(
    *,
    file: Path | None = None,
    sync: bool = False,
    as_json: bool = False,
) -> dict:
    """Run the CISA KEV importer.

    Exactly one of `file` or `sync` must be truthy.
    """
    if not file and not sync:
        raise ValueError("Specify --sync or --file")

    # Load catalog
    if file is not None:
        try:
            catalog = _load_local(file)
        except Exception as exc:
            msg = f"Failed to load {file}: {exc}"
            logger.error(msg)
            result = {"ok": False, "error": msg, "kev_flagged": 0, "new_inserted": 0}
            if as_json:
                print(json.dumps(result, indent=2))
            else:
                print(f"ERROR: {msg}", file=sys.stderr)
            return result
    else:
        try:
            catalog = _fetch_kev_json()
        except RuntimeError as exc:
            msg = str(exc)
            logger.error(msg)
            result = {"ok": False, "error": msg, "kev_flagged": 0, "new_inserted": 0}
            if as_json:
                print(json.dumps(result, indent=2))
            else:
                print(f"ERROR: {msg} (network unavailable?)", file=sys.stderr)
            return result

    vulnerabilities: list[dict] = catalog.get("vulnerabilities", [])
    total_catalog = len(vulnerabilities)

    conn = get_connection()
    try:
        kev_flagged, new_inserted = _merge(conn, vulnerabilities)
        conn.commit()
    finally:
        conn.close()

    result = {
        "ok": True,
        "catalog_entries": total_catalog,
        "kev_flagged": kev_flagged,
        "new_inserted": new_inserted,
    }

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{kev_flagged} CVEs flagged as KEV, {new_inserted} new rows inserted")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge CISA Known Exploited Vulnerabilities catalog into sg_cve_feed"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--sync", action="store_true", help="Fetch KEV catalog from CISA and merge")
    src.add_argument("--file", type=Path, metavar="PATH", help="Load KEV JSON from local file")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    result = run(file=args.file, sync=args.sync, as_json=args.as_json)
    if not result.get("ok", True):
        sys.exit(1)


if __name__ == "__main__":
    main()
