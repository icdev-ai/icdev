# CUI // SP-CTI
"""Genesis Reflex — passive network asset discovery (24h cadence, rmf-disc-02).

WHAT IT DOES

Runs the discovery engine on a cadence against the targets a deployment has
DECLARED in ``args/genesis_config.yaml`` (``reflexes.asset_discovery.targets``),
persists each sweep to ``nc_discovery_scans``, imports what answered into
``ni_devices`` with ``source='discovery'``, and measures the inventory it is
responsible for.

PASSIVE-ONLY BY DEFAULT, AND THAT IS A SECURITY POSTURE, NOT A CONVENIENCE

``ping`` is the only method this reflex will run unless a deployment explicitly
sets ``allow_active_scan: true``. The other two are not "more thorough" — they
are qualitatively different acts. ``snmp`` presents a community string and
``ssh`` presents an account password to live infrastructure, on a schedule, with
no human present. A credentialed sweep of production equipment initiated by an
autonomous daemon is exactly the thing an assessor asks about, and defaulting it
on because it returns richer data would be the ``|| true`` failure of this
repo's security posture in a new place. An operator who wants it turns it on and
owns the decision.

A ping sweep still discovers HOSTS (``ping_sweep`` returns the addresses that
answered) but produces no device records, because nothing on the wire tells an
ICMP echo what vendor answered it. So a passive run typically records a scan
with ``devices_discovered: 0`` and a real ``targets_scanned`` count. That is a
MEASUREMENT, not a failure, and it is reported as one.

NO TARGETS IS NOT `ok`

A deployment that has declared no targets — which is every deployment until
somebody edits the config — cannot discover anything, and this reflex says
``unmeasured`` with ``no_targets_declared`` rather than reporting a clean run.
A reflex whose empty result is indistinguishable from a healthy one is how a
capability stays dead behind a green dashboard, which is the defect this whole
card exists to close. The measurement it CAN always make is the inventory's
provenance split, and that is the part that runs unconditionally.

IT NEVER SEEDS SYNTHETIC DATA

``discovery_store.seed_synthetic_devices`` exists and this reflex does not call
it, at any setting. Synthetic rows are a demo fixture an operator asks for once
from the CLI; a daemon fabricating inventory rows on a 24-hour cadence would
manufacture its own evidence, and the ``source`` column that keeps those rows
honest would then be the only thing standing between a fabrication and every
inventory surface on the platform. One deliberate act is a fixture. A recurring
one is a data source.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

from datetime import datetime, timezone
from typing import Any

from icdev.core.paths import repo_root
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS = 24

# The ONE root resolver (xit-decl-03). `Path(__file__).parents[3]` would be a
# hard-coded claim about where this file sits -- true today, silently wrong the
# moment the kernel packages move.
_CONFIG_PATH = repo_root(__file__) / "args" / "genesis_config.yaml"

#: Ceiling on how many addresses ONE reflex cycle may sweep. The daemon gives
#: each reflex a timeout, and a sweep that blows through it is killed mid-run,
#: leaving a `running` scan row nothing will ever complete.
MAX_TARGETS_PER_CYCLE = 512

_FALLBACK_CFG: dict[str, Any] = {
    # Empty on purpose. Nothing can guess a deployment's address space, and a
    # default subnet here would make this reflex sweep whatever happened to be
    # on the other end of 10.0.0.0/24 at whatever site it was installed at.
    "targets": [],
    "method": "ping",
    "allow_active_scan": False,
    "topology_id": None,
    "timeout": 1.0,
    "hop_limit": 0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_config() -> dict[str, Any]:
    try:
        import yaml
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        section = ((cfg.get("reflexes") or {}).get("asset_discovery") or {})
        merged = dict(_FALLBACK_CFG)
        merged.update({k: v for k, v in section.items() if k in _FALLBACK_CFG})
        return merged
    except Exception as exc:
        logger.warning("[asset_discovery] config unreadable (%s); using fallback", exc)
        return dict(_FALLBACK_CFG)


def _hours_since(iso_str: str) -> float | None:
    """Age in hours, or None when there is no usable timestamp.

    None, never a large number: "no successful scan has ever been recorded" and
    "the last one was a long time ago" are different states, and collapsing the
    first into the second invents a scan that never happened.
    """
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 2)
    except Exception:
        return None


def _expand_size(targets: list[str]) -> int:
    """Address count *targets* expands to, without touching the network."""
    import ipaddress

    total = 0
    for t in targets:
        try:
            total += ipaddress.ip_network(t, strict=False).num_addresses
        except ValueError:
            total += 1
    return total


def _finalise(result: dict[str, Any]) -> dict[str, Any]:
    """Attach the `details` payload the daemon persists, then return.

    Every early return goes through here so a refusal is recorded on the
    genesis_audit row with the SAME shape a completed sweep is. A reflex whose
    refusals are invisible in its own audit trail is indistinguishable from one
    that is not running.
    """
    result["details"] = {
        "status": result.get("status"),
        "refusals": result.get("refusals", []),
        "errors": result.get("errors", []),
        "inventory": result.get("inventory"),
        "scan_id": result.get("scan_id"),
        "devices_discovered": result.get("devices_discovered"),
    }
    return result


def run(ctx: dict[str, Any], trust: Any = None) -> dict[str, Any]:
    """Sweep declared targets passively and measure the device inventory.

    The daemon dispatches reflexes as ``fn(config, trust)`` — the second
    positional argument is the TrustKernel, NOT a DB connection.

    ctx keys (each overrides the same key under
    ``reflexes.asset_discovery`` in args/genesis_config.yaml):
        targets            list of IPs/CIDRs. Empty => `unmeasured`.
        method             ping | snmp | ssh. Non-passive needs allow_active_scan.
        allow_active_scan  bool, default False.
        topology_id        topology to attribute discovered devices to.
        dry_run            plan the sweep, run nothing, write nothing.

    Returns:
        status, scan_id, targets_declared, addresses_expanded, devices_discovered,
        devices_imported, inventory, last_scan_age_hours, refusals, errors
    """
    from tools.network import discovery_store as store

    cfg = _load_config()
    for key in ("targets", "method", "allow_active_scan", "topology_id", "timeout", "hop_limit"):
        if key in ctx:
            cfg[key] = ctx[key]
    dry_run = bool(ctx.get("dry_run", False))

    targets = [str(t).strip() for t in (cfg.get("targets") or []) if str(t).strip()]
    method = str(cfg.get("method") or "ping").lower()

    result: dict[str, Any] = {
        # THE DAEMON'S CONTRACT, and it is easy to get silently wrong: a reflex
        # returning no `success` key is scored a FAILURE on every cycle forever
        # (tools/daemon/base.py::classify_failure). `success` here means "this
        # cycle completed" and is set False only by the error path below --
        # a refusal and an `unmeasured` verdict are the reflex WORKING, and
        # scoring them as failures would put a correctly-behaving reflex into
        # the circuit breaker on an unconfigured deployment.
        "success": True,
        # What the cycle actually did. `scans_recorded` would be a lifetime
        # count and `devices_discovered` is None on a passive sweep, so neither
        # describes THIS run; `metric_value` is the number of addresses this
        # cycle swept, which is 0 for a refusal and is exactly what
        # `success_metric: {name: addresses_swept, threshold: 0, operator: gte}`
        # in args/genesis_config.yaml is written against.
        "metric_value": 0.0,
        "cadence_hours": CADENCE_HOURS,
        "status": "ok",
        "dry_run": dry_run,
        "method": method,
        "targets_declared": len(targets),
        "addresses_expanded": None,
        "scan_id": None,
        "devices_discovered": None,
        "devices_imported": None,
        "inventory": None,
        "last_scan_age_hours": None,
        # Every refusal is NAMED. A reflex that declines to act and reports
        # nothing about why is indistinguishable from one that is not running.
        "refusals": [],
        "errors": [],
        "ran_at": _now(),
    }

    # ── The measurement that always runs ────────────────────────────────────
    # Independent of whether a sweep happened: this is the reflex's standing
    # answer to "what is in the inventory, and how much of it was OBSERVED".
    try:
        inv = store.device_inventory_stats()
        result["inventory"] = inv
        if not inv.get("measurable"):
            # UNMEASURABLE is never folded into a clean report.
            result["status"] = "unmeasured"
            result["refusals"].append("inventory_unreadable")
    except Exception as exc:
        logger.warning("[asset_discovery] inventory stats failed: %s", exc)
        result["errors"].append(f"inventory: {exc}")
        result["status"] = "unmeasured"

    try:
        scans = store.list_scans(limit=25)
        completed = [s for s in scans if s.get("status") == "completed"]
        if completed:
            result["last_scan_age_hours"] = _hours_since(
                completed[0].get("completed_at") or completed[0].get("created_at")
            )
        result["scans_recorded"] = len(scans)
    except Exception as exc:
        result["errors"].append(f"scan history: {exc}")

    # ── The sweep ───────────────────────────────────────────────────────────
    if not targets:
        # The common case, and it must not read as a healthy cycle. A
        # deployment that has declared no address space cannot discover
        # anything, and saying `ok` here is how this reflex would go inert
        # behind its own green status.
        result["status"] = "unmeasured"
        result["refusals"].append("no_targets_declared")
        return _finalise(result)

    if method not in store.PASSIVE_METHODS and not bool(cfg.get("allow_active_scan")):
        result["status"] = "refused"
        result["refusals"].append(f"active_method_{method}_requires_allow_active_scan")
        return _finalise(result)

    expanded = _expand_size(targets)
    result["addresses_expanded"] = expanded
    if expanded > MAX_TARGETS_PER_CYCLE:
        # Refused whole rather than truncated: a sweep that quietly scanned a
        # prefix of the declared space would report a partial estate as a
        # complete one, and the next cycle would do the same.
        result["status"] = "refused"
        result["refusals"].append(
            f"expands_to_{expanded}_addresses_over_{MAX_TARGETS_PER_CYCLE}"
        )
        return _finalise(result)

    if dry_run:
        result["status"] = "dry_run"
        return _finalise(result)

    from tools.network import discovery as _disc

    topology_id = cfg.get("topology_id") or None
    scan_id = None
    try:
        scan_id = store.create_scan(
            name=f"asset_discovery reflex — {method} sweep",
            targets=targets,
            method=method,
            topology_id=topology_id,
            config={"hop_limit": int(cfg.get("hop_limit") or 0), "reflex": "asset_discovery"},
        )
        result["scan_id"] = scan_id
        sweep = _disc.run_discovery(
            targets=targets,
            method=method,
            timeout=float(cfg.get("timeout") or 1.0),
            hop_limit=int(cfg.get("hop_limit") or 0),
        )
        store.record_scan_result(scan_id, sweep)
        stats = sweep.get("stats", {})
        result["devices_discovered"] = stats.get("devices_discovered")
        result["targets_scanned"] = stats.get("targets_scanned")
        result["stats"] = stats

        if topology_id:
            imported = store.import_scan_devices(
                scan_id, topology_id, graph=sweep.get("graph_json"),
            )
            result["devices_imported"] = (
                imported.get("devices_created", 0) + imported.get("devices_updated", 0)
            )
            result["import"] = imported
        else:
            # A sweep with nowhere to attribute its devices is recorded and left
            # for a human to import. ni_devices rows are keyed by topology, so
            # inventing a topology here would create an orphan estate nothing
            # renders.
            result["refusals"].append("no_topology_id_declared_devices_not_imported")

        # A completed sweep that found nothing is a real measurement, and the
        # ping method structurally cannot produce device records at all.
        if method in store.PASSIVE_METHODS and not stats.get("devices_discovered"):
            result["note"] = (
                "passive sweep: ICMP identifies live HOSTS but yields no device "
                "records — targets_scanned is the measurement here, not devices_discovered"
            )
    except Exception as exc:
        logger.error("[asset_discovery] sweep failed: %s", exc)
        result["status"] = "error"
        result["success"] = False
        result["errors"].append(str(exc))
        if scan_id:
            store.record_scan_failure(scan_id, str(exc))
    else:
        result["metric_value"] = float(result.get("targets_scanned") or 0)

    return _finalise(result)


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as
    # the GenesisDaemon. override=True: a pip-installed ICDEV in site-packages
    # may already have loaded a different checkout's .env at import. Repo root
    # via __file__, not cwd.
    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(repo_root(__file__) / ".env", override=True)
    except ImportError:
        pass
    import json as _json
    print(_json.dumps(run({}), indent=2, default=str))
