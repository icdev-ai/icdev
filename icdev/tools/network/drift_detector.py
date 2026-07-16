# CUI // SP-CTI
"""Network Config Drift Detector — compares network device configurations.

Detects configuration drift between a baseline (golden) snapshot and the current
live configuration for network devices. Classifies drift by category and severity,
then emits ACOIC events to trigger DIC document regeneration.

Drift categories:
  • ip_address_change     — IP address, subnet, or gateway changed
  • vlan_change           — VLAN table entry added/removed/modified
  • interface_state       — interface status or description changed
  • routing_change        — static routes or routing protocol neighbors changed
  • acl_change            — access-control list entry added/removed
  • snmp_change           — SNMP community string or trap target changed
  • banner_change         — login/motd banner changed (compliance indicator)
  • config_removed        — device entirely absent from current snapshot
  • config_added          — device newly appeared in current snapshot
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── Severity ranking ──────────────────────────────────────────────────────────

_CATEGORY_SEVERITY: dict[str, str] = {
    "config_removed":   "critical",
    "acl_change":       "high",
    "ip_address_change":"high",
    "routing_change":   "medium",
    "vlan_change":      "medium",
    "interface_state":  "low",
    "snmp_change":      "low",
    "banner_change":    "low",
    "config_added":     "info",
}

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class DriftItem:
    device_id: str
    category: str
    severity: str
    description: str
    baseline_value: str = ""
    current_value: str = ""


@dataclass
class DriftReport:
    topology_id: str
    baseline_snapshot_id: str
    current_snapshot_id: str
    detected_at: str = field(default_factory=_now)
    items: list[DriftItem] = field(default_factory=list)
    summary: str = ""
    overall_severity: str = "info"

    @property
    def drift_count(self) -> int:
        return len(self.items)

    @property
    def has_critical(self) -> bool:
        return any(i.severity == "critical" for i in self.items)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["drift_count"] = self.drift_count
        d["has_critical"] = self.has_critical
        return d


# ── Config parsers ─────────────────────────────────────────────────────────────

def _extract_ips(config_text: str) -> dict[str, str]:
    """Return {interface: ip/mask} from a Cisco IOS-style config blob."""
    ips: dict[str, str] = {}
    current_iface = None
    for line in config_text.splitlines():
        m = re.match(r"^interface\s+(\S+)", line)
        if m:
            current_iface = m.group(1)
        m2 = re.match(r"^\s+ip address\s+(\S+\s+\S+)", line)
        if m2 and current_iface:
            ips[current_iface] = m2.group(1)
    return ips


def _extract_vlans(config_text: str) -> dict[str, str]:
    """Return {vlan_id: name} from config."""
    vlans: dict[str, str] = {}
    for line in config_text.splitlines():
        m = re.match(r"^vlan\s+(\d+)", line)
        if m:
            vlans[m.group(1)] = ""
        m2 = re.match(r"^\s+name\s+(.+)", line)
        if m2 and vlans:
            last = list(vlans)[-1]
            vlans[last] = m2.group(1).strip()
    return vlans


def _extract_routes(config_text: str) -> list[str]:
    """Return list of static ip route lines."""
    return [
        line.strip() for line in config_text.splitlines()
        if re.match(r"^\s*ip route\s+", line)
    ]


def _extract_acls(config_text: str) -> list[str]:
    """Return ACL permit/deny lines."""
    return [
        line.strip() for line in config_text.splitlines()
        if re.match(r"^\s*(permit|deny)\s+", line)
    ]


def _extract_interfaces(config_text: str) -> dict[str, str]:
    """Return {interface: 'up'|'shutdown'}."""
    states: dict[str, str] = {}
    current_iface = None
    for line in config_text.splitlines():
        m = re.match(r"^interface\s+(\S+)", line)
        if m:
            current_iface = m.group(1)
            states.setdefault(current_iface, "up")
        # Match `shutdown` but not `no shutdown`
        if current_iface and re.match(r"^\s+shutdown\s*$", line):
            states[current_iface] = "shutdown"
        elif current_iface and re.match(r"^\s+no\s+shutdown\s*$", line):
            states[current_iface] = "up"
    return states


def _extract_banners(config_text: str) -> str:
    """Return banner motd/login content hash."""
    lines = []
    in_banner = False
    for line in config_text.splitlines():
        if re.match(r"^banner\s+(motd|login)", line):
            in_banner = True
        elif in_banner:
            if line.strip() == "^C" or line.strip().startswith("!"):
                in_banner = False
            else:
                lines.append(line)
    return "\n".join(lines)


# ── Core diff logic ────────────────────────────────────────────────────────────

def _diff_device(
    device_id: str,
    baseline_cfg: str,
    current_cfg: str,
) -> list[DriftItem]:
    items: list[DriftItem] = []

    # IP address drift
    b_ips = _extract_ips(baseline_cfg)
    c_ips = _extract_ips(current_cfg)
    for iface, b_ip in b_ips.items():
        c_ip = c_ips.get(iface)
        if c_ip is None:
            items.append(DriftItem(
                device_id=device_id,
                category="ip_address_change",
                severity=_CATEGORY_SEVERITY["ip_address_change"],
                description=f"Interface {iface}: IP removed",
                baseline_value=b_ip,
                current_value="<removed>",
            ))
        elif c_ip != b_ip:
            items.append(DriftItem(
                device_id=device_id,
                category="ip_address_change",
                severity=_CATEGORY_SEVERITY["ip_address_change"],
                description=f"Interface {iface}: IP changed",
                baseline_value=b_ip,
                current_value=c_ip,
            ))
    for iface, c_ip in c_ips.items():
        if iface not in b_ips:
            items.append(DriftItem(
                device_id=device_id,
                category="ip_address_change",
                severity="info",
                description=f"Interface {iface}: new IP assigned",
                baseline_value="<none>",
                current_value=c_ip,
            ))

    # VLAN drift
    b_vlans = _extract_vlans(baseline_cfg)
    c_vlans = _extract_vlans(current_cfg)
    for vid, bname in b_vlans.items():
        if vid not in c_vlans:
            items.append(DriftItem(
                device_id=device_id,
                category="vlan_change",
                severity=_CATEGORY_SEVERITY["vlan_change"],
                description=f"VLAN {vid} ({bname}) removed",
                baseline_value=f"vlan {vid} name={bname}",
                current_value="<removed>",
            ))
        elif c_vlans[vid] != bname:
            items.append(DriftItem(
                device_id=device_id,
                category="vlan_change",
                severity="low",
                description=f"VLAN {vid} renamed from '{bname}' to '{c_vlans[vid]}'",
                baseline_value=bname,
                current_value=c_vlans[vid],
            ))
    for vid, cname in c_vlans.items():
        if vid not in b_vlans:
            items.append(DriftItem(
                device_id=device_id,
                category="vlan_change",
                severity="info",
                description=f"VLAN {vid} ({cname}) added",
                baseline_value="<none>",
                current_value=f"vlan {vid} name={cname}",
            ))

    # Interface state drift
    b_ifaces = _extract_interfaces(baseline_cfg)
    c_ifaces = _extract_interfaces(current_cfg)
    for iface, b_state in b_ifaces.items():
        c_state = c_ifaces.get(iface, "up")
        if c_state != b_state:
            items.append(DriftItem(
                device_id=device_id,
                category="interface_state",
                severity=_CATEGORY_SEVERITY["interface_state"],
                description=f"Interface {iface}: state changed {b_state} → {c_state}",
                baseline_value=b_state,
                current_value=c_state,
            ))

    # Routing drift
    b_routes = set(_extract_routes(baseline_cfg))
    c_routes = set(_extract_routes(current_cfg))
    for r in b_routes - c_routes:
        items.append(DriftItem(
            device_id=device_id,
            category="routing_change",
            severity=_CATEGORY_SEVERITY["routing_change"],
            description=f"Static route removed: {r}",
            baseline_value=r,
            current_value="<removed>",
        ))
    for r in c_routes - b_routes:
        items.append(DriftItem(
            device_id=device_id,
            category="routing_change",
            severity="info",
            description=f"Static route added: {r}",
            baseline_value="<none>",
            current_value=r,
        ))

    # ACL drift
    b_acls = set(_extract_acls(baseline_cfg))
    c_acls = set(_extract_acls(current_cfg))
    for a in b_acls - c_acls:
        items.append(DriftItem(
            device_id=device_id,
            category="acl_change",
            severity=_CATEGORY_SEVERITY["acl_change"],
            description=f"ACL entry removed: {a}",
            baseline_value=a,
            current_value="<removed>",
        ))
    for a in c_acls - b_acls:
        items.append(DriftItem(
            device_id=device_id,
            category="acl_change",
            severity=_CATEGORY_SEVERITY["acl_change"],
            description=f"ACL entry added: {a}",
            baseline_value="<none>",
            current_value=a,
        ))

    # Banner drift
    b_banner = _extract_banners(baseline_cfg)
    c_banner = _extract_banners(current_cfg)
    if b_banner and c_banner and b_banner != c_banner:
        items.append(DriftItem(
            device_id=device_id,
            category="banner_change",
            severity=_CATEGORY_SEVERITY["banner_change"],
            description="Banner (motd/login) content changed",
            baseline_value=_hash(b_banner),
            current_value=_hash(c_banner),
        ))

    return items


# ── Public API ─────────────────────────────────────────────────────────────────

def detect_drift(
    topology_id: str,
    baseline_snapshot: dict[str, Any],
    current_snapshot: dict[str, Any],
    *,
    baseline_snapshot_id: str = "",
    current_snapshot_id: str = "",
) -> DriftReport:
    """Compare baseline vs current network snapshots and return a DriftReport.

    Args:
        topology_id: The network topology ID (used as a namespace in ACOIC events).
        baseline_snapshot: Dict mapping device_id → config_text (golden configs).
        current_snapshot: Dict mapping device_id → config_text (live configs).
        baseline_snapshot_id: Optional snapshot ID for the baseline.
        current_snapshot_id: Optional snapshot ID for the current state.

    Returns:
        DriftReport with drift items sorted by severity (critical first).
    """
    report = DriftReport(
        topology_id=topology_id,
        baseline_snapshot_id=baseline_snapshot_id or _hash(json.dumps(baseline_snapshot, sort_keys=True)),
        current_snapshot_id=current_snapshot_id or _hash(json.dumps(current_snapshot, sort_keys=True)),
    )

    b_devices = set(baseline_snapshot.keys())
    c_devices = set(current_snapshot.keys())

    # Devices present in baseline but missing from current
    for device_id in b_devices - c_devices:
        report.items.append(DriftItem(
            device_id=device_id,
            category="config_removed",
            severity=_CATEGORY_SEVERITY["config_removed"],
            description=f"Device {device_id} config missing from current snapshot",
            baseline_value=f"<{len(baseline_snapshot[device_id])} chars>",
            current_value="<absent>",
        ))

    # Devices new in current but absent from baseline
    for device_id in c_devices - b_devices:
        report.items.append(DriftItem(
            device_id=device_id,
            category="config_added",
            severity=_CATEGORY_SEVERITY["config_added"],
            description=f"New device {device_id} appeared in current snapshot",
            baseline_value="<absent>",
            current_value=f"<{len(current_snapshot[device_id])} chars>",
        ))

    # Devices present in both — deep compare
    for device_id in b_devices & c_devices:
        b_cfg = baseline_snapshot[device_id]
        c_cfg = current_snapshot[device_id]
        if b_cfg == c_cfg:
            continue
        report.items.extend(_diff_device(device_id, b_cfg, c_cfg))

    # Sort by severity (critical first)
    report.items.sort(
        key=lambda i: -_SEVERITY_ORDER.get(i.severity, 0)
    )

    # Compute overall severity
    if report.items:
        report.overall_severity = report.items[0].severity
        report.summary = (
            f"{report.drift_count} drift item(s) detected across "
            f"{len({i.device_id for i in report.items})} device(s). "
            f"Highest severity: {report.overall_severity}."
        )
    else:
        report.overall_severity = "info"
        report.summary = "No configuration drift detected."

    logger.info(
        "[drift_detector] topology=%s items=%d severity=%s",
        topology_id, report.drift_count, report.overall_severity,
    )
    return report


def emit_drift_events(
    report: DriftReport,
    *,
    document_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> dict[str, Any]:
    """Emit ACOIC drift events for all items in a DriftReport.

    Calls ``record_drift_event`` for each unique (device_id, category) pair,
    then optionally enqueues a DIC document for regeneration if document_id
    is provided.

    Returns a dict with {event_ids, enqueued, skipped}.
    """
    if not report.items:
        return {"event_ids": [], "enqueued": [], "skipped": 0}

    try:
        from tools.document_intelligence.acoic import record_drift_event, enqueue_regen
    except ImportError as exc:
        logger.warning("[drift_detector] ACOIC import failed: %s", exc)
        return {"error": str(exc)}

    event_ids: list[str] = []
    seen: set[tuple[str, str]] = set()

    for item in report.items:
        key = (item.device_id, item.category)
        if key in seen:
            continue
        seen.add(key)
        try:
            # Content-derived dedup key: the snapshot ids are already content
            # hashes (see detect_drift), so an unchanged drift re-reported on the
            # next scheduled run collapses onto the same row instead of appending
            # a duplicate. A real config change moves current_snapshot_id and
            # therefore produces a genuinely new event.
            eid = record_drift_event(
                source=f"network.{item.category}",
                entity=item.device_id,
                severity=item.severity,
                payload={
                    "topology_id": report.topology_id,
                    "category": item.category,
                    "description": item.description,
                    "baseline_value": item.baseline_value,
                    "current_value": item.current_value,
                    "snapshot_baseline": report.baseline_snapshot_id,
                    "snapshot_current": report.current_snapshot_id,
                    "document_id": document_id,
                },
                dedup_key=(
                    f"{report.topology_id}|{item.device_id}|{item.category}"
                    f"|{report.baseline_snapshot_id}|{report.current_snapshot_id}"
                ),
                tenant_id=tenant_id or None,
                classification=classification or None,
            )
            event_ids.append(eid)
        except Exception as exc:
            logger.warning("[drift_detector] record_drift_event failed: %s", exc)

    enqueued = []
    if document_id and event_ids:
        try:
            enqueued.append(enqueue_regen(
                document_id,
                event_id=event_ids[0],
                drift_source=f"network/{report.topology_id}",
                drift_entity=report.topology_id,
                severity=report.overall_severity,
                dedup_key=(
                    f"{document_id}|{report.topology_id}"
                    f"|{report.current_snapshot_id}"
                ),
                tenant_id=tenant_id or None,
                classification=classification or None,
            ))
        except Exception as exc:
            logger.warning("[drift_detector] enqueue_regen failed: %s", exc)

    return {
        "event_ids": event_ids,
        "enqueued": enqueued,
        "skipped": len(report.items) - len(seen),
    }


def run_drift_check(
    topology_id: str,
    baseline_snapshot: dict[str, Any],
    current_snapshot: dict[str, Any],
    *,
    document_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
    baseline_snapshot_id: str = "",
    current_snapshot_id: str = "",
) -> dict[str, Any]:
    """Convenience wrapper: detect drift, emit ACOIC events, return full result."""
    report = detect_drift(
        topology_id, baseline_snapshot, current_snapshot,
        baseline_snapshot_id=baseline_snapshot_id,
        current_snapshot_id=current_snapshot_id,
    )
    events = emit_drift_events(
        report,
        document_id=document_id,
        tenant_id=tenant_id,
        classification=classification,
    )
    return {
        "report": report.to_dict(),
        "events": events,
    }
