#!/usr/bin/env python3
# CUI // SP-CTI
"""COA (Courses of Action) Engine for Cloud Application Migration.

Generates structured migration path options with pros/cons, effort estimates,
risk levels, required tools, and AI opportunities for any source technology.

Data sources (no hardcoding):
  context/migration/service_mappings.yaml  — cloud targets + AI opps per tech
  context/migration/refactor_rules.yaml    — code transformation jobs per tech

Public API:
    get_coas(tech, cloud='aws') -> list[dict]
    get_deprecation_status(tech) -> dict
    explain_strategy(strategy_7r) -> dict
    get_canvas_links(project) -> dict
    format_coa_markdown(coas) -> str
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from pathlib import Path
from typing import Any

logger = get_logger("icdev.coa_engine")

_ICDEV_ROOT = Path(__file__).resolve().parent.parent.parent
_SERVICE_MAPPINGS_PATH = _ICDEV_ROOT / "context" / "migration" / "service_mappings.yaml"
_REFACTOR_RULES_PATH = _ICDEV_ROOT / "context" / "migration" / "refactor_rules.yaml"

# EOL/deprecation catalog — tech → {eol_date, reason, successor, severity}
# Entries sourced from endoflife.date and vendor advisories.
_EOL_CATALOG: dict[str, dict] = {
    "python2": {
        "eol_date": "2020-01-01", "severity": "critical",
        "reason": "Python 2 reached end-of-life on January 1 2020 — no security patches.",
        "successor": "Python 3.11+", "migration_path": "version_upgrade",
    },
    "java8": {
        "eol_date": "2030-12-31", "severity": "low",
        "reason": "Java 8 LTS is still supported but missing 15 years of platform features.",
        "successor": "Java 21 LTS", "migration_path": "version_upgrade",
    },
    "java11": {
        "eol_date": "2032-09-30", "severity": "low",
        "reason": "Java 11 LTS still supported — Java 21 LTS preferred for virtual threads.",
        "successor": "Java 21 LTS", "migration_path": "version_upgrade",
    },
    "struts": {
        "eol_date": "2023-12-31", "severity": "critical",
        "reason": "Apache Struts 2.x reached EOL; multiple critical CVEs (CVE-2018-11776, CVE-2023-50164).",
        "successor": "Spring Boot 3.x", "migration_path": "framework_migration",
    },
    "struts2": {
        "eol_date": "2023-12-31", "severity": "critical",
        "reason": "Apache Struts 2.x reached EOL; multiple critical CVEs.",
        "successor": "Spring Boot 3.x", "migration_path": "framework_migration",
    },
    "oracle": {
        "eol_date": "2027-07-01", "severity": "medium",
        "reason": "Oracle 19c extended support ends 2027; licensing costs prohibitive on cloud.",
        "successor": "Aurora PostgreSQL 15+", "migration_path": "db_ddl_generation",
    },
    "elasticsearch7": {
        "eol_date": "2024-08-31", "severity": "high",
        "reason": "Elasticsearch 7.x reached EOL August 2024; SSPL license blocks OSS use.",
        "successor": "OpenSearch 2.x (Apache 2.0)", "migration_path": "code_scaffold",
    },
    "elasticsearch6": {
        "eol_date": "2022-02-10", "severity": "critical",
        "reason": "Elasticsearch 6.x EOL February 2022.",
        "successor": "OpenSearch 2.x", "migration_path": "code_scaffold",
    },
    "log4j1": {
        "eol_date": "2015-08-05", "severity": "critical",
        "reason": "Log4j 1.x EOL since 2015 and contains known RCE vulnerabilities.",
        "successor": "Log4j 2.x or Logback", "migration_path": "version_upgrade",
    },
    "spring4": {
        "eol_date": "2020-12-31", "severity": "high",
        "reason": "Spring Framework 4.x reached EOL December 2020.",
        "successor": "Spring Boot 3.x / Spring Framework 6.x", "migration_path": "framework_migration",
    },
    "angular1": {
        "eol_date": "2021-12-31", "severity": "high",
        "reason": "AngularJS (Angular 1.x) reached EOL December 2021.",
        "successor": "Angular 17+ or React 18", "migration_path": "framework_migration",
    },
    "nifi": {
        "eol_date": None, "severity": "medium",
        "reason": "Apache NiFi is actively maintained but heavyweight for cloud-native; prefer managed ETL.",
        "successor": "AWS Glue + MSK + EventBridge", "migration_path": "code_scaffold",
    },
    "redis3": {
        "eol_date": "2022-06-01", "severity": "high",
        "reason": "Redis 3.x EOL. Redis 7.x requires commercial license for some use cases.",
        "successor": "ElastiCache for Redis 7 (Valkey)", "migration_path": "code_scaffold",
    },
}

# 7R strategy explanations
_7R_STRATEGIES: dict[str, dict] = {
    "rehost": {
        "label": "Rehost (Lift & Shift)",
        "description": "Move workload as-is to cloud VMs/containers with minimal changes.",
        "effort": "low", "risk": "low", "timeline_weeks": 2,
        "pros": ["Fastest path to cloud", "Minimal application changes", "Low risk", "Preserves existing skills"],
        "cons": ["No cloud-native benefits", "Still pays old licensing costs", "Technical debt persists", "No cost optimization"],
    },
    "replatform": {
        "label": "Replatform (Lift, Tinker & Shift)",
        "description": "Minor cloud optimizations without core architecture changes (e.g., OS to container).",
        "effort": "medium", "risk": "low", "timeline_weeks": 6,
        "pros": ["Moderate cloud savings", "Managed service reliability", "Reduced ops burden", "Incremental change"],
        "cons": ["Some refactoring needed", "Partial cloud-native", "Some vendor lock-in", "Limited scalability gains"],
    },
    "rearchitect": {
        "label": "Rearchitect (Re-architect)",
        "description": "Redesign the application to leverage cloud-native capabilities fully.",
        "effort": "high", "risk": "high", "timeline_weeks": 16,
        "pros": ["Maximum cloud-native benefits", "Best scalability", "Cost-optimized at scale", "AI/ML integration opportunities"],
        "cons": ["Highest effort and cost", "Longest timeline", "Requires deep expertise", "Business disruption risk"],
    },
    "repurchase": {
        "label": "Repurchase (Replace)",
        "description": "Replace the existing system with a SaaS solution.",
        "effort": "medium", "risk": "medium", "timeline_weeks": 8,
        "pros": ["Immediate SaaS benefits", "No infrastructure management", "Built-in updates", "Vendor expertise"],
        "cons": ["Data migration complexity", "Customization limits", "Ongoing SaaS costs", "Vendor dependency"],
    },
    "retain": {
        "label": "Retain (Revisit later)",
        "description": "Keep the application on-premises for now and revisit migration later.",
        "effort": "none", "risk": "none", "timeline_weeks": 0,
        "pros": ["No disruption", "Keep stable systems running", "Buy time for planning", "Regulatory compliance"],
        "cons": ["Continues on-prem costs", "Misses cloud benefits", "Technical debt grows", "Harder to migrate later"],
    },
    "retire": {
        "label": "Retire (Decommission)",
        "description": "Decommission the application — no longer needed or superseded.",
        "effort": "low", "risk": "low", "timeline_weeks": 4,
        "pros": ["Eliminates maintenance costs", "Simplifies portfolio", "Reduces attack surface", "Frees team capacity"],
        "cons": ["Data preservation required", "User impact if still used", "Dependencies must be resolved", "May need replacement"],
    },
    "relocate": {
        "label": "Relocate (Hypervisor migration)",
        "description": "Move from on-premises hypervisor to cloud hypervisor (VMware to VMware Cloud).",
        "effort": "low", "risk": "low", "timeline_weeks": 3,
        "pros": ["VMware expertise reuse", "Minimal changes", "Fast migration", "Familiar tooling"],
        "cons": ["Limited cloud-native benefits", "Higher cost than containers", "Hypervisor dependency", "Partial savings only"],
    },
}


def _load_yaml(path: Path) -> dict:
    """Load YAML file gracefully. Returns {} if unavailable."""
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (ImportError, FileNotFoundError, Exception) as exc:
        logger.debug("YAML load failed %s: %s", path, exc)
        return {}


def _load_service_mappings() -> dict:
    data = _load_yaml(_SERVICE_MAPPINGS_PATH)
    return data.get("mappings", {})


def _load_refactor_rules() -> list:
    data = _load_yaml(_REFACTOR_RULES_PATH)
    return data.get("rules", [])


def _normalize_tech(tech: str) -> str:
    """Normalize tech name for lookup."""
    return tech.lower().strip().replace("-", "").replace("_", "").replace(" ", "")


def _match_tech(query: str, mapping_key: str) -> bool:
    """Check if query matches a tech mapping key."""
    q = _normalize_tech(query)
    k = _normalize_tech(mapping_key)
    return q == k or q in k or k in q


def _find_mapping(tech: str) -> tuple[str, dict] | tuple[None, None]:
    """Find the best service mapping for a technology name. Returns (key, mapping) or (None, None)."""
    mappings = _load_service_mappings()
    for key, mapping in mappings.items():
        if _match_tech(tech, key):
            return key, mapping
        label = mapping.get("label", "")
        if _match_tech(tech, label):
            return key, mapping
    return None, None


def get_deprecation_status(tech: str) -> dict:
    """Check EOL/deprecation status for a technology.

    Returns dict: {tech, status, eol_date?, severity, reason?, successor?, migration_path?}
    """
    normalized = _normalize_tech(tech)
    for eol_key, eol_info in _EOL_CATALOG.items():
        if _normalize_tech(eol_key) in normalized or normalized in _normalize_tech(eol_key):
            return {
                "tech": tech,
                "status": "deprecated" if eol_info.get("eol_date") else "at_risk",
                **eol_info,
            }

    # Check service mappings for any hints
    key, mapping = _find_mapping(tech)
    if mapping:
        strategy = mapping.get("strategy_7r", "replatform")
        label = mapping.get("label", tech)
        return {
            "tech": tech,
            "status": "active",
            "severity": "info",
            "reason": f"{label} is actively maintained. Strategy: {_7R_STRATEGIES.get(strategy, {}).get('label', strategy)}.",
            "successor": mapping.get("aws_service", ""),
            "migration_path": "code_scaffold",
        }

    return {
        "tech": tech,
        "status": "unknown",
        "severity": "info",
        "reason": f"No EOL data found for '{tech}'. Check vendor release notes.",
        "successor": None,
        "migration_path": None,
    }


def get_coas(tech: str, cloud: str = "aws", include_retain_retire: bool = True) -> list[dict]:
    """Generate Courses of Action for a source technology.

    Returns list of COA dicts, each with:
      title, strategy, strategy_label, target_service, pros[], cons[],
      effort, effort_days, risk, risk_label, tools[], ai_opportunities[],
      canvas_hints{}, recommended (bool)
    """
    key, mapping = _find_mapping(tech)

    if not mapping:
        # Fallback — return generic 7R options without service specifics
        return _generic_coas(tech, cloud, include_retain_retire)

    label = mapping.get("label", tech)
    category = mapping.get("category", "unknown")
    primary_strategy = mapping.get("strategy_7r", "replatform")
    migration_tools = mapping.get("migration_tools", [])
    ai_opps = mapping.get("ai_opportunities", [])

    # Target services by cloud
    if cloud == "aws":
        target_service = mapping.get("aws_service", "AWS managed service")
    elif cloud == "azure":
        target_service = mapping.get("azure_service", "Azure managed service")
    elif cloud == "gcp":
        target_service = mapping.get("gcp_service", "GCP managed service")
    else:
        target_service = mapping.get("aws_service", "Managed service")

    # Get refactor jobs for this tech
    refactor_jobs = _get_refactor_jobs_for_tech(key or tech, primary_strategy)

    coas: list[dict] = []

    # Primary strategy COA (recommended)
    primary_7r = _7R_STRATEGIES.get(primary_strategy, _7R_STRATEGIES["replatform"])
    effort_days = _estimate_effort_days(category, primary_strategy, ai_opps)
    coas.append({
        "id": f"coa-{_normalize_tech(tech)}-{primary_strategy}",
        "title": f"Migrate {label} to {target_service}",
        "strategy": primary_strategy,
        "strategy_label": primary_7r["label"],
        "target_service": target_service,
        "target_service_id": mapping.get(f"{cloud}_service_id", ""),
        "category": category,
        "pros": _build_pros(primary_strategy, category, mapping, cloud, ai_opps),
        "cons": _build_cons(primary_strategy, category, mapping),
        "effort": primary_7r["effort"],
        "effort_days": effort_days,
        "timeline_weeks": primary_7r["timeline_weeks"],
        "risk": primary_7r["risk"],
        "risk_label": _risk_label(primary_7r["risk"]),
        "tools": migration_tools,
        "ai_opportunities": ai_opps,
        "refactor_jobs": refactor_jobs,
        "canvas_hints": _canvas_hints(category),
        "recommended": True,
        "recommended_reason": _recommended_reason(primary_strategy, category),
    })

    # Alternative: Rehost (if primary is not rehost)
    if primary_strategy != "rehost" and category not in ("database",):
        rehost_7r = _7R_STRATEGIES["rehost"]
        rehost_target = _rehost_target(tech, cloud)
        coas.append({
            "id": f"coa-{_normalize_tech(tech)}-rehost",
            "title": f"Rehost {label} to {rehost_target}",
            "strategy": "rehost",
            "strategy_label": rehost_7r["label"],
            "target_service": rehost_target,
            "target_service_id": "",
            "category": category,
            "pros": rehost_7r["pros"],
            "cons": rehost_7r["cons"] + [f"No {cloud.upper()} managed service benefits for {label}"],
            "effort": rehost_7r["effort"],
            "effort_days": max(3, effort_days // 4),
            "timeline_weeks": rehost_7r["timeline_weeks"],
            "risk": rehost_7r["risk"],
            "risk_label": _risk_label(rehost_7r["risk"]),
            "tools": ["eksctl", "kubectl", "helm"] if category == "container-orchestration" else ["aws-vm-import"],
            "ai_opportunities": [],
            "refactor_jobs": [],
            "canvas_hints": _canvas_hints(category),
            "recommended": False,
            "recommended_reason": None,
        })

    # Repurchase / SaaS alternative for certain categories
    repurchase_option = _get_repurchase_option(category, tech, cloud)
    if repurchase_option:
        coas.append(repurchase_option)

    # Retain option
    if include_retain_retire:
        retain_7r = _7R_STRATEGIES["retain"]
        coas.append({
            "id": f"coa-{_normalize_tech(tech)}-retain",
            "title": f"Retain {label} On-Premises",
            "strategy": "retain",
            "strategy_label": retain_7r["label"],
            "target_service": "On-premises (no change)",
            "target_service_id": "",
            "category": category,
            "pros": retain_7r["pros"],
            "cons": retain_7r["cons"] + [f"Misses AI opportunities available on {cloud.upper()}"],
            "effort": retain_7r["effort"],
            "effort_days": 0,
            "timeline_weeks": retain_7r["timeline_weeks"],
            "risk": retain_7r["risk"],
            "risk_label": _risk_label(retain_7r["risk"]),
            "tools": [],
            "ai_opportunities": [],
            "refactor_jobs": [],
            "canvas_hints": {},
            "recommended": False,
            "recommended_reason": None,
        })

    return coas


def get_all_coas_for_stack(components: list[dict], cloud: str = "aws") -> dict:
    """Generate COAs for a full application stack.

    components: list of {name, tech, strategy_7r?, ...} dicts from mc_app_inventory

    Returns {tech: [coa...], summary: {...}}
    """
    result: dict[str, Any] = {}
    for comp in components:
        tech = comp.get("app_type") or comp.get("name", "")
        if not tech:
            continue
        result[tech] = get_coas(tech, cloud=cloud, include_retain_retire=False)

    total_effort = sum(
        coas[0]["effort_days"] if coas else 0
        for coas in result.values()
    )
    recommended_strategies = {
        tech: (coas[0]["strategy"] if coas else "retain")
        for tech, coas in result.items()
    }

    result["_summary"] = {
        "total_components": len(components),
        "total_effort_days": total_effort,
        "estimated_weeks": round(total_effort / 5, 1),
        "recommended_strategies": recommended_strategies,
        "ai_opps_total": sum(
            len(coas[0].get("ai_opportunities", [])) if coas else 0
            for coas in result.values()
            if isinstance(coas, list)
        ),
    }
    return result


def explain_strategy(strategy_7r: str) -> dict:
    """Return full 7R strategy description with pros/cons."""
    info = _7R_STRATEGIES.get(strategy_7r.lower(), _7R_STRATEGIES["replatform"])
    return {"strategy": strategy_7r, **info}


def format_coa_markdown(coas: list[dict]) -> str:
    """Format COA list as a markdown response for plain-text chat."""
    if not coas:
        return "No COAs available for this technology."

    lines = []
    for i, coa in enumerate(coas, 1):
        rec_marker = " *** RECOMMENDED ***" if coa.get("recommended") else ""
        title_safe = coa['title'].replace('→', '->')
        lines.append(f"\n### COA {i}: {title_safe}{rec_marker}")
        lines.append(f"**Strategy:** {coa['strategy_label']}  |  "
                     f"**Effort:** {coa['effort_days']}d  |  "
                     f"**Risk:** {coa['risk_label']}  |  "
                     f"**Timeline:** {coa.get('timeline_weeks', '?')} weeks")

        if coa.get("recommended_reason"):
            lines.append(f"\n> {coa['recommended_reason']}")

        pros = coa.get("pros", [])
        cons = coa.get("cons", [])
        if pros:
            lines.append("\n**Pros:**")
            for p in pros[:4]:
                lines.append(f"  + {p}")
        if cons:
            lines.append("\n**Cons:**")
            for c in cons[:4]:
                lines.append(f"  - {c}")

        tools = coa.get("tools", [])
        if tools:
            lines.append(f"\n**Tools:** {', '.join(tools)}")

        ai_opps = coa.get("ai_opportunities", [])
        if ai_opps:
            opp_titles = [o.get("title", o.get("id", "")) for o in ai_opps[:3]]
            lines.append(f"\n**AI Opportunities:** {'; '.join(opp_titles)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _estimate_effort_days(category: str, strategy: str, ai_opps: list) -> int:
    base = {
        "database": {"rehost": 5, "replatform": 15, "rearchitect": 45},
        "cache": {"rehost": 2, "replatform": 7, "rearchitect": 14},
        "search": {"rehost": 3, "replatform": 10, "rearchitect": 21},
        "pipeline": {"rehost": 5, "replatform": 20, "rearchitect": 40},
        "frontend": {"rehost": 3, "replatform": 10, "rearchitect": 21},
        "container-orchestration": {"rehost": 7, "replatform": 14, "rearchitect": 30},
        "messaging": {"rehost": 3, "replatform": 10, "rearchitect": 21},
    }.get(category, {"rehost": 3, "replatform": 10, "rearchitect": 21})
    days = base.get(strategy, base.get("replatform", 10))
    # Add AI integration effort
    days += sum(o.get("effort_days", 0) for o in ai_opps) if strategy == "rearchitect" else 0
    return days


def _build_pros(strategy: str, category: str, mapping: dict, cloud: str, ai_opps: list) -> list[str]:
    base_pros = _7R_STRATEGIES.get(strategy, {}).get("pros", [])[:2]
    extras = []
    if cloud == "aws":
        target = mapping.get("aws_service", "AWS managed service")
    elif cloud == "azure":
        target = mapping.get("azure_service", "Azure managed service")
    else:
        target = mapping.get("gcp_service", "GCP managed service")

    extras.append(f"Managed {target} — no patching, automatic backups")
    extras.append("Pay-as-you-go pricing vs. perpetual licensing")

    if ai_opps:
        first_opp = ai_opps[0]
        extras.append(f"Enables AI: {first_opp.get('title', first_opp.get('id', 'AI integration'))}")

    if category == "database":
        extras.append("Automated failover + Multi-AZ HA")
    elif category == "search":
        extras.append("Vector search and semantic RAG capabilities")
    elif category == "pipeline":
        extras.append("Serverless pipeline execution — no cluster management")
    elif category == "frontend":
        extras.append("Global CDN distribution with edge caching")

    return (base_pros + extras)[:6]


def _build_cons(strategy: str, category: str, mapping: dict) -> list[str]:
    base_cons = _7R_STRATEGIES.get(strategy, {}).get("cons", [])[:2]
    extras = []
    if category == "database":
        extras.append("SQL dialect differences (e.g., Oracle PL/SQL → PostgreSQL PL/pgSQL)")
        extras.append("Schema conversion testing required")
    elif category == "search":
        extras.append("API compatibility differences between Elasticsearch and OpenSearch")
    elif category == "pipeline":
        extras.append("Flow conversion effort — NiFi flows must be rewritten as Glue/MSK")
    elif category == "frontend":
        extras.append("Build pipeline migration to Amplify CI/CD")
    elif category == "cache":
        extras.append("Cache warming period after migration")
    extras.append("Vendor lock-in to managed service")
    return (base_cons + extras)[:6]


def _risk_label(risk: str) -> str:
    return {"low": "Low", "medium": "Medium", "high": "High", "none": "None"}.get(risk, risk.capitalize())


def _canvas_hints(category: str) -> dict:
    """Return relevant canvas URLs/hints for a component category."""
    hints = {}
    if category in ("database",):
        hints["ddc"] = "View Oracle → Aurora lineage in DDC"
    if category in ("container-orchestration",):
        hints["idc"] = "View K8s → EKS migration in IDC"
    hints["ndc"] = "View AWS VPC landing zone in NDC"
    hints["mc"] = "View migration project in Migration Canvas"
    return hints


def _recommended_reason(strategy: str, category: str) -> str:
    reasons = {
        ("rearchitect", "database"): "Oracle→Aurora rearchitect unlocks pgvector, Bedrock NLQ, and multi-AZ HA at lower TCO than Oracle licensing.",
        ("replatform", "cache"): "Redis→ElastiCache replatform provides managed HA with <1hr effort for most workloads.",
        ("replatform", "search"): "ES→OpenSearch replatform preserves all APIs while enabling Bedrock Knowledge Bases for semantic RAG.",
        ("rearchitect", "pipeline"): "NiFi→Glue+MSK rearchitect eliminates cluster ops and unlocks Bedrock Data Automation for AI-driven transforms.",
        ("replatform", "frontend"): "React→Amplify replatform adds global CDN, auto HTTPS, and CI/CD with minimal frontend code changes.",
        ("rehost", "container-orchestration"): "K8s→EKS rehost is the fastest interim step; migrate to Fargate progressively.",
    }
    return reasons.get((strategy, category), f"{_7R_STRATEGIES.get(strategy, {}).get('label', strategy)} is the recommended path for {category} workloads.")


def _rehost_target(tech: str, cloud: str) -> str:
    targets = {
        "aws": "Amazon EC2 + EKS",
        "azure": "Azure VM + AKS",
        "gcp": "Compute Engine + GKE",
    }
    return targets.get(cloud, "Cloud VM")


def _get_repurchase_option(category: str, tech: str, cloud: str) -> dict | None:
    saas_map = {
        "pipeline": {
            "aws": "Amazon MWAA (Managed Airflow) or Step Functions",
            "azure": "Azure Data Factory", "gcp": "Cloud Composer",
        },
        "search": {
            "aws": "Amazon Kendra (enterprise search)",
            "azure": "Azure AI Search", "gcp": "Vertex AI Search",
        },
    }
    saas = saas_map.get(category, {}).get(cloud)
    if not saas:
        return None

    repurchase_7r = _7R_STRATEGIES["repurchase"]
    return {
        "id": f"coa-{_normalize_tech(tech)}-repurchase",
        "title": f"Replace {tech.title()} via {saas}",
        "strategy": "repurchase",
        "strategy_label": repurchase_7r["label"],
        "target_service": saas,
        "target_service_id": "",
        "category": category,
        "pros": repurchase_7r["pros"] + [f"{saas} is fully managed with built-in scaling"],
        "cons": repurchase_7r["cons"] + ["Custom flow logic may not map 1:1 to SaaS"],
        "effort": repurchase_7r["effort"],
        "effort_days": 15,
        "timeline_weeks": repurchase_7r["timeline_weeks"],
        "risk": repurchase_7r["risk"],
        "risk_label": _risk_label(repurchase_7r["risk"]),
        "tools": [],
        "ai_opportunities": [],
        "refactor_jobs": [],
        "canvas_hints": {},
        "recommended": False,
        "recommended_reason": None,
    }


def _get_refactor_jobs_for_tech(tech_key: str, strategy: str) -> list[dict]:
    """Return matching refactor rule descriptions for a tech."""
    rules = _load_refactor_rules()
    jobs = []
    for rule in rules:
        if _normalize_tech(rule.get("match_tech", "")) in _normalize_tech(tech_key):
            strat = rule.get("match_strategy", "")
            if not strat or strat == strategy:
                for job in rule.get("jobs", []):
                    jobs.append({
                        "type": job.get("type", ""),
                        "description": job.get("description", ""),
                    })
    return jobs[:4]


def _generic_coas(tech: str, cloud: str, include_retain_retire: bool) -> list[dict]:
    """Fallback COAs when tech is not in service_mappings."""
    coas = []
    for strategy in ("replatform", "rearchitect"):
        s = _7R_STRATEGIES[strategy]
        target = {"aws": "AWS managed service", "azure": "Azure managed service", "gcp": "GCP managed service"}.get(cloud, "Cloud service")
        coas.append({
            "id": f"coa-{_normalize_tech(tech)}-{strategy}",
            "title": f"{s['label']} {tech}",
            "strategy": strategy,
            "strategy_label": s["label"],
            "target_service": target,
            "target_service_id": "",
            "category": "unknown",
            "pros": s["pros"],
            "cons": s["cons"],
            "effort": s["effort"],
            "effort_days": s["timeline_weeks"] * 5,
            "timeline_weeks": s["timeline_weeks"],
            "risk": s["risk"],
            "risk_label": _risk_label(s["risk"]),
            "tools": [],
            "ai_opportunities": [],
            "refactor_jobs": [],
            "canvas_hints": {},
            "recommended": strategy == "replatform",
            "recommended_reason": "Replatform is typically the best starting point for unknown workloads." if strategy == "replatform" else None,
        })
    return coas
