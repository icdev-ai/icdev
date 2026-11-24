# CUI // SP-CTI
"""AWS Signature Version 4 for the cloud-configuration collector's read calls (icx-ent-02).

Standard library only (ICDEV does not depend on an AWS SDK). The algorithm is AWS's documented one
(https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv-create-signed-request.html): a canonical request,
its SHA-256 in a string to sign scoped to date/region/service, and an HMAC-SHA256 chain from the secret key. The
test pins it to AWS's published "get-vanilla" vector.
"""
from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone

ALGORITHM = "AWS4-HMAC-SHA256"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _enc(s: str, safe: str = "-_.~") -> str:
    return urllib.parse.quote(s, safe=safe)


def signing_key(secret: str, day: str, region: str, service: str) -> bytes:
    k = _hmac(("AWS4" + secret).encode("utf-8"), day)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def sign(method: str, url: str, headers: dict, body: bytes, *, access_key: str, secret_key: str, region: str,
         service: str, when: datetime | None = None, session_token: str | None = None) -> dict:
    """``headers`` plus ``X-Amz-Date`` (and ``X-Amz-Security-Token``) and ``Authorization``. Every header given is
    signed, with ``host``."""
    t = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
    amz_date, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    u = urllib.parse.urlsplit(url)
    out = dict(headers)
    out["X-Amz-Date"] = amz_date
    if session_token:
        out["X-Amz-Security-Token"] = session_token
    canon_h = {"host": u.netloc}
    for k, v in out.items():
        canon_h[k.lower()] = " ".join(str(v).strip().split())
    names = sorted(canon_h)
    query = sorted(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    canonical = "\n".join([
        method.upper(),
        "/".join(_enc(seg) for seg in (u.path or "/").split("/")) or "/",
        "&".join(f"{_enc(k)}={_enc(v)}" for k, v in query),
        "".join(f"{n}:{canon_h[n]}\n" for n in names),
        ";".join(names),
        hashlib.sha256(body or b"").hexdigest(),
    ])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join([ALGORITHM, amz_date, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    sig = hmac.new(signing_key(secret_key, day, region, service), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out["Authorization"] = f"{ALGORITHM} Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"
    return out
