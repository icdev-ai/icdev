# CUI // SP-CTI
"""icx-dec-01 -- ICDEV[OS]'s signed portfolio decisions, verified before a migration is planned.

tools/modernization/icos_decisions.py verifies a REAL export (tests/fixtures/icos/decisions.signed.json, signed by
ICDEV[OS]'s own code: one baseline retiring syn-supply-c into syn-supply-a, one cutover) against its pinned JWKS; a
changed document, an unpinned key or a broken chain is untrusted; may-plan and may-retire answer from what verified.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.modernization import icos_decisions as dec

FIX = Path(__file__).resolve().parent / "fixtures" / "icos"


def _signed() -> dict:
    return json.loads((FIX / "decisions.signed.json").read_text(encoding="utf-8"))


def _keys() -> dict:
    return dec.pinned(json.loads((FIX / "icos_jwks.json").read_text(encoding="utf-8")))


def test_a_real_icdev_os_export_verifies_and_answers():
    doc = dec.verify(_signed(), _keys())
    plan = dec.may_plan(doc, "syn-supply-c")
    assert plan["allowed"] and plan["role"] == "departing" and plan["approved_by"] == "ao" and plan["baseline"] == 1
    assert dec.may_plan(doc, "syn-supply-a")["role"] == "surviving"
    assert not dec.may_plan(doc, "syn-isr-e")["allowed"]
    assert dec.may_retire(doc, "syn-supply-c")["allowed"] and not dec.may_retire(doc, "syn-supply-a")["allowed"]


def test_a_changed_export_an_unpinned_key_or_a_broken_chain_is_untrusted():
    forged = _signed()
    forged["document"]["baselines"][0]["changes"][0]["departing"] = ["syn-isr-e"]
    with pytest.raises(dec.Untrusted, match="does not verify"):
        dec.verify(forged, _keys())
    with pytest.raises(dec.Untrusted, match="not a pinned"):
        dec.verify(_signed(), {})
    broken = json.loads((FIX / "decisions.broken.signed.json").read_text(encoding="utf-8"))   # ICDEV[OS] signed it
    with pytest.raises(dec.Untrusted, match="audit chain did not verify"):                  # honestly: no decisions
        dec.verify(broken, _keys())


def test_jcs_is_rfc_8785():
    assert dec.jcs({"b": [1, 2.5, None, True], "a": "é", "€": 1e21, "c": 1e-7}) == \
        '{"a":"é","b":[1,2.5,null,true],"c":1e-7,"€":1e+21}'
    assert dec.jcs(1794100000.0) == "1794100000" and dec.jcs(0.1) == "0.1"


def test_the_cli_exits_by_what_it_may_do(tmp_path, capsys):
    args = ["--export", str(FIX / "decisions.signed.json"), "--jwks", str(FIX / "icos_jwks.json")]
    assert dec.main([*args, "may-plan", "--app", "syn-supply-c"]) == 0
    assert dec.main([*args, "may-retire", "--app", "syn-supply-a"]) == 1
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps(copy.deepcopy(_signed()) | {"signature": {"protected": "e30", "signature": "AA"}}))
    assert dec.main(["--export", str(bad), "--jwks", str(FIX / "icos_jwks.json"), "may-plan", "--app", "syn-supply-c"]) == 2
    assert '"status": "untrusted"' in capsys.readouterr().out
