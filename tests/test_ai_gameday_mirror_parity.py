# CUI // SP-CTI
"""Content-level mirror-parity guard for the ai_gameday templates (gdx-vv-01).

ROOT-CAUSE GUARD for gdx-mir-01. The repo-wide mirror-parity gate compares file
LISTS, so ``hub.html`` and ``player.html`` diverged in CONTENT between the shim
tree (``tools/dashboard/templates/ai_gameday/``) and the packaged canonical tree
(``icdev/tools/dashboard/templates/ai_gameday/``) while the gate stayed green.
Two verified-shipped fixes therefore never reached the packaged product:

* **fga-gd-01** — the Scenario Manager / Scenario Builder links on ``hub.html``
  (``/gameday/scenarios`` and ``/gameday/scenarios/builder``).
* **fga-gd-02** — the ``{% if tool.navigable %}`` guard on ``player.html`` that
  stops the 12 POST-only ``AI_TOOLS_CATALOG`` entries rendering as anchors that
  open a tab and 405.

Comparison is on a NORMALISED hash — line endings normalised and trailing
whitespace stripped per line — because the two trees genuinely differ in CRLF
vs LF and a byte compare would be flaky on Windows. Real content drift (a
missing link, a missing Jinja guard) survives normalisation; a checkout's line
endings do not.

SCOPE: ai_gameday only, by design. Generalising content parity to every canvas
is a separate card.

CI WIRING — DONE (gdx-mir-01). This file is now in the pytest allowlist of the
``Test`` job in ``.github/workflows/icdev-ci.yml``, alongside the sibling guards
``test_data_canvas_mirror_parity.py`` and ``test_quality_monitor_parity.py``. It
was held out of that list while it was RED (gdx-vv-01's acceptance criterion),
because adding it then would have blocked every unrelated PR.

The content drift itself was closed by ``9cab8c3ca`` (``fix(tsr-core-01-d5):
resync the icdev/ mirror so the wheel ships current data``), a run of the mirror
SYNC tooling rather than a hand-copy — the two templates were restored as part
of a whole-tree resync, which is why no gdx-mir-01 commit touches them. Wiring
this guard into CI is what stops the next divergence shipping silently: without
it, parity holds only until the next unmirrored edit, exactly as before.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SHIM = _REPO_ROOT / "tools" / "dashboard" / "templates" / "ai_gameday"
_CANONICAL = _REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates" / "ai_gameday"

# Templates that legitimately exist in only one tree. EMPTY, and it should stay
# that way: gdx-mir-02 closed the only entries this ever held.
#
# register.html, registrations.html and simulator.html were authored straight
# into the icdev/ mirror and never existed in tools/ on any branch — the
# mirror-only authoring anti-pattern, whose files the next --clean sync deletes
# without trace. gdx-mir-02 dispositioned them as PROMOTE: they are now in both
# trees, so the file-set check below covers them like every other template and
# the content check pins them byte-for-byte.
#
# The pre-session registration / snake-draft feature they belong to is still
# unwired (no route renders them, and apps/ai_gameday/blueprint.py has never
# contained a register or form-teams route in any commit). That is tracked as a
# separate feature card — it is deliberately NOT a reason to re-exempt these
# files. An unrendered template is inert; an un-mirrored one silently vanishes.
_KNOWN_ONE_SIDED: frozenset = frozenset()

# Regression markers: substrings that must survive in BOTH copies. These are the
# exact fixes that the list-only gate let slip.
_REGRESSION_MARKERS = [
    ("hub.html", "gameday/scenarios"),   # fga-gd-01 — Scenario Manager / Builder links
    ("player.html", "tool.navigable"),   # fga-gd-02 — POST-only tool guard
]


def _normalised_hash(path: Path) -> str:
    """SHA-256 over the template with line endings and trailing space normalised.

    Reads bytes and decodes explicitly so the result never depends on the
    platform's default encoding.
    """
    text = path.read_bytes().decode("utf-8")
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return hashlib.sha256("\n".join(lines).rstrip("\n").encode("utf-8")).hexdigest()


def _template_names(root: Path) -> set:
    return {p.name for p in root.glob("*.html")}


def _shared_template_names() -> list:
    """Templates present in both trees — the set the content check applies to."""
    return sorted(_template_names(_SHIM) & _template_names(_CANONICAL))


def test_ai_gameday_template_dirs_exist():
    assert _SHIM.is_dir(), f"missing shim template dir: {_SHIM}"
    assert _CANONICAL.is_dir(), f"missing canonical template dir: {_CANONICAL}"


def test_no_unexpected_one_sided_ai_gameday_template():
    shim = _template_names(_SHIM)
    canonical = _template_names(_CANONICAL)
    missing_from_canonical = sorted((shim - canonical) - _KNOWN_ONE_SIDED)
    missing_from_shim = sorted((canonical - shim) - _KNOWN_ONE_SIDED)
    assert not missing_from_canonical, (
        "present in tools/ but missing from the packaged icdev/ mirror: "
        f"{missing_from_canonical}"
    )
    assert not missing_from_shim, (
        f"present in icdev/ but missing from tools/: {missing_from_shim}"
    )


@pytest.mark.parametrize("name", _shared_template_names())
def test_ai_gameday_template_content_matches_mirror(name: str):
    shim = _SHIM / name
    canonical = _CANONICAL / name
    shim_hash = _normalised_hash(shim)
    canonical_hash = _normalised_hash(canonical)
    assert shim_hash == canonical_hash, (
        f"ai_gameday mirror CONTENT drift in {name}: "
        f"tools/ ({shim.stat().st_size}B, sha {shim_hash[:12]}) differs from "
        f"icdev/tools/ ({canonical.stat().st_size}B, sha {canonical_hash[:12]}) "
        "after line-ending/trailing-whitespace normalisation. Re-sync the mirror "
        "with the companion/mirror sync tooling — do not hand-copy."
    )


@pytest.mark.parametrize("name,marker", _REGRESSION_MARKERS)
def test_ai_gameday_regression_marker_present_in_both_trees(name: str, marker: str):
    for label, root in (("tools", _SHIM), ("icdev/tools", _CANONICAL)):
        path = root / name
        assert path.exists(), f"{label}/dashboard/templates/ai_gameday/{name} is missing"
        # Compare on a bool, not `marker in <text>` — pytest's assertion
        # introspection would otherwise dump the whole template into the report.
        present = marker in path.read_bytes().decode("utf-8")
        assert present, (
            f"regression marker {marker!r} missing from "
            f"{label}/dashboard/templates/ai_gameday/{name} — a shipped GameDay fix "
            "has been lost from this tree"
        )
