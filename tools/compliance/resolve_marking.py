# CUI // SP-CTI
"""Resolve classification marking for a project.

Central resolver that determines the correct classification banner, code header,
and portion marking for a project based on its data categories, classification,
and impact level. All workflow commands call this tool early to replace hardcoded
CUI // SP-CTI with the project's actual marking.

Resolution chain (ADR D132):
  1. data_classifications table -> composite marking via get_project_marking()
  2. Fallback: projects.classification + projects.impact_level -> infer
  3. Public / IL2 -> marking_required: false (no markings needed)
  4. IL4/IL5 with nothing set -> CUI (backward compat per ADR D54)
  5. IL6 / SECRET -> SECRET marking

Usage:
    # Full marking info (JSON)
    python tools/compliance/resolve_marking.py --project-id proj-123 --json

    # Just the banner text
    python tools/compliance/resolve_marking.py --project-id proj-123 --banner-only

    # Just the code header for Python
    python tools/compliance/resolve_marking.py --project-id proj-123 --code-header python

    # Check if marking is required at all
    python tools/compliance/resolve_marking.py --project-id proj-123 --check-required
"""

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.compliance.resolve_marking")

DEFAULT_DB = BASE_DIR / "data" / "icdev.db"


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a read-only connection to the ICDEV database."""
    path = db_path or DEFAULT_DB
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project_metadata(project_id: str, db_path: Optional[Path] = None) -> Optional[dict]:
    """Fetch classification and impact_level from the projects table."""
    try:
        conn = _get_connection(db_path)
        row = conn.execute(
            "SELECT classification, impact_level FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception as exc:
        logger.debug("Could not read project metadata: %s", exc)
    return None


def _get_data_categories(project_id: str, db_path: Optional[Path] = None) -> List[str]:
    """Fetch confirmed data categories from data_classifications table."""
    try:
        conn = _get_connection(db_path)
        rows = conn.execute(
            "SELECT data_category FROM data_classifications WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        conn.close()
        return [r["data_category"] for r in rows]
    except Exception as exc:
        logger.debug("Could not read data_classifications: %s", exc)
    return []


def resolve_project_marking(
    project_id: str,
    db_path: Optional[Path] = None,
) -> Dict:
    """Resolve the classification marking for a project.

    Returns a dict with all information needed by workflow commands:
    - marking_required: whether marking gates apply
    - categories: list of data categories
    - banner: document banner line (e.g., "CUI // SP-CTI")
    - code_header: first-line code comment (e.g., "# CUI // SP-CTI")
    - footer: document footer text
    - grep_pattern: pattern for marking verification (grep -rL)
    - vision_assertion: assertion text for screenshot validation
    """
    # Step 1: Check data_classifications table
    categories = _get_data_categories(project_id, db_path)

    # Step 2: If no categories, infer from project metadata
    if not categories:
        meta = _get_project_metadata(project_id, db_path)
        if meta:
            cls = (meta.get("classification") or "").upper()
            il = (meta.get("impact_level") or "").upper()

            if cls == "PUBLIC" or il == "IL2":
                return _no_marking_result()
            if cls in ("SECRET", "TOP SECRET", "TOP_SECRET") or il == "IL6":
                categories = ["SECRET"]
            elif cls == "FOUO":
                categories = ["CUI"]
            elif il in ("IL4", "IL5") or cls == "CUI":
                categories = ["CUI"]
            else:
                # Unknown — conservative default
                categories = ["CUI"]
        else:
            # Project not found — backward compat default
            categories = ["CUI"]

    # Step 3: Build marking from resolved categories
    if not categories or categories == ["PUBLIC"]:
        return _no_marking_result()

    try:
        from tools.compliance.universal_classification_manager import (
            get_composite_banner,
            get_composite_code_header,
            get_composite_footer,
            get_composite_portion_marking,
            get_highest_sensitivity,
        )

        # Default subcategories
        subcats = {}
        if "CUI" in categories:
            subcats["CUI"] = "CTI"
        if "SECRET" in categories:
            subcats["SECRET"] = "NSI"

        banner_full = get_composite_banner(categories, subcats)
        # Extract just the marking line (second line of the full banner)
        banner_lines = banner_full.strip().splitlines()
        banner_short = banner_lines[1] if len(banner_lines) > 1 else banner_lines[0] if banner_lines else ""

        code_header = get_composite_code_header(categories, "python", subcats)
        # Extract just the first line of the code header (the marking comment)
        code_first_line = code_header.splitlines()[0] if code_header else ""

        footer = get_composite_footer(categories)
        portion = get_composite_portion_marking(categories)
        highest = get_highest_sensitivity(categories)

        # grep_pattern: the marking text to search for in source files
        grep_pattern = code_first_line.lstrip("# ").lstrip("// ").strip()

        # vision_assertion: what to assert in screenshot validation
        if grep_pattern:
            vision_assertion = f"A classification banner containing \"{grep_pattern}\" is visible at the top of the page"
        else:
            vision_assertion = "A classification banner is visible at the top of the page"

        return {
            "marking_required": True,
            "categories": categories,
            "banner": banner_short,
            "banner_full": banner_full,
            "code_header": code_first_line,
            "code_header_full": code_header,
            "footer": footer,
            "portion_marking": portion,
            "highest_sensitivity": highest,
            "grep_pattern": grep_pattern,
            "vision_assertion": vision_assertion,
        }

    except ImportError:
        # universal_classification_manager not available — basic fallback
        if "SECRET" in categories:
            mark = "SECRET // NSI"
        elif "CUI" in categories:
            mark = "CUI // SP-CTI"
        else:
            mark = categories[0]

        return {
            "marking_required": True,
            "categories": categories,
            "banner": mark,
            "banner_full": mark,
            "code_header": f"# {mark}",
            "code_header_full": f"# {mark}",
            "footer": mark,
            "portion_marking": f"({'|'.join(categories)})",
            "highest_sensitivity": categories[0],
            "grep_pattern": mark,
            "vision_assertion": f"A classification banner containing \"{mark}\" is visible at the top of the page",
        }


def _no_marking_result() -> Dict:
    """Return a result dict for projects that require no marking."""
    return {
        "marking_required": False,
        "categories": [],
        "banner": "",
        "banner_full": "",
        "code_header": "",
        "code_header_full": "",
        "footer": "",
        "portion_marking": "",
        "highest_sensitivity": "PUBLIC",
        "grep_pattern": "",
        "vision_assertion": "",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Classification Marking Resolver"
    )
    parser.add_argument("--project-id", required=True, help="ICDEV project ID")
    parser.add_argument("--json", action="store_true", help="Full JSON output")
    parser.add_argument("--banner-only", action="store_true", help="Print just the banner line")
    parser.add_argument("--code-header", metavar="LANG", help="Print code header for LANG (python, java, go, etc.)")
    parser.add_argument("--check-required", action="store_true", help="Exit 0 if marking required, 1 if not")

    args = parser.parse_args()

    result = resolve_project_marking(args.project_id)

    if args.check_required:
        sys.exit(0 if result["marking_required"] else 1)

    if args.banner_only:
        print(result["banner"])
        return

    if args.code_header:
        # Re-generate for the specified language
        if result["marking_required"] and result["categories"]:
            try:
                from tools.compliance.universal_classification_manager import (
                    get_composite_code_header,
                )
                subcats = {}
                if "CUI" in result["categories"]:
                    subcats["CUI"] = "CTI"
                if "SECRET" in result["categories"]:
                    subcats["SECRET"] = "NSI"
                header = get_composite_code_header(result["categories"], args.code_header, subcats)
                print(header)
            except ImportError:
                print(result["code_header"])
        else:
            print("")
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["marking_required"]:
            print(f"Classification: {', '.join(result['categories'])}")
            print(f"Banner: {result['banner']}")
            print(f"Code Header: {result['code_header']}")
            print(f"Grep Pattern: {result['grep_pattern']}")
        else:
            print("No classification marking required for this project.")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
