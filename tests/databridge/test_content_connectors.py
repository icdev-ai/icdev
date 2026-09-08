# CUI // SP-CTI
"""The four connectors that reach the operator's own content.

SharePoint, Confluence, Jira and an operator-owned SQL database. What is tested
here is not "does the API call work" -- no test touches a network -- but the
decisions each connector makes on its own: what it refuses, and where it says
`None` instead of a value it does not have.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tools.databridge.connector import ConnectorRequest, ConnectorType
from tools.databridge.connectors.confluence_connector import (
    ConfluenceConnector,
    storage_to_text,
)
from tools.databridge.connectors.jira_connector import JiraConnector, adf_to_text
from tools.databridge.connectors.local_database_connector import (
    LocalDatabaseConnector,
    TableNotDeclared,
    valid_identifier,
)
from tools.databridge.connectors.sharepoint_connector import (
    SharePointConnector,
    is_decodable,
)
from tools.databridge.registry import _REGISTRY


# ── registration ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name", ["confluence", "jira", "sharepoint", "local_database"]
)
def test_the_connector_is_registered_by_importing_its_module(name):
    """Registration is an import side effect; these four must not be the ones
    that quietly fail to register and answer 'not registered' at use time."""
    assert name in _REGISTRY


# ══════════════════════════════════════════════════════════════════════════
# Confluence
# ══════════════════════════════════════════════════════════════════════════

class TestConfluenceReadsBodies:

    def test_storage_markup_becomes_readable_text(self):
        out = storage_to_text(
            "<h1>Policy</h1><p>Rotate keys &amp; log it.</p>"
            "<ul><li>Annually</li><li>On compromise</li></ul>"
        )
        assert "Rotate keys & log it." in out
        assert "Annually" in out and "On compromise" in out
        assert "<" not in out and "&amp;" not in out

    def test_a_macro_loses_its_tags_not_its_prose(self):
        out = storage_to_text(
            '<ac:structured-macro ac:name="info"><ac:rich-text-body>'
            "<p>Superseded in 2026.</p></ac:rich-text-body></ac:structured-macro>"
        )
        assert out == "Superseded in 2026."

    def test_a_body_nobody_asked_for_is_None_not_empty_string(self):
        """`None` means 'not fetched'; '' would mean 'the page is blank'. A
        caller deciding whether it holds the current text of a document needs
        those apart."""
        c = ConfluenceConnector()
        c._base_url = "https://site.atlassian.net"
        page = {"id": "1", "title": "P", "spaceId": "9",
                "body": {"storage": {"value": "<p>text</p>"}}}
        assert c._page_row(page, with_body=False)["body_storage"] is None
        assert c._page_row(page, with_body=False)["body_text"] is None
        assert c._page_row(page, with_body=True)["body_text"] == "text"

    def test_an_unknown_table_is_refused_and_names_what_exists(self):
        c = ConfluenceConnector()
        r = c.read(ConnectorRequest(table_name="blogposts"))
        assert r.status == "error"
        assert "blogposts" in r.errors[0] and "spaces" in r.errors[0]

    def test_a_page_read_without_an_id_is_refused_before_any_request(self):
        c = ConfluenceConnector()
        c._base_url = "https://site.atlassian.net"

        def boom(url):
            raise AssertionError("no request should be made")

        c._http_get = boom                       # type: ignore[method-assign]
        r = c.read(ConnectorRequest(table_name="page"))
        assert r.status == "error" and "'id'" in r.errors[0]

    def test_a_page_read_hits_the_body_format_endpoint(self, monkeypatch):
        c = ConfluenceConnector()
        c._base_url = "https://site.atlassian.net"
        seen = {}

        def fake_get(url):
            seen["url"] = url
            return {"id": "77", "title": "Key Rotation", "spaceId": "3",
                    "status": "current", "version": {"number": 4},
                    "body": {"storage": {"value": "<p>Rotate quarterly.</p>"}},
                    "_links": {"webui": "/spaces/OPS/pages/77"}}

        c._http_get = fake_get                   # type: ignore[method-assign]
        r = c.read(ConnectorRequest(table_name="page", filters={"id": "77"}))
        assert r.status == "ok" and r.row_count == 1
        assert "body-format=storage" in seen["url"]
        row = r.data[0]
        assert row["title"] == "Key Rotation"
        assert row["body_text"] == "Rotate quarterly."
        assert row["url"].endswith("/wiki/spaces/OPS/pages/77")

    def test_search_needs_a_cql_and_says_so(self):
        c = ConfluenceConnector()
        r = c.read(ConnectorRequest(table_name="search"))
        assert r.status == "error" and "cql" in r.errors[0]


class TestAtlassianRefusesToConnectAnonymously:

    def test_no_token_is_a_refusal_not_an_anonymous_session(self, monkeypatch):
        """An anonymous read of a Confluence space is not a smaller version of
        an authenticated one; it is a different, usually empty answer wearing
        the same shape."""
        c = ConfluenceConnector()
        assert c.connect({
            "base_url": "https://site.atlassian.net",
            "email": "ops@example.com",
            # no auth_secret_ref at all
        }) is False
        assert c._connected is False

    def test_a_site_url_is_required(self):
        c = JiraConnector()
        assert c.connect({"email": "ops@example.com"}) is False

    def test_the_email_is_required_because_basic_auth_is_a_pair(self):
        c = JiraConnector()
        assert c.connect({"base_url": "https://site.atlassian.net"}) is False

    def test_the_token_is_read_through_the_secret_resolver(self, monkeypatch):
        import tools.databridge.connection_manager as cm

        monkeypatch.setattr(cm, "resolve_secret", lambda ref: "s3cret-token")
        c = JiraConnector()
        headers = c._build_auth_headers({
            "email": "ops@example.com", "auth_secret_ref": "env:JIRA_API_TOKEN",
        })
        assert headers["Authorization"].startswith("Basic ")
        # the credential is encoded, and the REFERENCE never becomes the value
        import base64
        decoded = base64.b64decode(headers["Authorization"][6:]).decode()
        assert decoded == "ops@example.com:s3cret-token"


# ══════════════════════════════════════════════════════════════════════════
# Jira
# ══════════════════════════════════════════════════════════════════════════

class TestJiraFlattensTheDocumentFormat:

    def test_paragraphs_become_lines(self):
        doc = {"type": "doc", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "First."}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Second."}]},
        ]}
        assert adf_to_text(doc).strip() == "First.\nSecond."

    def test_an_unknown_node_loses_its_formatting_never_its_words(self):
        """A node type this walker does not know still contributes its
        children. Losing formatting is survivable for a citation; losing the
        sentence is not."""
        doc = {"type": "doc", "content": [
            {"type": "someFutureNode", "content": [
                {"type": "text", "text": "still here"},
            ]},
        ]}
        assert "still here" in adf_to_text(doc)

    def test_wiki_markup_served_as_a_plain_string_survives(self):
        assert adf_to_text("h1. Old markup") == "h1. Old markup"

    def test_an_issue_with_no_description_reads_None(self):
        c = JiraConnector()
        c._base_url = "https://site.atlassian.net"
        row = c._issue_row({"id": "1", "key": "ENG-1", "fields": {
            "summary": "Rotate keys", "description": None,
            "status": {"name": "Done"}, "issuetype": {"name": "Task"},
        }})
        assert row["description"] is None
        assert row["status"] == "Done"
        assert row["url"] == "https://site.atlassian.net/browse/ENG-1"

    def test_the_changelog_flattens_to_one_row_per_field_change(self):
        c = JiraConnector()
        c._base_url = "https://site.atlassian.net"
        rows = c._changelog_rows("ENG-1", {
            "created": "2026-09-01T10:00:00.000+0000",
            "author": {"displayName": "Dana"},
            "items": [
                {"field": "status", "fromString": "To Do", "toString": "Done"},
                {"field": "priority", "fromString": "Low", "toString": "High"},
            ],
        })
        assert len(rows) == 2
        assert rows[0]["field"] == "status" and rows[0]["to_value"] == "Done"
        assert all(r["author"] == "Dana" for r in rows)

    def test_a_table_needing_an_issue_key_is_refused_by_name(self):
        c = JiraConnector()
        for table in ("issue", "comments", "changelog"):
            r = c.read(ConnectorRequest(table_name=table))
            assert r.status == "error", table
            assert "key" in r.errors[0], table

    def test_issues_needs_a_jql(self):
        c = JiraConnector()
        r = c.read(ConnectorRequest(table_name="issues"))
        assert r.status == "error" and "jql" in r.errors[0]

    def test_a_jql_search_binds_the_query_into_the_posted_body(self):
        """JQL goes in a POST body, not a URL the caller helped build."""
        c = JiraConnector()
        c._base_url = "https://site.atlassian.net"
        seen = {}

        def fake_post(url, body):
            seen.update({"url": url, "body": body})
            return {"issues": [{"id": "9", "key": "ENG-9", "fields":
                                {"summary": "s", "description": None}}]}

        c._http_post = fake_post                 # type: ignore[method-assign]
        r = c.read(ConnectorRequest(
            table_name="issues", filters={"jql": "project = ENG"}, limit=5))
        assert r.status == "ok" and r.row_count == 1
        assert seen["body"]["jql"] == "project = ENG"
        assert seen["body"]["maxResults"] == 5
        assert "summary" in seen["body"]["fields"]


# ══════════════════════════════════════════════════════════════════════════
# SharePoint
# ══════════════════════════════════════════════════════════════════════════

class TestSharePointKnowsWhatItCannotRead:

    @pytest.mark.parametrize("name,ctype", [
        ("policy.md", "text/markdown"),
        ("data.json", "application/json"),
        ("notes.txt", ""),
        ("feed.xml", "application/atom+xml"),
    ])
    def test_text_is_decodable(self, name, ctype):
        assert is_decodable(name, ctype) is True

    @pytest.mark.parametrize("name,ctype", [
        ("policy.docx", "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document"),
        ("report.pdf", "application/pdf"),
        ("logo.png", "image/png"),
    ])
    def test_binary_is_not(self, name, ctype):
        assert is_decodable(name, ctype) is False

    def test_a_docx_reports_needs_extractor_and_never_fabricates_text(self):
        """The alternative -- decoding a .docx with errors='replace' -- returns
        a string full of real-looking noise that a redraft would cite."""
        c = SharePointConnector()
        c._base_url = "https://graph.microsoft.com/v1.0"
        c._token, c._token_expires_at = "t", 1e12
        c._http_get = lambda url: {                # type: ignore[method-assign]
            "id": "i1", "name": "policy.docx", "size": 40000,
            "file": {"mimeType": "application/vnd.openxmlformats-officedocument"
                                 ".wordprocessingml.document"},
        }
        row = c._file_content_row("d1", "i1")
        assert row["text"] is None
        assert row["text_status"] == "needs_extractor"
        assert row["text_readable"] is False

    def test_a_folder_says_so_rather_than_returning_empty_text(self):
        c = SharePointConnector()
        c._base_url = "https://graph.microsoft.com/v1.0"
        c._token, c._token_expires_at = "t", 1e12
        c._http_get = lambda url: {                # type: ignore[method-assign]
            "id": "f1", "name": "Policies", "folder": {"childCount": 3},
        }
        row = c._file_content_row("d1", "f1")
        assert row["text"] is None and row["text_status"] == "is_a_folder"

    def test_tenant_and_client_are_required_before_any_token_fetch(self):
        c = SharePointConnector()

        def boom():
            raise AssertionError("no token fetch should be attempted")

        c._fetch_token = boom                      # type: ignore[method-assign]
        assert c.connect({"client_id": "abc"}) is False
        assert c.connect({"tenant_id": "t"}) is False

    def test_a_cached_token_is_reused_and_a_stale_one_is_refreshed(self):
        c = SharePointConnector()
        calls = []

        def fake_fetch():
            calls.append(1)
            c._token = f"token-{len(calls)}"
            c._token_expires_at = 1e12
            return c._token

        c._fetch_token = fake_fetch                # type: ignore[method-assign]
        assert c._ensure_token() == "token-1"
        assert c._ensure_token() == "token-1", "a live token is not re-fetched"
        assert len(calls) == 1

        c._token_expires_at = 0.0                  # expired
        assert c._ensure_token() == "token-2"
        assert len(calls) == 2

    def test_the_token_never_appears_in_the_health_report(self):
        c = SharePointConnector()
        c._base_url = "https://graph.microsoft.com/v1.0"
        c._config = {"tenant_id": "tenant-1"}
        c._token, c._token_expires_at = "super-secret-token", 1e12
        c._http_get = lambda url: {"value": [{"id": "s1"}]}  # type: ignore[method-assign]
        report = c.health_check()
        assert report["status"] == "healthy"
        assert "super-secret-token" not in str(report)

    def test_disconnect_forgets_the_token(self):
        c = SharePointConnector()
        c._token, c._token_expires_at = "tok", 1e12
        c.disconnect()
        assert c._token == "" and c._token_expires_at == 0.0

    def test_a_table_needing_a_drive_is_refused_by_name(self):
        c = SharePointConnector()
        r = c.read(ConnectorRequest(table_name="items"))
        assert r.status == "error" and "drive_id" in r.errors[0]


# ══════════════════════════════════════════════════════════════════════════
# The operator's own database
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def policy_db(tmp_path: Path) -> Path:
    path = tmp_path / "policy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE policy_register (policy_id TEXT, title TEXT, body TEXT, "
        "effective_date TEXT, owner TEXT, secret_note TEXT)"
    )
    conn.executemany(
        "INSERT INTO policy_register VALUES (?,?,?,?,?,?)",
        [
            ("POL-1", "Key rotation", "Rotate quarterly.", "2026-01-01",
             "Dana", "do not expose"),
            ("POL-2", "Retention", "Keep for 7 years.", "2026-06-01",
             "Kim", "do not expose"),
        ],
    )
    conn.execute("CREATE TABLE hr_salaries (person TEXT, amount INTEGER)")
    conn.execute("INSERT INTO hr_salaries VALUES ('Dana', 1)")
    conn.commit()
    conn.close()
    return path


def _connect(path: Path, tables):
    c = LocalDatabaseConnector()
    assert c.connect({
        "driver": "sqlite", "database_path": str(path), "tables": tables,
    })
    return c


class TestTheAllowlistIsTheWholeAccessControl:

    def test_no_declared_tables_means_no_connection_at_all(self, policy_db):
        """An empty allowlist is not 'expose everything'. Reporting connected
        would turn every later read into an empty result instead of the
        configuration error it is."""
        c = LocalDatabaseConnector()
        assert c.connect({"driver": "sqlite", "database_path": str(policy_db),
                          "tables": []}) is False
        assert c.list_tables() == []

    def test_a_table_that_exists_but_was_not_declared_is_refused(self, policy_db):
        c = _connect(policy_db, ["policy_register"])
        r = c.read(ConnectorRequest(table_name="hr_salaries"))
        assert r.status == "error"
        assert "not declared" in r.errors[0]
        assert "policy_register" in r.errors[0]
        c.disconnect()

    def test_list_tables_reports_the_declaration_never_the_catalogue(
        self, policy_db
    ):
        """Enumerating what exists tells an agent about tables it may not read,
        which is the first half of reading them."""
        c = _connect(policy_db, ["policy_register"])
        assert c.list_tables() == ["policy_register"]
        c.disconnect()

    def test_an_undeclared_column_is_never_returned(self, policy_db):
        c = _connect(policy_db, [{"name": "policy_register",
                                  "columns": ["policy_id", "title", "body"]}])
        r = c.read(ConnectorRequest(table_name="policy_register"))
        assert r.status == "ok" and r.row_count == 2
        assert set(r.data[0]) == {"policy_id", "title", "body"}
        assert "secret_note" not in str(r.data)
        c.disconnect()

    def test_a_filter_on_an_unexposed_column_is_refused_BY_NAME(self, policy_db):
        """Dropping it silently would widen the result set while the response
        still read 'ok' -- more rows than the caller asked for, reported as a
        success."""
        c = _connect(policy_db, [{"name": "policy_register",
                                  "columns": ["policy_id", "title"]}])
        r = c.read(ConnectorRequest(table_name="policy_register",
                                    filters={"secret_note": "x"}))
        assert r.status == "error"
        assert "secret_note" in r.errors[0] and "not exposed" in r.errors[0]
        c.disconnect()


class TestNoCallerTextEverBecomesSQL:

    @pytest.mark.parametrize("bad", [
        "policy_register; DROP TABLE policy_register",
        "policy_register--",
        "policy register",
        '"policy_register"',
        "policy_register/*x*/",
        "",
        "1register",
    ])
    def test_an_identifier_that_is_not_plain_is_rejected(self, bad):
        assert valid_identifier(bad) is False

    def test_a_declaration_that_is_not_an_identifier_is_not_exposed(self):
        allowed = LocalDatabaseConnector.parse_tables(
            ["good_table", "bad; DROP TABLE x", "also_good"]
        )
        assert sorted(allowed) == ["also_good", "good_table"]

    def test_a_schema_qualified_name_is_allowed_but_nothing_more(self):
        assert valid_identifier("app.policies", qualified=True) is True
        assert valid_identifier("app.policies", qualified=False) is False
        assert valid_identifier("a.b.c", qualified=True) is False

    def test_filter_values_are_bound_never_interpolated(self, policy_db):
        c = _connect(policy_db, [{"name": "policy_register",
                                  "columns": ["policy_id", "title"]}])
        sql, params = c.build_select(
            "policy_register", {"policy_id": "POL-1' OR '1'='1"}, 10
        )
        assert "OR" not in sql
        assert sql == ("SELECT policy_id, title FROM policy_register "
                       "WHERE policy_id = ? LIMIT 10")
        assert params == ["POL-1' OR '1'='1"]

        # and end to end: the injection matches nothing, it does not match all
        r = c.read(ConnectorRequest(table_name="policy_register",
                                    filters={"policy_id": "POL-1' OR '1'='1"}))
        assert r.status == "ok" and r.row_count == 0
        c.disconnect()

    def test_an_undeclared_table_raises_rather_than_building_a_statement(
        self, policy_db
    ):
        c = _connect(policy_db, ["policy_register"])
        with pytest.raises(TableNotDeclared):
            c.build_select("hr_salaries", {}, 10)
        c.disconnect()

    def test_the_limit_is_bounded_at_both_ends(self, policy_db):
        c = _connect(policy_db, ["policy_register"])
        assert c.build_select("policy_register", {}, 10_000)[0].endswith(
            "LIMIT 1000")
        assert c.build_select("policy_register", {}, 0)[0].endswith("LIMIT 100")
        assert c.build_select("policy_register", {}, -5)[0].endswith("LIMIT 1")
        c.disconnect()


class TestReadOnlyIsEnforcedNotJustDeclared:

    def test_write_refuses_and_explains(self, policy_db):
        c = _connect(policy_db, ["policy_register"])
        r = c.write(ConnectorRequest(table_name="policy_register"),
                    {"policy_id": "POL-9"})
        assert r.status == "error" and "READ-ONLY" in r.errors[0]
        c.disconnect()

    def test_the_capability_agrees_with_the_behaviour(self, policy_db):
        c = _connect(policy_db, ["policy_register"])
        assert c.capabilities.supports_write is False
        assert c.connector_type is ConnectorType.DATABASE
        c.disconnect()

    def test_the_sqlite_handle_itself_refuses_a_write(self, policy_db):
        """`mode=ro` is the only way sqlite3 opens a file read-only. Without it
        'read-only' would be a promise this module merely makes about itself."""
        c = _connect(policy_db, ["policy_register"])
        with pytest.raises(sqlite3.OperationalError):
            c._conn.execute("DELETE FROM policy_register")
        c.disconnect()

    def test_a_literal_dsn_is_refused_because_it_carries_the_password(self):
        c = LocalDatabaseConnector()
        c._config = {"dsn": "postgresql://user:hunter2@localhost/db"}
        assert c._resolve_dsn() == ""

    def test_an_unsupported_driver_is_refused_by_name(self, policy_db):
        c = LocalDatabaseConnector()
        assert c.connect({"driver": "mysql", "database_path": str(policy_db),
                          "tables": ["policy_register"]}) is False

    def test_a_missing_file_is_a_refusal_not_a_created_database(self, tmp_path):
        c = LocalDatabaseConnector()
        assert c.connect({"driver": "sqlite",
                          "database_path": str(tmp_path / "nope.db"),
                          "tables": ["t"]}) is False


class TestItActuallyReads:

    def test_rows_come_back_as_dicts_with_the_declared_columns(self, policy_db):
        c = _connect(policy_db, [{"name": "policy_register",
                                  "columns": ["policy_id", "title", "body"],
                                  "order_by": "policy_id"}])
        r = c.read(ConnectorRequest(table_name="policy_register", limit=10))
        assert r.status == "ok" and r.row_count == 2
        assert r.data[0]["policy_id"] == "POL-2", "order_by applied, descending"
        assert r.data[0]["body"] == "Keep for 7 years."
        c.disconnect()

    def test_a_bound_filter_selects_one_row(self, policy_db):
        c = _connect(policy_db, [{"name": "policy_register",
                                  "columns": ["policy_id", "title"]}])
        r = c.read(ConnectorRequest(table_name="policy_register",
                                    filters={"policy_id": "POL-1"}))
        assert r.row_count == 1 and r.data[0]["title"] == "Key rotation"
        c.disconnect()

    def test_health_check_reports_the_declared_tables(self, policy_db):
        c = _connect(policy_db, ["policy_register"])
        h = c.health_check()
        assert h["status"] == "healthy" and h["driver"] == "sqlite"
        assert h["tables_declared"] == ["policy_register"]
        c.disconnect()

    def test_schema_is_inferred_from_a_sampled_row(self, policy_db):
        c = _connect(policy_db, [{"name": "policy_register",
                                  "columns": ["policy_id", "title"]}])
        schema = c.infer_schema("policy_register")
        assert schema.metadata["status"] == "sampled"
        assert [f.name for f in schema.fields] == ["policy_id", "title"]
        c.disconnect()

    def test_an_undeclared_table_has_no_schema_to_infer(self, policy_db):
        c = _connect(policy_db, ["policy_register"])
        schema = c.infer_schema("hr_salaries")
        assert schema.metadata["status"] == "not_declared"
        assert schema.fields == []
        c.disconnect()

    def test_a_read_before_connect_is_refused(self):
        c = LocalDatabaseConnector()
        r = c.read(ConnectorRequest(table_name="anything"))
        assert r.status == "error" and "not connected" in r.errors[0]
