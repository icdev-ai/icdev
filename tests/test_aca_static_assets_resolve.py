# CUI // SP-CTI
"""Every static asset an academy template references must exist on disk.

mission.html has loaded four CodeMirror files since it shipped, and
static/vendor/codemirror/ did not exist. All four 404'd; because Flask serves an
HTML 404 page the browser reported MIME-type refusals instead of missing files, so
the console read as four cryptic errors rather than "you forgot to vendor this".
The DOMContentLoaded handler never fired its codemirror-ready path, window.editors
stayed empty, and runCode() silently fell back to the raw textarea — every coding
step was a bare box with no line numbers and nothing telling the learner why.

That mattered much more once aca-hon-05 gave Tier 1 real code to edit.

The general test walks url_for('static', filename=...) references in the academy
templates and asserts each resolves, so the next template to reference a
non-existent asset fails here instead of in a browser console.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "tools" / "dashboard" / "static"
TEMPLATES = REPO_ROOT / "tools" / "dashboard" / "templates" / "forge_academy"
CM = STATIC / "vendor" / "codemirror"

# url_for('static', filename='...') with either quote style.
_STATIC_REF = re.compile(
    r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]"
)


def _refs() -> list[tuple[Path, str]]:
    out = []
    for tpl in sorted(TEMPLATES.rglob("*.html")):
        text = tpl.read_text(encoding="utf-8", errors="replace")
        for m in _STATIC_REF.finditer(text):
            out.append((tpl, m.group(1)))
    return out


def test_academy_templates_reference_at_least_one_static_asset():
    assert _refs(), "fixture guard: expected static references to check"


def test_every_referenced_static_asset_exists():
    missing = []
    for tpl, ref in _refs():
        # Directory-style refs (…/vendor/codemirror/) are concatenated with a
        # filename in JS; require the directory to exist.
        target = STATIC / ref
        ok = target.exists() or (ref.endswith("/") and target.is_dir())
        if not ok:
            missing.append(f"{tpl.name} -> static/{ref}")
    assert not missing, "template references a static asset that does not exist:\n  " + \
        "\n  ".join(missing)


# ---------------------------------------------------------------------------
# The four files mission.html loads, specifically
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["codemirror.js", "codemirror.css", "python.js", "dracula.css"])
def test_codemirror_asset_is_present_and_substantial(name):
    p = CM / name
    assert p.is_file(), f"{name} missing — the editor cannot initialise"
    assert p.stat().st_size > 1000, f"{name} is suspiciously small ({p.stat().st_size} B)"


def test_codemirror_js_exposes_the_api_the_template_calls():
    """mission.html calls CodeMirror.fromTextArea — a CodeMirror 5 global."""
    src = (CM / "codemirror.js").read_text(encoding="utf-8", errors="replace")
    assert "fromTextArea" in src, "not the CodeMirror 5 API the runner uses"


def test_python_mode_registers_python():
    src = (CM / "python.js").read_text(encoding="utf-8", errors="replace")
    assert "defineMode" in src and "python" in src.lower()


def test_dracula_theme_matches_the_requested_theme_name():
    """mission.html passes theme: 'dracula'; the CSS class must match."""
    src = (CM / "dracula.css").read_text(encoding="utf-8", errors="replace")
    assert "cm-s-dracula" in src


def test_mit_licence_travels_with_the_vendored_code():
    lic = (CM / "LICENSE").read_text(encoding="utf-8", errors="replace")
    assert "MIT" in lic


def test_provenance_records_verifiable_hashes():
    """A vendored dependency in this repo has to be auditable after the fact."""
    man = json.loads((CM / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert man["library"] == "CodeMirror"
    assert man["version"]
    assert man["license"] == "MIT"
    import hashlib

    for name, meta in man["assets"].items():
        p = CM / name
        assert p.is_file(), f"PROVENANCE lists {name} but it is absent"
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        assert actual == meta["sha256"], (
            f"{name} on disk does not match its recorded sha256 — the vendored copy "
            "was modified after it was fetched"
        )
        if "crosscheck_identical" in meta:
            assert meta["crosscheck_identical"] is True, (
                f"{name} was not confirmed identical across two registries"
            )


def test_vendored_copy_is_mirrored_to_the_icdev_package():
    """Mirror parity: the packaged tree serves these too."""
    mirror = REPO_ROOT / "icdev" / "tools" / "dashboard" / "static" / "vendor" / "codemirror"
    for name in ("codemirror.js", "codemirror.css", "python.js", "dracula.css"):
        assert (mirror / name).is_file(), f"{name} not mirrored to icdev/"
        assert (mirror / name).read_bytes() == (CM / name).read_bytes(), \
            f"{name} differs between tools/ and icdev/"
