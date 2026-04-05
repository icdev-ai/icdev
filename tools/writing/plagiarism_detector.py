"""Plagiarism detector — thin wrapper re-exporting from analysis_engine."""

from tools.writing.analysis_engine import _plagiarism_check


def check_plagiarism(text: str) -> dict:
    return _plagiarism_check(text)
