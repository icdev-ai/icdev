# [CUI // SP-CTI]
"""ZIG External Adapter — ingest external scan results → ZIG activity completions.

Five ingest methods map tool output to ZIG activities for a given target:
  ingest_sbom(target_id, cyclonedx_json)   — CycloneDX SBOM JSON
  ingest_sast(target_id, bandit_json)      — Bandit SAST JSON output
  ingest_survey(target_id, survey_json)    — Security survey response dict
  ingest_nmap(target_id, nmap_xml)         — Nmap XML output string
  ingest_openapi(target_id, openapi_yaml)  — OpenAPI spec YAML/JSON string

Each method returns a summary dict:
  {"source": str, "target_id": str, "activities_updated": [...], "findings": int}
"""

import json
from datetime import datetime, timezone

# Safe XML parsing: defusedxml disables entity expansion (billion-laughs) and
# external entity resolution (XXE). It is already a project dependency
# (requirements.txt). ParseError is re-exported so callers can catch it.
from defusedxml.ElementTree import fromstring as _safe_fromstring
from xml.etree.ElementTree import ParseError

from tools.security_canvas.zig_activity_tracker import set_activity_status
from tools.security_canvas.constants import ZIG_EVIDENCE_MAP


# Cap the scan window for the cheap pre-parse guard; DOCTYPE/ENTITY markers
# in a well-formed XML prolog appear within the first few hundred bytes, but we
# also scan the full payload defensively.
_XML_GUARD_SCAN_BYTES = 65536


def _parse_xml_safe(xml_text):
    """Parse an XML string with entity-expansion / XXE defenses.

    Two layers of defense:
      1. Pre-parse guard — reject any payload containing ``<!DOCTYPE`` or
         ``<!ENTITY`` (case-insensitive) before it ever reaches the parser.
         This blocks billion-laughs entity-expansion DoS and DTD-based XXE
         regardless of parser configuration.
      2. defusedxml.ElementTree.fromstring — forbids entity expansion and
         external entity resolution as a second line of defense.

    Raises:
        ValueError: if a DOCTYPE / ENTITY declaration is present.
        xml.etree.ElementTree.ParseError: if the XML is malformed.
    """
    if xml_text is None:
        raise ValueError("empty XML payload")
    if isinstance(xml_text, bytes):
        probe = xml_text.decode("utf-8", errors="replace")
    else:
        probe = str(xml_text)

    # Scan the leading window (fast path) and, if clean, the full payload.
    head = probe[:_XML_GUARD_SCAN_BYTES].lower()
    lowered = head if len(probe) <= _XML_GUARD_SCAN_BYTES else probe.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError(
            "XML DOCTYPE/ENTITY declarations are not permitted "
            "(entity-expansion / XXE defense)"
        )
    return _safe_fromstring(xml_text)


def _upsert_activity_completion(target_id: str, activity_id: str,
                                evidence_note: str, completed_by: str = "adapter") -> bool:
    """Mark an activity as in_progress for a target via the evidence note.

    Returns True if the activity exists in ZIG and was updated.
    """
    try:
        set_activity_status(
            activity_id,
            status="in_progress",
            target_id=target_id,
            evidence_note=evidence_note,
            completed_by=completed_by,
        )
        return True
    except Exception:
        return False


def _map_finding_to_activities(source: str, finding_key: str) -> list:
    """Return ZIG activity IDs for a given source + finding type."""
    source_map = ZIG_EVIDENCE_MAP.get(source, {})
    activity_id = source_map.get(finding_key)
    return [activity_id] if activity_id else []


def ingest_sbom(target_id: str, cyclonedx_json: str) -> dict:
    """Ingest CycloneDX SBOM JSON and map findings to ZIG activities.

    Detects:
      - Components with known CVEs → zig-act-d08 (SBOM generation)
      - Outdated / risky dependencies → zig-act-p1-21 (SAST/SCA in CI/CD)
    """
    updated = []
    findings = 0
    now_note = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        data = json.loads(cyclonedx_json) if isinstance(cyclonedx_json, str) else cyclonedx_json
    except (json.JSONDecodeError, TypeError):
        return {"source": "sbom", "target_id": target_id,
                "activities_updated": [], "findings": 0, "error": "invalid JSON"}

    if not isinstance(data, dict):
        return {"source": "sbom", "target_id": target_id,
                "activities_updated": [], "findings": 0, "error": "not a mapping"}

    components = data.get("components", [])
    if not components:
        _upsert_activity_completion(
            target_id, "zig-act-d08",
            f"SBOM ingested {now_note} — no components found (empty SBOM)",
        )
        updated.append("zig-act-d08")

    for comp in components:
        if not isinstance(comp, dict):
            continue
        vulns = comp.get("vulnerabilities", []) or []
        for vuln in vulns:
            sev = (vuln.get("ratings", [{}]) or [{}])[0].get("severity", "").lower()
            if sev in ("critical", "high"):
                key = "cve_critical" if sev == "critical" else "cve_high"
                for act_id in _map_finding_to_activities("sbom", key):
                    note = (f"SBOM {now_note}: {comp.get('name','?')} "
                            f"CVE-{sev} {vuln.get('id','?')}")
                    if _upsert_activity_completion(target_id, act_id, note) and act_id not in updated:
                        updated.append(act_id)
                findings += 1

        # Risky licenses or missing version hints at supply chain gap
        if not comp.get("version"):
            for act_id in _map_finding_to_activities("sbom", "outdated_dep"):
                note = f"SBOM {now_note}: {comp.get('name','?')} missing version"
                if _upsert_activity_completion(target_id, act_id, note) and act_id not in updated:
                    updated.append(act_id)

    if components and not updated:
        # SBOM exists and is clean — mark SBOM activity positively
        _upsert_activity_completion(
            target_id, "zig-act-d08",
            f"SBOM ingested {now_note} — {len(components)} components, no high/critical CVEs",
        )
        updated.append("zig-act-d08")

    return {"source": "sbom", "target_id": target_id,
            "activities_updated": updated, "findings": findings}


def ingest_sast(target_id: str, bandit_json: str) -> dict:
    """Ingest Bandit SAST JSON output and map test IDs to ZIG activities.

    Bandit test ID → ZIG activity mapping (from ZIG_EVIDENCE_MAP):
      B105/B106 (hardcoded secret)  → zig-act-p1-29 (encryption at rest)
      B502/B503 (weak TLS)          → zig-act-p1-18 (encrypt traffic in transit)
      B608 (SQL injection)          → zig-act-p1-21 (SAST/SCA in CI/CD)
    """
    updated = []
    findings = 0
    now_note = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        data = json.loads(bandit_json) if isinstance(bandit_json, str) else bandit_json
    except (json.JSONDecodeError, TypeError):
        return {"source": "sast", "target_id": target_id,
                "activities_updated": [], "findings": 0, "error": "invalid JSON"}

    if not isinstance(data, dict):
        return {"source": "sast", "target_id": target_id,
                "activities_updated": [], "findings": 0, "error": "not a mapping"}

    results = data.get("results", [])
    for finding in results:
        if not isinstance(finding, dict):
            continue
        test_id = finding.get("test_id", "")
        severity = finding.get("issue_severity", "").upper()
        if severity not in ("HIGH", "MEDIUM"):
            continue

        for act_id in _map_finding_to_activities("sast", test_id):
            note = (f"SAST {now_note}: {test_id} {severity} in "
                    f"{finding.get('filename','?')}:{finding.get('line_number','?')}")
            if _upsert_activity_completion(target_id, act_id, note) and act_id not in updated:
                updated.append(act_id)
            findings += 1

    return {"source": "sast", "target_id": target_id,
            "activities_updated": updated, "findings": findings}


def ingest_survey(target_id: str, survey_json: str) -> dict:
    """Ingest a security survey response dict and map answers to ZIG activities.

    Expected survey keys (bool values):
      mfa, mfa_admin, rbac, pam, least_priv, lifecycle
    """
    updated = []
    findings = 0
    now_note = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        data = json.loads(survey_json) if isinstance(survey_json, str) else survey_json
    except (json.JSONDecodeError, TypeError):
        return {"source": "survey", "target_id": target_id,
                "activities_updated": [], "findings": 0, "error": "invalid JSON"}

    if not isinstance(data, dict):
        return {"source": "survey", "target_id": target_id,
                "activities_updated": [], "findings": 0, "error": "not a mapping"}

    for key, enabled in data.items():
        for act_id in _map_finding_to_activities("survey", key):
            note = (f"Survey {now_note}: {key}={'true' if enabled else 'false'} "
                    "— marked complete" if enabled else "— gap identified, in_progress")
            if _upsert_activity_completion(target_id, act_id, note):
                if enabled:
                    # Promote to complete for confirmed controls
                    set_activity_status(act_id, "complete",
                                        target_id=target_id, evidence_note=note,
                                        completed_by="survey")
                if act_id not in updated:
                    updated.append(act_id)
            findings += 1

    return {"source": "survey", "target_id": target_id,
            "activities_updated": updated, "findings": findings}


def ingest_nmap(target_id: str, nmap_xml: str) -> dict:
    """Ingest Nmap XML output and map network findings to ZIG activities.

    Detects:
      - Port 80 open without 443  → zig-act-p1-18 (encrypt traffic in transit)
      - Admin ports (22, 3389, 5985) reachable → zig-act-p1-16 (macro-segmentation)
      - API ports (8080, 8443) without TLS → zig-act-p2-15 (mTLS enforcement)
    """
    updated = []
    findings = 0
    now_note = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        root = _parse_xml_safe(nmap_xml)
    except (ParseError, ValueError) as exc:
        # ParseError → malformed XML; ValueError → entity-expansion / DTD guard
        # (defusedxml raises DefusedXmlException, a ValueError subclass).
        return {"source": "nmap", "target_id": target_id,
                "activities_updated": [], "findings": 0, "error": str(exc)}

    for host in root.findall(".//host"):
        open_ports = set()
        for port in host.findall(".//port"):
            state = port.find("state")
            if state is not None and state.get("state") == "open":
                try:
                    open_ports.add(int(port.get("portid", 0)))
                except ValueError:
                    pass

        # HTTP without HTTPS
        if 80 in open_ports and 443 not in open_ports:
            for act_id in _map_finding_to_activities("nmap", "http_without_https"):
                note = f"Nmap {now_note}: port 80 open without 443 — no HTTPS"
                if _upsert_activity_completion(target_id, act_id, note) and act_id not in updated:
                    updated.append(act_id)
                findings += 1

        # Admin ports exposed
        admin_ports = open_ports & {22, 3389, 5985}
        if admin_ports:
            for act_id in _map_finding_to_activities("nmap", "admin_ports_open"):
                note = f"Nmap {now_note}: admin ports open {sorted(admin_ports)}"
                if _upsert_activity_completion(target_id, act_id, note) and act_id not in updated:
                    updated.append(act_id)
                findings += 1

        # API ports without TLS (8080 open but 8443 not)
        if 8080 in open_ports and 8443 not in open_ports:
            for act_id in _map_finding_to_activities("nmap", "no_tls_on_api"):
                note = f"Nmap {now_note}: API port 8080 open without 8443 (no mTLS)"
                if _upsert_activity_completion(target_id, act_id, note) and act_id not in updated:
                    updated.append(act_id)
                findings += 1

    return {"source": "nmap", "target_id": target_id,
            "activities_updated": updated, "findings": findings}


def ingest_openapi(target_id: str, openapi_yaml: str) -> dict:
    """Ingest OpenAPI spec (YAML or JSON) and map security gaps to ZIG activities.

    Detects:
      - No securitySchemes defined → zig-act-p2-19 (context-based access control)
      - HTTP (not HTTPS) server URLs → zig-act-p1-18 (encrypt traffic in transit)
    """
    updated = []
    findings = 0
    now_note = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        try:
            import yaml
            data = yaml.safe_load(openapi_yaml)
        except ImportError:
            data = json.loads(openapi_yaml)
    except Exception as exc:
        return {"source": "openapi", "target_id": target_id,
                "activities_updated": [], "findings": 0, "error": str(exc)}

    if not isinstance(data, dict):
        return {"source": "openapi", "target_id": target_id,
                "activities_updated": [], "findings": 0, "error": "not a mapping"}

    # No security schemes
    components = data.get("components", {}) or {}
    schemes = components.get("securitySchemes", {}) or {}
    global_sec = data.get("security", [])
    if not schemes and not global_sec:
        for act_id in _map_finding_to_activities("openapi", "no_security_scheme"):
            note = f"OpenAPI {now_note}: no securitySchemes or global security defined"
            if _upsert_activity_completion(target_id, act_id, note) and act_id not in updated:
                updated.append(act_id)
            findings += 1

    # HTTP-only server URLs
    for server in data.get("servers", []):
        url = server.get("url", "")
        if url.startswith("http://"):
            for act_id in _map_finding_to_activities("openapi", "http_only"):
                note = f"OpenAPI {now_note}: HTTP-only server URL: {url}"
                if _upsert_activity_completion(target_id, act_id, note) and act_id not in updated:
                    updated.append(act_id)
                findings += 1

    return {"source": "openapi", "target_id": target_id,
            "activities_updated": updated, "findings": findings}
