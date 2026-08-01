# CUI // SP-CTI
"""Security tests for nav-sec-07 — confirmed XSS injection sites (2026-07-18 audit).

Six sites were flagged as stored/reflected XSS. Each fix is verified here.

Server-rendered sites (payload round-trips):
  1. intake PRD HTML — ``markdown(raw_md)`` passed raw HTML through and was rendered
     via ``{{ prd_html | safe }}``. FIX: sanitize the rendered HTML with the repo's
     canonical air-gap-safe ``tools.docgen.workflow._sanitize_html`` (no new dep).
  2. intake PRD markdown-in-<script> — ``json.dumps(raw_md)`` emitted via ``| safe``
     allowed a ``</script>`` breakout. FIX: template uses ``{{ prd_markdown_raw | tojson }}``.
  3. strategos/dat.html — ``{{ history_json|safe }}`` in a <script>. FIX: ``{{ history|tojson }}``.
  6. pulse_post.html — ``{{ post.body_html|safe }}`` (LLM/user HTML). FIX: sanitize
     body_html at the render chokepoint in the route.

JS-side sites (source scans — no live DOM):
  4. strategos commander.html / airspace.html — LLM/feed fields interpolated into
     innerHTML. FIX: escape via an ``esc()`` helper.
  5. static/js/strategos-chat.js — ``marked.parse(content)`` into innerHTML with no
     sanitizer. FIX: ``DOMPurify.sanitize(marked.parse(...))`` (DOMPurify vendored
     locally, air-gap, no CDN), fail-safe to textContent if unavailable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TEMPLATES = REPO / "tools" / "dashboard" / "templates"
STATIC = REPO / "tools" / "dashboard" / "static"

XSS_TAG = "<script>alert(1)</script>"
XSS_BREAKOUT = "</script><script>alert(1)</script>"


# ── Jinja render harness (real template files, stub base) ────────────────────

def _stub_app():
    """Flask app whose loader resolves the real templates but stubs base.html.

    Flask (not vanilla Jinja2) provides the ``tojson`` filter and .html autoescape,
    so we render through ``app.jinja_env`` inside an app context.
    """
    from flask import Flask
    from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

    app = Flask(__name__)
    stub = "{% block title %}{% endblock %}{% block content %}{% endblock %}"
    app.jinja_loader = ChoiceLoader(
        [DictLoader({"base.html": stub}), FileSystemLoader(str(TEMPLATES))]
    )
    return app


def _render(template_name: str, **ctx) -> str:
    app = _stub_app()
    # test_request_context so ``url_for('static', ...)`` in templates resolves.
    with app.test_request_context("/"):
        return app.jinja_env.get_template(template_name).render(**ctx)


# ── Site 1 — PRD HTML sanitized server-side ──────────────────────────────────

def test_site1_prd_html_sanitizer_strips_active_html():
    """The rendered markdown HTML must have script/handlers/js-uris removed."""
    import markdown

    from tools.docgen.workflow import _sanitize_html

    raw_md = (
        "# Title\n\n"
        f"{XSS_TAG}\n\n"
        "<img src=x onerror=alert(1)>\n\n"
        "[click](javascript:alert(1))\n\n"
        "<iframe src=//evil></iframe>"
    )
    rendered = markdown.markdown(raw_md, extensions=["tables", "fenced_code", "nl2br", "toc"])
    assert "<script" in rendered.lower()  # markdown passes raw HTML through (the bug)

    safe = _sanitize_html(rendered)
    low = safe.lower()
    assert "<script" not in low
    assert "onerror=" not in low
    assert "javascript:" not in low
    assert "<iframe" not in low


def test_site1_prd_template_renders_only_sanitized_html():
    """intake_prd_view.html renders prd_html; a route-sanitized value stays inert."""
    import markdown

    from tools.docgen.workflow import _sanitize_html

    prd_html = _sanitize_html(markdown.markdown(XSS_TAG))
    out = _render(
        "intake_prd_view.html",
        session_id="s1",
        session_customer="Cust",
        error=None,
        prd_html=prd_html,
        prd_markdown_raw="",
        classification_banner="CUI",
        generated_at="now",
    )
    assert XSS_TAG not in out
    assert "<script>alert(1)" not in out


# ── Site 2 — PRD markdown inside <script> escaped via tojson ──────────────────

def test_site2_prd_markdown_script_breakout_escaped():
    out = _render(
        "intake_prd_view.html",
        session_id="s1",
        session_customer="Cust",
        error=None,
        prd_html="",
        prd_markdown_raw=XSS_BREAKOUT,
        classification_banner="CUI",
        generated_at="now",
    )
    # tojson escapes '<' -> <, so the closing-script breakout cannot appear live.
    assert XSS_BREAKOUT not in out
    assert "</script><script>" not in out
    assert "\\u003c/script" in out  # proof the payload is present but escaped


def test_site2_template_no_json_safe_pattern():
    src = (TEMPLATES / "intake_prd_view.html").read_text(encoding="utf-8")
    assert "prd_markdown_json" not in src
    assert "prd_markdown_raw | tojson" in src or "prd_markdown_raw|tojson" in src


# ── Site 3 — strategos/dat.html history via tojson ───────────────────────────

def test_site3_dat_history_tojson_escapes_breakout():
    """Render the real dat.html assignment line with a malicious history payload."""
    history = [{"computed_at": "2026-01-01", "dti_score": XSS_BREAKOUT}]
    # The dat.html chart body references many optional globals; render just the
    # assignment mechanism the template now uses to prove escaping end-to-end.
    app = _stub_app()
    with app.app_context():
        out = app.jinja_env.from_string(
            "const historyData = {{ history|tojson }};"
        ).render(history=history)
    assert XSS_BREAKOUT not in out
    assert "</script>" not in out
    assert "\\u003c/script" in out


def test_site3_dat_template_uses_tojson_not_safe():
    src = (TEMPLATES / "strategos" / "dat.html").read_text(encoding="utf-8")
    assert "history_json|safe" not in src
    assert "history_json | safe" not in src
    assert "history|tojson" in src or "history | tojson" in src


# ── Site 6 — pulse post body_html sanitized ──────────────────────────────────

def test_site6_pulse_body_html_sanitizer():
    from tools.docgen.workflow import _sanitize_html

    dirty = f"<p>hello</p>{XSS_TAG}<img src=x onerror=alert(1)>"
    safe = _sanitize_html(dirty)
    low = safe.lower()
    assert "<script" not in low
    assert "onerror=" not in low
    assert "hello" in safe  # benign content preserved


def test_site6_pulse_template_renders_sanitized_body_inert():
    """pulse_post.html emits body_html|safe; a route-sanitized value stays inert."""
    from tools.docgen.workflow import _sanitize_html

    post = {
        "title": "T",
        "body_html": _sanitize_html(f"<p>ok</p>{XSS_TAG}"),
        "body_markdown": "",
        "status": "published",
    }
    out = _render("pulse_post.html", post=post, app_name="ICDEV")
    assert XSS_TAG not in out
    assert "<script>alert(1)" not in out


def test_site6_route_sanitizes_body_html_source():
    """The pulse route must call the sanitizer before rendering.

    nav-misc-03: the pulse routes were extracted from app.py into the
    pulse_api blueprint (tools/dashboard/api/pulse.py); the sanitize call
    moved verbatim with the route.
    """
    src = (REPO / "tools" / "dashboard" / "api" / "pulse.py").read_text(encoding="utf-8")
    assert 'post["body_html"] = _sanitize_post_html' in src


# ── Site 4 — strategos commander/airspace innerHTML escaping (source scan) ────

def test_site4_commander_escapes_llm_fields():
    src = (TEMPLATES / "strategos" / "commander.html").read_text(encoding="utf-8")
    assert "function esc(" in src
    # The flagged LLM/signal fields must be wrapped in esc() before interpolation.
    assert "esc(a.topic" in src
    assert "esc(a.signal_source" in src
    assert "esc(intsum.assessment_snippet)" in src
    # No raw interpolation of these into innerHTML remains.
    assert "${a.topic || 'CCIR'}" not in src
    assert "${intsum.assessment_snippet ||" not in src


def test_site4_airspace_escapes_entity_fields():
    src = (TEMPLATES / "strategos" / "airspace.html").read_text(encoding="utf-8")
    assert "function esc(" in src
    assert "esc(cfg.name)" in src
    assert "esc(cfg.meta)" in src
    assert "esc(cfg.badge)" in src
    assert "${cfg.name}" not in src
    assert "${cfg.meta}" not in src


# ── Site 5 — strategos-chat.js marked output sanitized (source scan) ──────────

def test_site5_chat_js_sanitizes_marked_output():
    src = (STATIC / "js" / "strategos-chat.js").read_text(encoding="utf-8")
    # Unsanitized marked.parse into innerHTML must be gone.
    assert "bubble.innerHTML = marked.parse(content" not in src
    # DOMPurify must wrap marked output.
    assert "DOMPurify.sanitize(marked.parse(" in src
    assert "typeof DOMPurify !== 'undefined'" in src


def test_site5_dompurify_vendored_locally():
    """DOMPurify must be vendored (air-gap: no CDN) and wired into base.html."""
    purify = STATIC / "vendor" / "dompurify" / "purify.min.js"
    assert purify.exists(), "DOMPurify must be vendored locally"
    head = purify.read_text(encoding="utf-8", errors="replace")[:400]
    assert "DOMPurify" in head
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "vendor/dompurify/purify.min.js" in base


# ── Mirror parity — icdev/ twins must match tools/ ───────────────────────────

@pytest.mark.parametrize(
    "rel",
    [
        "dashboard/app.py",
        "dashboard/templates/intake_prd_view.html",
        "dashboard/templates/base.html",
        "dashboard/templates/strategos/dat.html",
        "dashboard/templates/strategos/commander.html",
        "dashboard/templates/strategos/airspace.html",
        "dashboard/static/js/strategos-chat.js",
        "dashboard/static/vendor/dompurify/purify.min.js",
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
