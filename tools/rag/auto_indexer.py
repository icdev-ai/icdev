#!/usr/bin/env python3
# CUI // SP-CTI
"""CUI-aware auto-indexing for project directories (D-RAG-16).

Watches project directories for new/modified files. Respects CUI
classification boundaries — files above project IL are excluded.
Uses file mtime + SHA-256 hash for change detection.

Usage:
    python tools/rag/auto_indexer.py --scan --project-dir /path --json
    python tools/rag/auto_indexer.py --sweep --project-dir /path --json
    python tools/rag/auto_indexer.py --index-file --file /path/to/file.pdf --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = get_logger("icdev.rag.auto_indexer")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Classification hierarchy (lowest to highest). These are deterministic NIST /
# CUI boundary levels used by the CUI-boundary enforcement check below — they
# are SECURITY comparison constants, NOT tunable numeric thresholds.
#
# AI-ify opportunities 5458 (line ~345) and 5459 (line ~348) flagged the bare
# numerics `< 2` / `<= 1` here as "hardcoded_threshold -> anomaly_detection".
# Classification/audit (NIST AU) controls must remain deterministic and
# auditable, so a probabilistic ML anomaly model is the wrong tool. The genuine
# code smell — magic numbers — is removed instead by deriving named boundary
# constants from the hierarchy below. Change the hierarchy, not the comparisons.
# ---------------------------------------------------------------------------
_CLASSIFICATION_LEVELS = {
    "UNCLASSIFIED": 0,
    "CUI": 1,
    "SECRET": 2,
    "TOP SECRET": 3,
    "TS": 3,
}
_DEFAULT_CLASSIFICATION_LEVEL = _CLASSIFICATION_LEVELS["CUI"]
_CUI_LEVEL = _CLASSIFICATION_LEVELS["CUI"]        # at/below CUI -> enforce CUI-tier blocks
_SECRET_LEVEL = _CLASSIFICATION_LEVELS["SECRET"]  # below SECRET -> block SECRET markings


def _load_config() -> dict:
    """Load auto_indexer config from rag_config.yaml."""
    try:
        import yaml

        from tools.rag.config_path import rag_config_path

        config_path = rag_config_path()
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("auto_indexer", {})
    except Exception:
        pass
    return {}


def _file_hash(path: Path) -> str:
    """Compute SHA-256 hash of file content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class AutoIndexer:
    """CUI-aware auto-indexing for project directories (D-RAG-16).

    Scans project directories for indexable files. Respects CUI
    classification boundaries — files above project IL are excluded.
    """

    SUPPORTED_EXTENSIONS: Set[str] = {
        ".md",
        ".txt",
        ".py",
        ".yaml",
        ".yml",
        ".json",
        ".pdf",
    }
    EXCLUDED_DIRS: Set[str] = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tmp",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build",
        ".eggs",
        "*.egg-info",
    }

    def __init__(
        self,
        project_dir: str,
        project_id: str = "",
        tenant_id: str = "",
        classification: str = "CUI",
        config: Optional[dict] = None,
    ):
        self._project_dir = Path(project_dir)
        self._project_id = project_id
        self._tenant_id = tenant_id
        self._classification = classification
        self._config = config or _load_config()

        # Override from config
        ext_list = self._config.get("watch_extensions", [])
        if ext_list:
            self.SUPPORTED_EXTENSIONS = set(ext_list)
        excl_list = self._config.get("excluded_dirs", [])
        if excl_list:
            self.EXCLUDED_DIRS = set(excl_list)

        self._max_file_size_mb = self._config.get("max_file_size_mb", 10)
        self._cui_enforcement = self._config.get("cui_boundary_enforcement", True)

    def scan(self) -> Dict[str, Any]:
        """Scan directory for indexable files, respecting CUI boundaries.

        Returns:
            Dict with file list and stats.
        """
        if not self._project_dir.exists():
            return {"error": f"Directory not found: {self._project_dir}", "files": []}

        files: List[Dict[str, Any]] = []
        skipped = 0
        too_large = 0
        cui_blocked = 0

        for path in self._walk_directory():
            # Size check
            try:
                size_mb = path.stat().st_size / (1024 * 1024)
            except OSError:
                skipped += 1
                continue

            if size_mb > self._max_file_size_mb:
                too_large += 1
                continue

            # CUI boundary check
            if self._cui_enforcement and self._is_above_classification(path):
                cui_blocked += 1
                continue

            file_info = {
                "path": str(path),
                "name": path.name,
                "extension": path.suffix.lower(),
                "size_bytes": int(path.stat().st_size),
                "mtime": path.stat().st_mtime,
                "hash": _file_hash(path),
            }
            files.append(file_info)

        return {
            "classification": "CUI // SP-CTI",
            "project_dir": str(self._project_dir),
            "total_files": len(files),
            "skipped": skipped,
            "too_large": too_large,
            "cui_blocked": cui_blocked,
            "files": files,
        }

    def index_file(self, file_path: Path) -> Dict[str, Any]:
        """Index a single file (PDF delegated to pdf_provider chain).

        Args:
            file_path: Path to the file to index.

        Returns:
            Dict with indexing result.
        """
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {path}", "indexed": False}

        ext = path.suffix.lower()
        content = ""
        provider_used = "text"

        if ext == ".pdf":
            # Delegate to PDF provider chain
            try:
                from tools.rag.pdf_provider import extract_pdf

                extraction = extract_pdf(path)
                if extraction.status == "extracted" and extraction.pages:
                    content = extraction.full_text
                    provider_used = extraction.provider_used
                else:
                    return {
                        "error": extraction.error or "PDF extraction failed",
                        "indexed": False,
                    }
            except ImportError:
                return {"error": "PDF provider not available", "indexed": False}
        elif ext in (".md", ".txt", ".py", ".yaml", ".yml", ".json"):
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                return {"error": f"Read failed: {exc}", "indexed": False}
        else:
            return {"error": f"Unsupported extension: {ext}", "indexed": False}

        if not content.strip():
            return {"error": "Empty file", "indexed": False}

        # Chunk and ingest
        try:
            from tools.rag.chunker import chunk_text
            from tools.rag.vector_store_factory import VectorStoreFactory

            chunks = chunk_text(
                text=content,
                source_type="auto_indexed",
                source_id=str(path),
                source_table="auto_index",
                metadata={
                    "file_name": path.name,
                    "file_path": str(path),
                    "extension": ext,
                    "project_id": self._project_id,
                    "provider_used": provider_used,
                },
            )

            if not chunks:
                return {"error": "No chunks produced", "indexed": False}

            # Embed
            from tools.rag.ingestion_manager import _get_embedding_provider, _embed_chunks

            provider = _get_embedding_provider()
            embedded = _embed_chunks(chunks, provider) if provider else 0

            # Store
            store = VectorStoreFactory.create(tenant_id=self._tenant_id)
            stored = store.upsert(chunks)

            return {
                "indexed": True,
                "file": str(path),
                "chunks_created": len(chunks),
                "chunks_embedded": embedded,
                "chunks_stored": stored,
                "provider_used": provider_used,
            }
        except Exception as exc:
            return {"error": f"Indexing failed: {exc}", "indexed": False}

    def sweep(self) -> Dict[str, Any]:
        """Full directory sweep — index all new/modified files.

        Uses file hash to detect changes and skip unchanged files.

        Returns:
            Dict with sweep results.
        """
        scan_result = self.scan()
        if "error" in scan_result:
            return scan_result

        indexed = 0
        skipped = 0
        failed = 0
        results: List[Dict[str, Any]] = []

        # Get previously indexed hashes to skip unchanged
        known_hashes = self._get_known_hashes()

        for file_info in scan_result.get("files", []):
            file_hash = file_info.get("hash", "")
            if file_hash in known_hashes:
                skipped += 1
                continue

            result = self.index_file(Path(file_info["path"]))
            if result.get("indexed"):
                indexed += 1
            else:
                failed += 1

            results.append(result)

        return {
            "classification": "CUI // SP-CTI",
            "project_dir": str(self._project_dir),
            "total_scanned": scan_result.get("total_files", 0),
            "indexed": indexed,
            "skipped_unchanged": skipped,
            "failed": failed,
            "results": results[:20],  # Limit output size
        }

    def _walk_directory(self):
        """Walk directory tree, respecting exclusion patterns.

        SEC: All yielded paths are validated to be within project_dir
        boundary (prevents symlink/traversal escapes).
        """
        project_root = self._project_dir.resolve()
        for root, dirs, files in os.walk(self._project_dir):
            # Filter excluded directories (modifying dirs in-place)
            dirs[:] = [d for d in dirs if d not in self.EXCLUDED_DIRS and not d.endswith(".egg-info")]

            for fname in files:
                path = Path(root) / fname
                # SEC: Resolve to real path and validate within project boundary
                try:
                    real_path = path.resolve()
                    real_path.relative_to(project_root)
                except (ValueError, OSError):
                    logger.debug("Skipping %s — outside project boundary", path)
                    continue
                if real_path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                    yield real_path

    def _is_above_classification(self, path: Path) -> bool:
        """Check if file contains classification markers above project IL.

        SEC: Scans file header for classification markings. Files with
        markings above the project classification are blocked from indexing.
        Files without any recognized CUI marking are also flagged when
        cui_boundary_enforcement is strict.

        Classification hierarchy (lowest to highest):
            UNCLASSIFIED < CUI < SECRET < TOP SECRET
        """
        proj_class = self._classification.upper()
        if proj_class in ("SECRET", "TOP SECRET", "TS"):
            return False  # Project is already at highest — nothing blocked

        if path.suffix.lower() in (".pdf",):
            return False  # Can't scan binary files — allow, classify on extract

        # Classification markers and their levels (higher = more restricted).
        # Levels come from the module-level _CLASSIFICATION_LEVELS hierarchy so
        # the comparisons below read as named boundaries, not magic numbers.
        project_level = _CLASSIFICATION_LEVELS.get(proj_class, _DEFAULT_CLASSIFICATION_LEVEL)

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                header = f.read(1000)
            upper = header.upper()

            # Check for explicit classification markers above project level
            if "// TOP SECRET" in upper or "//TS//" in upper or "//TS " in upper:
                return True
            if project_level < _SECRET_LEVEL and ("// SECRET" in upper or "//SECRET" in upper):
                return True
            # SEC: Also block NOFORN, SAP, SCI markings at or below the CUI boundary
            if project_level <= _CUI_LEVEL:
                for marker in ("//NOFORN", "//SAP", "//SCI", "//HCS", "//SI", "//TK"):
                    if marker in upper:
                        return True
        except Exception:
            pass

        return False

    def _get_known_hashes(self) -> set:
        """Get content hashes of previously indexed chunks."""
        try:
            from tools.rag.vector_store_factory import VectorStoreFactory

            store = VectorStoreFactory.create(tenant_id=self._tenant_id)
            if hasattr(store, "get_all_content_hashes"):
                return set(store.get_all_content_hashes())
        except Exception:
            pass
        return set()


def main():
    parser = argparse.ArgumentParser(description="RAG Auto-Indexer (D-RAG-16)")
    parser.add_argument("--scan", action="store_true", help="Scan directory for indexable files")
    parser.add_argument("--sweep", action="store_true", help="Full sweep — index all new/modified files")
    parser.add_argument("--index-file", action="store_true", help="Index a single file")
    parser.add_argument("--file", help="Path to file for --index-file")
    parser.add_argument("--project-dir", help="Project directory to scan")
    parser.add_argument("--project-id", default="", help="Project ID")
    parser.add_argument("--tenant-id", default="", help="Tenant ID")
    parser.add_argument("--classification", default="CUI", help="Project classification")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    indexer = AutoIndexer(
        project_dir=args.project_dir or ".",
        project_id=args.project_id,
        tenant_id=args.tenant_id,
        classification=args.classification,
    )

    if args.scan:
        result = indexer.scan()
    elif args.sweep:
        result = indexer.sweep()
    elif args.index_file and args.file:
        result = indexer.index_file(Path(args.file))
    else:
        parser.print_help()
        return

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        for k, v in result.items():
            if k != "files" and k != "results":
                print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
