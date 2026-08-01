// CUI // SP-CTI
// Chat typed message renderers (Phase 69 — D-CU-2)
// Dispatches rendering based on content_type: markdown, code_block, phase_transition, citation, action_card.
// Depends on: marked.min.js (MIT), highlight.min.js (BSD) loaded globally in base.html.

(function () {
    'use strict';

    // Configure marked.js for security
    if (typeof marked !== 'undefined') {
        marked.setOptions({
            breaks: true,
            gfm: true,
            headerIds: false,
            mangle: false
        });

        // Custom renderer for code blocks with highlight.js
        var renderer = new marked.Renderer();
        renderer.code = function (code, lang) {
            var codeText = typeof code === 'object' ? (code.text || '') : code;
            var codeLang = typeof code === 'object' ? (code.lang || lang || '') : (lang || '');
            var highlighted = codeText;
            if (typeof hljs !== 'undefined' && codeLang && hljs.getLanguage(codeLang)) {
                try { highlighted = hljs.highlight(codeText, { language: codeLang }).value; } catch (e) { /* fallthrough */ }
            } else if (typeof hljs !== 'undefined') {
                try { highlighted = hljs.highlightAuto(codeText).value; } catch (e) { /* fallthrough */ }
            } else {
                highlighted = escapeHtml(codeText);
            }
            return '<div class="msg-code-block">'
                + '<div class="msg-code-block__header">'
                + '<span>' + escapeHtml(codeLang || 'code') + '</span>'
                + '<button class="msg-code-block__copy" onclick="ICDEV.copyCodeBlock(this)" title="Copy">Copy</button>'
                + '</div>'
                + '<pre><code class="hljs">' + highlighted + '</code></pre>'
                + '</div>';
        };
        marked.setOptions({ renderer: renderer });
    }

    function escapeHtml(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    // ===================================================================
    // Typed Renderers
    // ===================================================================

    /**
     * Render markdown content with code highlighting.
     * Falls back to escaped text if marked.js is unavailable.
     */
    function renderMarkdown(text) {
        // nav-sec-08: sanitize markdown output (marked + DOMPurify, fail-closed) so
        // untrusted agent/LLM message content cannot inject active markup. The shared
        // helper uses the globally-configured marked renderer (code highlighting).
        // DOMPurify strips inline on* handlers, so the code-block Copy button is wired
        // via delegation below instead of the inline onclick.
        if (typeof window.safeMarkdown === 'function') {
            return window.safeMarkdown(text);
        }
        if (typeof marked !== 'undefined') {
            try { return marked.parse(text); } catch (e) { /* fallthrough */ }
        }
        return '<span style="white-space:pre-wrap;word-break:break-word;">' + escapeHtml(text) + '</span>';
    }

    /**
     * Render a standalone code block with language label and copy button.
     */
    function renderCodeBlock(code, language) {
        var highlighted = escapeHtml(code);
        if (typeof hljs !== 'undefined' && language && hljs.getLanguage(language)) {
            try { highlighted = hljs.highlight(code, { language: language }).value; } catch (e) { /* fallthrough */ }
        } else if (typeof hljs !== 'undefined') {
            try { highlighted = hljs.highlightAuto(code).value; } catch (e) { /* fallthrough */ }
        }
        return '<div class="msg-code-block">'
            + '<div class="msg-code-block__header">'
            + '<span>' + escapeHtml(language || 'code') + '</span>'
            + '<button class="msg-code-block__copy" onclick="ICDEV.copyCodeBlock(this)" title="Copy">Copy</button>'
            + '</div>'
            + '<pre><code class="hljs">' + highlighted + '</code></pre>'
            + '</div>';
    }

    /**
     * Render a phase transition divider in the message stream.
     */
    function renderPhaseTransition(label) {
        return '<div class="phase-divider"><span class="phase-divider__label">' + escapeHtml(label) + '</span></div>';
    }

    /**
     * Render a citation badge linking to a file:line.
     */
    function renderCitation(file, line, snippet) {
        var display = file + (line ? ':' + line : '');
        var href = file + (line ? '#L' + line : '');
        return '<a class="citation-badge" href="/' + encodeURI(href) + '" title="' + escapeHtml(snippet || '') + '">'
            + '<span>&#128196;</span> '
            + escapeHtml(display)
            + '</a>';
    }

    /**
     * Render a list of citations from an array of {file, line, snippet}.
     */
    function renderCitations(citations) {
        if (!citations || !citations.length) return '';
        var html = '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:4px;">';
        for (var i = 0; i < citations.length; i++) {
            var c = citations[i];
            html += renderCitation(c.file, c.line, c.snippet);
        }
        html += '</div>';
        return html;
    }

    /**
     * Render an action card (structured response block).
     */
    function renderActionCard(title, body, actions) {
        var html = '<div class="action-card">';
        if (title) html += '<div class="action-card__title">' + escapeHtml(title) + '</div>';
        if (body) html += '<div class="action-card__body">' + renderMarkdown(body) + '</div>';
        if (actions && actions.length) {
            html += '<div style="margin-top:8px;display:flex;gap:6px;">';
            for (var i = 0; i < actions.length; i++) {
                html += '<button class="chat-toggle-btn" style="background:var(--accent-blue);" onclick="' + escapeHtml(actions[i].onclick || '') + '">'
                    + escapeHtml(actions[i].label) + '</button>';
            }
            html += '</div>';
        }
        html += '</div>';
        return html;
    }

    /**
     * Render a collapsible card (expandable section).
     */
    function renderCollapsible(title, body, open) {
        var cls = open ? 'collapsible-card collapsible-card--open' : 'collapsible-card';
        return '<div class="' + cls + '">'
            + '<div class="collapsible-card__header" onclick="this.parentElement.classList.toggle(\'collapsible-card--open\')">'
            + '<span>' + escapeHtml(title) + '</span>'
            + '<span class="collapsible-card__chevron">&#9654;</span>'
            + '</div>'
            + '<div class="collapsible-card__body">' + renderMarkdown(body) + '</div>'
            + '</div>';
    }

    /**
     * Copy code block content to clipboard.
     */
    function copyCodeBlock(btn) {
        var block = btn.closest('.msg-code-block');
        if (!block) return;
        var code = block.querySelector('code');
        if (!code) return;
        navigator.clipboard.writeText(code.textContent).then(function () {
            var orig = btn.textContent;
            btn.textContent = 'Copied!';
            setTimeout(function () { btn.textContent = orig; }, 1500);
        });
    }

    // nav-sec-08: delegated Copy handler. DOMPurify strips the inline
    // onclick="ICDEV.copyCodeBlock(this)" from sanitized markdown, so wire the
    // Copy buttons through a single document-level listener instead.
    if (typeof document !== 'undefined' && !window.__icdevCopyDelegated) {
        window.__icdevCopyDelegated = true;
        document.addEventListener('click', function (ev) {
            var btn = ev.target && ev.target.closest
                ? ev.target.closest('.msg-code-block__copy') : null;
            if (btn) copyCodeBlock(btn);
        });
    }

    // ===================================================================
    // Message Dispatcher
    // ===================================================================

    /**
     * Main dispatcher: given a message object, returns rendered HTML.
     * Checks msg.content_type and delegates to the appropriate renderer.
     */
    function renderMessageContent(msg) {
        var ct = msg.content_type || 'text';
        var content = msg.content || '';
        var meta = {};
        try { meta = JSON.parse(msg.metadata || '{}'); } catch (e) {}

        switch (ct) {
            case 'code_block':
                return renderCodeBlock(content, meta.language || '');
            case 'markdown':
                return renderMarkdown(content);
            case 'phase_transition':
                return renderPhaseTransition(content);
            case 'citation':
                return renderCitations(meta.citations || []);
            case 'action_card':
                return renderActionCard(meta.title || '', content, meta.actions);
            default:
                // Default: try markdown rendering (handles code fences in regular text)
                return renderMarkdown(content);
        }
    }

    // ===================================================================
    // Exports
    // ===================================================================

    var ns = window.ICDEV || {};
    ns.renderMarkdown = renderMarkdown;
    ns.renderCodeBlock = renderCodeBlock;
    ns.renderPhaseTransition = renderPhaseTransition;
    ns.renderCitation = renderCitation;
    ns.renderCitations = renderCitations;
    ns.renderActionCard = renderActionCard;
    ns.renderCollapsible = renderCollapsible;
    ns.renderMessageContent = renderMessageContent;
    ns.copyCodeBlock = copyCodeBlock;
    window.ICDEV = ns;
})();
