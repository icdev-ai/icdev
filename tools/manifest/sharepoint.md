# SharePoint Integration (Phase E / P4.1)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.
> Targets on-prem SharePoint Server 2016/2019/SE via REST `/_api/web/*`.
> SharePoint Online / M365 (needs Microsoft Graph + MSAL) is out of scope.

## SharePoint Integration (Phase E / P4.1)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SharePoint Client | tools/sharepoint/client.py | On-prem SharePoint REST client. Handles NTLM/Kerberos/Basic auth, exponential backoff on 5xx, hard-fail on 401. Methods: list_sites(), get_lists(site_url), get_list_items(site_url, list_id), get_file(site_url, server_rel_url), search(query). | endpoint, auth_mode, username, password, timeout, verify | list[dict] / bytes |
| SharePoint Ingest | tools/sharepoint/ingest.py | Walk all sites in site_scope (args/sharepoint.yaml), enumerate lists+items+documents, upsert to sharepoint_* tables via get_connection(). Emits integration_sync_pull audit event. Public API: ingest_all(config, site_override, dry_run). | --site URL, --dry-run, --json, --config PATH | JSON: {status, sites_processed, total_lists, total_items, total_documents, total_errors, dry_run} |
| SharePoint Selectors | tools/sharepoint/selectors.py | Centralized CSS/XPath DOM selector constants for classic SharePoint Server 2016/2019/SE UI. Used by browser_fallback.py (Phase F/P4.2). Covers: auth forms, list view table, document library rows/links, pagination, toolbar, nav, search, error labels, item forms. No logic — pure string constants. | import | module-level string constants |
| Browser Fallback | tools/sharepoint/browser_fallback.py | Phase F / P4.2. Selenium fallback for classic SharePoint pages REST can't render. Gated by args/sharepoint.yaml sharepoint.fallback_enabled (default false). Raises FallbackDisabledError when gate is off. Public API: fetch_classic_page(url, selectors) — selectors maps field_name→css_selector, returns list[dict[str,str]] of extracted rows. Uses tools.browser.driver_manager.get_driver(). | url: str, selectors: dict[str,str] | list[dict[str,str]] |
