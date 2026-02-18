// CUI // SP-CTI
// Controlled by: Department of Defense
// CUI Category: CTI
// Distribution: D
// POC: ICDEV System Administrator
//
// ICDEV Dashboard — Mermaid Diagram Integration
// Provides dark-themed Mermaid rendering, interactive click handlers,
// live editor with debounce, and SVG export.
// Pattern: charts.js IIFE + window.ICDEV namespace (D94)
// ADR: D-M1 (local-only), D-M3 (click handlers), D-M4 (editor), D-M6 (dark theme)

(function () {
    'use strict';

    var NS = window.ICDEV || (window.ICDEV = {});

    // ========================================================================
    // DARK THEME CONFIGURATION (D-M6)
    // Matches ICDEV CSS custom properties from style.css
    // ========================================================================

    var THEME_CONFIG = {
        theme: 'dark',
        themeVariables: {
            // Base colors
            primaryColor: '#1a3a5c',
            primaryTextColor: '#e0e0e0',
            primaryBorderColor: '#4a90d9',
            lineColor: '#4a90d9',
            secondaryColor: '#16213e',
            tertiaryColor: '#0f0f1a',
            // Backgrounds
            mainBkg: '#16213e',
            nodeBorder: '#4a90d9',
            clusterBkg: '#0f0f1a',
            clusterBorder: '#2a2a40',
            titleColor: '#e0e0e0',
            edgeLabelBackground: '#16213e',
            nodeTextColor: '#e0e0e0',
            // State diagram
            labelColor: '#e0e0e0',
            altBackground: '#1a1a2e',
            // Sequence diagram
            actorBkg: '#16213e',
            actorBorder: '#4a90d9',
            actorTextColor: '#e0e0e0',
            signalColor: '#4a90d9',
            signalTextColor: '#e0e0e0',
            noteBkgColor: '#1a1a2e',
            noteBorderColor: '#2a2a40',
            noteTextColor: '#e0e0e0',
            activationBkgColor: '#1a3a5c',
            activationBorderColor: '#4a90d9',
            // Class diagram
            classText: '#e0e0e0',
            // Flowchart
            fillType0: '#1a3a5c',
            fillType1: '#2d1a3a',
            fillType2: '#1a3a2d'
        },
        flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' },
        sequence: { useMaxWidth: true, mirrorActors: false },
        stateDiagram: { useMaxWidth: true },
        startOnLoad: false,
        securityLevel: 'strict'
    };

    // ========================================================================
    // CSS INJECTION
    // ========================================================================

    var _stylesInjected = false;

    function injectStyles() {
        if (_stylesInjected) return;
        _stylesInjected = true;
        var s = document.createElement('style');
        s.id = 'icdev-mermaid-css';
        s.textContent = [
            '/* Mermaid container */',
            '.mermaid-container { margin: 16px 0; padding: 16px;',
            '  background: var(--bg-card, #16213e); border: 1px solid var(--border-color, #2a2a40);',
            '  border-radius: 8px; overflow-x: auto; }',
            '.mermaid-container svg { max-width: 100%; height: auto; }',
            '.mermaid-container .error-text { color: var(--status-red, #dc3545); font-size: 0.85rem; }',
            '',
            '/* Diagram title */',
            '.mermaid-title { font-size: 0.95rem; font-weight: 600; color: var(--text-primary, #e0e0e0);',
            '  margin-bottom: 8px; }',
            '',
            '/* Editor layout */',
            '.mermaid-editor { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; min-height: 420px; }',
            '@media (max-width: 768px) { .mermaid-editor { grid-template-columns: 1fr; } }',
            '.mermaid-editor textarea { background: var(--bg-secondary, #1a1a2e);',
            '  color: var(--text-primary, #e0e0e0);',
            '  border: 1px solid var(--border-color, #2a2a40); border-radius: 6px; padding: 12px;',
            '  font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;',
            '  font-size: 0.85rem; resize: vertical; width: 100%; box-sizing: border-box; }',
            '.mermaid-editor textarea:focus { border-color: var(--accent-blue, #4a90d9);',
            '  outline: 2px solid rgba(74, 144, 217, 0.3); }',
            '.mermaid-preview { background: var(--bg-card, #16213e);',
            '  border: 1px solid var(--border-color, #2a2a40); border-radius: 6px; padding: 16px;',
            '  overflow: auto; display: flex; align-items: center; justify-content: center;',
            '  min-height: 300px; }',
            '',
            '/* Catalog grid */',
            '.mermaid-catalog { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));',
            '  gap: 20px; margin-top: 16px; }',
            '.mermaid-card { background: var(--bg-card, #16213e);',
            '  border: 1px solid var(--border-color, #2a2a40); border-radius: 8px; padding: 20px;',
            '  cursor: pointer; transition: border-color 0.2s ease, transform 0.15s ease; }',
            '.mermaid-card:hover { border-color: var(--accent-blue, #4a90d9);',
            '  transform: translateY(-2px); }',
            '.mermaid-card:focus { outline: 2px solid var(--accent-blue, #4a90d9);',
            '  outline-offset: 2px; }',
            '.mermaid-card h3 { margin: 0 0 6px 0; font-size: 1rem;',
            '  color: var(--text-primary, #e0e0e0); }',
            '.mermaid-card p { margin: 0; font-size: 0.85rem;',
            '  color: var(--text-secondary, #a0a0b8); }',
            '.mermaid-card .card-category { display: inline-block; font-size: 0.7rem;',
            '  padding: 2px 8px; border-radius: 10px; margin-top: 8px;',
            '  background: var(--bg-secondary, #1a1a2e); color: var(--accent-blue-light, #6db3f8); }',
            '',
            '/* Viewer */',
            '.mermaid-viewer { margin-top: 24px; display: none; }',
            '.mermaid-viewer.active { display: block; }',
            '.mermaid-viewer-header { display: flex; justify-content: space-between;',
            '  align-items: center; margin-bottom: 12px; }',
            '.mermaid-viewer-header h3 { margin: 0; color: var(--text-primary, #e0e0e0); }',
            '',
            '/* Tabs */',
            '.diagram-tabs { display: flex; gap: 0; margin-bottom: 20px;',
            '  border-bottom: 2px solid var(--border-color, #2a2a40); }',
            '.diagram-tab { background: transparent; border: none; border-bottom: 2px solid transparent;',
            '  color: var(--text-secondary, #a0a0b8); padding: 10px 20px; cursor: pointer;',
            '  font-size: 0.9rem; margin-bottom: -2px; transition: color 0.2s, border-color 0.2s; }',
            '.diagram-tab:hover { color: var(--text-primary, #e0e0e0); }',
            '.diagram-tab.active { color: var(--accent-blue, #4a90d9);',
            '  border-bottom-color: var(--accent-blue, #4a90d9); }',
            '.diagram-tab-content { display: none; }',
            '.diagram-tab-content.active { display: block; }',
            '',
            '/* Interactive nodes */',
            '.mermaid-container svg .clickable, .mermaid-container svg [data-href] {',
            '  cursor: pointer; }',
            '.mermaid-container svg .clickable:hover rect,',
            '.mermaid-container svg .clickable:hover polygon,',
            '.mermaid-container svg .clickable:hover circle {',
            '  filter: brightness(1.3); }',
            '',
            '/* Toolbar */',
            '.mermaid-toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }',
            '.mermaid-toolbar select, .mermaid-toolbar button {',
            '  background: var(--bg-secondary, #1a1a2e); color: var(--text-primary, #e0e0e0);',
            '  border: 1px solid var(--border-color, #2a2a40); border-radius: 4px;',
            '  padding: 6px 12px; font-size: 0.8rem; cursor: pointer; }',
            '.mermaid-toolbar button:hover { border-color: var(--accent-blue, #4a90d9); }',
            '.mermaid-toolbar label { font-size: 0.8rem; color: var(--text-secondary, #a0a0b8); }'
        ].join('\n');
        document.head.appendChild(s);
    }

    // ========================================================================
    // INITIALIZATION
    // ========================================================================

    var _initialized = false;

    NS.initMermaid = function initMermaid() {
        if (_initialized) return;
        if (typeof mermaid === 'undefined') {
            console.warn('[ICDEV Mermaid] mermaid.js library not loaded — skipping init');
            return;
        }
        _initialized = true;
        mermaid.initialize(THEME_CONFIG);
    };

    // ========================================================================
    // RENDERING
    // ========================================================================

    /** Render all <pre class="mermaid"> elements on the page. */
    NS.renderMermaidDiagrams = function renderMermaidDiagrams() {
        NS.initMermaid();
        if (typeof mermaid === 'undefined') return;
        mermaid.run({ querySelector: 'pre.mermaid' }).then(function () {
            // Post-render: attach click handlers to all mermaid containers
            var containers = document.querySelectorAll('pre.mermaid');
            containers.forEach(function (el) { _attachClickHandlers(el); });
        }).catch(function (err) {
            console.error('[ICDEV Mermaid] Batch render error:', err);
        });
    };

    /** Render a single diagram definition into a container by ID. */
    NS.renderMermaid = function renderMermaid(containerId, definition) {
        NS.initMermaid();
        if (typeof mermaid === 'undefined') return Promise.reject('mermaid not loaded');
        var container = document.getElementById(containerId);
        if (!container) return Promise.reject('container not found: ' + containerId);
        var uid = 'mermaid-' + Math.random().toString(36).substr(2, 9);
        return mermaid.render(uid, definition).then(function (result) {
            container.innerHTML = result.svg;
            _attachClickHandlers(container);
            return result;
        }).catch(function (err) {
            container.innerHTML = '<p class="error-text">Diagram render error: '
                + _escapeHtml(err.message || String(err)) + '</p>';
            throw err;
        });
    };

    // ========================================================================
    // INTERACTIVE CLICK HANDLERS (D-M3)
    // ========================================================================

    function _attachClickHandlers(container) {
        // Handle Mermaid click callbacks that produce data-href or href attributes
        var links = container.querySelectorAll('a[href], [data-href]');
        links.forEach(function (node) {
            node.style.cursor = 'pointer';
            node.setAttribute('role', 'link');
            node.addEventListener('click', function (e) {
                var href = node.getAttribute('data-href') || node.getAttribute('href');
                if (href && href !== '#' && !href.startsWith('javascript:')) {
                    e.preventDefault();
                    e.stopPropagation();
                    window.location.href = href;
                }
            });
        });
        // Also handle nodes with class clickable (added by Mermaid click syntax)
        var clickables = container.querySelectorAll('.clickable');
        clickables.forEach(function (node) {
            node.style.cursor = 'pointer';
            node.setAttribute('tabindex', '0');
            node.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    node.click();
                }
            });
        });
    }

    // ========================================================================
    // EDITOR (D-M4)
    // ========================================================================

    /** Initialize a Mermaid editor with live preview. */
    NS.initMermaidEditor = function initMermaidEditor(textareaId, previewId) {
        var textarea = document.getElementById(textareaId);
        var preview = document.getElementById(previewId);
        if (!textarea || !preview) return;

        var debounceTimer = null;

        textarea.addEventListener('input', function () {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(function () {
                var src = textarea.value.trim();
                if (src) {
                    NS.renderMermaid(previewId, src);
                } else {
                    preview.innerHTML = '<p style="color:var(--text-muted)">Type Mermaid syntax to see a preview</p>';
                }
            }, 500);
        });

        // Initial render if textarea has content
        if (textarea.value.trim()) {
            NS.renderMermaid(previewId, textarea.value.trim());
        }
    };

    // ========================================================================
    // SVG EXPORT
    // ========================================================================

    /** Export the SVG from a container as a downloadable file. */
    NS.exportMermaidSVG = function exportMermaidSVG(containerId, filename) {
        var container = document.getElementById(containerId);
        if (!container) return;
        var svg = container.querySelector('svg');
        if (!svg) {
            if (NS.showNotification) NS.showNotification('No diagram to export', 'warning');
            return;
        }
        var serializer = new XMLSerializer();
        var data = serializer.serializeToString(svg);
        // Prepend XML declaration
        data = '<?xml version="1.0" encoding="UTF-8"?>\n' + data;
        var blob = new Blob([data], { type: 'image/svg+xml;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename || 'icdev-diagram.svg';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        if (NS.showNotification) NS.showNotification('SVG exported', 'success');
    };

    // ========================================================================
    // UTILITIES
    // ========================================================================

    function _escapeHtml(str) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    // ========================================================================
    // AUTO-INITIALIZATION
    // ========================================================================

    function init() {
        injectStyles();
        NS.renderMermaidDiagrams();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
