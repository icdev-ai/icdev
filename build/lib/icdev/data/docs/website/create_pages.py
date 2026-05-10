"""Create WordPress pages for icdev.ai site navigation."""
import os
import sys
import xmlrpc.client

_user = os.getenv("WP_USERNAME", "pulse-bot")
_pass = os.getenv("WP_PASSWORD")
if not _pass:
    sys.exit("Error: Set WP_PASSWORD in .env before running this script.")

wp = xmlrpc.client.ServerProxy('https://icdev.ai/xmlrpc.php')
auth = (1, _user, _pass)

# --- Platform Page ---
platform_html = """<style>
:root{--bg2:#1e293b;--bg3:#334155;--accent:#3b82f6;--green:#10b981;--text:#e2e8f0;--dim:#94a3b8}
.icdev-page *{box-sizing:border-box;margin:0;padding:0}
.icdev-page{font-family:'Inter',system-ui,sans-serif;color:var(--text);max-width:1100px;margin:0 auto;line-height:1.7;padding:0 24px}
.icdev-page h2{font-size:1.8rem;font-weight:700;margin:48px 0 16px}
.icdev-page h3{font-size:1.2rem;font-weight:600;margin:24px 0 8px}
.icdev-page p{color:var(--dim);font-size:1.05rem;margin-bottom:16px}
.icdev-page table{width:100%;border-collapse:collapse;margin:16px 0}
.icdev-page th{text-align:left;padding:10px 14px;color:var(--dim);font-size:.75rem;text-transform:uppercase;border-bottom:2px solid var(--bg3)}
.icdev-page td{padding:10px 14px;border-bottom:1px solid var(--bg3)}
.highlight{color:var(--accent);font-weight:600}
.arch-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:20px 0}
.arch-card{background:var(--bg2);border-radius:10px;padding:20px;border:1px solid var(--bg3)}
.arch-card h3{margin:0 0 8px;color:var(--accent)}
.arch-card p{font-size:.9rem;margin:0}
@media(max-width:768px){.arch-grid{grid-template-columns:1fr}}
</style>
<div class="icdev-page">

<h2>The FORGE Framework</h2>
<p>ICDEV™ is built on a 6-layer agentic architecture called FORGE. The AI orchestrates &mdash; deterministic Python scripts execute. This separation ensures business logic is reproducible, auditable, and never probabilistic.</p>

<table>
<thead><tr><th>Layer</th><th>Purpose</th><th>Example</th></tr></thead>
<tbody>
<tr><td class="highlight">Goals</td><td>Process definitions &mdash; what to achieve</td><td>goals/compliance_workflow.md</td></tr>
<tr><td class="highlight">Orchestration</td><td>AI reads goals, decides tool order, handles errors</td><td>Claude Code / 15 agents</td></tr>
<tr><td class="highlight">Tools</td><td>Deterministic Python scripts, one job each</td><td>ssp_generator.py, stig_checker.py</td></tr>
<tr><td class="highlight">Context</td><td>Static reference material</td><td>Tone rules, writing samples, ICP descriptions</td></tr>
<tr><td class="highlight">Hard Prompts</td><td>Reusable LLM instruction templates</td><td>outline-to-post, rewrite-in-voice</td></tr>
<tr><td class="highlight">Args</td><td>YAML/JSON behavior settings</td><td>security_gates.yaml, scaling_config.yaml</td></tr>
</tbody>
</table>

<h2>Multi-Agent Architecture</h2>
<p>15 specialized agents communicate via A2A protocol (JSON-RPC 2.0 over mutual TLS). Each publishes an Agent Card and operates within its domain authority.</p>

<div class="arch-grid">
<div class="arch-card"><h3>Core Tier</h3><p>Orchestrator (task routing), Architect (system design)</p></div>
<div class="arch-card"><h3>Domain Tier</h3><p>Builder, Compliance, Security, Infrastructure, MBSE, Requirements, Supply Chain, Simulation, DevSecOps, Integration, Gateway</p></div>
<div class="arch-card"><h3>Support Tier</h3><p>Knowledge (self-healing), Monitor (logs, metrics, alerts)</p></div>
</div>

<h2>Multi-Cloud Support</h2>
<p>Deploy to any government cloud &mdash; or run air-gapped on-premises.</p>
<table>
<thead><tr><th>Cloud</th><th>Region</th><th>Certification</th></tr></thead>
<tbody>
<tr><td>AWS GovCloud</td><td>us-gov-west-1</td><td>FedRAMP High, DoD IL2-IL5</td></tr>
<tr><td>Azure Government</td><td>usgovvirginia</td><td>FedRAMP High, DoD IL2-IL5</td></tr>
<tr><td>GCP Assured Workloads</td><td>us-central1</td><td>FedRAMP Moderate, IL4</td></tr>
<tr><td>OCI Government</td><td>us-gov-ashburn-1</td><td>FedRAMP High</td></tr>
<tr><td>IBM Cloud for Gov</td><td>us-south</td><td>FedRAMP Moderate</td></tr>
<tr><td>On-Premises</td><td>Air-gapped</td><td>IL6 / SIPR</td></tr>
</tbody>
</table>

<h2>Genesis &mdash; Autonomous Self-Improvement</h2>
<p>Genesis v2.0 is an always-on daemon with 13 autonomous reflexes that run on schedule &mdash; researching CVEs, auditing code quality, generating tests, refreshing compliance evidence, and proposing improvements. Knowledge flows from the experimental branch to production via structured GKP artifacts. Your platform gets better while you sleep.</p>

</div>"""

# --- Solutions Page ---
solutions_html = """<style>
:root{--bg2:#1e293b;--bg3:#334155;--accent:#3b82f6;--green:#10b981;--text:#e2e8f0;--dim:#94a3b8;--orange:#f59e0b}
.icdev-page *{box-sizing:border-box;margin:0;padding:0}
.icdev-page{font-family:'Inter',system-ui,sans-serif;color:var(--text);max-width:1100px;margin:0 auto;line-height:1.7;padding:0 24px}
.icdev-page h2{font-size:1.8rem;font-weight:700;margin:48px 0 16px}
.icdev-page h3{font-size:1.2rem;font-weight:600;margin:0 0 8px}
.icdev-page p{color:var(--dim);font-size:1.05rem;margin-bottom:16px}
.sol-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin:20px 0}
.sol-card{background:var(--bg2);border-radius:12px;padding:28px;border:1px solid var(--bg3)}
.sol-card h3{color:var(--accent)}
.sol-before{color:var(--orange);font-size:.9rem;margin-bottom:8px}
.sol-after{color:var(--green);font-size:.9rem;margin-bottom:12px}
.sol-list{list-style:none;padding:0}
.sol-list li{padding:4px 0;font-size:.9rem;color:var(--dim)}
.sol-list li::before{content:"\\2713 ";color:var(--green);font-weight:700}
@media(max-width:768px){.sol-grid{grid-template-columns:1fr}}
</style>
<div class="icdev-page">

<p style="font-size:1.2rem;text-align:center;max-width:600px;margin:0 auto 40px;">Four use cases. One platform. Each transforms a manual, months-long process into an automated, continuous workflow.</p>

<div class="sol-grid">

<div class="sol-card">
<h3>ATO Acceleration</h3>
<div class="sol-before">Before: 12-18 months, $2.4M, 560 hours manual</div>
<div class="sol-after">After: Weeks, automated evidence, continuous monitoring</div>
<p>Generate SSPs, POAMs, STIG checklists, SBOMs, and OSCAL artifacts at build time. Map one NIST 800-53 control and cascade across 30+ frameworks. FedRAMP 20x KSI evidence built in.</p>
<ul class="sol-list">
<li>Dual-hub crosswalk engine (NIST + ISO 27001)</li>
<li>Continuous ATO monitoring with freshness scoring</li>
<li>OSCAL-native output for eMASS and Xacta</li>
<li>FedRAMP, CMMC, CJIS, HIPAA, PCI DSS, SOC 2</li>
</ul>
</div>

<div class="sol-card">
<h3>DevSecOps Pipeline</h3>
<div class="sol-before">Before: Security bolted on after development</div>
<div class="sol-after">After: Security woven into every commit</div>
<p>SAST, dependency audit, secret detection, and container scanning run automatically. Zero Trust architecture scored across 7 pillars. Policy-as-code with Kyverno or OPA.</p>
<ul class="sol-list">
<li>STIG-hardened Docker containers</li>
<li>Service mesh generation (Istio/Linkerd)</li>
<li>Network segmentation and mTLS</li>
<li>Pipeline security gates block on CAT1 findings</li>
</ul>
</div>

<div class="sol-card">
<h3>Legacy Modernization</h3>
<div class="sol-before">Before: Monolith, no documentation, ATO at risk</div>
<div class="sol-after">After: Decomposed, documented, ATO preserved</div>
<p>7R assessment (Retain, Retire, Rehost, Replatform, Refactor, Re-architect, Replace). Strangler fig tracking. Cross-language translation across 30 language pairs.</p>
<ul class="sol-list">
<li>Automated architecture extraction</li>
<li>ATO compliance bridge during migration</li>
<li>Digital thread maintained through decomposition</li>
<li>Version and framework migration (Python 2 to 3, Struts to Spring)</li>
</ul>
</div>

<div class="sol-card">
<h3>AI Governance</h3>
<div class="sol-before">Before: No inventory, no oversight, no accountability</div>
<div class="sol-after">After: Full transparency, model cards, impact assessments</div>
<p>AI inventory per OMB M-25-21. Model cards (Google format). System cards. Fairness assessment. Confabulation detection. CAIO designation and oversight plans.</p>
<ul class="sol-list">
<li>NIST AI RMF, ISO 42001, EU AI Act</li>
<li>MITRE ATLAS threat defense</li>
<li>OWASP LLM Top 10 + Agentic AI security</li>
<li>GAO-21-519SP evidence builder</li>
</ul>
</div>

</div>
</div>"""

# Create pages
platform_id = wp.wp.newPost(*auth, {
    'post_type': 'page', 'post_status': 'publish',
    'post_title': 'Platform', 'post_name': 'platform',
    'post_content': platform_html,
})
print(f'Platform: ID={platform_id}')

solutions_id = wp.wp.newPost(*auth, {
    'post_type': 'page', 'post_status': 'publish',
    'post_title': 'Solutions', 'post_name': 'solutions',
    'post_content': solutions_html,
})
print(f'Solutions: ID={solutions_id}')

blog_id = wp.wp.newPost(*auth, {
    'post_type': 'page', 'post_status': 'publish',
    'post_title': 'Blog', 'post_name': 'blog',
    'post_content': '',
})
print(f'Blog: ID={blog_id}')

# Set Blog as the posts page
try:
    wp.wp.setOptions(*auth, {'page_for_posts': str(blog_id)})
    print('Blog set as posts page')
except Exception as e:
    print(f'Could not set posts page: {e}')

print('\nPages created. Now updating navigation menu...')

# Get existing menus
terms = wp.wp.getTerms(*auth, 'nav_menu')
print(f'Existing menus: {[(t["term_id"], t["name"]) for t in terms]}')
