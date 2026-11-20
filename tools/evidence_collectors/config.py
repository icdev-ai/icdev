# CUI // SP-CTI
"""Load and check ``args/evidence_collectors.yaml`` (icx-ent-02). Fails closed: anything not understood is refused.

* every collector is one of the four modules, with a positive freshness budget;
* every mapping row names an objective of SP 800-171 Rev. 2 or SP 800-53 Rev. 5 (ICDEV[OS]'s backbone ids), once, and
  facts some collector observes;
* every system has an id ICDEV[OS] accepts, an owning org, a marking on the ladder, and per collector exactly the
  fields that collector reads. A credential is a REFERENCE (keyring: or vault:) or the file is refused, and a key
  that looks like a secret anywhere outside ``credentials`` -- at any depth, compared without case -- is refused too,
  so a token pasted in the wrong place never loads.

The path is fixed (the reviewed file in this repository); no environment variable or caller chooses another. Tests
and the e2e seed pass a parsed configuration to the functions that take ``cfg``.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from icdev.core.paths import repo_root

from tools.evidence_collectors import transport
from tools.evidence_collectors.collectors import FACT_OWNER, REGISTRY
from tools.evidence_collectors.collectors.aws_config import MANAGED_RULES
from tools.evidence_collectors.constants import CONFIG_RELPATH, CONFIG_SCHEMA, MARKINGS
from tools.evidence_collectors.credentials import CredentialError, check_ref

BASE_DIR = repo_root(__file__)
SYSTEM_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")          # fullmatch, ASCII only
OBJECTIVE = re.compile(r"sp800-171r2/3\.[0-9]{1,2}\.[0-9]{1,2}(\[[a-z]\])?|sp800-53r5/[a-z]{2}-[0-9]{1,2}(\.[0-9]{1,2})?_obj(\.[a-z0-9]{1,4}){0,4}")
SECRETISH = ("token", "secret", "password", "passwd", "api_key", "apikey", "private_key", "credential")

#: The fields each collector reads (required, optional). Anything else is refused.
FIELDS = {
    "aws-config": ({"endpoint", "region", "rules", "credentials"}, set()),
    "oidc-scim": ({"issuer", "scim_base", "credentials"}, {"page_size"}),
    "intune": ({"tenant_id", "client_id", "credentials"}, {"graph_base", "login_base"}),
    "splunk": ({"base", "audit_indexes", "required_retention_days", "review_searches", "credentials"}, set()),
}


#: Each service's own hosts. An endpoint is one of them, or a loopback stub; never another host, so a configuration
#: cannot point a credential at someone else. AWS Config: config(-fips).<region>.amazonaws.com for the system's own
#: region (https://docs.aws.amazon.com/general/latest/gr/awsconfig.html). Microsoft Graph and identity platform: the
#: commercial hosts, and the US Government (GCC High) and DoD ones.
GRAPH_HOSTS = ("graph.microsoft.com", "graph.microsoft.us", "dod-graph.microsoft.us")
LOGIN_HOSTS = ("login.microsoftonline.com", "login.microsoftonline.us")


def _service_host(name: str, key: str, spec: dict) -> bool:
    import urllib.parse

    u = urllib.parse.urlsplit(str(spec.get(key) or ""))
    host = (u.hostname or "").casefold()
    if u.scheme == "http":           # check_base already allows plain http only to loopback
        return True
    if name == "aws-config" and key == "endpoint":
        region = str(spec.get("region") or "").casefold()
        return host in (f"config.{region}.amazonaws.com", f"config-fips.{region}.amazonaws.com")
    if name == "intune" and key == "graph_base":
        return host in GRAPH_HOSTS
    if name == "intune" and key == "login_base":
        return host in LOGIN_HOSTS
    return True


class ConfigError(ValueError):
    """The configuration is refused."""


def _secretish_outside(node: Any, path: str, errs: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            key = str(k).casefold()
            if key == "credentials":
                continue
            if any(s in key for s in SECRETISH):
                errs.append(f"{path}.{k}: a credential belongs under credentials, as a reference")
            _secretish_outside(v, f"{path}.{k}", errs)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _secretish_outside(v, f"{path}[{i}]", errs)


def _check_system(s: dict, collectors: dict, errs: list[str]) -> None:
    sid = s.get("id")
    at = f"systems[{sid!r}]"
    if not isinstance(sid, str) or not SYSTEM_ID.fullmatch(sid):
        errs.append(f"{at}: id must match {SYSTEM_ID.pattern} (ICDEV[OS]'s system id)")
    if not str(s.get("org") or "").strip():
        errs.append(f"{at}: org (the owning organisation) is required")
    if s.get("marking") not in MARKINGS:
        errs.append(f"{at}: marking must be one of {', '.join(MARKINGS)}")
    extra = set(s) - {"id", "synthetic", "org", "marking", "collectors"}
    if extra:
        errs.append(f"{at}: unknown fields {sorted(extra)}")
    cols = s.get("collectors")
    if not isinstance(cols, dict) or not cols:
        errs.append(f"{at}: collectors is required")
        return
    _secretish_outside(s, at, errs)
    for name, spec in cols.items():
        where = f"{at}.collectors.{name}"
        if name not in collectors:
            errs.append(f"{where}: not a declared collector")
            continue
        if not isinstance(spec, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        need, opt = FIELDS[name]
        if need - set(spec):
            errs.append(f"{where}: missing {sorted(need - set(spec))}")
        if set(spec) - need - opt:
            errs.append(f"{where}: unknown fields {sorted(set(spec) - need - opt)}")
        creds = spec.get("credentials")
        mod = REGISTRY[name]
        if not isinstance(creds, dict):
            errs.append(f"{where}.credentials: must be a mapping of references")
        else:
            for k in mod.CREDENTIALS:
                if k not in creds:
                    errs.append(f"{where}.credentials.{k}: required")
            for k, ref in creds.items():
                if k not in mod.CREDENTIALS + tuple(getattr(mod, "OPTIONAL_CREDENTIALS", ())):
                    errs.append(f"{where}.credentials.{k}: not a credential this collector reads")
                try:
                    check_ref(ref)
                except CredentialError as exc:
                    errs.append(f"{where}.credentials.{k}: {exc}")
        for key in ("endpoint", "issuer", "scim_base", "graph_base", "login_base", "base"):
            if key in spec:
                try:
                    transport.check_base(str(spec[key]))
                except transport.CollectorError as exc:
                    errs.append(f"{where}.{key}: {exc}")
        for key in ("endpoint", "graph_base", "login_base"):
            if key in spec and not _service_host(name, key, spec):
                errs.append(f"{where}.{key}: not {name}'s own service host (or a loopback stub)")
        if name == "aws-config":
            rules = spec.get("rules")
            if not isinstance(rules, dict) or not rules or set(rules) - set(MANAGED_RULES):
                errs.append(f"{where}.rules: a mapping of managed-rule identifiers ({', '.join(MANAGED_RULES)}) "
                            "to the account's rule names")
            if not re.fullmatch(r"[a-z]{2}(-gov)?-[a-z]+-[0-9]", str(spec.get("region") or "")):
                errs.append(f"{where}.region: not an AWS region code")
        if name == "splunk":
            if not isinstance(spec.get("audit_indexes"), list) or not spec.get("audit_indexes"):
                errs.append(f"{where}.audit_indexes: a non-empty list of index names")
            if not isinstance(spec.get("review_searches"), list):
                errs.append(f"{where}.review_searches: a list of saved-search names")
            days = transport.finite(spec.get("required_retention_days"))
            if days is None or days <= 0:
                errs.append(f"{where}.required_retention_days: a positive, finite number of days")


def validate(cfg: Any) -> dict:
    """The parsed configuration, checked; :class:`ConfigError` with every problem otherwise."""
    errs: list[str] = []
    if not isinstance(cfg, dict) or cfg.get("schema") != CONFIG_SCHEMA:
        raise ConfigError(f"not an {CONFIG_SCHEMA} configuration")
    collectors = cfg.get("collectors") or {}
    for name, c in collectors.items():
        mod = REGISTRY.get(name)
        if mod is None:
            errs.append(f"collectors.{name}: no such collector ({', '.join(REGISTRY)})")
            continue
        if (c or {}).get("kind") != mod.KIND:
            errs.append(f"collectors.{name}.kind: must be {mod.KIND}")
        try:
            h = float((c or {}).get("max_age_h"))
            if not 0 < h <= 24 * 366:
                raise ValueError
        except (TypeError, ValueError):
            errs.append(f"collectors.{name}.max_age_h: a freshness budget in hours, above 0 and at most a year")
    lim = cfg.get("limits") or {}
    if not isinstance(lim, dict) or set(lim) - {"min_collect_interval_s", "run_seconds", "run_bytes"}:
        errs.append("limits: only min_collect_interval_s, run_seconds and run_bytes")
    else:
        for k, v in lim.items():
            f = transport.finite(v)
            if f is None or f < 0:
                errs.append(f"limits.{k}: a finite number, 0 or more")
    seen: set[str] = set()
    for i, m in enumerate(cfg.get("mapping") or []):
        obj = (m or {}).get("objective")
        if not isinstance(obj, str) or not OBJECTIVE.fullmatch(obj):
            errs.append(f"mapping[{i}]: {obj!r} is not an sp800-171r2 or sp800-53r5 objective id")
        elif obj in seen:
            errs.append(f"mapping[{i}]: {obj} is mapped twice")
        seen.add(str(obj))
        facts = (m or {}).get("all_of")
        if not isinstance(facts, list) or not facts:
            errs.append(f"mapping[{i}]: all_of lists the facts that must all hold")
        else:
            for f in facts:
                if f not in FACT_OWNER:
                    errs.append(f"mapping[{i}]: {f!r} is no collector's fact")
                elif FACT_OWNER[f] not in collectors:
                    errs.append(f"mapping[{i}]: {f} needs collector {FACT_OWNER[f]}, which is not declared")
        for k in ("scope", "basis"):
            if not str((m or {}).get(k) or "").strip():
                errs.append(f"mapping[{i}].{k}: required (it says what the observation covers and why)")
    ids = []
    for s in cfg.get("systems") or []:
        if not isinstance(s, dict):
            errs.append("systems: each system is a mapping")
            continue
        ids.append(s.get("id"))
        _check_system(s, collectors, errs)
    if len(ids) != len(set(map(str, ids))):
        errs.append("systems: an id is repeated")
    if errs:
        raise ConfigError("; ".join(errs[:20]))
    return cfg


def load(path: Path | None = None) -> dict:
    """``args/evidence_collectors.yaml``, checked, with ``_sha256`` of its bytes."""
    p = path or (BASE_DIR / CONFIG_RELPATH)
    raw = Path(p).read_bytes()
    cfg = validate(yaml.safe_load(raw.decode("utf-8")) or {})
    cfg["_sha256"] = hashlib.sha256(raw).hexdigest()
    cfg["_path"] = CONFIG_RELPATH
    return cfg


def system(cfg: dict, sid: str) -> dict | None:
    return next((s for s in cfg.get("systems") or [] if s.get("id") == sid), None)


def digest_of_system(cfg: dict, sid: str) -> str:
    """The SHA-256 of what decides a system's collection: its own block (endpoints, marking, org, credential
    references), the mapping and the collectors' budgets. A publication binds it."""
    from tools.idp.dora import digest

    s = system(cfg, sid) or {}
    return digest({"system": s, "mapping": cfg.get("mapping") or [], "collectors": cfg.get("collectors") or {}})
