# CUI // SP-CTI
"""Security tests for nav-sec-08 — systematic XSS sweep beyond the known sites.

Builds on nav-sec-07. This sweep triaged all remaining ``|safe`` template usages
and the high-risk ``innerHTML`` sinks, then:

  * Added ONE shared, fail-closed escaping/markdown helper
    (``tools/dashboard/static/js/esc.js`` → ``window.escHtml`` / ``escAttr`` /
    ``safeMarkdown``) loaded globally from ``base.html`` alongside a globally
    (no longer strategos-gated) DOMPurify.
  * Routed the LLM/user markdown sinks (chat multi-pane, chat renderers,
    codebase-assistant widget, writeguard preview) through ``safeMarkdown``.
  * Made the Jinja ``markdown`` filter sanitize its rendered output — fixing the
    ``document_intelligence/doc_detail.html`` external-document injection at the
    chokepoint.
  * Sanitized the strategos brief ``content_html`` server-side.
  * Added a ``json_script_safe`` filter and migrated 9 raw
    ``{{ json_string | safe }}`` → inline ``<script>`` sites (script-breakout
    class) to it — provably semantics-preserving (only escapes </script>-class
    characters that only occur inside JSON string values).
  * Escaped the boundary-compliance and network PPS-matrix innerHTML row fields.

Server-side behaviour is proven with payload round-trips; JS-side fixes are
proven with source scans (no live DOM in unit tests).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TEMPLATES = REPO / "tools" / "dashboard" / "templates"
STATIC = REPO / "tools" / "dashboard" / "static"
JS = STATIC / "js"

XSS_TAG = "<script>alert(1)</script>"
XSS_BREAKOUT = "</script><script>alert(1)</script>"


# ── Jinja harness (real templates, stub base, real ux filters) ───────────────

def _stub_app():
    from flask import Flask
    from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

    from tools.dashboard.ux_helpers import register_ux_filters

    app = Flask(__name__)
    stub = "{% block title %}{% endblock %}{% block content %}{% endblock %}"
    app.jinja_loader = ChoiceLoader(
        [DictLoader({"base.html": stub}), FileSystemLoader(str(TEMPLATES))]
    )
    register_ux_filters(app)
    return app


# ── Shared helper exists + wired into base.html ──────────────────────────────

def test_shared_helper_defines_all_three_exports():
    src = (JS / "esc.js").read_text(encoding="utf-8")
    assert "esc.js" or True
    assert "global.escHtml = escHtml" in src
    assert "global.escAttr = escAttr" in src
    assert "global.safeMarkdown = safeMarkdown" in src
    # safeMarkdown must combine marked + DOMPurify and fail CLOSED.
    assert "DOMPurify" in src and "sanitize" in src
    assert "marked" in src
    assert "_plainFallback" in src  # escaped-text fallback path


def test_base_loads_shared_helper_and_dompurify_globally():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "js/esc.js" in base
    assert "vendor/dompurify/purify.min.js" in base
    # DOMPurify must be loaded OUTSIDE the strategos-only script block so every
    # page can sanitize. Verify its load precedes the strategos-gated
    # strategos-chat.js load (which lives inside the /strategos conditional).
    dompurify_idx = base.index("vendor/dompurify/purify.min.js")
    strategos_script = base.index("js/strategos-chat.js")
    assert dompurify_idx < strategos_script, "DOMPurify must load globally, not only on /strategos"
    # And it must appear exactly once (no duplicate strategos-gated copy).
    assert base.count("vendor/dompurify/purify.min.js") == 1
    # esc.js must load after DOMPurify so safeMarkdown can reach it.
    assert base.index("js/esc.js") > dompurify_idx


# ── json_script_safe filter — the 9 migrated <script> sites ──────────────────

def test_json_script_safe_neutralizes_breakout_and_preserves_json():
    from tools.dashboard.ux_helpers import json_script_safe

    data = [{"name": "x </script><script>alert(1)</script>", "note": "a & b < c > d",
             "spaced": "keep  the   spaces"}]
    serialized = json.dumps(data)
    out = str(json_script_safe(serialized))
    # Breakout sequences are escaped.
    assert "</script>" not in out
    assert "<" not in out and ">" not in out
    assert "\\u003c" in out and "\\u003e" in out
    # Normal spaces are NOT converted to line separators.
    assert "keep  the   spaces".replace("<", "") in out.replace("\\u003c", "<")
    # Semantics preserved: the escaped text is still valid JSON that round-trips.
    assert json.loads(out) == data


def test_json_script_safe_returns_markup():
    from markupsafe import Markup

    from tools.dashboard.ux_helpers import json_script_safe

    assert isinstance(json_script_safe("[]"), Markup)
    assert str(json_script_safe(None)) == "null"


@pytest.mark.parametrize(
    "rel,needle",
    [
        ("agentic_ai_canvas/canvas.html", "design.graph_json"),
        ("infra_canvas/canvas.html", "design.graph_json"),
        ("data_canvas/lineage.html", "dag_json"),
        ("strategos/intel_brief.html", "briefs_json"),
        ("strategos/supply.html", "nodes_json"),
        ("strategos/supply.html", "edges_json"),
        ("security_canvas/assessment.html", "assessment.findings_json"),
        ("security_canvas/assessment.html", "assessment.recommendations_json"),
        ("security_canvas/remediation.html", "p.remediation_steps"),
    ],
)
def test_json_script_sites_migrated_off_safe(rel, needle):
    src = (TEMPLATES / rel).read_text(encoding="utf-8")
    # The variable now flows through json_script_safe, not raw |safe.
    assert f"{needle} | json_script_safe" in src or f"{needle}|json_script_safe" in src
    # No raw `<var> | safe` (or `<var>|safe`) remains for this variable.
    assert f"{needle} | safe" not in src
    assert f"{needle}|safe" not in src


def test_security_canvas_conditional_still_renders_valid_json():
    """The conditional `x|json_script_safe if x else '[]'` must emit parseable JSON."""
    app = _stub_app()
    payload = json.dumps([{"title": XSS_BREAKOUT}])
    with app.app_context():
        out = app.jinja_env.from_string(
            "const findings = {{ v|json_script_safe if v else '[]' }};"
        ).render(v=payload)
    assert "</script>" not in out
    # extract the JS literal and confirm it parses back to the original.
    literal = out.split("=", 1)[1].strip().rstrip(";")
    assert json.loads(literal) == [{"title": XSS_BREAKOUT}]


# ── markdown filter now sanitizes (doc_detail chokepoint) ────────────────────

def test_markdown_filter_sanitizes_active_html():
    from tools.dashboard.ux_helpers import _md_to_html

    out = _md_to_html(
        f"# Title\n\n{XSS_TAG}\n\n<img src=x onerror=alert(1)>\n\n[c](javascript:alert(1))"
    ).lower()
    assert "<script" not in out
    assert "onerror=" not in out
    assert "javascript:" not in out
    assert "<h1" in out  # benign markdown preserved


def test_doc_detail_content_renders_inert_through_markdown_filter():
    app = _stub_app()
    with app.app_context():
        out = app.jinja_env.from_string(
            "{{ s.content | markdown | safe }}"
        ).render(s={"content": f"hello {XSS_TAG}"})
    assert XSS_TAG not in out
    assert "<script>alert(1)" not in out
    assert "hello" in out


# ── strategos brief content_html sanitized server-side ───────────────────────

def test_brief_route_sanitizes_content_html_source():
    src = (REPO / "apps" / "strategos" / "blueprint.py").read_text(encoding="utf-8")
    # The route must sanitize the rendered markdown before handing it to the template.
    assert "_sanitize_html(content_html)" in src


def test_brief_detail_template_renders_sanitized_body_inert():
    """The brief-content chokepoint renders a route-sanitized value inert.

    brief_detail.html references many optional brief globals; render just the
    content block the fix targets (mirrors the nav-sec-07 dat.html approach).
    """
    from tools.docgen.workflow import _sanitize_html

    app = _stub_app()
    content_html = _sanitize_html(f"<p>ok</p>{XSS_TAG}<img src=x onerror=alert(1)>")
    with app.app_context():
        out = app.jinja_env.from_string(
            '<div class="brief-content">{{ content_html | safe }}</div>'
        ).render(content_html=content_html)
    assert XSS_TAG not in out
    assert "<script>alert(1)" not in out
    assert "onerror=" not in out.lower()
    assert "ok" in out  # benign content preserved


# ── JS sinks routed through the shared sanitizer (source scans) ──────────────

def test_chat_js_renderContent_uses_safe_markdown():
    src = (JS / "chat.js").read_text(encoding="utf-8")
    assert "window.safeMarkdown" in src
    # The old unsanitized path must be gone from renderContent.
    assert "try { return marked.parse(text); } catch (e) { /* fall through */ }" not in src


def test_chat_renderers_uses_safe_markdown_and_delegated_copy():
    src = (JS / "chat-renderers.js").read_text(encoding="utf-8")
    assert "window.safeMarkdown" in src
    # Copy button wired by delegation (DOMPurify strips inline onclick).
    assert "msg-code-block__copy" in src
    assert "addEventListener('click'" in src


def test_assistant_widget_uses_safe_markdown():
    src = (JS / "assistant-widget.js").read_text(encoding="utf-8")
    assert "window.safeMarkdown" in src
    # The raw fallback marked.parse must be gone.
    assert "rendered = marked.parse(msg.content)" not in src


def test_writeguard_prefers_safe_markdown():
    src = (JS / "writeguard-embedded.js").read_text(encoding="utf-8")
    assert "window.safeMarkdown" in src


# ── innerHTML row-field escaping (source scans) ──────────────────────────────

def test_boundary_compliance_escapes_gap_fields():
    src = (TEMPLATES / "boundary_canvas" / "compliance.html").read_text(encoding="utf-8")
    assert "window.escHtml" in src
    # Raw interpolation of the gap fields must be gone.
    assert "${g.severity || 'INFO'}" not in src
    assert "${g.title || g.rule || g.description || ''}" not in src


def test_pps_matrix_escapes_row_fields():
    src = (TEMPLATES / "network" / "pps_matrix.html").read_text(encoding="utf-8")
    assert "window.escHtml" in src
    assert "${row.justification}" not in src  # now _e(row.justification)
    assert "${row.protocol}</strong>" not in src


# ── Regression: no NEW raw |safe on the known-untrusted variables ────────────

def test_no_raw_safe_on_known_untrusted_vars():
    untrusted = {
        "agentic_ai_canvas/canvas.html": ["design.graph_json"],
        "infra_canvas/canvas.html": ["design.graph_json"],
        "data_canvas/lineage.html": ["dag_json"],
        "strategos/intel_brief.html": ["briefs_json"],
        "strategos/supply.html": ["nodes_json", "edges_json"],
        "security_canvas/assessment.html": ["assessment.findings_json",
                                            "assessment.recommendations_json"],
        "security_canvas/remediation.html": ["p.remediation_steps"],
    }
    offenders = []
    for rel, vars_ in untrusted.items():
        src = (TEMPLATES / rel).read_text(encoding="utf-8")
        for v in vars_:
            if f"{v} | safe" in src or f"{v}|safe" in src:
                offenders.append(f"{rel}:{v}")
    assert not offenders, f"raw |safe reintroduced on untrusted vars: {offenders}"


# ── Mirror parity — icdev/ twins must match tools/ ───────────────────────────

@pytest.mark.parametrize(
    "rel",
    [
        "dashboard/static/js/esc.js",
        "dashboard/templates/base.html",
        "dashboard/static/js/chat.js",
        "dashboard/static/js/chat-renderers.js",
        "dashboard/static/js/assistant-widget.js",
        "dashboard/static/js/writeguard-embedded.js",
        "dashboard/ux_helpers.py",
        "dashboard/templates/agentic_ai_canvas/canvas.html",
        "dashboard/templates/infra_canvas/canvas.html",
        "dashboard/templates/data_canvas/lineage.html",
        "dashboard/templates/strategos/intel_brief.html",
        "dashboard/templates/strategos/supply.html",
        "dashboard/templates/security_canvas/assessment.html",
        "dashboard/templates/security_canvas/remediation.html",
        "dashboard/templates/boundary_canvas/compliance.html",
        "dashboard/templates/network/pps_matrix.html",
    ],
)
def test_icdev_twin_matches(rel):
    live = REPO / "tools" / rel
    twin = REPO / "icdev" / "tools" / rel
    if not twin.exists():
        pytest.skip(f"no icdev twin for {rel}")
    assert live.read_bytes() == twin.read_bytes(), f"icdev twin drifted: {rel}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
