# CUI // SP-CTI
"""The target register: a tree is scanned only when registered and authorised by a person (icx-ent-01).

Vulnerability discovery is dual-use, and like the HTTP red team (``tools/security/redteam_scope.py``) the difference
between a security tool and an attack tool is the scope lock, so nothing here has a warn-and-continue path. It
reuses redteam_scope's :class:`~tools.security.redteam_scope.ScopeViolation` (raised, never downgraded) and its rule
that an allowlist alone grants nothing:

1. **A hard root allowlist** (``scope.allowed_roots`` in ``args/vuln_research.yaml``): a tree outside every root is
   refused at registration and at every scan.
2. **A registration at a digest.** Anyone, or an agent, REGISTERS a target: a local source tree, its scope globs,
   where its tests and fuzz entry points are, the tree's SHA-256 over every in-scope file (and the git commit when
   it is a checkout), and why. Registering decides nothing.
3. **A person's authorisation** (``decl-wft-vr-target-authorise``): a person who did not register it authorises it
   or refuses, with a reason, bound to the registration's digest. It lapses (``scope.authorisation_days``, or an
   earlier ``expires_on``).
4. **A stop.** Any signed-in person revokes it at once, with a reason; nothing scans it again.
5. **The tree must not have moved.** A scan recomputes the tree digest; a changed tree is a different target and
   needs a new registration. Every path is resolved and must stay inside the root (no symlink out).
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess  # nosec B404 — git rev-parse, fixed arguments
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from icdev.core.paths import repo_root
from tools.security.redteam_scope import ScopeViolation
from tools.vulnresearch import ledger
from tools.vulnresearch.constants import CONFIG_RELPATH, GENESIS, SURFACE_AUTHORISE, TEMPLATE_AUTHORISE

BASE_DIR = repo_root(__file__)

__all__ = ["ScopeViolation", "TargetRefused", "load_config", "register", "authorise", "revoke", "state",
           "assert_in_scope", "targets_view", "tree"]

TargetRefused = ledger.LedgerRefused


def load_config(path: Optional[Path] = None) -> dict:
    p = path or (BASE_DIR / CONFIG_RELPATH)
    raw = p.read_bytes()
    cfg = yaml.safe_load(raw.decode("utf-8")) or {}
    cfg["_sha256"] = hashlib.sha256(raw).hexdigest()
    return cfg


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


def allowed_roots(cfg: dict) -> list[Path]:
    # Only the reviewed config allows a root: no environment variable, flag or request can add one.
    roots = [Path(r) for r in ((cfg.get("scope") or {}).get("allowed_roots") or [])]
    return [(r if r.is_absolute() else BASE_DIR / r).resolve() for r in roots]


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def resolve_root(root: str, cfg: dict) -> Path:
    """The tree's real path, refused unless it sits under an allowed root."""
    if not _text(root):
        raise ScopeViolation("a target needs a root (a local source tree)")
    p = Path(root)
    p = (p if p.is_absolute() else BASE_DIR / p).resolve()
    if not p.is_dir():
        raise ScopeViolation(f"target root {root!r} is not a directory")
    roots = allowed_roots(cfg)
    if not any(_inside(p, r) for r in roots):
        raise ScopeViolation(
            f"{p} is not under an allowed root ({', '.join(str(r) for r in roots)}). Add the root deliberately "
            f"in {CONFIG_RELPATH} and have a person authorise the target; there is no warn-and-continue path.")
    return p


def _display(p: Path) -> str:
    try:
        return p.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(p)


def tree(root: Path, cfg: dict) -> dict:
    """Every in-scope file and its SHA-256, and the tree's digest. A symlink leaving the root is refused."""
    sc = cfg.get("scope") or {}
    include = sc.get("include") or ["**/*.py"]
    skip = set(sc.get("exclude_dirs") or [])
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if not any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g.split("/")[-1]) for g in include):
                continue
            if not _inside(full.resolve(), root):
                raise ScopeViolation(f"{rel} resolves outside the target root")
            data = full.read_bytes()
            if len(data) > int(sc.get("max_file_bytes") or 262144):
                continue
            files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    if len(files) > int(sc.get("max_files") or 400):
        raise ScopeViolation(f"the target has {len(files)} in-scope files (limit {sc.get('max_files')})")
    return {"files": files, "digest": ledger.digest([[f["path"], f["sha256"]] for f in files]), "count": len(files)}


def git_commit(root: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,  # nosec B603 B607
                             timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:  # noqa: BLE001 — not a checkout
        return None


# ── the register ─────────────────────────────────────────────────────────────


def _stream(tid: str) -> str:
    return f"target:{tid}"


def replay(ordered: list[dict]) -> dict:
    """One target's state from its stream: the registration, the person's decision, the stop."""
    reg = dec = rev = None
    history = []
    for r in ordered:
        p = ledger.payload(r)
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash")}
        history.append(h)
        if r["event"] == "registered" and reg is None:
            reg = {**h, "registration": p.get("content") or {}}
        elif r["event"] in ("authorised", "refused") and reg and dec is None:
            dec = {**h, "expires_on": (p.get("detail") or {}).get("expires_on")}
        elif r["event"] == "revoked" and rev is None:
            rev = h
    return {"registered": reg, "decision": dec, "revoked": rev, "history": history}


def _status(st: dict, today: Optional[str] = None) -> str:
    if not st.get("registered"):
        return "unknown"
    if st.get("revoked"):
        return "revoked"
    dec = st.get("decision")
    if not dec:
        return "awaiting_authorisation"
    if dec["event"] == "refused":
        return "refused"
    if dec.get("expires_on") and (today or date.today().isoformat()) > dec["expires_on"]:
        return "expired"
    return "authorised"


def state(tid: str, conn=None) -> dict:
    own = conn is None
    conn = conn or ledger.connection()
    try:
        ordered, problems = ledger.stream_rows(_stream(tid), conn)
    finally:
        if own:
            conn.close()
    st = replay(ordered)
    if st["registered"] is None:
        raise TargetRefused(404, "unknown_target", f"no target {tid!r} is registered")
    st.update(target_id=tid, status=_status(st), chain={"intact": not problems, "problems": problems},
              builders={st["registered"]["actor"]: "registered this target"})
    return st


def register(actor: Optional[dict], *, name: str, root: str, reason: str, src_root: str = ".",
             test_dir: str = "", fuzz_targets: Optional[list] = None, fuzz_seeds: Optional[dict] = None,
             expires_on: str = "", system_id: str = "", conn=None) -> dict:
    """Record a target at its tree digest. Anyone signed in, or an agent; it decides nothing."""
    if not isinstance(actor, dict) or not actor.get("id"):
        raise TargetRefused(401, "no_actor", "registering a target needs a signed-in person or a named agent")
    if not _text(name) or not _text(reason):
        raise TargetRefused(400, "fields", "a target needs a name and a reason (why it may be tested)")
    if _text(system_id) and not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", _text(system_id)):
        raise TargetRefused(400, "system_id", "system_id is the ICDEV[OS] registered system id "
                                              "(lower-case letters, digits, . _ -), e.g. icos")
    cfg = load_config()
    try:
        p = resolve_root(root, cfg)
        t = tree(p, cfg)
    except ScopeViolation as exc:
        raise TargetRefused(403, "out_of_scope", str(exc)) from exc
    if t["count"] == 0:
        raise TargetRefused(400, "empty", "the target has no in-scope files")
    if expires_on:
        try:
            date.fromisoformat(expires_on)
        except ValueError:
            raise TargetRefused(400, "expires_on", "expires_on must be YYYY-MM-DD") from None
    for ft in fuzz_targets or []:
        if ":" not in str(ft):
            raise TargetRefused(400, "fuzz_targets", f"a fuzz target is module:function, not {ft!r}")
    tid = f"vrt-{uuid.uuid4().hex[:10]}"
    registration = {"target_id": tid, "name": _text(name), "root": _display(p), "src_root": _text(src_root) or ".",
                    "test_dir": _text(test_dir), "fuzz_targets": [str(x) for x in fuzz_targets or []],
                    "fuzz_seeds": {str(k): [str(s) for s in v] for k, v in (fuzz_seeds or {}).items()},
                    "tree_digest": t["digest"], "files": t["count"], "commit": git_commit(p),
                    "expires_on": expires_on or None, "config_sha256": cfg["_sha256"],
                    # The ICDEV[OS] system under assessment this tree belongs to (osx-ent-01), when named: its
                    # vulnerability register (osx-ent-07) keys findings by it. Never guessed.
                    "system_id": _text(system_id) or None}
    own = conn is None
    conn = conn or ledger.connection()
    try:
        row = ledger.append(conn, stream=_stream(tid), event="registered", subject=tid, actor=_text(actor["id"]),
                            actor_kind=ledger.actor_kind(actor), actor_role=_text(actor.get("role")),
                            content=registration, reason=_text(reason), prev_hash=GENESIS)
    finally:
        if own:
            conn.close()
    return {"target_id": tid, "event_id": row["event_id"], "content_digest": row["content_digest"],
            "registration": registration, "template": TEMPLATE_AUTHORISE,
            "next": "a person who did not register it authorises it or refuses, on /vulnresearch/"}


def authorise(tid: str, *, decision: str, reason: str, read_digest: str, actor: Optional[dict], conn=None) -> dict:
    """A person who did not register the target authorises it for testing, or refuses (declared step)."""
    from tools.workflow_hitl import declared

    if not isinstance(actor, dict):
        raise TargetRefused(401, "no_person", "authorising a target is a signed-in person's declared step")
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st = state(tid, conn)
        if not st["chain"]["intact"]:
            raise TargetRefused(409, "chain_broken", f"{tid}'s ledger does not verify")
        if st["decision"] or st["revoked"]:
            raise TargetRefused(409, "already_decided", f"{tid} was already {st['status']}: register it again")
        reg = st["registered"]
        if _text(read_digest) != reg["content_digest"]:
            raise TargetRefused(409, "digest", "the registration is not what you read (reload it)")
        dec = _text(decision).lower()
        cfg = load_config()
        days = int((cfg.get("scope") or {}).get("authorisation_days") or 30)
        expires = (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()
        asked = reg["registration"].get("expires_on")
        if asked and asked < expires:
            expires = asked
        try:
            taken = declared.take_step(TEMPLATE_AUTHORISE, surface=SURFACE_AUTHORISE, decision=dec,
                                       subject=f"vr_target:{tid}", reason=_text(reason), actor=actor,
                                       separated_from=st["builders"],
                                       payload={"target_id": tid, "content_digest": reg["content_digest"],
                                                "tree_digest": reg["registration"]["tree_digest"],
                                                "expires_on": expires})
        except declared.StepRefused as exc:
            raise TargetRefused(exc.status, exc.code, exc.message) from exc
        event = "authorised" if taken["affirmative"] else "refused"
        row = ledger.append(conn, stream=_stream(tid), event=event, subject=tid, actor=taken["decided_by"],
                            actor_kind="person", actor_role=_text(actor.get("role")), step_id=taken["step_id"],
                            reason=_text(reason), prev_hash=ledger.head(ledger.stream_rows(_stream(tid), conn)[0]),
                            detail={"registration_event": reg["event_id"], "content_digest": reg["content_digest"],
                                    "expires_on": expires if event == "authorised" else None})
    finally:
        if own:
            conn.close()
    return {"target_id": tid, "event": event, "event_id": row["event_id"], "step": taken,
            "decided_by": taken["decided_by"], "expires_on": expires if event == "authorised" else None}


def revoke(tid: str, *, reason: str, actor: Optional[dict], conn=None) -> dict:
    """A STOP: any signed-in person revokes a target at once, with a reason. Nothing scans it again."""
    if not isinstance(actor, dict) or not actor.get("id") or ledger.actor_kind(actor) != "person":
        raise TargetRefused(401, "no_person", "revoking a target is a signed-in person's stop")
    if not _text(reason):
        raise TargetRefused(400, "reason", "a reason is required to revoke")
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st = state(tid, conn)
        if st["revoked"]:
            raise TargetRefused(409, "already_revoked", f"{tid} is already revoked")
        row = ledger.append(conn, stream=_stream(tid), event="revoked", subject=tid, actor=_text(actor["id"]),
                            actor_kind="person", actor_role=_text(actor.get("role")), reason=_text(reason),
                            prev_hash=ledger.head(ledger.stream_rows(_stream(tid), conn)[0]))
    finally:
        if own:
            conn.close()
    return {"target_id": tid, "event": "revoked", "event_id": row["event_id"], "by": row["actor"]}


def assert_in_scope(tid: str, conn=None) -> dict:
    """The single choke point before any target code is read or run. RAISES :class:`ScopeViolation`.

    Returns ``{target_id, root (Path), registration, tree}`` only when the target is registered, authorised by a
    person, not revoked, not expired, its ledger verifies, its root is still under an allowed root and its tree
    still has the digest it was registered at.
    """
    try:
        st = state(tid, conn)
    except TargetRefused as exc:
        raise ScopeViolation(exc.message) from exc
    if not st["chain"]["intact"]:
        raise ScopeViolation(f"{tid}'s ledger does not verify")
    if st["status"] != "authorised":
        raise ScopeViolation(f"{tid} is {st['status']}: only an authorised target is scanned")
    reg = st["registered"]["registration"]
    cfg = load_config()
    root = resolve_root(reg["root"], cfg)
    t = tree(root, cfg)
    if t["digest"] != reg["tree_digest"]:
        raise ScopeViolation(f"{tid}'s tree changed since it was registered ({t['digest'][:12]} is not "
                             f"{reg['tree_digest'][:12]}): register the new tree")
    return {"target_id": tid, "root": root, "registration": reg, "tree": t, "state": st}


def is_synthetic_benchmark(scoped: dict, cfg: Optional[dict] = None) -> bool:
    """True only when the target IS ICDEV's committed synthetic benchmark (its configured path, in this repo)."""
    cfg = cfg or load_config()
    bench = (cfg.get("benchmark") or {}).get("path") or ""
    if not bench:
        return False
    return Path(scoped["root"]).resolve() == (BASE_DIR / bench).resolve()


def in_scope_path(scoped: dict, rel: str) -> Path:
    """A file of the target, refused when it is not one of its in-scope files."""
    rel = _text(rel).replace("\\", "/").lstrip("/")
    if rel not in {f["path"] for f in scoped["tree"]["files"]}:
        raise ScopeViolation(f"{rel!r} is not an in-scope file of {scoped['target_id']}")
    return scoped["root"] / rel


def targets_view(conn=None) -> dict:
    """Every registered target and its state. Reads the ledger only."""
    own = conn is None
    conn = conn or ledger.connection()
    try:
        ch = ledger.chain(ledger.read(conn, prefix="target:"))
    finally:
        if own:
            conn.close()
    out = []
    for stream, ordered in ch.items():
        if stream == "_problems":
            continue
        st = replay(ordered)
        if not st["registered"]:
            continue
        st["status"] = _status(st)
        tid = stream.split(":", 1)[1]
        out.append({"target_id": tid, "name": st["registered"]["registration"].get("name"), "status": st["status"],
                    "registered": st["registered"], "decision": st["decision"], "revoked": st["revoked"],
                    "builders": {st["registered"]["actor"]: "registered this target"}})
    out.sort(key=lambda x: x["registered"]["created_at"], reverse=True)
    return {"targets": out, "chain": {"intact": not ch["_problems"], "problems": ch["_problems"]},
            "template": TEMPLATE_AUTHORISE}
