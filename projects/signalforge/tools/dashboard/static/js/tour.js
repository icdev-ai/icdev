// CUI // SP-CTI
/**
 * ICDEV Dashboard - Interactive Onboarding Tour Module
 * Guides first-time users through key dashboard UI elements with a step-by-step
 * spotlight walkthrough. Zero external dependencies. Air-gap safe.
 * Extends window.ICDEV (created by api.js, extended by ux.js).
 *
 * Features:
 *   1. First-visit detection via localStorage (icdev_tour_completed)
 *   2. Welcome overlay on first visit with Start/Skip options
 *   3. SVG spotlight overlay with cutout around each target element
 *   4. Tooltip card with step counter, title, description, and nav buttons
 *   5. WCAG accessible: focus trap, keyboard nav (Escape/Arrow/Tab), aria attrs
 *   6. Smooth scroll to off-screen elements; graceful skip of missing targets
 *   7. Resize-safe repositioning; animated transitions
 *
 * Public API:
 *   ICDEV.startTour()  - Manually start or restart the tour
 *   ICDEV.resetTour()  - Clear localStorage so tour shows again on next visit
 *
 * @module tour
 */
(function () {
    "use strict";

    var ICDEV = window.ICDEV || {};
    window.ICDEV = ICDEV;

    // ── Constants ────────────────────────────────────────────────────────────
    var STORAGE_KEY = "icdev_tour_completed";
    var STORAGE_STEP_KEY = "icdev_tour_last_step";
    var P = "icdev-tour"; // ID/class prefix

    /** Built-in tour steps (fallback if /api/tour/steps fetch fails — air-gap safe). */
    var DEFAULT_STEPS = [
        { selector: ".navbar", title: "Navigation Bar",
          desc: "Navigate between pages: Home, Projects, Agents, Monitoring, Quick Paths, and the Getting Started wizard." },
        { selector: ".card-grid", title: "Summary Cards",
          desc: "At-a-glance metrics: project counts, active agents, firing alerts, and compliance status." },
        { selector: ".chart-grid", title: "Visual Dashboards",
          desc: "Visual dashboards: compliance posture, alert trends, project status, and agent health charts." },
        { selector: ".table-container", title: "Data Tables",
          desc: "Detailed data tables with search, sort, filter, and CSV export capabilities." },
        { selector: "#role-select", title: "Role Selector",
          desc: "Switch views: Program Manager, Developer, ISSO, or Contracting Officer to see role-relevant information." },
        { selector: "a[href*='quick-paths'], a[href*='/quick-paths']", title: "Quick Paths",
          desc: "Pre-built workflow shortcuts for common tasks like ATO generation, project creation, and security scanning." }
    ];

    /** Active steps — populated from config endpoint or defaults. */
    var STEPS = DEFAULT_STEPS;
    var _stepsLoaded = false;

    /** Fetch tour steps from /api/tour/steps. Falls back to built-in defaults on error. */
    function loadStepsFromConfig(cb) {
        if (_stepsLoaded) { cb(); return; }
        fetch("/api/tour/steps")
        .then(function (resp) {
            if (!resp.ok) throw new Error("fetch failed");
            return resp.json();
        })
        .then(function (data) {
            if (data.steps && data.steps.length > 0) {
                STEPS = data.steps;
            }
            _stepsLoaded = true;
            cb();
        })
        .catch(function () {
            // Graceful fallback to built-in defaults (air-gap safe)
            STEPS = DEFAULT_STEPS;
            _stepsLoaded = true;
            cb();
        });
    }

    /** Dark government theme palette */
    var T = {
        card: "#16213e", blue: "#4a90d9", blueHover: "#5a9de6",
        text: "#e0e0e0", sec: "#a0a0b8", muted: "#6c6c80", border: "#2a2a40",
        font: "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"
    };
    var PAD = 8, RAD = 8; // spotlight padding & radius

    // ── State ────────────────────────────────────────────────────────────────
    var _step = -1, _steps = [], _overlay = null, _tip = null;
    var _active = false, _styled = false, _resizeTimer = null;

    // ── Style injection (same pattern as ux.js) ─────────────────────────────
    /** Inject all tour CSS into <head>. Called once. */
    function injectStyles() {
        if (_styled) return;
        _styled = true;
        var s = document.createElement("style");
        s.setAttribute("data-icdev-tour", "1");
        s.textContent =
            /* Overlay */ "." + P + "-overlay{position:fixed;inset:0;z-index:10000;pointer-events:auto;" +
            "transition:opacity .25s;opacity:0}" +
            "." + P + "-overlay.visible{opacity:1}" +
            "." + P + "-overlay svg{width:100%;height:100%;display:block}" +
            /* Tooltip card */ "." + P + "-tip{position:fixed;z-index:10001;width:340px;max-width:calc(100vw - 32px);" +
            "background:" + T.card + ";border:1px solid " + T.blue + ";border-radius:8px;" +
            "box-shadow:0 8px 32px rgba(0,0,0,.5);padding:20px;font-family:" + T.font + ";" +
            "color:" + T.text + ";opacity:0;transition:opacity .2s,transform .2s;transform:translateY(8px)}" +
            "." + P + "-tip.visible{opacity:1;transform:translateY(0)}" +
            /* Counter */ "." + P + "-ctr{font-size:.72rem;font-weight:600;color:" + T.blue + ";" +
            "margin-bottom:8px;letter-spacing:.5px;text-transform:uppercase}" +
            /* Title */ "." + P + "-ttl{font-size:1rem;font-weight:700;margin:0 0 8px;color:" + T.text + ";line-height:1.3}" +
            /* Description */ "." + P + "-dsc{font-size:.85rem;line-height:1.5;color:" + T.sec + ";margin:0 0 16px}" +
            /* Button row */ "." + P + "-btns{display:flex;align-items:center;gap:8px;flex-wrap:wrap}" +
            /* Button base */ "." + P + "-btn{border:none;border-radius:4px;padding:7px 16px;font-size:.8rem;" +
            "font-weight:600;cursor:pointer;font-family:" + T.font + ";transition:background .15s;white-space:nowrap}" +
            "." + P + "-btn:focus-visible{outline:2px solid " + T.blue + ";outline-offset:2px}" +
            /* Primary */ "." + P + "-btn-p{background:" + T.blue + ";color:#fff}" +
            "." + P + "-btn-p:hover{background:" + T.blueHover + "}" +
            /* Secondary */ "." + P + "-btn-s{background:transparent;color:" + T.sec + ";border:1px solid " + T.border + "}" +
            "." + P + "-btn-s:hover{background:rgba(74,144,217,.1);color:" + T.text + "}" +
            /* Skip link */ "." + P + "-skip{background:none;border:none;color:" + T.muted + ";font-size:.75rem;" +
            "cursor:pointer;margin-left:auto;padding:4px 8px;font-family:" + T.font + ";" +
            "text-decoration:underline;text-underline-offset:2px}" +
            "." + P + "-skip:hover{color:" + T.sec + "}" +
            "." + P + "-skip:focus-visible{outline:2px solid " + T.blue + ";outline-offset:2px}" +
            /* Welcome overlay */ "." + P + "-welcome{position:fixed;inset:0;z-index:10002;display:flex;" +
            "align-items:center;justify-content:center;background:rgba(0,0,0,.7);" +
            "opacity:0;transition:opacity .3s}" +
            "." + P + "-welcome.visible{opacity:1}" +
            "." + P + "-wcard{background:" + T.card + ";border:1px solid " + T.blue + ";border-radius:10px;" +
            "padding:32px 36px;max-width:440px;width:calc(100vw - 32px);text-align:center;" +
            "box-shadow:0 12px 48px rgba(0,0,0,.6);font-family:" + T.font + "}" +
            "." + P + "-wcard h2{color:" + T.text + ";margin:0 0 12px;font-size:1.3rem}" +
            "." + P + "-wcard p{color:" + T.sec + ";font-size:.9rem;line-height:1.55;margin:0 0 24px}";
        document.head.appendChild(s);
    }

    // ── DOM helpers ──────────────────────────────────────────────────────────
    /** Create an SVG element in SVG namespace */
    function svgEl(tag, attrs) {
        var el = document.createElementNS("http://www.w3.org/2000/svg", tag);
        if (attrs) { Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); }); }
        return el;
    }

    /** Build the SVG overlay with a mask cutout for the spotlight effect */
    function createOverlay() {
        var div = document.createElement("div");
        div.className = P + "-overlay";
        div.id = P + "-overlay";
        div.setAttribute("aria-hidden", "true");

        var svg = svgEl("svg", { xmlns: "http://www.w3.org/2000/svg" });
        var defs = svgEl("defs");
        var mask = svgEl("mask", { id: P + "-mask" });
        mask.appendChild(svgEl("rect", { x: "0", y: "0", width: "100%", height: "100%", fill: "white" }));
        mask.appendChild(svgEl("rect", { id: P + "-hole", x: "0", y: "0", width: "0", height: "0",
            rx: String(RAD), ry: String(RAD), fill: "black" }));
        defs.appendChild(mask);
        svg.appendChild(defs);
        svg.appendChild(svgEl("rect", { x: "0", y: "0", width: "100%", height: "100%",
            fill: "rgba(0,0,0,0.6)", mask: "url(#" + P + "-mask)" }));
        div.appendChild(svg);
        return div;
    }

    /** Build the tooltip card with counter, title, description, and nav buttons */
    function createTooltip() {
        var el = document.createElement("div");
        el.className = P + "-tip";
        el.id = P + "-tip";
        el.setAttribute("role", "dialog");
        el.setAttribute("aria-modal", "true");
        el.setAttribute("aria-label", "Tour step");

        /* Counter, title, description */
        var ctr = document.createElement("div"); ctr.className = P + "-ctr"; ctr.id = P + "-ctr";
        var ttl = document.createElement("h3");  ttl.className = P + "-ttl"; ttl.id = P + "-ttl";
        var dsc = document.createElement("p");   dsc.className = P + "-dsc"; dsc.id = P + "-dsc";
        el.appendChild(ctr); el.appendChild(ttl); el.appendChild(dsc);

        /* Buttons: Previous, Next/Finish, Skip Tour */
        var row = document.createElement("div"); row.className = P + "-btns";

        var prev = document.createElement("button");
        prev.className = P + "-btn " + P + "-btn-s"; prev.id = P + "-prev";
        prev.textContent = "Previous"; prev.setAttribute("aria-label", "Go to previous step");
        prev.addEventListener("click", goPrev);

        var next = document.createElement("button");
        next.className = P + "-btn " + P + "-btn-p"; next.id = P + "-next";
        next.textContent = "Next"; next.setAttribute("aria-label", "Go to next step");
        next.addEventListener("click", goNext);

        var skip = document.createElement("button");
        skip.className = P + "-skip"; skip.id = P + "-skip";
        skip.textContent = "Skip Tour"; skip.setAttribute("aria-label", "Skip the tour and close");
        skip.addEventListener("click", pauseTour);

        row.appendChild(prev); row.appendChild(next); row.appendChild(skip);
        el.appendChild(row);
        return el;
    }

    // ── Spotlight & positioning ──────────────────────────────────────────────
    /** Move the SVG mask cutout to frame targetEl */
    function updateSpotlight(targetEl) {
        var hole = document.getElementById(P + "-hole");
        if (!hole) return;
        var r = targetEl.getBoundingClientRect();
        hole.setAttribute("x", String(r.left - PAD));
        hole.setAttribute("y", String(r.top - PAD));
        hole.setAttribute("width", String(r.width + PAD * 2));
        hole.setAttribute("height", String(r.height + PAD * 2));
    }

    /** Position tooltip below (preferred) or above the target, clamped to viewport */
    function positionTooltip(targetEl) {
        if (!_tip) return;
        var r = targetEl.getBoundingClientRect();
        var tr = _tip.getBoundingClientRect();
        var vw = window.innerWidth, vh = window.innerHeight, gap = 12;
        var top, left;

        var below = vh - r.bottom - PAD, above = r.top - PAD;
        if (below >= tr.height + gap) top = r.bottom + PAD + gap;
        else if (above >= tr.height + gap) top = r.top - PAD - gap - tr.height;
        else top = vh - tr.height - 16;

        left = r.left + r.width / 2 - tr.width / 2;
        if (left < 16) left = 16;
        else if (left + tr.width > vw - 16) left = vw - tr.width - 16;

        _tip.style.top = Math.round(top) + "px";
        _tip.style.left = Math.round(left) + "px";
    }

    /** Smooth-scroll targetEl into view, then call cb. Calls cb immediately if already visible. */
    function scrollTo(targetEl, cb) {
        var r = targetEl.getBoundingClientRect();
        if (r.top >= 0 && r.bottom <= window.innerHeight) { cb(); return; }
        targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
        var settled = 0, lastY = window.scrollY;
        var iv = setInterval(function () {
            if (window.scrollY === lastY) settled++; else settled = 0;
            lastY = window.scrollY;
            if (settled >= 3) { clearInterval(iv); cb(); }
        }, 60);
    }

    // ── Step rendering ───────────────────────────────────────────────────────
    /** Render current step: update tooltip content, spotlight target, set focus */
    function renderStep() {
        if (_step < 0 || _step >= _steps.length) return;
        var step = _steps[_step];
        var el = document.querySelector(step.selector);
        if (!el) { goNext(); return; } // target vanished; skip

        var $ = function (id) { return document.getElementById(P + "-" + id); };
        var ctr = $("ctr"), ttl = $("ttl"), dsc = $("dsc"), prev = $("prev"), next = $("next");
        if (ctr) ctr.textContent = (_step + 1) + " of " + _steps.length;
        if (ttl) ttl.textContent = step.title;
        if (dsc) dsc.textContent = step.desc;
        if (prev) prev.style.display = _step === 0 ? "none" : "";
        var isLast = _step === _steps.length - 1;
        if (next) {
            next.textContent = isLast ? "Finish" : "Next";
            next.setAttribute("aria-label", isLast ? "Finish the tour" : "Go to next step");
        }
        if (_tip) _tip.setAttribute("aria-label",
            "Tour step " + (_step + 1) + " of " + _steps.length + ": " + step.title);

        scrollTo(el, function () {
            updateSpotlight(el);
            _tip.classList.add("visible");
            positionTooltip(el);
            var focus = $("next");
            if (focus) focus.focus();
        });
    }

    // ── Navigation ───────────────────────────────────────────────────────────
    /** Advance to next step, or complete if on last step */
    function goNext() {
        if (_step >= _steps.length - 1) { completeTour(); return; }
        _step++;
        if (_tip) _tip.classList.remove("visible");
        setTimeout(renderStep, 80);
    }

    /** Go back one step */
    function goPrev() {
        if (_step <= 0) return;
        _step--;
        if (_tip) _tip.classList.remove("visible");
        setTimeout(renderStep, 80);
    }

    /** Mark tour complete in localStorage and tear down */
    function completeTour() {
        try {
            localStorage.setItem(STORAGE_KEY, "1");
            localStorage.removeItem(STORAGE_STEP_KEY);
        } catch (e) { /* air-gapped fallback */ }
        teardown();
    }

    /** Save current step to localStorage so tour can resume later. */
    function saveTourStep() {
        if (_step >= 0 && _step < _steps.length) {
            try { localStorage.setItem(STORAGE_STEP_KEY, String(_step)); } catch (e) { /* noop */ }
        }
    }

    /** Close tour mid-progress — save step position, mark incomplete. */
    function pauseTour() {
        saveTourStep();
        teardown();
    }

    // ── Lifecycle ────────────────────────────────────────────────────────────
    /** Remove all tour DOM elements and event listeners */
    function teardown() {
        _active = false; _step = -1;
        [_overlay, _tip].forEach(function (el) {
            if (!el) return;
            el.classList.remove("visible");
            var ref = el;
            setTimeout(function () { if (ref.parentNode) ref.parentNode.removeChild(ref); }, 300);
        });
        _overlay = null; _tip = null;
        var w = document.getElementById(P + "-welcome");
        if (w && w.parentNode) w.parentNode.removeChild(w);
        document.removeEventListener("keydown", onKeydown);
        window.removeEventListener("resize", onResize);
    }

    /** Return STEPS filtered to only those with matching DOM targets */
    function resolveSteps() {
        return STEPS.filter(function (s) { return !!document.querySelector(s.selector); });
    }

    /** Show welcome overlay. onStart/onSkip/onResume callbacks fired on button click. */
    function showWelcome(onStart, onSkip, onResume) {
        var savedStep = -1;
        try {
            var s = localStorage.getItem(STORAGE_STEP_KEY);
            if (s !== null) savedStep = parseInt(s, 10);
        } catch (e) { /* noop */ }
        var hasResume = !isNaN(savedStep) && savedStep > 0;

        var wrap = document.createElement("div");
        wrap.className = P + "-welcome"; wrap.id = P + "-welcome";

        var card = document.createElement("div");
        card.className = P + "-wcard";
        card.setAttribute("role", "dialog");
        card.setAttribute("aria-modal", "true");
        card.setAttribute("aria-label", "Welcome to the ICDEV Dashboard");

        var h = document.createElement("h2"); h.textContent = "Welcome to ICDEV Dashboard";
        var p = document.createElement("p");
        if (hasResume) {
            p.textContent = "You left off at step " + (savedStep + 1) + ". " +
                "Resume where you were, start over, or skip the tour entirely.";
        } else {
            p.textContent = "Take a quick tour to learn where everything is. " +
                "We will highlight the key areas of the dashboard in 6 short steps.";
        }
        card.appendChild(h); card.appendChild(p);

        var row = document.createElement("div");
        row.style.cssText = "display:flex;gap:12px;justify-content:center;flex-wrap:wrap";

        function fadeAndCall(fn) {
            wrap.classList.remove("visible");
            setTimeout(function () { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); fn(); }, 300);
        }

        if (hasResume) {
            var resumeBtn = document.createElement("button");
            resumeBtn.className = P + "-btn " + P + "-btn-p";
            resumeBtn.textContent = "Resume Tour";
            resumeBtn.setAttribute("aria-label", "Resume tour from step " + (savedStep + 1));
            resumeBtn.addEventListener("click", function () {
                fadeAndCall(function () { onResume(savedStep); });
            });
            row.appendChild(resumeBtn);
        }

        var startBtn = document.createElement("button");
        startBtn.className = P + "-btn " + (hasResume ? P + "-btn-s" : P + "-btn-p");
        startBtn.textContent = hasResume ? "Start Over" : "Start Tour";
        startBtn.setAttribute("aria-label", hasResume ? "Start the tour from the beginning" : "Start the dashboard tour");
        startBtn.addEventListener("click", function () { fadeAndCall(onStart); });

        var skipBtn = document.createElement("button");
        skipBtn.className = P + "-btn " + P + "-btn-s";
        skipBtn.textContent = "Skip";
        skipBtn.setAttribute("aria-label", "Skip the tour");
        skipBtn.addEventListener("click", function () { fadeAndCall(onSkip); });

        row.appendChild(startBtn); row.appendChild(skipBtn);
        card.appendChild(row); wrap.appendChild(card);
        document.body.appendChild(wrap);
        void wrap.offsetHeight; // reflow
        wrap.classList.add("visible");
        (hasResume ? (row.firstChild) : startBtn).focus();

        /* Focus trap within welcome dialog (WCAG) */
        var allBtns = row.querySelectorAll("button");
        wrap.addEventListener("keydown", function (e) {
            if (e.key === "Tab") {
                var first = allBtns[0], last = allBtns[allBtns.length - 1];
                if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
            }
            if (e.key === "Escape") skipBtn.click();
        });
    }

    // ── Keyboard & resize handlers ───────────────────────────────────────────
    /**
     * Keyboard handler during tour: Escape=close, ArrowRight=next, ArrowLeft=prev,
     * Tab=focus trap within tooltip (WCAG).
     * @param {KeyboardEvent} e
     */
    function onKeydown(e) {
        if (!_active) return;
        if (e.key === "Escape")     { e.preventDefault(); pauseTour(); return; }
        if (e.key === "ArrowRight") { e.preventDefault(); goNext(); return; }
        if (e.key === "ArrowLeft")  { e.preventDefault(); goPrev(); return; }
        /* Focus trap within tooltip */
        if (e.key === "Tab" && _tip) {
            var btns = _tip.querySelectorAll("button:not([style*='display: none']):not([style*='display:none'])");
            if (btns.length === 0) return;
            var first = btns[0], last = btns[btns.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    }

    /** Debounced resize handler — reposition spotlight + tooltip on window resize */
    function onResize() {
        if (_resizeTimer) clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(function () {
            _resizeTimer = null;
            if (!_active || _step < 0 || _step >= _steps.length) return;
            var el = document.querySelector(_steps[_step].selector);
            if (el) { updateSpotlight(el); positionTooltip(el); }
        }, 100);
    }

    // ── Public API ───────────────────────────────────────────────────────────

    /**
     * Start or restart the onboarding tour. Tears down any running tour first.
     * Skips the welcome overlay when called manually (for "Restart Tour" UX).
     * @param {number} [fromStep=0] Optional step index to resume from.
     * @example
     * ICDEV.startTour();    // Start from beginning
     * ICDEV.startTour(3);   // Resume from step 4
     */
    ICDEV.startTour = function startTour(fromStep) {
        if (_active) teardown();
        injectStyles();

        // Ensure steps are loaded from config before starting
        loadStepsFromConfig(function () {
            _steps = resolveSteps();
            if (_steps.length === 0) return; // no targetable elements on this page
            _active = true;
            _step = (typeof fromStep === "number" && fromStep >= 0 && fromStep < _steps.length) ? fromStep : 0;
            // Clear saved step when explicitly starting
            try { localStorage.removeItem(STORAGE_STEP_KEY); } catch (e) { /* noop */ }

            _overlay = createOverlay();
            document.body.appendChild(_overlay);
            void _overlay.offsetHeight;
            _overlay.classList.add("visible");
            _overlay.addEventListener("click", function (e) {
                if (e.target === _overlay || e.target.tagName === "svg" || e.target.tagName === "rect") pauseTour();
            });

            _tip = createTooltip();
            document.body.appendChild(_tip);
            document.addEventListener("keydown", onKeydown);
            window.addEventListener("resize", onResize);
            renderStep();
        });
    };

    /**
     * Clear localStorage so the welcome overlay appears on the next page visit.
     * @example
     * ICDEV.resetTour();
     * location.reload(); // Tour will show again
     */
    ICDEV.resetTour = function resetTour() {
        try { localStorage.removeItem(STORAGE_KEY); } catch (e) { /* air-gapped fallback */ }
    };

    // ── Auto-initialization ──────────────────────────────────────────────────
    /** On DOMContentLoaded, show welcome overlay if tour not yet completed or paused */
    function autoInit() {
        var done = false;
        try { done = localStorage.getItem(STORAGE_KEY) === "1"; } catch (e) { done = true; }
        if (done) return;
        /* Short delay so dashboard renders first, then preload steps from config */
        setTimeout(function () {
            injectStyles();
            loadStepsFromConfig(function () {
                showWelcome(
                    function () { ICDEV.startTour(0); },
                    function () { completeTour(); },
                    function (savedStep) { ICDEV.startTour(savedStep); }
                );
            });
        }, 500);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", autoInit);
    } else {
        autoInit();
    }
})();
