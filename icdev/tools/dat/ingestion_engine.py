# CUI // SP-CTI
"""DAT Ingestion Engine — modular pipeline for three diplomatic data sources.

Reads from:
  1. State Department cable traffic  — CSV files
  2. UNSC meeting schedule           — JSON feeds
  3. Back-channel communication metadata — log files

Produces a unified JSON manifest with per-source record counts and an
append-only audit row in data/dat.db.

Usage::

    python tools/dat/ingestion_engine.py --json
    python tools/dat/ingestion_engine.py --dry-run
    python tools/dat/ingestion_engine.py --config args/dat_config.yaml --json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger("icdev.dat.ingestion_engine")

_DEFAULT_CONFIG = _REPO_ROOT / "args" / "dat_config.yaml"

# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dat_ingestion_log (
    id              TEXT PRIMARY KEY,
    run_at          TEXT NOT NULL,
    config_path     TEXT NOT NULL,
    state_dept_count INTEGER,
    unsc_count       INTEGER,
    backchannel_count INTEGER,
    total_count      INTEGER,
    status          TEXT NOT NULL,
    manifest_path   TEXT,
    detail_json     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


def _ensure_schema(conn: Any) -> None:
    conn.execute(_SCHEMA_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------


def _resolve_source_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return _REPO_ROOT / p


def read_state_dept_cables(cfg: dict) -> dict:
    """Ingest all CSV files from the configured cable traffic directory."""
    source_path = _resolve_source_path(cfg.get("source_path", "data/dat/state_dept_cables"))
    glob_pattern = cfg.get("file_glob", "*.csv")
    dialect = cfg.get("csv_dialect", "excel")
    extra_headers = int(cfg.get("extra_header_rows", 0))

    if not source_path.exists():
        logger.warning("state_dept_cables path not found: %s — returning 0 records", source_path)
        return {
            "source": "state_dept_cables",
            "source_path": str(source_path),
            "status": "path_not_found",
            "record_count": 0,
            "files_read": 0,
            "errors": [f"Path not found: {source_path}"],
        }

    files = list(source_path.rglob(glob_pattern)) if source_path.is_dir() else [source_path]
    total = 0
    errors: list[str] = []

    for fp in files:
        try:
            with fp.open(newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh, dialect=dialect)
                rows = list(reader)
            # Subtract 1 header row + any extra declared headers
            data_rows = max(0, len(rows) - 1 - extra_headers)
            total += data_rows
            logger.debug("cables: %s → %d records", fp.name, data_rows)
        except Exception as exc:
            msg = f"{fp.name}: {exc}"
            errors.append(msg)
            logger.warning("cables read error: %s", msg)

    return {
        "source": "state_dept_cables",
        "source_path": str(source_path),
        "status": "ok" if not errors else "partial",
        "record_count": total,
        "files_read": len(files),
        "errors": errors,
    }


def read_unsc_schedule(cfg: dict) -> dict:
    """Ingest all JSON feed files from the configured UNSC schedule directory."""
    source_path = _resolve_source_path(cfg.get("source_path", "data/dat/unsc_schedule"))
    glob_pattern = cfg.get("file_glob", "*.json")
    records_key = cfg.get("records_key", "meetings")

    if not source_path.exists():
        logger.warning("unsc_schedule path not found: %s — returning 0 records", source_path)
        return {
            "source": "unsc_schedule",
            "source_path": str(source_path),
            "status": "path_not_found",
            "record_count": 0,
            "files_read": 0,
            "errors": [f"Path not found: {source_path}"],
        }

    files = list(source_path.rglob(glob_pattern)) if source_path.is_dir() else [source_path]
    total = 0
    errors: list[str] = []

    for fp in files:
        try:
            with fp.open(encoding="utf-8") as fh:
                data = json.load(fh)

            if records_key:
                records = data.get(records_key, []) if isinstance(data, dict) else []
            else:
                records = data if isinstance(data, list) else [data]

            count = len(records)
            total += count
            logger.debug("unsc: %s → %d records", fp.name, count)
        except Exception as exc:
            msg = f"{fp.name}: {exc}"
            errors.append(msg)
            logger.warning("unsc read error: %s", msg)

    return {
        "source": "unsc_schedule",
        "source_path": str(source_path),
        "status": "ok" if not errors else "partial",
        "record_count": total,
        "files_read": len(files),
        "errors": errors,
    }


def read_backchannel_logs(cfg: dict) -> dict:
    """Ingest back-channel communication metadata log files.

    Each non-empty, non-comment line counts as one metadata record.
    """
    source_path = _resolve_source_path(cfg.get("source_path", "data/dat/backchannel_logs"))
    glob_pattern = cfg.get("file_glob", "*.log")
    comment_prefix = cfg.get("comment_prefix", "#")

    if not source_path.exists():
        logger.warning("backchannel_logs path not found: %s — returning 0 records", source_path)
        return {
            "source": "backchannel_logs",
            "source_path": str(source_path),
            "status": "path_not_found",
            "record_count": 0,
            "files_read": 0,
            "errors": [f"Path not found: {source_path}"],
        }

    files = list(source_path.rglob(glob_pattern)) if source_path.is_dir() else [source_path]
    total = 0
    errors: list[str] = []

    for fp in files:
        try:
            count = 0
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped and not stripped.startswith(comment_prefix):
                        count += 1
            total += count
            logger.debug("backchannel: %s → %d records", fp.name, count)
        except Exception as exc:
            msg = f"{fp.name}: {exc}"
            errors.append(msg)
            logger.warning("backchannel read error: %s", msg)

    return {
        "source": "backchannel_logs",
        "source_path": str(source_path),
        "status": "ok" if not errors else "partial",
        "record_count": total,
        "files_read": len(files),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest(
    cables_result: dict,
    unsc_result: dict,
    backchannel_result: dict,
    config_path: str,
) -> dict:
    run_at = datetime.now(timezone.utc).isoformat()
    total = (
        cables_result["record_count"]
        + unsc_result["record_count"]
        + backchannel_result["record_count"]
    )
    all_ok = all(
        r["status"] in ("ok", "path_not_found")
        for r in (cables_result, unsc_result, backchannel_result)
    )
    return {
        "manifest_id": str(uuid.uuid4()),
        "run_at": run_at,
        "config_path": config_path,
        "classification": "CUI",
        "sources": {
            "state_dept_cables": cables_result,
            "unsc_schedule": unsc_result,
            "backchannel_logs": backchannel_result,
        },
        "total_records": total,
        "status": "ok" if all_ok else "partial",
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


_ALLOWED_AUDIT_TABLES = {"dat_ingestion_log"}


def _persist_audit(manifest: dict, db_path: Path, audit_table: str, dry_run: bool) -> None:
    if dry_run:
        logger.debug("dry-run: skipping DB write")
        return
    if audit_table not in _ALLOWED_AUDIT_TABLES:
        raise ValueError(f"audit_table '{audit_table}' is not in the allowlist")
    try:
        conn = get_connection(str(db_path))
        _ensure_schema(conn)
        src = manifest["sources"]
        conn.execute(
            f"INSERT INTO {audit_table} "  # nosec B608 — table name validated against allowlist above
            "(id, run_at, config_path, state_dept_count, unsc_count, "
            "backchannel_count, total_count, status, manifest_path, detail_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                manifest["manifest_id"],
                manifest["run_at"],
                manifest["config_path"],
                src["state_dept_cables"]["record_count"],
                src["unsc_schedule"]["record_count"],
                src["backchannel_logs"]["record_count"],
                manifest["total_records"],
                manifest["status"],
                manifest.get("manifest_path", ""),
                json.dumps(manifest, default=str),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("audit DB write failed: %s", exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config_path: Path | None = None, dry_run: bool = False) -> dict:
    """Execute the full ingestion pipeline and return the manifest dict."""
    cfg_path = config_path or _DEFAULT_CONFIG
    with cfg_path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    cables_cfg = cfg.get("state_dept_cables", {})
    unsc_cfg = cfg.get("unsc_schedule", {})
    back_cfg = cfg.get("backchannel_logs", {})

    cables_result = (
        read_state_dept_cables(cables_cfg)
        if cables_cfg.get("enabled", True)
        else {"source": "state_dept_cables", "status": "disabled", "record_count": 0, "files_read": 0, "errors": []}
    )
    unsc_result = (
        read_unsc_schedule(unsc_cfg)
        if unsc_cfg.get("enabled", True)
        else {"source": "unsc_schedule", "status": "disabled", "record_count": 0, "files_read": 0, "errors": []}
    )
    back_result = (
        read_backchannel_logs(back_cfg)
        if back_cfg.get("enabled", True)
        else {"source": "backchannel_logs", "status": "disabled", "record_count": 0, "files_read": 0, "errors": []}
    )

    manifest = build_manifest(cables_result, unsc_result, back_result, str(cfg_path))

    # Write manifest to disk
    output_cfg = cfg.get("output", {})
    manifest_rel = output_cfg.get("manifest_path", "data/dat/ingestion_manifest.json")
    manifest_path = _REPO_ROOT / manifest_rel
    manifest["manifest_path"] = str(manifest_path)

    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        logger.info("manifest written: %s", manifest_path)

    # Audit DB
    db_cfg = cfg.get("database", {})
    db_path = _REPO_ROOT / db_cfg.get("path", "data/dat.db")
    audit_table = db_cfg.get("audit_table", "dat_ingestion_log")
    if not dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    _persist_audit(manifest, db_path, audit_table, dry_run)

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="DAT ingestion pipeline — reads cables, UNSC schedule, and back-channel logs."
    )
    p.add_argument("--config", metavar="PATH", help="Path to dat_config.yaml (default: args/dat_config.yaml)")
    p.add_argument("--dry-run", action="store_true", help="Read sources but do not write manifest or DB")
    p.add_argument("--json", action="store_true", dest="as_json", help="Output manifest as JSON")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )

    cfg_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    if not cfg_path.is_absolute():
        cfg_path = _REPO_ROOT / cfg_path

    manifest = run(config_path=cfg_path, dry_run=args.dry_run)

    if args.as_json:
        print(json.dumps(manifest, indent=2, default=str))
    else:
        src = manifest["sources"]
        print(f"DAT Ingestion — {manifest['run_at']}")
        print(f"  state_dept_cables : {src['state_dept_cables']['record_count']:>6} records")
        print(f"  unsc_schedule     : {src['unsc_schedule']['record_count']:>6} records")
        print(f"  backchannel_logs  : {src['backchannel_logs']['record_count']:>6} records")
        print("  " + "-" * 29)
        print(f"  total             : {manifest['total_records']:>6} records")
        print(f"  status            : {manifest['status']}")
        if not args.dry_run:
            print(f"  manifest          : {manifest.get('manifest_path', '')}")


if __name__ == "__main__":
    main()
