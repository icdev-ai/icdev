"""Tests for compliance/validator.py — requirement count verification."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from compliance.validator import count_and_verify_requirements, REQUIRED_REQUIREMENT_COUNT


class TestCountAndVerifyRequirements:
    def test_exactly_two_requirements_returns_success(self):
        result = count_and_verify_requirements(["req-1", "req-2"])
        assert result["success"] is True

    def test_exactly_two_requirements_correct_count(self):
        result = count_and_verify_requirements(["req-1", "req-2"])
        assert result["count"] == 2

    def test_exactly_two_requirements_expected_is_two(self):
        result = count_and_verify_requirements(["req-1", "req-2"])
        assert result["expected"] == REQUIRED_REQUIREMENT_COUNT

    def test_zero_requirements_returns_failure(self):
        result = count_and_verify_requirements([])
        assert result["success"] is False
        assert result["count"] == 0

    def test_one_requirement_returns_failure(self):
        result = count_and_verify_requirements(["req-1"])
        assert result["success"] is False
        assert result["count"] == 1

    def test_three_requirements_returns_failure(self):
        result = count_and_verify_requirements(["req-1", "req-2", "req-3"])
        assert result["success"] is False
        assert result["count"] == 3

    def test_success_message_included(self):
        result = count_and_verify_requirements(["r1", "r2"])
        assert "verified" in result["message"].lower()

    def test_failure_message_included(self):
        result = count_and_verify_requirements(["r1"])
        assert "mismatch" in result["message"].lower()

    def test_system_spec_constant_is_two(self):
        assert REQUIRED_REQUIREMENT_COUNT == 2

    def test_accepts_dict_requirements(self):
        reqs = [{"id": "AC-1", "status": "satisfied"}, {"id": "AC-2", "status": "satisfied"}]
        result = count_and_verify_requirements(reqs)
        assert result["success"] is True
