/* CUI // SP-CTI
 * OPT-68: filter_presets.js — save/load named filter presets to localStorage.
 *
 * Adapted from marmelab/react-admin's saved queries pattern (MIT).
 * See https://github.com/marmelab/react-admin
 *
 * Vanilla JS. LocalStorage only (no server round-trip in this iteration).
 *
 * Usage:
 *
 *   var presets = ICDEV.filterPresets.init({
 *       key: 'poam-filters-v1',     // namespace per page
 *       getCurrent: function () {
 *           return {
 *               canvas: document.getElementById('filter-canvas').value,
 *               severity: document.getElementById('filter-severity').value,
 *           };
 *       },
 *       onApply: function (state) {
 *           document.getElementById('filter-canvas').value = state.canvas || '';
 *           document.getElementById('filter-severity').value = state.severity || '';
 *           loadFindings();
 *       },
 *       selectEl: document.getElementById('preset-dropdown'),
 *   });
 *
 *   // Then call on button clicks:
 *   document.getElementById('save-btn').addEventListener('click', presets.save);
 *   document.getElementById('delete-btn').addEventListener('click', presets.deleteCurrent);
 */
(function () {
    'use strict';

    var STORAGE_PREFIX = 'icdev.filter_presets.';

    function _load(key) {
        try {
            var raw = window.localStorage.getItem(STORAGE_PREFIX + key);
            if (!raw) return {};
            var obj = JSON.parse(raw);
            return obj && typeof obj === 'object' ? obj : {};
        } catch (e) {
            return {};
        }
    }

    function _save(key, presets) {
        try {
            window.localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(presets));
            return true;
        } catch (e) {
            return false;
        }
    }

    /**
     * Initialize preset handling for a page.
     *
     * @param {Object} opts
     * @param {string} opts.key          — namespace (e.g. 'poam-filters-v1')
     * @param {Function} opts.getCurrent — returns current filter state object
     * @param {Function} opts.onApply    — called with state when preset applied
     * @param {HTMLSelectElement} opts.selectEl — dropdown populated with presets
     * @returns {Object} { save, deleteCurrent, list, applyByName, refresh }
     */
    function init(opts) {
        opts = opts || {};
        if (!opts.key || typeof opts.getCurrent !== 'function'
            || typeof opts.onApply !== 'function' || !opts.selectEl) {
            throw new Error('filterPresets.init: need key, getCurrent, onApply, selectEl');
        }

        var selectEl = opts.selectEl;

        function refresh() {
            var presets = _load(opts.key);
            var names = Object.keys(presets).sort();
            var currentVal = selectEl.value;
            selectEl.innerHTML = '<option value="">-- presets --</option>';
            for (var i = 0; i < names.length; i++) {
                var opt = document.createElement('option');
                opt.value = names[i];
                opt.textContent = names[i];
                selectEl.appendChild(opt);
            }
            if (currentVal && presets[currentVal]) selectEl.value = currentVal;
            return names;
        }

        function save(name) {
            var n = (typeof name === 'string' && name) ? name :
                    (window.prompt('Name this preset:') || '').trim();
            if (!n) return null;
            var presets = _load(opts.key);
            presets[n] = opts.getCurrent();
            _save(opts.key, presets);
            refresh();
            selectEl.value = n;
            return n;
        }

        function deleteCurrent() {
            var n = selectEl.value;
            if (!n) return false;
            var presets = _load(opts.key);
            if (!(n in presets)) return false;
            delete presets[n];
            _save(opts.key, presets);
            refresh();
            return true;
        }

        function applyByName(name) {
            var presets = _load(opts.key);
            if (!(name in presets)) return false;
            opts.onApply(presets[name]);
            return true;
        }

        function list() {
            return _load(opts.key);
        }

        selectEl.addEventListener('change', function () {
            if (selectEl.value) applyByName(selectEl.value);
        });

        refresh();

        return {
            save: save,
            deleteCurrent: deleteCurrent,
            applyByName: applyByName,
            list: list,
            refresh: refresh,
        };
    }

    window.ICDEV = window.ICDEV || {};
    window.ICDEV.filterPresets = {
        init: init,
        _load: _load,        // exposed for tests
        _save: _save,
        _STORAGE_PREFIX: STORAGE_PREFIX,
    };
})();
