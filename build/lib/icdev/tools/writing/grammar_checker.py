"""Grammar checker — thin wrapper re-exporting from analysis_engine."""

from tools.writing.analysis_engine import _grammar_check


def check_grammar(text: str) -> dict:
    return _grammar_check(text)
