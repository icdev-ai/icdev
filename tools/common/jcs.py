# CUI // SP-CTI
"""RFC 8785 (JSON Canonicalization Scheme) and base64url, one copy (icx-ent-04 review M4).

Every ICDEV[IT] reader or signer of an ICDEV[OS] JWS over canonical JSON uses this: the portfolio decisions verifier
(``tools/modernization/icos_decisions.py``) and the store publisher's Agent Card signer (``tools/store_publisher/card.py``).
``error`` is the exception a caller's contract raises for what JCS cannot represent (an integer outside the IEEE-754
safe range, a non-finite number, a value that is not JSON).

    jcs(value, error=ValueError) -> str     keys by UTF-16 code units, ECMAScript number form, no whitespace
    b64u(bytes) -> str / unb64u(str) -> bytes
"""
from __future__ import annotations

import base64
import json
import math

__all__ = ["b64u", "jcs", "num", "unb64u"]


def num(x, error=ValueError) -> str:
    """An ECMAScript Number::toString of ``x`` on its shortest round-trip digits (``repr``)."""
    if isinstance(x, bool):
        raise TypeError("bool is not a number")
    if isinstance(x, int):
        if abs(x) > 2 ** 53:
            raise error("integer outside the IEEE-754 safe range")
        return str(x)
    if not math.isfinite(x):
        raise error("non-finite number")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    mant, _, exp = repr(abs(x)).partition("e")
    digits, _, frac = mant.partition(".")
    e = int(exp or 0) + len(digits)
    s = (digits + frac).lstrip("0")
    e -= len(digits + frac) - len(s)
    s = s.rstrip("0") or "0"
    k, n = len(s), e
    if k <= n <= 21:
        out = s + "0" * (n - k)
    elif 0 < n <= 21:
        out = s[:n] + "." + s[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + s
    else:
        out = (s[0] + ("." + s[1:] if k > 1 else "")) + "e" + ("+" if n - 1 >= 0 else "-") + str(abs(n - 1))
    return sign + out


def jcs(v, error=ValueError) -> str:
    """The RFC 8785 canonical JSON of ``v``. Strings use JSON's escapes for '"', '\\' and control characters only."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return num(v, error)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ",".join(jcs(x, error) for x in v) + "]"
    if isinstance(v, dict):
        keys = sorted(v, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + jcs(v[k], error) for k in keys) + "}"
    raise error(f"not JSON: {type(v).__name__}")


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
