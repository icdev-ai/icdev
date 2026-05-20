# CUI // SP-CTI
"""BGP configuration generator — emits peer session configs for IOS-XR, JunOS, EOS, Nokia SR OS."""

from __future__ import annotations

from typing import Any


_OS_DEFAULTS = {
    "ios_xr": {"af_ipv4": "ipv4 unicast", "af_ipv6": "ipv6 unicast"},
    "junos": {"af_ipv4": "inet unicast", "af_ipv6": "inet6 unicast"},
    "eos": {"af_ipv4": "ipv4", "af_ipv6": "ipv6"},
    "nokia_sros": {"af_ipv4": "ipv4", "af_ipv6": "ipv6"},
    "frr": {"af_ipv4": "ipv4 unicast", "af_ipv6": "ipv6 unicast"},
    "bird2": {"af_ipv4": "ipv4", "af_ipv6": "ipv6"},
}


def generate_peer_session(
    peer: dict[str, Any],
    our_asn: int,
    os_type: str,
    neighbor_ip: str,
    *,
    import_policy: str = "IMPORT-PEER",
    export_policy: str = "EXPORT-PEER",
    max_prefixes_v4: int | None = None,
    max_prefixes_v6: int | None = None,
    md5_password: str = "",
    description: str = "",
) -> str:
    """Generate a BGP neighbor config stanza.

    Args:
        peer: Peer dict with asn, org_name fields
        our_asn: Our local ASN
        os_type: One of ios_xr | junos | eos | nokia_sros | frr | bird2
        neighbor_ip: Peer's IP address for the session
        import_policy: Import route policy name
        export_policy: Export route policy name
        max_prefixes_v4: IPv4 max-prefix limit
        max_prefixes_v6: IPv6 max-prefix limit
        md5_password: BGP MD5 password (empty = omit)
        description: Human-readable neighbor description
    """
    peer_asn = peer.get("asn", 0)
    peer_name = peer.get("org_name", f"AS{peer_asn}")
    desc = description or f"PEER: {peer_name} (AS{peer_asn})"
    is_v6 = ":" in neighbor_ip
    mpv4 = max_prefixes_v4 or peer.get("max_prefixes_v4", 1000)
    mpv6 = max_prefixes_v6 or peer.get("max_prefixes_v6", 1000)

    generators = {
        "ios_xr": _gen_ios_xr,
        "junos": _gen_junos,
        "eos": _gen_eos,
        "nokia_sros": _gen_nokia_sros,
        "frr": _gen_frr,
        "bird2": _gen_bird2,
    }

    fn = generators.get(os_type)
    if not fn:
        raise ValueError(f"Unsupported OS type: {os_type}. Choose from {list(generators)}")

    return fn(
        peer_asn=peer_asn,
        our_asn=our_asn,
        neighbor_ip=neighbor_ip,
        desc=desc,
        import_policy=import_policy,
        export_policy=export_policy,
        max_prefixes=(mpv6 if is_v6 else mpv4),
        md5_password=md5_password,
        is_v6=is_v6,
    )


def _gen_ios_xr(*, peer_asn, our_asn, neighbor_ip, desc, import_policy,
                export_policy, max_prefixes, md5_password, is_v6) -> str:
    af = "ipv6 unicast" if is_v6 else "ipv4 unicast"
    lines = [
        f"neighbor {neighbor_ip}",
        f"  remote-as {peer_asn}",
        f"  description {desc}",
        f"  update-source Loopback0",
    ]
    if md5_password:
        lines.append(f"  password clear {md5_password}")
    lines += [
        f"  address-family {af}",
        f"    route-policy {import_policy} in",
        f"    route-policy {export_policy} out",
        f"    maximum-prefix {max_prefixes} 80 warning-only",
        f"    soft-reconfiguration inbound always",
        "  !",
        "!",
    ]
    return "\n".join(lines)


def _gen_junos(*, peer_asn, our_asn, neighbor_ip, desc, import_policy,
               export_policy, max_prefixes, md5_password, is_v6) -> str:
    af = "inet6 unicast" if is_v6 else "inet unicast"
    lines = [
        f"set protocols bgp group PEERS-{peer_asn} type external",
        f"set protocols bgp group PEERS-{peer_asn} description \"{desc}\"",
        f"set protocols bgp group PEERS-{peer_asn} peer-as {peer_asn}",
        f"set protocols bgp group PEERS-{peer_asn} neighbor {neighbor_ip}",
    ]
    if md5_password:
        lines.append(
            f"set protocols bgp group PEERS-{peer_asn} neighbor {neighbor_ip} authentication-key \"{md5_password}\""
        )
    lines += [
        f"set protocols bgp group PEERS-{peer_asn} family {af} prefix-limit maximum {max_prefixes}",
        f"set protocols bgp group PEERS-{peer_asn} import {import_policy}",
        f"set protocols bgp group PEERS-{peer_asn} export {export_policy}",
    ]
    return "\n".join(lines)


def _gen_eos(*, peer_asn, our_asn, neighbor_ip, desc, import_policy,
             export_policy, max_prefixes, md5_password, is_v6) -> str:
    af = "ipv6" if is_v6 else "ipv4"
    lines = [
        f"neighbor {neighbor_ip} remote-as {peer_asn}",
        f"neighbor {neighbor_ip} description {desc}",
        f"neighbor {neighbor_ip} maximum-routes {max_prefixes} warning-only",
    ]
    if md5_password:
        lines.append(f"neighbor {neighbor_ip} password 7 {md5_password}")
    lines += [
        f"!",
        f"address-family {af}",
        f"   neighbor {neighbor_ip} activate",
        f"   neighbor {neighbor_ip} route-map {import_policy} in",
        f"   neighbor {neighbor_ip} route-map {export_policy} out",
    ]
    return "\n".join(lines)


def _gen_nokia_sros(*, peer_asn, our_asn, neighbor_ip, desc, import_policy,
                    export_policy, max_prefixes, md5_password, is_v6) -> str:
    af_section = "ipv6" if is_v6 else "ipv4"
    lines = [
        f"configure router bgp group \"PEERS-{peer_asn}\"",
        f"    description \"{desc}\"",
        f"    peer-as {peer_asn}",
        f"    import policy \"{import_policy}\"",
        f"    export policy \"{export_policy}\"",
        f"    neighbor {neighbor_ip}",
        f"        description \"{desc}\"",
    ]
    if md5_password:
        lines.append(f"        authentication-key \"{md5_password}\"")
    lines += [
        f"        family {af_section}",
        f"        prefix-limit {max_prefixes}",
        f"    exit",
        f"exit",
    ]
    return "\n".join(lines)


def _gen_frr(*, peer_asn, our_asn, neighbor_ip, desc, import_policy,
             export_policy, max_prefixes, md5_password, is_v6) -> str:
    af = "ipv6 unicast" if is_v6 else "ipv4 unicast"
    lines = [
        f"neighbor {neighbor_ip} remote-as {peer_asn}",
        f"neighbor {neighbor_ip} description {desc}",
    ]
    if md5_password:
        lines.append(f"neighbor {neighbor_ip} password {md5_password}")
    lines += [
        f"!",
        f"address-family {af}",
        f" neighbor {neighbor_ip} activate",
        f" neighbor {neighbor_ip} route-map {import_policy} in",
        f" neighbor {neighbor_ip} route-map {export_policy} out",
        f" neighbor {neighbor_ip} maximum-prefix {max_prefixes}",
        f"exit-address-family",
    ]
    return "\n".join(lines)


def _gen_bird2(*, peer_asn, our_asn, neighbor_ip, desc, import_policy,
               export_policy, max_prefixes, md5_password, is_v6) -> str:
    _table = "master6" if is_v6 else "master4"
    lines = [
        f"protocol bgp PEER_{peer_asn} {{",
        f"    description \"{desc}\";",
        f"    local as {our_asn};",
        f"    neighbor {neighbor_ip} as {peer_asn};",
    ]
    if md5_password:
        lines.append(f"    password \"{md5_password}\";")
    lines += [
        f"    ipv{'6' if is_v6 else '4'} {{",
        f"        import filter {import_policy};",
        f"        export filter {export_policy};",
        f"        import limit {max_prefixes} action warn;",
        f"    }};",
        f"}}",
    ]
    return "\n".join(lines)


def generate_prefix_list(
    list_name: str,
    prefixes: list[str],
    os_type: str,
    action: str = "permit",
) -> str:
    """Generate a prefix-list stanza from a list of CIDR prefixes."""
    if os_type in ("ios_xr", "eos", "frr"):
        lines = []
        for i, pfx in enumerate(prefixes, 1):
            lines.append(f"ip prefix-list {list_name} seq {i * 5} {action} {pfx}")
        return "\n".join(lines)
    elif os_type == "junos":
        members = "\n".join(f"    {pfx};" for pfx in prefixes)
        return f"set policy-options prefix-list {list_name} {{\n{members}\n}}"
    elif os_type == "nokia_sros":
        lines = [f"configure router policy-options prefix-list \"{list_name}\""]
        for pfx in prefixes:
            lines.append(f"    prefix {pfx} longer")
        lines.append("exit")
        return "\n".join(lines)
    return "\n".join(f"# {action} {pfx}" for pfx in prefixes)
