# [TEMPLATE: CUI // SP-CTI]
"""Template-render tests for the BD view added to govcon/pipeline.html
(prop-cap-14): CRM engagement heat badge per pipeline row, and a Forecast
Notices (SAM.gov presolicitation) section.

Renders the real template directly via Jinja2 (same pattern as
tests/test_proposals_detail_action_bar.py) rather than spinning up the full
Flask app factory, since govcon_pipeline_page() lives inside create_app()
which mounts ~40 blueprints. The backend functions themselves
(get_engagement_heat_by_agency, list_forecast_notices) are covered by
tests/test_crm_heat.py and tests/test_sam_scanner_forecast.py.
"""
from pathlib import Path


_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"


def _base_context(**overrides):
    ctx = {
        "stats": {
            "total_opportunities": 0, "total_requirements": 0, "total_patterns": 0,
            "total_capability_maps": 0, "total_drafts": 0, "total_awards": 0,
            "knowledge_blocks": 0, "linked_proposals": 0, "domain_distribution": {},
            "last_pipeline_run": None,
        },
        "opportunities": [],
        "linked_opp_ids": set(),
        "pipeline_rollup": {
            "total_weighted_pipeline_value": 0, "total_potential_value": 0,
            "scored_count": 0, "unscored_count": 0, "opportunities": [],
        },
        "active_proposals": [],
        "forecast_notices": {"notices": [], "count": 0},
    }
    ctx.update(overrides)
    return ctx


def _render(**overrides):
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    stub_base = (
        "{% block global_banner %}{% endblock %}"
        "{% block content %}{% endblock %}"
    )
    env = Environment(
        autoescape=True,
        loader=ChoiceLoader([
            DictLoader({"base.html": stub_base}),
            FileSystemLoader(str(_TEMPLATES_DIR)),
        ]),
    )
    tmpl = env.get_template("govcon/pipeline.html")
    return tmpl.render(**_base_context(**overrides))


class TestForecastNoticesSection:
    def test_heading_present(self):
        html = _render()
        assert "Forecast Notices" in html

    def test_empty_state_message(self):
        html = _render()
        assert "No forecast (presolicitation) notices cached yet." in html

    def test_notice_row_rendered(self):
        html = _render(forecast_notices={
            "notices": [{
                "id": "n1", "solicitation_number": "SOL-2026-001",
                "title": "Cyber Sustainment Presolicitation", "agency": "DoD",
                "posted_date": "2026-06-01",
            }],
            "count": 1,
        })
        assert "SOL-2026-001" in html
        assert "Cyber Sustainment Presolicitation" in html
        assert "2026-06-01" in html

    def test_count_badge_reflects_context(self):
        html = _render(forecast_notices={"notices": [], "count": 7})
        assert "7 presolicitation" in html


class TestEngagementHeatBadge:
    def _proposal(self, **overrides):
        base = {
            "id": "opp-1", "solicitation_number": "SOL-1", "title": "Test Opp",
            "agency": "DoD", "due_date": "2026-12-31", "days_left": 30,
            "status": "writing", "capture_phase": "", "capture_manager": "Jane Doe",
            "computed_pwin_pct": None, "weighted_value": None, "has_pwin_model": False,
            "pwin_factors": None, "win_probability": None, "engagement_heat": None,
        }
        base.update(overrides)
        return base

    def test_no_heat_badge_when_engagement_heat_none(self):
        html = _render(active_proposals=[self._proposal(engagement_heat=None)])
        assert "🔥" not in html
        assert "❄️" not in html

    def test_hot_heat_badge_rendered(self):
        html = _render(active_proposals=[self._proposal(engagement_heat={
            "level": "hot", "score": 80.0, "interaction_count": 15,
        })])
        assert "🔥" in html
        assert "hot" in html

    def test_warm_heat_badge_rendered(self):
        html = _render(active_proposals=[self._proposal(engagement_heat={
            "level": "warm", "score": 40.0, "interaction_count": 5,
        })])
        assert "🌤️" in html

    def test_cold_heat_badge_rendered(self):
        html = _render(active_proposals=[self._proposal(engagement_heat={
            "level": "cold", "score": 5.0, "interaction_count": 1,
        })])
        assert "❄️" in html
