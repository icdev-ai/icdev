# CUI // SP-CTI
"""The ``icos-findings/v1`` bundle: prepared by one person, published by another (icx-ent-01).

One bundle per target, for ICDEV[OS]'s vulnerability twin (osx-ent-07). It follows the prepared-by-one /
published-by-another pattern of the conformance and Academy bundles exactly (``tools/idp/conformance.py``,
``tools/idp/cop_tracks.py``), so ICDEV[OS] imports it with its one shared import helper:

* :func:`build` -- the target at its tree digest and the person's authorisation, the newest run and how it was
  isolated, every confirmed finding with its sealed PoV's digest and replay outcomes (never the PoV), its patch
  proposal and review, the dropped candidates counted by reason, the UNPROVEN dependency advisories with their KEV
  flag, and the newest benchmark measurement. ``content_digest`` is SHA-256 over the canonical JSON without
  ``content_digest`` and ``produced_at``. Validated against the DRAFT schema (``context/icos/findings.schema.json``,
  pinned by SHA-256 here) before it is returned;
* :func:`prepare` -- anyone, or an agent, records it at its digest as ``prepared`` on ``bundle:<target>``; it
  decides nothing;
* :func:`publish` -- a person who did not prepare it publishes or refuses, with a reason, bound to that digest
  (``decl-wft-vr-findings-publish``);
* :func:`published` -- the newest published bundle EXACTLY as prepared, and its publication BESIDE it
  (``icdev-findings-publication/v1``, the Academy publication's shape, ``context/icos/findings-publication.schema.json``);
* :func:`verify` -- both documents against their pinned schemas, the digest, the separation, and (given the
  ledger) the published row on an intact chain over that very bundle.

"Signed" here means what it means for every ICDEV bundle today: a PERSON's declared step bound to the content
digest, on a hash chain. A machine signature over the bundle (KMS or keyless) is the shared signing module's
(osx-ent-05 / icx-ent-03), not this card's.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from tools.idp.dora import _utc
from tools.vulnresearch import benchmark, discovery, ledger, patches, targets
from tools.vulnresearch.constants import (
    BUNDLE_SCHEMA,
    BUNDLE_SCHEMA_RELPATH,
    CLASSIFICATION,
    CONFIG_RELPATH,
    GENESIS,
    PRODUCER_SYSTEM,
    PRODUCER_TOOL,
    PUBLICATION_RELPATH,
    PUBLICATION_SCHEMA,
    SURFACE_PUBLISH,
    TEMPLATE_PUBLISH,
)

#: The DRAFT schemas, pinned: a changed file is refused, never silently used.
SCHEMA_SHA256 = "ef02a8ffa6cbeb8655cca734f4da36236cc83277e47b478ff60909b1d19302dc"
PUBLICATION_SHA256 = "58db1ff2e08c49f7629173522617ddbadb9100e12f157698df1d3c288695ef44"

BundleRefused = ledger.LedgerRefused
BUNDLE_FILE = "icos-findings.json"
PUBLICATION_FILE = "icos-findings.publication.json"


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("icdev")
    except Exception:  # noqa: BLE001 — an unpackaged checkout still says what it is
        return "unpackaged"


def _pinned(rel: str, sha: str) -> dict:
    raw = (targets.BASE_DIR / rel).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != sha:
        raise BundleRefused(500, "schema", f"{rel} is not the pinned draft (sha256 {got}, pinned {sha})")
    return json.loads(raw)


def schema() -> dict:
    return _pinned(BUNDLE_SCHEMA_RELPATH, SCHEMA_SHA256)


def publication_schema() -> dict:
    return _pinned(PUBLICATION_RELPATH, PUBLICATION_SHA256)


def _errors(doc: Any, sch: dict) -> list[str]:
    import jsonschema

    v = jsonschema.Draft202012Validator(sch, format_checker=jsonschema.FormatChecker())
    return sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in v.iter_errors(doc))


def content_digest(bundle: dict) -> str:
    return ledger.digest({k: v for k, v in bundle.items() if k not in ("content_digest", "produced_at")})


def validate(bundle: dict) -> list[str]:
    errors = _errors(bundle, schema())
    if not errors and bundle.get("content_digest") != content_digest(bundle):
        errors.append("content_digest is not the digest of the bundle's content")
    return errors


def _patch(st: Optional[dict]) -> Optional[dict]:
    p = (st or {}).get("proposed")
    if not p:
        return None
    prop = p["proposal"]
    d = p.get("decided")
    review = None
    if d:
        review = {"decision": "accept" if d["event"] == "patch_accepted" else "reject", "decided_by": d["actor"],
                  "step_id": d["step_id"], "decided_at": d["created_at"]}
    v = prop.get("validation") or {}
    return {"event_id": p["event_id"], "content_digest": p["content_digest"], "diff_digest": prop.get("diff_digest"),
            "proposed_by": p["actor"], "provenance": prop.get("provenance") or "person",
            "validation": {"pov_fails_after": bool(v.get("pov_fails_after")), "tests_pass": bool(v.get("tests_pass"))},
            "review": review, "applied": False}


def build(target_id: str, *, at: Optional[str] = None, conn=None, check: bool = True) -> dict:
    """The target's bundle from its newest run, unpublished. Deterministic given the ledger (``at`` is outside
    the digest). Refused when the target has no authorisation or no run."""
    st = targets.state(target_id, conn)
    if not st["decision"] or st["decision"]["event"] != "authorised":
        raise BundleRefused(409, "unauthorised", f"{target_id} was never authorised: there is nothing to report")
    rs = discovery.runs(target_id, conn)
    if not rs:
        raise BundleRefused(409, "no_run", f"{target_id} has no discovery run yet")
    newest = rs[0]
    run = newest["run"]
    reg = st["registered"]["registration"]
    pstates = patches.patches_for(target_id, conn)
    findings = []
    for f in run["findings"]:
        findings.append({"finding_id": f["finding_id"], "module": f["module"], "function": f["function"],
                         "file": f["file"], "line": int(f.get("line") or 0), "cwe": f["cwe"],
                         "weakness": f.get("weakness"), "severity": f.get("severity") or "high",
                         "sources": f["sources"], "status": "confirmed",
                         "pov": {k: f["pov"][k] for k in ("digest", "kind", "oracle", "recipe", "sealed")} | {
                             "replays": f["pov"]["replays"]},
                         "patch": _patch(pstates.get(f["finding_id"]))})
    m = benchmark.latest(conn)
    measurement = None
    if m:
        measurement = {"event_id": m["event_id"], "run_id": m["run_id"], "model_used": bool(m.get("model_used")),
                       "status": m.get("status") or "unverified", "why": m.get("why") or "",
                       "labels": {k: m["labels"].get(k) for k in ("path", "sha256", "synthetic", "labelled_by",
                                                                  "reviewed_by")},
                       "scores": {k: m["scores"][k] for k in ("tp", "fp", "fn", "tn", "false_positive_rate",
                                                             "false_negative_rate", "judged_by")}}
    iso = run.get("isolation") or {}
    cfg = targets.load_config()
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "producer": {"system": PRODUCER_SYSTEM, "tool": PRODUCER_TOOL, "version": _version()},
        "produced_at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "classification": CLASSIFICATION,
        "target": {"target_id": target_id, "name": reg["name"], "system_id": reg.get("system_id"),
                   "tree_digest": reg["tree_digest"],
                   "commit": reg.get("commit"), "files": int(reg.get("files") or 0),
                   "authorisation": {"event_id": st["decision"]["event_id"], "step_id": st["decision"]["step_id"],
                                     "decided_by": st["decision"]["actor"], "registered_by": st["registered"]["actor"],
                                     "expires_on": st["decision"]["expires_on"]}},
        "run": {"run_id": run["run_id"], "event_id": newest["event_id"], "content_digest": newest["content_digest"],
                "recorded_at": newest["created_at"],
                "isolation": {k: iso.get(k) for k in ("engine", "oci_runtime", "isolation", "microvm", "network",
                                                      "waiver")},
                "sast": {"tool": run["sast"].get("tool"), "ran": bool(run["sast"].get("ran"))},
                "sca": {"tool": run["sca"].get("tool"), "ran": bool(run["sca"].get("ran"))},
                "review": {"ran": bool(run["review"].get("ran")), "provenance": run["review"].get("provenance")},
                "fuzz": [{"module": z["module"], "function": z["function"], "engine": z.get("engine"),
                          "crashed": bool(z.get("crashed")), "iterations": z.get("iterations"),
                          "status": z.get("status")} for z in run["fuzz"]]},
        "findings": findings,
        "dropped": {"total": run["counts"]["dropped"], "by_reason": run["counts"]["dropped_by_reason"],
                    "by_source": run["counts"]["by_source"]},
        "advisories": run["advisories"],
        "measurement": measurement,
        "config": {"path": CONFIG_RELPATH, "sha256": cfg["_sha256"]},
    }
    bundle["content_digest"] = content_digest(bundle)
    if check:
        errors = validate(bundle)
        if errors:
            raise BundleRefused(500, "contract", "the bundle does not meet the icos-findings/v1 draft: "
                                + "; ".join(errors[:10]))
    return bundle


# ── the bundle ledger: prepared, then a person publishes ────────────────────


def _stream(tid: str) -> str:
    return f"bundle:{tid}"


def replay(ordered: list[dict]) -> dict:
    prepared = published = None
    history = []
    for r in ordered:
        det = ledger.payload(r).get("detail") or {}
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


def _state(tid: str, conn) -> tuple[dict, list[dict], list[dict]]:
    ordered, problems = ledger.stream_rows(_stream(tid), conn)
    return replay(ordered), problems, ordered


def builders(st: dict) -> dict[str, str]:
    p = st.get("prepared") or {}
    who = str(p.get("actor") or "")
    return {who: "prepared this bundle"} if who else {}


def prepare(target_id: str, actor: Optional[dict], *, note: str = "", conn=None) -> dict:
    """Build the target's bundle and record it, at its digest, as PREPARED. Decides nothing."""
    if not isinstance(actor, dict) or not actor.get("id"):
        raise BundleRefused(401, "no_actor", "preparing a bundle needs a signed-in person or a named agent")
    own = conn is None
    conn = conn or ledger.connection()
    try:
        b = build(target_id, conn=conn)
        _, problems, ordered = _state(target_id, conn)
        if problems:
            raise BundleRefused(409, "chain_broken", f"{target_id}'s bundle ledger does not verify")
        row = ledger.append(conn, stream=_stream(target_id), event="prepared", subject=target_id,
                            actor=str(actor["id"]), actor_kind=ledger.actor_kind(actor),
                            actor_role=str(actor.get("role") or ""), content=b, reason=note,
                            detail={"findings": len(b["findings"]), "run_id": b["run"]["run_id"]},
                            prev_hash=ledger.head(ordered) if ordered else GENESIS)
    finally:
        if own:
            conn.close()
    return {"target_id": target_id, "event_id": row["event_id"], "content_digest": b["content_digest"],
            "prepared_by": row["actor"], "actor_kind": row["actor_kind"], "findings": len(b["findings"]),
            "template": TEMPLATE_PUBLISH,
            "next": "a person who did not prepare it publishes it or refuses, on /vulnresearch/"}


def publish(target_id: str, *, decision: str, reason: str, read_digest: str, actor: Optional[dict], conn=None) -> dict:
    """A person who did not prepare it publishes the target's prepared bundle to ICDEV[OS], or refuses it."""
    from tools.workflow_hitl import declared

    if not isinstance(actor, dict):
        raise BundleRefused(401, "no_person", "publishing is a signed-in person's declared step on /vulnresearch/")
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, ordered = _state(target_id, conn)
        if problems:
            raise BundleRefused(409, "chain_broken", f"{target_id}'s bundle ledger does not verify")
        p = st["prepared"]
        if p is None:
            raise BundleRefused(409, "nothing_prepared", f"no bundle of {target_id} is prepared: prepare one first")
        if p.get("decided"):
            raise BundleRefused(409, "already_decided", f"the prepared bundle was already {p['decided']['event']}")
        if str(read_digest or "").strip() != p["content_digest"]:
            raise BundleRefused(409, "digest", "the prepared bundle is not what you read (reload it)")
        dec = str(decision or "").strip().lower()
        try:
            taken = declared.take_step(TEMPLATE_PUBLISH, surface=SURFACE_PUBLISH, decision=dec,
                                       subject=f"vr_findings:{target_id}", reason=str(reason or "").strip(),
                                       actor=actor, separated_from=builders(st),
                                       payload={"target_id": target_id, "content_digest": p["content_digest"],
                                                "prepared_event": p["event_id"]})
        except declared.StepRefused as exc:
            raise BundleRefused(exc.status, exc.code, exc.message) from exc
        event = "published" if taken["affirmative"] else "bundle_refused"
        row = ledger.append(conn, stream=_stream(target_id), event=event, subject=target_id,
                            actor=taken["decided_by"], actor_kind="person", actor_role=str(actor.get("role") or ""),
                            step_id=taken["step_id"], reason=str(reason or "").strip(),
                            detail={"prepared_event": p["event_id"], "decision": dec, "content_digest": p["content_digest"]},
                            prev_hash=ledger.head(ordered))
    finally:
        if own:
            conn.close()
    out = {"target_id": target_id, "event": event, "event_id": row["event_id"], "content_digest": p["content_digest"],
           "step": taken, "decided_by": taken["decided_by"], "prepared_by": p["actor"]}
    if event == "published":
        out["published"] = published(target_id)
    return out


def published(target_id: str, conn=None) -> Optional[dict]:
    """The newest PUBLISHED bundle, exactly as prepared (what ICDEV[OS] imports), and its publication beside it."""
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, ordered = _state(target_id, conn)
    finally:
        if own:
            conn.close()
    pub = st["published"]
    if pub is None or problems:
        return None
    prep = next((r for r in ordered if r["event_id"] == pub["prepared_event"]), None)
    if prep is None:
        return None
    b = ledger.payload(prep).get("content")
    decided = _utc(pub["created_at"])
    publication = {"schema": PUBLICATION_SCHEMA, "content_digest": prep["content_digest"], "signed_by": pub["actor"],
                   "role": pub["actor_role"] or None, "prepared_by": pub["prepared_by"], "step_id": pub["step_id"],
                   "template": TEMPLATE_PUBLISH,
                   "decided_at": decided.strftime("%Y-%m-%dT%H:%M:%SZ") if decided else pub["created_at"],
                   "event_id": pub["event_id"], "row_hash": pub["row_hash"],
                   "producer": {"system": PRODUCER_SYSTEM, "tool": PRODUCER_TOOL, "version": _version()}}
    return {"bundle": b, "publication": publication}


def verify(bundle: Any, publication: Any, rows: Optional[list[dict]] = None) -> dict:
    """Does an exported bundle and its publication hold? Both against their pinned schemas, the digest, the
    separation, and (given the ledger) its published row on an intact chain over that very bundle."""
    errors = [f"bundle: {e}" for e in validate(bundle)] if isinstance(bundle, dict) else ["bundle: not a JSON object"]
    if isinstance(publication, dict):
        errors += [f"publication: {e}" for e in _errors(publication, publication_schema())]
        if isinstance(bundle, dict) and publication.get("content_digest") != bundle.get("content_digest"):
            errors.append("the publication was taken at another digest")
        if publication.get("signed_by") and publication.get("signed_by") == publication.get("prepared_by"):
            errors.append("the bundle was published by the person who prepared it")
    else:
        errors.append("publication: missing (an unpublished bundle never leaves)")
    led = None
    if rows is not None and isinstance(publication, dict) and isinstance(bundle, dict):
        tid = (bundle.get("target") or {}).get("target_id") or ""
        ch = ledger.chain(rows)
        ordered = ch.get(_stream(tid), [])
        probs = [p for p in ch["_problems"] if p["stream"] == _stream(tid)]
        row = next((r for r in ordered if r["event_id"] == publication.get("event_id")), None)
        st = replay(ordered)
        prep = next((r for r in ordered if row and r["event_id"] == (ledger.payload(row).get("detail") or {})
                     .get("prepared_event")), None)
        led = {"chain_intact": not probs, "published_row": bool(row and row["event"] == "published")}
        if probs:
            errors.append(f"ledger: the bundle chain does not verify ({probs[0]['problem']})")
        if not row or row["event"] != "published" or row["row_hash"] != publication.get("row_hash"):
            errors.append("ledger: no published event with that id and hash")
        elif prep is None or ledger.payload(prep).get("content") != bundle:
            errors.append("ledger: the published event is not over this bundle")
        led["newest_published"] = bool(st["published"] and st["published"]["event_id"] == publication.get("event_id"))
    return {"ok": not errors, "errors": errors,
            "content_digest": bundle.get("content_digest") if isinstance(bundle, dict) else None, "ledger": led}


def ledger_view(target_id: str, conn=None) -> dict:
    own = conn is None
    conn = conn or ledger.connection()
    try:
        st, problems, _ = _state(target_id, conn)
    finally:
        if own:
            conn.close()
    p = st["prepared"]
    return {"prepared": p, "published": st["published"], "history": st["history"], "builders": builders(st),
            "chain": {"intact": not problems, "problems": problems},
            "next": ["publish"] if p and not p.get("decided") else ["prepare"], "template": TEMPLATE_PUBLISH}
