# CUI // SP-CTI
"""FORGE Academy tracks per Community of Practice, with xAPI out to ICDEV[OS] (icx-cop-01).

Owner decision (2026-11-17, "CoP home: split"): ICDEV[IT] hosts the CoP hubs and the Academy learning paths;
ICDEV[OS] holds the marked knowledge and the signed contribution workflow, and its CoP charters link these paths BY
ID (osx-cop-01). There is no cloud-vendor training or certification content: every mission is about this platform's
own patterns (red first, the standards register, golden paths, the governed router, agents that propose, the twin,
the five rules, declared workflows, marking, the append-only audit, PostgreSQL first) and cites the files a learner
reads (``args/academy_cop_tracks.yaml``).

**Four tracks, one per CoP** (engineering/AIDLC, AI and data, application, cloud). Each is a learning path whose
xAPI activity id is ``xapi.path_prefix`` + its id, the id ICDEV[OS]'s charters link.

**A mission is passed on the server, never in the browser.** A learner's answers are graded against the declared
key (which no route, page or agent ever sees). Every attempt is recorded in the append-only, hash-chained
``cop_academy_events``, on the learner's own chain: ``attempted`` (not passed) or ``completed`` (passed). Only a
signed-in person attempts; a service key or an agent never does.

**xAPI statements** (xAPI 1.0.3 statement shape, :func:`statements`) are DERIVED from that ledger alone, per learner
and path: ``completed`` (ADL verb; every mission of the path, as declared now, passed; ``result.completion`` and
``success`` true) or ``progressed`` (at least one passed; ``result.completion`` false, so it is never read as
complete). The actor is a pseudonymous ``account`` (a random name kept in ``cop_academy_learners``): no name, no
mailbox, nothing ICDEV[OS] can map back. The object is the path's activity id; the timestamp is the ledger time of
the pass that made it so; the id is a UUIDv5 over path, account, verb and timestamp, so the same ledger yields the
same statements.

**The batch** (:func:`build`) is ``icos-cop-academy/v1``, validated against ICDEV[OS]'s schema
(``context/icos/cop-academy-statements.schema.json``, vendored and pinned by SHA-256 here): the producer, the time,
the paths it reports on (all four: a path nobody completed is reported as such), the statements and a
``content_digest`` over everything else, as ICDEV[OS] recomputes it.

**Publishing is a person's decision** (``decl-wft-cop-academy-xapi-publish``). Anyone signed in, or an agent as a
draft, PREPARES the batch, which records it whole at its digest. A person who did not prepare it PUBLISHES it, or
refuses, with a reason, bound to that digest. The batch schema admits no extra field, so the publication travels
BESIDE it (``icos-cop-academy-publication/v1``, ``context/icos/cop-academy-publication.schema.json``, ICDEV[OS]'s
schema as adopted in osx-cop-07, pinned): who signed, who prepared, the step, the ledger row and its hash. :func:`verify` checks both
against their schemas, the digest, the separation and the ledger.

CLI: ``tools/idp/cop_xapi_export.py``.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

import yaml  # noqa: E402

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)
CONFIG_RELPATH = "args/academy_cop_tracks.yaml"
SCHEMA = "icos-cop-academy/v1"
SCHEMA_RELPATH = "context/icos/cop-academy-statements.schema.json"
#: ICDEV[OS]'s batch schema as drafted in osx-cop-01 and osx-cop-07 (its contract 1.195.0, not yet merged there),
#: vendored verbatim. A changed file is refused, never silently used: re-pin when ICDEV[OS] publishes it.
SCHEMA_SHA256 = "89966bdae51a33aef68347b539e43b6b33a3dad8e72c8b8560e1842824729028"
SCHEMA_CONTRACT = "1.195.0 (draft)"
PUBLICATION_SCHEMA = "icos-cop-academy-publication/v1"
PUBLICATION_RELPATH = "context/icos/cop-academy-publication.schema.json"
#: ICDEV[OS]'s publication schema (osx-cop-07 adopted ICDEV[IT]'s proposal under its own name), pinned the same way.
PUBLICATION_SHA256 = "9fef09ec0630f0ffbac52f14ae24bd7dc698cb84ceed34a17eb0c094ed08654b"
PRODUCER_SYSTEM = "icdev-it"
PRODUCER_TOOL = "icdev-it/cop_xapi_export"
CLASSIFICATION = "CUI // SP-CTI"
TABLE = "cop_academy_events"
LEARNERS = "cop_academy_learners"
GENESIS = "0" * 64
BUNDLE_SCOPE = "bundle"

TEMPLATE = "decl-wft-cop-academy-xapi-publish"
SURFACE = "idp.idp_api_cop_xapi_publish"

COPS = ("engineering", "ai-data", "application", "cloud")
EVENTS = ("attempted", "completed", "prepared", "published", "refused")
ACTOR_KINDS = ("person", "agent")

#: Fixed forever: changing it re-issues every statement id, and an LRS would take the history twice.
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:icdev:academy:cop-xapi")

_COLS = ("event_id", "scope", "event", "actor", "actor_kind", "actor_role", "learner", "path_id", "mission_id",
         "step_id", "reason", "content_digest", "bundle_json", "detail_json", "prev_hash", "row_hash", "created_at")
_HASHED = tuple(c for c in _COLS if c != "row_hash")


class CopRefused(Exception):
    """The request is refused. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


def canonical(value: Any) -> str:
    """ICDEV[OS]'s canonical JSON (icdev_os.canon): keys sorted, separators ',' and ':'."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _utc(ts: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(_text(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── configuration ────────────────────────────────────────────────────────────


def load_config(path: Optional[Path] = None) -> dict:
    """The declared tracks, checked. A config that breaks a rule is refused, never half-read."""
    p = path or (BASE_DIR / CONFIG_RELPATH)
    raw = p.read_bytes()
    cfg = yaml.safe_load(raw) or {}
    problems = config_problems(cfg)
    if problems:
        raise CopRefused(500, "config", f"{CONFIG_RELPATH}: " + "; ".join(problems[:5]))
    cfg["_sha256"] = hashlib.sha256(raw).hexdigest()
    return cfg


def config_problems(cfg: dict) -> list[str]:
    """What is wrong with a tracks config (empty: nothing)."""
    out: list[str] = []
    if cfg.get("version") != 1:
        out.append("version must be 1")
    x = cfg.get("xapi") or {}
    if x.get("schema") != SCHEMA:
        out.append(f"xapi.schema must be {SCHEMA}")
    for k in ("path_prefix", "homepage"):
        if not _text(x.get(k)):
            out.append(f"xapi.{k} is required")
    verbs = x.get("verbs") or {}
    for k in ("completed", "progressed"):
        if not _text(verbs.get(k)).startswith("http://adlnet.gov/expapi/verbs/"):
            out.append(f"xapi.verbs.{k} must be an ADL verb id")
    tracks = cfg.get("tracks") or []
    cops = [t.get("cop") for t in tracks]
    if sorted(cops) != sorted(COPS):
        out.append(f"exactly one track per CoP ({', '.join(COPS)}), not {cops}")
    seen_t, seen_m = set(), set()
    for t in tracks:
        tid = _text(t.get("id"))
        if not tid or tid in seen_t:
            out.append(f"track id {tid!r} is empty or repeated")
        seen_t.add(tid)
        missions = t.get("missions") or []
        if not missions:
            out.append(f"{tid}: a track needs a mission")
        for m in missions:
            mid = _text(m.get("id"))
            if not mid or mid in seen_m:
                out.append(f"{tid}: mission id {mid!r} is empty or repeated")
            seen_m.add(mid)
            if not m.get("reading"):
                out.append(f"{mid}: a mission cites what it teaches (reading)")
            qs = m.get("questions") or []
            if not qs:
                out.append(f"{mid}: a mission needs a question")
            qids = set()
            for q in qs:
                opts = q.get("options") or []
                if _text(q.get("id")) in qids or not _text(q.get("id")):
                    out.append(f"{mid}: question id {q.get('id')!r} is empty or repeated")
                qids.add(_text(q.get("id")))
                if len(opts) < 2 or not isinstance(q.get("answer"), int) or not 0 <= q["answer"] < len(opts):
                    out.append(f"{mid}/{q.get('id')}: two options or more, and an answer that is one of them")
    return out


def path_activity(cfg: dict, path_id: str) -> str:
    return f"{cfg['xapi']['path_prefix']}{path_id}"


def public_tracks(cfg: dict) -> list[dict]:
    """The tracks as a learner (or an agent) sees them: every question and option, never the answer."""
    out = []
    for t in cfg["tracks"]:
        out.append({"id": t["id"], "cop": t["cop"], "name": t["name"], "activity_id": path_activity(cfg, t["id"]),
                    "missions": [{"id": m["id"], "title": m["title"], "pattern": m.get("pattern"),
                                  "summary": _text(m.get("summary")), "reading": list(m.get("reading") or []),
                                  "questions": [{"id": q["id"], "prompt": q["prompt"], "options": list(q["options"])}
                                                for q in m["questions"]]}
                                 for m in t["missions"]]})
    return out


def find_mission(cfg: dict, mission_id: str) -> tuple[dict, dict]:
    for t in cfg["tracks"]:
        for m in t["missions"]:
            if m["id"] == mission_id:
                return t, m
    raise CopRefused(404, "no_mission", f"no mission {mission_id!r} in {CONFIG_RELPATH}")


def mission_digest(mission: dict) -> str:
    """What the learner was graded against (the mission as declared, key included)."""
    return digest({k: mission.get(k) for k in ("id", "title", "questions")})


def grade(mission: dict, answers: Any, pass_mark: float = 1.0) -> dict:
    """Grade answers (``{question id: option index}``) against the declared key. Pure. A missing or malformed
    answer is wrong, never guessed at."""
    answers = answers if isinstance(answers, dict) else {}
    right, wrong = [], []
    for q in mission["questions"]:
        given = answers.get(q["id"])
        ok = isinstance(given, int) and not isinstance(given, bool) and given == q["answer"]
        (right if ok else wrong).append(q["id"])
    total = len(mission["questions"])
    return {"score": len(right), "total": total, "wrong": wrong, "passed": total > 0 and len(right) / total >= pass_mark,
            "answers_digest": digest({k: answers.get(k) for k in sorted(q["id"] for q in mission["questions"])})}


# ── the ledger ───────────────────────────────────────────────────────────────


def _connection():
    """The platform database without the row-security predicate: the Academy's own record, with no label column
    (as coe_conformance_events is)."""
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def _row_hash(row: dict) -> str:
    return digest({k: row.get(k, "") for k in _HASHED})


def read_events(conn=None) -> list[dict]:
    own = conn is None
    conn = conn or _connection()
    try:
        rows = conn.execute(f"SELECT {', '.join(_COLS)} FROM {TABLE} "  # nosec B608 — fixed table and columns
                            "ORDER BY created_at, event_id").fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(_COLS, tuple(r))) for r in rows]


def chain(rows: Iterable[dict]) -> dict[str, Any]:
    """Each scope's rows in hash-chain order (a learner's chain, or the bundle's), plus ``_problems``."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r["scope"]), []).append(r)
    out: dict[str, Any] = {"_problems": []}
    for scope, items in sorted(by.items()):
        nxt = {str(r["prev_hash"]): r for r in items}
        ordered, prev = [], GENESIS
        while prev in nxt:
            r = nxt.pop(prev)
            ordered.append(r)
            prev = str(r["row_hash"])
        for r in items:
            if _row_hash(r) != r["row_hash"]:
                out["_problems"].append({"event_id": r["event_id"], "scope": scope,
                                         "problem": "row hash does not match its content"})
            elif r["event"] == "prepared":
                try:
                    b = json.loads(r["bundle_json"])
                    if content_digest(b) != r["content_digest"] or b.get("content_digest") != r["content_digest"]:
                        raise ValueError
                except (ValueError, TypeError):
                    out["_problems"].append({"event_id": r["event_id"], "scope": scope,
                                             "problem": "the bundle does not match its digest"})
        for r in nxt.values():
            out["_problems"].append({"event_id": r["event_id"], "scope": scope, "problem": "not on its hash chain"})
        out[scope] = ordered
    return out


def _detail(row: dict) -> dict:
    try:
        return json.loads(row.get("detail_json") or "{}")
    except ValueError:
        return {}


def _head(ordered: list[dict]) -> str:
    return ordered[-1]["row_hash"] if ordered else GENESIS


def _append(conn, *, scope: str, event: str, actor: str, actor_kind: str, content: str, prev_hash: str,
            actor_role: str = "", learner: str = "", path_id: str = "", mission_id: str = "", step_id: str = "",
            reason: str = "", bundle: Optional[dict] = None, detail: Optional[dict] = None,
            clock: Optional[Callable[[], str]] = None) -> dict:
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    row = {"event_id": f"copa-{uuid.uuid4().hex[:16]}", "scope": scope, "event": event, "actor": actor,
           "actor_kind": actor_kind, "actor_role": actor_role or "", "learner": learner or "",
           "path_id": path_id or "", "mission_id": mission_id or "", "step_id": step_id or "",
           "reason": reason or "", "content_digest": content,
           "bundle_json": canonical(bundle) if bundle is not None else "", "detail_json": canonical(detail or {}),
           "prev_hash": prev_hash, "created_at": (clock or _now)()}
    row["row_hash"] = _row_hash(row)
    try:
        conn.execute(f"INSERT INTO {TABLE} ({', '.join(_COLS)}) VALUES ({', '.join(['%s'] * len(_COLS))})",
                     tuple(row[c] for c in _COLS))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()     # PostgreSQL: the failed statement aborted the transaction
        except Exception:  # noqa: BLE001
            pass
        text = str(exc).lower()
        if "unique" in text or "duplicate" in text:
            raise CopRefused(409, "concurrent", "the record changed while you were writing: reload and try again") from exc
        raise CopRefused(503, "store_unavailable", f"the Academy ledger could not be written, so nothing changed ({exc})") from exc
    return row


def _is_person(actor: Any) -> bool:
    from tools.workflow_hitl.declared import SERVICE_ROLE

    return (isinstance(actor, dict) and bool(_text(actor.get("id"))) and actor.get("role") != SERVICE_ROLE
            and not _text(actor.get("id")).startswith("agent:"))


def pseudonym(user_id: str, conn=None, *, create: bool = True) -> Optional[str]:
    """The learner's pseudonymous xAPI account name: random, kept once, never derived from who they are."""
    own = conn is None
    conn = conn or _connection()
    try:
        row = conn.execute(f"SELECT pseudonym FROM {LEARNERS} WHERE user_id = %s", (user_id,)).fetchone()
        if row or not create:
            return tuple(row)[0] if row else None
        name = f"lrn-{uuid.uuid4().hex[:20]}"
        try:
            conn.execute(f"INSERT INTO {LEARNERS} (user_id, pseudonym, created_at) VALUES (%s, %s, %s)",
                         (user_id, name, _now()))
            conn.commit()
        except Exception:  # noqa: BLE001 — two first attempts at once: the first one's name stands
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            row = conn.execute(f"SELECT pseudonym FROM {LEARNERS} WHERE user_id = %s", (user_id,)).fetchone()
            if not row:
                raise
            return tuple(row)[0]
        return name
    finally:
        if own:
            conn.close()


def attempt(mission_id: str, answers: Any, actor: Optional[dict], *, conn=None, cfg: Optional[dict] = None,
            clock: Optional[Callable[[], str]] = None) -> dict:
    """A learner attempts a mission: graded on the server, recorded on their own chain. Returns the result and
    the statement it changes, if any. A service key or an agent never attempts."""
    if not _is_person(actor):
        raise CopRefused(403, "not_a_person", "a mission is attempted by a signed-in person, never a service key or an agent")
    cfg = cfg or load_config()
    track, mission = find_mission(cfg, _text(mission_id))
    g = grade(mission, answers, float((cfg.get("grading") or {}).get("pass_mark") or 1.0))
    own = conn is None
    conn = conn or _connection()
    try:
        learner = pseudonym(_text(actor["id"]), conn)
        scope = f"learner:{learner}"
        ch = chain(read_events(conn))
        problems = [p for p in ch["_problems"] if p["scope"] == scope]
        if problems:
            raise CopRefused(409, "chain_broken", f"your Academy record does not verify: {problems[0]['problem']}")
        row = _append(conn, scope=scope, event="completed" if g["passed"] else "attempted", actor=_text(actor["id"]),
                      actor_kind="person", actor_role=_text(actor.get("role")), learner=learner, path_id=track["id"],
                      mission_id=mission["id"], content=g["answers_digest"],
                      detail={"score": g["score"], "total": g["total"], "wrong": g["wrong"],
                              "mission_digest": mission_digest(mission), "config_sha256": cfg["_sha256"]},
                      prev_hash=_head(ch.get(scope, [])), clock=clock)
        rows = read_events(conn)
    finally:
        if own:
            conn.close()
    mine = [s for s in statements(cfg, rows) if s["actor"]["account"]["name"] == learner
            and s["object"]["id"] == path_activity(cfg, track["id"])]
    return {"mission_id": mission["id"], "path_id": track["id"], "passed": g["passed"], "score": g["score"],
            "total": g["total"], "wrong": g["wrong"], "event_id": row["event_id"], "event": row["event"],
            "learner": learner, "statement": mine[0] if mine else None}


# ── xAPI statements and the batch ────────────────────────────────────────────


def completions(cfg: dict, rows: Iterable[dict]) -> dict[tuple[str, str], dict[str, dict]]:
    """``(learner, path) -> {mission id: its FIRST passing event}``, from intact learner chains only, for the
    missions the path declares now."""
    ch = chain(rows)
    broken = {p["scope"] for p in ch["_problems"]}
    declared = {t["id"]: {m["id"] for m in t["missions"]} for t in cfg["tracks"]}
    out: dict[tuple[str, str], dict[str, dict]] = {}
    for scope, ordered in ch.items():
        if scope == "_problems" or not scope.startswith("learner:") or scope in broken:
            continue
        for r in ordered:
            if r["event"] != "completed" or r["mission_id"] not in declared.get(r["path_id"], set()):
                continue
            out.setdefault((r["learner"], r["path_id"]), {}).setdefault(r["mission_id"], r)
    return out


def _statement(cfg: dict, learner: str, path_id: str, verb_key: str, ts: str, result: dict) -> dict:
    verb = cfg["xapi"]["verbs"][verb_key]
    activity = path_activity(cfg, path_id)
    return {"id": str(uuid.uuid5(_NS, f"{activity}|{learner}|{verb}|{ts}")),
            "actor": {"objectType": "Agent", "account": {"homePage": cfg["xapi"]["homepage"], "name": learner}},
            "verb": {"id": verb, "display": {"en-US": verb_key}},
            "object": {"objectType": "Activity", "id": activity},
            "result": result,
            "timestamp": ts}


def statements(cfg: dict, rows: Iterable[dict]) -> list[dict]:
    """Every learner's statement per path, derived from the ledger alone. Deterministic."""
    sizes = {t["id"]: len(t["missions"]) for t in cfg["tracks"]}
    out = []
    for (learner, path_id), done in completions(cfg, rows).items():
        ts = max(r["created_at"] for r in done.values())
        if len(done) >= sizes[path_id]:
            out.append(_statement(cfg, learner, path_id, "completed", ts, {"completion": True, "success": True}))
        else:
            out.append(_statement(cfg, learner, path_id, "progressed", ts, {"completion": False}))
    return sorted(out, key=lambda s: (s["object"]["id"], s["actor"]["account"]["name"], s["timestamp"], s["id"]))


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("icdev")
    except Exception:  # noqa: BLE001 — an unpackaged checkout still says what it is
        return "unpackaged"


def content_digest(batch: dict) -> str:
    """SHA-256 over the canonical JSON of the batch WITHOUT ``content_digest`` (ICDEV[OS] recomputes it)."""
    return digest({k: v for k, v in batch.items() if k != "content_digest"})


def _pinned(rel: str, want: str) -> dict:
    raw = (BASE_DIR / rel).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != want:
        raise CopRefused(500, "schema", f"{rel} is not the pinned schema (sha256 {got}, pinned {want})")
    return json.loads(raw)


def schema() -> dict:
    return _pinned(SCHEMA_RELPATH, SCHEMA_SHA256)


def publication_schema() -> dict:
    return _pinned(PUBLICATION_RELPATH, PUBLICATION_SHA256)


def _errors(doc: Any, sch: dict) -> list[str]:
    import jsonschema

    v = jsonschema.Draft202012Validator(sch, format_checker=jsonschema.FormatChecker())
    return sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in v.iter_errors(doc))


def validate(batch: Any) -> list[str]:
    """The batch's errors against ICDEV[OS]'s schema, then its digest. Empty: ICDEV[OS] would take it."""
    errors = _errors(batch, schema())
    if not errors and batch.get("content_digest") != content_digest(batch):
        errors.append("content_digest is not the digest of the batch's content")
    return errors


def build(*, at: Optional[str] = None, cfg: Optional[dict] = None, rows: Optional[list[dict]] = None,
          check: bool = True) -> dict:
    """The ``icos-cop-academy/v1`` batch: every declared path and every statement the ledger supports, at ``at``
    (``produced_at``, inside the digest). Deterministic given the ledger, the config and ``at``."""
    cfg = cfg or load_config()
    rows = rows if rows is not None else read_events()
    batch = {
        "schema": SCHEMA,
        "producer": {"system": PRODUCER_SYSTEM, "tool": PRODUCER_TOOL, "version": _version()},
        "produced_at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "classification": CLASSIFICATION,
        "paths": [t["id"] for t in cfg["tracks"]],
        "statements": statements(cfg, rows),
    }
    batch["content_digest"] = content_digest(batch)
    if check:
        errors = validate(batch)
        if errors:
            raise CopRefused(500, "contract", f"the batch does not meet {SCHEMA}: " + "; ".join(errors[:10]))
    return batch


# ── prepared, then a person publishes ────────────────────────────────────────


def replay_bundle(ordered: list[dict]) -> dict:
    """The bundle chain's state: the latest prepared batch, whether a person decided it, the latest published."""
    prepared = published = None
    history = []
    for r in ordered:
        det = _detail(r)
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash", "prev_hash")} | {"detail": det}
        history.append(h)
        if r["event"] == "prepared":
            prepared = {**h, "decided": None}
        elif prepared and det.get("prepared_event") == prepared["event_id"]:
            prepared["decided"] = h
            if r["event"] == "published":
                published = {**h, "prepared_event": prepared["event_id"], "prepared_by": prepared["actor"]}
    return {"prepared": prepared, "published": published, "history": history}


def _bundle_state(conn) -> tuple[dict, list[dict], list[dict]]:
    ch = chain(read_events(conn))
    problems = [p for p in ch["_problems"] if p["scope"] == BUNDLE_SCOPE]
    ordered = ch.get(BUNDLE_SCOPE, [])
    return replay_bundle(ordered), problems, ordered


def builders(state: dict) -> dict[str, str]:
    """Who may not publish the prepared batch (``separate_from: builders``): whoever prepared it."""
    who = _text((state.get("prepared") or {}).get("actor"))
    return {who: "prepared this batch"} if who else {}


def prepare(actor: Optional[dict], *, note: str = "", conn=None, at: Optional[str] = None) -> dict:
    """Build the batch and record it, whole, at its digest, as PREPARED. Decides nothing; nothing leaves."""
    if not isinstance(actor, dict) or not _text(actor.get("id")):
        raise CopRefused(401, "no_actor", "preparing a batch needs a signed-in person or a named agent")
    kind = "person" if _is_person(actor) else "agent"
    own = conn is None
    conn = conn or _connection()
    try:
        cfg = load_config()
        rows = read_events(conn)
        batch = build(at=at, cfg=cfg, rows=rows)
        _, problems, ordered = _bundle_state(conn)
        if problems:
            raise CopRefused(409, "chain_broken", f"the bundle ledger does not verify: {problems[0]['problem']}")
        row = _append(conn, scope=BUNDLE_SCOPE, event="prepared", actor=_text(actor["id"]), actor_kind=kind,
                      actor_role=_text(actor.get("role")), content=batch["content_digest"], bundle=batch,
                      reason=_text(note), detail={"produced_at": batch["produced_at"],
                                                  "statements": len(batch["statements"]),
                                                  "config_sha256": cfg["_sha256"]},
                      prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    return {"event_id": row["event_id"], "content_digest": batch["content_digest"], "prepared_by": row["actor"],
            "actor_kind": kind, "produced_at": batch["produced_at"], "statements": len(batch["statements"]),
            "template": TEMPLATE, "next": "a person who did not prepare it publishes it or refuses, on /idp/cop-tracks"}


def publish(*, decision: str, reason: str, read_digest: str, actor: Optional[dict], conn=None) -> dict:
    """A person who did not prepare it publishes the prepared batch to ICDEV[OS], or refuses it.

    The person's step of ``decl-wft-cop-academy-xapi-publish``, with a reason, bound to the digest read.
    """
    from tools.workflow_hitl import declared

    if not isinstance(actor, dict):
        raise CopRefused(401, "no_person", "publishing is a signed-in person's declared step on /idp/cop-tracks")
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, ordered = _bundle_state(conn)
        if problems:
            raise CopRefused(409, "chain_broken", f"the bundle ledger does not verify: {problems[0]['problem']}")
        p = st["prepared"]
        if p is None:
            raise CopRefused(409, "nothing_prepared", "no xAPI batch is prepared: prepare one first")
        if p.get("decided"):
            raise CopRefused(409, "already_decided",
                             f"the prepared batch {p['content_digest'][:12]} was already {p['decided']['event']}: prepare a new one")
        if _text(read_digest) != p["content_digest"]:
            raise CopRefused(409, "digest", f"the prepared batch is {p['content_digest'][:12]}, not what you read (reload it)")
        dec = _text(decision).lower()
        try:
            taken = declared.take_step(
                TEMPLATE, surface=SURFACE, decision=dec, subject="cop_academy:xapi", reason=_text(reason),
                actor=actor, separated_from=builders(st),
                payload={"content_digest": p["content_digest"], "prepared_event": p["event_id"],
                         "produced_at": (p.get("detail") or {}).get("produced_at")})
        except declared.StepRefused as exc:
            raise CopRefused(exc.status, exc.code, exc.message) from exc
        event = "published" if taken["affirmative"] else "refused"
        row = _append(conn, scope=BUNDLE_SCOPE, event=event, actor=taken["decided_by"], actor_kind="person",
                      actor_role=_text(actor.get("role")), step_id=taken["step_id"], reason=_text(reason),
                      content=p["content_digest"], detail={"prepared_event": p["event_id"], "decision": dec},
                      prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    out = {"event": event, "event_id": row["event_id"], "content_digest": p["content_digest"], "step": taken,
           "decided_by": taken["decided_by"], "prepared_by": p["actor"]}
    if event == "published":
        out["published"] = published()
    return out


def published(conn=None) -> Optional[dict]:
    """The newest PUBLISHED batch, exactly as prepared (what ICDEV[OS] imports), and its publication beside it."""
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, ordered = _bundle_state(conn)
    finally:
        if own:
            conn.close()
    pub = st["published"]
    if pub is None or problems:
        return None
    prep = next((r for r in ordered if r["event_id"] == pub["prepared_event"]), None)
    if prep is None:
        return None
    batch = json.loads(prep["bundle_json"])
    decided = _utc(pub["created_at"])
    publication = {"schema": PUBLICATION_SCHEMA, "content_digest": pub["content_digest"], "signed_by": pub["actor"],
                   "role": pub["actor_role"] or None, "prepared_by": pub["prepared_by"], "step_id": pub["step_id"],
                   "template": TEMPLATE,
                   "decided_at": decided.strftime("%Y-%m-%dT%H:%M:%SZ") if decided else pub["created_at"],
                   "event_id": pub["event_id"], "row_hash": pub["row_hash"],
                   "producer": {"system": PRODUCER_SYSTEM, "tool": PRODUCER_TOOL, "version": _version()}}
    return {"batch": batch, "publication": publication}


def verify(batch: Any, publication: Any, rows: Optional[list[dict]] = None) -> dict:
    """Does an exported batch and its publication hold? Both against their pinned schemas, the batch's digest, the
    publication bound to it by someone who did not prepare it, and (given the ledger) that ledger's published row,
    on an intact chain, over that very batch."""
    errors = [f"batch: {e}" for e in validate(batch)] if isinstance(batch, dict) else ["batch: not a JSON object"]
    if isinstance(publication, dict):
        errors += [f"publication: {e}" for e in _errors(publication, publication_schema())]
        if isinstance(batch, dict) and publication.get("content_digest") != batch.get("content_digest"):
            errors.append("the publication was taken at another digest")
        if publication.get("signed_by") and publication.get("signed_by") == publication.get("prepared_by"):
            errors.append("the batch was published by the person who prepared it")
    else:
        errors.append("publication: missing (an unpublished batch never leaves)")
    ledger = None
    if rows is not None and isinstance(publication, dict) and isinstance(batch, dict):
        ch = chain(rows)
        ordered = ch.get(BUNDLE_SCOPE, [])
        probs = [p for p in ch["_problems"] if p["scope"] == BUNDLE_SCOPE]
        row = next((r for r in ordered if r["event_id"] == publication.get("event_id")), None)
        st = replay_bundle(ordered)
        prep = next((r for r in ordered if row and r["event_id"] == _detail(row).get("prepared_event")), None)
        ledger = {"chain_intact": not probs, "published_row": bool(row and row["event"] == "published")}
        if probs:
            errors.append(f"ledger: the bundle chain does not verify ({probs[0]['problem']})")
        if not row or row["event"] != "published" or row["row_hash"] != publication.get("row_hash"):
            errors.append("ledger: no published event with that id and hash")
        elif prep is None or prep["content_digest"] != batch.get("content_digest") or \
                json.loads(prep["bundle_json"] or "{}") != batch:
            errors.append("ledger: the published event is not over this batch")
        ledger["newest_published"] = bool(st["published"] and st["published"]["event_id"] == publication.get("event_id"))
    return {"ok": not errors, "errors": errors, "content_digest": (batch or {}).get("content_digest")
            if isinstance(batch, dict) else None, "ledger": ledger}


# ── reading ──────────────────────────────────────────────────────────────────


def progress(cfg: dict, rows: list[dict], learner: Optional[str]) -> dict[str, dict]:
    """One learner's state per path: the missions passed, and none / progressed / completed."""
    done = completions(cfg, rows)
    out = {}
    for t in cfg["tracks"]:
        passed = sorted((done.get((learner, t["id"])) or {}).keys()) if learner else []
        state = "completed" if len(passed) >= len(t["missions"]) else ("progressed" if passed else "none")
        out[t["id"]] = {"passed": passed, "of": len(t["missions"]), "state": state}
    return out


def bundle_view(conn=None) -> dict:
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, _ = _bundle_state(conn)
    finally:
        if own:
            conn.close()
    p = st["prepared"]
    return {"prepared": p, "published": st["published"], "history": st["history"], "builders": builders(st),
            "chain": {"intact": not problems, "problems": problems},
            "next": ["publish"] if p and not p.get("decided") else ["prepare"], "template": TEMPLATE}


def view(actor: Optional[dict] = None, conn=None) -> dict:
    """The page's view: the tracks (no answers), this learner's progress, the statements the next batch would carry,
    and the bundle ledger."""
    cfg = load_config()
    own = conn is None
    conn = conn or _connection()
    try:
        rows = read_events(conn)
        me = pseudonym(_text(actor["id"]), conn, create=False) if _is_person(actor) else None
        ledger = bundle_view(conn)
    finally:
        if own:
            conn.close()
    ch = chain(rows)
    return {"tracks": public_tracks(cfg), "learner": me, "progress": progress(cfg, rows, me),
            "statements": statements(cfg, rows), "ledger": ledger,
            "chain": {"intact": not ch["_problems"], "problems": ch["_problems"], "rows": len(rows)},
            "config": {"path": CONFIG_RELPATH, "sha256": cfg["_sha256"]},
            "schema": {"batch": SCHEMA, "path": SCHEMA_RELPATH, "contract": SCHEMA_CONTRACT,
                       "publication": PUBLICATION_SCHEMA, "publication_path": PUBLICATION_RELPATH},
            "xapi": {"path_prefix": cfg["xapi"]["path_prefix"], "homepage": cfg["xapi"]["homepage"],
                     "verbs": cfg["xapi"]["verbs"]}, "template": TEMPLATE}


def verify_published(conn=None) -> dict:
    """The newest published batch and its publication, verified against the ledger they came from."""
    own = conn is None
    conn = conn or _connection()
    try:
        pub = published(conn)
        rows = read_events(conn)
    finally:
        if own:
            conn.close()
    if pub is None:
        return {"ok": False, "errors": ["no xAPI batch has been published"], "content_digest": None, "ledger": None}
    return verify(pub["batch"], pub["publication"], rows)


# ── twin ─────────────────────────────────────────────────────────────────────


def twin_evidence() -> dict:
    """What the Academy twin reads: the config, the bundle ledger's state, and the completions the newest published
    batch does not carry (ICDEV[OS] cannot see them: unverified there)."""
    ev: dict[str, Any] = {"config": None, "bundle": None, "unreported": None, "problems": [], "errors": {}}
    try:
        cfg = load_config()
        ev["config"] = {"path": CONFIG_RELPATH, "sha256": cfg["_sha256"], "paths": [t["id"] for t in cfg["tracks"]]}
        ev["policy"] = cfg.get("publish") or {}
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["config"] = str(exc)
        return ev
    try:
        rows = read_events()
    except Exception as exc:  # noqa: BLE001
        ev["errors"]["ledger"] = str(exc)
        return ev
    ch = chain(rows)
    ev["problems"] = ch["_problems"]
    st = replay_bundle(ch.get(BUNDLE_SCOPE, []))
    pub, prep = st["published"], st["prepared"]
    batch = None
    if pub is not None:
        src = next((r for r in ch.get(BUNDLE_SCOPE, []) if r["event_id"] == pub["prepared_event"]), None)
        try:
            batch = json.loads(src["bundle_json"]) if src else None
        except ValueError:
            batch = None
    carried = {s["id"] for s in (batch or {}).get("statements") or []}
    now = statements(cfg, rows)
    missing = [s for s in now if s["id"] not in carried]
    newest = None
    if missing:
        latest = max(missing, key=lambda s: s["timestamp"])
        done = completions(cfg, rows)
        for (learner, path_id), events in done.items():
            for r in events.values():
                if learner == latest["actor"]["account"]["name"] and r["created_at"] == latest["timestamp"]:
                    newest = r["event_id"]
    try:
        off_contract = bool(batch is not None and validate(batch))
    except CopRefused as exc:
        off_contract = True
        ev["errors"]["schema"] = exc.message
    ev["bundle"] = {
        "prepared": prep and {k: prep[k] for k in ("event_id", "actor", "created_at", "content_digest")}
        | {"decided": bool(prep.get("decided"))},
        "published": pub and {k: pub[k] for k in ("event_id", "actor", "prepared_by", "created_at", "content_digest")}
        | {"produced_at": (batch or {}).get("produced_at"), "statements": len((batch or {}).get("statements") or []),
           "off_contract": off_contract}}
    ev["unreported"] = {"statements": len(missing), "newest_event": newest}
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)`` for the Academy tracks and their xAPI bundle. Pure: a replay judges the same."""
    from tools.twin_core.governance import age_hours, check, finding

    errors = ev.get("errors") or {}
    bundle = ev.get("bundle")
    checks = [check("cop.academy.config", f"The declared tracks ({CONFIG_RELPATH})", ev.get("config") is not None,
                    errors.get("config") or "not read"),
              check("cop.academy.ledger", f"The Academy ledger ({TABLE})", bundle is not None,
                    errors.get("ledger") or errors.get("config") or "not read")]
    findings: list[dict] = []
    pol = {**(ev.get("policy") or {}), **(policy or {})}
    stale = float(pol.get("stale_days") or 25)
    waiting = float(pol.get("waiting_warn_days") or 7)
    for p in ev.get("problems") or []:
        findings.append(finding("cop.academy.chain_broken", "critical", f"{p['scope']}: {p['problem']}",
                                thread_id=f"cop_academy:{p['event_id']}",
                                recommendation="The record cannot be trusted; the ledger is append-only, so find who "
                                               "wrote the row and treat it as an incident."))
    pub = (bundle or {}).get("published")
    prep = (bundle or {}).get("prepared")
    if bundle is not None:
        # Nothing published is unverified, never passed: ICDEV[OS] reads every path as unverified.
        checks.append(check("cop.academy.published", "A published xAPI batch for ICDEV[OS]", pub is not None,
                            "no xAPI batch has been published: ICDEV[OS] reads every learning path as unverified"))
    un = ev.get("unreported") or {}
    if un.get("statements") and un.get("newest_event"):
        findings.append(finding("cop.academy.unreported", "low",
                                f"{un['statements']} statement(s) the newest published batch does not carry",
                                thread_id=f"cop_academy:{un['newest_event']}",
                                recommendation="Prepare a batch; a person who did not prepare it publishes it. Until "
                                               "then ICDEV[OS] reads those completions as unverified."))
    if prep and not prep.get("decided"):
        age = age_hours(prep.get("created_at"), observed_at)
        if age is not None and age > waiting * 24:
            findings.append(finding("cop.academy.awaiting_publish", "low",
                                    f"a prepared batch has waited {age / 24:.0f} days for a person",
                                    thread_id=f"cop_academy:{prep['event_id']}",
                                    recommendation="A person who did not prepare it publishes it or refuses."))
    if pub:
        thread = f"cop_academy:{pub['event_id']}"
        if pub.get("actor") and pub.get("actor") == pub.get("prepared_by"):
            findings.append(finding("cop.academy.self_published", "critical",
                                    "the batch was published by the person who prepared it", thread_id=thread,
                                    recommendation="Treat it as unpublished; a person who did not prepare it "
                                                   "publishes a new one."))
        if pub.get("off_contract"):
            findings.append(finding("cop.academy.off_contract", "high",
                                    f"the published batch no longer meets {SCHEMA} as pinned", thread_id=thread,
                                    recommendation="Re-pin the schema ICDEV[OS] publishes, then prepare and publish "
                                                   "a new batch."))
        age = age_hours(pub.get("produced_at") or pub.get("created_at"), observed_at)
        if age is not None and age > stale * 24:
            findings.append(finding("cop.academy.stale", "medium",
                                    f"the newest published batch is {age / 24:.0f} days old", thread_id=thread,
                                    recommendation="ICDEV[OS] counts a batch past its freshness budget for nothing: "
                                                   "publish a new one."))
    summary = {"cop_academy": {"published": bool(pub), "statements_published": (pub or {}).get("statements"),
                               "unreported": un.get("statements")}}
    return checks, findings, summary
