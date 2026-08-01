/* CUI // SP-CTI — PDC shared fetch + toast helpers (pdx-fix-01).
 *
 * Single source of truth for JSON fetches on the Pipeline Design Canvas pages.
 * Prior code silently swallowed HTTP errors (no r.ok / data.error check, no
 * .catch), so a 413 / RLS-denial / server error looked like success and the
 * UI either did nothing or falsely reported completion.
 *
 * fetchJson(url, opts)
 *   - Performs fetch(url, opts), parses the body as JSON (tolerating an empty
 *     or non-JSON body), and REJECTS when the response is not ok OR the parsed
 *     body carries an `error` field. Callers attach `.catch` and surface the
 *     thrown Error via pdcToast so failures are always visible.
 *
 * pdcToast(message, type)
 *   - Small fixed-position toast. type: 'error' (default) | 'success' | 'info'.
 *     Auto-dismisses; click ✕ to close early. No external dependencies.
 */
(function (global) {
  'use strict';

  async function fetchJson(url, opts) {
    const resp = await fetch(url, opts || {});
    let data = null;
    try {
      const text = await resp.text();
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      data = null;
    }
    if (!resp.ok) {
      const msg = (data && data.error) ? data.error : ('Request failed (HTTP ' + resp.status + ')');
      throw new Error(msg);
    }
    if (data && data.error) {
      throw new Error(data.error);
    }
    return data;
  }

  function pdcToast(message, type) {
    const colors = {
      error: { border: '#c0392b', accent: '#e74c3c' },
      success: { border: '#1e7d3c', accent: '#27ae60' },
      info: { border: '#1e3a6e', accent: '#3498db' },
    };
    const c = colors[type] || colors.error;
    const toast = document.createElement('div');
    toast.className = 'pdc-toast';
    toast.style.cssText = [
      'position:fixed', 'bottom:24px', 'right:24px', 'z-index:10000',
      'background:#0f1e35', 'border:1px solid ' + c.border, 'border-left:4px solid ' + c.accent,
      'border-radius:8px', 'padding:12px 40px 12px 16px', 'max-width:360px',
      'box-shadow:0 4px 16px rgba(0,0,0,0.5)', 'font-family:sans-serif',
      'font-size:13px', 'color:#eaeaea',
    ].join(';');
    const msg = document.createElement('div');
    msg.textContent = String(message == null ? '' : message);
    toast.appendChild(msg);
    const close = document.createElement('button');
    close.textContent = '✕';
    close.setAttribute('aria-label', 'Dismiss');
    close.style.cssText = 'position:absolute;top:8px;right:8px;background:none;border:none;color:#7a8cb0;cursor:pointer;font-size:14px;';
    close.addEventListener('click', () => toast.remove());
    toast.appendChild(close);
    document.body.appendChild(toast);
    setTimeout(() => { if (toast.parentElement) toast.remove(); }, 6000);
    return toast;
  }

  global.fetchJson = fetchJson;
  global.pdcToast = pdcToast;
})(typeof window !== 'undefined' ? window : this);
