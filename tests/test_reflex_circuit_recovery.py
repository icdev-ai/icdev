# CUI // SP-CTI
"""Circuit-breaker half-open recovery + honest failure reporting (xbm-wake-01).

Regression cover for the two-stage failure that takes a reflex silently offline.

Stage 1 — the reason is erased. ``scout`` fetches GitHub with raw urllib, so when
those calls fail it returns ``success=False`` with no ``error`` key, and the daemon
substitutes the generic ``metric_threshold_not_met``. Measured live: with no
GITHUB_TOKEN configured, scout runs anonymously against a 60-request/hour per-IP
budget it cannot fit in, and all 16 repos return HTTP 403.

Stage 2 — the outage becomes permanent. Three such failures trip the breaker, and
with ``auto_reenable: false`` ``run_due_reflexes`` then skipped the reflex forever
with a bare ``continue`` — no audit row, no alert, no retry, no matter how long
ago the fault cleared. ``research``, ``docs`` and ``kanban`` were all sitting in
that state, each carrying the same uninformative ``metric_threshold_not_met``.

Covers:
  * ``circuit_probe_due`` — cooldown gating, exponential backoff, opt-out.
  * ``scout.run`` — a total API failure names the real reason in ``details.error``.
  * ``_github_api`` — 401 vs rate-limit vs network are distinguishable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from tools.daemon.base import circuit_probe_due


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
CB = {"max_consecutive_failures": 3, "cooldown_minutes": 60, "auto_reenable": True}


class TestCircuitProbeDue:
    def test_closed_breaker_never_probes(self):
        state = {"circuit_breaker_open": 0, "consecutive_failures": 0}
        assert circuit_probe_due(state, CB, now=NOW) is False

    def test_open_breaker_within_cooldown_does_not_probe(self):
        state = {
            "circuit_breaker_open": 1,
            "consecutive_failures": 3,
            "circuit_breaker_tripped_at": _iso(NOW - timedelta(minutes=30)),
        }
        assert circuit_probe_due(state, CB, now=NOW) is False

    def test_open_breaker_after_cooldown_probes(self):
        state = {
            "circuit_breaker_open": 1,
            "consecutive_failures": 3,
            "circuit_breaker_tripped_at": _iso(NOW - timedelta(minutes=61)),
        }
        assert circuit_probe_due(state, CB, now=NOW) is True

    def test_backoff_doubles_per_failed_probe(self):
        # 4 consecutive failures = 1 failed probe past the threshold -> 120m wait.
        state = {
            "circuit_breaker_open": 1,
            "consecutive_failures": 4,
            "circuit_breaker_tripped_at": _iso(NOW - timedelta(minutes=90)),
        }
        assert circuit_probe_due(state, CB, now=NOW) is False
        state["circuit_breaker_tripped_at"] = _iso(NOW - timedelta(minutes=121))
        assert circuit_probe_due(state, CB, now=NOW) is True

    def test_backoff_capped_at_max_cooldown(self):
        # A long-broken reflex must converge to one probe per max_cooldown_minutes,
        # not 2**n minutes (which would silently latch it off again).
        cb = {**CB, "max_cooldown_minutes": 1440}
        state = {
            "circuit_breaker_open": 1,
            "consecutive_failures": 50,
            "circuit_breaker_tripped_at": _iso(NOW - timedelta(minutes=1441)),
        }
        assert circuit_probe_due(state, cb, now=NOW) is True

    def test_auto_reenable_false_restores_latch(self):
        state = {
            "circuit_breaker_open": 1,
            "consecutive_failures": 3,
            "circuit_breaker_tripped_at": _iso(NOW - timedelta(days=30)),
        }
        assert circuit_probe_due(state, {**CB, "auto_reenable": False}, now=NOW) is False

    def test_missing_tripped_at_is_probeable(self):
        # Bookkeeping gap must not strand the reflex permanently.
        state = {"circuit_breaker_open": 1, "consecutive_failures": 3}
        assert circuit_probe_due(state, CB, now=NOW) is True

    def test_unparseable_tripped_at_is_probeable(self):
        state = {
            "circuit_breaker_open": 1,
            "consecutive_failures": 3,
            "circuit_breaker_tripped_at": "not-a-timestamp",
        }
        assert circuit_probe_due(state, CB, now=NOW) is True


class TestScoutSurfacesRealError:
    """A total scan failure must name its cause, not fall through to the daemon's
    generic metric excuse."""

    def _targets(self):
        return [
            {"repo": "owner/one", "category": "ai", "watch": []},
            {"repo": "owner/two", "category": "ai", "watch": []},
        ]

    def test_total_401_failure_reports_bad_token(self, tmp_path):
        from tools.genesis.reflexes import scout

        err = HTTPError("https://api.github.com/repos/owner/one", 401, "Unauthorized", {}, None)
        with patch.object(scout, "_load_targets", return_value=self._targets()), patch.object(
            scout, "BASE_DIR", tmp_path
        ), patch.object(scout, "urlopen", side_effect=err):
            result = scout.run({}, None)

        assert result["success"] is False
        assert result["metric_value"] == 0.0
        details = result["details"]
        # The whole point: an explicit error key, so base.run_reflex does NOT
        # substitute "metric_threshold_not_met".
        assert "error" in details
        assert "http_401_bad_github_token" in details["error"]
        assert details["repos_failed"] == 2
        assert details["api_error_reasons"] == ["http_401_bad_github_token"]

    def test_rate_limit_is_distinguishable_from_bad_token(self, tmp_path):
        from tools.genesis.reflexes import scout

        err = HTTPError("https://api.github.com/repos/owner/one", 403, "Forbidden", {}, None)
        with patch.object(scout, "_load_targets", return_value=self._targets()), patch.object(
            scout, "BASE_DIR", tmp_path
        ), patch.object(scout, "urlopen", side_effect=err):
            result = scout.run({}, None)

        assert "http_403_rate_limited" in result["details"]["error"]

    def test_network_failure_is_distinguishable(self, tmp_path):
        from tools.genesis.reflexes import scout

        with patch.object(scout, "_load_targets", return_value=self._targets()), patch.object(
            scout, "BASE_DIR", tmp_path
        ), patch.object(scout, "urlopen", side_effect=URLError("no route to host")):
            result = scout.run({}, None)

        assert "network_URLError" in result["details"]["error"]

    def test_partial_success_reports_no_error(self, tmp_path):
        """One reachable repo is a successful scan — it must not be labelled an error."""
        from tools.genesis.reflexes import scout

        err = HTTPError("https://api.github.com/repos/owner/two", 404, "Not Found", {}, None)

        def fake_info(owner_repo, description_max_chars=300, timeout=15, error_sink=None):
            if owner_repo == "owner/one":
                return {
                    "full_name": "owner/one",
                    "description": "",
                    "stars": 5,
                    "forks": 1,
                    "language": "Python",
                    "updated_at": "",
                    "open_issues": 0,
                    "topics": [],
                    "archived": False,
                }
            if error_sink is not None:
                error_sink.append(f"http_{err.code}")
            return None

        with patch.object(scout, "_load_targets", return_value=self._targets()), patch.object(
            scout, "BASE_DIR", tmp_path
        ), patch.object(scout, "_get_repo_info", side_effect=fake_info):
            result = scout.run({}, None)

        assert result["success"] is True
        assert "error" not in result["details"]


class TestGithubApiErrorClassification:
    def test_401_records_bad_token_reason(self):
        from tools.genesis.reflexes import scout

        sink: list = []
        err = HTTPError("https://api.github.com/x", 401, "Unauthorized", {}, None)
        with patch.object(scout, "urlopen", side_effect=err):
            assert scout._github_api("/x", error_sink=sink) is None
        assert sink == ["http_401_bad_github_token"]

    def test_429_records_rate_limit_reason(self):
        from tools.genesis.reflexes import scout

        sink: list = []
        err = HTTPError("https://api.github.com/x", 429, "Too Many Requests", {}, None)
        with patch.object(scout, "urlopen", side_effect=err):
            assert scout._github_api("/x", error_sink=sink) is None
        assert sink == ["http_429_rate_limited"]

    def test_sink_is_optional(self):
        from tools.genesis.reflexes import scout

        with patch.object(scout, "urlopen", side_effect=URLError("down")):
            assert scout._github_api("/x") is None
