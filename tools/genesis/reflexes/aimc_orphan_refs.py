#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — AIMC Orphan Model Reference Scanner (aimc_orphan_refs).

Scans aiml_nodes for foundation-model nodes whose model_id is not present
in the FOUNDATION_MODELS catalog constant.  Reports orphans; in non-dry-run
mode writes a Kanban suggestion if any are found.

COOLDOWN_HOURS = 4 (enforced by Genesis daemon).
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

COOLDOWN_HOURS = 4

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------

try:
    from tools.aiml_canvas.db.init_db import get_connection as _aimc_conn
except ImportError:
    _aimc_conn = None  # type: ignore[assignment]

try:
    from tools.db.storage import get_connection as _main_conn
except ImportError:
    _main_conn = None  # type: ignore[assignment]

try:
    from tools.aiml_canvas.constants import FOUNDATION_MODELS
    _CATALOG_IDS: frozenset = frozenset(fm["id"] for fm in FOUNDATION_MODELS)
except ImportError:
    FOUNDATION_MODELS = []
    _CATALOG_IDS = frozenset()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL_NODE_TYPES = frozenset({"foundation-model", "model", "llm-node", "model-node"})


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _aimc_connection() -> Any:
    if _aimc_conn is not None:
        return _aimc_conn()
    if _main_conn is not None:
        return _main_conn()
    raise RuntimeError("No DB connection available for AIMC orphan scan")


def _scan_orphans() -> List[Dict[str, Any]]:
    """Return list of orphan refs: nodes with model_id not in FOUNDATION_MODELS catalog."""
    if not _CATALOG_IDS:
        return []

    orphans: List[Dict[str, Any]] = []
    conn = None
    try:
        conn = _aimc_connection()
        rows = conn.execute(
            "SELECT id, design_id, node_type, label, properties_json "
            "FROM aiml_nodes"
        ).fetchall()
        for row in rows:
            node_type = (row["node_type"] if hasattr(row, "__getitem__") else row[2]) or ""
            if node_type.lower() not in _MODEL_NODE_TYPES:
                continue
            props_raw = (row["properties_json"] if hasattr(row, "__getitem__") else row[4]) or "{}"
            try:
                props = json.loads(props_raw) if isinstance(props_raw, str) else props_raw
            except Exception:
                props = {}
            model_id = props.get("model_id", "")
            if model_id and model_id not in _CATALOG_IDS:
                orphans.append({
                    "node_id": row["id"] if hasattr(row, "__getitem__") else row[0],
                    "design_id": row["design_id"] if hasattr(row, "__getitem__") else row[1],
                    "node_type": node_type,
                    "label": row["label"] if hasattr(row, "__getitem__") else row[3],
                    "model_id": model_id,
                })
    except Exception as exc:
        print(f"  [aimc_orphan_refs] WARNING: scan failed: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return orphans


def _create_kanban_suggestion(orphans: List[Dict[str, Any]]) -> Optional[str]:
    """Insert a Kanban chore suggestion for orphaned model refs."""
    if _main_conn is None:
        return None
    task_id = f"aimc-orphan-{_uid()}"
    ids_str = ", ".join(o["model_id"] for o in orphans[:5])
    if len(orphans) > 5:
        ids_str += f", … (+{len(orphans) - 5} more)"
    title = f"[AIMC] Resolve {len(orphans)} orphaned model reference(s)"
    description = (
        f"Genesis aimc_orphan_refs reflex found {len(orphans)} foundation-model node(s) "
        f"referencing model IDs not in FOUNDATION_MODELS catalog: {ids_str}. "
        "Update node properties_json to use a valid catalog model_id."
    )
    conn = None
    try:
        conn = _main_conn()
        conn.execute(
            "INSERT INTO tasks "
            "(id, title, description, status, task_type, priority, source, scheduled_at, created_at) "
            "VALUES (?, ?, ?, 'suggested', 'chore', 'medium', 'aimc_orphan_refs', ?, ?)",
            (task_id, title, description, _utcnow_iso(), _utcnow_iso()),
        )
        conn.commit()
        print(f"  [aimc_orphan_refs] Kanban suggestion created: {task_id}")
        return task_id
    except Exception as exc:
        print(f"  [aimc_orphan_refs] WARNING: Kanban suggestion failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Reflex entry point
# ---------------------------------------------------------------------------

def run(payload: dict, ctx: Any = None) -> Dict[str, Any]:
    """Scan AIMC designs for orphan model references.

    payload keys (all optional):
        dry_run (bool): if True, report but do not write Kanban suggestion.

    Returns status='ok' on success, status='error' on unrecoverable failure.
    """
    run_id = f"aimc-orphan-{_uid()}"
    start_wall = time.time()
    dry_run = bool(payload.get("dry_run", False))

    print(f"[aimc_orphan_refs] run start  run_id={run_id}  dry_run={dry_run}")

    orphans = _scan_orphans()
    elapsed_ms = int((time.time() - start_wall) * 1000)

    print(f"  [aimc_orphan_refs] orphans_found={len(orphans)}  elapsed_ms={elapsed_ms}")

    suggestion_id: Optional[str] = None
    if orphans and not dry_run:
        suggestion_id = _create_kanban_suggestion(orphans)

    result: Dict[str, Any] = {
        "status": "ok",
        "run_id": run_id,
        "dry_run": dry_run,
        "orphans_found": len(orphans),
        "orphans": orphans,
        "suggestion_id": suggestion_id,
        "elapsed_ms": elapsed_ms,
        "catalog_size": len(_CATALOG_IDS),
        "timestamp": _utcnow_iso(),
    }
    print(f"[aimc_orphan_refs] run complete  status=ok  orphans={len(orphans)}")
    return result
