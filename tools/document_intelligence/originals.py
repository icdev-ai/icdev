# CUI // SP-CTI
"""Retain the uploaded ORIGINAL of a DIC document, content-addressed (dwr-fid-01).

THE DEFECT. ``POST /document-intelligence/api/ingest`` saved the upload to a
``NamedTemporaryFile`` and ``os.unlink()``'d it in the ingest thread's
``finally``, while ``dic_documents.filepath`` kept pointing at the deleted path.
Measured on the live PG board 2026-09-07: 55 documents, 11 with a source file
still on disk, 29 whose recorded ``filepath`` no longer exists (every one under
``%TEMP%``), 15 with no ``filepath`` at all (documents generated IN the canvas —
docgen, IDR — which never had an upload). Without the original there is no
page geometry and no fidelity, ever: an extractor can be re-run over a retained
PDF, and it can never be re-run over a deleted temp file.

WHAT THIS DOES. ``retain_original`` copies the upload into a content-addressed
store BEFORE the temp file goes (``<root>/<sha256[:2]>/<sha256><suffix>``),
streaming the hash while it copies and landing the file with an atomic
``os.replace`` so a crash mid-copy never leaves a half file under the final
name. ``record_original`` writes ``original_path`` / ``original_sha256`` /
``original_retained_at`` on the ``dic_documents`` row (migration
20260908003311). The temp file's deletion is KEPT — the copy is what changes.

RETENTION IS A STORAGE DECISION, so it has knobs and a measurement:
  * ``ICDEV_DIC_ORIGINALS_DIR`` — the root. Defaults beside the export
    artifacts (``data/document_intelligence/originals``), the shape
    ``exporter.artifact_dir()`` already uses; a test points it at ``tmp_path``.
  * ``ICDEV_DIC_RETAIN_ORIGINALS=0`` — off. The upload is ingested as before,
    NOTHING is retained, and the ingest result says so
    (``original.reason: disabled_by_env``) rather than reading like a retained
    upload.
  * Growth: one file per DISTINCT upload content. Two uploads of the same bytes
    share one file (``deduplicated: True`` on the second); two different
    documents cost two files. Nothing prunes the root — ``survey()`` reports
    ``files_retained`` and ``bytes_retained`` so the growth is measured, and a
    pruning policy (keyed on ``original_retained_at`` and the collection's
    ``retention_days``) is a separate decision this module deliberately does
    not make.

THE VERDICT VOCABULARY, and why an absent original is never one bucket.
``original_verdict`` reads a ``dic_documents`` row and says ONE of:
  retained          original_path recorded and the file is there
  retained_missing  recorded, file gone — the root was moved or pruned
  retained_mismatch (``verify=True`` only) the file's sha256 is not the
                    recorded one — the store was tampered with or corrupted
  source_on_disk    nothing retained, but ``filepath`` (a CLI / batch ingest
                    of a real file) still exists — readable, NOT retained
  absent            nothing retained and ``filepath`` is gone. THE FINDING.
  no_source         no ``filepath`` at all: generated in-canvas, there was
                    never an upload to retain. NOT a finding.
``survey()`` counts them and states what it MEASURED: whether the retention
columns exist on the live table at all (``schema.original_columns_present``;
a board that has not run the migration cannot have retained anything, and the
survey says so instead of reporting 55 ``absent`` as if the writer had failed),
the root, and the bytes on disk. A board with no documents is ``unmeasurable``.

A library and a CLI::

    python -m tools.document_intelligence.originals --survey [--json] [--verify]
    python -m tools.document_intelligence.originals --root
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Where retained originals land. Overridable so a test never writes under data/.
ORIGINALS_DIR_ENV = "ICDEV_DIC_ORIGINALS_DIR"

#: Retention kill switch. Anything but "0" / "false" / "no" / "off" keeps it on.
RETAIN_ENV = "ICDEV_DIC_RETAIN_ORIGINALS"

TABLE = "dic_documents"

#: The columns this feature adds to dic_documents. Declaration order. The
#: migration's NEW_COLUMNS and the ingest DDL are pinned to this tuple by test.
ORIGINAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("original_path", "TEXT"),
    ("original_sha256", "TEXT"),
    ("original_retained_at", "TEXT"),
)

VERDICTS: tuple[str, ...] = (
    "retained",
    "retained_missing",
    "retained_mismatch",
    "source_on_disk",
    "absent",
    "no_source",
)

#: The verdicts that mean "an original a re-extraction could read" — for a
#: consumer that only needs a readable file, not a retained one.
READABLE_VERDICTS: frozenset[str] = frozenset({"retained", "source_on_disk"})

_CHUNK = 1 << 20


def originals_dir() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get(ORIGINALS_DIR_ENV)
        or pathlib.Path("data") / "document_intelligence" / "originals"
    )


def retention_enabled() -> bool:
    raw = (os.environ.get(RETAIN_ENV) or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_of(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class RetainedOriginal:
    path: str
    sha256: str
    byte_size: int
    deduplicated: bool
    retained_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def retained_path_for(sha256: str, suffix: str = "", root: pathlib.Path | None = None) -> pathlib.Path:
    """The content address: ``<root>/<sha[:2]>/<sha><suffix>``."""
    root = root or originals_dir()
    suffix = (suffix or "").lower()
    return root / sha256[:2] / f"{sha256}{suffix}"


def retain_original(src: str | os.PathLike, filename: str = "", *, root: pathlib.Path | None = None) -> RetainedOriginal:
    """Copy ``src`` into the content-addressed store. Raises on failure.

    Hashes WHILE copying (one read of the source), lands under a temp name in
    the final directory, then ``os.replace``s onto the content address — so a
    concurrent upload of the same bytes and a crash mid-copy both leave either
    the complete file or nothing. If the address already holds a file the copy
    is discarded and ``deduplicated`` is True.
    """
    src_path = pathlib.Path(src)
    root = root or originals_dir()
    # HOST-INDEPENDENT (dwr-fid-03) -- see ingest_guard.safe_suffix.
    from tools.document_intelligence.ingest_guard import safe_suffix

    suffix = safe_suffix(filename or src_path.name)
    root.mkdir(parents=True, exist_ok=True)

    h = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(prefix=".partial-", dir=str(root))
    try:
        with os.fdopen(fd, "wb") as out, open(src_path, "rb") as inp:
            for block in iter(lambda: inp.read(_CHUNK), b""):
                h.update(block)
                size += len(block)
                out.write(block)
        digest = h.hexdigest()
        dest = retained_path_for(digest, suffix, root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            os.unlink(tmp_name)
            return RetainedOriginal(str(dest), digest, size, True, _now())
        os.replace(tmp_name, dest)
        return RetainedOriginal(str(dest), digest, size, False, _now())
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ── schema probes ─────────────────────────────────────────────────────────────

def _backend(conn) -> str:
    return getattr(conn, "_backend", "sqlite")


def table_columns(conn, table: str = TABLE) -> set[str]:
    """The column names the LIVE table carries — what an UPDATE may name."""
    backend = _backend(conn)
    if backend == "postgresql":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
        out = set()
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else None
            out.add(d["column_name"] if d and "column_name" in d else r[0])
        return out
    # pg-portability: sqlite-only path — PRAGMA has no information_schema
    # equivalent; the PostgreSQL branch above reads information_schema instead.
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    out = set()
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else None
        out.add(d["name"] if d and "name" in d else r[1])
    return out


def original_columns_present(conn) -> bool:
    try:
        present = table_columns(conn)
    except Exception as exc:  # noqa: BLE001 — an unreadable catalogue is "not present", and logged
        logger.warning("dic originals: could not read %s columns: %s", TABLE, exc)
        return False
    return all(name in present for name, _ in ORIGINAL_COLUMNS)


def record_original(conn, doc_id: str, retained: RetainedOriginal) -> dict[str, Any]:
    """Write the retention columns on the document row. Commits.

    Returns ``{"recorded": bool, "reason": str}`` — ``column_absent`` when the
    migration has not run here (the file IS retained; the row cannot say so),
    ``no_row`` when nothing matched ``doc_id``.
    """
    if not original_columns_present(conn):
        return {"recorded": False, "reason": "column_absent"}
    cur = conn.execute(
        f"UPDATE {TABLE} SET original_path = %s, original_sha256 = %s, "
        "original_retained_at = %s WHERE doc_id = %s",
        (retained.path, retained.sha256, retained.retained_at, doc_id),
    )
    conn.commit()
    n = getattr(cur, "rowcount", -1)
    if n == 0:
        return {"recorded": False, "reason": "no_row"}
    return {"recorded": True, "reason": "ok"}


# ── verdicts ──────────────────────────────────────────────────────────────────

def _get(row: Any, name: str) -> Any:
    if row is None:
        return None
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return getattr(row, name, None)


def original_verdict(row: Any, *, verify: bool = False) -> dict[str, Any]:
    """One verdict for one ``dic_documents`` row. See the module docstring."""
    original_path = _get(row, "original_path")
    original_sha = _get(row, "original_sha256")
    filepath = _get(row, "filepath")

    if original_path:
        p = pathlib.Path(str(original_path))
        if not p.exists():
            return {"status": "retained_missing", "path": str(p), "sha256": original_sha}
        if verify:
            actual = sha256_of(p)
            if original_sha and actual != original_sha:
                return {
                    "status": "retained_mismatch", "path": str(p),
                    "sha256": original_sha, "actual_sha256": actual,
                }
            return {"status": "retained", "path": str(p), "sha256": original_sha, "verified": True}
        return {"status": "retained", "path": str(p), "sha256": original_sha, "verified": False}

    if not filepath:
        return {"status": "no_source", "path": None, "sha256": None}
    fp = pathlib.Path(str(filepath))
    if fp.exists():
        return {"status": "source_on_disk", "path": str(fp), "sha256": None}
    return {"status": "absent", "path": str(fp), "sha256": None}


def annotate_rows(rows: list[dict], *, columns_present: bool | None = None) -> list[dict]:
    """Attach ``original_status`` (and ``original_detail``) to listing rows in place."""
    for r in rows:
        v = original_verdict(r)
        r["original_status"] = v["status"]
        r["original_detail"] = v
        if columns_present is not None:
            r["original_detail"]["columns_present"] = columns_present
    return rows


def select_columns(conn) -> tuple[str, bool]:
    """The extra columns a listing SELECT may name on THIS database, and
    whether the retention columns are present. ``filepath`` is always there."""
    present = original_columns_present(conn)
    extra = ", filepath"
    if present:
        extra += ", " + ", ".join(name for name, _ in ORIGINAL_COLUMNS)
    return extra, present


# ── survey ────────────────────────────────────────────────────────────────────

def _root_usage(root: pathlib.Path) -> dict[str, Any]:
    if not root.exists():
        return {"exists": False, "files_retained": 0, "bytes_retained": 0}
    files = 0
    size = 0
    for p in root.rglob("*"):
        if p.is_file() and not p.name.startswith(".partial-"):
            files += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return {"exists": True, "files_retained": files, "bytes_retained": size}


def survey(conn=None, *, verify: bool = False, limit_examples: int = 10) -> dict[str, Any]:
    """Every document's verdict, counted, with what was measured stated."""
    own = conn is None
    if own:
        from tools.db.storage import get_connection
        conn = get_connection()
    root = originals_dir()
    try:
        columns_present = original_columns_present(conn)
        extra, _ = select_columns(conn)
        rows = conn.execute(
            f"SELECT doc_id, filename, collection_id, created_at{extra} FROM {TABLE} ORDER BY created_at"
        ).fetchall()
    finally:
        if own:
            conn.close()

    counts: dict[str, int] = {v: 0 for v in VERDICTS}
    examples: dict[str, list[dict]] = {v: [] for v in VERDICTS}
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else r
        v = original_verdict(d, verify=verify)
        counts[v["status"]] += 1
        if len(examples[v["status"]]) < limit_examples:
            examples[v["status"]].append({
                "doc_id": _get(d, "doc_id"), "filename": _get(d, "filename"),
                "collection_id": _get(d, "collection_id"), "path": v.get("path"),
            })
    total = len(rows)
    findings = counts["absent"] + counts["retained_missing"] + counts["retained_mismatch"]
    return {
        "state": "unmeasurable" if total == 0 else ("clean" if findings == 0 else "findings"),
        "measured_at": _now(),
        "total_documents": total,
        "counts": counts,
        "findings": findings if total else None,
        "readable": sum(counts[v] for v in READABLE_VERDICTS) if total else None,
        "verified_sha256": verify,
        "schema": {
            "original_columns_present": columns_present,
            "columns": [name for name, _ in ORIGINAL_COLUMNS],
        },
        "retention": {
            "enabled": retention_enabled(),
            "root": str(root),
            "root_env": ORIGINALS_DIR_ENV,
            **_root_usage(root),
        },
        "examples": examples,
    }


def _human(report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"DIC originals survey — {report['measured_at']}")
    lines.append(
        f"state: {report['state']}   documents: {report['total_documents']}   "
        f"findings: {report['findings']}   sha256 verified: {report['verified_sha256']}"
    )
    sch = report["schema"]
    lines.append(
        "schema: retention columns "
        + ("PRESENT" if sch["original_columns_present"] else "ABSENT (migration 20260908003311 not applied here — nothing can have been retained)")
    )
    ret = report["retention"]
    lines.append(
        f"retention: {'on' if ret['enabled'] else 'OFF (' + RETAIN_ENV + ')'}   root: {ret['root']}"
        f"   files: {ret['files_retained']}   bytes: {ret['bytes_retained']}"
    )
    lines.append("")
    for v in VERDICTS:
        lines.append(f"  {v:<18} {report['counts'][v]}")
    lines.append("")
    for v in ("absent", "retained_missing", "retained_mismatch"):
        for ex in report["examples"][v]:
            lines.append(f"  {v}: {ex['doc_id']}  {ex['filename']}  ({ex['path']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DIC retained-original survey")
    ap.add_argument("--survey", action="store_true", help="verdict per document, counted")
    ap.add_argument("--verify", action="store_true", help="re-hash every retained file (slow)")
    ap.add_argument("--root", action="store_true", help="print the retention root and usage")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.root:
        root = originals_dir()
        out = {"root": str(root), "env": ORIGINALS_DIR_ENV, "enabled": retention_enabled(), **_root_usage(root)}
        print(json.dumps(out, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in out.items()))
        return 0
    if not args.survey:
        ap.print_help()
        return 0
    try:
        report = survey(verify=args.verify)
    except Exception as exc:  # noqa: BLE001 — exit 2: the survey could not be produced
        print(f"survey could not be produced: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, default=str) if args.json else _human(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
