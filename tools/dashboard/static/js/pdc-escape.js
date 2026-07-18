/* CUI // SP-CTI — PDC shared HTML/attribute escaping helpers.
 *
 * Single source of truth for neutralizing untrusted (and cross-canvas, e.g.
 * NDC-supplied) strings before they are interpolated into innerHTML on the
 * Pipeline Design Canvas pages. Prevents the stored-XSS class swept in
 * task pdx-sec-02.
 *
 * escapeHtml  — element/text context. Escapes & < > " ' so the value cannot
 *               open a tag, break out of a quoted attribute, or inject an
 *               event handler.
 * escapeAttr  — attribute-value context. Same entity set (safe for both
 *               single- and double-quoted attributes) plus backtick.
 *
 * NOTE: neither helper makes a value safe for a *JavaScript* string context
 * (e.g. inline onclick="fn('${v}')"). The browser HTML-decodes attribute
 * values before the JS parser runs, so entity escaping does not protect that
 * sink — use data-* attributes + addEventListener there instead.
 */
(function (global) {
  'use strict';

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, '&#96;');
  }

  global.escapeHtml = escapeHtml;
  global.escapeAttr = escapeAttr;
})(typeof window !== 'undefined' ? window : this);
