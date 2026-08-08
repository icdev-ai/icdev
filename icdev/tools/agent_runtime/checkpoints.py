# CUI // SP-CTI
"""Filesystem checkpoints + rollback for the SAG runtime (sag-safe-02).

Before an *approved destructive* command runs (the sag-safe-01 approval flow is the
trigger), the affected paths are snapshotted so the operator can undo the change.

Snapshot strategy (per the guardrails):

- **Untracked files** are captured **copy-based** — the current bytes are copied
  into ``<repo>/.tmp/checkpoints/<id>/files/<rel-path>``.
- **Tracked files** lean on git: a single ``git stash create`` object records the
  whole working-tree state without touching it, and rollback restores individual
  paths with **explicit pathspecs** — ``git restore --source=<sha> -- <path>``.
  We never do ``git checkout <branch> -- .`` style mass restores.
- A file that does not yet exist is recorded as ``existed=False`` so rollback
  *deletes* it (undoing a create).

``/snapshot`` makes a manual checkpoint; ``/rollback [N]`` restores the Nth-newest
(default newest) after showing what will change. Rollback first snapshots the
current state of the same paths, so a rollback can itself be rolled back. Old
checkpoints (>7 days) are auto-pruned. All paths are ``pathlib``-based and Windows
safe — no ``/tmp`` literals (see the CLAUDE.md tmp-divergence guardrail).
"""
from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 — git plumbing, argument lists never shell-interpolated
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.checkpoints")

_PRUNE_AGE_DAYS = 7


def _find_repo_root(start: Path) -> Path:
    sentinels = ("pyproject.toml", ".git", "CLAUDE.md")
    for parent in [start, *start.parents]:
        if any((parent / s).exists() for s in sentinels):
            return parent
    return start.parents[1] if len(start.parents) > 1 else start


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)


def repo_root() -> Path:
    return _REPO_ROOT


def checkpoints_dir() -> Path:
    d = repo_root() / ".tmp" / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------
def _git(args: list[str], *, root: Optional[Path] = None) -> "tuple[int, str]":
    try:
        proc = subprocess.run(  # nosec B603 — fixed 'git' exe, list args, no shell
            ["git", *args],
            cwd=str(root or repo_root()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("checkpoints: git %s failed: %s", args, exc)
        return 1, ""


def _is_tracked(rel: str) -> bool:
    rc, out = _git(["ls-files", "--error-unmatch", "--", rel])
    return rc == 0 and bool(out)


def _stash_create() -> Optional[str]:
    """Snapshot tracked working-tree changes as a commit object (no WT change)."""
    rc, out = _git(["stash", "create", "sag-checkpoint"])
    if rc == 0 and out:
        return out.strip()
    return None


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------
@dataclass
class CheckpointFile:
    path: str          # repo-relative, posix
    existed: bool      # did the file exist at snapshot time?
    tracked: bool      # is it a git-tracked path?
    copy_name: Optional[str] = None  # relative name under files/ if copied


@dataclass
class Checkpoint:
    id: str
    created_at: str
    label: str
    tool_name: str = ""
    stash_sha: Optional[str] = None
    files: list[CheckpointFile] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return checkpoints_dir() / self.id

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Checkpoint":
        files = [CheckpointFile(**f) for f in d.get("files", [])]
        return Checkpoint(
            id=d["id"],
            created_at=d.get("created_at", ""),
            label=d.get("label", ""),
            tool_name=d.get("tool_name", ""),
            stash_sha=d.get("stash_sha"),
            files=files,
        )


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
def _new_id() -> str:
    # sortable timestamp + short counter for uniqueness within the same second
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"


def _resolve_rel(path: str) -> Optional[str]:
    """Normalise ``path`` to a repo-relative posix string, or None if it escapes."""
    if not path:
        return None
    root = repo_root()
    candidate = (root / path).resolve()
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        return None
    return rel.as_posix()


def create_checkpoint(
    paths: list[str], *, label: str = "", tool_name: str = ""
) -> Checkpoint:
    """Snapshot ``paths`` (repo-relative) into a new checkpoint directory."""
    cid = _new_id()
    cp = Checkpoint(
        id=cid,
        created_at=datetime.now(timezone.utc).isoformat(),
        label=label,
        tool_name=tool_name,
    )
    files_dir = cp.dir / "files"
    root = repo_root()

    any_tracked = False
    for raw in paths:
        rel = _resolve_rel(raw)
        if rel is None:
            logger.warning("checkpoints: skipping out-of-repo path %r", raw)
            continue
        abs_path = root / rel
        existed = abs_path.is_file()
        tracked = _is_tracked(rel)
        any_tracked = any_tracked or (tracked and existed)
        copy_name: Optional[str] = None
        # Copy-based capture for untracked existing files (tracked ones are
        # recoverable from the stash object). Copy untracked so they survive.
        if existed and not tracked:
            dest = files_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_path, dest)
            copy_name = rel
        cp.files.append(
            CheckpointFile(path=rel, existed=existed, tracked=tracked, copy_name=copy_name)
        )

    # One stash object covers all tracked files' pre-change state.
    if any_tracked:
        cp.stash_sha = _stash_create()

    cp.dir.mkdir(parents=True, exist_ok=True)
    (cp.dir / "manifest.json").write_text(
        json.dumps(cp.to_dict(), indent=2), encoding="utf-8", newline=""
    )
    logger.info("checkpoints: created %s (%d files)", cid, len(cp.files))
    return cp


# ---------------------------------------------------------------------------
# tool-call → affected paths
# ---------------------------------------------------------------------------
def affected_paths_for_tool(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    """Best-effort list of paths a mutating tool will touch (may be empty)."""
    if tool_name == "write_file":
        p = str((tool_input or {}).get("path", "")).strip()
        return [p] if p else []
    # run_command: paths are unknowable up front; the stash object still captures
    # tracked state, so rollback can restore whatever tracked files changed.
    return []


def snapshot_for_tool(
    tool_name: str, tool_input: dict[str, Any], *, label: str = ""
) -> Optional[Checkpoint]:
    """Create a checkpoint for an approved mutating tool call. None if nothing to do."""
    paths = affected_paths_for_tool(tool_name, tool_input)
    lbl = label or f"pre-{tool_name}"
    return create_checkpoint(paths, label=lbl, tool_name=tool_name)


# ---------------------------------------------------------------------------
# list / load
# ---------------------------------------------------------------------------
def load_checkpoint(cid: str) -> Optional[Checkpoint]:
    manifest = checkpoints_dir() / cid / "manifest.json"
    if not manifest.exists():
        return None
    try:
        return Checkpoint.from_dict(json.loads(manifest.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("checkpoints: cannot load %s: %s", cid, exc)
        return None


def list_checkpoints() -> list[Checkpoint]:
    """Return checkpoints, newest first (id is a sortable timestamp)."""
    out: list[Checkpoint] = []
    for child in checkpoints_dir().iterdir():
        if child.is_dir() and (child / "manifest.json").exists():
            cp = load_checkpoint(child.name)
            if cp is not None:
                out.append(cp)
    out.sort(key=lambda c: c.id, reverse=True)
    return out


# ---------------------------------------------------------------------------
# describe / rollback
# ---------------------------------------------------------------------------
def describe_changes(cp: Checkpoint) -> list[str]:
    """Human-readable list of what a rollback of ``cp`` will do."""
    changes: list[str] = []
    root = repo_root()
    # tracked files that currently differ from the stashed state
    changed_tracked: set[str] = set()
    if cp.stash_sha:
        rc, out = _git(["diff", "--name-only", cp.stash_sha])
        if rc == 0 and out:
            changed_tracked = {ln.strip() for ln in out.splitlines() if ln.strip()}
    for f in cp.files:
        abs_path = root / f.path
        if not f.existed:
            if abs_path.exists():
                changes.append(f"delete (was newly created): {f.path}")
        elif f.tracked:
            if f.path in changed_tracked or not cp.stash_sha:
                changes.append(f"restore tracked: {f.path}")
        else:
            changes.append(f"restore untracked copy: {f.path}")
    # tracked files changed by a run_command (no explicit path recorded)
    recorded = {f.path for f in cp.files}
    for path in sorted(changed_tracked - recorded):
        changes.append(f"restore tracked (command-modified): {path}")
    return changes


# confirm(changes) -> bool
Confirm = Callable[[list[str]], bool]


def _console_confirm(changes: list[str]) -> bool:
    print("\nRollback will make the following changes:")
    for c in changes:
        print(f"  - {c}")
    try:
        answer = input(f"\nProceed with rollback of {len(changes)} change(s)? [y/N]: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def rollback(
    cid: Optional[str] = None,
    *,
    confirm: Optional[Confirm] = None,
    snapshot_current: bool = True,
) -> dict[str, Any]:
    """Roll back to checkpoint ``cid`` (default: newest).

    Args:
        cid: Checkpoint id; when ``None`` the most recent checkpoint is used.
        confirm: Callable shown the change list; must return True to proceed.
            Defaults to a console prompt.
        snapshot_current: When True (default), snapshot the current state of the
            affected paths *before* restoring, so the rollback can itself be
            rolled back.

    Returns a result dict: ``{ok, checkpoint, changes, applied, undo_checkpoint,
    reason}``.
    """
    confirm = confirm or _console_confirm
    if cid is None:
        cps = list_checkpoints()
        if not cps:
            return {"ok": False, "reason": "no checkpoints available"}
        cp = cps[0]
    else:
        cp = load_checkpoint(cid)
        if cp is None:
            return {"ok": False, "reason": f"checkpoint {cid!r} not found"}

    changes = describe_changes(cp)
    if not changes:
        return {"ok": True, "checkpoint": cp.id, "changes": [], "applied": [],
                "reason": "nothing to restore"}

    if not confirm(changes):
        return {"ok": False, "checkpoint": cp.id, "changes": changes,
                "reason": "rollback declined by operator"}

    # Snapshot current state so the rollback is itself reversible.
    undo_id: Optional[str] = None
    if snapshot_current:
        affected = [f.path for f in cp.files]
        if cp.stash_sha:
            rc, out = _git(["diff", "--name-only", cp.stash_sha])
            if rc == 0 and out:
                affected.extend(ln.strip() for ln in out.splitlines() if ln.strip())
        undo = create_checkpoint(
            sorted(set(affected)), label=f"pre-rollback of {cp.id}", tool_name="rollback"
        )
        undo_id = undo.id

    applied = _apply_rollback(cp)
    return {
        "ok": True,
        "checkpoint": cp.id,
        "changes": changes,
        "applied": applied,
        "undo_checkpoint": undo_id,
    }


def _apply_rollback(cp: Checkpoint) -> list[str]:
    root = repo_root()
    applied: list[str] = []

    # 1. explicit recorded files
    for f in cp.files:
        abs_path = root / f.path
        if not f.existed:
            if abs_path.exists():
                try:
                    abs_path.unlink()
                    applied.append(f"deleted {f.path}")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("checkpoints: cannot delete %s: %s", f.path, exc)
            continue
        if f.tracked and cp.stash_sha:
            rc, _ = _git(["restore", "--source", cp.stash_sha, "--", f.path])
            if rc == 0:
                applied.append(f"restored tracked {f.path}")
                continue
            # fall through to copy if restore failed and a copy exists
        if f.copy_name:
            src = cp.dir / "files" / f.copy_name
            if src.exists():
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, abs_path)
                applied.append(f"restored untracked {f.path}")

    # 2. tracked files modified by a command (not individually recorded)
    if cp.stash_sha:
        recorded = {f.path for f in cp.files}
        rc, out = _git(["diff", "--name-only", cp.stash_sha])
        if rc == 0 and out:
            for path in out.splitlines():
                path = path.strip()
                if not path or path in recorded:
                    continue
                rc2, _ = _git(["restore", "--source", cp.stash_sha, "--", path])
                if rc2 == 0:
                    applied.append(f"restored tracked {path}")
    return applied


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------
def prune(max_age_days: int = _PRUNE_AGE_DAYS) -> int:
    """Delete checkpoint directories older than ``max_age_days``. Returns count."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for child in checkpoints_dir().iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("checkpoints: prune skip %s: %s", child, exc)
    if removed:
        logger.info("checkpoints: pruned %d stale checkpoint(s)", removed)
    return removed
