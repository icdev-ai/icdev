# CUI // SP-CTI
"""RPSL generator — RFC 2622 compliant aut-num, route, and as-set objects.

Pure Python, no external dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def generate_aut_num(
    our_asn: int,
    import_peers: list[dict],
    export_peers: list[dict],
    org_name: str = "MY-ORG",
    as_set: str = "",
    description: str = "",
) -> str:
    """Generate an aut-num RPSL object for our ASN.

    Args:
        our_asn: Our autonomous system number
        import_peers: List of {asn, policy, as_set, action} dicts
        export_peers: List of {asn, policy, as_set, action} dicts
        org_name: Organisation name string
        as_set: Our AS-SET name (e.g. AS64512:AS-CUSTOMERS)
        description: Human-readable description
    """
    lines = [
        f"aut-num:        AS{our_asn}",
        f"as-name:        AS{our_asn}-{org_name.upper().replace(' ', '-')[:20]}",
        f"descr:          {description or org_name}",
        f"org:            ORG-{org_name[:10].upper().replace(' ', '')}",
        "",
    ]

    if import_peers:
        lines.append("# Import policies")
        for p in import_peers:
            from_as = f"AS{p['asn']}" if p.get("asn") else p.get("as_set", "ANY")
            action = p.get("action", "accept ANY")
            lines.append(f"import:         from {from_as} accept {action}")
        lines.append("")

    if export_peers:
        lines.append("# Export policies")
        for p in export_peers:
            to_as = f"AS{p['asn']}" if p.get("asn") else p.get("as_set", "ANY")
            announce = p.get("announce", as_set or f"AS{our_asn}")
            lines.append(f"export:         to {to_as} announce {announce}")
        lines.append("")

    lines += [
        f"mnt-by:         MAINT-AS{our_asn}",
        f"changed:        noc@example.com {_ts()}",
        "source:         ARIN",
        "",
    ]
    return "\n".join(lines)


def generate_route_object(
    prefix: str,
    origin_asn: int,
    description: str = "",
    mnt_by: str = "",
) -> str:
    """Generate a route/route6 RPSL object.

    Args:
        prefix: CIDR prefix (e.g. 192.0.2.0/24 or 2001:db8::/32)
        origin_asn: Originating ASN
        description: Human-readable description
        mnt_by: Maintainer object name
    """
    obj_type = "route6" if ":" in prefix else "route"
    lines = [
        f"{obj_type}:           {prefix}",
        f"descr:          {description or f'Route for AS{origin_asn}'}",
        f"origin:         AS{origin_asn}",
        f"mnt-by:         {mnt_by or f'MAINT-AS{origin_asn}'}",
        f"changed:        noc@example.com {_ts()}",
        "source:         ARIN",
        "",
    ]
    return "\n".join(lines)


def generate_as_set(
    set_name: str,
    members: list[int | str],
    description: str = "",
    mnt_by: str = "",
) -> str:
    """Generate an as-set RPSL object.

    Args:
        set_name: AS-SET name (e.g. AS64512:AS-CUSTOMERS)
        members: List of ASNs (int) or AS-SET names (str)
        description: Human-readable description
        mnt_by: Maintainer object name
    """
    member_strs = []
    for m in members:
        member_strs.append(f"AS{m}" if isinstance(m, int) else str(m))

    members_line = ", ".join(member_strs) if member_strs else "# empty"
    lines = [
        f"as-set:         {set_name}",
        f"descr:          {description or set_name}",
        f"members:        {members_line}",
        f"mnt-by:         {mnt_by or 'MAINT-SET'}",
        f"changed:        noc@example.com {_ts()}",
        "source:         ARIN",
        "",
    ]
    return "\n".join(lines)


def export_to_irr(objects: list[str]) -> str:
    """Combine multiple RPSL objects into an IRR submission text."""
    return "\n".join(objects)


def generate_filter_set(
    our_asn: int,
    peer_as_sets: list[str],
    filter_name: str = "",
) -> str:
    """Generate a filter-set RPSL object restricting accepted routes."""
    fname = filter_name or f"FLTR-AS{our_asn}-IN"
    members_line = " OR ".join(peer_as_sets) if peer_as_sets else "ANY"
    lines = [
        f"filter-set:     {fname}",
        f"descr:          Inbound filter for AS{our_asn}",
        f"filter:         {members_line}",
        f"mnt-by:         MAINT-AS{our_asn}",
        f"changed:        noc@example.com {_ts()}",
        "source:         ARIN",
        "",
    ]
    return "\n".join(lines)
