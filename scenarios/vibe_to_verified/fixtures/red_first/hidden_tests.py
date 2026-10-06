# CUI // SP-CTI
# ruff: noqa: F821  -- `chunk` and `raises` are injected by the red-first harness
"""Hidden acceptance tests run against the TEAM's fix. Never shown to players.

Plain ``test_*`` functions -- the red-first harness collects them without pytest.
"""


def test_hidden_tail_is_kept():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_hidden_exact_multiple_keeps_last_window():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_hidden_overlap_covers_every_element():
    assert chunk([1, 2, 3, 4], 2, overlap=1) == [[1, 2], [2, 3], [3, 4]]


def test_hidden_shorter_than_window():
    assert chunk([1, 2], 5) == [[1, 2]]


def test_hidden_empty_input():
    assert chunk([], 3) == []


def test_hidden_invalid_args_still_raise():
    for size, overlap in ((0, 0), (3, 3), (3, -1)):
        with raises(ValueError):
            chunk([1, 2, 3], size, overlap)
