# CUI // SP-CTI
"""An undeclared optional dependency must not silently disable a feature.

`python-dateutil` was imported by two runtime modules and declared in NEITHER
`requirements.txt` nor `pyproject.toml`. Both imports sat inside a bare
`except Exception` returning a benign-looking value, so on an install without it
the failure was invisible:

  * the stale reaper skipped EVERY task — it had never once run on CI;
  * every notification duration rendered "unknown", with nothing to say why.

It passed on Windows, where dateutil arrives transitively as somebody else's
dependency, and failed on the CI runner and on any air-gapped install — the
deployment this project targets. That asymmetry is what kept it alive: the
machine where it was written could not reproduce it.

The parser is now stdlib-only and lives in `tools.common.helpers`.

RELATIONSHIP TO tests/test_undeclared_import_census.py
------------------------------------------------------
Two gates, deliberately, because they encode two different rules:

  * THIS file bans `dateutil` OUTRIGHT — anywhere under tools/, in any form,
    including behind a handler that logs honestly. The package was DELETED
    rather than declared, and the stdlib does the job, so there is no correct
    use left to permit.
  * The census gates the SHAPE for every other package: an undeclared import
    inside a SWALLOWING handler. It permits the same import behind a handler
    that names the missing package, because that degradation is visible and a
    genuinely optional dependency is allowed to be optional.

Neither subsumes the other. Do not delete one as redundant.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timezone

import pytest

from tools.common.helpers import parse_utc_timestamp

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Packages a runtime module must not import without declaring them. This is a
#: named list, not a scan of every third-party import: the point is the FAILURE
#: SHAPE — an optional package behind a swallowing except — and the census that
#: earns an entry here is a real incident, not a hunch.
UNDECLARED_FORBIDDEN = ("dateutil",)


def _imports(path: pathlib.Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, node.lineno


def test_no_runtime_module_imports_dateutil():
    """The gate. An import added back — even inside a try — reopens the defect."""
    offenders = []
    for root in ("tools", "icdev/tools"):
        base = REPO / root
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            for mod, lineno in _imports(py):
                if mod.split(".")[0] in UNDECLARED_FORBIDDEN:
                    offenders.append(f"{py.relative_to(REPO)}:{lineno} imports {mod}")
    assert not offenders, (
        "undeclared third-party import in a runtime module:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse tools.common.helpers.parse_utc_timestamp, or DECLARE the "
          "package in requirements.txt — but note that declaring it does not "
          "fix the shape: an optional import inside a swallowing except fails "
          "silently wherever the package is absent."
    )


def test_the_forbidden_package_is_still_undeclared():
    """If someone declares dateutil properly, this test should be revisited
    rather than left asserting a stale premise. It fails LOUDLY in that case
    instead of quietly protecting a rule that no longer applies."""
    declared = ""
    for name in ("requirements.txt", "pyproject.toml"):
        p = REPO / name
        if p.exists():
            declared += p.read_text(encoding="utf-8").lower()
    for pkg in UNDECLARED_FORBIDDEN:
        assert pkg not in declared, (
            f"{pkg} is now declared — this gate assumed it was not. Decide "
            f"whether to keep banning it (and why) or drop it from "
            f"UNDECLARED_FORBIDDEN."
        )


# ── the replacement parser ─────────────────────────────────────────────────
def test_isoformat_with_offset():
    got = parse_utc_timestamp("2026-08-17T23:41:42.587933+00:00")
    assert got == datetime(2026, 8, 17, 23, 41, 42, 587933, tzinfo=timezone.utc)


def test_trailing_Z_is_accepted():
    """fromisoformat rejected `Z` before 3.11, and stored rows outlive the
    interpreter that wrote them."""
    assert parse_utc_timestamp("2026-08-17T23:41:42Z") == datetime(
        2026, 8, 17, 23, 41, 42, tzinfo=timezone.utc)


def test_a_naive_stamp_is_read_as_utc():
    assert parse_utc_timestamp("2026-08-17T23:41:42").tzinfo is timezone.utc


def test_a_driver_native_datetime_passes_through():
    aware = datetime(2026, 8, 17, tzinfo=timezone.utc)
    assert parse_utc_timestamp(aware) is aware
    assert parse_utc_timestamp(datetime(2026, 8, 17)).tzinfo is timezone.utc


@pytest.mark.parametrize("junk", ["not a timestamp", "", "   ", None])
def test_unreadable_input_is_None_not_a_raise(junk):
    """None so the CALLER decides whether one bad row is fatal — the reaper logs
    and skips that task, the notifier renders 'unknown' for that one duration."""
    assert parse_utc_timestamp(junk) is None


# ── both former call sites still work ──────────────────────────────────────
def test_the_reaper_helper_delegates_rather_than_reimplementing():
    import tools.genesis.reflexes.kanban as km

    assert km._parse_utc_timestamp("2026-08-17T23:41:42Z") == datetime(
        2026, 8, 17, 23, 41, 42, tzinfo=timezone.utc)
    assert km._parse_utc_timestamp("nonsense") is None


def test_duration_str_renders_a_real_duration():
    """It used to return 'unknown' for every input wherever dateutil was
    missing, which is indistinguishable from a genuinely unknown duration."""
    from tools.notification_service.event_service import _duration_str

    assert _duration_str("2026-08-17T10:00:00Z", "2026-08-17T11:02:03Z") == "1h 2m 3s"
    assert _duration_str("2026-08-17T10:00:00Z", "2026-08-17T10:00:00Z") == "0s"


def test_duration_str_still_says_unknown_when_it_genuinely_is():
    from tools.notification_service.event_service import _duration_str

    assert _duration_str(None, "2026-08-17T11:00:00Z") == "unknown"
    assert _duration_str("nonsense", "2026-08-17T11:00:00Z") == "unknown"
