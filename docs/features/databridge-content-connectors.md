# DataBridge: the operator's own content (dic-bridge-01)

DataBridge could reach 37 vendor APIs and not one of the places an organisation
actually keeps its policies. This card adds four connectors that close that:
**SharePoint**, **Confluence**, **Jira**, and an **operator-owned SQL
database**.

The motivating question is document currency. A modernization run that wants to
say "this paragraph is stale" needs the *operative* text to compare against, and
the operative text is on a wiki page, in a document library, or in a table
somebody maintains by hand. Until now none of it was reachable.

## What ships

| Connector | Transport | Auth | The table that matters |
|---|---|---|---|
| `sharepoint` | Microsoft Graph | OAuth2 client credentials (app-only) | `file_content` — a file's bytes decoded to text |
| `confluence` | Confluence Cloud REST v2 | HTTP Basic (email + API token) | `pages` / `page` — with `body_storage` and `body_text` |
| `jira` | Jira Cloud REST v3 | HTTP Basic (email + API token) | `changelog` — one row per field change, with who and when |
| `local_database` | psycopg2 / sqlite3 | a DSN **reference** | whatever the operator declared, and nothing else |

All four are **read-only**. None constructs a write statement or a write
request anywhere.

`tools/databridge/connectors/atlassian_base.py` holds the half Confluence and
Jira genuinely share — the Basic-auth pair and the refusal to connect without a
resolvable token. Each product keeps only its own base URL, its own tables, and
its own way of turning a page of its API into rows.

## The three decisions worth reviewing

### 1. `None` is not `""`, and neither is an error

A Confluence body the caller did not request comes back `None`, not `""`. A
`.docx` in SharePoint comes back `text: None` with `text_status:
"needs_extractor"`, not as bytes decoded with `errors="replace"`. A Jira issue
with no description reads `None`.

The reason is the same in all three: a redraft built on `""` when the truth was
"nobody fetched it" is a redraft built on nothing, and it looks exactly like a
redraft built on a genuinely empty page. A `.docx` decoded as UTF-8 is worse
still — it produces a long string of real-looking noise that a citation can be
attached to. `needs_extractor` sends the caller to document intelligence's
ingest path instead, which does have an extractor.

### 2. The local database has no SQL surface, and that is not an oversight

There is no `query` table and no `sql` filter. The connector emits exactly one
statement shape:

```sql
SELECT <declared columns> FROM <declared table> [WHERE col = ?] ORDER BY <col> DESC LIMIT n
```

Three reasons, in order:

1. A connector is reachable **by agents** through the feeds surface. "The agent
   may read `policy_register`" is a grant a human can review. "The agent may run
   SQL" is not a grant, it is a shell.
2. Read-only is enforced three ways, not one: the sqlite handle is opened
   `mode=ro` (a test proves a `DELETE` through it raises), the Postgres session
   is `readonly=True`, and `write()` refuses with a reason.
3. A `SELECT` with a caller's `WHERE` clause is the same hole one indirection
   further away.

**No allowlist means no tables.** An empty or absent `tables:` is not "expose
everything" — `connect()` returns False and says why. `list_tables()` reports
the *declaration*, never the database's catalogue, because enumerating what
exists tells an agent about tables it may not read, which is the first half of
reading them.

A filter naming an unexposed column is refused **by name**. Dropping it silently
would widen the result set while the response still read `ok` — more rows than
the caller asked for, reported as a success.

Table and column identifiers cannot be bound as parameters in any driver, so
they are interpolated — but only after passing both a strict identifier pattern
and membership in the operator's own allowlist. That is a real control, which is
why the `# nosec B608` beside the statement names it. A suppression asserting a
control that does not exist is worse than no suppression.

### 3. Nothing is granted to an agent

All four connection records ship `status: disabled` with empty sites and empty
credentials. **None of the four is granted in
`args/databridge_agent_access.yaml`** — every one carries a credential, and that
file's standing rule is that a credentialed system is a per-deployment decision,
never a shipped grant. `tests/test_databridge_first_grant.py` fails the build on
any shipped grant whose descriptor carries an `auth_secret_ref`, so uncommenting
a template without reviewing it cannot happen quietly.

The commented grant templates in that file name the two things that need a real
decision: `tables` (an allowlist, not a hint — and if it is broader than
intended, narrow the *credential*, because the list cannot express "only the Ops
space") and `agents` (an empty list grants every agent, including
runtime-generated SMEs).

## Configuring one

Fill in the connection record in `args/databridge_connections.yaml`, set the
environment variable its `auth_secret_ref` names, and flip `status` to
`configured`. Secrets are always references (`env:` / `vault:` / `aws:` /
`file:`); the seeder refuses a literal, and `local_database` refuses a literal
DSN even when passed programmatically, so the YAML rule and the code rule are
the same rule.

Each connector has a CLI for checking the configuration before granting
anything. They are invoked as MODULES: running a file under `tools/` directly
puts that file's own directory on `sys.path[0]` and never the repository root,
which is why the other connectors here carry a path bootstrap. These four do
not need one, so they do not have one.

```powershell
python -m tools.databridge.connectors.confluence_connector --health
python -m tools.databridge.connectors.jira_connector --read issues --jql "project = ENG"
python -m tools.databridge.connectors.sharepoint_connector --read sites --search policy
python -m tools.databridge.connectors.local_database_connector --sqlite ./data/policy.db --table policy_register --health
```

## What this card does NOT do

It does not wire these connectors into document modernization. There is no seam
today between DataBridge and the redraft evidence path — `grep databridge
tools/document_intelligence/` returns nothing — so a redraft still cites only
what the document store holds. Building that seam means deciding how an external
page becomes citable evidence, which is a question about provenance rather than
about connectors, and it is the next card rather than a line in this one.

## Tests

`tests/databridge/test_content_connectors.py`, 66 tests, no network. What is
tested is not "does the API call work" but the decisions each connector makes on
its own: what it refuses, and where it says `None` instead of a value it does not
have. Two controls were mutation-checked — removing `mode=ro` and replacing the
unexposed-filter refusal with a silent skip each turn a test red.
