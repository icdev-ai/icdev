"""tools/network/diagram_analysis.py — Network Diagram Analysis Engine for NDC.

Supports PNG, JPG, multi-page PDF, draw.io XML, and DOCX uploads.
Performs cloud-aware, industry-specific AI analysis with 6-tab output
and generates an annotated draw.io XML export.
"""

import base64
import io
import zlib
import json
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

UPLOAD_DIR = Path("data/ndc_uploads/diagrams")
EXPORT_DIR = Path("data/ndc_uploads/exports")

# ── Token budget guardrails ───────────────────────────────────────────────────
_MAX_IMAGES_PER_CALL = 4       # max images per LLM call — avoids context overrun
_TOKEN_BUDGET_PER_IMAGE = 2048  # output tokens allocated per image/page (increased: 6-tab JSON needs room)
_TOKEN_BUDGET_BASE = 6144       # minimum tokens for any single call (was 2048 — truncated JSON)

# ── Format Detection ──────────────────────────────────────────────────────────

def detect_format(filename: str, file_bytes: bytes) -> str:
    """Return 'png'|'jpg'|'pdf'|'drawio'|'docx'|'unknown'."""
    ext = Path(filename).suffix.lower()
    if ext == ".png":
        return "png"
    if ext in (".jpg", ".jpeg"):
        return "jpg"
    if ext == ".pdf" or file_bytes[:4] == b"%PDF":
        return "pdf"
    if ext in (".drawio", ".xml"):
        snippet = file_bytes[:1024].decode("utf-8", errors="ignore")
        if "mxGraphModel" in snippet or "mxCell" in snippet:
            return "drawio"
        return "unknown"
    if ext == ".docx" or file_bytes[:4] == b"PK\x03\x04":
        return "docx"
    return "unknown"


# ── PDF Extraction ────────────────────────────────────────────────────────────

def extract_images_from_pdf(file_path: Path, max_pages: int = 20) -> list:
    """Rasterize each PDF page to PNG bytes. Returns list[bytes]."""
    pages = []
    try:
        import pypdfium2 as pdfium  # type: ignore
        pdf = pdfium.PdfDocument(str(file_path))
        for i, page in enumerate(pdf):
            if i >= max_pages:
                break
            bitmap = page.render(scale=1.5)
            pil_img = bitmap.to_pil()
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            pages.append(buf.getvalue())
        return pages
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("pypdfium2 PDF rasterization failed: %s", exc)
    return []


def extract_text_from_pdf(file_path: Path) -> str:
    """Extract plain text from all PDF pages using pypdf (fallback when rasterization unavailable)."""
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(file_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        logger.warning("PDF text extraction failed: %s", exc)
        return ""


def get_pdf_page_count(file_path: Path) -> int:
    """Return page count for a PDF file."""
    try:
        from pypdf import PdfReader  # type: ignore
        return len(PdfReader(str(file_path)).pages)
    except Exception:
        return 1


# ── DOCX Extraction ───────────────────────────────────────────────────────────

def extract_images_from_docx(file_path: Path) -> list:
    """Extract embedded images from DOCX via python-docx part relationships. Returns list[bytes]."""
    images = []
    try:
        from docx import Document  # type: ignore
        doc = Document(str(file_path))
        for rel in doc.part.rels.values():
            if "image" in rel.reltype:
                try:
                    images.append(rel.target_part.blob)
                except Exception:
                    pass
    except ImportError:
        logger.warning("python-docx not installed; DOCX image extraction unavailable")
    except Exception as exc:
        logger.warning("DOCX image extraction failed: %s", exc)
    return images


# ── draw.io Parsing ───────────────────────────────────────────────────────────

def parse_drawio_file(file_path: Path) -> dict:
    """Parse draw.io XML and extract embedded images.

    Returns: {"graph": dict, "images": list[bytes], "xml": str}
    Reuses tools.simulation.parsers.drawio_parser.parse_drawio for structural parse.
    """
    xml_str = file_path.read_text(encoding="utf-8", errors="ignore")
    graph = {}
    try:
        from tools.simulation.parsers.drawio_parser import parse_drawio
        graph = parse_drawio(xml_str)
    except Exception as exc:
        logger.warning("draw.io structural parse failed: %s", exc)

    # Extract base64-embedded images from mxCell style strings
    images = []
    try:
        root = ET.fromstring(xml_str)
        for cell in root.iter("mxCell"):
            style = cell.get("style", "")
            if "image=data:" in style:
                for part in style.split(";"):
                    if part.startswith("image=data:"):
                        try:
                            _, b64 = part.split(",", 1)
                            images.append(base64.b64decode(b64.strip()))
                        except Exception:
                            pass
    except Exception:
        pass

    return {"graph": graph, "images": images, "xml": xml_str}


# ── Cloud Provider Detection ──────────────────────────────────────────────────

_CLOUD_KEYWORDS = {
    "aws":   ["aws", "amazon", "ec2", "s3 ", " s3,", "vpc", "lambda", "route 53", "route53",
              "cloudfront", "rds", "eks", "cloudtrail", "transit gateway", "direct connect",
              "security group", "iam role", "guardduty"],
    "azure": ["azure", "microsoft azure", "vnet", " nsg", "expressroute", "azure ad",
              "azure active directory", "subscription", "resource group", "azure firewall",
              "blob storage", "aks", "azure devops"],
    "gcp":   ["gcp", "google cloud", "gke", "cloud run", "bigquery", "vpc service controls",
              "cloud interconnect", "cloud armor", "cloud sql", "google kubernetes"],
    "oci":   ["oracle cloud", " oci ", "fastconnect", " vcn ", "security list", " drg "],
    "ibm":   ["ibm cloud", "ibm ", "direct link", "cloud object storage", "ibm kubernetes"],
}

_HYBRID_KEYWORDS = [
    "vpn", "direct connect", "expressroute", "cloud interconnect", "fastconnect",
    "mpls", "sd-wan", "sdwan", "on-premises", "on-prem", "on premise",
    "data center", "datacenter", "colocation", "colo ", "private cloud",
]


def detect_cloud_providers(graph_json: Optional[dict], vision_text: str) -> dict:
    """Identify cloud providers and topology mode from the diagram.

    Returns:
        {
            "providers": ["aws", "azure"],
            "mode": "multi_cloud",          # cloud_native|hybrid|multi_cloud|on_prem
            "has_on_prem": True,
            "connectivity": ["vpn"],
        }
    """
    text_lower = (" " + vision_text + " ").lower()
    found = set()

    for provider, keywords in _CLOUD_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            found.add(provider)

    # Also scan draw.io node/zone labels
    if graph_json:
        all_labels = " ".join(
            (n.get("label") or "") for n in graph_json.get("nodes", [])
        ) + " " + " ".join(
            (z.get("label") or "") for z in graph_json.get("zones", [])
        )
        labels_lower = (" " + all_labels + " ").lower()
        for provider, keywords in _CLOUD_KEYWORDS.items():
            if any(kw in labels_lower for kw in keywords):
                found.add(provider)

    has_on_prem = any(kw in text_lower for kw in _HYBRID_KEYWORDS)
    if graph_json:
        node_text = (" " + " ".join(
            (n.get("label") or "") for n in graph_json.get("nodes", [])
        ) + " ").lower()
        has_on_prem = has_on_prem or any(kw in node_text for kw in _HYBRID_KEYWORDS)

    connectivity = []
    for kw in ["vpn", "direct connect", "expressroute", "cloud interconnect", "fastconnect", "mpls", "sd-wan"]:
        if kw in text_lower:
            connectivity.append(kw)

    providers = sorted(found)
    if not providers:
        mode = "on_prem"
    elif len(providers) > 1:
        mode = "multi_cloud"
    elif has_on_prem:
        mode = "hybrid"
    else:
        mode = "cloud_native"

    return {
        "providers": providers,
        "mode": mode,
        "has_on_prem": has_on_prem,
        "connectivity": connectivity,
    }


# ── Industry Prompt Builder ───────────────────────────────────────────────────

_INDUSTRY_LENS = {
    "dod_il4": """
DoD / IC IMPACT LEVEL 4 (IL4 — CUI / FedRAMP High):
- NIPR/SIPR separation: verify NIPR traffic cannot reach SIPR enclave without an approved CDS
- IPSec: IKEv2 with AES-256-GCM + SHA-384; no IKEv1, 3DES, or MD5 anywhere
- SCCA/SACA alignment: if cloud-hosted, check for BCAP, VDSS, VDMS, MGMT components
- DISA STIG: identify CAT I (critical) and CAT II deviations on all visible device types
- NIST SP 800-53 Rev 5 Moderate baseline: AC, AU, CA, CM, CP, IA, IR, MA, SC, SI controls
- FedRAMP High: verify CSP authorization and boundary scope
- CMMC Level 2: 110 NIST 800-171 practices (assess access control, audit, config mgmt)
- CNSSI 1253 moderate baseline
""",
    "dod_il5": """
DoD / IC IMPACT LEVEL 5 (IL5 — NIPR Secret-Enclave):
- All IL4 requirements, plus elevated controls
- NIPR Secret-Enclave: no internet-direct paths; all traffic through approved inspection points
- IPSec: Suite B cryptography (AES-256, ECDHE P-384, SHA-384)
- SCCA: mandatory for DoD cloud — all four components (BCAP, VDSS, VDMS, MGMT) required
- NIST SP 800-53 Rev 5 High baseline
- FedRAMP High + DoD CC SRG IL5 authorization
- CMMC Level 3: 130 practices
- Physical access controls must be documented
""",
    "dod_il6": """
DoD / IC IMPACT LEVEL 6 (SECRET):
- NSA Type 1 encryption required for all data in transit
- Cross-Domain Solution (CDS): approved CDS required at every cross-domain boundary
- Air-gap: no internet-facing egress from SECRET enclave; verify no path to NIPR
- SIPR-only: traffic must not traverse NIPR infrastructure
- CNSSI 1253 high baseline with SECRET-specific overlays
- DISA STIG: all CAT I findings are blocking (must remediate before authorization)
- Physical security: SCIF or equivalent required for all endpoints
""",
    "healthcare": """
Healthcare / HIPAA / HITRUST:
- PHI data flows: identify ALL network paths where PHI may transit — flag any unencrypted flows
- HIPAA § 164.312(a)(1) Access Control: unique user IDs, emergency access procedure, auto-logoff
- HIPAA § 164.312(b) Audit Controls: logging on all PHI-touching systems
- HIPAA § 164.312(c)(1) Integrity Controls: data integrity mechanisms on PHI stores
- HIPAA § 164.312(e)(1) Transmission Security: encryption required on all PHI transmission paths
- HITRUST CSF v11: assess applicable control categories
- Network segmentation: PHI systems in dedicated, isolated segments
- BAA: all third-party cloud services storing or processing PHI require Business Associate Agreement
""",
    "financial": """
Financial / PCI-DSS / SOC 2:
- PCI-DSS v4.0: identify Cardholder Data Environment (CDE) scope on the diagram
- Requirement 1: firewall rules isolating CDE from all untrusted networks
- Requirement 2: no default credentials on any CDE-adjacent device
- Requirement 4: TLS 1.2+ for all cardholder data in transit
- Requirement 6: WAF presence required for all public-facing CDE components
- Requirement 8: MFA for all access to the CDE
- Requirement 10: audit log retention (12 months, 3 months immediately available)
- SOC 2 Type II: availability, security, and confidentiality controls
- Network segmentation: CDE must be isolated — no flat network paths to non-CDE
""",
    "commercial": """
Commercial / Enterprise (ISO 27001 / NIST):
- NIST SP 800-53 Rev 5 Low/Moderate baseline
- ISO 27001:2022 Annex A: A.8.20 network security, A.5.15 access control, A.8.24 cryptography
- OWASP Top 10: check for architectural patterns enabling injection, broken access control, security misconfig
- CIS Controls v8: network monitoring (Control 13), audit logging (Control 8)
- Zero Trust principles: verify least-privilege network paths, no implicit trust zones
""",
}

_CLOUD_AGNOSTIC_CHECKS = """
CLOUD-AGNOSTIC BASELINE (apply regardless of cloud provider):
- CSA Cloud Controls Matrix (CCM v4): AIS, BCR, CEK, DSP, GRC, HRS, IAM, IPY, IVS, LOG, SEF, STA, TVM, UEM
- NIST SP 800-144: governance, compliance, trust, architecture, identity, and software security for cloud
- Shared responsibility boundary: explicitly identify cloud-provider vs. customer responsibility for each zone
- Encryption in transit: TLS 1.2+ or IPSec on ALL inter-service, inter-zone, and hybrid links
- Encryption at rest: all data stores, volumes, and object storage must be encrypted
- Identity federation: unified IdP across all zones (SSO, SAML 2.0, or OIDC)
- Least-privilege: service accounts and IAM roles scoped to minimum required permissions
- Network egress filtering: prevent data exfiltration — all egress must be logged and filtered
- Centralized SIEM: audit logs from all zones forwarded to a single logging plane
"""

_PROVIDER_CHECKS = {
    "aws": """
AWS-SPECIFIC CHECKS:
- Security Groups: flag any inbound rule with source 0.0.0.0/0 on ports other than 80/443
- IAM role trust policies: flag overly permissive AssumeRole (* or cross-account without condition)
- VPC Flow Logs: must be enabled on all VPCs
- CloudTrail: multi-region, log file validation enabled
- S3 bucket policies: S3 Block Public Access must be on for all buckets in CDE/PHI/CUI scope
- Transit Gateway: verify route tables segment spoke VPCs correctly
- GuardDuty + Security Hub: verify presence for threat detection
""",
    "azure": """
AZURE-SPECIFIC CHECKS:
- NSG rules: flag any inbound Allow rule with source * or 0.0.0.0/0 on sensitive ports
- Azure AD Conditional Access: MFA enforced for all privileged access
- VNet peering: hub-spoke preferred over full-mesh (security posture difference)
- Azure Monitor / Activity Log: Diagnostic Settings enabled on all resources
- Storage Account: public blob access disabled, soft delete enabled
- ExpressRoute: dual circuits for redundancy (single circuit = SPOF)
- Microsoft Defender for Cloud + Sentinel: verify presence
""",
    "gcp": """
GCP-SPECIFIC CHECKS:
- Firewall rules: flag any ingress allow from 0.0.0.0/0 or ::/0 to sensitive ports
- VPC Service Controls: verify access perimeters around sensitive BigQuery/GCS/Spanner
- Cloud Audit Logs: admin activity, data read, and data write logs enabled
- Cloud Interconnect: verify redundant VLAN attachments (single = SPOF)
- IAM bindings: flag Primitive roles (Owner, Editor) on any production resource
- Cloud Armor: WAF policy attached to all public-facing backend services
""",
    "oci": """
OCI-SPECIFIC CHECKS:
- Security Lists: flag STATEFUL ingress rules with source 0.0.0.0/0 on non-HTTP ports
- IAM policies: flag allow group <any> to manage all-resources in tenancy
- FastConnect: verify redundant virtual circuits
- VCN: subnet-level NSGs (not just legacy Security Lists) configured
""",
    "ibm": """
IBM CLOUD-SPECIFIC CHECKS:
- Cloud Object Storage: verify BYOK (customer-managed keys) for sensitive data
- Activity Tracker: forwarding to IBM LogAnalysis or external SIEM
- Direct Link: verify Connect or Dedicated with redundant cross-connects
- Security Groups: flag unrestricted inbound rules (0.0.0.0/0)
""",
}

_HYBRID_CHECKS = """
HYBRID / MULTI-CLOUD CHECKS:
- VPN tunnels: IKEv2 with AES-256-GCM + SHA-256 minimum; flag IKEv1 or weaker ciphers
- Cross-cloud traffic: dedicated inspection layer required (NGFW, CASB, or SASE solution)
- Identity federation: single IdP across all environments; no per-environment local accounts for admins
- DNS: consistent split-DNS across hybrid boundary; no DNS leakage
- Blast-radius: document single points of failure in hybrid interconnect links
- Data sovereignty: verify data residency requirements met for all regions/providers in scope
"""

_RESPONSE_SCHEMA = """
RESPONSE FORMAT:
Return ONLY a valid JSON object with exactly these 6 keys. No markdown fences, no prose.

{
  "overview": [
    {"title": "...", "detail": "...", "severity": "info|low|medium|high|critical"}
  ],
  "inventory": [
    {"component": "...", "type": "...", "count": 1, "provider": "aws|azure|gcp|oci|ibm|on_prem|unknown", "detail": "..."}
  ],
  "topology": [
    {"zone": "...", "description": "...", "trust_level": "untrusted|dmz|trusted|restricted", "cloud_provider": "...", "connectivity": "...", "severity": "info|medium|high|critical"}
  ],
  "security": [
    {"title": "...", "detail": "...", "severity": "critical|high|medium|low|info", "affected_component": "..."}
  ],
  "compliance": [
    {"control_id": "...", "framework": "...", "status": "gap|partial|met", "detail": "...", "severity": "critical|high|medium|low"}
  ],
  "remediate": [
    {"priority": 1, "title": "...", "detail": "...", "references": [{"type": "CVE|NIST|OWASP|STIG|CIS", "id": "...", "description": "..."}], "effort": "low|medium|high"}
  ]
}
"""


def build_industry_prompt(industry: str, cloud_context: dict) -> str:
    """Build the full system prompt for the given industry + detected cloud context."""
    lens = _INDUSTRY_LENS.get(industry, _INDUSTRY_LENS["commercial"])
    providers = cloud_context.get("providers", [])

    provider_section = "".join(
        _PROVIDER_CHECKS[p] for p in providers if p in _PROVIDER_CHECKS
    )

    hybrid_section = ""
    if cloud_context.get("has_on_prem") or cloud_context.get("mode") in ("hybrid", "multi_cloud"):
        hybrid_section = _HYBRID_CHECKS

    return (
        "You are a senior network security architect conducting a comprehensive diagram analysis.\n\n"
        + lens
        + "\n"
        + _CLOUD_AGNOSTIC_CHECKS
        + provider_section
        + hybrid_section
        + "\n"
        + _RESPONSE_SCHEMA
    )


# ── Vision Message Builder ────────────────────────────────────────────────────

def _bytes_to_data_uri(image_bytes: bytes) -> str:
    mime = "image/jpeg" if image_bytes[:2] == b"\xff\xd8" else "image/png"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_vision_messages(
    images: list,
    graph_json: Optional[dict],
    system_prompt: str,
    extra_context: str = "",
) -> list:
    """Build LLM messages list with image blocks and optional graph context."""
    intro = "Analyze the following network diagram(s) and return a JSON report per the schema."

    if extra_context:
        intro += f"\n\nAdditional context extracted from the document:\n{extra_context[:3000]}"

    if graph_json and graph_json.get("nodes"):
        node_labels = [n.get("label", "") for n in graph_json.get("nodes", []) if n.get("label")]
        zone_labels = [z.get("label", "") for z in graph_json.get("zones", []) if z.get("label")]
        intro += (
            f"\n\nParsed draw.io structure: {len(graph_json.get('nodes',[]))} nodes, "
            f"{len(graph_json.get('edges',[]))} edges, {len(graph_json.get('zones',[]))} zones."
        )
        if node_labels:
            intro += f"\nNode labels: {', '.join(node_labels[:40])}"
        if zone_labels:
            intro += f"\nZones/swimlanes: {', '.join(zone_labels[:20])}"

    content = [{"type": "text", "text": intro}]
    for img_bytes in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": _bytes_to_data_uri(img_bytes)},
        })

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


# ── Upload Loading ────────────────────────────────────────────────────────────

def _load_images_for_upload(upload: dict) -> tuple:
    """Return (images: list[bytes], graph_json: dict|None) for the upload record."""
    fmt = upload["format"]
    path = Path(upload["file_path"])
    graph_json = None
    images = []

    if fmt in ("png", "jpg"):
        images = [path.read_bytes()]
    elif fmt == "pdf":
        images = extract_images_from_pdf(path)
    elif fmt == "drawio":
        parsed = parse_drawio_file(path)
        graph_json = parsed["graph"]
        images = parsed["images"]
    elif fmt == "docx":
        images = extract_images_from_docx(path)

    return images, graph_json


# ── Response Parsing ──────────────────────────────────────────────────────────

def parse_analysis_response(raw_text: str) -> dict:
    """Parse LLM response into the 6-tab dict structure."""
    text = raw_text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end])

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    keys = ["overview", "inventory", "topology", "security", "compliance", "remediate"]
    return {k: data.get(k) if isinstance(data.get(k), list) else [] for k in keys}


def _fallback_analysis(upload: dict, industry: str, cloud_context: dict) -> dict:
    providers = ", ".join(cloud_context.get("providers", [])) or "unknown"
    mode = cloud_context.get("mode", "unknown")
    return {
        "overview": [{"title": "LLM analysis unavailable", "detail": f"Detected topology: {mode}, providers: {providers}. Manual review required.", "severity": "info"}],
        "inventory": [],
        "topology": [{"zone": "Unknown", "description": "Could not parse topology. Upload a clearer image or ensure LLM access.", "trust_level": "unknown", "cloud_provider": providers, "connectivity": "", "severity": "info"}],
        "security": [{"title": "Manual review required", "detail": "Automated security analysis could not complete. Please review the diagram manually.", "severity": "medium", "affected_component": "all"}],
        "compliance": [],
        "remediate": [],
    }


# ── Batch Merge Helper ────────────────────────────────────────────────────────

def _merge_tab_results(batches: list) -> dict:
    """Merge tab dicts from multiple batch LLM calls, deduplicating by title."""
    keys = ["overview", "inventory", "topology", "security", "compliance", "remediate"]
    merged = {k: [] for k in keys}
    seen: set = set()
    for batch in batches:
        for k in keys:
            for item in (batch or {}).get(k, []):
                title = str(
                    item.get("title") or item.get("component") or item.get("zone") or ""
                ).strip()[:120]
                dedup_key = f"{k}:{title}"
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    merged[k].append(item)
    for i, item in enumerate(merged["remediate"], 1):
        item["priority"] = i
    return merged


# ── Main Analysis Orchestrator ────────────────────────────────────────────────

def run_analysis(upload_id: str, industry: str, conn) -> dict:
    """Full diagram analysis pipeline.

    Steps:
    1. Load upload record and file
    2. Extract images (format-specific)
    3. Quick vision pass for cloud detection
    4. detect_cloud_providers()
    5. build_industry_prompt()
    6. Full LLM analysis call
    7. parse_analysis_response()
    8. Return {tabs, cloud_context, industry}
    """
    row = conn.execute("SELECT * FROM nc_diagram_uploads WHERE id=%s", (upload_id,)).fetchone()
    if not row:
        raise ValueError(f"Upload {upload_id!r} not found")
    upload = dict(row)

    images, graph_json = _load_images_for_upload(upload)

    # Quick pass for cloud provider detection (first image only, low token cost)
    vision_text = ""
    if images:
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest
            router = LLMRouter()
            quick_content = [
                {"type": "text", "text": "Describe all network components, zones, and cloud providers visible in this diagram. Be specific about provider names and service types."},
                {"type": "image_url", "image_url": {"url": _bytes_to_data_uri(images[0])}},
            ]
            quick_req = LLMRequest(
                messages=[{"role": "user", "content": quick_content}],
                max_tokens=800,
                temperature=0.1,
                skip_injection_scan=True,
            )
            quick_resp = router.invoke("ndc_diagram_quick_scan", quick_req)
            vision_text = getattr(quick_resp, "content", "") or ""
        except Exception as exc:
            logger.warning("Quick vision scan failed (continuing): %s", exc)

    # Include graph labels in cloud detection even without vision
    if graph_json:
        node_labels = " ".join((n.get("label") or "") for n in graph_json.get("nodes", []))
        vision_text += " " + node_labels

    cloud_context = detect_cloud_providers(graph_json, vision_text)

    system_prompt = build_industry_prompt(industry, cloud_context)

    extra_context = ""
    if upload["format"] == "pdf" and not images:
        extra_context = extract_text_from_pdf(Path(upload["file_path"]))

    tabs = {}
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        all_images = images or []
        chunks = [
            all_images[i:i + _MAX_IMAGES_PER_CALL]
            for i in range(0, max(len(all_images), 1), _MAX_IMAGES_PER_CALL)
        ] or [[]]
        total = len(chunks)
        batch_results = []
        for idx, chunk in enumerate(chunks):
            page_note = (
                f" Pages {idx * _MAX_IMAGES_PER_CALL + 1}–"
                f"{min((idx + 1) * _MAX_IMAGES_PER_CALL, len(all_images))} of {len(all_images)}."
            ) if total > 1 else ""
            ctx = extra_context + (f"\n\nBatch {idx + 1}/{total}.{page_note}" if total > 1 else "")
            msgs = build_vision_messages(
                chunk,
                graph_json if idx == 0 else None,
                system_prompt,
                ctx,
            )
            budget = max(_TOKEN_BUDGET_BASE, _TOKEN_BUDGET_PER_IMAGE * max(len(chunk), 1))
            req = LLMRequest(
                messages=msgs,
                max_tokens=budget,
                temperature=0.2,
                skip_injection_scan=True,
            )
            resp = router.invoke("ndc_diagram_analysis", req)
            raw = getattr(resp, "content", "") or ""
            logger.info(
                "Diagram analysis response: len=%d model=%s",
                len(raw),
                getattr(resp, "model_id", "unknown"),
            )
            batch_results.append(parse_analysis_response(raw))
            logger.info("Diagram analysis batch %d/%d complete", idx + 1, total)

        tabs = _merge_tab_results(batch_results) if total > 1 else (batch_results[0] if batch_results else {})
        if total > 1:
            tabs.setdefault("overview", []).insert(0, {
                "title": f"Multi-page analysis: {len(all_images)} pages in {total} batches",
                "detail": (
                    f"Token budget: {_TOKEN_BUDGET_PER_IMAGE} tokens/page, "
                    f"{_MAX_IMAGES_PER_CALL} pages/batch. Findings merged and deduplicated."
                ),
                "severity": "info",
            })
    except Exception as exc:
        logger.error("Diagram analysis LLM call failed: %s", exc)
        tabs = _fallback_analysis(upload, industry, cloud_context)

    return {"tabs": tabs, "cloud_context": cloud_context, "industry": industry}


# ── Findings Persistence ──────────────────────────────────────────────────────

def save_findings(conn, analysis_id: str, tabs: dict) -> None:
    """Persist per-tab findings to nc_diagram_findings."""
    tab_keys = ["overview", "inventory", "topology", "security", "compliance", "remediate"]
    for tab_key in tab_keys:
        for item in tabs.get(tab_key, []):
            fid = f"df-{uuid.uuid4().hex[:10]}"
            severity = item.get("severity", "info")
            title = str(
                item.get("title") or item.get("component") or item.get("zone") or "Finding"
            )[:255]
            detail = str(item.get("detail") or item.get("description") or "")[:2000]
            remediation = str(
                item.get("remediation") or (item.get("detail") if tab_key == "remediate" else "") or ""
            )[:2000]
            refs = item.get("references") or []
            refs_json = json.dumps(refs) if isinstance(refs, list) else "[]"

            conn.execute(
                "INSERT INTO nc_diagram_findings "
                "(id, analysis_id, tab, severity, title, detail, remediation, references_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (fid, analysis_id, tab_key, severity, title, detail, remediation, refs_json),
            )


# ── File Hash ─────────────────────────────────────────────────────────────────

def compute_file_hash(file_bytes: bytes) -> str:
    return format(zlib.crc32(file_bytes) & 0xFFFFFFFF, "08x")


# ── draw.io Export Generator ──────────────────────────────────────────────────

_SEV_FILL = {
    "critical": "#FF0000",
    "high":     "#FF6600",
    "medium":   "#FFCC00",
    "low":      "#99CC00",
    "info":     "#AACCFF",
    "clean":    "#00CC66",
}

_PROVIDER_DISPLAY = {
    "aws":     "Amazon Web Services",
    "azure":   "Microsoft Azure",
    "gcp":     "Google Cloud",
    "oci":     "Oracle Cloud",
    "ibm":     "IBM Cloud",
    "on_prem": "On-Premises",
}

_CLASSIFICATION_BANNERS = {
    "dod_il4": "UNCLASSIFIED // CUI",
    "dod_il5": "UNCLASSIFIED // CUI // IL5",
    "dod_il6": "SECRET // SI",
}


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _sev_rank(sev: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}.get(sev, 0)


def generate_drawio_export(
    analysis_result: dict,
    original_graph_json: Optional[dict],
    cloud_context: Optional[dict] = None,
    industry: str = "commercial",
    filename_stem: str = "diagram",
) -> str:
    """Generate an annotated, remediated draw.io XML file.

    Includes:
    - Severity-colored nodes (from security findings)
    - Annotation text cells for each security finding
    - Legend swimlane (severity scale + cloud provider badges)
    - Cloud provider swimlane containers (multi-cloud)
    - Shared-responsibility boundary dashed line (hybrid)
    - Classification banner at top (DoD IL4/IL5/IL6)
    """
    tabs = analysis_result.get("tabs", analysis_result)
    cloud_context = cloud_context or {}
    providers = cloud_context.get("providers", [])
    mode = cloud_context.get("mode", "on_prem")
    has_on_prem = cloud_context.get("has_on_prem", False)

    classification_text = _CLASSIFICATION_BANNERS.get(industry, "")

    cells = []
    _cid = [10]

    def nid() -> str:
        _cid[0] += 1
        return str(_cid[0])

    # Build severity lookup: component_label -> highest severity
    severity_map: dict = {}
    for finding in tabs.get("security", []):
        comp = (finding.get("affected_component") or "").lower().strip()
        sev = finding.get("severity", "info")
        if comp and comp != "all":
            if _sev_rank(sev) > _sev_rank(severity_map.get(comp, "info")):
                severity_map[comp] = sev

    # --- Classification banner ---
    if classification_text:
        bid = nid()
        cells.append(
            f'<mxCell id="{bid}" value="{_esc(classification_text)}" '
            f'style="text;html=1;align=center;fontSize=13;fontStyle=1;'
            f'fillColor=#FFE0E0;strokeColor=#CC0000;" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="160" y="5" width="650" height="28" as="geometry"/></mxCell>'
        )

    # --- Shared responsibility boundary ---
    if has_on_prem and providers:
        brid = nid()
        cells.append(
            f'<mxCell id="{brid}" '
            f'value="&#x25C4;&#x2014; Cloud-Managed &#x2014;&#x2014;&#x2014; Customer-Managed &#x2014;&#x25BA;" '
            f'style="text;html=1;align=center;fontSize=11;fontStyle=2;'
            f'fillColor=#FFF3E0;strokeColor=#FF9900;" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="50" y="38" width="700" height="26" as="geometry"/></mxCell>'
        )

    # --- Cloud provider swimlanes (multi-cloud) ---
    provider_swimlane = {}
    if mode == "multi_cloud" and providers:
        for idx, provider in enumerate(providers):
            sid = nid()
            provider_swimlane[provider] = sid
            x_off = 50 + idx * 520
            cells.append(
                f'<mxCell id="{sid}" value="{_esc(_PROVIDER_DISPLAY.get(provider, provider))}" '
                f'style="swimlane;startSize=28;fillColor=#dae8fc;strokeColor=#6c8ebf;fontStyle=1;" '
                f'vertex="1" parent="1">'
                f'<mxGeometry x="{x_off}" y="80" width="500" height="580" as="geometry"/></mxCell>'
            )

    # --- Base nodes (from original draw.io graph or inventory) ---
    if original_graph_json and original_graph_json.get("nodes"):
        nodes = original_graph_json.get("nodes", [])
        edges = original_graph_json.get("edges", [])
        zones = original_graph_json.get("zones", [])

        zone_cell: dict = {}
        for zone in zones:
            zid = nid()
            zone_cell[zone["id"]] = zid
            cells.append(
                f'<mxCell id="{zid}" value="{_esc(zone.get("label","Zone"))}" '
                f'style="swimlane;startSize=22;fillColor=#f5f5f5;strokeColor=#666666;" '
                f'vertex="1" parent="1">'
                f'<mxGeometry x="60" y="100" width="380" height="280" as="geometry"/></mxCell>'
            )

        for i, node in enumerate(nodes):
            node_nid = nid()
            label = node.get("label", "")
            fill = _SEV_FILL.get(severity_map.get(label.lower(), ""), "#FFFFFF")
            parent = zone_cell.get(node.get("zone") or "", "1")
            x = (i % 4) * 130 + 20
            y = (i // 4) * 90 + 35
            cells.append(
                f'<mxCell id="{node_nid}" value="{_esc(label)}" '
                f'style="rounded=1;fillColor={fill};strokeColor=#333333;fontSize=10;" '
                f'vertex="1" parent="{parent}">'
                f'<mxGeometry x="{x}" y="{y}" width="110" height="36" as="geometry"/></mxCell>'
            )

        for edge in edges:
            eid = nid()
            elabel = edge.get("label", "")
            style = "edgeStyle=orthogonalEdgeStyle;"
            if any(kw in (elabel + str(edge.get("source","")) + str(edge.get("target",""))).lower()
                   for kw in ("vpn", "ipsec", "ssl")):
                style += "dashed=1;strokeColor=#FF9900;"
                elabel = elabel or "VPN/IPSec"
            cells.append(
                f'<mxCell id="{eid}" value="{_esc(elabel)}" '
                f'style="{style}" edge="1" '
                f'source="{_esc(str(edge.get("source", "")))}" '
                f'target="{_esc(str(edge.get("target", "")))}" parent="1">'
                f'<mxGeometry relative="1" as="geometry"/></mxCell>'
            )
    else:
        # Build nodes from inventory findings when no original graph available
        for i, item in enumerate(tabs.get("inventory", [])[:24]):
            inv_nid = nid()
            label = item.get("component", f"Component {i+1}")
            fill = "#FFFFFF"
            x = 60 + (i % 5) * 165
            y = 80 + (i // 5) * 90
            cells.append(
                f'<mxCell id="{inv_nid}" value="{_esc(label)}" '
                f'style="rounded=1;fillColor={fill};strokeColor=#555555;fontSize=10;" '
                f'vertex="1" parent="1">'
                f'<mxGeometry x="{x}" y="{y}" width="140" height="36" as="geometry"/></mxCell>'
            )

    # --- Security finding annotation cells ---
    for i, finding in enumerate(tabs.get("security", [])[:18]):
        ann_id = nid()
        sev = finding.get("severity", "info")
        fill = _SEV_FILL.get(sev, "#FFCC00")
        title = str(finding.get("title", "Finding"))[:70]
        x = 1020 + (i % 3) * 290
        y = 80 + (i // 3) * 100
        cells.append(
            f'<mxCell id="{ann_id}" value="&#x26A0; {_esc(title)}" '
            f'style="text;html=1;fillColor={fill};strokeColor=#999999;rounded=1;fontSize=10;" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="270" height="55" as="geometry"/></mxCell>'
        )

    # --- Legend swimlane ---
    leg_id = nid()
    legend_h = 50 + len(_SEV_FILL) * 40 + len(providers) * 40 + 10
    cells.append(
        f'<mxCell id="{leg_id}" value="Legend" '
        f'style="swimlane;startSize=22;fillColor=#f8f8f8;strokeColor=#999999;fontStyle=1;" '
        f'vertex="1" parent="1">'
        f'<mxGeometry x="1020" y="580" width="280" height="{legend_h}" as="geometry"/></mxCell>'
    )
    legend_items = [
        ("Critical", "critical"), ("High", "high"), ("Medium", "medium"),
        ("Low", "low"), ("Info / Clean", "info"),
    ]
    for i, (label, sev) in enumerate(legend_items):
        lid = nid()
        cells.append(
            f'<mxCell id="{lid}" value="{label}" '
            f'style="rounded=1;fillColor={_SEV_FILL[sev]};strokeColor=#333333;fontSize=10;" '
            f'vertex="1" parent="{leg_id}">'
            f'<mxGeometry x="10" y="{28 + i * 40}" width="90" height="28" as="geometry"/></mxCell>'
        )
    for i, provider in enumerate(providers):
        pid = nid()
        cells.append(
            f'<mxCell id="{pid}" value="{_esc(_PROVIDER_DISPLAY.get(provider, provider))}" '
            f'style="rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=9;" '
            f'vertex="1" parent="{leg_id}">'
            f'<mxGeometry x="110" y="{28 + i * 40}" width="160" height="28" as="geometry"/></mxCell>'
        )

    cells_xml = "\n    ".join(cells)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<mxGraphModel dx="1422" dy="762" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1654" pageHeight="1169" math="0" shadow="0">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    {cells_xml}
  </root>
</mxGraphModel>"""


# ── ATT&CK + D3FEND Enrichment ────────────────────────────────────────────────

_ATTACK_MAP = [
    (
        ["0.0.0.0/0", "open port", "unrestricted inbound", "any source", "public access", "exposed"],
        [{"id": "T1190", "name": "Exploit Public-Facing Application", "url": "https://attack.mitre.org/techniques/T1190"},
         {"id": "T1133", "name": "External Remote Services", "url": "https://attack.mitre.org/techniques/T1133"}],
        [{"id": "D3-NTA", "name": "Network Traffic Analysis", "url": "https://d3fend.mitre.org/technique/d3f:NetworkTrafficAnalysis"},
         {"id": "D3-FW",  "name": "Filtering (Firewall)", "url": "https://d3fend.mitre.org/technique/d3f:NetworkTrafficFiltering"}],
    ),
    (
        ["encrypt", "tls", "ipsec", "plaintext", "unencrypted", "http ", "clear text", "weak cipher"],
        [{"id": "T1557", "name": "Adversary-in-the-Middle", "url": "https://attack.mitre.org/techniques/T1557"},
         {"id": "T1040", "name": "Network Sniffing", "url": "https://attack.mitre.org/techniques/T1040"}],
        [{"id": "D3-ET",   "name": "Encrypted Tunnels", "url": "https://d3fend.mitre.org/technique/d3f:EncryptedTunnels"},
         {"id": "D3-DNSE", "name": "Data in Transit Encryption", "url": "https://d3fend.mitre.org/technique/d3f:DatainTransitEncryption"}],
    ),
    (
        ["authentication", "mfa", "multi-factor", "password", "credential", "iam", "privilege", "account"],
        [{"id": "T1078", "name": "Valid Accounts", "url": "https://attack.mitre.org/techniques/T1078"},
         {"id": "T1110", "name": "Brute Force", "url": "https://attack.mitre.org/techniques/T1110"}],
        [{"id": "D3-MFA", "name": "Multi-Factor Authentication", "url": "https://d3fend.mitre.org/technique/d3f:Multi-FactorAuthentication"},
         {"id": "D3-OTP", "name": "One-Time Password", "url": "https://d3fend.mitre.org/technique/d3f:One-timePassword"}],
    ),
    (
        ["log", "audit", "monitoring", "siem", "cloudtrail", "activity log", "flow log", "detection"],
        [{"id": "T1562.001", "name": "Impair Defenses: Disable or Modify Tools", "url": "https://attack.mitre.org/techniques/T1562/001"},
         {"id": "T1070",     "name": "Indicator Removal", "url": "https://attack.mitre.org/techniques/T1070"}],
        [{"id": "D3-SDA", "name": "System Daemon Monitoring", "url": "https://d3fend.mitre.org/technique/d3f:SystemDaemonMonitoring"},
         {"id": "D3-NTA", "name": "Network Traffic Analysis", "url": "https://d3fend.mitre.org/technique/d3f:NetworkTrafficAnalysis"}],
    ),
    (
        ["lateral movement", "east-west", "segmentation", "vlan", "subnet", "zone separation", "micro-segmentation"],
        [{"id": "T1210", "name": "Exploitation of Remote Services", "url": "https://attack.mitre.org/techniques/T1210"},
         {"id": "T1021", "name": "Remote Services", "url": "https://attack.mitre.org/techniques/T1021"}],
        [{"id": "D3-NI",   "name": "Network Isolation", "url": "https://d3fend.mitre.org/technique/d3f:NetworkIsolation"},
         {"id": "D3-ISVA", "name": "Inbound Session Volume Analysis", "url": "https://d3fend.mitre.org/technique/d3f:InboundSessionVolumeAnalysis"}],
    ),
    (
        ["vpn", "tunnel", "ike", "ikev1", "weak vpn", "site-to-site"],
        [{"id": "T1572", "name": "Protocol Tunneling", "url": "https://attack.mitre.org/techniques/T1572"},
         {"id": "T1557", "name": "Adversary-in-the-Middle", "url": "https://attack.mitre.org/techniques/T1557"}],
        [{"id": "D3-ET",  "name": "Encrypted Tunnels", "url": "https://d3fend.mitre.org/technique/d3f:EncryptedTunnels"},
         {"id": "D3-NTA", "name": "Network Traffic Analysis", "url": "https://d3fend.mitre.org/technique/d3f:NetworkTrafficAnalysis"}],
    ),
    (
        ["cve", "vulnerability", "patch", "unpatched", "outdated", "eol", "end-of-life", "exploit"],
        [{"id": "T1190", "name": "Exploit Public-Facing Application", "url": "https://attack.mitre.org/techniques/T1190"},
         {"id": "T1203", "name": "Exploitation for Client Execution", "url": "https://attack.mitre.org/techniques/T1203"}],
        [{"id": "D3-SWU", "name": "Software Update", "url": "https://d3fend.mitre.org/technique/d3f:SoftwareUpdate"},
         {"id": "D3-VUL", "name": "Vulnerability Scanning", "url": "https://d3fend.mitre.org/technique/d3f:VulnerabilityScanning"}],
    ),
    (
        ["dns", "domain", "name resolution", "dns poisoning", "split-dns"],
        [{"id": "T1071.004", "name": "Application Layer Protocol: DNS", "url": "https://attack.mitre.org/techniques/T1071/004"},
         {"id": "T1568",     "name": "Dynamic Resolution", "url": "https://attack.mitre.org/techniques/T1568"}],
        [{"id": "D3-DNSAL", "name": "DNS Allowlisting", "url": "https://d3fend.mitre.org/technique/d3f:DNSAllowlisting"},
         {"id": "D3-DNSBL", "name": "DNS Denylisting", "url": "https://d3fend.mitre.org/technique/d3f:DNSDenylisting"}],
    ),
    (
        ["supply chain", "third-party", "vendor", "software integrity", "sbom"],
        [{"id": "T1195", "name": "Supply Chain Compromise", "url": "https://attack.mitre.org/techniques/T1195"},
         {"id": "T1554", "name": "Compromise Host Software Binary", "url": "https://attack.mitre.org/techniques/T1554"}],
        [{"id": "D3-SBV", "name": "Software Binary Attestation", "url": "https://d3fend.mitre.org/technique/d3f:SoftwareBinaryAttestation"},
         {"id": "D3-SWU", "name": "Software Update", "url": "https://d3fend.mitre.org/technique/d3f:SoftwareUpdate"}],
    ),
    (
        ["data exfiltration", "egress", "data loss", "dlp", "outbound"],
        [{"id": "T1041", "name": "Exfiltration Over C2 Channel", "url": "https://attack.mitre.org/techniques/T1041"},
         {"id": "T1048", "name": "Exfiltration Over Alternative Protocol", "url": "https://attack.mitre.org/techniques/T1048"}],
        [{"id": "D3-NTF", "name": "Network Traffic Filtering", "url": "https://d3fend.mitre.org/technique/d3f:NetworkTrafficFiltering"},
         {"id": "D3-DLP", "name": "Data Loss Prevention", "url": "https://d3fend.mitre.org/technique/d3f:DataLossPrevention"}],
    ),
]


def enrich_with_attack(tabs: dict) -> dict:
    """Add ATT&CK and D3FEND refs to security/compliance/remediate findings via keyword matching."""
    enriched = dict(tabs)

    def _match(text: str):
        t = text.lower()
        attack_refs, defend_refs = [], []
        seen_a: set = set()
        seen_d: set = set()
        for keywords, attacks, defends in _ATTACK_MAP:
            if any(kw in t for kw in keywords):
                for a in attacks:
                    if a["id"] not in seen_a:
                        seen_a.add(a["id"])
                        attack_refs.append({"type": "ATT&CK", "id": a["id"], "description": a["name"], "url": a["url"]})
                for d in defends:
                    if d["id"] not in seen_d:
                        seen_d.add(d["id"])
                        defend_refs.append({"type": "D3FEND", "id": d["id"], "description": d["name"], "url": d["url"]})
        return attack_refs, defend_refs

    for tab_key in ("security", "compliance", "remediate"):
        new_items = []
        for item in enriched.get(tab_key, []):
            haystack = f"{item.get('title', '')} {item.get('detail', '')} {item.get('control_id', '')}"
            atk, dfd = _match(haystack)
            if atk or dfd:
                item = dict(item)
                existing = item.get("references") or []
                if not isinstance(existing, list):
                    existing = []
                existing_ids = {r.get("id") for r in existing}
                item["references"] = existing + [r for r in atk + dfd if r["id"] not in existing_ids]
            new_items.append(item)
        enriched[tab_key] = new_items

    return enriched


# ── Standalone HTML Report Generator ─────────────────────────────────────────

_SEV_CSS = {
    "critical": "background:#c00;color:#fff",
    "high":     "background:#e65c00;color:#fff",
    "medium":   "background:#cc9900;color:#fff",
    "low":      "background:#669900;color:#fff",
    "info":     "background:#336699;color:#fff",
}

_HTML_STYLE = """
body{font-family:Arial,Helvetica,sans-serif;font-size:13px;margin:0;padding:16px;color:#222}
h1{font-size:18px;margin:0 0 4px}
h2{font-size:14px;margin:16px 0 6px;border-bottom:2px solid #336699;padding-bottom:3px;color:#336699}
.meta{font-size:11px;color:#555;margin-bottom:12px}
.badge{display:inline-block;padding:2px 7px;border-radius:3px;font-size:11px;font-weight:bold;margin-right:4px}
table{border-collapse:collapse;width:100%;margin-bottom:12px;font-size:12px}
th{background:#336699;color:#fff;padding:5px 8px;text-align:left}
td{padding:4px 8px;border-bottom:1px solid #ddd;vertical-align:top}
tr:nth-child(even){background:#f5f7fa}
.ref{font-size:10px;color:#555;margin-top:3px}
.classification{background:#c00;color:#fff;text-align:center;font-weight:bold;padding:4px;margin-bottom:10px;font-size:13px}
.section{page-break-inside:avoid}
.tag{display:inline-block;background:#e8f0fe;border:1px solid #b8cdf8;border-radius:3px;padding:1px 5px;font-size:10px;margin:1px}
@media print{body{padding:0}h2{color:#000;border-color:#000}}
"""


def generate_html_report(analysis_result: dict, meta: dict) -> str:
    """Generate a fully self-contained HTML report from a diagram analysis result.

    Args:
        analysis_result: dict with 'tabs' (6-tab dict) and optionally 'cloud_context'.
        meta: dict with filename, industry, topology_mode, cloud_providers, analysis_id, created_at.

    Returns:
        UTF-8 HTML string with inline CSS — no external dependencies.
    """
    from tools.network.constants import DIAGRAM_ANALYSIS_INDUSTRIES

    tabs = analysis_result.get("tabs", analysis_result)
    cloud_ctx = analysis_result.get("cloud_context", {})

    filename = meta.get("filename", "diagram")
    industry_key = meta.get("industry", "commercial")
    industry_label = DIAGRAM_ANALYSIS_INDUSTRIES.get(industry_key, {}).get("label", industry_key)
    providers = meta.get("cloud_providers") or cloud_ctx.get("providers") or []
    topology_mode = meta.get("topology_mode") or cloud_ctx.get("mode", "unknown")
    created_at = meta.get("created_at", "")
    analysis_id = meta.get("analysis_id", "")
    classification = meta.get("classification") or _CLASSIFICATION_BANNERS.get(industry_key, "")

    def sev_badge(sev: str) -> str:
        css = _SEV_CSS.get((sev or "info").lower(), _SEV_CSS["info"])
        return f'<span class="badge" style="{css}">{_esc((sev or "info").upper())}</span>'

    def refs_html(refs) -> str:
        if not refs:
            return ""
        parts = []
        for r in refs:
            rid = r.get("id", "")
            desc = r.get("description", "")
            rtype = r.get("type", "")
            url = r.get("url", "")
            label = f"[{rtype}] {rid}: {desc}" if desc else f"[{rtype}] {rid}"
            if url:
                parts.append(f'<a href="{_esc(url)}" style="color:#336699;text-decoration:none">{_esc(label)}</a>')
            else:
                parts.append(_esc(label))
        return '<div class="ref">' + " &nbsp;|&nbsp; ".join(parts) + "</div>"

    overview_rows = "".join(
        f"<tr><td>{sev_badge(f.get('severity', 'info'))}</td>"
        f"<td><b>{_esc(f.get('title', ''))}</b><br>{_esc(f.get('detail', ''))}</td></tr>"
        for f in tabs.get("overview", [])
    )
    inv_rows = "".join(
        f"<tr><td>{_esc(f.get('component', ''))}</td><td>{_esc(f.get('type', ''))}</td>"
        f"<td>{f.get('count', 0)}</td><td>{_esc(f.get('provider', ''))}</td>"
        f"<td>{_esc(f.get('detail', ''))}</td></tr>"
        for f in tabs.get("inventory", [])
    )
    topo_rows = "".join(
        f"<tr><td>{_esc(f.get('zone', ''))}</td>"
        f"<td>{sev_badge(f.get('severity', 'info'))}</td>"
        f"<td>{_esc(f.get('trust_level', ''))}</td>"
        f"<td>{_esc(f.get('cloud_provider', ''))}</td>"
        f"<td>{_esc(f.get('description', ''))}</td></tr>"
        for f in tabs.get("topology", [])
    )
    sec_rows = "".join(
        f"<tr><td>{sev_badge(f.get('severity', 'info'))}</td>"
        f"<td><b>{_esc(f.get('title', ''))}</b>"
        f"<br><small>{_esc(f.get('affected_component', ''))}</small>"
        f"<br>{_esc(f.get('detail', ''))}"
        f"{refs_html(f.get('references'))}</td></tr>"
        for f in sorted(tabs.get("security", []), key=lambda x: -_sev_rank(x.get("severity", "info")))
    )
    comp_rows = "".join(
        f"<tr><td>{_esc(f.get('control_id', ''))}</td>"
        f"<td>{_esc(f.get('framework', ''))}</td>"
        f"<td>{sev_badge(f.get('severity', 'info'))}</td>"
        f"<td><span style=\"color:{'#c00' if f.get('status') == 'gap' else '#669900' if f.get('status') == 'met' else '#cc9900'}\">"
        f"{_esc((f.get('status') or '').upper())}</span></td>"
        f"<td>{_esc(f.get('detail', ''))}{refs_html(f.get('references'))}</td></tr>"
        for f in tabs.get("compliance", [])
    )
    rem_rows = "".join(
        f"<tr><td style=\"text-align:center;font-weight:bold\">{f.get('priority', '')}</td>"
        f"<td><span style=\"background:#e8f0fe;padding:1px 6px;border-radius:3px;font-size:11px\">"
        f"{_esc((f.get('effort') or '').upper())}</span></td>"
        f"<td><b>{_esc(f.get('title', ''))}</b><br>{_esc(f.get('detail', ''))}"
        f"{refs_html(f.get('references'))}</td></tr>"
        for f in tabs.get("remediate", [])
    )
    provider_badges = "".join(
        f'<span class="tag">{_esc(_PROVIDER_DISPLAY.get(p, p))}</span>' for p in providers
    )

    clf_top = f"<div class='classification'>{_esc(classification)}</div>" if classification else ""
    clf_bot = f"<div class='classification' style='margin-top:16px'>{_esc(classification)}</div>" if classification else ""

    def _section(heading, empty_msg, table_header, rows):
        if not rows:
            return f"<div class='section'><h2>{heading}</h2><p><i>{empty_msg}</i></p></div>"
        return (
            f"<div class='section'><h2>{heading}</h2>"
            f"<table>{table_header}{rows}</table></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Network Diagram Analysis — {_esc(filename)}</title>
<style>{_HTML_STYLE}</style>
</head>
<body>
{clf_top}
<h1>Network Diagram Analysis Report</h1>
<div class="meta">
  <b>File:</b> {_esc(filename)} &nbsp;|&nbsp;
  <b>Industry:</b> {_esc(industry_label)} &nbsp;|&nbsp;
  <b>Topology:</b> {_esc(topology_mode)} &nbsp;|&nbsp;
  <b>Providers:</b> {provider_badges if provider_badges else "unknown"} &nbsp;|&nbsp;
  <b>Analysis ID:</b> {_esc(analysis_id)} &nbsp;|&nbsp;
  <b>Generated:</b> {_esc(str(created_at))}
</div>
{_section("Overview", "No overview findings.",
    "<tr><th width='90'>Severity</th><th>Finding</th></tr>", overview_rows)}
{_section("Inventory", "No inventory data.",
    "<tr><th>Component</th><th>Type</th><th>#</th><th>Provider</th><th>Detail</th></tr>", inv_rows)}
{_section("Topology", "No topology findings.",
    "<tr><th>Zone</th><th>Severity</th><th>Trust</th><th>Provider</th><th>Description</th></tr>", topo_rows)}
{_section("Security Findings", "No security findings.",
    "<tr><th width='90'>Severity</th><th>Finding</th></tr>", sec_rows)}
{_section("Compliance Gaps", "No compliance findings.",
    "<tr><th>Control</th><th>Framework</th><th>Severity</th><th>Status</th><th>Detail</th></tr>", comp_rows)}
{_section("Remediation Plan", "No remediation items.",
    "<tr><th width='40'>#</th><th width='70'>Effort</th><th>Action</th></tr>", rem_rows)}
{clf_bot}
</body>
</html>"""
