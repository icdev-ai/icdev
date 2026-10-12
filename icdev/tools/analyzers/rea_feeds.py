#!/usr/bin/env python3
# CUI // SP-CTI
"""REA evidence -> supply chain + attack surface feeds (rea-sec-01, project card ``rea``).

WHAT THIS IS. The ONE place that knows how an REA evidence record is shaped, so
the four consumers this card wires never parse REA themselves:

    SBOM hints        tools/compliance/binary_components.rea_hinted_components
    attack surface    tools/network/attack_surface_mapper.map_artifact_surface
    SCRM              tools/supply_chain/scrm_assessor.assess_artifact_dependencies
    ATO evidence      tools/compliance/evidence_collector.collect_evidence(rea_reports=)

Its input is a report from ``tools/analyzers/rea_adapter.analyze`` (or the same
shape loaded from JSON). Nothing here opens the target, starts REA or touches
the network: it reads what REA already OBSERVED.

EVERY OUTPUT IS AN OBSERVATION, NEVER AN ASSERTION. Each item carries
``basis: observed``, ``confidence: unasserted`` and the provenance that admits
it -- the REA ``evidence_id``, the target sha256 and the operator's
authorization basis. A report missing either of the last two is REFUSED, not
fed: a feed with no record of what was analysed, or on whose authority, is the
rail rea-wire-02 enforces at the front door, re-checked at this one. Nothing
here moves a signature verdict (the ``artifact_corroboration`` rule): the ATO
block is attached BESIDE an evidence record and refuses to overwrite.

AN UNMEASURED LIST IS ``None``, NEVER ``[]``. REA's summary detail reports
COUNTS (``literal_channels: 3``) without the names. A count of zero is a
measured zero and gives ``[]``; a non-zero count with no list gives ``None``
and ``unmeasured[kind]`` names why, so "no IPC channels" and "IPC channels we
could not see" never read alike.

WHICH SHAPES ARE MEASURED. rea-spike-01 recorded real REA 6.4.0 output for
.NET (``inspect_managed_artifact``: ``references[]``,
``metadata.table_row_counts``), JavaScript (``analyze_javascript_application``:
``summary.electron.ipc`` counts, ``graph.nodes[]`` source modules,
``semantic_graph.nodes[]`` of kind ``request``) and HAR
(``inspect_web_network_capture``: ``records[].reported.request``). Three keys
were NOT measured, because no spike fixture carried them, and each is read
only when present: ``electron.ipc.channels[]`` (the fixture app was not
Electron), ``module_references[]`` on .NET (the fixture had one ModuleRef row
and REA lists none by name), and the whole APK shape (``manifest`` /
``native_libraries`` -- REA's JADX path is not on the rea-wire-01 allowlist, so
an APK report reaches here only from an exported bundle). They are listed in
``ASSUMED_SHAPES`` so the day a recording contradicts one is a test failure,
not a silent empty feed.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

__all__ = [
    "ASSUMED_SHAPES",
    "BASIS_OBSERVED",
    "CONFIDENCE_UNASSERTED",
    "admit",
    "ato_provenance",
    "attach_ato_provenance",
    "attack_surface",
    "dependency_observations",
    "observation_provenance",
    "sbom_observations",
]

BASIS_OBSERVED = "observed"
#: The binary_components value, so an SBOM reader sees one vocabulary.
CONFIDENCE_UNASSERTED = "unasserted"

AUTHORIZATION_BASES: Tuple[str, ...] = ("owned", "licensed", "customer-authorized")
FEEDABLE_STATUSES: Tuple[str, ...] = ("ok", "partial")

#: Why a list is ``None``. Each sends a reader to a different repair.
UNMEASURED_COUNT_ONLY = "count_only"            # REA counted them and named none
UNMEASURED_SUMMARY_DETAIL = "summary_detail"    # the graph is only in detail=full
UNMEASURED_NOT_REPORTED = "not_reported_by_tool"

#: Keys read only when present -- never measured on a real REA 6.4.0 run.
ASSUMED_SHAPES: Tuple[str, ...] = (
    "analyze_javascript_application: summary.electron.ipc.channels[]",
    "analyze_javascript_application: semantic_graph.nodes[kind=route]",
    "inspect_managed_artifact: module_references[]",
    "apk: manifest.permissions[] / manifest.components[] / native_libraries[]",
)

ATO_RESULT_KEY = "rea_provenance"

_NODE_MODULES = re.compile(r"node_modules/((?:@[^/]+/)?[^/]+)/")


# ---------------------------------------------------------------------------
# Admission + provenance
# ---------------------------------------------------------------------------

def admit(report: Any) -> Optional[str]:
    """``None`` when *report* may be fed, else the refusal reason."""
    if not isinstance(report, Mapping) or report.get("analyzer") != "rea":
        return "not_a_rea_report"
    status = report.get("status")
    if status not in FEEDABLE_STATUSES:
        return "report_status_%s" % status
    prov = report.get("provenance") or {}
    if not prov.get("target_sha256"):
        return "provenance_missing_target_sha256"
    if prov.get("authorization_basis") not in AUTHORIZATION_BASES:
        return "authorization_missing"
    return None


def observation_provenance(report: Mapping[str, Any], entry: Mapping[str, Any]) -> Dict[str, Any]:
    """What admits one observation: its evidence id, the target, the authority."""
    prov = report.get("provenance") or {}
    return {
        "source": "rea",
        "evidence_id": entry.get("evidence_id"),
        "tool": entry.get("tool"),
        "family": report.get("family"),
        "target": report.get("target"),
        "target_sha256": prov.get("target_sha256"),
        "authorization_basis": prov.get("authorization_basis"),
        "analysis": prov.get("analysis", "local"),
        "rea_version": report.get("rea_version"),
        "report_status": report.get("status"),
    }


def _entries(report: Mapping[str, Any], tool: str) -> List[Mapping[str, Any]]:
    return [e for e in report.get("body") or []
            if e.get("tool") == tool and isinstance(e.get("normalized_result"), Mapping)]


def _family(report: Mapping[str, Any]) -> str:
    return str(report.get("family") or "")


def _refused(reason: str) -> Dict[str, Any]:
    return {"status": "refused", "reason": reason, "items": None, "unmeasured": {}}


def _result(items: List[Dict[str, Any]], unmeasured: Dict[str, str]) -> Dict[str, Any]:
    return {"status": "ok", "reason": None, "items": items, "unmeasured": unmeasured}


# ---------------------------------------------------------------------------
# Shape readers (one per REA operation)
# ---------------------------------------------------------------------------

def _dotnet(nr: Mapping[str, Any]) -> Tuple[Optional[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]], Dict[str, str]]:
    """``(managed references, native module references, unmeasured)``."""
    unmeasured: Dict[str, str] = {}
    managed: Optional[List[Dict[str, Any]]] = None
    if isinstance(nr.get("references"), list):
        managed = []
        for ref in nr["references"]:
            if not isinstance(ref, Mapping) or not ref.get("name"):
                continue
            key = ref.get("public_key_or_token") or {}
            managed.append({"name": str(ref["name"]), "version": ref.get("version"),
                            "public_key_token": key.get("token") if isinstance(key, Mapping) else None})
    else:
        unmeasured["managed_assembly"] = UNMEASURED_NOT_REPORTED

    native: Optional[List[Dict[str, Any]]] = None
    if isinstance(nr.get("module_references"), list):            # ASSUMED_SHAPES
        native = [{"name": str(m.get("name") if isinstance(m, Mapping) else m)}
                  for m in nr["module_references"]
                  if (m.get("name") if isinstance(m, Mapping) else m)]
    else:
        counts = ((nr.get("metadata") or {}).get("table_row_counts")
                  if isinstance(nr.get("metadata"), Mapping) else None)
        if isinstance(counts, Mapping):
            if int(counts.get("ModuleRef") or 0) == 0:
                native = []                                     # a measured zero
            else:
                unmeasured["native_module"] = "%s:ModuleRef=%s" % (
                    UNMEASURED_COUNT_ONLY, counts.get("ModuleRef"))
        else:
            unmeasured["native_module"] = UNMEASURED_NOT_REPORTED
    return managed, native, unmeasured


def _electron(nr: Mapping[str, Any]) -> Mapping[str, Any]:
    summary = nr.get("summary") if isinstance(nr.get("summary"), Mapping) else {}
    # detail=summary nests it one level down; detail=full puts it at summary.
    electron = summary.get("electron") if isinstance(summary.get("electron"), Mapping) else summary
    return electron if isinstance(electron, Mapping) else {}


def _ipc_channels(nr: Mapping[str, Any]) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    ipc = _electron(nr).get("ipc")
    if not isinstance(ipc, Mapping):
        return None, UNMEASURED_NOT_REPORTED
    if isinstance(ipc.get("channels"), list):                   # ASSUMED_SHAPES
        return [{"channel": str(c.get("channel")), "direction": c.get("direction"),
                 "operation": c.get("operation")}
                for c in ipc["channels"] if isinstance(c, Mapping) and c.get("channel")], None
    if not int(ipc.get("operations") or 0) and not int(ipc.get("literal_channels") or 0):
        return [], None
    return None, "%s:literal_channels=%s" % (UNMEASURED_COUNT_ONLY, ipc.get("literal_channels"))


def _semantic_nodes(nr: Mapping[str, Any], kind: str) -> Optional[List[Mapping[str, Any]]]:
    graph = nr.get("semantic_graph")
    if not isinstance(graph, Mapping) or not isinstance(graph.get("nodes"), list):
        return None
    return [n for n in graph["nodes"] if isinstance(n, Mapping) and n.get("kind") == kind]


def _js_packages(nr: Mapping[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """npm packages a bundle's source map attributes code to (``node_modules/<pkg>/``)."""
    graph = nr.get("graph")
    if not isinstance(graph, Mapping) or not isinstance(graph.get("nodes"), list):
        return None
    seen: Dict[str, Dict[str, Any]] = {}
    for node in graph["nodes"]:
        ident = node.get("identity") if isinstance(node, Mapping) else None
        source = str((ident or {}).get("original_source") or "").replace("\\", "/")
        match = _NODE_MODULES.search(source)
        if match and match.group(1) not in seen:
            seen[match.group(1)] = {"name": match.group(1), "evidence_string": source}
    return list(seen.values())


def _har_endpoints(nr: Mapping[str, Any]) -> Optional[List[Dict[str, Any]]]:
    records = nr.get("records")
    if not isinstance(records, list):
        return None
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for rec in records:
        reported = rec.get("reported") if isinstance(rec, Mapping) else None
        request = (reported or {}).get("request") if isinstance(reported, Mapping) else None
        if not isinstance(request, Mapping) or not request.get("url"):
            continue
        parts = urlsplit(str(request["url"]))
        # The query is DROPPED: REA redacts known credentials, and says unknown
        # parameter names do not establish sensitivity -- so a query is not safe
        # to carry into a surface map that leaves the analysis host.
        endpoint = "%s://%s%s" % (parts.scheme, parts.netloc, parts.path or "/")
        method = str(request.get("method") or "GET").upper()
        status = ((reported.get("response") or {}).get("status")
                  if isinstance(reported.get("response"), Mapping) else None)
        item = out.setdefault((method, endpoint), {"method": method, "endpoint": endpoint,
                                                   "statuses": [], "observations": 0,
                                                   "query_dropped": False})
        item["observations"] += 1
        item["query_dropped"] = item["query_dropped"] or bool(parts.query)
        if status is not None and status not in item["statuses"]:
            item["statuses"].append(status)
    return list(out.values())


def _apk(nr: Mapping[str, Any]) -> Mapping[str, Any]:
    manifest = nr.get("manifest")
    return manifest if isinstance(manifest, Mapping) else {}


# ---------------------------------------------------------------------------
# Feed 1: SBOM observations
# ---------------------------------------------------------------------------

def sbom_observations(report: Any) -> Dict[str, Any]:
    """Components REA observed in the artifact. Never raises.

    ``items``: ``{name, version, kind, evidence_string, provenance}``;
    ``version`` is ``None`` when the artifact did not carry one.
    """
    refused = admit(report)
    if refused:
        return _refused(refused)
    items: List[Dict[str, Any]] = []
    unmeasured: Dict[str, str] = {}
    family = _family(report)

    if family == "dotnet_assembly":
        for entry in _entries(report, "inspect_managed_artifact"):
            managed, native, gaps = _dotnet(entry["normalized_result"])
            unmeasured.update(gaps)
            for ref in managed or []:
                items.append({"name": ref["name"], "version": ref["version"],
                              "kind": "managed_assembly_reference",
                              "evidence_string": "AssemblyRef %s %s" % (ref["name"], ref["version"]),
                              "provenance": observation_provenance(report, entry)})
            for mod in native or []:
                items.append({"name": mod["name"], "version": None,
                              "kind": "native_module_reference",
                              "evidence_string": "ModuleRef %s" % mod["name"],
                              "provenance": observation_provenance(report, entry)})
    elif family == "js_bundle":
        for entry in _entries(report, "analyze_javascript_application"):
            packages = _js_packages(entry["normalized_result"])
            if packages is None:
                unmeasured["npm_package"] = UNMEASURED_SUMMARY_DETAIL
            for pkg in packages or []:
                items.append({"name": pkg["name"], "version": None, "kind": "npm_package",
                              "evidence_string": pkg["evidence_string"],
                              "provenance": observation_provenance(report, entry)})
    elif family == "apk":
        for entry in report.get("body") or []:
            libs = (entry.get("normalized_result") or {}).get("native_libraries")
            if not isinstance(libs, list):
                unmeasured["native_library"] = UNMEASURED_NOT_REPORTED
                continue
            for lib in libs:
                path = str(lib.get("path") if isinstance(lib, Mapping) else lib)
                items.append({"name": path.rsplit("/", 1)[-1], "version": None,
                              "kind": "native_library", "evidence_string": path,
                              "provenance": observation_provenance(report, entry)})
    return _result(items, unmeasured)


# ---------------------------------------------------------------------------
# Feed 2: attack surface
# ---------------------------------------------------------------------------

def _surface(kind: str, name: str, exposure: str, detail: Dict[str, Any],
             report: Mapping[str, Any], entry: Mapping[str, Any]) -> Dict[str, Any]:
    return {"surface_kind": kind, "name": name, "exposure": exposure, "detail": detail,
            "basis": BASIS_OBSERVED, "confidence": CONFIDENCE_UNASSERTED,
            "provenance": observation_provenance(report, entry)}


def attack_surface(report: Any) -> Dict[str, Any]:
    """Entry points REA observed: Electron IPC, routes/requests, HAR endpoints, APK permissions."""
    refused = admit(report)
    if refused:
        return _refused(refused)
    items: List[Dict[str, Any]] = []
    unmeasured: Dict[str, str] = {}
    family = _family(report)

    if family == "js_bundle":
        for entry in _entries(report, "analyze_javascript_application"):
            nr = entry["normalized_result"]
            channels, why = _ipc_channels(nr)
            if channels is None:
                unmeasured["electron_ipc_channel"] = why or UNMEASURED_NOT_REPORTED
            for ch in channels or []:
                items.append(_surface("electron_ipc_channel", ch["channel"], "local_ipc",
                                      {"direction": ch["direction"], "operation": ch["operation"]},
                                      report, entry))
            requests = _semantic_nodes(nr, "request")
            if requests is None:
                unmeasured["client_request"] = UNMEASURED_SUMMARY_DETAIL
            unresolved = 0
            for node in requests or []:
                props = node.get("properties") or {}
                if not props.get("endpoint"):
                    unresolved += 1
                    continue
                items.append(_surface("client_request", str(props["endpoint"]), "outbound_network",
                                      {"method": props.get("method")}, report, entry))
            if unresolved:
                unmeasured["client_request_endpoint"] = "dynamic_endpoint:%d" % unresolved
            for node in _semantic_nodes(nr, "route") or []:                 # ASSUMED_SHAPES
                props = node.get("properties") or {}
                items.append(_surface("route", str(props.get("path") or node.get("label")),
                                      "inbound_network", {"method": props.get("method")},
                                      report, entry))
    elif family == "har":
        for entry in _entries(report, "inspect_web_network_capture"):
            endpoints = _har_endpoints(entry["normalized_result"])
            if endpoints is None:
                unmeasured["http_endpoint"] = UNMEASURED_NOT_REPORTED
            for ep in endpoints or []:
                items.append(_surface("http_endpoint", "%s %s" % (ep["method"], ep["endpoint"]),
                                      "network", ep, report, entry))
    elif family == "apk":
        for entry in report.get("body") or []:
            manifest = _apk(entry.get("normalized_result") or {})
            perms = manifest.get("permissions")
            if not isinstance(perms, list):
                unmeasured["android_permission"] = UNMEASURED_NOT_REPORTED
            for perm in perms or []:
                name = perm.get("name") if isinstance(perm, Mapping) else perm
                if name:
                    items.append(_surface("android_permission", str(name), "device_capability",
                                          {"package": manifest.get("package")}, report, entry))
            comps = manifest.get("components")
            if not isinstance(comps, list):
                unmeasured["android_exported_component"] = UNMEASURED_NOT_REPORTED
            for comp in comps or []:
                if isinstance(comp, Mapping) and comp.get("exported") is True:
                    items.append(_surface("android_exported_component", str(comp.get("name")),
                                          "inter_app_ipc", {"kind": comp.get("kind"),
                                                            "permission": comp.get("permission")},
                                          report, entry))
    return _result(items, unmeasured)


# ---------------------------------------------------------------------------
# Feed 3: SCRM dependency observations
# ---------------------------------------------------------------------------

def dependency_observations(report: Any) -> Dict[str, Any]:
    """Dependencies a .NET assembly DECLARES in its own metadata.

    ``strong_named`` is ``None`` for a native module: a ModuleRef carries no
    publisher key, so the question does not apply rather than answering no.
    """
    refused = admit(report)
    if refused:
        return _refused(refused)
    items: List[Dict[str, Any]] = []
    unmeasured: Dict[str, str] = {}
    if _family(report) == "dotnet_assembly":
        for entry in _entries(report, "inspect_managed_artifact"):
            managed, native, gaps = _dotnet(entry["normalized_result"])
            unmeasured.update(gaps)
            for ref in managed or []:
                items.append({"name": ref["name"], "version": ref["version"],
                              "kind": "managed_assembly",
                              "publisher_key_token": ref["public_key_token"],
                              "strong_named": bool(ref["public_key_token"]),
                              "basis": BASIS_OBSERVED, "confidence": CONFIDENCE_UNASSERTED,
                              "provenance": observation_provenance(report, entry)})
            for mod in native or []:
                items.append({"name": mod["name"], "version": None, "kind": "native_module",
                              "publisher_key_token": None, "strong_named": None,
                              "basis": BASIS_OBSERVED, "confidence": CONFIDENCE_UNASSERTED,
                              "provenance": observation_provenance(report, entry)})
    return _result(items, unmeasured)


# ---------------------------------------------------------------------------
# Feed 4: ATO evidence provenance
# ---------------------------------------------------------------------------

def _bundle_digest(report: Mapping[str, Any]) -> str:
    canonical = json.dumps({"evidence_ids": report.get("evidence_ids"),
                            "body": report.get("body"),
                            "provenance": report.get("provenance")},
                           sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ato_provenance(report: Any) -> Dict[str, Any]:
    """The REA evidence bundle as a provenance block for an ATO evidence record."""
    refused = admit(report)
    if refused:
        return {"status": "refused", "reason": refused}
    prov = report.get("provenance") or {}
    return {
        "status": "attached",
        "reason": None,
        "kind": "rea_evidence_bundle",
        "family": report.get("family"),
        "target": report.get("target"),
        "target_sha256": prov.get("target_sha256"),
        "authorization_basis": prov.get("authorization_basis"),
        "analysis": prov.get("analysis", "local"),
        "rea_version": report.get("rea_version"),
        "report_status": report.get("status"),
        "evidence_ids": list(report.get("evidence_ids") or []),
        "bundle_sha256": _bundle_digest(report),
        "limitations": report.get("limitations"),
        "residual_unknowns": report.get("residual_unknowns"),
        "basis": BASIS_OBSERVED,
        "confidence": CONFIDENCE_UNASSERTED,
        "note": "Provenance BESIDE the evidence record; it never moves a signature verdict.",
    }


def attach_ato_provenance(record: Mapping[str, Any], blocks: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """A NEW mapping: *record* unchanged plus ``rea_provenance``; refuses to overwrite."""
    if ATO_RESULT_KEY in record:
        raise ValueError("refusing to overwrite an existing %r key on an evidence record"
                         % ATO_RESULT_KEY)
    merged = dict(record)
    merged[ATO_RESULT_KEY] = [dict(b) for b in blocks]
    return merged
