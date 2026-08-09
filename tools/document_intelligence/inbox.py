# CUI // SP-CTI
"""DIC inbox — bulk/watched file-drop ingestion into a collection.

``ingest_orchestrator.ingest_file`` handles exactly one file, in-process. Every
DIC consumer calls it programmatically, so the only way to get a folder of
documents into DIC was the web upload, one at a time. Teams pulling documents
out of SharePoint (Playwright download, export, or any other acquisition path)
had nowhere to put them — the acquisition was automated and the ingestion was
not. This closes that gap: drop files in a directory, they land in a named
collection with RAG chunks + KG bridge, exactly as if uploaded.

Deliberately acquisition-agnostic. It does not scrape, authenticate, or know
what SharePoint is — a browser session already solves auth across on-prem and
M365 far better than a REST client can. This is only the landing zone.

Mirrors ``tools/network/folder_watcher.py`` (scan-once / watch, SHA-256 dedup).

Dedup is by **content hash, never filename**: the same CR re-exported after a
revision keeps its filename but must re-ingest, while an unchanged re-download
must not. State lives in ``.dic_inbox_state.json`` inside the watch dir, so the
inbox stays self-contained (dic_ingest_jobs has no hash column) and a lost state
file only costs a re-ingest, never data.

Usage::

    python -m tools.document_intelligence.inbox --collection change-records --scan-once
    python -m tools.document_intelligence.inbox --collection change-records --watch --interval 30
    python -m tools.document_intelligence.inbox --dir /path/to/drop --collection cr --dry-run --json
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_WATCH_DIR = _ICDEV_ROOT / "data" / "dic_inbox"
_STATE_FILENAME = ".dic_inbox_state.json"

# Matches the accept list on the DIC upload dropzone — the inbox must not accept
# a type the web path would reject, or the two surfaces disagree.
_SUPPORTED_EXTS = {
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".png", ".jpg", ".jpeg",
    ".html", ".txt", ".md",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Content hash, streamed — inbox files can be large scanned PDFs."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def _state_path(watch_dir: Path) -> Path:
    return watch_dir / _STATE_FILENAME


def load_state(watch_dir: Path) -> dict[str, Any]:
    p = _state_path(watch_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        # A corrupt state file must not wedge ingestion; worst case we re-ingest.
        logger.warning("dic inbox: unreadable state file (%s) — treating as empty", exc)
        return {}


def save_state(watch_dir: Path, state: dict[str, Any]) -> None:
    try:
        _state_path(watch_dir).write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8", newline=""
        )
    except Exception as exc:
        logger.warning("dic inbox: could not persist state: %s", exc)


def discover(watch_dir: Path, recursive: bool = False) -> list[Path]:
    """Supported files in the drop dir, sorted for deterministic runs."""
    if not watch_dir.exists():
        return []
    it = watch_dir.rglob("*") if recursive else watch_dir.glob("*")
    return sorted(
        p for p in it
        if p.is_file()
        and p.suffix.lower() in _SUPPORTED_EXTS
        and not p.name.startswith(".")
    )


def ingest_directory(
    watch_dir: str | Path | None = None,
    collection_id: str = "inbox",
    *,
    recursive: bool = False,
    dry_run: bool = False,
    move_processed: bool = False,
    tenant_id: str | None = None,
    classification: str | None = None,
    created_by: str = "dic_inbox",
) -> dict[str, Any]:
    """Ingest every new supported file in ``watch_dir`` into ``collection_id``.

    Returns {ingested, skipped_duplicate, failed, files, errors, dry_run}.
    A per-file failure never aborts the run — one unreadable PDF must not strand
    the rest of a drop.
    """
    wd = Path(watch_dir) if watch_dir else _DEFAULT_WATCH_DIR
    result: dict[str, Any] = {
        "watch_dir": str(wd),
        "collection_id": collection_id,
        "ingested": 0,
        "skipped_duplicate": 0,
        "failed": 0,
        "files": [],
        "errors": [],
        "dry_run": dry_run,
    }
    files = discover(wd, recursive=recursive)
    if not files:
        return result

    state = load_state(wd)
    for path in files:
        try:
            digest = sha256_file(path)
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append(f"{path.name}: hash failed: {exc}")
            continue

        # Content hash, not filename: a revised CR keeps its name and must
        # re-ingest; an unchanged re-download must not.
        key = f"{collection_id}:{digest}"
        if key in state:
            result["skipped_duplicate"] += 1
            continue

        if dry_run:
            result["files"].append({"file": path.name, "sha256": digest, "would_ingest": True})
            result["ingested"] += 1
            continue

        try:
            from tools.document_intelligence.ingest_orchestrator import ingest_file

            outcome = ingest_file(
                str(path),
                collection_id,
                tenant_id=tenant_id,
                classification=classification,
                created_by=created_by,
            )
            doc_id = getattr(outcome, "doc_id", None) or (
                outcome.get("doc_id") if isinstance(outcome, dict) else None
            )
        except Exception as exc:
            # Left unrecorded on purpose: the next sweep retries it.
            result["failed"] += 1
            result["errors"].append(f"{path.name}: {exc}")
            logger.warning("dic inbox: ingest failed for %s: %s", path.name, exc)
            continue

        state[key] = {
            "file": path.name,
            "sha256": digest,
            "doc_id": doc_id,
            "collection_id": collection_id,
            "ingested_at": _now(),
        }
        result["ingested"] += 1
        result["files"].append({"file": path.name, "sha256": digest, "doc_id": doc_id})

        if move_processed:
            try:
                done = wd / "processed"
                done.mkdir(parents=True, exist_ok=True)
                path.rename(done / path.name)
            except Exception as exc:
                logger.warning("dic inbox: could not move %s: %s", path.name, exc)

    if not dry_run:
        save_state(wd, state)
    logger.info(
        "dic inbox: ingested=%s skipped=%s failed=%s -> %s",
        result["ingested"], result["skipped_duplicate"], result["failed"], collection_id,
    )
    return result


class InboxWatcher:
    """Poll a drop directory and ingest new files (mirrors NDC FolderWatcher)."""

    def __init__(
        self,
        watch_dir: str | None = None,
        collection_id: str = "inbox",
        poll_interval: int = 30,
        **ingest_kwargs: Any,
    ) -> None:
        self.watch_dir = Path(watch_dir) if watch_dir else _DEFAULT_WATCH_DIR
        self.collection_id = collection_id
        self.poll_interval = poll_interval
        self._ingest_kwargs = ingest_kwargs
        self._running = False

    def scan_once(self) -> dict[str, Any]:
        return ingest_directory(
            self.watch_dir, self.collection_id, **self._ingest_kwargs
        )

    def stop(self) -> None:
        self._running = False

    def watch(self, max_cycles: int | None = None) -> dict[str, Any]:
        """Poll until stopped. max_cycles bounds the loop for tests."""
        self._running = True
        totals = {"cycles": 0, "ingested": 0, "failed": 0}
        while self._running:
            out = self.scan_once()
            totals["cycles"] += 1
            totals["ingested"] += out["ingested"]
            totals["failed"] += out["failed"]
            if max_cycles is not None and totals["cycles"] >= max_cycles:
                break
            time.sleep(self.poll_interval)
        return totals


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="DIC inbox — ingest a folder of documents into a collection"
    )
    ap.add_argument("--dir", default=None, help=f"watch dir (default {_DEFAULT_WATCH_DIR})")
    ap.add_argument("--collection", required=True, help="target DIC/RAG collection id")
    ap.add_argument("--scan-once", action="store_true", help="ingest current files and exit")
    ap.add_argument("--watch", action="store_true", help="poll continuously")
    ap.add_argument("--interval", type=int, default=30, help="poll seconds (default 30)")
    ap.add_argument("--recursive", action="store_true", help="descend into subdirectories")
    ap.add_argument("--move-processed", action="store_true", help="move files to ./processed after ingest")
    ap.add_argument("--dry-run", action="store_true", help="report what would ingest; write nothing")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    if args.watch:
        w = InboxWatcher(
            args.dir, args.collection, poll_interval=args.interval,
            recursive=args.recursive, move_processed=args.move_processed,
        )
        out: dict[str, Any] = w.watch()
    else:
        out = ingest_directory(
            args.dir, args.collection,
            recursive=args.recursive, dry_run=args.dry_run,
            move_processed=args.move_processed,
        )

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(
            f"ingested={out.get('ingested', 0)} "
            f"skipped={out.get('skipped_duplicate', 0)} "
            f"failed={out.get('failed', 0)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
