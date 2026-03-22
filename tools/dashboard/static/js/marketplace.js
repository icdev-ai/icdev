/**
 * ICDEV™ Studio — Marketplace Storefront
 * Visual asset browser with search, filter, and one-click install.
 */

const Marketplace = (() => {
  let allAssets = [];
  let filteredAssets = [];
  let currentCategory = 'all';
  let currentQuery = '';
  let currentSort = 'name';
  let currentView = 'grid';
  let selectedAssetId = null;

  const $ = id => document.getElementById(id);

  // ── Toast ──
  function toast(msg, type = 'info') {
    const container = $('studio-toasts');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `studio-toast studio-toast--${type}`;
    el.innerHTML = `
      <div class="studio-toast__message">${msg}</div>
      <button class="studio-toast__dismiss" onclick="this.parentElement.remove()">&times;</button>
    `;
    container.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // ── Load Assets ──
  async function loadAssets() {
    try {
      const resp = await fetch('/api/studio/marketplace/assets');
      const data = await resp.json();
      allAssets = data.assets || [];

      // If API returns empty, show demo data for visual preview
      if (!allAssets.length) {
        allAssets = generateDemoAssets();
      }

      filteredAssets = [...allAssets];
      updateStats();
      renderGrid();
    } catch (e) {
      console.error('Failed to load assets:', e);
      allAssets = generateDemoAssets();
      filteredAssets = [...allAssets];
      updateStats();
      renderGrid();
    }
  }

  function generateDemoAssets() {
    return [
      { id: 'fedramp-moderate', name: 'FedRAMP Moderate Controls', category: 'compliance',
        description: 'Complete NIST 800-53 Moderate baseline with 325 controls, evidence templates, and SSP narrative generation.',
        version: '2.1.0', installs: 847, rating: 4.8, author: 'ICDEV™ Core',
        tags: ['FedRAMP', 'NIST', 'ATO'], status: 'published', framework: 'fedramp' },
      { id: 'cmmc-l2-assessment', name: 'CMMC Level 2 Assessment', category: 'compliance',
        description: 'Automated CMMC Level 2 assessment with 110 practices, evidence collection, and gap analysis.',
        version: '1.4.0', installs: 623, rating: 4.7, author: 'ICDEV™ Core',
        tags: ['CMMC', 'DoD', 'CUI'], status: 'published', framework: 'cmmc' },
      { id: 'ato-acceleration', name: 'ATO Acceleration Workflow', category: 'workflows',
        description: 'End-to-end ATO workflow: FIPS 199 categorization → assessment → SSP → POA&M → SBOM → OSCAL export.',
        version: '3.0.0', installs: 1205, rating: 4.9, author: 'ICDEV™ Core',
        tags: ['ATO', 'Workflow', 'Automation'], status: 'published', framework: 'nist' },
      { id: 'security-hardening', name: 'Security Hardening Suite', category: 'skills',
        description: 'SAST, dependency audit, secret detection, and container scanning in one integrated skill pack.',
        version: '2.0.1', installs: 934, rating: 4.6, author: 'ICDEV™ Core',
        tags: ['Security', 'SAST', 'DevSecOps'], status: 'published' },
      { id: 'stig-automation', name: 'STIG Automation Pack', category: 'compliance',
        description: 'Automated DISA STIG checking for 50+ technologies with auto-remediation scripts.',
        version: '1.8.0', installs: 712, rating: 4.5, author: 'ICDEV™ Core',
        tags: ['STIG', 'DISA', 'Hardening'], status: 'published', framework: 'nist' },
      { id: 'govcon-intelligence', name: 'GovCon Intelligence', category: 'skills',
        description: 'SAM.gov scanning, requirement extraction, capability mapping, and proposal response drafting.',
        version: '1.2.0', installs: 389, rating: 4.4, author: 'ICDEV™ Core',
        tags: ['GovCon', 'SAM.gov', 'Proposals'], status: 'published' },
      { id: 'tdd-workflow', name: 'TDD Build Pipeline', category: 'workflows',
        description: 'True RED → GREEN → REFACTOR cycle with automatic test generation and coverage tracking.',
        version: '2.5.0', installs: 1567, rating: 4.8, author: 'ICDEV™ Core',
        tags: ['TDD', 'Testing', 'CI/CD'], status: 'published' },
      { id: 'oscal-export', name: 'OSCAL Document Generator', category: 'compliance',
        description: 'Generate OSCAL-format compliance documents (SSP, SAP, SAR, POA&M) for automated ATO processing.',
        version: '1.1.0', installs: 456, rating: 4.3, author: 'ICDEV™ Core',
        tags: ['OSCAL', 'FedRAMP', 'Automation'], status: 'published', framework: 'fedramp' },
      { id: 'supply-chain-scrm', name: 'Supply Chain Risk Management', category: 'skills',
        description: 'SCRM assessment, dependency graph analysis, CVE triaging, and ISA management.',
        version: '1.0.0', installs: 278, rating: 4.2, author: 'ICDEV™ Core',
        tags: ['SCRM', 'Supply Chain', 'CVE'], status: 'published' },
      { id: 'ai-governance-pack', name: 'AI Governance & Accountability', category: 'compliance',
        description: 'EU AI Act classifier, NIST AI 600-1 assessor, model cards, system cards, and fairness assessment.',
        version: '1.3.0', installs: 345, rating: 4.6, author: 'ICDEV™ Core',
        tags: ['AI Governance', 'EU AI Act', 'NIST AI'], status: 'published' },
      { id: 'incident-response', name: 'Incident Response Playbook', category: 'workflows',
        description: 'Automated incident response with triage, containment, eradication, recovery, and lessons learned.',
        version: '1.5.0', installs: 521, rating: 4.7, author: 'ICDEV™ Core',
        tags: ['IR', 'Security', 'NIST 800-61'], status: 'published', framework: 'nist' },
      { id: 'prompt-engineering', name: 'Prompt Engineering Templates', category: 'hardprompts',
        description: 'Curated LLM prompt templates for code review, compliance narrative, and requirements elicitation.',
        version: '1.0.0', installs: 198, rating: 4.1, author: 'Community',
        tags: ['Prompts', 'LLM', 'Templates'], status: 'published' },
    ];
  }

  // ── Stats ──
  function updateStats() {
    $('mkt-total').textContent = allAssets.length;
    $('mkt-installed').textContent = allAssets.filter(a => a.installed).length;
    $('mkt-available').textContent = allAssets.filter(a => !a.installed).length;
    const cats = new Set(allAssets.map(a => a.category));
    $('mkt-categories-count').textContent = cats.size;
  }

  // ── Render ──
  function renderGrid() {
    const grid = $('mkt-grid');
    const empty = $('mkt-empty');

    if (!filteredAssets.length) {
      grid.innerHTML = '';
      empty.style.display = '';
      $('mkt-showing').textContent = 'No results';
      return;
    }

    empty.style.display = 'none';
    $('mkt-showing').textContent = `Showing ${filteredAssets.length} asset${filteredAssets.length !== 1 ? 's' : ''}`;

    grid.innerHTML = filteredAssets.map((asset, i) => `
      <div class="mkt-asset studio-animate-in" style="animation-delay:${i * 40}ms; cursor:pointer;"
           onclick="Marketplace.showDetail('${esc(asset.id)}')">
        <div class="mkt-asset__banner" style="background:${catGradient(asset.category)};"></div>
        <div class="mkt-asset__body">
          <div class="mkt-asset__icon" style="background:${catBg(asset.category)};color:${catFg(asset.category)};">
            ${catIcon(asset.category)}
          </div>
          <div class="mkt-asset__name">${esc(asset.name)}</div>
          <div class="mkt-asset__description">${esc(asset.description)}</div>
          <div class="mkt-asset__tags">
            ${(asset.tags || []).slice(0, 3).map(t =>
              `<span class="studio-tag">${esc(t)}</span>`
            ).join('')}
          </div>
        </div>
        <div class="mkt-asset__footer">
          <div class="studio-flex studio-gap-3">
            <span class="mkt-asset__stat">&#8615; ${asset.installs || 0}</span>
            <span class="mkt-asset__stat mkt-asset__rating">&#9733; ${asset.rating || '-'}</span>
          </div>
          <span class="studio-badge studio-badge--neutral">v${esc(asset.version || '1.0')}</span>
        </div>
      </div>
    `).join('');
  }

  // ── Search ──
  function search(query) {
    currentQuery = query.toLowerCase();
    applyFilters();
  }

  // ── Category Filter ──
  function filterCategory(el, cat) {
    currentCategory = cat;
    document.querySelectorAll('.mkt-category').forEach(c => c.classList.remove('mkt-category--active'));
    el.classList.add('mkt-category--active');
    applyFilters();
  }

  // ── Framework Filter ──
  function filterFramework(fw) {
    applyFilters();
  }

  // ── Sort ──
  function sort(by) {
    currentSort = by;
    applyFilters();
  }

  // ── Apply all filters ──
  function applyFilters() {
    const fw = $('mkt-framework')?.value || '';

    filteredAssets = allAssets.filter(a => {
      if (currentCategory !== 'all' && a.category !== currentCategory) return false;
      if (fw && a.framework !== fw) return false;
      if (currentQuery) {
        const searchable = `${a.name} ${a.description} ${(a.tags || []).join(' ')}`.toLowerCase();
        if (!searchable.includes(currentQuery)) return false;
      }
      return true;
    });

    // Sort
    filteredAssets.sort((a, b) => {
      switch (currentSort) {
        case 'rating': return (b.rating || 0) - (a.rating || 0);
        case 'installs': return (b.installs || 0) - (a.installs || 0);
        case 'recent': return (b.created_at || '').localeCompare(a.created_at || '');
        default: return (a.name || '').localeCompare(b.name || '');
      }
    });

    renderGrid();
  }

  function clearFilters() {
    currentCategory = 'all';
    currentQuery = '';
    $('mkt-search').value = '';
    $('mkt-framework').value = '';
    document.querySelectorAll('.mkt-category').forEach(c => c.classList.remove('mkt-category--active'));
    document.querySelector('[data-cat="all"]')?.classList.add('mkt-category--active');
    filteredAssets = [...allAssets];
    renderGrid();
  }

  // ── Detail Modal ──
  function showDetail(assetId) {
    const asset = allAssets.find(a => a.id === assetId);
    if (!asset) return;
    selectedAssetId = assetId;

    $('mkt-detail-name').textContent = asset.name;
    $('mkt-detail-body').innerHTML = `
      <div class="studio-flex studio-items-center studio-gap-3 studio-mb-6">
        <div class="mkt-asset__icon" style="width:56px;height:56px;font-size:1.5rem;background:${catBg(asset.category)};color:${catFg(asset.category)};">
          ${catIcon(asset.category)}
        </div>
        <div>
          <div style="font-size:1.125rem;font-weight:600;color:var(--studio-text-primary);">${esc(asset.name)}</div>
          <div class="studio-text-muted" style="font-size:0.8rem;">by ${esc(asset.author || 'Unknown')} &middot; v${esc(asset.version || '1.0')}</div>
        </div>
      </div>

      <div class="studio-stats" style="margin-bottom:24px;">
        <div class="studio-stat">
          <div class="studio-stat__label">Downloads</div>
          <div class="studio-stat__value">${asset.installs || 0}</div>
        </div>
        <div class="studio-stat">
          <div class="studio-stat__label">Rating</div>
          <div class="studio-stat__value" style="color:var(--studio-warning);">&#9733; ${asset.rating || '-'}</div>
        </div>
        <div class="studio-stat">
          <div class="studio-stat__label">Category</div>
          <div class="studio-stat__value" style="font-size:1rem;text-transform:capitalize;">${esc(asset.category)}</div>
        </div>
      </div>

      <div class="studio-form-group">
        <label class="studio-label">Description</label>
        <p style="color:var(--studio-text-secondary);font-size:0.875rem;line-height:1.7;">
          ${esc(asset.description)}
        </p>
      </div>

      <div class="studio-form-group">
        <label class="studio-label">Tags</label>
        <div class="studio-flex studio-gap-2" style="flex-wrap:wrap;">
          ${(asset.tags || []).map(t =>
            `<span class="studio-tag">${esc(t)}</span>`
          ).join('')}
        </div>
      </div>

      ${asset.framework ? `
      <div class="studio-form-group">
        <label class="studio-label">Compliance Framework</label>
        <span class="studio-badge studio-badge--accent">${esc(asset.framework).toUpperCase()}</span>
      </div>` : ''}
    `;

    $('mkt-install-btn').textContent = asset.installed ? '&#10003; Installed' : '&#8615; Install';
    $('mkt-install-btn').disabled = !!asset.installed;
    $('mkt-detail-modal').style.display = '';
  }

  function closeDetail() {
    $('mkt-detail-modal').style.display = 'none';
    selectedAssetId = null;
  }

  async function install() {
    if (!selectedAssetId) return;
    const btn = $('mkt-install-btn');
    btn.innerHTML = '<span class="studio-spinner studio-spinner--sm"></span> Installing...';
    btn.disabled = true;

    try {
      const resp = await fetch(`/api/studio/marketplace/assets/${selectedAssetId}/install`, {
        method: 'POST',
      });
      const data = await resp.json();
      if (data.error) {
        toast(data.error, 'error');
        btn.innerHTML = '&#8615; Install';
        btn.disabled = false;
      } else {
        toast(`${selectedAssetId} installed successfully`, 'success');
        btn.innerHTML = '&#10003; Installed';
        // Mark as installed
        const asset = allAssets.find(a => a.id === selectedAssetId);
        if (asset) asset.installed = true;
        updateStats();
      }
    } catch (e) {
      toast('Installation failed: ' + e.message, 'error');
      btn.innerHTML = '&#8615; Install';
      btn.disabled = false;
    }
  }

  // ── View toggle ──
  function setView(v) {
    currentView = v;
    const grid = $('mkt-grid');
    if (v === 'list') {
      grid.className = 'studio-grid';
      grid.style.gridTemplateColumns = '1fr';
    } else {
      grid.className = 'studio-grid studio-grid--auto';
      grid.style.gridTemplateColumns = '';
    }
  }

  // ── Helpers ──
  function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

  function catGradient(cat) {
    const m = {
      compliance: 'linear-gradient(135deg,#6366f1,#8b5cf6)',
      skills: 'linear-gradient(135deg,#f59e0b,#f97316)',
      workflows: 'linear-gradient(135deg,#22c55e,#10b981)',
      goals: 'linear-gradient(135deg,#3b82f6,#06b6d4)',
      hardprompts: 'linear-gradient(135deg,#ec4899,#f43f5e)',
      context: 'linear-gradient(135deg,#8b5cf6,#a855f7)',
      args: 'linear-gradient(135deg,#64748b,#94a3b8)',
    };
    return m[cat] || 'linear-gradient(135deg,#6366f1,#8b5cf6)';
  }

  function catBg(cat) {
    const m = {
      compliance: 'rgba(99,102,241,0.12)', skills: 'rgba(245,158,11,0.12)',
      workflows: 'rgba(34,197,94,0.12)', goals: 'rgba(59,130,246,0.12)',
      hardprompts: 'rgba(236,72,153,0.12)', context: 'rgba(139,92,246,0.12)',
      args: 'rgba(100,116,139,0.12)',
    };
    return m[cat] || 'rgba(99,102,241,0.12)';
  }

  function catFg(cat) {
    const m = {
      compliance: '#818cf8', skills: '#f59e0b', workflows: '#22c55e',
      goals: '#3b82f6', hardprompts: '#ec4899', context: '#a855f7',
      args: '#94a3b8',
    };
    return m[cat] || '#818cf8';
  }

  function catIcon(cat) {
    const m = {
      compliance: '&#128737;', skills: '&#9889;', workflows: '&#9881;',
      goals: '&#127919;', hardprompts: '&#128172;', context: '&#128214;',
      args: '&#9881;',
    };
    return m[cat] || '&#9632;';
  }

  // ── Keyboard shortcut: / focuses search ──
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
      e.preventDefault();
      $('mkt-search')?.focus();
    }
    if (e.key === 'Escape') closeDetail();
  });

  // ── Init ──
  document.addEventListener('DOMContentLoaded', loadAssets);

  return {
    search, filterCategory, filterFramework, sort, clearFilters,
    showDetail, closeDetail, install, setView,
  };
})();
