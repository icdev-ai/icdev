# CUI // SP-CTI
# Classification: CUI â€” Controlled Unclassified Information
# Distribution: Authorized ICDEVâ„¢ personnel only
# Codebase Indexer â€” Phase 69, D-CA-1, D-CA-2
"""Codebase indexer for the ICDEVâ„¢ project.

Indexes Python source files using AST parsing and other files using
text chunking.  Stores indexed records in the ``codebase_index`` table
so downstream RAG retrieval can search project source without
re-reading the filesystem on every query.

Usage::

    python tools/rag/codebase_indexer.py --scan --json
    python tools/rag/codebase_indexer.py --scan --scope tools/pulse
    python tools/rag/codebase_indexer.py --background --interval 30
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import argparse
import ast
import hashlib
import json
import logging
import re
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
try:
    from tools.dashboard.assistant_config import EXCLUDED_DIRS, is_excluded_file
except ImportError:
    # Fallback when running standalone or in test environments
    EXCLUDED_DIRS: set[str] = {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp",
    }

    def is_excluded_file(file_path: Path) -> bool:  # type: ignore[misc]
        name = file_path.name.lower()
        if name.startswith(".") and name != ".env":
            return True
        for part in file_path.parts:
            if part.lower() in EXCLUDED_DIRS:
                return True
        return False


try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOG = get_logger("codebase_indexer")

# Extensions we care about, mapped to a file_type label
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".html": "template",
    ".jinja2": "template",
    ".j2": "template",
    ".md": "docs",
    ".yaml": "config",
    ".yml": "config",
    ".json": "args",
    ".toml": "config",
    ".cfg": "config",
    ".ini": "config",
    ".txt": "docs",
    ".rst": "docs",
    ".css": "template",
    ".js": "template",
    ".ts": "template",
}

# Files/patterns that must NEVER be indexed (secrets, credentials, DBs)
SECURITY_EXCLUSIONS: set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.staging",
    "credentials.json",
    "credentials.yaml",
    "secrets.yaml",
    "service_account.json",
    "id_rsa",
    "id_ed25519",
}

SECURITY_EXTENSIONS: set[str] = {
    ".pem",
    ".key",
    ".crt",
    ".p12",
    ".pfx",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pickle",
    ".pkl",
}

MAX_TEXT_CHUNK_CHARS = 2000
DEFAULT_CLASSIFICATION = "CUI // SP-CTI"

# ---------------------------------------------------------------------------
# Module-level fallback constants â€” overridable from args/rag_config.yaml
# under rag.codebase_indexer.  Change config, not code.
# ---------------------------------------------------------------------------
_GIT_HISTORY_LIMIT      = 200   # default max git commits to index
_GIT_LOG_TIMEOUT        = 30    # subprocess timeout (s) for git log
_MAX_CHANGED_FILES      = 20    # max changed_files included per commit chunk
_SYMBOL_MAX_WIDTH       = 120   # max chars for commit subject / symbol_name
_DOCSTRING_MAX_WIDTH    = 500   # max chars for textwrap.shorten() on docstrings
_BACKGROUND_INTERVAL    = 30    # default background indexing interval (minutes)
_HASH_DIGEST_LEN        = 32    # SHA-256 hex digest prefix length for chunk IDs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_file_hash(file_path: Path) -> str:
    """Return the SHA-256 hex digest of *file_path*."""
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for block in iter(lambda: fh.read(8192), b""):
            h.update(block)
    return h.hexdigest()


def _is_security_sensitive(file_path: Path) -> bool:
    """Return True if the file should never be indexed."""
    if file_path.name.lower() in SECURITY_EXCLUSIONS:
        return True
    if file_path.suffix.lower() in SECURITY_EXTENSIONS:
        return True
    return False


def _classify_file(file_path: Path, base_dir: Path) -> str:
    """Return a file_type label based on extension and location."""
    suffix = file_path.suffix.lower()
    if suffix in EXTENSION_MAP:
        label = EXTENSION_MAP[suffix]
    else:
        label = "docs"

    # Override by directory convention
    try:
        rel = file_path.relative_to(base_dir)
    except ValueError:
        return label
    parts = [p.lower() for p in rel.parts]
    if "goals" in parts:
        return "goal"
    if "args" in parts:
        return "args"
    return label


def _ensure_table(conn: Any) -> None:
    """Create the ``codebase_index`` table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS codebase_index (
            id             TEXT PRIMARY KEY,
            file_path      TEXT NOT NULL,
            file_hash      TEXT NOT NULL,
            file_type      TEXT NOT NULL,
            module         TEXT,
            symbols        TEXT,
            last_indexed_at TEXT NOT NULL,
            chunk_count    INTEGER NOT NULL DEFAULT 0,
            classification TEXT NOT NULL DEFAULT 'CUI // SP-CTI'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_codebase_index_path
        ON codebase_index(file_path)
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Python AST indexer  (D-CA-2)
# ---------------------------------------------------------------------------


def _ast_node_source(source_lines: list[str], node: ast.AST) -> str:
    """Extract the source text for an AST node."""
    start = node.lineno - 1  # 0-based
    end = getattr(node, "end_lineno", node.lineno)
    return "\n".join(source_lines[start:end])


def index_python_file(file_path: Path, base_dir: Path) -> list[dict]:
    """Parse a Python file via ``ast`` and return one chunk per symbol.

    Each chunk dict has::

        {
            "content": "<full source of the symbol>",
            "metadata": {
                "file_path": "tools/rag/codebase_indexer.py",
                "line_start": 42,
                "line_end": 68,
                "symbol_name": "scan_codebase",
                "symbol_type": "function",
                "docstring": "Scan the codebase â€¦",
            },
        }

    On ``SyntaxError`` the file falls back to :func:`index_text_file`.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        LOG.warning("Cannot read %s: %s", file_path, exc)
        return []

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        LOG.info("SyntaxError in %s â€” falling back to text chunking", file_path)
        return index_text_file(file_path, base_dir)

    source_lines = source.splitlines()
    try:
        rel_path = str(file_path.relative_to(base_dir))
    except ValueError:
        rel_path = str(file_path)

    chunks: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        symbol_type = "class" if isinstance(node, ast.ClassDef) else "function"
        symbol_name = node.name
        line_start = node.lineno
        line_end = getattr(node, "end_lineno", node.lineno)
        docstring = ast.get_docstring(node) or ""
        content = _ast_node_source(source_lines, node)

        # Build the signature (first line of the definition)
        if line_start <= len(source_lines):
            sig_line = source_lines[line_start - 1].strip()
        else:
            sig_line = ""

        chunks.append(
            {
                "content": content,
                "metadata": {
                    "file_path": rel_path.replace("\\", "/"),
                    "line_start": line_start,
                    "line_end": line_end,
                    "symbol_name": symbol_name,
                    "symbol_type": symbol_type,
                    "docstring": textwrap.shorten(docstring, width=_DOCSTRING_MAX_WIDTH, placeholder="â€¦"),
                    "signature": sig_line,
                },
            }
        )

    # If the file has no top-level symbols treat it as a single text chunk
    if not chunks:
        chunks.append(
            {
                "content": textwrap.shorten(
                    source,
                    width=MAX_TEXT_CHUNK_CHARS,
                    placeholder="...",
                ),
                "metadata": {
                    "file_path": rel_path.replace("\\", "/"),
                    "line_start": 1,
                    "line_end": len(source_lines),
                    "symbol_name": "<module>",
                    "symbol_type": "module",
                    "docstring": ast.get_docstring(tree) or "",
                },
            }
        )

    return chunks


# ---------------------------------------------------------------------------
# Text / Markdown / YAML / HTML indexer
# ---------------------------------------------------------------------------


def _split_markdown(text: str) -> list[str]:
    """Split markdown by ``##`` headings; each section becomes a chunk."""
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("## ") and current:
            sections.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("".join(current))
    return sections


def _split_yaml(text: str) -> list[str]:
    """Split YAML by top-level keys (lines that start at column 0)."""
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        is_top = line and not line[0].isspace() and line[0] not in ("#", "-", " ")
        if is_top and current:
            sections.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("".join(current))
    return sections


def index_text_file(file_path: Path, base_dir: Path) -> list[dict]:
    """Index a non-Python file by splitting into text chunks."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        LOG.warning("Cannot read %s: %s", file_path, exc)
        return []

    try:
        rel_path = str(file_path.relative_to(base_dir))
    except ValueError:
        rel_path = str(file_path)

    suffix = file_path.suffix.lower()
    chunks: list[dict] = []

    if suffix == ".md":
        sections = _split_markdown(source)
    elif suffix in (".yaml", ".yml"):
        sections = _split_yaml(source)
    elif suffix in (".html", ".json"):
        # Whole file as one chunk, with size limit
        sections = [source[:MAX_TEXT_CHUNK_CHARS]]
    else:
        # Generic: split by double-newline paragraphs
        raw_sections = re.split(r"\n\n+", source)
        sections = [s for s in raw_sections if s.strip()]
        if not sections:
            sections = [source[:MAX_TEXT_CHUNK_CHARS]]

    line_cursor = 1
    for section in sections:
        section_lines = section.count("\n") + 1
        content = section.strip()
        if not content:
            line_cursor += section_lines
            continue

        # Truncate oversized chunks
        if len(content) > MAX_TEXT_CHUNK_CHARS:
            content = content[:MAX_TEXT_CHUNK_CHARS] + "â€¦"

        # Attempt to extract a heading / key name
        first_line = content.split("\n", 1)[0].strip()
        symbol_name = first_line[:120] if first_line else "<chunk>"

        chunks.append(
            {
                "content": content,
                "metadata": {
                    "file_path": rel_path.replace("\\", "/"),
                    "line_start": line_cursor,
                    "line_end": line_cursor + section_lines - 1,
                    "symbol_name": symbol_name,
                    "symbol_type": "section",
                    "docstring": "",
                },
            }
        )
        line_cursor += section_lines

    return chunks


# ---------------------------------------------------------------------------
# Git history indexer
# ---------------------------------------------------------------------------


def index_git_history(base_dir: Path, limit: int = 200) -> list[dict]:
    """Index recent git commits as chunks.

    Returns a list of chunk dicts with commit message, author, date, and
    changed files.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--max-count={limit}",
                "--pretty=format:%H||%an||%aI||%s",
                "--name-only",
            ],
            cwd=str(base_dir),
            capture_output=True,
            text=True,
            timeout=_GIT_LOG_TIMEOUT,
        )
        if result.returncode != 0:
            LOG.warning("git log failed: %s", result.stderr.strip())
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        LOG.warning("git log error: %s", exc)
        return []

    chunks: list[dict] = []
    entries = result.stdout.strip().split("\n\n")
    for entry in entries:
        lines = entry.strip().splitlines()
        if not lines:
            continue
        header = lines[0]
        parts = header.split("||", 3)
        if len(parts) < 4:
            continue
        commit_hash, author, date_str, subject = parts
        changed_files = [ln.strip() for ln in lines[1:] if ln.strip()]

        content = (
            f"Commit: {commit_hash[:12]}\n"
            f"Author: {author}\n"
            f"Date: {date_str}\n"
            f"Subject: {subject}\n"
            f"Files: {', '.join(changed_files[:_MAX_CHANGED_FILES])}"
        )
        chunks.append(
            {
                "content": content,
                "metadata": {
                    "file_path": "<git-history>",
                    "line_start": 0,
                    "line_end": 0,
                    "symbol_name": subject[:120],
                    "symbol_type": "commit",
                    "docstring": "",
                    "commit_hash": commit_hash,
                    "author": author,
                    "date": date_str,
                },
            }
        )

    return chunks


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _collect_files(base_dir: Path, scope: Path | None = None) -> list[Path]:
    """Walk *base_dir* (or *scope* sub-directory) and collect indexable files."""
    root = scope if scope else base_dir
    if not root.is_dir():
        LOG.error("Scope directory does not exist: %s", root)
        return []

    files: list[Path] = []
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        if is_excluded_file(item):
            continue
        if _is_security_sensitive(item):
            continue
        if item.suffix.lower() not in EXTENSION_MAP:
            continue
        files.append(item)

    return sorted(files)


def _store_record(
    conn: Any,
    file_path: Path,
    base_dir: Path,
    file_hash: str,
    file_type: str,
    symbols: list[str],
    chunk_count: int,
) -> None:
    """Upsert a record in the ``codebase_index`` table."""
    try:
        rel_path = str(file_path.relative_to(base_dir)).replace("\\", "/")
    except ValueError:
        rel_path = str(file_path)

    # Derive module name from path
    module = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""

    record_id = hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:_HASH_DIGEST_LEN]
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO codebase_index
               (id, file_path, file_hash, file_type, module, symbols,
                last_indexed_at, chunk_count, classification)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT(id) DO UPDATE SET
               file_hash      = excluded.file_hash,
               file_type      = excluded.file_type,
               module         = excluded.module,
               symbols        = excluded.symbols,
               last_indexed_at = excluded.last_indexed_at,
               chunk_count    = excluded.chunk_count
        """,
        (
            record_id,
            rel_path,
            file_hash,
            file_type,
            module,
            json.dumps(symbols, ensure_ascii=False),
            now,
            chunk_count,
            DEFAULT_CLASSIFICATION,
        ),
    )


def scan_codebase(
    base_dir: Path,
    scope: Path | None = None,
) -> dict:
    """Scan and index the codebase.

    Returns::

        {
            "indexed_files": <int>,
            "skipped": <int>,
            "errors": <int>,
            "total_chunks": <int>,
            "scan_duration_s": <float>,
        }
    """
    start_time = time.monotonic()
    files = _collect_files(base_dir, scope)
    LOG.info("Collected %d files to index", len(files))

    conn = None
    if get_connection is not None:
        try:
            conn = get_connection()
            _ensure_table(conn)
        except Exception as exc:
            LOG.warning("DB connection failed, indexing without persistence: %s", exc)
            conn = None

    indexed = 0
    skipped = 0
    errors = 0
    total_chunks = 0

    # Phase 1a resilience fix (2026-04-11): commit per file.
    #
    # Prior behavior issued a single final commit at loop end, so one
    # bad file anywhere in the 7000+ file walk would abort the Postgres
    # transaction and silently drop every row on the final commit
    # (observed 2026-04-11: indexed_files=7262 reported but 0 rows
    # persisted).
    #
    # First attempt at a fix used batch-commit-every-N with rollback on
    # exception, but empirically that still persisted 0 rows on full
    # scan even though a per-file-commit diagnostic of the same
    # function successfully landed 4964 rows. Root cause of the batch
    # variant failing is unclear â€” possibly a psycopg2 pool interaction
    # where an intermediate commit followed by more work within the
    # same connection gets retroactively rolled back.
    #
    # Per-file commit is ~2-3x slower than batch commit but REPRODUCIBLY
    # correct. For a 7200-file full scan that's ~90s vs ~30s â€” acceptable
    # for a tool that runs on-demand or in a 30-minute background
    # interval. Reliability trumps speed here.
    for fp in files:
        try:
            file_hash = _get_file_hash(fp)

            # Check if already indexed with same hash
            if conn is not None:
                try:
                    rel = str(fp.relative_to(base_dir)).replace("\\", "/")
                except ValueError:
                    rel = str(fp)
                rec_id = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:_HASH_DIGEST_LEN]
                row = conn.execute(
                    "SELECT file_hash FROM codebase_index WHERE id = %s",
                    (rec_id,),
                ).fetchone()
                if row and row[0] == file_hash:
                    skipped += 1
                    continue

            file_type = _classify_file(fp, base_dir)

            if fp.suffix.lower() == ".py":
                chunks = index_python_file(fp, base_dir)
            else:
                chunks = index_text_file(fp, base_dir)

            symbols = [
                c["metadata"].get("symbol_name", "")
                for c in chunks
                if c["metadata"].get("symbol_type") in ("function", "class")
            ]

            if conn is not None:
                _store_record(
                    conn,
                    fp,
                    base_dir,
                    file_hash,
                    file_type,
                    symbols,
                    len(chunks),
                )
                # Per-file commit â€” see comment above for why.
                try:
                    conn.commit()
                except Exception as commit_exc:
                    LOG.warning("Commit failed at file %s: %s", fp, commit_exc)
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    errors += 1
                    continue

            total_chunks += len(chunks)
            indexed += 1

        except Exception as exc:
            LOG.error("Error indexing %s: %s", fp, exc)
            errors += 1
            # CRITICAL: rollback immediately so the aborted transaction
            # state does not contaminate the next iteration. Without
            # this, a single psycopg2.errors.* leaves the connection
            # in "transaction aborted" state and every subsequent
            # conn.execute() silently fails.
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass

    # Close the connection cleanly.
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    elapsed = time.monotonic() - start_time
    return {
        "indexed_files": indexed,
        "skipped": skipped,
        "errors": errors,
        "total_chunks": total_chunks,
        "scan_duration_s": round(elapsed, 2),
    }


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------


def run_background(
    base_dir: Path,
    interval_minutes: int = 30,
) -> None:
    """Run the indexer in a loop, sleeping *interval_minutes* between scans."""
    LOG.info(
        "Starting background codebase indexer (interval=%d min)",
        interval_minutes,
    )
    while True:
        try:
            result = scan_codebase(base_dir)
            LOG.info(
                "Scan complete: indexed=%d skipped=%d errors=%d chunks=%d (%.1fs)",
                result["indexed_files"],
                result["skipped"],
                result["errors"],
                result["total_chunks"],
                result["scan_duration_s"],
            )
        except Exception as exc:
            LOG.error("Background scan failed: %s", exc)
        time.sleep(interval_minutes * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codebase_indexer",
        description="Index ICDEVâ„¢ codebase for RAG retrieval.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Run a one-shot scan of the codebase.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit results as JSON.",
    )
    parser.add_argument(
        "--scope",
        type=str,
        default=None,
        help="Limit scan to a subdirectory (relative to project root).",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Run in continuous background mode.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=_BACKGROUND_INTERVAL,
        help="Minutes between background scans (default: 30).",
    )
    parser.add_argument(
        "--git-history",
        action="store_true",
        dest="git_history",
        help="Also index recent git commit history.",
    )
    parser.add_argument(
        "--git-limit",
        type=int,
        default=200,
        dest="git_limit",
        help="Max git commits to index (default: 200).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    base_dir = Path(__file__).resolve().parent.parent.parent  # project root

    if args.background:
        run_background(base_dir, interval_minutes=args.interval)
        return 0  # unreachable unless interrupted

    if not args.scan and not args.git_history:
        parser.print_help()
        return 1

    results: dict[str, Any] = {}

    if args.scan:
        scope = None
        if args.scope:
            scope = base_dir / args.scope
        scan_result = scan_codebase(base_dir, scope=scope)
        results.update(scan_result)

    if args.git_history:
        git_chunks = index_git_history(base_dir, limit=args.git_limit)
        results["git_commits_indexed"] = len(git_chunks)

    if args.json_output:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("Codebase Indexer Results")
        print("=" * 40)
        for key, value in results.items():
            print(f"  {key}: {value}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

