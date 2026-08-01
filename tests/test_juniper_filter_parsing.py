# CUI // SP-CTI
"""Juniper firewall-filter extraction in config_parser.

parse_juniper previously returned `acls: []` unconditionally, so ACL/filter
drift — one of the highest-value signals for a runbook — could never fire on
Juniper, only Cisco. These cover both Junos syntaxes and pin the shared
contract so the IOS/NX-OS parsers stay untouched.

Stdlib only; no DB, no network, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Hierarchy form, with nesting (from { ... }) that a flat regex cannot match,
# plus an interface that *applies* a filter — which must not be mistaken for a
# filter definition.
JUNOS_HIER = """
system {
    host-name EDGE-MX304-01;
}
interfaces {
    ge-0/0/0 {
        unit 0 {
            family inet {
                filter {
                    input PROTECT-RE;
                }
                address 10.1.1.1/24;
            }
        }
    }
}
firewall {
    family inet {
        filter PROTECT-RE {
            term ALLOW-SSH {
                from {
                    source-address {
                        10.0.0.0/8;
                    }
                    protocol tcp;
                    destination-port 22;
                }
                then accept;
            }
            term DENY-ALL {
                then {
                    discard;
                }
            }
        }
        filter COUNT-ICMP {
            term ICMP {
                from {
                    protocol icmp;
                }
                then count icmp-counter;
            }
        }
    }
}
"""

JUNOS_SET = """
set system host-name EDGE-MX304-02
set interfaces ge-0/0/1 unit 0 family inet address 10.2.2.1/24
set firewall family inet filter MGMT-IN term ALLOW-NTP from source-address 10.9.0.0/16
set firewall family inet filter MGMT-IN term ALLOW-NTP from protocol udp
set firewall family inet filter MGMT-IN term ALLOW-NTP then accept
set firewall family inet filter MGMT-IN term DROP-REST then discard
set protocols bgp group EXT neighbor 10.5.5.5 peer-as 65001
"""


class TestJuniperHierarchyFilters:
    def test_filters_are_extracted(self):
        from tools.network.config_parser import parse_juniper

        acls = parse_juniper(JUNOS_HIER)["acls"]
        assert [a["name"] for a in acls] == ["PROTECT-RE", "COUNT-ICMP"]

    def test_one_entry_per_term_with_nested_from_flattened(self):
        from tools.network.config_parser import parse_juniper

        protect = next(a for a in parse_juniper(JUNOS_HIER)["acls"] if a["name"] == "PROTECT-RE")
        assert len(protect["entries"]) == 2
        allow = protect["entries"][0]
        assert allow.startswith("term ALLOW-SSH")
        # nested `from { source-address { ... } }` must survive flattening
        assert "10.0.0.0/8" in allow
        assert "destination-port 22" in allow
        assert "then accept" in allow
        assert protect["entries"][1].startswith("term DENY-ALL")

    def test_interface_filter_application_is_not_a_definition(self):
        """`family inet { filter { input PROTECT-RE; } }` applies a filter.

        Treating it as a definition would invent a phantom ACL.
        """
        from tools.network.config_parser import parse_juniper

        names = [a["name"] for a in parse_juniper(JUNOS_HIER)["acls"]]
        assert "input" not in names
        assert len(names) == 2


class TestJuniperSetFilters:
    def test_set_syntax_groups_fragments_per_term(self):
        from tools.network.config_parser import parse_juniper

        acls = parse_juniper(JUNOS_SET)["acls"]
        assert [a["name"] for a in acls] == ["MGMT-IN"]
        entries = acls[0]["entries"]
        # 4 set lines collapse to 2 terms, not 4 entries
        assert len(entries) == 2
        assert entries[0].startswith("term ALLOW-NTP")
        assert "10.9.0.0/16" in entries[0] and "protocol udp" in entries[0] and "then accept" in entries[0]
        assert entries[1] == "term DROP-REST then discard"

    def test_set_syntax_still_parses_the_rest(self):
        from tools.network.config_parser import parse_juniper

        out = parse_juniper(JUNOS_SET)
        assert out["hostname"] == "EDGE-MX304-02"
        assert out["bgp_neighbors"] == [{"ip": "10.5.5.5", "asn": 65001}]


class TestContract:
    def test_juniper_returns_the_shared_shape(self):
        from tools.network.config_parser import parse_juniper

        out = parse_juniper(JUNOS_HIER)
        for key in ("hostname", "vendor", "interfaces", "acls", "routes", "bgp_neighbors"):
            assert key in out, f"parse_config consumers rely on {key}"
        assert out["vendor"] == "juniper"

    def test_acls_shape_matches_the_ios_parser(self):
        """detect_drift compares across vendors, so the shape must be identical."""
        from tools.network.config_parser import parse_cisco_ios, parse_juniper

        ios = parse_cisco_ios(
            "hostname RTR1\n"
            "ip access-list extended BLOCK\n"
            " permit tcp any any eq 443\n"
            " deny ip any any\n"
        )["acls"]
        junos = parse_juniper(JUNOS_HIER)["acls"]
        assert ios and junos
        assert set(ios[0]) == set(junos[0]) == {"name", "entries"}
        assert all(isinstance(e, str) for e in junos[0]["entries"])

    def test_empty_and_filterless_configs_do_not_crash(self):
        from tools.network.config_parser import parse_juniper

        assert parse_juniper("")["acls"] == []
        assert parse_juniper("set system host-name X")["acls"] == []

    def test_unbalanced_braces_degrade_rather_than_raise(self):
        """A truncated config must not crash a scan sweep."""
        from tools.network.config_parser import parse_juniper

        truncated = "firewall {\n  family inet {\n    filter HALF {\n      term T {\n        then accept;\n"
        parse_juniper(truncated)  # must not raise


class TestVendorDispatchUnaffected:
    def test_detect_vendor_and_parse_config_still_route_correctly(self):
        from tools.network.config_parser import detect_vendor, parse_config

        assert parse_config(JUNOS_SET)["vendor"] == "juniper"
        ios_out = parse_config("hostname RTR1\ninterface GigabitEthernet0/0\n ip address 10.0.0.1 255.255.255.0\n")
        assert ios_out["vendor"] == "cisco_ios"
        assert ios_out["acls"] == []
        assert detect_vendor(JUNOS_SET) == "juniper"
