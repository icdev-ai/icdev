# CUI // SP-CTI
"""Container nodes for NDC — Docker containers as first-class topology nodes.

Provides validation and CRUD helpers for adding Docker containers to a network
design. Image whitelist enforcement is the primary supply-chain guardrail —
unapproved images fire a Supply Chain Boundary check; `privileged=True` and
`network_mode=host` trigger Boundary warnings.

Cross-platform: stdlib only (sqlite3, json, pathlib, uuid, datetime). No Flask.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.network.db.init_db import get_connection
from tools.network.constants import (
    CONTAINER_IMAGE_WHITELIST,
    CONTAINER_PROPERTIES_SCHEMA,
    NODE_TYPE_CONTAINER,
)

logger = get_logger("icdev.network.container_node")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_container_node(properties: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a container node spec.

    Returns
    -------
    dict with keys:
      - valid: bool — True if no errors (warnings still OK)
      - errors: list[str]
      - warnings: list[str]

    Checks
    ------
    - Required field `image` present.
    - `image` must be in `CONTAINER_IMAGE_WHITELIST` — if not, it's flagged as
      an error (this fires the Supply Chain Boundary check downstream).
    - `privileged=True` emits a warning (requires explicit approval).
    - `network_mode=host` emits a Boundary warning.
    - Type checks for each schema field.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(properties, dict):
        return {
            "valid": False,
            "errors": ["properties must be a dict"],
            "warnings": [],
        }

    # Required + whitelist check on image.
    image = properties.get("image")
    if not image:
        errors.append("image is required")
    elif not isinstance(image, str):
        errors.append("image must be a string")
    elif image not in CONTAINER_IMAGE_WHITELIST:
        errors.append(
            f"image '{image}' is not in approved whitelist "
            f"(Supply Chain Boundary violation)"
        )

    # Type checks for schema fields.
    type_map = {
        "str": str,
        "list": list,
        "dict": dict,
        "bool": bool,
    }
    for field, spec in CONTAINER_PROPERTIES_SCHEMA.items():
        if field == "image":
            continue  # handled above
        if field in properties and properties[field] is not None:
            expected = type_map.get(spec["type"])
            if expected is not None and not isinstance(properties[field], expected):
                errors.append(
                    f"{field} must be of type {spec['type']}, "
                    f"got {type(properties[field]).__name__}"
                )

    # Privileged → requires approval.
    if properties.get("privileged") is True:
        warnings.append(
            "privileged=True requires explicit approval "
            "(Boundary: elevated container privilege)"
        )

    # network_mode=host → Boundary warning.
    if properties.get("network_mode") == "host":
        warnings.append(
            "network_mode=host bypasses container network isolation "
            "(Boundary: host-network exposure)"
        )

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _sqlite_connection(db_path: str):
    """StorageConnection-wrapped SQLite DB at an explicit path (test override)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        from tools.db.storage import StorageConnection

        return StorageConnection(conn, "sqlite")
    except ImportError:
        return conn


def _connect(db_path: Optional[str] = None):
    # Default routes through the canvas get_connection() (honours
    # NC_STORAGE_BACKEND — PostgreSQL by default); an explicit db_path
    # override routes to the SQLite branch for tests.
    conn = _sqlite_connection(db_path) if db_path else get_connection()
    # Ensure a minimal table exists for container nodes (idempotent).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ndc_container_nodes (
            node_id     TEXT PRIMARY KEY,
            design_id   TEXT NOT NULL,
            name        TEXT NOT NULL,
            node_type   TEXT NOT NULL DEFAULT 'container',
            image       TEXT NOT NULL,
            properties  TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            UNIQUE (design_id, name)
        )
        """
    )
    conn.commit()
    return conn


def create_container_node(
    design_id: str,
    name: str,
    image: str,
    cmd: Optional[List[str]] = None,
    properties: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a container node to `design_id`.

    Returns dict with {node_id, name, image, validation}. If validation fails
    (`validation.valid == False`) the node is NOT persisted.
    """
    props: Dict[str, Any] = dict(properties or {})
    props["image"] = image
    if cmd is not None:
        props["cmd"] = cmd
    # Apply schema defaults for any missing optional fields.
    for field, spec in CONTAINER_PROPERTIES_SCHEMA.items():
        if field not in props and "default" in spec:
            props[field] = spec["default"]

    validation = validate_container_node(props)
    result: Dict[str, Any] = {
        "node_id": None,
        "name": name,
        "image": image,
        "validation": validation,
    }
    if not validation["valid"]:
        return result

    node_id = f"cn-{uuid.uuid4().hex[:12]}"
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO ndc_container_nodes
                (node_id, design_id, name, node_type, image, properties, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                node_id,
                design_id,
                name,
                NODE_TYPE_CONTAINER,
                image,
                json.dumps(props, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result["node_id"] = node_id
    logger.info("Created container node %s (%s) in design %s", node_id, image, design_id)
    return result


def list_container_nodes(
    design_id: str,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List all container nodes in a design."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT node_id, design_id, name, node_type, image, properties, created_at
              FROM ndc_container_nodes
             WHERE design_id = %s
             ORDER BY created_at ASC
            """,
            (design_id,),
        ).fetchall()
    finally:
        conn.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            props = json.loads(r["properties"]) if r["properties"] else {}
        except json.JSONDecodeError:
            props = {}
        out.append(
            {
                "node_id": r["node_id"],
                "design_id": r["design_id"],
                "name": r["name"],
                "node_type": r["node_type"],
                "image": r["image"],
                "properties": props,
                "created_at": r["created_at"],
            }
        )
    return out
