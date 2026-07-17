# CUI // SP-CTI
"""Two real duplications in the DIC blueprint.

1. /docdrift re-queried the three acoic tables inline while
   acoic.get_acoic_page_context() existed to bundle exactly those three lists —
   its docstring even spells out the call. The column list lived in two places
   and could drift from the module that owns the schema.

2. /review built `team_map`, then re-declared and rebuilt it identically a few
   lines later. The first block's DB round-trips (one per collection) ran and
   were thrown away.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = REPO_ROOT / "tools" / "document_intelligence" / "blueprint.py"


def _docdrift_src() -> str:
    src = BLUEPRINT.read_text(encoding="utf-8")
    start = src.index('@dic_bp.route("/docdrift")')
    end = src.index("@dic_bp.route", start + 10)
    return src[start:end]


def _review_src() -> str:
    src = BLUEPRINT.read_text(encoding="utf-8")
    start = src.index('@dic_bp.route("/review")')
    end = src.index("@dic_bp.route", start + 10)
    return src[start:end]


class TestDocdriftUsesTheOwningModule:
    def test_route_calls_the_helper(self):
        assert "get_acoic_page_context" in _docdrift_src()

    def test_route_no_longer_hardcodes_the_table_names(self):
        """The schema belongs to acoic; the route should not restate it."""
        src = _docdrift_src()
        for table in ("dic_drift_events", "dic_acoic_regen_queue", "dic_ssp_fragments"):
            assert f"FROM {table}" not in src, f"{table} still queried inline"

    def test_helper_returns_the_three_keys_the_template_needs(self):
        from tools.document_intelligence import acoic
        import inspect
        src = inspect.getsource(acoic.get_acoic_page_context)
        for key in ("drift_events", "regen_queue", "ssp_fragments"):
            assert key in src

    def test_helper_selects_a_superset_of_what_the_route_used(self):
        """Switching is only safe because the helper returns MORE, never less:
        regen adds item_id; fragments add fragment_id/verified/ai_labeled."""
        import inspect
        from tools.document_intelligence import acoic

        drift = inspect.getsource(acoic.list_drift_events)
        for col in ("source", "entity", "severity", "detected_at"):
            assert col in drift
        regen = inspect.getsource(acoic.list_regen_queue)
        for col in ("document_id", "impact_level", "state", "queued_at"):
            assert col in regen
        frag = inspect.getsource(acoic.list_ssp_fragments)
        for col in ("control_id", "document_id", "status"):
            assert col in frag


class TestReviewTeamMapBuiltOnce:
    def test_team_map_is_declared_exactly_once(self):
        """It was declared twice; the first result was discarded by the second
        assignment, so its per-collection queries were pure waste."""
        assert _review_src().count("team_map: dict[str, list[dict]] = {}") == 1

    def test_the_surviving_block_still_covers_both_sources(self):
        """The second block was the superset — versions AND docs. Deleting the
        first must not lose the docs half."""
        src = _review_src()
        assert "for v in pending_versions:" in src
        assert "for pd in pending_docs:" in src

    def test_team_map_still_reaches_the_template(self):
        assert "team_map=team_map" in _review_src()
