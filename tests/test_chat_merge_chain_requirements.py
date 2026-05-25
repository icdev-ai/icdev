"""Unit tests for _merge_chain_requirements() in tools/dashboard/api/chat.py."""

from tools.dashboard.api.chat import _merge_chain_requirements


def _uc(uc_id, reqs):
    return {"id": uc_id, "template_requirements": reqs}


def _req(text, priority="medium"):
    return {"text": text, "priority": priority}


class TestMergeChainRequirements:
    def test_no_duplicates_by_50_char_prefix(self):
        shared_text = "A" * 60  # first 50 chars match
        uc1 = _uc("uc1", [_req(shared_text + "X")])
        uc2 = _uc("uc2", [_req(shared_text + "Y")])

        result = _merge_chain_requirements([uc1, uc2])

        keys = [r["text"].strip().lower()[:50] for r in result]
        assert len(keys) == len(set(keys)), "Duplicate 50-char prefix keys found"
        assert len(result) == 1

    def test_higher_priority_survives_on_conflict(self):
        shared_prefix = "B" * 50
        uc1 = _uc("uc1", [_req(shared_prefix + "extra", priority="low")])
        uc2 = _uc("uc2", [_req(shared_prefix + "other", priority="critical")])

        result = _merge_chain_requirements([uc1, uc2])

        assert len(result) == 1
        assert result[0]["priority"] == "critical"
        assert result[0]["source_uc_id"] == "uc2"

    def test_sorted_by_priority_rank_critical_first(self):
        uc1 = _uc("uc1", [
            _req("low priority requirement text here for testing", priority="low"),
            _req("critical priority requirement text here testing", priority="critical"),
            _req("medium priority requirement text here testing", priority="medium"),
        ])
        uc2 = _uc("uc2", [
            _req("high priority requirement text here for testing", priority="high"),
        ])

        result = _merge_chain_requirements([uc1, uc2])

        priorities = [r["priority"] for r in result]
        assert priorities == ["critical", "high", "medium", "low"]

    def test_unique_requirements_all_preserved(self):
        uc1 = _uc("uc1", [
            _req("first unique requirement text"),
            _req("second unique requirement text"),
        ])
        uc2 = _uc("uc2", [
            _req("third unique requirement text"),
        ])

        result = _merge_chain_requirements([uc1, uc2])

        assert len(result) == 3

    def test_empty_use_cases_returns_empty(self):
        assert _merge_chain_requirements([]) == []

    def test_empty_template_requirements_skipped(self):
        uc1 = _uc("uc1", [])
        uc2 = _uc("uc2", None)
        assert _merge_chain_requirements([uc1, uc2]) == []

    def test_source_uc_id_annotated(self):
        uc1 = _uc("uc-alpha", [_req("some requirement text for annotation test")])
        result = _merge_chain_requirements([uc1])
        assert result[0]["source_uc_id"] == "uc-alpha"

    def test_rank_field_removed_from_output(self):
        uc1 = _uc("uc1", [_req("requirement without internal rank field")])
        result = _merge_chain_requirements([uc1])
        assert "_rank" not in result[0]

    def test_higher_priority_wins_regardless_of_order(self):
        """uc2 processed second but has higher priority — must win."""
        shared_prefix = "C" * 50
        uc1 = _uc("uc1", [_req(shared_prefix, priority="high")])
        uc2 = _uc("uc2", [_req(shared_prefix, priority="critical")])

        result = _merge_chain_requirements([uc1, uc2])

        assert result[0]["priority"] == "critical"
        assert result[0]["source_uc_id"] == "uc2"
