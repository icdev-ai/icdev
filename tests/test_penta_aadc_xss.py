# CUI // SP-CTI
"""Stored/DOM XSS escaping tests for the Agentic AI Canvas (penta-aadc-04).

The canvas renderer and its side panels build innerHTML strings from
user/server-controlled values (node labels/icons/colors persist in
graph_json; version/snippet/checkpoint metadata; regulatory/threat findings;
panel error echoes). Before the fix these were interpolated unescaped, so a
node label of ``<img src=x onerror=alert(1)>`` was a stored-XSS vector for
every subsequent viewer.

The fix adds a single ``esc()`` helper (entity-escapes & < > " ') and applies
it at every sink. These tests:
  1. Assert the ``esc()`` helper exists with all five entity replacements.
  2. Assert ``esc(`` is applied at each named sink (string-assertion on the
     rendered/static JS, as permitted by the task acceptance).
  3. Assert no known bare (unescaped) sink expression remains (regression).
  4. Unit-test the escape semantics via a faithful Python port — the XSS
     payload must render inert.
  5. Confirm the template also renders through Jinja and that the icdev/
     mirror is byte-identical.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TPL = ROOT / "tools" / "dashboard" / "templates" / "agentic_ai_canvas" / "canvas.html"
ICDEV_TPL = (
    ROOT / "icdev" / "tools" / "dashboard" / "templates"
    / "agentic_ai_canvas" / "canvas.html"
)

XSS_LABEL = "<img src=x onerror=alert(1)>"


@pytest.fixture(scope="module")
def src() -> str:
    return TPL.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Helper existence + all five entity replacements
# ---------------------------------------------------------------------------

def test_esc_helper_defined_with_all_five_entities(src):
    assert "function esc(v)" in src
    patterns = [
        ".replace(/&/g, '&amp;')",
        ".replace(/</g, '&lt;')",
        ".replace(/>/g, '&gt;')",
        '.replace(/"/g, ' + "'&quot;')",
        ".replace(/'/g, '&#39;')",
    ]
    for pat in patterns:
        assert pat in src, f"esc() missing replacement: {pat}"


def test_legacy_esc_delegates_to_esc(src):
    # The pre-existing _esc() is unified to delegate to esc() (single source).
    assert "function _esc(s)" in src
    idx = src.index("function _esc(s)")
    body = src[idx: idx + 120]
    assert "return esc(s)" in body


# ---------------------------------------------------------------------------
# 2. esc() applied at every named sink
# ---------------------------------------------------------------------------

# (label, substring that MUST be present) — one per sink from the task.
SINKS = [
    ("canvas node label",      "esc(n.label)"),
    ("canvas node icon",       "esc(n.icon||'⬡')"),
    ("canvas node color",      "esc(n.color||'#6366f1')"),
    ("versions label",         "esc(v.label)"),
    ("versions created_at",    "esc(v.created_at || '')"),
    ("snippet name",           "esc(s.name)"),
    ("checkpoint label",       "esc(c.label || 'Checkpoint')"),
    ("checkpoint id",          "esc(c.id)"),
    ("diff added/removed/changed node", "esc(n.label || n.id)"),
    ("provenance entry label", "esc(entry.label)"),
    ("provenance flag message", "esc(f.message)"),
    ("coord agent label",      "esc(a.label)"),
    ("coord topology",         "esc(data.topology)"),
    ("sim halted label",       "esc(data.halted_by_label)"),
    ("sim step node_label",    "esc(step.node_label)"),
    ("sim decision reason",    "esc(d.reason)"),
    ("lint recommendation msg", "esc(r.message)"),
    ("regulatory gap title",   "esc(g.title)"),
    ("regulatory requirement", "esc(g.requirement)"),
    ("threat STRIDE title",    "esc(t.title)"),
    ("threat STRIDE description", "esc(t.description)"),
    ("threat ATLAS title",     "esc(a.title)"),
    ("regulatory panel error", "'<div style=\"color:#f87171;padding:16px;\">' + esc(d.error) +"),
    ("svg export node label",  "font-family=\"sans-serif\">${esc(n.label)}</text>"),
]


@pytest.mark.parametrize("label,needle", SINKS, ids=[s[0] for s in SINKS])
def test_sink_is_escaped(src, label, needle):
    assert needle in src, f"sink not escaped: {label} (expected: {needle})"


# ---------------------------------------------------------------------------
# 3. Regression: no known bare (unescaped) sink expression remains
# ---------------------------------------------------------------------------

BARE_FORBIDDEN = [
    "${n.label}",              # canvas node + svg export
    "${entry.label}",          # provenance
    "${s.name}",               # snippet
    "${c.label || 'Checkpoint'}",  # checkpoint
    "${step.node_label}",      # sim trace
    "${g.title}",              # regulatory gap
    "${t.title}",              # threat STRIDE
    "' + d.error + '",         # panel error echo (regulatory + threat)
    "${n.label || n.id}",      # diff modal
]


@pytest.mark.parametrize("bare", BARE_FORBIDDEN)
def test_no_bare_unescaped_sink(src, bare):
    assert bare not in src, f"unescaped sink still present: {bare}"


# ---------------------------------------------------------------------------
# 4. Escape semantics — faithful Python port of esc()
# ---------------------------------------------------------------------------

def _js_esc(v) -> str:
    """Python mirror of the template's esc() — same five replacements, order."""
    s = "" if v is None else str(v)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def test_payload_renders_inert():
    out = _js_esc(XSS_LABEL)
    # No raw angle brackets survive → the <img> tag cannot form in the DOM.
    assert "<" not in out
    assert ">" not in out
    assert out == "&lt;img src=x onerror=alert(1)&gt;"


def test_esc_handles_quotes_and_ampersand():
    assert _js_esc('a & b') == "a &amp; b"
    assert _js_esc('"quoted"') == "&quot;quoted&quot;"
    assert _js_esc("it's") == "it&#39;s"
    # ampersand escaped first so entities are not double-encoded wrongly
    assert _js_esc("<a>&") == "&lt;a&gt;&amp;"


def test_esc_null_safe():
    assert _js_esc(None) == ""
    assert _js_esc(0) == "0"


# ---------------------------------------------------------------------------
# 5. Template still renders through Jinja; icdev mirror byte-identical
# ---------------------------------------------------------------------------

def test_main_script_block_renders_through_jinja(src):
    """Render the main <script> block via Jinja so its `{{ design.* }}`
    interpolations are exercised and the esc helper + a sink survive.

    There are now TWO layers, and this test asserts both:

    1. ``| json_script_safe`` (nav-sec-08) escapes ``<``/``>``/``&`` to ``\\uXXXX``
       as the JSON is embedded, so a payload containing ``</script>`` cannot break
       out of the script element in the first place.
    2. the DOM ``esc()`` call (asserted above) neutralizes the value at render time.

    The environment registers the REAL filter rather than a stub. A bare
    ``Environment`` has no application filters, so when json_script_safe was added
    to the template this test began failing with "No filter named
    'json_script_safe'" — a false alarm about an XSS defence, which is the worst
    kind to have sitting red.

    We render just the script block (not the whole page) to avoid the unrelated
    page-context vars the full template consumes.
    """
    from tools.dashboard.ux_helpers import json_script_safe

    start = src.index("<script>\n// ── HTML escaping")
    end = src.index("</script>", start)
    block = src[start:end]
    assert "function esc(v)" in block  # sanity: we sliced the right block

    env = Environment(autoescape=False)  # noqa: S701 - trusted first-party JS block
    env.filters["json_script_safe"] = json_script_safe
    tpl = env.from_string(block)
    design = {
        "id": "d1",
        "name": "Test Design",
        "classification": "CUI",
        "graph_json": '{"nodes":[{"id":"n1","label":"%s","type":"llm"}],"edges":[]}'
        % XSS_LABEL,
    }
    rendered = tpl.render(design=design, node_descs={})
    assert "function esc(v)" in rendered
    assert "esc(n.label)" in rendered

    # The payload no longer rides into the `graph` JS var verbatim: json_script_safe
    # escapes its angle brackets on the way in, so it cannot terminate the <script>
    # element. This assertion used to be `XSS_LABEL in rendered`, which was true when
    # the value passed through `| safe` untouched and is the WEAKER property.
    assert XSS_LABEL not in rendered, (
        "the raw payload reached the script block; json_script_safe did not run"
    )
    assert "\\u003cimg src=x onerror=alert(1)\\u003e" in rendered
    assert "</script>" not in rendered.split("function esc(v)")[-1], (
        "nothing in the embedded JSON may close the script element"
    )


def test_icdev_mirror_byte_identical(src):
    assert ICDEV_TPL.exists(), "icdev mirror missing"
    assert ICDEV_TPL.read_text(encoding="utf-8") == src
