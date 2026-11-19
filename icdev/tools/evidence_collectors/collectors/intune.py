# CUI // SP-CTI
"""Endpoints: Microsoft Intune through Microsoft Graph, read-only (icx-ent-02).

A real, named integration: ``GET /v1.0/deviceManagement/managedDevices`` (paged by ``@odata.nextLink``), with an
app-only token from the Microsoft identity platform's client-credentials grant (scope
``https://graph.microsoft.com/.default``). The application needs only ``DeviceManagementManagedDevices.Read.All``.
Source: https://learn.microsoft.com/en-us/graph/api/intune-devices-manageddevice-list?view=graph-rest-1.0 and
https://learn.microsoft.com/en-us/graph/api/resources/intune-devices-manageddevice?view=graph-rest-1.0.

Facts (devices are counted, never named):

* ``intune:devices_identified``  at least one managed device, each with an id and a name (none: unverified);
* ``intune:all_encrypted``       every device reports ``isEncrypted``;
* ``intune:all_compliant``       every device's ``complianceState`` is ``compliant`` (any ``noncompliant``: False;
                                 ``unknown``, ``conflict``, ``error``, ``inGracePeriod``, ``configManager``: unverified).

Freshness from the SOURCE: a device whose ``lastSyncDateTime`` is older than the budget, missing or unparseable has
not told us its state, so the encryption and compliance facts are UNVERIFIED while it is in the fleet, and their
``observed_at`` is the oldest device report, never the time we asked.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime
from typing import Callable

from tools.evidence_collectors import transport
from tools.evidence_collectors.collectors.base import instant, observation, rfc3339, unread
from tools.evidence_collectors.credentials import CredentialError

NAME = "intune"
KIND = "endpoint_mdm"
INTEGRATION = "Microsoft Intune via Microsoft Graph v1.0 managedDevices (DeviceManagementManagedDevices.Read.All)"
FACTS = ("intune:devices_identified", "intune:all_encrypted", "intune:all_compliant")
CREDENTIALS = ("client_secret",)
OPTIONAL_CREDENTIALS: tuple = ()
GRAPH = "https://graph.microsoft.com"
LOGIN = "https://login.microsoftonline.com"
_SELECT = "id,deviceName,operatingSystem,complianceState,isEncrypted,lastSyncDateTime"
MAX_PAGES = 500


def _token(spec: dict, secret) -> str:
    login = spec.get("login_base") or LOGIN
    url = transport.url_for(login, f"/{urllib.parse.quote(str(spec['tenant_id']), safe='')}/oauth2/v2.0/token")
    body = urllib.parse.urlencode({"client_id": spec["client_id"], "client_secret": secret.reveal(),
                                   "scope": "https://graph.microsoft.com/.default",
                                   "grant_type": "client_credentials"}).encode("utf-8")
    doc = transport.request_json("POST", url, headers={"Content-Type": "application/x-www-form-urlencoded"},
                                 body=body, op="oauth2:client_credentials_token")
    tok = doc.get("access_token") if isinstance(doc, dict) else None
    if not tok:
        raise transport.CollectorError(f"{urllib.parse.urlsplit(url).netloc}: no access_token in the token response")
    return str(tok)


def _devices(spec: dict, token: str) -> list[dict]:
    graph = spec.get("graph_base") or GRAPH
    url = transport.url_for(graph, "/v1.0/deviceManagement/managedDevices", {"$select": _SELECT})
    out: list[dict] = []
    for _ in range(MAX_PAGES):
        doc = transport.request_json("GET", url, headers={"Accept": "application/json",
                                                          "Authorization": f"Bearer {token}"})
        if not isinstance(doc, dict) or not isinstance(doc.get("value"), list):
            raise transport.CollectorError(f"{urllib.parse.urlsplit(url).netloc}: not a Graph collection")
        out += [d for d in doc["value"] if isinstance(d, dict)]
        nxt = doc.get("@odata.nextLink")
        if not nxt:
            return out
        url = transport.url_for(graph, str(nxt))     # a next link off the Graph host is refused
    raise transport.CollectorError("managedDevices: paging did not end")


def collect(system: str, spec: dict, *, now: datetime, max_age_h: float,
            resolve: Callable[[str], object]) -> list[dict]:
    src = f"{spec.get('graph_base') or GRAPH}/v1.0/deviceManagement/managedDevices"
    try:
        token = _token(spec, resolve(spec["credentials"]["client_secret"]))
        devices = _devices(spec, token)
    except (CredentialError, transport.CollectorError) as exc:
        why = f"credential: {exc}" if isinstance(exc, CredentialError) else str(exc)
        return unread(system=system, collector=NAME, kind=KIND, facts=FACTS, why=why, max_age_h=max_age_h, source=src)
    at_now = rfc3339(now)
    synced = [instant(d.get("lastSyncDateTime")) for d in devices]
    stale = sum(1 for t in synced if t is None or t > now or (now - t).total_seconds() > max_age_h * 3600)
    oldest = min((t for t in synced if t is not None and t <= now), default=None)
    states = [str(d.get("complianceState") or "") for d in devices]
    counts = {"devices": len(devices), "not_reported_within_budget": stale,
              "unencrypted": sum(1 for d in devices if d.get("isEncrypted") is not True),
              "noncompliant": states.count("noncompliant"),
              "compliance_unknown": sum(1 for s in states if s not in ("compliant", "noncompliant"))}

    def obs(fact, value, why, at):
        return observation(system=system, collector=NAME, kind=KIND, fact=fact, value=value, why=why, observed_at=at,
                           max_age_h=max_age_h, source=src, detail=counts)

    if not devices:
        none = "no managed devices: nothing to judge"
        return [obs(f, None, none, at_now) for f in FACTS]
    named = all(str(d.get("id") or "").strip() and str(d.get("deviceName") or "").strip() for d in devices)
    out = [obs("intune:devices_identified", named,
               f"{len(devices)} managed devices" + ("" if named else "; some have no id or name"), at_now)]
    if stale:
        why = f"{stale} of {len(devices)} devices have not reported within {max_age_h:g}h: their state is unknown"
        return out + [obs("intune:all_encrypted", None, why, None), obs("intune:all_compliant", None, why, None)]
    at = rfc3339(oldest) if oldest else None
    enc = counts["unencrypted"] == 0
    out.append(obs("intune:all_encrypted", enc, f"{counts['unencrypted']} of {len(devices)} devices not encrypted", at))
    if counts["noncompliant"]:
        comp = (False, f"{counts['noncompliant']} of {len(devices)} devices noncompliant")
    elif counts["compliance_unknown"]:
        comp = (None, f"{counts['compliance_unknown']} of {len(devices)} devices in no definite compliance state")
    else:
        comp = (True, f"all {len(devices)} devices compliant")
    out.append(obs("intune:all_compliant", *comp, at))
    return out
