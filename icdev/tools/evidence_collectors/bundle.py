# CUI // SP-CTI
"""From observations to one ``icos-objective-evidence/v1`` report per system per run (icx-ent-02).

**Judging an objective** (:func:`evaluate`) by the reviewed mapping (``args/evidence_collectors.yaml``). Each fact an
objective rests on is, at the moment the report is produced:

* unverified when it was not collected, its value is None, its time is missing, unparseable, without a zone or after
  the report (times are parsed and compared as instants, never as strings), or it is older than its collector's
  freshness budget (stale: it counts for nothing);
* otherwise fresh, True or False.

``other_than_satisfied`` when any fact is fresh and False (a finding stands even beside an unverified one);
``satisfied`` only when every fact is fresh and True; otherwise the objective is LEFT OUT of the report and listed as
unverified with why. Nothing is ever met from absence, and the report never claims more than was observed: each
entry's note carries what was observed, its scope and the mapping's digest. An entry's ``observed_at`` is the OLDEST
time of the facts it rests on. Entries cite nothing (``evidence: []``): what a collector saw is not a citation ICDEV[OS]
resolves, so at ICDEV[OS] they stand as unverified until an assessor's determination cites this report
(``report:<content digest>``), which is the contract's design.

**The report** (:func:`build`): the contract's fields only, ``classification`` the system's configured marking (a
claim: ICDEV[OS] marks it by its own registry). ``content_digest`` is ICDEV[OS]'s: SHA-256 of the canonical JSON (keys
sorted, separators ``,`` and ``:``) of the report without ``content_digest`` and ``publication``. It is validated
against the vendored, SHA-256-pinned schema before anything records it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from icdev.core.paths import repo_root

from tools.evidence_collectors.collectors import FACT_OWNER
from tools.evidence_collectors.collectors.base import instant, rfc3339
from tools.evidence_collectors.constants import PRODUCER, PUBLICATION_FIELDS, SCHEMA, SCHEMA_RELPATH, SCHEMA_SHA256
from tools.idp.dora import digest

BASE_DIR = repo_root(__file__)


class BundleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("icdev")
    except Exception:  # noqa: BLE001 — an unpackaged checkout still says what it is
        return "unpackaged"


def freshness(o: dict | None, made: datetime) -> tuple[str, str]:
    """``(state, why)``: state is ``fresh`` or ``unverified``."""
    if o is None:
        return "unverified", "not collected"
    if o.get("value") is None:
        return "unverified", str(o.get("why") or "the source did not say")
    t = instant(o.get("observed_at"))
    if t is None:
        return "unverified", "no RFC 3339 time with its zone"
    if t > made:
        return "unverified", "observed after the report was produced"
    age_h = (made - t).total_seconds() / 3600
    if age_h > float(o.get("max_age_h") or 0):
        return "unverified", f"stale: {age_h:.0f}h old, past its {float(o.get('max_age_h') or 0):g}h budget"
    return "fresh", ""


def evaluate(observations: list[dict], mapping: list[dict], made: datetime, *, mapping_sha256: str = "") -> dict:
    """``{"entries": [...], "unverified": [{objective, why}], "facts": {fact: state}}`` for one system."""
    by = {o["fact"]: o for o in observations}
    entries, unverified, facts = [], [], {}
    for rule in mapping:
        obj = rule["objective"]
        states = []
        for f in rule["all_of"]:
            st, why = freshness(by.get(f), made)
            facts[f] = st if st != "fresh" else ("true" if by[f]["value"] else "false")
            states.append((f, st, why, by.get(f)))
        false = [s for s in states if s[1] == "fresh" and s[3]["value"] is False]
        if false:
            result, basis = "other_than_satisfied", false
        elif all(s[1] == "fresh" and s[3]["value"] is True for s in states):
            result, basis = "satisfied", states
        else:
            unverified.append({"objective": obj, "why": "; ".join(f"{f}: {why}" for f, st, why, _ in states
                                                                 if st != "fresh")})
            continue
        oldest = min(instant(s[3]["observed_at"]) for s in basis)
        seen = "; ".join(f"{f}={'true' if o['value'] else 'false'} ({o['why']}; observed {o['observed_at']} by "
                         f"{o['collector']}, budget {o['max_age_h']:g}h)" for f, _, _, o in basis)
        note = (f"Observed by ICDEV[IT]'s read-only collectors: {seen}. Scope: {rule['scope']}. Basis: {rule['basis']}. "
                f"Mapping args/evidence_collectors.yaml sha256:{mapping_sha256[:12]}.")
        entries.append({"objective": obj, "result": result, "observed_at": rfc3339(oldest), "evidence": [],
                        "method": "automated", "note": note[:2000]})
    entries.sort(key=lambda e: e["objective"])
    return {"entries": entries, "unverified": sorted(unverified, key=lambda u: u["objective"]), "facts": facts}


def content_digest(report: dict) -> str:
    """ICDEV[OS]'s rule (icdev_os/backbone/evidence.py ``content_digest``): the report without ``content_digest``
    and ``publication``."""
    return digest({k: v for k, v in report.items() if k not in ("content_digest", "publication")})


def schema() -> dict:
    raw = (BASE_DIR / SCHEMA_RELPATH).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != SCHEMA_SHA256:
        raise BundleError("schema_unpinned", f"{SCHEMA_RELPATH} is not the pinned ICDEV[OS] contract (sha256 {got}, "
                                             f"pinned {SCHEMA_SHA256})")
    return json.loads(raw)


def validate(report: dict) -> list[str]:
    """The report's errors against the pinned contract, then what the schema cannot say: no time after
    ``produced_at``, the digest, and a publication of exactly the contract's fields at that digest by someone other
    than the preparer and not an agent. Empty: ICDEV[OS] would take it."""
    import jsonschema

    v = jsonschema.Draft202012Validator(schema())
    errors = sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
                    for e in v.iter_errors(report))
    if errors:
        return errors
    made = instant(report["produced_at"])
    if made is None:
        return ["produced_at: not an RFC 3339 time with its zone"]
    for e in report["entries"]:
        t = instant(e["observed_at"])
        if t is None or t > made:
            errors.append(f"{e['objective']}: observed_at is not a time at or before produced_at")
    if report.get("content_digest") != content_digest(report):
        errors.append("content_digest is not the digest of the report's content")
    pub = report.get("publication")
    if pub is not None:
        if set(pub) != set(PUBLICATION_FIELDS):
            errors.append("publication: exactly " + ", ".join(PUBLICATION_FIELDS))
        if pub.get("content_digest") != report.get("content_digest"):
            errors.append("publication: taken at another digest")
        if str(pub.get("signed_by") or "").strip().casefold() == str(pub.get("prepared_by") or "").strip().casefold():
            errors.append("publication: its preparer published it")
        if str(pub.get("signed_by") or "").strip().casefold().startswith("agent:"):
            errors.append("publication: an agent only prepares; a person publishes")
    return errors


def build(system: dict, observations: list[dict], mapping: list[dict], *, made: datetime | None = None,
          mapping_sha256: str = "") -> tuple[dict, dict]:
    """``(report, judged)``. :class:`BundleError` ``nothing_determinable`` when no objective could be judged (the
    contract needs one entry, and an empty report would read as evidence of nothing)."""
    made = (made or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    judged = evaluate([o for o in observations if o.get("system") == system["id"]], mapping, made,
                      mapping_sha256=mapping_sha256)
    if not judged["entries"]:
        raise BundleError("nothing_determinable",
                          f"no objective of {system['id']} could be judged from what was observed: every one is "
                          "unverified, so there is no report to prepare")
    used = sorted({FACT_OWNER[f] for r in mapping for f in r["all_of"]
                   if r["objective"] in {e["objective"] for e in judged["entries"]}})
    report = {"schema": SCHEMA, "system": system["id"], "as_of": made.date().isoformat(), "produced_at": rfc3339(made),
              "producer": {"tool": PRODUCER, "version": _version()},
              "collector": "ICDEV[IT] read-only evidence collectors: " + ", ".join(used),
              "classification": system["marking"], "entries": judged["entries"]}
    report["content_digest"] = content_digest(report)
    errors = validate(report)
    if errors:
        raise BundleError("contract", f"the report does not meet {SCHEMA}: " + "; ".join(errors[:10]))
    return report, judged


def published(report: dict, *, prepared_by: str, signed_by: str, step_id: str, decided_at: str) -> dict:
    """The report with its publication EMBEDDED, exactly the contract's fields. Validated before it is returned."""
    out = dict(report)
    out["publication"] = {"prepared_by": prepared_by, "signed_by": signed_by, "step_id": step_id,
                          "decided_at": decided_at, "content_digest": report["content_digest"]}
    errors = validate(out)
    if errors:
        raise BundleError("contract", "the published report does not meet the contract: " + "; ".join(errors[:5]))
    return out


def observations_digest(observations: list[dict]) -> str:
    return digest(sorted(observations, key=lambda o: (o["collector"], o["fact"])))


__all__ = ["BundleError", "build", "content_digest", "evaluate", "freshness", "observations_digest", "published",
           "schema", "validate"]

