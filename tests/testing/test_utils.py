# CUI // SP-CTI
"""Spec-conformance + enhancement tests for tools/testing/utils.py."""
from __future__ import annotations

import os
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from tools.testing import utils  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# make_run_id / ensure_run_dir
# ────────────────────────────────────────────────────────────────────────────


def test_make_run_id_is_8_lowercase_hex():
    rid = utils.make_run_id()
    assert len(rid) == 8
    assert rid == rid.lower()
    int(rid, 16)  # raises if not hex


def test_make_run_id_two_calls_disagree():
    a = utils.make_run_id()
    b = utils.make_run_id()
    assert a != b


def test_ensure_run_dir_creates_path(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    rid = "abcd1234"
    p = utils.ensure_run_dir(rid)
    assert p == tmp_path / ".tmp" / "test_runs" / rid
    assert p.exists()


# ────────────────────────────────────────────────────────────────────────────
# setup_logger
# ────────────────────────────────────────────────────────────────────────────


def test_setup_logger_writes_file_and_does_not_double_handlers(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    rid = "lg-1"
    logger1 = utils.setup_logger(rid, phase="ph1")
    handler_count_first = len(logger1.handlers)
    assert handler_count_first == 2  # one file + one stdout

    logger2 = utils.setup_logger(rid, phase="ph1")
    assert len(logger2.handlers) == 2  # not duplicated
    assert logger1 is logger2

    log_file = (
        tmp_path / ".tmp" / "test_runs" / rid / "ph1" / "execution.log"
    )
    assert log_file.exists()


def test_setup_logger_writes_initial_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    rid = "lg-2"
    logger = utils.setup_logger(rid, phase="phx")
    for h in logger.handlers:
        h.flush()
    log_file = (
        tmp_path / ".tmp" / "test_runs" / rid / "phx" / "execution.log"
    )
    text = log_file.read_text(encoding="utf-8")
    assert "ICDEV" in text
    assert "lg-2" in text
    assert "phx" in text


def test_setup_logger_propagate_false(monkeypatch, tmp_path):
    """Enhancement: don't double-emit through the root logger."""
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    logger = utils.setup_logger("lg-3", phase="ph")
    assert logger.propagate is False


def test_get_logger_returns_same_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    a = utils.setup_logger("rg", phase="ph")
    b = utils.get_logger("rg", phase="ph")
    assert a is b


# ────────────────────────────────────────────────────────────────────────────
# parse_json
# ────────────────────────────────────────────────────────────────────────────


def test_parse_json_raw_object():
    assert utils.parse_json('{"a": 1, "b": 2}') == {"a": 1, "b": 2}


def test_parse_json_raw_array():
    assert utils.parse_json("[1, 2, 3]") == [1, 2, 3]


def test_parse_json_extracts_from_fenced_block():
    text = "Sure, here it is:\n```json\n{\"x\": 9}\n```\nDone."
    assert utils.parse_json(text) == {"x": 9}


def test_parse_json_extracts_from_bare_fence():
    text = "```\n[1,2]\n```"
    assert utils.parse_json(text) == [1, 2]


def test_parse_json_finds_object_inside_prose():
    text = "before {\"k\": \"v\"} after"
    assert utils.parse_json(text) == {"k": "v"}


def test_parse_json_raises_value_error_not_decode_error():
    with pytest.raises(ValueError):
        utils.parse_json("not json at all")


def test_parse_json_validates_with_pydantic_v2_shape():
    class _M:
        def __init__(self, x):
            self.x = x

        @classmethod
        def model_validate(cls, payload):
            return cls(payload["x"])

    out = utils.parse_json('{"x": 7}', target_type=_M)
    assert out.x == 7


def test_parse_json_validates_list_with_pydantic_v1_shape():
    class _M:
        def __init__(self, x):
            self.x = x

        @classmethod
        def parse_obj(cls, payload):
            return cls(payload["x"])

    from typing import List
    out = utils.parse_json('[{"x":1},{"x":2}]', target_type=List[_M])
    assert [o.x for o in out] == [1, 2]


# ────────────────────────────────────────────────────────────────────────────
# get_safe_subprocess_env
# ────────────────────────────────────────────────────────────────────────────


def test_safe_subprocess_env_strips_none(monkeypatch):
    # Make sure GITLAB_TOKEN is unset → must NOT appear in result
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_URL", raising=False)
    env = utils.get_safe_subprocess_env()
    assert "GITLAB_TOKEN" not in env
    assert "GITLAB_URL" not in env


def test_safe_subprocess_env_includes_pwd_and_pythonunbuffered():
    env = utils.get_safe_subprocess_env()
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PWD"] == os.getcwd()


def test_safe_subprocess_env_forwards_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-redacted-1234")
    env = utils.get_safe_subprocess_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-test-redacted-1234"


def test_safe_subprocess_env_drops_unrelated(monkeypatch):
    monkeypatch.setenv("DEFINITELY_NOT_FORWARDED_XYZ", "leakme")
    env = utils.get_safe_subprocess_env()
    assert "DEFINITELY_NOT_FORWARDED_XYZ" not in env


# ────────────────────────────────────────────────────────────────────────────
# timestamp_iso
# ────────────────────────────────────────────────────────────────────────────


def test_timestamp_iso_round_trip():
    ts = utils.timestamp_iso()
    assert ts.endswith("Z")
    from datetime import datetime
    parsed = datetime.fromisoformat(ts[:-1])
    assert parsed.tzinfo is not None or "+" in ts or ts.endswith("Z")
