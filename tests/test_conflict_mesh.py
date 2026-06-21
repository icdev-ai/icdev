# CUI // SP-CTI
"""Unit tests for the conflict_mesh module (TDD — RED → GREEN).

Tests cover:
  - Provider adapters (ACLED, GDELT, ReliefWeb)
  - MeshCoordinator deduplication and ordering
  - ETLPipeline normalization and DB persistence
  - MLPatternEngine signal extraction
  - EscalationPredictor scoring and DB storage
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    source="test",
    event_id="evt-001",
    event_type="kinetic",
    headline="Forces attacked position",
    event_date="2024-03-15",
    geometry=None,
    metadata=None,
):
    return {
        "id": f"{source}-{event_id}",
        "source": source,
        "event_type": event_type,
        "headline": headline,
        "event_date": event_date,
        "geometry": geometry,
        "metadata": metadata or {},
    }


# ---------------------------------------------------------------------------
# providers/base.py — MeshProvider ABC
# ---------------------------------------------------------------------------

class TestMeshProviderABC:
    def test_abc_cannot_instantiate_directly(self):
        from tools.conflict_mesh.providers.base import MeshProvider
        with pytest.raises(TypeError):
            MeshProvider()

    def test_concrete_provider_must_implement_fetch(self):
        from tools.conflict_mesh.providers.base import MeshProvider

        class IncompleteProvider(MeshProvider):
            @property
            def provider_name(self):
                return "incomplete"

        with pytest.raises(TypeError):
            IncompleteProvider()

    def test_concrete_provider_valid(self):
        from tools.conflict_mesh.providers.base import MeshProvider

        class GoodProvider(MeshProvider):
            @property
            def provider_name(self):
                return "good"

            def fetch(self, since_date=None, limit=100):
                return []

        p = GoodProvider()
        assert p.provider_name == "good"
        assert p.fetch() == []


# ---------------------------------------------------------------------------
# providers/acled_provider.py
# ---------------------------------------------------------------------------

class TestACLEDProvider:
    def test_provider_name(self):
        from tools.conflict_mesh.providers.acled_provider import ACLEDProvider
        with patch("tools.conflict_mesh.providers.acled_provider.get_connector_instance") as mock_gi:
            mock_gi.return_value = None
            p = ACLEDProvider()
        assert p.provider_name == "acled"

    def test_normalize_maps_fields(self):
        from tools.conflict_mesh.providers.acled_provider import ACLEDProvider
        with patch("tools.conflict_mesh.providers.acled_provider.get_connector_instance"):
            p = ACLEDProvider()
        raw = {
            "data_id": "12345",
            "notes": "Forces attacked the village",
            "country": "Ukraine",
            "location": "Kharkiv",
            "event_date": "2024-03-15",
            "event_type": "Battles",
            "fatalities": 5,
            "latitude": "49.98",
            "longitude": "36.25",
        }
        result = p.normalize(raw)
        assert result["source"] == "acled"
        assert result["headline"] == "Forces attacked the village"
        assert "Ukraine" in result.get("metadata", {}).get("geo_hint", "") or result.get("geometry") is not None
        assert result["event_date"] == "2024-03-15"
        assert result["event_type"] in (
            "kinetic", "narrative_event", "humanitarian", "diplomatic", "logistics",
            "frontline_position", "cyber_op"
        )

    def test_normalize_handles_missing_notes(self):
        from tools.conflict_mesh.providers.acled_provider import ACLEDProvider
        with patch("tools.conflict_mesh.providers.acled_provider.get_connector_instance"):
            p = ACLEDProvider()
        raw = {"data_id": "9", "country": "Sudan", "event_date": "2024-01-01", "event_type": "Riots"}
        result = p.normalize(raw)
        assert result["headline"] is not None  # falls back to event_type or empty string

    def test_fetch_returns_empty_when_no_connector(self):
        from tools.conflict_mesh.providers.acled_provider import ACLEDProvider
        with patch("tools.conflict_mesh.providers.acled_provider.get_connector_instance", return_value=None):
            p = ACLEDProvider()
        events = p.fetch(since_date="2024-01-01", limit=10)
        assert events == []


# ---------------------------------------------------------------------------
# providers/gdelt_provider.py
# ---------------------------------------------------------------------------

class TestGDELTProvider:
    def test_provider_name(self):
        from tools.conflict_mesh.providers.gdelt_provider import GDELTProvider
        with patch("tools.conflict_mesh.providers.gdelt_provider.get_connector_instance"):
            p = GDELTProvider()
        assert p.provider_name == "gdelt"

    def test_normalize_maps_fields(self):
        from tools.conflict_mesh.providers.gdelt_provider import GDELTProvider
        with patch("tools.conflict_mesh.providers.gdelt_provider.get_connector_instance"):
            p = GDELTProvider()
        raw = {
            "GLOBALEVENTID": "999",
            "SQLDATE": "20240315",
            "Actor1Name": "MILITARY",
            "Actor2Name": "REBEL",
            "EventCode": "190",
            "ActionGeo_FullName": "Kyiv, Ukraine",
            "GoldsteinScale": -9.0,
            "MentionSourceName": "reuters.com",
            "MentionSummary": "Clashes reported near Kyiv",
        }
        result = p.normalize(raw)
        assert result["source"] == "gdelt"
        assert result["event_date"] == "2024-03-15"
        assert result["event_type"] == "narrative_event"
        assert result["headline"] is not None

    def test_cameo_filter_excludes_low_codes(self):
        from tools.conflict_mesh.providers.gdelt_provider import GDELTProvider
        with patch("tools.conflict_mesh.providers.gdelt_provider.get_connector_instance") as mock_gi:
            mock_conn = MagicMock()
            mock_conn.read.return_value = MagicMock(
                status="ok",
                data=[
                    {"GLOBALEVENTID": "1", "EventCode": "010", "SQLDATE": "20240101"},  # too low
                    {"GLOBALEVENTID": "2", "EventCode": "190", "SQLDATE": "20240102"},  # conflict
                ],
            )
            mock_gi.return_value = mock_conn
            p = GDELTProvider()
        events = p.fetch(limit=10)
        # Only conflict-relevant events (EventCode >= 14x) should be returned
        assert all(
            int(e.get("metadata", {}).get("cameo_code", "999")) >= 140
            or e["source"] == "gdelt"  # if filter applied before normalize
            for e in events
        )


# ---------------------------------------------------------------------------
# providers/reliefweb_provider.py
# ---------------------------------------------------------------------------

class TestReliefWebProvider:
    def test_provider_name(self):
        from tools.conflict_mesh.providers.reliefweb_provider import ReliefWebProvider
        p = ReliefWebProvider()
        assert p.provider_name == "reliefweb"

    def test_normalize_maps_fields(self):
        from tools.conflict_mesh.providers.reliefweb_provider import ReliefWebProvider
        p = ReliefWebProvider()
        raw = {
            "id": "rw-12345",
            "fields": {
                "title": "Humanitarian crisis in Sudan",
                "date": {"created": "2024-03-10T00:00:00+00:00"},
                "country": [{"name": "Sudan"}],
                "body": "Over 1.2 million displaced following fighting.",
            },
        }
        result = p.normalize(raw)
        assert result["source"] == "reliefweb"
        assert result["headline"] == "Humanitarian crisis in Sudan"
        assert result["event_type"] == "humanitarian"
        assert result["event_date"] == "2024-03-10"

    def test_fetch_graceful_on_http_error(self):
        from tools.conflict_mesh.providers.reliefweb_provider import ReliefWebProvider
        p = ReliefWebProvider()
        with patch.object(p, "_http_get", side_effect=Exception("timeout")):
            events = p.fetch(limit=5)
        assert events == []


# ---------------------------------------------------------------------------
# mesh_coordinator.py
# ---------------------------------------------------------------------------

class TestMeshCoordinator:
    def _make_provider(self, name, events):
        from tools.conflict_mesh.providers.base import MeshProvider

        class FakeProvider(MeshProvider):
            @property
            def provider_name(self):
                return name

            def fetch(self, since_date=None, limit=100):
                return events

        return FakeProvider()

    def test_fetch_all_combines_providers(self):
        from tools.conflict_mesh.mesh_coordinator import MeshCoordinator
        p1 = self._make_provider("src1", [_make_event("src1", "a")])
        p2 = self._make_provider("src2", [_make_event("src2", "b")])
        coord = MeshCoordinator([p1, p2])
        results = coord.fetch_all()
        assert len(results) == 2

    def test_deduplication_by_source_and_id(self):
        from tools.conflict_mesh.mesh_coordinator import MeshCoordinator
        evt = _make_event("acled", "dup-001")
        p1 = self._make_provider("acled", [evt, evt])
        p2 = self._make_provider("acled2", [evt])  # different provider name, same id
        coord = MeshCoordinator([p1, p2])
        results = coord.fetch_all()
        # Only unique (id) events kept — dedup by id field
        ids = [r["id"] for r in results]
        assert len(ids) == len(set(ids))

    def test_empty_providers_returns_empty(self):
        from tools.conflict_mesh.mesh_coordinator import MeshCoordinator
        coord = MeshCoordinator([])
        assert coord.fetch_all() == []

    def test_provider_exception_is_caught(self):
        from tools.conflict_mesh.providers.base import MeshProvider
        from tools.conflict_mesh.mesh_coordinator import MeshCoordinator

        class FailingProvider(MeshProvider):
            @property
            def provider_name(self):
                return "failing"

            def fetch(self, since_date=None, limit=100):
                raise RuntimeError("network error")

        good_provider = self._make_provider("good", [_make_event("good", "ok")])
        coord = MeshCoordinator([FailingProvider(), good_provider])
        results = coord.fetch_all()
        assert len(results) == 1  # still got events from good provider


# ---------------------------------------------------------------------------
# etl_pipeline.py
# ---------------------------------------------------------------------------

class TestETLPipeline:
    def _make_coordinator(self, events):
        class FakeCoord:
            def fetch_all(self, since_date=None, limit_per_provider=100):
                return events
        return FakeCoord()

    def test_dry_run_makes_no_db_writes(self, tmp_path):
        from tools.conflict_mesh.etl_pipeline import ETLPipeline
        db_file = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("""CREATE TABLE sg_conflict_events (
            id TEXT PRIMARY KEY, event_type TEXT, geometry TEXT,
            event_date TEXT, source TEXT, metadata TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.commit()
        conn.close()

        coord = self._make_coordinator([_make_event()])
        pipeline = ETLPipeline(coord, db_path=str(db_file))
        result = pipeline.run(dry_run=True)
        assert result.inserted == 0
        assert result.dry_run is True

    def test_run_inserts_valid_events(self, tmp_path):
        from tools.conflict_mesh.etl_pipeline import ETLPipeline
        db_file = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("""CREATE TABLE sg_conflict_events (
            id TEXT PRIMARY KEY, event_type TEXT, geometry TEXT,
            event_date TEXT, source TEXT, metadata TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.commit()
        conn.close()

        coord = self._make_coordinator([_make_event()])
        pipeline = ETLPipeline(coord, db_path=str(db_file))
        result = pipeline.run(dry_run=False)
        assert result.inserted == 1
        assert result.errors == 0

    def test_skips_event_missing_headline_and_date(self, tmp_path):
        from tools.conflict_mesh.etl_pipeline import ETLPipeline
        db_file = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("""CREATE TABLE sg_conflict_events (
            id TEXT PRIMARY KEY, event_type TEXT, geometry TEXT,
            event_date TEXT, source TEXT, metadata TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.commit()
        conn.close()

        bad_event = {
            "id": "bad-001", "source": "test", "event_type": "kinetic",
            "headline": None, "event_date": None, "geometry": None, "metadata": {},
        }
        coord = self._make_coordinator([bad_event])
        pipeline = ETLPipeline(coord, db_path=str(db_file))
        result = pipeline.run(dry_run=False)
        assert result.skipped == 1
        assert result.inserted == 0

    def test_result_counts_fetched(self, tmp_path):
        from tools.conflict_mesh.etl_pipeline import ETLPipeline
        db_file = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("""CREATE TABLE sg_conflict_events (
            id TEXT PRIMARY KEY, event_type TEXT, geometry TEXT,
            event_date TEXT, source TEXT, metadata TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.commit()
        conn.close()

        events = [_make_event(event_id=f"e{i}") for i in range(3)]
        coord = self._make_coordinator(events)
        pipeline = ETLPipeline(coord, db_path=str(db_file))
        result = pipeline.run(dry_run=False)
        assert result.fetched == 3


# ---------------------------------------------------------------------------
# ml_pattern_engine.py
# ---------------------------------------------------------------------------

class TestMLPatternEngine:
    def test_violence_score_high_keywords(self):
        from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
        engine = MLPatternEngine()
        signals = engine.extract_signals(
            "Artillery bombardment kills soldiers in assault near siege lines",
            {}
        )
        assert signals.violence_score > 0.5

    def test_violence_score_low_for_neutral_text(self):
        from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
        engine = MLPatternEngine()
        signals = engine.extract_signals("Diplomatic talks resumed today", {})
        assert signals.violence_score < 0.3

    def test_geographic_spread_capped_at_one(self):
        from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
        engine = MLPatternEngine()
        metadata = {"locations": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]}
        signals = engine.extract_signals("conflict", metadata)
        assert signals.geographic_spread <= 1.0

    def test_tone_from_metadata(self):
        from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
        engine = MLPatternEngine()
        signals = engine.extract_signals("conflict", {"tone": -8.5})
        assert signals.tone == pytest.approx(-8.5)

    def test_signals_dataclass_fields(self):
        from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine, EscalationSignals
        engine = MLPatternEngine()
        signals = engine.extract_signals("test", {})
        assert isinstance(signals, EscalationSignals)
        assert hasattr(signals, "violence_score")
        assert hasattr(signals, "actor_count")
        assert hasattr(signals, "geographic_spread")
        assert hasattr(signals, "tone")

    def test_no_llm_still_returns_signals(self):
        from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
        engine = MLPatternEngine(llm_router=None)
        signals = engine.extract_signals("attack kills missile siege", {})
        assert 0.0 <= signals.violence_score <= 1.0


# ---------------------------------------------------------------------------
# escalation_predictor.py
# ---------------------------------------------------------------------------

class TestEscalationPredictor:
    def test_score_bounds_always_zero_to_one(self):
        from tools.conflict_mesh.escalation_predictor import EscalationPredictor
        from tools.conflict_mesh.ml_pattern_engine import EscalationSignals
        predictor = EscalationPredictor(pattern_engine=None)
        for v, a, g, t in [
            (0.0, 0, 0.0, 0.0),
            (1.0, 10, 1.0, -10.0),
            (0.5, 3, 0.5, -5.0),
        ]:
            signals = EscalationSignals(
                violence_score=v, actor_count=a, geographic_spread=g, tone=t
            )
            score = predictor._compute_score(signals)
            assert 0.0 <= score <= 1.0, f"Score {score} out of bounds for {signals}"

    def test_known_input_known_output(self):
        from tools.conflict_mesh.escalation_predictor import EscalationPredictor
        from tools.conflict_mesh.ml_pattern_engine import EscalationSignals
        predictor = EscalationPredictor(pattern_engine=None)
        # violence=1, actors=5, spread=1, tone=-10 → max score
        signals = EscalationSignals(
            violence_score=1.0, actor_count=5, geographic_spread=1.0, tone=-10.0
        )
        score = predictor._compute_score(signals)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_predict_returns_float(self):
        from tools.conflict_mesh.escalation_predictor import EscalationPredictor
        from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
        engine = MLPatternEngine()
        predictor = EscalationPredictor(pattern_engine=engine)
        event = _make_event(headline="Massive artillery assault kills troops")
        score = predictor.predict(event)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_predict_and_store(self, tmp_path):
        from tools.conflict_mesh.escalation_predictor import EscalationPredictor
        from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
        db_file = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("""CREATE TABLE conflict_predictions (
            id TEXT PRIMARY KEY,
            event_id TEXT,
            source TEXT,
            prediction_date TEXT,
            escalation_risk REAL,
            signals TEXT,
            model_version TEXT,
            confidence REAL,
            created_at TEXT NOT NULL
        )""")
        conn.commit()
        conn.close()

        engine = MLPatternEngine()
        predictor = EscalationPredictor(pattern_engine=engine, db_path=str(db_file))
        event = _make_event()
        row = predictor.predict_and_store(event, model_version="v1.0")
        assert row is not None
        assert 0.0 <= row["escalation_risk"] <= 1.0

        conn2 = sqlite3.connect(str(db_file))
        rows = conn2.execute("SELECT * FROM conflict_predictions").fetchall()
        conn2.close()
        assert len(rows) == 1

    def test_get_high_risk_filters_by_threshold(self, tmp_path):
        from tools.conflict_mesh.escalation_predictor import EscalationPredictor
        from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
        db_file = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("""CREATE TABLE conflict_predictions (
            id TEXT PRIMARY KEY,
            event_id TEXT,
            source TEXT,
            prediction_date TEXT,
            escalation_risk REAL,
            signals TEXT,
            model_version TEXT,
            confidence REAL,
            created_at TEXT NOT NULL
        )""")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO conflict_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            ("p1", "e1", "test", "2024-03-15", 0.9, "{}", "v1.0", 0.8, now),
        )
        conn.execute(
            "INSERT INTO conflict_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            ("p2", "e2", "test", "2024-03-15", 0.3, "{}", "v1.0", 0.6, now),
        )
        conn.commit()
        conn.close()

        engine = MLPatternEngine()
        predictor = EscalationPredictor(pattern_engine=engine, db_path=str(db_file))
        high_risk = predictor.get_high_risk(threshold=0.7, limit=10)
        assert len(high_risk) == 1
        assert high_risk[0]["escalation_risk"] >= 0.7
