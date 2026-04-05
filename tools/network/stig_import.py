# [CUI // SP-CTI]
"""ICDEV™ Network Design Canvas — STIG XCCDF/CKL Import Engine.

Parses DISA STIG Viewer .ckl files and XCCDF benchmark results,
matches hostnames to canvas devices, and returns per-device compliance
status (green/yellow/red) for overlay coloring.

No Flask dependency — takes file content and graph data, returns results.
"""

from defusedxml.ElementTree import fromstring as safe_fromstring
from typing import Any


# ── Severity mapping ─────────────────────────────────────────────────────────

# CKL status values → normalized status
CKL_STATUS_MAP = {
    "NotAFinding": "pass",
    "Open": "fail",
    "Not_Applicable": "na",
    "Not_Reviewed": "nr",
}

# XCCDF result values → normalized status
XCCDF_RESULT_MAP = {
    "pass": "pass",
    "fail": "fail",
    "error": "fail",
    "unknown": "nr",
    "notapplicable": "na",
    "notchecked": "nr",
    "notselected": "na",
    "informational": "na",
    "fixed": "pass",
}

# STIG severity → CAT mapping
SEVERITY_TO_CAT = {
    "high": "CAT1",
    "medium": "CAT2",
    "low": "CAT3",
}


def detect_format(content: str) -> str:
    """Detect whether content is CKL or XCCDF format.

    Returns 'ckl', 'xccdf', or 'unknown'.
    """
    # CKL files have a <CHECKLIST> root
    if "<CHECKLIST>" in content[:2000]:
        return "ckl"
    # XCCDF results have Benchmark or TestResult elements
    if "<Benchmark" in content[:2000] or "<TestResult" in content[:5000]:
        return "xccdf"
    return "unknown"


def parse_ckl(content: str) -> dict[str, Any]:
    """Parse a DISA STIG Viewer .ckl file.

    Returns:
        {
            "hosts": {
                "hostname1": {
                    "ip": "...",
                    "mac": "...",
                    "fqdn": "...",
                    "findings": [
                        {"vuln_id": "V-xxxxx", "rule_id": "SV-xxxxx",
                         "stig_id": "...", "severity": "CAT1",
                         "title": "...", "status": "fail",
                         "finding_details": "...", "comments": "..."},
                        ...
                    ],
                    "summary": {"pass": N, "fail": N, "na": N, "nr": N}
                }
            },
            "stig_name": "...",
            "stig_version": "..."
        }
    """
    root = safe_fromstring(content)

    result: dict[str, Any] = {"hosts": {}, "stig_name": "", "stig_version": ""}

    # Extract STIG info from first VULN's STIG_DATA
    stigs = root.findall(".//iSTIG/STIG_INFO/SI_DATA")
    for si in stigs:
        name_el = si.find("SID_NAME")
        val_el = si.find("SID_DATA")
        if name_el is not None and val_el is not None:
            if name_el.text == "title":
                result["stig_name"] = val_el.text or ""
            elif name_el.text == "version":
                result["stig_version"] = val_el.text or ""

    # Extract asset (host) info
    asset = root.find(".//ASSET")
    hostname = ""
    host_info: dict[str, str] = {}
    if asset is not None:
        hostname = (asset.findtext("HOST_NAME") or "").strip()
        host_info = {
            "ip": (asset.findtext("HOST_IP") or "").strip(),
            "mac": (asset.findtext("HOST_MAC") or "").strip(),
            "fqdn": (asset.findtext("HOST_FQDN") or "").strip(),
        }

    if not hostname:
        hostname = "unknown_host"

    findings = []
    summary = {"pass": 0, "fail": 0, "na": 0, "nr": 0}

    for vuln in root.findall(".//VULN"):
        finding: dict[str, str] = {}

        # Extract STIG_DATA attributes
        for sd in vuln.findall("STIG_DATA"):
            attr_name = (sd.findtext("VULN_ATTRIBUTE") or "").strip()
            attr_val = (sd.findtext("ATTRIBUTE_DATA") or "").strip()
            if attr_name == "Vuln_Num":
                finding["vuln_id"] = attr_val
            elif attr_name == "Rule_ID":
                finding["rule_id"] = attr_val
            elif attr_name == "Rule_Ver":
                finding["stig_id"] = attr_val
            elif attr_name == "Severity":
                finding["severity"] = SEVERITY_TO_CAT.get(attr_val, "CAT2")
            elif attr_name == "Rule_Title":
                finding["title"] = attr_val
            elif attr_name == "Group_Title":
                finding.setdefault("title", attr_val)

        # Extract status
        raw_status = (vuln.findtext("STATUS") or "Not_Reviewed").strip()
        finding["status"] = CKL_STATUS_MAP.get(raw_status, "nr")
        finding["finding_details"] = (vuln.findtext("FINDING_DETAILS") or "").strip()
        finding["comments"] = (vuln.findtext("COMMENTS") or "").strip()

        findings.append(finding)
        summary[finding["status"]] = summary.get(finding["status"], 0) + 1

    result["hosts"][hostname] = {
        **host_info,
        "findings": findings,
        "summary": summary,
    }
    return result


def parse_xccdf(content: str) -> dict[str, Any]:
    """Parse an XCCDF benchmark results file.

    Handles both XCCDF 1.1 and 1.2 namespaces, including SCAP output.

    Returns same structure as parse_ckl.
    """
    root = safe_fromstring(content)

    # Detect namespace
    ns = ""
    tag = root.tag
    if "}" in tag:
        ns = tag.split("}")[0] + "}"

    result: dict[str, Any] = {"hosts": {}, "stig_name": "", "stig_version": ""}

    # Benchmark title
    title_el = root.find(f"{ns}title")
    if title_el is not None and title_el.text:
        result["stig_name"] = title_el.text.strip()
    version_el = root.find(f"{ns}version")
    if version_el is not None and version_el.text:
        result["stig_version"] = version_el.text.strip()

    # Find TestResult elements
    test_results = root.findall(f".//{ns}TestResult")
    if not test_results:
        # Maybe the root IS the TestResult (standalone results file)
        if "TestResult" in root.tag:
            test_results = [root]

    for tr in test_results:
        # Extract target hostname
        hostname = (tr.findtext(f"{ns}target") or "").strip()
        if not hostname:
            hostname = "unknown_host"

        # Target address (IP)
        ip_addr = (tr.findtext(f"{ns}target-address") or "").strip()

        findings = []
        summary = {"pass": 0, "fail": 0, "na": 0, "nr": 0}

        for rr in tr.findall(f"{ns}rule-result"):
            rule_ref = rr.get("idref", "")
            severity = rr.get("severity", "medium")
            raw_result = (rr.findtext(f"{ns}result") or "unknown").strip().lower()

            status = XCCDF_RESULT_MAP.get(raw_result, "nr")

            # Try to find rule title from the benchmark
            title = rule_ref
            rule_el = root.find(f".//{ns}Rule[@id='{rule_ref}']")
            if rule_el is not None:
                rule_title = rule_el.findtext(f"{ns}title")
                if rule_title:
                    title = rule_title.strip()

            findings.append(
                {
                    "vuln_id": rule_ref,
                    "rule_id": rule_ref,
                    "stig_id": rule_ref,
                    "severity": SEVERITY_TO_CAT.get(severity, "CAT2"),
                    "title": title,
                    "status": status,
                    "finding_details": "",
                    "comments": "",
                }
            )
            summary[status] = summary.get(status, 0) + 1

        if hostname in result["hosts"]:
            # Multiple TestResults for same host — merge
            result["hosts"][hostname]["findings"].extend(findings)
            for k in summary:
                result["hosts"][hostname]["summary"][k] += summary[k]
        else:
            result["hosts"][hostname] = {
                "ip": ip_addr,
                "mac": "",
                "fqdn": "",
                "findings": findings,
                "summary": summary,
            }

    return result


def match_hosts_to_devices(
    parsed: dict[str, Any],
    graph: dict[str, Any],
) -> dict[str, Any]:
    """Match parsed STIG hostnames to canvas topology nodes.

    Matching strategy (case-insensitive):
    1. Exact label match
    2. Hostname contained in label (or vice versa)
    3. FQDN match
    4. IP address match against label

    Returns:
        {
            "matched": [
                {"node_id": "...", "label": "...", "hostname": "...",
                 "status": "red|yellow|green", "summary": {...},
                 "cat1_count": N, "cat2_count": N, "cat3_count": N,
                 "findings": [...]},
            ],
            "unmatched_hosts": ["hostname1", ...],
            "unmatched_devices": [{"id": "...", "label": "..."}],
            "stig_name": "...",
            "stig_version": "...",
        }
    """
    nodes = graph.get("nodes", [])
    hosts = parsed.get("hosts", {})

    # Build lookup tables for matching
    node_lookup: dict[str, dict] = {}  # lowercase label -> node
    node_by_id: dict[str, dict] = {}
    for n in nodes:
        label = (n.get("label") or n.get("id", "")).strip()
        node_lookup[label.lower()] = n
        node_by_id[n.get("id", "")] = n

    matched = []
    matched_node_ids: set[str] = set()
    matched_hostnames: set[str] = set()

    for hostname, host_data in hosts.items():
        hn_lower = hostname.lower()
        fqdn = (host_data.get("fqdn") or "").lower()
        ip = (host_data.get("ip") or "").lower()

        best_node = None

        # Strategy 1: exact label match
        if hn_lower in node_lookup:
            best_node = node_lookup[hn_lower]
        else:
            # Strategy 2: partial match (hostname in label or label in hostname)
            for label_lower, node in node_lookup.items():
                if node.get("id") in matched_node_ids:
                    continue
                if hn_lower in label_lower or label_lower in hn_lower:
                    best_node = node
                    break
            # Strategy 3: FQDN match
            if not best_node and fqdn:
                for label_lower, node in node_lookup.items():
                    if node.get("id") in matched_node_ids:
                        continue
                    if fqdn in label_lower or label_lower in fqdn:
                        best_node = node
                        break
            # Strategy 4: IP match
            if not best_node and ip:
                for label_lower, node in node_lookup.items():
                    if node.get("id") in matched_node_ids:
                        continue
                    if ip == label_lower:
                        best_node = node
                        break

        if best_node:
            node_id = best_node.get("id", "")
            matched_node_ids.add(node_id)
            matched_hostnames.add(hostname)

            # Compute compliance color
            fail_findings = [f for f in host_data["findings"] if f["status"] == "fail"]
            cat1_count = sum(1 for f in fail_findings if f.get("severity") == "CAT1")
            cat2_count = sum(1 for f in fail_findings if f.get("severity") == "CAT2")
            cat3_count = sum(1 for f in fail_findings if f.get("severity") == "CAT3")

            if cat1_count > 0:
                status = "red"
            elif cat2_count > 0:
                status = "yellow"
            elif cat3_count > 0:
                status = "yellow"
            elif len(fail_findings) > 0:
                status = "yellow"
            else:
                status = "green"

            matched.append(
                {
                    "node_id": node_id,
                    "label": best_node.get("label", node_id),
                    "hostname": hostname,
                    "status": status,
                    "summary": host_data["summary"],
                    "cat1_count": cat1_count,
                    "cat2_count": cat2_count,
                    "cat3_count": cat3_count,
                    "findings": fail_findings,  # Only include failures for overlay
                }
            )

    # Unmatched
    unmatched_hosts = [h for h in hosts if h not in matched_hostnames]
    unmatched_devices = [
        {"id": n.get("id", ""), "label": n.get("label", n.get("id", ""))}
        for n in nodes
        if n.get("id") not in matched_node_ids
        # Exclude drawing shapes (rectangles, text, etc.)
        and n.get("type", "")
        not in (
            "rectangle",
            "circle",
            "ellipse",
            "diamond",
            "triangle",
            "hexagon",
            "star",
            "arrow",
            "text",
        )
    ]

    return {
        "matched": matched,
        "unmatched_hosts": unmatched_hosts,
        "unmatched_devices": unmatched_devices,
        "stig_name": parsed.get("stig_name", ""),
        "stig_version": parsed.get("stig_version", ""),
    }


def import_stig_file(content: str, graph: dict[str, Any]) -> dict[str, Any]:
    """Main entry point: parse a STIG file and match to topology devices.

    Args:
        content: Raw XML content of .ckl or XCCDF file.
        graph: Topology graph_json dict with nodes/edges.

    Returns:
        Full import result with matched devices, colors, and findings.
    """
    fmt = detect_format(content)
    if fmt == "unknown":
        return {"error": "Unrecognized file format. Expected .ckl or XCCDF XML."}

    if fmt == "ckl":
        parsed = parse_ckl(content)
    else:
        parsed = parse_xccdf(content)

    if not parsed["hosts"]:
        return {"error": "No host data found in the uploaded file."}

    result = match_hosts_to_devices(parsed, graph)
    result["format"] = fmt
    result["total_hosts"] = len(parsed["hosts"])
    result["total_matched"] = len(result["matched"])

    # Compute overall stats
    total_findings = 0
    total_pass = 0
    total_fail = 0
    for host_data in parsed["hosts"].values():
        total_findings += len(host_data["findings"])
        total_pass += host_data["summary"].get("pass", 0)
        total_fail += host_data["summary"].get("fail", 0)

    result["total_findings"] = total_findings
    result["total_pass"] = total_pass
    result["total_fail"] = total_fail

    return result
