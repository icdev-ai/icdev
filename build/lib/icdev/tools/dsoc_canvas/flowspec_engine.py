# CUI // SP-CTI
"""BGP Flowspec rule generator — RFC 5575 / RFC 8955."""
from __future__ import annotations

from datetime import datetime, timezone


# ── NLRI / config generators ──────────────────────────────────────────────────

def generate_flowspec_nlri(rule: dict) -> str:
    """Format a human-readable NLRI string from rule fields."""
    parts: list[str] = []
    if rule.get("destination_prefix"):
        parts.append(f"dst {rule['destination_prefix']}")
    if rule.get("source_prefix"):
        parts.append(f"src {rule['source_prefix']}")
    if rule.get("protocol"):
        parts.append(f"proto {rule['protocol']}")
    if rule.get("dst_port"):
        parts.append(f"dport {rule['dst_port']}")
    if rule.get("src_port"):
        parts.append(f"sport {rule['src_port']}")
    if rule.get("packet_length"):
        parts.append(f"pktlen {rule['packet_length']}")
    return " ".join(parts) if parts else "(empty rule)"


def generate_ios_xr_flowspec(rule: dict) -> str:
    """Return IOS-XR policy-map + class-map config snippet for a flowspec rule."""
    name = rule.get("rule_name", "dsoc-rule")
    action = rule.get("action", "drop")
    rate_bps = rule.get("rate_limit_bps", 0)

    match_lines: list[str] = []
    if rule.get("destination_prefix"):
        match_lines.append(f"   match destination-address ipv4 {rule['destination_prefix']}")
    if rule.get("source_prefix"):
        match_lines.append(f"   match source-address ipv4 {rule['source_prefix']}")
    if rule.get("protocol") and rule["protocol"] != "any":
        match_lines.append(f"   match protocol {rule['protocol']}")
    if rule.get("dst_port"):
        match_lines.append(f"   match destination-port {rule['dst_port']}")
    if rule.get("src_port"):
        match_lines.append(f"   match source-port {rule['src_port']}")
    if rule.get("packet_length"):
        match_lines.append(f"   match packet length {rule['packet_length']}")

    match_block = "\n".join(match_lines) if match_lines else "   ! (no match criteria)"

    if action == "drop":
        action_line = "   drop"
    elif action == "rate-limit" and rate_bps:
        kbps = max(1, rate_bps // 1000)
        action_line = f"   police rate {kbps} kbps"
    elif action == "redirect":
        vrf = rule.get("redirect_vrf", "SCRUBBING")
        action_line = f"   redirect vrf {vrf}"
    elif action == "mark":
        dscp = rule.get("dscp", "0")
        action_line = f"   set dscp {dscp}"
    elif action == "sample":
        action_line = "   traffic-class 1"
    else:
        action_line = f"   ! action: {action}"

    return (
        f"class-map match-all DSOC-{name}\n"
        f"{match_block}\n"
        f"!\n"
        f"policy-map DSOC-FLOWSPEC\n"
        f" class DSOC-{name}\n"
        f"{action_line}\n"
        f"!"
    )


def generate_junos_flowspec(rule: dict) -> str:
    """Return JunOS flow route config snippet for a flowspec rule."""
    name = rule.get("rule_name", "dsoc-rule")
    action = rule.get("action", "drop")
    rate_bps = rule.get("rate_limit_bps", 0)
    community = rule.get("community", "")

    match_lines: list[str] = []
    if rule.get("destination_prefix"):
        match_lines.append(f"        destination {rule['destination_prefix']};")
    if rule.get("source_prefix"):
        match_lines.append(f"        source {rule['source_prefix']};")
    if rule.get("protocol") and rule["protocol"] != "any":
        match_lines.append(f"        protocol {rule['protocol']};")
    if rule.get("dst_port"):
        match_lines.append(f"        destination-port {rule['dst_port']};")
    if rule.get("src_port"):
        match_lines.append(f"        source-port {rule['src_port']};")
    if rule.get("packet_length"):
        match_lines.append(f"        packet-length {rule['packet_length']};")

    match_block = "\n".join(match_lines) if match_lines else "        # (no match criteria)"

    if action == "drop":
        then_block = "        discard;"
    elif action == "rate-limit" and rate_bps:
        then_block = f"        rate-limit {rate_bps};"
    elif action == "redirect":
        vrf = rule.get("redirect_vrf", "SCRUBBING")
        then_block = f"        routing-instance {vrf};"
    elif action == "mark":
        dscp = rule.get("dscp", "0")
        then_block = f"        dscp {dscp};"
    elif action == "sample":
        then_block = "        sample;"
    else:
        then_block = f"        # action: {action}"

    community_line = f"\n        community {community};" if community else ""

    return (
        f"flow {{\n"
        f"    route {name} {{\n"
        f"        match {{\n"
        f"{match_block}\n"
        f"        }}\n"
        f"        then {{\n"
        f"{then_block}{community_line}\n"
        f"        }}\n"
        f"    }}\n"
        f"}}"
    )


# ── DB mutation helpers ───────────────────────────────────────────────────────

def _safe_fetch(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT with PG-style params; fall back to SQLite-style on failure."""
    def _rows(cur) -> list[dict]:
        rows = cur.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    try:
        cur = conn.cursor()
        cur.execute(sql_pg, params)
        return _rows(cur)
    except Exception:
        try:
            cur = conn.cursor()
            cur.execute(sql_sq, params)
            return _rows(cur)
        except Exception:
            return []


def activate_rule(conn, rule_id: int) -> dict:
    """Set a flowspec rule to status='active' and update updated_at."""
    now = datetime.now(timezone.utc).isoformat()
    sql_pg = "UPDATE dsoc_flowspec_rules SET status='active', updated_at=%s WHERE id=%s"
    sql_sq = "UPDATE dsoc_flowspec_rules SET status='active', updated_at=? WHERE id=?"
    try:
        conn.execute(sql_pg, (now, rule_id))
        conn.commit()
    except Exception:
        conn.execute(sql_sq, (now, rule_id))
        conn.commit()

    rows = _safe_fetch(
        conn,
        "SELECT * FROM dsoc_flowspec_rules WHERE id=%s",
        "SELECT * FROM dsoc_flowspec_rules WHERE id=?",
        (rule_id,),
    )
    return rows[0] if rows else {}


def withdraw_rule(conn, rule_id: int) -> dict:
    """Set a flowspec rule to status='withdrawn' and update updated_at."""
    now = datetime.now(timezone.utc).isoformat()
    sql_pg = "UPDATE dsoc_flowspec_rules SET status='withdrawn', updated_at=%s WHERE id=%s"
    sql_sq = "UPDATE dsoc_flowspec_rules SET status='withdrawn', updated_at=? WHERE id=?"
    try:
        conn.execute(sql_pg, (now, rule_id))
        conn.commit()
    except Exception:
        conn.execute(sql_sq, (now, rule_id))
        conn.commit()

    rows = _safe_fetch(
        conn,
        "SELECT * FROM dsoc_flowspec_rules WHERE id=%s",
        "SELECT * FROM dsoc_flowspec_rules WHERE id=?",
        (rule_id,),
    )
    return rows[0] if rows else {}
