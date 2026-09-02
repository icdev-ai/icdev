# CUI // SP-CTI
"""Agentic AI Design Canvas — Accreditation Package Builder (Phase 7).

Assembles all governance artifacts for a design into a downloadable ZIP:
  - oscal-component.json
  - threat-model.json
  - risk-register.json
  - ato-checklist.json
  - regulatory-gaps.json
  - red-team-report.json
  - exec-summary.json
  - assessment.json
  - README.md (package cover sheet)

The ZIP mechanics and the cover sheet live in
``tools.compliance.ato_packager`` — this module decides only WHICH artifacts an
AADC design contributes and HOW its posture reads. That split is the point:
``POST /api/ato-package/generate`` needed a packager and this one worked, so it
was generalised rather than forked (rmf-inert-01).
"""

from __future__ import annotations

from tools.compliance import ato_packager


def build_accred_zip(
    design: dict,
    assessment: dict | None,
    risks: list[dict],
    threat_model_data: dict | None,
    ato_data: dict,
    reg_data: dict,
    red_team_data: dict,
    exec_data: dict,
    oscal_data: dict,
) -> bytes:
    """
    Build an accreditation package ZIP and return raw bytes.

    Args:
        All data dicts as produced by the respective AADC modules.

    Returns:
        ZIP file as bytes (write to HTTP response or disk).
    """
    design_id = design.get("id", "unknown")

    artifacts = [
        ato_packager.PackageArtifact(
            f"oscal-component-{design_id}.json",
            oscal_data,
            "OSCAL 1.1 Component Definition (FedRAMP/ATO)",
        ),
        ato_packager.PackageArtifact(
            f"threat-model-{design_id}.json",
            threat_model_data or {},
            "STRIDE + ATLAS threat model",
        ),
        ato_packager.PackageArtifact(
            f"risk-register-{design_id}.json",
            {"risks": risks, "design_id": design_id},
            "Risk register items",
        ),
        ato_packager.PackageArtifact(
            f"ato-checklist-{design_id}.json", ato_data, "ATO readiness checklist"
        ),
        ato_packager.PackageArtifact(
            f"regulatory-gaps-{design_id}.json",
            reg_data,
            "Regulatory gap analysis (EU AI Act / DoD / OMB)",
        ),
        ato_packager.PackageArtifact(
            f"red-team-report-{design_id}.json",
            red_team_data,
            "AI red team adversarial analysis",
        ),
        ato_packager.PackageArtifact(
            f"exec-summary-{design_id}.json", exec_data, "Executive summary report"
        ),
    ]
    if assessment:
        artifacts.append(
            ato_packager.PackageArtifact(
                f"assessment-{design_id}.json",
                assessment,
                "Latest NIST AI RMF / OWASP assessment",
            )
        )

    summary = ato_data.get("summary", {})
    metrics = [
        (
            "Combined Posture",
            f"**{exec_data.get('posture_rating', 'UNRATED')}** ({exec_data.get('combined_score', 0)}%)",
        ),
        ("Assessment Score", f"{exec_data.get('overall_score', 0)}%"),
        ("ATO Readiness Score", f"{exec_data.get('ato_score', 0)}%"),
        ("Regulatory Score", f"{exec_data.get('reg_score', 0)}%"),
        ("ATO Ready", "✓ YES" if summary.get("ato_ready") else "✗ NO"),
        ("NIST AI RMF", f"{exec_data.get('nist_score', 0)}%"),
        ("OWASP LLM", f"{exec_data.get('owasp_score', 0)}%"),
    ]

    return ato_packager.build_package_zip(
        subject={
            "id": design_id,
            "name": design.get("name", "Unnamed"),
            "classification": design.get("classification", "CUI"),
            "id_label": "Design ID",
            "metadata": [
                ("Domain", design.get("domain", "unspecified")),
                ("Safety Impacting", "Yes" if design.get("safety_impacting") else "No"),
                ("Rights Impacting", "Yes" if design.get("rights_impacting") else "No"),
            ],
        },
        artifacts=artifacts,
        title="Accreditation Package",
        metrics=metrics,
        gaps=exec_data.get("critical_gaps", []),
        actions=exec_data.get("recommended_actions", []),
        generator="ICDEV™ AADC — Agentic AI Design Canvas",
    )
