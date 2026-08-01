#!/usr/bin/env python3
# CUI // SP-CTI
"""Fine-tuning dataset manager — CRUD, versioning, JSONL export (D-FT-9).

Datasets are versioned collections of training examples. Examples are
APPEND-ONLY (D6). Each version is a frozen snapshot (content-hashed).

Usage:
    python tools/finetune/dataset_manager.py --create --name "proposal-v1" --purpose proposal_drafting --json
    python tools/finetune/dataset_manager.py --list --json
    python tools/finetune/dataset_manager.py --add-example --dataset-id "ds-xxx" --user-input "..." --expected-output "..." --json  # noqa: E501
    python tools/finetune/dataset_manager.py --export --dataset-id "ds-xxx" --output data/finetune/export.jsonl --json
    python tools/finetune/dataset_manager.py --stats --dataset-id "ds-xxx" --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.finetune.dataset_manager")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

VALID_PURPOSES = ("general", "proposal_drafting", "compliance_export", "code_generation", "custom")
VALID_SOURCES = ("manual", "rag_auto_generated", "document_extraction", "imported", "marketplace")
VALID_STATUSES = ("draft", "labeling", "ready", "archived")


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _audit(conn: sqlite3.Connection, project_id: str, event_type: str, details: str) -> None:
    """Append to audit trail."""
    try:
        conn.execute(
            "INSERT INTO audit_trail (project_id, event_type, details, actor, created_at) VALUES (%s, %s, %s, %s, %s)",
            (project_id, event_type, details, "finetune_dataset_manager", _now()),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


# ── Dataset CRUD ──────────────────────────────────────────────────────


def create_dataset(
    name: str,
    purpose: str = "general",
    description: str = "",
    base_model: str = "qwen3:latest",
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    created_by: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create a new dataset."""
    if purpose not in VALID_PURPOSES:
        return {"success": False, "error": f"Invalid purpose: {purpose}. Valid: {VALID_PURPOSES}"}

    ds_id = f"ds-{uuid.uuid4().hex[:12]}"
    now = _now()

    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO ft_datasets
               (id, name, description, purpose, base_model, version, example_count,
                classification, tenant_id, project_id, created_by, status, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, 1, 0, %s, %s, %s, %s, 'draft', %s, %s)""",
            (
                ds_id,
                name,
                description,
                purpose,
                base_model,
                classification,
                tenant_id,
                project_id,
                created_by,
                now,
                now,
            ),
        )
        _audit(
            conn,
            project_id,
            "finetune_dataset_created",
            json.dumps({"dataset_id": ds_id, "name": name, "purpose": purpose}),
        )
        conn.commit()
        return {"success": True, "dataset_id": ds_id, "name": name, "purpose": purpose}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def list_datasets(
    tenant_id: str = "",
    project_id: str = "",
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """List datasets with optional filters."""
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM ft_datasets WHERE 1=1"
        params: List[Any] = []
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC"

        rows = conn.execute(query, params).fetchall()
        datasets = [dict(r) for r in rows]
        return {"success": True, "datasets": datasets, "count": len(datasets)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_dataset(dataset_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get a single dataset by ID."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM ft_datasets WHERE id = %s", (dataset_id,)).fetchone()
        if not row:
            return {"success": False, "error": f"Dataset not found: {dataset_id}"}
        return {"success": True, "dataset": dict(row)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def update_dataset_status(
    dataset_id: str,
    status: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Update dataset status."""
    if status not in VALID_STATUSES:
        return {"success": False, "error": f"Invalid status: {status}. Valid: {VALID_STATUSES}"}

    conn = _get_db(db_path)
    try:
        conn.execute(
            "UPDATE ft_datasets SET status = %s, updated_at = %s WHERE id = %s",
            (status, _now(), dataset_id),
        )
        conn.commit()
        return {"success": True, "dataset_id": dataset_id, "status": status}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ── Example Management (APPEND-ONLY) ─────────────────────────────────


def add_example(
    dataset_id: str,
    user_input: str,
    expected_output: str,
    system_prompt: str = "",
    source: str = "manual",
    source_chunk_id: str = "",
    source_document_id: str = "",
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Add a training example to a dataset (append-only, D6)."""
    if source not in VALID_SOURCES:
        return {"success": False, "error": f"Invalid source: {source}. Valid: {VALID_SOURCES}"}

    content = f"{system_prompt}|{user_input}|{expected_output}"
    chash = _content_hash(content)

    conn = _get_db(db_path)
    try:
        # Dedup check (D-FT-9)
        existing = conn.execute(
            "SELECT id FROM ft_dataset_examples WHERE dataset_id = %s AND content_hash = %s",
            (dataset_id, chash),
        ).fetchone()
        if existing:
            return {"success": False, "error": "Duplicate example (content hash match)", "duplicate_id": existing["id"]}

        conn.execute(
            """INSERT INTO ft_dataset_examples
               (dataset_id, system_prompt, user_input, expected_output, source,
                source_chunk_id, source_document_id, content_hash, classification,
                tenant_id, project_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                dataset_id,
                system_prompt,
                user_input,
                expected_output,
                source,
                source_chunk_id,
                source_document_id,
                chash,
                classification,
                tenant_id,
                project_id,
                _now(),
            ),
        )

        # Update example count
        count = conn.execute(
            "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = %s",
            (dataset_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE ft_datasets SET example_count = %s, updated_at = %s WHERE id = %s",
            (count, _now(), dataset_id),
        )
        conn.commit()
        return {"success": True, "dataset_id": dataset_id, "content_hash": chash, "example_count": count}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def add_examples_batch(
    dataset_id: str,
    examples: List[Dict[str, str]],
    source: str = "manual",
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Add multiple examples in a batch."""
    added = 0
    skipped = 0
    errors = []

    for ex in examples:
        result = add_example(
            dataset_id=dataset_id,
            user_input=ex.get("user_input", ""),
            expected_output=ex.get("expected_output", ""),
            system_prompt=ex.get("system_prompt", ""),
            source=source,
            source_chunk_id=ex.get("source_chunk_id", ""),
            source_document_id=ex.get("source_document_id", ""),
            classification=classification,
            tenant_id=tenant_id,
            project_id=project_id,
            db_path=db_path,
        )
        if result.get("success"):
            added += 1
        elif "Duplicate" in result.get("error", ""):
            skipped += 1
        else:
            errors.append(result.get("error", "Unknown error"))

    return {
        "success": True,
        "added": added,
        "skipped": skipped,
        "errors": errors,
        "total_attempted": len(examples),
    }


def list_examples(
    dataset_id: str,
    approved_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """List examples in a dataset."""
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM ft_dataset_examples WHERE dataset_id = ?"
        params: List[Any] = [dataset_id]
        if approved_only:
            query += " AND approved = 1"
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        examples = [dict(r) for r in rows]

        total = conn.execute(
            "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = %s",
            (dataset_id,),
        ).fetchone()[0]

        return {"success": True, "examples": examples, "count": len(examples), "total": total}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_dataset_stats(dataset_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get dataset statistics."""
    conn = _get_db(db_path)
    try:
        ds = conn.execute("SELECT * FROM ft_datasets WHERE id = %s", (dataset_id,)).fetchone()
        if not ds:
            return {"success": False, "error": f"Dataset not found: {dataset_id}"}

        total = conn.execute(
            "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = %s",
            (dataset_id,),
        ).fetchone()[0]

        approved = conn.execute(
            "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = %s AND approved = 1",
            (dataset_id,),
        ).fetchone()[0]

        by_source = {}
        for row in conn.execute(
            "SELECT source, COUNT(*) as cnt FROM ft_dataset_examples WHERE dataset_id = %s GROUP BY source",
            (dataset_id,),
        ).fetchall():
            by_source[row["source"]] = row["cnt"]

        avg_scores = conn.execute(
            """SELECT AVG(quality_score) as avg_quality,
                      AVG(compliance_score) as avg_compliance,
                      AVG(relevance_score) as avg_relevance
               FROM ft_dataset_examples WHERE dataset_id = %s AND approved = 1""",
            (dataset_id,),
        ).fetchone()

        return {
            "success": True,
            "dataset_id": dataset_id,
            "name": ds["name"],
            "status": ds["status"],
            "total_examples": total,
            "approved_examples": approved,
            "pending_examples": total - approved,
            "approval_rate": round(approved / total * 100, 1) if total > 0 else 0.0,
            "by_source": by_source,
            "avg_quality": round(avg_scores["avg_quality"] or 0, 2),
            "avg_compliance": round(avg_scores["avg_compliance"] or 0, 2),
            "avg_relevance": round(avg_scores["avg_relevance"] or 0, 2),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ── JSONL Export ──────────────────────────────────────────────────────


def export_jsonl(
    dataset_id: str,
    output_path: str,
    approved_only: bool = True,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Export dataset to JSONL format for training.

    Output format (chat/instruct):
        {"messages": [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."}
        ]}
    """
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM ft_dataset_examples WHERE dataset_id = ?"
        params: List[Any] = [dataset_id]
        if approved_only:
            query += " AND approved = 1"
        query += " ORDER BY id ASC"

        rows = conn.execute(query, params).fetchall()
        if not rows:
            return {
                "success": False,
                "error": "No examples to export (none approved)" if approved_only else "Dataset is empty",
            }

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        exported = 0
        with open(out, "w", encoding="utf-8") as f:
            for row in rows:
                messages = []
                if row["system_prompt"]:
                    messages.append({"role": "system", "content": row["system_prompt"]})
                messages.append({"role": "user", "content": row["user_input"]})
                messages.append({"role": "assistant", "content": row["expected_output"]})
                f.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")
                exported += 1

        # Update dataset content hash
        all_hashes = [row["content_hash"] for row in rows]
        dataset_hash = _content_hash("|".join(all_hashes))
        conn.execute(
            "UPDATE ft_datasets SET content_hash = %s, updated_at = %s WHERE id = %s",
            (dataset_hash, _now(), dataset_id),
        )
        conn.commit()

        return {
            "success": True,
            "output_path": str(out),
            "exported_examples": exported,
            "content_hash": dataset_hash,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning dataset manager")
    parser.add_argument("--create", action="store_true", help="Create a new dataset")
    parser.add_argument("--list", action="store_true", help="List datasets")
    parser.add_argument("--get", action="store_true", help="Get dataset details")
    parser.add_argument("--add-example", action="store_true", help="Add a training example")
    parser.add_argument("--list-examples", action="store_true", help="List examples")
    parser.add_argument("--export", action="store_true", help="Export to JSONL")
    parser.add_argument("--stats", action="store_true", help="Dataset statistics")
    parser.add_argument("--update-status", type=str, help="Update dataset status")

    parser.add_argument("--dataset-id", type=str, default="")
    parser.add_argument("--name", type=str, default="")
    parser.add_argument("--purpose", type=str, default="general")
    parser.add_argument("--description", type=str, default="")
    parser.add_argument("--base-model", type=str, default="qwen3:latest")
    parser.add_argument("--user-input", type=str, default="")
    parser.add_argument("--expected-output", type=str, default="")
    parser.add_argument("--system-prompt", type=str, default="")
    parser.add_argument("--source", type=str, default="manual")
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument("--classification", type=str, default="CUI")
    parser.add_argument("--tenant-id", type=str, default="")
    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)

    args = parser.parse_args()

    result: Dict[str, Any] = {"success": False, "error": "No action specified"}

    if args.create:
        result = create_dataset(
            name=args.name,
            purpose=args.purpose,
            description=args.description,
            base_model=args.base_model,
            classification=args.classification,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    elif args.list:
        result = list_datasets(
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    elif args.get:
        result = get_dataset(args.dataset_id)
    elif args.add_example:
        result = add_example(
            dataset_id=args.dataset_id,
            user_input=args.user_input,
            expected_output=args.expected_output,
            system_prompt=args.system_prompt,
            source=args.source,
            classification=args.classification,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    elif args.list_examples:
        result = list_examples(
            dataset_id=args.dataset_id,
            approved_only=args.approved_only,
            limit=args.limit,
            offset=args.offset,
        )
    elif args.export:
        result = export_jsonl(
            dataset_id=args.dataset_id,
            output_path=args.output,
            approved_only=args.approved_only,
        )
    elif args.stats:
        result = get_dataset_stats(args.dataset_id)
    elif args.update_status:
        result = update_dataset_status(args.dataset_id, args.update_status)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
