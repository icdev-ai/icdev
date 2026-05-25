import json
from unittest.mock import MagicMock, patch

import pytest

from src.clients.diplomatic_client import DiplomaticClient
from src.clients.osint_client import OSINTClient
from src.clients.satellite_client import SatelliteClient
from src.services.aggregator import AggregatorService, AggregatorError


def _make_mock_resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    m.raise_for_status.return_value = None
    return m


def test_aggregate_outputs_valid_geojson():
    osint = OSINTClient(base_url="http://o")
    sat = SatelliteClient(base_url="http://s")
    dip = DiplomaticClient(base_url="http://d")
    svc = AggregatorService(osint, sat, dip)

    def _route(method, url, **kwargs):
        if url.startswith("http://o/feeds"):
            return _make_mock_resp({
                "items": [
                    {"id": "o1", "source": "osint", "title": "O1", "content": "c", "timestamp": "2026-05-16T10:00:00Z", "metadata": {"longitude": 10.0, "latitude": 20.0}},
                ]
            })
        if url.startswith("http://s/scenes") and "region" not in url.split("?")[-1]:
            return _make_mock_resp({
                "items": [
                    {"id": "s1", "source": "sat", "title": "S1", "content": "c", "timestamp": "2026-05-16T10:00:00Z", "metadata": {"longitude": 30.0, "latitude": 40.0}},
                ]
            })
        if url.startswith("http://d/summaries"):
            return _make_mock_resp({
                "items": [
                    {"id": "d1", "source": "diplomatic", "title": "D1", "content": "c", "timestamp": "2026-05-16T10:00:00Z", "metadata": {"longitude": 50.0, "latitude": 60.0}},
                ]
            })
        return _make_mock_resp({"items": []})

    with patch("requests.request", side_effect=_route):
        result = svc.aggregate()

    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) == 3
    assert result["properties"]["sources"]["osint"] == 1
    assert result["properties"]["sources"]["satellite"] == 1
    assert result["properties"]["sources"]["diplomatic"] == 1

    for f in result["features"]:
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] == "Point"
        assert len(f["geometry"]["coordinates"]) == 2
        assert "properties" in f


def test_aggregate_falls_back_geo_coordinates():
    osint = OSINTClient(base_url="http://o")
    sat = SatelliteClient(base_url="http://s")
    dip = DiplomaticClient(base_url="http://d")
    svc = AggregatorService(osint, sat, dip)

    def _route(method, url, **kwargs):
        if url.startswith("http://o/feeds"):
            return _make_mock_resp({
                "items": [
                    {"id": "o1", "source": "osint", "title": "O1", "content": "c", "timestamp": "2026-05-16T10:00:00Z"},
                ]
            })
        return _make_mock_resp({"items": []})

    with patch("requests.request", side_effect=_route):
        result = svc.aggregate()

    assert len(result["features"]) == 1
    coords = result["features"][0]["geometry"]["coordinates"]
    assert len(coords) == 2


def test_aggregate_raises_on_osint_failure():
    osint = OSINTClient(base_url="http://o")
    sat = SatelliteClient(base_url="http://s")
    dip = DiplomaticClient(base_url="http://d")
    svc = AggregatorService(osint, sat, dip)

    with patch("requests.request", side_effect=Exception("down")):
        with pytest.raises(AggregatorError, match="OSINT fetch failed"):
            svc.aggregate()


def test_to_geojson_string():
    osint = OSINTClient(base_url="http://o")
    sat = SatelliteClient(base_url="http://s")
    dip = DiplomaticClient(base_url="http://d")
    svc = AggregatorService(osint, sat, dip)

    with patch("requests.request", return_value=_make_mock_resp({"items": []})):
        s = svc.to_geojson_string()

    parsed = json.loads(s)
    assert parsed["type"] == "FeatureCollection"
    assert parsed["features"] == []
