// CUI // SP-CTI
// ICDEV Dashboard — Keyboard Shortcuts Module (Zero Dependencies)
// Classification: CUI // SP-CTI
(function () {
    "use strict";

    // Ensure ICDEV namespace exists (api.js should have created it)
    var ICDEV = window.ICDEV || {};
    window.ICDEV = ICDEV;

    // ========================================================================
    // 1. CONFIGURATION
    // ========================================================================

    /** Chord timeout — user has this long after pressing `g` for the second key. */
    var CHORD_TIMEOUT_MS = 1500;

    /** Storage key for user-customized shortcuts. */
    var STORAGE_KEY = "icdev_shortcuts_config";

    /** Default navigation shortcuts: press `g` then the key within the timeout. */
    var DEFAULT_NAV_SHORTCUTS = {
        h: { path: "/",            label: "Home" },
        p: { path: "/projects",    label: "Projects" },
        a: { path: "/agents",      label: "Agents" },
        m: { path: "/monitoring",  label: "Monitoring" },
        q: { path: "/quick-paths", label: "Quick Paths" },
        w: { path: "/wizard",      label: "Wizard" },
        e: { path: "/events",      label: "Events" },
        n: { path: "/query",       label: "NLQ Query" },
        b: { path: "/batch",       label: "Batch Operations" }
    };

    /** Default direct (single-key) shortcuts shown in the help modal. */
    var DEFAULT_DIRECT_SHORTCUTS = {
        "?": "Show this help",
        "/": "Focus search / go to NLQ Query",
        "n": "Show notifications",
        "t": "Toggle guided tour",
        "r": "Refresh current page data"
    };

    /** Active shortcuts — defaults merged with user customizations from localStorage. */
    var NAV_SHORTCUTS = {};
    var DIRECT_SHORTCUTS = {};

    function loadShortcutConfig() {
        // Start from defaults
        var k;
        for (k in DEFAULT_NAV_SHORTCUTS) {
            if (DEFAULT_NAV_SHORTCUTS.hasOwnProperty(k)) {
                NAV_SHORTCUTS[k] = { path: DEFAULT_NAV_SHORTCUTS[k].path, label: DEFAULT_NAV_SHORTCUTS[k].label };
            }
        }
        for (k in DEFAULT_DIRECT_SHORTCUTS) {
            if (DEFAULT_DIRECT_SHORTCUTS.hasOwnProperty(k)) {
                DIRECT_SHORTCUTS[k] = DEFAULT_DIRECT_SHORTCUTS[k];
            }
        }
        // Merge user overrides from localStorage
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            if (raw) {
                var cfg = JSON.parse(raw);
                if (cfg.nav) {
                    for (k in cfg.nav) {
                        if (cfg.nav.hasOwnProperty(k) && cfg.nav[k].path) {
                            NAV_SHORTCUTS[k] = { path: cfg.nav[k].path, label: cfg.nav[k].label || cfg.nav[k].path };
                        }
                    }
                }
            }
        } catch (e) { /* noop — use defaults */ }
    }

    function saveShortcutConfig() {
        try {
            var cfg = { nav: {} };
            for (var k in NAV_SHORTCUTS) {
                if (NAV_SHORTCUTS.hasOwnProperty(k)) {
                    cfg.nav[k] = { path: NAV_SHORTCUTS[k].path, label: NAV_SHORTCUTS[k].label };
                }
            }
            localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
        } catch (e) { /* noop */ }
    }

    // ========================================================================
    // 2. STATE
    // ========================================================================

    var _chordActive = false;
    var _chordTimer = null;
    var _chordIndicator = null;
    var _helpModalOpen = false;
    var _helpOverlay = null;
    var _stylesInjected = false;
    var _previousFocus = null;

    // ========================================================================
    // 3. STYLES (injected via JS, matching dark government theme)
    // ========================================================================

    function injectStyles() {
        if (_stylesInjected) { return; }
        _stylesInjected = true;
        var style = document.createElement("style");
        style.setAttribute("data-icdev", "shortcuts");
        style.textContent =
            /* Chord indicator pill */
            ".icdev-chord-indicator{position:fixed;bottom:16px;left:16px;z-index:10001;" +
            "background:#0f0f1a;color:#a0a0b8;border:1px solid #3a3a55;border-radius:16px;" +
            "padding:4px 14px;font-family:monospace;font-size:.85rem;font-weight:600;" +
            "pointer-events:none;opacity:0;transform:translateY(8px);" +
            "transition:opacity .2s ease,transform .2s ease}" +
            ".icdev-chord-indicator.visible{opacity:1;transform:translateY(0)}" +
            /* Help modal overlay */
            ".icdev-shortcuts-overlay{position:fixed;inset:0;z-index:10002;" +
            "background:rgba(0,0,0,.7);display:flex;align-items:center;justify-content:center;" +
            "opacity:0;transition:opacity .2s ease;" +
            "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}" +
            ".icdev-shortcuts-overlay.visible{opacity:1}" +
            /* Help modal card */
            ".icdev-shortcuts-card{background:#16213e;border:1px solid #3a3a55;border-radius:8px;" +
            "padding:28px 32px 24px;max-width:680px;width:90%;max-height:80vh;overflow-y:auto;" +
            "color:#e0e0e0;box-shadow:0 8px 32px rgba(0,0,0,.5)}" +
            ".icdev-shortcuts-card:focus{outline:2px solid #4a90d9;outline-offset:2px}" +
            /* Header */
            ".icdev-shortcuts-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}" +
            ".icdev-shortcuts-title{font-size:1.15rem;font-weight:700;color:#e0e0e0;margin:0}" +
            ".icdev-shortcuts-close{background:none;border:1px solid #3a3a55;border-radius:4px;" +
            "color:#a0a0b8;font-size:.85rem;padding:4px 12px;cursor:pointer;font-family:inherit;" +
            "transition:background .15s ease,color .15s ease}" +
            ".icdev-shortcuts-close:hover,.icdev-shortcuts-close:focus{background:#2a2a40;color:#e0e0e0;outline:none}" +
            /* Two-column layout */
            ".icdev-shortcuts-columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}" +
            "@media(max-width:560px){.icdev-shortcuts-columns{grid-template-columns:1fr}}" +
            ".icdev-shortcuts-section-title{font-size:.78rem;font-weight:700;text-transform:uppercase;" +
            "letter-spacing:.06em;color:#4a90d9;margin:0 0 12px}" +
            /* Shortcut row */
            ".icdev-shortcut-row{display:flex;align-items:center;gap:12px;margin-bottom:10px}" +
            ".icdev-shortcut-keys{display:flex;align-items:center;gap:4px;flex-shrink:0;min-width:90px}" +
            ".icdev-shortcut-desc{color:#a0a0b8;font-size:.82rem;line-height:1.3}" +
            /* Key cap */
            ".icdev-key{display:inline-block;background:#0f0f1a;border:1px solid #3a3a55;" +
            "border-radius:4px;padding:2px 8px;font-family:monospace;font-size:.8rem;" +
            "font-weight:600;color:#e0e0e0;line-height:1.5;min-width:20px;text-align:center}" +
            /* Chord separator */
            ".icdev-key-sep{color:#6c6c80;font-size:.72rem;font-style:italic}";
        document.head.appendChild(style);
    }

    // ========================================================================
    // 4. UTILITY HELPERS
    // ========================================================================

    /** Returns true when the active element is a form field or editable region. */
    function isTypingContext() {
        var el = document.activeElement;
        if (!el) { return false; }
        var tag = el.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") { return true; }
        return !!el.isContentEditable;
    }

    /** Returns true when Ctrl, Alt, or Meta is held (Shift is allowed for `?`). */
    function hasBlockingModifier(e) {
        return e.ctrlKey || e.altKey || e.metaKey;
    }

    /** Delegate to shared ICDEV.escapeHTML (api.js). */
    function escapeHTML(str) {
        return ICDEV.escapeHTML ? ICDEV.escapeHTML(str) : String(str || "");
    }

    // ========================================================================
    // 5. CHORD INDICATOR
    // ========================================================================

    /** Lazily create the chord indicator pill and append to body. */
    function ensureChordIndicator() {
        if (_chordIndicator) { return _chordIndicator; }
        _chordIndicator = document.createElement("div");
        _chordIndicator.className = "icdev-chord-indicator";
        _chordIndicator.setAttribute("aria-live", "polite");
        _chordIndicator.setAttribute("aria-label", "Keyboard chord in progress");
        _chordIndicator.textContent = "g\u2026";
        document.body.appendChild(_chordIndicator);
        return _chordIndicator;
    }

    /** Show the chord indicator pill with a fade-in. */
    function showChordIndicator() {
        var pill = ensureChordIndicator();
        pill.textContent = "g\u2026";
        void pill.offsetHeight; // force reflow for transition
        pill.classList.add("visible");
    }

    /** Hide the chord indicator pill with a fade-out. */
    function hideChordIndicator() {
        if (_chordIndicator) { _chordIndicator.classList.remove("visible"); }
    }

    /** Enter chord mode: set state, show indicator, start timeout. */
    function enterChordMode() {
        _chordActive = true;
        showChordIndicator();
        _chordTimer = setTimeout(function () { cancelChordMode(); }, CHORD_TIMEOUT_MS);
    }

    /** Cancel chord mode: clear timer, hide indicator, reset state. */
    function cancelChordMode() {
        _chordActive = false;
        if (_chordTimer) { clearTimeout(_chordTimer); _chordTimer = null; }
        hideChordIndicator();
    }

    // ========================================================================
    // 6. SHORTCUTS HELP MODAL
    // ========================================================================

    function buildKeyCap(label) {
        return '<span class="icdev-key">' + escapeHTML(label) + "</span>";
    }

    function buildChordKeys(secondKey) {
        return buildKeyCap("g") +
            ' <span class="icdev-key-sep">then</span> ' +
            buildKeyCap(secondKey);
    }

    function buildShortcutRow(keysHTML, description) {
        return '<div class="icdev-shortcut-row">' +
            '<div class="icdev-shortcut-keys">' + keysHTML + "</div>" +
            '<div class="icdev-shortcut-desc">' + escapeHTML(description) + "</div>" +
            "</div>";
    }

    /** Build the full help modal DOM and return the overlay element. */
    function buildHelpModal() {
        // Navigation column
        var navHTML = '<div class="icdev-shortcuts-section-title">Navigation (g + key)</div>';
        var navKeys = Object.keys(NAV_SHORTCUTS);
        for (var i = 0; i < navKeys.length; i++) {
            var entry = NAV_SHORTCUTS[navKeys[i]];
            navHTML += buildShortcutRow(buildChordKeys(navKeys[i]), entry.label + " (" + entry.path + ")");
        }

        // Direct shortcuts column
        var directHTML = '<div class="icdev-shortcuts-section-title">Direct Shortcuts</div>';
        var directKeys = Object.keys(DIRECT_SHORTCUTS);
        for (var j = 0; j < directKeys.length; j++) {
            directHTML += buildShortcutRow(buildKeyCap(directKeys[j]), DIRECT_SHORTCUTS[directKeys[j]]);
        }
        directHTML += buildShortcutRow(buildKeyCap("Esc"), "Close any open modal or overlay");

        // Assemble overlay
        var overlay = document.createElement("div");
        overlay.className = "icdev-shortcuts-overlay";
        overlay.setAttribute("role", "dialog");
        overlay.setAttribute("aria-modal", "true");
        overlay.setAttribute("aria-label", "Keyboard shortcuts");

        var card = document.createElement("div");
        card.className = "icdev-shortcuts-card";
        card.setAttribute("tabindex", "-1");

        // Header with title and close button
        var header = document.createElement("div");
        header.className = "icdev-shortcuts-header";
        var title = document.createElement("h2");
        title.className = "icdev-shortcuts-title";
        title.textContent = "Keyboard Shortcuts";
        var closeBtn = document.createElement("button");
        closeBtn.className = "icdev-shortcuts-close";
        closeBtn.setAttribute("aria-label", "Close shortcuts help");
        closeBtn.textContent = "Close (Esc)";
        closeBtn.addEventListener("click", function () { ICDEV.hideShortcutsHelp(); });
        header.appendChild(title);
        header.appendChild(closeBtn);
        card.appendChild(header);

        // Two-column grid
        var columns = document.createElement("div");
        columns.className = "icdev-shortcuts-columns";
        var leftCol = document.createElement("div");
        leftCol.innerHTML = navHTML;
        var rightCol = document.createElement("div");
        rightCol.innerHTML = directHTML;
        columns.appendChild(leftCol);
        columns.appendChild(rightCol);
        card.appendChild(columns);
        overlay.appendChild(card);

        // Click on backdrop to close
        overlay.addEventListener("click", function (e) {
            if (e.target === overlay) { ICDEV.hideShortcutsHelp(); }
        });

        return overlay;
    }

    /** Open the shortcuts help modal. */
    ICDEV.showShortcutsHelp = function showShortcutsHelp() {
        if (_helpModalOpen) { return; }
        _helpModalOpen = true;
        _previousFocus = document.activeElement;

        if (!_helpOverlay) { _helpOverlay = buildHelpModal(); }
        document.body.appendChild(_helpOverlay);
        void _helpOverlay.offsetHeight; // force reflow
        _helpOverlay.classList.add("visible");

        var card = _helpOverlay.querySelector(".icdev-shortcuts-card");
        if (card) { card.focus(); }
        _helpOverlay.addEventListener("keydown", trapFocus);
    };

    /** Close the shortcuts help modal. */
    ICDEV.hideShortcutsHelp = function hideShortcutsHelp() {
        if (!_helpModalOpen || !_helpOverlay) { return; }
        _helpModalOpen = false;
        _helpOverlay.classList.remove("visible");
        _helpOverlay.removeEventListener("keydown", trapFocus);

        setTimeout(function () {
            if (_helpOverlay && _helpOverlay.parentNode && !_helpModalOpen) {
                _helpOverlay.parentNode.removeChild(_helpOverlay);
            }
        }, 220);

        if (_previousFocus && typeof _previousFocus.focus === "function") {
            _previousFocus.focus();
            _previousFocus = null;
        }
    };

    /** Focus trap: Tab and Shift+Tab cycle within the modal. */
    function trapFocus(e) {
        if (e.key !== "Tab" || !_helpOverlay) { return; }
        var focusable = _helpOverlay.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) { e.preventDefault(); return; }
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (e.shiftKey) {
            if (document.activeElement === first) { e.preventDefault(); last.focus(); }
        } else {
            if (document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    }

    // ========================================================================
    // 7. DIRECT SHORTCUT HANDLERS
    // ========================================================================

    /** Toggle the shortcuts help modal. */
    function handleShowHelp() {
        if (_helpModalOpen) { ICDEV.hideShortcutsHelp(); }
        else { ICDEV.showShortcutsHelp(); }
    }

    /** Focus the search input if present, otherwise navigate to /query. */
    function handleFocusSearch() {
        var searchInput = document.querySelector(
            'input[aria-label="Search table rows"], input[type="search"], input[placeholder*="Search"]'
        );
        if (searchInput) {
            searchInput.focus();
            searchInput.select();
        } else {
            window.location.href = "/query";
        }
    }

    /** Fetch notifications from /api/notifications and show as toasts. */
    function handleNotifications() {
        if (typeof ICDEV.fetchJSON !== "function") { return; }
        ICDEV.fetchJSON("/api/notifications").then(function (data) {
            if (!data) {
                if (typeof ICDEV.showNotification === "function") {
                    ICDEV.showNotification("No new notifications", "info");
                }
                return;
            }
            var items = data.notifications || data.items || [];
            if (items.length === 0) {
                if (typeof ICDEV.showNotification === "function") {
                    ICDEV.showNotification("No new notifications", "info");
                }
                return;
            }
            for (var i = 0; i < Math.min(items.length, 5); i++) {
                var item = items[i];
                var msg = item.message || item.text || String(item);
                var lvl = item.level || item.type || "info";
                if (typeof ICDEV.showNotification === "function") {
                    ICDEV.showNotification(msg, lvl);
                }
            }
        });
    }

    /** Toggle the guided tour if ICDEV.startTour is available. */
    function handleToggleTour() {
        if (typeof ICDEV.startTour === "function") {
            ICDEV.startTour();
        } else if (typeof ICDEV.showNotification === "function") {
            ICDEV.showNotification("Guided tour is not available on this page", "info");
        }
    }

    /** Refresh page data via ICDEV API helpers or fall back to full reload. */
    function handleRefresh() {
        if (typeof ICDEV.fetchJSON !== "function") {
            window.location.reload();
            return;
        }
        var refreshed = false;
        if (typeof ICDEV.refreshAlertBadge === "function") {
            ICDEV.refreshAlertBadge();
            refreshed = true;
        }
        if (typeof ICDEV.refreshHealthStatus === "function") {
            ICDEV.refreshHealthStatus();
            refreshed = true;
        }
        if (refreshed && typeof ICDEV.showNotification === "function") {
            ICDEV.showNotification("Page data refreshed", "success", 2000);
        } else {
            window.location.reload();
        }
    }

    // ========================================================================
    // 8. MAIN KEYDOWN HANDLER
    // ========================================================================

    function onKeyDown(e) {
        var key = e.key;

        // Escape always closes modals/overlays
        if (key === "Escape") {
            if (_helpModalOpen) { ICDEV.hideShortcutsHelp(); e.preventDefault(); return; }
            if (_chordActive) { cancelChordMode(); e.preventDefault(); return; }
            return; // let Escape propagate for other overlays (tour, etc.)
        }

        // Block shortcuts when modifier keys are held
        if (hasBlockingModifier(e)) {
            if (_chordActive) { cancelChordMode(); }
            return;
        }

        // Block shortcuts when typing in form fields
        if (isTypingContext()) {
            if (_chordActive) { cancelChordMode(); }
            return;
        }

        // When help modal is open, only Escape (above) and Tab (trapped) matter
        if (_helpModalOpen) { return; }

        // Chord mode: waiting for the second key
        if (_chordActive) {
            cancelChordMode();
            var navEntry = NAV_SHORTCUTS[key];
            if (navEntry) { e.preventDefault(); window.location.href = navEntry.path; }
            return;
        }

        // Enter chord mode on `g`
        if (key === "g") { e.preventDefault(); enterChordMode(); return; }

        // Direct shortcuts
        if (key === "?") { e.preventDefault(); handleShowHelp(); return; }
        if (key === "/") { e.preventDefault(); handleFocusSearch(); return; }
        if (key === "n") { e.preventDefault(); handleNotifications(); return; }
        if (key === "t") { e.preventDefault(); handleToggleTour(); return; }
        if (key === "r") { e.preventDefault(); handleRefresh(); return; }
    }

    // ========================================================================
    // 9. INITIALIZATION
    // ========================================================================

    /**
     * Set a custom navigation shortcut. Persists to localStorage.
     * @param {string} key  Single character key (after pressing `g`)
     * @param {string} path URL path to navigate to
     * @param {string} label Display label for the help modal
     * @example ICDEV.setShortcut("x", "/settings", "Settings");
     */
    ICDEV.setShortcut = function (key, path, label) {
        if (!key || key.length !== 1 || !path) return;
        NAV_SHORTCUTS[key] = { path: path, label: label || path };
        saveShortcutConfig();
        // Rebuild help modal on next show
        _helpOverlay = null;
    };

    /**
     * Remove a custom navigation shortcut. Persists to localStorage.
     * @param {string} key  Single character key to remove
     */
    ICDEV.removeShortcut = function (key) {
        if (!key) return;
        delete NAV_SHORTCUTS[key];
        saveShortcutConfig();
        _helpOverlay = null;
    };

    /**
     * Reset all shortcuts to defaults. Clears localStorage.
     */
    ICDEV.resetShortcuts = function () {
        try { localStorage.removeItem(STORAGE_KEY); } catch (e) { /* noop */ }
        loadShortcutConfig();
        _helpOverlay = null;
    };

    /**
     * Get current shortcut configuration (for debugging / display).
     * @returns {{nav: Object, direct: Object}}
     */
    ICDEV.getShortcuts = function () {
        return { nav: NAV_SHORTCUTS, direct: DIRECT_SHORTCUTS };
    };

    function init() {
        loadShortcutConfig();
        injectStyles();
        document.addEventListener("keydown", onKeyDown);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

})();
