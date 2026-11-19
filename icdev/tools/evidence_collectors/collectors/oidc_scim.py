# CUI // SP-CTI
"""Identity provider: OpenID Connect Discovery and SCIM 2.0, read-only (icx-ent-02).

Real, named integrations, both open standards any conforming identity provider serves:

* OpenID Connect Discovery 1.0 (https://openid.net/specs/openid-connect-discovery-1_0.html):
  ``GET <issuer>/.well-known/openid-configuration``; the ``issuer`` it returns must be the configured one exactly
  (§4.3). ``code_challenge_methods_supported`` is RFC 8414 §2's.
* SCIM 2.0 (RFC 7644 §3.4.2, RFC 7643 §4.1): ``GET <scim>/Users?startIndex=&count=`` with a bearer token that can
  only read (the provider's read-only SCIM scope), ``application/scim+json``, a ListResponse paged by
  ``totalResults``.

Facts (each user is counted, never named):

* ``oidc:discovery_valid``           the document's issuer is the configured one and its endpoints are https;
* ``oidc:pkce_s256``                 ``S256`` is advertised (absent: unverified -- not advertised is not "no");
* ``oidc:signed_id_tokens``          ``none`` is not an ID-token signing algorithm;
* ``scim:users_identified``          every ACTIVE user has a userName, unique without case (RFC 7643: case-insensitive);
* ``scim:group_membership_specified`` every ACTIVE user is in at least one group (a provider that returns no
                                     ``groups`` at all leaves it unverified).

Discovery metadata says what a provider OFFERS, not what it enforces; the reviewed mapping (args/) decides which
fact speaks for which objective.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from tools.evidence_collectors import transport
from tools.evidence_collectors.collectors.base import observation, rfc3339, unread
from tools.evidence_collectors.credentials import CredentialError

NAME = "oidc-scim"
KIND = "identity_provider"
INTEGRATION = "OpenID Connect Discovery 1.0 and SCIM 2.0 (RFC 7644) /Users, read-only token"
OIDC_FACTS = ("oidc:discovery_valid", "oidc:pkce_s256", "oidc:signed_id_tokens")
SCIM_FACTS = ("scim:users_identified", "scim:group_membership_specified")
FACTS = OIDC_FACTS + SCIM_FACTS
CREDENTIALS = ("scim_token",)
OPTIONAL_CREDENTIALS: tuple = ()
MAX_USERS = 100_000


def _https(u) -> bool:
    """https (or http to a loopback stub, the transport's one exception)."""
    import urllib.parse

    p = urllib.parse.urlsplit(str(u or ""))
    try:
        transport.check_base(f"{p.scheme}://{p.netloc}")
    except transport.CollectorError:
        return False
    return True


def _oidc(system: str, spec: dict, now: datetime, max_age_h: float) -> list[dict]:
    issuer = str(spec["issuer"]).rstrip("/")
    src = f"{issuer}/.well-known/openid-configuration"
    try:
        doc = transport.request_json("GET", transport.url_for(issuer, "/.well-known/openid-configuration"),
                                     headers={"Accept": "application/json"})
    except transport.CollectorError as exc:
        return unread(system=system, collector=NAME, kind=KIND, facts=OIDC_FACTS, why=str(exc), max_age_h=max_age_h,
                      source=src)
    at = rfc3339(now)
    if not isinstance(doc, dict):
        doc = {}

    def obs(fact, value, why, detail=None):
        return observation(system=system, collector=NAME, kind=KIND, fact=fact, value=value, why=why, observed_at=at,
                           max_age_h=max_age_h, source=src, detail=detail)

    ends = [doc.get(k) for k in ("authorization_endpoint", "token_endpoint", "jwks_uri")]
    if doc.get("issuer") != spec["issuer"]:
        valid = (False, "the document's issuer is not the configured issuer (OIDC Discovery 1.0 §4.3)")
    elif not all(_https(e) for e in ends):
        valid = (False, "an authorization, token or JWKS endpoint is missing or not https")
    else:
        valid = (True, "issuer matches; authorization, token and JWKS endpoints are https")
    methods = doc.get("code_challenge_methods_supported")
    if not isinstance(methods, list):
        pkce = (None, "code_challenge_methods_supported is not advertised")
    else:
        pkce = ("S256" in methods, f"code_challenge_methods_supported: {', '.join(map(str, methods))[:200]}")
    algs = doc.get("id_token_signing_alg_values_supported")
    if not isinstance(algs, list) or not algs:
        signed = (None, "id_token_signing_alg_values_supported is missing (it is REQUIRED)")
    else:
        signed = ("none" not in algs, f"id_token_signing_alg_values_supported: {', '.join(map(str, algs))[:200]}")
    return [obs("oidc:discovery_valid", *valid), obs("oidc:pkce_s256", *pkce), obs("oidc:signed_id_tokens", *signed)]


def _users(spec: dict, token) -> list[dict]:
    page = int(spec.get("page_size") or 200)
    users: list[dict] = []
    start = 1
    for _ in range(MAX_USERS // max(page, 1) + 1):
        doc = transport.request_json("GET", transport.url_for(spec["scim_base"], "/Users",
                                                              {"startIndex": start, "count": page}),
                                     headers={"Accept": "application/scim+json",
                                              "Authorization": f"Bearer {token.reveal()}"})
        if not isinstance(doc, dict) or "totalResults" not in doc:
            raise transport.CollectorError(f"{spec['scim_base']}/Users: not a SCIM ListResponse")
        got = [r for r in (doc.get("Resources") or []) if isinstance(r, dict)]
        users += got
        total = int(doc.get("totalResults") or 0)
        if total > MAX_USERS:
            raise transport.CollectorError(f"{spec['scim_base']}/Users: more than {MAX_USERS} users")
        if not got or len(users) >= total:
            return users
        start += len(got)
    raise transport.CollectorError(f"{spec['scim_base']}/Users: paging did not end")


def _scim(system: str, spec: dict, now: datetime, max_age_h: float, resolve) -> list[dict]:
    src = f"{str(spec['scim_base']).rstrip('/')}/Users"
    try:
        token = resolve(spec["credentials"]["scim_token"])
        users = _users(spec, token)
    except (CredentialError, transport.CollectorError) as exc:
        why = f"credential: {exc}" if isinstance(exc, CredentialError) else str(exc)
        return unread(system=system, collector=NAME, kind=KIND, facts=SCIM_FACTS, why=why, max_age_h=max_age_h,
                      source=src)
    at = rfc3339(now)
    active = [u for u in users if u.get("active", True) is True]
    names = [str(u.get("userName") or "").strip().casefold() for u in active]
    unnamed = sum(1 for n in names if not n)
    dupes = len([n for n in names if n]) - len({n for n in names if n})
    carries_groups = any("groups" in u for u in users)
    no_group = sum(1 for u in active if not (u.get("groups") or []))
    counts = {"users": len(users), "active": len(active), "without_username": unnamed, "duplicate_usernames": dupes,
              "active_without_group": no_group if carries_groups else None}

    def obs(fact, value, why):
        return observation(system=system, collector=NAME, kind=KIND, fact=fact, value=value, why=why, observed_at=at,
                           max_age_h=max_age_h, source=src, detail=counts)

    if not active:
        ident = (None, "no active users: nothing to judge")
        grp = (None, "no active users: nothing to judge")
    else:
        ident = ((unnamed == 0 and dupes == 0),
                 f"{len(active)} active users; {unnamed} without a userName, {dupes} duplicate userNames")
        grp = ((None, "the provider returns no group membership on /Users") if not carries_groups else
               (no_group == 0, f"{no_group} of {len(active)} active users are in no group"))
    return [obs("scim:users_identified", *ident), obs("scim:group_membership_specified", *grp)]


def collect(system: str, spec: dict, *, now: datetime, max_age_h: float,
            resolve: Callable[[str], object]) -> list[dict]:
    return _oidc(system, spec, now, max_age_h) + _scim(system, spec, now, max_age_h, resolve)
