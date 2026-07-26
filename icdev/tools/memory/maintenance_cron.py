#!/usr/bin/env python3
# CUI // SP-CTI
"""Memory maintenance orchestrator — flush, embed, backup, prune (D179-D182).

Runs a 4-step maintenance pipeline:
  1. Flush auto-capture buffer to memory_entries
  2. Generate embeddings for unembedded entries (D72 compliant)
  3. Prune stale low-importance entries
  4. Backup memory.db

Usage:
    python tools/memory/maintenance_cron.py --all --json
    python tools/memory/maintenance_cron.py --flush-buffer --json
    python tools/memory/maintenance_cron.py --embed-unembedded --json
    python tools/memory/maintenance_cron.py --prune-stale --days 180 --json
    python tools/memory/maintenance_cron.py --backup --json
"""

import argparse
import json
import struct
import sys
import time
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(BASE_DIR))


def _load_config():
    """Load maintenance settings from memory_config.yaml."""
    config_path = BASE_DIR / "args" / "memory_config.yaml"
    defaults = {
        "prune_stale_days": 180,
        "prune_min_importance": 3,
        "prune_types": ["event", "thinking"],
        "embed_batch_size": 20,
        "backup_before_prune": True,
    }
    try:
        import yaml

        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            maint = data.get("maintenance", {})
            for key in defaults:
                if key in maint:
                    defaults[key] = maint[key]
    except (ImportError, Exception):
        pass
    return defaults


def flush_buffer(db_path=None):
    """Flush memory buffer to memory_entries."""
    try:
        from tools.memory.auto_capture import flush_buffer as _flush

        return _flush(db_path=db_path)
    except (ImportError, Exception) as exc:
        return {"flushed": 0, "error": str(exc)}


def embed_unembedded(db_path=None, limit=None):
    """Generate embeddings for entries missing them (D72 compliant).

    ``limit`` bounds how many unembedded entries one call processes, so a large
    backlog is caught up over several scheduled runs instead of stalling a single
    reflex cycle. ``None`` processes all missing (the CLI/backfill default).
    """
    conn = get_connection()
    c = conn.cursor()
    # LIMIT takes an internal int; inline it so the query needs no placeholder and
    # runs on both backends.
    sql = "SELECT id, content FROM memory_entries WHERE embedding IS NULL"
    if limit is not None:
        sql += f" ORDER BY created_at DESC LIMIT {int(limit)}"
    c.execute(sql)
    rows = c.fetchall()

    if not rows:
        conn.close()
        return {"embedded": 0, "status": "all_embedded"}

    # Use LLM provider abstraction (D72)
    provider = None
    provider_name = "none"
    try:
        from tools.llm import get_embedding_provider

        provider = get_embedding_provider()
        provider_name = "llm_provider"
    except Exception:
        pass

    # Fallback to direct OpenAI
    if provider is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(BASE_DIR / ".env")
        except ImportError:
            pass

        import os

        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            try:
                import openai

                provider = openai.OpenAI(api_key=api_key)
                provider_name = "openai_direct"
            except ImportError:
                pass

    if provider is None:
        conn.close()
        return {"embedded": 0, "status": "no_provider", "total_unembedded": len(rows)}

    cfg = _load_config()
    batch_size = cfg.get("embed_batch_size", 20)
    embedded = 0
    errors = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [row[1] for row in batch]
        ids = [row[0] for row in batch]

        try:
            if hasattr(provider, "embed"):
                # LLM provider interface
                for j, text in enumerate(texts):
                    emb = provider.embed(text)
                    blob = struct.pack(f"{len(emb)}f", *emb)
                    c.execute(
                        "UPDATE memory_entries SET embedding = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (blob, ids[j]),
                    )
                    embedded += 1
            else:
                # Direct OpenAI client
                response = provider.embeddings.create(input=texts, model="text-embedding-3-small")
                for j, emb_data in enumerate(response.data):
                    blob = struct.pack(f"{len(emb_data.embedding)}f", *emb_data.embedding)
                    c.execute(
                        "UPDATE memory_entries SET embedding = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (blob, ids[j]),
                    )
                    embedded += 1
            conn.commit()
        except Exception:
            errors += 1

    conn.close()
    return {
        "embedded": embedded,
        "errors": errors,
        "total_unembedded": len(rows),
        "provider": provider_name,
    }


def prune_stale(days=None, db_path=None):
    """Remove low-importance entries older than threshold.

    Only prunes entries with importance <= configured threshold and
    type in configured prune_types list.
    """
    cfg = _load_config()
    prune_days = days or cfg.get("prune_stale_days", 180)
    min_importance = cfg.get("prune_min_importance", 3)
    prune_types = cfg.get("prune_types", ["event", "thinking"])

    conn = get_connection()
    c = conn.cursor()

    placeholders = ",".join(["%s"] * len(prune_types))
    c.execute(
        f"""DELETE FROM memory_entries
           WHERE importance <= %s
             AND type IN ({placeholders})
             AND created_at < datetime('now', %s || ' days')""",  # nosec B608 -- table/column names are internal constants, not user input
        [min_importance] + prune_types + [str(-prune_days)],
    )
    pruned = c.rowcount
    conn.commit()
    conn.close()
    return {
        "pruned": pruned,
        "threshold_days": prune_days,
        "min_importance": min_importance,
        "prune_types": prune_types,
    }


def backup_memory(db_path=None):
    """Backup the main DB (memory tables consolidated into icdev.db)."""
    try:
        from tools.db.backup_manager import BackupManager

        mgr = BackupManager()
        icdev_db = BASE_DIR / "data" / "icdev.db"
        result = mgr.backup_sqlite(db_path or icdev_db)
        return {"status": "ok", "backup_path": str(result.get("backup_path", ""))}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def run_all(db_path=None, prune_days=None):
    """Run full maintenance pipeline: flush → embed → prune → backup."""
    cfg = _load_config()
    results = {
        "classification": "CUI // SP-CTI",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    start = time.monotonic()

    # Step 1: Flush buffer
    results["flush"] = flush_buffer(db_path)

    # Step 2: Embed unembedded entries
    results["embed"] = embed_unembedded(db_path)

    # Step 3: Backup before prune (if configured)
    if cfg.get("backup_before_prune", True):
        results["backup"] = backup_memory(db_path)

    # Step 4: Prune stale entries
    results["prune"] = prune_stale(prune_days, db_path)

    # Step 5: Backup after prune (if not done before)
    if not cfg.get("backup_before_prune", True):
        results["backup"] = backup_memory(db_path)

    results["duration_ms"] = int((time.monotonic() - start) * 1000)
    return results


def main():
    parser = argparse.ArgumentParser(description="Memory maintenance orchestrator (D179-D182)")
    parser.add_argument("--all", action="store_true", help="Run full maintenance pipeline")
    parser.add_argument("--flush-buffer", action="store_true", help="Flush auto-capture buffer only")
    parser.add_argument("--embed-unembedded", action="store_true", help="Generate embeddings for unembedded entries")
    parser.add_argument("--prune-stale", action="store_true", help="Prune stale low-importance entries")
    parser.add_argument("--backup", action="store_true", help="Backup memory.db")
    parser.add_argument("--days", type=int, help="Override prune threshold days")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.all:
        result = run_all(prune_days=args.days)
    elif args.flush_buffer:
        result = flush_buffer()
    elif args.embed_unembedded:
        result = embed_unembedded()
    elif args.prune_stale:
        result = prune_stale(days=args.days)
    elif args.backup:
        result = backup_memory()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.all:
            fl = result.get("flush", {})
            em = result.get("embed", {})
            pr = result.get("prune", {})
            bk = result.get("backup", {})
            print(f"Maintenance complete ({result.get('duration_ms', 0)}ms):")
            print(f"  Flush:  {fl.get('flushed', 0)} entries flushed")
            print(f"  Embed:  {em.get('embedded', 0)} entries embedded (provider: {em.get('provider', 'n/a')})")
            print(f"  Prune:  {pr.get('pruned', 0)} entries pruned (>{pr.get('threshold_days', '?')} days)")
            print(f"  Backup: {bk.get('status', 'skipped')}")
        else:
            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
