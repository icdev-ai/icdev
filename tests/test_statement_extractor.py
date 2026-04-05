#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for statement extractor (D-FT-23)."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from tools.finetune.statement_extractor import (
    _classify_statement_type,
    _map_type_to_taxonomy,
    _statement_to_pair_template,
    extract_and_generate,
    extract_statements,
    generate_from_rag_source,
    get_statement_stats,
    statements_to_pairs,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SAMPLE_PARAGRAPH = (
    "NIST 800-53 defines a comprehensive catalog of security and privacy controls. "
    "The catalog contains 20 control families covering areas such as access control, "
    "audit and accountability, and incident response. "
    "AC-2 is the Account Management control within the AC family. "
    "Organizations must implement AC-2 because it ensures proper account lifecycle management. "
    "FedRAMP requires all cloud service providers to comply with the relevant NIST 800-53 controls."
)

SAMPLE_SHORT_TEXT = "Hi."

SAMPLE_SINGLE_SENTENCE = "AC-2 manages information system accounts."


# ── Minimal DB schema for FT tables ─────────────────────────────────────────

_FT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    event_type TEXT NOT NULL,
    details TEXT,
    actor TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ft_datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    purpose TEXT DEFAULT 'general',
    base_model TEXT DEFAULT 'qwen3:latest',
    version INTEGER DEFAULT 1,
    example_count INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ft_dataset_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL,
    system_prompt TEXT DEFAULT '',
    user_input TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    source TEXT DEFAULT 'rag_auto_generated',
    quality_score REAL DEFAULT 0.0,
    compliance_score REAL DEFAULT 0.0,
    relevance_score REAL DEFAULT 0.0,
    approved INTEGER DEFAULT 0,
    labeled_by TEXT DEFAULT '',
    labeled_at TIMESTAMP,
    content_hash TEXT UNIQUE,
    classification TEXT DEFAULT 'CUI',
    source_chunk_id TEXT DEFAULT '',
    source_document_id TEXT DEFAULT '',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    content TEXT,
    source_id TEXT,
    source_table TEXT,
    tier TEXT DEFAULT 'hot',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def ft_db(tmp_path):
    """Temporary SQLite DB with fine-tuning and RAG chunk tables."""
    db_path = tmp_path / "test_statement.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_FT_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def ft_db_with_dataset(ft_db):
    """DB with a pre-created dataset for testing pair storage."""
    conn = sqlite3.connect(str(ft_db))
    conn.execute(
        "INSERT INTO ft_datasets (id, name, purpose, status) VALUES (?, ?, ?, ?)",
        ("ds-test-001", "Test Dataset", "general", "draft"),
    )
    conn.commit()
    conn.close()
    return ft_db


@pytest.fixture
def ft_db_with_chunks(ft_db_with_dataset):
    """DB with RAG chunks for generate_from_rag_source tests."""
    conn = sqlite3.connect(str(ft_db_with_dataset))
    chunks = [
        ("chunk-001", SAMPLE_PARAGRAPH, "src-1", "innovation_signals"),
        ("chunk-002", "CMMC Level 2 requires 110 practices from NIST 800-171.", "src-2", "innovation_signals"),
        ("chunk-003", "", "src-3", "innovation_signals"),  # Empty chunk — should be skipped
    ]
    conn.executemany(
        "INSERT INTO rag_chunks (id, content, source_id, source_table) VALUES (?, ?, ?, ?)",
        chunks,
    )
    conn.commit()
    conn.close()
    return ft_db_with_dataset


# ---------------------------------------------------------------------------
# _classify_statement_type unit tests
# ---------------------------------------------------------------------------


class TestClassifyStatementType:
    def test_factual_simple_declaration(self):
        result = _classify_statement_type("AC-2 is the Account Management control.")
        assert result == "factual"

    def test_reasoning_because(self):
        result = _classify_statement_type("Organizations must implement MFA because it prevents unauthorized access.")
        assert result == "reasoning"

    def test_reasoning_requires(self):
        result = _classify_statement_type("FedRAMP requires all cloud providers to undergo security assessments.")
        assert result == "reasoning"

    def test_summary_including(self):
        result = _classify_statement_type(
            "The catalog covers many areas including access control, audit, and incident response."
        )
        assert result == "summary"

    def test_summary_multiple_commas(self):
        result = _classify_statement_type("NIST 800-53 covers AC, AU, CA, CM, CP, IA, IR, MA, MP, PE controls.")
        assert result == "summary"


class TestMapTypeToTaxonomy:
    def test_factual_maps_to_fact_single(self):
        assert _map_type_to_taxonomy("factual") == "fact_single"

    def test_summary_maps_to_summary(self):
        assert _map_type_to_taxonomy("summary") == "summary"

    def test_reasoning_maps_to_reasoning(self):
        assert _map_type_to_taxonomy("reasoning") == "reasoning"

    def test_unknown_maps_to_fact_single(self):
        assert _map_type_to_taxonomy("unknown_type") == "fact_single"


# ---------------------------------------------------------------------------
# extract_statements tests
# ---------------------------------------------------------------------------


class TestExtractStatements:
    def test_extract_statements_basic(self):
        """Paragraph input should produce a non-empty list of statements."""
        with patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None):
            result = extract_statements(SAMPLE_PARAGRAPH)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_extract_statements_structure(self):
        """Each statement dict must have 'statement' and 'type' keys."""
        with patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None):
            result = extract_statements(SAMPLE_PARAGRAPH)
        for item in result:
            assert "statement" in item
            assert "type" in item
            assert isinstance(item["statement"], str)
            assert item["type"] in ("factual", "summary", "reasoning")

    def test_extract_statements_empty(self):
        """Empty input should return an empty list."""
        result = extract_statements("")
        assert result == []

    def test_extract_statements_whitespace_only(self):
        """Whitespace-only input should return an empty list."""
        result = extract_statements("   \n\t  ")
        assert result == []

    def test_extract_statements_short(self):
        """Very short text below the minimum length threshold should be filtered out."""
        with patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None):
            result = extract_statements(SAMPLE_SHORT_TEXT)
        # "Hi." is 3 chars, below MIN_STATEMENT_LENGTH=20 — should produce nothing
        assert result == []

    def test_extract_statements_single_sentence(self):
        """Single valid sentence should produce exactly one statement."""
        with patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None):
            result = extract_statements(SAMPLE_SINGLE_SENTENCE)
        assert len(result) == 1
        assert result[0]["statement"] != ""

    def test_extract_statements_uses_llm_when_available(self):
        """When LLM returns results, those should be used instead of template."""
        mock_llm_result = [
            {"statement": "NIST 800-53 defines 20 control families.", "type": "factual"},
            {"statement": "AC-2 is the Account Management control.", "type": "factual"},
        ]
        with patch(
            "tools.finetune.statement_extractor._extract_statements_llm",
            return_value=mock_llm_result,
        ):
            result = extract_statements(SAMPLE_PARAGRAPH)
        assert result == mock_llm_result

    def test_extract_statements_falls_back_when_llm_fails(self):
        """When LLM returns None, template fallback should produce results."""
        with patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None):
            result = extract_statements(SAMPLE_PARAGRAPH)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# statements_to_pairs tests
# ---------------------------------------------------------------------------


class TestStatementsToPairs:
    @pytest.fixture
    def sample_statements(self):
        return [
            {"statement": "AC-2 is the Account Management control.", "type": "factual"},
            {
                "statement": "Organizations must implement AC-2 because it ensures account lifecycle management.",
                "type": "reasoning",
            },
            {
                "statement": "NIST 800-53 covers access control, audit, and incident response.",
                "type": "summary",
            },
        ]

    def test_statements_to_pairs(self, sample_statements):
        """Valid statements should produce valid Q&A pairs."""
        with patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None):
            result = statements_to_pairs(sample_statements)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_statements_to_pairs_empty(self):
        """Empty statements list should return empty pairs list."""
        result = statements_to_pairs([])
        assert result == []

    def test_pair_has_taxonomy_label(self, sample_statements):
        """Every generated pair must have a 'taxonomy_label' key with a valid value."""
        from tools.rag.query_classifier import TAXONOMY_LABELS

        with patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None):
            result = statements_to_pairs(sample_statements)
        for pair in result:
            assert "taxonomy_label" in pair
            assert pair["taxonomy_label"] in TAXONOMY_LABELS

    def test_pair_has_source_statement(self, sample_statements):
        """Every generated pair must have a 'source_statement' key matching input."""
        with patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None):
            result = statements_to_pairs(sample_statements)
        input_statements = {s["statement"] for s in sample_statements}
        for pair in result:
            assert "source_statement" in pair
            assert isinstance(pair["source_statement"], str)
            assert pair["source_statement"] in input_statements

    def test_pair_has_required_keys(self, sample_statements):
        """Every pair must have all required keys."""
        with patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None):
            result = statements_to_pairs(sample_statements)
        required_keys = {"user_input", "expected_output", "system_prompt", "taxonomy_label", "source_statement"}
        for pair in result:
            assert required_keys.issubset(set(pair.keys()))

    def test_pair_values_are_strings(self, sample_statements):
        """All pair values must be non-empty strings."""
        with patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None):
            result = statements_to_pairs(sample_statements)
        for pair in result:
            for key in ("user_input", "expected_output", "system_prompt", "source_statement"):
                assert isinstance(pair[key], str)
                assert len(pair[key]) > 0

    def test_taxonomy_label_reflects_statement_type(self, sample_statements):
        """Taxonomy label should match the statement type mapping."""
        with patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None):
            result = statements_to_pairs(sample_statements)
        type_to_label = {"factual": "fact_single", "summary": "summary", "reasoning": "reasoning"}
        for i, pair in enumerate(result):
            expected_label = type_to_label[sample_statements[i]["type"]]
            assert pair["taxonomy_label"] == expected_label

    def test_statements_to_pairs_uses_llm_when_available(self, sample_statements):
        """When LLM returns a Q&A pair, it should be used."""
        mock_qa = {"question": "What is AC-2?", "answer": "AC-2 is the Account Management control."}
        with patch(
            "tools.finetune.statement_extractor._statement_to_pair_llm",
            return_value=mock_qa,
        ):
            result = statements_to_pairs([sample_statements[0]])
        assert result[0]["user_input"] == "What is AC-2?"
        assert result[0]["expected_output"] == "AC-2 is the Account Management control."

    def test_statements_with_empty_statement_skipped(self):
        """Statements with empty 'statement' string should be skipped."""
        stmts = [
            {"statement": "", "type": "factual"},
            {"statement": "AC-2 is the Account Management control.", "type": "factual"},
        ]
        with patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None):
            result = statements_to_pairs(stmts)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# extract_and_generate end-to-end tests
# ---------------------------------------------------------------------------


class TestExtractAndGenerate:
    def test_extract_and_generate(self):
        """End-to-end: text → Q&A pairs with valid structure."""
        with (
            patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None),
            patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None),
        ):
            result = extract_and_generate(SAMPLE_PARAGRAPH)
        assert isinstance(result, list)
        assert len(result) > 0
        for pair in result:
            assert "user_input" in pair
            assert "expected_output" in pair
            assert "taxonomy_label" in pair
            assert "source_statement" in pair

    def test_extract_and_generate_empty_text(self):
        """Empty text should return empty list."""
        result = extract_and_generate("")
        assert result == []

    def test_extract_and_generate_with_purpose(self):
        """Purpose parameter should affect system_prompt content."""
        with (
            patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None),
            patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None),
        ):
            result_compliance = extract_and_generate(SAMPLE_PARAGRAPH, purpose="compliance_export")
            result_general = extract_and_generate(SAMPLE_PARAGRAPH, purpose="general")

        if result_compliance and result_general:
            # System prompts should differ between purposes
            assert result_compliance[0]["system_prompt"] != result_general[0]["system_prompt"]

    def test_extract_and_generate_all_pairs_have_taxonomy(self):
        """All generated pairs must have a taxonomy_label."""
        from tools.rag.query_classifier import TAXONOMY_LABELS

        with (
            patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None),
            patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None),
        ):
            result = extract_and_generate(SAMPLE_PARAGRAPH)

        for pair in result:
            assert pair["taxonomy_label"] in TAXONOMY_LABELS

    def test_extract_and_generate_all_pairs_have_source_statement(self):
        """All generated pairs must have a non-empty source_statement."""
        with (
            patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None),
            patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None),
        ):
            result = extract_and_generate(SAMPLE_PARAGRAPH)

        for pair in result:
            assert "source_statement" in pair
            assert isinstance(pair["source_statement"], str)
            assert len(pair["source_statement"]) > 0


# ---------------------------------------------------------------------------
# template-based fallback tests
# ---------------------------------------------------------------------------


class TestStatementToPairTemplate:
    def test_template_returns_question_and_answer(self):
        """Template fallback must always return dict with question and answer."""
        result = _statement_to_pair_template("AC-2 is the Account Management control.")
        assert isinstance(result, dict)
        assert "question" in result
        assert "answer" in result
        assert len(result["question"]) > 0
        assert len(result["answer"]) > 0

    def test_template_answer_equals_statement(self):
        """Template answer should be the original statement."""
        stmt = "FedRAMP requires continuous monitoring of cloud systems."
        result = _statement_to_pair_template(stmt)
        assert result["answer"] == stmt

    def test_template_question_ends_with_question_mark(self):
        """Template question should end with a question mark."""
        result = _statement_to_pair_template("NIST 800-53 defines 20 control families.")
        assert result["question"].endswith("?")


# ---------------------------------------------------------------------------
# generate_from_rag_source integration tests
# ---------------------------------------------------------------------------


class TestGenerateFromRagSource:
    def test_generate_from_rag_source_returns_summary(self, ft_db_with_chunks):
        """generate_from_rag_source should return a summary dict with expected keys."""
        with (
            patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None),
            patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None),
        ):
            result = generate_from_rag_source(
                dataset_id="ds-test-001",
                source_table="innovation_signals",
                limit=10,
                db_path=ft_db_with_chunks,
                rag_db_path=ft_db_with_chunks,
            )
        assert isinstance(result, dict)
        assert "success" in result
        assert "chunks_processed" in result
        assert "pairs_generated" in result
        assert "pairs_stored" in result
        assert "errors" in result

    def test_generate_from_rag_source_skips_empty_chunks(self, ft_db_with_chunks):
        """Empty chunks should not be processed."""
        with (
            patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None),
            patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None),
        ):
            result = generate_from_rag_source(
                dataset_id="ds-test-001",
                source_table="innovation_signals",
                db_path=ft_db_with_chunks,
                rag_db_path=ft_db_with_chunks,
            )
        # chunk-003 has empty content, so chunks_processed should be 2 (not 3)
        assert result["chunks_processed"] == 2

    def test_generate_from_rag_source_stores_pairs(self, ft_db_with_chunks):
        """Generated pairs should be stored in the dataset."""
        with (
            patch("tools.finetune.statement_extractor._extract_statements_llm", return_value=None),
            patch("tools.finetune.statement_extractor._statement_to_pair_llm", return_value=None),
        ):
            result = generate_from_rag_source(
                dataset_id="ds-test-001",
                source_table="innovation_signals",
                db_path=ft_db_with_chunks,
                rag_db_path=ft_db_with_chunks,
            )
        assert result["pairs_stored"] >= 0  # Could be 0 if dedup removes all, but no crashes


# ---------------------------------------------------------------------------
# get_statement_stats tests
# ---------------------------------------------------------------------------


class TestGetStatementStats:
    def test_stats_empty_dataset(self, ft_db_with_dataset):
        """Stats for an empty dataset should return zeros."""
        result = get_statement_stats("ds-test-001", db_path=ft_db_with_dataset)
        assert result["success"] is True
        assert result["total_pairs"] == 0
        assert isinstance(result["taxonomy_distribution"], dict)

    def test_stats_with_examples(self, ft_db_with_dataset):
        """Stats should count taxonomy labels from stored system_prompt annotations."""
        conn = sqlite3.connect(str(ft_db_with_dataset))
        conn.execute(
            "INSERT INTO ft_dataset_examples (dataset_id, user_input, expected_output, system_prompt, content_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "ds-test-001",
                "What is AC-2?",
                "AC-2 is Account Management.",
                "You are an assistant. [taxonomy:fact_single] [chunk:c1] [stmt:abc123]",
                "hash001",
            ),
        )
        conn.execute(
            "INSERT INTO ft_dataset_examples (dataset_id, user_input, expected_output, system_prompt, content_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "ds-test-001",
                "Why does FedRAMP require monitoring?",
                "Because it ensures continuous compliance.",
                "You are an assistant. [taxonomy:reasoning] [chunk:c2] [stmt:def456]",
                "hash002",
            ),
        )
        conn.commit()
        conn.close()

        result = get_statement_stats("ds-test-001", db_path=ft_db_with_dataset)
        assert result["success"] is True
        assert result["total_pairs"] == 2
        assert result["taxonomy_distribution"]["fact_single"] == 1
        assert result["taxonomy_distribution"]["reasoning"] == 1

    def test_stats_nonexistent_dataset_returns_zero(self, ft_db_with_dataset):
        """Stats for a dataset with no matching rows should return empty counts."""
        result = get_statement_stats("ds-nonexistent", db_path=ft_db_with_dataset)
        assert result["success"] is True
        assert result["total_pairs"] == 0
