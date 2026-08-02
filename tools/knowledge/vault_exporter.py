#!/usr/bin/env python3
# CUI // SP-CTI
"""Air-gap knowledge vault exporter (adapt-tol-01/02).

Exports ICDEV's context/, hardprompts/, and memory_entries DB rows to a
portable .zip vault for IL5/IL6 air-gap installations or git-native review.
Import path restores the vault to the same layout.

Usage:
    python tools/knowledge/vault_exporter.py --output .tmp/vault --json
    python tools/knowledge/vault_exporter.py --import-vault .tmp/vault/vault-2026-06-14.zip --json

Output zip layout:
    vault-YYYY-MM-DD/
      manifest.json          # metadata + file inventory
      context/               # full context/ tree (relative paths preserved)
      hardprompts/           # full hardprompts/ tree
      memory_entries.jsonl   # one JSON object per DB row
      knowledge_patterns.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from tools.logging.icdev_logger import get_logger
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONTEXT_DIR = BASE_DIR / "context"
HARDPROMPTS_DIR = BASE_DIR / "hardprompts"

_EXPORT_GLOBS = [
    ("context", CONTEXT_DIR, "**/*"),
    ("hardprompts", HARDPROMPTS_DIR, "**/*"),
]
_SKIP_SUFFIXES = frozenset({".pyc", ".pyo"})
_SKIP_DIRS = frozenset({"__pycache__", ".git", ".tmp"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_files(root: Path, glob: str) -> list[Path]:
    out: list[Path] = []
    for p in root.glob(glob):
        if not p.is_file():
            continue
        if p.suffix in _SKIP_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


def _load_memory_entries() -> list[dict]:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        rows = conn.execute(
            # `topics`, not `tags` — see the re-import below (swp-scan-01).
            "SELECT id, type, content, topics, metadata, classification, created_at "
            "FROM memory_entries ORDER BY created_at ASC"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.warning("vault_exporter: memory_entries load failed: %s", exc)
        return []


def _load_knowledge_patterns() -> list[dict]:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, pattern_type, pattern_name, description, content, "
            "confidence, classification, created_at FROM knowledge_patterns "
            "ORDER BY created_at ASC LIMIT 5000"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.warning("vault_exporter: knowledge_patterns load failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_vault(output_dir: Path, label: str | None = None) -> Path:
    """Export context/ + hardprompts/ + DB rows to a dated .zip vault.

    Args:
        output_dir: Directory to write the vault zip into.
        label: Optional label appended to zip name (e.g. branch name).

    Returns:
        Path to the created .zip file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    vault_name = f"vault-{today}" + (f"-{label}" if label else "")
    zip_path = output_dir / f"{vault_name}.zip"

    manifest: dict = {
        "vault_name": vault_name,
        "exported_at": _now_iso(),
        "icdev_version": "1.0",
        "classification": "CUI",
        "files": [],
        "memory_entry_count": 0,
        "knowledge_pattern_count": 0,
    }

    memory_entries = _load_memory_entries()
    knowledge_patterns = _load_knowledge_patterns()
    manifest["memory_entry_count"] = len(memory_entries)
    manifest["knowledge_pattern_count"] = len(knowledge_patterns)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # File tree exports
        for section, root, pattern in _EXPORT_GLOBS:
            if not root.exists():
                continue
            for fpath in _collect_files(root, pattern):
                rel = fpath.relative_to(BASE_DIR)
                arc = f"{vault_name}/{rel.as_posix()}"
                zf.write(fpath, arc)
                manifest["files"].append({
                    "arc": arc,
                    "sha256": _file_sha256(fpath),
                    "size": fpath.stat().st_size,
                })

        # DB rows
        memory_jsonl = "\n".join(json.dumps(r, default=str) for r in memory_entries)
        zf.writestr(f"{vault_name}/memory_entries.jsonl", memory_jsonl)

        patterns_json = json.dumps(knowledge_patterns, indent=2, default=str)
        zf.writestr(f"{vault_name}/knowledge_patterns.json", patterns_json)

        # Write manifest last
        zf.writestr(f"{vault_name}/manifest.json", json.dumps(manifest, indent=2))

    logger.info("vault_exporter: exported %d files to %s", len(manifest["files"]), zip_path)
    return zip_path


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_vault(vault_zip: Path, dry_run: bool = False) -> dict:
    """Restore a vault zip: extract files and re-insert memory rows.

    Args:
        vault_zip: Path to the .zip created by export_vault().
        dry_run: If True, validate and report without writing anything.

    Returns:
        Summary dict: {files_restored, memory_rows_inserted, errors}
    """
    if not vault_zip.exists():
        return {"error": f"File not found: {vault_zip}"}

    result: dict = {"files_restored": 0, "memory_rows_inserted": 0, "errors": []}

    try:
        zf_ctx = zipfile.ZipFile(vault_zip, "r")
    except zipfile.BadZipFile as exc:
        return {"error": f"Invalid zip file: {exc}", "files_restored": 0,
                "memory_rows_inserted": 0, "errors": [str(exc)]}

    with zf_ctx as zf:
        names = zf.namelist()
        vault_prefix = names[0].split("/")[0] + "/" if names else ""

        # Find and parse manifest
        manifest_name = f"{vault_prefix}manifest.json"
        if manifest_name not in names:
            result["errors"].append("manifest.json not found in vault")
            return result
        # Restore file tree
        for info in zf.infolist():
            arc = info.filename
            if arc == manifest_name or arc.endswith("manifest.json"):
                continue
            if arc.endswith("memory_entries.jsonl") or arc.endswith("knowledge_patterns.json"):
                continue
            if info.is_dir():
                continue

            rel = arc[len(vault_prefix):]  # strip vault-YYYY-MM-DD/ prefix
            dest = BASE_DIR / rel
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(arc))
            result["files_restored"] += 1

        # Re-insert memory rows
        mem_name = f"{vault_prefix}memory_entries.jsonl"
        if mem_name in names:
            jsonl = zf.read(mem_name).decode("utf-8")
            rows = [json.loads(line) for line in jsonl.splitlines() if line.strip()]
            if not dry_run and rows:
                try:
                    from tools.db.storage import get_connection
                    conn = get_connection()
                    for row in rows:
                        conn.execute(
                            # `tags` is not a column on memory_entries — the
                            # live column is `topics` (swp-scan-01). Every
                            # vault re-import raised UndefinedColumn and was
                            # swallowed by the except below, so no memory row
                            # was ever restored.
                            """INSERT INTO memory_entries
                               (id, type, content, topics, metadata, classification, created_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (id) DO NOTHING""",
                            (
                                row.get("id"), row.get("type"), row.get("content"),
                                row.get("topics", row.get("tags")), row.get("metadata"),
                                row.get("classification", "CUI"), row.get("created_at"),
                            ),
                        )
                    conn.commit()
                    conn.close()
                    result["memory_rows_inserted"] = len(rows)
                except Exception as exc:
                    result["errors"].append(f"memory_entries insert failed: {exc}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(description="ICDEV Knowledge Vault Exporter (adapt-tol-01/02)")
    parser.add_argument("--output", metavar="DIR", help="Export: write vault zip to this directory")
    parser.add_argument("--label", metavar="LABEL", help="Optional tag appended to vault name")
    parser.add_argument("--import-vault", metavar="ZIP", dest="import_vault",
                        help="Import: restore files + DB rows from vault zip")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if args.output:
        zip_path = export_vault(Path(args.output), label=args.label)
        result = {"status": "ok", "vault": str(zip_path)}
        if args.as_json:
            print(json.dumps(result))
        else:
            print(f"Vault exported to: {zip_path}")

    elif args.import_vault:
        summary = import_vault(Path(args.import_vault), dry_run=args.dry_run)
        if args.as_json:
            print(json.dumps(summary))
        else:
            print(f"Import complete: {summary}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()
