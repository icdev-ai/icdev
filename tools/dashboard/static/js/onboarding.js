/* ICDEV™ Onboarding Wizard — structured API class.
 * Complements the inline _onboarding_wizard.html script.
 * Usage: ICDEV.Onboarding.init()  (called automatically on DOMContentLoaded)
 */
(function (root) {
  'use strict';

  root.ICDEV = root.ICDEV || {};

  function Onboarding() {
    this._step = 0;
    this._total = 3;
    this._selectedCanvases = [];
  }

  /* -------------------------------------------------------------------------
   * Private helpers
   * ---------------------------------------------------------------------- */

  Onboarding.prototype._show = function () {
    var el = document.getElementById('onb-backdrop');
    if (el) el.classList.add('onb-open');
  };

  Onboarding.prototype._hide = function () {
    var el = document.getElementById('onb-backdrop');
    if (el) el.classList.remove('onb-open');
  };

  Onboarding.prototype._renderStep = function (n) {
    for (var i = 0; i < this._total; i++) {
      var pane = document.getElementById('onb-step-' + i);
      var dot  = document.getElementById('onb-dot-' + i);
      if (pane) pane.classList.toggle('active', i === n);
      if (dot) {
        dot.classList.toggle('active', i === n);
        dot.classList.toggle('done',   i < n);
      }
    }
    var backBtn = document.getElementById('onb-back-btn');
    var nextBtn = document.getElementById('onb-next-btn');
    if (backBtn) backBtn.style.display = n > 0 ? '' : 'none';
    if (nextBtn) nextBtn.textContent   = n === this._total - 1 ? 'Finish ✓' : 'Next ›';
  };

  Onboarding.prototype._patchState = function (fields) {
    return fetch('/api/onboarding/state', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields)
    });
  };

  /* -------------------------------------------------------------------------
   * Public API
   * ---------------------------------------------------------------------- */

  /** Check state via GET and show wizard if not yet completed. */
  Onboarding.prototype.init = function () {
    var self = this;
    fetch('/api/onboarding/state')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.completed === false) {
          self._step = d.current_step || 0;
          self._renderStep(self._step);
          self._show();
        }
      })
      .catch(function () {
        /* silent — don't block app if onboarding API is unreachable */
      });
  };

  /**
   * Validate and advance to step *stepNum*.
   * PATCHes state when leaving each step.
   */
  Onboarding.prototype.nextStep = function (stepNum) {
    var self = this;
    var current = (typeof stepNum === 'number') ? stepNum : this._step;

    if (current === 0) {
      this._patchState({ current_step: 1, llm_configured: true, steps_done: ['llm'] });
    }

    if (current === 1) {
      var payload = { current_step: 2, steps_done: ['llm', 'canvas'] };
      if (this._selectedCanvases.length) {
        payload.canvas_enabled = this._selectedCanvases[0];
      }
      this._patchState(payload);
    }

    if (current < this._total - 1) {
      this._step = current + 1;
      this._renderStep(this._step);
    } else {
      this.markComplete();
    }
  };

  /**
   * Call /api/llm/health and update the status indicator.
   * Returns a Promise resolved with {ok: bool, message: string}.
   */
  Onboarding.prototype.testLlmConnection = function () {
    var btn    = document.getElementById('onb-test-btn');
    var status = document.getElementById('onb-test-status');
    if (btn)    btn.disabled = true;
    if (status) { status.textContent = 'Testing…'; status.className = 'onb-test-status'; }

    return fetch('/api/llm/health', { method: 'GET' })
      .then(function (r) {
        if (r.ok) return r.json().then(function () { return { ok: true, message: '✓ Connection successful' }; });
        return { ok: false, message: '⚠ Provider returned ' + r.status };
      })
      .catch(function () {
        return { ok: false, message: '⚠ Could not reach provider — check key/URL and retry' };
      })
      .then(function (result) {
        if (status) {
          status.textContent = result.message;
          status.className   = 'onb-test-status ' + (result.ok ? 'ok' : 'err');
        }
        if (btn) btn.disabled = false;
        return result;
      });
  };

  /**
   * Enable a canvas by POSTing to /api/components/enable.
   * Stores the key locally so nextStep() can include it in the PATCH.
   */
  Onboarding.prototype.enableCanvas = function (key) {
    if (key && this._selectedCanvases.indexOf(key) === -1) {
      this._selectedCanvases.push(key);
    }
    return fetch('/api/components/enable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: key, enabled: true })
    }).catch(function () { /* best-effort; wizard continues on error */ });
  };

  /**
   * Run an example IQE query and display the result.
   * PATCHes first_iqe_run=true on success.
   */
  Onboarding.prototype.runIqeExample = function () {
    var btn = document.getElementById('onb-iqe-btn');
    var out = document.getElementById('onb-iqe-result');
    var inp = document.getElementById('onb-iqe-input');
    var q   = inp ? (inp.value || '').trim() : 'What are the top security risks in my architecture?';
    if (!q) return Promise.resolve(null);

    if (btn) btn.disabled = true;
    if (out) { out.style.display = 'block'; out.textContent = 'Running query…'; }

    var self = this;
    return fetch('/api/iqe/dispatch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ canvas: 'core', query: q })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var text = d.answer || d.result || JSON.stringify(d, null, 2);
        if (out) out.textContent = text;
        self._patchState({ first_iqe_run: true });
        return d;
      })
      .catch(function (e) {
        if (out) out.textContent = 'Error: ' + e;
        return null;
      })
      .finally(function () {
        if (btn) btn.disabled = false;
      });
  };

  /** PATCH completed=true and hide the modal permanently. */
  Onboarding.prototype.markComplete = function () {
    var self = this;
    return this._patchState({ completed: true })
      .finally(function () { self._hide(); });
  };

  /* -------------------------------------------------------------------------
   * Singleton + auto-init
   * ---------------------------------------------------------------------- */

  var _instance = new Onboarding();
  root.ICDEV.Onboarding = _instance;

  document.addEventListener('DOMContentLoaded', function () {
    // Automated browsers (Playwright, Selenium, ...) set navigator.webdriver.
    // Skip the auto-popup there — E2E tests don't expect a blocking first-run
    // modal, and its hidden-but-DOM-present inputs otherwise collide with
    // broad `input[type="password"]`-style locators used by unrelated tests.
    if (navigator.webdriver) return;
    _instance.init();
  });

}(typeof window !== 'undefined' ? window : this));
