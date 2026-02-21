# CUI // SP-CTI
"""Git worktree manager — task-isolated parallel development with sparse checkout.

Decision D32: Git worktrees with sparse checkout for task isolation.
Zero-conflict parallelism, per-task branches, classification markers.

Usage:
    python tools/ci/modules/worktree.py --create --task-id test-123 --target-dir src/
    python tools/ci/modules/worktree.py --list
    python tools/ci/modules/worktree.py --cleanup --worktree-name icdev-test-123
    python tools/ci/modules/worktree.py --status --worktree-name icdev-test-123
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
TREES_DIR = BASE_DIR / "trees"


@dataclass
class WorktreeInfo:
    """Information about a git worktree."""
    worktree_name: str
    task_id: str
    branch_name: str
    target_directory: str
    path: str
    classification: str = "CUI"
    status: str = "active"
    agent_id: Optional[str] = None
    issue_number: Optional[int] = None
    created_at: Optional[str] = None


def _run_git(args: list, cwd: str = None) -> subprocess.CompletedProcess:
    """Run a git command safely."""
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or str(BASE_DIR),
        timeout=60,
    )


def _log_to_db(worktree: WorktreeInfo, status: str):
    """Log worktree state to database."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO ci_worktrees
               (worktree_name, task_id, issue_number, branch_name,
                target_directory, classification, status, agent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(worktree_name) DO UPDATE SET
                   status = excluded.status,
                   completed_at = CASE WHEN excluded.status IN ('completed', 'failed', 'cleaned')
                       THEN datetime('now') ELSE completed_at END""",
            (
                worktree.worktree_name,
                worktree.task_id,
                worktree.issue_number,
                worktree.branch_name,
                worktree.target_directory,
                worktree.classification,
                status,
                worktree.agent_id,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: DB log failed: {e}", file=sys.stderr)


def create_worktree(task_id: str, target_dir: str, classification: str = "CUI",
                    issue_number: int = None, agent_id: str = None) -> WorktreeInfo:
    """Create an isolated git worktree with sparse checkout.

    Pattern (from TAC-8, adapted for ICDEV):
        git worktree add --no-checkout trees/<task_id> -b icdev-<task_id>
        git -C trees/<task_id> sparse-checkout init --cone
        git -C trees/<task_id> sparse-checkout set <target_directory>
        git -C trees/<task_id> checkout
        echo "CUI // SP-CTI" > trees/<task_id>/.classification
    """
    worktree_name = f"icdev-{task_id}"
    branch_name = f"icdev-{task_id}"
    worktree_path = TREES_DIR / task_id

    TREES_DIR.mkdir(parents=True, exist_ok=True)

    # Create worktree with new branch, no checkout initially
    result = _run_git(["worktree", "add", "--no-checkout", str(worktree_path), "-b", branch_name])
    if result.returncode != 0:
        # Branch might already exist, try without -b
        result = _run_git(["worktree", "add", "--no-checkout", str(worktree_path)])
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create worktree: {result.stderr}")

    # Initialize sparse checkout
    result = _run_git(["sparse-checkout", "init", "--cone"], cwd=str(worktree_path))
    if result.returncode != 0:
        raise RuntimeError(f"Failed to init sparse-checkout: {result.stderr}")

    # Set sparse checkout to target directory
    result = _run_git(["sparse-checkout", "set", target_dir], cwd=str(worktree_path))
    if result.returncode != 0:
        raise RuntimeError(f"Failed to set sparse-checkout: {result.stderr}")

    # Checkout the files
    result = _run_git(["checkout"], cwd=str(worktree_path))
    if result.returncode != 0:
        raise RuntimeError(f"Failed to checkout: {result.stderr}")

    # Write classification marker
    classification_file = worktree_path / ".classification"
    classification_file.write_text(f"{classification} // SP-CTI\n")

    # Write agent identity file
    agent_file = worktree_path / ".icdev-agent"
    agent_file.write_text(json.dumps({
        "task_id": task_id,
        "worktree_name": worktree_name,
        "agent_id": agent_id,
        "classification": classification,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))

    info = WorktreeInfo(
        worktree_name=worktree_name,
        task_id=task_id,
        branch_name=branch_name,
        target_directory=target_dir,
        path=str(worktree_path),
        classification=classification,
        status="active",
        agent_id=agent_id,
        issue_number=issue_number,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    _log_to_db(info, "active")
    return info


def list_worktrees() -> List[WorktreeInfo]:
    """List all ICDEV git worktrees."""
    result = _run_git(["worktree", "list", "--porcelain"])
    worktrees = []

    if result.returncode != 0:
        return worktrees

    current = {}
    for line in result.stdout.strip().split("\n"):
        if line.startswith("worktree "):
            if current and "trees" in current.get("path", ""):
                # Parse task_id from path
                path = Path(current["path"])
                task_id = path.name
                worktrees.append(WorktreeInfo(
                    worktree_name=f"icdev-{task_id}",
                    task_id=task_id,
                    branch_name=current.get("branch", "").replace("refs/heads/", ""),
                    target_directory="",
                    path=current["path"],
                    status="active",
                ))
            current = {"path": line[9:]}
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line == "":
            pass

    # Handle last entry
    if current and "trees" in current.get("path", ""):
        path = Path(current["path"])
        task_id = path.name
        worktrees.append(WorktreeInfo(
            worktree_name=f"icdev-{task_id}",
            task_id=task_id,
            branch_name=current.get("branch", "").replace("refs/heads/", ""),
            target_directory="",
            path=current["path"],
            status="active",
        ))

    return worktrees


def cleanup_worktree(worktree_name: str) -> bool:
    """Remove a worktree and its branch."""
    task_id = worktree_name.replace("icdev-", "")
    worktree_path = TREES_DIR / task_id

    # Remove worktree
    result = _run_git(["worktree", "remove", str(worktree_path), "--force"])
    if result.returncode != 0:
        print(f"Warning: worktree remove failed: {result.stderr}", file=sys.stderr)

    # Update DB
    info = WorktreeInfo(
        worktree_name=worktree_name,
        task_id=task_id,
        branch_name=f"icdev-{task_id}",
        target_directory="",
        path=str(worktree_path),
        status="cleaned",
    )
    _log_to_db(info, "cleaned")

    return result.returncode == 0


def get_worktree_status(worktree_name: str) -> dict:
    """Get detailed status of a worktree."""
    task_id = worktree_name.replace("icdev-", "")
    worktree_path = TREES_DIR / task_id

    status = {
        "worktree_name": worktree_name,
        "task_id": task_id,
        "path": str(worktree_path),
        "exists": worktree_path.exists(),
        "classification": "unknown",
    }

    if worktree_path.exists():
        # Read classification
        class_file = worktree_path / ".classification"
        if class_file.exists():
            status["classification"] = class_file.read_text().strip()

        # Read agent identity
        agent_file = worktree_path / ".icdev-agent"
        if agent_file.exists():
            status["agent"] = json.loads(agent_file.read_text())

        # Git status
        result = _run_git(["status", "--porcelain"], cwd=str(worktree_path))
        if result.returncode == 0:
            changes = [line for line in result.stdout.strip().split("\n") if line.strip()]
            status["changed_files"] = len(changes)
            status["changes"] = changes[:20]  # Limit output

        # Branch info
        result = _run_git(["branch", "--show-current"], cwd=str(worktree_path))
        if result.returncode == 0:
            status["branch"] = result.stdout.strip()

    # DB status
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM ci_worktrees WHERE worktree_name = ?",
            (worktree_name,),
        ).fetchone()
        if row:
            status["db_record"] = dict(row)
        conn.close()
    except Exception:
        pass

    return status


def main():
    parser = argparse.ArgumentParser(description="ICDEV Git Worktree Manager")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create a new worktree")
    group.add_argument("--list", action="store_true", help="List all worktrees")
    group.add_argument("--cleanup", action="store_true", help="Remove a worktree")
    group.add_argument("--status", action="store_true", help="Get worktree status")

    parser.add_argument("--task-id", help="Task identifier")
    parser.add_argument("--target-dir", help="Directory for sparse checkout")
    parser.add_argument("--worktree-name", help="Worktree name (for cleanup/status)")
    parser.add_argument("--classification", default="CUI", help="Classification level")
    parser.add_argument("--issue-number", type=int, help="GitLab issue number")
    parser.add_argument("--agent-id", help="Agent identifier")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.create:
        if not args.task_id or not args.target_dir:
            parser.error("--create requires --task-id and --target-dir")
        info = create_worktree(
            args.task_id, args.target_dir, args.classification,
            args.issue_number, args.agent_id,
        )
        if args.json:
            print(json.dumps(asdict(info), indent=2))
        else:
            print(f"Created worktree: {info.worktree_name}")
            print(f"  Path: {info.path}")
            print(f"  Branch: {info.branch_name}")
            print(f"  Target: {info.target_directory}")
            print(f"  Classification: {info.classification}")

    elif args.list:
        worktrees = list_worktrees()
        if args.json:
            print(json.dumps([asdict(w) for w in worktrees], indent=2))
        else:
            if not worktrees:
                print("No active worktrees")
            for w in worktrees:
                print(f"  {w.worktree_name}: {w.path} [{w.branch_name}]")

    elif args.cleanup:
        if not args.worktree_name:
            parser.error("--cleanup requires --worktree-name")
        success = cleanup_worktree(args.worktree_name)
        if args.json:
            print(json.dumps({"success": success, "worktree": args.worktree_name}))
        else:
            print(f"{'Cleaned' if success else 'Failed to clean'}: {args.worktree_name}")

    elif args.status:
        if not args.worktree_name:
            parser.error("--status requires --worktree-name")
        status = get_worktree_status(args.worktree_name)
        print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
