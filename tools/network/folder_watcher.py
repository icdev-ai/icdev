# CUI // SP-CTI
"""ICDEV™ Network Canvas — Folder Watcher for File Drop Ingestion.

Polls a configurable directory for new files and auto-ingests them
via the unified ingestion pipeline. Supports separation-of-duty
workflows where network teams drop exports into a shared folder.

Uses SHA-256 hash tracking for dedup (same pattern as auto_indexer.py).

Usage::

    python tools/network/folder_watcher.py --scan-once
    python tools/network/folder_watcher.py --watch --interval 30
    python tools/network/folder_watcher.py --watch --dir data/ndc_inbox
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import json
import logging
import time
from tools.db.storage import get_connection
from pathlib import Path
from typing import Any, Dict, Optional

logger = get_logger("icdev.network.folder_watcher")

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_WATCH_DIR = _ICDEV_ROOT / "data" / "ndc_inbox"
_DEFAULT_DB = _ICDEV_ROOT / "data" / "network_canvas.db"

# Supported extensions (from network_canvas_config.yaml)
_SUPPORTED_EXTS = {
    ".drawio", ".vdx", ".svg",
    ".pdf", ".docx", ".txt", ".md",
    ".csv", ".xlsx",
    ".jpg", ".jpeg", ".png",
    ".conf", ".cfg", ".log",
}


class FolderWatcher:
    """Polls a directory for new files and ingests them.

    Tracks processed files by SHA-256 hash in nc_ingestion_log to avoid
    re-processing duplicates.
    """

    def __init__(
        self,
        watch_dir: Optional[str] = None,
        poll_interval: int = 30,
        project_id: str = "default",
        db_path: Optional[str] = None,
    ) -> None:
        self._watch_dir = Path(watch_dir) if watch_dir else _DEFAULT_WATCH_DIR
        self._poll_interval = poll_interval
        self._project_id = project_id
        self._db_path = db_path or str(_DEFAULT_DB)
        self._running = False
        self._processed_hashes = set()  # type: set

        # Load already-processed hashes from DB
        self._load_processed_hashes()

    def _load_processed_hashes(self) -> None:
        """Load hashes of already-processed files from nc_ingestion_log."""
        try:
            conn = get_connection(str(self._db_path))
            rows = conn.execute(
                "SELECT file_hash FROM nc_ingestion_log "
                "WHERE channel='folder_watch' AND status='completed'"
            ).fetchall()
            self._processed_hashes = {r[0] for r in rows if r[0]}
            conn.close()
            logger.info("Loaded %d processed hashes", len(self._processed_hashes))
        except Exception as exc:
            logger.warning("Could not load processed hashes: %s", exc)

    def _file_hash(self, path: Path) -> str:
        """SHA-256 hash of file contents."""
        h = hashlib.sha256()
        with open(str(path), "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def scan_once(self) -> Dict[str, Any]:
        """Scan the watch directory once and ingest new files.

        Returns:
            {scanned: int, ingested: int, skipped: int, errors: int, results: [...]}
        """
        if not self._watch_dir.exists():
            self._watch_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created watch directory: %s", self._watch_dir)

        from tools.network.ingestion_pipeline import ingest_file

        results = []
        scanned = 0
        ingested = 0
        skipped = 0
        errors = 0

        for path in sorted(self._watch_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _SUPPORTED_EXTS:
                continue

            scanned += 1
            fhash = self._file_hash(path)

            if fhash in self._processed_hashes:
                skipped += 1
                continue

            logger.info("Ingesting: %s", path.name)
            try:
                result = ingest_file(
                    str(path),
                    channel="folder_watch",
                    project_id=self._project_id,
                    db_path=self._db_path,
                )
                self._processed_hashes.add(fhash)
                if "error" in result:
                    errors += 1
                else:
                    ingested += 1
                results.append({"file": path.name, "result": result})
            except Exception as exc:
                errors += 1
                logger.error("Failed to ingest %s: %s", path.name, exc)
                results.append({"file": path.name, "error": str(exc)})

        return {
            "watch_dir": str(self._watch_dir),
            "scanned": scanned,
            "ingested": ingested,
            "skipped": skipped,
            "errors": errors,
            "results": results,
        }

    def start(self) -> None:
        """Start the polling loop (blocking)."""
        self._running = True
        logger.info(
            "Folder watcher started: dir=%s interval=%ds",
            self._watch_dir, self._poll_interval,
        )
        while self._running:
            try:
                result = self.scan_once()
                if result["ingested"] > 0 or result["errors"] > 0:
                    logger.info(
                        "Scan: ingested=%d skipped=%d errors=%d",
                        result["ingested"], result["skipped"], result["errors"],
                    )
            except Exception as exc:
                logger.error("Scan failed: %s", exc)
            time.sleep(self._poll_interval)

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV NDC Folder Watcher")
    parser.add_argument("--dir", default=None, help="Watch directory (default: data/ndc_inbox)")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds")
    parser.add_argument("--project-id", default="default", help="Project ID")
    parser.add_argument("--scan-once", action="store_true", help="Scan once and exit")
    parser.add_argument("--watch", action="store_true", help="Start continuous polling")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    watcher = FolderWatcher(
        watch_dir=args.dir,
        poll_interval=args.interval,
        project_id=args.project_id,
    )

    if args.scan_once:
        result = watcher.scan_once()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print("Scanned: %d  Ingested: %d  Skipped: %d  Errors: %d" % (
                result["scanned"], result["ingested"], result["skipped"], result["errors"]
            ))
    elif args.watch:
        watcher.start()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()