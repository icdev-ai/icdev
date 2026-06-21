"""Tests for MarkItDown DIC adapter (adapt-md-04)."""
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import os

BASE_DIR = Path(__file__).resolve().parent.parent
ADAPTER_MODULE = "tools.document_intelligence.converters.markitdown_adapter"


def test_adapter_module_importable():
    """adapt-md-02: Module must be importable."""
    from tools.document_intelligence.converters.markitdown_adapter import (
        convert,
        is_available,
        should_use_markitdown,
        SUPPORTED_EXTENSIONS,
    )
    assert callable(convert)
    assert callable(is_available)
    assert callable(should_use_markitdown)
    assert isinstance(SUPPORTED_EXTENSIONS, frozenset)


def test_supported_extensions_covers_office_formats():
    from tools.document_intelligence.converters.markitdown_adapter import SUPPORTED_EXTENSIONS
    for ext in (".docx", ".pptx", ".xlsx", ".pdf"):
        assert ext in SUPPORTED_EXTENSIONS, f"{ext} must be in SUPPORTED_EXTENSIONS"


def test_is_available_returns_bool():
    from tools.document_intelligence.converters.markitdown_adapter import is_available
    result = is_available()
    assert isinstance(result, bool)


def test_convert_missing_file_returns_empty_extraction():
    from tools.document_intelligence.converters.markitdown_adapter import convert
    result = convert(Path("/nonexistent/file.docx"))
    assert result.text == "" or "not found" in " ".join(result.warnings).lower()
    assert result.provider in ("markitdown", "markitdown-unavailable", "markitdown-error")


def test_graceful_degradation_when_markitdown_absent():
    """adapt-md-02: Returns empty Extraction (not exception) when markitdown missing."""
    from tools.document_intelligence.converters import markitdown_adapter

    # Temporarily force unavailable
    original = markitdown_adapter._markitdown_available
    markitdown_adapter._markitdown_available = False
    try:
        result = markitdown_adapter.convert(Path("dummy.docx"))
        assert result.text == ""
        assert result.provider == "markitdown-unavailable"
        assert len(result.warnings) > 0
    finally:
        markitdown_adapter._markitdown_available = original


def test_should_use_markitdown_false_for_preferred_builtins():
    from tools.document_intelligence.converters.markitdown_adapter import should_use_markitdown
    # Plain text files should use built-in extractors
    assert not should_use_markitdown(".txt")
    assert not should_use_markitdown(".md")
    assert not should_use_markitdown(".py")


def test_should_use_markitdown_true_when_available_and_docx():
    from tools.document_intelligence.converters import markitdown_adapter
    original = markitdown_adapter._markitdown_available
    markitdown_adapter._markitdown_available = True
    try:
        result = markitdown_adapter.should_use_markitdown(".docx")
        assert result is True
    finally:
        markitdown_adapter._markitdown_available = original


def test_extract_file_falls_back_to_builtin_when_markitdown_absent():
    """adapt-md-03: extract_file must still work when MarkItDown unavailable."""
    from tools.document_intelligence import extractors
    from tools.document_intelligence.converters import markitdown_adapter

    original = markitdown_adapter._markitdown_available
    markitdown_adapter._markitdown_available = False
    try:
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
            f.write("hello world")
            tmp_path = f.name
        try:
            result = extractors.extract_file(tmp_path)
            assert "hello world" in result.text
        finally:
            os.unlink(tmp_path)
    finally:
        markitdown_adapter._markitdown_available = original


def test_convert_with_mocked_markitdown():
    """Test successful conversion path via mocked markitdown library."""
    from tools.document_intelligence.converters import markitdown_adapter

    mock_result = MagicMock()
    mock_result.text_content = "# Header\n\nBody text here."
    mock_result.title = "Test Document"

    mock_md_instance = MagicMock()
    mock_md_instance.convert.return_value = mock_result

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        markitdown_adapter._markitdown_available = True
        with patch.dict("sys.modules", {"markitdown": MagicMock(MarkItDown=MagicMock(return_value=mock_md_instance))}):
            result = markitdown_adapter.convert(tmp_path)
        assert result.text == "# Header\n\nBody text here."
        assert result.provider == "markitdown"
        assert result.title == "Test Document"
    finally:
        tmp_path.unlink(missing_ok=True)
        markitdown_adapter._markitdown_available = None  # reset cache
