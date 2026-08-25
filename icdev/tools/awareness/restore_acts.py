# CUI // SP-CTI
"""The `restore` tier, enumerated: a CLOSED set of mechanical acts (autonomy-act-03).

`claim_verifier.TIER` names three levels of corrective action — report | restore
| propose — and deliberately has NO tier that edits a claim, a threshold or an
assertion so that a surface agrees: a verifier that may rewrite what it verifies
can always make itself green, which is the same move as a test quietly weakened
to match code that broke. This module does not add one. It gives `restore` a
body, and the body is a LIST.

    A CLOSED SET IS THE CONTROL. An open-ended "the agent decides what to fix"
    is not self-healing; it is an unaudited actuator with write access to its
    own guardrails.

FOUR ACTS, and the registry is frozen — a fifth cannot be registered at
runtime, and a test pins the names. The fourth (autonomy-dep-04) was a
DELIBERATE addition to a closed set, not a drift: it exists because the third
shipped reporter for the same freeze (autonomy-dep-03) was cleared by hand and
recurred within a day.

    reap_dead_lease           release `kanban:task:<id>` when the holder pid is
                              PROVABLY dead AND the task is not heartbeating.
                              Cannot-tell means ALIVE (autonomy-adm-03).
    prune_gone_census_entry   drop ONE line from ONE of the enumerated census
                              files when the file that line names no longer
                              exists. The existing `--prune` shape, narrowed to
                              the case a `Path.exists()` can verify.
    restart_stale_daemon      terminate ONE supervised child whose import
                              closure is PROVABLY stale (autonomy-id-02) while a
                              supervisor is UP to restart it (autonomy-id-03).
                              No supervisor, no act — that is a kill, not a
                              restart.
    restore_auto_managed_file `git checkout --` ONE tracked file a reflex
                              REGENERATES in the working tree, when the update
                              guard is blocked on EXACTLY enumerated
                              auto-managed files AND the writer, re-run over the
                              committed text, reproduces the local diff — so the
                              dirt is regenerable, never a human's edit. Then
                              pull through the same guard and re-run the writer
                              on the pulled tree. A diff the writer cannot
                              reproduce REFUSES (autonomy-dep-04).

EVERY ACT HAS THE SAME FOUR STEPS, in this order, and the order is the point:

    prove     re-derive the precondition from PRIMARY evidence, right now.
              `proven` is True | False | None, and None ("cannot tell") REFUSES.
    audit     write the intent row to `audit_trail` BEFORE acting, with
              `raise_on_error=True`. If the row cannot be written the act does
              not run: an unaudited automatic repair is indistinguishable from
              drift. `audit_trail` is append-only — never UPDATE or DELETE.
    apply     the mechanical act. One lease file, one census line, one pid.
    confirm   re-read the world and check the invariant now holds. An act whose
              effect cannot be confirmed is reported `applied_unconfirmed`,
              never `applied`.

WHAT MAKES AN ACT ADMISSIBLE HERE. Mechanical (no judgement in `apply`),
individually verifiable (a single observable fact flips), and reversible (each
act names its own undo). Anything failing one of the three is `propose` — seed a
card, a human decides — and does not belong in this file.

Usage:
    python tools/awareness/restore_acts.py --list
    python tools/awareness/restore_acts.py --plan [--json]          # candidates; acts nothing
    python tools/awareness/restore_acts.py --apply reap_dead_lease --target <task-id>
    python tools/awareness/restore_acts.py --apply prune_gone_census_entry --target <entry>
    python tools/awareness/restore_acts.py --apply restart_stale_daemon --target tools.genesis.daemon
    python tools/awareness/restore_acts.py --apply <act> --target <t> --dry-run   # prove only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.awareness.claim_verifier import TIER  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.project.kanban_project_sync import TRACKED_RELPATH as _PROJECTS_REGISTRY  # noqa: E402

logger = get_logger(__name__)

#: The one audit event type every act writes under. Admitted to
#: `audit_logger.VALID_EVENT_TYPES`; the live CHECK is rebuilt by migration
#: 20260821045946. On a database that has not run it, `log_event` raises and the
#: act is REFUSED — which is the correct reading of "the audit could not be
#: written", not an obstacle to route around.
AUDIT_EVENT_TYPE = "awareness.restore_act"
AUDIT_ACTOR = "restore_acts"

# ── Outcomes ────────────────────────────────────────────────────────────────
#: Precondition not proven (False) or not provable (None). Nothing happened.
REFUSED = "refused"
#: The intent row could not be written. Nothing happened. Reported apart from
#: REFUSED because the repair is different: a migration, not a precondition.
UNAUDITED_REFUSED = "unaudited_refused"
#: `--dry-run`: proven, and deliberately not acted on.
WOULD_APPLY = "would_apply"
#: Applied AND the invariant was re-read and holds.
APPLIED = "applied"
#: Applied, but the invariant could not be confirmed. Never folded into APPLIED.
APPLIED_UNCONFIRMED = "applied_unconfirmed"
#: `apply` raised after the intent row was written. The row stands as evidence.
FAILED = "failed"

#: The census files an entry may be pruned from. Enumerated, never globbed:
#: a glob over `args/*.txt` would make every text file a census.
CENSUS_FILES = (
    "args/undeclared_import_census.txt",
    "args/perfect_score_census.txt",
    "args/kanban_raw_insert_census.txt",
    "args/ci_test_backlog.txt",
    "args/ci_skip_census.txt",
)

#: Only this lease namespace may be reaped. `service:`/`git:`/`migration:`
#: leases guard things with no heartbeat to consult, so the two-signal proof
#: below cannot be made for them.
LEASE_PREFIX = "kanban:task:"

#: How long to wait for a terminated child to leave the process table.
RESTART_CONFIRM_SECONDS = 15.0


@dataclass
class Proof:
    """The re-derived precondition. `proven` None is CANNOT TELL and refuses."""

    proven: Optional[bool]
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RestoreAct:
    name: str
    description: str
    #: How to undo it. Stated per act so the reversibility claim is checkable.
    reverse: str
    prove: Callable[..., Proof]
    apply: Callable[..., Dict[str, Any]]
    confirm: Callable[..., Optional[bool]]


# --------------------------------------------------------------------------- #
# Act 1 — reap a PROVABLY dead lease
# --------------------------------------------------------------------------- #
def _default_heartbeating(task_id: str) -> bool:
    # rem-hyg-15's question, asked through rem-hyg-15's function. autonomy-adm-03
    # consolidates every reaper on it; this is not a third opinion. Imported
    # lazily: the kanban reflex module costs ~1s to import and pulls in the
    # scheduler, which a `--list` should never pay for.
    from tools.genesis.reflexes.kanban import _task_is_heartbeating
    return bool(_task_is_heartbeating(task_id))


def _lease_resource(task_id: str) -> str:
    """A bare task id becomes `kanban:task:<id>`; anything already namespaced is
    passed through UNCHANGED so the namespace check below can refuse it —
    prefixing `service:dashboard` would have smuggled it into scope."""
    return task_id if ":" in task_id else f"{LEASE_PREFIX}{task_id}"


def prove_dead_lease(task_id: str, *, leases=None,
                     heartbeating: Callable[[str], bool] = None, **_: Any) -> Proof:
    """Dead pid AND no heartbeat. Either signal alone is not a proof.

    The pid on the lease is the pid that TOOK it — for a dispatch, the
    scheduler's short-lived child, which exits as soon as it has handed off while
    the worker runs on under another pid. rem-hyg-13 measured
    `holder_is_alive() is False` four seconds after a heartbeat. So the
    heartbeat is the signal about the WORK, and it must be absent too.
    """
    if leases is None:
        from tools.coordination import leases as leases  # noqa: PLW0127
    heartbeating = heartbeating or _default_heartbeating
    resource = _lease_resource(task_id)
    if not resource.startswith(LEASE_PREFIX):
        return Proof(False, f"only {LEASE_PREFIX}* leases are in scope", {"resource": resource})
    bare_id = resource[len(LEASE_PREFIX):]
    meta = leases.holder(resource)
    if meta is None:
        return Proof(False, "no live lease — nothing to reap", {"resource": resource})
    alive = leases.holder_is_alive(resource)
    evidence = {"resource": resource, "holder_pid": meta.get("pid"),
                "holder_session": meta.get("holder_session"),
                "acquired_at": meta.get("acquired_at"), "holder_alive": alive}
    if alive is None:
        return Proof(None, "holder liveness cannot be determined — cannot-tell is ALIVE",
                     evidence)
    if alive:
        return Proof(False, "the holder process is running", evidence)
    try:
        beating = bool(heartbeating(bare_id))
    except Exception as exc:  # noqa: BLE001 — an unreadable heartbeat is not a licence
        evidence["heartbeat_error"] = str(exc)[:200]
        return Proof(None, "heartbeat could not be read — cannot-tell is ALIVE", evidence)
    evidence["task_heartbeating"] = beating
    if beating:
        return Proof(False, "the holder pid is dead but the task IS heartbeating — "
                            "the worker outlived the process that took the lease", evidence)
    return Proof(True, "holder pid is dead and the task is not heartbeating", evidence)


def apply_dead_lease(task_id: str, *, leases=None, **_: Any) -> Dict[str, Any]:
    if leases is None:
        from tools.coordination import leases as leases  # noqa: PLW0127
    # release_stale re-checks liveness under the file lock and refuses a live
    # holder itself — a second, independent refusal between proof and act.
    return {"released": bool(leases.release_stale(_lease_resource(task_id)))}


def confirm_dead_lease(task_id: str, *, leases=None, **_: Any) -> Optional[bool]:
    if leases is None:
        from tools.coordination import leases as leases  # noqa: PLW0127
    try:
        return leases.holder(_lease_resource(task_id)) is None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Act 2 — drop a census entry whose FILE is gone
# --------------------------------------------------------------------------- #
def _entry_key(line: str) -> str:
    """The bare key of a census line: comment and trailing annotation stripped."""
    return line.split("#", 1)[0].strip()


def _entry_path(key: str) -> Optional[str]:
    """The repo-relative path an entry names, or None if it names none.

    Every enumerated census keys on a path first: `<file>::<site>` for the
    site censuses, a bare `<file>` for the test backlog. Anything else is not a
    path claim and is never pruned.
    """
    head = key.split("::", 1)[0].strip()
    if not head or head.startswith("#"):
        return None
    if any(ch in head for ch in "*?[]") or " " in head:
        return None                          # a glob or prose is not a file claim
    if not head.endswith(".py") and "/" not in head:
        return None
    return head.replace("\\", "/")


def _census_lines(root: Path, census: str) -> Optional[List[str]]:
    path = root / census
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None


def gone_census_entries(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every enumerated census line whose named file no longer exists."""
    base = root or _BASE
    out = []
    for census in CENSUS_FILES:
        lines = _census_lines(base, census)
        if lines is None:
            continue
        for line in lines:
            key = _entry_key(line)
            rel = _entry_path(key) if key else None
            if rel and not (base / rel).exists():
                out.append({"census": census, "entry": key, "path": rel})
    return out


def prove_gone_entry(entry: str, *, root: Optional[Path] = None, **_: Any) -> Proof:
    base = root or _BASE
    key = _entry_key(entry)
    rel = _entry_path(key) if key else None
    if not rel:
        return Proof(False, "the entry names no file path — nothing to verify", {"entry": key})
    holders = []
    for census in CENSUS_FILES:
        lines = _census_lines(base, census)
        if lines and any(_entry_key(line) == key for line in lines):
            holders.append(census)
    evidence = {"entry": key, "path": rel, "census_files": holders}
    if not holders:
        return Proof(False, "no enumerated census carries this entry", evidence)
    if len(holders) > 1:
        return Proof(False, "the entry appears in more than one census — not one line", evidence)
    target = base / rel
    if target.exists():
        return Proof(False, "the file still exists — a fixed site is the scanner's "
                            "--prune to decide, not this act's", evidence)
    if not (base / holders[0]).exists():
        return Proof(None, "the census file could not be re-read", evidence)
    return Proof(True, f"{rel} does not exist; {holders[0]} still lists it", evidence)


def apply_gone_entry(entry: str, *, root: Optional[Path] = None, **_: Any) -> Dict[str, Any]:
    base = root or _BASE
    key = _entry_key(entry)
    proof = prove_gone_entry(entry, root=base)
    if not proof.proven:
        raise RuntimeError(f"precondition no longer holds: {proof.reason}")
    census = proof.evidence["census_files"][0]
    path = base / census
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if _entry_key(line) != key]
    dropped = len(lines) - len(kept)
    # ONLY EVER SHRINKS, and writes the file back in its own line convention.
    path.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return {"census": census, "dropped": dropped}


def confirm_gone_entry(entry: str, *, root: Optional[Path] = None, **_: Any) -> Optional[bool]:
    base = root or _BASE
    key = _entry_key(entry)
    for census in CENSUS_FILES:
        lines = _census_lines(base, census)
        if lines is None:
            continue
        if any(_entry_key(line) == key for line in lines):
            return False
    return True


# --------------------------------------------------------------------------- #
# Act 3 — restart a PROVABLY stale daemon, via the supervisor
# --------------------------------------------------------------------------- #
def _service_for_module(module: str):
    """The supervised Service whose script this module is, or None.

    Only a child the supervisor manages can be restarted BY the supervisor; an
    arbitrary process in the identity registry has nothing watching it, and
    terminating it would be a kill. Uses the same `match` fragments
    `launcher._kill_stale_instances` keys on, via `supervisor_status.SERVICES`.
    """
    from tools.genesis.supervisor_status import SERVICES
    stem = module.replace(".", "/")
    for svc in SERVICES:
        frag = svc.match[:-3] if svc.match.endswith(".py") else svc.match
        if stem == frag or stem.endswith("/" + frag):
            return svc
    return None


def _default_cmdline(pid: int) -> Optional[List[str]]:
    try:
        import psutil
        return list(psutil.Process(pid).cmdline() or [])
    except Exception:  # noqa: BLE001 — no psutil, no process, no permission
        return None


def prove_stale_daemon(module: str, *, root: Optional[Path] = None,
                       supervisor_fn: Callable[..., Dict[str, Any]] = None,
                       staleness_fn: Callable[[], Dict[str, Any]] = None,
                       cmdline_fn: Callable[[int], Optional[List[str]]] = None,
                       **_: Any) -> Proof:
    """A supervisor is UP, the module is a supervised child, its recorded
    process is STALE per autonomy-id-02, and the pid's command line says it is
    that child. Every one of the four must hold; a missing psutil refuses."""
    from tools.genesis import supervisor_status as ss
    supervisor_fn = supervisor_fn or (lambda: ss.supervisor(ss.pid_file_for(root)))
    if staleness_fn is None:
        from tools.awareness.code_staleness import report as staleness_fn  # noqa: PLW0127
    cmdline_fn = cmdline_fn or _default_cmdline

    svc = _service_for_module(module)
    if svc is None:
        return Proof(False, f"{module} is not a supervised service — nothing would "
                            f"restart it", {"module": module})
    sup = supervisor_fn()
    evidence: Dict[str, Any] = {"module": module, "service": svc.name,
                                "supervisor": sup}
    if sup.get("state") == "unknown":
        return Proof(None, "the supervisor's state cannot be determined", evidence)
    if sup.get("state") != "up":
        return Proof(False, "no supervisor is running — terminating the child would "
                            "be a kill, not a restart", evidence)

    rep = staleness_fn()
    if rep.get("state") != "measured":
        return Proof(None, f"staleness is unmeasurable: {rep.get('reason')}", evidence)
    rows = [p for p in rep.get("processes", []) if p.get("module") == module]
    if not rows:
        return Proof(False, "no live process recorded for this module", evidence)
    stale = [p for p in rows if p.get("verdict") == "stale"]
    if not stale:
        verdicts = sorted({str(p.get("verdict")) for p in rows})
        kind = None if "unmeasurable" in verdicts else False
        return Proof(kind, f"the process is not provably stale ({', '.join(verdicts)})",
                     dict(evidence, verdicts=verdicts))
    if len(stale) > 1:
        return Proof(False, f"{len(stale)} stale processes recorded for one module — "
                            f"not one pid", dict(evidence, pids=[p.get("pid") for p in stale]))
    proc = stale[0]
    pid = proc.get("pid")
    evidence.update(pid=pid, code_version=proc.get("code_version"),
                    changed_in_closure=proc.get("changed_in_closure", [])[:10],
                    changed_count=proc.get("changed_count"))
    if not isinstance(pid, int) or pid <= 0:
        return Proof(None, "the identity row records no usable pid", evidence)
    if pid == os.getpid():
        return Proof(False, "the stale process is THIS process", evidence)
    if pid == sup.get("pid"):
        return Proof(False, "the pid is the supervisor itself", evidence)
    argv = cmdline_fn(pid)
    if argv is None:
        return Proof(None, "the pid's command line cannot be read — it is not proven "
                           "to be the child", evidence)
    if "-c" in argv:
        argv = argv[: argv.index("-c")]
    joined = " ".join(argv)
    evidence["cmdline_matches_service"] = svc.match in joined
    if svc.match not in joined:
        return Proof(False, f"pid {pid}'s command line does not run {svc.match} — "
                            f"a reused pid, or the wrong process", evidence)
    return Proof(True, f"{svc.name} (pid {pid}) imports {proc.get('changed_count')} "
                       f"changed file(s); supervisor pid {sup.get('pid')} will restart it",
                 evidence)


def apply_stale_daemon(module: str, *, evidence: Dict[str, Any] = None,
                       kill_fn: Callable[[int, bool], bool] = None, **_: Any) -> Dict[str, Any]:
    if kill_fn is None:
        from tools.compat.platform_utils import kill_process as kill_fn  # noqa: PLW0127
    pid = int((evidence or {})["pid"])
    # Graceful: terminate, never kill. The supervisor's monitor loop sees the
    # exit on its next 30s tick and starts a fresh child from the current tree.
    return {"pid": pid, "terminate_sent": bool(kill_fn(pid, False))}


def confirm_stale_daemon(module: str, *, evidence: Dict[str, Any] = None,
                         pid_exists_fn: Callable[[int], bool] = None,
                         sleep: Callable[[float], None] = time.sleep,
                         wait_seconds: float = RESTART_CONFIRM_SECONDS,
                         **_: Any) -> Optional[bool]:
    if pid_exists_fn is None:
        from tools.compat.platform_utils import pid_exists as pid_exists_fn  # noqa: PLW0127
    pid = int((evidence or {})["pid"])
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            if not pid_exists_fn(pid):
                return True
        except Exception:  # noqa: BLE001
            return None
        if time.monotonic() >= deadline:
            return False
        sleep(0.5)


# --------------------------------------------------------------------------- #
# Act 4 — restore an AUTO-MANAGED tracked file the deployment is frozen on
# --------------------------------------------------------------------------- #
#: The tracked files a reflex REGENERATES in the working tree, repo-relative
#: path -> the writer module's dotted name. ENUMERATED, and declared by the
#: WRITER (`kanban_project_sync.TRACKED_RELPATH`), never spelled here by hand:
#: a path this module typed itself could drift from the one the writer actually
#: touches, and the proof below would then prove the wrong file. One entry
#: today. Adding one is a decision about a writer, made next to that writer.
AUTO_MANAGED_FILES: Mapping[str, str] = MappingProxyType({
    _PROJECTS_REGISTRY: "tools.project.kanban_project_sync",
})

#: The guard's one refusal this act can answer. Any other reason — not on main,
#: a merge in flight, a non-fast-forward — is not a regenerable file's fault.
OVERLAP_REASON = "local changes would be lost"
#: `pull_if_safe(dry_run=True)` verdicts that mean "no overlap stands".
_NO_OVERLAP_REASONS = frozenset({"already current", "would pull"})


def _rel(target: str) -> str:
    text = str(target or "").replace("\\", "/").strip()
    return text[2:] if text.startswith("./") else text


def _default_git(args: List[str], root: Path):
    import subprocess  # nosec B404 — git only, fixed argv, shell=False
    return subprocess.run(  # nosec B603 B607 — fixed argv, shell=False
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, check=False, shell=False,
    )


def _default_freshness(root: Path) -> Dict[str, Any]:
    # The SAME reporter autonomy-dep-03 ships, which asks the SAME guard
    # (`code_reload.pull_if_safe`, dry run). Not a second copy of the ladder.
    from tools.genesis.deployment_freshness import freshness
    return freshness(root=str(root))


def _default_board() -> Dict[str, Any]:
    from tools.project.kanban_project_sync import _scan_db
    return _scan_db()


def _default_pull(root: Path) -> Dict[str, Any]:
    # THROUGH the guard, never a bare `git pull`: the act restores one file so
    # that the guard's own ladder passes; it does not overrule the ladder.
    from tools.genesis.code_reload import pull_if_safe
    return pull_if_safe(root, min_interval=0)


def _default_probe(root: Path) -> Dict[str, Any]:
    from tools.genesis.code_reload import pull_if_safe
    return pull_if_safe(root, dry_run=True)


def _default_sync(path: Path, board: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    from tools.project.kanban_project_sync import sync_projects
    return sync_projects(path=path, board=board)


def _default_regenerate(head_text: str, board: Dict[str, Any]) -> List[Dict[str, Any]]:
    from tools.project.kanban_project_sync import regenerated_projects
    return regenerated_projects(head_text, board)


def _projects_of(text: str) -> Optional[List[Dict[str, Any]]]:
    """The registry's project list, or None if the text is not a registry."""
    from yaml import YAMLError, safe_load
    try:
        data = safe_load(text) if text else {}
    except YAMLError:
        return None
    if data is None:
        return []
    if not isinstance(data, dict):
        return None
    projects = data.get("projects")
    if projects is None:
        return []
    if not isinstance(projects, list) or not all(isinstance(p, dict) for p in projects):
        return None
    return projects


def regenerable_diff(head_projects: List[Dict[str, Any]],
                     working_projects: List[Dict[str, Any]],
                     regen_projects: List[Dict[str, Any]]
                     ) -> Tuple[bool, str, Dict[str, Any]]:
    """Is every difference HEAD -> working something the writer ADDS from the board?

    The writer only ever APPENDS — a new project, or missing epics under an
    existing one — and never renames, removes or edits what a human wrote
    (its own contract: "name/description/briefs set by humans are never
    overwritten"). So a working copy is regenerable exactly when (1) every
    committed project survives unchanged apart from epics appended at the end,
    and (2) every addition is one the writer, re-run over the committed text
    against the CURRENT board, would also make. The board may have grown since
    the dirt was written, so the regeneration may carry MORE than the working
    copy — that is fine; what it may not do is carry LESS, or differ.

    A working copy SEMANTICALLY equal to HEAD (the pre-dep-03 whole-document
    writer reflowed the file without changing a value) is regenerable too:
    nothing is lost by restoring it. Returns ``(ok, reason, evidence)``.
    """
    head = {str(p.get("key")): p for p in head_projects}
    work = {str(p.get("key")): p for p in working_projects}
    regen = {str(p.get("key")): p for p in regen_projects}
    added_projects: List[str] = []
    added_epics: List[str] = []

    def _fields(*entries: Dict[str, Any]) -> set:
        return {f for e in entries for f in e if f != "epics"}

    for key, hp in head.items():
        wp = work.get(key)
        if wp is None:
            return (False, f"project '{key}' is committed and missing from the working "
                           f"copy — the writer never removes a project", {"project": key})
        for field_name in sorted(_fields(hp, wp)):
            if hp.get(field_name) != wp.get(field_name):
                return (False, f"project '{key}' field '{field_name}' differs from HEAD — "
                               f"a human edit, which this act never reverts",
                        {"project": key, "field": field_name})
        h_epics = list(hp.get("epics") or [])
        w_epics = list(wp.get("epics") or [])
        if w_epics[:len(h_epics)] != h_epics:
            return (False, f"project '{key}' has committed epics edited, removed or "
                           f"reordered — the writer only appends", {"project": key})
        r_epics = list((regen.get(key) or {}).get("epics") or [])
        for epic in w_epics[len(h_epics):]:
            if epic not in r_epics:
                return (False, f"epic '{epic.get('key')}' under '{key}' is not one the "
                               f"writer would add from the board",
                        {"project": key, "epic": epic.get("key")})
            added_epics.append(f"{key}/{epic.get('key')}")

    for key, wp in work.items():
        if key in head:
            continue
        rp = regen.get(key)
        if rp is None:
            return (False, f"project '{key}' is not one the writer would register from "
                           f"the board", {"project": key})
        for field_name in sorted(_fields(wp, rp)):
            if wp.get(field_name) != rp.get(field_name):
                return (False, f"project '{key}' field '{field_name}' is not what the "
                               f"writer would produce — a human edited an auto-registered "
                               f"entry in the working tree", {"project": key, "field": field_name})
        r_epics = list(rp.get("epics") or [])
        for epic in wp.get("epics") or []:
            if epic not in r_epics:
                return (False, f"epic '{epic.get('key')}' under '{key}' is not one the "
                               f"writer would add from the board",
                        {"project": key, "epic": epic.get("key")})
        added_projects.append(key)

    evidence = {"added_projects": added_projects, "added_epics": added_epics,
                "format_only": not added_projects and not added_epics}
    if evidence["format_only"]:
        return True, "the working copy equals HEAD in content — formatting drift only", evidence
    return (True, f"the diff is {len(added_projects)} project(s) and {len(added_epics)} "
                  f"epic(s) the writer adds from the board", evidence)


def prove_auto_managed_file(target: str, *, root: Optional[Path] = None,
                            freshness_fn: Callable[[Path], Dict[str, Any]] = None,
                            board_fn: Callable[[], Dict[str, Any]] = None,
                            git_fn: Callable[[List[str], Path], Any] = None,
                            regenerate_fn: Callable[..., List[Dict[str, Any]]] = None,
                            **_: Any) -> Proof:
    """Enumerated file, guard blocked on EXACTLY enumerated files, and the
    writer reproduces the local diff. Every one of the three must hold.

    The human-edit guarantee lives in the third: a name, description or brief
    that differs from HEAD is not something the writer produces, so the proof
    is False and nothing is checked out. An unreadable board, HEAD or working
    copy is None — "cannot tell" never becomes "safe to discard".
    """
    base = Path(root or _BASE)
    rel = _rel(target)
    freshness_fn = freshness_fn or _default_freshness
    board_fn = board_fn or _default_board
    git_fn = git_fn or _default_git
    regenerate_fn = regenerate_fn or _default_regenerate

    if rel not in AUTO_MANAGED_FILES:
        return Proof(False, f"{rel or '<empty>'} is not an enumerated auto-managed file; "
                            f"the set is {sorted(AUTO_MANAGED_FILES)}",
                     {"path": rel, "auto_managed": sorted(AUTO_MANAGED_FILES)})
    try:
        rep = freshness_fn(base)
    except Exception as exc:  # noqa: BLE001
        return Proof(None, f"deployment freshness could not be asked: {exc}", {"path": rel})
    state = str(rep.get("state") or "")
    conflicts = [_rel(c) for c in (rep.get("conflicts") or [])]
    evidence: Dict[str, Any] = {
        "path": rel, "root": str(base), "writer": AUTO_MANAGED_FILES[rel],
        "freshness": {"state": state, "behind_by": rep.get("behind_by"),
                      "reason": rep.get("reason"), "conflicts": conflicts},
    }
    if state == "unmeasurable":
        return Proof(None, f"deployment freshness is unmeasurable: {rep.get('reason')}",
                     evidence)
    if state != "blocked":
        return Proof(False, f"the deployment is {state or 'unknown'} — nothing to restore",
                     evidence)
    if str(rep.get("reason") or "") != OVERLAP_REASON or rel not in conflicts:
        return Proof(False, f"the guard refuses for another reason ({rep.get('reason')}), "
                            f"not an overlap on {rel}", evidence)
    foreign = [c for c in conflicts if c not in AUTO_MANAGED_FILES]
    if foreign:
        return Proof(False, f"the guard is also blocked on {len(foreign)} file(s) NO writer "
                            f"regenerates ({', '.join(foreign[:3])}) — restoring {rel} would "
                            f"not unblock it, and those may be a human's work",
                     dict(evidence, foreign=foreign))

    shown = git_fn(["show", f"HEAD:{rel}"], base)
    if getattr(shown, "returncode", 1) != 0:
        return Proof(None, f"HEAD:{rel} could not be read", evidence)
    head_text = getattr(shown, "stdout", "") or ""
    try:
        working_text = (base / rel).read_text(encoding="utf-8")
    except OSError as exc:
        return Proof(None, f"the working copy could not be read: {exc}", evidence)
    try:
        board = board_fn()
    except Exception as exc:  # noqa: BLE001
        return Proof(None, f"the board could not be read — regenerability cannot be "
                           f"proven: {exc}", evidence)
    if not board:
        return Proof(None, "the board reports no card-shaped tasks — regenerability "
                           "cannot be proven against an empty board", evidence)
    head_projects = _projects_of(head_text)
    working_projects = _projects_of(working_text)
    if head_projects is None or working_projects is None:
        return Proof(None, "HEAD or the working copy is not a parseable registry — "
                           "cannot tell what would be lost", evidence)
    try:
        regen_projects = regenerate_fn(head_text, board)
    except Exception as exc:  # noqa: BLE001
        return Proof(None, f"the writer could not be re-run over HEAD: {exc}", evidence)
    ok, why, detail = regenerable_diff(head_projects, working_projects, regen_projects)
    evidence.update(detail)
    if not ok:
        return Proof(False, why, evidence)
    return Proof(True, f"{rel}: {why}; the guard is blocked on it alone, "
                       f"{rep.get('behind_by')} commit(s) behind", evidence)


def apply_auto_managed_file(target: str, *, root: Optional[Path] = None,
                            git_fn: Callable[[List[str], Path], Any] = None,
                            pull_fn: Callable[[Path], Dict[str, Any]] = None,
                            sync_fn: Callable[..., Dict[str, Any]] = None,
                            board_fn: Callable[[], Dict[str, Any]] = None,
                            **deps: Any) -> Dict[str, Any]:
    """checkout -> pull through the guard -> re-run the writer. In that order.

    The pull sits BETWEEN the other two on purpose: re-running the writer first
    would re-dirty the file with the very cards that made it dirty, and the
    guard would refuse again. Re-running it AFTER the pull derives the board's
    cards on top of whatever the incoming commits registered by hand.
    """
    base = Path(root or _BASE)
    rel = _rel(target)
    git_fn = git_fn or _default_git
    pull_fn = pull_fn or _default_pull
    sync_fn = sync_fn or _default_sync
    # Re-proven at the moment of acting, like apply_gone_entry: the board or
    # the tree may have moved since the intent row was written.
    proof = prove_auto_managed_file(target, root=base, git_fn=git_fn,
                                    board_fn=board_fn, **deps)
    if not proof.proven:
        raise RuntimeError(f"precondition no longer holds: {proof.reason}")
    board = board_fn() if board_fn is not None else None
    out = git_fn(["checkout", "--", rel], base)
    if getattr(out, "returncode", 1) != 0:
        raise RuntimeError(f"git checkout -- {rel} failed: "
                           f"{(getattr(out, 'stderr', '') or '').strip()[:200]}")
    result: Dict[str, Any] = {"restored": rel}
    result["pull"] = pull_fn(base)
    synced = sync_fn(base / rel, board)
    result["sync"] = {k: synced.get(k) for k in ("new_projects", "updated_projects", "written")}
    return result


def confirm_auto_managed_file(target: str, *, root: Optional[Path] = None,
                              probe_fn: Callable[[Path], Dict[str, Any]] = None,
                              **_: Any) -> Optional[bool]:
    """Ask the guard again, dry run: does it still name this file?"""
    base = Path(root or _BASE)
    rel = _rel(target)
    probe_fn = probe_fn or _default_probe
    try:
        verdict = probe_fn(base)
    except Exception:  # noqa: BLE001
        return None
    reason = str(verdict.get("reason") or "")
    conflicts = [_rel(c) for c in (verdict.get("conflicts") or [])]
    if reason in _NO_OVERLAP_REASONS:
        return True
    if reason == OVERLAP_REASON:
        return rel not in conflicts
    return None


# --------------------------------------------------------------------------- #
# The closed set
# --------------------------------------------------------------------------- #
ACTS: Mapping[str, RestoreAct] = MappingProxyType({
    "reap_dead_lease": RestoreAct(
        name="reap_dead_lease",
        description="release kanban:task:<id> when its holder pid is dead AND the "
                    "task is not heartbeating; cannot-tell is alive",
        reverse="python tools/kanban/cli.py --claim <task-id> re-takes the lease",
        prove=prove_dead_lease, apply=apply_dead_lease, confirm=confirm_dead_lease,
    ),
    "prune_gone_census_entry": RestoreAct(
        name="prune_gone_census_entry",
        description="drop one census line whose named file no longer exists, from "
                    "one of the enumerated census files; only ever shrinks",
        reverse="git checkout -- <census file> restores the line",
        prove=prove_gone_entry, apply=apply_gone_entry, confirm=confirm_gone_entry,
    ),
    "restart_stale_daemon": RestoreAct(
        name="restart_stale_daemon",
        description="terminate one supervised child whose import closure is stale, "
                    "while a supervisor is up to restart it from the current tree",
        reverse="nothing to undo — the supervisor restarts it within 30s; if the "
                "supervisor is gone, python tools/genesis/supervisor_status.py --ensure",
        prove=prove_stale_daemon, apply=apply_stale_daemon, confirm=confirm_stale_daemon,
    ),
    "restore_auto_managed_file": RestoreAct(
        name="restore_auto_managed_file",
        description="git checkout -- one enumerated auto-managed tracked file the "
                    "update guard is blocked on, only when its writer re-run over HEAD "
                    "reproduces the local diff; then pull through the guard and re-run "
                    "the writer on the pulled tree. A human edit refuses",
        reverse="nothing to undo — the discarded diff was, by proof, what the writer "
                "re-derives from the board, and the re-run after the pull re-derives it",
        prove=prove_auto_managed_file, apply=apply_auto_managed_file,
        confirm=confirm_auto_managed_file,
    ),
})

assert "restore" in TIER, "claim_verifier.TIER must still name the restore tier"


def _default_audit(action: str, details: Dict[str, Any]) -> int:
    from tools.audit.audit_logger import log_event
    return log_event(
        AUDIT_EVENT_TYPE, AUDIT_ACTOR, action, details=details,
        raise_on_error=True,
    )


def perform(act_name: str, target: str, *, dry_run: bool = False,
            audit: Callable[[str, Dict[str, Any]], Any] = None,
            **deps: Any) -> Dict[str, Any]:
    """Run one act end to end: prove -> audit intent -> apply -> confirm.

    Never raises. ``deps`` are passed to every step (test seams: a fake
    ``leases`` module, a ``root``, a ``heartbeating`` callable, ...).
    """
    audit = audit or _default_audit
    if act_name not in ACTS:
        return {"act": act_name, "target": target, "outcome": REFUSED,
                "reason": f"not an enumerated act; the set is {sorted(ACTS)}"}
    act = ACTS[act_name]
    result: Dict[str, Any] = {"act": act_name, "target": target,
                              "reverse": act.reverse, "audited": False}
    try:
        proof = act.prove(target, **deps)
    except Exception as exc:  # noqa: BLE001
        proof = Proof(None, f"the proof itself failed: {exc}")
    result.update(proven=proof.proven, reason=proof.reason, evidence=proof.evidence)
    if proof.proven is not True:
        result["outcome"] = REFUSED
        return result
    if dry_run:
        result["outcome"] = WOULD_APPLY
        return result

    # THE ROW BEFORE THE ACT. Fail-closed: no row, no act.
    intent = {"target": target, "reason": proof.reason, "evidence": proof.evidence,
              "reverse": act.reverse, "tier": "restore"}
    try:
        result["audit_id"] = audit(f"restore.{act_name}.intent", intent)
        result["audited"] = True
    except Exception as exc:  # noqa: BLE001
        result.update(outcome=UNAUDITED_REFUSED,
                      reason=f"the intent row could not be written: {exc}")
        logger.warning("restore_acts: %s on %s refused — unaudited: %s",
                       act_name, target, exc)
        return result

    try:
        result["applied"] = act.apply(target, evidence=proof.evidence, **deps)
    except Exception as exc:  # noqa: BLE001
        result.update(outcome=FAILED, reason=f"apply failed: {exc}")
        _audit_outcome(audit, act_name, result)
        return result

    try:
        confirmed = act.confirm(target, evidence=proof.evidence, **deps)
    except Exception:  # noqa: BLE001
        confirmed = None
    result["confirmed"] = confirmed
    result["outcome"] = APPLIED if confirmed is True else APPLIED_UNCONFIRMED
    _audit_outcome(audit, act_name, result)
    return result


def _audit_outcome(audit, act_name: str, result: Dict[str, Any]) -> None:
    """The outcome row. Best-effort — the act has already happened — but its
    failure is REPORTED on the result, never swallowed."""
    payload = {k: result.get(k) for k in ("target", "applied", "confirmed", "reason")}
    try:
        audit(f"restore.{act_name}.{result['outcome']}", payload)
        result["outcome_audited"] = True
    except Exception as exc:  # noqa: BLE001
        result["outcome_audited"] = False
        result["outcome_audit_error"] = str(exc)[:200]


def plan(root: Optional[Path] = None, **deps: Any) -> Dict[str, Any]:
    """Every candidate each act would currently accept. ACTS NOTHING.

    Each candidate is re-proven here, so the plan shows the refusals too — a
    list of only the things that would pass is a list that hides why the rest
    would not.
    """
    base = root or _BASE
    out: Dict[str, Any] = {"acts": sorted(ACTS), "candidates": []}

    leases = deps.get("leases")
    if leases is None:
        try:
            from tools.coordination import leases
        except Exception:  # noqa: BLE001
            leases = None
    out["leases_state"] = "unmeasurable" if leases is None else "measured"
    if leases is not None:
        try:
            held = [m for m in leases.list_leases()
                    if str(m.get("resource", "")).startswith(LEASE_PREFIX)]
        except Exception:  # noqa: BLE001
            held = []
            out["leases_state"] = "unmeasurable"
        out["leases_held"] = len(held)
        for meta in held:
            target = str(meta["resource"])[len(LEASE_PREFIX):]
            p = prove_dead_lease(target, leases=leases, heartbeating=deps.get("heartbeating"))
            out["candidates"].append({"act": "reap_dead_lease", "target": target,
                                      "proven": p.proven, "reason": p.reason})

    out["census_files_read"] = sum(
        1 for c in CENSUS_FILES if _census_lines(base, c) is not None)
    for gone in gone_census_entries(base):
        p = prove_gone_entry(gone["entry"], root=base)
        out["candidates"].append({"act": "prune_gone_census_entry", "target": gone["entry"],
                                  "census": gone["census"], "proven": p.proven,
                                  "reason": p.reason})

    staleness_fn = deps.get("staleness_fn")
    if staleness_fn is None:
        try:
            from tools.awareness.code_staleness import report as staleness_fn
        except Exception:  # noqa: BLE001
            staleness_fn = None
    out["staleness_state"] = "unmeasurable"
    if staleness_fn is not None:
        try:
            rep = staleness_fn()
        except Exception as exc:  # noqa: BLE001
            rep = {"state": "unmeasurable", "reason": str(exc)[:200]}
        out["staleness_state"] = rep.get("state")
        cached = (lambda _rep=rep: _rep)
        for proc in rep.get("processes", []) or []:
            if proc.get("verdict") != "stale" or not proc.get("module"):
                continue
            p = prove_stale_daemon(proc["module"], root=base, staleness_fn=cached,
                                   supervisor_fn=deps.get("supervisor_fn"),
                                   cmdline_fn=deps.get("cmdline_fn"))
            out["candidates"].append({"act": "restart_stale_daemon", "target": proc["module"],
                                      "proven": p.proven, "reason": p.reason})

    freshness_fn = deps.get("freshness_fn") or _default_freshness
    try:
        fresh = freshness_fn(base)
    except Exception as exc:  # noqa: BLE001
        fresh = {"state": "unmeasurable", "reason": str(exc)[:200], "conflicts": []}
    out["freshness_state"] = fresh.get("state")
    if fresh.get("state") == "blocked":
        cached_fresh = (lambda _root, _rep=fresh: _rep)
        for path in fresh.get("conflicts") or []:
            rel = _rel(path)
            if rel in AUTO_MANAGED_FILES:
                p = prove_auto_managed_file(rel, root=base, freshness_fn=cached_fresh,
                                            board_fn=deps.get("board_fn"),
                                            git_fn=deps.get("git_fn"),
                                            regenerate_fn=deps.get("regenerate_fn"))
                proven, reason = p.proven, p.reason
            else:
                # Listed as a REFUSAL, not omitted: a blocked deployment whose
                # plan shows no candidate reads as "nothing to do".
                proven, reason = False, (f"{rel} is locally modified and incoming, and "
                                         f"no enumerated writer regenerates it — a "
                                         f"human's work, or an unregistered writer")
            out["candidates"].append({"act": "restore_auto_managed_file", "target": rel,
                                      "proven": proven, "reason": reason})

    out["provable"] = sum(1 for c in out["candidates"] if c["proven"] is True)
    out["refused"] = sum(1 for c in out["candidates"] if c["proven"] is not True)
    return out


def render_plan(rep: Dict[str, Any]) -> str:
    out = [f"Restore tier — {len(rep['acts'])} enumerated act(s): {', '.join(rep['acts'])}",
           f"  candidates {len(rep['candidates'])} · provable {rep['provable']} · "
           f"refused {rep['refused']}   (a plan ACTS NOTHING)", ""]
    mark = {True: " READY", False: " no   ", None: "  ??  "}
    for c in rep["candidates"]:
        out.append(f"{mark[c['proven']]} {c['act']:24} {c['target']}")
        out.append(f"        {c['reason']}")
    if not rep["candidates"]:
        out.append("  no candidate — no held task lease, no gone census entry, "
                   "no stale supervised child, no blocked auto-managed file among "
                   "what was MEASURED:")
    # What the plan could see. "No candidate" over an unmeasured fleet is not a
    # clean bill of health, so the coverage is printed beside the verdict.
    out.append(f"  measured: leases={rep.get('leases_state')} · "
               f"census_files={rep.get('census_files_read')}/{len(CENSUS_FILES)} · "
               f"staleness={rep.get('staleness_state')} · "
               f"freshness={rep.get('freshness_state')}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true", help="the closed set of acts")
    parser.add_argument("--plan", action="store_true", help="candidates; acts nothing")
    parser.add_argument("--apply", choices=sorted(ACTS), help="perform ONE act")
    parser.add_argument("--target", help="task id / census entry / module for --apply")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --apply: prove, write no audit row, act on nothing")
    parser.add_argument("--root", help="checkout to act on (default: this module's tree)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else None

    if args.list:
        for name in sorted(ACTS):
            a = ACTS[name]
            print(f"  {name:26} {a.description}")
            print(f"  {'':26} undo: {a.reverse}")
        print(f"\n  tier text: {TIER['restore']}")
        return 0

    if args.apply:
        if not args.target:
            parser.error("--apply requires --target")
        result = perform(args.apply, args.target, dry_run=args.dry_run, root=root)
        print(json.dumps(result, indent=2, default=str) if args.json else
              f"{result['outcome']}: {args.apply} {args.target}\n  {result.get('reason', '')}")
        return 0 if result["outcome"] in (APPLIED, WOULD_APPLY) else 1

    rep = plan(root)
    print(json.dumps(rep, indent=2, default=str) if args.json else render_plan(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
