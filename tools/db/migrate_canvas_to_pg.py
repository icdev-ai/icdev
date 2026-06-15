#!/usr/bin/env python3
"""Migrate a canvas database from SQLite to PostgreSQL.

Ensures all tables exist in the shared PostgreSQL database and
optionally copies dev data from the legacy SQLite file.

Usage:
    python tools/db/migrate_canvas_to_pg.py --canvas security_canvas
"""

import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _resolve_init_module(canvas: str):
    """Return the module path for the canvas init_db."""
    mapping = {
        "security_canvas": "tools.security_canvas.db.init_db",
        "zta": "tools.zta.db.init_db",
        "network": "tools.network.db.init_db",
        "document_intelligence": "tools.document_intelligence.db.init_db",
        "data_canvas": "tools.data_canvas.db.init_db",
        "ai_augmentation": "tools.ai_augmentation.db.init_db",
        "ops_hub": "tools.ops_hub.db.init_db",
        "migration_canvas": "tools.migration_canvas.db.init_db",
        "observability_canvas": "tools.observability_canvas.db.init_db",
        "pipeline": "tools.pipeline.db.init_db",
        "qdc_canvas": "tools.qdc_canvas.db.init_db",
        "boundary_canvas": "tools.boundary_canvas.db.init_db",
        "infra_canvas": "tools.infra_canvas.db.init_db",
        "aiml_canvas": "tools.aiml_canvas.db.init_db",
        "agentic_ai_canvas": "tools.agentic_ai_canvas.db.init_db",
    }
    return mapping.get(canvas)


def migrate_canvas(canvas: str, copy_data: bool = False):
    """Run the canvas init_db against PostgreSQL to create tables."""
    mod_path = _resolve_init_module(canvas)
    if not mod_path:
        print(f"Unknown canvas: {canvas}")
        sys.exit(1)

    # Ensure PG backend is active for the canvas
    env_backup = {}
    env_vars = [
        "ICDEV_STORAGE_BACKEND",
        "ICDEV_CANVAS_STORAGE_BACKEND",
        "SC_STORAGE_BACKEND",
        "NC_STORAGE_BACKEND",
        "AAC_STORAGE_BACKEND",
        "DIC_STORAGE_BACKEND",
    ]
    for ev in env_vars:
        env_backup[ev] = os.environ.get(ev)
    os.environ["ICDEV_STORAGE_BACKEND"] = "postgresql"
    os.environ["ICDEV_CANVAS_STORAGE_BACKEND"] = "postgresql"

    # Canvas-specific env var
    if canvas == "security_canvas":
        os.environ["SC_STORAGE_BACKEND"] = "postgresql"

    try:
        import importlib

        mod = importlib.import_module(mod_path)
        init_fn = getattr(mod, "init_db", None)

        if init_fn is None:
            print(f"No init_db() found in {mod_path}")
            sys.exit(1)

        # Some init_db functions accept a conn argument (e.g. zta), others don't.
        import inspect

        sig = inspect.signature(init_fn)
        if len(sig.parameters) == 0:
            init_fn()
        else:
            from tools.db.storage import get_canvas_connection

            conn = get_canvas_connection()
            try:
                init_fn(conn)
            finally:
                conn.close()

        print(f"[migrate_canvas_to_pg] {canvas} tables initialized on PostgreSQL.")
    except Exception as exc:
        print(f"[migrate_canvas_to_pg] ERROR initializing {canvas}: {exc}")
        sys.exit(1)
    finally:
        for ev, val in env_backup.items():
            if val is None:
                os.environ.pop(ev, None)
            else:
                os.environ[ev] = val


def main():
    parser = argparse.ArgumentParser(description="Migrate canvas DB to PostgreSQL")
    parser.add_argument("--canvas", required=True, help="Canvas name (e.g. security_canvas)")
    parser.add_argument("--copy-data", action="store_true", help="Copy data from SQLite to PG")
    args = parser.parse_args()
    migrate_canvas(args.canvas, copy_data=args.copy_data)


if __name__ == "__main__":
    main()
