# CUI // SP-CTI
"""GitLab Task Board Monitor — polls issues for {{icdev: workflow}} tags.

Decision D33: GitLab tags replace Notion's {{prototype: type}} pattern.

Supported tags:
    {{icdev: intake}}     -> Start RICOAS intake session
    {{icdev: build}}      -> Run TDD build workflow
    {{icdev: sdlc}}       -> Full Plan->Build->Test->Review
    {{icdev: comply}}     -> Generate compliance artifacts
    {{icdev: secure}}     -> Run security scanning
    {{icdev: modernize}}  -> Legacy app modernization
    {{icdev: deploy}}     -> Generate IaC and deploy
    {{icdev: maintain}}   -> Maintenance audit

Usage:
    python tools/ci/triggers/gitlab_task_monitor.py
    python tools/ci/triggers/gitlab_task_monitor.py --interval 30
    python tools/ci/triggers/gitlab_task_monitor.py --dry-run
"""

import argparse
import json
import re
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Tag-to-workflow mapping (Decision D33)
ICDEV_TAG_MAP = {
    "intake": "icdev_intake",
    "build": "icdev_build",
    "sdlc": "icdev_sdlc",
    "comply": "icdev_comply",
    "secure": "icdev_secure",
    "modernize": "icdev_modernize",
    "deploy": "icdev_deploy",
    "maintain": "icdev_maintain",
    "test": "icdev_test",
    "review": "icdev_review",
    "plan": "icdev_plan",
    "plan_build": "icdev_plan_build",
}

TAG_PATTERN = re.compile(r"\{\{icdev:\s*(\w+)\}\}", re.IGNORECASE)

# Bot marker to prevent loops
BOT_MARKER = "[ICDEV-BOT]"


def extract_icdev_tag(text: str) -> Optional[str]:
    """Extract {{icdev: workflow}} tag from issue body or comment."""
    if not text:
        return None
    match = TAG_PATTERN.search(text)
    if match:
        tag = match.group(1).lower().strip()
        return tag if tag in ICDEV_TAG_MAP else None
    return None


def is_claimed(issue_iid: int) -> bool:
    """Check if an issue has already been claimed."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT id FROM gitlab_task_claims WHERE issue_iid = ? AND status NOT IN ('failed')",
            (issue_iid,),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def claim_issue(issue_iid: int, issue_url: str, icdev_tag: str, worktree_name: str = None) -> str:
    """Claim an issue for processing. Returns claim ID."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO gitlab_task_claims
               (issue_iid, issue_url, icdev_tag, worktree_name, status)
               VALUES (?, ?, ?, ?, 'claimed')""",
            (issue_iid, issue_url, icdev_tag, worktree_name),
        )
        conn.commit()
        claim_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return str(claim_id)
    except Exception as e:
        print(f"Warning: Failed to claim issue: {e}", file=sys.stderr)
        return ""


def update_claim(issue_iid: int, status: str, run_id: str = None):
    """Update claim status."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        if run_id:
            conn.execute(
                "UPDATE gitlab_task_claims SET status = ?, run_id = ?, completed_at = datetime('now') WHERE issue_iid = ? AND status = 'claimed'",
                (status, run_id, issue_iid),
            )
        else:
            conn.execute(
                "UPDATE gitlab_task_claims SET status = ? WHERE issue_iid = ? AND status IN ('claimed', 'processing')",
                (status, issue_iid),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def add_comment(issue_iid: int, message: str):
    """Add a comment to the GitLab issue."""
    full_message = f"{BOT_MARKER} {message}"
    try:
        subprocess.run(
            ["glab", "issue", "note", str(issue_iid), "-m", full_message],
            capture_output=True, text=True, timeout=30, cwd=str(BASE_DIR),
        )
    except Exception as e:
        print(f"Warning: Failed to comment on issue {issue_iid}: {e}", file=sys.stderr)


def add_label(issue_iid: int, label: str):
    """Add a label to the GitLab issue."""
    try:
        subprocess.run(
            ["glab", "issue", "update", str(issue_iid), "--label", label],
            capture_output=True, text=True, timeout=30, cwd=str(BASE_DIR),
        )
    except Exception as e:
        print(f"Warning: Failed to add label to issue {issue_iid}: {e}", file=sys.stderr)


def spawn_workflow(tag: str, issue_iid: int, issue_data: dict,
                   worktree_path: str = None, dry_run: bool = False) -> Optional[str]:
    """Route issue through EventEnvelope + EventRouter (D132).

    Replaces the old hardcoded workflow_map routing. EventRouter determines
    the correct workflow script from the envelope's workflow_command.
    """
    if dry_run:
        workflow_name = ICDEV_TAG_MAP.get(tag, "unknown")
        print(f"[DRY RUN] Would route: issue #{issue_iid} -> {workflow_name} via EventRouter")
        return "dry-run"

    try:
        from tools.ci.core.event_envelope import EventEnvelope
        from tools.ci.core.event_router import EventRouter

        # Normalize into EventEnvelope using GitLab tag factory
        envelope = EventEnvelope.from_gitlab_tag(issue_data, tag)

        # Route through central router
        router = EventRouter()
        result = router.route(envelope)

        action = result.get("action", "")
        if action == "launched":
            run_id = result.get("run_id", "")
            workflow = result.get("workflow", "")
            print(f"  EventRouter launched {workflow} (run_id: {run_id})")
            return run_id
        elif action == "queued":
            print(f"  EventRouter queued event ({result.get('reason', '')})")
            return "queued"
        else:
            print(f"  EventRouter ignored ({result.get('reason', '')})")
            return None

    except Exception as e:
        print(f"Error routing via EventRouter: {e}", file=sys.stderr)
        # Fallback to direct subprocess spawn
        workflow_name = ICDEV_TAG_MAP.get(tag)
        if not workflow_name:
            return None
        script_path = BASE_DIR / "tools" / "ci" / "workflows" / f"{workflow_name}.py"
        if not script_path.exists():
            print(f"Warning: Workflow script not found: {script_path}", file=sys.stderr)
            return None
        cwd = worktree_path or str(BASE_DIR)
        process = subprocess.Popen(
            [sys.executable, str(script_path), str(issue_iid)],
            cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return str(process.pid)


def list_open_issues(label: str = "icdev") -> list:
    """List open GitLab issues with the icdev label."""
    try:
        result = subprocess.run(
            ["glab", "issue", "list", "--label", label, "--state", "opened", "--output", "json"],
            capture_output=True, text=True, timeout=30, cwd=str(BASE_DIR),
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        print(f"Warning: Failed to list issues: {e}", file=sys.stderr)
    return []


def poll_gitlab_tasks(interval: int = 20, dry_run: bool = False):
    """Main polling loop for GitLab task board monitoring."""
    print(f"[GitLab Task Monitor] Starting (interval={interval}s, dry_run={dry_run})")
    print(f"[GitLab Task Monitor] Supported tags: {list(ICDEV_TAG_MAP.keys())}")

    running = True

    def handle_signal(sig, frame):
        nonlocal running
        print("\n[GitLab Task Monitor] Shutting down...")
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while running:
        try:
            issues = list_open_issues()

            for issue in issues:
                iid = issue.get("iid")
                title = issue.get("title", "")
                body = issue.get("description", "") or issue.get("body", "")
                web_url = issue.get("web_url", "")

                if not iid:
                    continue

                # Extract tag from body
                tag = extract_icdev_tag(body)
                if not tag:
                    # Check latest comment
                    # (simplified — in production would fetch comments)
                    continue

                # Skip if already claimed
                if is_claimed(iid):
                    continue

                print(f"[GitLab Task Monitor] Found: #{iid} '{title}' -> {{{{icdev: {tag}}}}}")

                # Claim the issue
                claim_id = claim_issue(iid, web_url, tag)

                # Add processing label
                add_label(iid, "icdev-processing")
                add_comment(iid, f"Claimed for `{tag}` workflow. Processing...")

                # Optionally create worktree for isolation
                worktree_path = None
                try:
                    from tools.ci.modules.worktree import create_worktree
                    worktree_info = create_worktree(
                        task_id=str(iid),
                        target_dir=".",
                        issue_number=iid,
                        agent_id="gitlab-task-monitor",
                    )
                    worktree_path = worktree_info.path
                    print(f"  Worktree created: {worktree_path}")
                except Exception as e:
                    print(f"  Worktree creation skipped: {e}", file=sys.stderr)

                # Spawn workflow via EventRouter (D132)
                pid = spawn_workflow(tag, iid, issue, worktree_path, dry_run)
                if pid:
                    update_claim(iid, "processing", run_id=pid)
                    print(f"  Workflow spawned (PID: {pid})")
                else:
                    update_claim(iid, "failed")
                    add_comment(iid, f"Failed to spawn `{tag}` workflow.")

        except Exception as e:
            print(f"[GitLab Task Monitor] Poll error: {e}", file=sys.stderr)

        # Wait for next poll
        for _ in range(interval):
            if not running:
                break
            time.sleep(1)

    print("[GitLab Task Monitor] Stopped.")


def main():
    parser = argparse.ArgumentParser(description="ICDEV GitLab Task Board Monitor")
    parser.add_argument("--interval", type=int, default=20, help="Poll interval in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually spawn workflows")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    args = parser.parse_args()

    if args.once:
        issues = list_open_issues()
        for issue in issues:
            body = issue.get("description", "") or issue.get("body", "")
            tag = extract_icdev_tag(body)
            if tag:
                print(f"Issue #{issue.get('iid')}: {issue.get('title')} -> {{{{icdev: {tag}}}}}")
    else:
        poll_gitlab_tasks(interval=args.interval, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
