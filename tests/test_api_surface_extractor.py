#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for API Surface Extractor (D-API-1)."""

from __future__ import annotations

import textwrap

import pytest

from tools.testing.api_surface_extractor import (
    _compute_mock_targets,
    _compute_module_path,
    extract_api_surface,
    extract_directory,
    format_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_py(tmp_path, filename, code):
    """Write a .py file with dedented code and return its path."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(code), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Function extraction
# ---------------------------------------------------------------------------


class TestFunctionExtraction:
    def test_basic_function(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            '''\
            def greet(name: str, count: int = 1) -> str:
                """Say hello."""
                return f"Hello {name}" * count
        ''',
        )
        surface = extract_api_surface(path)
        assert len(surface["functions"]) == 1
        fn = surface["functions"][0]
        assert fn["name"] == "greet"
        assert fn["return_type"] == "str"
        assert len(fn["parameters"]) == 2
        assert fn["parameters"][0]["name"] == "name"
        assert fn["parameters"][0]["type"] == "str"
        assert fn["parameters"][1]["name"] == "count"
        assert fn["parameters"][1]["default"] == "1"
        assert fn["docstring"] == "Say hello."

    def test_async_function(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            async def fetch(url: str) -> bytes:
                pass
        """,
        )
        surface = extract_api_surface(path)
        fn = surface["functions"][0]
        assert fn["is_async"] is True
        assert fn["name"] == "fetch"
        assert fn["return_type"] == "bytes"

    def test_no_annotation_no_default(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            def process(data):
                pass
        """,
        )
        surface = extract_api_surface(path)
        fn = surface["functions"][0]
        assert fn["parameters"][0]["type"] is None
        assert fn["parameters"][0]["default"] is None
        assert fn["return_type"] is None

    def test_private_function_filtered(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            def public_fn():
                pass

            def _private_fn():
                pass
        """,
        )
        surface = extract_api_surface(path, include_private=False)
        assert len(surface["functions"]) == 1
        assert surface["functions"][0]["name"] == "public_fn"

    def test_private_function_included(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            def public_fn():
                pass

            def _private_fn():
                pass
        """,
        )
        surface = extract_api_surface(path, include_private=True)
        assert len(surface["functions"]) == 2
        names = {f["name"] for f in surface["functions"]}
        assert names == {"public_fn", "_private_fn"}

    def test_decorated_function(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            import functools

            @functools.lru_cache(maxsize=128)
            def cached_fn() -> int:
                return 42
        """,
        )
        surface = extract_api_surface(path)
        fn = surface["functions"][0]
        assert "functools.lru_cache" in fn["decorators"]


# ---------------------------------------------------------------------------
# Class extraction
# ---------------------------------------------------------------------------


class TestClassExtraction:
    def test_basic_class(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            '''\
            class MyService:
                """Service that does things."""

                def __init__(self, host: str, port: int = 8080):
                    self.host = host
                    self.port = port

                def start(self) -> bool:
                    pass

                def _internal(self):
                    pass
        ''',
        )
        surface = extract_api_surface(path)
        assert len(surface["classes"]) == 1
        cls = surface["classes"][0]
        assert cls["name"] == "MyService"
        assert cls["docstring"] == "Service that does things."
        # __init__ params (self skipped)
        assert len(cls["init_params"]) == 2
        assert cls["init_params"][0]["name"] == "host"
        assert cls["init_params"][1]["default"] == "8080"
        # Public methods (excluding _internal and __init__)
        assert len(cls["public_methods"]) == 1
        assert cls["public_methods"][0]["name"] == "start"
        assert cls["public_methods"][0]["return_type"] == "bool"

    def test_class_with_bases(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            class Base:
                pass

            class Child(Base):
                pass
        """,
        )
        surface = extract_api_surface(path)
        child = [c for c in surface["classes"] if c["name"] == "Child"][0]
        assert child["bases"] == ["Base"]

    def test_properties_and_classmethods(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            class Config:
                @property
                def value(self) -> int:
                    return 42

                @classmethod
                def from_dict(cls, data: dict) -> "Config":
                    pass

                @staticmethod
                def default() -> "Config":
                    pass
        """,
        )
        surface = extract_api_surface(path)
        cls = surface["classes"][0]
        assert "value" in cls["properties"]
        assert "from_dict" in cls["class_methods"]
        assert "default" in cls["static_methods"]

    def test_abstract_class(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            from abc import ABC, abstractmethod

            class BaseProvider(ABC):
                @abstractmethod
                def connect(self) -> bool:
                    pass
        """,
        )
        surface = extract_api_surface(path)
        cls = surface["classes"][0]
        assert cls["is_abc"] is True
        assert cls["public_methods"][0]["is_abstract"] is True


# ---------------------------------------------------------------------------
# Dataclass extraction
# ---------------------------------------------------------------------------


class TestDataclassExtraction:
    def test_basic_dataclass(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            from dataclasses import dataclass

            @dataclass
            class SearchResult:
                content: str
                score: float = 0.0
                metadata: dict = None
        """,
        )
        surface = extract_api_surface(path)
        cls = surface["classes"][0]
        assert cls["is_dataclass"] is True
        fields = cls["dataclass_fields"]
        assert len(fields) == 3
        assert fields[0]["name"] == "content"
        assert fields[0]["type"] == "str"
        assert fields[0]["default"] is None
        assert fields[1]["name"] == "score"
        assert fields[1]["default"] == "0.0"
        assert fields[2]["name"] == "metadata"

    def test_dataclass_field_default_factory(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            from dataclasses import dataclass, field

            @dataclass
            class Config:
                tags: list = field(default_factory=list)
                options: dict = field(default_factory=dict)
                name: str = field(default="unnamed")
        """,
        )
        surface = extract_api_surface(path)
        cls = surface["classes"][0]
        fields = {f["name"]: f for f in cls["dataclass_fields"]}
        assert fields["tags"]["default_factory"] == "list"
        assert fields["options"]["default_factory"] == "dict"
        # ast may produce single or double quotes depending on source
        assert "unnamed" in fields["name"]["default"]

    def test_dataclass_post_init(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            from dataclasses import dataclass

            @dataclass
            class Item:
                raw: str
                processed: str = ""

                def __post_init__(self):
                    self.processed = self.raw.upper()

                def validate(self) -> bool:
                    return len(self.raw) > 0
        """,
        )
        surface = extract_api_surface(path)
        cls = surface["classes"][0]
        assert cls["is_dataclass"] is True
        assert len(cls["dataclass_fields"]) == 2
        # validate should appear as public method
        method_names = [m["name"] for m in cls["public_methods"]]
        assert "validate" in method_names

    def test_dataclass_with_complex_types(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            from __future__ import annotations
            from dataclasses import dataclass, field
            from typing import List, Optional

            @dataclass
            class Chunk:
                chunk_id: str
                embeddings: List[float] = field(default_factory=list)
                parent: Optional[str] = None
        """,
        )
        surface = extract_api_surface(path)
        assert surface["future_annotations"] is True
        cls = surface["classes"][0]
        fields = {f["name"]: f for f in cls["dataclass_fields"]}
        assert fields["chunk_id"]["type"] == "str"
        assert fields["parent"]["default"] == "None"


# ---------------------------------------------------------------------------
# Dict constant extraction
# ---------------------------------------------------------------------------


class TestDictConstants:
    def test_upper_case_dict(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            SOURCE_REGISTRY = {
                "innovation_signals": {"table": "signals", "pk": "id", "content_cols": ["title"]},
                "compliance_artifacts": {"table": "artifacts", "pk": "artifact_id", "content_cols": ["body"]},
            }
        """,
        )
        surface = extract_api_surface(path)
        assert len(surface["dict_constants"]) == 1
        dc = surface["dict_constants"][0]
        assert dc["name"] == "SOURCE_REGISTRY"
        assert dc["key_count"] == 2
        assert "innovation_signals" in dc["keys"]
        assert "compliance_artifacts" in dc["keys"]
        # Nested value schema
        assert dc["value_schema"] is not None
        assert "table" in dc["value_schema"]
        assert "content_cols" in dc["value_schema"]

    def test_registry_suffix_dict(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            handler_registry = {
                "create": handle_create,
                "delete": handle_delete,
            }
        """,
        )
        # handler_registry ends with _registry but isn't UPPER_CASE
        # Check that it's NOT captured as UPPER_CASE but IS captured by _REGISTRY suffix
        # Actually _is_upper_name("handler_registry") is False, but the code checks
        # `name.endswith("_REGISTRY")` — lowercase won't match _REGISTRY
        # So this tests that only proper naming conventions are captured
        surface = extract_api_surface(path)
        # handler_registry does not end in _REGISTRY (case sensitive), and is not UPPER
        assert len(surface["dict_constants"]) == 0

    def test_flat_dict_no_value_schema(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            ERROR_CODES = {
                "E001": "Missing field",
                "E002": "Invalid type",
            }
        """,
        )
        surface = extract_api_surface(path)
        dc = surface["dict_constants"][0]
        assert dc["name"] == "ERROR_CODES"
        assert dc["key_count"] == 2
        assert dc["value_schema"] is None  # Flat dict, values aren't dicts


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_scalar_constant(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            MAX_RETRIES = 3
            BASE_URL = "https://example.com"
            CHARS_PER_TOKEN = 4
        """,
        )
        surface = extract_api_surface(path)
        names = {c["name"] for c in surface["constants"]}
        assert "MAX_RETRIES" in names
        assert "BASE_URL" in names
        assert "CHARS_PER_TOKEN" in names
        retries = [c for c in surface["constants"] if c["name"] == "MAX_RETRIES"][0]
        assert retries["type"] == "int"
        assert retries["value"] == "3"

    def test_annotated_constant(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            TIMEOUT: int = 30
        """,
        )
        surface = extract_api_surface(path)
        assert len(surface["constants"]) == 1
        c = surface["constants"][0]
        assert c["name"] == "TIMEOUT"
        assert c["type"] == "int"
        assert c["value"] == "30"


# ---------------------------------------------------------------------------
# Imports and mock targets
# ---------------------------------------------------------------------------


class TestImportsAndMocks:
    def test_from_import(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            from tools.rag.vector_store_provider import SearchResult
            from tools.rag.vector_store_factory import VectorStoreFactory
        """,
        )
        surface = extract_api_surface(path)
        assert len(surface["imports"]) == 2
        assert surface["imports"][0]["module"] == "tools.rag.vector_store_provider"
        assert "SearchResult" in surface["imports"][0]["names"]
        # Mock targets
        assert "tools.rag.vector_store_provider.SearchResult" in surface["mock_targets"]
        assert "tools.rag.vector_store_factory.VectorStoreFactory" in surface["mock_targets"]

    def test_import_alias(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            import numpy as np
        """,
        )
        surface = extract_api_surface(path)
        imp = surface["imports"][0]
        assert imp["module"] == "numpy"
        assert imp["alias"] == "np"

    def test_future_annotations_detected(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            from __future__ import annotations
            def foo() -> int:
                pass
        """,
        )
        surface = extract_api_surface(path)
        assert surface["future_annotations"] is True
        # __future__ import should NOT appear in imports list
        assert len(surface["imports"]) == 0

    def test_mock_target_computation(self):
        imports = [
            {"module": "tools.rag.retriever", "names": ["RAGRetriever"], "alias": None},
            {"module": "os.path", "names": ["join", "exists"], "alias": None},
        ]
        targets = _compute_mock_targets(imports)
        assert "tools.rag.retriever.RAGRetriever" in targets
        assert "os.path.join" in targets
        assert "os.path.exists" in targets


# ---------------------------------------------------------------------------
# __all__ exports
# ---------------------------------------------------------------------------


class TestAllExports:
    def test_all_detected(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            __all__ = ["PublicClass", "public_fn"]

            class PublicClass:
                pass

            def public_fn():
                pass

            def _private():
                pass
        """,
        )
        surface = extract_api_surface(path)
        assert surface["all_exports"] == ["PublicClass", "public_fn"]

    def test_no_all(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            def foo():
                pass
        """,
        )
        surface = extract_api_surface(path)
        assert surface["all_exports"] is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_file(self, tmp_path):
        path = _write_py(tmp_path, "empty.py", "")
        surface = extract_api_surface(path)
        assert surface["functions"] == []
        assert surface["classes"] == []
        assert surface["imports"] == []
        assert surface["constants"] == []
        assert surface["dict_constants"] == []

    def test_syntax_error(self, tmp_path):
        path = _write_py(tmp_path, "bad.py", "def broken(\n")
        surface = extract_api_surface(path)
        assert "error" in surface
        assert "SyntaxError" in surface["error"]

    def test_not_python_file(self, tmp_path):
        path = tmp_path / "readme.txt"
        path.write_text("hello")
        surface = extract_api_surface(str(path))
        assert "error" in surface

    def test_nonexistent_file(self):
        surface = extract_api_surface("/nonexistent/path/module.py")
        assert "error" in surface

    def test_enum_class(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            """\
            from enum import Enum

            class Color(Enum):
                RED = "red"
                GREEN = "green"
                BLUE = "blue"
        """,
        )
        surface = extract_api_surface(path)
        cls = surface["classes"][0]
        assert cls["is_enum"] is True
        members = {m["name"] for m in cls["enum_members"]}
        assert members == {"RED", "GREEN", "BLUE"}


# ---------------------------------------------------------------------------
# Directory extraction
# ---------------------------------------------------------------------------


class TestDirectoryExtraction:
    def test_multi_file_scan(self, tmp_path):
        _write_py(
            tmp_path,
            "alpha.py",
            """\
            def alpha_fn() -> int:
                return 1
        """,
        )
        _write_py(
            tmp_path,
            "beta.py",
            """\
            class BetaClass:
                pass
        """,
        )
        # Non-python file should be ignored
        (tmp_path / "readme.md").write_text("# Hello")

        result = extract_directory(str(tmp_path))
        assert result["file_count"] == 2
        assert "alpha.py" in result["files"]
        assert "beta.py" in result["files"]
        assert result["files"]["alpha.py"]["functions"][0]["name"] == "alpha_fn"
        assert result["files"]["beta.py"]["classes"][0]["name"] == "BetaClass"

    def test_excludes_pycache(self, tmp_path):
        _write_py(tmp_path, "main.py", "X = 1\n")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        _write_py(cache, "cached.py", "Y = 2\n")

        result = extract_directory(str(tmp_path))
        assert result["file_count"] == 1

    def test_not_a_directory(self):
        result = extract_directory("/nonexistent/dir")
        assert "error" in result


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------


class TestFormatMarkdown:
    def test_basic_formatting(self, tmp_path):
        path = _write_py(
            tmp_path,
            "mod.py",
            '''\
            MAX_SIZE = 100

            def process(data: str) -> dict:
                """Process input data."""
                pass

            class Handler:
                def __init__(self, name: str):
                    self.name = name

                def handle(self) -> bool:
                    pass
        ''',
        )
        surface = extract_api_surface(path)
        md = format_markdown(surface)
        assert "## " in md
        assert "process" in md
        assert "Handler" in md
        assert "MAX_SIZE" in md
        assert "__init__ params:" in md

    def test_error_formatting(self):
        surface = {"error": "SyntaxError: invalid syntax"}
        md = format_markdown(surface)
        assert "ERROR:" in md


# ---------------------------------------------------------------------------
# Module path computation
# ---------------------------------------------------------------------------


class TestModulePath:
    def test_init_py(self, tmp_path):
        path = tmp_path / "pkg" / "__init__.py"
        path.parent.mkdir()
        path.write_text("")
        # _compute_module_path handles __init__.py
        mod_path = _compute_module_path(str(path))
        assert mod_path.endswith("pkg")
        assert not mod_path.endswith("__init__")

    def test_regular_module(self, tmp_path):
        path = tmp_path / "pkg" / "module.py"
        path.parent.mkdir()
        path.write_text("")
        mod_path = _compute_module_path(str(path))
        assert mod_path.endswith("pkg.module")


# ---------------------------------------------------------------------------
# Real-world module verification
# ---------------------------------------------------------------------------


class TestRealModules:
    """Verify the extractor produces correct output against actual ICDEV™ modules."""

    def test_rag_retriever(self):
        """Verify RAGRetriever class and standalone functions are correctly extracted."""
        import os

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "tools", "rag", "retriever.py")
        if not os.path.exists(path):
            pytest.skip("tools/rag/retriever.py not found")

        surface = extract_api_surface(path, include_private=True)
        assert "error" not in surface

        # Should have the RAGRetriever class
        class_names = [c["name"] for c in surface["classes"]]
        assert "RAGRetriever" in class_names

        # Should have standalone functions
        fn_names = [f["name"] for f in surface["functions"]]
        assert "_rrf_fusion" in fn_names
        assert "_time_decay_adjust" in fn_names

        # RAGRetriever should have search method
        rag_cls = [c for c in surface["classes"] if c["name"] == "RAGRetriever"][0]
        method_names = [m["name"] for m in rag_cls["public_methods"]]
        assert "search" in method_names

        # search method should have correct params
        search_method = [m for m in rag_cls["public_methods"] if m["name"] == "search"][0]
        param_names = [p["name"] for p in search_method["parameters"]]
        assert "query" in param_names
        assert "top_k" in param_names
        assert "source_types" in param_names

        # Mock targets should include key imports
        assert any("VectorStoreFactory" in t for t in surface["mock_targets"])
        assert any("SearchResult" in t for t in surface["mock_targets"])
