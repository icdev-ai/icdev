# CUI // SP-CTI
"""The opt-in floci IaC gate holds its shape offline (flx-ci-01).

NO DOCKER, NO NETWORK, NO EMULATOR. Everything here is either a pure function,
a recorded plan shape, or a structural assertion about the workflow and the
fixtures. The LIVE job is what measures floci; this is what stops the live job
from being wrong in ways a green run would hide.

THE THINGS THAT FAIL *GREEN* IF THEY BREAK, which is why each has a test:
  * ``gate_stricter_than_api`` counted as a finding -> every honest run red ->
    the job switched off within a week.
  * ``unmeasurable`` collapsed into agreement -> a run that measured nothing
    reads as a match.
  * a fixture using a service ``FLOCI_PROVIDER_OVERRIDE`` does not redirect ->
    the plan goes to REAL AWS, and the auth error looks like a broken emulator.
  * the two fixtures drifting onto the same side of the gate -> the job passes
    whether or not the gate still discriminates.
  * the workflow acquiring a ``push``/unconditional ``pull_request`` trigger ->
    an emulator run in front of every merge on a near-serial runner pool.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from tools.ci import floci_iac_gate as gate
from tools.cloud import emulator
from tools.infra_canvas import preapply_gate

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "floci-iac-gate.yml"
CONFIG = REPO_ROOT / "args" / "floci_iac_gate.yaml"

#: The four checks branch protection requires. This job is never one of them.
REQUIRED_CHECKS = ("Lint", "Test", "Security Scan", "Helm Lint")


@pytest.fixture(scope="module")
def cfg() -> dict:
    return gate.load_config(CONFIG)


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow(workflow_text: str) -> dict:
    return yaml.safe_load(workflow_text)


# ── The four cells ─────────────────────────────────────────────────────────


class TestAgreement:
    def test_the_four_cells(self):
        assert gate.classify_agreement("pass", "accepted") == gate.AGREE_PERMITTED
        assert gate.classify_agreement("pass", "rejected") == gate.GATE_MISSED_REJECTION
        assert gate.classify_agreement("fail", "accepted") == gate.GATE_STRICTER_THAN_API
        assert gate.classify_agreement("fail", "rejected") == gate.AGREE_REFUSED

    def test_only_a_missed_rejection_is_a_finding(self):
        assert gate.is_finding(gate.GATE_MISSED_REJECTION)
        # A compliance gate refusing what AWS would happily build is the gate
        # working. Failing on it makes every honest run red.
        assert not gate.is_finding(gate.GATE_STRICTER_THAN_API)
        assert not gate.is_finding(gate.AGREE_PERMITTED)
        assert not gate.is_finding(gate.AGREE_REFUSED)
        assert not gate.is_finding(gate.UNMEASURABLE)

    @pytest.mark.parametrize(
        "gate_verdict,api_verdict",
        [
            ("unmeasured", "accepted"),
            ("pass", "unmeasured"),
            ("unmeasured", "unmeasured"),
            ("", ""),
            ("PASS", "accepted"),  # the apply executor's vocabulary, not the gate's
        ],
    )
    def test_either_side_unmeasured_is_never_agreement(self, gate_verdict, api_verdict):
        assert gate.classify_agreement(gate_verdict, api_verdict) == gate.UNMEASURABLE

    def test_warn_from_the_apply_executor_is_unmeasured_not_accepted(self):
        # run_apply returns WARN when Docker is unavailable or no .tf was
        # found -- nothing was ever sent to the API surface.
        assert gate.api_verdict_from_apply({"gate": "WARN"}) == gate.API_UNMEASURED
        assert gate.api_verdict_from_apply({}) == gate.API_UNMEASURED
        assert gate.api_verdict_from_apply({"gate": "PASS"}) == gate.API_ACCEPTED
        assert gate.api_verdict_from_apply({"gate": "FAIL"}) == gate.API_REJECTED


class TestExitCodes:
    def test_could_not_run_stays_red(self):
        assert gate.exit_code({"state": gate.STATE_COULD_NOT_RUN}) == 2

    def test_a_finding_exits_one_and_clean_exits_zero(self):
        assert gate.exit_code({"state": gate.STATE_FINDINGS}) == 1
        assert gate.exit_code({"state": gate.STATE_CLEAN}) == 0

    def test_not_configured_exits_zero_and_is_not_a_clean_gate(self):
        # An operator who has not opted in must not fail the job -- and must
        # not be told the gate is clean either.
        report = gate.run({"image": "", "fixtures": []})
        assert report["state"] == gate.STATE_NOT_CONFIGURED
        assert gate.exit_code(report) == 0
        assert report["state"] != gate.STATE_CLEAN
        assert "not a clean gate" in report["could_not_run"].lower()

    def test_an_unreadable_declaration_is_could_not_run(self, tmp_path, capsys):
        missing = tmp_path / "nope.yaml"
        assert gate.main(["--config", str(missing), "--json"]) == 2
        assert "could_not_run" in capsys.readouterr().out


class TestImageResolution:
    def test_the_declaration_is_the_default_and_overrides_beat_it(self, monkeypatch, cfg):
        monkeypatch.delenv("FLOCI_CI_IMAGE", raising=False)
        assert gate.resolve_image(cfg) == cfg["image"]
        assert gate.resolve_image(cfg, "other/image:9") == "other/image:9"
        monkeypatch.setenv("FLOCI_CI_IMAGE", "env/image:9")
        assert gate.resolve_image(cfg) == "env/image:9"
        assert gate.resolve_image(cfg, "arg/image:9") == "arg/image:9"

    def test_the_image_is_pinned_never_latest_or_nightly(self, cfg):
        # The job's entire output is a comparison against an API surface. An
        # unpinned surface makes a disagreement unattributable.
        image = cfg["image"]
        assert ":" in image, "the emulator image must carry an explicit tag"
        tag = image.rsplit(":", 1)[1]
        assert tag not in ("latest", "nightly")
        assert not tag.startswith("nightly")
        assert re.fullmatch(r"\d+\.\d+\.\d+(-\w+)?", tag), f"not a pinned version: {tag}"


# ── Which services a fixture may use ───────────────────────────────────────


class TestSupportedServices:
    def test_the_allowed_set_is_derived_from_the_seam_not_hand_listed(self):
        endpoints = gate.override_endpoint_services()
        assert "s3" in endpoints, "the provider override must redirect s3"
        supported = gate.supported_fixture_services()
        assert supported <= endpoints
        # Container-backed services are excluded because this job deliberately
        # does not mount the host docker socket into the emulator.
        assert supported.isdisjoint(emulator.CONTAINER_BACKED_SERVICES)
        for service in ("rds", "ec2", "elasticache"):
            if service in endpoints:
                assert service not in supported

    def test_an_unmapped_resource_type_is_refused_not_assumed_supported(self):
        problems = gate.unsupported_fixture_services(
            'resource "aws_wibble_thing" "x" {\n}\n'
        )
        assert problems and "TF_TYPE_SERVICE" in problems[0]

    def test_a_service_the_override_does_not_redirect_is_refused(self):
        # aws_db_instance -> rds: redirected but container-backed. A prefix
        # heuristic would have derived "db", matched nothing, and let it pass.
        gate.TF_TYPE_SERVICE.setdefault("aws_db_instance", "rds")
        assert gate.unsupported_fixture_services('resource "aws_db_instance" "x" {\n}\n')

    def test_every_declared_fixture_only_uses_supported_services(self, cfg):
        for fixture in cfg["fixtures"]:
            source = REPO_ROOT / fixture["source"]
            for tf in source.glob("main_*.tf"):
                assert gate.unsupported_fixture_services(
                    tf.read_text(encoding="utf-8")
                ) == [], f"{tf} would be sent to real AWS"


# ── The fixtures ───────────────────────────────────────────────────────────


class TestFixtures:
    def test_two_fixtures_with_opposite_declared_gate_expectations(self, cfg):
        expectations = {f["canvas"]: f["expect_gate"] for f in cfg["fixtures"]}
        assert len(expectations) >= 2
        assert set(expectations.values()) == {"pass", "fail"}, (
            "without a fixture the gate must REFUSE, a green run proves only "
            "that the job ran -- not that the gate still discriminates"
        )

    def test_each_fixture_holds_exactly_one_main_tf(self, cfg):
        # run_apply re-derives its own file list from the staged canvas dir;
        # exactly one file is what makes the planned and applied Terraform the
        # same Terraform.
        for fixture in cfg["fixtures"]:
            source = REPO_ROOT / fixture["source"]
            assert len(list(source.glob("main_*.tf"))) == 1, source

    def test_no_fixture_declares_its_own_aws_provider_block(self, cfg):
        # FLOCI_PROVIDER_OVERRIDE is injected as a second file; a duplicate
        # provider configuration is a terraform error that reads as the
        # emulator rejecting a plan it never received.
        for fixture in cfg["fixtures"]:
            for tf in (REPO_ROOT / fixture["source"]).glob("main_*.tf"):
                body = tf.read_text(encoding="utf-8")
                assert not re.search(r'^\s*provider\s+"aws"', body, re.M), tf

    def test_the_recorded_plan_still_describes_its_own_terraform(self, cfg):
        # The recorded plan is a real capture from a live run, not a shape
        # somebody typed -- but it is a capture of a PAST tree. This is the
        # cheap tie between it and the .tf as it stands now; the live job is
        # the expensive one, and the two disagreeing is what it exists to find.
        for fixture in cfg["fixtures"]:
            source = REPO_ROOT / fixture["source"]
            tf = next(source.glob("main_*.tf"))
            body = tf.read_text(encoding="utf-8")
            declared = re.findall(r'^\s*resource\s+"([\w]+)"\s+"([\w]+)"', body, re.M)
            plan = json.loads((source / "recorded_plan.json").read_text(encoding="utf-8"))
            recorded = [(rc["type"], rc["name"]) for rc in plan["resource_changes"]]
            assert declared == recorded, f"{source} drifted from its recorded plan"
            # On the VALUE, not the key: terraform emits `"tags": null` for a
            # resource with no tags block, so key presence says nothing.
            has_tags_block = bool(re.search(r"^\s*tags\s*=\s*\{", body, re.M))
            recorded_tags = plan["resource_changes"][0]["change"]["after"].get("tags")
            assert has_tags_block == bool(recorded_tags), source

    def test_the_recorded_plans_land_on_opposite_sides_of_the_gate(self, cfg):
        verdicts = {}
        for fixture in cfg["fixtures"]:
            plan = json.loads(
                (REPO_ROOT / fixture["source"] / "recorded_plan.json").read_text(
                    encoding="utf-8"
                )
            )
            result = preapply_gate.run_gate(plan)
            verdicts[fixture["canvas"]] = result["gate"]
            assert result["gate"] == fixture["expect_gate"], (
                f"{fixture['canvas']}: declared {fixture['expect_gate']}, "
                f"measured {result['gate']} -- violations "
                f"{[v['check'] for v in result['violations']]}"
            )
        assert set(verdicts.values()) == {"pass", "fail"}


# ── The gate under test ────────────────────────────────────────────────────


class TestPreapplyGate:
    def test_the_job_names_one_gate_and_it_is_the_surviving_one(self, workflow_text):
        """The job names its gate, and the gate it names is the only one left.

        THIS ASSERTION INVERTED, deliberately. While TWO pre-apply gates existed
        this test required the workflow to name BOTH -- the one it used and the
        one it did not -- because a job that silently picks one of a duplicate
        pair blesses the pair. flx-ci-02 measured the pair and deleted the
        loser, so naming it now points a reader at a file that does not exist,
        which CLAUDE.md forbids for exactly the reason it forbids a documented
        command with no file behind it.
        """
        assert gate.PREAPPLY_GATE_MODULE == "tools/infra_canvas/preapply_gate.py"
        assert (REPO_ROOT / gate.PREAPPLY_GATE_MODULE).exists()
        assert "tools/infra_canvas/preapply_gate.py" in workflow_text

        # The duplicate is gone; nothing may advertise it as a live alternative.
        assert not (REPO_ROOT / "tools/infra_canvas/pre_apply_gate.py").exists(), (
            "the second pre-apply gate is back -- see "
            "tests/infra_canvas/test_one_preapply_gate.py before restoring it"
        )

    def test_a_query_over_an_unprovided_collection_is_skipped_not_failed(self):
        # Three of the eight infra .iqe files read infra.ai_decisions, a
        # collection this gate never registers. Running them raised, each raise
        # was recorded CAT3, and run_gate returned `fail` for EVERY plan.
        compliant = json.loads(
            (
                REPO_ROOT / "tests/fixtures/floci_iac/flocigate_ok/recorded_plan.json"
            ).read_text(encoding="utf-8")
        )
        result = preapply_gate.run_gate(compliant)
        assert result["gate"] == "pass"
        assert result["violations"] == []
        skipped = {s["check"]: s for s in result["skipped"]}
        assert skipped, "a check that did not run must be named"
        for name, entry in skipped.items():
            assert entry["reason"] in ("collection_not_provided", "parse_error", "empty")
        assert any(
            s["reason"] == "collection_not_provided" for s in result["skipped"]
        )

    def test_skipped_is_neither_a_violation_nor_a_pass(self):
        result = preapply_gate.run_gate({})
        # An empty plan still reports which checks did not run: `pass` over no
        # rules and `pass` over satisfied rules are different facts.
        assert result["gate"] == "pass"
        assert result["skipped"]
        skipped_names = {s["check"] for s in result["skipped"]}
        assert skipped_names.isdisjoint({v["check"] for v in result["violations"]})


# ── The workflow ───────────────────────────────────────────────────────────


class TestWorkflow:
    def test_it_exists_and_parses(self, workflow):
        assert workflow["name"]

    def test_it_is_never_one_of_the_four_required_checks(self, workflow):
        assert workflow["name"] not in REQUIRED_CHECKS
        for job in workflow["jobs"].values():
            assert job.get("name") not in REQUIRED_CHECKS

    def test_it_is_opt_in_and_never_runs_on_every_push_or_pr(self, workflow):
        # `on:` parses to True under YAML 1.1 -- read the key the loader gave.
        triggers = workflow.get("on", workflow.get(True))
        assert "workflow_dispatch" in triggers
        assert "schedule" in triggers
        assert "push" not in triggers, (
            "runners here are near-serial; a push trigger puts an emulator run "
            "in front of every merge on the board"
        )
        pr = triggers.get("pull_request")
        if pr is not None:
            assert pr.get("types") == ["labeled"], (
                "only `labeled`: `synchronize` would queue one emulator run per "
                "push on a labelled PR"
            )

    def test_the_label_gates_the_job(self, workflow):
        job = workflow["jobs"]["floci-iac-gate"]
        assert "floci-gate" in job["if"]
        assert "pull_request" in job["if"]

    def test_no_shell_neutraliser_and_no_continue_on_error(self, workflow_text, workflow):
        # Exit 2 is COULD NOT RUN and it must stay red. Comment lines are
        # excluded -- the workflow's own prose says why `|| true` is absent,
        # and a check that cannot tell an explanation from a neutraliser would
        # forbid documenting the rule.
        live = [
            ln for ln in workflow_text.splitlines() if not ln.lstrip().startswith("#")
        ]
        assert not [ln for ln in live if "|| true" in ln]
        # The gate step itself must not swallow an exit code at all.
        gate_step = next(
            s for s in workflow["jobs"]["floci-iac-gate"]["steps"] if s.get("id") == "gate"
        )
        # `${{ a || b }}` is GitHub's default operator, evaluated before the
        # shell ever sees the line -- strip expressions before looking for a
        # SHELL `||`, which is the thing that would swallow exit 2.
        shell = re.sub(r"\$\{\{.*?\}\}", "", gate_step["run"], flags=re.S)
        assert "||" not in shell
        for job in workflow["jobs"].values():
            assert not job.get("continue-on-error")
            for step in job.get("steps", []):
                assert not step.get("continue-on-error")

    def test_the_evidence_is_uploaded_even_when_the_job_fails(self, workflow):
        steps = workflow["jobs"]["floci-iac-gate"]["steps"]
        uploads = [s for s in steps if "upload-artifact" in str(s.get("uses", ""))]
        assert uploads, "a gate whose evidence is not captured proves nothing"
        for step in uploads:
            # Without `always()` GitHub skips the upload on the run whose
            # evidence is most wanted.
            assert str(step.get("if", "")).strip() == "always()"
            path = str(step["with"]["path"])
            assert "floci-iac-gate.json" in path
            assert "floci-iac-gate-artifacts" in path

    def test_the_emulator_switch_is_set_for_the_run_step(self, workflow):
        steps = workflow["jobs"]["floci-iac-gate"]["steps"]
        run_step = next(s for s in steps if s.get("id") == "gate")
        assert run_step["env"]["FLOCI_ENABLED"] == "true"
        assert "tools/ci/floci_iac_gate.py" in run_step["run"]

    def test_it_declares_concurrency_at_workflow_level(self, workflow):
        # mfx-ci-02: a superseded request must not keep a near-serial runner.
        assert workflow["concurrency"]["cancel-in-progress"] is True
        for job in workflow["jobs"].values():
            assert "concurrency" not in job, (
                "a job-level group carves the job out of the workflow group and "
                "keeps it queueing for an abandoned ref"
            )


class TestSafety:
    def test_the_driver_refuses_a_non_emulated_mode(self, monkeypatch, cfg):
        # A runner with AWS credentials and no emulator switch gets `aws` from
        # detect_mode, and this job would plan and APPLY against a real account.
        monkeypatch.setattr(gate, "docker_available", lambda: True)
        monkeypatch.setattr(gate, "detect_mode", lambda _env: "aws")
        report = gate.run(cfg, start=False)
        assert report["state"] == gate.STATE_COULD_NOT_RUN
        assert gate.exit_code(report) == 2
        assert "REFUSING" in report["could_not_run"]

    def test_no_docker_is_could_not_run_never_clean(self, monkeypatch, cfg):
        monkeypatch.setattr(gate, "docker_available", lambda: False)
        report = gate.run(cfg, start=False)
        assert report["state"] == gate.STATE_COULD_NOT_RUN
        assert gate.exit_code(report) == 2

    def test_the_emulator_is_started_without_the_host_docker_socket(self):
        source = Path(gate.__file__).read_text(encoding="utf-8")
        start = source.split("def start_emulator", 1)[1].split("\ndef ", 1)[0]
        assert "/var/run/docker.sock" not in start.split('"""', 2)[-1], (
            "a container holding the runner's docker socket is root-equivalent "
            "on that runner"
        )

    def test_the_health_wait_is_a_wait_and_uses_the_seam(self, monkeypatch, cfg):
        calls = {"n": 0}

        def flaky(_env, timeout=2.0):
            calls["n"] += 1
            return calls["n"] >= 3

        monkeypatch.setattr(gate.emulator, "reachable", flaky)
        monkeypatch.setattr(gate.time, "sleep", lambda _s: None)
        out = gate.wait_for_health({}, timeout_seconds=30, poll_seconds=0.01)
        assert out["healthy"] and out["attempts"] == 3

    def test_the_health_wait_gives_up_and_says_so(self, monkeypatch):
        monkeypatch.setattr(gate.emulator, "reachable", lambda *_a, **_k: False)
        monkeypatch.setattr(gate.time, "sleep", lambda _s: None)
        out = gate.wait_for_health({}, timeout_seconds=0.0, poll_seconds=0.01)
        assert not out["healthy"]
        assert emulator.HEALTH_PATH in out["error"]

    def test_the_performance_claim_guard_travels_with_the_report(self, cfg):
        report = gate.run({"image": "", "fixtures": []})
        assert "performance" in report["note"].lower()
        assert "twx-spk-01" in report["note"]
