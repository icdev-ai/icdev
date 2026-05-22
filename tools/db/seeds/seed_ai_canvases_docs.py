# CUI // SP-CTI
"""Download and ingest 12 public DoD/IC AI governance PDFs + 5 synthetic
reference documents into the ICDEV™ RAG pipeline.

PDFs are downloaded to context/ai_canvases/docs/.
Synthetic markdown docs are already present in context/ai_canvases/.
Both are registered in the RAG source registry and ingested via ingestion_manager.

Public documents (12):
  1. NIST AI RMF 1.0 (NIST.AI.100-1.pdf)
  2. NIST AI 600-1 GenAI Profile
  3. DoD AI Strategy 2024
  4. OMB M-25-21
  5. CMMC Level 2 Assessment Guide
  6. MITRE ATLAS v5.4 (HTML fallback)
  7. DoD Responsible AI (RAI Guidelines)
  8. FedRAMP Authorization Boundary Guidance
  9. NIST SP 800-53 Rev 5 (pages 1-60)
  10. EO 14110 Section 4 (AI Safety)
  11. DoD RAI Guidelines (ai.mil)
  12. JADC2 Implementation Summary

Flags:
  --dry-run     Show what would be downloaded without fetching
  --json        Machine-readable output
  --force       Re-download even if file exists
  --skip-ingest Download only; do not run RAG ingestion

Run:
    python tools/db/seeds/seed_ai_canvases_docs.py --dry-run
    python tools/db/seeds/seed_ai_canvases_docs.py --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

DOCS_DIR = ROOT / "context" / "ai_canvases" / "docs"
SYNTHETIC_DIR = ROOT / "context" / "ai_canvases"

# ── 12 Public Documents ───────────────────────────────────────────────────────
# Each entry: (filename, url, description, fallback_url_or_None)
# URLs reference publicly accessible government/standards documents.
PUBLIC_DOCS = [
    {
        "filename": "NIST.AI.100-1_AI_RMF.pdf",
        "url": "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
        "description": "NIST AI RMF 1.0 — Artificial Intelligence Risk Management Framework",
        "classification": "UNCLASSIFIED",
        "relevance": "AADC/AIMC assessment scoring framework",
    },
    {
        "filename": "NIST.AI.600-1_GenAI_Profile.pdf",
        "url": "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf",
        "description": "NIST AI 600-1 — GenAI Profile (confabulation detection logic)",
        "classification": "UNCLASSIFIED",
        "relevance": "Observatory confabulation_flag logic, GAI.1",
    },
    {
        "filename": "DoD_Data_AI_Strategy_2024.pdf",
        "url": "https://media.defense.gov/2024/Feb/08/2003381965/-1/-1/1/DOD-DATA-AI-STRATEGY.PDF",
        "description": "DoD Data and AI Strategy 2024",
        "classification": "UNCLASSIFIED",
        "relevance": "AADC use case contexts, CDAO mission alignment",
    },
    {
        "filename": "OMB_M-25-21_AI_Federal_Government.pdf",
        "url": "https://www.whitehouse.gov/wp-content/uploads/2025/04/M-25-21.pdf",
        "description": "OMB M-25-21 — Accelerating Federal Use of AI Responsibly",
        "classification": "UNCLASSIFIED",
        "relevance": "AADC OMB compliance scoring, rights-impacting §4",
    },
    {
        "filename": "CMMC_L2_Assessment_Guide_v2.pdf",
        "url": "https://dodcio.defense.gov/Portals/0/Documents/CMMC/AG_Level2_MasterV2.0_FINAL_20211013_508.pdf",
        "description": "CMMC Level 2 Assessment Guide v2.0",
        "classification": "UNCLASSIFIED",
        "relevance": "AADC ATO/CMMC checklist, 110 practices",
    },
    {
        "filename": "FedRAMP_Authorization_Boundary_Guidance.pdf",
        "url": "https://www.fedramp.gov/assets/resources/documents/CSP_Authorization_Boundary_Guidance.pdf",
        "description": "FedRAMP Authorization Boundary Guidance",
        "classification": "UNCLASSIFIED",
        "relevance": "AADC IL level classification, CSP authorization boundaries",
    },
    {
        "filename": "DoD_ZeroTrust_Strategy_2022.pdf",
        "url": "https://media.defense.gov/2022/Nov/22/2003113366/-1/-1/0/THE-DEPARTMENT-OF-DEFENSE-ZERO-TRUST-STRATEGY.PDF",
        "description": "DoD Zero Trust Strategy 2022 — network security reference",
        "classification": "UNCLASSIFIED",
        "relevance": "AADC agent isolation boundary nodes, ZT architecture context",
    },
    {
        "filename": "DoD_AI_Ethics_Principles.pdf",
        "url": "https://media.defense.gov/2019/Oct/31/2002204458/-1/-1/0/DIB_AI_PRINCIPLES_PRIMARY_DOCUMENT.PDF",
        "description": "DoD AI Ethics Principles — Responsible, Equitable, Traceable, Reliable, Governable",
        "classification": "UNCLASSIFIED",
        "relevance": "AIMC DoD RAI 5 Principles scoring baseline",
    },
    {
        "filename": "NIST_SP_800-53_Rev5_Controls.pdf",
        "url": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf",
        "description": "NIST SP 800-53 Rev 5 — Security and Privacy Controls",
        "classification": "UNCLASSIFIED",
        "relevance": "AC/AU/SI control family → AADC ATO checklist, FedRAMP High",
    },
    {
        "filename": "CISA_AI_Roadmap_2023.pdf",
        "url": "https://www.cisa.gov/sites/default/files/2023-11/2023-2024_CISA-Roadmap-for-AI_508c.pdf",
        "description": "CISA Roadmap for Artificial Intelligence 2023–2024",
        "classification": "UNCLASSIFIED",
        "relevance": "AADC cybersecurity canvas use cases, SIEM/threat hunting context",
    },
    {
        "filename": "DoD_Instruction_8582_01_AI_Systems.pdf",
        "url": "https://www.esd.whs.mil/Portals/54/Documents/DD/issuances/dodi/858201p.pdf",
        "description": "DoDI 8582.01 — AI Autonomy in Weapon Systems",
        "classification": "UNCLASSIFIED",
        "relevance": "AADC autonomy_max levels, safety-impacting classification rules",
    },
    {
        "filename": "JADC2_Implementation_Framework.pdf",
        "url": "https://media.defense.gov/2022/Mar/17/2002958406/-1/-1/0/JOINT-ALL-DOMAIN-COMMAND-AND-CONTROL-APPROACH.PDF",
        "description": "DoD JADC2 Implementation Framework 2022",
        "classification": "UNCLASSIFIED",
        "relevance": "AADC Record 3 (JADC2 Mission Planning) context and COA patterns",
    },
]

# ── 5 Synthetic Reference Docs (already present in context/ai_canvases/) ──────
SYNTHETIC_DOCS = [
    "dod_ai_use_case_registry.md",
    "ic_ai_governance_requirements.md",
    "dod_aiml_model_selection_guide.md",
    "legacy_system_ai_modernization_playbook.md",
    "dod_ai_demo_scenarios_and_objections.md",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def download_docs(force: bool = False, verbose: bool = True) -> list[dict]:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for doc in PUBLIC_DOCS:
        dest = DOCS_DIR / doc["filename"]
        if dest.exists() and not force:
            if verbose:
                print(f"  [SKIP] {doc['filename']} (exists, use --force to re-download)")
            results.append({"filename": doc["filename"], "status": "skipped", "path": str(dest)})
            continue

        if verbose:
            print(f"  [DOWN] {doc['filename']} ...")

        try:
            req = urllib.request.Request(
                doc["url"],
                headers={"User-Agent": "ICDEV-DocIngester/1.0 (DoD AI Canvas Seed)"},
            )
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            elapsed = round((time.monotonic() - t0) * 1000)

            with open(dest, "wb") as f:
                f.write(data)

            sha = _sha256(dest)
            size_kb = len(data) // 1024
            if verbose:
                print(f"  [OK]   {doc['filename']} ({size_kb}KB, {elapsed}ms, sha256={sha[:12]}...)")
            results.append({
                "filename": doc["filename"],
                "status": "downloaded",
                "path": str(dest),
                "size_bytes": len(data),
                "sha256": sha,
                "elapsed_ms": elapsed,
            })
        except Exception as exc:
            if verbose:
                print(f"  [FAIL] {doc['filename']}: {exc}")
            results.append({
                "filename": doc["filename"],
                "status": "error",
                "error": str(exc),
                "url": doc["url"],
            })

    return results


def ingest_canvas_sources(verbose: bool = True) -> dict:
    """Trigger RAG ingestion for the 3 AI canvas source types."""
    try:
        from tools.rag.ingestion_manager import ingest_source
    except ImportError as exc:
        return {"status": "error", "error": f"ingestion_manager import failed: {exc}"}

    canvas_sources = ["aadc_designs", "aadc_assessments", "aiml_designs",
                      "aiml_assessments", "aiml_artifacts", "aac_opportunities", "aac_roadmaps"]
    results = {}
    for src in canvas_sources:
        if verbose:
            print(f"  [RAG]  Ingesting source: {src} ...")
        try:
            result = ingest_source(src, project_id="dod-ic-demo")
            results[src] = {
                "status": "ok",
                "chunks": result.get("chunks_written", 0),
                "records": result.get("records_processed", 0),
            }
            if verbose:
                print(f"  [OK]   {src}: {results[src]['chunks']} chunks from {results[src]['records']} records")
        except Exception as exc:
            results[src] = {"status": "error", "error": str(exc)[:120]}
            if verbose:
                print(f"  [FAIL] {src}: {exc}")

    return {"canvas_sources": results}


def ingest_pdf_docs(verbose: bool = True) -> dict:
    """Ingest downloaded PDFs via ingestion_manager pdf_documents source."""
    try:
        from tools.rag.ingestion_manager import ingest_source
    except ImportError as exc:
        return {"status": "error", "error": f"ingestion_manager import failed: {exc}"}

    if verbose:
        print("  [RAG]  Ingesting PDF documents source ...")
    try:
        result = ingest_source("pdf_documents", project_id="dod-ic-demo")
        chunks = result.get("chunks_written", 0)
        records = result.get("records_processed", 0)
        if verbose:
            print(f"  [OK]   pdf_documents: {chunks} chunks from {records} records")
        return {"status": "ok", "chunks": chunks, "records": records}
    except Exception as exc:
        if verbose:
            print(f"  [FAIL] pdf_documents: {exc}")
        return {"status": "error", "error": str(exc)[:200]}


def main(
    dry_run: bool = False,
    force: bool = False,
    skip_ingest: bool = False,
    verbose: bool = True,
) -> dict:
    result: dict = {
        "downloads": [],
        "canvas_ingest": {},
        "pdf_ingest": {},
        "synthetic_docs": [],
    }

    if dry_run:
        if verbose:
            print("=== DRY RUN — no downloads or ingestion ===\n")
            print(f"Would download {len(PUBLIC_DOCS)} PDF(s) to: {DOCS_DIR}")
            for doc in PUBLIC_DOCS:
                dest = DOCS_DIR / doc["filename"]
                exists = "EXISTS" if dest.exists() else "MISSING"
                print(f"  [{exists}] {doc['filename']}")
                print(f"         {doc['description']}")
                print(f"         Relevance: {doc['relevance']}")
            print(f"\nWould ingest {len(SYNTHETIC_DOCS)} synthetic reference doc(s) from: {SYNTHETIC_DIR}")
            for doc in SYNTHETIC_DOCS:
                path = SYNTHETIC_DIR / doc
                exists = "EXISTS" if path.exists() else "MISSING"
                print(f"  [{exists}] {doc}")
        result["dry_run"] = True
        return result

    # Step 1 — Download PDFs
    if verbose:
        print(f"Downloading {len(PUBLIC_DOCS)} DoD/IC AI governance PDFs ...")
    result["downloads"] = download_docs(force=force, verbose=verbose)

    # Step 2 — Verify synthetic docs
    for docname in SYNTHETIC_DOCS:
        path = SYNTHETIC_DIR / docname
        result["synthetic_docs"].append({
            "filename": docname,
            "exists": path.exists(),
            "path": str(path),
        })
    missing = [d for d in result["synthetic_docs"] if not d["exists"]]
    if missing and verbose:
        print(f"  [WARN] {len(missing)} synthetic docs missing — run seed_ai_canvases_all.py first")

    if skip_ingest:
        if verbose:
            print("  [SKIP] RAG ingestion skipped (--skip-ingest)")
        return result

    # Step 3 — Ingest canvas sources into RAG
    if verbose:
        print("\nIngesting AI canvas sources into RAG ...")
    result["canvas_ingest"] = ingest_canvas_sources(verbose=verbose)

    # Step 4 — Ingest PDF documents
    if verbose:
        print("\nIngesting PDF documents into RAG ...")
    result["pdf_ingest"] = ingest_pdf_docs(verbose=verbose)

    # Summary
    downloaded = sum(1 for d in result["downloads"] if d["status"] == "downloaded")
    skipped = sum(1 for d in result["downloads"] if d["status"] == "skipped")
    errors = sum(1 for d in result["downloads"] if d["status"] == "error")
    if verbose:
        print(f"\nDone: {downloaded} downloaded, {skipped} skipped, {errors} errors")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download + ingest DoD/IC AI governance docs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without downloading")
    parser.add_argument("--force", action="store_true", help="Re-download even if file exists")
    parser.add_argument("--skip-ingest", action="store_true", help="Download only; skip RAG ingestion")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    result = main(
        dry_run=args.dry_run,
        force=args.force,
        skip_ingest=args.skip_ingest,
        verbose=not args.as_json,
    )

    if args.as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        sys.exit(0)
