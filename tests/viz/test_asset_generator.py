# CUI // SP-CTI
"""Unit tests for the ICDEV-native media asset generator.

Tests run without GPU or cloud keys; they exercise air-gap gating, caching,
provider selection, and SVG/matplotlib fallback paths.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.viz.asset_generator import (
    AssetGenerator,
    AssetRequest,
    _available_providers,
    _cache_key,
    _configured_priority,
    check_gpu,
    generate_for_slide,
    generate_hero_image,
    is_air_gap_media_mode,
)


class TestAirGapDetection:
    def test_sqlite_backend_is_air_gap(self):
        with patch.dict(os.environ, {"ICDEV_STORAGE_BACKEND": "sqlite"}, clear=False):
            assert is_air_gap_media_mode() is True

    def test_postgresql_without_cloud_keys_is_air_gap(self):
        env = {
            "ICDEV_STORAGE_BACKEND": "postgresql",
            "OPENAI_API_KEY": "",
            "GOOGLE_API_KEY": "",
            "OPENROUTER_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=True):
            assert is_air_gap_media_mode() is True

    def test_postgresql_with_cloud_key_is_not_air_gap(self):
        env = {
            "ICDEV_STORAGE_BACKEND": "postgresql",
            "OPENAI_API_KEY": "sk-test",
        }
        with patch.dict(os.environ, env, clear=True):
            assert is_air_gap_media_mode() is False

    def test_airgap_media_flag_forces_air_gap(self):
        env = {
            "ICDEV_STORAGE_BACKEND": "postgresql",
            "ICDEV_AIRGAP_MEDIA": "1",
            "OPENAI_API_KEY": "sk-test",
        }
        with patch.dict(os.environ, env, clear=True):
            assert is_air_gap_media_mode() is True


class TestProviderSelection:
    def test_available_providers_always_includes_native(self):
        providers = _available_providers()
        assert "slides_svg" in providers
        assert "slides_matplotlib" in providers

    def test_configured_priority_reads_yaml(self, tmp_path):
        yaml_path = tmp_path / "media_providers.yaml"
        yaml_path.write_text("providers:\n  priority:\n    - slides_svg\n    - slides_matplotlib\n")
        with patch("tools.viz.asset_generator._load_providers_config") as mock_cfg:
            mock_cfg.return_value = {"providers": {"priority": ["slides_svg", "slides_matplotlib"]}}
            assert _configured_priority() == ["slides_svg", "slides_matplotlib"]


class TestCaching:
    def test_cache_key_is_deterministic(self):
        req = AssetRequest(context="slides", title="T", bullets=["a", "b"], theme="midnight_executive")
        assert _cache_key(req) == _cache_key(req)

    def test_cache_key_changes_with_content(self):
        req1 = AssetRequest(context="slides", title="T1")
        req2 = AssetRequest(context="slides", title="T2")
        assert _cache_key(req1) != _cache_key(req2)


class TestGeneration:
    def test_generate_for_slide_falls_back_to_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_for_slide(
                title="Test Slide",
                bullets=["Point one", "Point two"],
                theme="midnight_executive",
                output_path=str(Path(tmp) / "out.svg"),
                preferred_providers=["slides_svg"],
            )
            assert result["success"] is True
            assert result["method"] == "slides_svg"
            assert Path(result["path"]).exists()

    def test_generate_hero_image_falls_back_to_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("tools.viz.asset_generator._available_providers", return_value=["slides_svg"]):
                result = generate_hero_image(
                    title="Compliance Update",
                    category="compliance",
                    output_path=str(Path(tmp) / "hero.svg"),
                    prefer_gpu=False,
                )
            assert result["success"] is True
            assert result["method"] == "slides_svg"
            assert Path(result["path"]).exists()

    def test_asset_generator_caches_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen = AssetGenerator(output_dir=Path(tmp))
            req = AssetRequest(
                context="slides",
                title="Cached Slide",
                preferred_providers=["slides_svg"],
            )
            r1 = gen.generate(req)
            r2 = gen.generate(req)
            assert r1["success"] is True
            assert r2.get("cached") is True
            assert r1["path"] == r2["path"]

    def test_air_gap_filters_non_native_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"ICDEV_STORAGE_BACKEND": "sqlite"}, clear=False):
                gen = AssetGenerator(output_dir=Path(tmp))
                req = AssetRequest(
                    context="slides",
                    title="Air Gap Slide",
                    preferred_providers=["dalle", "slides_svg"],
                )
                result = gen.generate(req)
                assert result["success"] is True
                assert result["method"] == "slides_svg"


class TestGpuCheck:
    def test_check_gpu_returns_dict(self):
        result = check_gpu()
        assert isinstance(result, dict)
        assert "cuda_available" in result
        assert "sdxl_turbo_compatible" in result
