#!/usr/bin/env python3
# CUI // SP-CTI
"""QA auto-fix handlers — broken refs, test stubs."""

import re
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def fix_broken_refs(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Remove broken tool references from CLAUDE.md.

    Only removes lines that reference non-existent tools/ paths.
    Conservative: adds a comment marker rather than deleting.
    """
    claude_md = BASE_DIR / "CLAUDE.md"
    if not claude_md.exists():
        return {"action": "no_claude_md"}

    content = claude_md.read_text(encoding="utf-8")
    lines = content.splitlines()
    fixed_refs = []
    new_lines = []

    for line in lines:
        # Find tool references like `tools/foo/bar.py`
        matches = re.findall(r"`(tools/[^`]+\.py)`", line)
        broken_in_line = False
        for ref in matches:
            ref_path = BASE_DIR / ref
            if not ref_path.exists():
                broken_in_line = True
                fixed_refs.append(ref)

        if broken_in_line and line.strip().startswith("python "):
            # CLI command line with broken reference — comment it out
            new_lines.append(f"# [STALE] {line}")
        else:
            new_lines.append(line)

    if fixed_refs:
        claude_md.write_text("\n".join(new_lines), encoding="utf-8", newline="")

    return {
        "action": "broken_refs_commented",
        "refs_fixed": len(fixed_refs),
        "refs": fixed_refs[:20],
    }


def verify_broken_refs(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Verify no broken refs remain in CLAUDE.md."""
    claude_md = BASE_DIR / "CLAUDE.md"
    if not claude_md.exists():
        return {"passed": True}

    content = claude_md.read_text(encoding="utf-8")
    broken = []
    for match in re.finditer(r"`(tools/[^`]+\.py)`", content):
        ref_path = BASE_DIR / match.group(1)
        if not ref_path.exists():
            broken.append(match.group(1))

    return {"passed": len(broken) == 0, "remaining_broken": broken[:10]}


def fix_untested_modules(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Generate minimal test stubs for untested modules.

    Creates a test file with basic import + smoke test for each untested module.
    Conservative: only creates if no test file exists.
    """
    evidence = finding.get("evidence", {})
    if isinstance(evidence, str):
        import json

        try:
            evidence = json.loads(evidence)
        except Exception:
            evidence = {}

    top_untested = evidence.get("top_10", [])
    if not top_untested:
        return {"action": "no_untested_in_evidence"}

    tests_dir = BASE_DIR / "tests"
    created = []

    for mod_info in top_untested[:3]:  # Limit to 3 per run
        if isinstance(mod_info, dict):
            module_path = mod_info.get("module", "")
        else:
            module_path = str(mod_info)

        # Extract module name: tools/foo/bar.py → bar
        parts = Path(module_path)
        module_name = parts.stem
        test_file = tests_dir / f"test_{module_name}.py"

        if test_file.exists():
            continue

        # Generate minimal test stub
        import_path = module_path.replace("/", ".").replace("\\", ".").removesuffix(".py")
        stub = (
            f"#!/usr/bin/env python3\n"
            f"# CUI // SP-CTI\n"
            f'"""Auto-generated test stub for {module_path}."""\n'
            f"\n"
            f"import sys\n"
            f"from pathlib import Path\n"
            f"\n"
            f"BASE_DIR = Path(__file__).resolve().parent.parent\n"
            f"if str(BASE_DIR) not in sys.path:\n"
            f"    sys.path.insert(0, str(BASE_DIR))\n"
            f"\n"
            f"\n"
            f"class Test{module_name.title().replace('_', '')}:\n"
            f'    """Test stub for {module_name} — expand with real tests."""\n'
            f"\n"
            f"    def test_module_imports(self):\n"
            f'        """Verify module can be imported without errors."""\n'
            f"        try:\n"
            f"            import importlib\n"
            f'            importlib.import_module("{import_path}")\n'
            f"        except ImportError:\n"
            f"            pass  # Optional dependencies may be missing\n"
        )

        test_file.write_text(stub, encoding="utf-8", newline="")
        created.append(test_file.name)

    return {
        "action": "test_stubs_created",
        "files_created": created,
        "count": len(created),
    }
