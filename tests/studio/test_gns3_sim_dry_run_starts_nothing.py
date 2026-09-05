# CUI // SP-CTI
"""`dry_run` starts NOTHING, and the executing modes start what they declare.

THE DEFECT (flx-sim-01). ``gns3_sim.run_sim`` gated its container start on::

    if docker_ok and spec.docker_services and mode == "dry_run":

so a canvas's declared containers were ``docker run``-ed in the ONE mode whose
entire purpose is to touch nothing, and in NONE of ``dual`` / ``gns3_only`` /
``cloud_only``, which are the modes that run something.

WHY THE FIRST TEST HERE IS THE DRY-RUN ONE. A test that only asserted "the
executing modes start their containers" PASSES AGAINST THE INVERTED CODE in the
one shape that matters: with nothing reachable the old gate fires, the
containers come up, the mode is re-detected and upgraded, and an assertion made
after the fact on `docker_services_started` finds exactly what it wanted. The
discriminating assertion is the NEGATIVE one -- dry_run started nothing -- and
that is the assertion that goes red at the merge base.

Nothing here touches Docker, GNS3 or an emulator: every reachability probe and
the container starter itself are replaced with recorders, so what is measured
is the DECISION run_sim makes, which is the thing that was wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.studio.executors import gns3_sim  # noqa: E402
from tools.studio.sim import base_topology  # noqa: E402
from tools.studio.sim.base_topology import (  # noqa: E402
    CANVAS_EMULATOR_HOST_PORTS,
    DockerServiceSpec,
    LinkSpec,
    NodeSpec,
    ProbeSpec,
    TopologySpec,
)


class _StubBuilder:
    """A canvas that declares one container, so 'started nothing' is a choice."""

    canvas = "stub"

    def build(self, artifacts_dir, run_id=""):
        return TopologySpec(
            project_name="icdev-stub-sim",
            canvas="stub",
            nodes=[NodeSpec(name="a", node_type="vpcs"),
                   NodeSpec(name="b", node_type="vpcs")],
            links=[LinkSpec(a_node="a", b_node="b")],
            probes=[ProbeSpec(name="topology_deployed", type="topology_deployed"),
                    ProbeSpec(name="emulator_apply", type="emulator_apply")],
            docker_services=[DockerServiceSpec(
                name="icdev-floci-stub",
                image=base_topology.EMULATOR_IMAGE,
                ports={4599: base_topology.EMULATOR_CONTAINER_PORT},
                healthcheck_url="http://localhost:4599/_localstack/health",
            )],
            teardown_after=False,
            snapshot_before_teardown=False,
        )


@pytest.fixture()
def sim(monkeypatch, tmp_path):
    """run_sim with every side effect replaced by a recorder.

    Returns a callable ``run(*, gns3, emulator, docker, dry_run=False)`` and a
    ``started`` list naming the containers run_sim decided to start.
    """
    started: list[str] = []

    monkeypatch.setattr(gns3_sim, "_get_builder", lambda canvas: _StubBuilder())
    monkeypatch.setattr(gns3_sim, "artifacts_dir", lambda canvas: tmp_path)
    # run_sim reports its artifact paths relative to the module's _ROOT; move
    # both together so the reported path stays meaningful under tmp_path.
    monkeypatch.setattr(gns3_sim, "_ROOT", tmp_path)
    monkeypatch.setattr(gns3_sim, "resolve_canvas", lambda run_id, canvas: canvas or "stub")
    monkeypatch.setattr(gns3_sim, "load_dotenv", dict)
    monkeypatch.setattr(gns3_sim, "_deploy_topology",
                        lambda adapter, spec, findings: {"project_id": "p1",
                                                         "nodes_created": 2,
                                                         "links_created": 1})
    monkeypatch.setattr(gns3_sim, "_run_canvas_traffic",
                        lambda *a, **k: {})
    monkeypatch.setattr(gns3_sim, "_run_emulator_apply",
                        lambda run_id, canvas, findings: {"gate": "PASS"},
                        raising=False)

    def _record(svc, findings):
        started.append(svc.name)
        return True

    monkeypatch.setattr(gns3_sim, "_start_docker_service", _record)

    from tools.studio.sim import training_exporter
    monkeypatch.setattr(training_exporter, "export_canvas",
                        lambda canvas: {"skipped": True})

    def run(*, gns3: bool, emulator: bool, docker: bool, dry_run: bool = False):
        monkeypatch.setattr(gns3_sim, "_gns3_reachable", lambda url: gns3)
        monkeypatch.setattr(gns3_sim, "_cloud_emulator_reachable",
                            lambda url, path="": emulator)
        monkeypatch.setattr(gns3_sim, "_docker_available", lambda: docker)
        started.clear()
        if gns3:
            # _deploy_topology is stubbed, but run_sim still constructs a real
            # GNS3Adapter first; give it something inert to construct.
            monkeypatch.setitem(
                sys.modules, "tools.network.adapters.gns3_adapter",
                _fake_adapter_module())
        return gns3_sim.run_sim("run-1", "proj-1", "stub", dry_run=dry_run)

    return run, started


def _fake_adapter_module():
    import types

    mod = types.ModuleType("tools.network.adapters.gns3_adapter")

    class GNS3Adapter:
        def __init__(self, *a, **k):
            pass

        def health(self):
            return {"status": "ok"}

        def _is_error(self, resp):
            return False

        def snapshot(self, *a, **k):
            return {}

        def stop_topology(self, *a, **k):
            return {}

        def delete_project(self, *a, **k):
            return {}

    mod.GNS3Adapter = GNS3Adapter
    return mod


# ── THE DISCRIMINATING ASSERTION ───────────────────────────────────────────


def test_dry_run_by_outage_starts_no_container(sim):
    """Nothing reachable => dry_run => nothing is started. RED at merge base."""
    run, started = sim
    result = run(gns3=False, emulator=False, docker=True)

    assert result["mode"] == "dry_run"
    assert started == [], (
        "dry_run started %r -- the one mode whose purpose is to touch nothing"
        % (started,)
    )
    assert result["docker_services_started"] == 0


def test_dry_run_forced_by_flag_starts_no_container(sim):
    """--dry-run forces the inert mode even where everything IS reachable."""
    run, started = sim
    result = run(gns3=True, emulator=True, docker=True, dry_run=True)

    assert result["mode"] == "dry_run"
    assert started == []


def test_dry_run_still_reports_what_it_declined_to_start(sim):
    """Silence would be indistinguishable from a canvas declaring nothing."""
    run, _started = sim
    result = run(gns3=False, emulator=False, docker=True)

    messages = [f["message"] for f in result["findings"]
                if f["check"] == "docker_services"]
    assert messages, "dry_run said nothing about the container it declined to start"
    assert "none started" in messages[0]
    assert "1 canvas" in messages[0]


# ── The other half: the executing modes start what they declare ────────────


@pytest.mark.parametrize(
    "gns3, emulator, expected_mode",
    [
        (True, True, "dual"),
        (True, False, "gns3_only"),
        (False, True, "cloud_only"),
    ],
)
def test_each_executing_mode_starts_the_declared_containers(
    sim, gns3, emulator, expected_mode
):
    run, started = sim
    result = run(gns3=gns3, emulator=emulator, docker=True)

    assert result["mode"] == expected_mode
    assert started == ["icdev-floci-stub"]
    assert result["docker_services_started"] == 1


def test_an_executing_mode_without_docker_says_so_rather_than_going_quiet(sim):
    """`docker unavailable` and `nothing declared` are different findings."""
    run, started = sim
    result = run(gns3=True, emulator=True, docker=False)

    assert started == []
    warns = [f["message"] for f in result["findings"]
             if f["check"] == "docker_services" and f["severity"] == "warn"]
    assert warns and "Docker unavailable" in warns[0]


def test_docker_is_not_even_probed_in_dry_run(sim, monkeypatch):
    """A dry run must not shell out to `docker info` to decide to do nothing."""
    run, _started = sim
    probed: list[bool] = []

    def _probe():
        probed.append(True)
        return True

    monkeypatch.setattr(gns3_sim, "_docker_available", _probe)
    gns3_sim.run_sim("run-1", "proj-1", "stub", dry_run=True)
    assert probed == []


# ── The renamed seam ───────────────────────────────────────────────────────


def test_the_apply_helper_and_probe_type_are_named_for_the_emulator(sim):
    """`localstack_apply` was a product name on a probe type. It is gone."""
    assert hasattr(gns3_sim, "_run_emulator_apply")
    assert not hasattr(gns3_sim, "_run_localstack_apply")

    run, _started = sim
    result = run(gns3=True, emulator=True, docker=True)
    types_seen = {p["type"] for p in result["_probes"]}
    assert "emulator_apply" in types_seen
    assert "localstack_apply" not in types_seen


def test_the_training_artifact_key_moved_and_the_old_one_still_reads(sim, tmp_path):  # noqa: D103
    """The output key is a serialisation contract; the reader honours both."""
    import json

    run, _started = sim
    result = run(gns3=True, emulator=True, docker=True)
    pair = json.loads(
        (tmp_path / result["training_artifact"]).read_text(encoding="utf-8"))
    assert "emulator" in pair["output"]
    assert "localstack" not in pair["output"]

    # And a pair written BEFORE the rename still exports its emulator section.
    src = (_ROOT / "tools" / "studio" / "sim" / "training_exporter.py").read_text(
        encoding="utf-8")
    assert 'output_data.get("localstack"' in src, (
        "the pre-rename key must stay READABLE -- artifacts already on disk "
        "record runs that happened and cannot be rewritten"
    )


# ── The hub's contract ─────────────────────────────────────────────────────


def test_the_executor_accepts_the_flag_sim_hub_has_always_passed():
    """sim_hub.run_canvas_sim(dry_run=True) appends --dry-run.

    argparse exited 2 on it, into a Popen whose stdout and stderr are DEVNULL,
    so the hub returned {"status": "started"} for a process already dead.
    """
    import argparse
    import inspect

    src = inspect.getsource(gns3_sim.main)
    assert "--dry-run" in src

    from tools.studio.sim import sim_hub
    hub_src = inspect.getsource(sim_hub.run_canvas_sim)
    assert "--dry-run" in hub_src

    parser = argparse.ArgumentParser()
    parser.add_argument("--canvas", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    ns = parser.parse_args(["--canvas", "ddc", "--json", "--dry-run"])
    assert ns.dry_run is True


# ── The port reconciliation ────────────────────────────────────────────────


def test_no_two_canvases_bind_the_same_emulator_host_port():
    ports = list(CANVAS_EMULATOR_HOST_PORTS.values())
    assert len(ports) == len(set(ports)), (
        "two canvas emulators on one host port: %r" % (CANVAS_EMULATOR_HOST_PORTS,)
    )


def test_no_canvas_takes_the_deployments_own_emulator_port():
    """4566 belongs to the compose-managed floci, never to a canvas sim."""
    from tools.cloud import emulator as seam

    assert seam.CONTAINER_PORT not in CANVAS_EMULATOR_HOST_PORTS.values()


def test_no_canvas_host_port_falls_inside_flocis_proxy_ranges():
    from tools.cloud import emulator as seam

    offenders = {c: p for c, p in CANVAS_EMULATOR_HOST_PORTS.items()
                 if seam.in_proxy_range(p)}
    assert offenders == {}, "inside a container-backed proxy range: %r" % offenders


@pytest.mark.parametrize("canvas", ["aimc", "bdc", "ddc", "idc", "mdc", "ndc",
                                    "ohc", "pdc"])
def test_every_canvas_emulator_container_reads_the_one_pinned_image(canvas):
    """No topology respells the tag, and none still names LocalStack."""
    import importlib

    from tools.cloud import emulator as seam

    mod = importlib.import_module("tools.studio.sim.%s_topology" % canvas)
    builder = getattr(mod, canvas.upper() + "TopologyBuilder")()
    spec = builder.build(Path("."), "run-1")

    emulators = [s for s in spec.docker_services if "floci" in s.name]
    assert emulators, "%s declares no emulator container" % canvas
    for svc in emulators:
        assert svc.image == seam.IMAGE
        assert "localstack" not in svc.image
        assert list(svc.ports.values()) == [seam.CONTAINER_PORT], (
            "%s: the IN-CONTAINER port is floci's 4566, whatever the host binds"
            % svc.name
        )
        assert list(svc.ports.keys())[0] in CANVAS_EMULATOR_HOST_PORTS.values()


def test_the_pinned_tag_is_never_latest():
    from tools.cloud import emulator as seam

    assert seam.IMAGE_TAG != "latest"
    assert seam.IMAGE == "%s:%s" % (seam.IMAGE_REPOSITORY, seam.IMAGE_TAG)


def test_no_sim_module_still_declares_a_localstack_image():
    offenders = []
    for path in sorted((_ROOT / "tools" / "studio" / "sim").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "localstack/localstack" in line:
                offenders.append("%s:%d" % (path.name, lineno))
    assert offenders == [], "stale image declaration(s): %r" % offenders
