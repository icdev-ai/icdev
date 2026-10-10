# DataBridge external rung — 33 connectors, now ONE authorized (cef-fnd-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.databridge.seed_connections --seed --json     # db_connections <- args/databridge_connections.yaml
python -m tools.databridge.seed_connections --dry-run --json  # validate, write nothing
python -m tools.databridge.seed_connections --verify --json   # row present? credential resolves?
python -c "from icdev.tools.databridge import broker; print(broker.list_available('doc_reviewer'))"
```

TWO files, on purpose: args/databridge_agent_access.yaml is the AUTHORIZATION
(connector+table allowlist, per-agent grants, classification ceiling) and
args/databridge_connections.yaml is the ENDPOINT (url, egress allowlist,
auth_secret_ref). Different reviewers, different cadence; merging them would
reopen the security review on every endpoint edit.
NO SECRET VALUE IN EITHER. auth_secret_ref must be an env:/vault:/aws:/file:
REFERENCE and the seeder REFUSES a literal — refused, not warned, because a
warning still lands the secret in git. classification is the LABEL
(UNCLASSIFIED/CUI/...), never the banner 'CUI // SP-CTI': that column feeds the
RLS predicate, and a banner matches no label at any clearance, so the row is
written, retained and invisible.
Three things had to be fixed before ANY grant could work, each silent:
 * connectors register on IMPORT and nothing imported them, so every fetch died
   at "connector 'rss' is not registered" — and `tools.databridge.registry` vs
   `icdev.tools.databridge.registry` are two module objects with two _REGISTRY
   dicts, so the connectors registered into the copy the broker does not read;
 * `connection_id` was decorative — the broker never read the row, so a
   per-connection egress_allowlist was declared and never enforced;
 * rss_connector does not extend saas_base and so fetched with NO egress guard,
   while being the one connector an agent can reach through the broker.
Every call, allowed or denied, writes one databridge_agent_access_log row.
