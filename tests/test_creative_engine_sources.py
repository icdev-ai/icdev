"""Creative engine sources, domains and dedup (engine loose ends found on 2026-11-08).

* robots.txt is honoured and fail closed (tools/http/robots.py); Reddit, whose robots.txt disallows every
  client, is read only through its OAuth Data API, and without credentials the scan says so once;
* Product Hunt is read from its public Atom feed, held to the active domain's keywords, each launch once;
* subreddits and Product Hunt topics follow the active domain; an unknown --domain is an error naming the
  domains that exist, never a silent fallback;
* a pain point is versioned append-only (D6), so one gap and one spec per pain point -- not per version;
  ``dedupe_specs`` rejects the extra specs made before the fix, deleting nothing;
* release titles that are only a version, a nightly build or a codename are not "gaps".
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tests.innovation.test_innovation_kanban_promoter import _table_ddl  # noqa: E402
from tools.creative import competitor_discoverer as cd  # noqa: E402
from tools.creative import source_scanner as ss  # noqa: E402
from tools.http import github_token, robots  # noqa: E402


class _Resp:
    def __init__(self, status=200, text="", body=None):
        self.status_code, self.text = status, text
        self._body = body

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _clean():
    robots.clear()
    github_token.clear()
    yield
    robots.clear()
    github_token.clear()


# --- robots.txt ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("status", "text", "path", "allowed"), [
    (200, "User-agent: *\nDisallow: /private", "/public/x", True),
    (200, "User-agent: *\nDisallow: /private", "/private/x", False),
    (200, "User-agent: *\nDisallow: /", "/anything", False),
    (404, "", "/anything", True),
    (403, "", "/anything", False),
    (503, "", "/anything", False),
])
def test_robots_rules_apply_and_unreadable_rules_refuse(status, text, path, allowed):
    with mock.patch("tools.http.client.request", return_value=_Resp(status, text)):
        assert robots.allows(f"https://site.example{path}", "ua") is allowed


def test_robots_refuses_a_network_failure_and_a_non_http_url():
    with mock.patch("tools.http.client.request", side_effect=OSError("down")):
        assert robots.allows("https://site.example/x", "ua") is False
    assert robots.allows("file:///etc/passwd", "ua") is False


# --- Reddit -------------------------------------------------------------------------------------------------------

def _reddit_config():
    return {"sources": {"community_forums": {"enabled": True, "platforms": [
        {"name": "reddit", "subreddits": ["selfhosted", "DataHoarder"], "min_upvotes": 0,
         "subreddits_by_domain": {"data pipeline": ["dataengineering"]}}]}},
        "data_pipeline": {"name": "data pipeline", "keywords": ["etl"]}}


def test_reddit_without_api_credentials_fetches_nothing_and_says_why_once(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    with mock.patch.object(ss, "_http_request") as http:
        sig = ss.scan_reddit(_reddit_config())
    http.assert_not_called()
    assert len(sig) == 1 and sig[0]["source_type"] == "scan_error"
    assert "robots.txt disallows automated access" in sig[0]["body"] and "REDDIT_CLIENT_ID" in sig[0]["body"]


def test_reddit_reads_the_oauth_api_for_the_active_domains_subreddits(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    calls = []
    listing = {"data": {"children": [{"data": {"id": "p1", "title": "Our ETL keeps drifting", "selftext": "", "score": 5,
                                               "permalink": "/r/dataengineering/p1"}}]}}

    def fake(method, url, **kw):
        calls.append((method, url, kw.get("headers", {}).get("Authorization")))
        if method == "POST":
            return _Resp(200, body={"access_token": "tok"})
        return _Resp(200)

    cfg = {**_reddit_config(), "_active_domain": "data pipeline"}
    with mock.patch.object(ss, "_http_request", side_effect=fake), mock.patch.object(ss, "_safe_get", return_value=(listing, None)) as get:
        sig = ss.scan_reddit(cfg)
    assert calls[0][0] == "POST" and calls[0][1] == "https://www.reddit.com/api/v1/access_token"
    [(args, kw)] = [(c.args, c.kwargs) for c in get.call_args_list]
    assert args[0] == "https://oauth.reddit.com/r/dataengineering/hot" and kw["headers"]["Authorization"] == "Bearer tok"
    assert [s["title"] for s in sig] == ["Our ETL keeps drifting"]


# --- Product Hunt -------------------------------------------------------------------------------------------------

ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>PipeKit</title><link href="https://ph.example/pipekit"/><content>An ETL tool for &lt;b&gt;data pipelines&lt;/b&gt;</content>
<author><name>ann</name></author></entry>
<entry><title>NotchIsland</title><link href="https://ph.example/notch"/><content>A menu bar toy</content></entry>
</feed>"""
BOMB = """<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><feed><entry><title>&b;</title></entry></feed>"""


def _ph_config(**extra):
    return {"sources": {"producthunt": {"enabled": True, "topics": ["file-sync"],
                                        "topics_by_domain": {"data pipeline": ["data-engineering", "data-integration"]}}},
            "data_pipeline": {"name": "data pipeline", "keywords": ["etl", "data pipeline"]}, **extra}


def test_atom_and_rss_entries_parse_and_a_dtd_is_refused():
    [a, b] = ss._parse_atom_or_rss(ATOM)
    assert (a["title"], a["url"], a["author"]) == ("PipeKit", "https://ph.example/pipekit", "ann") and "data pipelines" in a["body"]
    rss = "<rss><channel><item><title>T</title><link>https://x/1</link><description>d</description></item></channel></rss>"
    assert ss._parse_atom_or_rss(rss) == [{"title": "T", "url": "https://x/1", "body": "d", "author": None}]
    with pytest.raises(ValueError, match="feed_parse"):
        ss._parse_atom_or_rss(BOMB)


def test_product_hunt_reads_the_domains_feeds_keeps_matching_launches_once(monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    urls = []

    def fake(method, url, **kw):
        urls.append(url)
        return _Resp(200, ATOM)
    with mock.patch.object(ss, "_http_request", side_effect=fake), mock.patch("tools.http.robots.allows", return_value=True):
        sig = ss.scan_producthunt(_ph_config(_active_domain="data pipeline"))
    assert urls == ["https://www.producthunt.com/feed?category=data-engineering", "https://www.producthunt.com/feed?category=data-integration"]
    assert [s["title"] for s in sig] == ["PipeKit"]                     # the toy is off-domain; PipeKit is kept once
    assert json.loads(sig[0]["metadata"])["via"] == "atom_feed"


def test_product_hunt_honours_robots_and_reports_http_errors(monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    with mock.patch("tools.http.robots.allows", return_value=False), mock.patch.object(ss, "_http_request") as http:
        sig = ss.scan_producthunt(_ph_config())
    http.assert_not_called()
    assert [s["body"] for s in sig] == ["robots_disallowed"]
    with mock.patch("tools.http.robots.allows", return_value=True), mock.patch.object(ss, "_http_request", return_value=_Resp(403)):
        assert [s["body"] for s in ss.scan_producthunt(_ph_config())] == ["http_403"]


# --- domains ------------------------------------------------------------------------------------------------------

def test_an_unknown_domain_is_an_error_that_names_the_known_ones():
    cfg = {"domain": {"name": "file synchronization"},
           "data_pipeline": {"name": "data pipeline", "g2_category_url": "https://g2.example/etl"},
           "sources": {"x": 1}}
    with mock.patch.object(cd, "_load_config", return_value=cfg), mock.patch.object(cd, "_audit"):
        got = cd.run_discovery(domain="data pipeline / data integration")
    assert got == {"error": "Unknown domain 'data pipeline / data integration'. Use a config key or name: data_pipeline"}


def test_the_shipped_config_has_a_data_pipeline_domain_with_its_own_sources():
    cfg = ss._load_config()
    assert cfg["data_pipeline"]["name"] == "data pipeline" and cfg["data_pipeline"]["keywords"]
    assert ss._by_domain({**cfg, "_active_domain": "data_pipeline"}, cfg["sources"]["producthunt"]["topics_by_domain"], [])[0] == "data-engineering"


def test_the_review_site_discoverer_honours_robots():
    with mock.patch("tools.http.robots.allows", return_value=False), mock.patch.object(cd, "_http_request") as http:
        assert cd._safe_get("https://www.g2.com/categories/x", as_text=True) == (None, "robots_disallowed")
    http.assert_not_called()


def test_the_github_token_falls_back_to_the_gh_cli(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(github_token.shutil, "which", lambda n: "/usr/bin/gh")
    monkeypatch.setattr(github_token.subprocess, "run", lambda *a, **k: type("O", (), {"returncode": 0, "stdout": "cli\n"})())
    assert github_token.github_token() == "cli"
    monkeypatch.setenv("GH_TOKEN", "env")
    assert github_token.github_token() == "env"


# --- one gap and one spec per pain point -----------------------------------------------------------------------------

@pytest.fixture
def creative_db(tmp_path, monkeypatch):
    from tools.db.init_icdev_db import SCHEMA_SQL
    db = tmp_path / "creative.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    monkeypatch.delenv("ICDEV_PG_NO_FALLBACK", raising=False)
    conn = sqlite3.connect(str(db))
    for t in ("creative_pain_points", "creative_feature_gaps", "creative_specs", "creative_competitors", "creative_signals"):
        conn.executescript(_table_ddl(SCHEMA_SQL, t))
    # One pain point, re-scored three times: three rows, three ids, one fingerprint (append-only, D6).
    for n, seen in enumerate(("2026-06-14T00:00:00", "2026-06-20T00:00:00", "2026-07-01T00:00:00")):
        conn.execute("""INSERT INTO creative_pain_points (id, title, description, category, frequency, signal_ids, competitor_ids,
                        keyword_fingerprint, keywords, severity, status, composite_score, score_breakdown, first_seen, last_seen)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (f"pp-v{n}", "Schema drift breaks pipelines", "d", "integration", 5, "[]", "[]", "fp-drift", "[]", "high",
                      "scored", 0.9, json.dumps({"dimensions": {"gap_uniqueness": 0.6, "pain_frequency": 1.0}}),
                      "2026-06-14T00:00:00", seen))
    conn.commit()
    conn.close()
    return db


def test_gaps_are_per_pain_point_not_per_version(creative_db):
    from tools.creative import gap_scorer
    from tools.db.storage import get_connection
    first = gap_scorer.identify_feature_gaps(db_path=creative_db)
    assert first["gaps_identified"] == 1
    conn = sqlite3.connect(str(creative_db))
    conn.execute("""INSERT INTO creative_pain_points (id, title, description, category, frequency, signal_ids, competitor_ids,
                    keyword_fingerprint, keywords, severity, status, composite_score, score_breakdown, first_seen, last_seen)
                    SELECT 'pp-v3', title, description, category, frequency, signal_ids, competitor_ids, keyword_fingerprint,
                           keywords, severity, status, composite_score, score_breakdown, first_seen, '2026-11-08T00:00:00'
                    FROM creative_pain_points WHERE id = 'pp-v2'""")
    conn.commit()
    conn.close()
    again = gap_scorer.identify_feature_gaps(db_path=creative_db)
    assert again["gaps_identified"] == 0
    c = get_connection(db_path=str(creative_db))
    assert [dict(r)["n"] for r in c.execute("SELECT COUNT(*) AS n FROM creative_feature_gaps").fetchall()] == [1]


def test_dedupe_rejects_extra_specs_keeping_the_earliest_and_deletes_nothing(creative_db):
    from tools.creative import spec_generator
    conn = sqlite3.connect(str(creative_db))
    for i, (pp, at) in enumerate((("pp-v0", "2026-06-16"), ("pp-v1", "2026-06-26"), ("pp-v2", "2026-11-08"))):
        conn.execute("""INSERT INTO creative_specs (id, feature_gap_id, pain_point_id, title, spec_content, composite_score,
                        justification, estimated_effort, status, metadata, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (f"cspec-{i}", f"fg-{i}", pp, "Schema drift", "spec", 0.9, "why", "M", "generated", "{}", at))
    conn.commit()
    conn.close()
    dry = spec_generator.dedupe_specs(db_path=creative_db)
    assert (dry["pain_points"], dry["specs"], dry["duplicates"], dry["applied"]) == (1, 3, 2, False)
    assert [d["duplicate_of"] for d in dry["duplicates_detail"]] == ["cspec-0", "cspec-0"]
    done = spec_generator.dedupe_specs(apply=True, db_path=creative_db)
    assert done["applied"] is True
    rows = {r[0]: (r[1], json.loads(r[2] or "{}")) for r in sqlite3.connect(str(creative_db)).execute(
        "SELECT id, status, metadata FROM creative_specs ORDER BY id")}
    assert rows["cspec-0"][0] == "generated"
    assert rows["cspec-1"][0] == rows["cspec-2"][0] == "rejected" and rows["cspec-2"][1]["duplicate_of"] == "cspec-0"
    assert spec_generator.dedupe_specs(db_path=creative_db)["duplicates"] == 0


# --- release titles are not gaps ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("name", "tag", "noise"), [
    ("Airbyte 2.0", "v2.0.0", True),
    ("3.9.1.dev7: Nightly Development Release", "3.9.1.dev7", True),
    ("3.9.2 - It's okay to let go of a lock that already let go of you", "3.9.2", True),
    ("Hermes Agent v0.22.0 — The Threshold Release", "v0.22.0", True),
    ("3.2.508", "3.2.508", True),
    ("3.2.0 - Added streaming support", "3.2.0", False),
    ("Dagster 1.9: new asset checks", "1.9.0", False),
    ("Release 4.2 adds OpenLineage support", "4.2", False),
])
def test_release_titles_that_carry_no_feature_are_noise(name, tag, noise):
    from tools.innovation.competitive_intel import _is_version_release_noise
    assert _is_version_release_noise(name, tag) is noise
