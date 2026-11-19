# CUI // SP-CTI
"""AI-assisted vulnerability discovery, proven or dropped (icx-ent-01).

What the card promises, each clause a test here:

  * a target is scanned only when registered and authorised by a person who did not register it, under an allowed
    root, at its tree digest; a revoked, expired, changed or unregistered target is refused (the scope lock);
  * target code runs only in SandboxExecutor.run_isolated: no network, read-only root, capabilities dropped, a
    writable mount only under /out;
  * a candidate (SAST, SCA, the agentic review, a fuzz crash) becomes a finding ONLY with a proof of vulnerability
    that replays through reproduction_validator and discriminates against its benign control; every other
    candidate is dropped and counted; the agentic review only proposes; PoVs are sealed (never in a view);
  * a patch is proposed only when its PoV now fails and the tests pass on a copy, reviewed by a person who did not
    propose it, and never applied;
  * FP/FN rates are measured against planted labels, no model judging;
  * the icos-findings/v1 bundle validates against its pinned draft; one person prepares it, another publishes it;
    its publication travels beside it (the Academy shape) and verifies against the ledger;
  * the page, routes, twin (threads, replay) and MCP read and draft.

The sandbox here is a TEST DOUBLE (``LocalDouble``) that runs the SAME runner scripts with the container's mount
points mapped to temp directories, so the suite needs no container engine; the product never runs target code any
other way (``test_the_real_sandbox_end_to_end`` runs the real one where an engine and the pinned image are present).
SAST and SCA output is CANNED (synthetic bandit and pip-audit results over the benchmark), so CI without bandit or
network judges the same. People here are labelled test persons (synthetic); the benchmark is synthetic planted code.
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
BENCH = "context/vulnresearch/benchmark"
REGISTRANT = {"id": "u-ent01-registrant", "role": "reviewer"}     # labelled test persons (synthetic)
AUTHORISER = {"id": "u-ent01-authoriser", "role": "isso"}
DEVELOPER = {"id": "u-ent01-dev", "role": "developer"}


class LocalDouble:
    """Runs the sandbox runner scripts as a subprocess with /target, /harness and /out mapped (tests only)."""

    calls: list = []

    def run_isolated(self, argv, *, image, mounts, timeout_seconds=None, **_):
        m = {ctr: host for host, ctr, _mode in mounts}

        def mapped(a):
            for ctr in sorted(m, key=len, reverse=True):
                if a.startswith(ctr):
                    return m[ctr] + a[len(ctr):]
            return a

        cmd = [sys.executable if argv[0] == "python" else argv[0], *[mapped(a) for a in argv[1:]]]
        env = {**os.environ, "VR_TARGET": m.get("/target", ""), "VR_OUT": m.get("/out", "")}
        LocalDouble.calls.append({"argv": argv, "image": image, "mounts": mounts})
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds or 60, env=env,
                               cwd=m.get("/out") or m.get("/target"))
        except subprocess.TimeoutExpired:
            return SimpleNamespace(status="timeout", stdout="", stderr="", exit_code=-1, error="timeout")
        return SimpleNamespace(status="completed", stdout=p.stdout, stderr=p.stderr, exit_code=p.returncode, error="")


def _line(rel: str, needle: str) -> int:
    for i, line in enumerate((ROOT / BENCH / rel).read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not in {rel}")


def canned_bandit(project_path, **_):
    """SYNTHETIC bandit output over the benchmark, so the suite judges the same without bandit."""
    root = Path(project_path)
    hits = [("src/vb_cmdi.py", "shell=True", "B602"), ("src/vb_cmdi_safe.py", "import subprocess", "B404"),
            ("src/vb_cmdi_safe.py", "subprocess.run(", "B603"), ("src/vb_sqli.py", "SELECT name FROM users", "B608"),
            ("src/vb_sqli_safe.py", "SELECT name FROM", "B608"), ("src/vb_eval.py", "eval(", "B307"),
            ("src/vb_pickle.py", "import pickle", "B403"), ("src/vb_pickle.py", "pickle.loads", "B301")]
    if not (root / "src" / "vb_cmdi.py").exists():
        return {"success": True, "findings": [], "raw_output": ""}
    return {"success": True, "raw_output": "", "findings": [
        {"file": str(root / rel), "line": _line(rel, needle), "test_id": tid, "issue_text": f"synthetic {tid}"}
        for rel, needle, tid in hits]}


def canned_audit(project_path, **_):
    return {"success": True, "raw_output": "", "findings": [
        {"package": "jinja2", "version": "2.10", "vulnerability_id": "PYSEC-2019-217", "aliases": ["CVE-2019-10906"],
         "fix_versions": ["2.10.1"]}]}


@pytest.fixture()
def vr(wf_db, monkeypatch, tmp_path):
    """The conftest SQLite platform DB, the sandbox double, canned SAST/SCA, a private work dir, no model."""
    from tools.security import dependency_auditor, sast_runner
    from tools.vulnresearch import harness, pov

    monkeypatch.setenv("ICDEV_VR_DATA_DIR", str(tmp_path / "vr"))
    monkeypatch.setenv("ICDEV_NO_LLM", "true")
    monkeypatch.setattr(pov, "EXECUTOR", lambda: LocalDouble())
    monkeypatch.setattr(harness, "image_present", lambda image: False)
    monkeypatch.setattr(sast_runner, "run_bandit", canned_bandit)
    monkeypatch.setattr(dependency_auditor, "audit_python", canned_audit)
    LocalDouble.calls = []
    return wf_db


def _bench(**kw):
    from tools.vulnresearch import targets

    r = targets.register(REGISTRANT, name="benchmark (synthetic)", root=BENCH, reason="icx-ent-01 test (synthetic)",
                         src_root="src", test_dir="tests",
                         fuzz_targets=["vb_parser:parse_record", "vb_parser_safe:parse_record"], **kw)
    targets.authorise(r["target_id"], decision="authorise", reason="Synthetic benchmark (test).",
                      read_digest=r["content_digest"], actor=AUTHORISER)
    return r["target_id"]


def _fixed_diff() -> str:
    src = (ROOT / BENCH / "src/vb_cmdi.py").read_text(encoding="utf-8")
    fixed = src.replace('subprocess.run(f"echo hello {name}", shell=True, capture_output=True, text=True)',
                        'subprocess.run(["echo", "hello", name], capture_output=True, text=True)')
    assert fixed != src
    return "".join(difflib.unified_diff(src.splitlines(True), fixed.splitlines(True), "a/src/vb_cmdi.py",
                                        "b/src/vb_cmdi.py"))


# ── the scope lock ───────────────────────────────────────────────────────────


def test_a_target_is_scanned_only_when_registered_and_authorised_by_another_person(vr):
    from tools.security.redteam_scope import ScopeViolation
    from tools.vulnresearch import discovery, targets

    r = targets.register(REGISTRANT, name="bench", root=BENCH, reason="test (synthetic)", src_root="src")
    assert r["registration"]["tree_digest"] and r["registration"]["files"] >= 12
    with pytest.raises(ScopeViolation, match="awaiting_authorisation"):
        targets.assert_in_scope(r["target_id"])
    with pytest.raises(discovery.RunRefused) as exc:
        discovery.run(r["target_id"], REGISTRANT)
    assert exc.value.code == "out_of_scope", "an unauthorised target is never scanned"
    with pytest.raises(targets.TargetRefused) as exc:
        targets.authorise(r["target_id"], decision="authorise", reason="mine", read_digest=r["content_digest"],
                          actor=REGISTRANT)
    assert exc.value.code == "separation", "the registrant never authorises their own target"
    with pytest.raises(targets.TargetRefused) as exc:
        targets.authorise(r["target_id"], decision="authorise", reason="x", read_digest=r["content_digest"],
                          actor={"id": "agent:x", "role": "service"})
    assert exc.value.code == "not_a_person"
    with pytest.raises(targets.TargetRefused) as exc:
        targets.authorise(r["target_id"], decision="authorise", reason="x", read_digest="0" * 64, actor=AUTHORISER)
    assert exc.value.code == "digest"
    out = targets.authorise(r["target_id"], decision="authorise", reason="ok (synthetic)",
                            read_digest=r["content_digest"], actor=AUTHORISER)
    assert out["event"] == "authorised" and out["expires_on"]
    assert targets.assert_in_scope(r["target_id"])["tree"]["digest"] == r["registration"]["tree_digest"]
    with pytest.raises(ScopeViolation):
        targets.assert_in_scope("vrt-0000000000")


def test_a_root_outside_the_allowlist_is_refused(vr, tmp_path):
    from tools.vulnresearch import targets

    (tmp_path / "x.py").write_text("def f(a):\n    return a\n")
    with pytest.raises(targets.TargetRefused) as exc:
        targets.register(REGISTRANT, name="elsewhere", root=str(tmp_path), reason="no")
    assert exc.value.code == "out_of_scope" and "not under an allowed root" in exc.value.message
    with pytest.raises(targets.TargetRefused):
        targets.register(REGISTRANT, name="up", root=BENCH + "/../../..", reason="no")


def test_a_changed_revoked_or_expired_target_is_refused(vr, tmp_path, monkeypatch):
    from tools.security.redteam_scope import ScopeViolation
    from tools.vulnresearch import targets

    tree = tmp_path / "roots" / "t1"
    shutil.copytree(ROOT / BENCH, tree)
    monkeypatch.setenv("ICDEV_VR_ALLOWED_ROOTS", str(tmp_path / "roots"))
    r = targets.register(REGISTRANT, name="copy", root=str(tree), reason="test (synthetic)", src_root="src")
    targets.authorise(r["target_id"], decision="authorise", reason="ok", read_digest=r["content_digest"],
                      actor=AUTHORISER)
    targets.assert_in_scope(r["target_id"])
    (tree / "src" / "vb_eval.py").write_text("def calc(expr):\n    return 0\n")
    with pytest.raises(ScopeViolation, match="tree changed"):
        targets.assert_in_scope(r["target_id"])
    t2 = _bench(expires_on="2026-01-01")
    with pytest.raises(ScopeViolation, match="expired"):
        targets.assert_in_scope(t2)
    t3 = _bench()
    with pytest.raises(targets.TargetRefused) as exc:
        targets.revoke(t3, reason="", actor=DEVELOPER)
    assert exc.value.code == "reason"
    assert targets.revoke(t3, reason="stop (synthetic)", actor=DEVELOPER)["event"] == "revoked"
    with pytest.raises(ScopeViolation, match="revoked"):
        targets.assert_in_scope(t3)


def test_the_isolated_run_has_no_network_and_writes_only_under_out(tmp_path):
    from tools.security.sandbox_executor import SandboxExecutor, isolated_profile

    seen = {}

    def runner(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    ex = SandboxExecutor(config={"enabled": True, "isolated": {"engine": "docker"}, "audit_all_executions": False})
    res = ex.run_isolated(["python", "-c", "1"], image="img@sha256:" + "a" * 64,
                          mounts=[(str(tmp_path), "/target", "ro")], runner=runner)
    assert res.status == "completed"
    cmd = seen["cmd"]
    for flag in ("--network", "none", "--read-only", "--cap-drop", "ALL", "no-new-privileges", "65534:65534"):
        assert flag in cmd, flag
    assert cmd[cmd.index("--network") + 1] == "none"
    bad = ex.run_isolated(["true"], image="img", mounts=[(str(tmp_path), "/target", "rw")], runner=runner)
    assert bad.status == "failed" and "only /out" in bad.error
    gone = ex.run_isolated(["true"], image="img", mounts=[(str(tmp_path / "nope"), "/target", "ro")], runner=runner)
    assert gone.status == "failed"
    assert "network" not in SandboxExecutor.run_isolated.__code__.co_varnames, "there is no switch for the network"
    assert isolated_profile({"isolated": {}})["microvm"] is False, "a container never calls itself a microVM"
    assert isolated_profile({"isolated": {"oci_runtime": "runsc"}})["isolation"] == "gvisor"


def test_reproduction_validator_replays_a_sandbox_pov_only_through_a_registered_engine(monkeypatch):
    from tools.security import reproduction_validator as rv

    repro = rv.Reproduction(kind="sandbox_pov", target="vrt-x", steps=[{"input": "pov", "spec": {}}],
                            predicate={"type": "pov_oracle", "oracle": "file_created"})
    assert rv.validate_reproduction(repro) == []
    monkeypatch.setattr(rv, "_POV_REPLAYER", None)
    assert rv.replay(repro).outcome == rv.ReplayOutcome.unavailable, "no engine: nothing is established"
    bad = rv.Reproduction(kind="sandbox_pov", target="", steps=[{"input": "pov"}], predicate={"type": "status_in"})
    assert len(rv.validate_reproduction(bad)) == 2
    ok, why = rv.decide_discrimination(before_fired=True, before_decisive=True, before_outcome="reproduced",
                                       after_fired=True, after_decisive=True, after_outcome="reproduced",
                                       vocabulary=rv.POV_VOCABULARY)
    assert not ok and "control" in why


# ── discovery: proven or dropped ─────────────────────────────────────────────


def test_a_candidate_becomes_a_finding_only_with_a_discriminating_pov(vr):
    from tools.vulnresearch import discovery

    tid = _bench()
    out = discovery.run(tid, REGISTRANT, use_model=False)
    run = out["run"]
    got = {(f["module"], f["function"], f["cwe"]) for f in run["findings"]}
    assert got == {("vb_cmdi", "greet", "CWE-78"), ("vb_sqli", "find_user", "CWE-89"), ("vb_eval", "calc", "CWE-95"),
                   ("vb_pickle", "load_prefs", "CWE-502"), ("vb_parser", "parse_record", "CWE-20")}
    for f in run["findings"]:
        assert f["pov"]["replays"]["pov"] == "reproduced" and f["pov"]["replays"]["control"] == "not_reproduced"
        assert f["pov"]["sealed"] is True and "input" not in json.dumps(f["pov"]), "a PoV is sealed"
    dropped = {(d.get("file"), d.get("function"), d["reason"]) for d in run["dropped"]}
    assert ("src/vb_cmdi_safe.py", "greet", "not_reproduced") in dropped, "a SAST hit with no PoV is dropped"
    assert ("src/vb_sqli_safe.py", "find_user", "not_reproduced") in dropped
    assert run["counts"]["dropped"] == len(run["dropped"]) and run["counts"]["findings"] == 5
    fuzz = {z["module"]: z for z in run["fuzz"]}
    assert fuzz["vb_parser"]["crashed"] and fuzz["vb_parser"]["exception"] == "IndexError"
    assert not fuzz["vb_parser_safe"]["crashed"], "the safe parser rejects with ValueError: no crash"
    assert run["advisories"][0]["status"] == "unproven" and run["advisories"][0]["kev"] in (None, False)
    assert run["review"]["ran"] is False and "not asked" in run["review"]["note"]
    assert "sealed" not in run, "views never carry the sealed inputs"
    assert all(c["mounts"][0][1] == "/target" and c["mounts"][0][2] == "ro" for c in LocalDouble.calls)
    rows = discovery.runs(tid)
    assert rows[0]["run"]["run_id"] == out["run_id"] and "sealed" not in rows[0]["run"]
    f, sealed = discovery.sealed_for(rows[0]["run"]["findings"][0]["finding_id"])
    assert sealed["pov"]["input"] and sealed["control"]["input"]


def test_a_pov_that_also_fires_on_its_control_or_cannot_run_is_dropped(vr, monkeypatch):
    from tools.vulnresearch import discovery, pov, targets

    tid = _bench()
    cfg = targets.load_config()
    cfg["recipes"]["CWE-95"]["control"] = cfg["recipes"]["CWE-95"]["payload"]     # a tautological recipe
    monkeypatch.setattr(targets, "load_config", lambda path=None: cfg)
    run = discovery.run(tid, REGISTRANT, use_model=False)["run"]
    calc = next(d for d in run["dropped"] if d.get("function") == "calc")
    assert calc["reason"] == "tautology" and run["counts"]["dropped_by_reason"]["tautology"] == 1

    class Down:
        def run_isolated(self, *a, **k):
            return SimpleNamespace(status="unavailable", stdout="", stderr="", exit_code=-1, error="no engine")

    monkeypatch.setattr(pov, "EXECUTOR", lambda: Down())
    run = discovery.run(tid, REGISTRANT, use_model=False)["run"]
    assert run["findings"] == [], "no sandbox, no finding"
    assert run["counts"]["dropped_by_reason"]["sandbox_unavailable"] >= 4


def test_the_recipes_are_canary_only_and_mapped_from_sast(vr):
    from tools.vulnresearch import pov, targets

    cfg = targets.load_config()
    assert set(cfg["recipes"]) == {"CWE-78", "CWE-95", "CWE-22", "CWE-89", "CWE-502", "CWE-20"}
    for cwe, r in cfg["recipes"].items():
        m = pov.materialise(r, f"k-{cwe}", crash_input="UjEx", seed_input="")
        assert m["canary"].startswith("/tmp/vr_canary_") and r["oracle"] in ("file_created", "opened", "returned",
                                                                              "exception")
        text = json.dumps(m)
        for forbidden in ("curl", "wget", "nc ", "/etc/", "rm ", "socket", "http"):
            assert forbidden not in text, (cwe, forbidden)
    assert cfg["sast_cwe"]["B602"] == "CWE-78" and cfg["sast_cwe"]["B301"] == "CWE-502"


def test_the_agentic_review_only_proposes_and_an_invented_function_is_dropped(vr, monkeypatch):
    from tools.cortex import api
    from tools.vulnresearch import discovery

    monkeypatch.delenv("ICDEV_NO_LLM", raising=False)
    asked = {}

    def fake_complete(prompt, function="", ctx=None, **kw):
        asked["function"] = function
        asked["prompt"] = prompt
        return SimpleNamespace(text=json.dumps([
            {"file": "src/vb_traversal.py", "function": "read_doc", "cwe": "CWE-22", "hypothesis": "joined path"},
            {"file": "src/vb_cmdi_hidden.py", "function": "greet", "cwe": "CWE-78", "hypothesis": "indirect system"},
            {"file": "src/vb_traversal_safe.py", "function": "read_doc", "cwe": "CWE-22", "hypothesis": "maybe"},
            {"file": "src/nowhere.py", "function": "ghost", "cwe": "CWE-78", "hypothesis": "invented"},
        ]), model="test-double")

    monkeypatch.setattr(api, "complete", fake_complete)
    tid = _bench()
    run = discovery.run(tid, REGISTRANT, use_model=True)["run"]
    assert asked["function"] == "vuln_source_review", "routed by llm_function, never a model id"
    assert "Do not write exploits" in asked["prompt"]
    got = {(f["module"], f["cwe"]): f for f in run["findings"]}
    assert ("vb_traversal", "CWE-22") in got and got[("vb_traversal", "CWE-22")]["sources"] == ["agent"]
    assert ("vb_cmdi_hidden", "CWE-78") in got, "the agent proposed it; the PoV proved it"
    dropped = {(d.get("file"), d["reason"]) for d in run["dropped"]}
    assert ("src/vb_traversal_safe.py", "not_reproduced") in dropped, "a model's suspicion alone is not a finding"
    assert ("src/nowhere.py", "out_of_scope") in dropped
    assert run["review"]["provenance"] == "modelled"


# ── patches ──────────────────────────────────────────────────────────────────


def test_a_patch_is_proposed_only_when_its_pov_fails_and_the_tests_pass(vr):
    from tools.vulnresearch import discovery, patches

    tid = _bench()
    run = discovery.run(tid, REGISTRANT, use_model=False)["run"]
    fid = next(f["finding_id"] for f in run["findings"] if f["module"] == "vb_cmdi")
    src = (ROOT / BENCH / "src/vb_cmdi.py").read_text(encoding="utf-8")
    noop = "".join(difflib.unified_diff(src.splitlines(True), src.replace("shell=True", "shell=True ").splitlines(True),
                                        "a/src/vb_cmdi.py", "b/src/vb_cmdi.py"))
    with pytest.raises(patches.PatchRefused) as exc:
        patches.propose(fid, noop, DEVELOPER)
    assert exc.value.code == "not_validated" and exc.value.validation["pov_patched"] == "reproduced"
    breaks = "".join(difflib.unified_diff(src.splitlines(True), src.replace(
        'subprocess.run(f"echo hello {name}", shell=True, capture_output=True, text=True)',
        'subprocess.run(["echo", name], capture_output=True, text=True)').splitlines(True),
        "a/src/vb_cmdi.py", "b/src/vb_cmdi.py"))
    with pytest.raises(patches.PatchRefused) as exc:
        patches.propose(fid, breaks, DEVELOPER)
    assert exc.value.code == "not_validated" and exc.value.validation["tests_pass"] is False, "tests must pass"
    with pytest.raises(patches.PatchRefused) as exc:
        patches.propose(fid, "--- a/../../etc/x\n+++ b/../../etc/x\n", DEVELOPER)
    assert exc.value.code == "diff_path"
    out = patches.propose(fid, _fixed_diff(), DEVELOPER)
    v = out["validation"]
    assert v["pov_vulnerable"] == "reproduced" and v["pov_patched"] == "not_reproduced" and v["tests_pass"]
    assert (ROOT / BENCH / "src/vb_cmdi.py").read_text(encoding="utf-8") == src, "the target is never changed"


def test_a_patch_is_reviewed_by_a_person_never_its_proposer_and_never_applied(vr):
    from tools.vulnresearch import discovery, patches

    tid = _bench()
    run = discovery.run(tid, REGISTRANT, use_model=False)["run"]
    fid = next(f["finding_id"] for f in run["findings"] if f["module"] == "vb_cmdi")
    p = patches.propose(fid, _fixed_diff(), DEVELOPER)
    with pytest.raises(patches.PatchRefused) as exc:
        patches.review(fid, decision="accept", reason="mine", read_digest=p["content_digest"], actor=DEVELOPER)
    assert exc.value.code == "separation"
    with pytest.raises(patches.PatchRefused) as exc:
        patches.review(fid, decision="accept", reason="", read_digest=p["content_digest"], actor=AUTHORISER)
    assert exc.value.code == "reason"
    out = patches.review(fid, decision="accept", reason="Read it (synthetic).", read_digest=p["content_digest"],
                         actor=AUTHORISER)
    assert out["event"] == "patch_accepted" and out["applied"] is False
    with pytest.raises(patches.PatchRefused):
        patches.review(fid, decision="reject", reason="again", read_digest=p["content_digest"], actor=REGISTRANT)
    with pytest.raises(patches.PatchRefused) as exc:
        patches.draft(fid, DEVELOPER)
    assert exc.value.code == "no_model"


# ── measurement ──────────────────────────────────────────────────────────────


def test_the_rates_are_measured_against_planted_labels_with_no_model_judging(vr):
    from tools.vulnresearch import benchmark

    labels = benchmark.load_labels()
    assert labels["synthetic"] is True and labels["reviewed_by"] is None, "labelled by construction, unreviewed"
    s = benchmark.score([{"module": "vb_cmdi", "function": "greet", "cwe": "CWE-78"},
                         {"module": "vb_eval_safe", "function": "calc", "cwe": "CWE-95"}], labels)
    assert (s["tp"], s["fp"], s["fn"], s["tn"]) == (1, 1, 6, 4)
    assert s["false_positive_rate"] == 0.2 and s["false_negative_rate"] == round(6 / 7, 4)
    tid = _bench()
    m = benchmark.measure(tid, REGISTRANT, use_model=False)
    sc = m["scores"]
    assert (sc["tp"], sc["fp"], sc["fn"], sc["tn"]) == (5, 0, 2, 5)
    assert sc["false_positive_rate"] == 0.0 and sc["false_negative_rate"] == round(2 / 7, 4)
    assert set(sc["false_negatives"]) == {"vb_cmdi_hidden:greet:CWE-78", "vb_traversal:read_doc:CWE-22"}
    assert "no model judges" in sc["judged_by"] and m["model_used"] is False
    assert benchmark.latest()["event_id"] == m["event_id"]


# ── the bundle ───────────────────────────────────────────────────────────────


def test_the_schemas_are_pinned_and_the_publication_has_the_academy_shape():
    import hashlib

    from tools.vulnresearch import bundle

    for rel, sha in ((bundle.BUNDLE_SCHEMA_RELPATH, bundle.SCHEMA_SHA256),
                     (bundle.PUBLICATION_RELPATH, bundle.PUBLICATION_SHA256)):
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == sha, rel
    pub = bundle.publication_schema()
    assert pub["required"] == ["schema", "content_digest", "signed_by", "prepared_by", "step_id", "template",
                               "decided_at", "event_id", "row_hash", "producer"]
    assert set(pub["properties"]) == set(pub["required"]) | {"role"}
    s = bundle.schema()
    assert s["additionalProperties"] is False and "publication" not in s["properties"], "it travels beside"
    assert s["x-icos-contract"] == "draft" and s["x-license"] == "Apache-2.0"


def test_publishing_is_a_persons_step_never_the_preparers_and_it_verifies(vr):
    from tools.vulnresearch import bundle, discovery, ledger

    tid = _bench()
    with pytest.raises(bundle.BundleRefused) as exc:
        bundle.prepare(tid, REGISTRANT)
    assert exc.value.code == "no_run"
    discovery.run(tid, REGISTRANT, use_model=False)
    b = bundle.build(tid, at="2026-11-18T00:00:00Z")
    assert bundle.validate(b) == [] and b["schema"] == "icos-findings/v1"
    assert b == {**bundle.build(tid, at="2026-11-18T00:00:00Z")}, "deterministic given the ledger"
    assert '"input"' not in json.dumps(b["findings"]) and b["target"]["authorisation"]["decided_by"] == AUTHORISER["id"]
    p = bundle.prepare(tid, REGISTRANT)
    assert p["content_digest"] == b["content_digest"]
    with pytest.raises(bundle.BundleRefused) as exc:
        bundle.publish(tid, decision="publish", reason="mine", read_digest=p["content_digest"], actor=REGISTRANT)
    assert exc.value.code == "separation"
    assert bundle.published(tid) is None, "an unpublished bundle never leaves"
    out = bundle.publish(tid, decision="publish", reason="Read it (synthetic).", read_digest=p["content_digest"],
                         actor=AUTHORISER)
    assert out["event"] == "published"
    pub = bundle.published(tid)
    assert pub["publication"]["signed_by"] == AUTHORISER["id"] and pub["publication"]["prepared_by"] == REGISTRANT["id"]
    v = bundle.verify(pub["bundle"], pub["publication"], ledger.read())
    assert v["ok"], v["errors"]
    forged = {**pub["publication"], "signed_by": REGISTRANT["id"]}
    assert not bundle.verify(pub["bundle"], forged)["ok"], "self-published is refused"
    tampered = json.loads(json.dumps(pub["bundle"]))
    tampered["findings"] = tampered["findings"][1:]
    assert not bundle.verify(tampered, pub["publication"])["ok"]
    assert not bundle.verify(pub["bundle"], None)["ok"]


def test_the_ledger_is_append_only_and_hash_chained(vr):
    from tools.vulnresearch import ledger

    tid = _bench()
    rows = ledger.read(stream=f"target:{tid}")
    assert [r["event"] for r in rows] == ["registered", "authorised"]
    assert rows[1]["prev_hash"] == rows[0]["row_hash"] and ledger.chain(rows)["_problems"] == []
    conn = sqlite3.connect(str(vr))
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE vr_events SET reason = 'x' WHERE event_id = ?", (rows[0]["event_id"],))
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM vr_events WHERE event_id = ?", (rows[0]["event_id"],))
    finally:
        conn.close()
    forged = [dict(rows[0], reason="forged"), rows[1]]
    assert ledger.chain(forged)["_problems"], "a changed row does not verify"


# ── console, twin, MCP ───────────────────────────────────────────────────────


def _page_app(user):
    from tools.dashboard.app import app as dash
    from tools.vulnresearch.blueprint import vr_bp

    app = Flask(__name__, template_folder=dash.template_folder, static_folder=dash.static_folder)
    app.config.update(dash.config)
    app.jinja_env.filters.update(dash.jinja_env.filters)
    app.jinja_env.globals.update(dash.jinja_env.globals)
    app.template_context_processors[None].extend(dash.template_context_processors.get(None, []))
    app.secret_key = dash.secret_key or "test-secret"
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(user)
        g.security_context = {"tenant_id": "default", "user_id": user["id"], "classification": "CUI"}

    app.register_blueprint(vr_bp)
    return app.test_client()


def test_the_routes_and_the_page(vr):
    import yaml

    a, b = _page_app(REGISTRANT), _page_app(AUTHORISER)
    r = a.post("/vulnresearch/api/targets", json={"name": "bench (synthetic)", "root": BENCH, "src_root": "src",
                                                  "test_dir": "tests", "reason": "route test (synthetic)",
                                                  "fuzz_targets": ["vb_parser:parse_record"]})
    assert r.status_code == 201, r.get_data(as_text=True)
    tid, digest = r.get_json()["target_id"], r.get_json()["content_digest"]
    html = a.get("/vulnresearch/").get_data(as_text=True)
    for want in ("proven or dropped", "decl-wft-vr-target-authorise", "decl-wft-vr-patch-review",
                 "decl-wft-vr-findings-publish", "vulnresearch.vr_api_target_authorise", "vulnresearch.vr_api_run",
                 "vulnresearch.vr_api_patch_propose", "vulnresearch.vr_api_bundle_publish", "not a microVM",
                 f'data-target="{tid}"'):
        assert want in html, want
    assert "/api/" not in (ROOT / "tools/dashboard/templates/vulnresearch/index.html").read_text(encoding="utf-8")
    assert a.post(f"/vulnresearch/api/targets/{tid}/runs", json={"use_model": False}).status_code == 403
    body = {"decision": "authorise", "reason": "ok (synthetic)", "digest": digest}
    assert a.post(f"/vulnresearch/api/targets/{tid}/authorise", json=body).get_json()["code"] == "separation"
    assert b.post(f"/vulnresearch/api/targets/{tid}/authorise", json=body).get_json()["event"] == "authorised"
    run = a.post(f"/vulnresearch/api/targets/{tid}/runs", json={"use_model": False})
    assert run.status_code == 201 and run.get_json()["run"]["counts"]["findings"] == 5
    fid = next(f["finding_id"] for f in run.get_json()["run"]["findings"] if f["module"] == "vb_cmdi")
    p = a.post(f"/vulnresearch/api/findings/{fid}/patches", json={"diff": _fixed_diff()})
    assert p.status_code == 201, p.get_data(as_text=True)
    rv = b.post(f"/vulnresearch/api/findings/{fid}/review",
                json={"decision": "accept", "reason": "ok", "digest": p.get_json()["content_digest"]})
    assert rv.get_json()["event"] == "patch_accepted"
    assert a.get(f"/vulnresearch/api/targets/{tid}/bundle").get_json()["code"] == "unpublished"
    prep = a.post(f"/vulnresearch/api/targets/{tid}/bundle/prepare", json={})
    assert prep.status_code == 201
    pd = prep.get_json()["content_digest"]
    pubbody = {"decision": "publish", "reason": "ok (synthetic)", "digest": pd}
    assert a.post(f"/vulnresearch/api/targets/{tid}/bundle/publish", json=pubbody).status_code == 403
    assert b.post(f"/vulnresearch/api/targets/{tid}/bundle/publish", json=pubbody).get_json()["event"] == "published"
    got = a.get(f"/vulnresearch/api/targets/{tid}/bundle").get_json()
    assert got["bundle"]["content_digest"] == pd == got["publication"]["content_digest"]
    cmdi = next(f for f in got["bundle"]["findings"] if f["module"] == "vb_cmdi")
    assert cmdi["patch"]["review"]["decision"] == "accept" and cmdi["patch"]["applied"] is False
    view = a.get(f"/vulnresearch/api/targets/{tid}").get_json()
    assert "sealed" not in view["runs"][0]["run"]
    assert a.post(f"/vulnresearch/api/targets/{tid}/revoke", json={"reason": "stop (synthetic)"}).get_json()[
        "event"] == "revoked"
    reg = yaml.safe_load((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8-sig"))
    comp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == "vulnresearch")
    assert "/vulnresearch/" in [lk["href"] for lk in comp["nav"]["links"]]


def test_the_twin_observes_with_threads_and_replays(vr):
    import tools.twin_core.adapters.vulnresearch  # noqa: F401
    from tools.twin_core import governance
    from tools.vulnresearch import bundle, discovery

    adapter = governance.adapter_for("vulnresearch")
    assert adapter.observes == ("vulnresearch.discovery",)
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["vulnresearch.ledger"]["status"] == "verified"
    assert chk["vulnresearch.measurement"]["status"] == "unverified", "no measurement is never a pass"
    tid = _bench()
    discovery.run(tid, REGISTRANT, use_model=False)
    env = governance.observe(adapter)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk[f"vulnresearch.bundle.{tid}"]["status"] == "unverified"
    rules = {v["rule_id"] for v in env["violations"]}
    assert {"vulnresearch.finding_unpatched", "vulnresearch.isolation_container"} <= rules
    for v in env["violations"]:
        assert v["thread_id"], v
        if v["rule_id"] == "vulnresearch.finding_unpatched":
            res = governance.resolve_thread(v["thread_id"])
            assert res["resolved"] and res["record"]["event"] == "run_recorded"
    p = bundle.prepare(tid, REGISTRANT)
    bundle.publish(tid, decision="publish", reason="ok", read_digest=p["content_digest"], actor=AUTHORISER)
    env2 = governance.observe(adapter)
    chk = {x["id"]: x for x in env2["extra"]["checks"]}
    assert chk[f"vulnresearch.bundle.{tid}"]["status"] == "verified"
    rep = governance.replay(adapter, env2["extra"]["observation_id"])
    assert rep["match"], rep


def test_the_mcp_tools_read_without_sealed_povs_and_only_prepare(vr):
    from tools.conformance.feature_audit import approval_policy
    from tools.mcp.gap_handlers import handle_vulnresearch_prepare, handle_vulnresearch_status
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert {"vulnresearch_status", "vulnresearch_prepare"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["vulnresearch_status"] is True
    assert tool_authorization("vulnresearch_status")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["vulnresearch_prepare"] is False
    policy = approval_policy(ROOT, "args/agent_approval_policy.yaml")
    assert policy["tiers"].get("vulnresearch_prepare", policy["default"]) in policy["halts"]
    from tools.vulnresearch import discovery

    tid = _bench()
    discovery.run(tid, REGISTRANT, use_model=False)
    one = handle_vulnresearch_status({"target_id": tid})
    assert one["runs"][0]["run"]["counts"]["findings"] == 5
    assert '"sealed": {' not in json.dumps(one) and "vr_canary_" not in json.dumps(one), "never a PoV input"
    out = handle_vulnresearch_prepare({"agent_id": "ent01-drafter", "target_id": tid})
    assert out["draft"]["prepared_by"] == "agent:ent01-drafter" and out["draft"]["actor_kind"] == "agent"
    assert handle_vulnresearch_status({"target_id": "vrt-0000000000"})["code"] == "unknown_target"


# ── the real sandbox ─────────────────────────────────────────────────────────


def test_the_real_sandbox_end_to_end(wf_db, monkeypatch, tmp_path):
    """The real isolated container (a container engine is required: CI's ubuntu runners have one, and the pinned
    image is pulled on the host when missing): a planted command injection is proven, its patch makes the proof
    fail. Never skipped: a sandbox that cannot run here is a failure, not a pass."""
    assert shutil.which("docker"), "the isolated sandbox needs a container engine on this host"
    from tools.security import dependency_auditor, sast_runner
    from tools.vulnresearch import discovery, patches

    monkeypatch.setenv("ICDEV_VR_DATA_DIR", str(tmp_path / "vr"))
    monkeypatch.setenv("ICDEV_NO_LLM", "true")
    monkeypatch.setattr(sast_runner, "run_bandit", canned_bandit)
    monkeypatch.setattr(dependency_auditor, "audit_python", canned_audit)
    tid = _bench()
    run = discovery.run(tid, REGISTRANT, use_model=False)["run"]
    assert run["isolation"]["network"] == "none"
    assert {f["module"] for f in run["findings"]} == {"vb_cmdi", "vb_sqli", "vb_eval", "vb_pickle", "vb_parser"}
    fid = next(f["finding_id"] for f in run["findings"] if f["module"] == "vb_cmdi")
    assert patches.propose(fid, _fixed_diff(), DEVELOPER)["validation"]["pov_fails_after"] is True


# ── review fixes (FIX FIRST): offensive work stays bounded ───────────────────


def test_a_replay_request_cannot_carry_its_own_input_or_root(vr, tmp_path):
    """The sandbox PoV engine replays only what this module sealed, on the scope-locked tree or a copy it made."""
    from tools.security import reproduction_validator as rv
    from tools.vulnresearch import discovery, pov

    tid = _bench()
    run = discovery.run(tid, REGISTRANT, use_model=False)["run"]
    fid = run["findings"][0]["finding_id"]
    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "x.py").write_text("def f(a):\n    return open('/etc/hostname').read()\n")
    LocalDouble.calls = []
    forged = [
        {"input": "pov", "spec": {"module": "x", "function": "f", "input": "", "oracle": "returned", "canary": "/tmp/c",
                                  "token": "t", "plant": False}, "root_override": str(tmp_path / "evil")},
        {"finding_id": fid, "input": "pov", "root_override": str(tmp_path / "evil")},
        {"finding_id": fid, "input": "pov", "copy": "not-a-copy-this-module-made"},
        {"finding_id": "vrf-000000000000", "input": "pov"},
    ]
    for step in forged:
        repro = rv.Reproduction(kind="sandbox_pov", target=tid, steps=[step],
                                predicate={"type": "pov_oracle", "oracle": "returned"})
        out = rv.replay(repro)
        assert out.outcome == rv.ReplayOutcome.refused, (step, out.to_dict())
    assert LocalDouble.calls == [], "nothing a request carried ever reached the sandbox"
    ok = rv.replay(rv.Reproduction(kind="sandbox_pov", target=tid, steps=[{"finding_id": fid, "input": "pov"}],
                                   predicate={"type": "pov_oracle", "oracle": "x"}))
    assert ok.outcome == rv.ReplayOutcome.reproduced, "the sealed PoV of a recorded finding still replays"
    assert "Traceback" not in ok.detail and pov.sealed_digest  # no host stderr is handed back


def test_the_mcp_replay_tools_refuse_a_sandbox_pov(vr):
    from tools.mcp.gap_handlers import handle_finding_replay, handle_finding_verify_discrimination

    repro = {"kind": "sandbox_pov", "target": "vrt-0000000000", "steps": [{"input": "pov"}],
             "predicate": {"type": "pov_oracle", "oracle": "file_created"}}
    LocalDouble.calls = []
    one = handle_finding_replay({"reproduction": repro})
    two = handle_finding_verify_discrimination({"reproduction": repro, "vulnerable_target": "/", "fixed_target": "/"})
    for out in (one, two):
        assert out.get("refused") is True and "sandbox_pov" in out["error"], out
    assert LocalDouble.calls == []
