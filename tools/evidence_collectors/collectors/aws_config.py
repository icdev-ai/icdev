# CUI // SP-CTI
"""Cloud configuration: AWS Config, read-only (icx-ent-02).

A real, named integration: the AWS Config API (JSON 1.1 protocol, ``X-Amz-Target: StarlingDoveService.<operation>``,
SigV4 service ``config``), two read operations only:

* ``DescribeComplianceByConfigRule``      each named rule's ComplianceType (COMPLIANT, NON_COMPLIANT,
                                           NOT_APPLICABLE, INSUFFICIENT_DATA);
* ``DescribeConfigRuleEvaluationStatus``  each rule's ``LastSuccessfulEvaluationTime``: when the result was observed.

The role it signs as needs read access only (AWS's managed policy ``AWSConfigUserAccess`` grants Config's
Get/Describe/List). Sources: https://docs.aws.amazon.com/config/latest/APIReference/API_DescribeComplianceByConfigRule.html,
.../API_DescribeConfigRuleEvaluationStatus.html, https://docs.aws.amazon.com/config/latest/developerguide/security-iam-awsmanpol.html.

Facts are keyed by AWS's managed-rule IDENTIFIER (``aws-config:<IDENTIFIER>``); each system's configuration names the
rule its account deployed for it (``rules: {IDENTIFIER: rule-name}``). COMPLIANT is True, NON_COMPLIANT False;
NOT_APPLICABLE, INSUFFICIENT_DATA, a rule not in the account and a rule never evaluated successfully are UNVERIFIED.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

from tools.evidence_collectors import sigv4, transport
from tools.evidence_collectors.collectors.base import observation, rfc3339, unread
from tools.evidence_collectors.credentials import CredentialError

NAME = "aws-config"
KIND = "cloud_config"
INTEGRATION = "AWS Config API (DescribeComplianceByConfigRule, DescribeConfigRuleEvaluationStatus), read-only role"
#: The managed rules this collector knows (identifier -> what COMPLIANT means), checked against AWS's rule pages
#: (https://docs.aws.amazon.com/config/latest/developerguide/<rule-name>.html). ROOT_ACCOUNT_MFA_ENABLED is not
#: offered in the AWS GovCloud (US) Regions, so a GovCloud account leaves it unverified.
MANAGED_RULES = {
    "CLOUD_TRAIL_ENABLED": "a CloudTrail trail is enabled in the account",
    "CLOUD_TRAIL_LOG_FILE_VALIDATION_ENABLED": "CloudTrail log file validation is enabled on the trails",
    "ENCRYPTED_VOLUMES": "attached EBS volumes are encrypted",
    "S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED": "S3 buckets have default server-side encryption",
    "IAM_USER_MFA_ENABLED": "IAM users have MFA enabled",
    "ROOT_ACCOUNT_MFA_ENABLED": "the root user has MFA enabled",
}
FACTS = tuple(f"{NAME}:{k}" for k in MANAGED_RULES)
CREDENTIALS = ("access_key_id", "secret_access_key")
OPTIONAL_CREDENTIALS = ("session_token",)
_BATCH = 25     # ConfigRuleNames takes at most 25 names
_MAX_PAGES = 100


def _call(spec: dict, op: str, payload: dict, creds: dict, now: datetime) -> dict:
    url = transport.url_for(spec["endpoint"], "/")
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    headers = sigv4.sign("POST", url, {"Content-Type": "application/x-amz-json-1.1",
                                       "X-Amz-Target": f"StarlingDoveService.{op}"}, body,
                         access_key=creds["access_key_id"].reveal(), secret_key=creds["secret_access_key"].reveal(),
                         session_token=creds["session_token"].reveal() if creds.get("session_token") else None,
                         region=spec["region"], service="config", when=now)
    return transport.request_json("POST", url, headers=headers, body=body, op=f"{NAME}:{op}")


def _all(spec: dict, op: str, names: list[str], key: str, creds: dict, now: datetime) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(names), _BATCH):
        token = None
        for _ in range(_MAX_PAGES):           # a bounded number of pages; past it, the source is unread, not partial
            payload: dict = {"ConfigRuleNames": names[i:i + _BATCH]}
            if token:
                payload["NextToken"] = token
            doc = _call(spec, op, payload, creds, now)
            if not isinstance(doc, dict):
                raise transport.CollectorError(f"{spec['endpoint']}: {op} did not answer a JSON object")
            out += [r for r in (doc.get(key) or []) if isinstance(r, dict)]
            token = doc.get("NextToken")
            if not token:
                break
        else:
            raise transport.CollectorError(f"{spec['endpoint']}: {op} paging did not end in {_MAX_PAGES} pages")
    return out


def collect(system: str, spec: dict, *, now: datetime, max_age_h: float,
            resolve: Callable[[str], object]) -> list[dict]:
    rules: dict = spec.get("rules") or {}
    facts = [f"{NAME}:{k}" for k in rules]
    src = f"{transport.check_base(spec['endpoint'])} ({spec['region']})"
    try:
        creds = {k: resolve(spec["credentials"][k]) for k in CREDENTIALS}
        creds |= {k: resolve(spec["credentials"][k]) for k in OPTIONAL_CREDENTIALS if spec["credentials"].get(k)}
    except CredentialError as exc:
        return unread(system=system, collector=NAME, kind=KIND, facts=facts, why=f"credential: {exc}",
                      max_age_h=max_age_h, source=src)
    names = sorted(set(rules.values()))
    try:
        comp = {r.get("ConfigRuleName"): r for r in _all(spec, "DescribeComplianceByConfigRule", names,
                                                         "ComplianceByConfigRules", creds, now)}
        status = {r.get("ConfigRuleName"): r for r in _all(spec, "DescribeConfigRuleEvaluationStatus", names,
                                                           "ConfigRulesEvaluationStatus", creds, now)}
    except transport.CollectorError as exc:
        return unread(system=system, collector=NAME, kind=KIND, facts=facts, why=str(exc), max_age_h=max_age_h,
                      source=src)
    out = []
    for ident, rule in sorted(rules.items()):
        fact = f"{NAME}:{ident}"
        c, s = comp.get(rule), status.get(rule) or {}
        kind = ((c or {}).get("Compliance") or {}).get("ComplianceType")
        last = s.get("LastSuccessfulEvaluationTime")
        at = None
        if isinstance(last, (int, float)) and not isinstance(last, bool):
            at = rfc3339(datetime.fromtimestamp(float(last), timezone.utc))
        value, why = None, ""
        if c is None:
            why = f"rule {rule} is not in the account (not deployed)"
        elif at is None:
            why = f"rule {rule} has never been evaluated successfully"
        elif kind == "COMPLIANT":
            value, why = True, f"rule {rule}: COMPLIANT"
        elif kind == "NON_COMPLIANT":
            value, why = False, f"rule {rule}: NON_COMPLIANT"
        else:
            why = f"rule {rule}: {kind or 'no result'}"
        cnt = ((c or {}).get("Compliance") or {}).get("ComplianceContributorCount") or {}
        out.append(observation(system=system, collector=NAME, kind=KIND, fact=fact, value=value, why=why,
                               observed_at=at, max_age_h=max_age_h, source=f"{src} {rule}",
                               detail={"rule": rule, "compliance_type": kind,
                                       "noncompliant_resources": cnt.get("CappedCount")}))
    return out
