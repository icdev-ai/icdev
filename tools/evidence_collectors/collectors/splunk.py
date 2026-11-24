# CUI // SP-CTI
"""SIEM: Splunk Enterprise REST API, read-only (icx-ent-02).

A real, named integration on the management port (8089 by default), with a Splunk authentication token
(``Authorization: Bearer <token>``) of a role that can only read:

* ``GET /services/data/indexes?output_mode=json&count=0``  each index's ``frozenTimePeriodInSecs`` and ``disabled``;
* ``GET /services/saved/searches?output_mode=json&count=0`` each saved search's ``is_scheduled`` and ``disabled``.

Sources: https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.0/introspection-endpoints/introspection-endpoint-descriptions,
.../search-endpoints/search-endpoint-descriptions, and .../use-authentication-tokens. The JSON form (``entry[].name``,
``entry[].content``) is the Atom feed's, as ``output_mode=json`` returns it.

Facts, over the indexes and searches the system's configuration names (a name that is not there is UNVERIFIED --
it may be named otherwise -- never a failure):

* ``splunk:audit_retention_met``      every named audit index is enabled and keeps data at least the declared
                                       ``required_retention_days``;
* ``splunk:review_searches_scheduled`` every named review search is enabled and scheduled.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from tools.evidence_collectors import transport
from tools.evidence_collectors.collectors.base import observation, rfc3339, unread
from tools.evidence_collectors.credentials import CredentialError

NAME = "splunk"
KIND = "siem"
INTEGRATION = "Splunk Enterprise REST API (/services/data/indexes, /services/saved/searches), read-only token"
FACTS = ("splunk:audit_retention_met", "splunk:review_searches_scheduled")
CREDENTIALS = ("token",)
OPTIONAL_CREDENTIALS: tuple = ()


def _flag(v) -> bool | None:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return True if s in ("1", "true") else False if s in ("0", "false") else None


def _entries(spec: dict, path: str, token) -> dict[str, dict]:
    doc = transport.request_json("GET", transport.url_for(spec["base"], path, {"output_mode": "json", "count": 0}),
                                 headers={"Accept": "application/json", "Authorization": f"Bearer {token.reveal()}"})
    if not isinstance(doc, dict) or not isinstance(doc.get("entry"), list):
        raise transport.CollectorError(f"{spec['base']}{path}: not a Splunk entry feed")
    return {str(e.get("name")): (e.get("content") or {}) for e in doc["entry"] if isinstance(e, dict)}


def collect(system: str, spec: dict, *, now: datetime, max_age_h: float,
            resolve: Callable[[str], object]) -> list[dict]:
    src = f"{transport.check_base(spec['base'])}/services"
    try:
        token = resolve(spec["credentials"]["token"])
        indexes = _entries(spec, "/services/data/indexes", token)
        searches = _entries(spec, "/services/saved/searches", token)
    except (CredentialError, transport.CollectorError) as exc:
        why = f"credential: {exc}" if isinstance(exc, CredentialError) else str(exc)
        return unread(system=system, collector=NAME, kind=KIND, facts=FACTS, why=why, max_age_h=max_age_h, source=src)
    at = rfc3339(now)
    need_d = transport.finite(spec["required_retention_days"])
    if need_d is None or need_d <= 0:
        return unread(system=system, collector=NAME, kind=KIND, facts=FACTS, max_age_h=max_age_h, source=src,
                      why="required_retention_days is not a positive, finite number of days")
    need_s = need_d * 86400

    idx = []
    for name in spec["audit_indexes"]:
        c = indexes.get(name)
        if c is None:
            idx.append((None, f"index {name} is not there"))
            continue
        keep = transport.finite(c.get("frozenTimePeriodInSecs"))
        if keep is None or keep < 0:
            idx.append((None, f"index {name}: frozenTimePeriodInSecs is not a finite number of seconds"))
            continue
        off = _flag(c.get("disabled"))
        if off is None:
            idx.append((None, f"index {name}: disabled is unreadable"))
        elif off:
            idx.append((False, f"index {name} is disabled"))
        elif keep < need_s:
            idx.append((False, f"index {name} keeps {keep / 86400:g} days, under {need_s / 86400:g}"))
        else:
            idx.append((True, f"index {name} keeps {keep / 86400:g} days"))
    srch = []
    for name in spec["review_searches"]:
        c = searches.get(name)
        if c is None:
            srch.append((None, f"saved search {name} is not there"))
            continue
        off, sched = _flag(c.get("disabled")), _flag(c.get("is_scheduled"))
        if off is None or sched is None:
            srch.append((None, f"saved search {name}: disabled/is_scheduled unreadable"))
        elif off or not sched:
            srch.append((False, f"saved search {name} is {'disabled' if off else 'not scheduled'}"))
        else:
            srch.append((True, f"saved search {name} is enabled and scheduled"))

    def fold(parts):
        if any(v is False for v, _ in parts):
            return False
        if parts and all(v is True for v, _ in parts):
            return True
        return None

    counts = {"audit_indexes": len(spec["audit_indexes"]), "review_searches": len(spec["review_searches"])}
    return [observation(system=system, collector=NAME, kind=KIND, fact="splunk:audit_retention_met", value=fold(idx),
                        why="; ".join(w for _, w in idx)[:500], observed_at=at, max_age_h=max_age_h,
                        source=f"{src}/data/indexes", detail=counts),
            observation(system=system, collector=NAME, kind=KIND, fact="splunk:review_searches_scheduled",
                        value=fold(srch), why="; ".join(w for _, w in srch)[:500], observed_at=at,
                        max_age_h=max_age_h, source=f"{src}/saved/searches", detail=counts)]
