#!/usr/bin/env python3
# CUI // SP-CTI
"""NDC Design Snapshots — frozen restorable points with manifest + optional blob.

A snapshot is an immutable capture of a design's state:
  * Topology row (graph_json + metadata)
  * All nc_objects attached to the topology (device configs)
  * Lineage (origin, parent snapshots, lab_run_id if applicable)
  * SHA256 over canonical manifest JSON for integrity verification

Snapshots are stored in the NDC SQLite DB (table nc_design_snapshots).
Optionally, an external blob (e.g. .gns3project tarball) may be referenced
via blob_uri.

CLI:
    python tools/network/snapshots.py --design-id <id> --create --json
    python tools/network/snapshots.py --list --json
    python tools/network/snapshots.py --snap-id <id> --get --json
    python tools/network/snapshots.py --snap-id <id> --verify --json
    python tools/network/snapshots.py --snap-id <id> --restore --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.network.db.init_db import DB_PATH  # noqa: E402


def _connect() -> sqlite3.Connection:
    """Open a connection to the NDC DB via storage abstraction layer."""
    conn = get_connection(str(DB_PATH))
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(obj: Any) -> str:
    """Canonical JSON serialization for stable SHA256."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fetch_design(conn: sqlite3.Connection, design_id: str) -> Optional[dict]:
    """Fetch topology row keyed by design_id."""
    row = conn.execute(
        "SELECT id, name, description, graph_json, template_id, "
        "classification, created_at, updated_at FROM topologies WHERE id = %s",
        (design_id,),
    ).fetchone()
    return dict(row) if row else None


def _fetch_objects(conn: sqlite3.Connection, design_id: str) -> list:
    """Fetch all nc_objects (device configs) for this design."""
    rows = conn.execute(
        "SELECT id, topology_id, object_type, label, config_json, pos_x, pos_y "
        "FROM nc_objects WHERE topology_id = %s",
        (design_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _build_manifest(
    design: dict,
    objects: list,
    lab_run_id: Optional[str],
    description: str,
    created_by: str,
) -> dict:
    """Assemble the canonical manifest dict."""
    graph = {}
    try:
        graph = json.loads(design.get("graph_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        graph = {}

    device_configs = []
    for obj in objects:
        cfg = {}
        try:
            cfg = json.loads(obj.get("config_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            cfg = {}
        device_configs.append({
            "id": obj["id"],
            "object_type": obj["object_type"],
            "label": obj["label"],
            "config": cfg,
            "pos": {"x": obj["pos_x"], "y": obj["pos_y"]},
        })

    return {
        "manifest_version": "1.0",
        "design": {
            "id": design["id"],
            "name": design["name"],
            "description": design.get("description") or "",
            "template_id": design.get("template_id"),
            "classification": design.get("classification") or "CUI",
            "graph": graph,
        },
        "device_configs": device_configs,
        "lineage": {
            "origin_design_id": design["id"],
            "lab_run_id": lab_run_id,
            "snapshot_description": description,
            "created_by": created_by,
            "created_at": _now(),
        },
    }


def create_snapshot(
    design_id: str,
    description: str = "",
    lab_run_id: Optional[str] = None,
    blob_uri: Optional[str] = None,
    created_by: str = "",
) -> dict:
    """Freeze a design: capture topology + all device configs + lineage.

    Returns:
        {snap_id, manifest, sha256, size_bytes, created_at}
    """
    conn = _connect()
    try:
        design = _fetch_design(conn, design_id)
        if not design:
            raise ValueError(f"design not found: {design_id}")
        objects = _fetch_objects(conn, design_id)

        manifest = _build_manifest(design, objects, lab_run_id, description, created_by)
        manifest_text = _canonical_json(manifest)
        digest = _sha256(manifest_text)
        size_bytes = len(manifest_text.encode("utf-8"))
        snap_id = str(uuid.uuid4())
        created_at = _now()

        conn.execute(
            "INSERT INTO nc_design_snapshots "
            "(snap_id, design_id, lab_run_id, manifest_json, blob_uri, "
            " sha256, classification, created_by, created_at, description, size_bytes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                snap_id,
                design_id,
                lab_run_id,
                manifest_text,
                blob_uri,
                digest,
                design.get("classification") or "CUI",
                created_by,
                created_at,
                description,
                size_bytes,
            ),
        )
        conn.commit()
        return {
            "snap_id": snap_id,
            "manifest": manifest,
            "sha256": digest,
            "size_bytes": size_bytes,
            "created_at": created_at,
        }
    finally:
        conn.close()


def list_snapshots(design_id: Optional[str] = None, limit: int = 50) -> list:
    """List snapshots, optionally filtered by design."""
    conn = _connect()
    try:
        if design_id:
            rows = conn.execute(
                "SELECT snap_id, design_id, lab_run_id, sha256, classification, "
                "created_by, created_at, description, size_bytes, blob_uri "
                "FROM nc_design_snapshots WHERE design_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (design_id, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT snap_id, design_id, lab_run_id, sha256, classification, "
                "created_by, created_at, description, size_bytes, blob_uri "
                "FROM nc_design_snapshots "
                "ORDER BY created_at DESC LIMIT %s",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_snapshot(snap_id: str) -> dict:
    """Retrieve full manifest + metadata."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT snap_id, design_id, lab_run_id, manifest_json, blob_uri, "
            "sha256, classification, created_by, created_at, description, size_bytes "
            "FROM nc_design_snapshots WHERE snap_id = %s",
            (snap_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"snapshot not found: {snap_id}")
        out = dict(row)
        try:
            out["manifest"] = json.loads(out.pop("manifest_json"))
        except (json.JSONDecodeError, TypeError):
            out["manifest"] = {}
        return out
    finally:
        conn.close()


def verify_snapshot(snap_id: str) -> dict:
    """Verify manifest integrity via SHA256 check."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT snap_id, manifest_json, sha256 FROM nc_design_snapshots "
            "WHERE snap_id = %s",
            (snap_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"snapshot not found: {snap_id}")
        stored_hash = row["sha256"]
        recomputed = _sha256(row["manifest_json"])
        return {
            "snap_id": snap_id,
            "stored_sha256": stored_hash,
            "recomputed_sha256": recomputed,
            "valid": stored_hash == recomputed,
        }
    finally:
        conn.close()


def restore_snapshot(snap_id: str, target_design_id: Optional[str] = None) -> dict:
    """Restore snapshot: apply manifest to target design (or create new).

    Returns:
        {design_id, restored_from_snap_id, restored_at}
    """
    snap = get_snapshot(snap_id)
    manifest = snap.get("manifest") or {}
    design = manifest.get("design") or {}
    device_configs = manifest.get("device_configs") or []

    conn = _connect()
    try:
        # Determine target design id
        if target_design_id is None:
            target_design_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO topologies "
                "(id, name, description, graph_json, template_id, classification, "
                " created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    target_design_id,
                    (design.get("name") or "restored") + " (restored)",
                    design.get("description") or "",
                    json.dumps(design.get("graph") or {}),
                    design.get("template_id"),
                    design.get("classification") or "CUI",
                    _now(),
                    _now(),
                ),
            )
        else:
            existing = _fetch_design(conn, target_design_id)
            if existing:
                conn.execute(
                    "UPDATE topologies SET name = %s, description = %s, graph_json = %s, "
                    "template_id = %s, classification = %s, updated_at = %s WHERE id = %s",
                    (
                        design.get("name") or existing["name"],
                        design.get("description") or "",
                        json.dumps(design.get("graph") or {}),
                        design.get("template_id"),
                        design.get("classification") or "CUI",
                        _now(),
                        target_design_id,
                    ),
                )
                # Remove existing objects for clean restore
                conn.execute(
                    "DELETE FROM nc_objects WHERE topology_id = %s",
                    (target_design_id,),
                )
            else:
                conn.execute(
                    "INSERT INTO topologies "
                    "(id, name, description, graph_json, template_id, classification, "
                    " created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        target_design_id,
                        design.get("name") or "restored",
                        design.get("description") or "",
                        json.dumps(design.get("graph") or {}),
                        design.get("template_id"),
                        design.get("classification") or "CUI",
                        _now(),
                        _now(),
                    ),
                )

        # Re-materialize nc_objects
        for cfg in device_configs:
            new_obj_id = cfg.get("id") or str(uuid.uuid4())
            # If target uses a different design_id, keep object-level ids unique
            if target_design_id != (manifest.get("lineage") or {}).get("origin_design_id"):
                new_obj_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO nc_objects "
                "(id, topology_id, object_type, label, config_json, pos_x, pos_y) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    new_obj_id,
                    target_design_id,
                    cfg.get("object_type") or "",
                    cfg.get("label") or "",
                    json.dumps(cfg.get("config") or {}),
                    float((cfg.get("pos") or {}).get("x") or 0),
                    float((cfg.get("pos") or {}).get("y") or 0),
                ),
            )
        conn.commit()
        return {
            "design_id": target_design_id,
            "restored_from_snap_id": snap_id,
            "restored_at": _now(),
        }
    finally:
        conn.close()


def _cli() -> int:
    p = argparse.ArgumentParser(description="NDC Design Snapshots")
    p.add_argument("--design-id", help="Design (topology) id")
    p.add_argument("--snap-id", help="Snapshot id")
    p.add_argument("--description", default="", help="Snapshot description")
    p.add_argument("--lab-run-id", default=None, help="Associated lab run id")
    p.add_argument("--blob-uri", default=None, help="Optional blob URI")
    p.add_argument("--created-by", default="", help="User id")
    p.add_argument("--target-design-id", default=None, help="Target design id for restore")
    p.add_argument("--limit", type=int, default=50)

    p.add_argument("--create", action="store_true")
    p.add_argument("--list", action="store_true", dest="do_list")
    p.add_argument("--get", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--restore", action="store_true")
    p.add_argument("--json", action="store_true", default=True)

    args = p.parse_args()
    try:
        if args.create:
            if not args.design_id:
                raise SystemExit("--design-id required for --create")
            out = create_snapshot(
                args.design_id,
                description=args.description,
                lab_run_id=args.lab_run_id,
                blob_uri=args.blob_uri,
                created_by=args.created_by,
            )
        elif args.do_list:
            out = {"snapshots": list_snapshots(args.design_id, args.limit)}
        elif args.get:
            if not args.snap_id:
                raise SystemExit("--snap-id required for --get")
            out = get_snapshot(args.snap_id)
        elif args.verify:
            if not args.snap_id:
                raise SystemExit("--snap-id required for --verify")
            out = verify_snapshot(args.snap_id)
        elif args.restore:
            if not args.snap_id:
                raise SystemExit("--snap-id required for --restore")
            out = restore_snapshot(args.snap_id, args.target_design_id)
        else:
            p.print_help()
            return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps({"ok": True, "result": out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
