/* CUI // SP-CTI
 * OPT-68: undo_toast.js — reusable Snackbar-style undo toast.
 *
 * Adapted from marmelab/react-admin's undoable mutation pattern (MIT).
 * See https://github.com/marmelab/react-admin
 *
 * Usage from any template (no dependencies — vanilla JS, no framework):
 *
 *     // After a destructive mutation returns 200:
 *     ICDEV.undoToast.show({
 *         message: 'Task moved to done',
 *         undoCallback: async () => {
 *             await fetch('/api/kanban/tasks/' + id + '/move', {
 *                 method: 'POST',
 *                 headers: {'Content-Type': 'application/json'},
 *                 body: JSON.stringify({status: 'in_progress'}),
 *             });
 *         },
 *         durationMs: 5000,   // optional, default 5000
 *     });
 *
 * The toast stacks in the bottom-right corner. Clicking "Undo" calls
 * the callback and dismisses the toast early. After durationMs the
 * toast auto-dismisses with no action.
 */
(function () {
    'use strict';

    var CONTAINER_ID = 'icdev-undo-toast-container';
    var STYLE_ID = 'icdev-undo-toast-style';

    function ensureStyles() {
        if (document.getElementById(STYLE_ID)) return;
        var style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = [
            '#' + CONTAINER_ID + '{position:fixed;bottom:20px;right:20px;',
            'z-index:10000;display:flex;flex-direction:column;gap:10px;',
            'pointer-events:none;max-width:360px;}',
            '.icdev-undo-toast{background:#1e293b;color:#e2e8f0;',
            'border:1px solid #334155;border-left:4px solid #10b981;',
            'padding:12px 16px;border-radius:6px;',
            'box-shadow:0 8px 24px rgba(0,0,0,.4);',
            'display:flex;align-items:center;gap:12px;',
            'font-family:system-ui,sans-serif;font-size:13px;',
            'pointer-events:auto;min-width:260px;',
            'animation:icdev-undo-in 0.18s ease-out;}',
            '.icdev-undo-toast.hiding{animation:icdev-undo-out 0.18s ease-in;',
            'animation-fill-mode:forwards;}',
            '.icdev-undo-toast-msg{flex:1;line-height:1.4;}',
            '.icdev-undo-toast-btn{background:transparent;border:1px solid #64748b;',
            'color:#fbbf24;padding:4px 10px;border-radius:4px;',
            'cursor:pointer;font-weight:600;font-size:12px;',
            'text-transform:uppercase;letter-spacing:0.5px;}',
            '.icdev-undo-toast-btn:hover{background:#334155;color:#fde68a;}',
            '.icdev-undo-toast-close{background:transparent;border:none;',
            'color:#94a3b8;cursor:pointer;font-size:16px;line-height:1;',
            'padding:0 4px;}',
            '@keyframes icdev-undo-in{from{opacity:0;transform:translateY(20px)}',
            'to{opacity:1;transform:translateY(0)}}',
            '@keyframes icdev-undo-out{from{opacity:1;transform:translateY(0)}',
            'to{opacity:0;transform:translateY(20px)}}',
        ].join('');
        document.head.appendChild(style);
    }

    function ensureContainer() {
        var c = document.getElementById(CONTAINER_ID);
        if (c) return c;
        c = document.createElement('div');
        c.id = CONTAINER_ID;
        document.body.appendChild(c);
        return c;
    }

    /**
     * Show an undo toast.
     * @param {Object} opts
     * @param {string} opts.message              - text to display
     * @param {Function} [opts.undoCallback]     - async () => { ... }
     * @param {number} [opts.durationMs=5000]    - auto-dismiss window
     * @param {Function} [opts.onExpire]         - called when window closes without undo
     * @returns {{dismiss: Function}}
     */
    function show(opts) {
        opts = opts || {};
        ensureStyles();
        var container = ensureContainer();

        var toast = document.createElement('div');
        toast.className = 'icdev-undo-toast';

        var msg = document.createElement('div');
        msg.className = 'icdev-undo-toast-msg';
        msg.textContent = opts.message || 'Action completed';

        var undoBtn = null;
        if (typeof opts.undoCallback === 'function') {
            undoBtn = document.createElement('button');
            undoBtn.className = 'icdev-undo-toast-btn';
            undoBtn.type = 'button';
            undoBtn.textContent = 'Undo';
        }

        var closeBtn = document.createElement('button');
        closeBtn.className = 'icdev-undo-toast-close';
        closeBtn.type = 'button';
        closeBtn.innerHTML = '&times;';
        closeBtn.setAttribute('aria-label', 'Dismiss');

        toast.appendChild(msg);
        if (undoBtn) toast.appendChild(undoBtn);
        toast.appendChild(closeBtn);
        container.appendChild(toast);

        var duration = typeof opts.durationMs === 'number' ? opts.durationMs : 5000;
        var expired = false;
        var undone = false;

        function dismiss(viaUndo) {
            if (toast.classList.contains('hiding')) return;
            toast.classList.add('hiding');
            setTimeout(function () {
                if (toast.parentNode) toast.parentNode.removeChild(toast);
                if (!viaUndo && !expired && typeof opts.onExpire === 'function') {
                    try { opts.onExpire(); } catch (_e) { /* swallow */ }
                }
            }, 200);
        }

        var timerId = setTimeout(function () {
            expired = true;
            if (!undone) {
                if (typeof opts.onExpire === 'function') {
                    try { opts.onExpire(); } catch (_e) { /* swallow */ }
                }
                dismiss(false);
            }
        }, duration);

        if (undoBtn) {
            undoBtn.addEventListener('click', function () {
                if (undone || expired) return;
                undone = true;
                clearTimeout(timerId);
                try {
                    var ret = opts.undoCallback();
                    if (ret && typeof ret.then === 'function') {
                        ret.catch(function (err) {
                            console.warn('undo callback failed:', err);
                        });
                    }
                } catch (err) {
                    console.warn('undo callback threw:', err);
                }
                dismiss(true);
            });
        }

        closeBtn.addEventListener('click', function () {
            clearTimeout(timerId);
            dismiss(false);
        });

        return { dismiss: function () { clearTimeout(timerId); dismiss(false); } };
    }

    window.ICDEV = window.ICDEV || {};
    window.ICDEV.undoToast = { show: show };
})();
