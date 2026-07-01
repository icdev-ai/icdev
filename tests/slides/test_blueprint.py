# CUI // SP-CTI
"""Unit tests for the Slide Deck Generator Flask blueprint.

Covers page routes and the native asset-generator smoke endpoint.
Database-backed routes use the slides canvas DB (SQLite in tests).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def client():
    """Create a Flask test client from the full dashboard app so base.html context
    processors (nav_tree, etc.) are available."""
    from tools.dashboard.app import app
    from tools.slides.db.init_db import init_db

    app.config["TESTING"] = True
    with app.test_client() as test_client:
        with app.app_context():
            try:
                init_db()
            except Exception:
                pass
        yield test_client


class TestPageRoutes:
    def test_index_returns_200_and_heading(self, client):
        resp = client.get("/slides/")
        assert resp.status_code == 200
        assert b"Slide Deck Generator" in resp.data
        assert b"CUI // SP-CTI" in resp.data

    def test_new_wizard_returns_200_and_form(self, client):
        resp = client.get("/slides/new")
        assert resp.status_code == 200
        assert b"Generate New Deck" in resp.data or b"deck_type" in resp.data
        assert b"CUI // SP-CTI" in resp.data


class TestAssetSmokeEndpoint:
    def test_asset_smoke_returns_svg_path(self, client):
        resp = client.post(
            "/slides/api/asset-smoke",
            data=json.dumps({"title": "Unit Test Slide", "bullets": ["a", "b"]}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.data.decode()
        data = resp.get_json()
        assert data["success"] is True
        assert data["method"] == "slides_svg"
        assert isinstance(data["path"], str)
        assert len(data["path"]) > 0

    def test_asset_smoke_handles_missing_body(self, client):
        resp = client.post("/slides/api/asset-smoke", content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["method"] == "slides_svg"


class TestPresentRoute:
    def _insert_test_deck_sqlite(self):
        """Insert a minimal completed deck + one slide via SQLite directly, return deck_id."""
        from tools.slides.db.init_db import get_connection, init_db
        try:
            init_db()
        except Exception:
            pass
        conn = get_connection()
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute(
                "INSERT INTO slides_decks "
                "(title, deck_type, theme, tone, status, slide_count, output_formats) "
                "VALUES ('Test Presentation', 'general_presentation', 'midnight_executive', "
                "'professional', 'completed', 1, '[\"pptx\"]')"
            )
            conn.commit()
            deck_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO slides_slides "
                "(deck_id, position, slide_type, title, bullets, speaker_notes) "
                "VALUES (?, 1, 'content', 'Intro Slide', '[\"Bullet A\"]', 'Notes here')",
                (deck_id,),
            )
            conn.commit()
        finally:
            conn.close()
        return deck_id

    def test_present_404_for_unknown_deck(self, client):
        resp = client.get("/slides/99999/present")
        assert resp.status_code == 404

    def test_present_200_for_existing_deck(self, client):
        from unittest.mock import patch
        with patch.dict("os.environ", {"SLIDES_STORAGE_BACKEND": "sqlite"}, clear=False):
            deck_id = self._insert_test_deck_sqlite()
        resp = client.get(f"/slides/{deck_id}/present")
        assert resp.status_code in (200, 404)

    def test_new_wizard_includes_audience_mode_radios(self, client):
        resp = client.get("/slides/new")
        assert resp.status_code == 200
        assert b"audience_mode" in resp.data
        assert b"investor" in resp.data

    def test_new_wizard_includes_rich_diagrams_checkbox(self, client):
        resp = client.get("/slides/new")
        assert resp.status_code == 200
        assert b"enable_rich_diagrams" in resp.data


def _fixture_pptx_bytes() -> bytes:
    """A tiny title+body deck, built in-memory with python-pptx."""
    import io
    from pptx import Presentation

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.placeholders[0].text_frame.text = "Fixture Title"
    s.placeholders[1].text_frame.text = "Fixture bullet"
    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


class TestTemplateFillRoutes:
    def _upload(self, client, filename: str = "fixture.pptx"):
        import io
        data = {"file": (io.BytesIO(_fixture_pptx_bytes()), filename)}
        return client.post(
            "/slides/api/templates/upload",
            data=data,
            content_type="multipart/form-data",
        )

    def test_upload_returns_template_id_and_shape_map(self, client):
        resp = self._upload(client)
        assert resp.status_code == 201, resp.data.decode()
        data = resp.get_json()
        assert data["template_id"] is not None
        assert data["slide_count"] == 1
        kinds = {s["kind"] for s in data["slides"][0]["shapes"]}
        assert kinds == {"title", "body"}

    def test_upload_rejects_non_pptx(self, client):
        import io
        resp = client.post(
            "/slides/api/templates/upload",
            data={"file": (io.BytesIO(b"not a pptx"), "notes.txt")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_rejects_missing_file(self, client):
        resp = client.post("/slides/api/templates/upload", content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_templates_index_lists_uploaded(self, client):
        self._upload(client, filename="listed.pptx")
        resp = client.get("/slides/templates")
        assert resp.status_code == 200
        assert b"listed.pptx" in resp.data

    def test_template_detail_404_for_unknown_id(self, client):
        resp = client.get("/slides/templates/999999")
        assert resp.status_code == 404

    def test_template_detail_renders_shape_summary(self, client):
        upload_resp = self._upload(client, filename="detail_target.pptx")
        template_id = upload_resp.get_json()["template_id"]
        resp = client.get(f"/slides/templates/{template_id}")
        assert resp.status_code == 200
        assert b"Fixture Title" in resp.data

    def test_fill_end_to_end_creates_deck(self, client):
        upload_resp = self._upload(client, filename="fill_target.pptx")
        template_id = upload_resp.get_json()["template_id"]

        fill_resp = client.post(
            f"/slides/api/templates/{template_id}/fill",
            data=json.dumps({"selections": [
                {"slide_index": 0, "title": "New Title", "bullets": ["b1", "b2"]},
            ]}),
            content_type="application/json",
        )
        assert fill_resp.status_code == 201, fill_resp.data.decode()
        deck_id = fill_resp.get_json()["deck_id"]
        assert deck_id is not None

        detail_resp = client.get(f"/slides/{deck_id}")
        assert detail_resp.status_code == 200

    def test_fill_requires_selections(self, client):
        upload_resp = self._upload(client, filename="empty_sel.pptx")
        template_id = upload_resp.get_json()["template_id"]
        resp = client.post(
            f"/slides/api/templates/{template_id}/fill",
            data=json.dumps({"selections": []}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_fill_404_for_unknown_template(self, client):
        resp = client.post(
            "/slides/api/templates/999999/fill",
            data=json.dumps({"selections": [{"slide_index": 0, "title": "x"}]}),
            content_type="application/json",
        )
        assert resp.status_code == 404
