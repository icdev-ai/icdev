# CUI // SP-CTI
"""Run the declared discovery adapters and report health PER FABRIC (rmf-disc-01).

    python -m tools.assets.discovery_adapters.runner --health --json
    python -m tools.assets.discovery_adapters.runner --dry-run
    python -m tools.assets.discovery_adapters.runner --run --fabric lab-gns3
    python -m tools.assets.discovery_adapters.runner --list

PER FABRIC IS THE WHOLE POINT. A programme that holds a continuous ATO across N
classification fabrics cannot read one aggregate "discovery: OK" — the fabric
whose only source has been unreachable for a month is invisible inside an
average, and it is the one that matters. Every fabric therefore reports its own
health roll-up, and the roll-up NEVER blends fabrics.

FOUR FABRIC STATES, AND TWO OF THEM MEAN "NO INVENTORY":

  unmeasured  no adapter on this fabric was in a state that says anything about
              a source (every one disabled / unconfigured / never asked). This
              is NOT a clean bill of health and it is not an outage either.
  blind       adapters WERE asked and not one can discover — every source is
              unreachable, or its dependency is absent. The estate is unmeasured
              and we know exactly why.
  partial     at least one source answers, and either another measured source
              does not or an ENABLED source was mis-declared and never asked.
              The inventory is real and incomplete, and it says so.
  covered     every source that was asked answers.

NO PERCENTAGES. Not one number in this report is a rate. A discovery sweep has
no authoritative denominator — nothing here knows how many devices the fabric
CONTAINS, only how many a source reported — so a coverage percentage would be a
fabrication (rmf-vis-01 owns the measurement that can carry a denominator).
``device_count`` is None, never 0, when nothing was discovered.

WRITES ONLY UNDER ``--run``. ``--health`` runs no discovery at all and
``--dry-run`` discovers and persists nothing, so the sweep can be inspected
against a live fabric before a single row lands in ni_devices.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.assets.discovery_adapters.base import (
    AdapterHealth,
    AdapterRegistry,
    DiscoveryResult,
    utcnow,
)
from tools.assets.discovery_adapters.sink import SinkReport, persist
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.assets.discovery_adapters.runner")

CONFIG_RELPATH = "args/discovery_adapters.yaml"

#: Config keys whose value is a CREDENTIAL. A literal in one of these is
#: REFUSED, not warned about: this repository is public, and a warning still
#: lands the secret in git. Same rule the DataBridge connection seeder applies
#: to ``auth_secret_ref``.
SECRET_KEYS: tuple[str, ...] = (
    "token",
    "password",
    "community",
    "enable_secret",
    "secret",
    "api_key",
)

#: Reference schemes a secret-bearing key may use.
SECRET_SCHEMES: tuple[str, ...] = ("env:", "file:")

FABRIC_STATES: tuple[str, ...] = ("covered", "partial", "blind", "unmeasured")


def default_config_path() -> Path:
    from icdev.core.paths import repo_root

    return repo_root(__file__) / CONFIG_RELPATH


class SecretRefusal(ValueError):
    """A credential was written literally into the declaration."""


def resolve_secret(key: str, value: Any) -> str:
    """Resolve ``env:VAR`` / ``file:PATH``; refuse a literal.

    An UNRESOLVED reference (the variable is not set) returns "" — the adapter
    then reports ``unconfigured``, which is correct and is not the same as a
    refusal.
    """
    raw = str(value or "")
    if not raw:
        return ""
    if raw.startswith("env:"):
        return os.environ.get(raw[4:], "")
    if raw.startswith("file:"):
        path = Path(raw[5:])
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    raise SecretRefusal(
        "`%s` holds a literal value. Declare it as %s — a credential in this "
        "file is a credential in a public git history."
        % (key, " or ".join(s + "<...>" for s in SECRET_SCHEMES))
    )


def resolve_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Copy of ``raw`` with secret-bearing keys resolved. May raise SecretRefusal."""
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        if key in SECRET_KEYS:
            out[key] = resolve_secret(key, value)
        else:
            out[key] = value
    return out


# ── Report shapes ─────────────────────────────────────────────────────────────


@dataclass
class FabricReport:
    fabric: str
    name: str = ""
    classification: str = ""
    results: list[DiscoveryResult] = field(default_factory=list)
    persisted: SinkReport | None = None

    @property
    def misconfigured(self) -> int:
        """Sources that are ENABLED but whose declaration could not be used.

        Distinct from ``disabled``, which is a deliberate choice, and from
        ``unreachable``, which is a fact about the source. A misconfigured
        source is a broken declaration — somebody meant to measure this and it
        never happened.
        """
        return sum(1 for r in self.results if r.health.state == "unconfigured")

    @property
    def state(self) -> str:
        measured = [r for r in self.results if r.health.measured]
        if not measured:
            return "unmeasured"
        discovering = [r for r in measured if r.health.can_discover]
        if not discovering:
            return "blind"
        if len(discovering) < len(measured):
            return "partial"
        # `covered` is the claim "every source that was asked answers". A
        # source somebody ENABLED and mis-declared was never asked, so the
        # claim is not available — the demo run that built this fabric report
        # read `covered` while its NetBox token resolved to empty.
        return "partial" if self.misconfigured else "covered"

    @property
    def device_count(self) -> int | None:
        """Devices discovered on this fabric, or None if nothing was discovered.

        None, never 0: "no source was asked" and "every source was asked and
        the fabric is empty" justify opposite decisions.
        """
        if not any(r.discovered for r in self.results):
            return None
        return sum(len(r.devices) for r in self.results)

    def health_by_state(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.health.state] = counts.get(result.health.state, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "fabric": self.fabric,
            "name": self.name,
            "classification": self.classification,
            "state": self.state,
            "device_count": self.device_count,
            "sources_declared": len(self.results),
            "sources_measured": sum(1 for r in self.results if r.health.measured),
            "sources_discovering": sum(
                1 for r in self.results if r.health.can_discover
            ),
            "sources_misconfigured": self.misconfigured,
            "health_by_state": self.health_by_state(),
            "adapters": [r.to_dict() for r in self.results],
            "persisted": self.persisted.to_dict() if self.persisted else None,
        }


# ── Config loading ────────────────────────────────────────────────────────────


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else default_config_path()
    if not cfg_path.exists():
        return {"fabrics": [], "_config_path": str(cfg_path), "_error": "not found"}
    try:
        import yaml

        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        return {
            "fabrics": [],
            "_config_path": str(cfg_path),
            "_error": "unreadable: %s" % exc,
        }
    data["_config_path"] = str(cfg_path)
    return data


def _build_result(
    fabric_id: str, entry: dict[str, Any]
) -> tuple[DiscoveryResult, Any]:
    """Health-check one declared adapter instance. Returns (result, adapter|None).

    ``adapter`` is None whenever nothing may be discovered — the caller never
    has to re-derive that from the state.
    """
    adapter_name = str(entry.get("adapter") or entry.get("id") or "")
    instance_id = str(entry.get("id") or adapter_name)

    def _result(state: str, detail: str, **kw: Any) -> tuple[DiscoveryResult, Any]:
        health = AdapterHealth(
            adapter=instance_id, fabric=fabric_id, state=state, detail=detail, **kw
        )
        return (
            DiscoveryResult(adapter=instance_id, fabric=fabric_id, health=health),
            None,
        )

    if not adapter_name:
        return _result("unconfigured", "declaration names no `adapter`")
    if not entry.get("enabled", False):
        # `disabled` says NOTHING about the source and is never counted as a
        # verdict on it — see AdapterHealth.measured.
        return _result(
            "disabled",
            str(entry.get("disabled_reason") or "enabled: false in %s" % CONFIG_RELPATH),
        )

    try:
        adapter_cls = AdapterRegistry.get(adapter_name)
    except KeyError as exc:
        return _result("unconfigured", str(exc))

    try:
        config = resolve_config(entry.get("config") or {})
    except SecretRefusal as exc:
        return _result("unconfigured", str(exc))

    adapter = adapter_cls(fabric=fabric_id, config=config)
    # The INSTANCE id, not the class name: two CSV exports on one fabric are two
    # sources, and stable_id() mixes the adapter in, so sharing a name would
    # collapse their devices onto one another's rows.
    adapter.name = instance_id
    try:
        health = adapter.health()
    except Exception as exc:  # noqa: BLE001 — a contract violation, reported
        return _result(
            "unreachable",
            "health() raised (it must not): %s: %s" % (type(exc).__name__, exc),
        )
    health.adapter = instance_id
    result = DiscoveryResult(adapter=instance_id, fabric=fabric_id, health=health)
    return result, (adapter if health.can_discover else None)


def collect(
    config: dict[str, Any] | None = None,
    *,
    fabric: str = "",
    discover: bool = True,
) -> list[FabricReport]:
    """Health-check (and optionally discover) every declared adapter instance."""
    cfg = config if config is not None else load_config()
    reports: list[FabricReport] = []
    for fab in cfg.get("fabrics") or []:
        fabric_id = str(fab.get("id") or "")
        if fabric and fabric_id != fabric:
            continue
        report = FabricReport(
            fabric=fabric_id,
            name=str(fab.get("name") or fabric_id),
            classification=str(fab.get("classification") or ""),
        )
        for entry in fab.get("adapters") or []:
            result, adapter = _build_result(fabric_id, entry)
            if discover and adapter is not None:
                try:
                    result.devices = adapter.discover()
                    result.discovered = True
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(
                        "discover() failed: %s: %s" % (type(exc).__name__, exc)
                    )
                    logger.warning(
                        "discover() failed for %s on %s: %s",
                        result.adapter,
                        fabric_id,
                        exc,
                    )
            report.results.append(result)
        reports.append(report)
    return reports


def run(
    config: dict[str, Any] | None = None,
    *,
    fabric: str = "",
    discover: bool = True,
    write: bool = False,
    conn=None,
) -> dict[str, Any]:
    """Full sweep. ``write=False`` (the default) persists NOTHING."""
    cfg = config if config is not None else load_config()
    reports = collect(cfg, fabric=fabric, discover=discover)

    if write and discover:
        for report in reports:
            devices = [d for r in report.results if r.discovered for d in r.devices]
            if devices:
                report.persisted = persist(devices, conn=conn)

    fabric_states: dict[str, int] = {}
    for report in reports:
        fabric_states[report.state] = fabric_states.get(report.state, 0) + 1
    counted = [r.device_count for r in reports if r.device_count is not None]

    return {
        "generated_at": utcnow(),
        "config_path": cfg.get("_config_path", ""),
        "config_error": cfg.get("_error"),
        "mode": "run" if write else ("dry-run" if discover else "health"),
        "registered_adapters": AdapterRegistry.names(),
        "fabrics": [r.to_dict() for r in reports],
        "totals": {
            "fabrics_declared": len(reports),
            "fabric_states": fabric_states,
            # None, never 0, when no fabric discovered anything.
            "devices_discovered": sum(counted) if counted else None,
            "devices_written": (
                sum(r.persisted.written for r in reports if r.persisted)
                if write
                else None
            ),
        },
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def _render(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Asset discovery — %s (%s)" % (result["mode"], result["generated_at"]))
    lines.append("config: %s" % (result.get("config_path") or "(none)"))
    if result.get("config_error"):
        lines.append("CONFIG ERROR: %s" % result["config_error"])
    lines.append("")
    for fab in result["fabrics"]:
        count = fab["device_count"]
        lines.append(
            "[%s] %s — %s | devices: %s"
            % (
                fab["state"].upper(),
                fab["fabric"],
                fab["classification"] or "unlabelled",
                "not discovered" if count is None else count,
            )
        )
        for ad in fab["adapters"]:
            health = ad["health"]
            dev = ad["device_count"]
            lines.append(
                "    %-14s %-12s %s"
                % (
                    ad["adapter"],
                    health["state"],
                    "%s device(s)" % dev if dev is not None else "not asked",
                )
            )
            if health["detail"]:
                lines.append("        %s" % health["detail"])
            for err in ad["errors"]:
                lines.append("        ERROR %s" % err)
        if fab.get("persisted"):
            p = fab["persisted"]
            lines.append(
                "    persisted: %d inserted, %d updated%s"
                % (
                    p["inserted"],
                    p["updated"],
                    (
                        " | unplaced: %s" % ", ".join(p["unplaced_fields"])
                        if p["unplaced_fields"]
                        else ""
                    ),
                )
            )
            for err in p["errors"]:
                lines.append("        WRITE ERROR %s" % err)
        lines.append("")
    totals = result["totals"]
    lines.append(
        "fabrics: %d | states: %s | devices discovered: %s"
        % (
            totals["fabrics_declared"],
            totals["fabric_states"] or "{}",
            (
                "none discovered"
                if totals["devices_discovered"] is None
                else totals["devices_discovered"]
            ),
        )
    )
    if totals["devices_written"] is not None:
        lines.append("rows written to ni_devices: %d" % totals["devices_written"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run declared asset discovery adapters, reporting health per fabric"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--health",
        action="store_true",
        help="health-check every declared adapter; discover nothing, write nothing",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="health-check and discover; write NOTHING to ni_devices",
    )
    mode.add_argument(
        "--run", action="store_true", help="health-check, discover, and write ni_devices"
    )
    mode.add_argument(
        "--list", action="store_true", help="list registered adapter names and exit"
    )
    parser.add_argument("--config", default="", help="path to the declaration YAML")
    parser.add_argument("--fabric", default="", help="restrict to one fabric id")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.list:
        payload = {"registered_adapters": AdapterRegistry.names()}
        print(json.dumps(payload, indent=2) if args.json else "\n".join(payload["registered_adapters"]))
        return 0

    cfg = load_config(args.config or None)
    discover = not args.health
    result = run(cfg, fabric=args.fabric, discover=discover, write=bool(args.run))
    print(json.dumps(result, indent=2, default=str) if args.json else _render(result))
    # Exit 2 = the sweep could not be produced. A sweep that could not run is
    # never the same as a sweep that found nothing.
    return 2 if result.get("config_error") else 0


if __name__ == "__main__":
    sys.exit(main())
