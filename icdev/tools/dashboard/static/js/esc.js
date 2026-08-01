/* CUI // SP-CTI — Shared XSS-safe escaping + markdown rendering helpers.
 *
 * Single source of truth (task nav-sec-08) for neutralizing untrusted
 * (user-, LLM-, or external-API-supplied) strings before they reach an
 * innerHTML sink anywhere in the dashboard. Loaded early from base.html so
 * every page-level script can rely on window.escHtml / window.escAttr /
 * window.safeMarkdown without redefining a local copy.
 *
 *   escHtml(v)      — element/text context. Escapes & < > " ' so the value
 *                     cannot open a tag, break out of a quoted attribute, or
 *                     inject an event handler.
 *   escAttr(v)      — attribute-value context. escHtml + backtick.
 *   safeMarkdown(t) — render markdown to HTML and sanitize it. Uses marked()
 *                     when present, then DOMPurify.sanitize() to strip
 *                     <script>, event handlers, and javascript: URIs. Fails
 *                     CLOSED: if marked or DOMPurify is unavailable, returns
 *                     escaped, whitespace-preserving plain text — never raw,
 *                     unsanitized markup.
 *
 * DOMPurify + marked are vendored locally (air-gap, no CDN) and loaded before
 * this file in base.html.
 *
 * NOTE: escHtml/escAttr do NOT make a value safe for a *JavaScript* string
 * context (e.g. inline onclick="fn('${v}')"). The browser HTML-decodes
 * attribute values before the JS parser runs; use data-* attributes +
 * addEventListener there instead.
 */
(function (global) {
  'use strict';

  function escHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escAttr(value) {
    return escHtml(value).replace(/`/g, '&#96;');
  }

  // Escaped, whitespace-preserving plain-text fallback (fail-closed path).
  function _plainFallback(text) {
    return '<span style="white-space:pre-wrap;word-break:break-word;">'
      + escHtml(text) + '</span>';
  }

  function safeMarkdown(text) {
    if (text === null || text === undefined) return '';
    var html;
    if (typeof global.marked !== 'undefined') {
      try {
        html = typeof global.marked.parse === 'function'
          ? global.marked.parse(String(text))
          : global.marked(String(text));
      } catch (e) {
        return _plainFallback(text);
      }
    } else {
      // No markdown renderer — cannot safely produce HTML; escape instead.
      return _plainFallback(text);
    }
    if (typeof global.DOMPurify !== 'undefined' && global.DOMPurify.sanitize) {
      try {
        return global.DOMPurify.sanitize(html);
      } catch (e) {
        return _plainFallback(text);
      }
    }
    // marked ran but no sanitizer available: fail closed rather than emit
    // unsanitized markup that may contain <script>/onerror/javascript:.
    return _plainFallback(text);
  }

  global.escHtml = escHtml;
  global.escAttr = escAttr;
  global.safeMarkdown = safeMarkdown;
  // Back-compat aliases for existing local helper names.
  if (typeof global.escapeHtml === 'undefined') global.escapeHtml = escHtml;
  if (typeof global.escapeAttr === 'undefined') global.escapeAttr = escAttr;
})(typeof window !== 'undefined' ? window : this);
