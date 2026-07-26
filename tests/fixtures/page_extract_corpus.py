# CUI // SP-CTI
"""Fixed 20-page corpus for `tools/http/page_extract` regression tests.

Each entry is a realistic page shape: the same boilerplate chrome every real
site carries (nav, cookie banner, sidebar, related links, comments, footer)
wrapped around a distinct content body.  The corpus is checked in and static,
so the token-reduction and determinism assertions are reproducible.

`ANSWER` marks the sentence a caller asking `QUERY` actually wants — several
pages deliberately bury it below the 7000-character positional cut that
`tools/chat_router/url_analyzer.py` applies today.
"""

from __future__ import annotations

_NAV = (
    '<nav class="site-nav"><ul>'
    '<li><a href="/">Home</a></li><li><a href="/docs">Docs</a></li>'
    '<li><a href="/blog">Blog</a></li><li><a href="/pricing">Pricing</a></li>'
    '<li><a href="/careers">Careers</a></li><li><a href="/contact">Contact</a></li>'
    "</ul></nav>"
)
_COOKIE = (
    '<div id="cookie-banner" class="cookie-consent"><p>We use cookies and similar '
    "technologies to personalise content and analyse traffic across this website."
    '</p><button>Accept all</button></div>'
)
_SIDEBAR = (
    '<aside class="sidebar widget-area"><h3>Related</h3><ul>'
    '<li><a href="/x1">Ten things you missed</a></li>'
    '<li><a href="/x2">Sponsored: buy our thing</a></li>'
    '<li><a href="/x3">Newsletter signup</a></li></ul></aside>'
)
_COMMENTS = (
    '<section class="comments" id="disqus_thread"><h3>Comments</h3>'
    "<p>First! Great article, thanks for sharing this with the community.</p>"
    "<p>I disagree with paragraph three but otherwise a solid write-up here.</p>"
    "</section>"
)
_FOOTER = (
    '<footer class="site-footer"><p>Copyright 2026 Example Corp. All rights '
    "reserved. Terms of service, privacy policy, and acceptable use policy apply."
    '</p><ul><li><a href="/tos">Terms</a></li><li><a href="/privacy">Privacy</a>'
    '</li><li><a href="/legal">Legal</a></li></ul></footer>'
)
_SCRIPTS = (
    "<script>window.dataLayer=[];function track(e){dataLayer.push(e);}</script>"
    "<style>.site-nav{display:flex}.sidebar{float:right}</style>"
)

_FILLER = (
    "The platform records every configuration change in an append-only ledger so "
    "operators can reconstruct the state of the system at any point in time. "
    "Retention windows are configured per environment and enforced by the "
    "collector rather than the query layer. "
)


def _filler(paragraphs: int, repeats: int = 4) -> str:
    """Deterministic long-form body text used to push answers past the cut."""
    return "".join(f"<p>{_FILLER * repeats}</p>" for _ in range(paragraphs))


def _page(title: str, body: str, *, long_form: bool = False) -> str:
    padding = _filler(12) if long_form else ""
    return (
        f"<!doctype html><html><head><title>{title}</title>{_SCRIPTS}</head><body>"
        f"{_NAV}{_COOKIE}"
        f'<div class="layout"><main><article class="post entry-content">'
        f"<h1>{title}</h1>{padding}{body}</article>{_COMMENTS}</main>{_SIDEBAR}</div>"
        f"{_FOOTER}</body></html>"
    )


# (slug, query, answer-substring, html)
CORPUS: list[tuple[str, str, str, str]] = [
    (
        "key-rotation",
        "rotate the signing key",
        "run icdev keys rotate --signing",
        _page(
            "Key Rotation Guide",
            "<h2>Rotating the signing key</h2>"
            "<p>To rotate the signing key, run icdev keys rotate --signing and then "
            "restart every worker so the verifier pool reloads the new material.</p>"
            "<ul><li>Generate a fresh keypair</li>"
            '<li>Publish the public half to the <a href="https://jwks.example.gov/keys">'
            "JWKS endpoint</a></li>"
            "<li>Retire the previous key once the grace period elapses</li></ul>",
            long_form=True,
        ),
    ),
    (
        "backup-restore",
        "restore a backup from cold storage",
        "restore from cold storage with icdev backup restore",
        _page(
            "Backup and Restore",
            "<h2>Restoring from cold storage</h2>"
            "<p>You restore from cold storage with icdev backup restore --snapshot "
            "&lt;id&gt;, which rehydrates the archive before replaying the write "
            "ahead log up to the requested point in time.</p>",
            long_form=True,
        ),
    ),
    (
        "rate-limits",
        "what are the api rate limits",
        "600 requests per minute per tenant",
        _page(
            "API Reference",
            "<h2>Rate limits</h2>"
            "<p>Every tenant is allowed 600 requests per minute per tenant across all "
            "endpoints; bursts above that receive a 429 with a Retry-After header.</p>"
            "<table><tr><th>Tier</th><th>Requests/min</th><th>Burst</th></tr>"
            "<tr><td>Free</td><td>60</td><td>120</td></tr>"
            "<tr><td>Team</td><td>600</td><td>1200</td></tr>"
            "<tr><td>Enterprise</td><td>6000</td><td>12000</td></tr></table>",
        ),
    ),
    (
        "webhook-retries",
        "how many times are webhooks retried",
        "retried eight times with exponential backoff",
        _page(
            "Webhooks",
            "<h2>Delivery guarantees</h2>"
            "<p>A failed webhook delivery is retried eight times with exponential "
            "backoff starting at thirty seconds and capping at six hours.</p>",
            long_form=True,
        ),
    ),
    (
        "sso-setup",
        "configure saml single sign on",
        "upload the IdP metadata XML",
        _page(
            "Single Sign-On",
            "<h2>SAML configuration</h2>"
            "<p>To configure SAML single sign on, upload the IdP metadata XML in the "
            "tenant admin console and map the NameID claim to the email attribute.</p>"
            "<ol><li>Register the service provider</li><li>Upload metadata</li>"
            "<li>Assign the default role</li></ol>",
        ),
    ),
    (
        "cli-install",
        "install the cli on windows",
        "pipx install icdev",
        _page(
            "Installation",
            "<h2>Windows</h2><p>On Windows the supported install path is pipx install "
            "icdev, which isolates the tool from the system interpreter.</p>"
            "<pre><code>pipx install icdev\nicdev --version</code></pre>",
        ),
    ),
    (
        "audit-log-schema",
        "what columns does the audit log have",
        "actor, action, resource, classification, and occurred_at",
        _page(
            "Audit Log",
            "<h2>Schema</h2><p>Each audit row records actor, action, resource, "
            "classification, and occurred_at; the table is append-only and never "
            "updated in place.</p>",
            long_form=True,
        ),
    ),
    (
        "data-residency",
        "where is customer data stored",
        "replicated only within the selected region",
        _page(
            "Data Residency",
            "<h2>Regions</h2><p>Customer data is replicated only within the selected "
            "region and never leaves the boundary for backups or analytics.</p>",
        ),
    ),
    (
        "changelog",
        "when was mutual tls added",
        "mutual TLS between agents landed in 4.2",
        _page(
            "Changelog",
            "<h2>4.2</h2><p>Support for mutual TLS between agents landed in 4.2 along "
            "with certificate rotation hooks.</p>"
            "<h2>4.1</h2><p>Added the OSCAL exporter and fixed a POA&amp;M numbering "
            "bug that duplicated identifiers across catalogues.</p>",
            long_form=True,
        ),
    ),
    (
        "error-codes",
        "what does error E409 mean",
        "E409 means the resource version you supplied is stale",
        _page(
            "Error Codes",
            "<h2>Conflict errors</h2><p>E409 means the resource version you supplied "
            "is stale; refetch the object and reapply your change.</p>"
            "<table><tr><th>Code</th><th>Meaning</th></tr>"
            "<tr><td>E400</td><td>Malformed request body</td></tr>"
            "<tr><td>E409</td><td>Version conflict</td></tr></table>",
        ),
    ),
    (
        "pricing-model",
        "how is usage metered",
        "metered per thousand processed tokens",
        _page(
            "Pricing",
            "<h2>Metering</h2><p>Usage is metered per thousand processed tokens and "
            "aggregated hourly for each tenant.</p>",
            long_form=True,
        ),
    ),
    (
        "airgap-deploy",
        "deploy in an air gapped network",
        "mirror the registry then run icdev deploy --offline",
        _page(
            "Air-Gap Deployment",
            "<h2>Offline install</h2><p>Mirror the registry then run icdev deploy "
            "--offline; no outbound DNS or HTTPS is attempted in this mode.</p>",
        ),
    ),
    (
        "rbac-roles",
        "which role can approve a release",
        "only the release-approver role may approve",
        _page(
            "Roles and Permissions",
            "<h2>Approvals</h2><p>Only the release-approver role may approve a "
            "promotion to production; auditors have read-only visibility.</p>"
            "<ul><li>viewer</li><li>builder</li><li>release-approver</li>"
            "<li>auditor</li></ul>",
            long_form=True,
        ),
    ),
    (
        "log-retention",
        "how long are logs kept",
        "retained for four hundred days",
        _page(
            "Log Retention",
            "<h2>Defaults</h2><p>Security-relevant logs are retained for four hundred "
            "days; application debug logs roll off after fourteen.</p>",
        ),
    ),
    (
        "migration-guide",
        "migrate from sqlite to postgresql",
        "set ICDEV_STORAGE_BACKEND=postgresql",
        _page(
            "Migration Guide",
            "<h2>Switching backends</h2><p>To migrate, set "
            "ICDEV_STORAGE_BACKEND=postgresql, run the migration runner, then verify "
            "row counts against the source database.</p>",
            long_form=True,
        ),
    ),
    (
        "faq",
        "does it work offline",
        "the entire toolchain runs offline",
        _page(
            "Frequently Asked Questions",
            "<h2>Connectivity</h2><p>Yes — the entire toolchain runs offline once the "
            "model weights and package mirror are staged locally.</p>"
            "<h2>Licensing</h2><p>Licences are per named operator and transferable "
            "once per quarter.</p>",
        ),
    ),
    (
        "security-contact",
        "how do I report a vulnerability",
        "email security@example.gov with a proof of concept",
        _page(
            "Security",
            "<h2>Reporting</h2><p>Email security@example.gov with a proof of concept "
            "and we acknowledge within one business day.</p>"
            '<p>Our advisories are published at '
            '<a href="https://example.gov/advisories">the advisory index</a>.</p>',
            long_form=True,
        ),
    ),
    (
        "perf-tuning",
        "reduce p99 latency",
        "raise the connection pool ceiling",
        _page(
            "Performance Tuning",
            "<h2>Latency</h2><p>The single highest-leverage change is to raise the "
            "connection pool ceiling so requests stop queueing behind checkout.</p>"
            "<blockquote>Measure before tuning; most p99 spikes are pool "
            "starvation.</blockquote>",
        ),
    ),
    (
        "sbom-policy",
        "when is the sbom regenerated",
        "regenerated on every build",
        _page(
            "Supply Chain",
            "<h2>SBOM</h2><p>The SBOM is regenerated on every build and attached to "
            "the release artefact before signing.</p>",
            long_form=True,
        ),
    ),
    (
        "support-sla",
        "severity one response time",
        "acknowledged within fifteen minutes",
        _page(
            "Support SLA",
            "<h2>Severity 1</h2><p>A severity one incident is acknowledged within "
            "fifteen minutes, around the clock, every day of the year.</p>"
            "<table><tr><th>Severity</th><th>Ack</th></tr>"
            "<tr><td>Sev1</td><td>15 min</td></tr>"
            "<tr><td>Sev2</td><td>2 hours</td></tr></table>",
        ),
    ),
]

# Text that only ever appears in page chrome — never in extracted content.
CHROME_MARKERS = (
    "We use cookies",
    "Sponsored: buy our thing",
    "Newsletter signup",
    "All rights reserved",
    "Great article, thanks for sharing",
    "dataLayer",
    "display:flex",
)
