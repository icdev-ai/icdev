# CUI // SP-CTI
"""pmacct NetFlow/IPFIX/sFlow connector for NOCC SLA enrichment.

pmacct (https://github.com/pmacct/pmacct) is the standard open-source flow
accounting daemon. This connector reads aggregated accounting data from either:
  - pmacct PostgreSQL accounting tables (pgsql plugin)
  - pmacct JSON export files (print plugin with json_output true)

and ingests flow data into NOCC noc_sla_records for SLA tracking (traffic_bps).

Configuration (env):
  PMACCT_DB_HOST        — PostgreSQL host (default: localhost)
  PMACCT_DB_PORT        — PostgreSQL port (default: 5432)
  PMACCT_DB_NAME        — pmacct database name (default: pmacct)
  PMACCT_DB_USER        — database user (default: pmacct)
  PMACCT_DB_PASS        — database password
  PMACCT_SRC_TABLE      — accounting table name (default: acct)
  PMACCT_JSON_DIR       — path to pmacct JSON export directory (overrides DB)
  PMACCT_CIRCUIT_FIELD  — field mapping to circuit_id (default: in_iface)
  PMACCT_CARRIER_FIELD  — field mapping to carrier (default: peer_ip_src)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PMACCT_DB_HOST       = os.environ.get("PMACCT_DB_HOST", "localhost")
PMACCT_DB_PORT       = int(os.environ.get("PMACCT_DB_PORT", "5432"))
PMACCT_DB_NAME       = os.environ.get("PMACCT_DB_NAME", "pmacct")
PMACCT_DB_USER       = os.environ.get("PMACCT_DB_USER", "pmacct")
PMACCT_DB_PASS       = os.environ.get("PMACCT_DB_PASS", "")
PMACCT_SRC_TABLE     = os.environ.get("PMACCT_SRC_TABLE", "acct")
PMACCT_JSON_DIR      = os.environ.get("PMACCT_JSON_DIR", "")
PMACCT_CIRCUIT_FIELD = os.environ.get("PMACCT_CIRCUIT_FIELD", "in_iface")
PMACCT_CARRIER_FIELD = os.environ.get("PMACCT_CARRIER_FIELD", "peer_ip_src")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_exec(conn, sql_pg: str, params: tuple = ()) -> None:
    try:
        conn.execute(sql_pg, params)
    except Exception:
        conn.execute(sql_pg.replace("%s", "?"), params)


def _get_pmacct_pg_connection():
    """Open a direct connection to the pmacct PostgreSQL accounting database."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return psycopg2.connect(
        host=PMACCT_DB_HOST,
        port=PMACCT_DB_PORT,
        dbname=PMACCT_DB_NAME,
        user=PMACCT_DB_USER,
        password=PMACCT_DB_PASS,
        cursor_factory=RealDictCursor,
    )


def _read_json_records(json_dir: str) -> list[dict]:
    """Read pmacct JSON export files (NDJSON lines) from the directory."""
    records = []
    base = Path(json_dir)
    if not base.exists():
        return records
    for jf in sorted(base.glob("*.json"))[-10:]:  # last 10 export files
        try:
            with open(jf, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception:
            pass
    return records


def fetch_accounting_records(limit: int = 1000) -> list[dict]:
    """Fetch flow accounting records from pmacct (JSON dir preferred over DB)."""
    if PMACCT_JSON_DIR:
        raw = _read_json_records(PMACCT_JSON_DIR)
        return raw[:limit]

    try:
        pmconn = _get_pmacct_pg_connection()
        cur = pmconn.cursor()
        cur.execute(
            f"SELECT * FROM {PMACCT_SRC_TABLE} ORDER BY stamp_updated DESC LIMIT %s",
            (limit,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        pmconn.close()
        return rows
    except Exception as exc:
        return [{"_error": str(exc)}]


def ingest_to_nocc(nocc_conn, records: Optional[list[dict]] = None) -> dict:
    """Ingest pmacct flow records into NOCC noc_sla_records.

    Converts each pmacct record into a traffic_bps SLA measurement:
      circuit_id  ← PMACCT_CIRCUIT_FIELD (default: in_iface)
      carrier     ← PMACCT_CARRIER_FIELD  (default: peer_ip_src)
      measured_value ← bytes * 8 (bits; callers divide by window duration for bps)
    """
    if records is None:
        records = fetch_accounting_records()

    inserted = 0
    errors = 0
    now = _now()

    for rec in records:
        if "_error" in rec:
            errors += 1
            continue
        try:
            circuit_id = str(rec.get(PMACCT_CIRCUIT_FIELD, "unknown"))
            carrier    = str(rec.get(PMACCT_CARRIER_FIELD, ""))
            byte_count = int(rec.get("bytes", 0) or 0)
            stamp_start = str(rec.get("stamp_inserted", now))
            stamp_end   = str(rec.get("stamp_updated", now))
            measured_bits = byte_count * 8

            _safe_exec(
                nocc_conn,
                """INSERT INTO noc_sla_records
                   (circuit_id, carrier, sla_type, target_value, measured_value,
                    period_start, period_end, classification)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (circuit_id, carrier, "traffic_bps", 0, measured_bits,
                 stamp_start, stamp_end, "CUI"),
            )
            inserted += 1
        except Exception:
            errors += 1

    if inserted > 0:
        nocc_conn.commit()

    return {
        "source": "pmacct",
        "records_processed": len(records),
        "records_inserted": inserted,
        "errors": errors,
        "ingested_at": now,
    }


def test_connection() -> dict:
    """Verify pmacct connectivity (JSON dir or PostgreSQL)."""
    if PMACCT_JSON_DIR:
        path = Path(PMACCT_JSON_DIR)
        return {
            "backend": "json_dir",
            "path": str(path),
            "exists": path.exists(),
            "file_count": len(list(path.glob("*.json"))) if path.exists() else 0,
        }
    try:
        conn = _get_pmacct_pg_connection()
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS n FROM {PMACCT_SRC_TABLE}")
        row = cur.fetchone()
        conn.close()
        return {
            "backend": "postgresql",
            "host": PMACCT_DB_HOST,
            "database": PMACCT_DB_NAME,
            "table": PMACCT_SRC_TABLE,
            "record_count": int(row["n"]) if row else 0,
            "status": "ok",
        }
    except Exception as exc:
        return {"backend": "postgresql", "status": "error", "error": str(exc)}


if __name__ == "__main__":
    import json as _j
    print(_j.dumps(test_connection(), indent=2))
