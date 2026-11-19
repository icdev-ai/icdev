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
marking (CUI) and is in no org: it sees a system's state only at or below that marking and prepares only drafts.
"""
from __future__ import annotations

import contextlib
import json
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
    """Fail closed: a reader, cleared for the system's marking, and of its owning org (an agent, in no org, reads
    only at or below the MCP marking and never publishes)."""
    if not reader or not _cleared(reader, system["marking"]):
        return False
    if reader.get("org") is None and str(reader.get("id", "")).startswith("agent:"):
        return True
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
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    resolve = resolve or resolve_ref
    out: list[dict] = []
    for name, spec in sorted((system.get("collectors") or {}).items()):
        mod = REGISTRY[name]
        budget = float(cfg["collectors"][name]["max_age_h"])
        try:
            out += mod.collect(system["id"], spec, now=now, max_age_h=budget, resolve=resolve)
        except Exception as exc:  # noqa: BLE001 — a collector fault is an unverified source, said
            from tools.evidence_collectors.collectors.base import unread

            out += unread(system=system["id"], collector=name, kind=mod.KIND, facts=mod.FACTS,
                          why=f"collector fault ({type(exc).__name__})", max_age_h=budget, source=name)
    return out


# ── the ledger ───────────────────────────────────────────────────────────────


def _stream(sid: str) -> str:
    return f"system:{sid}"


def replay(ordered: list[dict]) -> dict:
    """One system's ledger: the newest prepared report, whether a person decided it, the newest published."""
    prepared = published = None
    history = []
    for r in ordered:
        p = ledger.payload(r)
        det = p.get("detail") or {}
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash", "prev_hash")}
        history.append(h)
        if r["event"] == "prepared":
            prepared = {**h, "decided": None, "report": p.get("content"), "detail": det}
        elif prepared and det.get("prepared_event") == prepared["event_id"]:
            prepared["decided"] = h
            if r["event"] == "published":
                published = {**h, "prepared_event": prepared["event_id"], "prepared_by": prepared["actor"],
                             "report": prepared["report"], "prepared_detail": prepared["detail"]}
    return {"prepared": prepared, "published": published, "history": history}


def _state(sid: str, conn) -> tuple[dict, list[dict], list[dict]]:
    ordered, problems = ledger.stream_rows(_stream(sid), conn)
    return replay(ordered), problems, ordered


def builders(state: dict) -> dict[str, str]:
    """Who may not publish (``separate_from: builders``): whoever prepared the report."""
    who = str((state.get("prepared") or {}).get("actor") or "").strip()
    return {who: "prepared this report"} if who else {}


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
    obs = observations if observations is not None else collect(s, cfg, now=made, resolve=resolve)
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
                                    "observations_digest": bmod.observations_digest(obs)},
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
        if str(read_digest or "").strip() != p["content_digest"]:
            raise Refused(409, "digest", f"the prepared report is {p['content_digest'][:12]}, not what you read "
                                         "(reload it)")
        sdig = cmod.digest_of_system(cfg, s["id"])
        if (p.get("detail") or {}).get("system_digest") != sdig:
            raise Refused(409, "config_changed", "the system's endpoints, credential references or the mapping "
                                                 "changed since this report was prepared: prepare a new one")
        dec = str(decision or "").strip().lower()
        try:
            taken = declared.take_step(
                TEMPLATE, surface=SURFACE, decision=dec, subject=f"objective_evidence:{s['id']}",
                reason=str(reason or "").strip(), actor=actor, separated_from=builders(st),
                payload={"system": s["id"], "org": s["org"], "content_digest": p["content_digest"],
                         "prepared_event": p["event_id"], "system_digest": sdig})
        except declared.StepRefused as exc:
            raise Refused(exc.status, exc.code, exc.message) from exc
        event = "published" if taken["affirmative"] else "refused"
        row = ledger.append(conn, stream=_stream(s["id"]), event=event, system_id=s["id"],
                            actor=taken["decided_by"], actor_kind="person", actor_role=str(actor.get("role") or ""),
                            step_id=taken["step_id"], reason=str(reason or "").strip(),
                            content_digest=p["content_digest"],
                            detail={"prepared_event": p["event_id"], "decision": dec, "org": s["org"],
                                    "system_digest": sdig}, prev_hash=ledger.head(ordered))
    finally:
        if own:
            conn.close()
    out = {"system": s["id"], "event": event, "event_id": row["event_id"], "content_digest": p["content_digest"],
           "step": taken, "decided_by": taken["decided_by"], "prepared_by": p["actor"]}
    if event == "published":
        out["bundle"] = published_bundle(s["id"], reader, cfg=cfg)
    return out


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
        st = replay(ch.get(_stream(s["id"]), []))
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
            "next": ["publish"] if p and not p.get("decided") else ["prepare"], "template": TEMPLATE}


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


def twin_evidence(cfg: dict | None = None) -> dict:
    """Each system's ledger state and its newest published report's facts. A system above the twin's marking (CUI)
    is counted, never described."""
    ev: dict[str, Any] = {"systems": None, "errors": {}, "held_back": 0}
    try:
        cfg = cfg or cmod.load()
        ch = ledger.chain(ledger.read())
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["ledger"] = f"{type(exc).__name__}: {exc}"[:300]
        return ev
    items = []
    twin = mcp_reader("governance-twin")
    for s in cfg.get("systems") or []:
        if not _cleared(twin, s["marking"]):
            ev["held_back"] += 1
            continue
        st = replay(ch.get(_stream(s["id"]), []))
        pub, prep = st["published"], st["prepared"]
        facts = None
        if pub is not None:
            rep = pub.get("report") or {}
            facts = {"produced_at": rep.get("produced_at"),
                     "other_than_satisfied": sorted(e["objective"] for e in rep.get("entries") or []
                                                    if e.get("result") == "other_than_satisfied"),
                     "unverified": len((pub.get("prepared_detail") or {}).get("unverified") or []),
                     "budget_h": min((float(c["max_age_h"]) for c in cfg["collectors"].values()), default=24)}
        items.append({"system": s["id"],
                      "prepared": prep and {k: prep[k] for k in ("event_id", "actor", "created_at", "content_digest")}
                      | {"decided": bool(prep.get("decided"))},
                      "published": pub and {k: pub[k] for k in ("event_id", "actor", "prepared_by", "created_at",
                                                                "content_digest")},
                      "facts": facts})
    ev["systems"] = items
    visible_streams = {_stream(i["system"]) for i in items}
    ev["problems"] = [p for p in ch["_problems"] if p["stream"] in visible_streams]
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)``. Pure: a replay judges the same."""
    from tools.twin_core.governance import age_hours, check, finding

    errors = ev.get("errors") or {}
    systems = ev.get("systems")
    checks = [check("evidence_collectors.ledger", "Objective-evidence ledger (ec_events)", systems is not None,
                    errors.get("ledger") or "ledger not read")]
    findings: list[dict] = []
    waiting = float((policy or {}).get("waiting_warn_days") or 7)
    for p in ev.get("problems") or []:
        findings.append(finding("evidence_collectors.chain_broken", "critical", f"{p['stream']}: {p['problem']}",
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
        if pub is None:
            continue
        published += 1
        thread = f"evidence_collectors:{pub['event_id']}"
        if str(pub.get("actor") or "").casefold() == str(pub.get("prepared_by") or "").casefold():
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
                                    f"{r['system']}: {obj} was observed other than satisfied",
                                    thread_id=thread, recommendation="Fix what the collector observed, or record "
                                                                     "a POA&M item in ICDEV[OS]."))
        if facts.get("unverified"):
            findings.append(finding("evidence_collectors.unverified_objectives", "low",
                                    f"{r['system']}: {facts['unverified']} mapped objectives could not be judged",
                                    thread_id=thread, recommendation="Restore the unreadable sources or credentials "
                                                                     "and collect again."))
    summary = {"evidence_systems": len(systems or []), "evidence_published": published,
               "evidence_held_back": ev.get("held_back", 0)}
    return checks, findings, summary


def to_json(x: Any) -> str:
    return json.dumps(x, indent=2, sort_keys=True)
