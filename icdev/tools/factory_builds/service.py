# CUI // SP-CTI
"""Signed factory builds (icx-ent-03): prepare, publish, export, read, observe.

ICDEV[IT] builds; ICDEV[OS] admits. For a configured target (``args/factory_builds.yaml``) the factory builds ONE
``icos-artefact/v1`` -- CycloneDX 1.7 SBOM or AI-BOM, SLSA v1.2 provenance, OpenVEX VEX, Sigstore bundles over the
artefact, a model's weights signed with OpenSSF model-signing, OCI referrers (``build.py``) -- and signs it with the
FACTORY's key (``tools/crypto/artefact_signer.py``: KMS, the keyring, or keyless with a configured issuer).

**Preparing is a person's act, and decides nothing** (:func:`prepare`). It makes the factory's key sign, so an agent
or a service key never prepares (``agent_cannot_sign``): agents read through MCP and nothing more. The build is
recorded whole at its artefact digest in the append-only, hash-chained ``fb_events`` (``ledger.py``).

**Publishing is another person's declared step** (:func:`publish`, ``decl-wft-fb-build-publish``): a person of the
target's org, cleared for its marking, who did not prepare it, publishes it or refuses, with a reason, bound to the
digest they read. Before the step the recorded build is verified again under its own key, and a model build's
promotion must still stand in the fine-tuning registry (a demotion since is ``promotion_withdrawn``).

**Only a published build leaves** (:func:`export`): ``{"bundle": <icos-release-transfer/v1>, "publication": ...}``,
the publication BESIDE the artefact, exactly what ICDEV[OS]'s ``icdev_os/release/admission.py`` reads with the one
shared import (``icdev_os/publication.py``). The publication counts only when its declared step is recorded, that
way, by that person (``step_missing`` otherwise): an effect checks what was signed, never only the ledger's word.

**Who sees what.** A target is shown, built, published or exported only for a reader whose tenant is the target's
org and whose clearance is at or above its marking; anything else is "no such target", byte for byte, and a list
counts what it holds back without describing it. An agent through MCP is in no org: it sees SYNTHETIC targets only.
"""
from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from tools.factory_builds import build as bmod
from tools.factory_builds import config as cmod
from tools.factory_builds import ledger, sigstore
from tools.factory_builds.constants import (
    MARKINGS,
    MCP_CLEARANCE,
    PRODUCER,
    PUBLICATION_RELPATH,
    PUBLICATION_SCHEMA,
    PUBLICATION_SHA256,
    SURFACE,
    TEMPLATE,
)

Refused = ledger.Refused
_ORDER = {m: i for i, m in enumerate(MARKINGS)}
NOT_FOUND = "no such target"


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
    c = str((reader or {}).get("clearance") or "").strip().upper()
    return c in _ORDER and marking in _ORDER and _ORDER[c] >= _ORDER[marking]


def visible(target: dict, reader: dict | None) -> bool:
    """Fail closed: a reader, cleared for the target's marking, and of its owning org. An agent is in no org, so it
    sees a SYNTHETIC target only."""
    if not reader or not _cleared(reader, target["marking"]):
        return False
    if reader.get("org") is None and str(reader.get("id", "")).startswith("agent:"):
        return target.get("synthetic") is True
    return bool(str(reader.get("org") or "").strip()) and \
        str(reader.get("org") or "").strip() == str(target["org"]).strip()


def _target(tid: str, reader: dict | None, cfg: dict) -> dict:
    t = cmod.target(cfg, str(tid or ""))
    if t is None or not visible(t, reader):
        raise Refused(404, "not_found", NOT_FOUND)     # one answer for "unknown" and "not yours"
    return t


def _person(actor: dict | None) -> dict:
    if not isinstance(actor, dict) or not actor.get("id"):
        raise Refused(401, "no_person", "a build is a signed-in person's act")
    aid = str(actor["id"])
    if actor.get("role") == "service" or aid.startswith(("agent:", "bootstrap:")):
        raise Refused(403, "agent_cannot_sign", "an agent or a service key never makes the factory's key sign: a "
                                                "person prepares a build, and another person publishes it")
    return actor


# ── the ledger, replayed ─────────────────────────────────────────────────────


def _stream(tid: str) -> str:
    return f"target:{tid}"


def replay(ordered: list[dict]) -> dict:
    """One target's ledger: the newest prepared build, whether a person decided it, the newest published one."""
    prepared = published = None
    history = []
    for r in ordered:
        p = ledger.payload(r)
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash", "prev_hash")}
        history.append(h)
        if r["event"] == "prepared":
            prepared = {**h, "decided": None, "record": p.get("record") or {}}
        elif prepared and not prepared["decided"] and (p.get("detail") or {}).get("prepared_event") == \
                prepared["event_id"]:
            prepared["decided"] = h
            if r["event"] == "published":
                published = {**h, "prepared_event": prepared["event_id"], "prepared_by": prepared["actor"],
                             "record": prepared["record"]}
    return {"prepared": prepared, "published": published, "history": history}


def _state(tid: str, conn) -> tuple[dict, list[dict], list[dict]]:
    ch = ledger.chain(ledger.read(conn, stream=_stream(tid)))
    ordered = ch.get(_stream(tid), [])
    return replay(ordered), ch["_problems"], ordered


def builders(state: dict) -> dict[str, str]:
    p = state.get("prepared") or {}
    return {str(p["actor"]): "prepared this build"} if p.get("actor") else {}


def _files_of(ordered: list[dict], event_id: str) -> dict[str, bytes]:
    row = next((r for r in ordered if r["event_id"] == event_id), None)
    if row is None:
        raise Refused(409, "chain_broken", "the prepared build is not on the target's chain")
    return sigstore.from_transfer(ledger.payload(row).get("transfer"))


def step_ok(pub: dict) -> bool:
    """The publication's declared step is recorded, decided affirmatively, by the person the ledger names."""
    from tools.workflow_hitl import declared

    try:
        st = declared.step_state(str(pub.get("step_id") or ""))
        decided = (declared._events(str(pub.get("step_id") or "")) or {}).get("decided") or {} if st else {}
        signed = json.loads(decided.get("payload_json") or "{}") if decided else {}
    except Exception:  # noqa: BLE001 — unreadable: not recorded
        return False
    # The step must say what was signed: this template, this build's digest, its prepared event.
    return bool(st) and st["status"] in ("decided", "spent") and st["affirmative"] is True \
        and str(st["decided_by"]) == str(pub["actor"]) and decided.get("template_id") == TEMPLATE \
        and signed.get("content_digest") == pub["content_digest"] \
        and signed.get("prepared_event") == pub.get("prepared_event")


# ── signing ──────────────────────────────────────────────────────────────────


def signer_for(cfg: dict):
    """The factory's signer from the reviewed config (no key configured: refused, ``no_key``)."""
    from tools.crypto import artefact_signer as asig

    try:
        if (cfg.get("signing") or {}).get("mode") == "keyless":
            return asig.open_keyless(cfg.get("keyless") or {}, root=cmod.BASE_DIR,
                                     deadline_s=float(cfg["limits"]["deadline_seconds"]))
        return asig.open_signer((cfg.get("signing") or {}).get("key"))
    except asig.SigningError as exc:
        raise Refused(409 if exc.code == "no_key" else 503, exc.code, str(exc)) from exc


def _at(now: datetime | None) -> str:
    t = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── prepare ──────────────────────────────────────────────────────────────────


def prepare(tid: str, actor: dict | None, reader: dict | None, *, version: str, note: str = "",
            cfg: dict | None = None, signer=None, now: datetime | None = None, conn=None,
            commit: str | None = None, tlog: Callable | None = None) -> dict:
    """Build and sign the target, and record it at its artefact digest as PREPARED. Decides nothing."""
    actor = _person(actor)
    cfg = cfg or cmod.load()
    t = _target(tid, reader, cfg)
    signer = signer or signer_for(cfg)
    own = conn is None
    conn = conn or ledger.connection()
    try:
        _, problems, ordered = _state(t["id"], conn)
        if problems:
            raise Refused(409, "chain_broken", "this target's build ledger does not verify: nothing is added to it")
        try:
            out = bmod.build(t, cfg, signer=signer, version=version, at=_at(now), commit=commit, conn=conn, tlog=tlog)
        except bmod.BuildRefused as exc:
            raise Refused(409, exc.code, exc.message) from exc
        row = ledger.append(conn, stream=_stream(t["id"]), event="prepared", target_id=t["id"],
                            system_id=t["system_id"], actor=str(actor["id"]), actor_kind="person",
                            actor_role=str(actor.get("role") or ""), content_digest=out["digest"],
                            reason=str(note or "")[:500], prev_hash=ledger.head(ordered),
                            body={"transfer": sigstore.transfer(out["files"]), "record": out["record"]})
    finally:
        if own:
            conn.close()
    return {"target": t["id"], "event_id": row["event_id"], "digest": out["digest"], "prepared_by": row["actor"],
            "record": out["record"], "template": TEMPLATE,
            "next": "a person of the target's org who did not prepare it publishes it or refuses, on /factory-builds/"}


# ── publish ──────────────────────────────────────────────────────────────────


def publish(tid: str, *, decision: str, reason: str, read_digest: str, actor: dict | None, reader: dict | None,
            cfg: dict | None = None, conn=None) -> dict:
    """A person who did not prepare it publishes the target's prepared build to ICDEV[OS], or refuses it."""
    from tools.crypto import artefact_signer as asig
    from tools.factory_builds import aibom
    from tools.workflow_hitl import declared

    actor = _person(actor)
    cfg = cfg or cmod.load()
    t = _target(tid, reader, cfg)
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, ordered = _state(t["id"], conn)
        if problems:
            raise Refused(409, "chain_broken", "this target's build ledger does not verify: nothing is decided on it")
        p = st["prepared"]
        if p is None:
            raise Refused(409, "nothing_prepared", "no build of this target is prepared")
        if p["decided"]:
            raise Refused(409, "already_decided", f"the prepared build was already {p['decided']['event']}: prepare "
                                                  "a new one")
        if str(read_digest or "").strip() != p["content_digest"]:
            raise Refused(409, "digest", "the prepared build is not the one you read: reload it")
        dec = str(decision or "").strip().lower()
        if dec == "publish":
            files = _files_of(ordered, p["event_id"])
            m = json.loads(files["artefact.json"])
            spki = None
            with contextlib.suppress(asig.SigningError, KeyError, TypeError):
                spki = asig.spki_from_pem((p.get("record") or {})["signing"]["public_key"])
            if spki is None or asig.key_id(spki) != m["key"]["id"] or bmod.self_check(files, spki):
                raise Refused(409, "signature", "the recorded build no longer verifies under its own key")
            promo = (p.get("record") or {}).get("promotion")
            if promo and not aibom.still_promoted(promo["model_version_id"], promo["promotion_log_id"], conn):
                raise Refused(409, "promotion_withdrawn", "the model's promotion in the fine-tuning registry no longer "
                                                          "stands: a demoted model is not published")
        try:
            taken = declared.take_step(TEMPLATE, surface=SURFACE, decision=dec, subject=f"factory_build:{t['id']}",
                                       reason=str(reason or ""), actor=actor, separated_from=builders(st),
                                       payload={"target": t["id"], "system_id": t["system_id"],
                                                "content_digest": p["content_digest"], "prepared_event": p["event_id"]})
        except declared.StepRefused as exc:
            raise Refused(exc.status, exc.code, exc.message) from exc
        event = "published" if taken["affirmative"] else "refused"
        row = ledger.append(conn, stream=_stream(t["id"]), event=event, target_id=t["id"], system_id=t["system_id"],
                            actor=taken["decided_by"], actor_kind="person", actor_role=str(actor.get("role") or ""),
                            step_id=taken["step_id"], reason=str(reason or "")[:2000], content_digest=p["content_digest"],
                            prev_hash=ledger.head(ordered),
                            body={"detail": {"prepared_event": p["event_id"], "decision": dec}})
    finally:
        if own:
            conn.close()
    return {"target": t["id"], "event": event, "event_id": row["event_id"], "digest": p["content_digest"],
            "step_id": taken["step_id"], "decided_by": taken["decided_by"], "prepared_by": p["actor"]}


# ── export ───────────────────────────────────────────────────────────────────


def _publication_errors(pub: dict) -> list[str]:
    import hashlib

    import jsonschema

    raw = (cmod.BASE_DIR / PUBLICATION_RELPATH).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PUBLICATION_SHA256:
        return [f"{PUBLICATION_RELPATH} is not the pinned draft"]
    v = jsonschema.Draft202012Validator(json.loads(raw), format_checker=jsonschema.FormatChecker())
    return sorted(e.message[:200] for e in v.iter_errors(pub))


def export(tid: str, reader: dict | None, *, cfg: dict | None = None, conn=None) -> dict:
    """The newest PUBLISHED build of the target, as ICDEV[OS] imports it. Refused unless published, its step recorded."""
    cfg = cfg or cmod.load()
    t = _target(tid, reader, cfg)
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, ordered = _state(t["id"], conn)
    finally:
        if own:
            conn.close()
    if problems:
        raise Refused(409, "chain_broken", "this target's build ledger does not verify: nothing leaves")
    pub = st["published"]
    if pub is None:
        raise Refused(404, "unpublished", "no build of this target has been published: a draft never leaves")
    if not step_ok(pub):
        raise Refused(409, "step_missing", "the publication's declared step is not recorded that way, by that person: "
                                           "it publishes nothing")
    files = _files_of(ordered, pub["prepared_event"])
    import importlib.metadata as md

    try:
        version = md.version("icdev")
    except Exception:  # noqa: BLE001
        version = "unpackaged"
    from tools.twin_core.governance import parse_ts

    decided = parse_ts(pub["created_at"])
    publication = {"schema": PUBLICATION_SCHEMA, "content_digest": pub["content_digest"], "signed_by": pub["actor"],
                   "role": pub["actor_role"] or None, "prepared_by": pub["prepared_by"], "step_id": pub["step_id"],
                   "template": TEMPLATE, "decided_at": decided.strftime("%Y-%m-%dT%H:%M:%SZ") if decided else "",
                   "event_id": pub["event_id"], "row_hash": pub["row_hash"],
                   "producer": {"system": "icdev-it", "tool": PRODUCER, "version": version}}
    errs = _publication_errors(publication)
    if errs:
        raise Refused(500, "contract", "the publication does not meet its contract: " + "; ".join(errs[:3]))
    return {"bundle": sigstore.transfer(files), "publication": publication}


# ── reading ──────────────────────────────────────────────────────────────────


def _summary(t: dict, st: dict, problems: list[dict]) -> dict:
    p, pub = st["prepared"], st["published"]
    return {"id": t["id"], "kind": t["kind"], "name": t["name"], "system_id": t["system_id"],
            "marking": t["marking"], "synthetic": t.get("synthetic") is True,
            "prepared": None if p is None else {k: p[k] for k in ("event_id", "actor", "content_digest", "created_at")}
            | {"decided": (p["decided"] or {}).get("event")},
            "published": None if pub is None else {k: pub[k] for k in ("event_id", "actor", "content_digest",
                                                                        "created_at", "prepared_by", "step_id")},
            "chain_intact": not problems}


def targets_view(reader: dict | None, *, cfg: dict | None = None) -> dict:
    """Every target the reader may see with its ledger state; the others only counted."""
    cfg = cfg or cmod.load()
    ch = ledger.chain(ledger.read())
    out, held = [], 0
    for t in cfg.get("targets") or []:
        if not visible(t, reader):
            held += 1
            continue
        s = _stream(t["id"])
        out.append(_summary(t, replay(ch.get(s, [])), [x for x in ch["_problems"] if x["stream"] == s]))
    sg = cfg.get("signing") or {}
    return {"targets": out, "held_back": held, "template": TEMPLATE,
            "signing": {"mode": sg.get("mode"), "configured": bool(sg.get("key")) or sg.get("mode") == "keyless"},
            "transparency_log": {"mode": (cfg.get("transparency_log") or {}).get("mode")},
            "builder_id": cfg["builder_id"]}


def target_view(tid: str, reader: dict | None, *, cfg: dict | None = None) -> dict:
    """One target: its newest prepared build's record, manifest, SBOM summary, VEX and history."""
    cfg = cfg or cmod.load()
    t = _target(tid, reader, cfg)
    conn = ledger.connection()
    try:
        st, problems, ordered = _state(t["id"], conn)
    finally:
        conn.close()
    view = _summary(t, st, problems)
    view["history"] = st["history"]
    view["builders"] = builders(st)
    p = st["prepared"]
    if p is not None and not problems:
        files = _files_of(ordered, p["event_id"])
        m = json.loads(files["artefact.json"])
        sb = json.loads(files["sbom.cdx.json"])
        vex = json.loads(files["vex.openvex.json"])
        view["build"] = {"digest": p["content_digest"], "record": p["record"], "subject": m["subject"],
                         "key_id": m["key"]["id"], "files": [{k: f[k] for k in ("path", "role", "sha256", "size")}
                                                             for f in m["files"]],
                         "sbom": {"components": len(sb.get("components") or []),
                                  "root": {k: (sb.get("metadata") or {}).get("component", {}).get(k)
                                           for k in ("type", "name", "version")},
                                  "vulnerabilities": [{"id": v["id"], "severity": (v.get("ratings") or [{}])[0]
                                                       .get("severity")} for v in sb.get("vulnerabilities") or []],
                                  "aibom": any(c.get("type") == "machine-learning-model"
                                               for c in [(sb.get("metadata") or {}).get("component") or {},
                                                         *(sb.get("components") or [])])},
                         "vex": [{"vulnerability": s["vulnerability"]["name"], "status": s["status"],
                                  "justification": s.get("justification")} for s in vex["statements"]],
                         "self_check": "verified" if not problems else "not verified"}
    return view


def thread_record(event_id: str, reader: dict | None, cfg: dict | None = None) -> dict | None:
    """One ``fb_events`` row as a thread, for a reader who may see its target; None for a row that does not exist and
    one the reader may not see alike."""
    cfg = cfg or cmod.load()
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
    t = cmod.target(cfg, str(r["target_id"]))
    if t is None or not visible(t, reader):
        return None
    return {k: r[k] for k in ledger.COLS if k != "payload_json"}


# ── the twin ─────────────────────────────────────────────────────────────────


def twin_evidence(reader: dict | None = None, *, cfg: dict | None = None, check_steps: bool = True) -> dict:
    """Each target's ledger state as the twin judges it. With a ``reader``: that reader's targets by name. Without one
    (the platform observation MCP and the observatory read): SYNTHETIC targets at or below the twin's marking only;
    every other target is only counted (``held_back``), with no id, time or digest."""
    ev: dict[str, Any] = {"targets": None, "errors": {}, "held_back": 0, "scope": "reader" if reader else "platform"}
    try:
        cfg = cfg or cmod.load()
        ch = ledger.chain(ledger.read())
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["ledger"] = f"the ledger could not be read ({type(exc).__name__})"
        return ev
    from tools.factory_builds import aibom

    twin = mcp_reader("governance-twin")
    items = []
    for t in sorted(cfg.get("targets") or [], key=lambda x: x["id"]):
        if not (visible(t, reader) if reader else (t.get("synthetic") is True and _cleared(twin, t["marking"]))):
            ev["held_back"] += 1
            continue
        s = _stream(t["id"])
        st = replay(ch.get(s, []))
        p, pub = st["prepared"], st["published"]
        row = {"target": t["id"], "kind": t["kind"],
               "problems": [x for x in ch["_problems"] if x["stream"] == s],
               "prepared": None if p is None else {"event_id": p["event_id"], "created_at": p["created_at"],
                                                    "decided": bool(p["decided"])},
               "published": None, "tlog": None, "signing_mode": None}
        if pub is not None:
            rec = pub.get("record") or {}
            promo = rec.get("promotion")
            row["published"] = {"event_id": pub["event_id"], "created_at": pub["created_at"],
                                "self_published": str(pub["actor"]).strip().casefold() ==
                                str(pub["prepared_by"]).strip().casefold(),
                                "step_ok": step_ok(pub) if check_steps else True,
                                "promotion_stands": None if not promo else
                                aibom.still_promoted(promo["model_version_id"], promo["promotion_log_id"])}
            row["tlog"] = (rec.get("transparency_log") or {}).get("mode")
            row["signing_mode"] = (rec.get("signing") or {}).get("mode")
        items.append(row)
    ev["targets"] = items
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)``. Pure: a replay judges the same."""
    from tools.twin_core.governance import age_hours, check, finding

    targets = ev.get("targets")
    checks = [check("factory_builds.ledger", "Signed factory build ledger (fb_events)", targets is not None,
                    (ev.get("errors") or {}).get("ledger") or "ledger not read")]
    findings: list[dict] = []
    waiting = float((policy or {}).get("waiting_warn_days") or 7)
    published = 0
    for r in targets or []:
        for p in r.get("problems") or []:
            findings.append(finding("factory_builds.chain_broken", "critical", f"{r['target']}: {p['problem']}",
                                    thread_id=f"factory_builds:{p['event_id']}",
                                    recommendation="The build history cannot be trusted; the ledger is append-only, so "
                                                   "treat the row as an incident."))
        pub, prep = r.get("published"), r.get("prepared")
        # A target with no published build is unverified, never passed.
        checks.append(check(f"factory_builds.{r['target']}", f"A published signed build of {r['target']}",
                            pub is not None and pub.get("step_ok") is True,
                            "no build of this target has been published (or its step is not recorded): unverified"))
        if prep and not prep["decided"]:
            age = age_hours(prep["created_at"], observed_at)
            if age is not None and age > waiting * 24:
                findings.append(finding("factory_builds.awaiting_publish", "low",
                                        f"{r['target']}: a signed build has waited {age / 24:.0f} days for a person",
                                        thread_id=f"factory_builds:{prep['event_id']}",
                                        recommendation="A person of the target's org who did not prepare it publishes "
                                                       "it or refuses."))
        if pub is None:
            continue
        published += 1
        th = f"factory_builds:{pub['event_id']}"
        if pub["self_published"]:
            findings.append(finding("factory_builds.self_published", "critical",
                                    f"{r['target']}: the build was published by the person who prepared it",
                                    thread_id=th, recommendation="Refuse it at ICDEV[OS]; find how the step was taken."))
        if not pub["step_ok"]:
            findings.append(finding("factory_builds.step_missing", "high",
                                    f"{r['target']}: the publication's declared step is not recorded: it exports "
                                    "nothing", thread_id=th, recommendation="A person decides the build again."))
        if pub.get("promotion_stands") is False:
            findings.append(finding("factory_builds.promotion_withdrawn", "high",
                                    f"{r['target']}: the published model was demoted in the fine-tuning registry since",
                                    thread_id=th, recommendation="Withdraw the release at ICDEV[OS]; build the model "
                                                                 "that is promoted now."))
        if r.get("signing_mode") == "keyless":
            findings.append(finding("factory_builds.keyless_not_admitted", "medium",
                                    f"{r['target']}: a keyless build, which ICDEV[OS]'s register does not admit yet",
                                    thread_id=th, recommendation="Sign with a KMS or keyring key the system's owner "
                                                                 "pinned, until ICDEV[OS] pins certificate identities."))
    summary = {"targets": len(targets or []), "published": published, "held_back": ev.get("held_back", 0),
               "scope": ev.get("scope")}
    return checks, findings, summary


__all__ = ["Refused", "current_reader", "export", "mcp_reader", "prepare", "publish", "replay", "step_ok",
           "target_view", "targets_view", "thread_record", "twin_evidence", "twin_judge", "visible"]
