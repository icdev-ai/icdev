# CUI // SP-CTI
"""Store publisher (icx-ent-04): prepare, publish, export, read, observe.

ICDEV[IT] packages; ICDEV[OS] reviews, certifies and installs. For a declared package (``args/store_publisher.yaml``)
the packager builds ONE signed ``icos-store-package/v1`` (``package.py``) -- its card signed by the PUBLISHER's key
(``card.py``: the OS keyring), ICDEV's seven scanner gates run on it (``gates.py``) -- and this module records and
moves it.

**Preparing is a person's act, and decides nothing** (:func:`prepare`). It makes the publisher's key sign, so an agent
or a service key never prepares (``agent_cannot_sign``), and only a person in ``packager_roles`` does. The package is
recorded whole at its content digest in the append-only, hash-chained ``sp_events`` (``ledger.py``).

**Publishing is another person's declared step** (:func:`publish`, ``icos-store-publish``): a person of the package's
org, cleared for its marking, who did not prepare it, publishes it or refuses, with a reason, bound to the digest they
read. Before the step the recorded package is checked again (its digest, its card under its own key, the binding) and
its key must still be the publisher's current key.

**Only a published package leaves** (:func:`export`): ``{"bundle": <icos-store-package/v1>, "publication": ...}``,
the publication BESIDE it, exactly what ICDEV[OS]'s ``icdev_os/store/review.py`` imports through the one shared import
(``icdev_os/publication.py``). The publication counts only while its declared step is recorded, that way, by that
person (``step_missing`` otherwise). Importing it into ICDEV[OS] is a third person's act there; nothing here imports,
and no agent publishes.

**Who sees what.** A package is shown, prepared, published or exported only for a reader of its org whose clearance
is at or above what its marking needs; anything else is "no such package", byte for byte, and a list counts what it
holds back without describing it. An agent through MCP is in no org: it sees SYNTHETIC packages only.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import re
from typing import Any

from tools.store_publisher import card as cmod
from tools.store_publisher import config as cfgmod
from tools.store_publisher import ledger
from tools.store_publisher import package as pkgmod
from tools.store_publisher.constants import (
    CLEARANCES,
    ICDEV_CLEARANCE_FOR,
    MCP_CLEARANCE,
    PUBLICATION_RELPATH,
    PUBLICATION_SCHEMA,
    PUBLICATION_SHA256,
    SURFACE,
    TEMPLATE,
)

Refused = ledger.Refused
_ORDER = {m: i for i, m in enumerate(CLEARANCES)}
NOT_FOUND = "no such package"
#: The e2e server's THROWAWAY publisher key (icx-ent-04): honoured ONLY while the reviewed config names no key, and ONLY
#: for a regular file under the repository's ``tests/e2e/fixtures/`` (a deployment installs no tests/, so it cannot swap
#: a key in through it, and it never replaces a key a site configured).
E2E_KEY_ENV = "ICDEV_STORE_PUBLISHER_E2E_KEY"


# ── readers ──────────────────────────────────────────────────────────────────


def current_reader() -> dict | None:
    """The signed-in principal as a reader: ``{id, role, clearance, org}``; None outside a request or signed out.
    The org and clearance are read FRESH from the person's own row (an API key's principal carries no tenant)."""
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
    need = ICDEV_CLEARANCE_FOR.get(marking)
    c = str((reader or {}).get("clearance") or "").strip().upper()
    return need is not None and c in _ORDER and _ORDER[c] >= _ORDER[need]


def visible(p: dict, reader: dict | None) -> bool:
    """Fail closed: a reader cleared for the package's marking, of its owning org. An agent is in no org, so it sees
    a SYNTHETIC package only."""
    if not reader or not _cleared(reader, p["marking"]):
        return False
    if reader.get("org") is None and str(reader.get("id", "")).startswith("agent:"):
        return p.get("synthetic") is True
    org = str(reader.get("org") or "").strip()
    return bool(org) and org == str(p["org"]).strip()


def _package(pid: str, reader: dict | None, cfg: dict) -> dict:
    p = cfgmod.package(cfg, str(pid or ""))
    if p is None or not visible(p, reader):
        raise Refused(404, "not_found", NOT_FOUND)     # one answer for "unknown" and "not yours"
    return p


def _person(actor: dict | None) -> dict:
    if not isinstance(actor, dict) or not actor.get("id"):
        raise Refused(401, "no_person", "a store package is a signed-in person's act")
    aid = str(actor["id"])
    if actor.get("role") == "service" or aid.startswith(("agent:", "bootstrap:")):
        raise Refused(403, "agent_cannot_sign", "an agent or a service key never makes the publisher's key sign or "
                                                "publishes: a person prepares a package, and another person publishes it")
    return actor


# ── the ledger, replayed ─────────────────────────────────────────────────────


def _stream(pid: str) -> str:
    return f"package:{pid}"


def replay(ordered: list[dict]) -> dict:
    """One package's ledger: the newest prepared package, whether a person decided it, the newest published one."""
    prepared = published = None
    history = []
    for r in ordered:
        b = ledger.payload(r)
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash", "prev_hash")}
        history.append(h)
        if r["event"] == "prepared":
            prepared = {**h, "decided": None, "record": b.get("record") or {}}
        elif prepared and not prepared["decided"] and (b.get("detail") or {}).get("prepared_event") == \
                prepared["event_id"]:
            prepared["decided"] = h
            if r["event"] == "published":
                published = {**h, "prepared_event": prepared["event_id"], "prepared_by": prepared["actor"],
                             "record": prepared["record"]}
    return {"prepared": prepared, "published": published, "history": history}


def _state(pid: str, conn) -> tuple[dict, list[dict], list[dict]]:
    ch = ledger.chain(ledger.read(conn, stream=_stream(pid)))
    ordered = ch.get(_stream(pid), [])
    return replay(ordered), ch["_problems"], ordered


def builders(state: dict) -> dict[str, str]:
    p = state.get("prepared") or {}
    return {str(p["actor"]): "prepared this package"} if p.get("actor") else {}


def _package_of(ordered: list[dict], event_id: str) -> dict:
    row = next((r for r in ordered if r["event_id"] == event_id), None)
    pkg = ledger.payload(row).get("package") if row else None
    if not isinstance(pkg, dict):
        raise Refused(409, "chain_broken", "the prepared package is not on its chain")
    return pkg


def step_ok(pub: dict) -> bool:
    """The publication's declared step is recorded, decided affirmatively, by the person the ledger names, over this
    package's digest and its prepared event: an effect checks what was signed, never only the ledger's word."""
    from tools.workflow_hitl import declared

    try:
        st = declared.step_state(str(pub.get("step_id") or ""))
        decided = (declared._events(str(pub.get("step_id") or "")) or {}).get("decided") or {} if st else {}
        signed = json.loads(decided.get("payload_json") or "{}") if decided else {}
    except Exception:  # noqa: BLE001 — unreadable: not recorded
        return False
    return bool(st) and st["status"] in ("decided", "spent") and st["affirmative"] is True \
        and str(st["decided_by"]) == str(pub["actor"]) and decided.get("template_id") == TEMPLATE \
        and signed.get("content_digest") == pub["content_digest"] \
        and signed.get("prepared_event") == pub.get("prepared_event")


# ── the key ──────────────────────────────────────────────────────────────────


def _e2e_signer(cfg: dict):
    import os
    from pathlib import Path

    raw = os.environ.get(E2E_KEY_ENV, "").strip()
    if not raw or (cfg.get("signing") or {}).get("key"):
        return None
    fixtures = (cfgmod.BASE_DIR / "tests" / "e2e" / "fixtures").resolve()
    p = Path(raw)
    if p.is_symlink() or not p.resolve().is_relative_to(fixtures) or not p.is_file():
        return None
    return cmod.PemSigner(p.read_bytes(), source="file:(e2e throwaway key under tests/e2e/fixtures)")


def signer_for(cfg: dict):
    """The publisher's card key from the reviewed config (no key configured: refused, ``no_key``)."""
    try:
        e2e = _e2e_signer(cfg)
        if e2e is not None:
            return e2e
        return cmod.open_signer((cfg.get("signing") or {}).get("key"))
    except cmod.CardError as exc:
        raise Refused(409 if exc.code == "no_key" else 503, exc.code, str(exc)) from exc


def _packager(actor: dict, cfg: dict) -> None:
    """Making the publisher's key sign is the sensitive act: only a person in ``packager_roles`` prepares."""
    if str(actor.get("role") or "") not in (cfg.get("packager_roles") or []):
        raise Refused(403, "role", "preparing a package makes the publisher's key sign: it is held by "
                                   + ", ".join(cfg.get("packager_roles") or []) + "; your role is "
                                   f"{actor.get('role') or 'none'}")


def _key_current(cfg: dict, record: dict, signer=None) -> None:
    """The package was signed by the publisher's CURRENT key: a key rotated or withdrawn since stops it leaving."""
    current = signer or signer_for(cfg)
    if cmod.kid(current.spki()) != record.get("kid"):
        raise Refused(409, "key_mismatch", "the package was signed by a key that is not the publisher's current key: "
                                           "prepare it again")


# ── prepare ──────────────────────────────────────────────────────────────────


def prepare(pid: str, actor: dict | None, reader: dict | None, *, version: str, note: str = "",
            cfg: dict | None = None, signer=None, now: dt.datetime | None = None, conn=None) -> dict:
    """Build and sign the package and record it at its content digest as PREPARED. Decides nothing."""
    actor = _person(actor)
    cfg = cfg or cfgmod.load()
    p = _package(pid, reader, cfg)
    _packager(actor, cfg)
    signer = signer or signer_for(cfg)
    own = conn is None
    conn = conn or ledger.connection()
    try:
        _, problems, ordered = _state(p["id"], conn)
        if problems:
            raise Refused(409, "chain_broken", "this package's ledger does not verify: nothing is added to it")
        try:
            out = pkgmod.build(p, cfg, signer=signer, version=version, now=now)
        except pkgmod.PackageRefused as exc:
            raise Refused(409, exc.code, exc.message) from exc
        body = out["package"]
        row = ledger.append(conn, stream=_stream(p["id"]), event="prepared", package_id=p["id"], org=p["org"],
                            actor=str(actor["id"]), actor_kind="person", actor_role=str(actor.get("role") or ""),
                            content_digest=body["content_digest"], reason=str(note or "")[:500],
                            prev_hash=ledger.head(ordered), body={"package": body, "record": out["record"]})
    finally:
        if own:
            conn.close()
    return {"package": p["id"], "event_id": row["event_id"], "digest": body["content_digest"],
            "prepared_by": row["actor"], "scan": body["scan"], "kid": out["record"]["kid"], "template": TEMPLATE,
            "next": "a person of the package's org who did not prepare it publishes it or refuses, on /store-publisher/"}


# ── publish ──────────────────────────────────────────────────────────────────


def publish(pid: str, *, decision: str, reason: str, read_digest: str, actor: dict | None, reader: dict | None,
            cfg: dict | None = None, conn=None, signer=None) -> dict:
    """A person who did not prepare it publishes the package's prepared version, or refuses it."""
    from tools.workflow_hitl import declared

    actor = _person(actor)
    cfg = cfg or cfgmod.load()
    p = _package(pid, reader, cfg)
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, ordered = _state(p["id"], conn)
        if problems:
            raise Refused(409, "chain_broken", "this package's ledger does not verify: nothing is decided on it")
        prep = st["prepared"]
        if prep is None:
            raise Refused(409, "nothing_prepared", "no version of this package is prepared")
        if prep["decided"]:
            raise Refused(409, "already_decided", f"the prepared package was already {prep['decided']['event']}: "
                                                  "prepare a new one")
        if str(read_digest or "").strip() != prep["content_digest"]:
            raise Refused(409, "digest", "the prepared package is not the one you read: reload it")
        dec = str(decision or "").strip().lower()
        if dec == "publish":
            body = _package_of(ordered, prep["event_id"])
            rec = prep.get("record") or {}
            try:
                spki = cmod.spki_from_jwk(rec.get("public_jwk") or {})
            except cmod.CardError:
                spki = None
            if spki is None or cmod.kid(spki) != rec.get("kid") or pkgmod.self_check(body, spki) \
                    or pkgmod.schema_errors(body):
                raise Refused(409, "signature", "the recorded package no longer verifies under its own key")
            _key_current(cfg, rec, signer)
        try:
            taken = declared.take_step(TEMPLATE, surface=SURFACE, decision=dec, subject=f"store_package:{p['id']}",
                                       reason=str(reason or ""), actor=actor, separated_from=builders(st),
                                       payload={"package": p["id"], "version": (prep.get("record") or {}).get("version"),
                                                "content_digest": prep["content_digest"],
                                                "prepared_event": prep["event_id"]})
        except declared.StepRefused as exc:
            raise Refused(exc.status, exc.code, exc.message) from exc
        event = "published" if taken["affirmative"] else "refused"
        try:
            row = ledger.append(conn, stream=_stream(p["id"]), event=event, package_id=p["id"], org=p["org"],
                                actor=taken["decided_by"], actor_kind="person",
                                actor_role=str(actor.get("role") or ""), step_id=taken["step_id"],
                                reason=str(reason or "")[:2000], content_digest=prep["content_digest"],
                                prev_hash=ledger.head(ordered),
                                body={"detail": {"prepared_event": prep["event_id"], "decision": dec}})
        except Refused as exc:
            # The declared step is recorded but the ledger was not written: nothing is published (export needs both);
            # the step is named so it is reconciled, and the package may be decided again.
            from tools.logging.icdev_logger import get_logger

            get_logger("icdev.store_publisher").warning(
                "declared step %s recorded but the sp_events row was not written (%s): nothing published",
                taken["step_id"], exc.code)
            raise Refused(exc.status, "not_recorded", f"your decision's step {taken['step_id']} was recorded but the "
                                                      "ledger could not be written, so nothing was published: decide "
                                                      "it again") from exc
    finally:
        if own:
            conn.close()
    return {"package": p["id"], "event": event, "event_id": row["event_id"], "digest": prep["content_digest"],
            "step_id": taken["step_id"], "decided_by": taken["decided_by"], "prepared_by": prep["actor"]}


# ── export ───────────────────────────────────────────────────────────────────


def publication_errors(pub: dict) -> list[str]:
    import jsonschema

    raw = (cfgmod.BASE_DIR / PUBLICATION_RELPATH).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PUBLICATION_SHA256:
        return [f"{PUBLICATION_RELPATH} is not the pinned ICDEV[OS] contract"]
    v = jsonschema.Draft202012Validator(json.loads(raw), format_checker=jsonschema.FormatChecker())
    return sorted(e.message[:200] for e in v.iter_errors(pub))


def _decided_at(created_at: str) -> str:
    try:
        t = dt.datetime.strptime(str(created_at), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return ""
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def export(pid: str, reader: dict | None, *, cfg: dict | None = None, conn=None) -> dict:
    """The newest PUBLISHED version of the package, as ICDEV[OS] imports it. Refused unless published, its step
    recorded."""
    cfg = cfg or cfgmod.load()
    p = _package(pid, reader, cfg)
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, ordered = _state(p["id"], conn)
    finally:
        if own:
            conn.close()
    if problems:
        raise Refused(409, "chain_broken", "this package's ledger does not verify: nothing leaves")
    pub = st["published"]
    if pub is None:
        raise Refused(404, "unpublished", "no version of this package has been published: a draft never leaves")
    if not step_ok(pub):
        raise Refused(409, "step_missing", "the publication's declared step is not recorded that way, by that person: "
                                           "it publishes nothing")
    body = _package_of(ordered, pub["prepared_event"])
    publication = {"schema": PUBLICATION_SCHEMA, "content_digest": pub["content_digest"], "signed_by": pub["actor"],
                   "role": pub["actor_role"] or None, "prepared_by": pub["prepared_by"], "step_id": pub["step_id"],
                   "template": TEMPLATE, "decided_at": _decided_at(pub["created_at"]), "event_id": pub["event_id"],
                   "row_hash": pub["row_hash"]}
    errs = publication_errors(publication)
    if errs:
        raise Refused(500, "contract", "the publication does not meet its contract: " + "; ".join(errs[:3]))
    return {"bundle": body, "publication": publication}


# ── reading ──────────────────────────────────────────────────────────────────


def _summary(p: dict, st: dict, problems: list[dict]) -> dict:
    prep, pub = st["prepared"], st["published"]
    return {"id": p["id"], "kind": p["kind"], "title": p["title"], "marking": p["marking"],
            "synthetic": p.get("synthetic") is True, "tools": list(p.get("tools") or []),
            "prepared": None if prep is None else {k: prep[k] for k in ("event_id", "actor", "content_digest",
                                                                        "created_at")}
            | {"decided": (prep["decided"] or {}).get("event"), "version": (prep.get("record") or {}).get("version")},
            "published": None if pub is None else {k: pub[k] for k in ("event_id", "actor", "content_digest",
                                                                        "created_at", "prepared_by", "step_id")}
            | {"version": (pub.get("record") or {}).get("version")},
            "chain_intact": not problems}


def packages_view(reader: dict | None, *, cfg: dict | None = None) -> dict:
    """Every package the reader may see with its ledger state; the others only counted."""
    cfg = cfg or cfgmod.load()
    ch = ledger.chain(ledger.read())
    out, held = [], 0
    for p in cfg.get("packages") or []:
        if not visible(p, reader):
            held += 1
            continue
        s = _stream(p["id"])
        out.append(_summary(p, replay(ch.get(s, [])), [x for x in ch["_problems"] if x["stream"] == s]))
    sg = cfg.get("signing") or {}
    return {"packages": out, "held_back": held, "template": TEMPLATE, "publisher": dict(cfg["publisher"]),
            "signing": {"configured": bool(sg.get("key")) or _e2e_signer(cfg) is not None, "kid": sg.get("kid")}}


def package_view(pid: str, reader: dict | None, *, cfg: dict | None = None) -> dict:
    """One package: its newest prepared version's card, gates, red-team summary, SBOM and history."""
    cfg = cfg or cfgmod.load()
    p = _package(pid, reader, cfg)
    conn = ledger.connection()
    try:
        st, problems, ordered = _state(p["id"], conn)
    finally:
        conn.close()
    view = _summary(p, st, problems)
    view["history"] = st["history"]
    view["builders"] = builders(st)
    prep = st["prepared"]
    if prep is not None and not problems:
        body = _package_of(ordered, prep["event_id"])
        rec = prep.get("record") or {}
        c = body.get("agent_card") or {}
        view["build"] = {"digest": prep["content_digest"], "version": body.get("version"), "kind": body.get("kind"),
                         "kid": rec.get("kid"), "public_jwk": rec.get("public_jwk"),
                         "card": {"name": c.get("name"), "version": c.get("version"),
                                  "skills": [s.get("id") for s in c.get("skills") or []],
                                  "binding": (c.get("metadata") or {}).get("icos.store/package_digest")},
                         "gates": rec.get("gates") or {}, "scan": body.get("scan") or {},
                         "redteam": rec.get("redteam") or {}, "sbom": rec.get("sbom") or {},
                         "ceilings": {k: body.get(k) for k in ("marking", "marking_ceiling", "egress_ceiling")},
                         "budget": body.get("budget"), "self_check": _self_check_now(body, rec)}
    return view


def _self_check_now(body: dict, rec: dict) -> str:
    """Re-run the package's self-check NOW, under the public key its record keeps: what the page says is what is true of
    the recorded bytes at this read."""
    try:
        spki = cmod.spki_from_jwk(rec.get("public_jwk") or {})
    except cmod.CardError:
        return "fails now: no recorded key"
    if cmod.kid(spki) != rec.get("kid"):
        return "fails now: the recorded key is not the card's"
    return "verified now" if not pkgmod.self_check(body, spki) else "fails now"


def thread_record(event_id: str, reader: dict | None, cfg: dict | None = None) -> dict | None:
    """One ``sp_events`` row as a thread, for a reader who may see its package; None for a row that does not exist and
    one the reader may not see alike."""
    cfg = cfg or cfgmod.load()
    eid = str(event_id or "")
    if not re.fullmatch(ledger.EVENT_ID, eid, re.ASCII):
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
    p = cfgmod.package(cfg, str(r["package_id"]))
    if p is None or not visible(p, reader):
        return None
    return {k: r[k] for k in ledger.COLS if k != "payload_json"}


# ── the twin ─────────────────────────────────────────────────────────────────


def twin_evidence(reader: dict | None = None, *, cfg: dict | None = None, check_steps: bool = True) -> dict:
    """Each package's ledger state as the twin judges it. With a ``reader``: that reader's packages by name. Without
    one (the platform observation MCP and the observatory read): SYNTHETIC packages only; every other package is only
    counted (``held_back``), with no id, time or digest."""
    ev: dict[str, Any] = {"packages": None, "errors": {}, "held_back": 0, "scope": "reader" if reader else "platform"}
    try:
        cfg = cfg or cfgmod.load()
        ch = ledger.chain(ledger.read())
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["ledger"] = f"the ledger could not be read ({type(exc).__name__})"
        return ev
    twin = mcp_reader("governance-twin")
    items = []
    for p in sorted(cfg.get("packages") or [], key=lambda x: x["id"]):
        if not (visible(p, reader) if reader else visible(p, twin)):
            ev["held_back"] += 1
            continue
        s = _stream(p["id"])
        st = replay(ch.get(s, []))
        prep, pub = st["prepared"], st["published"]
        row = {"package": p["id"], "kind": p["kind"],
               "problems": [x for x in ch["_problems"] if x["stream"] == s],
               "prepared": None if prep is None else {"event_id": prep["event_id"], "created_at": prep["created_at"],
                                                      "decided": bool(prep["decided"])},
               "published": None}
        if pub is not None:
            rec = pub.get("record") or {}
            row["published"] = {"event_id": pub["event_id"], "created_at": pub["created_at"],
                                "self_published": str(pub["actor"]).strip().casefold() ==
                                str(pub["prepared_by"]).strip().casefold(),
                                "step_ok": step_ok(pub) if check_steps else True,
                                "gates_not_passed": sorted(k for k, v in (rec.get("gates") or {}).items()
                                                           if (v or {}).get("status") not in ("pass", "not_applicable")),
                                "redteam_not_passed": sorted({*((rec.get("redteam") or {}).get("failed") or []),
                                                              *((rec.get("redteam") or {}).get("unverified") or [])})}
        items.append(row)
    ev["packages"] = items
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)``. Pure: a replay judges the same."""
    from tools.twin_core.governance import age_hours, check, finding

    packages = ev.get("packages")
    checks = [check("store_publisher.ledger", "Store publisher ledger (sp_events)", packages is not None,
                    (ev.get("errors") or {}).get("ledger") or "ledger not read")]
    findings: list[dict] = []
    waiting = float((policy or {}).get("waiting_warn_days") or 7)
    published = 0
    for r in packages or []:
        for pr in r.get("problems") or []:
            findings.append(finding("store_publisher.chain_broken", "critical", f"{r['package']}: {pr['problem']}",
                                    thread_id=f"store_publisher:{pr['event_id']}",
                                    recommendation="The package's history cannot be trusted; the ledger is append-only, "
                                                   "so treat the row as an incident."))
        pub, prep = r.get("published"), r.get("prepared")
        # A package with no published version is unverified, never passed.
        checks.append(check(f"store_publisher.{r['package']}", f"A published store package {r['package']}",
                            pub is not None and pub.get("step_ok") is True,
                            "no version of this package has been published (or its step is not recorded): unverified"))
        if prep and not prep["decided"]:
            age = age_hours(prep["created_at"], observed_at)
            if age is not None and age > waiting * 24:
                findings.append(finding("store_publisher.awaiting_publish", "low",
                                        f"{r['package']}: a prepared package has waited {age / 24:.0f} days for a person",
                                        thread_id=f"store_publisher:{prep['event_id']}",
                                        recommendation="A person of the package's org who did not prepare it publishes "
                                                       "it or refuses."))
        if pub is None:
            continue
        published += 1
        th = f"store_publisher:{pub['event_id']}"
        if pub["self_published"]:
            findings.append(finding("store_publisher.self_published", "critical",
                                    f"{r['package']}: published by the person who prepared it", thread_id=th,
                                    recommendation="ICDEV[OS] refuses it; find how the step was taken."))
        if not pub["step_ok"]:
            findings.append(finding("store_publisher.step_missing", "high",
                                    f"{r['package']}: the publication's declared step is not recorded: it exports "
                                    "nothing", thread_id=th, recommendation="A person decides the package again."))
        if pub.get("gates_not_passed"):
            findings.append(finding("store_publisher.gate_not_passed", "medium",
                                    f"{r['package']}: published with scanner gate(s) not passed: "
                                    f"{', '.join(pub['gates_not_passed'])}", thread_id=th,
                                    recommendation="ICDEV[OS] re-runs its gates and will block what does not pass: "
                                                   "fix the package and prepare a new version."))
        if pub.get("redteam_not_passed"):
            findings.append(finding("store_publisher.redteam_not_passed", "medium",
                                    f"{r['package']}: its red-team report has failed or unverified ids "
                                    f"({', '.join(pub['redteam_not_passed'][:6])})", thread_id=th,
                                    recommendation="ICDEV[OS]'s review blocks any failed or unverified id."))
    summary = {"packages": len(packages or []), "published": published, "held_back": ev.get("held_back", 0),
               "scope": ev.get("scope")}
    return checks, findings, summary


__all__ = ["Refused", "current_reader", "export", "mcp_reader", "package_view", "packages_view", "prepare", "publish",
           "replay", "step_ok", "thread_record", "twin_evidence", "twin_judge", "visible"]
