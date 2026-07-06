#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.ace.persona_query (synchronous single-persona query) and
the ace_persona_query MCP tool handler (tools.mcp.gap_handlers)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestQueryPersona:
    def test_empty_role_id_raises_value_error(self):
        from tools.ace.persona_query import query_persona

        with pytest.raises(ValueError):
            query_persona("", "some question")

    def test_empty_question_raises_value_error(self):
        from tools.ace.persona_query import query_persona

        with pytest.raises(ValueError):
            query_persona("architect", "")

    def test_uses_soul_identity_as_system_prompt(self):
        from tools.ace import persona_query

        fake_response = MagicMock(content="Consider the load-bearing constraint here.")
        fake_router = MagicMock()
        fake_router.invoke.return_value = fake_response

        with patch("tools.ace.persona_query.build_identity_preamble", return_value="## Identity\nYou are the architect."), \
             patch("tools.ace.persona_query.get_router", return_value=fake_router):
            answer = persona_query.query_persona("architect", "Should we split this service?")

        assert answer == "Consider the load-bearing constraint here."
        request = fake_router.invoke.call_args[0][1]
        assert request.system_prompt == "## Identity\nYou are the architect."
        assert fake_router.invoke.call_args[0][0] == "ace_persona_query"

    def test_unknown_role_falls_back_to_generalist_system_prompt(self):
        """build_identity_preamble returns '' for a role with no SOUL.md and
        no accumulated facts -- query_persona must still produce an answer,
        framed by a generalist fallback identity, not raise or return ''."""
        from tools.ace import persona_query

        fake_response = MagicMock(content="A generalist answer.")
        fake_router = MagicMock()
        fake_router.invoke.return_value = fake_response

        with patch("tools.ace.persona_query.build_identity_preamble", return_value=""), \
             patch("tools.ace.persona_query.get_router", return_value=fake_router):
            answer = persona_query.query_persona("not_a_real_role", "question")

        assert answer == "A generalist answer."
        request = fake_router.invoke.call_args[0][1]
        assert request.system_prompt == persona_query._UNKNOWN_ROLE_FALLBACK

    def test_context_is_folded_into_user_message_when_provided(self):
        from tools.ace import persona_query

        fake_response = MagicMock(content="ok")
        fake_router = MagicMock()
        fake_router.invoke.return_value = fake_response

        with patch("tools.ace.persona_query.build_identity_preamble", return_value="id"), \
             patch("tools.ace.persona_query.get_router", return_value=fake_router):
            persona_query.query_persona("architect", "Should we split this?", context="Idea: a widget scheduler.")

        request = fake_router.invoke.call_args[0][1]
        user_message = request.messages[0]["content"]
        assert "Idea: a widget scheduler." in user_message
        assert "Should we split this?" in user_message


class TestHandleAcePersonaQuery:
    def test_missing_role_id_returns_error_dict(self):
        from tools.mcp.gap_handlers import handle_ace_persona_query

        result = handle_ace_persona_query({"question": "q"})
        assert "error" in result

    def test_missing_question_returns_error_dict(self):
        from tools.mcp.gap_handlers import handle_ace_persona_query

        result = handle_ace_persona_query({"role_id": "architect"})
        assert "error" in result

    def test_success_returns_answer_key(self):
        from tools.mcp import gap_handlers

        with patch("tools.ace.persona_query.query_persona", return_value="Here's my take."):
            result = gap_handlers.handle_ace_persona_query(
                {"role_id": "architect", "question": "q", "context": "c"}
            )
        assert result == {"answer": "Here's my take.", "role_id": "architect"}

    def test_exception_is_caught_and_returns_error_dict(self):
        """Handlers in this codebase catch their own exceptions and return
        {"error": ...} rather than letting them propagate -- matches the
        established convention (e.g. handle_nova_get_trust_score)."""
        from tools.mcp import gap_handlers

        with patch("tools.ace.persona_query.query_persona", side_effect=RuntimeError("LLM unavailable")):
            result = gap_handlers.handle_ace_persona_query({"role_id": "architect", "question": "q"})
        assert "error" in result
        assert "LLM unavailable" in result["error"]

    def test_no_role_id_generates_a_persona_on_the_fly_from_domain_description(self):
        """Regression: role_id is optional when domain_description is given
        -- a persona is generated (or reused) for that domain via
        tools.ace.persona_generator, and the response's role_id reports
        which persona actually answered."""
        from tools.mcp import gap_handlers

        with patch(
            "tools.ace.persona_generator.get_or_generate_persona",
            return_value={"role_id": "garden_services", "status": "generated"},
        ) as mock_gen, patch(
            "tools.ace.persona_query.query_persona", return_value="Watch seasonal demand."
        ) as mock_query:
            result = gap_handlers.handle_ace_persona_query({
                "question": "q", "domain_description": "a community garden scheduler",
            })

        mock_gen.assert_called_once_with("a community garden scheduler")
        mock_query.assert_called_once_with("garden_services", "q", "")
        assert result == {"answer": "Watch seasonal demand.", "role_id": "garden_services"}
