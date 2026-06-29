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


def _dispatch_source() -> str:
    """Determine who dispatched the current Claude Code session (guard-23).

    Returns one of:
      - 'genesis_scheduler'  — ICDEV_DISPATCH_SOURCE env var set by scheduler
                              OR running inside a kanban worktree
      - 'claude_interactive' — Claude Code session started by user (default)
      - 'user_manual'        — if stop.py were somehow invoked outside Claude
                              (can't really happen — this hook only fires from
                              Claude Code). Reserved for future use.
    """
    env_source = os.environ.get("ICDEV_DISPATCH_SOURCE", "").strip()
    if env_source:
        return env_source
    if _is_kanban_worktree():
        return "genesis_scheduler"
    return "claude_interactive"


def _current_branch(run) -> str:
    """Get the current git branch (or empty string if detached)."""
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    name = branch.stdout.strip()
    return "" if not name or name == "HEAD" else name


def _auto_commit_and_push():
    """Stage, commit locally, validate, then push ONLY if validation passes.

    Unified flow for all Claude sessions (interactive and kanban):
      1. Stage + commit locally (preserves work even if validation fails)
      2. Run validation suite: CodeLens + Coherence + E2E + companion sync
      3. Push ONLY if all gates pass

    Kanban-dispatched sessions: the scheduler handles merge-to-main + push
    after its own verification passes (which includes this same validation
    suite), so the stop hook commits locally and exits.

    Interactive sessions: stop hook runs validation itself and pushes only
    on green.

    Rationale: nothing lands on origin/main without passing all 4 gates.
    """
    # For kanban worktrees, work in the worktree cwd; for interactive, main.
    is_kanban = _is_kanban_worktree()
    cwd = os.getcwd() if is_kanban else str(PROJECT_ROOT)
    run = lambda cmd: subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=60
    )

    # Branch-safety guard: never auto-commit onto a protected/shared branch from
    # an interactive session. Committing+pushing straight onto main/irad/feature
    # dumps work onto the working branch and bypasses the branch→V&V→MR flow.
    # Kanban worktrees (kanban/* branches) are exempt — the scheduler owns their
    # lifecycle and merges after its own validation. Override with
    # ICDEV_AUTO_COMMIT_ALLOW_PROTECTED=true.
    if not is_kanban:
        _branch = _current_branch(run)
        _protected = {
            b.strip() for b in os.environ.get(
                "ICDEV_PROTECTED_BRANCHES", "main,master,irad/feature"
            ).split(",") if b.strip()
        }
        _allow = os.environ.get(
            "ICDEV_AUTO_COMMIT_ALLOW_PROTECTED", ""
        ).lower() in ("1", "true", "yes")
        if _branch and _branch in _protected and not _allow:
            print(
                f"[stop-hook] Auto-commit skipped: HEAD is protected branch "
                f"'{_branch}'. Create a feature branch (or set "
                f"ICDEV_AUTO_COMMIT_ALLOW_PROTECTED=true) so work goes through "
                f"branch->V&V->MR instead of landing on {_branch}.",
                file=sys.stderr,
            )
            return

    # Check if there are changes to commit
    status = run(["git", "status", "--porcelain"])
    if not status.stdout.strip():
        return  # Nothing to commit

    # Stage modified tracked files first
    run(["git", "add", "-u"])

    # CRITICAL FIX: also stage NEW files in safe directories. The previous
    # `git add -u` only path silently dropped Claude's new file creations
    # (Write tool output) — verification then saw "no commits" and rejected
    # the task as a no-op when in fact the agent had done the work. We
    # whitelist source directories to stage and let .gitignore handle junk.
    SAFE_DIRS = [
        "tools/", "tests/", "args/", "docs/", "goals/", "hardprompts/",
        "context/", ".claude/", ".github/", ".gitlab/",
        "scripts/", "schemas/", "marketplace-saas/",
    ]
    SAFE_ROOT_FILES = {
        "CLAUDE.md", "AGENTS.md", "CONVENTIONS.md", "README.md",
        "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
    }
    untracked = [
        ln[3:].strip()
        for ln in (status.stdout or "").splitlines()
        if ln.startswith("?? ") and ln[3:].strip()
    ]
    to_add: list = []
    for path in untracked:
        if any(path.startswith(d) for d in SAFE_DIRS) or path in SAFE_ROOT_FILES:
            # Skip nested .tmp/ paths inside otherwise-safe dirs
            if "/.tmp/" in path or path.startswith(".tmp/"):
                continue
            to_add.append(path)
    if to_add:
        # Stage in batches to avoid exceeding command-line length limits
        for i in range(0, len(to_add), 50):
            run(["git", "add"] + to_add[i:i+50])

    staged = run(["git", "diff", "--cached", "--quiet"])
    if staged.returncode == 0:
        return  # Nothing staged

    # Commit locally (work preserved even if validation later fails)
    # guard-23: embed dispatch_source in commit trailer for traceability
    source = _dispatch_source()
    commit_msg = (
        "chore: auto-commit from Claude Code session\n\n"
        f"Dispatch-Source: {source}\n"
        "Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
    )
    result = run(["git", "commit", "-m", commit_msg])
    if result.returncode != 0:
        return

    branch_name = _current_branch(run)
    if not branch_name:
        return  # Detached HEAD — don't push

    # KANBAN SESSIONS: scheduler owns the merge+push after its own validation.
    # Commit stays local on the kanban branch.
    if is_kanban or branch_name.startswith("kanban/"):
        return

    # INTERACTIVE SESSIONS: pre-push route smoke for changed files
    # Runs only when the server is up — skips gracefully if not.
    try:
        changed_result = run(["git", "diff", "HEAD~1", "HEAD", "--name-only"])
        changed_files = [f for f in (changed_result.stdout or "").splitlines() if f.strip()]
        if changed_files:
            from tools.testing.route_smoke import run_smoke, _routes_for_changed_files
            smoke_routes = _routes_for_changed_files(changed_files)
            if smoke_routes:
                smoke_ok, smoke_results = run_smoke(smoke_routes, verbose=False)
                if not smoke_ok:
                    failed = [r["route"] for r in smoke_results if not r.get("ok")]
                    print(
                        f"[stop-hook] Route smoke FAILED: {failed}\n"
                        f"[stop-hook] Commit is local. Fix the routes above, then push manually:\n"
                        f"[stop-hook]   git push origin {branch_name}",
                        file=sys.stderr,
                    )
                    return
    except Exception as exc:
        print(f"[stop-hook] Route smoke skipped: {exc}", file=sys.stderr)

    # INTERACTIVE SESSIONS: run the unified validation suite, push only if green
    try:
        from tools.workflow.validated_commit import validate_working_tree

        ok, reason, _metrics = validate_working_tree(
            cwd=cwd,
            compare_to_main=False,  # interactive session IS main
            run_e2e=True,
            run_companion=True,
        )
        if not ok:
            print(
                f"[stop-hook] Commit created locally but NOT pushed.\n"
                f"[stop-hook] Validation FAILED: {reason}\n"
                f"[stop-hook] Fix the issue and push manually with: "
                f"git push origin {branch_name}",
                file=sys.stderr,
            )
            return
    except Exception as exc:
        print(
            f"[stop-hook] Validation error: {exc}. Commit kept local, NOT pushed.",
            file=sys.stderr,
        )
        return

    # All gates passed — safe to push
    run(["git", "push", "origin", branch_name])


if __name__ == "__main__":
    main()
