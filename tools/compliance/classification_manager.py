#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Classification and Marking Manager for ICDEV.

Handles CUI, SECRET, and TOP SECRET classification markings and maps DoD
Impact Levels (IL2-IL6) to compliance baselines, encryption requirements,
network requirements, and cloud environments.

Loads impact-level profiles from context/compliance/impact_level_profiles.json
and marking configuration from args/classification_markings.yaml (with
backward-compatible fallback to args/cui_markings.yaml).

CLI:
    python tools/compliance/classification_manager.py --impact-level IL5
    python tools/compliance/classification_manager.py --classification SECRET --banner
    python tools/compliance/classification_manager.py --code-header python --classification CUI
    python tools/compliance/classification_manager.py --cross-domain IL4 IL6
    python tools/compliance/classification_manager.py --validate proj-123 --json
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
IL_PROFILES_PATH = BASE_DIR / "context" / "compliance" / "impact_level_profiles.json"
MARKINGS_PATH = BASE_DIR / "args" / "classification_markings.yaml"
CUI_MARKINGS_PATH = BASE_DIR / "args" / "cui_markings.yaml"

VALID_CLASSIFICATIONS = ("PUBLIC", "CUI", "SECRET", "TOP SECRET", "TOP SECRET//SCI")
VALID_IMPACT_LEVELS = ("IL2", "IL4", "IL5", "IL6")

# Module-level caches (populated on first call)
_IL_PROFILES_CACHE: Optional[Dict] = None
_MARKINGS_CACHE: Optional[Dict] = None

# Classification-to-impact-level mapping
_CLASSIFICATION_MAP = {
    "IL2": "PUBLIC",
    "IL4": "CUI",
    "IL5": "CUI",
    "IL6": "SECRET",
}

# Language comment-style mapping (language key -> prefix style)
# Kept in-module so the tool works standalone without language_support.py.
_COMMENT_STYLES = {
    "python":     "hash",
    "ruby":       "hash",
    "java":       "c-style",
    "go":         "c-style",
    "rust":       "c-style",
    "csharp":     "c-style",
    "c#":         "c-style",
    "typescript":  "c-style",
    "javascript":  "c-style",
    "xml":        "xml-style",
    "html":       "xml-style",
}

# Portion-marking shortcuts
_PORTION_MARKS = {
    "PUBLIC":          "",
    "CUI":             "(CUI)",
    "SECRET":          "(S)",
    "TOP SECRET":      "(TS)",
    "TOP SECRET//SCI": "(TS//SCI)",
}


# ---------------------------------------------------------------------------
# Profile / config loaders
# ---------------------------------------------------------------------------

def load_impact_level_profiles() -> Dict:
    """Load and cache DoD Impact Level profiles from JSON.

    Returns:
        Dict keyed by impact level (IL2, IL4, IL5, IL6) with full profile
        data including classification, compliance baselines, encryption and
        network requirements.

    Falls back to a minimal default dict when the file is missing so that
    downstream functions still return sensible CUI defaults.
    """
    global _IL_PROFILES_CACHE

    if _IL_PROFILES_CACHE is not None:
        return _IL_PROFILES_CACHE

    if IL_PROFILES_PATH.exists():
        try:
            with open(IL_PROFILES_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            _IL_PROFILES_CACHE = raw.get("profiles", raw)
            # Also stash cross-domain requirements alongside profiles
            if "cross_domain_requirements" in raw:
                _IL_PROFILES_CACHE["_cross_domain"] = raw["cross_domain_requirements"]
            return _IL_PROFILES_CACHE
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"Warning: Could not load IL profiles ({exc}); "
                "using built-in CUI defaults.",
                file=sys.stderr,
            )

    # Minimal fallback
    _IL_PROFILES_CACHE = {
        "IL4": {
            "classification": "CUI",
            "fedramp_baseline": "moderate",
            "cmmc_level": 2,
            "nist_800_171_required": True,
            "cloud_environments": ["aws-govcloud"],
            "marking_banner": "CUI // SP-CTI",
            "marking_category": "CTI",
            "required_controls_overlay": [],
            "encryption_requirements": {
                "at_rest": "FIPS 140-2 validated modules",
                "in_transit": "TLS 1.2+ with FIPS 140-2 validated modules",
                "key_management": "Customer managed keys in FIPS 140-2 validated HSM",
            },
            "network_requirements": {
                "dedicated_infrastructure": False,
                "cross_domain": False,
                "vpn_required": True,
                "sipr_only": False,
            },
        },
    }
    return _IL_PROFILES_CACHE


def load_markings_config() -> Dict:
    """Load and cache classification-marking YAML configuration.

    Tries ``args/classification_markings.yaml`` first, then falls back to
    ``args/cui_markings.yaml`` for backward compatibility, and finally to
    hard-coded CUI defaults if neither file is available.

    Returns:
        Dict with keys like ``banner_top``, ``banner_bottom``,
        ``designation_indicator``, ``portion_marking``, ``code_header``,
        ``document_header``, ``document_footer``.
    """
    global _MARKINGS_CACHE

    if _MARKINGS_CACHE is not None:
        return _MARKINGS_CACHE

    # Hard-coded defaults (CUI)
    defaults: Dict[str, Any] = {
        "banner_top": "CUI // SP-CTI",
        "banner_bottom": "CUI // SP-CTI",
        "designation_indicator": {
            "controlled_by": "Department of Defense",
            "categories": "CTI",
            "distribution": "Distribution D",
            "poc": "ICDEV System Administrator",
        },
        "portion_marking": "(CUI)",
        "decontrol_instructions": "Decontrol on: 10 years from creation date",
        "code_header": (
            "CUI // SP-CTI\n"
            "Controlled by: Department of Defense\n"
            "CUI Category: CTI\n"
            "Distribution: D\n"
            "POC: ICDEV System Administrator"
        ),
        "document_header": (
            "////////////////////////////////////////////////////////////////////\n"
            "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
            "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
            "////////////////////////////////////////////////////////////////////"
        ),
        "document_footer": (
            "////////////////////////////////////////////////////////////////////\n"
            "CUI // SP-CTI | Department of Defense\n"
            "////////////////////////////////////////////////////////////////////"
        ),
    }

    # Try primary path, then fallback
    for config_path in (MARKINGS_PATH, CUI_MARKINGS_PATH):
        if not config_path.exists():
            continue
        try:
            import yaml  # type: ignore[import-untyped]

            with open(config_path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh)
            if loaded and isinstance(loaded, dict):
                for key, value in loaded.items():
                    defaults[key] = value
            break  # stop after the first successful load
        except ImportError:
            # PyYAML not available -- simple key: value parsing
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    _parse_simple_yaml(fh.read(), defaults)
                break
            except Exception:
                continue
        except Exception:
            continue

    _MARKINGS_CACHE = defaults
    return _MARKINGS_CACHE


def _parse_simple_yaml(content: str, config: Dict) -> None:
    """Minimal YAML-like parser for flat ``key: value`` and ``|`` blocks."""
    lines = content.split("\n")
    current_key: Optional[str] = None
    multiline_buf: List[str] = []
    in_multiline = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if in_multiline:
                multiline_buf.append("")
            continue

        if in_multiline:
            if line and not line[0].isspace():
                config[current_key] = "\n".join(multiline_buf).strip()  # type: ignore[index]
                in_multiline = False
                multiline_buf = []
            else:
                multiline_buf.append(line.strip())
                continue

        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "|":
                current_key = key
                in_multiline = True
                multiline_buf = []
            elif value and not value.startswith("{"):
                value = value.strip('"').strip("'")
                config[key] = value

    if in_multiline and multiline_buf:
        config[current_key] = "\n".join(multiline_buf).strip()  # type: ignore[index]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return a database connection with Row factory."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn: sqlite3.Connection, project_id: str) -> Dict:
    """Load project row from the ``projects`` table."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _log_audit_event(
    conn: sqlite3.Connection,
    project_id: str,
    action: str,
    details: Dict,
) -> None:
    """Log an append-only audit event (NIST 800-53 AU compliant)."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "classification.validation",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps([]),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as exc:
        print(
            f"Warning: Could not log audit event: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_impact_level_profile(il_level: str) -> Dict:
    """Return the full profile dict for a given impact level.

    Args:
        il_level: One of IL2, IL4, IL5, IL6.

    Returns:
        Dict containing classification, compliance, encryption, network,
        and other profile fields.

    Raises:
        ValueError: If *il_level* is not a valid impact level.
    """
    il_upper = il_level.upper()
    if il_upper not in VALID_IMPACT_LEVELS:
        raise ValueError(
            f"Invalid impact level '{il_level}'. "
            f"Valid levels: {', '.join(VALID_IMPACT_LEVELS)}"
        )

    profiles = load_impact_level_profiles()
    profile = profiles.get(il_upper)
    if profile is None:
        raise ValueError(
            f"Profile for '{il_upper}' not found in impact level data. "
            f"Available profiles: {', '.join(k for k in profiles if not k.startswith('_'))}"
        )
    return profile


def get_classification_for_il(il_level: str) -> str:
    """Return the classification string for an impact level.

    Mapping:
        IL2 -> PUBLIC, IL4 -> CUI, IL5 -> CUI, IL6 -> SECRET.

    Args:
        il_level: One of IL2, IL4, IL5, IL6.

    Returns:
        Classification string.

    Raises:
        ValueError: If *il_level* is not valid.
    """
    il_upper = il_level.upper()
    if il_upper not in VALID_IMPACT_LEVELS:
        raise ValueError(
            f"Invalid impact level '{il_level}'. "
            f"Valid levels: {', '.join(VALID_IMPACT_LEVELS)}"
        )

    # Prefer the authoritative mapping from profiles when available
    profiles = load_impact_level_profiles()
    profile = profiles.get(il_upper)
    if profile and "classification" in profile:
        return profile["classification"]

    return _CLASSIFICATION_MAP.get(il_upper, "CUI")


def get_marking_banner(
    classification: str = "CUI",
    category: str = "CTI",
    dissemination: Optional[str] = None,
) -> str:
    """Return a full multi-line document banner block for a classification.

    Args:
        classification: One of PUBLIC, CUI, SECRET, TOP SECRET,
            TOP SECRET//SCI.
        category: Sub-category (default CTI for CUI).
        dissemination: Override for the dissemination/caveats line.
            Defaults to NOFORN for SECRET, SCI for TOP SECRET, etc.

    Returns:
        Multi-line string suitable for document headers/footers.
    """
    cls_upper = classification.upper()

    if cls_upper == "PUBLIC":
        return (
            "////////////////////////////////////////////////////////////////////\n"
            "PUBLIC RELEASE -- No restrictions on distribution\n"
            "////////////////////////////////////////////////////////////////////"
        )

    if cls_upper == "CUI":
        banner_line = f"CUI // SP-{category}"
        dist_line = "Distribution: Distribution D -- Authorized DoD Personnel Only"
        ctrl_line = "Controlled by: Department of Defense"
        return (
            f"////////////////////////////////////////////////////////////////////\n"
            f"CONTROLLED UNCLASSIFIED INFORMATION ({banner_line})\n"
            f"{dist_line}\n"
            f"{ctrl_line}\n"
            f"////////////////////////////////////////////////////////////////////"
        )

    if cls_upper == "SECRET":
        caveat = dissemination or "NOFORN"
        banner_line = f"SECRET // {caveat}"
        dist_line = "Distribution: Authorized SECRET-cleared personnel only"
        ctrl_line = "Controlled by: Department of Defense"
        return (
            f"////////////////////////////////////////////////////////////////////\n"
            f"{banner_line}\n"
            f"{dist_line}\n"
            f"{ctrl_line}\n"
            f"////////////////////////////////////////////////////////////////////"
        )

    if cls_upper == "TOP SECRET":
        caveat = dissemination or "SCI"
        banner_line = f"TOP SECRET // {caveat}"
        dist_line = "Distribution: Authorized TS/SCI-cleared personnel only"
        ctrl_line = "Controlled by: Department of Defense"
        return (
            f"////////////////////////////////////////////////////////////////////\n"
            f"{banner_line}\n"
            f"{dist_line}\n"
            f"{ctrl_line}\n"
            f"////////////////////////////////////////////////////////////////////"
        )

    if cls_upper == "TOP SECRET//SCI":
        banner_line = "TOP SECRET // SCI"
        dist_line = "Distribution: Authorized TS/SCI-cleared personnel only"
        ctrl_line = "Controlled by: Department of Defense"
        return (
            f"////////////////////////////////////////////////////////////////////\n"
            f"{banner_line}\n"
            f"{dist_line}\n"
            f"{ctrl_line}\n"
            f"////////////////////////////////////////////////////////////////////"
        )

    # Unknown -- fall back to CUI
    return get_marking_banner("CUI", category=category)


def get_code_header(
    classification: str = "CUI",
    language: str = "python",
    category: str = "CTI",
) -> str:
    """Return a classification code-file header in the correct comment style.

    Attempts to import ``language_support.get_cui_header`` for CUI headers
    but works standalone for any classification.

    Args:
        classification: Classification level.
        language: Programming language key (python, java, go, etc.).
        category: Sub-category for CUI markings (default CTI).

    Returns:
        Multi-line comment block suitable for the top of a source file.
    """
    cls_upper = classification.upper()

    # Build the raw header lines based on classification
    if cls_upper == "CUI":
        header_lines = [
            f"CUI // SP-{category}",
            "Controlled by: Department of Defense",
            f"CUI Category: {category}",
            "Distribution: D",
            "POC: ICDEV System Administrator",
        ]
        # Try language_support module for CUI (it has registry-aware logic)
        if language.lower() in ("python", "ruby", "java", "go", "rust",
                                "csharp", "c#", "typescript", "javascript"):
            try:
                sys.path.insert(0, str(BASE_DIR / "tools" / "builder"))
                from language_support import get_cui_header as _ls_header
                result = _ls_header(language)
                if result:
                    return result
            except Exception:
                pass  # Fall through to local logic
    elif cls_upper == "SECRET":
        header_lines = [
            "SECRET // NOFORN",
            "Controlled by: Department of Defense",
            "Classification: SECRET",
            "Distribution: Authorized SECRET-cleared personnel only",
            "POC: ICDEV System Administrator",
        ]
    elif cls_upper in ("TOP SECRET", "TOP SECRET//SCI"):
        header_lines = [
            "TOP SECRET // SCI",
            "Controlled by: Department of Defense",
            "Classification: TOP SECRET // SCI",
            "Distribution: Authorized TS/SCI-cleared personnel only",
            "POC: ICDEV System Administrator",
        ]
    elif cls_upper == "PUBLIC":
        header_lines = [
            "PUBLIC RELEASE",
            "No distribution restrictions",
        ]
    else:
        # Default to CUI
        return get_code_header("CUI", language, category)

    # Determine comment style from local mapping
    lang_lower = language.lower()
    style = _COMMENT_STYLES.get(lang_lower, "hash")

    result_lines: List[str] = []

    if style == "hash":
        for line in header_lines:
            result_lines.append(f"# {line}")
    elif style == "c-style":
        for line in header_lines:
            result_lines.append(f"// {line}")
    elif style == "xml-style":
        result_lines.append("<!--")
        for line in header_lines:
            result_lines.append(f"  {line}")
        result_lines.append("-->")
    else:
        for line in header_lines:
            result_lines.append(f"# {line}")

    return "\n".join(result_lines) + "\n"


def get_document_banner(classification: str = "CUI") -> Dict[str, str]:
    """Return document header and footer banners for markdown/text files.

    Args:
        classification: Classification level.

    Returns:
        Dict with ``header`` and ``footer`` string values.
    """
    cls_upper = classification.upper()

    if cls_upper == "CUI":
        config = load_markings_config()
        header = config.get("document_header", "").strip()
        footer = config.get("document_footer", "").strip()
        if header and footer:
            return {"header": header, "footer": footer}
        # Fallback
        return {
            "header": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "footer": (
                "////////////////////////////////////////////////////////////////////\n"
                "CUI // SP-CTI | Department of Defense\n"
                "////////////////////////////////////////////////////////////////////"
            ),
        }

    if cls_upper == "SECRET":
        return {
            "header": (
                "////////////////////////////////////////////////////////////////////\n"
                "SECRET // NOFORN\n"
                "Distribution: Authorized SECRET-cleared personnel only\n"
                "Controlled by: Department of Defense\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "footer": (
                "////////////////////////////////////////////////////////////////////\n"
                "SECRET // NOFORN | Department of Defense\n"
                "////////////////////////////////////////////////////////////////////"
            ),
        }

    if cls_upper in ("TOP SECRET", "TOP SECRET//SCI"):
        return {
            "header": (
                "////////////////////////////////////////////////////////////////////\n"
                "TOP SECRET // SCI\n"
                "Distribution: Authorized TS/SCI-cleared personnel only\n"
                "Controlled by: Department of Defense\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "footer": (
                "////////////////////////////////////////////////////////////////////\n"
                "TOP SECRET // SCI | Department of Defense\n"
                "////////////////////////////////////////////////////////////////////"
            ),
        }

    if cls_upper == "PUBLIC":
        return {
            "header": (
                "////////////////////////////////////////////////////////////////////\n"
                "PUBLIC RELEASE -- No restrictions on distribution\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "footer": "",
        }

    # Default to CUI
    return get_document_banner("CUI")


def get_portion_marking(classification: str = "CUI") -> str:
    """Return an inline portion-marking string.

    Args:
        classification: Classification level.

    Returns:
        Short inline marker such as ``(CUI)``, ``(S)``, ``(TS)``, or
        ``(TS//SCI)``.
    """
    return _PORTION_MARKS.get(classification.upper(), "(CUI)")


def get_required_baseline(il_level: str) -> Dict:
    """Return the compliance baseline requirements for an impact level.

    Args:
        il_level: One of IL2, IL4, IL5, IL6.

    Returns:
        Dict with ``fedramp_baseline``, ``cmmc_level``,
        ``nist_800_171_required``, and ``required_controls_overlay``.

    Raises:
        ValueError: If *il_level* is invalid.
    """
    profile = get_impact_level_profile(il_level)
    return {
        "fedramp_baseline": profile.get("fedramp_baseline", "moderate"),
        "cmmc_level": profile.get("cmmc_level"),
        "nist_800_171_required": profile.get("nist_800_171_required", False),
        "required_controls_overlay": profile.get("required_controls_overlay", []),
    }


def get_encryption_requirements(il_level: str) -> Dict:
    """Return encryption requirements for an impact level.

    Args:
        il_level: One of IL2, IL4, IL5, IL6.

    Returns:
        Dict with ``at_rest``, ``in_transit``, ``key_management``, and
        any additional encryption-related fields.

    Raises:
        ValueError: If *il_level* is invalid.
    """
    profile = get_impact_level_profile(il_level)
    return profile.get("encryption_requirements", {
        "at_rest": "FIPS 140-2 validated modules",
        "in_transit": "TLS 1.2+",
        "key_management": "Customer managed keys",
    })


def get_network_requirements(il_level: str) -> Dict:
    """Return network requirements for an impact level.

    Args:
        il_level: One of IL2, IL4, IL5, IL6.

    Returns:
        Dict with ``dedicated_infrastructure``, ``cross_domain``,
        ``vpn_required``, ``sipr_only``, and other network fields.

    Raises:
        ValueError: If *il_level* is invalid.
    """
    profile = get_impact_level_profile(il_level)
    return profile.get("network_requirements", {
        "dedicated_infrastructure": False,
        "cross_domain": False,
        "vpn_required": False,
        "sipr_only": False,
    })


def get_cloud_environments(il_level: str) -> List[str]:
    """Return valid cloud environments for an impact level.

    Args:
        il_level: One of IL2, IL4, IL5, IL6.

    Returns:
        List of cloud environment identifiers.

    Raises:
        ValueError: If *il_level* is invalid.
    """
    profile = get_impact_level_profile(il_level)
    return profile.get("cloud_environments", [])


def validate_classification(
    project_id: str,
    db_path: Optional[Path] = None,
) -> Dict:
    """Validate that a project's classification matches its impact level.

    Checks the ``projects`` table for ``classification`` and
    ``impact_level`` columns, then verifies consistency.

    Args:
        project_id: The project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with ``valid`` (bool), ``project_id``, ``classification``,
        ``impact_level``, and ``issues`` (list of issue strings).
    """
    conn = _get_connection(db_path)
    issues: List[str] = []

    try:
        project = _get_project(conn, project_id)

        proj_classification = (project.get("classification") or "CUI").upper()
        proj_il = (project.get("impact_level") or "").upper()

        result: Dict[str, Any] = {
            "valid": True,
            "project_id": project_id,
            "classification": proj_classification,
            "impact_level": proj_il,
            "issues": issues,
        }

        # Check classification is valid
        if proj_classification not in VALID_CLASSIFICATIONS:
            issues.append(
                f"Invalid classification '{proj_classification}'. "
                f"Valid: {', '.join(VALID_CLASSIFICATIONS)}"
            )

        # Check impact level is valid (if set)
        if proj_il and proj_il not in VALID_IMPACT_LEVELS:
            issues.append(
                f"Invalid impact level '{proj_il}'. "
                f"Valid: {', '.join(VALID_IMPACT_LEVELS)}"
            )

        # Cross-validate classification vs. impact level
        if proj_il and proj_il in VALID_IMPACT_LEVELS:
            expected_cls = get_classification_for_il(proj_il)
            if proj_classification != expected_cls:
                issues.append(
                    f"Classification mismatch: project is '{proj_classification}' "
                    f"but impact level '{proj_il}' requires '{expected_cls}'."
                )

            # Check marking banner consistency
            profiles = load_impact_level_profiles()
            profile = profiles.get(proj_il, {})
            expected_banner = profile.get("marking_banner")
            if expected_banner and proj_classification == "CUI":
                config = load_markings_config()
                current_banner = config.get("banner_top", "")
                if expected_banner not in current_banner and current_banner not in expected_banner:
                    issues.append(
                        f"Marking banner mismatch: expected '{expected_banner}' "
                        f"for {proj_il}, current config has '{current_banner}'."
                    )

        elif not proj_il:
            issues.append(
                "Impact level not set on project. Recommend setting "
                "impact_level to ensure compliance mapping."
            )

        result["valid"] = len(issues) == 0

        # Log audit event
        _log_audit_event(conn, project_id, "Classification validation", {
            "classification": proj_classification,
            "impact_level": proj_il,
            "valid": result["valid"],
            "issues": issues,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return result

    finally:
        conn.close()


def get_cross_domain_controls(
    source_il: str,
    target_il: str,
) -> Dict:
    """Return additional controls required for cross-domain solutions.

    Looks up the ``cross_domain_requirements`` section of the impact
    level profiles for the source->target pair.

    Args:
        source_il: Source impact level (e.g. IL4).
        target_il: Target impact level (e.g. IL6).

    Returns:
        Dict with ``description``, ``additional_controls``,
        ``solution_type``, ``approval_required``, and ``direction``.

    Raises:
        ValueError: If either IL is invalid or no cross-domain mapping
            exists for the pair.
    """
    for il in (source_il, target_il):
        if il.upper() not in VALID_IMPACT_LEVELS:
            raise ValueError(
                f"Invalid impact level '{il}'. "
                f"Valid: {', '.join(VALID_IMPACT_LEVELS)}"
            )

    profiles = load_impact_level_profiles()
    cross_domain = profiles.get("_cross_domain", {})

    # Determine direction (always low -> high for cross-domain lookup)
    il_order = {"IL2": 0, "IL4": 1, "IL5": 2, "IL6": 3}
    src = source_il.upper()
    tgt = target_il.upper()
    low, high = (src, tgt) if il_order.get(src, 0) <= il_order.get(tgt, 0) else (tgt, src)

    # Lookup key format: "ILx_to_ILy"
    lookup_key = f"{low}_to_{high}"
    mapping = cross_domain.get(lookup_key)

    if mapping:
        result = dict(mapping)
        result["direction"] = f"{src} -> {tgt}"
        return result

    # Also try classification-level keys (e.g. SECRET_to_TS)
    src_cls = get_classification_for_il(src).replace(" ", "_").upper()
    tgt_cls = get_classification_for_il(tgt).replace(" ", "_").upper()
    cls_key = f"{src_cls}_to_{tgt_cls}"
    mapping = cross_domain.get(cls_key)

    if mapping:
        result = dict(mapping)
        result["direction"] = f"{src} ({src_cls}) -> {tgt} ({tgt_cls})"
        return result

    # Same level or adjacent levels with no explicit cross-domain needs
    if src == tgt:
        return {
            "description": "Same impact level -- no cross-domain controls required.",
            "additional_controls": [],
            "solution_type": "N/A",
            "approval_required": "N/A",
            "direction": f"{src} -> {tgt}",
        }

    # No explicit mapping found -- return a conservative response
    return {
        "description": (
            f"Cross-domain transfer between {src} and {tgt}. "
            "No explicit mapping found; treat as requiring AO approval."
        ),
        "additional_controls": ["AC-4", "SC-7(5)"],
        "solution_type": "Consult AO for approved data transfer mechanism",
        "approval_required": "AO approval required",
        "direction": f"{src} -> {tgt}",
    }


def upgrade_markings(
    content: str,
    from_classification: str,
    to_classification: str,
) -> str:
    """Replace classification banners in content for an upgrade.

    Scans for banner patterns from *from_classification* and replaces
    them with *to_classification* banners. Handles both document banners
    and inline portion markings.

    Args:
        content: Document or code content string.
        from_classification: Current classification (e.g. CUI).
        to_classification: Target classification (e.g. SECRET).

    Returns:
        Updated content with new markings.
    """
    from_cls = from_classification.upper()
    to_cls = to_classification.upper()

    if from_cls == to_cls:
        return content

    result = content

    # --- Banner replacements ---
    from_banners = get_document_banner(from_cls)
    to_banners = get_document_banner(to_cls)

    # Replace header banner
    if from_banners.get("header"):
        result = result.replace(from_banners["header"], to_banners.get("header", ""))

    # Replace footer banner
    if from_banners.get("footer"):
        result = result.replace(from_banners["footer"], to_banners.get("footer", ""))

    # --- Inline banner-line replacements ---
    # Order matters: replace longest/most-specific strings first to avoid
    # partial double-replacements.
    # CUI -> SECRET
    if from_cls == "CUI" and to_cls == "SECRET":
        result = result.replace(
            "CONTROLLED UNCLASSIFIED INFORMATION (CUI // SP-CTI)",
            "SECRET // NOFORN",
        )
        result = result.replace(
            "CONTROLLED UNCLASSIFIED INFORMATION",
            "SECRET // NOFORN",
        )
        result = result.replace("CUI // SP-CTI", "SECRET // NOFORN")
        result = result.replace(
            "Distribution D -- Authorized DoD Personnel Only",
            "Authorized SECRET-cleared personnel only",
        )
        result = result.replace("CUI Category: CTI", "Classification: SECRET")

    elif from_cls == "CUI" and to_cls in ("TOP SECRET", "TOP SECRET//SCI"):
        result = result.replace(
            "CONTROLLED UNCLASSIFIED INFORMATION (CUI // SP-CTI)",
            "TOP SECRET // SCI",
        )
        result = result.replace(
            "CONTROLLED UNCLASSIFIED INFORMATION",
            "TOP SECRET // SCI",
        )
        result = result.replace("CUI // SP-CTI", "TOP SECRET // SCI")
        result = result.replace(
            "Distribution D -- Authorized DoD Personnel Only",
            "Authorized TS/SCI-cleared personnel only",
        )
        result = result.replace("CUI Category: CTI", "Classification: TOP SECRET // SCI")

    elif from_cls == "SECRET" and to_cls in ("TOP SECRET", "TOP SECRET//SCI"):
        result = result.replace("SECRET // NOFORN", "TOP SECRET // SCI")
        result = result.replace(
            "Authorized SECRET-cleared personnel only",
            "Authorized TS/SCI-cleared personnel only",
        )
        result = result.replace("Classification: SECRET", "Classification: TOP SECRET // SCI")

    # --- Portion marking replacement ---
    from_portion = get_portion_marking(from_cls)
    to_portion = get_portion_marking(to_cls)
    if from_portion and to_portion:
        result = result.replace(from_portion, to_portion)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point and demonstration of all capabilities."""
    parser = argparse.ArgumentParser(
        description="Classification & Marking Manager for ICDEV"
    )
    parser.add_argument(
        "--impact-level",
        choices=["IL2", "IL4", "IL5", "IL6"],
        help="Show profile for an impact level",
    )
    parser.add_argument(
        "--classification",
        choices=["PUBLIC", "CUI", "SECRET", "TOP SECRET", "TOP SECRET//SCI"],
        help="Show markings for a classification level",
    )
    parser.add_argument(
        "--banner",
        action="store_true",
        help="Show document banner for the specified classification",
    )
    parser.add_argument(
        "--code-header",
        metavar="LANGUAGE",
        help="Show code header for a language (python, java, go, rust, etc.)",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Show required compliance baseline for the impact level",
    )
    parser.add_argument(
        "--encryption",
        action="store_true",
        help="Show encryption requirements for the impact level",
    )
    parser.add_argument(
        "--network",
        action="store_true",
        help="Show network requirements for the impact level",
    )
    parser.add_argument(
        "--cloud",
        action="store_true",
        help="Show valid cloud environments for the impact level",
    )
    parser.add_argument(
        "--cross-domain",
        nargs=2,
        metavar=("SOURCE_IL", "TARGET_IL"),
        help="Show cross-domain controls between two impact levels",
    )
    parser.add_argument(
        "--validate",
        metavar="PROJECT_ID",
        help="Validate project classification consistency",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Database path override",
    )

    args = parser.parse_args()

    # If no arguments given, show a full demo
    if not any([
        args.impact_level, args.classification, args.banner,
        args.code_header, args.baseline, args.encryption, args.network,
        args.cloud, args.cross_domain, args.validate,
    ]):
        _run_demo(args.json)
        return

    classification = args.classification or "CUI"

    try:
        # --- Impact-level profile ---
        if args.impact_level and not any([args.baseline, args.encryption, args.network, args.cloud]):
            profile = get_impact_level_profile(args.impact_level)
            if args.json:
                print(json.dumps({args.impact_level: profile}, indent=2))
            else:
                print(f"Impact Level: {args.impact_level}")
                print(f"  Classification: {profile.get('classification')}")
                print(f"  Description: {profile.get('description', 'N/A')}")
                print(f"  FedRAMP Baseline: {profile.get('fedramp_baseline')}")
                print(f"  CMMC Level: {profile.get('cmmc_level')}")
                print(f"  NIST 800-171 Required: {profile.get('nist_800_171_required')}")
                print(f"  Marking Banner: {profile.get('marking_banner')}")
                envs = profile.get("cloud_environments", [])
                print(f"  Cloud Environments: {', '.join(envs)}")

        # --- Baseline ---
        if args.baseline:
            if not args.impact_level:
                print("Error: --baseline requires --impact-level", file=sys.stderr)
                sys.exit(1)
            baseline = get_required_baseline(args.impact_level)
            if args.json:
                print(json.dumps(baseline, indent=2))
            else:
                print(f"Compliance Baseline for {args.impact_level}:")
                print(f"  FedRAMP Baseline: {baseline['fedramp_baseline']}")
                print(f"  CMMC Level: {baseline['cmmc_level']}")
                print(f"  NIST 800-171 Required: {baseline['nist_800_171_required']}")
                overlay = baseline.get("required_controls_overlay", [])
                if overlay:
                    print(f"  Controls Overlay ({len(overlay)}):")
                    for ctrl in overlay:
                        print(f"    - {ctrl}")

        # --- Encryption ---
        if args.encryption:
            if not args.impact_level:
                print("Error: --encryption requires --impact-level", file=sys.stderr)
                sys.exit(1)
            enc = get_encryption_requirements(args.impact_level)
            if args.json:
                print(json.dumps(enc, indent=2))
            else:
                print(f"Encryption Requirements for {args.impact_level}:")
                for key, value in enc.items():
                    print(f"  {key}: {value}")

        # --- Network ---
        if args.network:
            if not args.impact_level:
                print("Error: --network requires --impact-level", file=sys.stderr)
                sys.exit(1)
            net = get_network_requirements(args.impact_level)
            if args.json:
                print(json.dumps(net, indent=2))
            else:
                print(f"Network Requirements for {args.impact_level}:")
                for key, value in net.items():
                    print(f"  {key}: {value}")

        # --- Cloud ---
        if args.cloud:
            if not args.impact_level:
                print("Error: --cloud requires --impact-level", file=sys.stderr)
                sys.exit(1)
            envs = get_cloud_environments(args.impact_level)
            if args.json:
                print(json.dumps({"cloud_environments": envs}, indent=2))
            else:
                print(f"Cloud Environments for {args.impact_level}:")
                for env in envs:
                    print(f"  - {env}")

        # --- Banner ---
        if args.banner:
            banner = get_marking_banner(classification)
            if args.json:
                doc = get_document_banner(classification)
                print(json.dumps({
                    "classification": classification,
                    "banner": banner,
                    "document_header": doc["header"],
                    "document_footer": doc["footer"],
                    "portion_marking": get_portion_marking(classification),
                }, indent=2))
            else:
                print(f"Banner for {classification}:\n")
                print(banner)
                print(f"\nPortion marking: {get_portion_marking(classification)}")

        # --- Code header ---
        if args.code_header:
            header = get_code_header(classification, args.code_header)
            if args.json:
                print(json.dumps({
                    "classification": classification,
                    "language": args.code_header,
                    "code_header": header,
                }, indent=2))
            else:
                print(f"Code header for {classification} ({args.code_header}):\n")
                print(header)

        # --- Cross-domain ---
        if args.cross_domain:
            src, tgt = args.cross_domain
            controls = get_cross_domain_controls(src, tgt)
            if args.json:
                print(json.dumps(controls, indent=2))
            else:
                print(f"Cross-Domain Controls: {src} -> {tgt}")
                print(f"  Direction: {controls.get('direction')}")
                print(f"  Description: {controls.get('description')}")
                print(f"  Solution Type: {controls.get('solution_type')}")
                print(f"  Approval Required: {controls.get('approval_required')}")
                addl = controls.get("additional_controls", [])
                if addl:
                    print(f"  Additional Controls ({len(addl)}):")
                    for ctrl in addl:
                        print(f"    - {ctrl}")

        # --- Validate ---
        if args.validate:
            result = validate_classification(args.validate, db_path=args.db_path)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                status = "VALID" if result["valid"] else "INVALID"
                print(f"Classification Validation: {status}")
                print(f"  Project: {result['project_id']}")
                print(f"  Classification: {result['classification']}")
                print(f"  Impact Level: {result['impact_level'] or 'not set'}")
                if result["issues"]:
                    print(f"  Issues ({len(result['issues'])}):")
                    for issue in result["issues"]:
                        print(f"    - {issue}")
                else:
                    print("  No issues found.")

    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def _run_demo(as_json: bool = False) -> None:
    """Demonstrate all classification manager capabilities."""
    print("=" * 70)
    print("  ICDEV Classification & Marking Manager -- Demo")
    print("=" * 70)
    print()

    # 1. Impact level profiles
    print("--- Impact Level Profiles ---")
    for il in VALID_IMPACT_LEVELS:
        try:
            cls = get_classification_for_il(il)
            print(f"  {il}: classification={cls}")
        except ValueError:
            print(f"  {il}: (profile not available)")
    print()

    # 2. Marking banners
    print("--- Marking Banners ---")
    for cls in ("CUI", "SECRET", "TOP SECRET"):
        banner = get_marking_banner(cls)
        print(f"\n  [{cls}]")
        for line in banner.split("\n"):
            print(f"    {line}")
    print()

    # 3. Portion markings
    print("--- Portion Markings ---")
    for cls in VALID_CLASSIFICATIONS:
        print(f"  {cls}: {get_portion_marking(cls)}")
    print()

    # 4. Code headers
    print("--- Code Headers ---")
    for lang in ("python", "java", "xml"):
        header = get_code_header("CUI", lang)
        print(f"\n  [{lang} / CUI]")
        for line in header.rstrip("\n").split("\n"):
            print(f"    {line}")
    print()

    # 5. Code header for SECRET
    print("  [python / SECRET]")
    header = get_code_header("SECRET", "python")
    for line in header.rstrip("\n").split("\n"):
        print(f"    {line}")
    print()

    # 6. Compliance baselines
    print("--- Compliance Baselines ---")
    for il in ("IL4", "IL5", "IL6"):
        try:
            baseline = get_required_baseline(il)
            print(f"  {il}: FedRAMP={baseline['fedramp_baseline']}, "
                  f"CMMC={baseline['cmmc_level']}, "
                  f"NIST 171={baseline['nist_800_171_required']}, "
                  f"Overlay controls={len(baseline.get('required_controls_overlay', []))}")
        except ValueError:
            print(f"  {il}: (not available)")
    print()

    # 7. Encryption requirements
    print("--- Encryption Requirements (IL6) ---")
    try:
        enc = get_encryption_requirements("IL6")
        for k, v in enc.items():
            print(f"  {k}: {v}")
    except ValueError:
        print("  (not available)")
    print()

    # 8. Network requirements
    print("--- Network Requirements (IL5) ---")
    try:
        net = get_network_requirements("IL5")
        for k, v in net.items():
            print(f"  {k}: {v}")
    except ValueError:
        print("  (not available)")
    print()

    # 9. Cloud environments
    print("--- Cloud Environments ---")
    for il in ("IL4", "IL5", "IL6"):
        try:
            envs = get_cloud_environments(il)
            print(f"  {il}: {', '.join(envs)}")
        except ValueError:
            print(f"  {il}: (not available)")
    print()

    # 10. Cross-domain controls
    print("--- Cross-Domain Controls ---")
    for src, tgt in [("IL4", "IL5"), ("IL5", "IL6")]:
        try:
            cd = get_cross_domain_controls(src, tgt)
            print(f"  {src} -> {tgt}: {cd.get('solution_type', 'N/A')}")
            addl = cd.get("additional_controls", [])
            if addl:
                print(f"    Additional controls: {', '.join(addl)}")
        except ValueError:
            print(f"  {src} -> {tgt}: (not available)")
    print()

    # 11. Marking upgrade demo
    print("--- Marking Upgrade Demo ---")
    sample = (
        "////////////////////////////////////////////////////////////////////\n"
        "CONTROLLED UNCLASSIFIED INFORMATION (CUI // SP-CTI)\n"
        "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
        "////////////////////////////////////////////////////////////////////\n"
        "\n"
        "This is a (CUI) document with CUI // SP-CTI markings.\n"
    )
    upgraded = upgrade_markings(sample, "CUI", "SECRET")
    print("  Before:")
    for line in sample.rstrip("\n").split("\n"):
        print(f"    {line}")
    print("  After upgrade to SECRET:")
    for line in upgraded.rstrip("\n").split("\n"):
        print(f"    {line}")
    print()

    print("=" * 70)
    print("  Demo complete. Use --help for CLI options.")
    print("=" * 70)


if __name__ == "__main__":
    main()
