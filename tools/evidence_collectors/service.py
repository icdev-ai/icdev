# CUI // SP-CTI
"""Evidence collectors for customer environments (icx-ent-02): collect, prepare, publish, read, observe.

ICDEV[IT] produces; ICDEV[OS] judges. Four read-only collectors -- cloud configuration (AWS Config), the identity
provider (OIDC Discovery and SCIM 2.0), endpoints (Microsoft Intune via Graph) and the SIEM (Splunk) -- read a
customer system's sources with credentials taken BY REFERENCE from the customer's vault or the OS keyring, and record
OBSERVATIONS (a fact, true/false/unverified, observed_at, collector, system, freshness budget). A reviewed mapping
(``args/evidence_collectors.yaml``) turns them into objective results for SP 800-171 Rev. 2 and SP 800-53 Rev. 5,
never claiming more than was observed (``bundle.py``). One ``icos-objective-evidence/v1`` report per system per run.

**Preparing decides nothing** (:func:`prepare`): anyone signed in, or an agent (recorded as one), runs the collectors
and records the report at its content digest, with the observations, the configuration's digest and the system's
digest, in the append-only, hash-chained ``ec_events`` (``ledger.py``).

**Publishing is a person's step** (:func:`publish`, ``decl-wft-ec-evidence-publish``): a person of the system's
owning org, cleared for its marking, who did not prepare it, publishes it or refuses, with a reason, bound to the
digest they read AND to the system's configuration digest (a changed endpoint, mapping or credential reference since
it was prepared is refused). Only then is the report exported, its publication EMBEDDED exactly as ICDEV[OS]'s
``icdev_os/publication.py`` checks it (``split(raw, "embedded")``, ``check_publication``).

**Who sees what.** A system is shown, prepared for, published or exported only for a reader whose tenant is the
system's org and whose clearance is at or above its marking; anything else is "no such system" (never confirming it
exists), and a list counts what it holds back without describing it. An agent reads through MCP at the MCP results'
marking (CUI), is in no org and names itself (the MCP caller says who it is), so it sees and prepares SYNTHETIC
systems only, never a customer's, and only drafts. Live collection is throttled per system (:func:`_throttle`).

**The twin is per reader.** The console's twin route judges only the systems its reader may see and counts the rest
(:func:`twin_evidence` with a reader); the platform observation (no reader; MCP and the observatory read it) names
synthetic systems only and gives customer systems as totals, with no id, ordinal, event, time or digest of theirs;
the generic thread resolver returns no system, person or reason (:func:`thread_record` is the per-reader one).

**A decision counts only with its step.** Publishing writes the ledger row first, then records the declared step
under the id the row carries; a row whose step is not recorded (that way, by that person) decides nothing for any
reader -- the page, the bundle, IQE, the twin (``step_missing``) -- and the report can be decided again
(:func:`replay_checked`).
"""
from __future__ import annotations

import contextlib
import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from tools.evidence_collectors import bundle as bmod
from tools.evidence_collectors import config as cmod
from tools.evidence_collectors import ledger
from tools.evidence_collectors.collectors import REGISTRY
from tools.evidence_collectors.collectors.base import instant, rfc3339
from tools.evidence_collectors.constants import MARKINGS, MCP_CLEARANCE, SURFACE, TEMPLATE
from tools.evidence_collectors.credentials import resolve as resolve_ref

Refused = ledger.LedgerRefused
_ORDER = {m: i for i, m in enumerate(MARKINGS)}


# ── readers ──────────────────────────────────────────────────────────────────


def current_reader() -> dict | None:
    """The signed-in principal as a reader: ``{id, role, clearance, org}``; None outside a request or signed out."""
    try:
        from flask import g, has_request_context
    except Exception:  # noqa: BLE001
        return None
    if not has_request_context():
        return None
    u = getattr(g, "current_user", None)
    if not u:
        return None
    get = (u.get if isinstance(u, dict) else lambda k, d=None: getattr(u, k, d))
    if not get("id"):
        return None
    clearance, org = get("clearance_level"), get("tenant_id")
    if str(get("role") or "") != "service":
        # The org and clearance are read FRESH from the person's own row: a session carries the whole row, but a
        # dashboard API key's principal carries no tenant_id (tools/dashboard/auth.py validate_api_key), and a
        # reader of no org sees nothing. An unreadable row leaves what the request carried.
        row = None
        with contextlib.suppress(Exception):     # unreadable: what the request carried (fail closed below)
            from tools.dashboard.auth import get_user_by_id

            row = get_user_by_id(str(get("id")))
        if row is not None:
            row = dict(row)
            clearance, org = row.get("clearance_level"), row.get("tenant_id")
    return {"id": str(get("id")), "role": str(get("role") or ""), "clearance": str(clearance or ""),
            "org": str(org or "")}


def mcp_reader(agent: str) -> dict:
    """An agent through MCP: cleared to the MCP results' marking, in no org."""
    return {"id": f"agent:{agent}", "role": "service", "clearance": MCP_CLEARANCE, "org": None}


def _cleared(reader: dict | None, marking: str) -> bool:
    c = str((reader or {}).get("clearance") or "").strip().upper()
    return c in _ORDER and marking in _ORDER and _ORDER[c] >= _ORDER[marking]


def visible(system: dict, reader: dict | None) -> bool:
    """Fail closed: a reader, cleared for the system's marking, and of its owning org. An agent is in no org and
    its name is its caller's say-so, so it sees a SYNTHETIC system only (at or below the MCP marking), never a
    customer's; it never publishes."""
    if not reader or not _cleared(reader, system["marking"]):
        return False
    if reader.get("org") is None and str(reader.get("id", "")).startswith("agent:"):
        return system.get("synthetic") is True
    return str(reader.get("org") or "").strip() == str(system["org"]).strip()


def _system(sid: str, reader: dict | None, cfg: dict) -> dict:
    s = cmod.system(cfg, str(sid or ""))
    if s is None or not visible(s, reader):
        # One answer for "unknown" and "not yours": it confirms nothing.
        raise Refused(404, "not_found", "no such system")
    return s


# ── collecting ───────────────────────────────────────────────────────────────


def collect(system: dict, cfg: dict, *, now: datetime | None = None,
            resolve: Callable[[str], Any] | None = None) -> list[dict]:
    """Run every collector the system declares. A collector that cannot read its source yields its facts as
    unverified, with why; it never raises past here."""
    from tools.evidence_collectors import transport

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    resolve = resolve or resolve_ref
    lim = cfg.get("limits") or {}
    run_s, run_b = float(lim.get("run_seconds", 300)), int(lim.get("run_bytes", 64 * 1024 * 1024))
    out: list[dict] = []
    for name, spec in sorted((system.get("collectors") or {}).items()):
        mod = REGISTRY[name]
        budget = float(cfg["collectors"][name]["max_age_h"])
        try:
            with transport.budget(seconds=run_s, max_bytes=run_b):      # one allowance per collector run
                out += mod.collect(system["id"], spec, now=now, max_age_h=budget, resolve=resolve)
        except Exception as exc:  # noqa: BLE001 — a collector fault is an unverified source, said
            from tools.evidence_collectors.collectors.base import unread

            out += unread(system=system["id"], collector=name, kind=mod.KIND, facts=mod.FACTS,
                          why=f"collector fault ({type(exc).__name__})", max_age_h=budget, source=name)
    return out


# ── the ledger ───────────────────────────────────────────────────────────────


def _stream(sid: str) -> str:
    return f"system:{sid}"


def replay(ordered: list[dict], step_taken: Callable[[dict, bool], bool] | None = None) -> dict:
    """One system's ledger: the newest prepared report, whether a person decided it, the newest published.

    A decision row (published/refused) on the open preparation:

    * named by a later ``step_lost`` row: VOID -- it decides nothing and the preparation is open again (listed in
      ``lost_steps``). Only that explicit row reopens a report;
    * else, with ``step_taken`` (:func:`_step_taken`, through :func:`replay_checked`) saying its declared step is
      recorded, that way, by that person: the decision;
    * else (its step not recorded YET, or the step store unreadable): PENDING -- it blocks every further decision
      and publishes nothing until its step lands (then it is the decision) or a ``step_lost`` row voids it. A report
      is never reopened by an absent or unreadable step, so a later decision can never be overturned by a step that
      lands after it.

    Pure when ``step_taken`` is None (the chain alone: every decision row counts).
    """
    voided = {str((ledger.payload(r).get("detail") or {}).get("decision_event") or "")
              for r in ordered if r["event"] == "step_lost"}
    prepared = published = None
    history, lost, after_void, superseded = [], [], [], []
    for r in ordered:
        p = ledger.payload(r)
        det = p.get("detail") or {}
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash", "prev_hash")}
        history.append(h)
        if r["event"] == "prepared":
            prepared = {**h, "decided": None, "pending": None, "report": p.get("content"), "detail": det}
        elif r["event"] in ("published", "refused") and not (
                prepared and not prepared["decided"] and not prepared["pending"]
                and det.get("prepared_event") == prepared["event_id"]):
            # A decision on a preparation that was not open: it decides nothing. Its step being recorded is a finding.
            if step_taken is not None and step_taken(h, r["event"] == "published"):
                superseded.append(h)
        elif r["event"] in ("published", "refused"):
            if r["event_id"] in voided:
                lost.append(h)
                if step_taken is not None and step_taken(h, r["event"] == "published"):
                    after_void.append(h)      # voided, yet its step was recorded later: it still decides nothing
                continue
            if step_taken is not None and not step_taken(h, r["event"] == "published"):
                prepared["pending"] = h
                continue
            prepared["decided"] = h
            if r["event"] == "published":
                published = {**h, "prepared_event": prepared["event_id"], "prepared_by": prepared["actor"],
                             "report": prepared["report"], "prepared_detail": prepared["detail"]}
    return {"prepared": prepared, "published": published, "history": history, "lost_steps": lost,
            "pending": (prepared or {}).get("pending"), "recorded_after_void": after_void,
            "recorded_superseded": superseded}


def _step_taken(row: dict, affirmative: bool) -> bool:
    """The decision row's declared step is recorded as decided, that way, by the row's own person. False when it is
    not (yet) or cannot be read: the row is then PENDING (it blocks), never void."""
    from tools.workflow_hitl import declared

    try:
        st = declared.step_state(str(row.get("step_id") or ""))
    except Exception:  # noqa: BLE001 — unreadable: decides nothing
        return False
    return bool(st) and st["status"] in ("decided", "spent") and st["affirmative"] is affirmative \
        and st["decided_by"] == row["actor"]


def replay_checked(ordered: list[dict]) -> dict:
    """:func:`replay` with each decision held to its recorded step: what every reader and the twin use."""
    return replay(ordered, step_taken=_step_taken)


def _state(sid: str, conn) -> tuple[dict, list[dict], list[dict]]:
    ordered, problems = ledger.stream_rows(_stream(sid), conn)
    return replay_checked(ordered), problems, ordered


def builders(state: dict) -> dict[str, str]:
    """Who may not publish (``separate_from: builders``): whoever prepared the report."""
    who = str((state.get("prepared") or {}).get("actor") or "").strip()
    return {who: "prepared this report"} if who else {}


_LAST_LIVE: dict[str, float] = {}
_LIVE_LOCK = threading.Lock()


def _throttle(sid: str, cfg: dict, made: datetime, conn=None) -> None:
    """At most one LIVE collection per system per ``limits.min_collect_interval_s``, on the injected clock: a refused
    or failed attempt counts (this process remembers it), and so does a live report already in the ledger (every
    process sees it, by its ``produced_at``). A time ahead of now counts as now. A person or an agent cannot hammer a
    customer's sources with credentialed reads."""
    gap = float((cfg.get("limits") or {}).get("min_collect_interval_s", 300))
    if gap <= 0:
        return
    now = made.timestamp()
    own = conn is None
    c = conn or ledger.connection()
    try:
        ordered, _ = ledger.stream_rows(_stream(sid), c)
    finally:
        if own:
            c.close()
    last = 0.0
    for r in reversed(ordered):
        p = ledger.payload(r)
        if r["event"] == "prepared" and (p.get("detail") or {}).get("live") is True:
            t = instant((p.get("content") or {}).get("produced_at"))
            last = min(now, t.timestamp()) if t is not None else now     # unreadable: as if just now
            break
    with _LIVE_LOCK:
        last = max(last, min(now, _LAST_LIVE.get(sid, 0.0)))
        if now - last < gap:
            raise Refused(429, "too_soon", f"this system's sources were read less than {gap:g}s ago: try again in "
                                           f"{int(gap - (now - last)) + 1}s")
        _LAST_LIVE[sid] = now


def prepare(sid: str, actor: dict | None, reader: dict | None, *, note: str = "", conn=None,
            cfg: dict | None = None, observations: list[dict] | None = None, now: datetime | None = None,
            resolve: Callable[[str], Any] | None = None) -> dict:
    """Collect from the system's sources, build its report and record it PREPARED at its digest. Decides nothing.

    ``observations``/``resolve`` are for the tests and the e2e seed (local stubs); no route passes them.
    """
    if not isinstance(actor, dict) or not actor.get("id"):
        raise Refused(401, "no_actor", "preparing needs a signed-in person or a named agent")
    cfg = cfg or cmod.load()
    s = _system(sid, reader, cfg)
    made = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    own0 = conn is None
    c0 = conn or ledger.connection()
    try:
        st0, _, _ = _state(s["id"], c0)
    finally:
        if own0:
            c0.close()
    if st0.get("pending"):
        # A new report would supersede the one a decision is being recorded on.
        raise Refused(409, "pending", "a decision on this system's report is being recorded: it stands until it is, "
                                      "or until it is marked lost")
    live = observations is None
    if live:
        _throttle(s["id"], cfg, made, conn)
    obs = observations if not live else collect(s, cfg, now=made, resolve=resolve)
    try:
        report, judged = bmod.build(s, obs, cfg["mapping"], made=made, mapping_sha256=cfg.get("_sha256", ""))
    except bmod.BundleError as exc:
        raise Refused(409 if exc.code == "nothing_determinable" else 500, exc.code, exc.message) from exc
    own = conn is None
    conn = conn or ledger.connection()
    try:
        _, problems, ordered = _state(s["id"], conn)
        if problems:
            raise Refused(409, "chain_broken", f"the ledger of {s['id']} does not verify: {problems[0]['problem']}")
        row = ledger.append(conn, stream=_stream(s["id"]), event="prepared", system_id=s["id"],
                            actor=str(actor["id"]), actor_kind=ledger.actor_kind(actor),
                            actor_role=str(actor.get("role") or ""), content_digest=report["content_digest"],
                            content=report, reason=note,
                            detail={"observations": obs, "unverified": judged["unverified"], "facts": judged["facts"],
                                    "config_sha256": cfg.get("_sha256", ""),
                                    "system_digest": cmod.digest_of_system(cfg, s["id"]), "org": s["org"],
                                    "observations_digest": bmod.observations_digest(obs), "live": live},
                            prev_hash=ledger.head(ordered))
    finally:
        if own:
            conn.close()
    return {"system": s["id"], "event_id": row["event_id"], "content_digest": report["content_digest"],
            "prepared_by": row["actor"], "actor_kind": row["actor_kind"], "entries": len(report["entries"]),
            "unverified": len(judged["unverified"]), "template": TEMPLATE,
            "next": "a person of the system's org who did not prepare it publishes it or refuses, on "
                    "/evidence-collectors/"}


def publish(sid: str, *, decision: str, reason: str, read_digest: str, actor: dict | None, reader: dict | None,
            conn=None, cfg: dict | None = None) -> dict:
    """A person who did not prepare it publishes the system's prepared report to ICDEV[OS], or refuses it."""
    from tools.workflow_hitl import declared

    if not isinstance(actor, dict):
        raise Refused(401, "no_person", "publishing is a signed-in person's declared step on /evidence-collectors/")
    cfg = cfg or cmod.load()
    s = _system(sid, reader, cfg)
    if reader.get("org") is None:
        raise Refused(403, "not_a_person", "an agent only prepares; a person of the system's org publishes")
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, ordered = _state(s["id"], conn)
        if problems:
            raise Refused(409, "chain_broken", f"the ledger of {s['id']} does not verify: {problems[0]['problem']}")
        p = st["prepared"]
        if p is None:
            raise Refused(409, "nothing_prepared", f"no report of {s['id']} is prepared: prepare one first")
        if p.get("decided"):
            raise Refused(409, "already_decided", f"the prepared report {p['content_digest'][:12]} was already "
                                                  f"{p['decided']['event']}: prepare a new one")
        if p.get("pending"):
            raise Refused(409, "pending", "a decision on this report is being recorded (or its record cannot be "
                                          "read): it stands until it is, or until it is marked lost; reload it")
        if str(read_digest or "").strip() != p["content_digest"]:
            raise Refused(409, "digest", f"the prepared report is {p['content_digest'][:12]}, not what you read "
                                         "(reload it)")
        sdig = cmod.digest_of_system(cfg, s["id"])
        if (p.get("detail") or {}).get("system_digest") != sdig:
            raise Refused(409, "config_changed", "the system's endpoints, credential references or the mapping "
                                                 "changed since this report was prepared: prepare a new one")
        dec = str(decision or "").strip().lower()
        why = str(reason or "").strip()
        step = {"surface": SURFACE, "decision": dec, "reason": why, "actor": actor, "separated_from": builders(st)}
        try:
            # Checked first, then the LEDGER is written (it claims the chain head: a concurrent decision is refused
            # here, before any step is recorded), then the step under the id the row already carries. Until that
            # step is recorded the row is PENDING and blocks every other decision (replay); if recording it is
            # refused, a step_lost row voids it and the report is open again. Nothing is ever reopened otherwise.
            affirmative = declared.check_step(TEMPLATE, **step)
        except declared.StepRefused as exc:
            raise Refused(exc.status, exc.code, exc.message) from exc
        step_id = declared.new_step_id()
        event = "published" if affirmative else "refused"
        row = ledger.append(conn, stream=_stream(s["id"]), event=event, system_id=s["id"],
                            actor=str(actor["id"]), actor_kind="person", actor_role=str(actor.get("role") or ""),
                            step_id=step_id, reason=why, content_digest=p["content_digest"],
                            detail={"prepared_event": p["event_id"], "decision": dec, "org": s["org"],
                                    "system_digest": sdig}, prev_hash=ledger.head(ordered))
        try:
            taken = declared.take_step(
                TEMPLATE, subject=f"objective_evidence:{s['id']}", step_id=step_id, **step,
                payload={"system": s["id"], "org": s["org"], "content_digest": p["content_digest"],
                         "prepared_event": p["event_id"], "system_digest": sdig, "ledger_event": row["event_id"]})
        except declared.StepRefused as exc:
            note = "the decision is void and the report open again"
            try:
                ledger.append(conn, stream=_stream(s["id"]), event="step_lost", system_id=s["id"],
                              actor=str(actor["id"]), actor_kind="person", actor_role=str(actor.get("role") or ""),
                              step_id=step_id, reason=f"the declared step was not recorded ({exc.code})",
                              detail={"decision_event": row["event_id"], "prepared_event": p["event_id"],
                                      "org": s["org"]}, prev_hash=row["row_hash"])
            except Refused:
                note = "it stays pending (blocking) until an operator marks it lost"
            raise Refused(exc.status, exc.code, f"{exc.message}; {note}") from exc
        # The step is recorded; was the decision voided meanwhile (an operator marked it lost)? Never report success.
        after, _, _ = _state(s["id"], conn)
        if ((after["prepared"] or {}).get("decided") or {}).get("event_id") != row["event_id"]:
            raise Refused(409, "voided", "your decision was marked lost before its step was recorded, so it decides "
                                         "nothing (your step stands in the decision log): reload and decide again")
    finally:
        if own:
            conn.close()
    out = {"system": s["id"], "event": event, "event_id": row["event_id"], "content_digest": p["content_digest"],
           "step": taken, "decided_by": taken["decided_by"], "prepared_by": p["actor"]}
    if event == "published":
        out["bundle"] = published_bundle(s["id"], reader, cfg=cfg)
    return out


#: How long a decision may wait for its step before an operator may mark it lost (an in-flight request is never
#: raced: the step store is checked to hold no record of it at all).
STEP_GRACE_S = 600


def mark_step_lost(sid: str, event_id: str, *, actor: dict, reason: str, asked_by: str, os_user: str,
                   cfg: dict | None = None, now: datetime | None = None, conn=None) -> dict:
    """The operator's path after a crash between a decision's ledger row and its step: void the PENDING decision
    ``event_id`` with a ``step_lost`` row, so a person can decide the report again. Refused unless it is the pending
    decision of the open report, at least :data:`STEP_GRACE_S` old, and the step store is READABLE and holds no
    record of its step (an unreadable store stays blocking). It decides nothing itself. Host-side (cli.py)."""
    from tools.workflow_hitl import declared

    cfg = cfg or cmod.load()
    s = cmod.system(cfg, str(sid or ""))
    if s is None:
        raise Refused(404, "not_found", "no such system")
    if not str(reason or "").strip() or not isinstance(actor, dict) or not actor.get("id") \
            or not str(asked_by or "").strip() or not str(os_user or "").strip():
        raise Refused(400, "reason", "the operator (OS user and agent), the person who asked, and a reason are "
                                     "required")
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, ordered = _state(s["id"], conn)
        if problems:
            raise Refused(409, "chain_broken", "the ledger does not verify")
        pend = st["pending"]
        if not pend or pend["event_id"] != str(event_id or ""):
            raise Refused(409, "not_pending", "that is not the pending decision of this system's open report")
        t = instant(str(pend["created_at"]))
        at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if t is None or (at - t).total_seconds() < STEP_GRACE_S:
            raise Refused(409, "too_soon", f"a decision may still be recording its step: wait {STEP_GRACE_S}s")
        try:
            known = declared.step_state(str(pend["step_id"]))
        except Exception as exc:
            raise Refused(503, "store_unavailable", "the step store cannot be read: the decision stays pending") \
                from exc
        if known is not None:
            raise Refused(409, "step_recorded", "its step has a record: it is not lost")
        row = ledger.append(conn, stream=_stream(s["id"]), event="step_lost", system_id=s["id"],
                            actor=str(actor["id"]), actor_kind=ledger.actor_kind(actor),
                            actor_role=str(actor.get("role") or ""), step_id=str(pend["step_id"]),
                            reason=f"{str(reason).strip()} (asked by {str(asked_by).strip()})",
                            detail={"decision_event": pend["event_id"], "org": s["org"],
                                    "os_user": str(os_user).strip(), "asked_by": str(asked_by).strip(),
                                    "prepared_event": (st["prepared"] or {}).get("event_id")},
                            prev_hash=ledger.head(ordered))
    finally:
        if own:
            conn.close()
    return {"system": s["id"], "event_id": row["event_id"], "voided": pend["event_id"]}


def published_bundle(sid: str, reader: dict | None, *, conn=None, cfg: dict | None = None) -> dict | None:
    """The system's newest PUBLISHED report with its publication embedded, as ICDEV[OS] imports it; None when none
    is, or the ledger does not verify."""
    cfg = cfg or cmod.load()
    s = _system(sid, reader, cfg)
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, _ = _state(s["id"], conn)
    finally:
        if own:
            conn.close()
    pub = st["published"]
    if pub is None or problems:
        return None
    t = instant(pub["created_at"]) or datetime.now(timezone.utc)
    try:
        return bmod.published(pub["report"], prepared_by=pub["prepared_by"], signed_by=pub["actor"],
                              step_id=pub["step_id"], decided_at=rfc3339(t))
    except bmod.BundleError:
        return None


# ── reading ──────────────────────────────────────────────────────────────────


def _collectors_of(s: dict, cfg: dict) -> list[dict]:
    out = []
    for name, spec in sorted((s.get("collectors") or {}).items()):
        mod = REGISTRY[name]
        out.append({"name": name, "kind": mod.KIND, "integration": mod.INTEGRATION,
                    "max_age_h": float(cfg["collectors"][name]["max_age_h"]),
                    "credentials": sorted({str(v).split(":", 1)[0] for v in (spec.get("credentials") or {}).values()}),
                    "facts": list(mod.FACTS) if name != "aws-config" else [f"aws-config:{k}" for k in spec["rules"]]})
    return out


def _brief(st: dict) -> dict:
    p, pub = st["prepared"], st["published"]
    return {"prepared": p and {k: p[k] for k in ("event_id", "actor", "actor_kind", "created_at", "content_digest")}
            | {"decided": (p.get("decided") or {}).get("event")},
            "published": pub and {k: pub[k] for k in ("event_id", "actor", "prepared_by", "created_at",
                                                      "content_digest", "step_id")}}


def systems_view(reader: dict | None, conn=None, cfg: dict | None = None) -> dict:
    """Every system the reader may see, with its ledger state; the rest are COUNTED, never described."""
    cfg = cfg or cmod.load()
    ch = ledger.chain(ledger.read(conn))
    seen, held = [], 0
    for s in cfg.get("systems") or []:
        if not visible(s, reader):
            held += 1
            continue
        st = replay_checked(ch.get(_stream(s["id"]), []))
        seen.append({"id": s["id"], "synthetic": bool(s.get("synthetic")), "marking": s["marking"],
                     "collectors": _collectors_of(s, cfg), **_brief(st)})
    probs = [p for p in ch["_problems"] if p["stream"] in {_stream(x["id"]) for x in seen}]
    return {"systems": seen, "held_back": held, "chain": {"intact": not probs, "problems": probs},
            "config": {"path": cfg.get("_path"), "sha256": cfg.get("_sha256")}, "template": TEMPLATE,
            "mapping": [{"objective": m["objective"], "all_of": m["all_of"], "scope": m["scope"],
                         "basis": m["basis"], "crosswalk": crosswalk_hint(m["objective"])} for m in cfg["mapping"]]}


def system_view(sid: str, reader: dict | None, conn=None, cfg: dict | None = None) -> dict:
    cfg = cfg or cmod.load()
    s = _system(sid, reader, cfg)
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, _ = _state(s["id"], conn)
    finally:
        if own:
            conn.close()
    p = st["prepared"]
    return {"system": {"id": s["id"], "synthetic": bool(s.get("synthetic")), "marking": s["marking"],
                       "collectors": _collectors_of(s, cfg)},
            "prepared": p and {**{k: p[k] for k in ("event_id", "actor", "actor_kind", "created_at", "content_digest",
                                                    "reason")},
                               "decided": p.get("decided"), "report": p.get("report"),
                               "observations": (p.get("detail") or {}).get("observations") or [],
                               "unverified": (p.get("detail") or {}).get("unverified") or []},
            "published": _brief(st)["published"], "history": st["history"], "builders": builders(st),
            "chain": {"intact": not problems, "problems": problems},
            "pending": st.get("pending") and {k: st["pending"][k] for k in ("event_id", "event", "actor", "created_at")},
            "next": (["wait"] if st.get("pending") else ["publish"] if p and not p.get("decided") else ["prepare"]),
            "template": TEMPLATE}


def crosswalk_hint(objective: str) -> str | None:
    """ICDEV's own 800-53 to 800-171 crosswalk (tools/compliance/crosswalk_engine.py) as a REVIEW AID beside a mapping
    row: what it relates to the objective's control. It never carries evidence (ICDEV[OS]'s rule: only the objective
    itself does)."""
    try:
        from tools.compliance import crosswalk_engine

        fw, _, oid = objective.partition("/")
        if fw == "sp800-53r5":
            ctl = oid.split("_obj")[0].split(".")[0].upper()
            v = crosswalk_engine.get_frameworks_for_control(ctl).get("nist_800_171")
            return f"ICDEV crosswalk: {ctl} relates to SP 800-171 {str(v).replace('171-', '')}" if v else None
        if fw == "sp800-171r2":
            req = oid.split("[")[0]
            hits = [e.get("nist_800_53") for e in crosswalk_engine.load_crosswalk()
                    if str(e.get("nist_800_171") or "").replace("171-", "") == req]
            return f"ICDEV crosswalk: {req} relates to SP 800-53 {', '.join(sorted(map(str, hits)))}" if hits else None
    except Exception:  # noqa: BLE001 — a review aid that cannot be read is simply absent
        return None
    return None


# ── twin ─────────────────────────────────────────────────────────────────────


def _twin_row(label: str, st: dict, cfg: dict, *, named: bool) -> dict:
    """One system's twin row from its checked replay. ``named``: objectives by name (a reader's own, or a synthetic
    system); otherwise counted."""
    pub, prep = st["published"], st["prepared"]
    facts = None
    if pub is not None:
        rep = pub.get("report") or {}
        other = sorted(e["objective"] for e in rep.get("entries") or [] if e.get("result") == "other_than_satisfied")
        facts = {"produced_at": rep.get("produced_at"), "other_than_satisfied": other if named else len(other),
                 "unverified": len((pub.get("prepared_detail") or {}).get("unverified") or []),
                 "budget_h": min((float(c["max_age_h"]) for c in cfg["collectors"].values()), default=24)}
    return {"system": label,
            "prepared": prep and {k: prep[k] for k in ("event_id", "created_at", "content_digest")}
            | {"decided": bool(prep.get("decided"))},
            "published": pub and {k: pub[k] for k in ("event_id", "created_at", "content_digest")}
            | {"self_published": str(pub.get("actor") or "").casefold()
               == str(pub.get("prepared_by") or "").casefold()},
            "step_missing": [st["pending"]["event_id"]] if st.get("pending") else [],
            "step_recorded_after_void": [h["event_id"] for h in st.get("recorded_after_void") or []],
            "step_on_superseded": [h["event_id"] for h in st.get("recorded_superseded") or []],
            "facts": facts}


def twin_evidence(cfg: dict | None = None, reader: dict | None = None, *, observed_at: str = "",
                  waiting_days: float = 7) -> dict:
    """Each system's ledger state (decisions held to their recorded steps) and its newest published report's facts.

    With a ``reader`` (the console's twin route): only the systems that reader may see (:func:`visible`), each by
    name, the rest counted in ``held_back``. Without one (the platform observation, recorded and replayed, which MCP
    and the observatory read): a row per SYNTHETIC system at or below the twin's marking, and for every customer
    system only TOTALS (``customers``: how many, published, unpublished, objectives other than satisfied, unverified,
    stale, awaiting a person, self-published, steps missing, chains broken) -- no id, ordinal, event, time or digest
    of any customer system; each total a BAND (:func:`band`), and nothing but "fewer than 5" below
    :data:`CUSTOMER_K` distinct customer ORGS (a tenant could otherwise subtract its own systems). Systems above the twin's marking are counted in ``held_back``.
    """
    ev: dict[str, Any] = {"systems": None, "errors": {}, "held_back": 0, "scope": "reader" if reader else "platform"}
    try:
        cfg = cfg or cmod.load()
        ch = ledger.chain(ledger.read())
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["ledger"] = f"the ledger could not be read ({type(exc).__name__})"
        return ev
    from tools.twin_core.governance import age_hours

    at = observed_at or rfc3339(datetime.now(timezone.utc))
    budget = min((float(c["max_age_h"]) for c in cfg["collectors"].values()), default=24)
    waiting_h = float(waiting_days) * 24
    items: list[dict] = []
    orgs: set[str] = set()
    cust = {"systems": 0, "published": 0, "unpublished": 0, "other_than_satisfied": 0, "unverified_objectives": 0,
            "stale": 0, "awaiting_publish": 0, "self_published": 0, "step_missing": 0, "step_recorded_after_void": 0,
            "chain_broken": 0}
    broken = {p["stream"] for p in ch["_problems"]}
    problems = []
    twin = mcp_reader("governance-twin")
    for s in sorted(cfg.get("systems") or [], key=lambda x: str(x.get("id"))):
        if not (visible(s, reader) if reader else _cleared(twin, s["marking"])):
            ev["held_back"] += 1
            continue
        stream = _stream(s["id"])
        st = replay_checked(ch.get(stream, []))
        if reader or s.get("synthetic") is True:
            items.append(_twin_row(s["id"], st, cfg, named=True))
            problems += [{"event_id": p["event_id"], "system": s["id"],
                          "problem": "a row does not verify on the system's hash chain"}
                         for p in ch["_problems"] if p["stream"] == stream]
            continue
        # A customer system, seen by no particular reader: totals only.
        row = _twin_row("", st, cfg, named=False)
        pub, prep, facts = row["published"], row["prepared"], row["facts"] or {}
        cust["systems"] += 1
        orgs.add(str(s.get("org") or "").strip().casefold())
        cust["published" if pub else "unpublished"] += 1
        cust["other_than_satisfied"] += int(facts.get("other_than_satisfied") or 0)
        cust["unverified_objectives"] += int(facts.get("unverified") or 0)
        age = age_hours(facts.get("produced_at"), at) if pub else None
        cust["stale"] += int(age is not None and age > budget)
        wait = age_hours(prep.get("created_at"), at) if prep and not prep["decided"] else None
        cust["awaiting_publish"] += int(wait is not None and wait > waiting_h)
        cust["self_published"] += int(bool(pub and pub["self_published"]))
        cust["step_missing"] += len(row["step_missing"])
        cust["step_recorded_after_void"] += len(row["step_recorded_after_void"]) + len(row["step_on_superseded"])
        cust["chain_broken"] += int(stream in broken)
    ev["systems"] = items
    ev["problems"] = problems
    if not reader:
        # Fewer than K customer systems: nothing but that (a tenant could subtract its own system from a total);
        # otherwise every total as a band, never an exact count (no single event shows as a delta).
        ev["customers"] = ({"systems": f"fewer than {CUSTOMER_K} customer orgs"} if len(orgs) < CUSTOMER_K
                           else {k: band(v) for k, v in cust.items()})
    return ev


#: Customer totals reach the platform observation only when the customer systems span at least this many orgs.
CUSTOMER_K = 5
_BANDS = ((0, "0"), (4, "1-4"), (9, "5-9"), (24, "10-24"), (99, "25-99"))


def band(n: int) -> str:
    """A count as a band: 0, 1-4, 5-9, 10-24, 25-99, 100+."""
    return next((label for top, label in _BANDS if n <= top), "100+")


def thread_record(event_id: str, reader: dict | None, cfg: dict | None = None) -> dict | None:
    """One ``ec_events`` row as a thread, in full (who, why, which system), for a reader who may see its system;
    None for a row that does not exist and one the reader may not see alike."""
    cfg = cfg or cmod.load()
    eid = str(event_id or "")
    if not re.fullmatch(r"ece-[0-9a-f]{16}", eid):
        return None
    conn = ledger.connection()
    try:
        rows = conn.execute(f"SELECT {', '.join(ledger.COLS)} FROM {ledger.TABLE} WHERE event_id = %s",  # nosec B608
                            (eid,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    r = dict(zip(ledger.COLS, tuple(rows[0])))
    s = cmod.system(cfg, str(r["system_id"]))
    if s is None or not visible(s, reader):
        return None
    return {k: r[k] for k in ledger.COLS if k != "payload_json"}


def twin_judge(ev: dict, policy: dict, observed_at: str, obs_thread: str = "") -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)``. Pure: a replay judges the same.

    ``obs_thread`` (``twin_obs:<observation>#evidence_collectors.customers``) is the thread of the customer TOTALS
    (they name no ledger row on purpose); it is required when the evidence carries them."""
    from tools.twin_core.governance import age_hours, check, finding

    errors = ev.get("errors") or {}
    systems = ev.get("systems")
    checks = [check("evidence_collectors.ledger", "Objective-evidence ledger (ec_events)", systems is not None,
                    errors.get("ledger") or "ledger not read")]
    findings: list[dict] = []
    waiting = float((policy or {}).get("waiting_warn_days") or 7)
    for p in ev.get("problems") or []:
        findings.append(finding("evidence_collectors.chain_broken", "critical", f"{p['system']}: {p['problem']}",
                                thread_id=f"evidence_collectors:{p['event_id']}",
                                recommendation="The evidence history cannot be trusted; the ledger is append-only, so "
                                               "find who wrote the row and treat it as an incident."))
    published = 0
    for r in systems or []:
        pub, prep, facts = r.get("published"), r.get("prepared"), r.get("facts") or {}
        # A system with no published report is unverified, never passed.
        checks.append(check(f"evidence_collectors.{r['system']}", f"A published objective-evidence report for "
                                                                  f"{r['system']}",
                            pub is not None, "no report of this system has been published: unverified"))
        if prep and not prep.get("decided"):
            age = age_hours(prep.get("created_at"), observed_at)
            if age is not None and age > waiting * 24:
                findings.append(finding("evidence_collectors.awaiting_publish", "low",
                                        f"{r['system']}: a prepared report has waited {age / 24:.0f} days for a person",
                                        thread_id=f"evidence_collectors:{prep['event_id']}",
                                        recommendation="A person of the system's org who did not prepare it "
                                                       "publishes it or refuses."))
        for eid in r.get("step_missing") or []:
            findings.append(finding("evidence_collectors.step_missing", "medium",
                                    f"{r['system']}: a decision was written to the ledger but its declared step is "
                                    "not recorded (or cannot be read): it publishes nothing and blocks the report",
                                    thread_id=f"evidence_collectors:{eid}",
                                    recommendation="If it does not land, an operator marks it lost (cli.py "
                                                   "step-lost) and a person decides the report again."))
        for eid in r.get("step_recorded_after_void") or []:
            findings.append(finding("evidence_collectors.step_recorded_after_void", "high",
                                    f"{r['system']}: a decision marked lost had its declared step recorded "
                                    "afterwards: the step stands in the decision log but decides nothing here",
                                    thread_id=f"evidence_collectors:{eid}",
                                    recommendation="Reconcile with the person who decided: the report's current "
                                                   "decision is the one on the ledger after the step_lost row."))
        for eid in r.get("step_on_superseded") or []:
            findings.append(finding("evidence_collectors.step_on_superseded", "high",
                                    f"{r['system']}: a declared step was recorded for a decision on a report that "
                                    "was no longer open: it decides nothing",
                                    thread_id=f"evidence_collectors:{eid}",
                                    recommendation="Reconcile with the person who decided, and decide the current "
                                                   "report."))
        if pub is None:
            continue
        published += 1
        thread = f"evidence_collectors:{pub['event_id']}"
        if pub.get("self_published"):
            findings.append(finding("evidence_collectors.self_published", "critical",
                                    f"{r['system']}: the report was published by the person who prepared it",
                                    thread_id=thread, recommendation="Treat it as unpublished; prepare and have "
                                                                     "another person publish a new one."))
        age = age_hours(facts.get("produced_at"), observed_at)
        if age is not None and age > float(facts.get("budget_h") or 24):
            findings.append(finding("evidence_collectors.stale", "medium",
                                    f"{r['system']}: the newest published report is {age:.0f}h old, past the "
                                    "shortest collector budget: its observations count for nothing now",
                                    thread_id=thread, recommendation="Collect, prepare and publish a current report."))
        for obj in facts.get("other_than_satisfied") or []:
            findings.append(finding("evidence_collectors.other_than_satisfied", "high",
                                    f"{r['system']}: {obj} observed other than satisfied",
                                    thread_id=thread, recommendation="Fix what the collector observed, or record "
                                                                     "a POA&M item in ICDEV[OS]."))
        if facts.get("unverified"):
            findings.append(finding("evidence_collectors.unverified_objectives", "low",
                                    f"{r['system']}: {facts['unverified']} mapped objectives could not be judged",
                                    thread_id=thread, recommendation="Restore the unreadable sources or credentials "
                                                                     "and collect again."))
    cust = ev.get("customers")
    summary = {"evidence_systems": len(systems or []), "evidence_published": published,
               "evidence_held_back": ev.get("held_back", 0)}
    if cust is not None and len(cust) == 1:
        summary["customer_systems"] = cust["systems"]          # too few to say anything more
    elif cust is not None:
        if not obs_thread:
            raise ValueError("customer totals need the observation's own thread")
        # Customer systems, seen by no particular reader: totals only, no system named or threaded.
        checks.append(check("evidence_collectors.customers", "A published objective-evidence report for every "
                                                             "customer system", cust["unpublished"] == "0",
                            f"{cust['unpublished']} of {cust['systems']} customer systems have no published report: "
                            "unverified"))
        for key, sev, what in (("chain_broken", "critical", "customer system(s) whose ledger does not verify"),
                               ("self_published", "critical", "customer report(s) published by their preparer"),
                               ("other_than_satisfied", "high", ("objective(s) observed other than satisfied across "
                                                                "customer systems")),
                               ("step_missing", "medium", ("customer decision(s) whose declared step was never "
                                                          "recorded (they decide nothing)")),
                               ("step_recorded_after_void", "high", ("customer step(s) recorded for a decision "
                                                                     "that was void or not open (it decides "
                                                                     "nothing)")),
                               ("stale", "medium", ("customer system(s) whose newest published report is past the "
                                                   "shortest collector budget")),
                               ("unverified_objectives", "low", ("mapped objective(s) left unverified across customer "
                                                                "systems")),
                               ("awaiting_publish", "low", "customer report(s) waiting for a person")):
            if cust.get(key, "0") != "0":
                findings.append(finding(f"evidence_collectors.customers.{key}", sev, f"{cust[key]} {what}",
                                        thread_id=obs_thread, recommendation="Each customer's own people see their "
                                                                             "systems on /evidence-collectors/."))
        summary.update({f"customer_{k}": v for k, v in cust.items()})
    return checks, findings, summary


def to_json(x: Any) -> str:
    return json.dumps(x, indent=2, sort_keys=True)
