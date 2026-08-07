"""xbm-wake-01 — a failing `scout` must say WHY it failed.

Background: `scout` went dormant for five weeks. #1297 fixed the mechanism that
held it down (the circuit breaker was a permanent latch) and made a failed reflex
stop claiming "metric_threshold_not_met". What that leaves is the reflex's own
half of the contract: `classify_failure` can only report a cause if the reflex
supplies one in ``details['error']``.

Before this change `_github_api()` swallowed every exception into a bare ``None``
plus a ``print()`` nothing captured, so scout reported ``success=False`` with no
error key and the best the daemon could say was "the reflex reported failure".
That is not actionable, because the two failure modes scout actually hits need
OPPOSITE responses:

  * ``401`` — the configured GITHUB_TOKEN is stale. Replace it. (An anonymous
    call would have worked, so this is strictly self-inflicted.)
  * ``403``/``429`` — we are running ANONYMOUSLY against the 60-request/hour
    per-IP budget, and a full 16-repo watchlist scan costs 16-32 calls. A token
    fixes this; a retry does not.

These tests pin that distinction end to end.

The other half of this change -- registering `idp_score_recorder`, which was
enabled in config with a working module but absent from REFLEX_NAMES and so never
dispatched once -- is already guarded by the repo's existing register-or-exempt
gate, tests/test_reflex_registration.py. That gate fails on `main` naming exactly
that reflex and passes with the fix, so it is not duplicated here.
"""

import json
import urllib.error

import pytest

from tools.genesis.reflexes import scout


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.github.com/repos/o/r", code=code, msg="boom", hdrs=None, fp=None
    )


class TestGithubApiClassifiesFailures:
    """`_github_api` must name the failure, not just return None."""

    @pytest.mark.parametrize(
        "code,expected",
        [
            (401, "http_401_bad_github_token"),
            (403, "http_403_rate_limited"),
            (429, "http_429_rate_limited"),
            (404, "http_404"),
            (500, "http_500"),
        ],
    )
    def test_http_errors_are_classified_into_the_sink(self, code, expected, monkeypatch):
        monkeypatch.setattr(scout, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(code)))
        sink: list = []

        assert scout._github_api("/repos/o/r", error_sink=sink) is None
        assert sink == [expected]

    def test_401_and_403_are_distinguishable(self, monkeypatch):
        """The whole point: these two need opposite fixes, so they must not collapse.

        401 says the token we have is bad; 403 says we have no token at all and
        spent the anonymous budget. A single generic "github failed" string would
        send an operator to replace a token that is fine, or to wait out a rate
        limit that will never clear on its own.
        """
        seen = {}
        for code in (401, 403):
            monkeypatch.setattr(scout, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(code)))
            sink: list = []
            scout._github_api("/repos/o/r", error_sink=sink)
            seen[code] = sink[0]

        assert seen[401] != seen[403]
        assert "token" in seen[401]
        assert "rate_limited" in seen[403]

    def test_network_errors_are_classified_by_exception_type(self, monkeypatch):
        monkeypatch.setattr(
            scout, "urlopen", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("no route"))
        )
        sink: list = []

        assert scout._github_api("/repos/o/r", error_sink=sink) is None
        assert sink == ["network_URLError"]

    def test_json_decode_error_is_classified(self, monkeypatch):
        monkeypatch.setattr(
            scout, "urlopen", lambda *a, **k: (_ for _ in ()).throw(json.JSONDecodeError("bad", "", 0))
        )
        sink: list = []

        assert scout._github_api("/repos/o/r", error_sink=sink) is None
        assert sink == ["network_JSONDecodeError"]

    def test_error_sink_is_optional(self, monkeypatch):
        """Callers that pass no sink must still get the old None, not a crash."""
        monkeypatch.setattr(scout, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(403)))

        assert scout._github_api("/repos/o/r") is None

    def test_sink_accumulates_across_calls(self, monkeypatch):
        """run() passes ONE sink across the whole watchlist so it can pick a
        dominant reason; that requires appending, not overwriting."""
        monkeypatch.setattr(scout, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(403)))
        sink: list = []

        for _ in range(3):
            scout._github_api("/repos/o/r", error_sink=sink)

        assert sink == ["http_403_rate_limited"] * 3


class TestErrorSinkReachesTheHelpers:
    """The sink is only useful if the functions run() actually calls thread it."""

    def test_get_repo_info_forwards_the_sink(self, monkeypatch):
        monkeypatch.setattr(scout, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(403)))
        sink: list = []

        assert scout._get_repo_info("owner/repo", error_sink=sink) is None
        assert sink == ["http_403_rate_limited"]

    def test_get_latest_release_forwards_the_sink(self, monkeypatch):
        monkeypatch.setattr(scout, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(401)))
        sink: list = []

        assert scout._get_latest_release("owner/repo", error_sink=sink) is None
        assert sink == ["http_401_bad_github_token"]
