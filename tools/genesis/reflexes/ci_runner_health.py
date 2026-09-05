#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex -- CI Runner Health (ci_runner_health, mfx-boot-02).

WHAT IT AUTOMATES. Measured 2026-09-03, and twice before: every docker runner
(icdev-ft-runner, -2, -3, icdev-rt-runner) sat in ``Restarting (1)`` for hours
with ``Http response code: NotFound from 'POST .../actions/runner-registration'``
-- an expired registration token baked into the container's environment --
while the forge listed each one ``offline`` and every job in icdev_ft /
icdev_rt QUEUED with no red anywhere. The recovery is documented in the runner
README and was performed BY HAND each time: mint a token, ``docker compose -p
<project> up -d`` with RUNNER_TOKEN / RUNNER_CONTAINER / RUNNER_NAME. This
reflex performs exactly that act, for exactly the containers declared in
``args/ci_runners.yaml``, and nothing else.

TWO SIGNALS, AND ONLY THEIR INTERSECTION IS ACTED ON. Each run reads the
forge's runner list (``gh api repos/<repo>/actions/runners`` -> the offline
set) and the docker process table (``docker ps -a`` -> the Restarting set).
A container in BOTH is a candidate. A container in only one is REPORTED and
never touched: offline-but-Up is a network event a fresh token does not fix,
and Restarting-but-online is a race the next poll settles. A candidate is then
PROVEN from its own log: the tail must carry a registration-failure signature
(every one in the config was taken from a live crash loop). A container crash-
looping for any other reason is ``restarting_unproven`` -- re-registering it
would fix nothing and recreating it would destroy the evidence.

EVERY ACT IS prove -> audit -> apply -> confirm, IN THAT ORDER, the shape
``tools/awareness/restore_acts.py`` established. The intent row is written to
audit_trail BEFORE the act with ``raise_on_error=True``; no row, no act
(``unaudited_refused``). The apply is ONE ``docker compose up -d``; this
module never runs ``docker rm``, ``docker compose down`` or anything that
drops a volume, and a test reads its source to prove it. Confirm re-reads
docker AND the forge: ``applied`` means the forge lists the runner online
again; ``applied_unconfirmed`` is never reported as ``applied``.

THE TOKEN IS NEVER PERSISTED. It is minted at the moment of the act, handed to
compose through the process ENVIRONMENT (never argv -- the process table shows
argv), and appears in no audit row, log line or report field. The declaration
file holds none, by test.

BOUNDED, COOLED, REPORTED. ``max_reregistrations_per_run`` caps the acts one
cycle performs; further candidates are proven and reported ``deferred``. A
container re-registered inside ``reregister_cooldown_seconds`` (read from this
reflex's OWN intent rows in audit_trail -- no new table) is refused
``recently_acted``: a runner still crash-looping after a fresh token has a
different defect that a second token cannot repair. A container whose live
compose-project label disagrees with the declaration is refused: that is the
cross-fleet adoption the RT compose file documents (2026-08-30).

UNMEASURABLE IS NEVER SUCCESS-SHAPED. No declaration, an empty fleet, an
unreadable docker daemon, or a forge that cannot answer for a repo reports
``unmeasurable`` with the reason named -- never a clean sweep it did not make.
``success`` stays True for the daemon's circuit breaker, as lease_litter_reflex
and claim_verifier_reflex do and for the same reason: a host with no docker
must not trip the breaker and make the reflex permanently inert.

Cadence: every 15 minutes (args/genesis_config.yaml). A queued job is the cost
of waiting, and the measured firing rate is ~weekly. GREEN tier: two read-only
forge calls, one docker ps, and -- on a proven candidate -- one compose up.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# sys.path BOOTSTRAP ONLY (kax-conflict-04): resolves the IMPORT root so that
# `python <this file>` can find first-party code at all. It is never used as a
# fact about where the repo is -- that is repo_root()'s job below (xit-decl-03).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

#: The ONE root resolver.
BASE_DIR = repo_root(__file__)

logger = get_logger("icdev.genesis.ci_runner_health")

REFLEX_NAME = "ci_runner_health"
DECLARATION_PATH = BASE_DIR / "args" / "ci_runners.yaml"
DECLARATION_ENV = "ICDEV_CI_RUNNERS_CONFIG"

METRIC_NAME = "runners_reregistered"

STATUS_OK = "ok"                       # measured; every declared runner healthy
STATUS_FINDINGS = "findings"           # something was acted on, deferred, or reported
STATUS_UNMEASURABLE = "unmeasurable"   # docker, the forge or the declaration could not answer
STATUS_ERROR = "error"

# Classification of one declared container from the two signals.
CLASS_HEALTHY = "healthy"                          # online x running
CLASS_CANDIDATE = "candidate"                      # offline x restarting -- THE case
CLASS_OFFLINE_BUT_UP = "offline_but_up"            # offline x running: a network event, never touched
CLASS_RESTARTING_BUT_ONLINE = "restarting_but_online"  # online x restarting: a race, never touched
CLASS_STOPPED = "stopped"                          # exited/created/dead/paused: somebody stopped it
CLASS_CONTAINER_ABSENT = "container_absent"        # docker has no such container
CLASS_FORGE_UNMEASURED = "forge_unmeasured"        # the forge could not answer for this repo

# Outcomes of an act on a candidate.
APPLIED = "applied"
APPLIED_UNCONFIRMED = "applied_unconfirmed"
WOULD_APPLY = "would_apply"
REFUSED = "refused"
UNAUDITED_REFUSED = "unaudited_refused"
FAILED = "failed"
DEFERRED = "deferred"

# Audit vocabulary. One EXISTING event type -- `self_heal_triggered` is in
# VALID_EVENT_TYPES already, so no CHECK rebuild and no migration -- with the
# surface namespaced in `action`, the migration_canvas / zt.stub_gate precedent.
AUDIT_EVENT_TYPE = "self_heal_triggered"
AUDIT_ACTOR = "genesis:ci_runner_health"
ACTION_INTENT = "ci_runner_health.reregister.intent"
ACTION_PREFIX = "ci_runner_health.reregister."

DEFAULT_MAX_PER_RUN = 2
DEFAULT_COOLDOWN_SECONDS = 6 * 3600
DEFAULT_CONFIRM_WAIT = 90
DEFAULT_CONFIRM_POLL = 10
DEFAULT_LOG_TAIL = 40
DEFAULT_GH_TIMEOUT = 30
DEFAULT_DOCKER_TIMEOUT = 60
DEFAULT_COMPOSE_TIMEOUT = 300

_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")

#: Test seams. A test substitutes the command runner and the clock; production
#: leaves them None and gets subprocess.run / time.sleep.
_COMMAND_RUNNER: Optional[Callable[..., subprocess.CompletedProcess]] = None
_SLEEP: Optional[Callable[[float], None]] = None


# --------------------------------------------------------------------------- #
# declaration
# --------------------------------------------------------------------------- #
def declaration_path() -> Path:
    override = os.environ.get(DECLARATION_ENV, "").strip()
    return Path(override) if override else DECLARATION_PATH


def load_declaration(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The declared fleet, or None when it cannot be read. None is the honest
    answer: an empty dict would read as a clean fleet."""
    p = path or declaration_path()
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ci_runner_health: declaration unreadable at %s: %s", p, exc)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("repos"), list):
        logger.warning("ci_runner_health: declaration at %s has no `repos` list", p)
        return None
    return data


def _int(cfg: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(cfg.get(key) if cfg.get(key) is not None else default)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def _exec(argv: List[str], *, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None,
          timeout: float = 60.0) -> subprocess.CompletedProcess:
    runner = _COMMAND_RUNNER or subprocess.run
    return runner(
        argv, cwd=str(cwd) if cwd else None, env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def _sleep(seconds: float) -> None:
    (_SLEEP or time.sleep)(seconds)


def _binary(cfg: Dict[str, Any], key: str, name: str) -> Optional[str]:
    declared = str(cfg.get(key) or name)
    return shutil.which(declared) or (declared if Path(declared).exists() else None)


def forge_runners(repo: str, gh: str, timeout: float) -> Optional[Dict[str, str]]:
    """``{runner_name: status}`` for one repo, or None when gh cannot answer."""
    try:
        proc = _exec([gh, "api", f"repos/{repo}/actions/runners", "--paginate"], timeout=timeout)
        if proc.returncode != 0:
            logger.warning("ci_runner_health: gh api runners failed for %s: %s", repo, (proc.stderr or "")[:300])
            return None
        out: Dict[str, str] = {}
        # --paginate concatenates one JSON object per page.
        for chunk in _json_objects(proc.stdout or ""):
            for r in chunk.get("runners", []) or []:
                if isinstance(r, dict) and r.get("name"):
                    out[str(r["name"])] = str(r.get("status") or "unknown")
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("ci_runner_health: gh unreadable for %s: %s", repo, exc)
        return None


def _json_objects(text: str) -> List[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    try:
        one = json.loads(text)
        return [one] if isinstance(one, dict) else []
    except json.JSONDecodeError:
        pass
    objs: List[Dict[str, Any]] = []
    dec = json.JSONDecoder()
    i = 0
    while i < len(text):
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            objs.append(obj)
        i = end
        while i < len(text) and text[i].isspace():
            i += 1
    return objs


def docker_containers(docker: str, timeout: float) -> Optional[Dict[str, Dict[str, str]]]:
    """``{container: {state, status, project}}`` for every container, or None
    when the daemon cannot answer."""
    fmt = '{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Label "com.docker.compose.project"}}'
    try:
        proc = _exec([docker, "ps", "-a", "--no-trunc", "--format", fmt], timeout=timeout)
        if proc.returncode != 0:
            logger.warning("ci_runner_health: docker ps failed: %s", (proc.stderr or "")[:300])
            return None
        out: Dict[str, Dict[str, str]] = {}
        for line in (proc.stdout or "").splitlines():
            parts = line.rstrip("\r").split("\t")
            if len(parts) < 3 or not parts[0]:
                continue
            out[parts[0]] = {
                "state": parts[1].strip().lower(),
                "status": parts[2].strip(),
                "project": (parts[3].strip() if len(parts) > 3 else ""),
            }
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("ci_runner_health: docker unreadable: %s", exc)
        return None


def container_logs(docker: str, container: str, tail: int, timeout: float) -> Optional[str]:
    try:
        proc = _exec([docker, "logs", "--tail", str(tail), container], timeout=timeout)
        if proc.returncode != 0:
            return None
        return (proc.stdout or "") + "\n" + (proc.stderr or "")
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# classify + prove
# --------------------------------------------------------------------------- #
def classify(forge_status: Optional[str], docker_state: Optional[str], *,
             forge_measured: bool = True) -> str:
    """The two signals, reduced to one class. Pure; the table the tests pin."""
    if docker_state is None:
        return CLASS_CONTAINER_ABSENT
    if not forge_measured:
        return CLASS_FORGE_UNMEASURED
    online = forge_status == "online"
    if docker_state == "restarting":
        return CLASS_RESTARTING_BUT_ONLINE if online else CLASS_CANDIDATE
    if docker_state == "running":
        return CLASS_HEALTHY if online else CLASS_OFFLINE_BUT_UP
    return CLASS_STOPPED


def prove_registration_failure(logs: Optional[str], signatures: List[str]) -> Tuple[Optional[bool], Optional[str]]:
    """(proven, matched signature). None when the log could not be read --
    and None REFUSES, like every prove in restore_acts."""
    if logs is None:
        return None, None
    for sig in signatures:
        if sig and sig in logs:
            return True, sig
    return False, None


def _recently_acted(window_seconds: int) -> Optional[Dict[str, str]]:
    """``{container: iso-at}`` for every intent row inside the window, read from
    this reflex's own audit rows. None when audit_trail cannot be read -- the
    intent WRITE will then refuse the act anyway, so a read failure is
    reported and not treated as a clean cooldown."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT details FROM audit_trail WHERE event_type = %s AND action = %s "
                "ORDER BY id DESC LIMIT 200",
                (AUDIT_EVENT_TYPE, ACTION_INTENT),
            ).fetchall()
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("ci_runner_health: audit_trail unreadable for the cooldown: %s", exc)
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    recent: Dict[str, str] = {}
    for row in rows:
        raw = row[0] if not isinstance(row, dict) else row.get("details")
        try:
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
            at = datetime.fromisoformat(str(d.get("at")).replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        name = str(d.get("container") or "")
        if name and at >= cutoff and name not in recent:
            recent[name] = at.isoformat()
    return recent


def _audit(action: str, details: Dict[str, Any]) -> Any:
    """Resolved at call time so a test can substitute the writer. raise_on_error
    is the point: no row, no act."""
    from tools.audit.audit_logger import log_event

    return log_event(AUDIT_EVENT_TYPE, AUDIT_ACTOR, action, details=details, raise_on_error=True)


def _scrub(details: Dict[str, Any]) -> Dict[str, Any]:
    """Belt and braces: no key that could carry the token reaches a row."""
    return {k: v for k, v in details.items() if "token" not in str(k).lower()}


# --------------------------------------------------------------------------- #
# the act
# --------------------------------------------------------------------------- #
def _compose_file(compose_dir: Path) -> Optional[Path]:
    for name in _COMPOSE_FILES:
        if (compose_dir / name).is_file():
            return compose_dir / name
    return None


def mint_token(repo: str, gh: str, timeout: float) -> Optional[str]:
    """A registration token, valid ~1h, minted now. Never logged."""
    proc = _exec(
        [gh, "api", "-X", "POST", f"repos/{repo}/actions/runners/registration-token", "--jq", ".token"],
        timeout=timeout,
    )
    if proc.returncode != 0:
        logger.warning("ci_runner_health: token mint failed for %s: %s", repo, (proc.stderr or "")[:300])
        return None
    token = (proc.stdout or "").strip()
    return token or None


def reregister(repo: str, compose_dir: Path, entry: Dict[str, str], *, cfg: Dict[str, Any],
               gh: str, docker: str, evidence: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    """prove -> audit -> apply -> confirm for ONE declared container. The
    proof (signature + classification + project label) has already been made
    by the caller and is carried in ``evidence``; this re-checks the two things
    that are cheap to re-derive (the compose file, the label) and acts."""
    container, runner_name, project = entry["container"], entry["runner_name"], entry["project"]
    result: Dict[str, Any] = {
        "repo": repo, "container": container, "runner_name": runner_name, "project": project,
        "outcome": None, "reason": None, "confirmed": False, "audit_id": None, "dry_run": dry_run,
    }
    compose_file = _compose_file(compose_dir)
    if compose_file is None:
        result.update(outcome=REFUSED, reason=f"no compose file under {compose_dir}")
        return result
    if dry_run:
        result.update(outcome=WOULD_APPLY, reason="dry run: proven, not acted")
        return result

    at = datetime.now(timezone.utc).isoformat()
    intent = _scrub({
        "reflex": REFLEX_NAME, "repo": repo, "container": container, "runner_name": runner_name,
        "project": project, "compose_dir": str(compose_dir), "at": at, "phase": "intent", **evidence,
    })
    try:
        result["audit_id"] = _audit(ACTION_INTENT, intent)
    except Exception as exc:  # noqa: BLE001
        result.update(outcome=UNAUDITED_REFUSED, reason=f"intent row could not be written: {exc}")
        return result

    token = mint_token(repo, gh, float(_int(cfg, "gh_timeout_seconds", DEFAULT_GH_TIMEOUT)))
    if not token:
        result.update(outcome=FAILED, reason="registration token could not be minted")
        _after(result, intent)
        return result

    env = dict(os.environ)
    env.update({"RUNNER_TOKEN": token, "RUNNER_CONTAINER": container, "RUNNER_NAME": runner_name})
    del token  # the only copy outside the child's environment
    try:
        proc = _exec(
            [docker, "compose", "-p", project, "-f", compose_file.name, "up", "-d"],
            cwd=compose_dir, env=env, timeout=float(_int(cfg, "compose_timeout_seconds", DEFAULT_COMPOSE_TIMEOUT)),
        )
    except Exception as exc:  # noqa: BLE001
        result.update(outcome=FAILED, reason=f"docker compose up raised: {exc}")
        _after(result, intent)
        return result
    if proc.returncode != 0:
        result.update(outcome=FAILED, reason=f"docker compose up exited {proc.returncode}: {(proc.stderr or '')[-400:]}")
        _after(result, intent)
        return result

    # confirm: docker says running AND the forge lists it online.
    wait = _int(cfg, "confirm_wait_seconds", DEFAULT_CONFIRM_WAIT)
    poll = max(1, _int(cfg, "confirm_poll_seconds", DEFAULT_CONFIRM_POLL))
    deadline = time.monotonic() + wait
    docker_state = forge_status = None
    while True:
        containers = docker_containers(docker, float(_int(cfg, "docker_timeout_seconds", DEFAULT_DOCKER_TIMEOUT)))
        docker_state = (containers or {}).get(container, {}).get("state") if containers else None
        runners = forge_runners(repo, gh, float(_int(cfg, "gh_timeout_seconds", DEFAULT_GH_TIMEOUT)))
        forge_status = runners.get(runner_name) if runners is not None else None
        if docker_state == "running" and forge_status == "online":
            break
        if time.monotonic() >= deadline:
            break
        _sleep(poll)
    result["confirm"] = {"docker_state": docker_state, "forge_status": forge_status}
    if docker_state == "running" and forge_status == "online":
        result.update(outcome=APPLIED, confirmed=True, reason="container running, forge lists the runner online")
    elif docker_state == "running":
        result.update(outcome=APPLIED_UNCONFIRMED, reason=f"container running; forge reports {forge_status!r} after {wait}s")
    else:
        result.update(outcome=FAILED, reason=f"container state {docker_state!r} after compose up")
    _after(result, intent)
    return result


def _after(result: Dict[str, Any], intent: Dict[str, Any]) -> None:
    """The outcome row. Best-effort: the intent row already exists, so a failed
    outcome write cannot make the act unaudited -- but it is reported."""
    details = _scrub({**intent, "phase": "outcome", "outcome": result["outcome"], "reason": result["reason"],
                      "confirmed": result["confirmed"], "confirm": result.get("confirm"),
                      "at": datetime.now(timezone.utc).isoformat()})
    try:
        _audit(ACTION_PREFIX + str(result["outcome"]), details)
        result["outcome_audited"] = True
    except Exception as exc:  # noqa: BLE001
        result["outcome_audited"] = False
        result["outcome_audit_error"] = str(exc)


# --------------------------------------------------------------------------- #
# the sweep
# --------------------------------------------------------------------------- #
def sweep(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One pass. Never raises. Returns the report the daemon persists."""
    rcfg = config or {}
    dry_run = bool(rcfg.get("dry_run", False))
    report: Dict[str, Any] = {
        "reflex": REFLEX_NAME, "dry_run": dry_run, "declaration": str(declaration_path()),
        "repos_declared": 0, "containers_declared": 0, "forge_readable": {}, "docker_readable": None,
        "classified": {}, "candidates": 0, "reregistered": 0, "would_reregister": 0, "deferred": 0,
        "outcomes": {}, "refused_by_reason": {}, "acted": [], "reported": [], "errors": [], "status": None,
    }

    decl = load_declaration(Path(rcfg["declaration_path"]) if rcfg.get("declaration_path") else None)
    if decl is None:
        report["status"] = STATUS_UNMEASURABLE
        report["errors"].append("declaration unreadable -- nothing was measured")
        return report
    repos = [r for r in decl.get("repos") or [] if isinstance(r, dict) and r.get("repo")]
    report["repos_declared"] = len(repos)
    report["containers_declared"] = sum(len(r.get("containers") or []) for r in repos)
    if not repos or report["containers_declared"] == 0:
        report["status"] = STATUS_UNMEASURABLE
        report["errors"].append("no_runners_declared -- this host declares no runner fleet")
        return report

    bound = _int(rcfg, "max_reregistrations_per_run", _int(decl, "max_reregistrations_per_run", DEFAULT_MAX_PER_RUN))
    cooldown = _int(decl, "reregister_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
    signatures = [str(s) for s in (decl.get("registration_failure_signatures") or []) if s]
    tail = _int(decl, "log_tail_lines", DEFAULT_LOG_TAIL)
    gh_timeout = float(_int(decl, "gh_timeout_seconds", DEFAULT_GH_TIMEOUT))
    docker_timeout = float(_int(decl, "docker_timeout_seconds", DEFAULT_DOCKER_TIMEOUT))
    report["max_reregistrations_per_run"] = bound

    gh = _binary(decl, "gh_bin", "gh")
    docker = _binary(decl, "docker_bin", "docker")
    if not docker:
        report["status"] = STATUS_UNMEASURABLE
        report["docker_readable"] = False
        report["errors"].append("docker binary not found -- the Restarting set cannot be measured")
        return report
    containers = docker_containers(docker, docker_timeout)
    report["docker_readable"] = containers is not None
    if containers is None:
        report["status"] = STATUS_UNMEASURABLE
        report["errors"].append("docker daemon did not answer -- the Restarting set cannot be measured")
        return report

    recent = _recently_acted(cooldown)
    if recent is None:
        report["cooldown_readable"] = False
        report["errors"].append("audit_trail unreadable -- cooldown not measured (the intent write refuses anyway)")
        recent = {}
    else:
        report["cooldown_readable"] = True

    classes: Counter = Counter()
    outcomes: Counter = Counter()
    refused: Counter = Counter()
    performed = 0

    for repo_block in repos:
        repo = str(repo_block["repo"])
        compose_dir = Path(str(repo_block.get("compose_dir") or ""))
        runners = forge_runners(repo, gh, gh_timeout) if gh else None
        report["forge_readable"][repo] = runners is not None
        if runners is None:
            report["errors"].append(f"{repo}: forge unreadable{'' if gh else ' (gh binary not found)'}")

        for entry in repo_block.get("containers") or []:
            if not isinstance(entry, dict) or not entry.get("container"):
                continue
            entry = {k: str(entry.get(k) or "") for k in ("container", "runner_name", "project")}
            live = containers.get(entry["container"])
            forge_status = runners.get(entry["runner_name"]) if runners is not None else None
            klass = classify(forge_status, live["state"] if live else None, forge_measured=runners is not None)
            classes[klass] += 1
            item = {
                "repo": repo, "container": entry["container"], "runner_name": entry["runner_name"],
                "project": entry["project"], "class": klass, "forge_status": forge_status,
                "docker_state": live["state"] if live else None, "docker_status": live["status"] if live else None,
                "live_project": live["project"] if live else None,
            }
            if klass != CLASS_CANDIDATE:
                if klass != CLASS_HEALTHY:
                    report["reported"].append(item)
                continue

            report["candidates"] += 1
            # prove, from the container's own log
            proven, matched = prove_registration_failure(
                container_logs(docker, entry["container"], tail, docker_timeout), signatures)
            item["proven"] = proven
            item["matched_signature"] = matched
            if proven is None:
                item["class"] = "restarting_log_unreadable"
                refused["container log unreadable"] += 1
                report["reported"].append(item)
                continue
            if proven is False:
                item["class"] = "restarting_unproven"
                refused["no registration-failure signature in the log"] += 1
                report["reported"].append(item)
                continue
            if live and live["project"] and live["project"] != entry["project"]:
                item["class"] = "project_label_mismatch"
                refused[f"live compose project {live['project']!r} != declared {entry['project']!r}"] += 1
                report["reported"].append(item)
                continue
            if entry["container"] in recent:
                item["class"] = "recently_acted"
                item["last_intent_at"] = recent[entry["container"]]
                refused["re-registered inside the cooldown window"] += 1
                report["reported"].append(item)
                continue
            if performed >= bound:
                item["outcome"] = DEFERRED
                report["deferred"] += 1
                report["acted"].append(item)
                continue

            evidence = {"class": klass, "forge_status": forge_status, "docker_status": item["docker_status"],
                        "matched_signature": matched, "live_project": item["live_project"]}
            result = reregister(repo, compose_dir, entry, cfg=decl, gh=gh, docker=docker,
                                evidence=evidence, dry_run=dry_run)
            outcomes[str(result["outcome"])] += 1
            item.update({k: result.get(k) for k in ("outcome", "reason", "confirmed", "audit_id", "confirm",
                                                    "outcome_audited")})
            report["acted"].append(item)
            if result["outcome"] == WOULD_APPLY:
                report["would_reregister"] += 1
            elif result["outcome"] in (APPLIED, APPLIED_UNCONFIRMED):
                report["reregistered"] += 1
                performed += 1
            elif result["outcome"] in (REFUSED, UNAUDITED_REFUSED):
                refused[str(result["reason"])[:80]] += 1
            elif result["outcome"] == FAILED:
                performed += 1   # a failed act still spent the bound; it must not retry in-cycle

    report["classified"] = dict(classes)
    report["outcomes"] = dict(outcomes)
    report["refused_by_reason"] = dict(refused)

    if not any(report["forge_readable"].values()):
        report["status"] = STATUS_UNMEASURABLE
        report["errors"].append("the forge answered for no repo -- the offline set cannot be measured")
    elif report["acted"] or report["reported"]:
        report["status"] = STATUS_FINDINGS
    else:
        report["status"] = STATUS_OK
    return report


def run(config: dict, state: object) -> dict:
    """Entry point called by the Genesis daemon (``config`` is this reflex's
    block from args/genesis_config.yaml)."""
    try:
        report = sweep(config or {})
        if report["status"] == STATUS_UNMEASURABLE:
            logger.warning("ci_runner_health: unmeasurable: %s", "; ".join(report["errors"]))
        elif report["acted"]:
            logger.info(
                "ci_runner_health: %d candidate(s), %d re-registered, %d deferred, outcomes %s",
                report["candidates"], report["reregistered"], report["deferred"], report["outcomes"],
            )
        # `success` drives the daemon's circuit breaker: an unmeasurable cycle
        # must not trip it (see module docstring). The status carries the truth.
        return {
            "success": True,
            "metric_value": float(report["reregistered"]),
            "status": report["status"],
            "details": report,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("ci_runner_health failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0, "status": STATUS_ERROR}


_TOKEN_SHAPE = re.compile(r"\bA[A-Z0-9]{28}\b")


def declaration_holds_no_token(path: Optional[Path] = None) -> bool:
    """A runner registration token is 29 chars beginning with `A`. The
    declaration must never carry one; tested, not assumed."""
    text = (path or declaration_path()).read_text(encoding="utf-8")
    return _TOKEN_SHAPE.search(text) is None


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    print(json.dumps(run({"dry_run": "--apply" not in sys.argv}, None), indent=2, default=str))
