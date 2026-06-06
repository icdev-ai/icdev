# CUI // SP-CTI
"""Done-artifact auditor — verify that 'done' Kanban tasks actually shipped.

A post-process review for every batch and every project-in-flight. It exists
because `status='done'` is *not* evidence an artifact exists: autonomous
sessions mark tasks done on stale per-task `kanban/<id>` branches that never
merge, point branches at unrelated commits (zero artifact), or build against
divergent schemas. The board silently lies until someone asks "is X built?".

For each `done` task it parses the artifact file paths (and `Verify:` commands)
claimed in the task description, then checks those paths exist on the working
tree (optionally: tracked by git on the current branch). Tasks whose claimed
artifacts are missing are flagged. It NEVER mutates task status — the live
scheduler would re-dispatch and recreate the divergence; this only reports.

Usage:
    python tools/kanban/done_artifact_auditor.py --all [--json] [--gate] [--git]
    python tools/kanban/done_artifact_auditor.py --project ace [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# Top-level directories under which a "claimed artifact" path is meaningful.
SOURCE_DIRS = (
    "tools",
    "icdev",
    "args",
    "tests",
    "goals",
    "context",
    "docs",
    "frontend",
    "features",
    "hardprompts",
    ".claude",
)

# Real source-file extensions. The allowlist is what separates a file path from
# a dotted module.function reference: `tools/aiify/scorer.score_opportunity` must
# NOT be read as a file just because `.score_` looks extension-shaped.
_FILE_EXTS = (
    "py", "md", "yaml", "yml", "html", "htm", "json", "js", "ts", "tsx", "jsx",
    "sql", "txt", "sh", "css", "toml", "ini", "cfg", "env", "csv", "xml", "rst",
)

# A concrete repo-relative file path: rooted in a known source dir, one or more
# path segments, a filename ending in an allowlisted extension. The char class
# `[\w.\-]` excludes '<' '>' so placeholders like tools/manifest/<topic>.md never
# match; the non-greedy filename + extension allowlist + trailing lookahead stop
# `module.function(...)` references from being mistaken for files.
_PATH_RE = re.compile(
    r"(?<![\w./-])(?:" + "|".join(SOURCE_DIRS) + r")"
    r"/(?:[\w.\-]+/)*[\w.\-]+?\.(?:" + "|".join(_FILE_EXTS) + r")(?![\w])"
)

# Text following a "Verify:" marker, up to the next sentence-ish boundary.
_VERIFY_RE = re.compile(r"Verify:\s*(.+?)(?:\s+Expected:|\.\s|$)", re.IGNORECASE | re.DOTALL)

_VERDICT_OK = "ok"
_VERDICT_MISSING = "missing_artifacts"
_VERDICT_NONE = "no_claims"


def extract_artifact_paths(description: str) -> list[str]:
    """Return concrete repo-relative file paths claimed in a task description."""
    if not description:
        return []
    seen: dict[str, None] = {}
    for m in _PATH_RE.finditer(description):
        path = m.group(0).rstrip(".,;:)]}'\"")
        if path not in seen:
            seen[path] = None
    return list(seen)


def extract_verify_commands(description: str) -> list[str]:
    """Return the verification snippets a task description documents."""
    if not description:
        return []
    out: list[str] = []
    for m in _VERIFY_RE.finditer(description):
        snippet = m.group(1).strip()
        if snippet:
            out.append(snippet)
    return out


def _is_tracked(repo_root: Path, rel_path: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel_path],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def audit_task(task: dict, repo_root: Path, *, use_git: bool = False) -> dict:
    """Audit a single task dict {id, status, description, project_id?}."""
    description = task.get("description") or ""
    claimed = extract_artifact_paths(description)
    missing: list[str] = []
    untracked: list[str] = []
    for rel in claimed:
        exists = (repo_root / rel).exists()
        if not exists:
            missing.append(rel)
        elif use_git and not _is_tracked(repo_root, rel):
            untracked.append(rel)

    if not claimed:
        verdict = _VERDICT_NONE
    elif missing:
        verdict = _VERDICT_MISSING
    else:
        verdict = _VERDICT_OK

    return {
        "task_id": task.get("id"),
        "project_id": task.get("project_id"),
        "status": task.get("status"),
        "claimed": claimed,
        "missing": missing,
        "untracked": untracked,
        "verify_commands": extract_verify_commands(description),
        "verdict": verdict,
    }


def audit_tasks(
    tasks: list[dict],
    repo_root: Path,
    *,
    only_status: str | None = None,
    use_git: bool = False,
) -> list[dict]:
    """Audit a list of task dicts, optionally filtered to one status."""
    selected = [t for t in tasks if only_status is None or t.get("status") == only_status]
    return [audit_task(t, repo_root, use_git=use_git) for t in selected]


def summarize(results: list[dict]) -> dict:
    """Roll up verdict counts."""
    summary = {_VERDICT_OK: 0, _VERDICT_MISSING: 0, _VERDICT_NONE: 0, "total": len(results)}
    for r in results:
        summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
    return summary


# ---------------------------------------------------------------------------
# DB layer (thin)
# ---------------------------------------------------------------------------


def _fetch_done_tasks(conn, project_id: str | None) -> list[dict]:
    cur = conn.cursor()
    if project_id:
        cur.execute(
            "SELECT id, status, description, project_id FROM kanban_tasks "
            "WHERE status = 'done' AND project_id = %s ORDER BY id",
            (project_id,),
        )
    else:
        cur.execute(
            "SELECT id, status, description, project_id FROM kanban_tasks "
            "WHERE status = 'done' ORDER BY project_id, id"
        )
    return [
        {"id": r[0], "status": r[1], "description": r[2], "project_id": r[3]}
        for r in cur.fetchall()
    ]


def audit_project(project_id: str, conn, repo_root: Path, *, use_git: bool = False) -> list[dict]:
    tasks = _fetch_done_tasks(conn, project_id)
    return audit_tasks(tasks, repo_root, use_git=use_git)


def audit_all(conn, repo_root: Path, *, use_git: bool = False) -> list[dict]:
    tasks = _fetch_done_tasks(conn, None)
    return audit_tasks(tasks, repo_root, use_git=use_git)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_markdown(results: list[dict], summary: dict) -> str:
    lines = [
        "# Done-Artifact Audit",
        "",
        f"- total done tasks audited: **{summary['total']}**",
        f"- [OK]      ok: {summary[_VERDICT_OK]}",
        f"- [MISSING] missing artifacts: {summary[_VERDICT_MISSING]}",
        f"- [NONE]    no parseable claims: {summary[_VERDICT_NONE]}",
        "",
    ]
    flagged = [r for r in results if r["verdict"] == _VERDICT_MISSING]
    if flagged:
        lines.append("## Tasks flagged: 'done' but artifacts missing on working tree")
        lines.append("")
        for r in flagged:
            lines.append(f"### {r['task_id']}  ({r.get('project_id') or '-'})")
            for p in r["missing"]:
                lines.append(f"  - [MISSING] `{p}`")
            lines.append("")
    untracked = [r for r in results if r["untracked"]]
    if untracked:
        lines.append("## Exists on disk but NOT tracked on current branch")
        for r in untracked:
            for p in r["untracked"]:
                lines.append(f"  - [UNTRACKED] {r['task_id']}: `{p}`")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit that 'done' tasks shipped their artifacts.")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="Audit every done task across all projects.")
    scope.add_argument("--project", help="Audit done tasks for one project_id.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--git", action="store_true", help="Also flag artifacts not tracked on current branch.")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit non-zero if any done task has missing artifacts (CI gate).",
    )
    args = parser.parse_args(argv)

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        if args.all:
            results = audit_all(conn, _BASE, use_git=args.git)
        else:
            results = audit_project(args.project, conn, _BASE, use_git=args.git)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    summary = summarize(results)

    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
    else:
        print(_render_markdown(results, summary))

    if args.gate and summary[_VERDICT_MISSING] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
