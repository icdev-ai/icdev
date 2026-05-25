"""GovCon Demo Ingest Orchestrator.

End-to-end pipeline for setting up the GovCon Proposal demo:
  1. Seed DB with 50 synthetic proposals (5 archetypes × 10)
  2. RAG-index all proposal sections (govcon_proposal_section source type)
  3. Bridge RAG chunks to Knowledge Graph
  4. Generate fine-tuning Q&A pairs (~450 pairs)
  5. (Optional) Trigger Bedrock or local QLoRA fine-tuning

No real data from D:\\Work\\Proposals is read or used at any step.

Usage:
  python tools/govcon/demo_ingest.py --dry-run
  python tools/govcon/demo_ingest.py --run --json
  python tools/govcon/demo_ingest.py --run --train bedrock --json
  python tools/govcon/demo_ingest.py --run --train local --model-path /path/to/model --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def _step_seed(dry_run: bool) -> dict:
    from tools.db.seeds.seed_govcon_proposals import seed
    return seed(dry_run=dry_run)


def _step_rag_index(dry_run: bool) -> dict:
    if dry_run:
        return {"dry_run": True, "action": "rag_index govcon_proposal_section"}
    try:
        from tools.rag.ingestion_manager import ingest_source
        result = ingest_source("govcon_proposal_section")
        return result if isinstance(result, dict) else {"status": "ok", "result": str(result)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300]}


def _step_kg_bridge(dry_run: bool) -> dict:
    if dry_run:
        return {"dry_run": True, "action": "kg_bridge govcon_proposal_section"}
    try:
        from tools.rag.rag_to_kg_ingester import run_backfill
        result = run_backfill()
        return result if isinstance(result, dict) else {"status": "ok", "result": str(result)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300]}


def _step_generate_ft_pairs(dry_run: bool) -> dict:
    """Generate fine-tuning pairs from proposal sections into ft_dataset_examples."""
    if dry_run:
        return {"dry_run": True, "action": "generate ft_dataset_examples for govcon-proposals"}

    try:
        import hashlib
        import uuid
        from datetime import datetime, timezone
        from tools.db.storage import get_connection
        from tools.finetune.govcon_pair_generator import generate_govcon_pairs

        conn = get_connection()
        now = datetime.now(timezone.utc).isoformat()

        # Load proposal sections via cascade from synthetic_demo opportunities
        sections = conn.execute(
            """
            SELECT id, title, description, notes, opportunity_id
            FROM proposal_sections
            WHERE opportunity_id IN (
                SELECT id FROM proposal_opportunities WHERE created_by = 'synthetic_demo'
            )
            ORDER BY created_at ASC
            """
        ).fetchall()

        if not sections:
            return {"status": "no_sections", "pairs_inserted": 0}

        # Get or create the govcon-proposals dataset
        ds_row = conn.execute(
            "SELECT id FROM ft_datasets WHERE name = ? LIMIT 1",
            ("govcon-proposals",),
        ).fetchone()

        if ds_row:
            dataset_id = ds_row[0]
        else:
            dataset_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO ft_datasets
                    (id, name, description, purpose, example_count, version,
                     status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    dataset_id, "govcon-proposals",
                    "Synthetic GovCon proposal Q&A pairs for fine-tuning",
                    "proposal_drafting", 0, 1, "labeling", now, now,
                ),
            )

        # Clear stale examples (idempotent re-run)
        conn.execute(
            "DELETE FROM ft_dataset_examples WHERE dataset_id = ?",
            (dataset_id,),
        )

        inserted = 0
        for sec in sections:
            sec_dict = {
                "id": sec[0], "title": sec[1], "description": sec[2],
                "notes": sec[3], "opportunity_id": sec[4],
            }
            pairs = generate_govcon_pairs(sec_dict)
            for pair in pairs:
                content_hash = hashlib.sha256(
                    (pair["user_input"] + pair["expected_output"]).encode("utf-8")
                ).hexdigest()
                conn.execute(
                    """
                    INSERT INTO ft_dataset_examples
                        (dataset_id, system_prompt, user_input, expected_output,
                         source, source_chunk_id, content_hash, classification, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        dataset_id, pair["system_prompt"], pair["user_input"],
                        pair["expected_output"], "rag_auto_generated",
                        str(sec[0]), content_hash, "CUI", now,
                    ),
                )
                inserted += 1

        # Update dataset count and status
        conn.execute(
            "UPDATE ft_datasets SET example_count = ?, status = ?, updated_at = ? WHERE id = ?",
            (inserted, "ready", now, dataset_id),
        )

        conn.commit()
        return {"status": "ok", "sections_processed": len(sections), "pairs_inserted": inserted}

    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _step_train_bedrock(s3_bucket: str, base_model: str) -> dict:
    from tools.finetune.bedrock_ft_pipeline import run as bedrock_run
    return bedrock_run(
        dataset_name="govcon-proposals",
        base_model=base_model,
        s3_bucket=s3_bucket,
    )


def _step_train_local(model_path: str, output_dir: str) -> dict:
    from tools.finetune.govcon_ft_pipeline import run as local_run
    return local_run(
        dataset_name="govcon-proposals",
        model_path=model_path,
        output_dir=output_dir,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(
    dry_run: bool = False,
    train: str = "",          # "" | "bedrock" | "local"
    s3_bucket: str = "",
    bedrock_model: str = "amazon.nova-lite-v1:0",
    model_path: str = "",
    adapter_output: str = "models/govcon-adapter",
) -> dict:
    """Run the full demo ingest pipeline."""
    results = {}
    t0 = time.time()

    print("\n=== GovCon Demo Ingest Pipeline ===")

    # Step 1: Seed database
    print("\n[1/4] Seeding synthetic proposals...")
    results["seed"] = _step_seed(dry_run)
    if not dry_run:
        ins = results["seed"].get("inserted", {})
        print(f"      Inserted: {ins.get('proposals', 0)} proposals, "
              f"{ins.get('volumes', 0)} volumes, {ins.get('sections', 0)} sections")

    # Step 2: RAG indexing
    print("\n[2/4] RAG indexing govcon_proposal_section...")
    results["rag_index"] = _step_rag_index(dry_run)
    if not dry_run:
        print(f"      RAG index: {results['rag_index'].get('status', 'done')}")

    # Step 3: KG bridge
    print("\n[3/4] Bridging RAG chunks to Knowledge Graph...")
    results["kg_bridge"] = _step_kg_bridge(dry_run)
    if not dry_run:
        print(f"      KG bridge: {results['kg_bridge'].get('status', 'done')}")

    # Step 4: Generate fine-tuning pairs
    print("\n[4/4] Generating fine-tuning Q&A pairs...")
    results["ft_pairs"] = _step_generate_ft_pairs(dry_run)
    if not dry_run:
        pairs = results["ft_pairs"].get("pairs_inserted", 0)
        print(f"      Fine-tuning pairs: {pairs} inserted into govcon-proposals dataset")

    # Optional: Fine-tuning
    if train and not dry_run:
        if train == "bedrock":
            print(f"\n[5/5] Triggering Bedrock fine-tuning ({bedrock_model})...")
            results["train"] = _step_train_bedrock(s3_bucket, bedrock_model)
        elif train == "local":
            print(f"\n[5/5] Running local QLoRA fine-tuning...")
            results["train"] = _step_train_local(model_path, adapter_output)
        print(f"      Training: {results['train'].get('status', 'done')}")

    elapsed = round(time.time() - t0, 1)
    results["elapsed_seconds"] = elapsed
    results["timestamp"] = datetime.now(timezone.utc).isoformat()

    if dry_run:
        print("\n[dry-run] No changes made. Run with --run to execute.")
    else:
        print(f"\nPipeline complete in {elapsed}s")
        print("Navigate to http://localhost:5050/govcon/pipeline to view the demo data.")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="GovCon demo ingest orchestrator")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="Execute the pipeline")
    mode.add_argument("--dry-run", action="store_true", help="Preview without DB writes")

    parser.add_argument("--train", choices=["bedrock", "local"], default="",
                        help="Optionally trigger fine-tuning after data ingestion")
    parser.add_argument("--s3-bucket", default="",
                        help="S3 bucket for Bedrock training data (Track A)")
    parser.add_argument("--bedrock-model", default="amazon.nova-lite-v1:0")
    parser.add_argument("--model-path", default="",
                        help="Local model weights path (Track B)")
    parser.add_argument("--adapter-output", default="models/govcon-adapter")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    result = run(
        dry_run=args.dry_run,
        train=args.train if args.run else "",
        s3_bucket=args.s3_bucket,
        bedrock_model=args.bedrock_model,
        model_path=args.model_path,
        adapter_output=args.adapter_output,
    )

    if args.as_json:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _main()
