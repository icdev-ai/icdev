# CUI // SP-CTI
"""penta-aca-03 — FORGE Academy Tier-3 canvas-taxonomy content regression tests.

The m-t3-05-canvas-selection lesson previously taught an obsolete "7 Data Canvas"
model (Network Data Canvas, Signal Data Canvas, Pattern Data Canvas, ...) that does
not match the live component registry. This test pins the lesson to the CURRENT,
registry-accurate Design Canvas taxonomy and guards against the obsolete names
creeping back in.

Ground truth: args/component_registry.yaml
  ndc -> Network Design Canvas
  sdc -> Security Design Canvas
  pdc -> Pipeline Design Canvas
  bdc -> Boundary & Supply Chain Canvas
  ddc -> Data Design Canvas
  odc -> Observability Design Canvas
  idc -> Infrastructure Design Canvas
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "apps" / "forge_academy" / "content" / "tier3" / "m-t3-05-canvas-selection" / "steps"
_LESSON = _MODULE / "step1_canvas_selection.md"
_STARTER = _MODULE / "step1_starter.py"
_TEST = _MODULE / "step1_test.py"

# Correct display names — must match args/component_registry.yaml.
CURRENT_NAMES = [
    "Network Design Canvas",
    "Security Design Canvas",
    "Pipeline Design Canvas",
    "Boundary & Supply Chain Canvas",
    "Data Design Canvas",
    "Observability Design Canvas",
    "Infrastructure Design Canvas",
]

# Obsolete "Data Canvas" expansions that must never reappear.
OBSOLETE_NAMES = [
    "Network Data Canvas",
    "Signal Data Canvas",
    "Pattern Data Canvas",
    "Business Data Canvas",
    "Domain Data Canvas",
    "Operations Data Canvas",
    "Intelligence Data Canvas",
]


def _read(p: Path) -> str:
    assert p.exists(), f"expected content file missing: {p}"
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Lesson markdown
# ---------------------------------------------------------------------------

def test_lesson_uses_current_display_names():
    text = _read(_LESSON)
    for name in CURRENT_NAMES:
        assert name in text, f"lesson must teach current canvas name '{name}'"


def test_lesson_has_no_obsolete_names():
    text = _read(_LESSON)
    for name in OBSOLETE_NAMES:
        assert name not in text, f"lesson still contains obsolete canvas name '{name}'"


def test_lesson_is_registry_driven():
    text = _read(_LESSON).lower()
    assert "component_registry.yaml" in text, "lesson must point at the registry as source of truth"
    assert "profile" in text, "lesson must mention core profiles"


# ---------------------------------------------------------------------------
# Starter code
# ---------------------------------------------------------------------------

def test_starter_uses_current_names_and_drops_obsolete():
    text = _read(_STARTER)
    for name in CURRENT_NAMES:
        assert name in text, f"starter CANVASES must use current name '{name}'"
    for name in OBSOLETE_NAMES:
        assert name not in text, f"starter still contains obsolete canvas name '{name}'"


# ---------------------------------------------------------------------------
# Auto-grader consistency: the starter + a reference solution must pass the test
# ---------------------------------------------------------------------------

_REFERENCE_SOLUTION = """
def score_canvas(canvas_code, description):
    c = CANVASES.get(canvas_code)
    if not c:
        return 0.0
    kws = c["keywords"]
    if not kws:
        return 0.0
    d = description.lower()
    return float(sum(1 for k in kws if k in d)) / len(kws)


def explain_canvas(canvas_code):
    c = CANVASES.get(canvas_code)
    return c["description"] if c else "Unknown canvas"


class CanvasSelector:
    def select(self, description):
        scores = {code: score_canvas(code, description) for code in CANVASES}
        best, best_s = None, -1.0
        for code in CANVASES:  # registry order tie-break
            if scores[code] > best_s:
                best_s, best = scores[code], code
        if best_s <= 0.0:
            r = dict(NO_MATCH)
            r["all_scores"] = scores
            return r
        return {
            "canvas": best,
            "name": CANVASES[best]["name"],
            "confidence": float(best_s),
            "reasoning": f"Matched keyword(s) from {best} canvas",
            "all_scores": scores,
        }

    def rank_canvases(self, description):
        items = [
            {"canvas": code, "name": CANVASES[code]["name"], "score": score_canvas(code, description)}
            for code in CANVASES
        ]
        items.sort(key=lambda x: -x["score"])
        return items
"""


def test_reference_solution_passes_grader():
    """A correct solution against the current starter must satisfy step1_test.py.

    This guarantees the rewritten lesson/starter/test stay internally consistent
    (descriptions map unambiguously to the intended Design Canvas).
    """
    starter = _read(_STARTER).split("# Test", 1)[0]
    grader = _read(_TEST)
    ns: dict = {}
    exec(starter + "\n" + _REFERENCE_SOLUTION + "\n" + grader, ns)  # noqa: S102 - trusted first-party content
