#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
"""A worktree HUSK with no .git marker is provably dead -- sweep it on a short
clock, not the 7-day one (mfx-own-04).

THE INCIDENT. task-det-e9a2e3ea16 sat in `validating` and BOTH requeue proofs
refused it: the orphan proof on `branch_exists`/`worktree_exists`, the
empty-checkout proof on `worktree_unregistered`. The blocker was a 534 MB
directory at .tmp/worktrees/task-det-e9a2e3ea16 with NO .git file at all -- the
husk of a former worktree, unregistered with git, whose newest content was
.logs/*.ndjson. Its branch was 0 commits ahead of main with no remote branch,
so there was nothing to lose and nothing to recover. `kanban_requeue_reflex`
(mfx-own-03) had run 60 times, 60 successes, 0 failures, `last_metric_value`
0.0 -- CORRECTLY refusing, because an unregistered directory refuses by design
(kpr-dup-10: `git status` inside one describes the ENCLOSING checkout). A
landed fix reported perfect health while the one card it exists for stayed
stuck, and a human had to look.

THE GAP IS THE CLOCK, NOT THE PROOF -- and, measured, not only the clock.
`_sweep_old_worktrees` uses KANBAN_WORKTREE_STALE_AGE_DAYS (7), tuned for a
directory that MIGHT still be a live agent's worktree. A directory with NO
.git marker cannot be one: `git worktree add` writes that file before anything
else, so every live worktree carries it. The 7-day wait buys nothing and costs
the card up to a week of invisibility. Worse, re-reading the sweeper: a
.git-less directory was never a CANDIDATE at all (`_sweep_candidates` returns
only directories carrying `.git`), and `_worktree_is_disposable` REFUSES
"entries but no .git -- possibly a partial delete" by design. So the 7-day
clock would never have fired on it either; the husk was structurally invisible
to every cleanup path.

THE CLASS, and why it is safe. A husk is defined by the ABSENCE of git
metadata, so the questions the 7-day path asks of a registered worktree
(uncommitted changes? unpushed commits?) cannot even be asked of it -- which is
exactly why it must NEVER be widened to registered worktrees. What CAN be asked
is asked, and every answer must hold:

  in_scope      a DIRECT child of WORKTREE_BASE. That flat layout's contract is
                `<base>/<task-id>`, the one place a task id is a FACT rather
                than a guess; under the sanctioned root a directory is named
                for a slug, an invented id matches no task, and an id that
                matches nothing silently defeats the in_progress guard. Such
                directories are SURVEYED by name and never acted on.
  no_git_marker no `.git` file or directory. A `.git` means the 7-day path
                owns it.
  unregistered  absent from a SUCCESSFUL `git worktree list --porcelain` run
                from the canonical repo root. A failed listing proves nothing
                and REFUSES.
  board_row     the task id has a row on the board. `data` and `tools` under
                .tmp/worktrees (measured: residue of a root-computing bug, one
                still being written to) have none and are refused.
  not_in_progress  the task is not `in_progress`. KEPT from the 7-day rule.
  aged          the NEWEST mtime in the WHOLE tree (files and directories,
                bounded walk) is older than `husk_age_hours`. The top-level
                mtime is not enough: on Windows it moves only when a direct
                child changes, and a process still writing .logs/*.ndjson deep
                inside is the one sign of life a husk can show. A walk that
                exceeds its entry or time budget REFUSES (`age_unmeasurable`)
                -- a partial walk over-estimates the age, the direction that
                errs toward deleting.

Anything unreadable is `proven: None` and refuses. `proven` is True | False |
None and None never acts.

EVERY ACT IS prove -> audit -> apply -> confirm, IN THAT ORDER (the restore_acts
shape). The audit row (`worktree_cleaned` / `husk_sweep.remove.intent`, an
EXISTING event type so no CHECK rebuild) is written with raise_on_error=True
BEFORE rmtree; no row, no act: `unaudited_refused`. That is the answer to the
operator who DECLINED a manual `rm -rf` of this husk on 2026-09-06 -- the right
instinct, and precisely why the deletion needs a proof and an audit row rather
than a person. apply is ONE rmtree, with the read-only-file and long-path
handling a Windows `node_modules` needs (the `<id>/node_modules`-only residue
measured under .tmp/worktrees is what an unhandled rmtree leaves). confirm
re-reads the path; `applied_unconfirmed` is never `applied`.

SURVEYED BEFORE ARMING (docs/audits/mfx-own-04-worktree-husk-survey.md), live
roots 2026-09-06: 26 in-scope husks, ~7.0 GB, ages 1.17-13.55 days, 25 `done`
+ 1 `validating`, 0 `in_progress`; 2 .git-less directories with no board row
(refused); 13 husk-shaped directories under the sanctioned root with no task
id (reported, out of scope). Not one candidate belonged to a live task.

Report only for `--survey` / `--plan`; `--apply` acts on ONE named task.
Consumed by `_sweep_old_worktrees` in tools/genesis/reflexes/kanban.py on the
scheduler's opportunistic sweep cadence. Config: args/worktree_husk_sweep.yaml.
Kill switch: KANBAN_WORKTREE_HUSK_SWEEP=0.

Headless:
    python -m tools.kanban.worktree_husks --survey [--json]
    python -m tools.kanban.worktree_husks --plan [--json]        # acts on nothing
    python -m tools.kanban.worktree_husks --apply <task-id> [--dry-run]
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess  # nosec B404 -- git plumbing only, fixed argv
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# sys.path BOOTSTRAP first, so `python tools/kanban/worktree_husks.py --survey`
# can import `icdev.core.paths` from a source checkout.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("kanban.worktree_husks")

CONFIG_PATH = BASE_DIR / "args" / "worktree_husk_sweep.yaml"
ACTOR = "worktree_husk_sweep"
#: An EXISTING audit event type (admitted by the deployed CHECK; the
#: empty-checkout act already writes it), so no migration. The act and its phase
#: ride in ``action`` as ``husk_sweep.remove.<phase>``.
AUDIT_EVENT_TYPE = "worktree_cleaned"
AUDIT_ACTION_PREFIX = "husk_sweep.remove"
KILL_SWITCH_ENV = "KANBAN_WORKTREE_HUSK_SWEEP"
AGE_ENV = "KANBAN_WORKTREE_HUSK_AGE_HOURS"

DEFAULTS: Dict[str, Any] = {
    "husk_age_hours": 6,
    "max_removals_per_run": 3,
    "max_walk_entries": 250000,
    "walk_budget_seconds": 120,
}

#: Outcomes of :func:`apply`. ``applied_unconfirmed`` is never ``applied``.
OUTCOMES = ("applied", "applied_unconfirmed", "refused", "unaudited_refused",
            "dry_run", "disabled")


# ── config ──────────────────────────────────────────────────────────────────


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """DEFAULTS overlaid by args/worktree_husk_sweep.yaml, then the env override
    for the clock. A malformed file degrades to DEFAULTS with a warning: this
    file governs HOW FAST a husk is swept, never whether a live worktree is."""
    cfg = dict(DEFAULTS)
    p = Path(path) if path is not None else CONFIG_PATH
    try:
        if p.exists():
            import yaml  # noqa: PLC0415

            loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                for k in DEFAULTS:
                    if k in loaded and loaded[k] is not None:
                        cfg[k] = loaded[k]
    except Exception as exc:  # noqa: BLE001
        logger.warning("worktree_husks: config %s unreadable (%s); defaults", p, exc)
    env_hours = os.environ.get(AGE_ENV)
    if env_hours:
        try:
            cfg["husk_age_hours"] = float(env_hours)
        except ValueError:
            logger.warning("worktree_husks: %s=%r is not a number; ignored", AGE_ENV, env_hours)
    for k in DEFAULTS:
        try:
            cfg[k] = float(cfg[k]) if k in ("husk_age_hours", "walk_budget_seconds") else int(cfg[k])
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS[k]
    return cfg


def sweep_enabled() -> bool:
    return os.environ.get(KILL_SWITCH_ENV, "1").strip().lower() not in ("0", "false", "off", "no")


# ── verdict ──────────────────────────────────────────────────────────────────


@dataclass
class HuskVerdict:
    """One directory, re-derived. ``proven`` is True | False | None; only True acts."""

    path: str
    task_id: Optional[str]
    in_scope: bool
    proven: Optional[bool]
    reasons: List[str] = field(default_factory=list)
    task_status: Optional[str] = None
    age_hours: Optional[float] = None
    newest_path: Optional[str] = None
    size_bytes: Optional[int] = None
    entries: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── primary-data probes (each injectable) ───────────────────────────────────


def _norm(p) -> str:
    s = str(Path(p).resolve()).replace("\\", "/").rstrip("/")
    return s.lower() if os.name == "nt" else s


def worktree_listing(repo_root_path) -> Optional[set]:
    """The set of registered worktree paths (normalised), or ``None`` when
    ``git worktree list`` did not succeed -- absence from a failed listing
    proves nothing (the measured inversion kpr-dup-10 recorded)."""
    try:
        proc = subprocess.run(  # nosec B603 B607 -- fixed argv
            ["git", "worktree", "list", "--porcelain"], cwd=str(repo_root_path),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("worktree_husks: git worktree list could not run (%s)", exc)
        return None
    if proc.returncode != 0:
        return None
    out = set()
    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            out.add(_norm(line[len("worktree "):].strip()))
    return out


def board_statuses(task_ids: List[str], get_conn: Optional[Callable[[], Any]] = None
                   ) -> Optional[Dict[str, Optional[str]]]:
    """``{task_id: status | None}`` (None = no row), or ``None`` when the board
    could not be read -- an unreadable board is not an empty one."""
    if not task_ids:
        return {}
    try:
        if get_conn is None:
            from tools.db.storage import get_connection  # noqa: PLC0415

            conn = get_connection()
        else:
            conn = get_conn()
        try:
            out: Dict[str, Optional[str]] = {t: None for t in task_ids}
            for tid in task_ids:
                row = conn.execute(
                    "SELECT status FROM kanban_tasks WHERE id = %s", (tid,)
                ).fetchone()
                if row is not None:
                    out[tid] = dict(row).get("status") if not isinstance(row, tuple) else row[0]
            return out
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("worktree_husks: board unreadable (%s)", exc)
        return None


def newest_mtime(path: Path, *, max_entries: int, budget_seconds: float,
                 cutoff: Optional[float] = None) -> Dict[str, Any]:
    """The newest mtime in the WHOLE tree, or why it could not be measured.

    Returns ``{measured, newest, newest_path, entries, size_bytes, reason}``.
    Stops EARLY as soon as an entry newer than ``cutoff`` is seen (the verdict
    is already "young"); stops as UNMEASURED when the entry or time budget is
    exceeded, because a partial walk over-estimates the age.
    """
    out: Dict[str, Any] = {"measured": False, "newest": None, "newest_path": ".",
                           "entries": 0, "size_bytes": 0, "reason": None}
    t0 = time.monotonic()
    try:
        st = os.lstat(path)
    except OSError as exc:
        out["reason"] = f"unreadable:{exc.__class__.__name__}"
        return out
    newest = st.st_mtime
    newest_path = "."
    entries = 0
    size = 0
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            entries += 1
            if entries > max_entries:
                out.update(entries=entries, size_bytes=size,
                           reason=f"walk_exceeded_{max_entries}_entries")
                return out
            if time.monotonic() - t0 > budget_seconds:
                out.update(entries=entries, size_bytes=size,
                           reason=f"walk_exceeded_{int(budget_seconds)}s")
                return out
            full = os.path.join(root, name)
            try:
                s = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISDIR(s.st_mode):
                size += s.st_size
            if s.st_mtime > newest:
                newest = s.st_mtime
                newest_path = os.path.relpath(full, path)
                if cutoff is not None and newest > cutoff:
                    out.update(measured=True, newest=newest, newest_path=newest_path,
                               entries=entries, size_bytes=size, reason="young_short_circuit")
                    return out
    out.update(measured=True, newest=newest, newest_path=newest_path,
               entries=entries, size_bytes=size)
    return out


# ── the class ───────────────────────────────────────────────────────────────


def in_scope(path: Path, base: Path) -> bool:
    """A DIRECT child of the flat WORKTREE_BASE, by resolved path -- the one
    layout where the directory name IS the task id."""
    try:
        return Path(path).resolve().parent == Path(base).resolve()
    except OSError:
        return False


def classify(path, *, base, listing: Optional[set], statuses: Optional[Dict[str, Optional[str]]],
             cfg: Optional[Dict[str, Any]] = None, now: Optional[float] = None,
             repo_root_path=None) -> HuskVerdict:
    """Re-derive the husk class for ONE directory from primary data.

    ``listing`` is :func:`worktree_listing`'s result (None = unreadable);
    ``statuses`` is :func:`board_statuses`'s (None = unreadable).
    """
    cfg = cfg or load_config()
    now = time.time() if now is None else now
    path = Path(path)
    scoped = in_scope(path, base)
    v = HuskVerdict(path=str(path), task_id=path.name if scoped else None,
                    in_scope=scoped, proven=False)
    if not scoped:
        v.reasons.append("out_of_scope:not_a_direct_child_of_worktree_base")
        return v
    if path.is_symlink() or not path.is_dir():
        v.proven = False
        v.reasons.append("not_a_plain_directory")
        return v
    if repo_root_path is not None:
        try:
            rr = Path(repo_root_path).resolve()
            pr = path.resolve()
            if rr == pr or rr.is_relative_to(pr):
                v.reasons.append("would_remove_repo_root")
                return v
        except OSError:
            v.proven = None
            v.reasons.append("repo_root_unresolvable")
            return v
    if (path / ".git").exists():
        v.reasons.append("has_git_marker")   # the 7-day path's; not this class
        return v
    if listing is None:
        v.proven = None
        v.reasons.append("worktree_list_unreadable")
        return v
    if _norm(path) in listing:
        v.reasons.append("registered")
        return v
    if statuses is None:
        v.proven = None
        v.reasons.append("board_unreadable")
        return v
    status = statuses.get(path.name)
    v.task_status = status
    if status is None:
        v.reasons.append("no_board_row")
        return v
    if status == "in_progress":
        v.reasons.append("task_in_progress")
        return v
    hours = float(cfg["husk_age_hours"])
    cutoff = now - hours * 3600.0
    walk = newest_mtime(path, max_entries=int(cfg["max_walk_entries"]),
                        budget_seconds=float(cfg["walk_budget_seconds"]), cutoff=cutoff)
    v.entries = walk["entries"]
    v.size_bytes = walk["size_bytes"]
    if not walk["measured"]:
        v.proven = None
        v.reasons.append(f"age_unmeasurable:{walk['reason']}")
        return v
    v.age_hours = round((now - walk["newest"]) / 3600.0, 3)
    v.newest_path = walk["newest_path"]
    if walk["newest"] > cutoff:
        v.reasons.append(f"younger_than_{hours:g}h")
        return v
    v.proven = True
    return v


# ── survey / plan ───────────────────────────────────────────────────────────


def _git_less_children(root: Path) -> Optional[List[Path]]:
    """Direct child directories of ``root`` carrying no ``.git``; None if unreadable."""
    try:
        return sorted(c for c in root.iterdir()
                      if c.is_dir() and not c.is_symlink() and not (c / ".git").exists())
    except OSError:
        return None


def _husk_shaped_under(root: Path, max_depth: int = 3) -> List[Dict[str, Any]]:
    """Under the NESTED sanctioned root, the .git-less non-container leaves:
    directories with content whose subtree holds no ``.git`` at all. Reported
    only; no task id can be derived there."""
    out: List[Dict[str, Any]] = []

    def has_git_beneath(d: Path, depth: int) -> bool:
        if depth > max_depth:
            return False
        try:
            kids = [c for c in d.iterdir() if c.is_dir() and not c.is_symlink()]
        except OSError:
            return False
        for c in kids:
            if (c / ".git").exists() or has_git_beneath(c, depth + 1):
                return True
        return False

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            kids = sorted(c for c in d.iterdir() if c.is_dir() and not c.is_symlink())
        except OSError:
            return
        for c in kids:
            if (c / ".git").exists():
                continue
            try:
                n = sum(1 for _ in c.iterdir())
            except OSError:
                continue
            if n and not has_git_beneath(c, depth + 1):
                try:
                    age = (time.time() - c.stat().st_mtime) / 3600.0
                except OSError:
                    age = None
                out.append({"path": str(c), "entries": n,
                            "top_level_age_hours": None if age is None else round(age, 1)})
            elif n:
                walk(c, depth + 1)

    walk(root, 1)
    return out


def _default_base() -> Path:
    from tools.genesis.reflexes.kanban import WORKTREE_BASE  # noqa: PLC0415

    return Path(WORKTREE_BASE)


def _default_repo_root() -> Path:
    from tools.genesis.reflexes.kanban import _canonical_repo_root  # noqa: PLC0415

    return Path(_canonical_repo_root())


def survey(*, base=None, repo_root_path=None, sanctioned_root=None,
           get_conn: Optional[Callable[[], Any]] = None,
           cfg: Optional[Dict[str, Any]] = None, now: Optional[float] = None,
           include_out_of_scope: bool = True) -> Dict[str, Any]:
    """Every .git-less directory under the live roots, classified. Acts on
    NOTHING. ``state`` is unmeasurable | clean | findings -- and unmeasurable,
    never a clean zero, when WORKTREE_BASE cannot be read."""
    cfg = cfg or load_config()
    now = time.time() if now is None else now
    base = Path(base) if base is not None else _default_base()
    rr = Path(repo_root_path) if repo_root_path is not None else _default_repo_root()
    out: Dict[str, Any] = {
        "state": "unmeasurable", "base": str(base), "repo_root": str(rr),
        "husk_age_hours": cfg["husk_age_hours"], "enabled": sweep_enabled(),
        "in_scope": [], "out_of_scope": [], "roots": [],
        "proven": 0, "refused": 0, "unmeasurable": 0, "size_bytes_proven": 0,
        "error": None,
    }
    if not base.is_dir():
        out["roots"].append({"path": str(base), "readable": False})
        out["error"] = "worktree_base_missing"
        return out
    kids = _git_less_children(base)
    if kids is None:
        out["roots"].append({"path": str(base), "readable": False})
        out["error"] = "worktree_base_unreadable"
        return out
    out["roots"].append({"path": str(base), "readable": True, "git_less_children": len(kids)})
    listing = worktree_listing(rr)
    statuses = board_statuses([k.name for k in kids], get_conn) if kids else {}
    for k in kids:
        v = classify(k, base=base, listing=listing, statuses=statuses, cfg=cfg, now=now,
                     repo_root_path=rr)
        out["in_scope"].append(v.to_dict())
        if v.proven is True:
            out["proven"] += 1
            out["size_bytes_proven"] += v.size_bytes or 0
        elif v.proven is None:
            out["unmeasurable"] += 1
        else:
            out["refused"] += 1
    if include_out_of_scope:
        sroot = sanctioned_root
        if sroot is None:
            try:
                from tools.git.worktree_paths import worktree_root  # noqa: PLC0415

                sroot = Path(str(worktree_root()))
            except Exception as exc:  # noqa: BLE001
                out["roots"].append({"path": None, "readable": False, "error": str(exc)})
        if sroot is not None:
            sroot = Path(sroot)
            if sroot.is_dir():
                out["out_of_scope"] = _husk_shaped_under(sroot)
                out["roots"].append({"path": str(sroot), "readable": True,
                                     "husk_shaped": len(out["out_of_scope"])})
            else:
                out["roots"].append({"path": str(sroot), "readable": False})
    if listing is None or statuses is None:
        out["state"] = "unmeasurable"
        out["error"] = "worktree_list_unreadable" if listing is None else "board_unreadable"
    else:
        out["state"] = "findings" if out["proven"] else "clean"
    return out


# ── the act: prove -> audit -> apply -> confirm ─────────────────────────────


def _audit_default(action: str, details: Dict[str, Any], *, raise_on_error: bool) -> Optional[int]:
    from tools.audit.audit_logger import log_event  # noqa: PLC0415

    return log_event(AUDIT_EVENT_TYPE, ACTOR, action, details=details,
                     raise_on_error=raise_on_error)


def _rmtree(path: Path) -> None:
    """ONE rmtree, with what a Windows node_modules needs: read-only bits
    cleared on refusal, and the extended-length prefix so a path past MAX_PATH
    is reachable (the `<id>/node_modules`-only residue measured under
    .tmp/worktrees is what an unhandled rmtree leaves behind)."""
    target = str(Path(path).resolve())
    if os.name == "nt" and not target.startswith("\\\\?\\"):
        target = "\\\\?\\" + target

    def _on_error(func, p, exc_info):  # noqa: ARG001
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except Exception:  # noqa: BLE001
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(target, onexc=lambda f, p, e: _on_error(f, p, e))
    else:  # pragma: no cover -- older interpreters
        shutil.rmtree(target, onerror=_on_error)


def apply(path, *, base=None, repo_root_path=None, get_conn=None, cfg=None,
          now=None, dry_run: bool = False, audit: Optional[Callable] = None,
          rmtree: Optional[Callable[[Path], None]] = None) -> Dict[str, Any]:
    """prove -> audit -> apply -> confirm for ONE directory. Never raises."""
    cfg = cfg or load_config()
    base = Path(base) if base is not None else _default_base()
    rr = Path(repo_root_path) if repo_root_path is not None else _default_repo_root()
    audit = audit or _audit_default
    rmtree = rmtree or _rmtree
    path = Path(path)
    listing = worktree_listing(rr)
    statuses = board_statuses([path.name], get_conn) if in_scope(path, base) else {}
    v = classify(path, base=base, listing=listing, statuses=statuses, cfg=cfg, now=now,
                 repo_root_path=rr)
    result: Dict[str, Any] = {"task_id": v.task_id, "path": str(path), "verdict": v.to_dict(),
                              "outcome": "refused", "audit_id": None, "error": None}
    if v.proven is not True:
        return result
    if not sweep_enabled():
        result["outcome"] = "disabled"
        return result
    if dry_run:
        result["outcome"] = "dry_run"
        return result
    details = {"task_id": v.task_id, "path": str(path), "task_status": v.task_status,
               "age_hours": v.age_hours, "newest_path": v.newest_path,
               "size_bytes": v.size_bytes, "entries": v.entries,
               "husk_age_hours": cfg["husk_age_hours"], "proof": "no_git_marker+unregistered+"
               "board_row+not_in_progress+aged"}
    try:
        result["audit_id"] = audit(f"{AUDIT_ACTION_PREFIX}.intent", details, raise_on_error=True)
    except Exception as exc:  # noqa: BLE001
        result["outcome"] = "unaudited_refused"
        result["error"] = f"audit: {exc}"
        logger.warning("worktree_husks: NOT removing %s -- intent row could not be written (%s)",
                       path, exc)
        return result
    try:
        rmtree(path)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"rmtree: {exc}"
    gone = not path.exists()
    result["outcome"] = "applied" if gone else "applied_unconfirmed"
    try:
        audit(f"{AUDIT_ACTION_PREFIX}.{'applied' if gone else 'unconfirmed'}",
              {**details, "error": result["error"]}, raise_on_error=False)
    except Exception:  # noqa: BLE001 -- the confirm row is best-effort; the intent row is not
        pass
    if gone:
        logger.info("worktree_husks: removed husk %s (task %s %s, age %.1fh, %d entries)",
                    path, v.task_id, v.task_status, v.age_hours or -1, v.entries or 0)
    else:
        logger.warning("worktree_husks: husk %s survived rmtree (%s) -- will retry", path,
                       result["error"])
    return result


def sweep_husks(*, base=None, repo_root_path=None, get_conn=None, cfg=None, now=None,
                dry_run: bool = False, audit=None, rmtree=None) -> Dict[str, Any]:
    """The sweeper's entry point: every proven husk under WORKTREE_BASE, OLDEST
    FIRST, bounded by ``max_removals_per_run``; the rest are ``deferred`` by
    name. Never raises. ``state`` is unmeasurable | disabled | clean | acted."""
    cfg = cfg or load_config()
    now = time.time() if now is None else now
    out: Dict[str, Any] = {"state": "unmeasurable", "applied": [], "unconfirmed": [],
                           "refused": [], "unmeasurable": [], "deferred": [],
                           "max_removals_per_run": cfg["max_removals_per_run"],
                           "dry_run": dry_run, "error": None}
    try:
        s = survey(base=base, repo_root_path=repo_root_path, get_conn=get_conn, cfg=cfg,
                   now=now, include_out_of_scope=False)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"survey: {exc}"
        return out
    if s["state"] == "unmeasurable":
        out["error"] = s["error"]
        return out
    verdicts = s["in_scope"]
    out["refused"] = [v["task_id"] for v in verdicts if v["proven"] is False]
    out["unmeasurable"] = [v["task_id"] for v in verdicts if v["proven"] is None]
    proven = sorted((v for v in verdicts if v["proven"] is True),
                    key=lambda v: -(v["age_hours"] or 0.0))
    if not sweep_enabled():
        out["state"] = "disabled"
        out["deferred"] = [v["task_id"] for v in proven]
        return out
    cap = int(cfg["max_removals_per_run"])
    out["deferred"] = [v["task_id"] for v in proven[cap:]]
    for v in proven[:cap]:
        r = apply(v["path"], base=base, repo_root_path=repo_root_path, get_conn=get_conn,
                  cfg=cfg, now=now, dry_run=dry_run, audit=audit, rmtree=rmtree)
        bucket = {"applied": "applied", "dry_run": "applied",
                  "applied_unconfirmed": "unconfirmed"}.get(r["outcome"])
        if bucket:
            out[bucket].append(r)
        else:
            out["refused"].append(r["task_id"])
    out["state"] = "acted" if (out["applied"] or out["unconfirmed"]) else "clean"
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────


def _render_survey(s: Dict[str, Any]) -> str:
    lines = [f"worktree husk survey  state={s['state']}  base={s['base']}  "
             f"clock={s['husk_age_hours']:g}h  enabled={s['enabled']}"]
    if s.get("error"):
        lines.append(f"  error: {s['error']}")
    lines.append(f"  in scope: {len(s['in_scope'])}  proven={s['proven']}  refused={s['refused']}"
                 f"  unmeasurable={s['unmeasurable']}  "
                 f"proven size={s['size_bytes_proven'] / 1e6:.1f} MB")
    for v in s["in_scope"]:
        age = "?" if v["age_hours"] is None else f"{v['age_hours'] / 24:.2f}d"
        mb = "?" if v["size_bytes"] is None else f"{v['size_bytes'] / 1e6:.1f}"
        verdict = {True: "PROVEN", False: "refused", None: "UNMEASURABLE"}[v["proven"]]
        lines.append(f"  {v['task_id']:28s} {verdict:12s} status={v['task_status'] or '-':12s} "
                     f"age={age:8s} MB={mb:8s} {' '.join(v['reasons'])}")
    if s.get("out_of_scope"):
        lines.append(f"  out of scope (no task id derivable; NEVER acted on): {len(s['out_of_scope'])}")
        for o in s["out_of_scope"]:
            lines.append(f"    {o['path']}  entries={o['entries']}  top-level age="
                         f"{o['top_level_age_hours']}h")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--survey", action="store_true", help="every .git-less directory under the live roots")
    g.add_argument("--plan", action="store_true", help="what a sweep would act on; acts on nothing")
    g.add_argument("--apply", metavar="TASK_ID", help="prove -> audit -> remove ONE husk")
    ap.add_argument("--dry-run", action="store_true", help="with --apply: prove, audit nothing, act on nothing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.survey or a.plan:
        s = survey(include_out_of_scope=bool(a.survey))
        if a.json:
            print(json.dumps(s, indent=2, default=str))
        else:
            print(_render_survey(s))
        return 2 if s["state"] == "unmeasurable" else 0
    base = _default_base()
    r = apply(base / a.apply, dry_run=a.dry_run)
    if a.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        v = r["verdict"]
        print(f"{a.apply}: {r['outcome']}  proven={v['proven']}  {' '.join(v['reasons'])}"
              f"  age={v['age_hours']}h  audit_id={r['audit_id']}  error={r['error']}")
    return 0 if r["outcome"] in ("applied", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
