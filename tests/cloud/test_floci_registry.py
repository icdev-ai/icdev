# CUI // SP-CTI — floci's registry posture and its credentials (flx-airgap-03)
"""An INTERNAL mirror is not a public-internet dependency. A public one is.

BOTH DIRECTIONS ARE ASSERTED OVER THE SAME UNCACHED DESIGN, and that is the
whole point of this file. flx-airgap-02 could only answer "is it on this disk",
so a registry-mandating site — which cannot pre-seed each host's cache — read
``blocked`` on a deployment with no public-internet dependency at all. The
extension must therefore fire on a config pointing at a PUBLIC registry and NOT
fire on one pointing at a MIRROR; a rule that always fires and a rule that never
fires are indistinguishable from a test that checks one side.

THE CACHE IS ALWAYS STATED via an injected prober and the REGISTRY POSTURE is
always stated via an injected declaration. A test reading either from the host
would pass or fail on a `docker rmi` nobody ran, or on whatever
args/floci_registry.yaml happens to say on the day.
"""
from __future__ import annotations

import pytest

from tools.cloud import floci_registry as fr
from tools.cloud import runtime_images as ri
from tools.twin_core import airgap_rules as ar

RULE_ID = "airgap-emulator-runtime-images"

# Two container-backed services, each with its variant named, so the required
# set is DETERMINED and the verdict turns on the registry posture alone.
DESIGN = {
    "resources": [
        {"type": "aws_lambda_function", "name": "ingest", "runtime": "python3.11"},
        {"type": "aws_db_instance", "name": "store", "engine": "postgres"},
    ]
}

# `.mil` is in args/twin_airgap_rules.yaml's internal_host_suffixes — the SAME
# allowlist airgap-internal-registry matches against. Not a private list.
INTERNAL_MIRROR = "registry.internal.example.mil:5000"
PUBLIC_MIRROR = "mirror.gcr.io"


def _mirrored(mirror: str) -> dict:
    """A declaration mirroring BOTH registries the measured image set spans."""
    return {
        "enabled": True,
        "registries": [
            {
                "registry": "docker.io",
                "mirror": mirror,
                "mechanism": fr.MECHANISM_DAEMON_MIRROR,
                "username_ref": "env:FLOCI_MIRROR_USERNAME",
                "password_ref": "env:FLOCI_MIRROR_PASSWORD",
            },
            {
                "registry": "public.ecr.aws",
                "mirror": mirror,
                "mechanism": fr.MECHANISM_REPOSITORY_REWRITE,
                "username_ref": "env:FLOCI_MIRROR_USERNAME",
                "password_ref": "env:FLOCI_MIRROR_PASSWORD",
            },
        ],
    }


def _no_declaration() -> dict:
    return {"enabled": False, "registries": []}


def _cache(state, basis="stated by the test"):
    def prober(image):
        return {
            "ref": image["ref"],
            "digest": image.get("digest"),
            "state": state,
            "basis": basis,
        }

    return prober


EMPTY_CACHE = _cache(ri.ABSENT)
FULL_CACHE = _cache(ri.PRESENT_TAGGED)


# ---------------------------------------------------------------------------
# registry_of — Docker Hub is implicit, and getting this wrong mirrors nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("postgres:16.3-alpine", "docker.io"),
        ("mysql:8.0.36", "docker.io"),
        ("valkey/valkey:8", "docker.io"),            # org/name is still Hub
        ("public.ecr.aws/lambda/python:3.11", "public.ecr.aws"),
        ("registry.internal.example.mil:5000/postgres:16", "registry.internal.example.mil:5000"),
        ("localhost:5000/postgres:16", "localhost:5000"),
        ("", "docker.io"),
    ],
)
def test_registry_of(ref, expected):
    assert fr.registry_of(ref) == expected


def test_every_measured_image_resolves_to_a_registry_the_example_declares():
    """The eleven measured refs span exactly the two registries the file names.

    A third would mean the worked example in args/floci_registry.yaml mirrors an
    incomplete set while reading as a complete posture.
    """
    spans = {fr.registry_of(img["ref"]) for img in ri.declared_images()}
    assert spans == {"docker.io", "public.ecr.aws"}, spans


# ---------------------------------------------------------------------------
# is_internal_host — read from THE air-gap allowlist, and port-aware
# ---------------------------------------------------------------------------


def test_internal_suffixes_come_from_the_airgap_rules_not_a_private_copy():
    """A private list here could call a host internal that the neighbouring rule
    calls public, and both would be 'the rule'."""
    import yaml

    with open(fr.AIRGAP_RULES_PATH, encoding="utf-8") as fh:
        declared = (yaml.safe_load(fh).get("allowlist") or {}).get("internal_host_suffixes")
    assert [s.lower() for s in declared] == fr.internal_host_suffixes(force=True)


@pytest.mark.parametrize(
    "host,internal",
    [
        ("registry.internal.example.mil:5000", True),   # port stripped
        ("nexus.internal", True),
        ("host.local", True),
        ("docker.io", False),
        ("public.ecr.aws", False),
        ("mirror.gcr.io", False),
        ("", False),
    ],
)
def test_is_internal_host(host, internal):
    assert fr.is_internal_host(host) is internal


# ---------------------------------------------------------------------------
# pull_origin — BOTH directions
# ---------------------------------------------------------------------------


def test_no_declaration_reports_external_for_every_image():
    """The shipped posture is byte-identical to flx-airgap-02: the local cache
    is the only discriminator, so every uncached pull leaves the enclave."""
    cfg = _no_declaration()
    for img in ri.declared_images():
        row = fr.pull_origin(img["ref"], cfg)
        assert row["external"] is True
        assert row["origin"] == fr.ORIGIN_MIRROR_DISABLED


def test_internal_mirror_makes_the_pull_internal():
    cfg = _mirrored(INTERNAL_MIRROR)
    for img in ri.declared_images():
        row = fr.pull_origin(img["ref"], cfg)
        assert row["external"] is False, row
        assert row["origin"] == fr.ORIGIN_INTERNAL_MIRROR
        assert row["mirror"] == INTERNAL_MIRROR


def test_a_public_mirror_is_still_an_external_pull():
    """THE OPPOSITE DIRECTION. Declaring a mirror is not enough — it has to be
    inside the enclave, judged by the air-gap rules' own allowlist."""
    cfg = _mirrored(PUBLIC_MIRROR)
    for img in ri.declared_images():
        row = fr.pull_origin(img["ref"], cfg)
        assert row["external"] is True, row
        assert row["origin"] == fr.ORIGIN_MIRROR_NOT_INTERNAL


def test_an_unmirrored_registry_is_external_even_when_another_is_mirrored():
    """A partial declaration must not bless the registries it does not name."""
    cfg = {
        "enabled": True,
        "registries": [
            {
                "registry": "docker.io",
                "mirror": INTERNAL_MIRROR,
                "mechanism": fr.MECHANISM_DAEMON_MIRROR,
            }
        ],
    }
    assert fr.pull_origin("postgres:16.3-alpine", cfg)["external"] is False
    ecr = fr.pull_origin("public.ecr.aws/lambda/python:3.11", cfg)
    assert ecr["external"] is True
    assert ecr["origin"] == fr.ORIGIN_NO_MIRROR


def test_a_mirrored_row_never_claims_the_mirror_holds_the_image():
    """Mirror COMPLETENESS is a different question with a different repair."""
    row = fr.pull_origin("postgres:16.3-alpine", _mirrored(INTERNAL_MIRROR))
    assert row["verified"] is False
    assert "completeness" in row["reason"].lower()


# ---------------------------------------------------------------------------
# The declaration refuses what it must
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["username_ref", "password_ref"])
@pytest.mark.parametrize("literal", ["hunter2", "plain:hunter2", "AKIAIOSFODNN7EXAMPLE"])
def test_a_literal_credential_is_refused_not_warned(key, literal):
    """A warning still lands the secret in git, and this repository is public."""
    entry = {
        "registry": "docker.io",
        "mirror": INTERNAL_MIRROR,
        "mechanism": fr.MECHANISM_DAEMON_MIRROR,
        key: literal,
    }
    with pytest.raises(fr.RegistryDeclarationError, match="LITERAL"):
        fr.validate({"enabled": True, "registries": [entry]})


def test_reference_prefixes_match_the_seed_connections_precedent():
    """Pinned equal rather than imported: two credential vocabularies that drift
    apart mean a ref accepted by one door and refused by the other."""
    from tools.databridge import seed_connections

    assert fr.CREDENTIAL_REF_PREFIXES == seed_connections.SECRET_REF_PREFIXES


@pytest.mark.parametrize("ref", ["env:X", "vault:kv/x", "aws:x", "file:/x"])
def test_every_sanctioned_prefix_is_accepted(ref):
    entries = fr.validate({
        "enabled": True,
        "registries": [{
            "registry": "docker.io", "mirror": INTERNAL_MIRROR,
            "mechanism": fr.MECHANISM_DAEMON_MIRROR, "password_ref": ref,
        }],
    })
    assert entries["docker.io"]["password_ref"] == ref


def test_daemon_registry_mirror_is_refused_for_a_non_hub_registry():
    """Docker's `registry-mirrors` redirects Docker Hub pulls ONLY. Believing it
    for public.ecr.aws reports a clean verdict for a host that still reaches
    Amazon on first Lambda invoke."""
    with pytest.raises(fr.RegistryDeclarationError, match="DOCKER HUB"):
        fr.validate({
            "enabled": True,
            "registries": [{
                "registry": "public.ecr.aws",
                "mirror": INTERNAL_MIRROR,
                "mechanism": fr.MECHANISM_DAEMON_MIRROR,
            }],
        })


def test_repository_rewrite_is_accepted_for_a_non_hub_registry():
    """The opposite direction: the mechanism that CAN redirect it is allowed."""
    entries = fr.validate({
        "enabled": True,
        "registries": [{
            "registry": "public.ecr.aws",
            "mirror": INTERNAL_MIRROR,
            "mechanism": fr.MECHANISM_REPOSITORY_REWRITE,
        }],
    })
    assert entries["public.ecr.aws"]["mechanism"] == fr.MECHANISM_REPOSITORY_REWRITE


@pytest.mark.parametrize(
    "entry,match",
    [
        ({"mirror": INTERNAL_MIRROR, "mechanism": fr.MECHANISM_DAEMON_MIRROR}, "missing `registry`"),
        ({"registry": "docker.io", "mechanism": fr.MECHANISM_DAEMON_MIRROR}, "`mirror` is required"),
        ({"registry": "docker.io", "mirror": INTERNAL_MIRROR, "mechanism": "wishful"}, "not one of"),
    ],
)
def test_an_incomplete_entry_is_refused(entry, match):
    with pytest.raises(fr.RegistryDeclarationError, match=match):
        fr.validate({"enabled": True, "registries": [entry]})


def test_two_mirrors_for_one_registry_are_refused():
    """Picking one silently is a guess, and a guess is not a posture."""
    dup = [
        {"registry": "docker.io", "mirror": INTERNAL_MIRROR,
         "mechanism": fr.MECHANISM_DAEMON_MIRROR},
        {"registry": "docker.io", "mirror": "other.internal",
         "mechanism": fr.MECHANISM_DAEMON_MIRROR},
    ]
    with pytest.raises(fr.RegistryDeclarationError, match="declared twice"):
        fr.validate({"enabled": True, "registries": dup})


def test_the_shipped_declaration_is_valid_and_disabled():
    """Shipped disabled: this file must not change a verdict on a deployment
    that has not opted in."""
    cfg = fr.load_declaration(force=True)
    assert cfg.get("enabled") is False
    assert fr.validate(cfg) == {}


def test_the_shipped_declaration_holds_no_credential_literal():
    """A greppable guard over the committed file itself, not over a fixture."""
    text = fr.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped.startswith(("username_ref:", "password_ref:")):
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            assert value.startswith(fr.CREDENTIAL_REF_PREFIXES), line


# ---------------------------------------------------------------------------
# FLOCI_DOCKER_DOCKER_HOST — the third name, kept apart from the other two
# ---------------------------------------------------------------------------


def test_docker_host_defaults_to_the_mounted_socket():
    """Unset means today's posture: the socket compose mounts into floci."""
    got = fr.docker_host(_no_declaration(), env={})
    assert got["effective"] == fr.DEFAULT_CONTAINER_DOCKER_HOST
    assert got["remote"] is False
    assert got["basis"] == "compose default"


def test_the_environment_wins_over_the_declaration():
    """Compose reads the variable; a YAML key cannot set a container's env."""
    cfg = {"enabled": False, "registries": [], "docker_host": "tcp://declared:2376"}
    got = fr.docker_host(cfg, env={fr.DOCKER_HOST_ENV: "tcp://from-env:2376"})
    assert got["effective"] == "tcp://from-env:2376"
    assert got["basis"] == "environment"
    assert got["remote"] is True


def test_the_declaration_answers_when_the_environment_is_silent():
    cfg = {"enabled": False, "registries": [], "docker_host": "tcp://declared:2376"}
    got = fr.docker_host(cfg, env={})
    assert got["effective"] == "tcp://declared:2376"
    assert got["basis"] == "declaration"


def test_the_three_floci_docker_names_are_three_different_things():
    """THE DOCUMENTED TRAP. FLOCI_DOCKER_SOCKET is read by
    emulator.docker_basis() to answer how the HOST PYTHON PROCESS reaches a
    daemon; setting it to the compose MOUNT spelling makes
    service_supported('lambda') return a fabricated False on Windows. This
    module must never read it, and must never be read by that one.

    Asserted BEHAVIOURALLY, not by grepping the source: both names appear in
    this module's docstring precisely because the trap is documented there, so
    a text search would fail on the explanation of the thing it is checking."""
    import inspect

    from tools.cloud import emulator

    assert fr.DOCKER_HOST_ENV == "FLOCI_DOCKER_DOCKER_HOST"

    # An environment carrying the OTHER two names, and only those, must leave
    # this module on its compose default — it reads neither.
    noise = {
        "FLOCI_DOCKER_SOCKET": "tcp://wrong-answer:2375",
        "FLOCI_DOCKER_SOCKET_MOUNT": "//var/run/docker.sock",
    }
    got = fr.docker_host(_no_declaration(), env=noise)
    assert got["effective"] == fr.DEFAULT_CONTAINER_DOCKER_HOST
    assert got["basis"] == "compose default"

    # And the emulator's host-side question is untouched: it reads
    # FLOCI_DOCKER_SOCKET / DOCKER_HOST and must not have grown a third door.
    assert fr.DOCKER_HOST_ENV not in inspect.getsource(emulator)
    assert emulator.docker_basis(env={fr.DOCKER_HOST_ENV: "tcp://remote:2376"}) != (
        emulator.BASIS_DECLARED_REMOTE
    ), "emulator.docker_basis must not read FLOCI_DOCKER_DOCKER_HOST"


def test_compose_wires_the_third_name_and_defaults_to_the_mounted_socket():
    """MEASURED with `docker compose config`: unset renders
    `unix:///var/run/docker.sock` and a set value renders the remote daemon.

    The default is pinned to DEFAULT_CONTAINER_DOCKER_HOST here because the
    dangerous spelling is `${FLOCI_DOCKER_DOCKER_HOST:-}` — compose sets the key
    to an EMPTY STRING when the variable is unset, which is not the same as
    leaving DOCKER_HOST alone and would break the daemon lookup on every
    deployment that has not opted in.
    """
    import yaml

    compose = yaml.safe_load((fr.BASE_DIR / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["floci"]["environment"]
    assert env["DOCKER_HOST"] == (
        "${" + fr.DOCKER_HOST_ENV + ":-" + fr.DEFAULT_CONTAINER_DOCKER_HOST + "}"
    )
    # The mount source stays its own variable, and the host-process name is not
    # in the container's environment at all.
    assert "FLOCI_DOCKER_SOCKET" not in env
    volumes = compose["services"]["floci"]["volumes"]
    assert any("FLOCI_DOCKER_SOCKET_MOUNT" in v for v in volumes)


def test_this_module_never_reaches_a_registry():
    """No network, no subprocess. The mirror claim is about CONFIGURATION."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(fr))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for banned in ("subprocess", "socket", "requests", "urllib", "httpx"):
        assert banned not in imported, f"{banned} reached from floci_registry"


# ---------------------------------------------------------------------------
# THE EVALUATOR — one rule, one meaning of "run-time pull", both directions
# ---------------------------------------------------------------------------


def test_empty_cache_and_no_mirror_is_blocked():
    """Unchanged from flx-airgap-02, and asserted so this card cannot have
    quietly turned the default posture green."""
    report = ri.evaluate(DESIGN, prober=EMPTY_CACHE, registry_config=_no_declaration())
    assert report["state"] == ri.STATE_BLOCKED
    assert report["basis"] == ri.BASIS_EXTERNAL_PULL
    assert report["missing"]
    assert not report["mirror_served"]


def test_empty_cache_WITH_an_internal_mirror_is_satisfied():
    """THE CARD. Same design, same empty cache — the posture is what changes."""
    report = ri.evaluate(DESIGN, prober=EMPTY_CACHE, registry_config=_mirrored(INTERNAL_MIRROR))
    assert report["state"] == ri.STATE_SATISFIED
    assert report["basis"] == ri.BASIS_INTERNAL_MIRROR
    assert not report["missing"]
    assert len(report["mirror_served"]) == len(report["requirements"])


def test_empty_cache_with_a_PUBLIC_mirror_is_still_blocked():
    """THE OTHER DIRECTION, and the one that stops this becoming a rule that
    never fires: any string in `mirror:` must not silence the finding."""
    report = ri.evaluate(DESIGN, prober=EMPTY_CACHE, registry_config=_mirrored(PUBLIC_MIRROR))
    assert report["state"] == ri.STATE_BLOCKED
    assert report["basis"] == ri.BASIS_EXTERNAL_PULL
    assert not report["mirror_served"]


def test_a_full_cache_is_satisfied_on_the_local_cache_basis_whatever_the_mirror():
    """A cached image pulls nothing, so the mirror cannot change this verdict."""
    for cfg in (_no_declaration(), _mirrored(INTERNAL_MIRROR), _mirrored(PUBLIC_MIRROR)):
        report = ri.evaluate(DESIGN, prober=FULL_CACHE, registry_config=cfg)
        assert report["state"] == ri.STATE_SATISFIED
        assert report["basis"] == ri.BASIS_LOCAL_CACHE


def test_absent_from_cache_is_reported_even_when_a_mirror_serves_it():
    """'would be pulled from outside' and 'is not on this disk' are different
    facts. A mirrored deployment must still be able to see the second."""
    report = ri.evaluate(DESIGN, prober=EMPTY_CACHE, registry_config=_mirrored(INTERNAL_MIRROR))
    assert len(report["absent_from_cache"]) == len(report["requirements"])
    assert report["state"] == ri.STATE_SATISFIED


def test_a_digest_mismatch_behind_an_internal_mirror_is_not_a_blocker():
    """The wrong digest is re-pulled — from the mirror, so it stays internal."""
    report = ri.evaluate(
        DESIGN, prober=_cache(ri.DIGEST_MISMATCH), registry_config=_mirrored(INTERNAL_MIRROR)
    )
    assert report["state"] == ri.STATE_SATISFIED
    assert not report["mismatched"]
    report = ri.evaluate(
        DESIGN, prober=_cache(ri.DIGEST_MISMATCH), registry_config=_no_declaration()
    )
    assert report["state"] == ri.STATE_BLOCKED
    assert report["mismatched"]


def test_an_unreadable_cache_stays_unmeasured_under_any_posture():
    """A mirror says where a pull GOES. It cannot answer what is on the disk,
    so it must never upgrade an unmeasured cache to a clean bill of health."""
    for cfg in (_no_declaration(), _mirrored(INTERNAL_MIRROR)):
        report = ri.evaluate(DESIGN, prober=_cache(ri.UNMEASURED), registry_config=cfg)
        assert report["state"] == ri.STATE_UNMEASURED


def test_a_malformed_declaration_reads_external_and_says_which():
    """Fail-closed: an unusable mirror declaration is not 'no mirror', and it
    must surface the blocker rather than bless the deployment."""
    bad = {"enabled": True, "registries": [{"registry": "docker.io", "mirror": INTERNAL_MIRROR,
                                            "mechanism": "wishful"}]}
    report = ri.evaluate(DESIGN, prober=EMPTY_CACHE, registry_config=bad)
    assert report["state"] == ri.STATE_BLOCKED
    assert "refused" in (report["registry_posture"] or {}).get("basis", "")


def test_the_satisfied_reason_never_claims_the_images_are_cached_when_they_are_not():
    report = ri.evaluate(DESIGN, prober=EMPTY_CACHE, registry_config=_mirrored(INTERNAL_MIRROR))
    assert "already cached" not in report["reason"]
    assert "NOT VERIFIED" in report["reason"] or "not verified" in report["reason"].lower()


# ---------------------------------------------------------------------------
# The air-gap RULE — the same two directions, through the consumer
# ---------------------------------------------------------------------------


def _rule_config() -> dict:
    cfg = ar.load_rules(force=True)
    return {**cfg, "enabled": True}


def test_the_rule_fires_on_a_public_registry_posture():
    violations = ar.evaluate_airgap(
        DESIGN, config=_rule_config(), active=True,
        runtime_image_probe=EMPTY_CACHE, registry_config=_mirrored(PUBLIC_MIRROR),
    )
    assert [v for v in violations if v.get("rule_id") == RULE_ID]


def test_the_rule_does_NOT_fire_on_an_internal_mirror_posture():
    violations = ar.evaluate_airgap(
        DESIGN, config=_rule_config(), active=True,
        runtime_image_probe=EMPTY_CACHE, registry_config=_mirrored(INTERNAL_MIRROR),
    )
    assert not [v for v in violations if str(v.get("rule_id", "")).startswith(RULE_ID)]


def test_the_rule_still_fires_with_no_declaration():
    """flx-airgap-02's behaviour is preserved for a deployment that has not
    opted in, which is every deployment until someone edits the file."""
    violations = ar.evaluate_airgap(
        DESIGN, config=_rule_config(), active=True,
        runtime_image_probe=EMPTY_CACHE, registry_config=_no_declaration(),
    )
    assert [v for v in violations if v.get("rule_id") == RULE_ID]
