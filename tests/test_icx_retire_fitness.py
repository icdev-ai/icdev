# CUI // SP-CTI
"""icx-rtr-01 -- 7R retire fitness reads a MEASURED redundancy, and its criteria are the catalog's own.

icx-mdz-04: that redundancy is ICDEV[OS]'s own measurement (osx-mdz-02), read only from its SIGNED decisions export;
the unsigned redundancy file this module once read is gone.

The old check returned low_usage/redundant_functionality/no_active_development = 0.5 under names no catalog
criterion has, so ``_score_strategy`` scored them POSITIONALLY as other criteria: redundant_functionality counted
as data_disposition, and every application looked half-retirable on no evidence at all.
"""
from __future__ import annotations

import json

from tools.modernization import seven_r_assessor as sr

PROFILE = {"framework": "spring", "framework_version": "5.3", "primary_language": "java", "language_version": "17"}


def _retire_criteria():
    return next(s for s in sr.load_seven_rs_catalog()["strategies"] if s["id"] == "retire")["scoring_criteria"]


def test_retire_checks_are_keyed_by_the_catalogs_criteria():
    crit = _retire_criteria()
    assert set(sr._check_retire_fitness(PROFILE)) == set(crit), "no positional fallback for retire"
    assert abs(sum(c["weight"] for c in crit.values()) - 1.0) < 1e-9


def test_unmeasured_is_left_out_never_half():
    got = sr._check_retire_fitness(PROFILE)
    assert got["functional_redundancy"] is None and got["dependency_analysis"] is None
    assert got["risk_reduction"] == 0.0, "spring 5 / java 17 is not end-of-life"
    # with only risk_reduction measured, the score is exactly it -- not pulled towards 0.5 by unmeasured criteria
    assert sr._score_strategy("retire", got, _retire_criteria()) == 0.0


def test_a_measured_redundancy_moves_retire():
    crit = _retire_criteria()
    none = sr._score_strategy("retire", sr._check_retire_fitness(PROFILE), crit)
    high = sr._score_strategy("retire", sr._check_retire_fitness(PROFILE, {"share": 0.9, "source": "icos overlap"}), crit)
    w_r, w_k = crit["functional_redundancy"]["weight"], crit["risk_reduction"]["weight"]
    assert none == 0.0 and abs(high - (0.9 * w_r) / (w_r + w_k)) < 1e-9
    assert sr._check_retire_fitness(PROFILE, {"share": 4})["functional_redundancy"] == 1.0, "clamped to [0, 1]"


def _signed(tmp_path, doc: dict):
    """``doc`` as ICDEV[OS] exports it -- a detached ES384 JWS over its JCS form -- and the JWKS pinning the test key."""
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    from tools.modernization import icos_decisions as icd

    key = ec.generate_private_key(ec.SECP384R1())
    protected = icd._b64u(json.dumps({"alg": icd.ALG, "typ": icd.TYP, "kid": "k1"}).encode())
    r, s = decode_dss_signature(key.sign(f"{protected}.{icd._b64u(icd.jcs(doc).encode())}".encode(),
                                         ec.ECDSA(hashes.SHA384())))
    export = tmp_path / "decisions.json"
    export.write_text(json.dumps({"document": doc, "signature": {
        "protected": protected, "signature": icd._b64u(r.to_bytes(48, "big") + s.to_bytes(48, "big"))}}))
    nums = key.public_key().public_numbers()
    b = lambda n: base64.urlsafe_b64encode(n.to_bytes(48, "big")).rstrip(b"=").decode()  # noqa: E731
    jwks = tmp_path / "icos_jwks.json"
    jwks.write_text(json.dumps({"keys": [{"kty": "EC", "crv": "P-384", "kid": "k1", "x": b(nums.x), "y": b(nums.y)}]}))
    return export, jwks


def _doc(rows):
    return {"schema": "icos-portfolio-decisions/v1", "issuer": "icos-test", "chain": {"verifies": True},
            "baselines": [], "cutovers": [], "strategies": [], "redundancy": rows}


ROW_A = {"application": "app-a", "marking": "internal", "criterion": "redundant_functionality", "value": 0.62,
         "state": "measured", "features": 5, "redundant": [{"feature": "cart", "counterpart": "app-b:basket"}],
         "unmeasured": [], "why": None, "method": "portfolio/v1 strategy against overlap (deterministic)"}
ROW_B = {"application": "app-b", "marking": "internal", "criterion": "redundant_functionality", "value": None,
         "state": "unmeasured", "features": 3, "redundant": [], "unmeasured": [{"feature": "x", "why": "no confirmed capability"}],
         "why": "1 of 3 feature(s) unmeasured: never a score", "method": "portfolio/v1 strategy against overlap (deterministic)"}


def test_retire_reads_icos_measured_redundancy_only_from_the_signed_export(tmp_path, monkeypatch):
    """icx-mdz-04 (gap G2): retire's functional_redundancy is ICDEV[OS]'s MEASURED redundancy, from its signed decisions
    export verified against the pinned key -- an unmeasured or absent row, an unsigned or altered export, or none at all
    leaves the criterion unmeasured (None), never a score."""
    monkeypatch.delenv("ICDEV_ICOS_DECISIONS", raising=False)
    monkeypatch.delenv("ICDEV_ICOS_JWKS", raising=False)
    export, jwks = _signed(tmp_path, _doc([ROW_A, ROW_B]))
    got = sr.icos_redundancy("app-a", export=str(export), jwks=str(jwks))
    assert got["state"] == "measured" and got["share"] == 0.62 and got["criterion"] == "redundant_functionality"
    assert "icos-test" in got["source"]
    assert sr._check_retire_fitness(PROFILE, got)["functional_redundancy"] == 0.62
    un = sr.icos_redundancy("app-b", export=str(export), jwks=str(jwks))
    assert un["share"] is None and un["state"] == "unmeasured" and "never a score" in un["why"]
    assert sr._check_retire_fitness(PROFILE, un)["functional_redundancy"] is None
    none = sr.icos_redundancy("app-c", export=str(export), jwks=str(jwks))
    assert none["share"] is None and none["state"] == "unreported"
    assert sr.icos_redundancy("app-a")["state"] == "missing", "no export configured: unmeasured"
    signed = json.loads(export.read_text())
    signed["document"]["redundancy"][0]["value"] = 1.0                 # a share anyone could write
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(signed))
    bad = sr.icos_redundancy("app-a", export=str(forged), jwks=str(jwks))
    assert bad["share"] is None and bad["state"] == "untrusted"
    (tmp_path / "odd").mkdir()
    odd, jw2 = _signed(tmp_path / "odd", _doc([{**ROW_A, "value": 4}]))
    assert sr.icos_redundancy("app-a", export=str(odd), jwks=str(jw2))["share"] is None, "not a share in [0, 1]"
    assert not hasattr(sr, "load_redundancy"), "an unsigned redundancy file is no source"


def test_the_assessment_reads_the_configured_export_and_records_what_it_read(tmp_path, monkeypatch):
    """With no redundancy handed in, run_seven_r_assessment asks the export the operator configured."""
    export, jwks = _signed(tmp_path, _doc([ROW_A]))
    monkeypatch.setenv("ICDEV_ICOS_DECISIONS", str(export))
    monkeypatch.setenv("ICDEV_ICOS_JWKS", str(jwks))
    seen = {}
    real = sr._check_retire_fitness

    def spy(profile, redundancy=None):
        seen["redundancy"] = redundancy
        return real(profile, redundancy)
    monkeypatch.setattr(sr, "_get_app_profile", lambda app_id, db_path=None: dict(PROFILE, name="a"))
    monkeypatch.setitem(sr.FITNESS_CHECKS, "retire", spy)
    monkeypatch.setattr(sr, "_get_ui_complexity", lambda *a, **k: None)
    monkeypatch.setattr(sr, "_persist_assessment", lambda assessment, db_path=None: None)
    out = sr.run_seven_r_assessment("p", "app-a")
    assert seen["redundancy"]["share"] == 0.62 and seen["redundancy"]["state"] == "measured"
    ev = json.loads(out["evidence"])
    assert ev["redundancy"]["state"] == "measured" and ev["fitness_results"]["retire"]["functional_redundancy"] == 0.62
    assert "functional_redundancy" not in ev["strategies"]["retire"]["unmeasured"]


def test_the_mcp_tool_passes_the_signed_redundancy_through(tmp_path, monkeypatch):
    from tools.mcp import modernization_server as ms
    seen = {}
    monkeypatch.delenv("ICDEV_ICOS_DECISIONS", raising=False)
    monkeypatch.delenv("ICDEV_ICOS_JWKS", raising=False)

    def fake_assess(project_id, app_id, custom_weights=None, redundancy=None):
        seen.update(app=app_id, redundancy=redundancy)
        return {"ok": True}
    real = ms._import_tool
    monkeypatch.setattr(ms, "_import_tool", lambda mod, fn: fake_assess if fn == "run_seven_r_assessment" else real(mod, fn))
    export, jwks = _signed(tmp_path, _doc([ROW_A]))
    args = {"project_id": "p", "app_id": "app-a", "icos_decisions": str(export), "icos_jwks": str(jwks)}
    assert ms.handle_assess_seven_r(args) == {"ok": True}
    assert seen["app"] == "app-a" and seen["redundancy"]["share"] == 0.62
    assert ms.handle_assess_seven_r({"project_id": "p", "app_id": "app-a"}) == {"ok": True}
    assert seen["redundancy"]["share"] is None and seen["redundancy"]["state"] == "missing"
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"applications": {"app-a": {"share": 0.9}}}), encoding="utf-8")
    assert ms.handle_assess_seven_r({"project_id": "p", "app_id": "app-a", "redundancy_path": str(p)}) == {"ok": True}
    assert seen["redundancy"]["share"] is None, "an unsigned file is not read"
