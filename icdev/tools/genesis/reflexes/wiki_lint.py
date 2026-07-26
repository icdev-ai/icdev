# CUI // SP-CTI
"""Genesis Wiki-Lint Reflex — Karpathy LLM Wiki health checks on memory files.

Runs four deterministic health checks over the ICDEV memory wiki
(project memory/ dir and Claude Code auto-memory):

  orphan       — .md files not linked from MEMORY.md
  broken_link  — [[slug]] references pointing to non-existent files
  stale        — files with old absolute dates + "current state" language
  overflow     — MEMORY.md approaching the 200-line Claude Code truncation limit

All findings are inserted as oracle_predictions rows (lens=internal_awareness)
and promoted to kanban suggested-cards via write_suggested_cards().

No LLM calls — fully deterministic, scanner-tier (risk_tier: green).
Schedule: daily 04:00 (configurable via args/genesis_config.yaml).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parents[3]
LENS_NAME = "internal_awareness"

# ── Patterns ─────────────────────────────────────────────────────────────────

# ISO dates (YYYY-MM-DD or YYYY-MM) embedded in text
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}(?:-\d{2})?)\b")

# "Current state" language — signals the file claims to reflect present reality
_CURRENT_RE = re.compile(
    r"\b(currently|active|ongoing|in[- ]progress|running|live|today|now active|"
    r"in.flight|open|pending|not yet|still broken|still missing)\b",
    re.IGNORECASE,
)

# [[slug]] wiki cross-links
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# MEMORY.md list entries: - [Title](file.md) — description
_MEMENTRY_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)\)")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Config ────────────────────────────────────────────────────────────────────


def _load_config() -> Dict[str, Any]:
    config_path = BASE_DIR / "args" / "genesis_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("reflexes", {}).get("wiki_lint", {})
    except Exception:
        return {}


def _memory_dirs(cfg: Dict[str, Any]) -> List[Path]:
    """Return list of memory directories to scan (project + Claude Code auto-memory)."""
    dirs: List[Path] = []

    # 1. ICDEV project memory
    project_mem = BASE_DIR / "memory"
    if project_mem.is_dir():
        dirs.append(project_mem)

    # 2. Claude Code auto-memory — configurable or derived by convention
    auto_mem_override = cfg.get("auto_memory_path")
    if auto_mem_override:
        p = Path(auto_mem_override)
        if p.is_dir():
            dirs.append(p)
    else:
        # Convention: USERPROFILE/.claude/projects/<project-slug>/memory
        # Slug = BASE_DIR path with path-separators and colons replaced by dashes
        userprofile = Path(os.environ.get("USERPROFILE", Path.home()))
        project_slug = (
            str(BASE_DIR)
            .replace("\\", "-")
            .replace("/", "-")
            .replace(":", "-")
            .lstrip("-")
        )
        auto_mem = userprofile / ".claude" / "projects" / project_slug / "memory"
        if auto_mem.is_dir():
            dirs.append(auto_mem)

    return dirs


# ── Index parser ──────────────────────────────────────────────────────────────


def _parse_memory_index(mem_dir: Path) -> Tuple[Dict[str, Path], int]:
    """Parse MEMORY.md → {filename: Path} and total line count."""
    index_file = mem_dir / "MEMORY.md"
    if not index_file.exists():
        return {}, 0
    text = index_file.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    linked: Dict[str, Path] = {}
    for m in _MEMENTRY_RE.finditer(text):
        fname = m.group(2)
        linked[fname] = mem_dir / fname
    return linked, len(lines)


# ── Lint checks ───────────────────────────────────────────────────────────────


def _scan_orphans(mem_dir: Path, linked: Dict[str, Path]) -> List[Dict]:
    """Files in mem_dir that are not linked from MEMORY.md."""
    findings = []
    skip = {"MEMORY.md", "__init__.md"}
    for f in sorted(mem_dir.glob("*.md")):
        if f.name in skip or f.name in linked:
            continue
        findings.append({
            "type": "orphan",
            "file": str(f),
            "slug": f.stem,
            "detail": f"File not linked from MEMORY.md: {f.name} (in {mem_dir.name}/)",
        })
    return findings


def _scan_broken_links(mem_dir: Path, linked: Dict[str, Path]) -> List[Dict]:
    """[[slug]] references in any memory file that point to non-existent files."""
    findings = []
    all_stems = {f.stem for f in mem_dir.glob("*.md")}
    all_names = {f.name for f in mem_dir.glob("*.md")}

    # Check both linked files and any other .md in the dir
    candidates = dict(linked)
    for f in mem_dir.glob("*.md"):
        candidates.setdefault(f.name, f)

    seen_refs: set = set()
    for fname, fpath in candidates.items():
        if not fpath.exists():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _LINK_RE.finditer(text):
            ref = m.group(1).strip()
            key = (fname, ref)
            if key in seen_refs:
                continue
            seen_refs.add(key)
            ref_md = ref if ref.endswith(".md") else ref + ".md"
            if ref_md not in all_names and ref not in all_stems:
                findings.append({
                    "type": "broken_link",
                    "file": str(fpath),
                    "slug": ref,
                    "detail": f"[[{ref}]] in {fname} references a non-existent file",
                })
    return findings


def _scan_stale(mem_dir: Path, linked: Dict[str, Path], staleness_days: int) -> List[Dict]:
    """Files whose newest embedded date is older than staleness_days but still
    use "current state" language (active, ongoing, currently, etc.)."""
    findings = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=staleness_days)

    for fname, fpath in linked.items():
        if not fpath.exists():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Skip if no current-state language
        if not _CURRENT_RE.search(text):
            continue

        # Parse all ISO dates from text
        dates: List[datetime] = []
        for m in _DATE_RE.finditer(text):
            ds = m.group(1)
            try:
                if len(ds) == 7:  # YYYY-MM → pad to first of month
                    ds += "-01"
                dt = datetime.fromisoformat(ds).replace(tzinfo=timezone.utc)
                dates.append(dt)
            except ValueError:
                continue

        if not dates:
            continue

        latest = max(dates)
        if latest < cutoff:
            age_days = (datetime.now(timezone.utc) - latest).days
            findings.append({
                "type": "stale",
                "file": str(fpath),
                "slug": fpath.stem,
                "detail": (
                    f"{fname}: newest date {latest.date().isoformat()} is {age_days}d old "
                    f"but file still uses current-state language"
                ),
                "age_days": age_days,
            })
    return findings


def _scan_overflow(mem_dir: Path, line_count: int, warn_lines: int) -> List[Dict]:
    """MEMORY.md approaching the 200-line Claude Code truncation ceiling."""
    if line_count < warn_lines:
        return []
    index_file = mem_dir / "MEMORY.md"
    return [{
        "type": "overflow",
        "file": str(index_file),
        "slug": "MEMORY",
        "detail": (
            f"MEMORY.md in {mem_dir.name}/ is {line_count} lines "
            f"(warn threshold: {warn_lines}); Claude Code truncates at 200 lines"
        ),
    }]


# ── Prediction emitter ────────────────────────────────────────────────────────

_SEVERITY = {"orphan": "info", "broken_link": "warning", "stale": "info", "overflow": "warning"}
_CONFIDENCE = {"orphan": 0.75, "broken_link": 0.85, "stale": 0.70, "overflow": 0.90}


def _pred_id(finding_type: str, slug: str) -> str:
    namespace = f"wiki_lint::{finding_type}::{slug}"
    return f"wl-{uuid.uuid5(uuid.NAMESPACE_DNS, namespace).hex[:12]}"


def _insert_prediction(conn: Any, finding: Dict[str, Any]) -> Optional[str]:
    """Insert one finding as an oracle_predictions row; returns pred_id or None on dup/error."""
    ftype = finding["type"]
    slug = finding.get("slug", "unknown")
    pred_id = _pred_id(ftype, slug)

    try:
        existing = conn.execute(
            "SELECT outcome FROM oracle_predictions WHERE id = %s", (pred_id,)
        ).fetchone()
        if existing:
            # Re-raise if already promoted/dismissed; re-insert if dismissed > 30 days ago
            outcome = (existing["outcome"] if hasattr(existing, "__getitem__") else existing[0]) or ""
            if outcome in ("pending", "") or outcome.startswith("promoted:"):
                return None  # already tracked
    except Exception:
        pass

    evidence = json.dumps({
        "finding_type": ftype,
        "file": finding.get("file", ""),
        "slug": slug,
        "detail": finding.get("detail", ""),
        "age_days": finding.get("age_days"),
    }, ensure_ascii=False)

    try:
        conn.execute(
            "INSERT INTO oracle_predictions "
            "(id, lens_id, lens_name, prediction_text, confidence, "
            " created_at, subject_type, subject_id, prediction_type, "
            " severity, horizon_days, evidence_json, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                pred_id,
                "internal_awareness",
                LENS_NAME,
                f"[wiki-lint:{ftype}] {finding['detail']}",
                _CONFIDENCE.get(ftype, 0.75),
                _utcnow(),
                "memory_file",
                f"memory::{slug}",
                f"wiki_lint::{ftype}",
                _SEVERITY.get(ftype, "info"),
                7,
                evidence,
                "CUI // SP-CTI",
            ),
        )
        conn.commit()
        return pred_id
    except Exception as exc:
        logger.warning("wiki_lint: prediction insert failed for %s/%s: %s", ftype, slug, exc)
        return None


# ── Main pipeline ─────────────────────────────────────────────────────────────


def run_wiki_lint() -> Dict[str, Any]:
    """Run all lint checks across all configured memory dirs. Returns finding summary."""
    from tools.db.storage import get_connection

    cfg = _load_config()
    staleness_days = int(cfg.get("staleness_days", 90))
    overflow_warn_lines = int(cfg.get("overflow_warn_lines", 160))

    all_findings: List[Dict] = []
    scanned_dirs: List[str] = []

    for mem_dir in _memory_dirs(cfg):
        linked, line_count = _parse_memory_index(mem_dir)
        scanned_dirs.append(str(mem_dir))
        logger.debug("wiki_lint: scanning %s (%d linked, %d lines)", mem_dir, len(linked), line_count)

        all_findings.extend(_scan_orphans(mem_dir, linked))
        all_findings.extend(_scan_broken_links(mem_dir, linked))
        all_findings.extend(_scan_stale(mem_dir, linked, staleness_days))
        all_findings.extend(_scan_overflow(mem_dir, line_count, overflow_warn_lines))

    # Dedup by (type, slug) across dirs
    seen: set = set()
    deduped: List[Dict] = []
    for f in all_findings:
        key = (f["type"], f.get("slug", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    # Emit oracle_predictions
    conn = get_connection()
    inserted = 0
    try:
        for finding in deduped:
            if _insert_prediction(conn, finding):
                inserted += 1
    finally:
        conn.close()

    by_type = {t: 0 for t in ("orphan", "broken_link", "stale", "overflow")}
    for f in deduped:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1

    return {
        "scanned_dirs": scanned_dirs,
        "total_findings": len(deduped),
        "inserted_predictions": inserted,
        "by_type": by_type,
        "findings": deduped,
    }


def run(args: dict, _ctx=None) -> Dict[str, Any]:
    """Daemon dispatch entry (rri).

    The daemon schedules reflexes by calling ``run(args, ctx)`` on the module.
    This file had ``run_reflex()`` — whose docstring even claimed it was "called
    by Genesis daemon" — but no ``run``, so the dispatcher marked wiki_lint
    undispatchable and skipped it every cycle. This thin shim connects the two.
    Caught by check_reflex_registry, which is exactly the class of gap it exists
    to find.
    """
    return run_reflex()


def run_reflex() -> Dict[str, Any]:
    """Wiki-lint reflex body. Dispatched via :func:`run`."""
    lint = run_wiki_lint()

    cards: Dict[str, Any] = {}
    if lint["inserted_predictions"] > 0:
        try:
            from tools.awareness.suggested_card_writer import write_suggested_cards
            cards = write_suggested_cards(min_confidence=0.70)
        except Exception as exc:
            cards = {"error": str(exc)}
            logger.warning("wiki_lint: write_suggested_cards failed: %s", exc)

    return {
        "reflex": "wiki_lint",
        "lint": lint,
        "cards": cards,
        "completed_at": _utcnow(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Genesis Wiki-Lint Reflex")
    parser.add_argument("--lint", action="store_true", help="Run lint checks only")
    parser.add_argument("--full", action="store_true", help="Full reflex (lint + emit cards)")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.full:
        result = run_reflex()
    elif args.lint:
        result = run_wiki_lint()
    else:
        parser.print_help()
        result = None

    if result is not None:
        print(json.dumps(result, indent=2, default=str))
