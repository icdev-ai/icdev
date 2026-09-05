# CUI // SP-CTI — floci runtime base images + the air-gap rule (flx-airgap-02)
"""An emulator that would PULL at run time is a deployment_blocker.

BOTH DIRECTIONS ARE ASSERTED, and that is the point of this file. A rule that
never fires and a rule that always fires are indistinguishable from a passing
test that only checks one side — so every behavioural test here has a partner
asserting the opposite outcome over the SAME design with a DIFFERENT cache.

The cache is always STATED via an injected prober, never read from whatever
this host happens to hold. A test that probed the live daemon would pass or
fail on a ``docker rmi`` nobody in the test ran, and would report ``unmeasured``
on every CI runner.
"""
from __future__ import annotations

import pytest

from tools.cloud import runtime_images as ri
from tools.twin_core import airgap_rules as ar

RULE_ID = "airgap-emulator-runtime-images"
UNMEASURED_RULE_ID = f"{RULE_ID}-unmeasured"

# A design declaring two container-backed services, each with its variant named.
DESIGN = {
    "resources": [
        {"type": "aws_lambda_function", "name": "ingest", "runtime": "python3.11"},
        {"type": "aws_db_instance", "name": "store", "engine": "postgres"},
    ]
}


def _cache(state, basis="stated by the test"):
    """A prober that reports ``state`` for every image."""

    def prober(image):
        return {"ref": image["ref"], "digest": image.get("digest"), "state": state, "basis": basis}

    return prober


def _cache_holding(*refs):
    """A prober where only ``refs`` are present; everything else is absent."""
    wanted = set(refs)

    def prober(image):
        present = image["ref"] in wanted
        return {
            "ref": image["ref"],
            "digest": image.get("digest"),
            "state": ri.PRESENT_TAGGED if present else ri.ABSENT,
            "basis": "stated by the test",
        }

    return prober


def _rule_violations(design, prober, rule_id=RULE_ID):
    return [
        v
        for v in ar.evaluate_airgap(design, source_canvas="idc", runtime_image_probe=prober)
        if v.get("rule_id") == rule_id
    ]


# ── The declaration itself ────────────────────────────────────────────────


def test_declaration_is_enabled_and_measured():
    cfg = ri.load_declaration(force=True)
    assert cfg.get("enabled") is True
    assert cfg.get("measured_on"), "the declaration must record WHEN it was measured"
    assert cfg.get("measured_against", {}).get("method"), "and HOW"


def test_every_declared_image_carries_a_digest():
    """A ref without a digest cannot be verified, and two of these are `:latest`."""
    for row in ri.declared_images():
        assert row["digest"], f"{row['ref']} declares no digest"
        assert row["digest"].startswith("sha256:"), row["ref"]
        assert len(row["digest"].split(":", 1)[1]) == 64, row["ref"]


def test_mutable_tags_are_declared_as_such():
    """floci names two of its own backing images by `:latest`. Say so, don't hide it."""
    by_ref = {r["ref"]: r for r in ri.declared_images()}
    for ref, row in by_ref.items():
        if ref.endswith(":latest"):
            assert row["mutable_tag"] is True, f"{ref} is a mutable tag and must be flagged"


def test_the_ecs_workload_image_is_not_declared_as_a_runtime_base():
    """`alpine:3.19` was pulled during the measured run BY THE PROBE'S OWN TASK
    DEFINITION. It is a workload image, not a floci runtime base, and recording
    it would tell an operator to vendor an image floci never chooses."""
    assert "alpine:3.19" not in {r["ref"] for r in ri.declared_images()}


# ── Deriving the requirement from a design ────────────────────────────────


def test_variant_is_load_bearing_python_vs_nodejs():
    """MEASURED: the runtime, not the service, picks the image."""
    py, _ = ri.images_for(["lambda"], variants=["python3.11"])
    node, _ = ri.images_for(["lambda"], variants=["nodejs20.x"])
    assert [r["ref"] for r in py] == ["public.ecr.aws/lambda/python:3.11"]
    assert [r["ref"] for r in node] == ["public.ecr.aws/lambda/nodejs:20"]


def test_variant_is_load_bearing_postgres_vs_mysql():
    pg, _ = ri.images_for(["rds"], variants=["postgres"])
    my, _ = ri.images_for(["rds"], variants=["mysql"])
    assert [r["ref"] for r in pg] == ["postgres:16.3-alpine"]
    assert [r["ref"] for r in my] == ["mysql:8.0.36"]


def test_a_service_with_no_resolvable_variant_is_undetermined_not_guessed():
    """The honest third answer. Guessing a runtime fabricates either a blocker
    or a clean bill, and both are worse than saying we cannot tell."""
    images, undetermined = ri.images_for(["lambda"], variants=[])
    assert images == []
    assert undetermined == ["lambda"]


def test_a_variantless_service_needs_exactly_one_image():
    images, undetermined = ri.images_for(["opensearch"], variants=[])
    assert [r["ref"] for r in images] == ["opensearchproject/opensearch:2.19.5"]
    assert undetermined == []


def test_ecs_implies_the_ecr_registry_container():
    """Declared as DATA in the yaml `implies` block, not hard-coded in Python."""
    assert "ecr" in ri.declared_services({"type": "aws_ecs_cluster"})


def test_services_are_derived_from_iac_resource_types():
    found = ri.declared_services(DESIGN)
    assert {"lambda", "rds"} <= found


def test_a_design_with_no_container_backed_service_requires_nothing():
    """The negative control for the derivation itself: an S3-only design pulls
    nothing, so `satisfied` here is a real measurement over an empty set and is
    reported as such."""
    report = ri.evaluate({"resources": [{"type": "aws_s3_bucket", "name": "docs"}]},
                         prober=_cache(ri.ABSENT))
    assert report["state"] == ri.STATE_SATISFIED
    assert report["requirements"] == []


# ── THE RULE: both directions ─────────────────────────────────────────────


def test_rule_FIRES_when_a_required_image_is_missing():
    violations = _rule_violations(DESIGN, _cache(ri.ABSENT))
    assert violations, "a host missing every required image must trip the rule"
    assert {v["severity"] for v in violations} == {"blocker"}, "deployment_blocker -> blocker"
    named = " ".join(v["title"] for v in violations)
    assert "public.ecr.aws/lambda/python:3.11" in named
    assert "postgres:16.3-alpine" in named


def test_rule_does_NOT_fire_when_the_cache_is_fully_populated():
    """The partner assertion. Without it, a rule that always fires would pass
    the test above."""
    assert _rule_violations(DESIGN, _cache(ri.PRESENT_TAGGED)) == []


def test_rule_fires_on_exactly_the_missing_image_not_the_present_one():
    """Sharper than either direction alone: a rule that fired on the whole
    requirement whenever ANY image was missing would pass both tests above."""
    violations = _rule_violations(
        DESIGN, _cache_holding("public.ecr.aws/lambda/python:3.11")
    )
    assert len(violations) == 1
    assert "postgres:16.3-alpine" in violations[0]["title"]
    assert "lambda/python" not in violations[0]["title"]


@pytest.mark.parametrize(
    "present_state",
    [ri.PRESENT_TAGGED, ri.PRESENT_BY_DIGEST, ri.PRESENT_BY_ID],
)
def test_every_present_rung_satisfies_the_rule(present_state):
    """`present_by_id` is the one that matters operationally: MEASURED
    2026-09-05, a `docker save repo@sha256:...` bundle loaded on the high side
    carries NO tag and NO RepoDigest and resolves by image ID alone. A rule
    that only accepted `present_tagged` would report a fabricated blocker for a
    correctly vendored, fully offline-capable host."""
    assert _rule_violations(DESIGN, _cache(present_state)) == []


def test_a_digest_mismatch_is_a_blocker_and_is_not_called_absent():
    """Present under the tag but a DIFFERENT image. Not absent, and a different
    repair — re-vendor, don't re-mirror."""
    violations = _rule_violations(DESIGN, _cache(ri.DIGEST_MISMATCH))
    assert len(violations) == 2
    assert {v["severity"] for v in violations} == {"blocker"}
    assert all(ri.DIGEST_MISMATCH in v["detail"] for v in violations)


# ── Unmeasured is never clean, and never a blocker ────────────────────────


def test_unreadable_cache_is_reported_and_is_not_silence():
    violations = _rule_violations(DESIGN, _cache(ri.UNMEASURED), rule_id=UNMEASURED_RULE_ID)
    assert len(violations) == 1, "a cache nobody could read must not read as clean"
    assert "UNMEASURED" in violations[0]["title"]


def test_unreadable_cache_is_not_a_blocker():
    """It PROVES NOTHING. Blocking every CI runner and reviewer laptop on a
    docker daemon they do not have is how a gate earns itself a `|| true`."""
    violations = _rule_violations(DESIGN, _cache(ri.UNMEASURED), rule_id=UNMEASURED_RULE_ID)
    assert {v["severity"] for v in violations} == {"medium"}
    assert _rule_violations(DESIGN, _cache(ri.UNMEASURED)) == [], (
        "an unmeasured cache must not be emitted under the blocking rule id"
    )


def test_an_undetermined_variant_is_reported_at_the_unmeasured_severity():
    design = {"resources": [{"type": "aws_lambda_function", "name": "x"}]}  # no runtime
    report = ri.evaluate(design, prober=_cache(ri.PRESENT_TAGGED))
    assert report["state"] == ri.STATE_INDETERMINATE
    violations = _rule_violations(design, _cache(ri.PRESENT_TAGGED), rule_id=UNMEASURED_RULE_ID)
    assert len(violations) == 1
    assert {v["severity"] for v in violations} == {"medium"}


def test_missing_outranks_unmeasured():
    """Ordered worst-first: a PROVEN absent image is a finding and must not be
    downgraded by an unrelated image the probe could not read."""

    def mixed(image):
        state = ri.ABSENT if "postgres" in image["ref"] else ri.UNMEASURED
        return {"ref": image["ref"], "digest": image.get("digest"), "state": state, "basis": "x"}

    report = ri.evaluate(DESIGN, prober=mixed)
    assert report["state"] == ri.STATE_BLOCKED


# ── The other rules are untouched by this one ─────────────────────────────


def test_the_string_matching_rules_still_fire_alongside():
    """This rule is additive. The four deny-by-match rules must be unaffected,
    and a design naming a public registry must still trip the registry rule."""
    graph = {"nodes": [{"id": "n1", "type": "container", "image": "docker.io/library/nginx:1"}]}
    rids = {
        v["rule_id"]
        for v in ar.evaluate_airgap(graph, active=True, runtime_image_probe=_cache(ri.PRESENT_TAGGED))
    }
    assert "airgap-internal-registry" in rids


def test_nothing_in_this_path_can_pull():
    """Structural, not behavioural. The presence probe reaches docker only
    through image_vendor's one allowlisted door, whose command set contains no
    `pull` -- so `--check` on a disconnected host cannot fabricate the green
    cache it is measuring."""
    from tools.airgap import image_vendor

    assert "pull" not in image_vendor.ALLOWED_DOCKER_COMMANDS
    assert image_vendor.ALLOWED_DOCKER_COMMANDS == frozenset(
        {"version", "image", "save", "load"}
    )


def test_repo_of_keeps_a_registry_port_and_strips_a_tag():
    assert ri.repo_of("postgres:16.3-alpine") == "postgres"
    assert ri.repo_of("public.ecr.aws/lambda/python:3.11") == "public.ecr.aws/lambda/python"
    assert ri.repo_of("localhost:5000/floci/floci") == "localhost:5000/floci/floci"
