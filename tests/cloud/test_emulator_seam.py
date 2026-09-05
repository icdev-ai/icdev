# CUI // SP-CTI
"""The ONE emulator switch, and both existing switches delegating to it (flx-seam-01).

RED AT THE MERGE BASE: `tools/cloud/emulator.py` does not exist there, so every
test in this file fails at collection. What each test then pins is a specific
way the two switches used to be able to disagree.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest
import yaml

from tools.cloud import emulator

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def emulator_logs(caplog):
    """Capture what ``emulator.logger`` emits.

    The house logger (``tools.logging.icdev_logger.get_logger``) sets
    ``propagate = False``, so caplog's root handler never sees these records.
    Attaching caplog's own handler to the logger under test is what makes the
    assertion about the SHIPPED logger rather than about a stand-in.
    """
    emulator.logger.addHandler(caplog.handler)
    previous = emulator.logger.level
    emulator.logger.setLevel(logging.DEBUG)
    emulator.reset_alias_warnings()
    try:
        yield caplog
    finally:
        emulator.logger.removeHandler(caplog.handler)
        emulator.logger.setLevel(previous)
        emulator.reset_alias_warnings()


def _warnings(records):
    return [r for r in records if r.levelno == logging.WARNING]


# ── The switch ─────────────────────────────────────────────────────────────


def test_default_posture_is_off_and_air_gap_safe():
    """A deployment that declares nothing reaches no emulator."""
    assert emulator.enabled({}) is False
    assert emulator.status({}, probe=False) == emulator.STATUS_DISABLED


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on"])
def test_floci_enabled_truthy_spellings(raw):
    assert emulator.enabled({"FLOCI_ENABLED": raw}) is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "", "maybe"])
def test_floci_enabled_falsey_spellings(raw):
    assert emulator.enabled({"FLOCI_ENABLED": raw}) is False


def test_declared_defaults():
    assert emulator.endpoint({}) == "http://localhost:4566"
    assert emulator.region({}) == "us-gov-west-1"
    assert emulator.account_id({}) == "000000000000"
    assert emulator.credentials({}) == ("test", "test")
    assert emulator.MODE == "floci"


def test_endpoint_strips_trailing_slash():
    assert emulator.endpoint({"FLOCI_ENDPOINT": "http://host:4566/"}) == "http://host:4566"


def test_account_id_must_be_twelve_digits():
    """floci isolates state per account id, so a malformed one is a config error."""
    assert emulator.account_id({"FLOCI_ACCOUNT_ID": "123456789012"}) == "123456789012"
    emulator.reset_alias_warnings()
    assert emulator.account_id({"FLOCI_ACCOUNT_ID": "42"}) == emulator.DEFAULT_ACCOUNT_ID
    emulator.reset_alias_warnings()
    assert emulator.account_id({"FLOCI_ACCOUNT_ID": "abcdefghijkl"}) == emulator.DEFAULT_ACCOUNT_ID


def test_credentials_never_leak_the_ambient_aws_pair():
    """These values reach `docker run -e` and a Terraform provider block.

    The emulator accepts any non-empty pair, so honouring a real
    AWS_ACCESS_KEY_ID buys nothing and hands live credentials to a container
    talking to localhost. A developer with GovCloud keys exported in the same
    shell is the normal case.
    """
    real = {"AWS_ACCESS_KEY_ID": "AKIAREAL0000", "AWS_SECRET_ACCESS_KEY": "s3cr3t"}
    assert emulator.credentials(real) == ("test", "test")
    assert emulator.credentials({}) == ("test", "test")

    from tools.studio.executors._base import docker_aws_flags

    flags = docker_aws_flags(real, emulator.MODE)
    assert "AKIAREAL0000" not in " ".join(flags)
    assert "s3cr3t" not in " ".join(flags)


# ── Deprecated aliases ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("canonical", "alias", "value", "read"),
    [
        ("FLOCI_ENABLED", "LOCALSTACK_ENABLED", "true", lambda e: emulator.enabled(e)),
        ("FLOCI_ENDPOINT", "LOCALSTACK_ENDPOINT", "http://ls:4566",
         lambda e: emulator.endpoint(e)),
        ("FLOCI_REGION", "LOCALSTACK_REGION", "us-east-1", lambda e: emulator.region(e)),
    ],
)
def test_deprecated_alias_resolves_and_warns(canonical, alias, value, read, emulator_logs):
    """The alias is still HONOURED, and saying so out loud is half the contract.

    Kept rather than dropped because floci's own compat layer honours
    LOCALSTACK_* anyway, and dockerfile_generator.py has already emitted those
    names into customer compose files we do not control.
    """
    resolved = read({alias: value})

    expected = value.rstrip("/") if canonical == "FLOCI_ENDPOINT" else value
    if canonical == "FLOCI_ENABLED":
        assert resolved is True
    else:
        assert resolved == expected

    warnings = _warnings(emulator_logs.records)
    assert len(warnings) == 1, f"expected exactly one deprecation line, got {warnings}"
    message = warnings[0].getMessage()
    assert alias in message and canonical in message


def test_canonical_wins_over_the_alias_and_warns_about_nothing(emulator_logs):
    got = emulator.endpoint(
        {"FLOCI_ENDPOINT": "http://new:4566", "LOCALSTACK_ENDPOINT": "http://old:4566"}
    )
    assert got == "http://new:4566"
    assert _warnings(emulator_logs.records) == []


def test_alias_warning_is_deduplicated_per_process(emulator_logs):
    """Read on every call — a per-read warning would be noise nobody reads."""
    for _ in range(5):
        emulator.endpoint({"LOCALSTACK_ENDPOINT": "http://ls:4566"})
    assert len(_warnings(emulator_logs.records)) == 1


def test_empty_canonical_does_not_mask_a_set_alias():
    """`FLOCI_ENDPOINT=` has declared nothing; the deprecation must not go lossy."""
    emulator.reset_alias_warnings()
    assert emulator.endpoint(
        {"FLOCI_ENDPOINT": "", "LOCALSTACK_ENDPOINT": "http://ls:4566"}
    ) == "http://ls:4566"


# ── docker_backed / degraded_no_docker ─────────────────────────────────────


def test_docker_backed_is_tri_state_and_none_is_not_false():
    """`None` means CANNOT TELL and must never be read as an absence.

    MEASURED on the authoring host 2026-09-04: Docker Desktop 28.5.1 was
    RUNNING and os.path.exists(r"\\\\.\\pipe\\docker_engine") returned False. A
    plain existence check therefore reports a definite absence for a working
    daemon — so an unresolvable socket is None, not False.
    """
    assert emulator.docker_backed({"DOCKER_HOST": "tcp://10.0.0.1:2375"}) is True
    assert emulator.docker_backed({"DOCKER_HOST": "unix:///nope/does/not/exist.sock"}) is False
    assert emulator.docker_backed({"DOCKER_HOST": "wat"}) is None
    assert emulator.docker_backed({"DOCKER_HOST": "wat"}) is not False


def test_docker_basis_keeps_absent_and_unknown_apart():
    assert emulator.docker_basis({"DOCKER_HOST": "unix:///nope.sock"}) == (
        emulator.BASIS_SOCKET_ABSENT
    )
    assert emulator.docker_basis({"DOCKER_HOST": "wat"}) == emulator.BASIS_DECLARED_UNPARSED
    assert emulator.docker_basis({"DOCKER_HOST": "tcp://h:1"}) == emulator.BASIS_DECLARED_REMOTE


def test_container_backed_service_is_unsupported_only_when_the_socket_is_proven_absent():
    """`unsupported_without_docker` is not `[]`, and it is not a guess either.

    A docker socket is needed ONLY for container-backed services. Refusing on an
    UNPROVEN absence would fabricate a refusal for a service that works — the
    mirror image of fabricating an empty list.
    """
    absent = {"DOCKER_HOST": "unix:///nope.sock"}
    unknown = {"DOCKER_HOST": "wat"}
    present = {"DOCKER_HOST": "tcp://h:1"}

    assert emulator.service_supported("lambda", absent) is False
    assert emulator.service_supported("lambda", unknown) is True
    assert emulator.service_supported("lambda", present) is True

    # Served in-process by the emulator — no socket needed, ever.
    for svc in ("s3", "dynamodb", "sqs", "ecr", "iam", "sts", "kms"):
        assert emulator.service_supported(svc, absent) is True


def test_the_container_backed_set_is_the_measured_one():
    for svc in ("lambda", "rds", "elasticache", "opensearch", "msk", "ecs", "ec2", "eks"):
        assert svc in emulator.CONTAINER_BACKED_SERVICES
    for svc in ("s3", "dynamodb", "sqs", "sns", "ecr", "iam", "ssm", "sts", "kms"):
        assert svc not in emulator.CONTAINER_BACKED_SERVICES


def test_status_reports_degraded_no_docker_without_conflating_it_with_disabled():
    on_no_docker = {"FLOCI_ENABLED": "true", "DOCKER_HOST": "unix:///nope.sock"}
    on_with_docker = {"FLOCI_ENABLED": "true", "DOCKER_HOST": "tcp://h:1"}

    assert emulator.status(on_no_docker, probe=False) == emulator.STATUS_DEGRADED_NO_DOCKER
    assert emulator.status(on_with_docker, probe=False) == emulator.STATUS_ENABLED
    assert emulator.status({"DOCKER_HOST": "unix:///nope.sock"}, probe=False) == (
        emulator.STATUS_DISABLED
    )
    assert len(
        {
            emulator.STATUS_DISABLED,
            emulator.STATUS_ENABLED,
            emulator.STATUS_UNREACHABLE,
            emulator.STATUS_DEGRADED_NO_DOCKER,
        }
    ) == 4


def test_status_reports_unreachable_when_the_probe_fails(monkeypatch):
    monkeypatch.setattr(emulator, "reachable", lambda env=None, timeout=2.0: False)
    assert emulator.status({"FLOCI_ENABLED": "true"}) == emulator.STATUS_UNREACHABLE


def test_unreachable_outranks_no_docker():
    """Nothing answers at all — that is the finding, not the missing socket."""
    env = {"FLOCI_ENABLED": "true", "FLOCI_ENDPOINT": "http://127.0.0.1:1",
           "DOCKER_HOST": "unix:///nope.sock"}
    assert emulator.status(env, timeout=0.2) == emulator.STATUS_UNREACHABLE


def test_nothing_in_the_module_touches_the_network_at_import_time():
    """Every studio executor imports this seam.

    A probe at module scope would put a socket timeout on the front of every
    workflow step, and would fire on an air-gapped host that never asked.
    """
    src = (_ROOT / "tools" / "cloud" / "emulator.py").read_text(encoding="utf-8")
    networking = {"urlopen", "reachable", "health", "status", "Request"}
    offenders = []
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            name = getattr(inner.func, "attr", None) or getattr(inner.func, "id", None)
            if name in networking:
                offenders.append(f"line {inner.lineno}: {name}()")
    assert not offenders, "network call at import time: " + "; ".join(offenders)


# ── Switch #1: feature_flags.localstack() delegates ────────────────────────


def test_feature_flags_delegates_to_the_seam(monkeypatch):
    from tools.databridge.feature_flags import IntegrationFeatureFlags

    monkeypatch.delenv("LOCALSTACK_ENABLED", raising=False)
    monkeypatch.delenv("FLOCI_ENABLED", raising=False)
    assert IntegrationFeatureFlags.localstack().enabled is False

    monkeypatch.setenv("FLOCI_ENABLED", "true")
    status = IntegrationFeatureFlags.localstack()
    assert status.enabled is True
    assert status.reason == ""


def test_feature_flags_honours_the_deprecated_alias(monkeypatch):
    from tools.databridge.feature_flags import IntegrationFeatureFlags

    monkeypatch.delenv("FLOCI_ENABLED", raising=False)
    monkeypatch.setenv("LOCALSTACK_ENABLED", "true")
    assert IntegrationFeatureFlags.localstack().enabled is True


def test_feature_flags_reason_names_a_compose_profile_that_actually_EXISTS(
    monkeypatch,
):
    """The reason may name a profile only while docker-compose.yml declares it.

    Telling an operator to run `docker compose --profile localstack up -d` sent
    them at a profile that has never existed in this tree. flx-compose-01
    declares a `floci` profile, so the reason can finally point somewhere real
    -- and this test RE-DERIVES the profile from the compose file rather than
    trusting the string, which is the whole difference between the two.
    """
    from tools.databridge.feature_flags import IntegrationFeatureFlags

    monkeypatch.delenv("LOCALSTACK_ENABLED", raising=False)
    monkeypatch.delenv("FLOCI_ENABLED", raising=False)
    reason = IntegrationFeatureFlags.localstack().reason

    assert "--profile localstack" not in reason
    assert "FLOCI_ENABLED" in reason
    assert "--profile floci" in reason

    compose = _ROOT / "docker-compose.yml"
    declared = {
        p
        for svc in yaml.safe_load(compose.read_text(encoding="utf-8"))["services"].values()
        for p in (svc.get("profiles") or [])
    }
    named = reason.split("--profile ", 1)[1].split()[0]
    assert named in declared, (
        f"the reason sends an operator at `--profile {named}`, which "
        f"docker-compose.yml does not declare. Declared: {sorted(declared)}"
    )


def test_compose_declares_no_localstack_SERVICE_or_PROFILE():
    """The emulator of record is floci; `localstack` must name nothing here.

    NARROWED from a substring search over the whole file (flx-compose-01). That
    search was right about the intent and wrong about the vocabulary: floci is a
    documented LocalStack drop-in and KEEPS the `/_localstack/health` path, so
    the floci service's healthcheck legitimately contains the string. Matching
    on it would have forced either an obfuscated health URL or no healthcheck at
    all -- weakening a real probe to satisfy an over-broad assertion.

    So this asserts the thing that actually matters, structurally: no service
    and no profile is NAMED localstack. That is strictly stronger than the
    substring check for this question, because a comment can no longer satisfy
    or break it. The remaining literal occurrences are then pinned to the health
    path alone, so the vocabulary cannot creep back in.
    """
    compose = _ROOT / "docker-compose.yml"
    raw = compose.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)

    assert "localstack" not in doc["services"]
    declared = {
        p for svc in doc["services"].values() for p in (svc.get("profiles") or [])
    }
    assert "localstack" not in declared, declared
    assert emulator.MODE in declared, declared

    # Every surviving mention in the DECLARATION itself is part of the drop-in
    # health path -- nothing configured is naming an emulator ICDEV no longer
    # uses. Comment lines are excluded on purpose: the floci block explains the
    # LOCALSTACK_* compat layer and the superseded spike, and prose describing
    # why the name is gone is not the name coming back.
    declaration = [
        ln for ln in raw.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    for line in declaration:
        if "localstack" in line.lower():
            assert emulator.HEALTH_PATH in line, line


def test_feature_flags_endpoint_region_credentials_delegate(monkeypatch):
    from tools.databridge.feature_flags import IntegrationFeatureFlags

    for key in ("FLOCI_ENDPOINT", "FLOCI_REGION", "LOCALSTACK_ENDPOINT", "LOCALSTACK_REGION"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("FLOCI_ENDPOINT", "http://seam:4566/")
    monkeypatch.setenv("FLOCI_REGION", "us-gov-east-1")
    assert IntegrationFeatureFlags.localstack_endpoint() == "http://seam:4566"
    assert IntegrationFeatureFlags.localstack_region() == "us-gov-east-1"
    assert IntegrationFeatureFlags.localstack_credentials() == emulator.credentials()


def test_feature_flags_no_longer_reads_the_env_var_itself():
    """Two readers of LOCALSTACK_ENABLED is how the switches came to disagree."""
    src = (_ROOT / "tools" / "databridge" / "feature_flags.py").read_text(encoding="utf-8")
    assert "getenv(\"LOCALSTACK_ENABLED\"" not in src
    assert "getenv(\"LOCALSTACK_ENDPOINT\"" not in src
    assert "getenv(\"LOCALSTACK_REGION\"" not in src


# ── Switch #2: detect_mode() delegates ─────────────────────────────────────


def test_detect_mode_returns_floci_and_decides_on_the_seam():
    from tools.studio.executors._base import detect_mode

    assert detect_mode({"FLOCI_ENABLED": "true"}) == emulator.MODE
    assert detect_mode({"FLOCI_ENABLED": "true"}) == "floci"
    assert detect_mode({"LOCALSTACK_ENABLED": "true"}) == "floci"


def test_detect_mode_no_longer_fires_on_a_bare_endpoint_variable():
    """The switch is an operator's INTENT, not leftover configuration."""
    from tools.studio.executors._base import detect_mode

    assert detect_mode({"LOCALSTACK_ENDPOINT": "http://localhost:4566"}) != "floci"


def test_an_endpoint_declared_while_the_switch_is_off_never_becomes_real_aws():
    """THE SAFETY CASE. Under the old rule this returned `localstack` — local and
    harmless. Falling through to `aws` on credentials that also happen to be set
    would send a `terraform apply` written for a local emulator at a REAL
    account. Neither reading is defensible, so it degrades to plan-only.
    """
    from tools.studio.executors._base import detect_mode

    contradictory = {
        "LOCALSTACK_ENDPOINT": "http://localhost:4566",
        "AWS_ACCESS_KEY_ID": "AKIAREAL",
        "AWS_SECRET_ACCESS_KEY": "real",
    }
    assert detect_mode(contradictory) == "dry_run"
    assert detect_mode({"FLOCI_ENDPOINT": "http://x:4566", "AWS_ACCESS_KEY_ID": "A"}) == "dry_run"


def test_detect_mode_keeps_its_other_three_verdicts():
    from tools.studio.executors._base import detect_mode

    assert detect_mode({"AWS_SAM_LOCAL": "true"}) == "sam"
    assert detect_mode({"AWS_ACCESS_KEY_ID": "AKIA"}) == "aws"
    assert detect_mode({}) == "dry_run"


def test_the_seam_survives_the_aws_env_allowlist(monkeypatch):
    """aws_env() filters env to an allowlist. Omit the seam keys from it and
    detect_mode never sees the flag — the switch would be silently dead."""
    from tools.studio.executors import _base

    monkeypatch.setattr(_base, "load_dotenv", dict)
    monkeypatch.setenv("FLOCI_ENABLED", "true")
    monkeypatch.setenv("FLOCI_ENDPOINT", "http://seam:4566")
    env = _base.aws_env()
    assert env.get("FLOCI_ENABLED") == "true"
    assert _base.detect_mode(env) == emulator.MODE


def test_is_emulated_is_the_one_predicate():
    from tools.studio.executors._base import is_emulated

    assert is_emulated(emulator.MODE) is True
    assert is_emulated("sam") is True
    assert is_emulated("aws") is False
    assert is_emulated("dry_run") is False
    assert is_emulated("localstack") is False


def test_docker_aws_flags_still_dummy_credential_the_emulated_modes():
    from tools.studio.executors._base import docker_aws_flags

    flags = docker_aws_flags({}, emulator.MODE)
    assert "AWS_ACCESS_KEY_ID=test" in flags
    assert "AWS_SECRET_ACCESS_KEY=test" in flags
    assert f"AWS_DEFAULT_REGION={emulator.DEFAULT_REGION}" in flags
    assert docker_aws_flags({}, "sam") == flags
    assert docker_aws_flags({}, "aws") == []


# ── No stale comparison anywhere in the executor package ───────────────────


def test_no_studio_executor_still_compares_against_the_old_mode_string():
    """detect_mode() moved from `localstack` to `floci`.

    A consumer left comparing against the old literal FAILS GREEN: the provider
    override is simply never written, and terraform runs against whatever the
    ambient credentials point at. Nothing else in the tree can catch that, so
    it is asserted over the source.

    A COMPARISON, not the bare literal: `gns3_sim.py` uses "localstack" as an
    output dict KEY that `tools/studio/sim/training_exporter.py` reads back, and
    that is a serialisation contract rather than a switch (flx-sim-01's card).
    Flagging it here would make this test a rename tracker instead of a guard.
    """
    pkg = _ROOT / "tools" / "studio" / "executors"
    offenders = []
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for literal in ast.walk(node):
                if isinstance(literal, ast.Constant) and literal.value == "localstack":
                    offenders.append(
                        f"{path.relative_to(_ROOT).as_posix()}:{literal.lineno}"
                    )
    assert offenders == [], "stale mode comparison(s):\n" + "\n".join(sorted(set(offenders)))


@pytest.mark.parametrize(
    "module",
    [
        "tools.studio.executors.terraform_apply",
        "tools.studio.executors.terraform_plan",
        "tools.studio.executors.terraform_destroy",
        "tools.studio.executors.aws_config_executor",
        "tools.studio.executors.gns3_sim",
    ],
)
def test_every_rewired_executor_imports_and_reads_the_seam(module):
    """Each of these lost a direct read of an emulator env var to the seam.

    Importing them is not ceremony: `_base` gained a module-level
    `from tools.cloud import emulator` beneath its own sys.path bootstrap, and
    an ordering mistake there breaks every executor in the package at import.
    """
    import importlib

    mod = importlib.import_module(module)
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "LOCALSTACK_ENDPOINT" not in src, (
        f"{module} still reads the raw endpoint variable instead of emulator.endpoint()"
    )


def test_the_emulated_provider_override_is_written_for_floci_not_for_aws(tmp_path, monkeypatch):
    """The BEHAVIOURAL consequence of the rename, in the executors that apply IaC.

    detect_mode() moved from `localstack` to `floci`. A consumer left comparing
    against the old literal fails GREEN — the Terraform provider override is
    simply never written and terraform runs against whatever the ambient
    credentials point at. This asserts the override block is produced for the
    emulated modes and withheld for the real-AWS one.
    """
    from tools.studio.executors import _base

    for mode, expected in ((emulator.MODE, True), ("sam", True),
                           ("aws", False), ("dry_run", False)):
        assert _base.is_emulated(mode) is expected

    env = {"FLOCI_ENABLED": "true", "FLOCI_ENDPOINT": "http://localhost:4566"}
    assert _base.detect_mode(env) == emulator.MODE

    rendered = _base.LOCALSTACK_PROVIDER_OVERRIDE.format(
        ep=_base.localstack_docker_endpoint(emulator.endpoint(env)),
        region=emulator.region(env),
    )
    # localhost is rewritten so a terraform container can reach the host.
    assert "host.docker.internal:4566" in rendered
    assert "localhost" not in rendered
    assert emulator.DEFAULT_REGION in rendered
    assert 's3_use_path_style           = true' in rendered


def test_boto3_client_endpoint_override_is_decided_by_the_seam():
    """It used to point at the emulator whenever LOCALSTACK_ENDPOINT was set —
    so it could target an emulator while feature_flags reported it disabled.

    Read from the AST with the docstring dropped: this module's own prose NAMES
    the variable it stopped reading, and a substring scan cannot tell an
    explanation from a read.
    """
    src = (_ROOT / "tools" / "studio" / "executors" / "_base.py").read_text(encoding="utf-8")
    fn = next(
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name == "boto3_client"
    )
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    literals = {
        n.value for stmt in body for n in ast.walk(stmt)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert "LOCALSTACK_ENDPOINT" not in literals
    calls = {
        n.func.id for stmt in body for n in ast.walk(stmt)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert {"detect_mode", "is_emulated"} <= calls
