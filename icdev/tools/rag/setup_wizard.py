#!/usr/bin/env python3
# CUI // SP-CTI
"""tools/rag/setup_wizard.py — Interactive greenfield/brownfield RAG+KG installer.

Entry point: run_setup(interactive=True, mode=None, conn=None)

CLI:
    python tools/rag/setup_wizard.py --interactive
    python tools/rag/setup_wizard.py --mode greenfield --json
    python tools/rag/setup_wizard.py --mode brownfield --no-backfill --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
RAG_CONFIG_PATH = BASE_DIR / "args" / "rag_config.yaml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_llm() -> bool:
    try:
        from tools.llm.router import LLMRouter
        return LLMRouter().has_any_llm()
    except Exception:
        return False


def _get_conn():
    try:
        from tools.db.storage import get_connection
        return get_connection()
    except Exception:
        return None


def _count_rag_chunks(conn) -> int:
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rag_chunks")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _list_sources() -> List[str]:
    try:
        from tools.rag.source_registry import get_all_sources
        return get_all_sources()
    except Exception:
        return []


def _write_enabled_sources(sources: List[str]) -> None:
    try:
        import yaml  # type: ignore
    except ImportError:
        # Fallback: write raw YAML append
        with open(RAG_CONFIG_PATH, "a", encoding="utf-8") as fh:
            fh.write("\nenabled_sources:\n")
            for s in sources:
                fh.write(f"  - {s}\n")
        return

    with open(RAG_CONFIG_PATH, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    config["enabled_sources"] = sources

    with open(RAG_CONFIG_PATH, "w", encoding="utf-8") as fh:
        yaml.dump(config, fh, default_flow_style=False, allow_unicode=True)


def _initialize_default_graph(conn) -> Dict[str, Any]:
    try:
        from tools.knowledge_graph.kg_init import initialize_default_graph
        return initialize_default_graph(conn)
    except ImportError:
        return {"status": "skipped", "reason": "kg_init module not found"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


def _run_backfill() -> Dict[str, Any]:
    try:
        from tools.rag.rag_to_kg_ingester import run_backfill
        return run_backfill(as_json=False)
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


def _run_disambiguation_sweep() -> Dict[str, Any]:
    try:
        from tools.knowledge_graph.disambiguator import find_duplicates
        return find_duplicates()
    except ImportError:
        try:
            # Fallback: check rag_to_kg_ingester for any sweep function
            from tools.rag.rag_to_kg_ingester import run_disambiguation_sweep  # type: ignore
            return run_disambiguation_sweep()
        except (ImportError, AttributeError):
            return {"status": "skipped", "reason": "disambiguation module not found"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


def _prompt(question: str, default: str = "") -> str:
    try:
        answer = input(question).strip()
        return answer if answer else default
    except (EOFError, KeyboardInterrupt):
        return default


# ---------------------------------------------------------------------------
# Core setup logic
# ---------------------------------------------------------------------------

def run_setup(
    interactive: bool = True,
    mode: Optional[str] = None,
    conn=None,
    no_backfill: bool = False,
    no_disambiguation: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run greenfield or brownfield RAG+KG setup.

    Args:
        interactive: Prompt user for choices when True.
        mode: 'greenfield' or 'brownfield'. Required when interactive=False.
        conn: Optional DB connection. Opens one internally if None.
        no_backfill: Skip KG backfill in brownfield mode (non-interactive).
        no_disambiguation: Skip disambiguation sweep in brownfield mode.
        dry_run: Validate paths without writing to DB or config files.

    Returns:
        Dict with keys: mode, status, and step results.
    """
    result: Dict[str, Any] = {"mode": mode, "status": "ok", "steps": []}
    if dry_run:
        result["dry_run"] = True

    if not interactive and mode not in ("greenfield", "brownfield"):
        raise ValueError(
            "mode must be 'greenfield' or 'brownfield' when interactive=False"
        )

    _conn = conn or _get_conn()

    # ------------------------------------------------------------------
    # Step 1 — Detect or ask for mode
    # ------------------------------------------------------------------
    count = _count_rag_chunks(_conn) if _conn else 0

    if mode is None:
        if count == 0:
            suggestion = "greenfield"
            print("No existing RAG chunks detected.")
        else:
            suggestion = "brownfield"
            print(f"Detected {count:,} existing RAG chunks.")

        if interactive:
            answer = _prompt(
                f"Proceed in {suggestion} mode? [Y/n]: ", default="y"
            ).lower()
            if answer not in ("y", "yes", ""):
                flip = "brownfield" if suggestion == "greenfield" else "greenfield"
                answer2 = _prompt(f"Use {flip} mode instead? [Y/n]: ", default="y").lower()
                mode = flip if answer2 in ("y", "yes", "") else suggestion
            else:
                mode = suggestion
        else:
            mode = suggestion

    result["mode"] = mode
    result["rag_chunks_count"] = count

    # ------------------------------------------------------------------
    # Step 2 — Greenfield path
    # ------------------------------------------------------------------
    if mode == "greenfield":
        print("Initializing RAG pipeline from scratch.")
        result["steps"].append("init_greenfield")

        all_sources = _list_sources()
        if all_sources:
            print(f"\nAvailable source types ({len(all_sources)}):")
            for i, s in enumerate(all_sources, 1):
                print(f"  {i:>2}. {s}")
        else:
            print("(No source types registered — source_registry may be empty.)")

        enabled: List[str] = all_sources
        if interactive and all_sources:
            answer = _prompt(
                "\nEnable all sources? [Y/n] or enter comma-separated names: ",
                default="y",
            )
            if answer.lower() not in ("y", "yes", ""):
                custom = [s.strip() for s in answer.split(",") if s.strip()]
                valid = [s for s in custom if s in all_sources]
                invalid = [s for s in custom if s not in all_sources]
                if invalid:
                    print(f"Warning: unknown sources ignored: {invalid}")
                enabled = valid if valid else all_sources

        if not dry_run:
            _write_enabled_sources(enabled)
            print(f"Written {len(enabled)} source(s) to {RAG_CONFIG_PATH.name}.")
        else:
            print(f"[dry-run] Would write {len(enabled)} source(s) to {RAG_CONFIG_PATH.name}.")
        result["enabled_sources"] = enabled

        if not dry_run:
            kg_result = _initialize_default_graph(_conn)
        else:
            kg_result = {"status": "skipped", "reason": "dry-run mode"}
        result["kg_init"] = kg_result
        if kg_result.get("status") == "skipped":
            print("KG init skipped (kg_init module not available).")
        elif kg_result.get("status") == "error":
            print(f"KG init warning: {kg_result.get('reason')}")
        else:
            print("Default KG graph initialized.")

        print(
            "\nGreenfield setup complete."
            " Run ingestion_manager.py --ingest-all to populate."
        )
        result["steps"].append("greenfield_complete")
        result["status"] = "complete"

    # ------------------------------------------------------------------
    # Step 3 — Brownfield path
    # ------------------------------------------------------------------
    else:
        print(f"Brownfield mode: {count:,} existing chunks detected.")
        result["steps"].append("init_brownfield")
        result["chunks_detected"] = count

        # Backfill
        do_backfill = not no_backfill and not dry_run
        if interactive and not dry_run:
            answer = _prompt("Run KG backfill now? (y/N): ", default="n").lower()
            do_backfill = answer in ("y", "yes")

        if do_backfill:
            print("Running KG backfill…")
            backfill_result = _run_backfill()
            result["backfill"] = backfill_result
            print(f"Backfill complete: {backfill_result}")
        else:
            reason = "dry-run mode" if dry_run else "user skipped"
            print(
                "Skipping backfill."
                " Run manually: python tools/rag/rag_to_kg_ingester.py --backfill"
            )
            result["backfill"] = {"status": "skipped", "reason": reason}

        # Disambiguation
        do_disambig = not no_disambiguation and not dry_run
        if interactive and not dry_run:
            answer = _prompt(
                "Run disambiguation sweep after backfill? (y/N): ", default="n"
            ).lower()
            do_disambig = answer in ("y", "yes")

        if do_disambig:
            print("Running disambiguation sweep…")
            disambig_result = _run_disambiguation_sweep()
            result["disambiguation"] = disambig_result
            print(f"Disambiguation complete: {disambig_result}")
        else:
            reason = "dry-run mode" if dry_run else "user skipped"
            print(
                "Skipping disambiguation sweep."
                " Run manually: python tools/rag/setup_wizard.py --mode brownfield"
            )
            result["disambiguation"] = {"status": "skipped", "reason": reason}

        print("\nBrownfield setup complete.")
        result["steps"].append("brownfield_complete")
        result["status"] = "complete"

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAG+KG setup wizard (greenfield/brownfield)."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for all choices (default when no --mode given).",
    )
    group.add_argument(
        "--mode",
        choices=["greenfield", "brownfield"],
        help="Run non-interactively in the specified mode.",
    )
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Skip KG backfill in brownfield mode.",
    )
    parser.add_argument(
        "--no-disambiguation",
        action="store_true",
        help="Skip disambiguation sweep in brownfield mode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Validate paths without writing to DB or config files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output result as JSON.",
    )
    args = parser.parse_args()

    interactive = args.interactive or (args.mode is None)

    try:
        result = run_setup(
            interactive=interactive,
            mode=args.mode,
            no_backfill=args.no_backfill,
            no_disambiguation=args.no_disambiguation,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        if args.as_json:
            print(json.dumps({"status": "error", "reason": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
