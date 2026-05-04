/* CUI // SP-CTI
 * OPT-68: debounce_filter.js — filter-as-you-type helper with debouncing.
 *
 * Adapted from marmelab/react-admin's useListFilter pattern (MIT).
 * See https://github.com/marmelab/react-admin
 *
 * Pure vanilla JS — no framework. Wires an <input> element (or a
 * container of inputs) to a fetch+render callback with a 250ms
 * debounce so typing feels live but doesn't hammer the server.
 *
 * Usage:
 *
 *   // Single input
 *   ICDEV.debounceFilter.bind(document.getElementById('search'), {
 *       delayMs: 250,
 *       onFilter: async function (value) {
 *           const r = await fetch('/api/tasks?q=' + encodeURIComponent(value));
 *           const data = await r.json();
 *           renderTable(data.items);
 *       },
 *   });
 *
 *   // Multiple inputs sharing one callback (form-style filters)
 *   ICDEV.debounceFilter.bindForm(document.getElementById('filters'), {
 *       delayMs: 300,
 *       onFilter: function (filters) {
 *           // filters is {name: value, ...}
 *           const params = new URLSearchParams(filters);
 *           fetch('/api/poam?' + params).then(...).then(renderPoam);
 *       },
 *   });
 */
(function () {
    'use strict';

    function _debounce(fn, wait) {
        var timerId = null;
        var debounced = function () {
            var args = arguments;
            var ctx = this;
            if (timerId) clearTimeout(timerId);
            timerId = setTimeout(function () {
                timerId = null;
                fn.apply(ctx, args);
            }, wait);
        };
        debounced.cancel = function () {
            if (timerId) { clearTimeout(timerId); timerId = null; }
        };
        debounced.flush = function () {
            if (timerId) {
                clearTimeout(timerId);
                timerId = null;
                fn.apply(null, arguments);
            }
        };
        return debounced;
    }

    function _collectFormValues(formEl) {
        var out = {};
        var inputs = formEl.querySelectorAll('input, select, textarea');
        for (var i = 0; i < inputs.length; i++) {
            var el = inputs[i];
            if (!el.name) continue;
            if (el.type === 'checkbox' || el.type === 'radio') {
                if (el.checked) out[el.name] = el.value;
            } else {
                out[el.name] = el.value;
            }
        }
        return out;
    }

    /**
     * Bind a single input to an onFilter callback with debouncing.
     */
    function bind(inputEl, opts) {
        opts = opts || {};
        var delay = typeof opts.delayMs === 'number' ? opts.delayMs : 250;
        if (!inputEl || typeof opts.onFilter !== 'function') {
            throw new Error('debounceFilter.bind: need inputEl + onFilter');
        }
        var debounced = _debounce(function () {
            try {
                opts.onFilter(inputEl.value);
            } catch (err) {
                console.warn('debounceFilter onFilter threw:', err);
            }
        }, delay);
        inputEl.addEventListener('input', debounced);
        return {
            cancel: debounced.cancel,
            unbind: function () { inputEl.removeEventListener('input', debounced); },
            flush: function () { debounced.flush(); },
        };
    }

    /**
     * Bind every input/select inside a container to a single onFilter.
     * The callback receives a {name: value, ...} snapshot of the whole
     * form on every keystroke.
     */
    function bindForm(formEl, opts) {
        opts = opts || {};
        var delay = typeof opts.delayMs === 'number' ? opts.delayMs : 250;
        if (!formEl || typeof opts.onFilter !== 'function') {
            throw new Error('debounceFilter.bindForm: need formEl + onFilter');
        }
        var debounced = _debounce(function () {
            try {
                opts.onFilter(_collectFormValues(formEl));
            } catch (err) {
                console.warn('debounceFilter onFilter threw:', err);
            }
        }, delay);
        var listener = function () { debounced(); };
        formEl.addEventListener('input', listener);
        formEl.addEventListener('change', listener);
        return {
            cancel: debounced.cancel,
            unbind: function () {
                formEl.removeEventListener('input', listener);
                formEl.removeEventListener('change', listener);
            },
            flush: function () { debounced.flush(); },
        };
    }

    window.ICDEV = window.ICDEV || {};
    window.ICDEV.debounceFilter = {
        bind: bind,
        bindForm: bindForm,
        _debounce: _debounce,  // exposed for tests
    };
})();
