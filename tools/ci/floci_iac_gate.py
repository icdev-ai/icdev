# CUI // SP-CTI
"""The opt-in floci IaC gate -- twx-spk-01's pattern 1 (flx-ci-01).

WHAT THIS ANSWERS
-----------------
Does ``tools/infra_canvas/preapply_gate.py``'s verdict on a Terraform plan
match what a real AWS API surface actually ACCEPTS?

The pre-apply gate has only ever been checked against fixtures written beside
it. floci is an INDEPENDENT derivation: an AWS API surface that has never read
our IQE queries. Plan -> gate -> apply, in that order, which is the gate's real
position in the pipeline.

WHICH GATE. There is now exactly ONE, and this module names it in one place --
``PREAPPLY_GATE_MODULE``. ``tools/infra_canvas/preapply_gate.py`` (has a CLI)
runs the ``context/iqe/queries/infra/*.iqe`` checks over the plan delta and
returns ``{"gate": "pass"|"fail", "violations": [...], "delta": {...},
"skipped": [...]}``.

When flx-ci-01 wrote this job there were TWO, and it had to NAME the one it
used rather than choose, because a job that silently picks one of a duplicate
pair blesses the pair. flx-ci-02 then measured the pair and DELETED the other
(``pre_apply_gate.py``, "IDC IaC Twin Phase 1"): it had zero runtime callers,
and it could not tell the ``flocigate_ok`` fixture from the ``flocigate_violating``
one -- identical verdict both times -- because its rules asked
ESTATE-COMPLETENESS questions ("is there a KMS service in this design?") of a
plan DELTA. Its rulebook was not lost: ``infra_engine.assess_infra_design`` is
consumed live by ``tools/infra_canvas/blueprint.py`` over the full design
graph, which is the input those rules were written for.
Derivation: ``docs/audits/flx-ci-02-two-preapply-gates.md``.
Standing guard: ``tests/infra_canvas/test_one_preapply_gate.py``.

THE FOUR CELLS, AND ONLY ONE IS A FINDING
-----------------------------------------
========================  ==============================================
gate pass, api accepted   ``agree_permitted``
gate pass, api REJECTED   ``gate_missed_rejection``  <- THE FINDING
gate fail, api accepted   ``gate_stricter_than_api`` <- EXPECTED, never a finding
gate fail, api rejected   ``agree_refused``
========================  ==============================================

``gate_stricter_than_api`` is what a compliance gate IS. AWS will happily build
an untagged, unencrypted, non-GovCloud bucket; the gate refuses it; the two
disagree and nothing is wrong. Reporting that as a defect would make the job
red on every honest run and it would be switched off within a week. Only the
opposite direction -- the gate blessing a plan the API surface refuses -- means
the gate is wrong about reality.

EITHER SIDE UNMEASURED IS ``unmeasurable``, NEVER AGREEMENT. Docker absent, the
emulator unreachable, a plan that would not parse: none of those is a verdict,
and two empty sides are not a match.

EXIT CODES
----------
0  clean -- or ``not_configured`` (an empty ``image:``, an operator stand-down),
   which is stated in words and is NEVER presented as a clean gate.
1  a finding.
2  COULD NOT RUN. Stays RED: a gate that could not run is not a gate that found
   nothing.

CLI::

    python tools/ci/floci_iac_gate.py --json
    python tools/ci/floci_iac_gate.py --fixture flocigate_ok
    python tools/ci/floci_iac_gate.py --no-start          # emulator already up
    python tools/ci/floci_iac_gate.py --artifacts .tmp/floci-gate
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- not
# the import root. Bootstrap it before the first first-party import below.
# THIS NAME IS USED FOR sys.path AND NOTHING ELSE (xit-decl-03): it answers
# "where do my imports come from", which is the same before and after a move.
# Where the repo root is asked as a FACT -- to find args/ and the fixtures --
# `icdev.core.paths.repo_root` is the one resolver, bound below.
_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

_REPO_ROOT = repo_root(__file__)

from tools.cloud import emulator  # noqa: E402
from tools.infra_canvas import preapply_gate  # noqa: E402
from tools.studio.executors import terraform_apply  # noqa: E402
from tools.studio.executors._base import (  # noqa: E402
    FLOCI_PROVIDER_OVERRIDE,
    TFVARS_DEFAULTS,
    artifacts_dir,
    aws_env,
    detect_mode,
    docker_available,
    docker_aws_flags,
    docker_run,
    emulator_docker_endpoint,
    is_emulated,
    pull_image,
)

#: The gate under test, named ONCE. See the module docstring for why the other
#: one exists and why this job does not silently choose between them.
PREAPPLY_GATE_MODULE = "tools/infra_canvas/preapply_gate.py"

CONFIG_PATH = _REPO_ROOT / "args" / "floci_iac_gate.yaml"

# ── Verdict vocabularies ───────────────────────────────────────────────────
#
# Kept apart on purpose. The gate answers pass/fail, the API surface answers
# accepted/rejected, and neither may be spelled in the other's words -- a plan
# the emulator refuses has not "failed the gate".
GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_UNMEASURED = "unmeasured"

API_ACCEPTED = "accepted"
API_REJECTED = "rejected"
API_UNMEASURED = "unmeasured"

AGREE_PERMITTED = "agree_permitted"
GATE_MISSED_REJECTION = "gate_missed_rejection"
GATE_STRICTER_THAN_API = "gate_stricter_than_api"
AGREE_REFUSED = "agree_refused"
UNMEASURABLE = "unmeasurable"

#: Run states. `not_configured` and `could_not_run` are deliberately distinct:
#: the first is an operator who has not opted in, the second is an opt-in that
#: broke, and they exit 0 and 2 respectively.
STATE_CLEAN = "clean"
STATE_FINDINGS = "findings"
STATE_NOT_CONFIGURED = "not_configured"
STATE_COULD_NOT_RUN = "could_not_run"

EXIT_CLEAN = 0
EXIT_FINDING = 1
EXIT_COULD_NOT_RUN = 2


# ── Which AWS services a fixture may use ───────────────────────────────────
#
# DERIVED, never hand-listed. Two independent constraints, and BOTH fail in the
# direction that looks like an emulator problem when it is actually ours:
#
#   1. The service must appear in FLOCI_PROVIDER_OVERRIDE's endpoints{} block.
#      A service NOT redirected there is not sent to the emulator at all -- the
#      stock provider talks to REAL AWS with the dummy credentials the override
#      supplies, and the resulting auth error is indistinguishable from floci
#      being broken.
#   2. It must not be container-backed. This job does NOT mount the host docker
#      socket into the emulator (see `start_emulator`), so Lambda, RDS,
#      ElastiCache, OpenSearch, MSK and ECS/EC2/EKS cannot be served --
#      `emulator.CONTAINER_BACKED_SERVICES` is the declared list and is read,
#      not respelled.

_ENDPOINT_LINE = re.compile(r"^\s*([a-z0-9_]+)\s*=\s*\"\{ep\}\"\s*$")


def override_endpoint_services() -> frozenset[str]:
    """Service names FLOCI_PROVIDER_OVERRIDE redirects to the emulator."""
    inside = False
    found: set[str] = set()
    for line in FLOCI_PROVIDER_OVERRIDE.splitlines():
        if "endpoints {{" in line:
            inside = True
            continue
        if inside:
            if line.strip().startswith("}"):
                break
            m = _ENDPOINT_LINE.match(line)
            if m:
                found.add(m.group(1))
    return frozenset(found)


def supported_fixture_services() -> frozenset[str]:
    """Services a fixture may use: redirected AND not container-backed."""
    return frozenset(
        s for s in override_endpoint_services()
        if s not in emulator.CONTAINER_BACKED_SERVICES
    )


#: Terraform resource type -> AWS service name.
#:
#: An explicit map rather than a prefix heuristic, because the heuristic is
#: WRONG in the direction that matters: `aws_db_instance` would derive `db`,
#: which is in no list, and a fixture using it would sail past a check meant to
#: refuse exactly that (RDS is container-backed). An UNKNOWN type is REFUSED,
#: never assumed supported -- extend this map when a fixture needs a new type.
TF_TYPE_SERVICE: dict[str, str] = {
    "aws_s3_bucket": "s3",
    "aws_s3_bucket_versioning": "s3",
    "aws_s3_bucket_public_access_block": "s3",
    "aws_s3_bucket_server_side_encryption_configuration": "s3",
    "aws_s3_object": "s3",
    "aws_iam_role": "iam",
    "aws_iam_policy": "iam",
    "aws_iam_role_policy": "iam",
    "aws_iam_role_policy_attachment": "iam",
    "aws_ssm_parameter": "ssm",
    "aws_kms_key": "kms",
    "aws_kms_alias": "kms",
}

_RESOURCE_DECL = re.compile(r'^\s*resource\s+"([A-Za-z0-9_]+)"\s+"[^"]+"\s*\{')


def fixture_resource_types(tf_text: str) -> list[str]:
    """Terraform resource types declared in a fixture's HCL, in source order."""
    return [m.group(1) for m in (_RESOURCE_DECL.match(ln) for ln in tf_text.splitlines()) if m]


def unsupported_fixture_services(tf_text: str) -> list[str]:
    """Reasons this fixture cannot be run against the emulator. Empty == fine.

    Fail-closed: an unmapped resource type is reported, not waved through.
    """
    allowed = supported_fixture_services()
    problems: list[str] = []
    for rtype in fixture_resource_types(tf_text):
        service = TF_TYPE_SERVICE.get(rtype)
        if service is None:
            problems.append(
                f"{rtype}: not in TF_TYPE_SERVICE -- add it (with its AWS "
                f"service) rather than assuming it is supported"
            )
        elif service not in allowed:
            problems.append(
                f"{rtype} ({service}): not redirected by FLOCI_PROVIDER_OVERRIDE "
                f"or container-backed; allowed = {sorted(allowed)}"
            )
    return problems


# ── Configuration ──────────────────────────────────────────────────────────


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read args/floci_iac_gate.yaml. Raises on an unreadable declaration."""
    import yaml

    cfg_path = path or CONFIG_PATH
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"{cfg_path} did not parse to a mapping")
    return cfg


def resolve_image(cfg: dict[str, Any], override: str = "") -> str:
    """The emulator image, most specific source first.

    ``--image`` / ``FLOCI_CI_IMAGE`` beat the declaration so a dispatch can
    re-point the job at a newer tag without a code change -- and an empty
    result is an operator stand-down, not a default.
    """
    return (override or os.environ.get("FLOCI_CI_IMAGE") or cfg.get("image") or "").strip()


# ── Agreement ──────────────────────────────────────────────────────────────


def classify_agreement(gate_verdict: str, api_verdict: str) -> str:
    """Cross the gate's verdict with what the API surface did.

    ``unmeasurable`` whenever either side has no verdict. Two empty sides are
    NOT agreement -- the defect claim_verifier exists to refuse, one layer over.
    """
    if gate_verdict not in (GATE_PASS, GATE_FAIL):
        return UNMEASURABLE
    if api_verdict not in (API_ACCEPTED, API_REJECTED):
        return UNMEASURABLE
    if gate_verdict == GATE_PASS:
        return AGREE_PERMITTED if api_verdict == API_ACCEPTED else GATE_MISSED_REJECTION
    return GATE_STRICTER_THAN_API if api_verdict == API_ACCEPTED else AGREE_REFUSED


def is_finding(agreement: str) -> bool:
    """Only the gate blessing what the API surface refuses is a finding.

    NOT ``gate_stricter_than_api``: refusing what AWS would build is the gate's
    job, and failing on it would make an honest run red.
    NOT ``unmeasurable``: that is a could-not-run, carried by the run state.
    """
    return agreement == GATE_MISSED_REJECTION


def api_verdict_from_apply(apply_result: dict[str, Any]) -> str:
    """Map terraform_apply.run_apply()'s gate onto the API vocabulary.

    ``WARN`` is UNMEASURED, never accepted: run_apply returns it when Docker is
    unavailable or no .tf files were found, i.e. when nothing was ever sent to
    the API surface.
    """
    gate = str(apply_result.get("gate", "")).upper()
    if gate == "PASS":
        return API_ACCEPTED
    if gate == "FAIL":
        return API_REJECTED
    return API_UNMEASURED


# ── Emulator lifecycle ─────────────────────────────────────────────────────


def _docker(*args: str, timeout: int = 180) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "docker executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"docker {' '.join(args)} timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        return 1, "", str(exc)


def wait_for_health(
    env: dict[str, str], timeout_seconds: float, poll_seconds: float = 2.0
) -> dict[str, Any]:
    """Poll ``/_localstack/health`` until it answers or the deadline passes.

    A WAIT, not a sleep: a fixed sleep is either too short (the job fails on a
    cold pull) or wasted on every healthy run. Reachability is asked through
    ``emulator.reachable()`` -- the seam already owns the endpoint and the
    health path, and a second HTTP client here could disagree with it.
    """
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    while True:
        attempts += 1
        if emulator.reachable(env, timeout=min(5.0, max(1.0, poll_seconds))):
            return {"healthy": True, "attempts": attempts}
        if time.monotonic() >= deadline:
            return {
                "healthy": False,
                "attempts": attempts,
                "error": (
                    f"{emulator.endpoint(env)}{emulator.HEALTH_PATH} did not answer "
                    f"within {timeout_seconds}s"
                ),
            }
        time.sleep(poll_seconds)


def start_emulator(cfg: dict[str, Any], image: str) -> dict[str, Any]:
    """Pull the pinned image and start the container. Never mounts the socket.

    THE HOST DOCKER SOCKET IS DELIBERATELY NOT MOUNTED. floci's own quick-start
    mounts it, and it is required for container-backed services -- but a
    container holding the runner's docker socket is root-equivalent on that
    runner, and this job's fixtures need only in-process services. The cost is
    stated rather than hidden: Lambda/RDS/ElastiCache/OpenSearch/MSK/ECS/EC2/EKS
    cannot be exercised here, which is exactly what
    ``unsupported_fixture_services`` refuses a fixture for.
    """
    name = str(cfg.get("container_name") or "icdev-floci-iac-gate")
    port = int(cfg.get("port") or 4566)

    _docker("rm", "-f", name, timeout=60)  # a leftover from a cancelled run

    rc, _, err = _docker("pull", image, timeout=600)
    if rc != 0:
        return {"started": False, "error": f"docker pull {image} failed: {err.strip()[:400]}"}

    rc, out, err = _docker(
        "run", "-d", "--name", name, "-p", f"{port}:4566", image, timeout=180
    )
    if rc != 0:
        return {"started": False, "error": f"docker run failed: {err.strip()[:400]}"}
    return {"started": True, "container": name, "container_id": out.strip()[:12]}


def stop_emulator(cfg: dict[str, Any]) -> None:
    _docker("rm", "-f", str(cfg.get("container_name") or "icdev-floci-iac-gate"), timeout=60)


# ── Plan ───────────────────────────────────────────────────────────────────


def stage_fixture(canvas: str, source: Path) -> Path:
    """Copy a fixture's single ``main_*.tf`` into its canvas terraform dir.

    EXACTLY ONE .tf, on purpose. ``terraform_apply.run_apply`` re-derives its
    own file list from this directory (newest ``main_*.tf`` plus an optional
    ``variables.tf``); staging one file is what guarantees the plan this module
    hands the gate and the apply the executor runs are the SAME Terraform.
    """
    tf_dir = artifacts_dir(canvas) / "terraform"
    tf_dir.mkdir(parents=True, exist_ok=True)
    for stale in tf_dir.glob("main_*.tf"):
        stale.unlink()
    srcs = sorted(source.glob("main_*.tf"))
    if len(srcs) != 1:
        raise ValueError(
            f"fixture {source} must hold exactly one main_*.tf, found {len(srcs)}"
        )
    dst = tf_dir / srcs[0].name
    shutil.copy2(srcs[0], dst)
    return dst


def build_plan_json(
    cfg: dict[str, Any], tf_path: Path, env: dict[str, str], mode: str
) -> dict[str, Any]:
    """``terraform init`` -> ``plan -out`` -> ``show -json``, in the emulator.

    The workspace is assembled exactly as the Studio executors assemble theirs
    -- the same FLOCI_PROVIDER_OVERRIDE, the same docker_aws_flags, the same
    image -- so the plan the gate reads is the plan the apply will run.

    Returns ``{"plan": {...}}`` or ``{"error": "..."}``. Never both.
    """
    image = str(cfg.get("terraform_image") or "hashicorp/terraform:1.9")
    tmo = int(cfg.get("terraform_timeout_seconds") or 300)

    if not pull_image(image):
        return {"error": f"could not obtain {image}"}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copy2(tf_path, tmp_path / tf_path.name)
        if is_emulated(mode):
            docker_ep = emulator_docker_endpoint(emulator.endpoint(env))
            region = env.get("AWS_DEFAULT_REGION") or emulator.region(env)
            (tmp_path / "floci_override.tf").write_text(
                FLOCI_PROVIDER_OVERRIDE.format(ep=docker_ep, region=region),
                encoding="utf-8",
                newline="",
            )
        (tmp_path / "auto.tfvars").write_text(TFVARS_DEFAULTS, encoding="utf-8", newline="")

        flags = docker_aws_flags(env, mode)
        rc, _, err = docker_run(
            image, tmp, flags, "terraform",
            "init", "-backend=false", "-input=false", "-no-color", timeout=tmo,
        )
        if rc != 0:
            return {"error": f"terraform init failed: {err.strip()[:400]}"}

        rc, out, err = docker_run(
            image, tmp, flags, "terraform",
            "plan", "-out=tfplan", "-input=false", "-no-color", timeout=tmo,
        )
        if rc != 0:
            return {"error": f"terraform plan failed: {(err or out).strip()[:400]}"}

        # `terraform show -json <planfile>`, NEVER `terraform plan -json`: the
        # latter streams NDJSON progress LOG lines, which carry no
        # `resource_changes` key at all -- preapply_gate would read {} and
        # report a clean `pass` over a plan it never saw.
        rc, out, err = docker_run(
            image, tmp, flags, "terraform", "show", "-json", "tfplan", timeout=tmo,
        )
        if rc != 0:
            return {"error": f"terraform show -json failed: {err.strip()[:400]}"}
        try:
            plan = json.loads(out)
        except json.JSONDecodeError as exc:
            return {"error": f"plan JSON did not parse: {exc}"}
        if not isinstance(plan, dict):
            return {"error": "plan JSON did not parse to an object"}
        return {"plan": plan}


# ── One fixture, end to end ────────────────────────────────────────────────


def run_fixture(
    cfg: dict[str, Any],
    fixture: dict[str, Any],
    env: dict[str, str],
    mode: str,
    artifacts: Path | None = None,
) -> dict[str, Any]:
    """plan -> preapply gate -> apply -> compare, for one fixture canvas."""
    canvas = str(fixture["canvas"])
    source = _REPO_ROOT / str(fixture["source"])
    expect_gate = str(fixture.get("expect_gate") or "")
    expect_api = str(fixture.get("expect_api") or "")

    result: dict[str, Any] = {
        "canvas": canvas,
        "source": str(fixture["source"]),
        "gate_module": PREAPPLY_GATE_MODULE,
        "expect_gate": expect_gate,
        "expect_api": expect_api,
        "gate_verdict": GATE_UNMEASURED,
        "api_verdict": API_UNMEASURED,
        "agreement": UNMEASURABLE,
        "findings": [],
        "could_not_run": None,
    }

    if not source.is_dir():
        result["could_not_run"] = f"fixture directory missing: {source}"
        return result

    try:
        tf_path = stage_fixture(canvas, source)
    except (OSError, ValueError) as exc:
        result["could_not_run"] = f"could not stage fixture: {exc}"
        return result

    unsupported = unsupported_fixture_services(tf_path.read_text(encoding="utf-8"))
    if unsupported:
        # Refused BEFORE planning. A resource the override does not redirect
        # would be sent to real AWS, and this job must never do that.
        result["could_not_run"] = "; ".join(unsupported)
        return result

    built = build_plan_json(cfg, tf_path, env, mode)
    if "error" in built:
        result["could_not_run"] = built["error"]
        return result
    plan = built["plan"]

    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / f"{canvas}.plan.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8"
        )

    gate_out = preapply_gate.run_gate(plan)
    result["gate"] = gate_out
    result["gate_verdict"] = str(gate_out.get("gate") or GATE_UNMEASURED)

    if artifacts is not None:
        (artifacts / f"{canvas}.gate.json").write_text(
            json.dumps(gate_out, indent=2), encoding="utf-8"
        )

    # The apply half goes through the EXISTING executor -- not a second
    # terraform invocation written here -- so what this job measures is the
    # code path Studio actually runs.
    try:
        apply_out = terraform_apply.run_apply("", "floci-iac-gate", canvas)
    except Exception as exc:  # noqa: BLE001 -- an executor crash is could-not-run
        result["could_not_run"] = f"terraform_apply raised: {exc}"
        return result

    result["apply"] = {
        "gate": apply_out.get("gate"),
        "mode": apply_out.get("mode"),
        "resources_created": apply_out.get("resources_created"),
        "findings": apply_out.get("findings", []),
    }
    result["api_verdict"] = api_verdict_from_apply(apply_out)

    if artifacts is not None:
        (artifacts / f"{canvas}.apply.json").write_text(
            json.dumps(result["apply"], indent=2, default=str), encoding="utf-8"
        )

    result["agreement"] = classify_agreement(result["gate_verdict"], result["api_verdict"])

    findings: list[dict[str, str]] = []
    if result["agreement"] == UNMEASURABLE:
        result["could_not_run"] = (
            f"gate={result['gate_verdict']} api={result['api_verdict']} -- "
            "one side has no verdict, so there is nothing to compare"
        )
    elif is_finding(result["agreement"]):
        findings.append({
            "kind": GATE_MISSED_REJECTION,
            "detail": (
                f"{PREAPPLY_GATE_MODULE} passed a plan the AWS API surface "
                f"REFUSED. The gate is wrong about what is buildable."
            ),
        })

    # The declared-expectation half: a gate that has stopped discriminating
    # reports `pass` on both fixtures and every agreement cell still looks fine.
    if expect_gate and result["gate_verdict"] in (GATE_PASS, GATE_FAIL) \
            and result["gate_verdict"] != expect_gate:
        findings.append({
            "kind": "gate_expectation_mismatch",
            "detail": (
                f"declared expect_gate={expect_gate}, measured "
                f"{result['gate_verdict']} -- the gate's behaviour on this "
                f"fixture changed"
            ),
        })
    if expect_api and result["api_verdict"] in (API_ACCEPTED, API_REJECTED) \
            and result["api_verdict"] != expect_api:
        findings.append({
            "kind": "api_expectation_mismatch",
            "detail": (
                f"declared expect_api={expect_api}, measured "
                f"{result['api_verdict']} -- the API surface's behaviour on "
                f"this fixture changed"
            ),
        })

    result["findings"] = findings
    return result


# ── Driver ─────────────────────────────────────────────────────────────────


def run(
    cfg: dict[str, Any],
    *,
    image_override: str = "",
    only: str = "",
    start: bool = True,
    artifacts: Path | None = None,
) -> dict[str, Any]:
    """Run the whole job. Returns the report; the caller decides the exit code."""
    report: dict[str, Any] = {
        "state": STATE_COULD_NOT_RUN,
        "gate_module": PREAPPLY_GATE_MODULE,
        "image": "",
        "fixtures": [],
        "findings": 0,
        "could_not_run": None,
        "note": (
            "An emulator reproduces the AWS API contract, NOT its performance "
            "characteristics. Never source a performance, cost or capacity "
            "claim from this job (docs/spikes/twx-spk-01-localstack-go-no-go.md)."
        ),
    }

    image = resolve_image(cfg, image_override)
    report["image"] = image
    if not image:
        report["state"] = STATE_NOT_CONFIGURED
        report["could_not_run"] = (
            "no emulator image declared (args/floci_iac_gate.yaml `image:` is "
            "empty and neither --image nor FLOCI_CI_IMAGE is set). NOTHING WAS "
            "MEASURED -- this is not a clean gate."
        )
        return report

    if not docker_available():
        report["could_not_run"] = "docker is not available on this host"
        return report

    # Emulator mode is asserted, never assumed. `detect_mode` answers `aws` on
    # a host with AWS credentials and no emulator switch, and this job would
    # then plan and APPLY against a real account.
    env = aws_env()
    mode = detect_mode(env)
    report["mode"] = mode
    if not is_emulated(mode):
        report["could_not_run"] = (
            f"detect_mode() answered {mode!r}, not an emulated mode. Set "
            "FLOCI_ENABLED=true (and FLOCI_ENDPOINT if not the default). "
            "REFUSING to plan or apply against a real account."
        )
        return report

    started_here = False
    if start:
        started = start_emulator(cfg, image)
        if not started.get("started"):
            report["could_not_run"] = started.get("error") or "emulator did not start"
            return report
        started_here = True
        report["container"] = started.get("container")

    try:
        health = wait_for_health(
            env,
            float(cfg.get("health_timeout_seconds") or 120),
            float(cfg.get("health_poll_seconds") or 2),
        )
        report["health"] = health
        if not health.get("healthy"):
            report["could_not_run"] = health.get("error") or "emulator never became healthy"
            return report

        fixtures = [
            f for f in (cfg.get("fixtures") or [])
            if not only or str(f.get("canvas")) == only
        ]
        if not fixtures:
            report["could_not_run"] = (
                f"no fixtures to run{f' matching {only!r}' if only else ''}"
            )
            return report

        results = [run_fixture(cfg, f, env, mode, artifacts) for f in fixtures]
        report["fixtures"] = results

        blocked = [r for r in results if r.get("could_not_run")]
        if blocked:
            report["could_not_run"] = "; ".join(
                f"{r['canvas']}: {r['could_not_run']}" for r in blocked
            )
            return report

        report["findings"] = sum(len(r["findings"]) for r in results)
        report["state"] = STATE_FINDINGS if report["findings"] else STATE_CLEAN
        return report
    finally:
        if started_here:
            stop_emulator(cfg)


def exit_code(report: dict[str, Any]) -> int:
    state = report.get("state")
    if state == STATE_COULD_NOT_RUN:
        return EXIT_COULD_NOT_RUN
    if state == STATE_FINDINGS:
        return EXIT_FINDING
    return EXIT_CLEAN


def _render(report: dict[str, Any]) -> str:
    lines = [
        f"floci IaC gate -- {report.get('state')}",
        f"  gate module : {report.get('gate_module')}",
        f"  image       : {report.get('image') or '(none declared)'}",
    ]
    if report.get("could_not_run"):
        lines.append(f"  could not run: {report['could_not_run']}")
    for r in report.get("fixtures", []):
        lines.append(
            f"  {r['canvas']}: gate={r['gate_verdict']} "
            f"(expected {r['expect_gate'] or '?'}) "
            f"api={r['api_verdict']} (expected {r['expect_api'] or '?'}) "
            f"-> {r['agreement']}"
        )
        for f in r.get("findings", []):
            lines.append(f"      FINDING {f['kind']}: {f['detail']}")
    lines.append(f"  findings    : {report.get('findings', 0)}")
    lines.append(f"  {report['note']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Opt-in floci IaC gate: does tools/infra_canvas/preapply_gate.py "
            "agree with what a real AWS API surface accepts?"
        )
    )
    ap.add_argument("--config", default="", help="Override args/floci_iac_gate.yaml")
    ap.add_argument("--image", default="", help="Override the pinned emulator image")
    ap.add_argument("--fixture", default="", help="Run one fixture canvas only")
    ap.add_argument(
        "--no-start", action="store_true",
        help="An emulator is already listening; do not start or remove one",
    )
    ap.add_argument("--artifacts", default="", help="Directory for plan/gate/apply JSON")
    ap.add_argument("--json", action="store_true", help="Emit the report as JSON")
    ap.add_argument("--out", default="", help="Also write the JSON report to this path")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(Path(args.config) if args.config else None)
    except Exception as exc:  # noqa: BLE001
        report = {
            "state": STATE_COULD_NOT_RUN,
            "could_not_run": f"could not read the declaration: {exc}",
            "gate_module": PREAPPLY_GATE_MODULE,
            "fixtures": [],
            "findings": 0,
            "note": "",
        }
        print(json.dumps(report, indent=2) if args.json else _render(report))
        return EXIT_COULD_NOT_RUN

    report = run(
        cfg,
        image_override=args.image,
        only=args.fixture,
        start=not args.no_start,
        artifacts=Path(args.artifacts) if args.artifacts else None,
    )

    payload = json.dumps(report, indent=2, default=str)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
    print(payload if args.json else _render(report))
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
