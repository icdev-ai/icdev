from __future__ import annotations

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Server Migration Canvas engine.

Supports P2P, P2V, and V2V server migrations.  All functions are
deterministic — no mandatory LLM dependency.  Cloud instance catalog is
queried from mc_cloud_instances (pre-seeded at DB init; refreshed from
public pricing APIs when internet is available).
"""

import csv
import io
import json
import socket
import uuid
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

logger = get_logger("icdev.server_migration")


def _mc_conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_id() -> str:
    return "smig-" + uuid.uuid4().hex[:12]


# ── Inventory Parsing ─────────────────────────────────────────────────────────

def parse_server_inventory(raw_input: str, fmt: str) -> dict:
    """Parse server inventory from multiple input formats.

    Args:
        raw_input: Raw text input.
        fmt: 'csv', 'json', 'vmware_export', or 'manual'.

    Returns:
        {"ok": bool, "inventory": {...}, "services": [...],
         "nics": [...], "disks": [...], "errors": [...]}
    """
    errors = []
    inventory = {
        "vcpus": 0, "ram_gb": 0, "disk_count": 0, "total_disk_gb": 0,
        "disk_type": "", "nic_count": 0, "primary_nic_gbps": 0,
        "os_family": "", "os_name": "", "os_arch": "x86_64",
        "bios_type": "UEFI", "virtualization_ext": 0,
        "raw_export_json": "{}",
    }
    services, nics, disks = [], [], []

    try:
        if fmt == "csv":
            inventory, nics, disks, services = _parse_csv(raw_input, errors)
        elif fmt == "json":
            inventory, nics, disks, services = _parse_json_input(raw_input, errors)
        elif fmt == "vmware_export":
            inventory, nics, disks, services = _parse_vmware_ovf(raw_input, errors)
        else:  # manual
            inventory, nics, disks, services = _parse_json_input(raw_input, errors)
    except Exception as exc:
        errors.append(f"Parse error: {exc}")

    return {
        "ok": len(errors) == 0,
        "inventory": inventory,
        "services": services,
        "nics": nics,
        "disks": disks,
        "errors": errors,
    }


def _parse_csv(raw: str, errors: list) -> tuple:
    inventory = {"vcpus": 0, "ram_gb": 0, "disk_count": 0, "total_disk_gb": 0,
                 "disk_type": "", "nic_count": 0, "primary_nic_gbps": 0,
                 "os_family": "", "os_name": "", "os_arch": "x86_64",
                 "bios_type": "UEFI", "virtualization_ext": 0, "raw_export_json": "{}"}
    nics, disks, services = [], [], []
    reader = csv.DictReader(io.StringIO(raw.strip()))
    rows = list(reader)
    if not rows:
        errors.append("CSV has no data rows")
        return inventory, nics, disks, services
    r = rows[0]
    inventory["vcpus"] = int(r.get("vcpus", r.get("cpus", 0)) or 0)
    inventory["ram_gb"] = float(r.get("ram_gb", r.get("memory_gb", 0)) or 0)
    inventory["total_disk_gb"] = float(r.get("disk_gb", r.get("total_disk_gb", 0)) or 0)
    inventory["disk_count"] = int(r.get("disk_count", 1) or 1)
    inventory["disk_type"] = r.get("disk_type", "SSD")
    inventory["nic_count"] = int(r.get("nic_count", 1) or 1)
    inventory["primary_nic_gbps"] = float(r.get("nic_speed_gbps", 1) or 1)
    inventory["os_name"] = r.get("os", r.get("os_name", ""))
    inventory["os_family"] = _infer_os_family(inventory["os_name"])
    inventory["os_arch"] = r.get("os_arch", "x86_64")
    return inventory, nics, disks, services


def _parse_json_input(raw: str, errors: list) -> tuple:
    inventory = {"vcpus": 0, "ram_gb": 0, "disk_count": 0, "total_disk_gb": 0,
                 "disk_type": "", "nic_count": 0, "primary_nic_gbps": 0,
                 "os_family": "", "os_name": "", "os_arch": "x86_64",
                 "bios_type": "UEFI", "virtualization_ext": 0, "raw_export_json": "{}"}
    nics, disks, services = [], [], []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid JSON: {exc}")
        return inventory, nics, disks, services

    for key in ("vcpus", "ram_gb", "disk_count", "total_disk_gb", "disk_type",
                "nic_count", "primary_nic_gbps", "os_family", "os_name",
                "os_arch", "bios_type", "virtualization_ext"):
        if key in data:
            inventory[key] = data[key]
    if not inventory["os_family"] and inventory["os_name"]:
        inventory["os_family"] = _infer_os_family(inventory["os_name"])
    inventory["raw_export_json"] = json.dumps(data)

    nics = data.get("nics", data.get("network_interfaces", []))
    disks = data.get("disks", data.get("storage", []))
    services = data.get("services", [])
    return inventory, nics, disks, services


def _parse_vmware_ovf(raw: str, errors: list) -> tuple:
    """Parse VMware OVF/XML export into canonical dict."""
    inventory = {"vcpus": 0, "ram_gb": 0, "disk_count": 0, "total_disk_gb": 0,
                 "disk_type": "SSD", "nic_count": 0, "primary_nic_gbps": 1,
                 "os_family": "", "os_name": "", "os_arch": "x86_64",
                 "bios_type": "UEFI", "virtualization_ext": 1, "raw_export_json": "{}"}
    nics, disks, services = [], [], []
    try:
        root = ET.fromstring(raw)  # nosec B314 — OVF XML from controlled VMware export, not user-supplied input
        # OVF namespace URIs used inline via Clark notation in findtext calls below
        # OS
        os_el = root.find(".//{http://schemas.dmtf.org/ovf/envelope/1}OperatingSystemSection")
        if os_el is not None:
            os_desc = os_el.findtext("{http://schemas.dmtf.org/ovf/envelope/1}Description", "")
            inventory["os_name"] = os_desc
            inventory["os_family"] = _infer_os_family(os_desc)
        # vCPU / RAM
        for item in root.iter("{http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData}Item"):
            rtype = item.findtext("{http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData}ResourceType", "")
            qty = item.findtext("{http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData}VirtualQuantity", "0")
            if rtype == "3":  # CPU
                inventory["vcpus"] = int(qty)
            elif rtype == "4":  # Memory (MB)
                inventory["ram_gb"] = round(int(qty) / 1024, 2)
            elif rtype == "17":  # Disk
                inventory["disk_count"] += 1
                inventory["total_disk_gb"] += round(int(qty) / (1024 ** 3), 1)
            elif rtype == "10":  # NIC
                inventory["nic_count"] += 1
        inventory["raw_export_json"] = json.dumps({"source": "vmware_ovf"})
    except Exception as exc:
        errors.append(f"OVF parse error: {exc}")
    return inventory, nics, disks, services


def _infer_os_family(os_name: str) -> str:
    n = os_name.lower()
    if any(w in n for w in ("windows", "win", "server 20", "server 2003", "server 2008")):
        return "windows"
    if any(w in n for w in ("linux", "rhel", "centos", "ubuntu", "debian", "suse", "fedora", "rocky", "alma")):
        return "linux"
    if any(w in n for w in ("aix", "solaris", "hpux", "hp-ux", "unix")):
        return "unix"
    return "other"


# ── Instance Catalog ──────────────────────────────────────────────────────────

def get_cloud_instances(provider: str = "", filters: dict | None = None) -> list[dict]:
    """Query mc_cloud_instances with optional filters.

    Args:
        provider: Provider slug or '' for all.
        filters: Optional dict: min_vcpus, max_vcpus, min_ram_gb, max_ram_gb,
                 govcloud_only (bool), il_required ('4'/'5'), family (str),
                 cost_tier (str), use_case (str), eol_status (str).

    Returns:
        List of row dicts sorted by vcpus asc, ram_gb asc.
    """
    filters = filters or {}
    clauses, params = [], []
    if provider:
        clauses.append("provider = %s")
        params.append(provider)
    if filters.get("min_vcpus"):
        clauses.append("vcpus >= %s")
        params.append(int(filters["min_vcpus"]))
    if filters.get("max_vcpus"):
        clauses.append("vcpus <= %s")
        params.append(int(filters["max_vcpus"]))
    if filters.get("min_ram_gb"):
        clauses.append("ram_gb >= %s")
        params.append(float(filters["min_ram_gb"]))
    if filters.get("max_ram_gb"):
        clauses.append("ram_gb <= %s")
        params.append(float(filters["max_ram_gb"]))
    if filters.get("govcloud_only"):
        clauses.append("govcloud = 1")
    if filters.get("family"):
        clauses.append("family = %s")
        params.append(filters["family"])
    if filters.get("cost_tier"):
        clauses.append("cost_tier = %s")
        params.append(filters["cost_tier"])
    eol = filters.get("eol_status", "active")
    clauses.append("eol_status = %s")
    params.append(eol)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM mc_cloud_instances {where} ORDER BY vcpus ASC, ram_gb ASC"

    conn = _mc_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        results = [dict(r) for r in rows]
        # Post-filter by IL if requested
        il_req = str(filters.get("il_required", ""))
        if il_req:
            results = [r for r in results if il_req in json.loads(r.get("il_support", "[]"))]
        # Post-filter by use_case tag
        uc = filters.get("use_case", "")
        if uc:
            results = [r for r in results if uc in json.loads(r.get("use_case_tags", "[]"))]
        return results
    finally:
        conn.close()


# ── Rightsizing ───────────────────────────────────────────────────────────────

def recommend_target(
    src_specs: dict,
    migration_type: str,
    tgt_platform: str,
    perf_data: dict | None = None,
    headroom: float = 1.2,
) -> list[dict]:
    """Return top-3 recommended target instances ranked by fit score.

    Args:
        src_specs: Dict from mc_srv_inventory (vcpus, ram_gb, total_disk_gb,
                   primary_nic_gbps).
        migration_type: Server migration type string.
        tgt_platform: Provider slug.
        perf_data: Optional dict from mc_srv_performance (cpu_avg_pct,
                   ram_avg_pct). If None, assumes 100% utilization.
        headroom: Headroom multiplier (default 1.2 = 20% overhead).

    Returns:
        List of up to 3 recommendation dicts.
    """
    src_vcpus = src_specs.get("vcpus", 1) or 1
    src_ram = src_specs.get("ram_gb", 1) or 1

    if perf_data:
        cpu_util = perf_data.get("cpu_avg_pct", 100) / 100.0
        ram_util = perf_data.get("ram_avg_pct", 100) / 100.0
    else:
        cpu_util = ram_util = 1.0

    vcpu_req = max(1, src_vcpus * cpu_util * headroom)
    ram_req = max(0.5, src_ram * ram_util * headroom)

    candidates = get_cloud_instances(tgt_platform, {
        "min_vcpus": int(vcpu_req),
        "min_ram_gb": ram_req,
    })

    if not candidates:
        # Relax filter — return smallest available
        candidates = get_cloud_instances(tgt_platform)

    scored = []
    for inst in candidates:
        vcpu_ratio = inst["vcpus"] / max(vcpu_req, 1)
        ram_ratio = inst["ram_gb"] / max(ram_req, 0.5)
        # Penalise over-provisioning >4x
        vcpu_penalty = max(0, (vcpu_ratio - 4) * 0.1)
        ram_penalty = max(0, (ram_ratio - 4) * 0.1)
        score = 1.0 / (1 + abs(vcpu_ratio - 1) + abs(ram_ratio - 1) + vcpu_penalty + ram_penalty)
        scored.append((score, inst))

    scored.sort(key=lambda x: -x[0])
    results = []
    for rank, (score, inst) in enumerate(scored[:3], 1):
        results.append({
            "rank": rank,
            "instance": inst,
            # Promote common fields to top level for template convenience
            "instance_type": inst.get("instance_type", ""),
            "provider": inst.get("provider", ""),
            "vcpus": inst.get("vcpus", 0),
            "ram_gb": inst.get("ram_gb", 0),
            "fit_score": round(score, 4),
            "rationale": (
                f"Requires ~{vcpu_req:.1f} vCPU and ~{ram_req:.1f} GB RAM "
                f"(source × headroom {headroom}). "
                f"{inst['instance_type']} provides {inst['vcpus']} vCPU / {inst['ram_gb']} GB RAM."
            ),
            "cost_tier": inst.get("cost_tier", "medium"),
            "vcpu_req": round(vcpu_req, 2),
            "ram_req_gb": round(ram_req, 2),
        })
    return results


def compute_rightsizing(session_id: str) -> dict:
    """Analyze utilization and write top-3 recommendations to mc_srv_rightsizing."""
    conn = _mc_conn()
    try:
        sess = conn.execute("SELECT * FROM mc_srv_sessions WHERE id=%s", (session_id,)).fetchone()
        if not sess:
            return {"error": "session not found"}
        sess = dict(sess)

        inv = conn.execute("SELECT * FROM mc_srv_inventory WHERE session_id=%s", (session_id,)).fetchone()
        src_specs = dict(inv) if inv else {}

        perf = conn.execute("SELECT * FROM mc_srv_performance WHERE session_id=%s ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        perf_data = dict(perf) if perf else None
        perf_source = (perf_data or {}).get("source", "none") if perf_data else "none"

        recs = recommend_target(
            src_specs, sess["migration_type"], sess["tgt_platform"], perf_data
        )

        # Persist recommendations
        conn.execute("DELETE FROM mc_srv_rightsizing WHERE session_id=%s", (session_id,))
        for rec in recs:
            conn.execute(
                "INSERT INTO mc_srv_rightsizing "
                "(session_id, recommended_instance_id, rank, cost_tier, rationale, "
                "vcpu_req, ram_req_gb, disk_req_gb, headroom_factor) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (session_id, rec["instance"]["id"], rec["rank"], rec["cost_tier"],
                 rec["rationale"], rec["vcpu_req"], rec["ram_req_gb"],
                 src_specs.get("total_disk_gb", 0), 1.2),
            )
        conn.commit()
        return {"recommendations": recs, "perf_source": perf_source, "headroom_factor": 1.2}
    finally:
        conn.close()


# ── Compatibility Checks ──────────────────────────────────────────────────────

def run_compatibility_checks(session_id: str) -> list[dict]:
    """Generate CAT1/CAT2/CAT3 compatibility findings and persist to DB."""
    conn = _mc_conn()
    try:
        sess = conn.execute("SELECT * FROM mc_srv_sessions WHERE id=%s", (session_id,)).fetchone()
        if not sess:
            return []
        sess = dict(sess)

        inv_row = conn.execute("SELECT * FROM mc_srv_inventory WHERE session_id=%s", (session_id,)).fetchone()
        inv = dict(inv_row) if inv_row else {}

        perf_row = conn.execute("SELECT * FROM mc_srv_performance WHERE session_id=%s ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        perf = dict(perf_row) if perf_row else {}

        tgt_row = None
        if sess.get("tgt_instance_id"):
            tgt_row = conn.execute("SELECT * FROM mc_cloud_instances WHERE id=%s", (sess["tgt_instance_id"],)).fetchone()
        tgt = dict(tgt_row) if tgt_row else {}

        findings = []

        # ── Compute checks ────────────────────────────────────────────────────
        if inv and tgt:
            src_vcpu = inv.get("vcpus", 0) or 0
            tgt_vcpu = tgt.get("vcpus", 0) or 0
            src_ram = inv.get("ram_gb", 0) or 0
            tgt_ram = tgt.get("ram_gb", 0) or 0
            cpu_util = perf.get("cpu_avg_pct", 100) / 100.0 if perf else 1.0
            ram_util = perf.get("ram_avg_pct", 100) / 100.0 if perf else 1.0
            vcpu_req = max(1, src_vcpu * cpu_util * 1.2)
            ram_req = max(0.5, src_ram * ram_util * 1.2)

            if tgt_vcpu < vcpu_req:
                findings.append(("compute", "Insufficient vCPU", f">={vcpu_req:.1f}", str(tgt_vcpu), "cat1"))
            elif tgt_vcpu < vcpu_req * 1.1:
                findings.append(("compute", "vCPU headroom < 10%", f">={vcpu_req * 1.1:.1f}", str(tgt_vcpu), "cat2"))

            if tgt_ram < ram_req:
                findings.append(("compute", "Insufficient RAM", f">={ram_req:.1f} GB", f"{tgt_ram} GB", "cat1"))
            elif tgt_ram < ram_req * 1.1:
                findings.append(("compute", "RAM headroom < 10%", f">={ram_req * 1.1:.1f} GB", f"{tgt_ram} GB", "cat2"))

        # ── OS checks ─────────────────────────────────────────────────────────
        os_name = inv.get("os_name", "").lower() if inv else ""
        os_arch = inv.get("os_arch", "x86_64") if inv else "x86_64"
        bios = inv.get("bios_type", "") if inv else ""

        if any(eol in os_name for eol in ("2003", "2008", "xp", "vista", "windows 7", "rhel 5", "rhel 6", "centos 6")):
            findings.append(("os", "End-of-Life OS detected", "Supported OS", os_name, "cat1"))
        if "32" in os_arch and tgt.get("vcpus", 0) > 4:
            findings.append(("os", "32-bit OS on large target", "64-bit OS for >4 vCPU", os_arch, "cat1"))
        if bios == "Legacy BIOS" and sess.get("tgt_platform") in ("aws", "azure", "gcp"):
            findings.append(("os", "Legacy BIOS on UEFI-required cloud", "UEFI", "Legacy BIOS", "cat2"))

        # ── Security checks ───────────────────────────────────────────────────
        if inv:
            vt = inv.get("virtualization_ext", 0)
            if not vt and "p2v" in sess.get("migration_type", ""):
                findings.append(("security", "No VT-x/AMD-V for P2V", "VT-x or AMD-V enabled", "Not detected", "cat1"))

        # ── Storage checks ────────────────────────────────────────────────────
        if inv and tgt:
            src_disk = inv.get("total_disk_gb", 0) or 0
            tgt_local = tgt.get("local_storage_gb", 0) or 0
            if tgt.get("storage_type") == "EBS-only" and src_disk > 0:
                findings.append(("storage", "Target uses block storage only", "EBS volume provisioned separately", "EBS-only instance", "cat2"))
            elif tgt_local > 0 and tgt_local < src_disk:
                findings.append(("storage", "Target local storage < source disk", f">={src_disk} GB", f"{tgt_local} GB", "cat1"))

        # ── Network checks ────────────────────────────────────────────────────
        if inv and tgt:
            src_nics = inv.get("nic_count", 0) or 0
            if src_nics > 8 and sess.get("tgt_platform") in ("aws", "azure", "gcp", "oci", "ibm"):
                findings.append(("network", "NIC count exceeds cloud limit", "<=8 NICs", f"{src_nics} NICs", "cat2"))

        # ── Licensing checks ──────────────────────────────────────────────────
        os_fam = inv.get("os_family", "") if inv else ""
        if os_fam == "windows" and tgt.get("govcloud") == 1:
            findings.append(("licensing", "Windows requires BYOL on GovCloud", "BYOL license available", "Not confirmed", "cat2"))

        # Persist — delete old auto-detected rows first
        conn.execute("DELETE FROM mc_srv_compat_checks WHERE session_id=%s AND auto_detected=1", (session_id,))
        result = []
        for category, check_name, expected, actual, severity in findings:
            status = "fail" if severity in ("cat1", "cat2") else "warning"
            conn.execute(
                "INSERT INTO mc_srv_compat_checks "
                "(session_id, category, check_name, expected, actual, severity, status, auto_detected) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,1)",
                (session_id, category, check_name, expected, actual, severity, status),
            )
            result.append({"category": category, "check_name": check_name,
                           "expected": expected, "actual": actual,
                           "severity": severity, "status": status})
        conn.commit()
        return result
    finally:
        conn.close()


# ── Cutover Step Templates ────────────────────────────────────────────────────

_CUTOVER_TEMPLATES = {
    "p2p": [
        ("pre_migration", 1, "Verify target hardware is racked, cabled, and powered",
         "Confirm BIOS/UEFI settings match source — boot order, VT-x, SR-IOV", "Re-verify source hardware is accessible", 30),
        ("pre_migration", 2, "Take final full block-level backup of source server",
         "Confirm backup checksum and restore test", "Restore from previous backup", 120),
        ("pre_migration", 3, "Install OS on target; apply same patch baseline",
         "Verify OS version and patch level match source", "Re-image from source baseline", 60),
        ("pre_migration", 4, "Validate network connectivity on all target NICs",
         "Ping gateway; confirm VLAN tags active", "Reseat cables; reconfigure switch port", 20),
        ("parallel_run", 5, "Restore backup data to target; validate integrity checksums",
         "Compare sha256 of key directories/files", "Re-copy from source or backup", 90),
        ("parallel_run", 6, "Start all services on target in shadow/read-only mode",
         "Confirm services running; compare output vs source", "Stop services; investigate startup errors", 30),
        ("cutover", 7, "Enter maintenance window; notify all stakeholders",
         "Confirm stakeholder acknowledgement", "Defer window; re-notify", 15),
        ("cutover", 8, "Stop all services on source server",
         "Verify all service PIDs are gone", "Re-stop any remaining services", 10),
        ("cutover", 9, "Swap IP addresses and DNS A records to target",
         "Verify DNS propagation via nslookup from external vantage", "Revert DNS to source", 15),
        ("cutover", 10, "Start all services on target in production mode",
         "Run smoke test suite; verify application endpoints", "Stop target services; revert DNS; restart source", 20),
        ("post_migration", 11, "Verify monitoring agents report from target",
         "Confirm dashboards show target hostname", "Reinstall agent on target", 15),
        ("post_migration", 12, "Confirm backup agent is running on target",
         "Trigger manual backup; verify completion", "Reinstall backup agent", 30),
        ("post_migration", 13, "Decommission source server after 30-day hold",
         "Remove from DNS, CMDB, IPAM", "Extend hold period if issues found", 10),
        ("rollback", 14, "Re-stop services on target",
         "Verify target services stopped", "Force kill remaining processes", 10),
        ("rollback", 15, "Re-start services on source server",
         "Verify source services running and healthy", "Investigate source startup errors", 15),
        ("rollback", 16, "Re-swap DNS/IP back to source",
         "Verify DNS resolves to source IP", "Manual DNS record update", 10),
    ],
    "p2v_onprem": [
        ("pre_migration", 1, "Verify hypervisor host has sufficient capacity (vCPU, RAM, datastore)",
         "Confirm resource pool shows available headroom", "Free capacity or provision additional host", 20),
        ("pre_migration", 2, "Install P2V conversion tool on source or conversion host",
         "Confirm tool version and connectivity to hypervisor", "Re-install or update conversion tool", 30),
        ("pre_migration", 3, "Take quiesced snapshot/backup of source physical server",
         "Confirm backup completed without errors", "Re-run backup; investigate VSS/snapshot errors", 60),
        ("pre_migration", 4, "Configure target VM hardware profile (vCPUs, RAM, virtual NIC, SCSI)",
         "VM configuration matches rightsizing recommendation", "Adjust VM hardware settings", 15),
        ("parallel_run", 5, "Run P2V conversion to create VM (cold or warm clone)",
         "Verify VM boots successfully in isolated test network", "Re-run conversion; check disk geometry", 120),
        ("parallel_run", 6, "Boot VM in isolated test VLAN; validate OS and all services",
         "All services start; application responds to test requests", "Fix driver issues; re-inject drivers", 45),
        ("parallel_run", 7, "Verify hypervisor tools installed (VMware Tools / Hyper-V ICS)",
         "Tools show 'running' in hypervisor console", "Reinstall hypervisor tools inside VM", 20),
        ("cutover", 8, "Enter maintenance window; shut down physical server",
         "Physical server powered off; IPMI shows Off state", "Re-schedule window", 10),
        ("cutover", 9, "Start VM in production VLAN; re-assign IP address and DNS",
         "Verify DNS resolves to VM; ping from multiple subnets", "Revert network config; restart source physical", 15),
        ("cutover", 10, "Verify all services running via monitoring dashboard",
         "All service health checks green in monitoring", "Stop VM; start physical; investigate service failures", 20),
        ("post_migration", 11, "Remove source server from DNS, CMDB, and IPAM",
         "No references to old IP remain in DNS or CMDB", "Manual CMDB cleanup", 20),
        ("post_migration", 12, "Take first post-migration VM snapshot as clean baseline",
         "Snapshot visible in hypervisor console", "Retry snapshot; check datastore space", 10),
        ("rollback", 13, "Power off VM; bring physical server back online; re-swap DNS",
         "Physical server services running; DNS resolves to physical IP", "Investigate startup failures", 20),
    ],
    "p2v_cloud": [
        ("pre_migration", 1, "Provision target cloud VPC, subnet, security groups / NSG, IAM roles",
         "VPC/subnet reachable; SG rules reviewed; IAM role has required permissions", "Review cloud console errors; fix policy", 60),
        ("pre_migration", 2, "Install cloud migration agent on source server (AWS MGN / Azure Migrate)",
         "Agent shows 'connected' in migration dashboard", "Re-install agent; check outbound port 443", 30),
        ("pre_migration", 3, "Initiate initial full disk replication (may take hours)",
         "Migration dashboard shows 0% lag and 'Healthy' status", "Investigate replication errors; check bandwidth", 240),
        ("parallel_run", 4, "Verify continuous delta replication is keeping pace",
         "Replication lag < 5 minutes", "Increase bandwidth allocation; check firewall rules", 30),
        ("parallel_run", 5, "Launch test instance from replicated snapshot in isolated cloud VPC",
         "Application responds correctly in cloud test environment", "Fix driver or networking issues in test VM", 60),
        ("parallel_run", 6, "Validate application in cloud test environment; confirm Direct Connect / VPN",
         "End-to-end application test passes; latency acceptable", "Investigate connectivity; adjust routing", 45),
        ("cutover", 7, "Finalize replication; reduce replication lag to < 1 MB",
         "Migration tool shows lag < 1 MB", "Wait for replication to converge", 30),
        ("cutover", 8, "Enter maintenance window; stop all writes to source server",
         "No active sessions; application shows maintenance page", "Re-schedule window", 15),
        ("cutover", 9, "Trigger cutover in migration tool; launch production cloud instance",
         "Instance shows Running state; passes health checks", "Rollback: terminate cloud instance; re-start source", 20),
        ("cutover", 10, "Update DNS records to cloud instance IP / load balancer DNS",
         "DNS resolves to cloud endpoint; application accessible", "Revert DNS to source", 15),
        ("cutover", 11, "Run smoke test suite against cloud endpoint",
         "All smoke tests pass; monitoring shows green", "Revert DNS; re-start source", 20),
        ("post_migration", 12, "Remove migration agent from source server",
         "Agent uninstalled; no outbound replication traffic", "Manual agent removal", 15),
        ("post_migration", 13, "Enable cloud-native backup (EBS snapshots / Azure Backup / GCS snapshots)",
         "First backup completes successfully", "Review backup policy; adjust retention", 20),
        ("post_migration", 14, "Enable cloud-native monitoring (CloudWatch / Azure Monitor / Cloud Ops)",
         "Dashboards show metrics from cloud instance", "Reinstall monitoring agent inside VM", 20),
        ("post_migration", 15, "Decommission source server after 30-day hold",
         "Source removed from DNS, CMDB, network", "Extend hold; investigate any remaining dependencies", 10),
        ("rollback", 16, "Stop cloud instance; re-start source server; re-point DNS to source",
         "Source services running; DNS resolves to source", "Investigate rollback failures; escalate", 30),
        ("rollback", 17, "Notify stakeholders of rollback; document root cause",
         "All stakeholders notified", "N/A", 15),
    ],
    "v2v_cloud": [
        ("pre_migration", 1, "Export source VM as OVA/OVF or enable cloud replication tool",
         "OVA file generated and checksummed; or replication agent connected", "Re-export or re-install agent", 120),
        ("pre_migration", 2, "Provision target cloud VPC, subnets, security groups, IAM",
         "Cloud networking ready; IAM role permissions validated", "Fix cloud console errors", 60),
        ("pre_migration", 3, "Upload OVA to cloud object storage or start continuous replication",
         "Upload complete (progress visible); or replication lag < 30 min", "Resume upload; check bandwidth", 180),
        ("parallel_run", 4, "Import OVA as cloud VM image (EC2 AMI / Azure Managed Image / GCE image)",
         "Image import status shows 'Available'", "Check import task logs for errors", 60),
        ("parallel_run", 5, "Launch test instance from cloud image in isolated subnet",
         "Instance boots; application responds in test environment", "Fix driver or config issues", 45),
        ("parallel_run", 6, "Validate application functionality vs source VM output",
         "Feature parity confirmed; no data discrepancies", "Investigate differences; fix configs", 30),
        ("cutover", 7, "Enter maintenance window; take final snapshot of source VM",
         "Snapshot completes; source VM is quiesced", "Wait for snapshot; retry if fails", 20),
        ("cutover", 8, "Import final snapshot; launch production cloud instance",
         "Production instance running; passes health checks", "Rollback: terminate instance; re-start source VM", 30),
        ("cutover", 9, "Update DNS and load balancer to cloud instance endpoint",
         "DNS resolves to cloud; LB health check green", "Revert DNS; re-start source VM", 15),
        ("cutover", 10, "Run smoke tests; confirm monitoring shows cloud instance",
         "All tests pass; metrics flowing in cloud monitoring", "Revert DNS; rollback instance", 20),
        ("post_migration", 11, "Power off source VM; begin 30-day decommission hold",
         "Source VM off; noted in CMDB with decom date", "Extend hold period", 10),
        ("post_migration", 12, "Enable cloud-native HA / auto-scaling if applicable",
         "Auto-scaling group active; cross-AZ placement confirmed", "Review scaling policy", 30),
        ("rollback", 13, "Power on source VM; re-point DNS; terminate cloud instance",
         "Source VM services running; DNS resolves to source", "Investigate rollback failures", 20),
    ],
    "v2v_hypervisor": [
        ("pre_migration", 1, "Verify target hypervisor host has sufficient capacity",
         "vCPU, RAM, datastore headroom confirmed", "Free resources or add host", 20),
        ("pre_migration", 2, "Install cross-hypervisor conversion tool (StarWind V2V, qemu-img)",
         "Tool installed and accessible on conversion host", "Re-install or update tool", 20),
        ("pre_migration", 3, "Stop source VM; export disk image (VMDK→QCOW2 or VHDX→VMDK)",
         "Disk image file created with correct checksum", "Re-export; check source VM state", 60),
        ("pre_migration", 4, "Convert disk format using qemu-img or vendor tool",
         "Converted disk file created; no corruption errors", "Re-run conversion with different block size", 45),
        ("parallel_run", 5, "Create target VM with matching vCPU/RAM; attach converted disk",
         "VM configuration matches source; disk visible in VM", "Re-create VM; verify disk controller type", 20),
        ("parallel_run", 6, "Boot target VM in isolated test VLAN; verify OS boot",
         "OS boots fully; no disk or driver errors in boot log", "Inject correct drivers; fix bootloader config", 30),
        ("parallel_run", 7, "Install target hypervisor tools (qemu-guest-agent / open-vm-tools)",
         "Guest agent shows 'running' in hypervisor console", "Manual install inside VM", 20),
        ("parallel_run", 8, "Update NIC drivers; verify network connectivity in test VLAN",
         "Network ping to gateway successful from VM", "Re-install NIC driver; reconfigure network", 20),
        ("cutover", 9, "Enter maintenance window; shut down source VM",
         "Source VM powered off in source hypervisor", "Re-schedule window", 10),
        ("cutover", 10, "Final disk sync or snapshot-and-convert",
         "Final converted image matches source snapshot checksum", "Re-run conversion", 30),
        ("cutover", 11, "Start target VM in production VLAN; re-assign IP/DNS",
         "Ping to VM IP from gateway; DNS resolves correctly", "Revert network; restart source VM", 15),
        ("cutover", 12, "Run smoke test suite on target VM",
         "All services healthy; application endpoints respond", "Revert DNS; re-start source VM", 20),
        ("post_migration", 13, "Remove source VM from source hypervisor inventory after 30-day hold",
         "VM removed from vCenter/Hyper-V Manager; CMDB updated", "Extend hold period", 10),
        ("rollback", 14, "Power off target VM; power on source VM in source hypervisor; re-point DNS",
         "Source VM services running; DNS resolves to original IP", "Investigate rollback failures", 20),
    ],
}


def generate_default_cutover_steps(session_id: str, migration_type: str) -> list[dict]:
    """Seed mc_srv_cutover_steps with template steps for the migration type."""
    template_key = migration_type
    if template_key not in _CUTOVER_TEMPLATES:
        # fallback
        template_key = "p2v_cloud" if "cloud" in migration_type else "p2v_onprem"

    conn = _mc_conn()
    try:
        conn.execute("DELETE FROM mc_srv_cutover_steps WHERE session_id=%s", (session_id,))
        rows = []
        for phase, seq, desc, verify, rollback, dur in _CUTOVER_TEMPLATES[template_key]:
            conn.execute(
                "INSERT INTO mc_srv_cutover_steps "
                "(session_id, phase, seq_no, description, verify_action, rollback_action, duration_min, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (session_id, phase, seq, desc, verify, rollback, dur, "pending"),
            )
            rows.append({"phase": phase, "seq_no": seq, "description": desc,
                         "verify_action": verify, "rollback_action": rollback, "duration_min": dur})
        conn.commit()
        return rows
    finally:
        conn.close()


# ── Test Cases ────────────────────────────────────────────────────────────────

_TEST_CASE_TEMPLATES = [
    ("pre", 1, "Verify source server backup exists and is restorable",
     "Trigger restore test from latest backup; verify data integrity", "Backup restore completes without errors"),
    ("pre", 2, "Confirm all source services are healthy before migration",
     "Run health check against all service endpoints", "All services return HTTP 200 / healthy status"),
    ("pre", 3, "Capture source server performance baseline",
     "Record CPU, RAM, disk IOPS, and network metrics over 15 min", "Metrics captured and saved for post-migration comparison"),
    ("pre", 4, "Validate network connectivity from source to target environment",
     "Ping and traceroute from source to target subnet / cloud VPC", "Round-trip latency < 100ms; no packet loss"),
    ("post", 5, "Verify target server is reachable via DNS",
     "nslookup/dig hostname from multiple network segments", "DNS resolves to target IP correctly"),
    ("post", 6, "Confirm all services start and reach healthy state on target",
     "Run health check against all service endpoints on target", "All services return expected healthy responses"),
    ("post", 7, "Validate data integrity — compare checksum of critical data",
     "sha256sum key data directories/files on source vs target", "Checksums match; no data corruption"),
    ("post", 8, "Performance regression test — compare against pre-migration baseline",
     "Re-run same metrics collection for 15 min; compare to pre-migration baseline", "CPU/RAM/IOPS within 20% of source baseline"),
    ("post", 9, "Confirm monitoring and alerting active on target",
     "Trigger test alert; verify it appears in monitoring dashboard", "Alert fires and resolves correctly"),
    ("post", 10, "Confirm backup agent running and first backup completes",
     "Trigger manual backup; verify completion in backup dashboard", "Backup completes with no errors"),
]


def generate_default_test_cases(session_id: str) -> list[dict]:
    """Seed mc_srv_test_cases with pre/post test templates."""
    conn = _mc_conn()
    try:
        conn.execute("DELETE FROM mc_srv_test_cases WHERE session_id=%s", (session_id,))
        rows = []
        for phase, seq, name, procedure, expected in _TEST_CASE_TEMPLATES:
            conn.execute(
                "INSERT INTO mc_srv_test_cases "
                "(session_id, phase, seq_no, test_name, procedure, expected_result) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (session_id, phase, seq, name, procedure, expected),
            )
            rows.append({"phase": phase, "seq_no": seq, "test_name": name,
                         "procedure": procedure, "expected_result": expected})
        conn.commit()
        return rows
    finally:
        conn.close()


# ── ERB Package ───────────────────────────────────────────────────────────────

def build_erb_package(session_id: str) -> dict:
    """Assemble a complete ERB document JSON from all session sub-tables."""
    conn = _mc_conn()
    try:
        sess = conn.execute("SELECT * FROM mc_srv_sessions WHERE id=%s", (session_id,)).fetchone()
        if not sess:
            return {"error": "session not found"}
        sess = dict(sess)

        inv = conn.execute("SELECT * FROM mc_srv_inventory WHERE session_id=%s LIMIT 1", (session_id,)).fetchone()
        tgt_inst = None
        if sess.get("tgt_instance_id"):
            tgt_inst = conn.execute("SELECT * FROM mc_cloud_instances WHERE id=%s", (sess["tgt_instance_id"],)).fetchone()

        compat_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_srv_compat_checks WHERE session_id=%s", (session_id,)).fetchall()]
        cat1 = sum(1 for r in compat_rows if r["severity"] == "cat1" and r["status"] not in ("pass", "overridden"))
        cat2 = sum(1 for r in compat_rows if r["severity"] == "cat2")
        cat3 = sum(1 for r in compat_rows if r["severity"] == "cat3")

        rightsizing = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_srv_rightsizing WHERE session_id=%s ORDER BY rank LIMIT 1", (session_id,)).fetchall()]

        cutover = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_srv_cutover_steps WHERE session_id=%s ORDER BY seq_no", (session_id,)).fetchall()]
        pre_tests = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_srv_test_cases WHERE session_id=%s AND phase='pre' ORDER BY seq_no", (session_id,)).fetchall()]
        post_tests = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_srv_test_cases WHERE session_id=%s AND phase='post' ORDER BY seq_no", (session_id,)).fetchall()]

        erb = conn.execute("SELECT * FROM mc_srv_erb_metadata WHERE session_id=%s LIMIT 1", (session_id,)).fetchone()

        score_result = compute_readiness_score(session_id)

        return {
            "session": sess,
            "inventory": dict(inv) if inv else {},
            "target_instance": dict(tgt_inst) if tgt_inst else {},
            "compat_summary": {"cat1": cat1, "cat2": cat2, "cat3": cat3, "blockers": cat1},
            "compat_checks": compat_rows,
            "rightsizing": rightsizing,
            "cutover_steps": cutover,
            "test_cases": {"pre": pre_tests, "post": post_tests},
            "erb": dict(erb) if erb else {},
            "readiness_score": score_result.get("overall", 0),
            "classification": "CUI // SP-CTI",
            "generated_at": _now(),
        }
    finally:
        conn.close()


# ── Readiness Score ───────────────────────────────────────────────────────────

def compute_readiness_score(session_id: str) -> dict:
    """Compute weighted 0–100 readiness score and update mc_srv_sessions."""
    conn = _mc_conn()
    try:
        inv = conn.execute("SELECT id FROM mc_srv_inventory WHERE session_id=%s LIMIT 1", (session_id,)).fetchone()
        perf = conn.execute("SELECT id FROM mc_srv_performance WHERE session_id=%s LIMIT 1", (session_id,)).fetchone()
        svcs = conn.execute("SELECT COUNT(*) FROM mc_srv_services WHERE session_id=%s", (session_id,)).fetchone()[0]
        compat_total = conn.execute("SELECT COUNT(*) FROM mc_srv_compat_checks WHERE session_id=%s", (session_id,)).fetchone()[0]
        cat1_open = conn.execute(
            "SELECT COUNT(*) FROM mc_srv_compat_checks WHERE session_id=%s AND severity='cat1' AND status NOT IN ('pass','overridden')",
            (session_id,)).fetchone()[0]
        rightsize = conn.execute("SELECT COUNT(*) FROM mc_srv_rightsizing WHERE session_id=%s", (session_id,)).fetchone()[0]
        nics = conn.execute("SELECT COUNT(*) FROM mc_srv_nic_map WHERE session_id=%s", (session_id,)).fetchone()[0]
        storage = conn.execute("SELECT COUNT(*) FROM mc_srv_storage_map WHERE session_id=%s", (session_id,)).fetchone()[0]
        cutover = conn.execute("SELECT COUNT(*) FROM mc_srv_cutover_steps WHERE session_id=%s", (session_id,)).fetchone()[0]
        tests = conn.execute("SELECT COUNT(*) FROM mc_srv_test_cases WHERE session_id=%s", (session_id,)).fetchone()[0]
        erb = conn.execute("SELECT id FROM mc_srv_erb_metadata WHERE session_id=%s LIMIT 1", (session_id,)).fetchone()

        breakdown = {
            "inventory": 15 if inv else 0,
            "performance": 10 if perf else 0,
            "services": 5 if svcs >= 1 else 0,
            "compat": 15 if compat_total >= 1 else 0,
            "rightsizing": 15 if rightsize >= 1 else 0,
            "nic_map": 10 if nics >= 1 else 0,
            "storage_map": 10 if storage >= 1 else 0,
            "cutover": 10 if cutover >= 1 else 0,
            "tests": 5 if tests >= 1 else 0,
            "erb": 5 if erb else 0,
        }
        penalty = cat1_open * 20
        blockers = []
        if cat1_open:
            blockers.append(f"{cat1_open} unresolved CAT1 compatibility issue(s)")

        score = max(0.0, min(100.0, float(sum(breakdown.values())) - penalty))

        conn.execute("UPDATE mc_srv_sessions SET readiness_score=%s, updated_at=%s WHERE id=%s",
                     (score, _now(), session_id))
        conn.commit()

        return {"overall": round(score, 1), "breakdown": breakdown, "blockers": blockers}
    finally:
        conn.close()


# ── Catalog Sync (online → DB; air-gap fallback to seed data) ─────────────────

def _is_online(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    """Return True if internet is reachable via a quick socket probe."""
    try:
        # Check tools.airgap first if available
        try:
            from tools.airgap import is_airgap
            if is_airgap():
                return False
        except ImportError:
            pass
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except OSError:
        return False


def _fetch_aws_instances() -> list[dict]:
    """Fetch EC2 instance types from AWS public pricing index (no auth required).

    Uses the regional price list JSON — filters to Linux/Shared tenancy only.
    Returns a normalised list suitable for mc_cloud_instances upsert.
    """
    import urllib.request
    import urllib.error

    url = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-MigrationCanvas/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("AWS pricing fetch failed: %s", exc)
        return []

    results = []
    seen: set = set()
    products = data.get("products", {})
    for _sku, prod in products.items():
        attrs = prod.get("attributes", {})
        if attrs.get("operatingSystem") != "Linux":
            continue
        if attrs.get("tenancy") not in ("Shared", "Host"):
            continue
        itype = attrs.get("instanceType", "")
        if not itype or itype in seen:
            continue
        seen.add(itype)
        try:
            vcpus = int(attrs.get("vcpu", "0").replace(",", ""))
            ram_str = attrs.get("memory", "0 GiB").replace(" GiB", "").replace(",", "")
            ram_gb = float(ram_str)
            storage_str = attrs.get("storage", "EBS only")
            storage_gb = 0.0
            storage_type = "EBS-only"
            if "NVMe" in storage_str or "SSD" in storage_str:
                storage_type = "NVMe" if "NVMe" in storage_str else "SSD"
            net = attrs.get("networkPerformance", "")
            net_gbps = _parse_aws_network(net)
            family = itype.split(".")[0] if "." in itype else itype
            use_case = _aws_use_case_tags(family)
            results.append({
                "provider": "aws", "instance_type": itype, "family": family,
                "vcpus": vcpus, "ram_gb": ram_gb, "local_storage_gb": storage_gb,
                "storage_type": storage_type, "network_gbps": net_gbps,
                "premium_disk_opt": 1, "cost_tier": _cost_tier_from_vcpu(vcpus),
                "govcloud": 1, "il_support": '["2","4","5"]',
                "use_case_tags": json.dumps(use_case), "eol_status": "active",
                "compliance_certs": '{"fedramp":"high"}',
            })
        except (ValueError, KeyError):
            continue
    return results


def _parse_aws_network(net: str) -> float:
    n = net.lower()
    if "100 gigabit" in n or "100 gigabits" in n:
        return 100.0
    if "50 gigabit" in n or "50 gigabits" in n:
        return 50.0
    if "25 gigabit" in n or "25 gigabits" in n:
        return 25.0
    if "20 gigabit" in n:
        return 20.0
    if "10 gigabit" in n:
        return 10.0
    if "12.5 gigabit" in n:
        return 12.5
    if "5 gigabit" in n:
        return 5.0
    if "up to 25 gigabit" in n:
        return 25.0
    if "up to 12.5 gigabit" in n:
        return 12.5
    if "gigabit" in n:
        return 1.0
    return 0.0


def _aws_use_case_tags(family: str) -> list:
    f = family.lower()
    if f.startswith("t"):
        return ["burstable", "general"]
    if f.startswith("m"):
        return ["general"]
    if f.startswith("c"):
        return ["compute"]
    if f.startswith("r") or f.startswith("x") or f.startswith("u"):
        return ["memory"]
    if f.startswith("i") or f.startswith("d") or f.startswith("h"):
        return ["storage"]
    if f.startswith("p") or f.startswith("g") or f.startswith("inf"):
        return ["gpu"]
    return ["general"]


def _fetch_azure_instances() -> list[dict]:
    """Fetch Azure VM SKUs from public Retail Prices API (no auth required)."""
    import urllib.request
    import urllib.parse

    results = []
    seen: set = set()
    url = (
        "https://prices.azure.com/api/retail/prices"
        "?api-version=2023-01-01-preview"
        "&$filter=serviceName%20eq%20%27Virtual%20Machines%27%20and%20priceType%20eq%20%27Consumption%27"
        "&$top=500"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-MigrationCanvas/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Azure pricing fetch failed: %s", exc)
        return []

    for item in data.get("Items", []):
        sku = item.get("skuName", "")
        # skuName format: "D4s v5" → instance_type: "Standard_D4s_v5"
        name = item.get("armSkuName", "")
        if not name or name in seen:
            continue
        if "Windows" in sku or "Spot" in sku:
            continue
        seen.add(name)
        # Parse vCPU / RAM from sku name heuristic
        vcpus, ram_gb = _azure_parse_sku_heuristic(name)
        if vcpus == 0:
            continue
        family = _azure_family(name)
        results.append({
            "provider": "azure", "instance_type": name, "family": family,
            "vcpus": vcpus, "ram_gb": ram_gb, "local_storage_gb": 0,
            "storage_type": "SSD", "network_gbps": _cost_tier_to_net(vcpus),
            "premium_disk_opt": 1, "cost_tier": _cost_tier_from_vcpu(vcpus),
            "govcloud": 1, "il_support": '["2","4","5"]',
            "use_case_tags": json.dumps(_azure_use_case_tags(name)),
            "eol_status": "active", "compliance_certs": '{"fedramp":"moderate"}',
        })
    return results


def _azure_parse_sku_heuristic(name: str) -> tuple[int, float]:
    """Estimate vCPUs and RAM from Azure VM name (heuristic — not 100% accurate)."""
    import re
    m = re.search(r"_([A-Z])(\d+)", name)
    if not m:
        return 0, 0
    vcpus = int(m.group(2))
    series = m.group(1)
    ram_ratios = {"D": 4, "E": 8, "F": 2, "B": 4, "L": 8, "M": 28, "N": 14, "H": 6}
    ratio = ram_ratios.get(series, 4)
    return vcpus, float(vcpus * ratio)


def _azure_family(name: str) -> str:
    import re
    m = re.match(r"Standard_([A-Z]+\d*[a-z]*)", name)
    return m.group(1) if m else name


def _azure_use_case_tags(name: str) -> list:
    n = name.upper()
    if "_B" in n:
        return ["burstable", "general"]
    if "_D" in n or "_A" in n:
        return ["general"]
    if "_F" in n or "_HC" in n:
        return ["compute"]
    if "_E" in n or "_M" in n:
        return ["memory"]
    if "_L" in n:
        return ["storage"]
    if "_N" in n or "_ND" in n:
        return ["gpu"]
    return ["general"]


def _fetch_gcp_instances() -> list[dict]:
    """Fetch GCP machine types from Cloud Billing SKU API (public, no auth)."""
    import urllib.request

    url = "https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus?pageSize=500"
    results = []
    seen: set = set()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-MigrationCanvas/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("GCP pricing fetch failed: %s", exc)
        return []

    for sku in data.get("skus", []):
        desc = sku.get("description", "")
        if "Instance" not in desc:
            continue
        cat = sku.get("category", {})
        resource = cat.get("resourceFamily", "")
        if resource != "Compute":
            continue
        itype_raw = cat.get("resourceGroup", "")
        itype = itype_raw.lower().replace("custom", "").strip()
        if not itype or itype in seen or len(itype) < 3:
            continue
        seen.add(itype)
        vcpus, ram_gb = _gcp_parse_family(itype)
        if vcpus == 0:
            continue
        results.append({
            "provider": "gcp", "instance_type": itype, "family": itype.split("-")[0],
            "vcpus": vcpus, "ram_gb": ram_gb, "local_storage_gb": 0,
            "storage_type": "SSD", "network_gbps": min(32.0, vcpus * 0.5),
            "premium_disk_opt": 0, "cost_tier": _cost_tier_from_vcpu(vcpus),
            "govcloud": 0, "il_support": '["2"]',
            "use_case_tags": json.dumps(_gcp_use_case(itype)),
            "eol_status": "active", "compliance_certs": '{"fedramp":"li-saas"}',
        })
    return results


def _gcp_parse_family(itype: str) -> tuple[int, float]:
    # e.g. n2-standard-8 → 8 vCPU, 32 GB RAM (4:1)
    import re
    m = re.search(r"-(\d+)$", itype)
    if not m:
        return 0, 0
    vcpus = int(m.group(1))
    ratios = {"e2": 4, "n2": 4, "n1": 3.75, "c2": 4, "m1": 24, "m2": 24, "a2": 7, "t2": 4}
    for prefix, ratio in ratios.items():
        if itype.startswith(prefix):
            return vcpus, round(vcpus * ratio, 2)
    return vcpus, float(vcpus * 4)


def _gcp_use_case(itype: str) -> list:
    if itype.startswith("t2") or itype.startswith("e2-micro") or itype.startswith("e2-small"):
        return ["burstable", "general"]
    if itype.startswith("e2") or itype.startswith("n") or itype.startswith("t"):
        return ["general"]
    if itype.startswith("c"):
        return ["compute"]
    if itype.startswith("m"):
        return ["memory"]
    if itype.startswith("a") or itype.startswith("g"):
        return ["gpu"]
    return ["general"]


def _fetch_oci_instances() -> list[dict]:
    """Fetch OCI compute shapes from public OCI REST API (no auth required)."""
    import urllib.request

    url = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?productType=compute&limit=200"
    results = []
    seen: set = set()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-MigrationCanvas/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("OCI shapes fetch failed: %s", exc)
        return []

    for item in data.get("items", data.get("data", [])):
        name = item.get("partNumber", item.get("displayName", ""))
        if not name or name in seen:
            continue
        seen.add(name)
        vcpus = int(item.get("ocpuCount", item.get("cpuCount", 0)) or 0) * 2
        ram_gb = float(item.get("memoryInGBs", item.get("memory", 0)) or 0)
        if vcpus == 0:
            continue
        results.append({
            "provider": "oci", "instance_type": name, "family": name.split(".")[1] if "." in name else name,
            "vcpus": vcpus, "ram_gb": ram_gb, "local_storage_gb": 0,
            "storage_type": "Block", "network_gbps": min(50.0, vcpus * 0.5),
            "premium_disk_opt": 0, "cost_tier": _cost_tier_from_vcpu(vcpus),
            "govcloud": 1, "il_support": '["2","4","5"]',
            "use_case_tags": '["general"]', "eol_status": "active",
            "compliance_certs": '{"fedramp":"high"}',
        })
    return results


def _cost_tier_from_vcpu(vcpus: int) -> str:
    if vcpus <= 2:
        return "low"
    if vcpus <= 8:
        return "medium"
    if vcpus <= 32:
        return "high"
    return "very_high"


def _cost_tier_to_net(vcpus: int) -> float:
    if vcpus <= 2:
        return 0.8
    if vcpus <= 8:
        return 5.0
    if vcpus <= 32:
        return 12.5
    return 25.0


def sync_cloud_catalog(providers: list[str] | None = None, force: bool = False) -> dict:
    """Refresh mc_cloud_instances from public cloud APIs when internet is available.

    Args:
        providers: List of provider slugs to sync ('aws','azure','gcp','oci').
                   If None, syncs all four.
        force: If True, skip connectivity check.

    Returns:
        {"status": "ok"|"airgap"|"partial", "providers_synced": [...],
         "rows_upserted": int, "skipped_reason": str}
    """
    if not force and not _is_online():
        logger.info("sync_cloud_catalog: air-gap detected — skipping live sync")
        return {"status": "airgap", "providers_synced": [], "rows_upserted": 0,
                "skipped_reason": "No internet connectivity detected"}

    active = providers or ["aws", "azure", "gcp", "oci"]
    fetchers = {
        "aws": _fetch_aws_instances,
        "azure": _fetch_azure_instances,
        "gcp": _fetch_gcp_instances,
        "oci": _fetch_oci_instances,
    }

    conn = _mc_conn()
    total_upserted = 0
    synced = []
    errors = []

    try:
        now = _now()
        # Positional %s placeholders (PG-native).  Named :name binds are NOT
        # portable: translate_sql only rewrites ?→%s, and StorageCursor.execute
        # wraps a dict param into a 1-tuple, so a psycopg2 %(name)s / SQLite
        # :name mapping never reaches the driver.  Column order below MUST match
        # _UPSERT_COLS so the ordered param tuple lines up.
        _UPSERT_COLS = (
            "provider", "instance_type", "family", "vcpus", "ram_gb",
            "local_storage_gb", "storage_type", "network_gbps",
            "premium_disk_opt", "cost_tier", "govcloud", "il_support",
            "use_case_tags", "eol_status", "compliance_certs",
            "last_refreshed_at",
        )
        upsert_sql = (
            "INSERT OR REPLACE INTO mc_cloud_instances "
            "(provider, instance_type, family, vcpus, ram_gb, local_storage_gb, "
            "storage_type, network_gbps, premium_disk_opt, cost_tier, govcloud, "
            "il_support, use_case_tags, eol_status, compliance_certs, "
            "source, last_refreshed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'api',%s)"
        )
        for prov in active:
            if prov not in fetchers:
                continue
            try:
                rows = fetchers[prov]()
                for row in rows:
                    row.setdefault("eol_status", "active")
                    row.setdefault("compliance_certs", "{}")
                    row.setdefault("local_storage_gb", 0)
                    row["last_refreshed_at"] = now
                    conn.execute(upsert_sql, tuple(row.get(c) for c in _UPSERT_COLS))
                total_upserted += len(rows)
                if rows:
                    synced.append(prov)
                conn.commit()
                logger.info("sync_cloud_catalog: %s — upserted %d rows", prov, len(rows))
            except Exception as exc:
                logger.warning("sync_cloud_catalog: %s failed — %s", prov, exc)
                errors.append(f"{prov}: {exc}")
    finally:
        conn.close()

    status = "ok" if not errors else ("partial" if synced else "error")
    return {
        "status": status,
        "providers_synced": synced,
        "rows_upserted": total_upserted,
        "errors": errors,
    }


# ── Hypervisor Live Import ────────────────────────────────────────────────────

def import_from_hypervisor(
    session_id: str,
    adapter_type: str,
    host: str,
    user: str,
    password: str,
    datacenter: str | None = None,
    cluster: str | None = None,
) -> dict:
    """Pull live VM inventory from a hypervisor and upsert into mc_srv_inventory.

    Args:
        session_id: Server migration session ID.
        adapter_type: 'vmware', 'hyperv', or 'nutanix'.
        host: Hypervisor host/IP.
        user: Credentials username.
        password: Credentials password.
        datacenter: Optional datacenter filter (VMware).
        cluster: Optional cluster filter (Nutanix).

    Returns:
        {"ok": bool, "imported": int, "hypervisor_session_id": str, "error": str | None}
    """
    from tools.migration_canvas.adapters import pull_inventory as _pull

    kwargs: dict = {}
    if datacenter:
        kwargs["datacenter"] = datacenter
    if cluster:
        kwargs["cluster"] = cluster

    result = _pull(adapter_type, host, user, password, **kwargs)
    vms = result.get("vms", [])
    ok = result.get("ok", False)

    conn = _mc_conn()
    now = _now()
    hv_session_id = "hvs-" + uuid.uuid4().hex[:10] if "uuid" in dir() else \
        "hvs-" + str(id(result))[-10:]

    try:
        import uuid as _uuid
        hv_session_id = "hvs-" + _uuid.uuid4().hex[:10]
    except Exception:
        pass

    try:
        # Record the pull session
        conn.execute(
            "INSERT OR IGNORE INTO mc_srv_hypervisor_sessions "
            "(id, session_id, adapter_type, host, datacenter, cluster, pulled_at, vm_count, status, error_msg) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                hv_session_id, session_id, adapter_type, host,
                datacenter or "", cluster or "", now,
                len(vms), "ok" if ok else "error",
                result.get("error") or "",
            ),
        )

        # Upsert each VM into mc_srv_inventory
        imported = 0
        for vm in vms:
            inv_id = "inv-" + uuid.uuid4().hex[:10]
            conn.execute(
                "INSERT OR IGNORE INTO mc_srv_inventory "
                "(id, session_id, hostname, vcpus, ram_gb, disk_count, total_disk_gb, "
                "disk_type, nic_count, os_family, os_name, os_arch, bios_type, "
                "virtualization_ext, source_format, imported_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    inv_id, session_id,
                    vm.get("hostname", "unknown"),
                    vm.get("vcpus", 1),
                    vm.get("ram_gb", 0),
                    vm.get("disk_count", 0),
                    vm.get("total_disk_gb", 0),
                    vm.get("disk_type", ""),
                    vm.get("nic_count", 1),
                    vm.get("os_family", ""),
                    vm.get("os_name", ""),
                    vm.get("os_arch", "x86_64"),
                    vm.get("bios_type", "UEFI"),
                    vm.get("virtualization_ext", 1),
                    f"hypervisor:{adapter_type}",
                    now,
                ),
            )
            imported += 1

        conn.commit()
        logger.info("import_from_hypervisor: imported %d VMs from %s (%s)", imported, host, adapter_type)
        return {
            "ok": ok,
            "imported": imported,
            "hypervisor_session_id": hv_session_id,
            "error": result.get("error"),
        }
    finally:
        conn.close()


# ── Advanced Cloud Catalog (spot / reserved / savings plans) ──────────────────

def get_cloud_instances_advanced(
    provider: str,
    filters: dict | None = None,
    pricing_model: str = "on_demand",
    vcpu_min: int = 0,
    ram_min: float = 0,
) -> list[dict]:
    """Return cloud instances filtered by pricing model.

    Extends get_cloud_instances() with spot/reserved/savings_plan pricing columns
    added by migration 084.

    Args:
        provider: 'aws', 'azure', 'gcp', 'oci' or 'all'.
        filters: Optional dict of column filters (same as get_cloud_instances).
        pricing_model: 'on_demand', 'spot', 'reserved_1yr', 'reserved_3yr', 'savings_plan'.
        vcpu_min: Minimum vCPU count.
        ram_min: Minimum RAM in GB.

    Returns:
        List of instance dicts ranked by effective cost (lowest first).
    """
    VALID_MODELS = {"on_demand", "spot", "reserved_1yr", "reserved_3yr", "savings_plan"}
    if pricing_model not in VALID_MODELS:
        pricing_model = "on_demand"

    price_col_map = {
        "spot": "spot_price",
        "reserved_1yr": "reserved_1yr_price",
        "reserved_3yr": "reserved_3yr_price",
        "savings_plan": "savings_plan_price",
        "on_demand": None,
    }
    price_col = price_col_map[pricing_model]

    conn = _mc_conn()
    try:
        where = ["1=1"]
        params: list = []

        if provider and provider != "all":
            where.append("provider=%s")
            params.append(provider)
        if vcpu_min:
            where.append("vcpus >= %s")
            params.append(vcpu_min)
        if ram_min:
            where.append("ram_gb >= %s")
            params.append(ram_min)

        for f_filters in (filters or {}).items():
            col, val = f_filters
            where.append(f"{col}=%s")
            params.append(val)

        # If pricing column exists and is not null, filter to those rows for non-on-demand
        if price_col:
            where.append(f"{price_col} IS NOT NULL")

        order_col = price_col if price_col else "vcpus"
        sql = (
            f"SELECT *, {price_col or 'NULL'} AS effective_price "
            f"FROM mc_cloud_instances WHERE {' AND '.join(where)} "
            f"ORDER BY {order_col} ASC LIMIT 100"
        )
        rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["pricing_model"] = pricing_model
            result.append(d)
        return result
    except Exception as exc:
        logger.warning("get_cloud_instances_advanced failed: %s", exc)
        return []
    finally:
        conn.close()
