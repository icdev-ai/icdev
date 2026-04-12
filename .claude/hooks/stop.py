# [TEMPLATE: CUI // SP-CTI]
"""
Session stop hook — captures session completion event and saves chat transcript.

Features:
    - Stores stop event in DB via send_event
    - Captures full session transcript from .jsonl to .tmp/sessions/{session_id}/chat.json
    - Auto-commit & push when ICDEV_AUTO_COMMIT=true in .env

Always exits 0.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HOOKS_DIR.parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SESSION_DIR = PROJECT_ROOT / ".tmp" / "sessions"


def capture_transcript(session_id: str, input_data: dict):
    """Capture the session transcript from .jsonl to session directory."""
    transcript_path = input_data.get("transcript_path", "")
    if not transcript_path or not os.path.exists(transcript_path):
        return

    try:
        session_dir = SESSION_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        chat_data = []
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        chat_data.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # Skip malformed lines

        chat_file = session_dir / "chat.json"
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(chat_data, f, indent=2)

    except Exception:
        pass  # Never fail the stop hook


def main():
    try:
        input_data = json.load(sys.stdin)
        session_id = input_data.get("session_id", "")
        stop_reason = input_data.get("reason", "unknown")

        from send_event import get_session_id, store_event

        sid = session_id or get_session_id()
        store_event(
            session_id=sid,
            hook_type="stop",
            payload={
                "stop_reason": stop_reason,
                "session_id": sid,
                "transcript_captured": "transcript_path" in input_data,
            },
        )

        # Always attempt transcript capture
        if sid:
            capture_transcript(sid, input_data)

    except Exception:
        pass

    # Auto-commit & push if ICDEV_AUTO_COMMIT=true
    try:
        auto_commit_enabled = False
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ICDEV_AUTO_COMMIT="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
                        auto_commit_enabled = val == "true"
                        break

        # Also check environment variable directly
        if os.environ.get("ICDEV_AUTO_COMMIT", "").lower() == "true":
            auto_commit_enabled = True

        if auto_commit_enabled:
            _auto_commit_and_push()
    except Exception:
        pass  # Never fail the stop hook

    sys.exit(0)


def _is_kanban_worktree() -> bool:
    """Return True if the current process is running inside a kanban worktree.

    Kanban worktrees live under PROJECT_ROOT/.tmp/worktrees/ and have branches
    named kanban/task-*. Scheduler-dispatched Claude CLI sessions run with
    cwd set to the worktree path.
    """
    try:
        cwd = Path(os.getcwd()).resolve()
        worktrees_base = (PROJECT_ROOT / ".tmp" / "worktrees").resolve()
        return str(cwd).startswith(str(worktrees_base))
    except Exception:
        return False


def _current_branch(run) -> str:
    """Get the current git branch (or empty string if detached)."""
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    name = branch.stdout.strip()
    return "" if not name or name == "HEAD" else name


def _auto_commit_and_push():
    """Stage modified/new files, commit, and conditionally push.

    For interactive sessions (cwd = main checkout): commit + push as before.
    For kanban-dispatched sessions (cwd = worktree): commit locally to the
    kanban branch but DO NOT push. The scheduler handles the merge to main +
    push ONLY after full validation (CodeLens + Coherence + E2E + companion)
    passes. This prevents unverified agent work from landing on origin/main.
    """
    # For kanban worktrees, work in the worktree cwd; for interactive, main.
    cwd = os.getcwd() if _is_kanban_worktree() else str(PROJECT_ROOT)
    run = lambda cmd: subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=60
    )

    # Check if there are changes to commit
    status = run(["git", "status", "--porcelain"])
    if not status.stdout.strip():
        return  # Nothing to commit

    # Stage tracked (modified) files only — skip untracked to avoid committing junk
    run(["git", "add", "-u"])

    # Check if anything is staged
    staged = run(["git", "diff", "--cached", "--quiet"])
    if staged.returncode == 0:
        return  # Nothing staged

    # Commit
    result = run([
        "git", "commit", "-m",
        "chore: auto-commit from Claude Code session\n\n"
        "Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>",
    ])
    if result.returncode != 0:
        return

    branch_name = _current_branch(run)
    if not branch_name:
        return  # Detached HEAD — don't push

    # KANBAN GATE: if this is a scheduler-dispatched session, commit stays
    # local on the kanban branch. The scheduler will validate and then merge +
    # push to main only if all gates pass. This prevents unverified work from
    # ever reaching origin/main.
    if _is_kanban_worktree() or branch_name.startswith("kanban/"):
        return  # Do NOT push; scheduler controls the merge+push

    # Interactive sessions: push as before
    run(["git", "push", "origin", branch_name])


if __name__ == "__main__":
    main()
