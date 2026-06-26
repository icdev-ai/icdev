# CUI // SP-CTI
"""Unit tests for the Pulse hero image generator wrapper.

The wrapper delegates to tools.viz.asset_generator, so these tests mock the
shared dispatcher and verify that the existing Pulse API builds the right
AssetRequest and returns the expected shape.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.pulse.engine.image_generator import (
    check_gpu,
    create_post_image,
    generate_hero_image,
    generate_image,
    generate_svg,
    main,
)


@pytest.fixture
def mock_asset_generator():
    """Patch the AssetGenerator class inside the pulse wrapper module."""
    with patch("tools.pulse.engine.image_generator.AssetGenerator") as MockGen:
        instance = MockGen.return_value
        instance.generate.return_value = {
            "success": True,
            "path": "/tmp/fake.png",
            "method": "pulse_sdxl",
        }
        yield MockGen, instance


@pytest.fixture
def mock_asset_check_gpu():
    """Patch the imported check_gpu delegate from asset_generator."""
    with patch("tools.pulse.engine.image_generator.asset_check_gpu") as mock_fn:
        mock_fn.return_value = {
            "cuda_available": False,
            "device_name": "Mock GPU",
            "vram_total_gb": 0,
            "vram_free_gb": 0,
            "sdxl_turbo_compatible": False,
        }
        yield mock_fn


class TestCheckGpu:
    def test_check_gpu_delegates(self, mock_asset_check_gpu):
        result = check_gpu()
        mock_asset_check_gpu.assert_called_once_with()
        assert result["cuda_available"] is False


class TestGenerateSvg:
    def test_generate_svg_builds_request(self, mock_asset_generator):
        MockGen, instance = mock_asset_generator
        result = generate_svg(
            title="Hero Title",
            category="compliance",
            output_path="/tmp/hero.svg",
            width=100,
            height=200,
        )

        MockGen.assert_called_once()
        assert instance.generate.call_count == 1
        req = instance.generate.call_args[0][0]
        assert req.context == "pulse"
        assert req.title == "Hero Title"
        assert req.category == "compliance"
        assert req.width == 100
        assert req.height == 200
        assert req.output_path == "/tmp/hero.svg"
        assert req.preferred_providers == ["slides_svg"]
        assert result["elapsed_ms"] >= 0


class TestGenerateImage:
    def test_generate_image_builds_request(self, mock_asset_generator):
        MockGen, instance = mock_asset_generator
        result = generate_image(
            title="GPU Hero",
            category="security",
            output_path="/tmp/gpu.png",
            prompt_override="custom prompt",
            width=512,
            height=384,
            steps=8,
            guidance_scale=1.5,
            seed=123,
            topic="zero trust",
        )

        req = instance.generate.call_args[0][0]
        assert req.context == "pulse"
        assert req.title == "GPU Hero"
        assert req.category == "security"
        assert req.prompt == "custom prompt"
        assert req.width == 512
        assert req.height == 384
        assert req.steps == 8
        assert req.guidance_scale == 1.5
        assert req.seed == 123
        assert req.topic == "zero trust"
        assert req.preferred_providers == ["pulse_sdxl", "slides_svg"]
        assert result["elapsed_ms"] >= 0


class TestGenerateHeroImage:
    def test_empty_title_returns_error(self, mock_asset_generator):
        MockGen, _ = mock_asset_generator
        result = generate_hero_image("")
        assert result["success"] is False
        assert "empty title" in result["error"].lower()
        MockGen.assert_not_called()

    def test_generate_hero_image_defaults(self, mock_asset_generator):
        MockGen, instance = mock_asset_generator
        result = generate_hero_image("Default Hero", category="ai")

        MockGen.assert_called_once()
        req = instance.generate.call_args[0][0]
        assert req.context == "pulse"
        assert req.title == "Default Hero"
        assert req.category == "ai"
        assert req.prefer_gpu is True
        assert req.preferred_providers == []
        assert result["elapsed_ms"] >= 0

    def test_generate_hero_image_cpu_mode(self, mock_asset_generator):
        _, instance = mock_asset_generator
        result = generate_hero_image("CPU Hero", prefer_gpu=False)
        req = instance.generate.call_args[0][0]
        assert req.prefer_gpu is False
        assert result["success"] is True


class TestCreatePostImage:
    def test_create_post_image_sets_url(self, mock_asset_generator):
        result = create_post_image("Pipeline Hero", topic="compliance")
        assert result["url"] == result["path"]
        assert result["success"] is True


class TestCli:
    def test_main_health_json(self, mock_asset_check_gpu, capsys):
        with patch.object(sys, "argv", ["image_generator.py", "--health", "--json"]):
            main()
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["cuda_available"] is False

    def test_main_svg_only_json(self, mock_asset_generator, capsys):
        _, instance = mock_asset_generator
        instance.generate.return_value = {
            "success": True,
            "path": "/tmp/cli.svg",
            "method": "slides_svg",
        }
        with patch.object(
            sys, "argv", ["image_generator.py", "--prompt", "T", "--svg-only", "--json"]
        ):
            main()
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["success"] is True
        assert out["method"] == "slides_svg"

    def test_main_gpu_only_json(self, mock_asset_generator, capsys):
        _, instance = mock_asset_generator
        with patch.object(
            sys, "argv", ["image_generator.py", "--prompt", "T", "--gpu-only", "--json"]
        ):
            main()
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["success"] is True
        req = instance.generate.call_args[0][0]
        assert req.preferred_providers == ["pulse_sdxl", "slides_svg"]

    def test_main_failure_exits_with_error(self, mock_asset_generator):
        _, instance = mock_asset_generator
        instance.generate.return_value = {
            "success": False,
            "error": "generation failed",
        }
        with patch.object(
            sys, "argv", ["image_generator.py", "--prompt", "T", "--json"]
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1
