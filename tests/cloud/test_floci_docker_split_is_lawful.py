# CUI // SP-CTI — the floci suite's Docker split, and the rules that hold it (flx-test-01)
"""The no-Docker half stays no-Docker, and the Docker half is kept off the
default runners LAWFULLY rather than by being invisible.

WHY THIS FILE EXISTS
====================
Splitting a suite by infrastructure requirement creates two failure modes that
both report GREEN, and neither is visible to any check the tree already has:

1. THE DOCKER HALF GOES QUIET. A file that is in no allowlist, no exclusion and
   no backlog fails `--check-coverage`, so that door is shut. But a file that is
   EXCLUDED and run by nothing is a dead suite with paperwork — exactly the
   defect args/test_gating_gate.yaml was written to close, wearing the costume
   of compliance with it. An exclusion whose reason cites a CI job is a promise,
   and nothing in the tree checked the job existed.

2. THE NO-DOCKER HALF QUIETLY ACQUIRES A DOCKER DEPENDENCY. Docker Desktop was
   RUNNING on the host this card was built on, so a gated test that started
   probing a live daemon would have passed here, passed on any developer laptop,
   and passed on the GitHub-hosted runners (which also have Docker) — while
   being wrong about what the gate proves. It would surface only on an
   air-gapped or container-less runner, i.e. the deployment this project
   targets. That asymmetry is what keeps such a defect alive, and it is the same
   asymmetry that kept the `python-dateutil` sites in tsg-iso-03 alive.

So the split is asserted here, on the DEFAULT runners, with no Docker.

WHAT THIS FILE IS NOT
=====================
It is not a second copy of the census. It reads `tools/ci/gated_test_list.py`'s
own `census()` and `load_gate_config()` for what "gated" and "excluded" mean,
because a private re-derivation could disagree with the gate and be wrong in the
reassuring direction.

MEASURED 2026-09-05: the fifteen gated floci/emulator test files pass with the
Docker socket PROVEN ABSENT (`DOCKER_HOST=/nonexistent/does-not-exist.sock`) —
389 passed. That measurement is what the standing guard below keeps true.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

DOCKER_SUITE = "tests/cloud/test_floci_container_docker.py"
GATE_CONFIG = REPO_ROOT / "args" / "test_gating_gate.yaml"
REQUIREMENTS = REPO_ROOT / "requirements.txt"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "floci-iac-gate.yml"

#: The distribution, and the top-level module it installs. They differ, and both
#: matter: the first is what `pip install` takes, the second is what an import
#: line names. A guard that knew only one of them could not check the other.
DISTRIBUTION = "testcontainers-floci"
MODULE = "floci"

#: The gated floci/emulator suite this card measured as Docker-free. Enumerated
#: rather than globbed: a glob would silently stop covering a file that got
#: renamed, and "the set shrank" is the failure this list exists to notice.
GATED_NO_DOCKER_SUITE = (
    "tests/cloud/test_emulator_seam.py",
    "tests/cloud/test_emulator_seam_documented.py",
    "tests/cloud/test_studio_provider_override.py",
    "tests/cloud/test_workflow_template_modes.py",
    "tests/cloud/test_floci_compose_profile.py",
    "tests/cloud/test_floci_component_registration.py",
    "tests/cloud/test_floci_infra_canvas.py",
    "tests/cloud/test_floci_registry.py",
    "tests/cloud/test_floci_runtime_images.py",
    "tests/databridge/test_floci_connector.py",
    "tests/databridge/test_floci_grant.py",
    "tests/ci/test_floci_iac_gate.py",
    "tests/infra_canvas/test_one_preapply_gate.py",
    "tests/studio/test_gns3_sim_dry_run_starts_nothing.py",
    "tests/test_floci_twin_adapter.py",
)


def _gate_module():
    """The gate's own module. Imported, never re-derived.

    Loaded by path rather than imported as `tools.ci.gated_test_list` for the
    same reason `tools/ci/skip_census.py` does it: this is the definition of
    "gated" and "excluded", and a second copy could disagree with the gate while
    being wrong in the reassuring direction.
    """
    import importlib.util

    path = REPO_ROOT / "tools" / "ci" / "gated_test_list.py"
    spec = importlib.util.spec_from_file_location("_flx_gated_test_list", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sets():
    """(gated, excluded) as SETS.

    `census()` returns COUNTS for both, so the sets are rebuilt here from the
    gate's own `collect_test_files` / `gated_targets` / `_matches` — the same
    three calls, in the same order, that `census()` itself makes. Note the file
    list comes from git-TRACKED files, so a brand-new test file is invisible
    until it is staged.
    """
    gate = _gate_module()
    config = gate.load_gate_config(REPO_ROOT)
    test_files = gate.collect_test_files(REPO_ROOT, config)
    gated = gate.gated_targets(REPO_ROOT, test_files)

    excluded = set()
    for rule in config.get("exclusions") or []:
        pattern = str((rule or {}).get("pattern", ""))
        if not pattern:
            continue
        excluded |= {f for f in test_files if gate._matches(f, pattern)}
    # The gate's own precedence: naming a file in an allowlist gates it whatever
    # glob covers it.
    excluded -= gated
    return gated, excluded


def _gate_config() -> dict:
    return yaml.safe_load(GATE_CONFIG.read_text(encoding="utf-8")) or {}


def _exclusion_rule(pattern: str):
    for rule in _gate_config().get("exclusions") or []:
        if str((rule or {}).get("pattern", "")) == pattern:
            return rule
    return None


def _source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}


# ── The Docker half is off the default runners, LAWFULLY ───────────────────


def test_the_docker_suite_exists_at_the_path_everything_else_names():
    """Three files name this path. If it moves, they all go stale at once."""
    assert (REPO_ROOT / DOCKER_SUITE).is_file(), (
        "%s is named by args/test_gating_gate.yaml, requirements.txt and the "
        "floci workflow; it is not in the tree" % DOCKER_SUITE
    )


def test_the_docker_suite_is_excluded_by_a_rule_carrying_a_written_reason():
    """The sanctioned door: an `exclusions:` entry, with a REASON.

    A bare pattern is not the door — the reason is what a reviewer reads to
    decide whether the exclusion is still true, and `gated_test_list` reports a
    pattern matching nothing as STALE for the same purpose.
    """
    rule = _exclusion_rule(DOCKER_SUITE)
    assert rule is not None, (
        "no exclusion names %s; without one --check-coverage fails it as an "
        "unlisted new test file" % DOCKER_SUITE
    )
    reason = str(rule.get("reason") or "").strip()
    assert len(reason) >= 80, "an exclusion reason this short is not a reason"
    assert "docker" in reason.lower(), "the reason does not state the requirement"


def test_the_exclusion_is_not_stale_and_the_census_agrees_it_is_excluded():
    """Asked of the gate's own census, not of a private re-derivation."""
    census = _gate_module().census(REPO_ROOT)
    assert census.get("ran") is not False

    gated, excluded = _sets()
    stale = set(census.get("stale_exclusions") or [])

    assert DOCKER_SUITE in excluded, (
        "the census does not consider %s excluded (excluded=%d)"
        % (DOCKER_SUITE, len(excluded))
    )
    assert DOCKER_SUITE not in gated, (
        "the docker suite is GATED — it would start a container on every PR"
    )
    assert DOCKER_SUITE not in stale, "the exclusion pattern matches nothing"


def test_the_docker_suite_is_not_in_the_backlog_census():
    """`excluded` and `backlog` are different claims and must not both hold.

    A backlog line says "should be gated, is not yet" — a debt to drain. This
    file should never be gated on the default runners, so a line here would
    point `gate_promoter` at it and inflate a ratchet nobody can lower.
    """
    backlog = (REPO_ROOT / "args" / "ci_test_backlog.txt").read_text(encoding="utf-8")
    assert DOCKER_SUITE not in backlog.split(), (
        "%s is in the backlog census AND excluded; pick one" % DOCKER_SUITE
    )


def test_the_docker_suite_owes_no_skip_census_entry():
    """Its `importorskip` is lawful only because the file is not gated.

    skip_census scopes itself to gated modules. This asserts the two facts stay
    consistent: if a future card gates the file, this fails and the skip has to
    go before the gate lands — rather than the skip quietly breaching skip_max,
    which may only go DOWN.
    """
    census_txt = (REPO_ROOT / "args" / "ci_skip_census.txt").read_text(encoding="utf-8")
    assert DOCKER_SUITE not in census_txt, (
        "a skip census entry for an EXCLUDED file: it costs skip_max headroom "
        "and buys nothing"
    )


# ── The dependency is declared, and the import names it ────────────────────


def test_the_container_library_is_declared_in_requirements():
    """Undeclared is a cliff a clean install finds at the first call.

    The requirement is on the DISTRIBUTION name; a commented line is not a
    declaration, so the match is anchored to the start of a line.
    """
    lines = [
        ln.strip() for ln in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert any(re.match(r"^%s\b" % re.escape(DISTRIBUTION), ln) for ln in lines), (
        "%s is not declared (uncommented) in requirements.txt" % DISTRIBUTION
    )


def test_the_docker_suite_guards_its_import_and_names_the_distribution():
    """The skip reason is the only thing a reader sees when it skips.

    `No module named 'floci'` does not tell anyone what to install, and the
    module name (`floci`) is not the distribution name (`testcontainers-floci`)
    — so both are required in the reason.
    """
    source = _source(DOCKER_SUITE)
    tree = ast.parse(source)

    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "importorskip":
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and arg.value == MODULE:
                guarded = True
                reason = ""
                for kw in node.keywords:
                    if kw.arg == "reason":
                        reason = ast.unparse(kw.value)
                assert DISTRIBUTION in reason, (
                    "the skip reason does not name the distribution to install"
                )
    assert guarded, (
        "%s imports `%s` without an importorskip guard; a bare import makes "
        "`pytest tests/` fail on any host without the library"
        % (DOCKER_SUITE, MODULE)
    )


# ── The excluded file is RUN somewhere, and a skip there is refused ─────────


def test_a_ci_job_actually_runs_the_excluded_suite():
    """Excluded is not "unrun".

    The exclusion's reason cites this workflow. That citation is a promise, and
    this is the check that it is kept — otherwise the whole split is a dead
    suite with paperwork.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert DOCKER_SUITE in text, (
        "no job in %s runs %s" % (WORKFLOW.name, DOCKER_SUITE)
    )

    jobs = _workflow().get("jobs") or {}
    runner = [
        name for name, job in jobs.items()
        if DOCKER_SUITE in yaml.safe_dump(job)
    ]
    assert runner, "the path appears in the file but in no job"


def test_that_job_is_never_neutralised():
    """A `|| true` or a `continue-on-error` here gates nothing.

    The same rule the rest of this workflow already states for the IaC gate.
    """
    jobs = _workflow().get("jobs") or {}
    for name, job in jobs.items():
        dumped = yaml.safe_dump(job)
        if DOCKER_SUITE not in dumped:
            continue
        assert job.get("continue-on-error") is not True, (
            "job %s is continue-on-error" % name
        )
        for step in job.get("steps") or []:
            assert step.get("continue-on-error") is not True, (
                "a step of %s is continue-on-error" % name
            )
            assert "|| true" not in str(step.get("run") or ""), (
                "a step of %s wraps its command in `|| true`" % name
            )


def test_that_job_refuses_a_run_that_skipped_itself():
    """On a runner WITH Docker, a skip means the assertions never ran.

    A gated file gets this from skip_census. An excluded file has to bring its
    own refusal, or `importorskip` turns the one job that ever runs these
    assertions into a green no-op.
    """
    jobs = _workflow().get("jobs") or {}
    target = [job for job in jobs.values() if DOCKER_SUITE in yaml.safe_dump(job)]
    assert target, "no job runs the docker suite"

    dumped = yaml.safe_dump(target[0])
    assert "skipped" in dumped, (
        "the job does not inspect its own skip count, so a fully-skipped run "
        "would report green"
    )
    assert "junitxml" in dumped, (
        "nothing captures a machine-readable report, so the skip count cannot "
        "be re-derived"
    )


def test_that_job_is_not_one_of_the_four_required_checks():
    """The required set is Lint, Test, Security Scan, Helm Lint.

    A job that starts an emulator on every PR puts itself in front of every
    merge on a near-serial runner pool, which is how a gate earns a bypass. The
    admission rule is the label / dispatch / schedule one this workflow already
    uses.
    """
    jobs = _workflow().get("jobs") or {}
    target = {n: j for n, j in jobs.items() if DOCKER_SUITE in yaml.safe_dump(j)}
    for name, job in target.items():
        condition = str(job.get("if") or "")
        assert condition, "job %s has no admission condition" % name
        assert "floci-gate" in condition, (
            "job %s does not require the opt-in label" % name
        )

    triggers = _workflow().get(True) or _workflow().get("on") or {}
    pr = (triggers or {}).get("pull_request") or {}
    assert pr.get("types") == ["labeled"], (
        "the workflow would run on every push to a labelled PR"
    )


# ── THE STANDING GUARD: the gated half stays Docker-free ───────────────────


def test_the_gated_no_docker_suite_is_all_present_and_gated():
    """The enumerated set above is what was MEASURED Docker-free.

    If a file leaves the gate, the measurement stops covering it and the guard
    below stops guarding it — silently, unless this fails.
    """
    gated, _excluded = _sets()
    missing = [f for f in GATED_NO_DOCKER_SUITE if f not in gated]
    assert missing == [], "measured Docker-free but no longer gated: %r" % missing


@pytest.mark.parametrize("rel", GATED_NO_DOCKER_SUITE)
def test_no_gated_floci_test_imports_the_container_library(rel):
    """The container library is the Docker half's alone.

    A gated file importing it would pass here (Docker Desktop is running on the
    build host) and on the GitHub runners, and fail only on the container-less
    deployment this project targets — the asymmetry that hides this class of
    defect. Checked by AST, so a mention in prose is fine and a binding is not.
    """
    tree = ast.parse(_source(rel))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != MODULE, (
                    "%s imports `%s` — that belongs to the docker half" % (rel, MODULE)
                )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root != MODULE, (
                "%s imports from `%s` — that belongs to the docker half" % (rel, MODULE)
            )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "importorskip":
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant):
                    assert arg.value != MODULE, (
                        "%s importorskips `%s`: on a runner WITHOUT Docker it "
                        "would skip, and a gated file that skips is unmeasured, "
                        "not passing" % (rel, MODULE)
                    )


@pytest.mark.parametrize("rel", GATED_NO_DOCKER_SUITE)
def test_no_gated_floci_test_shells_out_to_docker(rel):
    """A `docker` subprocess is the other way a gated test acquires the dependency.

    Matched on the COMMAND position (a quoted `docker` token at the head of an
    argv list or a command string), so a test that merely discusses docker in an
    assertion message, or reads the seam's `docker_backed()` tri-state, is
    untouched — those are the correct things for a gated test to do.
    """
    source = _source(rel)
    offenders = [
        "%s:%d" % (rel, n)
        for n, line in enumerate(source.splitlines(), 1)
        if re.search(r"""["'](?:docker)["']\s*,""", line)
        or re.search(r"""["']docker (?:run|ps|info|pull|compose|logs)\b""", line)
    ]
    assert offenders == [], "gated test shells out to docker: %r" % offenders


# ── The Docker half takes the deployment's pin, not the library's default ──


def test_the_docker_suite_never_takes_the_libraries_latest_default():
    """`FlociContainer()` with no image resolves `floci/floci:latest`.

    Taking it would pull at run time — defeating the pinned-digest local cache
    the air-gap posture rests on (flx-airgap-01/02) — and would test a different
    emulator from the one the deployment runs. Structural, because a behavioural
    test would need the container and so could never run on this side of the
    split.
    """
    tree = ast.parse(_source(DOCKER_SUITE))
    constructions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "FlociContainer"
    ]
    assert constructions, "the docker suite constructs no container"
    for node in constructions:
        image = [kw for kw in node.keywords if kw.arg == "image"]
        assert image, (
            "FlociContainer() built with no `image=`; that is the library's "
            "`latest` default"
        )
        rendered = ast.unparse(image[0].value)
        assert "seam.IMAGE" in rendered or "emulator.IMAGE" in rendered, (
            "the image is %s, not the seam's pin" % rendered
        )


def test_the_docker_suite_uses_the_declared_region_not_the_libraries():
    """The library defaults to `us-east-1`; the operator declared `us-gov-west-1`.

    A suite that silently ran in `us-east-1` would exercise none of the GovCloud
    presets the twin adapter's `target_csp` exists to reach.
    """
    from tools.cloud import emulator as seam

    assert seam.region() == "us-gov-west-1"

    source = _source(DOCKER_SUITE)
    assert "with_region(seam.region())" in source, (
        "the container is not told the seam's region"
    )
    assert not re.search(r"""["']us-east-1["']""", source), (
        "the docker suite hard-codes the library's default region"
    )
