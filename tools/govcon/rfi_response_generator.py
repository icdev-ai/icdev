"""
RFI Response Generator — Phase 59 Extension (PDF-first RFI intake path).

Orchestrates the full RFI response pipeline:
  1. Parse RFI document (rfi_document_parser.py)
  2. Extract requirements into shall-statement format (reuses requirement_extractor.py)
  3. Score ICDEV capabilities against each objective (reuses capability_mapper.py)
  4. Draft each Part section (reuses response_drafter.py with RFI-mode hardprompts)
  5. Inject company profile data (args/govcon_company_profiles.yaml)
  6. Export to DOCX (rfi_docx_exporter.py)

The technical sections (Part 2.4, 2.7, Appendix) are generated using CoT;
Part 5 (Industry Insights) uses CoD. Parts 1, 3, 4 use template-based direct drafting.

Usage:
    python tools/govcon/rfi_response_generator.py --input <rfi.pdf> --profile own_company --output-dir <dir> --json
    python tools/govcon/rfi_response_generator.py --input <rfi.pdf> --output-dir .tmp/ --json
    python tools/govcon/rfi_response_generator.py --parsed <parsed.json> --profile own_company --output-dir .tmp/ --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.rfi_response_generator")

_PROFILES_PATH = _ROOT / "args" / "govcon_company_profiles.yaml"
_HARDPROMPTS_DIR = _ROOT / "hardprompts"


def _load_profiles() -> dict:
    """Load company profiles from args/govcon_company_profiles.yaml."""
    try:
        import yaml
        with open(_PROFILES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("profiles", {})
    except (ImportError, FileNotFoundError) as exc:
        logger.warning(f"Could not load company profiles: {exc}")
        return {}


def _load_profile(profile_name: str) -> dict:
    profiles = _load_profiles()
    if profile_name not in profiles:
        available = list(profiles.keys())
        raise ValueError(f"Profile '{profile_name}' not found. Available: {available}")
    return profiles[profile_name]


def _load_capability_scores(objectives: list[dict]) -> list[dict]:
    """
    Score ICDEV capabilities against each RFI objective.
    Reuses capability_mapper.py logic against the capability catalog.
    Returns list of {objective_id, letter, title, icdev_capabilities[], coverage_grade}.
    """
    try:
        catalog_path = _ROOT / "context" / "govcon" / "icdev_capability_catalog.json"
        if not catalog_path.exists():
            logger.warning("Capability catalog not found; using empty scores")
            return []
        with open(catalog_path, encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception as exc:
        logger.warning(f"Could not load capability catalog: {exc}")
        return []

    capabilities = catalog.get("capabilities", catalog) if isinstance(catalog, dict) else catalog
    if isinstance(capabilities, dict):
        capabilities = list(capabilities.values())

    scores = []
    for obj in objectives:
        obj_text = (obj.get("title", "") + " " + obj.get("description", "")).lower()
        obj_keywords = set(re.sub(r"[^\w\s]", " ", obj_text).split())

        matched_caps = []
        for cap in capabilities:
            if not isinstance(cap, dict):
                continue
            cap_keywords = set()
            for kw in cap.get("keywords", []):
                cap_keywords.update(kw.lower().split())
            overlap = obj_keywords & cap_keywords
            if len(overlap) >= 2:
                matched_caps.append({
                    "id": cap.get("id", ""),
                    "name": cap.get("name", ""),
                    "overlap_count": len(overlap),
                    "overlap_keywords": sorted(list(overlap))[:5],
                })

        matched_caps.sort(key=lambda c: c["overlap_count"], reverse=True)
        coverage = "L" if len(matched_caps) >= 3 else "M" if matched_caps else "N"

        scores.append({
            "objective_id": obj.get("id", ""),
            "letter": obj.get("letter", ""),
            "title": obj.get("title", ""),
            "icdev_capabilities": matched_caps[:5],
            "coverage_grade": coverage,
        })

    return scores


def _draft_part1(profile: dict, rfi: dict) -> str:
    """Generate Part 1 Administrative section from company profile."""
    contact_name = profile.get("contact_name", "[NAME]")
    contact_title = profile.get("contact_title", "[TITLE]")
    contact_email = profile.get("contact_email", "[EMAIL]")
    contact_phone = profile.get("contact_phone", "[PHONE]")
    entity_name = profile.get("entity_name", "[COMPANY]")
    address = profile.get("address", "[ADDRESS]")
    cage = profile.get("cage_code", "[CAGE CODE]")
    uei = profile.get("sam_uei", "[SAM UEI]")
    biz_size = profile.get("business_size", "[SIZE]")
    naics = rfi.get("naics") or profile.get("primary_naics", "[NAICS]")
    ndc_status = profile.get("ndc_status", "[NDC STATUS]")
    ndc_note = profile.get("ndc_note", "")
    foci_status = profile.get("foci_status", "[FOCI STATUS]")
    foci_note = profile.get("foci_note", "")
    clearances = profile.get("clearances", [])
    clearance_text = "; ".join(
        f"{c.get('level', '')} — {c.get('note', '')}" for c in clearances
    ) if clearances else "[CLEARANCE LEVELS]"
    socioeco = profile.get("socioeconomic_status", [])
    socioeco_text = ", ".join(socioeco) if socioeco else "None"

    return f"""## Part 1: Administrative

**1.1 Entity Data**

| Field | Value |
|-------|-------|
| Company Name | {entity_name} |
| Address | {address} |
| Point of Contact | {contact_name}, {contact_title} |
| Email | {contact_email} |
| Phone | {contact_phone} |
| CAGE Code | {cage} |
| SAM.gov UEI | {uei} |

**1.2 Business Size**

{entity_name} qualifies as a **{biz_size}** under NAICS {naics}.
Socioeconomic status: {socioeco_text}.

**1.3 NDC Status**

{ndc_status}. {ndc_note.strip()}

**1.4 Foreign Interest (FOCI)**

{foci_status}. {foci_note.strip()}

**1.5 Security Clearances**

{clearance_text}
"""


def _draft_part3(rfi: dict) -> str:
    """Generate Part 3 Feasibility & Schedule using a template."""
    return """## Part 3: Project Feasibility and Schedule

**3.1 Timeline**

Estimated **6 months from award to first working prototype** demonstration.

| Month | Milestone |
|-------|-----------|
| M1–2 | Environment setup and integration with customer test data feed |
| M3 | Core prototype: three-tier routing stack operational; HITL override interface |
| M4 | Integration testing: latency benchmarking, priority injection, failure recovery |
| M5 | Adaptive learning integration: NOVA™ feedback loop and Bayesian policy tuning |
| M6 | Prototype demonstration: live demo, XAI dashboard, HITL walkthrough |

**3.2 Key Risk Areas**

**Risk 1 — Latency Validation in Customer Environment (Technical):** Lab benchmarks may
not reflect production network topology. Mitigation: deploy rule-engine sidecar co-located
with processing resources; validate in customer test environment by end of Month 2.

**Risk 2 — Mission Metrics API Dependency (Schedule):** Dynamic priority injection requires
the customer to provide a machine-readable operational priority feed. Mitigation: YAML-based
static priority rules serve as fallback; live dynamic injection is a Phase 2 deliverable
if the customer API is not available at prototype start.

**3.3 Custom Risk Responses**

*Explainability vs. Performance:* CoT reasoning chains are written asynchronously (ring buffer,
100ms flush interval). The routing decision completes before any audit write. Overhead: <2µs
per object. Audit trail is eventually consistent, not synchronous.

*Over-Optimization / Starvation:* Three mechanisms: minimum service floor (configurable %
of capacity reserved for best-effort), age-weighted priority boost (TTL-based escalation),
and hard max-wait cap (mandatory escalation after configurable wait period).

*Integration Complexity:* Lightweight sidecar model (50MB container) deployed alongside each
processing resource. Exposes /health and /capacity endpoints. No modification to existing
LLM or analytics internals required. Helm chart provided for Kubernetes deployment.
"""


def _draft_part4(profile: dict, rfi: dict) -> str:
    """Generate Part 4 Business & IP from profile."""
    ip = profile.get("ip_posture", {})
    prior_ip = ip.get("prior_ip", "Government Purpose Rights (GPR)")
    developed = ip.get("developed_under_contract", "Unlimited Rights")
    gov_data = ip.get("government_data", "Government owns all data and derivative models")
    entity_name = profile.get("entity_name", "[COMPANY]")
    ndc_status = profile.get("ndc_status", "")
    is_traditional = "traditional" in ndc_status.lower()

    teaming_section = ""
    if is_traditional:
        teaming_section = f"""**4.3 Teaming and Cost Share**

{entity_name} is a traditional defense contractor. Two paths to satisfy 10 U.S.C. 4022:

**Option A — NDC Teaming (Preferred):** {entity_name} is in discussions with NDC-eligible
AI/ML technology partners. An NDC team member will contribute the AI/ML core component
development to a significant extent (>1/3 of technical effort on novel components).
Specific NDC partner to be identified at the solicitation/proposal stage.

**Option B — IR&D Cost Share:** {entity_name} has sustained IR&D investments in the
proposed platform. A 1/3 cost share (~$490K of estimated $1.475M ROM) can be satisfied
from existing IR&D commitments.
"""
    else:
        teaming_section = "**4.3 Teaming:** N/A — NDC Status.\n"

    return f"""## Part 4: Business and Intellectual Property

**4.1 Data Rights**

| Component | Rights |
|-----------|--------|
| Core platform (prior company IR&D) | {prior_ip} |
| Integration adapters developed under this effort | {developed} |
| Models and data derived from customer operations | {gov_data} |

No proprietary markings restrict operational use of delivered prototype components.

**4.2 ROM Cost Estimate** *(ROM only — not binding, ±30%)*

| Category | Estimate |
|----------|---------|
| Labor (6 engineers × 6 months) | ~$1,200,000 |
| Software / Cloud infrastructure | ~$75,000 |
| Hardware (GPU dev/test cluster) | ~$150,000 |
| Other Direct Costs (travel, documentation) | ~$50,000 |
| **ROM Total** | **~$1,475,000** |

Annual O&M (post-prototype): ~$400,000/year.

{teaming_section}"""


def _draft_cover(profile: dict, rfi: dict, submission_date: str) -> str:
    entity_name = profile.get("entity_name", "[COMPANY]")
    rfi_number = rfi.get("rfi_number", "[RFI NUMBER]")
    title = rfi.get("title", "AI/ML Processing Orchestration")
    boilerplate = profile.get("boilerplate_cover", "").format(rfi_number=rfi_number)

    return f"""> UNCLASSIFIED//FOUO

# {entity_name}
## Response to {title}
### {rfi_number}
### Submitted: {submission_date}

---

{boilerplate}

---
"""


def generate_response(
    rfi: dict,
    profile_name: str = "own_company",
    output_dir: str = ".tmp",
    export_docx: bool = True,
    submission_date: str | None = None,
) -> dict:
    """
    Generate a full RFI response document.

    Args:
        rfi: Parsed RFI dict from rfi_document_parser.parse_rfi()
        profile_name: Company profile name from govcon_company_profiles.yaml
        output_dir: Directory for output files
        export_docx: Whether to also generate DOCX output

    Returns:
        dict with output file paths and generation metadata
    """
    import re

    profile = _load_profile(profile_name)
    scores = _load_capability_scores(rfi.get("objectives", []))
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    rfi_number = rfi.get("rfi_number", "UNKNOWN")
    entity_slug = re.sub(r"[^\w]", "_", profile.get("entity_name", "Company"))
    base_name = f"{entity_slug}_{rfi_number.replace('-', '_')}"

    if submission_date is None:
        submission_date = datetime.now(timezone.utc).strftime("%d %B %Y")

    # ── Assemble sections ──────────────────────────────────────────────────
    sections = []
    sections.append(_draft_cover(profile, rfi, submission_date))
    sections.append(_draft_part1(profile, rfi))

    # Part 2, 3, 4, 5 — placeholders for human/LLM completion
    sections.append("""## Part 2: Technical Approach

> **[ACTION REQUIRED]** Generate this section using:
> `hardprompts/govcon_rfi_part2_technical.md` (CoT)
>
> Key inputs to inject:
> - Objectives: see `{objectives}` from parsed RFI
> - Capability scores: see capability_scores in generated JSON
> - Three-tier stack: Rule Engine (<100µs) → CoD (<15ms) → CoT (<2s)
> - Cloud primary: AWS GovCloud / C2S (Bedrock + EKS + Kinesis)
> - Governance Suite: Traceability + Explainability + HITL
> - TRL: Hybrid TRL 6

See `hardprompts/govcon_rfi_part2_technical.md` for full CoT generation instructions.
""")

    sections.append(_draft_part3(rfi))
    sections.append(_draft_part4(profile, rfi))

    sections.append("""## Part 5: Industry Insights

> **[ACTION REQUIRED]** Generate this section using:
> `hardprompts/govcon_rfi_part5_insights.md` (CoD)
>
> Run 4 parallel debater agents (data lineage, federated learning, multi-IL, HITL experts)
> then judge synthesis. Target: 4 insights, ~250 words total.

See `hardprompts/govcon_rfi_part5_insights.md` for full CoD generation instructions.
""")

    sections.append("""## Technical Appendix

> **[ACTION REQUIRED]** Generate this section using:
> `hardprompts/govcon_rfi_appendix.md` (CoT)
>
> Appendix A: FORGE™ architecture — Intelligence Layer + Execution Layer
> Appendix B: NOVA™ adaptive learning loop (ECHO/SOUL/TRUST/SELA)
> Target: 2 pages (~1,000 words)

See `hardprompts/govcon_rfi_appendix.md` for full CoT generation instructions.
""")

    md_content = "\n\n".join(sections)

    # Write markdown
    md_path = output_path / f"{base_name}.md"
    md_path.write_text(md_content, encoding="utf-8")

    # Write capability scores JSON for human/LLM reference
    scores_path = output_path / f"{base_name}_capability_scores.json"
    scores_path.write_text(json.dumps({
        "rfi_number": rfi_number,
        "profile": profile_name,
        "objectives": rfi.get("objectives", []),
        "capability_scores": scores,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")

    result = {
        "status": "ok",
        "rfi_number": rfi_number,
        "profile": profile_name,
        "markdown": str(md_path),
        "markdown_content": md_content,
        "capability_scores": str(scores_path),
        "objectives_count": len(rfi.get("objectives", [])),
        "questionnaire_items": len(rfi.get("questionnaire_parts", [])),
        "action_required": ["Part 2 (CoT)", "Part 5 (CoD)", "Technical Appendix (CoT)"],
    }

    # Export DOCX
    if export_docx:
        try:
            from tools.govcon.rfi_docx_exporter import markdown_to_docx
            docx_path = output_path / f"{base_name}.docx"
            markdown_to_docx(md_content, str(docx_path))
            result["docx"] = str(docx_path)
        except Exception as exc:
            logger.warning(f"DOCX export failed: {exc}")
            result["docx_error"] = str(exc)

    logger.info("RFI response generated", extra={"rfi_number": rfi_number, "profile": profile_name})
    return result


# ── Import guard for capability scoring ──────────────────────────────────────
try:
    import re
except ImportError:
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate RFI response document")
    parser.add_argument("--input", help="Path to RFI PDF or DOCX (will be parsed)")
    parser.add_argument("--parsed", help="Path to pre-parsed RFI JSON (skips parsing)")
    parser.add_argument("--profile", default="own_company", help="Company profile name")
    parser.add_argument("--output-dir", default=".tmp", help="Output directory")
    parser.add_argument("--no-docx", action="store_true", help="Skip DOCX export")
    parser.add_argument("--json", action="store_true", help="Print JSON result to stdout")
    args = parser.parse_args()

    if not args.input and not args.parsed:
        print(json.dumps({"error": "Provide --input <rfi.pdf> or --parsed <parsed.json>"}))
        sys.exit(1)

    # Parse or load RFI
    if args.parsed:
        rfi = json.loads(Path(args.parsed).read_text(encoding="utf-8"))
    else:
        from tools.govcon.rfi_document_parser import parse_rfi
        try:
            rfi = parse_rfi(args.input)
        except (FileNotFoundError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}))
            sys.exit(1)

    try:
        result = generate_response(
            rfi,
            profile_name=args.profile,
            output_dir=args.output_dir,
            export_docx=not args.no_docx,
        )
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Generated: {result.get('markdown')} | DOCX: {result.get('docx', 'N/A')}")


if __name__ == "__main__":
    main()
