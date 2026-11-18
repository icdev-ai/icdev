# CUI // SP-CTI
"""Engineering standards register with a lifecycle, on the IDP (icx-coe-01).

ICDEV enforces a great deal by machine (security gates, red-first, coherence,
the five-rules audit, the guard hooks) and had nowhere a person could browse
those standards, see who owns them, or propose, adopt and retire one. This
module is that register.

ONE ENTRY PER STANDARD (``args/engineering_standards.yaml`` declares the seeds):
id, version, owner (a role), sources (by reference: a repo path, a citation
catalog key, a URL, or a file pinned at a commit in another repo), scope, and the
check-as-code: an existing IDP scorecard rule (``args/scorecards/``) or a named
manual evidence step. The WG 22 citation catalog ``args/standards_registry.yaml``
is NOT this register and is not changed; an entry may cite one of its keys.

THE LIFECYCLE is propose -> review -> adopt -> deprecate. Status is never a
field anyone edits: it is the replay of ``coe_standard_events``, an append-only,
hash-chained ledger (each row carries the hash of the standard's previous row,
and ``UNIQUE(standard_id, prev_hash)`` stops the history forking).

* ``propose``  anyone, or an agent through MCP: a draft. It decides nothing.
* ``review``   a steward's declared step (``decl-wft-coe-standard-review``):
               endorse or return, with a reason.
* ``adopt``    a steward SIGNS a reviewed standard into force
               (``decl-wft-coe-standard-adopt``), at the entry digest read.
* ``deprecate`` a steward retires a standard in force
               (``decl-wft-coe-standard-deprecate``).

Three rules hold everywhere:

1. **The author is never the signer.** Every step is ``separate_from:
   builders``; :func:`builders` names the version's author and the route passes
   them, so the author is refused at review, adoption and deprecation.
2. **A standard without a check is a wish.** :func:`adopt` refuses an entry
   that names neither an existing scorecard rule nor a named manual evidence
   step, before any step is offered (:func:`resolve_check`).
3. **Nothing advances on its own.** Each transition needs the state before it
   and a person's step; seeds are recorded by the labelled bootstrap identity
   (``seed_identity``), never as a person's signature, and enter ``adopted``
   only when their check resolves.

CLI::

    python tools/idp/standards.py --list [--json]
    python tools/idp/standards.py --show ENG-RED-FIRST [--json]
    python tools/idp/standards.py --validate [--json]
    python tools/idp/standards.py --seed [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

import yaml  # noqa: E402

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)
REGISTER_RELPATH = "args/engineering_standards.yaml"
CATALOG_RELPATH = "args/standards_registry.yaml"
TABLE = "coe_standard_events"
GENESIS = "0" * 64

#: The declared steps, and the route each one decides.
TEMPLATES = {
    "review": "decl-wft-coe-standard-review",
    "adopt": "decl-wft-coe-standard-adopt",
    "deprecate": "decl-wft-coe-standard-deprecate",
}
SURFACES = {
    "review": "idp.idp_api_standard_review",
    "adopt": "idp.idp_api_standard_adopt",
    "deprecate": "idp.idp_api_standard_deprecate",
}

EVENTS = ("seeded", "proposed", "reviewed", "returned", "adopted", "refused", "deprecated")
#: A version still waiting on a person.
OPEN_STATES = ("proposed", "reviewed")
ACTOR_KINDS = ("person", "agent", "bootstrap")
SEED_STATES = ("adopted", "proposed")
CHECK_KINDS = ("scorecard_rule", "manual_evidence")
WISH = "a standard without a check is a wish"

_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_COLS = ("event_id", "standard_id", "version", "event", "actor", "actor_kind", "actor_role",
         "step_id", "reason", "entry_digest", "entry_json", "detail_json", "prev_hash",
         "row_hash", "created_at")
#: The fields a row's hash covers (everything but the hash itself).
_HASHED = tuple(c for c in _COLS if c != "row_hash")


class StandardRefused(Exception):
    """The register refuses the request. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


# ── canonical form ───────────────────────────────────────────────────────────


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def normalise(entry: dict) -> dict:
    """The entry's fields of record, in a fixed shape. What is digested and stored."""
    out: dict[str, Any] = {
        "id": _text(entry.get("id")).upper(),
        "title": _text(entry.get("title")),
        "version": _text(entry.get("version")),
        "owner": _text(entry.get("owner")).lower(),
        "scope": " ".join(_text(entry.get("scope")).split()),
        "sources": [],
        "check": None,
    }
    for src in entry.get("sources") or []:
        if isinstance(src, dict):
            out["sources"].append({k: _text(v) for k, v in sorted(src.items()) if _text(v)})
    check = entry.get("check")
    if isinstance(check, dict) and check:
        kind = next(iter(check))
        body = check.get(kind) if isinstance(check.get(kind), dict) else {}
        out["check"] = {kind: {k: " ".join(_text(v).split()) for k, v in sorted(body.items())
                               if _text(v)}}
    return out


def entry_digest(entry: dict) -> str:
    return digest(normalise(entry))


def subject(standard_id: str, version: str) -> str:
    return f"standard:{standard_id}@{version}"


# ── the register (declared seeds) and the citation catalog ───────────────────


def load_register(path: Optional[Path] = None) -> dict:
    """``args/engineering_standards.yaml``: ``{meta..., standards: [...]}``."""
    p = Path(path or BASE_DIR / REGISTER_RELPATH)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    data.setdefault("standards", [])
    data["_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    data["_path"] = REGISTER_RELPATH if path is None else str(p)
    return data


def seed_identity(register: Optional[dict] = None) -> str:
    ident = _text((register or load_register()).get("seed_identity"))
    if not ident.startswith("bootstrap:"):
        raise ValueError("seed_identity must be a labelled bootstrap identity ('bootstrap:...')")
    return ident


def catalog_keys(root: Optional[Path] = None) -> set[str]:
    """The keys of the WG 22 citation catalog (read, never written)."""
    try:
        data = yaml.safe_load((Path(root or BASE_DIR) / CATALOG_RELPATH).read_text(
            encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return set()
    return set((data.get("standards") or {}).keys())


def _local(root: Path, rel: str) -> Optional[Path]:
    """A repo-relative path inside the repo, or None."""
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
        return None
    return root / rel


def validate_entry(entry: dict, register: Optional[dict] = None,
                   root: Optional[Path] = None) -> list[str]:
    """Shape errors for one entry. Empty when it is well formed. A missing check
    is NOT a shape error (a draft may lack one); adoption refuses it."""
    root = Path(root or BASE_DIR)
    reg = register if register is not None else load_register()
    e = normalise(entry)
    sid = e["id"] or "<no id>"
    errors = []
    if not _ID_RE.match(e["id"]):
        errors.append(f"{sid}: id must look like ENG-SOMETHING (capitals, digits, dashes)")
    for field in ("title", "version", "scope"):
        if not e[field]:
            errors.append(f"{sid}: {field} is required")
    roles = [str(r) for r in reg.get("owner_roles") or []]
    if e["owner"] not in roles:
        errors.append(f"{sid}: owner must be one of the roles {', '.join(roles)}")
    if not e["sources"]:
        errors.append(f"{sid}: at least one source is required (sources are references)")
    keys = catalog_keys(root)
    for src in e["sources"]:
        kinds = [k for k in ("path", "catalog", "url", "repo") if k in src]
        if src.get("repo"):
            if not _HEX40.match(src.get("commit", "")) or not src.get("path") or \
                    not _HEX64.match(src.get("sha256", "")):
                errors.append(f"{sid}: a source in another repo is pinned: repo, a 40-hex "
                              "commit, path and its sha256")
        elif kinds == ["path"]:
            if _local(root, src["path"]) is None:
                errors.append(f"{sid}: source path {src['path']!r} must be inside this repo")
        elif kinds == ["catalog"]:
            if src["catalog"] not in keys:
                errors.append(f"{sid}: {src['catalog']!r} is not a key of {CATALOG_RELPATH}")
        elif kinds == ["url"]:
            if not src["url"].startswith("https://"):
                errors.append(f"{sid}: a source URL must be https")
        else:
            errors.append(f"{sid}: a source is one of path, catalog, url, or repo+commit+path+sha256")
    check = e["check"]
    if check is not None:
        kind = next(iter(check))
        if kind not in CHECK_KINDS or len(check) != 1:
            errors.append(f"{sid}: check is one of {', '.join(CHECK_KINDS)}")
    return errors


def _scorecards(root: Path) -> dict[str, set[str]]:
    from tools.idp.scorecard import load_scorecards

    return {c.key: {r.identifier for r in c.rules}
            for c in load_scorecards(root / "args" / "scorecards")}


def resolve_check(entry: dict, root: Optional[Path] = None,
                  scorecards: Optional[dict[str, set[str]]] = None) -> dict:
    """Whether the entry's check-as-code exists, and why not when it does not.

    ``scorecard_rule`` resolves when the scorecard and its rule exist under
    ``args/scorecards/``. ``manual_evidence`` resolves when the step is named and
    says what a person does, and any ``produced_by`` file it names exists.
    """
    root = Path(root or BASE_DIR)
    check = normalise(entry)["check"]
    if not check:
        return {"kind": None, "resolved": False,
                "why": f"no check-as-code and no named manual evidence step: {WISH}"}
    kind, body = next(iter(check.items()))
    if kind == "scorecard_rule":
        card, rule = body.get("scorecard", ""), body.get("rule", "")
        try:
            known = scorecards if scorecards is not None else _scorecards(root)
        except Exception as exc:  # noqa: BLE001 — unreadable is unresolved, never assumed
            return {"kind": kind, "resolved": False, "scorecard": card, "rule": rule,
                    "why": f"the scorecards could not be read ({exc})"}
        if card not in known:
            return {"kind": kind, "resolved": False, "scorecard": card, "rule": rule,
                    "why": f"no scorecard {card!r} under args/scorecards/: {WISH}"}
        if rule not in known[card]:
            return {"kind": kind, "resolved": False, "scorecard": card, "rule": rule,
                    "why": f"scorecard {card!r} has no rule {rule!r}: {WISH}"}
        return {"kind": kind, "resolved": True, "scorecard": card, "rule": rule,
                "why": f"IDP scorecard rule {card}/{rule}"}
    if kind == "manual_evidence":
        name, how, made = body.get("name", ""), body.get("procedure", ""), body.get("produced_by", "")
        out = {"kind": kind, "name": name, "produced_by": made or None}
        if not name or not how:
            return {**out, "resolved": False,
                    "why": f"a manual evidence step needs a name and a procedure: {WISH}"}
        if made:
            p = _local(root, made)
            if p is None or not p.exists():
                return {**out, "resolved": False,
                        "why": f"the evidence is produced by {made!r}, which does not exist"}
        return {**out, "resolved": True, "why": f"named manual evidence step: {name}"}
    return {"kind": kind, "resolved": False, "why": f"unknown check kind {kind!r}: {WISH}"}


def pin_sources(entry: dict, root: Optional[Path] = None) -> list[dict]:
    """Each source as referenced, with the sha256 of a local file now (None when absent)."""
    root = Path(root or BASE_DIR)
    keys = catalog_keys(root)
    out = []
    for src in normalise(entry)["sources"]:
        row = dict(src)
        if src.get("repo"):
            row["kind"] = "pinned"
        elif "path" in src:
            row["kind"] = "path"
            p = _local(root, src["path"])
            try:
                row["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest() if p else None
            except OSError:
                row["sha256"] = None
        elif "catalog" in src:
            row["kind"] = "catalog"
            row["in_catalog"] = src["catalog"] in keys
        else:
            row["kind"] = "url"
        out.append(row)
    return out


def validate_register(register: Optional[dict] = None, root: Optional[Path] = None) -> list[str]:
    """Every seed well formed, ids unique, and a seed ``adopted`` only with a resolving check."""
    reg = register if register is not None else load_register()
    errors: list[str] = []
    try:
        seed_identity(reg)
    except ValueError as exc:
        errors.append(str(exc))
    seen: set[str] = set()
    for entry in reg.get("standards") or []:
        errors.extend(validate_entry(entry, reg, root))
        sid = normalise(entry)["id"]
        if sid in seen:
            errors.append(f"{sid}: declared twice")
        seen.add(sid)
        seed = _text(entry.get("seed"))
        if seed not in SEED_STATES:
            errors.append(f"{sid}: seed must be one of {', '.join(SEED_STATES)}")
        elif seed == "adopted" and not resolve_check(entry, root)["resolved"]:
            errors.append(f"{sid}: seeded as adopted but its check does not resolve "
                          f"({resolve_check(entry, root)['why']})")
    return errors


# ── the ledger ───────────────────────────────────────────────────────────────


def _connection():
    """The platform database, without the row-security predicate.

    ``coe_standard_events`` has no tenant or classification column: the register
    is the platform's own engineering governance, not marked data, and who may
    change it is held by the declared steps, not by row labels. Inside a
    signed-in request ``get_connection`` injects a classification predicate that
    would fail on it (the same reason twin threads read platform tables this
    way). Every other table keeps its predicate.
    """
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def _row_hash(row: dict) -> str:
    return digest({k: row.get(k, "") for k in _HASHED})


def read_events(conn=None, standard_id: Optional[str] = None) -> list[dict]:
    """Every ledger row (or one standard's), as stored. Raises when unreadable."""
    own = conn is None
    conn = conn or _connection()
    try:
        sql = f"SELECT {', '.join(_COLS)} FROM {TABLE}"  # nosec B608 — fixed table and columns
        params: tuple = ()
        if standard_id:
            sql += " WHERE standard_id = %s"
            params = (standard_id,)
        rows = conn.execute(sql + " ORDER BY created_at, event_id", params).fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(_COLS, tuple(r))) for r in rows]


def chain(rows: Iterable[dict]) -> dict[str, Any]:
    """Order each standard's rows along its hash chain and verify every link.

    Returns ``{standard_id: [rows in chain order]}`` plus ``_problems``: a row
    whose hash or entry digest does not match, or a row the chain never reaches
    (a fork or a gap).
    """
    by_sid: dict[str, list[dict]] = {}
    for r in rows:
        by_sid.setdefault(str(r["standard_id"]), []).append(r)
    out: dict[str, Any] = {"_problems": []}
    for sid, items in sorted(by_sid.items()):
        nxt = {str(r["prev_hash"]): r for r in items}
        ordered, prev = [], GENESIS
        while prev in nxt:
            r = nxt.pop(prev)
            ordered.append(r)
            prev = str(r["row_hash"])
        for r in items:
            if _row_hash(r) != r["row_hash"]:
                out["_problems"].append({"event_id": r["event_id"], "standard_id": sid,
                                         "problem": "row hash does not match its content"})
            try:
                if digest(json.loads(r["entry_json"])) != r["entry_digest"]:
                    raise ValueError
            except (ValueError, TypeError):
                out["_problems"].append({"event_id": r["event_id"], "standard_id": sid,
                                         "problem": "entry does not match its digest"})
        for r in nxt.values():
            out["_problems"].append({"event_id": r["event_id"], "standard_id": sid,
                                     "problem": "not on the standard's hash chain"})
        out[sid] = ordered
    return out


def _detail(row: dict) -> dict:
    try:
        return json.loads(row.get("detail_json") or "{}")
    except ValueError:
        return {}


def replay(ordered: list[dict]) -> dict:
    """One standard's state from its rows in chain order. Pure.

    Returns ``{id, versions: {version: record}, latest, in_force, history}``.
    A version's state is the last event on it; an adopted version a newer
    adoption replaced reads ``superseded``.
    """
    versions: dict[str, dict] = {}
    order: list[str] = []
    in_force: Optional[str] = None
    history = []
    for r in ordered:
        v = str(r["version"])
        entry = json.loads(r["entry_json"])
        det = _detail(r)
        rec = versions.get(v)
        if rec is None:
            rec = versions[v] = {"version": v, "entry": entry, "entry_digest": r["entry_digest"],
                                 "author": r["actor"], "author_kind": r["actor_kind"],
                                 "proposed_at": r["created_at"], "state": None, "events": []}
            order.append(v)
        event = r["event"]
        state = det.get("seed") if event == "seeded" else event
        rec["state"] = state
        rec["events"].append(r["event_id"])
        if event == "reviewed":
            rec["reviewed_by"] = r["actor"]
        if state == "adopted":
            if in_force and in_force != v:
                versions[in_force]["state"] = "superseded"
                versions[in_force]["superseded_by"] = v
            in_force = v
            rec.update(adopted_at=r["created_at"], adopted_by=r["actor"],
                       adopted_kind=r["actor_kind"], adopted_event=r["event_id"],
                       adopted_check=det.get("check"), adopted_sources=det.get("sources"))
        if event == "deprecated":
            rec.update(deprecated_at=r["created_at"], deprecated_by=r["actor"],
                       superseded_by=det.get("superseded_by") or rec.get("superseded_by"))
            if in_force == v:
                in_force = None
        history.append({k: r[k] for k in ("event_id", "version", "event", "actor", "actor_kind",
                                          "actor_role", "step_id", "reason", "entry_digest",
                                          "created_at", "row_hash", "prev_hash")}
                       | {"state": state, "detail": det})
    latest = versions[order[-1]] if order else None
    return {"versions": versions, "order": order, "latest": latest, "in_force": in_force,
            "history": history}


def _append(conn, *, standard_id: str, version: str, event: str, actor: str, actor_kind: str,
            entry: dict, actor_role: str = "", step_id: str = "", reason: str = "",
            detail: Optional[dict] = None, prev_hash: str) -> dict:
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    norm = normalise(entry)
    row = {"event_id": f"coe-{uuid.uuid4().hex[:16]}", "standard_id": standard_id,
           "version": version, "event": event, "actor": actor, "actor_kind": actor_kind,
           "actor_role": actor_role or "", "step_id": step_id or "", "reason": reason or "",
           "entry_digest": digest(norm), "entry_json": canonical(norm),
           "detail_json": canonical(detail or {}), "prev_hash": prev_hash,
           "created_at": _now()}
    row["row_hash"] = _row_hash(row)
    try:
        conn.execute(
            f"INSERT INTO {TABLE} ({', '.join(_COLS)}) VALUES ({', '.join(['%s'] * len(_COLS))})",
            tuple(row[c] for c in _COLS),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()     # PostgreSQL: the failed statement aborted the transaction
        except Exception:  # noqa: BLE001
            pass
        text = str(exc).lower()
        if "unique" in text or "duplicate" in text:
            raise StandardRefused(409, "concurrent", (
                f"{standard_id} changed while you were deciding: reload it")) from exc
        raise StandardRefused(503, "store_unavailable",
                              f"the register could not be written, so nothing changed ({exc})") from exc
    return row


def _state(standard_id: str, conn) -> tuple[dict, list[dict]]:
    """``(replayed state, chain problems)`` for one standard."""
    rows = read_events(conn, standard_id)
    ch = chain(rows)
    return replay(ch.get(standard_id, [])), ch["_problems"]


def _head(st: dict) -> str:
    return st["history"][-1]["row_hash"] if st["history"] else GENESIS


# ── reading ──────────────────────────────────────────────────────────────────


def _summary(sid: str, st: dict, root: Path, scorecards: Optional[dict]) -> dict:
    latest = st["latest"] or {}
    entry = latest.get("entry") or {}
    force = st["versions"].get(st["in_force"]) if st["in_force"] else None
    return {
        "id": sid, "title": entry.get("title", ""), "owner": entry.get("owner", ""),
        "latest_version": latest.get("version"), "state": latest.get("state"),
        "in_force": st["in_force"],
        "adopted_by": (force or {}).get("adopted_by"),
        "adopted_kind": (force or {}).get("adopted_kind"),
        "adopted_at": (force or {}).get("adopted_at"),
        "check": resolve_check(entry, root, scorecards),
        "author": latest.get("author"), "author_kind": latest.get("author_kind"),
        "updated_at": st["history"][-1]["created_at"] if st["history"] else None,
        "events": len(st["history"]),
    }


def register_view(conn=None, root: Optional[Path] = None) -> dict:
    """Every standard in the ledger with its lifecycle, plus seeds not yet recorded.

    Read-only. ``chain.intact`` is false, with each problem named, when any row
    does not verify.
    """
    root = Path(root or BASE_DIR)
    reg = load_register()
    rows = read_events(conn)
    ch = chain(rows)
    try:
        cards = _scorecards(root)
    except Exception:  # noqa: BLE001 — each check then says the scorecards were unreadable
        cards = None
    standards = [_summary(sid, replay(items), root, cards)
                 for sid, items in sorted(ch.items()) if sid != "_problems"]
    recorded = {s["id"] for s in standards}
    unseeded = [normalise(e)["id"] for e in reg["standards"] if normalise(e)["id"] not in recorded]
    counts: dict[str, int] = {}
    for s in standards:
        counts[s["state"] or "none"] = counts.get(s["state"] or "none", 0) + 1
    return {"standards": standards, "counts": counts, "unseeded": unseeded,
            "chain": {"intact": not ch["_problems"], "problems": ch["_problems"],
                      "rows": len(rows)},
            "register": {"path": REGISTER_RELPATH, "sha256": reg["_sha256"],
                         "seed_identity": reg.get("seed_identity"),
                         "owner_roles": reg.get("owner_roles") or []},
            "catalog": {"path": CATALOG_RELPATH, "note": (
                "the WG 22 citation catalog; an entry may cite one of its keys as a source")},
            "templates": dict(TEMPLATES)}


def standard_view(standard_id: str, conn=None, root: Optional[Path] = None) -> dict:
    """One standard: every version, its lifecycle, the full history, the checks and sources."""
    root = Path(root or BASE_DIR)
    sid = _text(standard_id).upper()
    st, problems = _state(sid, conn)
    if not st["history"]:
        raise StandardRefused(404, "unknown_standard", f"no standard {sid} in the register")
    latest = st["latest"]
    try:
        cards = _scorecards(root)
    except Exception:  # noqa: BLE001
        cards = None
    return {
        **_summary(sid, st, root, cards),
        "entry": latest["entry"], "entry_digest": latest["entry_digest"],
        "sources": pin_sources(latest["entry"], root),
        "versions": [{k: v for k, v in st["versions"][ver].items() if k != "events"}
                     for ver in st["order"]],
        "history": st["history"],
        "builders": builders(latest),
        "next": next_steps(latest, st),
        "chain": {"intact": not problems, "problems": problems},
    }


def next_steps(record: dict, st: dict) -> list[str]:
    """The steps that may be taken next on this version (what the page offers)."""
    state = record.get("state")
    if state == "proposed":
        return ["review"]
    if state == "reviewed":
        return ["adopt"]
    if state == "adopted" and st.get("in_force") == record.get("version"):
        return ["deprecate"]
    return []


def builders(record: dict) -> dict[str, str]:
    """Who may not take a step on this version: its author (``separate_from: builders``)."""
    author = _text((record or {}).get("author"))
    if not author:
        return {}
    return {author: f"authored version {record.get('version')} of this standard"}


# ── changing: propose (a draft), then the three declared steps ───────────────


def _actor_kind(actor: dict) -> str:
    role = _text(actor.get("role"))
    if role == "service" or _text(actor.get("id")).startswith("agent:"):
        return "agent"
    return "person"


def propose(entry: dict, actor: Optional[dict], note: str = "", conn=None) -> dict:
    """Record a draft. Anyone signed in, or an agent: proposing decides nothing.

    Refused: an entry that is not well formed; a version that already exists;
    a standard with a version still open (proposed or reviewed) -- review or
    return it first. A missing check is allowed in a draft and shown; adoption
    refuses it.
    """
    if not actor or not _text(actor.get("id")):
        raise StandardRefused(401, "no_actor", "a proposal needs a signed-in person or a named agent")
    norm = normalise(entry)
    errors = validate_entry(norm)
    if errors:
        raise StandardRefused(400, "invalid", "; ".join(errors))
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems = _state(norm["id"], conn)
        if problems:
            raise StandardRefused(409, "chain_broken",
                                  f"{norm['id']}'s history does not verify: {problems[0]['problem']}")
        if norm["version"] in st["versions"]:
            raise StandardRefused(409, "version_exists",
                                  f"{norm['id']} {norm['version']} exists: propose a new version")
        open_v = [v for v, r in st["versions"].items() if r["state"] in OPEN_STATES]
        if open_v:
            raise StandardRefused(409, "open_version", (
                f"{norm['id']} {open_v[0]} is still {st['versions'][open_v[0]]['state']}: "
                "it is reviewed, returned, adopted or refused before a new version is proposed"))
        row = _append(conn, standard_id=norm["id"], version=norm["version"], event="proposed",
                      actor=_text(actor["id"]), actor_kind=_actor_kind(actor),
                      actor_role=_text(actor.get("role")), entry=norm, reason=_text(note),
                      detail={"check": resolve_check(norm), "sources": pin_sources(norm)},
                      prev_hash=_head(st))
    finally:
        if own:
            conn.close()
    return {"standard_id": norm["id"], "version": norm["version"], "state": "proposed",
            "event_id": row["event_id"], "entry_digest": row["entry_digest"],
            "author_kind": row["actor_kind"], "check": resolve_check(norm)}


def _decide(step: str, standard_id: str, version: str, want: str, *, decision: str,
            reason: str, read_digest: str, actor: Optional[dict], conn=None,
            superseded_by: str = "") -> dict:
    from tools.workflow_hitl import declared

    sid, ver = _text(standard_id).upper(), _text(version)
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems = _state(sid, conn)
        if not st["history"]:
            raise StandardRefused(404, "unknown_standard", f"no standard {sid} in the register")
        if problems:
            raise StandardRefused(409, "chain_broken",
                                  f"{sid}'s history does not verify: {problems[0]['problem']}")
        rec = st["versions"].get(ver)
        if rec is None:
            raise StandardRefused(404, "unknown_version", f"{sid} has no version {ver}")
        if rec["state"] != want or (step == "deprecate" and st["in_force"] != ver):
            raise StandardRefused(409, "out_of_order", (
                f"{sid} {ver} is {rec['state']}: {step} needs it {want}"
                + (" and in force" if step == "deprecate" else "")))
        if _text(read_digest) != rec["entry_digest"]:
            raise StandardRefused(409, "digest", (
                f"{sid} {ver} is not what you read: its digest is {rec['entry_digest'][:12]} "
                "(reload it)"))
        check = resolve_check(rec["entry"])
        if step == "adopt" and decision == "sign" and not check["resolved"]:
            # A standard without a check is a wish: refused before any step is offered.
            raise StandardRefused(409, "no_check", f"{sid} {ver} cannot be adopted: {check['why']}")
        payload = {"standard_id": sid, "version": ver, "entry_digest": rec["entry_digest"]}
        if step == "adopt":
            payload["check"] = check
        if step == "deprecate":
            payload["superseded_by"] = _text(superseded_by)
        try:
            taken = declared.take_step(TEMPLATES[step], surface=SURFACES[step],
                                       decision=_text(decision).lower(), subject=subject(sid, ver),
                                       reason=_text(reason), actor=actor, payload=payload,
                                       separated_from=builders(rec))
        except declared.StepRefused as exc:
            raise StandardRefused(exc.status, exc.code, exc.message) from exc
        event = {"review": {True: "reviewed", False: "returned"},
                 "adopt": {True: "adopted", False: "refused"},
                 "deprecate": {True: "deprecated", False: "deprecated"}}[step][taken["affirmative"]]
        detail: dict[str, Any] = {"decision": taken["decision"], "template_id": taken["template_id"],
                                  "instance_id": taken["instance_id"]}
        if event == "adopted":
            detail.update(check=check, sources=pin_sources(rec["entry"]),
                          supersedes=st["in_force"])
        if event == "deprecated":
            detail["superseded_by"] = _text(superseded_by)
        row = _append(conn, standard_id=sid, version=ver, event=event, actor=taken["decided_by"],
                      actor_kind="person", actor_role=_text((actor or {}).get("role")),
                      step_id=taken["step_id"], reason=_text(reason), entry=rec["entry"],
                      detail=detail, prev_hash=_head(st))
    finally:
        if own:
            conn.close()
    return {"standard_id": sid, "version": ver, "state": event, "event_id": row["event_id"],
            "decision_step": taken}


def review(standard_id: str, version: str, *, decision: str, reason: str, read_digest: str,
           actor: Optional[dict], conn=None) -> dict:
    """A steward (never the author) endorses a proposed version or returns it."""
    return _decide("review", standard_id, version, "proposed", decision=decision, reason=reason,
                   read_digest=read_digest, actor=actor, conn=conn)


def adopt(standard_id: str, version: str, *, decision: str, reason: str, read_digest: str,
          actor: Optional[dict], conn=None) -> dict:
    """A steward (never the author) signs a reviewed version into force, or refuses it.

    Refused with ``no_check`` when the entry names no existing scorecard rule
    and no named manual evidence step.
    """
    return _decide("adopt", standard_id, version, "reviewed", decision=decision, reason=reason,
                   read_digest=read_digest, actor=actor, conn=conn)


def deprecate(standard_id: str, version: str, *, reason: str, read_digest: str,
              actor: Optional[dict], superseded_by: str = "", conn=None) -> dict:
    """A steward (never the author) retires the version in force, with a reason."""
    return _decide("deprecate", standard_id, version, "adopted", decision="deprecate",
                   reason=reason, read_digest=read_digest, actor=actor, conn=conn,
                   superseded_by=superseded_by)


def seed(conn=None, register: Optional[dict] = None) -> dict:
    """Record every declared seed not yet in the ledger. Idempotent.

    The actor is the register's labelled ``seed_identity`` (``actor_kind:
    bootstrap``), never a person, and no declared step is taken: a seed is
    ``adopted`` only when its check resolves (``validate_register`` refuses the
    register otherwise), and says so in its detail.
    """
    reg = register if register is not None else load_register()
    errors = validate_register(reg)
    if errors:
        raise StandardRefused(400, "invalid_register", "; ".join(errors))
    ident = seed_identity(reg)
    own = conn is None
    conn = conn or _connection()
    seeded, skipped = [], []
    try:
        for entry in reg["standards"]:
            norm = normalise(entry)
            st, _ = _state(norm["id"], conn)
            if st["history"]:
                skipped.append(norm["id"])
                continue
            state = _text(entry.get("seed"))
            _append(conn, standard_id=norm["id"], version=norm["version"], event="seeded",
                    actor=ident, actor_kind="bootstrap", entry=norm,
                    reason=("already enforced by machine; recorded by a labelled bootstrap "
                            "identity, not a person's signature") if state == "adopted"
                    else "a reference seeded as a proposal: whether ICDEV adopts it is a person's step",
                    detail={"seed": state, "check": resolve_check(norm),
                            "sources": pin_sources(norm), "register_sha256": reg["_sha256"]},
                    prev_hash=GENESIS)
            seeded.append(norm["id"])
    finally:
        if own:
            conn.close()
    return {"seeded": seeded, "skipped": skipped, "seed_identity": ident}


def parse_sources(text: str) -> list[dict]:
    """One source per line, as the console form sends them: ``catalog:<key>``,
    an ``https://`` URL, or a repo path."""
    out = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("catalog:"):
            out.append({"catalog": line.split(":", 1)[1].strip()})
        elif line.lower().startswith(("http://", "https://")):
            out.append({"url": line})
        else:
            out.append({"path": line})
    return out


def entry_from_form(data: dict) -> dict:
    """An entry from the console's propose form (or an MCP call)."""
    sources = data.get("sources")
    if isinstance(sources, str):
        sources = parse_sources(sources)
    check = data.get("check")
    if not isinstance(check, dict):
        kind = _text(data.get("check_kind"))
        if kind == "scorecard_rule":
            check = {"scorecard_rule": {"scorecard": _text(data.get("scorecard")),
                                        "rule": _text(data.get("rule"))}}
        elif kind == "manual_evidence":
            check = {"manual_evidence": {"name": _text(data.get("evidence_name")),
                                         "procedure": _text(data.get("evidence_procedure")),
                                         "produced_by": _text(data.get("produced_by"))}}
        else:
            check = None
    return {"id": data.get("id"), "title": data.get("title"), "version": data.get("version"),
            "owner": data.get("owner"), "scope": data.get("scope"), "sources": sources or [],
            "check": check}


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Engineering standards register (icx-coe-01).")
    ap.add_argument("--list", action="store_true", help="every standard and its lifecycle")
    ap.add_argument("--show", metavar="ID", help="one standard with its full history")
    ap.add_argument("--validate", action="store_true",
                    help="validate args/engineering_standards.yaml (no database)")
    ap.add_argument("--seed", action="store_true",
                    help="record declared seeds not yet in the ledger (bootstrap identity)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)
    try:
        if args.validate:
            errors = validate_register()
            out: Any = {"valid": not errors, "errors": errors}
        elif args.seed:
            out = seed()
        elif args.show:
            out = standard_view(args.show)
        else:
            out = register_view()
    except StandardRefused as exc:
        print(json.dumps(exc.to_dict(), indent=2))
        return 1
    if args.json or not isinstance(out, dict) or "standards" not in out:
        print(json.dumps(out, indent=2, default=str))
    else:
        for s in out["standards"]:
            force = f"in force {s['in_force']}" if s["in_force"] else "not in force"
            print(f"{s['id']:<24} {s['state'] or '-':<11} {force:<16} {s['title']}")
        if out["unseeded"]:
            print(f"not yet seeded: {', '.join(out['unseeded'])} (run --seed)")
    return 0 if not (args.validate and out.get("errors")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
