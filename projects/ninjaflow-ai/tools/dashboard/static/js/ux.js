/**
 * ICDEV Dashboard - UX Utilities Module
 * Comprehensive UX helpers for non-technical government users ("GI proof").
 * Extends window.ICDEV (created by api.js).
 *
 * Features:
 *   1. Glossary tooltip system (DoD/compliance acronyms)
 *   2. Timestamp formatting (human-readable, relative)
 *   3. Accessibility helpers (icons, ARIA, focus)
 *   4. Progress pipeline component
 *   5. Notification system (toast)
 *   6. Score display helper
 */

(function () {
    "use strict";

    // Ensure ICDEV namespace exists (api.js should have created it)
    var ICDEV = window.ICDEV || {};
    window.ICDEV = ICDEV;

    // ========================================================================
    // 1. GLOSSARY TOOLTIP SYSTEM
    // ========================================================================

    /**
     * Dictionary of government/DoD/compliance acronyms with plain-English
     * definitions. Used by the tooltip system and <abbr> auto-fill.
     */
    ICDEV.glossary = {
        "POA&M":   "Plan of Action & Milestones \u2014 your fix-it list for security gaps with deadlines",
        "POAM":    "Plan of Action & Milestones \u2014 your fix-it list for security gaps with deadlines",
        "STIG":    "Security Technical Implementation Guide \u2014 DoD security checklist for systems",
        "SBOM":    "Software Bill of Materials \u2014 inventory of all software components and dependencies",
        "SSP":     "System Security Plan \u2014 the master document describing your system\u2019s security",
        "CAT-I":   "Category I \u2014 Critical severity finding that must be fixed before deployment",
        "CAT-II":  "Category II \u2014 High severity finding with a 30-day fix deadline",
        "CAT-III": "Category III \u2014 Medium severity finding with a 90-day fix deadline",
        "CUI":     "Controlled Unclassified Information \u2014 sensitive but not classified data",
        "IL2":     "Impact Level 2 \u2014 Public/non-sensitive data, commercial cloud OK",
        "IL4":     "Impact Level 4 \u2014 CUI data, requires AWS GovCloud",
        "IL5":     "Impact Level 5 \u2014 CUI data, requires dedicated GovCloud infrastructure",
        "IL6":     "Impact Level 6 \u2014 SECRET data, requires SIPR network and air-gapped systems",
        "ATO":     "Authorization to Operate \u2014 formal approval to run your system in production",
        "cATO":    "Continuous Authorization to Operate \u2014 automated ongoing security monitoring",
        "FedRAMP": "Federal Risk and Authorization Management Program \u2014 cloud security standard",
        "CMMC":    "Cybersecurity Maturity Model Certification \u2014 DoD contractor security standard",
        "NIST":    "National Institute of Standards and Technology \u2014 sets security frameworks",
        "OSCAL":   "Open Security Controls Assessment Language \u2014 machine-readable compliance format",
        "ISSO":    "Information System Security Officer \u2014 person responsible for system security",
        "RMF":     "Risk Management Framework \u2014 the 6-step process for getting your ATO",
        "eMASS":   "Enterprise Mission Assurance Support Service \u2014 DoD\u2019s system for tracking ATOs",
        "A2A":     "Agent-to-Agent \u2014 how ICDEV\u2019s AI agents communicate with each other",
        "SAFe":    "Scaled Agile Framework \u2014 method for organizing large development teams",
        "WSJF":    "Weighted Shortest Job First \u2014 prioritization formula: value divided by effort",
        "BDD":     "Behavior-Driven Development \u2014 writing tests in plain English before coding",
        "TDD":     "Test-Driven Development \u2014 write failing tests first, then write code to pass them",
        "PI":      "Program Increment \u2014 a 10-week development cycle in SAFe",
        "COA":     "Course of Action \u2014 a proposed approach with cost, timeline, and risk tradeoffs",
        "SCRM":    "Supply Chain Risk Management \u2014 assessing vendor and dependency risks",
        "ISA":     "Interconnection Security Agreement \u2014 contract governing data sharing between systems",
        "CVE":     "Common Vulnerabilities and Exposures \u2014 a known security vulnerability with an ID number",
        "RTM":     "Requirements Traceability Matrix \u2014 maps requirements to tests and code",
        "FIPS":    "Federal Information Processing Standards \u2014 government encryption and security standards",
        "SAST":    "Static Application Security Testing \u2014 scanning code for vulnerabilities without running it",
        "IaC":     "Infrastructure as Code \u2014 defining servers and networks in code files (Terraform, Ansible)",
        "ReqIF":   "Requirements Interchange Format \u2014 standard format for sharing requirements between tools",
        "SysML":   "Systems Modeling Language \u2014 visual language for describing complex system architectures",
        "MBSE":    "Model-Based Systems Engineering \u2014 designing systems using models instead of documents",
        "DES":     "Digital Engineering Strategy \u2014 DoD mandate to use digital tools for engineering"
    };

    // --- Tooltip element (shared singleton) ---

    var _tooltipEl = null;
    var _tooltipHideTimer = null;

    /**
     * Lazily create the shared tooltip DOM element and inject its styles.
     * The tooltip is appended to document.body and absolutely positioned.
     */
    function ensureTooltipElement() {
        if (_tooltipEl) {
            return _tooltipEl;
        }

        // Inject tooltip CSS once
        var style = document.createElement("style");
        style.textContent = [
            ".icdev-tooltip {",
            "  position: absolute;",
            "  z-index: 9999;",
            "  max-width: 340px;",
            "  padding: 10px 14px;",
            "  background: #1a1a2e;",
            "  color: #e0e0e0;",
            "  border: 1px solid #3a3a55;",
            "  border-radius: 6px;",
            "  font-size: 0.82rem;",
            "  line-height: 1.45;",
            "  box-shadow: 0 4px 16px rgba(0,0,0,0.45);",
            "  pointer-events: none;",
            "  opacity: 0;",
            "  transition: opacity 0.15s ease;",
            "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;",
            "}",
            ".icdev-tooltip.visible {",
            "  opacity: 1;",
            "}",
            ".icdev-tooltip::before {",
            "  content: '';",
            "  position: absolute;",
            "  bottom: -6px;",
            "  left: 50%;",
            "  transform: translateX(-50%);",
            "  width: 0;",
            "  height: 0;",
            "  border-left: 6px solid transparent;",
            "  border-right: 6px solid transparent;",
            "  border-top: 6px solid #3a3a55;",
            "}",
            ".icdev-tooltip .icdev-tooltip-icon {",
            "  display: inline-block;",
            "  width: 16px;",
            "  height: 16px;",
            "  line-height: 16px;",
            "  text-align: center;",
            "  border-radius: 50%;",
            "  background: #4a90d9;",
            "  color: #fff;",
            "  font-size: 0.7rem;",
            "  font-weight: 700;",
            "  font-style: italic;",
            "  margin-right: 8px;",
            "  vertical-align: middle;",
            "  flex-shrink: 0;",
            "}",
            ".icdev-tooltip .icdev-tooltip-body {",
            "  display: inline;",
            "  vertical-align: middle;",
            "}",
            // Pipeline styles
            ".icdev-pipeline {",
            "  display: flex;",
            "  align-items: flex-start;",
            "  gap: 0;",
            "  overflow-x: auto;",
            "  padding: 16px 8px;",
            "}",
            ".icdev-pipeline-step {",
            "  display: flex;",
            "  flex-direction: column;",
            "  align-items: center;",
            "  flex: 1;",
            "  min-width: 120px;",
            "  position: relative;",
            "}",
            ".icdev-pipeline-step .step-connector {",
            "  position: absolute;",
            "  top: 14px;",
            "  left: calc(50% + 16px);",
            "  right: calc(-50% + 16px);",
            "  height: 3px;",
            "  background: #2a2a40;",
            "}",
            ".icdev-pipeline-step.step-completed .step-connector {",
            "  background: #28a745;",
            "}",
            ".icdev-pipeline-step.step-active .step-connector {",
            "  background: linear-gradient(90deg, #4a90d9 0%, #2a2a40 100%);",
            "}",
            ".icdev-pipeline-step .step-dot {",
            "  width: 28px;",
            "  height: 28px;",
            "  border-radius: 50%;",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: center;",
            "  font-size: 0.75rem;",
            "  font-weight: 700;",
            "  z-index: 1;",
            "  border: 2px solid transparent;",
            "  margin-bottom: 8px;",
            "}",
            ".icdev-pipeline-step.step-completed .step-dot {",
            "  background: #28a745;",
            "  color: #fff;",
            "  border-color: #28a745;",
            "}",
            ".icdev-pipeline-step.step-active .step-dot {",
            "  background: #16213e;",
            "  color: #4a90d9;",
            "  border-color: #4a90d9;",
            "  box-shadow: 0 0 8px rgba(74,144,217,0.5);",
            "  animation: icdev-pulse 1.5s infinite;",
            "}",
            ".icdev-pipeline-step.step-pending .step-dot {",
            "  background: #16213e;",
            "  color: #6c6c80;",
            "  border-color: #2a2a40;",
            "}",
            ".icdev-pipeline-step.step-blocked .step-dot {",
            "  background: rgba(220,53,69,0.15);",
            "  color: #dc3545;",
            "  border-color: #dc3545;",
            "}",
            ".icdev-pipeline-step.step-skipped .step-dot {",
            "  background: #16213e;",
            "  color: #6c6c80;",
            "  border-color: #2a2a40;",
            "  text-decoration: line-through;",
            "}",
            ".icdev-pipeline-step .step-name {",
            "  font-size: 0.78rem;",
            "  font-weight: 600;",
            "  color: #e0e0e0;",
            "  text-align: center;",
            "  line-height: 1.3;",
            "}",
            ".icdev-pipeline-step.step-pending .step-name,",
            ".icdev-pipeline-step.step-skipped .step-name {",
            "  color: #6c6c80;",
            "}",
            ".icdev-pipeline-step .step-detail {",
            "  font-size: 0.7rem;",
            "  color: #a0a0b8;",
            "  text-align: center;",
            "  margin-top: 4px;",
            "  max-width: 140px;",
            "}",
            "@keyframes icdev-pulse {",
            "  0%, 100% { box-shadow: 0 0 4px rgba(74,144,217,0.3); }",
            "  50% { box-shadow: 0 0 12px rgba(74,144,217,0.6); }",
            "}",
            // Notification toast styles
            ".icdev-toast-container {",
            "  position: fixed;",
            "  bottom: 24px;",
            "  right: 24px;",
            "  z-index: 10000;",
            "  display: flex;",
            "  flex-direction: column-reverse;",
            "  gap: 10px;",
            "  max-width: 400px;",
            "}",
            ".icdev-toast {",
            "  display: flex;",
            "  align-items: flex-start;",
            "  gap: 10px;",
            "  padding: 14px 18px;",
            "  border-radius: 6px;",
            "  font-size: 0.85rem;",
            "  line-height: 1.4;",
            "  cursor: pointer;",
            "  box-shadow: 0 4px 16px rgba(0,0,0,0.4);",
            "  border: 1px solid transparent;",
            "  opacity: 0;",
            "  transform: translateX(40px);",
            "  transition: opacity 0.25s ease, transform 0.25s ease;",
            "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;",
            "}",
            ".icdev-toast.visible {",
            "  opacity: 1;",
            "  transform: translateX(0);",
            "}",
            ".icdev-toast.removing {",
            "  opacity: 0;",
            "  transform: translateX(40px);",
            "}",
            ".icdev-toast-success {",
            "  background: #0d2818;",
            "  color: #28a745;",
            "  border-color: #28a745;",
            "}",
            ".icdev-toast-error {",
            "  background: #2a0a0a;",
            "  color: #dc3545;",
            "  border-color: #dc3545;",
            "}",
            ".icdev-toast-warning {",
            "  background: #2a2000;",
            "  color: #ffc107;",
            "  border-color: #ffc107;",
            "}",
            ".icdev-toast-info {",
            "  background: #0a1a2e;",
            "  color: #4a90d9;",
            "  border-color: #4a90d9;",
            "}",
            ".icdev-toast .toast-icon {",
            "  font-size: 1.1rem;",
            "  flex-shrink: 0;",
            "  line-height: 1;",
            "  margin-top: 1px;",
            "}",
            ".icdev-toast .toast-message {",
            "  flex: 1;",
            "}"
        ].join("\n");
        document.head.appendChild(style);

        // Create tooltip element
        _tooltipEl = document.createElement("div");
        _tooltipEl.className = "icdev-tooltip";
        _tooltipEl.setAttribute("role", "tooltip");
        _tooltipEl.setAttribute("id", "icdev-glossary-tooltip");
        document.body.appendChild(_tooltipEl);

        return _tooltipEl;
    }

    /**
     * Position the tooltip above the target element and show it.
     * Falls back to below if there is not enough room above.
     */
    function showTooltip(targetEl, text) {
        var tip = ensureTooltipElement();
        tip.innerHTML = '<span class="icdev-tooltip-icon">i</span><span class="icdev-tooltip-body">' + escapeHTML(text) + "</span>";

        // Reset arrow position
        tip.style.removeProperty("top");
        tip.style.removeProperty("left");
        tip.classList.add("visible");

        // Calculate position
        var rect = targetEl.getBoundingClientRect();
        var tipRect = tip.getBoundingClientRect();
        var scrollX = window.pageXOffset || document.documentElement.scrollLeft;
        var scrollY = window.pageYOffset || document.documentElement.scrollTop;

        var left = rect.left + scrollX + (rect.width / 2) - (tipRect.width / 2);
        var top = rect.top + scrollY - tipRect.height - 10;

        // Clamp left so tooltip does not overflow viewport
        if (left < 8) {
            left = 8;
        } else if (left + tipRect.width > document.documentElement.clientWidth - 8) {
            left = document.documentElement.clientWidth - tipRect.width - 8;
        }

        // If not enough room above, show below
        if (top < scrollY + 4) {
            top = rect.bottom + scrollY + 10;
            tip.style.setProperty("--arrow-side", "top");
        } else {
            tip.style.removeProperty("--arrow-side");
        }

        tip.style.left = left + "px";
        tip.style.top = top + "px";

        // Clear any pending hide
        if (_tooltipHideTimer) {
            clearTimeout(_tooltipHideTimer);
            _tooltipHideTimer = null;
        }
    }

    /**
     * Hide the tooltip with a small delay to prevent flicker.
     */
    function hideTooltip() {
        if (_tooltipHideTimer) {
            clearTimeout(_tooltipHideTimer);
        }
        _tooltipHideTimer = setTimeout(function () {
            if (_tooltipEl) {
                _tooltipEl.classList.remove("visible");
            }
            _tooltipHideTimer = null;
        }, 100);
    }

    /** Delegate to shared ICDEV.escapeHTML (api.js). */
    function escapeHTML(str) {
        return ICDEV.escapeHTML ? ICDEV.escapeHTML(str) : String(str || "");
    }

    /**
     * Initialize the glossary tooltip system.
     * - Attach hover/focus tooltips to all [data-glossary] elements.
     * - Auto-fill empty <abbr> titles from the glossary dictionary.
     */
    ICDEV.initGlossary = function initGlossary() {
        var glossaryEls = document.querySelectorAll("[data-glossary]");

        glossaryEls.forEach(function (el) {
            var term = el.getAttribute("data-glossary");
            var definition = ICDEV.glossary[term];
            if (!definition) {
                return;
            }

            // Make focusable for keyboard accessibility if not already
            if (!el.getAttribute("tabindex")) {
                el.setAttribute("tabindex", "0");
            }
            el.setAttribute("aria-describedby", "icdev-glossary-tooltip");

            // Style hint: subtle dotted underline
            el.style.borderBottom = "1px dotted #6c6c80";
            el.style.cursor = "help";

            el.addEventListener("mouseenter", function () {
                showTooltip(el, definition);
            });
            el.addEventListener("mouseleave", function () {
                hideTooltip();
            });
            el.addEventListener("focus", function () {
                showTooltip(el, definition);
            });
            el.addEventListener("blur", function () {
                hideTooltip();
            });
        });

        // Auto-fill <abbr> elements whose title is empty and text matches a glossary key
        var abbrEls = document.querySelectorAll("abbr");
        abbrEls.forEach(function (abbr) {
            var existingTitle = (abbr.getAttribute("title") || "").trim();
            if (existingTitle) {
                return;
            }
            var text = (abbr.textContent || "").trim();
            var definition = ICDEV.glossary[text];
            if (definition) {
                abbr.setAttribute("title", definition);
            }
        });
    };

    // ========================================================================
    // 2. TIMESTAMP FORMATTING
    // ========================================================================

    var MONTH_NAMES_SHORT = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
    ];

    /**
     * Format an ISO-8601 string to "Feb 18, 2026 at 2:30 PM".
     * @param {string} isoString - ISO-8601 date string
     * @returns {string} Formatted timestamp, or empty string on invalid input
     */
    ICDEV.formatTimestamp = function formatTimestamp(isoString) {
        if (!isoString) {
            return "";
        }
        var d = new Date(isoString);
        if (isNaN(d.getTime())) {
            return isoString;
        }

        var month = MONTH_NAMES_SHORT[d.getMonth()];
        var day = d.getDate();
        var year = d.getFullYear();
        var hours = d.getHours();
        var minutes = d.getMinutes();
        var ampm = hours >= 12 ? "PM" : "AM";

        hours = hours % 12;
        if (hours === 0) {
            hours = 12;
        }
        var minuteStr = minutes < 10 ? "0" + minutes : String(minutes);

        return month + " " + day + ", " + year + " at " + hours + ":" + minuteStr + " " + ampm;
    };

    /**
     * Format an ISO-8601 string to "Feb 18, 2026".
     * @param {string} isoString - ISO-8601 date string
     * @returns {string} Short formatted date
     */
    ICDEV.formatTimestampShort = function formatTimestampShort(isoString) {
        if (!isoString) {
            return "";
        }
        var d = new Date(isoString);
        if (isNaN(d.getTime())) {
            return isoString;
        }

        var month = MONTH_NAMES_SHORT[d.getMonth()];
        var day = d.getDate();
        var year = d.getFullYear();

        return month + " " + day + ", " + year;
    };

    /**
     * Format an ISO-8601 string to a relative time string like "3 hours ago".
     * @param {string} isoString - ISO-8601 date string
     * @returns {string} Relative time string
     */
    ICDEV.formatTimeAgo = function formatTimeAgo(isoString) {
        if (!isoString) {
            return "";
        }
        var d = new Date(isoString);
        if (isNaN(d.getTime())) {
            return isoString;
        }

        var now = Date.now();
        var diffMs = now - d.getTime();

        // Handle future dates
        if (diffMs < 0) {
            return "just now";
        }

        var diffSec = Math.floor(diffMs / 1000);
        var diffMin = Math.floor(diffSec / 60);
        var diffHour = Math.floor(diffMin / 60);
        var diffDay = Math.floor(diffHour / 24);
        var diffWeek = Math.floor(diffDay / 7);
        var diffMonth = Math.floor(diffDay / 30);
        var diffYear = Math.floor(diffDay / 365);

        if (diffSec < 60) {
            return "just now";
        }
        if (diffMin < 2) {
            return "1 minute ago";
        }
        if (diffMin < 60) {
            return diffMin + " minutes ago";
        }
        if (diffHour < 2) {
            return "1 hour ago";
        }
        if (diffHour < 24) {
            return diffHour + " hours ago";
        }
        if (diffDay < 2) {
            return "1 day ago";
        }
        if (diffDay < 7) {
            return diffDay + " days ago";
        }
        if (diffWeek < 2) {
            return "1 week ago";
        }
        if (diffWeek < 5) {
            return diffWeek + " weeks ago";
        }
        if (diffMonth < 2) {
            return "1 month ago";
        }
        if (diffMonth < 12) {
            return diffMonth + " months ago";
        }
        if (diffYear < 2) {
            return "1 year ago";
        }
        return diffYear + " years ago";
    };

    /**
     * Scan the DOM for elements with data-timestamp attribute and replace
     * their text content with formatted timestamps.
     *
     * Supported data-timestamp-format values:
     *   "ago"   -> uses formatTimeAgo
     *   "short" -> uses formatTimestampShort
     *   (default) -> uses formatTimestamp
     */
    ICDEV.initTimestamps = function initTimestamps() {
        var elements = document.querySelectorAll("[data-timestamp]");

        elements.forEach(function (el) {
            var isoString = el.getAttribute("data-timestamp");
            var format = (el.getAttribute("data-timestamp-format") || "").toLowerCase();
            var formatted;

            if (format === "ago") {
                formatted = ICDEV.formatTimeAgo(isoString);
            } else if (format === "short") {
                formatted = ICDEV.formatTimestampShort(isoString);
            } else {
                formatted = ICDEV.formatTimestamp(isoString);
            }

            if (formatted) {
                el.textContent = formatted;
                // Store the original ISO value as a title for full precision on hover
                el.setAttribute("title", isoString);
            }
        });
    };

    // ========================================================================
    // 3. ACCESSIBILITY HELPERS
    // ========================================================================

    /**
     * Badge class-to-icon mapping. Each group maps to a prefix icon string.
     */
    var BADGE_ICON_MAP = {
        success:   "\u2713 ",  // checkmark
        active:    "\u2713 ",
        healthy:   "\u2713 ",
        resolved:  "\u2713 ",
        approved:  "\u2713 ",
        completed: "\u2713 ",
        closed:    "\u2713 ",
        inactive:  "\u2715 ",  // X mark
        error:     "\u2715 ",
        critical:  "\u2715 ",
        warning:   "\u26A0 ",  // warning triangle
        degraded:  "\u26A0 ",
        firing:    "\u26A0 ",
        open:      "\u26A0 ",
        info:      "\u25CF ",  // filled circle
        pending:   "\u25CF ",
        draft:     "\u25CF ",
        submitted: "\u25CF "
    };

    /**
     * Marker attribute to prevent double-initialization on badges.
     */
    var BADGE_INITIALIZED_ATTR = "data-icdev-a11y";

    /**
     * Initialize accessibility enhancements across the page:
     * - Prepend status icons to .badge elements
     * - Add ARIA attributes to status dots and health banners
     */
    ICDEV.initAccessibility = function initAccessibility() {
        // --- Badge icons ---
        var badges = document.querySelectorAll(".badge");
        badges.forEach(function (badge) {
            // Skip already-processed badges
            if (badge.getAttribute(BADGE_INITIALIZED_ATTR)) {
                return;
            }

            var classList = badge.className.split(/\s+/);
            var iconPrefix = null;

            for (var i = 0; i < classList.length; i++) {
                var cls = classList[i];
                // Extract the suffix after "badge-"
                if (cls.indexOf("badge-") === 0) {
                    var suffix = cls.substring(6);
                    if (BADGE_ICON_MAP[suffix]) {
                        iconPrefix = BADGE_ICON_MAP[suffix];
                        break;
                    }
                }
            }

            if (iconPrefix) {
                // Only prepend if the text does not already start with the icon
                var currentText = badge.textContent || "";
                if (currentText.indexOf(iconPrefix.trim()) !== 0) {
                    badge.textContent = iconPrefix + currentText;
                }
                badge.setAttribute(BADGE_INITIALIZED_ATTR, "1");
            }
        });

        // --- Status dots: role="img" + aria-label ---
        var dots = document.querySelectorAll(".status-dot");
        dots.forEach(function (dot) {
            dot.setAttribute("role", "img");
            // Derive label from color class
            if (dot.classList.contains("green")) {
                dot.setAttribute("aria-label", "Status: healthy");
            } else if (dot.classList.contains("red")) {
                dot.setAttribute("aria-label", "Status: critical");
            } else if (dot.classList.contains("yellow")) {
                dot.setAttribute("aria-label", "Status: warning");
            } else {
                dot.setAttribute("aria-label", "Status indicator");
            }
        });

        // --- Health banners: role="alert" ---
        var banners = document.querySelectorAll(".health-banner");
        banners.forEach(function (banner) {
            banner.setAttribute("role", "alert");
        });
    };

    // ========================================================================
    // 4. PROGRESS PIPELINE COMPONENT
    // ========================================================================

    /**
     * Status-to-dot-symbol mapping.
     */
    var PIPELINE_DOT_SYMBOLS = {
        completed: "\u2713",   // checkmark
        active:    "\u25B6",   // play triangle
        pending:   "\u2022",   // bullet
        blocked:   "\u2715",   // X
        skipped:   "\u2014"    // em-dash
    };

    /**
     * Create a horizontal progress pipeline inside the specified container.
     *
     * @param {string} containerId - DOM id of the container element
     * @param {Array<{name: string, status: string, detail?: string}>} steps -
     *   Pipeline steps. Status: "completed"|"active"|"pending"|"blocked"|"skipped"
     */
    ICDEV.createProgressPipeline = function createProgressPipeline(containerId, steps) {
        var container = document.getElementById(containerId);
        if (!container) {
            console.error("[ICDEV UX] Pipeline container not found: #" + containerId);
            return;
        }

        if (!steps || steps.length === 0) {
            container.innerHTML = '<div class="text-muted" style="padding:16px;">No pipeline steps defined.</div>';
            return;
        }

        var pipelineEl = document.createElement("div");
        pipelineEl.className = "icdev-pipeline";
        pipelineEl.setAttribute("role", "list");
        pipelineEl.setAttribute("aria-label", "Progress pipeline");

        steps.forEach(function (step, index) {
            var status = step.status || "pending";
            var stepEl = document.createElement("div");
            stepEl.className = "icdev-pipeline-step step-" + status;
            stepEl.setAttribute("role", "listitem");

            // Connector line (between dots, not on last step)
            if (index < steps.length - 1) {
                var connector = document.createElement("div");
                connector.className = "step-connector";
                connector.setAttribute("aria-hidden", "true");
                stepEl.appendChild(connector);
            }

            // Dot
            var dot = document.createElement("div");
            dot.className = "step-dot";
            dot.setAttribute("aria-hidden", "true");
            dot.textContent = PIPELINE_DOT_SYMBOLS[status] || "\u2022";
            stepEl.appendChild(dot);

            // Step name
            var nameEl = document.createElement("div");
            nameEl.className = "step-name";
            nameEl.textContent = step.name || "Step " + (index + 1);
            stepEl.appendChild(nameEl);

            // Detail text
            if (step.detail) {
                var detailEl = document.createElement("div");
                detailEl.className = "step-detail";
                detailEl.textContent = step.detail;
                stepEl.appendChild(detailEl);
            }

            // Accessible label
            var ariaLabel = step.name + ": " + status;
            if (step.detail) {
                ariaLabel += " \u2014 " + step.detail;
            }
            stepEl.setAttribute("aria-label", ariaLabel);

            pipelineEl.appendChild(stepEl);
        });

        container.innerHTML = "";
        container.appendChild(pipelineEl);
    };

    // ========================================================================
    // 5. NOTIFICATION SYSTEM
    // ========================================================================

    var _toastContainer = null;

    /**
     * Icon mapping for notification types.
     */
    var TOAST_ICONS = {
        success: "\u2713",   // checkmark
        error:   "\u2715",   // X
        warning: "\u26A0",   // warning
        info:    "\u2139"    // info
    };

    /**
     * Ensure the toast container exists in the DOM.
     */
    function ensureToastContainer() {
        if (_toastContainer) {
            return _toastContainer;
        }
        _toastContainer = document.createElement("div");
        _toastContainer.className = "icdev-toast-container";
        _toastContainer.setAttribute("aria-live", "polite");
        _toastContainer.setAttribute("aria-label", "Notifications");
        document.body.appendChild(_toastContainer);
        return _toastContainer;
    }

    /**
     * Show a toast notification.
     *
     * @param {string} message  - The notification message
     * @param {string} [type]   - "success"|"error"|"warning"|"info" (default: "info")
     * @param {number} [duration] - Auto-dismiss after this many ms (default: 5000)
     */
    ICDEV.showNotification = function showNotification(message, type, duration) {
        var validTypes = { success: 1, error: 1, warning: 1, info: 1 };
        type = validTypes[type] ? type : "info";
        duration = typeof duration === "number" && duration > 0 ? duration : 5000;

        var container = ensureToastContainer();

        var toast = document.createElement("div");
        toast.className = "icdev-toast icdev-toast-" + type;
        toast.setAttribute("role", "status");

        var iconSpan = document.createElement("span");
        iconSpan.className = "toast-icon";
        iconSpan.setAttribute("aria-hidden", "true");
        iconSpan.textContent = TOAST_ICONS[type] || TOAST_ICONS.info;
        toast.appendChild(iconSpan);

        var msgSpan = document.createElement("span");
        msgSpan.className = "toast-message";
        msgSpan.textContent = message;
        toast.appendChild(msgSpan);

        container.appendChild(toast);

        // Trigger reflow, then animate in
        // eslint-disable-next-line no-unused-expressions
        toast.offsetHeight;
        toast.classList.add("visible");

        var dismissed = false;

        function dismiss() {
            if (dismissed) {
                return;
            }
            dismissed = true;
            toast.classList.remove("visible");
            toast.classList.add("removing");
            setTimeout(function () {
                if (toast.parentNode) {
                    toast.parentNode.removeChild(toast);
                }
            }, 300);
        }

        // Click to dismiss
        toast.addEventListener("click", dismiss);

        // Auto-dismiss
        setTimeout(dismiss, duration);
    };

    // ========================================================================
    // 6. SCORE DISPLAY HELPER
    // ========================================================================

    /**
     * Generate an HTML string representing a score with color-coded status.
     *
     * @param {number} value     - The score value (0-100 expected, or 0.0-1.0)
     * @param {number} threshold - The pass threshold (same scale as value)
     * @param {string} [label]   - Optional label text shown after the percentage
     * @returns {string} HTML string with icon, colored percentage, and label
     *
     * Color logic:
     *   value >= threshold           -> green  + checkmark
     *   value >= threshold * 0.8     -> yellow + warning
     *   value < threshold * 0.8      -> red    + X
     */
    ICDEV.formatScore = function formatScore(value, threshold, label) {
        // Normalize to percentage for display (handles both 0-1 and 0-100 inputs)
        var displayValue = value;
        var displayThreshold = threshold;

        // If values appear to be in 0-1 range, convert to percentages for display
        if (threshold <= 1 && threshold > 0) {
            displayValue = Math.round(value * 100);
            displayThreshold = threshold;
            // Keep comparison in original scale
        } else {
            displayValue = Math.round(value);
        }

        var icon, color, statusText;
        var almostThreshold = threshold * 0.8;

        if (value >= threshold) {
            icon = "\u2713";
            color = "#28a745";
            statusText = label || "Ready";
        } else if (value >= almostThreshold) {
            icon = "\u26A0";
            color = "#ffc107";
            statusText = label || "Almost ready";
        } else {
            icon = "\u2715";
            color = "#dc3545";
            statusText = label || "Needs work";
        }

        return '<span style="color:' + color + '; font-weight:600;">' +
               icon + " " + displayValue + "% \u2014 " + escapeHTML(statusText) +
               "</span>";
    };

    // ========================================================================
    // 7. INITIALIZATION
    // ========================================================================

    /**
     * Run all UX initializers on DOMContentLoaded.
     */
    function initAll() {
        ICDEV.initGlossary();
        ICDEV.initTimestamps();
        ICDEV.initAccessibility();
    }

    // If DOM is already loaded (e.g., script loaded with defer/async after load),
    // run immediately. Otherwise, wait for DOMContentLoaded.
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initAll);
    } else {
        initAll();
    }

})();
