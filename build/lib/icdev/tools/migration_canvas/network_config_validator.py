# CUI // SP-CTI
"""Network migration post-cutover config validator.

Compares source device running config against target device config after
cutover to verify completeness.  Writes results to mc_net_config_validation.

No mandatory LLM dependency.  SSH-based config pull is optional — callers
can also pass config text directly.

Public functions:
    diff_configs(source_config, target_config, vendor)   → dict
    validate_migration_completeness(session_id)          → dict
    run_connectivity_tests(session_id, test_pairs)       → list[dict]
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import re
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone

logger = get_logger("icdev.migration_canvas.network_config_validator")

_TIMEOUT = 10

_CONFIG_VALIDATION_DDL = """
CREATE TABLE IF NOT EXISTS mc_net_config_validation (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    device_id           TEXT,
    run_at              TEXT NOT NULL,
    diff_summary        TEXT DEFAULT '{}',
    completeness_score  REAL DEFAULT 0,
    status              TEXT DEFAULT 'pending'
        CHECK(status IN ('pass','partial','fail','pending'))
);
CREATE INDEX IF NOT EXISTS idx_mc_net_cfgval_session ON mc_net_config_validation(session_id);
"""


def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_table():
    with _conn() as db:
        db.executescript(_CONFIG_VALIDATION_DDL)
        db.commit()


# ── Config section extractors ─────────────────────────────────────────────────

def _extract_sections(config_text: str, vendor: str) -> dict[str, list[str]]:
    """Break a device config into named sections for comparison."""
    text = config_text or ""
    sections: dict[str, list[str]] = {
        "interfaces": [],
        "routing": [],
        "bgp_neighbors": [],
        "ospf": [],
        "acls": [],
        "vlans": [],
        "mpls": [],
        "ntp": [],
        "logging": [],
        "other": [],
    }

    v = vendor.lower()

    if "juniper" in v:
        # JunOS set-format sections
        for line in text.splitlines():
            l = line.strip()
            if not l or l.startswith("#"):
                continue
            if l.startswith("set interfaces"):
                sections["interfaces"].append(l)
            elif l.startswith("set protocols bgp"):
                sections["bgp_neighbors"].append(l)
            elif l.startswith("set protocols ospf"):
                sections["ospf"].append(l)
            elif l.startswith("set protocols mpls") or l.startswith("set protocols ldp"):
                sections["mpls"].append(l)
            elif l.startswith("set firewall"):
                sections["acls"].append(l)
            elif l.startswith("set vlans") or "vlan" in l:
                sections["vlans"].append(l)
            elif l.startswith("set system ntp"):
                sections["ntp"].append(l)
            elif l.startswith("set system syslog"):
                sections["logging"].append(l)
            else:
                sections["routing"].append(l) if "routing" in l or "route" in l else sections["other"].append(l)

    elif "arista" in v:
        # Arista EOS — Cisco-like syntax with VXLAN/EVPN additions
        current_section = "other"
        for line in text.splitlines():
            l = line.strip()
            if l.startswith("interface"):
                current_section = "interfaces"
            elif l.startswith("router bgp"):
                current_section = "bgp_neighbors"
            elif l.startswith("router ospf"):
                current_section = "ospf"
            elif l.startswith("vlan "):
                current_section = "vlans"
            elif l.startswith("ip access-list") or l.startswith("mac access-list"):
                current_section = "acls"
            elif l.startswith("mpls"):
                current_section = "mpls"
            elif l.startswith("ntp"):
                current_section = "ntp"
            elif l.startswith("logging"):
                current_section = "logging"
            elif l.startswith("ip route") or l.startswith("ipv6 route"):
                current_section = "routing"
            elif l.startswith("!") or not l:
                current_section = "other"
                continue
            sections[current_section].append(l)

    else:
        # Cisco IOS / NX-OS (default)
        current_section = "other"
        for line in text.splitlines():
            l = line.strip()
            if l.startswith("interface"):
                current_section = "interfaces"
            elif l.startswith("router bgp") or l.startswith("neighbor"):
                current_section = "bgp_neighbors"
            elif l.startswith("router ospf"):
                current_section = "ospf"
            elif l.startswith("vlan "):
                current_section = "vlans"
            elif re.match(r"(ip|ipv6)\s+access-list", l):
                current_section = "acls"
            elif l.startswith("mpls") or l.startswith("ldp"):
                current_section = "mpls"
            elif l.startswith("ntp"):
                current_section = "ntp"
            elif l.startswith("logging"):
                current_section = "logging"
            elif re.match(r"(ip|ipv6)\s+route", l):
                current_section = "routing"
            elif l.startswith("!") or not l:
                current_section = "other"
                continue
            sections[current_section].append(l)

    return sections


def _section_overlap_score(src: list[str], tgt: list[str]) -> float:
    """Return fraction of src lines that have a fuzzy match in tgt (0-1)."""
    if not src:
        return 1.0
    tgt_set = set(tgt)
    matched = sum(1 for line in src if line in tgt_set)
    return round(matched / len(src), 3)


# ── Public API ────────────────────────────────────────────────────────────────

def diff_configs(source_config: str, target_config: str, vendor: str = "cisco") -> dict:
    """Compare source and target device configs section by section.

    Returns:
        {
            "sections": {section_name: {"src_count", "tgt_count", "overlap_score"}},
            "completeness_score": float (0-100),
            "missing_lines": [str],     # lines in src not found in tgt
            "extra_lines": [str],       # lines in tgt not found in src
        }
    """
    src_sections = _extract_sections(source_config, vendor)
    tgt_sections = _extract_sections(target_config, vendor)

    section_scores = {}
    all_src_lines: set[str] = set()
    all_tgt_lines: set[str] = set()

    for sec, src_lines in src_sections.items():
        tgt_lines = tgt_sections.get(sec, [])
        score = _section_overlap_score(src_lines, tgt_lines)
        section_scores[sec] = {
            "src_count": len(src_lines),
            "tgt_count": len(tgt_lines),
            "overlap_score": score,
        }
        all_src_lines.update(src_lines)
        all_tgt_lines.update(tgt_lines)

    missing = sorted(all_src_lines - all_tgt_lines)[:50]  # cap at 50
    extra = sorted(all_tgt_lines - all_src_lines)[:50]

    # Weight interfaces + routing heavier than ntp/logging
    weights = {
        "interfaces": 0.30, "routing": 0.15, "bgp_neighbors": 0.15,
        "ospf": 0.10, "acls": 0.10, "vlans": 0.10,
        "mpls": 0.05, "ntp": 0.025, "logging": 0.025, "other": 0.0,
    }
    weighted_score = 0.0
    for sec, w in weights.items():
        if sec in section_scores and section_scores[sec]["src_count"] > 0:
            weighted_score += section_scores[sec]["overlap_score"] * w

    completeness = round(weighted_score * 100, 1)

    return {
        "sections": section_scores,
        "completeness_score": completeness,
        "missing_lines": missing,
        "extra_lines": extra,
    }


def validate_migration_completeness(session_id: str) -> dict:
    """For each device in a network migration session, diff source vs pulled config.

    Reads mc_net_sessions (src_config_raw) and attempts to SSH-pull the
    target config.  Falls back to a synthetic "no target config" result if
    SSH is not available.

    Writes results to mc_net_config_validation.
    """
    _ensure_table()

    with _conn() as db:
        sess = db.execute(
            "SELECT src_config_raw, src_model, tgt_model, tgt_device_name "
            "FROM mc_net_sessions WHERE id=?",
            (session_id,),
        ).fetchone()

    if not sess:
        return {"error": f"Session {session_id} not found"}

    src_config = sess[0] or ""
    tgt_device = sess[3] or ""
    vendor = "cisco"  # default

    # Attempt SSH pull of target config (best-effort)
    tgt_config = ""
    if tgt_device:
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                 tgt_device, "show running-config"],
                capture_output=True, text=True, timeout=20
            )
            if result.returncode == 0:
                tgt_config = result.stdout
        except Exception:
            pass

    if not src_config and not tgt_config:
        return {"error": "No source or target config available — import config first."}

    diff = diff_configs(src_config, tgt_config, vendor) if tgt_config else {
        "completeness_score": 0,
        "sections": {},
        "missing_lines": [],
        "extra_lines": [],
        "note": "Target config not available for comparison.",
    }

    score = diff.get("completeness_score", 0)
    status = "pass" if score >= 90 else ("partial" if score >= 50 else "fail")
    if not tgt_config:
        status = "pending"

    row_id = str(uuid.uuid4())
    run_at = _now()
    with _conn() as db:
        db.execute(
            "INSERT INTO mc_net_config_validation "
            "(id, session_id, run_at, diff_summary, completeness_score, status) "
            "VALUES (?,?,?,?,?,?)",
            (row_id, session_id, run_at, json.dumps(diff), score, status),
        )
        db.commit()

    return {
        "validation_id": row_id,
        "session_id": session_id,
        "status": status,
        "completeness_score": score,
        "diff": diff,
        "run_at": run_at,
    }


def run_connectivity_tests(session_id: str, test_pairs: list[dict]) -> list[dict]:
    """Ping/TCP between source→target pairs to confirm reachability.

    test_pair fields: src_ip, tgt_ip, ports (list[int]), description
    """
    results = []
    run_at = _now()

    for pair in test_pairs:
        src = pair.get("src_ip", "")
        tgt = pair.get("tgt_ip", "")
        ports = pair.get("ports", [22, 80, 443])
        desc = pair.get("description", f"{src}→{tgt}")

        reachable_ports = []
        failed_ports = []
        start = time.monotonic()

        for port in ports:
            try:
                with socket.create_connection((tgt, port), timeout=_TIMEOUT):
                    reachable_ports.append(port)
            except OSError:
                failed_ports.append(port)

        elapsed = int((time.monotonic() - start) * 1000)
        status = "pass" if not failed_ports else ("partial" if reachable_ports else "fail")
        detail = (
            f"Ports OK: {reachable_ports}; Failed: {failed_ports}"
            if failed_ports else f"All {len(ports)} ports reachable"
        )

        results.append({
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "description": desc,
            "src": src,
            "tgt": tgt,
            "status": status,
            "detail": detail,
            "elapsed_ms": elapsed,
            "run_at": run_at,
        })

    return results
