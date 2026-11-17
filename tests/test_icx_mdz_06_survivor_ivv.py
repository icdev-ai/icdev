# CUI // SP-CTI
"""IV&V for survivor assurance (icx-mdz-06).

Covers:
  * the run is ICDEV's own IV&V (``ivv_assessor.evaluate``, IEEE 1012) over the application's modernisation outputs, and
    the result names every area pass, fail or unverified, its findings, each requirement's evidence digest, the outputs'
    manifest digest, the tool and its version, and the date, bound by one digest; it is recorded append-only and read
    back, and a tampered record is refused;
  * an area with anything not verified automatically (not assessed, manual, semi-automated) is unverified, NEVER pass;
    with no outputs every area is; CERTIFY only when every area passed;
  * independence is a person: the sign-off is a declared step (``decl-wft-mdz-survivor-ivv-signoff``) refused to anyone
    who built or approved the application's plan -- in ICDEV (its 7R record, a CAM job review, its capacity or a process
    review) or as ICDEV[OS] named them in a may-plan it allowed -- bound to the result's digest and taken once; a step
    whose caller names no builders is refused;
  * a newer result after the sign-off leaves it stale, and the bundle stops carrying the IV&V;
  * the bundle carries only a signed, current result, in ICDEV[OS] 1.189.0's own ``ivv`` section, and says why
    otherwise; it validates against the pinned schema either way;
  * the page, the twin (finding with a thread to the result, replays) and the read-only MCP tool.

Run: pytest tests/test_icx_mdz_06_survivor_ivv.py -v
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tests.test_icx_mdz_03_bundle_features as mdz03
from tests.test_icx_mdz_03_bundle_features import _bundle_evidence, _client, _conn, _report

shop, canvas = mdz03.shop, mdz03.canvas      # icx-mdz-03's fixtures (a synthetic shop, the migration canvas), reused

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-11-16T00:00:00Z"
PAGE = "tools/dashboard/templates/migration_canvas/modernisation.html"
SIGN = "/migration-canvas/api/modernisation/apps/app-1/ivv/signoff"
#: Labelled test persons (synthetic): an independent ISSO, an admin who reviewed the plan, an AO named by ICDEV[OS].
ISSO = {"id": "u-mdz06-isso", "role": "isso"}
LEAD = {"id": "u-mdz06-lead", "role": "admin"}
AO = {"id": "u-mdz06-ao", "role": "isso"}
DEV = {"id": "u-mdz06-dev", "role": "developer"}


def _outputs(tmp_path) -> Path:
    """Modernised code as a CAM job would leave it (synthetic)."""
    out = tmp_path / "outputs"
    (out / "src").mkdir(parents=True)
    (out / "src" / "cart.py").write_text("def add(cart, item):\n    return cart + [item]\n", encoding="utf-8")
    (out / "Containerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    return out


def _run(tmp_path, **kw):
    from tools.modernization import survivor_ivv

    return survivor_ivv.run("app-1", target_dir=str(_outputs(tmp_path)), **kw)


def _rows(*specs):
    """Requirement rows for one area: (id, status, automation)."""
    return [{"id": i, "title": i, "priority": "high", "automation": a, "status": s, "evidence_digest": "0" * 64}
            for i, s, a in specs]


# ── the result, pure ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("rows, want", [
    ((("R1", "pass", "auto"), ("R2", "pass", "auto")), "pass"),
    ((("R1", "pass", "auto"), ("R2", "pass", "semi")), "unverified"),
    ((("R1", "pass", "auto"), ("R2", "not_assessed", "manual")), "unverified"),
    ((("R1", "pass", "auto"), ("R2", "deferred", "auto")), "unverified"),
    ((("R1", "not_applicable", "auto"),), "unverified"),
    ((("R1", "pass", "auto"), ("R2", "partial", "auto")), "fail"),
    ((("R1", "not_assessed", "manual"), ("R2", "fail", "auto")), "fail"),
    ((("R1", "pass", "auto"), ("R2", "not_applicable", "auto")), "pass"),
])
def test_an_unverified_area_never_passes(rows, want):
    from tools.modernization import survivor_ivv

    area = survivor_ivv._area("X", "Area", _rows(*rows), 75.0, [])
    assert area["status"] == want, area["why"]
    assert (area["score"] is None) == (want == "unverified"), "an unverified area carries no score"


def test_the_run_is_ivv_assessor_over_the_outputs_and_names_everything(shop, canvas, tmp_path, monkeypatch):
    from tools.compliance import ivv_assessor
    from tools.modernization import survivor_ivv

    seen = []
    real = ivv_assessor.evaluate
    monkeypatch.setattr(ivv_assessor, "evaluate", lambda *a, **k: seen.append((a, k)) or real(*a, **k))
    r = _run(tmp_path)
    assert seen and seen[0][0][1] == str((tmp_path / "outputs").resolve()), "ICDEV's own IV&V, over the outputs"
    catalogue = json.loads((ROOT / "context/compliance/ivv_requirements.json").read_text(encoding="utf-8"))
    assert sorted(q["id"] for a in r["areas"] for q in a["requirements"]) == \
        sorted(q["id"] for q in catalogue["requirements"]), "every requirement, in its area"
    assert {a["code"] for a in r["areas"]} == set(ivv_assessor.PROCESS_AREA_CODES.values())
    assert all(re.fullmatch(r"[0-9a-f]{64}", q["evidence_digest"]) for a in r["areas"] for q in a["requirements"])
    assert r["target"]["basis"] == "declared" and r["target"]["files"] == 2 and len(r["target"]["digest"]) == 64
    assert re.fullmatch(r"1\.0\+sha256\.[0-9a-f]{12}", r["tool_version"]) and r["tool"] == "icdev-it/ivv_assessor"
    assert datetime.strptime(r["assessed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= \
        datetime.now(timezone.utc)
    assert r["expires_at"] > r["assessed_at"] and r["digest"] == survivor_ivv.digest(r)
    assert r["recommendation"] in ("CONDITIONAL", "DENY"), "a manual requirement is never verified by a machine"
    assert r["conditions"] and all(a["status"] in ("pass", "fail", "unverified") for a in r["areas"])
    assert r["subject"]["inventory_digest"] == _client(canvas).get(
        "/migration-canvas/api/modernisation/apps/app-1/bundle").get_json()["inventory_digest"]
    # recorded append-only and read back, whole
    back = survivor_ivv.latest("app-1")
    assert back["digest"] == r["digest"] and back["audit_id"] == r["audit_id"] and back["areas"] == r["areas"]
    with sqlite3.connect(str(shop)) as c:
        (event, actor), = c.execute("SELECT event_type, actor FROM audit_trail WHERE id = ?", (r["audit_id"],))
    assert (event, actor) == ("compliance_check", "icdev-it/ivv_assessor")


def test_a_tampered_record_is_refused(shop, canvas, tmp_path):
    from tools.modernization import survivor_ivv

    r = _run(tmp_path)
    forged = {**r, "recommendation": "CERTIFY"}
    forged.pop("audit_id")
    with sqlite3.connect(str(shop)) as c:
        c.execute("INSERT INTO audit_trail (event_type, actor, action, details) VALUES (?, ?, ?, ?)",
                  ("compliance_check", "someone", f"{survivor_ivv.ACTION}:app-1", json.dumps(forged)))
    with pytest.raises(survivor_ivv.IvvError, match="does not match its own digest"):
        survivor_ivv.latest("app-1")


def test_with_no_outputs_every_area_is_unverified_and_nothing_is_certified(shop, canvas):
    from tools.modernization import survivor_ivv

    r = survivor_ivv.run("app-1")
    assert r["target"]["path"] is None and "no modernisation outputs" in r["target"]["why"]
    assert {a["status"] for a in r["areas"]} == {"unverified"} and r["recommendation"] == "CONDITIONAL"
    assert all(a["score"] is None for a in r["areas"])


def test_certify_only_when_every_area_passed_and_deny_on_a_critical_finding(shop, canvas, monkeypatch):
    from tools.compliance import ivv_assessor
    from tools.modernization import survivor_ivv

    def fake(status):
        def evaluate(project_id, project_dir=None, **_):
            reqs = json.loads((ROOT / "context/compliance/ivv_requirements.json").read_text())["requirements"]
            results = [{"requirement_id": q["id"], "process_area": q["process_area"], "title": q["title"],
                        "priority": q.get("priority", "medium"), "automation_level": "auto",
                        "status": status, "evidence": "e", "details": ""} for q in reqs]
            findings = [] if status == "pass" else [{"finding_id": "F-1", "severity": "critical", "process_area":
                                                     reqs[0]["process_area"], "title": "t", "recommendation": "r"}]
            return {"results": results, "findings": findings, "scores": {"area_scores": {}, "overall_score": 100.0},
                    "gate_result": {"critical_findings_count": len(findings)}}
        return evaluate

    conn = _conn()
    try:
        monkeypatch.setattr(ivv_assessor, "evaluate", fake("pass"))
        assert survivor_ivv.assess("app-1", conn)["recommendation"] == "CERTIFY"
        monkeypatch.setattr(ivv_assessor, "evaluate", fake("fail"))
        assert survivor_ivv.assess("app-1", conn)["recommendation"] == "DENY"
    finally:
        conn.close()


# ── the person's sign-off ────────────────────────────────────────────────────


def _took(actor: dict, template: str, payload: dict, subject: str = "s-1"):
    from tools.workflow_hitl import declared

    tmpl = declared.load_templates()[template]
    return declared.take_step(template, surface=tmpl["decides"][0], decision=next(iter(
        declared.manual_stage(tmpl)["decisions"])), subject=subject, reason="mdz06 test (synthetic)", actor=actor,
        payload=payload)


def test_signoff_by_the_plans_own_approver_is_refused(shop, canvas, tmp_path):
    from tools.audit.audit_logger import log_event
    from tools.modernization import plan_gate

    r = _run(tmp_path)
    body = {"decision": "sign", "reason": "mdz06 test: independent review (synthetic)", "digest": r["digest"]}
    _took(LEAD, "decl-wft-mdz-capacity-review", {"app_id": "app-1", "digest": "c" * 64}, "capacity:Shop")
    out = _client(canvas, LEAD).post(SIGN, json=body)
    assert out.status_code == 403 and out.get_json()["code"] == "separation", out.get_json()
    assert "reviewed its team capacity" in out.get_json()["error"]
    # the person ICDEV[OS] named as approving the plan, in a may-plan ICDEV recorded
    log_event("approval_granted", actor="test", action=f"{plan_gate.ALLOWED}:app-1",
              details={"application": "app-1", "status": "allowed", "approved_by": AO["id"]}, raise_on_error=True)
    out = _client(canvas, AO).post(SIGN, json=body)
    assert out.status_code == 403 and "approved the change that plans it in ICDEV[OS]" in out.get_json()["error"]
    seen = _client(canvas).get("/migration-canvas/api/modernisation/apps/app-1/ivv").get_json()
    assert set(seen["separate_from"]) == {LEAD["id"], AO["id"]} and seen["signoff"] == {"state": "unsigned"}
    # someone independent of both signs it
    ok = _client(canvas, ISSO).post(SIGN, json=body)
    assert ok.status_code == 200, ok.get_data(as_text=True)
    assert ok.get_json()["signoff"] == {"state": "signed", "decision": "sign", "by": ISSO["id"],
                                        "result_digest": r["digest"]}


def test_the_7r_record_cam_review_and_process_review_make_a_builder(shop, canvas):
    from tools.modernization import survivor_ivv

    _took({"id": "u-7r", "role": "admin"}, "decl-wft-mdz-7r-strategy", {"legacy_app_id": "app-1", "strategy": "rehost"})
    _took({"id": "u-cam", "role": "admin"}, "decl-wft-mdz-cam-refactor-approve", {"project_id": "mcp-app-1"}, "job-1")
    _took({"id": "u-proc", "role": "admin"}, "decl-wft-mdz-process-review", {"app_id": "app-1"}, "app-1:p")
    _took({"id": "u-other", "role": "admin"}, "decl-wft-mdz-process-review", {"app_id": "app-2"}, "app-2:p")
    assert survivor_ivv.builders("app-1") == {"u-7r": "recorded its 7R strategy",
                                             "u-cam": "reviewed a CAM job's output for it",
                                             "u-proc": "reviewed one of its processes"}


def test_the_signoff_is_a_declared_step_bound_to_the_digest_and_taken_once(shop, canvas, tmp_path):
    from tools.modernization import survivor_ivv
    from tools.workflow_hitl import declared

    assert _client(canvas, ISSO).post(SIGN, json={"decision": "sign", "reason": "x", "digest": "0"}).status_code == 404, \
        "nothing run: nothing to sign"
    r = _run(tmp_path)
    body = {"decision": "sign", "reason": "mdz06 test: independent review (synthetic)", "digest": r["digest"]}
    assert _client(canvas, ISSO).post(SIGN, json={**body, "reason": ""}).status_code == 400
    assert _client(canvas, DEV).post(SIGN, json=body).status_code == 403, "a developer does not hold it"
    assert _client(canvas, ISSO).post(SIGN, json={**body, "digest": "0" * 64}).status_code == 409
    assert _client(canvas, ISSO).post("/migration-canvas/api/modernisation/apps/nope/ivv/signoff",
                                      json=body).status_code == 404
    out = _client(canvas, ISSO).post(SIGN, json={**body, "decision": "refuse"})
    assert out.status_code == 200 and out.get_json()["signoff"]["state"] == "refused"
    assert out.get_json()["decision_step"]["template_id"] == "decl-wft-mdz-survivor-ivv-signoff"
    assert _client(canvas, {"id": "u-mdz06-isso2", "role": "reviewer"}).post(SIGN, json=body).status_code == 409, \
        "decided once at a digest"
    # the engine itself refuses a builders step whose caller names no one
    with pytest.raises(declared.StepRefused) as exc:
        declared.take_step(survivor_ivv.TEMPLATE, surface=survivor_ivv.SURFACE, decision="sign", reason="x",
                           subject=survivor_ivv.subject("app-1"), actor=ISSO, payload=survivor_ivv.payload(r))
    assert exc.value.code == "undeclared" and "independence cannot be judged" in exc.value.message


def test_the_template_is_declared_with_separation_from_builders():
    from tools.modernization import decisions, survivor_ivv
    from tools.workflow_hitl import declared

    t = declared.load_templates()[survivor_ivv.TEMPLATE]
    assert declared.validate_template(t) == [] and decisions.TEMPLATE_IVV == survivor_ivv.TEMPLATE
    stage = declared.manual_stage(t)
    assert stage["separate_from"] == "builders" and set(stage["reason_required"]) == {"sign", "refuse"}
    assert "developer" not in stage["holders"] and "migration_engineer" not in stage["holders"]
    assert t["decides"] == [survivor_ivv.SURFACE]
    assert declared.validate_template({**t, "stages": [{**s, "separate_from": "anyone"} if s is stage else s
                                                       for s in t["stages"]]})


# ── a changed result, and the bundle ─────────────────────────────────────────


def _bundle():
    from tools.modernization import apptwin_export as ax
    from tools.modernization import survivor_ivv

    conn = _conn()
    try:
        return ax.build("app-1", conn, at=AT, extract=_report(), survivor_ivv=survivor_ivv.for_bundle("app-1", conn))
    finally:
        conn.close()


def test_the_bundle_carries_only_a_signed_current_result_and_always_validates(shop, canvas, tmp_path):
    from tools.modernization import apptwin_export as ax

    b = _bundle()
    assert b["ivv"] is None and "no survivor IV&V has run" in b["sections"]["ivv"]["reason"] and ax.validate(b) == []
    r = _run(tmp_path)
    b = _bundle()
    st = b["sections"]["ivv"]
    assert b["ivv"] is None and st["state"] == "missing" and st["reason"].startswith("unsigned:")
    assert r["digest"][:12] in st["reason"] and ax.validate(b) == []
    body = {"decision": "sign", "reason": "mdz06 test: independent review (synthetic)", "digest": r["digest"]}
    assert _client(canvas, ISSO).post(SIGN, json=body).status_code == 200
    b = _bundle()
    assert ax.validate(b) == [], "the pinned contract (1.192.0) and ICDEV[OS]'s agreement rules"
    iv = b["ivv"]
    assert (iv["kind"], iv["assessor"], iv["recommendation"]) == ("independent", ISSO["id"], r["recommendation"])
    assert iv["assessed_at"] == r["assessed_at"] and iv["expires_at"] == r["expires_at"]
    assert iv["tool"] == f"icdev-it/ivv_assessor@{r['tool_version']}"
    assert set(iv["areas"]) == {a["code"] for a in r["areas"] if a["status"] != "unverified"}, \
        "only a measured area has a score: unverified is never passed"
    assert iv["subject"]["inventory_digest"] == ax.inventory_digest(b), "bound to what it assessed"
    assert b["sections"]["ivv"]["state"] == "present" and f"signed by {ISSO['id']}" in b["sections"]["ivv"]["reason"]
    # osx-mdz-09 (ICDEV[OS] 1.190.0): every area's status, the findings, the evidence and the sign-off, as fields
    assert iv["area_status"] == {a["code"]: {"status": a["status"], "name": a["name"], "why": a["why"]} for a in r["areas"]}
    assert set(iv["areas"]) <= set(iv["area_status"]), "ICDEV[OS] refuses a score with no status"
    assert [f["id"] for f in iv["findings"]] == [f["finding_id"] for f in r["findings"]]
    assert all(f["area"] in iv["area_status"] for f in iv["findings"]), "a finding names its area by code"
    ev = iv["evidence"]
    assert ev["result_digest"] == r["digest"] and ev["outputs_digest"] == r["target"]["digest"]
    assert ev["outputs_files"] == r["target"]["files"] == 2
    assert ev["requirements"] == {q["id"]: q["evidence_digest"] for a in r["areas"] for q in a["requirements"]}
    assert iv["tool_version"] == r["tool_version"]
    so = iv["signoff"]
    assert (so["signed_by"], so["result_digest"], so["role"]) == (ISSO["id"], r["digest"], "isso")
    assert so["step_id"].startswith("step-") and so["decided_at"] >= iv["assessed_at"], "signed after it was assessed"


def test_a_newer_result_after_the_signoff_leaves_it_stale_and_uncarried(shop, canvas, tmp_path):
    import time

    from tools.modernization import apptwin_export as ax
    from tools.modernization import survivor_ivv

    r = _run(tmp_path)
    body = {"decision": "sign", "reason": "mdz06 test: independent review (synthetic)", "digest": r["digest"]}
    assert _client(canvas, ISSO).post(SIGN, json=body).status_code == 200
    assert _bundle()["ivv"] is not None
    time.sleep(1.1)                                   # a new run, a new date: a new result
    (tmp_path / "outputs" / "src" / "order.py").write_text("ORDER = 1\n", encoding="utf-8")
    r2 = survivor_ivv.run("app-1", target_dir=str(tmp_path / "outputs"))
    assert r2["digest"] != r["digest"] and r2["target"]["digest"] != r["target"]["digest"]
    so = survivor_ivv.signoff("app-1", survivor_ivv.latest("app-1"))
    assert so["state"] == "stale" and so["result_digest"] == r["digest"]
    b = _bundle()
    assert b["ivv"] is None and "not the current" in b["sections"]["ivv"]["reason"] and ax.validate(b) == []
    assert _client(canvas, ISSO).post(SIGN, json=body).status_code == 409, "the old digest is not what is there now"
    assert _client(canvas, ISSO).post(SIGN, json={**body, "digest": r2["digest"]}).status_code == 200
    assert _bundle()["ivv"]["assessed_at"] == r2["assessed_at"]


def test_the_cli_runs_and_shows(shop, canvas, tmp_path, capsys):
    from tools.modernization import survivor_ivv

    assert survivor_ivv.main(["--app-id", "app-1", "--target-dir", str(_outputs(tmp_path))]) == 0
    ran = json.loads(capsys.readouterr().out)
    assert survivor_ivv.main(["--app-id", "app-1", "--show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["result"]["digest"] == ran["digest"] and shown["signoff"] == {"state": "unsigned"}
    assert survivor_ivv.main(["--app-id", "nope"]) == 1


# ── the page, the twin, MCP ──────────────────────────────────────────────────


def test_the_page_shows_the_result_per_area_and_offers_sign_and_refuse():
    text = (ROOT / PAGE).read_text(encoding="utf-8")
    for needle in ('id="mdz-ivv"', "migration_canvas.api_modernisation_survivor_ivv",
                   "migration_canvas.api_modernisation_ivv_signoff", "data-sign-ivv", "data-refuse-ivv",
                   "mdz-ivv-areas", "evidence_digest", "tool_version", "never a pass",
                   "decl-wft-mdz-survivor-ivv-signoff", "May not sign", "mdz-ivv-bundle"):
        assert needle in text, needle
    assert "/api/" not in text, "routes by endpoint id"
    assert (ROOT / "icdev" / PAGE).read_text(encoding="utf-8") == text, "not mirrored to icdev/"


def test_the_twin_flags_unsigned_refused_and_unpassed_areas_with_a_thread():
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    def bundle(app, ivv, reason=None):
        return {"app": app, "name": app.title(), "valid": True, "error": None, "features": 1, "processes": [],
                "missing": {}, "ivv": ivv, "ivv_reason": reason}

    def result(audit, state, areas):
        return {"audit_id": audit, "digest": "d" * 64, "assessed_at": AT, "recommendation": "CONDITIONAL",
                "areas": areas, "signoff": {"state": state, "by": "u-x", "step_id": "step-1"}}

    ev = _bundle_evidence(bundles=[
        bundle("app-1", result(11, "unsigned", {"REQ": "pass"}), "unsigned: no independent sign-off"),
        bundle("app-2", result(12, "stale", {"REQ": "pass", "SEC": "unverified"})),
        bundle("app-3", result(13, "refused", {"REQ": "fail"})),
        bundle("app-4", result(14, "signed", {"REQ": "pass"})),
        bundle("app-5", None),
        bundle("app-6", {"error": "no audit trail"})])
    out = ModernisationGovernanceTwin().judge(ev)
    found = {(f["rule_id"], f["thread_id"]) for f in out["findings"] if f["rule_id"].startswith("mdz.ivv.")}
    assert found == {("mdz.ivv.unsigned", "audit_trail:11"), ("mdz.ivv.unsigned", "audit_trail:12"),
                     ("mdz.ivv.areas_not_passed", "audit_trail:12"), ("mdz.ivv.refused", "audit_trail:13"),
                     ("mdz.ivv.areas_not_passed", "audit_trail:13"), ("mdz.ivv.unreadable", "legacy_app:app-6")}, \
        "signed and all passed, or never run: nothing to say"
    stale = next(f for f in out["findings"] if f["thread_id"] == "audit_trail:12" and f["rule_id"] == "mdz.ivv.unsigned")
    assert "earlier result" in stale["title"]
    assert out["summary"]["survivor_ivv_results"] == 4 and out["summary"]["survivor_ivv_signed"] == 1


def test_the_twin_observes_an_unsigned_result_with_a_thread_and_replays(shop, canvas, tmp_path):
    from tools.twin_core import governance

    r = _run(tmp_path)
    adapter = governance.adapter_for("mdc")
    env = governance.observe(adapter)
    threads = {(v["rule_id"], v["thread_id"]) for v in env["violations"]}
    assert ("mdz.ivv.unsigned", f"audit_trail:{r['audit_id']}") in threads
    replayed = governance.replay(adapter, env["extra"]["observation_id"])
    assert replayed["match"] is True, replayed["reason"]


def test_the_mcp_survivor_ivv_tool_is_read_only_and_says_why(shop, canvas, tmp_path):
    from tools.mcp import gap_handlers
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert "modernisation_survivor_ivv" in set(list_tools())
    assert READ_ONLY_DECLARATIONS["modernisation_survivor_ivv"] is True
    assert tool_authorization("modernisation_survivor_ivv")["tier"] == "read"
    assert gap_handlers.handle_modernisation_survivor_ivv({}) == {"error": "app_id is required"}
    assert "no legacy application" in gap_handlers.handle_modernisation_survivor_ivv({"app_id": "nope"})["error"]
    none = gap_handlers.handle_modernisation_survivor_ivv({"app_id": "app-1"})
    assert none["result"] is None and "no survivor IV&V has run" in none["bundle"]["reason"]
    r = _run(tmp_path)
    out = gap_handlers.handle_modernisation_survivor_ivv({"app_id": "app-1"})
    assert out["result"]["digest"] == r["digest"] and out["signoff"] == {"state": "unsigned"}
    assert out["bundle"]["carried"] is False and out["bundle"]["reason"].startswith("unsigned:")
    assert all(len(d) == 64 for a in out["result"]["areas"] for d in a["evidence"].values())
    assert json.dumps(out)
