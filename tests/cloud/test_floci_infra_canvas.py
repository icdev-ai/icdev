# CUI // SP-CTI
"""The Infra-Canvas adapter and the GENERATED customer compose emit floci (flx-gen-01).

TWO DIFFERENT KINDS OF ARTIFACT, AND ONLY ONE OF THEM STAYS HERE
----------------------------------------------------------------
``tools/infra_canvas/adapters/floci_adapter.py`` is ours: it points boto3,
awscli and ``terraform apply`` at whatever the ONE seam
(``tools/cloud/emulator.py``, flx-seam-01) says the emulator is. If it is wrong
we find out on this host.

``tools/infra_canvas/dockerfile_generator.py`` writes a docker-compose.yml into
SOMEBODY ELSE'S project, which is then rebuilt on their infrastructure, possibly
disconnected. Every emulator choice this project made deliberately has to be in
that file or the customer silently gets the opposite one:

  * a PINNED tag, never ``:latest``  -- a moving tag makes "the image we tested"
    unanswerable and an air-gapped rebuild unreproducible;
  * persistent state;
  * the GovCloud region default;
  * the Docker socket mounted ONLY with the comment naming it as a security
    decision the customer has to make. A container holding the host Docker
    socket is root-equivalent on that host, and a mount inherited silently from
    a generator is a decision nobody made.

WHY THE IMAGE STRING IS ASSERTED AND NOT EYEBALLED
--------------------------------------------------
The swap's failure mode is GREEN: a compose file naming
``localstack/localstack:latest`` is valid YAML, ``docker compose config``
accepts it, and every test that only checks "the compose file parses" passes
while the customer pulls the product this project measured as NO-GO for air-gap
(docs/spikes/twx-spk-01-localstack-go-no-go.md). So the generated text is
asserted against ``emulator.DEFAULT_IMAGE`` -- the constant, never a second
spelling of it -- and separately asserted to contain no ``localstack/`` image
reference at all.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.cloud import emulator  # noqa: E402
from tools.infra_canvas import dockerfile_generator as dg  # noqa: E402


def _adapter():
    """Import the adapter INSIDE the test, on purpose.

    At the merge base this name does not exist, and a module-level import would
    turn every test in this file into one collection error -- including the
    generated-compose ones, whose whole job is to fail on the OLD image string.
    A red that only says "the module was not there yet" proves the rename and
    says nothing about the swap.
    """
    from tools.infra_canvas.adapters import FlociAdapter

    return FlociAdapter


_GRAPH = {
    "nodes": [
        {
            "id": "n1",
            "type": "service",
            "label": "Orders API",
            "properties": {"runtime": "python", "port": 8000},
        }
    ]
}


@pytest.fixture(autouse=True)
def _quiet_alias_warnings():
    """The seam warns once per process per deprecated alias; keep tests independent."""
    emulator.reset_alias_warnings()
    yield
    emulator.reset_alias_warnings()


def _emulator_on(monkeypatch, **overrides):
    monkeypatch.setenv("FLOCI_ENABLED", "true")
    for key in ("FLOCI_ENDPOINT", "FLOCI_REGION", "LOCALSTACK_ENDPOINT", "LOCALSTACK_REGION"):
        monkeypatch.delenv(key, raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)


# --------------------------------------------------------------------------- #
# The adapter — the name moved, and it reads the seam
# --------------------------------------------------------------------------- #


def test_adapter_module_and_class_are_floci_named():
    from tools.infra_canvas.adapters import floci_adapter

    assert floci_adapter.FlociAdapter is _adapter()
    assert Path(floci_adapter.__file__).name == "floci_adapter.py"


@pytest.mark.parametrize("tree", ["tools", "icdev/tools"])
def test_no_survivor_names_the_retired_adapter(tree):
    """No module in EITHER tree still names the retired module or class.

    Both halves matter. The old file being gone is not enough: a
    ``from ...localstack_adapter import LocalStackAdapter`` left behind is an
    ImportError that takes the whole ``adapters`` package down at import, which
    is how a rename ships looking clean and breaks the canvas.
    """
    root = _ROOT / tree
    assert root.is_dir(), f"{tree} is not a directory"
    assert not (root / "infra_canvas" / "adapters" / "localstack_adapter.py").exists()

    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "LocalStackAdapter" in text or "localstack_adapter" in text:
            offenders.append(str(path.relative_to(_ROOT)))
    assert offenders == [], f"retired adapter name still referenced: {offenders}"


def test_get_env_vars_points_every_service_at_the_seams_endpoint(monkeypatch):
    _emulator_on(
        monkeypatch,
        FLOCI_ENDPOINT="http://emulator.internal:4566",
        FLOCI_REGION="us-gov-east-1",
    )
    env = _adapter().from_env().get_env_vars()

    assert env["AWS_ENDPOINT_URL"] == "http://emulator.internal:4566"
    assert env["AWS_DEFAULT_REGION"] == "us-gov-east-1"

    overrides = {k: v for k, v in env.items() if k.startswith("AWS_ENDPOINT_URL_")}
    assert overrides, "no per-service endpoint overrides emitted"
    assert set(overrides.values()) == {"http://emulator.internal:4566"}


def test_get_env_vars_never_carries_the_ambient_aws_credentials(monkeypatch):
    """A developer with GovCloud keys exported in the same shell is the normal case."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAREALLYREALKEY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "a-real-secret")
    _emulator_on(monkeypatch)

    env = _adapter().from_env().get_env_vars()
    assert (env["AWS_ACCESS_KEY_ID"], env["AWS_SECRET_ACCESS_KEY"]) == emulator.credentials()


def test_adapter_defaults_to_the_seam_region_not_us_east_1(monkeypatch):
    _emulator_on(monkeypatch)
    assert _adapter().from_env().get_env_vars()["AWS_DEFAULT_REGION"] == "us-gov-west-1"


def test_deprecated_localstack_alias_still_reaches_the_adapter(monkeypatch):
    """floci honours LOCALSTACK_* itself; an operator who set them is not broken."""
    _emulator_on(monkeypatch)
    monkeypatch.delenv("FLOCI_ENDPOINT", raising=False)
    monkeypatch.setenv("LOCALSTACK_ENDPOINT", "http://legacy-host:4566")
    assert _adapter().from_env().get_endpoint() == "http://legacy-host:4566"


# --------------------------------------------------------------------------- #
# terraform apply / destroy follow the same env
# --------------------------------------------------------------------------- #


class _Recorder:
    """Stands in for subprocess.run and records the env each call was handed."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs.get("env") or {}))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.mark.parametrize("method", ["deploy_terraform_local", "destroy_namespace"])
def test_terraform_calls_are_pointed_at_the_emulator(monkeypatch, tmp_path, method):
    _emulator_on(monkeypatch, FLOCI_ENDPOINT="http://emulator.internal:4566")
    recorder = _Recorder()
    monkeypatch.setattr(
        "tools.infra_canvas.adapters.floci_adapter.subprocess.run", recorder
    )

    adapter = _adapter().from_env()
    result = getattr(adapter, method)(str(tmp_path), namespace="dev")

    assert result["status"] == "ok"
    assert recorder.calls, "terraform was never invoked"
    for cmd, env in recorder.calls:
        assert cmd[0] == "terraform"
        assert env["AWS_ENDPOINT_URL"] == "http://emulator.internal:4566"
        assert env["TF_VAR_namespace"] == "dev"


@pytest.mark.parametrize("method", ["deploy_terraform_local", "destroy_namespace"])
def test_terraform_runs_nothing_while_the_switch_is_off(monkeypatch, tmp_path, method):
    monkeypatch.setenv("FLOCI_ENABLED", "false")
    monkeypatch.delenv("LOCALSTACK_ENABLED", raising=False)
    recorder = _Recorder()
    monkeypatch.setattr(
        "tools.infra_canvas.adapters.floci_adapter.subprocess.run", recorder
    )

    result = getattr(_adapter().from_env(), method)(str(tmp_path))

    assert recorder.calls == []
    assert result.get("enabled") is False or result.get("status") != "ok"


# --------------------------------------------------------------------------- #
# The generated CUSTOMER compose
# --------------------------------------------------------------------------- #


def _compose(monkeypatch=None, **kwargs) -> str:
    return dg.generate_all(_GRAPH, project_name="acme-local", **kwargs)["compose"]


def test_generated_compose_names_the_pinned_floci_image():
    compose = _compose()
    doc = yaml.safe_load(compose)
    image = doc["services"]["floci"]["image"]

    assert image == emulator.DEFAULT_IMAGE
    tag = image.rsplit(":", 1)[-1]
    assert tag and tag != "latest", f"customer compose must pin a tag, got {image!r}"


def test_generated_compose_has_no_localstack_image_left():
    """The swap's failure mode is green -- assert the OLD string is gone."""
    compose = _compose()
    assert "localstack/localstack" not in compose
    # The health PATH is floci's too -- it keeps LocalStack's URL, which is
    # what "drop-in" means. That one occurrence is expected and is asserted
    # elsewhere; nothing else may name the old product as an image or service.
    doc = yaml.safe_load(compose)
    assert doc["services"].get("localstack") is None
    # Read the IMAGES rather than the text: ":latest" appears in the file as
    # prose telling the customer never to use it.
    images = [svc["image"] for svc in doc["services"].values() if "image" in svc]
    assert images, "no images in the generated compose"
    assert not any(img.endswith(":latest") for img in images), images


def test_generated_compose_persists_state_and_defaults_to_govcloud():
    doc = yaml.safe_load(_compose())
    floci = doc["services"]["floci"]

    assert floci["environment"]["FLOCI_STORAGE_MODE"] == "persistent"
    assert floci["environment"]["FLOCI_DEFAULT_REGION"] == "us-gov-west-1"
    assert any(v.endswith(":/var/lib/floci") for v in floci["volumes"])
    assert floci["healthcheck"]["test"][-1].endswith(emulator.HEALTH_PATH)


def test_generated_compose_region_follows_the_seam(monkeypatch):
    _emulator_on(monkeypatch, FLOCI_REGION="us-gov-east-1")
    doc = yaml.safe_load(_compose())
    assert doc["services"]["floci"]["environment"]["FLOCI_DEFAULT_REGION"] == "us-gov-east-1"
    app_env = doc["services"]["orders-api"]["environment"]
    assert "AWS_DEFAULT_REGION=us-gov-east-1" in app_env


def test_app_containers_reach_the_emulator_by_service_name_not_loopback(monkeypatch):
    """The HOST endpoint must never be written into a CONTAINER's environment.

    ``emulator.endpoint()`` answers for the host -- ``http://localhost:4566``
    unless an operator set FLOCI_ENDPOINT -- and a container told to talk to
    localhost talks to ITSELF. Inside the generated network the emulator is
    reached by its service name.
    """
    _emulator_on(monkeypatch, FLOCI_ENDPOINT="http://localhost:4566")
    doc = yaml.safe_load(_compose())
    app_env = doc["services"]["orders-api"]["environment"]

    assert f"AWS_ENDPOINT_URL={dg.DEFAULT_COMPOSE_ENDPOINT}" in app_env
    assert not any(e.startswith("AWS_ENDPOINT_URL=http://localhost") for e in app_env)


def test_docker_socket_mount_ships_with_its_security_decision():
    """The socket is mounted ONLY beside the comment that names what it costs.

    A generated bind mount of the host Docker socket, with no comment, is a
    root-equivalence grant the customer inherited instead of deciding.
    """
    lines = _compose().splitlines()
    socket_lines = [i for i, line in enumerate(lines) if "/var/run/docker.sock" in line]
    assert len(socket_lines) == 1, "expected exactly one docker socket mount"

    idx = socket_lines[0]
    preceding = []
    cursor = idx - 1
    while cursor >= 0 and lines[cursor].strip().startswith("#"):
        preceding.append(lines[cursor])
        cursor -= 1
    comment = "\n".join(reversed(preceding)).upper()

    assert comment, "the docker socket mount carries no explanatory comment"
    assert "SECURITY DECISION" in comment
    assert "ROOT-EQUIVALENT" in comment
    # It must also tell the customer how to decline it.
    assert "DELETE" in comment


def test_generated_compose_is_parseable_with_no_services_at_all():
    """An empty design still yields a valid file -- and still no dangling volume."""
    doc = yaml.safe_load(dg.generate_all({"nodes": []})["compose"])
    assert list(doc["services"]) == ["floci"]
    # The old file declared a top-level `localstack-data:` volume nothing
    # mounted. A named volume no service references is noise in an artifact
    # somebody else has to review.
    assert "volumes" not in doc
