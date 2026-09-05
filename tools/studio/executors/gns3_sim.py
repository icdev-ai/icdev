"""GNS3 + cloud-emulator Simulation Executor — Canvas-agnostic Shared Step.

Dual-layer simulation:
  Network layer  → GNS3 (virtual topology: nodes, links, routing)
  Services layer → floci / Azurite / GCP emulator / Minio (cloud APIs)
  Docker layer   → Canvas-specific containers (DB, monitoring, CI tools)

Modes (auto-detected, or forced to dry_run with --dry-run):
  dual            — GNS3 + cloud emulator both reachable
  gns3_only       — GNS3 reachable, no cloud emulator
  cloud_only      — cloud emulator reachable, no GNS3
  dry_run         — validate the topology spec only. STARTS NOTHING.

DRY_RUN STARTS NOTHING, AND THAT USED TO BE INVERTED (flx-sim-01)
-----------------------------------------------------------------
The container-start gate read ``mode == "dry_run"``, so canvas containers were
`docker run`-ed in the ONE mode whose entire purpose is to touch nothing, and
in none of the three modes that are supposed to run something. The reading that
made it look deliberate is a bootstrap one -- "nothing is reachable, so start
the declared containers and re-detect" -- and it fails on its own terms: a
canvas emulator publishes its own host port (see
``sim.base_topology.CANVAS_EMULATOR_HOST_PORTS``), not the one
``emulator.endpoint()`` names, so ``dual`` and ``cloud_only`` runs -- exactly
the runs that need it -- never started the canvas's own container either.

So the gate is now the executing modes, and dry_run is inert. A caller that
wanted the old bootstrap wants an emulator brought up out of band; a caller
that wants to touch nothing has ``--dry-run``, which FORCES the mode rather
than inferring it from an outage.

Canvas topology builders live in tools/studio/sim/<canvas>_topology.py.
Each builder reads design artifacts and emits a TopologySpec.

Training artifacts saved to:
  data/studio_artifacts/<canvas>/training/<uid>/
    topology.json
    probe_results.json
    training_pair.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from tools.studio.executors._base import (  # noqa: E402
    artifacts_dir, resolve_canvas, load_dotenv,
)
from tools.cloud import emulator  # noqa: E402

# ── topology builder registry ──────────────────────────────────────────────

def _get_builder(canvas: str):
    _MAP = {
        "ddc":  "tools.studio.sim.ddc_topology",
        "ndc":  "tools.studio.sim.ndc_topology",
        "sdc":  "tools.studio.sim.sdc_topology",
        "bdc":  "tools.studio.sim.bdc_topology",
        "pdc":  "tools.studio.sim.pdc_topology",
        "odc":  "tools.studio.sim.odc_topology",
        "idc":  "tools.studio.sim.idc_topology",
        "qdc":  "tools.studio.sim.qdc_topology",
        "mdc":  "tools.studio.sim.mdc_topology",
        "aadc": "tools.studio.sim.aadc_topology",
        "aimc": "tools.studio.sim.aimc_topology",
        "ohc":  "tools.studio.sim.ohc_topology",
    }
    module_path = _MAP.get(canvas)
    if module_path:
        import importlib
        mod = importlib.import_module(module_path)
        # Class name convention: <CANVAS_UPPER>TopologyBuilder
        cls_name = canvas.upper() + "TopologyBuilder"
        cls = getattr(mod, cls_name, None)
        if cls:
            return cls()
    from tools.studio.sim.base_topology import BaseTopologyBuilder
    return BaseTopologyBuilder()


# ── GNS3 health check ──────────────────────────────────────────────────────

def _gns3_reachable(url: str) -> bool:
    try:
        from tools.network.adapters.gns3_adapter import GNS3Adapter
        env = load_dotenv()
        adapter = GNS3Adapter(
            url,
            username=env.get("GNS3_USERNAME", ""),
            password=env.get("GNS3_PASSWORD", ""),
            token=env.get("GNS3_TOKEN", ""),
        )
        h = adapter.health()
        return h.get("status") == "ok"
    except Exception:
        return False


def _cloud_emulator_reachable(url: str, path: str = "") -> bool:
    """Check if a cloud emulator is reachable. url may be a full URL or base+path."""
    try:
        import urllib.request
        full = url if not path else f"{url.rstrip('/')}/{path.lstrip('/')}"
        urllib.request.urlopen(full, timeout=4)
        return True
    except Exception:
        return False


# ── Docker container management ────────────────────────────────────────────

def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
        return True
    except Exception:
        return False


def _container_running(name: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def _start_docker_service(svc, findings: list[dict]) -> bool:
    """Start a DockerServiceSpec container if not already running."""
    if _container_running(svc.name):
        findings.append({"severity": "info", "check": f"docker_{svc.name}",
                          "message": f"Container '{svc.name}' already running"})
        return True
    port_flags: list[str] = []
    for host_p, cont_p in svc.ports.items():
        port_flags += ["-p", f"{host_p}:{cont_p}"]
    env_flags: list[str] = []
    for k, v in svc.env.items():
        env_flags += ["-e", f"{k}={v}"]
    cmd = ["docker", "run", "-d", "--name", svc.name] + port_flags + env_flags + [svc.image]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            findings.append({"severity": "warn", "check": f"docker_{svc.name}",
                              "message": f"Failed to start '{svc.name}': {r.stderr.strip()[:200]}"})
            return False
        # Wait for healthcheck
        if svc.healthcheck_url:
            for _ in range(20):
                if _cloud_emulator_reachable(svc.healthcheck_url):
                    break
                time.sleep(1)
        findings.append({"severity": "info", "check": f"docker_{svc.name}",
                          "message": f"Started container '{svc.name}' ({svc.image})"})
        return True
    except Exception as exc:
        findings.append({"severity": "warn", "check": f"docker_{svc.name}",
                          "message": f"Exception starting '{svc.name}': {exc}"})
        return False


# ── GNS3 topology deployment ───────────────────────────────────────────────

def _deploy_topology(adapter, spec, findings: list[dict]) -> dict:
    """Create GNS3 project, add nodes and links, start all nodes."""
    # Create project
    proj = adapter.create_project(spec.project_name)
    if adapter._is_error(proj):
        findings.append({"severity": "fail", "check": "gns3_create_project",
                          "message": f"Failed to create project: {proj.get('error', 'unknown')}"})
        return {}
    project_id = proj.get("project_id", "")
    findings.append({"severity": "pass", "check": "gns3_create_project",
                      "message": f"Project '{spec.project_name}' → {project_id}"})

    # Add nodes
    node_id_map: dict[str, str] = {}
    for node_spec in spec.nodes:
        resp = adapter.add_node(
            project_id, node_spec.name, node_spec.node_type,
            node_spec.compute_id, node_spec.properties or None,
        )
        if adapter._is_error(resp):
            findings.append({"severity": "warn", "check": "gns3_add_node",
                              "message": f"Node '{node_spec.name}': {resp.get('error', 'unknown')}"})
        else:
            node_id = resp.get("node_id", "")
            node_id_map[node_spec.name] = node_id

    findings.append({"severity": "pass" if node_id_map else "warn", "check": "gns3_nodes",
                      "message": f"{len(node_id_map)}/{len(spec.nodes)} nodes created"})

    # Add links
    links_created = 0
    for link_spec in spec.links:
        a_id = node_id_map.get(link_spec.a_node, "")
        b_id = node_id_map.get(link_spec.b_node, "")
        if not a_id or not b_id:
            continue
        resp = adapter.link_nodes(project_id, a_id, link_spec.a_port, b_id, link_spec.b_port)
        if not adapter._is_error(resp):
            links_created += 1

    findings.append({"severity": "pass" if links_created else "warn", "check": "gns3_links",
                      "message": f"{links_created}/{len(spec.links)} links created"})

    # Start all nodes
    start_resp = adapter.start_topology(project_id)
    if adapter._is_error(start_resp):
        findings.append({"severity": "warn", "check": "gns3_start",
                          "message": f"Start warning: {start_resp.get('error', 'unknown')}"})
    else:
        findings.append({"severity": "pass", "check": "gns3_start",
                          "message": "All nodes started"})

    return {"project_id": project_id, "node_id_map": node_id_map,
            "nodes_created": len(node_id_map), "links_created": links_created}


# ── Probe runner ───────────────────────────────────────────────────────────

def _run_probes(spec, topo_result: dict, emulator_result: dict,
                findings: list[dict]) -> list[dict]:
    results: list[dict] = []
    for probe in spec.probes:
        if probe.type == "topology_deployed":
            ok = bool(topo_result.get("project_id"))
            results.append({"name": probe.name, "type": probe.type,
                             "pass": ok, "detail": "GNS3 project deployed" if ok else "Not deployed"})
            findings.append({"severity": "pass" if ok else "warn",
                              "check": f"probe_{probe.name}", "message": results[-1]["detail"]})

        elif probe.type == "link_count":
            actual = topo_result.get("links_created", 0)
            expected = probe.expected_value or len(spec.links)
            ok = actual >= expected
            results.append({"name": probe.name, "type": probe.type,
                             "pass": ok, "detail": f"{actual}/{expected} links"})
            findings.append({"severity": "pass" if ok else "warn",
                              "check": f"probe_{probe.name}",
                              "message": f"Links: {actual}/{expected}"})

        elif probe.type == "emulator_apply":
            ok = emulator_result.get("gate") in ("PASS", "WARN")
            detail = f"Cloud emulator apply: {emulator_result.get('gate', 'SKIPPED')}"
            results.append({"name": probe.name, "type": probe.type, "pass": ok, "detail": detail})
            findings.append({"severity": "pass" if ok else "warn",
                              "check": f"probe_{probe.name}", "message": detail})

        elif probe.type in ("tcp_connect", "http_get"):
            results.append({"name": probe.name, "type": probe.type,
                             "pass": True, "detail": "Simulated (virtual node)"})

    return results


# ── Cloud emulator apply (cloud services layer) ───────────────────────────

def _run_emulator_apply(run_id: str, canvas: str, findings: list[dict]) -> dict:
    try:
        from tools.studio.executors.terraform_apply import run_apply
        result = run_apply(run_id, "gns3-sim", canvas)
        findings.append({"severity": "info", "check": "emulator_layer",
                          "message": f"Cloud services layer: {result['gate']} "
                                     f"({result.get('resources_created', 0)} resources)"})
        return result
    except Exception as exc:
        findings.append({"severity": "warn", "check": "emulator_layer",
                          "message": f"Cloud services layer skipped: {exc}"})
        return {"gate": "SKIP"}


# ── Training artifact writer ───────────────────────────────────────────────

def _save_training_artifact(canvas: str, spec, topo_result: dict,
                             probe_results: list[dict], emu_result: dict,
                             uid: str, gate: str, out_dir: Path,
                             traffic_result: dict | None = None) -> str:
    training_dir = out_dir / "training" / uid
    training_dir.mkdir(parents=True, exist_ok=True)

    topology_data = {
        "project_name": spec.project_name,
        "gns3_project_id": topo_result.get("project_id", ""),
        "nodes": [{"name": n.name, "node_type": n.node_type, "meta": n.meta}
                  for n in spec.nodes],
        "links": [{"a": l.a_node, "b": l.b_node} for l in spec.links],
        "metadata": spec.metadata,
    }
    (training_dir / "topology.json").write_text(
        json.dumps(topology_data, indent=2), encoding="utf-8", newline="")
    (training_dir / "probe_results.json").write_text(
        json.dumps(probe_results, indent=2), encoding="utf-8", newline="")

    # `emulator`, not `localstack`: this key is a SERIALISATION CONTRACT that
    # sim/training_exporter.py reads back, so it reads BOTH -- the old key for
    # pairs already on disk, this one for everything written from now on.
    output_section: dict = {"probes": probe_results, "emulator": emu_result}
    if traffic_result:
        traffic_phase = traffic_result.get("phases", {}).get("traffic", {})
        output_section["traffic"] = {
            "flows_tested":  traffic_phase.get("flows_tested", 0),
            "reachable":     traffic_phase.get("reachable", 0),
            "unreachable":   traffic_phase.get("unreachable", 0),
            "flows":         traffic_phase.get("flows", []),
        }
        (training_dir / "traffic_flows.json").write_text(
            json.dumps(traffic_phase, indent=2, default=str), encoding="utf-8", newline="")

    pair = {
        "canvas": canvas, "uid": uid, "gate": gate,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": {"topology": topology_data},
        "output": output_section,
    }
    pair_path = training_dir / "training_pair.json"
    pair_path.write_text(json.dumps(pair, indent=2), encoding="utf-8", newline="")
    return pair_path.relative_to(_ROOT).as_posix()


# ── Canvas traffic engine integration ─────────────────────────────────────────

def _run_canvas_traffic(canvas: str, gns3_url: str, project_name: str,
                        findings: list[dict]) -> dict:
    """Run canvas traffic engine (ZTP + traffic matrix) post-topology-deploy."""
    try:
        from tools.studio.sim.canvas_traffic_engine import get_canvas_engine
        engine = get_canvas_engine(canvas, server=gns3_url, project=project_name)
        result = engine.run(do_ztp=True, do_traffic=True)
        traffic_phase = result.get("phases", {}).get("traffic", {})
        reachable  = traffic_phase.get("reachable", 0)
        total      = traffic_phase.get("flows_tested", 0)
        stored     = traffic_phase.get("stored_to_db", 0)
        findings.append({
            "severity": "pass" if reachable > 0 else "warn",
            "check": "canvas_traffic",
            "message": (f"Traffic: {reachable}/{total} flows reachable  "
                        f"stored={stored} rows  "
                        f"ztp_dir={result.get('ztp_dir', 'n/a')}"),
        })
        return result
    except Exception as exc:
        findings.append({"severity": "info", "check": "canvas_traffic",
                          "message": f"Traffic engine skipped: {exc}"})
        return {}


# ── Main run function ──────────────────────────────────────────────────────

def run_sim(run_id: str, project_id: str, canvas: str = "",
            dry_run: bool = False) -> dict:
    canvas = resolve_canvas(run_id, canvas)
    out_dir = artifacts_dir(canvas)
    env = load_dotenv()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uid = uuid.uuid4().hex[:8]

    findings: list[dict] = []
    gate = "PASS"

    # Build topology spec
    builder = _get_builder(canvas)
    spec = builder.build(out_dir, run_id)

    # Detect simulation mode
    gns3_url = env.get("GNS3_URL", "http://localhost:3080")
    emu_endpoint = emulator.endpoint(env)

    # --dry-run FORCES the inert mode and is asked BEFORE the probes: a caller
    # that wants to touch nothing must not have its answer decided by whether
    # something happened to be listening.
    if dry_run:
        gns3_ok = False
        emu_ok = False
        mode = "dry_run"
    else:
        gns3_ok = _gns3_reachable(gns3_url)
        emu_ok = _cloud_emulator_reachable(emu_endpoint, emulator.HEALTH_PATH)
        if gns3_ok and emu_ok:
            mode = "dual"
        elif gns3_ok:
            mode = "gns3_only"
        elif emu_ok:
            mode = "cloud_only"
        else:
            mode = "dry_run"

    findings.append({"severity": "info", "check": "sim_mode",
                      "message": f"Simulation mode: {mode.upper()} — "
                                 f"GNS3={'✓' if gns3_ok else '✗'} "
                                 f"emulator={'✓' if emu_ok else '✗'}"
                                 + (" (forced by --dry-run)" if dry_run else "")})

    # Start Docker services (canvas-specific containers).
    #
    # THE EXECUTING MODES ONLY. `dry_run` validates the spec and starts
    # nothing -- see the module docstring for the inversion this replaces.
    docker_started: list[str] = []
    docker_ok = False
    if mode == "dry_run":
        if spec.docker_services:
            findings.append({
                "severity": "info", "check": "docker_services",
                "message": (f"DRY RUN — {len(spec.docker_services)} canvas "
                            f"container(s) declared, none started"),
            })
    else:
        docker_ok = _docker_available()
        if docker_ok and spec.docker_services:
            findings.append({"severity": "info", "check": "docker_services",
                              "message": f"Starting {len(spec.docker_services)} canvas container(s)…"})
            for svc in spec.docker_services:
                if _start_docker_service(svc, findings):
                    docker_started.append(svc.name)
            # A canvas container can complete the pair the mode was missing --
            # a gns3_only run whose canvas emulator has now come up is dual.
            if not gns3_ok:
                gns3_ok = _gns3_reachable(gns3_url)
            if not emu_ok:
                emu_ok = _cloud_emulator_reachable(emu_endpoint, emulator.HEALTH_PATH)
            upgraded = "dual" if (gns3_ok and emu_ok) else mode
            if upgraded != mode:
                mode = upgraded
                findings.append({"severity": "info", "check": "sim_mode_upgrade",
                                  "message": f"Mode upgraded to {mode.upper()} after container start"})
        elif spec.docker_services and not docker_ok:
            findings.append({
                "severity": "warn", "check": "docker_services",
                "message": (f"Docker unavailable — {len(spec.docker_services)} canvas "
                            f"container(s) declared, none started"),
            })

    # GNS3 topology deployment
    topo_result: dict = {}
    if gns3_ok:
        from tools.network.adapters.gns3_adapter import GNS3Adapter
        adapter = GNS3Adapter(
            gns3_url,
            username=env.get("GNS3_USERNAME", ""),
            password=env.get("GNS3_PASSWORD", ""),
            token=env.get("GNS3_TOKEN", ""),
        )
        topo_result = _deploy_topology(adapter, spec, findings)
        if not topo_result.get("project_id"):
            gate = "WARN"
    else:
        findings.append({"severity": "info", "check": "gns3_topology",
                          "message": f"GNS3 not available — topology dry-run: "
                                     f"{len(spec.nodes)} nodes, {len(spec.links)} links validated"})
        topo_result = {"nodes_created": len(spec.nodes), "links_created": len(spec.links),
                       "project_id": "", "dry_run": True}

    # Cloud services layer (floci / other emulators)
    emu_result: dict = {"gate": "SKIP"}
    if emu_ok and mode in ("dual", "cloud_only"):
        emu_result = _run_emulator_apply(run_id, canvas, findings)
        # Cloud services layer failure → WARN only; GNS3 topology success is the primary gate
        if emu_result.get("gate") == "FAIL" and gate == "PASS":
            gate = "WARN"
    elif not emu_ok:
        findings.append({"severity": "info", "check": "cloud_layer",
                          "message": "Cloud emulator not available — services layer skipped"})

    # Run probes
    probe_results = _run_probes(spec, topo_result, emu_result, findings)

    # Canvas traffic engine (ZTP + traffic matrix) — runs after topology is live
    traffic_result: dict = {}
    if gns3_ok and topo_result.get("project_id") and mode in ("dual", "gns3_only"):
        traffic_result = _run_canvas_traffic(
            canvas, gns3_url, spec.project_name, findings)
        # Enrich gate: any reachable traffic flow upgrades a WARN to PASS
        if gate == "WARN":
            t_phase = traffic_result.get("phases", {}).get("traffic", {})
            if t_phase.get("reachable", 0) > 0:
                gate = "PASS"

    # GNS3 snapshot before teardown
    snapshot_name = ""
    if gns3_ok and topo_result.get("project_id") and spec.snapshot_before_teardown:
        snap = adapter.snapshot(topo_result["project_id"], f"sim-{uid}")
        if not adapter._is_error(snap):
            snapshot_name = f"sim-{uid}"
            findings.append({"severity": "info", "check": "gns3_snapshot",
                              "message": f"Snapshot saved: {snapshot_name}"})

    # Teardown GNS3 project
    if gns3_ok and topo_result.get("project_id") and spec.teardown_after:
        adapter.stop_topology(topo_result["project_id"])
        adapter.delete_project(topo_result["project_id"])
        findings.append({"severity": "info", "check": "gns3_teardown",
                          "message": f"Project '{spec.project_name}' torn down"})

    # Determine final gate
    if any(f["severity"] == "fail" for f in findings):
        gate = "FAIL"
    elif mode == "dry_run":
        gate = "WARN"

    # Save training artifact (includes traffic flows when available)
    training_pair_path = _save_training_artifact(
        canvas, spec, topo_result, probe_results, emu_result, uid, gate, out_dir,
        traffic_result=traffic_result or None)

    # Auto-export training pair to finetune dataset (non-blocking)
    try:
        from tools.studio.sim.training_exporter import export_canvas as _export_canvas
        _export_canvas(canvas)
    except Exception:
        pass

    # Build Markdown report
    lines = [
        f"# GNS3 + Cloud Simulation Report — {canvas.upper()}",
        f"**Generated:** {ts}  ",
        f"**Canvas:** {canvas}  ",
        f"**Mode:** {mode.upper()}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '⚠ WARN' if gate == 'WARN' else '✗ FAIL'}  ",
        f"**Nodes:** {len(spec.nodes)}  **Links:** {len(spec.links)}  **Probes:** {len(probe_results)}",
        "",
    ]
    if docker_started:
        lines += ["## Docker Services Started", ""]
        for n in docker_started:
            lines.append(f"- `{n}`")
        lines.append("")

    for grp, label in [
        ([f for f in findings if f["severity"] == "fail"],  "✗ Failures"),
        ([f for f in findings if f["severity"] == "warn"],  "⚠ Warnings"),
        ([f for f in findings if f["severity"] == "pass"],  "✓ Passed"),
        ([f for f in findings if f["severity"] == "info"],  "ℹ Info"),
    ]:
        if grp:
            lines.append(f"## {label}")
            for f in grp:
                lines.append(f"- **[{f['check']}]** {f['message'][:300]}")
            lines.append("")

    lines += [
        "## Probe Results", "",
        "| Probe | Type | Result |",
        "|-------|------|--------|",
    ]
    for p in probe_results:
        icon = "✓" if p["pass"] else "✗"
        lines.append(f"| {p['name']} | {p['type']} | {icon} {p['detail'][:80]} |")
    lines.append("")

    # Traffic flow section
    t_phase = traffic_result.get("phases", {}).get("traffic", {}) if traffic_result else {}
    if t_phase.get("flows"):
        reachable_count = t_phase.get("reachable", 0)
        total_count     = t_phase.get("flows_tested", 0)
        lines += [
            f"## Traffic Flows ({reachable_count}/{total_count} reachable)", "",
            "| Src | Dst IP | Label | Scenario | Result | RTT |",
            "|-----|--------|-------|----------|--------|-----|",
        ]
        for fl in t_phase["flows"]:
            icon = "✓" if fl.get("reachable") else "✗"
            rtt  = f"{fl['avg_rtt_ms']:.1f}ms" if fl.get("avg_rtt_ms") is not None else "N/A"
            lines.append(
                f"| {fl['src']} | {fl.get('dst_ip', '')} | {fl['label'][:30]} "
                f"| {fl.get('scenario', '')} | {icon} | {rtt} |"
            )
        lines.append("")

    if snapshot_name:
        lines += ["## GNS3 Snapshot", "", f"Snapshot `{snapshot_name}` saved for topology replay.", ""]

    lines += [
        "## Training Artifact", "",
        f"Saved: `{training_pair_path}`",
        "Feed into `/finetune/datasets` for canvas model fine-tuning.",
        "",
    ]

    report_path = out_dir / f"gns3_sim_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    traffic_flows_tested  = t_phase.get("flows_tested", 0)
    traffic_flows_reached = t_phase.get("reachable", 0)

    return {
        "gate": gate,
        "mode": mode,
        "canvas": canvas,
        "gns3_project_id": topo_result.get("project_id", ""),
        "nodes_deployed": topo_result.get("nodes_created", len(spec.nodes)),
        "links_deployed": topo_result.get("links_created", len(spec.links)),
        "probes_passed": sum(1 for p in probe_results if p["pass"]),
        "probes_total": len(probe_results),
        "traffic_flows_tested":   traffic_flows_tested,
        "traffic_flows_reachable": traffic_flows_reached,
        "docker_services_started": len(docker_started),
        "snapshot": snapshot_name,
        "training_artifact": training_pair_path,
        "findings": findings,
        "_probes": probe_results,
        "report_path": report_path.relative_to(_ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="GNS3 + Cloud Simulation Executor")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="", help="Canvas slug (auto-detected if omitted)")
    parser.add_argument("--json", action="store_true")
    # sim_hub.run_canvas_sim(dry_run=True) has always appended this flag, and
    # argparse exited 2 on it -- into a Popen with stdout and stderr at
    # DEVNULL, which returned {"status": "started"} for a process that was
    # already dead. Declared here so the hub's contract is honoured rather
    # than merely surviving.
    parser.add_argument("--dry-run", action="store_true",
                        help="Force dry_run: validate the spec, start nothing")
    args = parser.parse_args()
    try:
        result = run_sim(args.run_id, args.project_id, args.canvas,
                         dry_run=args.dry_run)
        gate = result["gate"]
        # Summarise probe results for the DAG detail panel
        probe_summary = [
            {"name": p["name"], "type": p["type"],
             "pass": p["pass"], "detail": p.get("detail", "")}
            for p in result.get("_probes", [])
        ]
        output = {
            "status": "success" if gate in ("PASS", "WARN") else "failed",
            "gate": gate,
            "canvas": result["canvas"],
            "mode": result["mode"],
            "nodes_deployed": result["nodes_deployed"],
            "links_deployed": result["links_deployed"],
            "probes_passed": result["probes_passed"],
            "probes_total": result["probes_total"],
            "traffic_flows_tested":    result.get("traffic_flows_tested", 0),
            "traffic_flows_reachable": result.get("traffic_flows_reachable", 0),
            "docker_services_started": result["docker_services_started"],
            "gns3_project_id": result.get("gns3_project_id", ""),
            "probe_results": probe_summary,
            "findings": len(result["findings"]),
            "artifacts": [
                {"name": "GNS3 Simulation Report",
                 "path": result["report_path"], "type": "md"},
                {"name": "Training Pair",
                 "path": result["training_artifact"], "type": "json"},
            ],
        }
        if result.get("snapshot"):
            output["gns3_snapshot"] = result["snapshot"]
        print(json.dumps(output))
        sys.exit(0 if gate in ("PASS", "WARN") else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
