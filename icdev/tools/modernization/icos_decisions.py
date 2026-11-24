# CUI // SP-CTI
"""ICDEV[OS]'s signed portfolio decisions, verified before any migration is planned (icx-dec-01; ICDEV[OS] Phase 166).

When a legacy application belongs to an ICDEV[OS] application portfolio, the strategy is not approved here: it is a
portfolio baseline that ICDEV[OS]'s mission owner endorsed and AO approved (``portfolio-change``), and a retirement is a
cutover two more people signed (``portfolio-cutover``). ICDEV[OS] exports both, signed
(``GET /api/v1/portfolio/decisions/export``, ``icos-portfolio-decisions/v1``): a detached JWS, ES384, over the
document's RFC 8785 (JCS) canonical JSON, with the key its ``/oauth/jwks`` publishes.

This module trusts that export only after:

* the signature verifies against a PINNED ICDEV[OS] JWKS (a file the operator pinned once). The ``jku`` in the header is
  a hint, never a source of trust: a forger controls it too;
* the document says its audit chain verified when it was exported. An export from a broken chain carries no decisions,
  and is refused here as such.

Then it answers, deterministically:

    may-plan    an approved baseline names the application as departing or surviving a change, or ICDEV[OS]'s mission
                owner accepted its 7R strategy (``strategies``, ICDEV[OS] Phase 250): a migration may be planned for it
                (goals/modernization_workflow.md Step 5) -- with ``--strategy``, only the strategy accepted. Anything
                else: no.
    may-retire  a recorded cutover names the application as departing: it may be switched off. Anything else: no.
    redundancy  (icx-mdz-04; ICDEV[OS] osx-mdz-02) the application's MEASURED redundancy -- the share of its features a
                counterpart carries in a duplicate or partial overlap -- which 7R's retire fitness reads as
                ``functional_redundancy`` (gap G2). ``value`` null in the export is unmeasured, with ICDEV[OS]'s why; no
                row is unreported. Neither is ever a score.

    python tools/modernization/icos_decisions.py --export decisions.json --jwks icos_jwks.json may-plan --app <id>
    python tools/modernization/icos_decisions.py --export decisions.json --jwks icos_jwks.json may-retire --app <id>
    python tools/modernization/icos_decisions.py --export decisions.json --jwks icos_jwks.json redundancy --app <id>

JSON out; exit 0 allowed (redundancy: measured), 1 not allowed (unmeasured or unreported), 2 the export cannot be
trusted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- bootstrap the import root first.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.common import jcs as _jcs  # noqa: E402

SCHEMA = "icos-portfolio-decisions/v1"
TYP = "icos-portfolio-decisions+jws"
ALG = "ES384"


class Untrusted(ValueError):
    """The export cannot be relied on: not signed by a pinned key, changed, or from a broken chain."""


# --- RFC 8785 (JSON Canonicalization Scheme) and base64url: the one shared copy (tools/common/jcs.py) ---------------

def _num(x) -> str:
    return _jcs.num(x, Untrusted)


def jcs(v) -> str:
    """The RFC 8785 canonical JSON of ``v`` (keys sorted by UTF-16 code units, ECMAScript number form)."""
    return _jcs.jcs(v, Untrusted)


# --- the signature ---------------------------------------------------------------------------------

_b64u = _jcs.b64u
_unb64u = _jcs.unb64u


def pinned(jwks: dict) -> dict:
    """``{kid: public key}`` from a pinned ICDEV[OS] JWKS (P-384 EC keys only)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    out = {}
    for k in jwks.get("keys") or []:
        if k.get("kty") == "EC" and k.get("crv") == "P-384" and k.get("kid"):
            nums = ec.EllipticCurvePublicNumbers(int.from_bytes(_unb64u(k["x"]), "big"),
                                                 int.from_bytes(_unb64u(k["y"]), "big"), ec.SECP384R1())
            out[k["kid"]] = nums.public_key()
    return out


def verify(signed: dict, keys: dict) -> dict:
    """The document, once its signature verifies against a pinned key and its chain verified. Else Untrusted."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    try:
        doc, sig = signed["document"], signed["signature"]
        header = json.loads(_unb64u(sig["protected"]))
        raw = _unb64u(sig["signature"])
    except (KeyError, ValueError, TypeError) as exc:
        raise Untrusted("not a signed icos-portfolio-decisions export") from exc
    if header.get("alg") != ALG or header.get("typ") != TYP or doc.get("schema") != SCHEMA:
        raise Untrusted(f"unexpected export: {header.get('alg')}/{header.get('typ')}/{doc.get('schema')}")
    key = keys.get(header.get("kid", ""))
    if key is None:
        raise Untrusted(f"kid {header.get('kid')!r} is not a pinned ICDEV[OS] key")
    if len(raw) != 96:
        raise Untrusted("bad signature length")
    der = encode_dss_signature(int.from_bytes(raw[:48], "big"), int.from_bytes(raw[48:], "big"))
    message = f"{sig['protected']}.{_b64u(jcs(doc).encode())}".encode()
    try:
        key.verify(der, message, ec.ECDSA(hashes.SHA384()))
    except InvalidSignature as exc:
        raise Untrusted("the signature does not verify: the export was changed or signed by another key") from exc
    chain = doc.get("chain") or {}
    if chain.get("verifies") is not True:
        raise Untrusted(f"ICDEV[OS]'s audit chain did not verify at export (broken at {chain.get('broken_at')})")
    return doc


# --- the questions ---------------------------------------------------------------------------------

def accepted_strategy(doc: dict, app: str) -> dict | None:
    """The 7R strategy ICDEV[OS]'s mission owner accepted for ``app`` (``migration-assessment``, ICDEV[OS] Phase 250),
    the latest one; None when none is accepted."""
    rows = [x for x in doc.get("strategies") or [] if x.get("application") == app]
    return max(rows, key=lambda x: float(x.get("at") or 0)) if rows else None


def may_plan(doc: dict, app: str, strategy: str | None = None) -> dict:
    """Whether ``app`` may have a migration planned: an approved baseline decided its future (departing or surviving a
    change), or ICDEV[OS]'s mission owner accepted its strategy. With ``strategy``, it must be the one accepted."""
    acc = accepted_strategy(doc, app)
    if strategy and acc and acc.get("strategy") != strategy:
        return {"allowed": False, "application": app,
                "reason": f"ICDEV[OS] accepted {acc.get('strategy')} for this application, not {strategy}", "accepted": acc}
    if acc:
        return {"allowed": True, "application": app, "role": "strategy accepted", "strategy": acc.get("strategy"),
                "item": acc.get("item"), "accepted_by": acc.get("accepted_by"), "decisions": [acc.get("decision")]}
    for b in doc.get("baselines") or []:
        for c in b.get("changes") or []:
            role = "departing" if app in (c.get("departing") or []) else "surviving" if app in (c.get("survivors") or []) else None
            if role:
                return {"allowed": True, "application": app, "role": role, "baseline": b.get("version"),
                        "baseline_digest": b.get("digest"), "change": c.get("item"), "ops": c.get("ops"),
                        "endorsed_by": b.get("endorsed_by"), "approved_by": b.get("approved_by"),
                        "decisions": [b.get("endorsement"), b.get("decision")]}
    return {"allowed": False, "application": app,
            "reason": "no approved ICDEV[OS] portfolio baseline names this application: plan no migration for it"}


def may_retire(doc: dict, app: str) -> dict:
    """Whether a recorded cutover lets ``app`` be switched off."""
    for c in doc.get("cutovers") or []:
        if app in (c.get("departing") or []):
            return {"allowed": True, "application": app, "cutover": c.get("item"), "change": c.get("change"),
                    "survivors": c.get("survivors"), "parity": (c.get("parity") or {}).get("score"),
                    "confirmed_by": c.get("confirmed_by"), "approved_by": c.get("approved_by"),
                    "decisions": [c.get("confirmation"), c.get("decision")]}
    return {"allowed": False, "application": app,
            "reason": "no ICDEV[OS] cutover is recorded for this application: it may not be retired"}


def redundancy(doc: dict, app: str) -> dict:
    """``app``'s measured redundancy in a VERIFIED export (``redundancy`` rows, ICDEV[OS] ``duplicates.redundancy``):
    ``share`` is its ``value`` when ICDEV[OS] measured it, else None -- unmeasured (with ICDEV[OS]'s why) or unreported
    (no row). A share is never defaulted, and one outside [0, 1] is refused (an export saying it is not a measurement)."""
    src = f"ICDEV[OS] decisions export ({doc.get('issuer') or 'unknown issuer'})"
    rows = [r for r in doc.get("redundancy") or [] if isinstance(r, dict) and r.get("application") == app]
    if not rows:
        return {"share": None, "state": "unreported", "source": src,
                "why": "the export carries no measured redundancy for this application"}
    r = rows[-1]
    base = {"source": f"{src}: {r.get('method') or 'overlap'}", "criterion": r.get("criterion"),
            "features": r.get("features"), "redundant": r.get("redundant") or [], "unmeasured": r.get("unmeasured") or []}
    v = r.get("value")
    if v is None or r.get("state") not in (None, "measured"):
        return {**base, "share": None, "state": "unmeasured", "why": r.get("why") or "ICDEV[OS] did not measure it"}
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= float(v) <= 1.0:
        return {**base, "share": None, "state": "unmeasured", "why": f"the export's value {v!r} is not a share in [0, 1]"}
    return {**base, "share": float(v), "state": "measured"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify ICDEV[OS]'s signed portfolio decisions; may-plan / may-retire")
    ap.add_argument("--export", type=Path, required=True, help="the signed export (GET /api/v1/portfolio/decisions/export)")
    ap.add_argument("--jwks", type=Path, required=True, help="the ICDEV[OS] JWKS you PINNED (its /oauth/jwks, fetched once)")
    ap.add_argument("--json", action="store_true", help="JSON out (the default)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("may-plan", "may-retire", "redundancy"):
        sp = sub.add_parser(name)
        sp.add_argument("--app", required=True, help="the application's id in its icos-apptwin bundle")
        if name == "may-plan":
            sp.add_argument("--strategy", help="the plan's strategy: it must be the one ICDEV[OS] accepted, if one was")
    a = ap.parse_args(argv)
    try:
        doc = verify(json.loads(a.export.read_text(encoding="utf-8")), pinned(json.loads(a.jwks.read_text(encoding="utf-8"))))
    except (Untrusted, OSError, ValueError) as exc:
        print(json.dumps({"status": "untrusted", "error": str(exc)}))
        return 2
    if a.cmd == "redundancy":
        out = {"application": a.app, **redundancy(doc, a.app)}
        print(json.dumps({"status": "ok", "issuer": doc.get("issuer"), **out}, sort_keys=True))
        return 0 if out["state"] == "measured" else 1
    out = may_plan(doc, a.app, a.strategy) if a.cmd == "may-plan" else may_retire(doc, a.app)
    print(json.dumps({"status": "ok", "issuer": doc.get("issuer"), **out}, sort_keys=True))
    return 0 if out["allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
