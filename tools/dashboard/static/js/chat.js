// CUI // SP-CTI
// ICDEV Requirements Chat — client-side logic
// Manages conversational intake, file upload, readiness tracking,
// BDD preview, framework tags, and post-export actions.

(function () {
    "use strict";

    var ns = window.ICDEV || {};
    var sessionId = null;
    var readinessTimer = null;
    var coaTimer = null;
    var coasLoaded = false;
    var buildTimer = null;
    var turnCount = 0;

    // Framework display name mapping
    var FRAMEWORK_NAMES = {
        fedramp_moderate: "FedRAMP Moderate",
        fedramp_high: "FedRAMP High",
        cmmc_l2: "CMMC L2",
        cmmc_l3: "CMMC L3",
        nist_800_171: "NIST 800-171",
        nist_800_207: "NIST 800-207 (ZTA)",
        cnssi_1253: "CNSSI 1253",
        hipaa: "HIPAA",
        pci_dss: "PCI DSS",
        cjis: "CJIS",
        soc2: "SOC 2",
        iso_27001: "ISO 27001",
        hitrust: "HITRUST"
    };

    // -----------------------------------------------------------------------
    // Initialisation
    // -----------------------------------------------------------------------

    function init() {
        var cfg = window._CHAT_CONFIG || {};
        sessionId = cfg.sessionId || null;

        // Wire up input
        var input = document.getElementById("chat-input");
        var sendBtn = document.getElementById("chat-send-btn");
        if (input) {
            input.addEventListener("input", function () {
                autoGrow(input);
                sendBtn.disabled = !input.value.trim();
            });
            input.addEventListener("keydown", function (e) {
                if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (input.value.trim()) ns.chatSend();
                }
            });
        }

        // Wire up file upload button
        var uploadBtn = document.getElementById("chat-upload-btn");
        var fileInput = document.getElementById("chat-file-input");
        if (uploadBtn && fileInput) {
            uploadBtn.addEventListener("click", function () { fileInput.click(); });
            fileInput.addEventListener("change", function () {
                if (fileInput.files.length > 0) ns.chatUpload(fileInput.files);
                fileInput.value = "";
            });
        }

        // Wire up drag-and-drop
        var messagesPane = document.querySelector(".chat-messages-pane");
        var dropZone = document.getElementById("chat-upload-zone");
        if (messagesPane && dropZone) {
            var dragCounter = 0;
            messagesPane.addEventListener("dragenter", function (e) {
                e.preventDefault();
                dragCounter++;
                dropZone.classList.add("active");
            });
            messagesPane.addEventListener("dragleave", function () {
                dragCounter--;
                if (dragCounter <= 0) { dropZone.classList.remove("active"); dragCounter = 0; }
            });
            messagesPane.addEventListener("dragover", function (e) { e.preventDefault(); });
            messagesPane.addEventListener("drop", function (e) {
                e.preventDefault();
                dragCounter = 0;
                dropZone.classList.remove("active");
                if (e.dataTransfer.files.length > 0) ns.chatUpload(e.dataTransfer.files);
            });
        }

        // Display framework tags from wizard config
        displayFrameworkTags(cfg.wizardFrameworks || "");

        // If no session yet, create one from wizard params
        if (!sessionId && cfg.wizardGoal) {
            ns.chatCreateSession(
                cfg.wizardGoal, cfg.wizardRole, cfg.wizardClassification
            );
        } else if (sessionId) {
            startReadinessPolling();
            startCoaPolling();
            scrollToBottom();
            ns.chatRefreshReadiness();
            ns.chatRefreshCoas();
            ns.chatRefreshComplexity();
            loadTechniques();
            // Check for existing build pipeline
            ns.chatRefreshBuild();
        }
    }

    // -----------------------------------------------------------------------
    // Framework tags display
    // -----------------------------------------------------------------------

    function displayFrameworkTags(frameworksStr) {
        if (!frameworksStr) return;
        var section = document.getElementById("frameworks-section");
        var container = document.getElementById("framework-tags");
        if (!section || !container) return;

        var frameworks = frameworksStr.split(",").filter(function (f) { return f.trim(); });
        if (frameworks.length === 0) return;

        section.style.display = "block";
        container.innerHTML = "";
        for (var i = 0; i < frameworks.length; i++) {
            var fwId = frameworks[i].trim();
            var tag = document.createElement("span");
            tag.className = "framework-tag";
            tag.textContent = FRAMEWORK_NAMES[fwId] || fwId;
            container.appendChild(tag);
        }
    }

    // -----------------------------------------------------------------------
    // Session creation
    // -----------------------------------------------------------------------

    ns.chatCreateSession = function (goal, role, classification) {
        var cfg = window._CHAT_CONFIG || {};
        var sendBtn = document.getElementById("chat-send-btn");
        if (sendBtn) sendBtn.disabled = true;

        var frameworks = (cfg.wizardFrameworks || "").split(",").filter(function (f) { return f.trim(); });

        fetch("/api/intake/session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                goal: goal || "build",
                role: role || "developer",
                classification: classification || "il4",
                customer_name: "Dashboard User",
                frameworks: frameworks,
                custom_role_name: cfg.customRoleName || "",
                custom_role_description: cfg.customRoleDesc || "",
            }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Error creating session: " + data.error);
                return;
            }
            sessionId = data.session_id;

            // Replace default welcome with persona-specific welcome
            if (data.message) {
                var messagesEl = document.getElementById("chat-messages");
                if (messagesEl) {
                    // Clear the default welcome and show persona welcome
                    messagesEl.innerHTML = "";
                    appendBubble("analyst", data.message);
                }
            }

            // Update URL without reload
            history.replaceState(null, "", "/chat/" + sessionId);
            startReadinessPolling();
            startCoaPolling();
            ns.chatRefreshComplexity();
            loadTechniques();
            if (sendBtn) sendBtn.disabled = false;
        })
        .catch(function (err) {
            appendSystemMessage("Connection error: " + err.message);
        });
    };

    // -----------------------------------------------------------------------
    // Send message
    // -----------------------------------------------------------------------

    ns.chatSend = function () {
        var input = document.getElementById("chat-input");
        var message = input ? input.value.trim() : "";
        if (!message || !sessionId) return;

        // Append customer bubble immediately
        appendBubble("customer", message);
        input.value = "";
        autoGrow(input);
        document.getElementById("chat-send-btn").disabled = true;

        // Show typing indicator
        var typingId = appendTypingIndicator();

        fetch("/api/intake/turn", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId, message: message }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            removeTypingIndicator(typingId);
            if (data.error) {
                appendSystemMessage("Error: " + data.error);
                return;
            }
            // Append analyst response
            appendBubble("analyst", data.analyst_response || "Thank you. Tell me more about your requirements.");

            // Update stats
            turnCount = data.turn_number || turnCount + 2;
            updateStat("stat-turns", turnCount);
            if (data.total_requirements !== undefined) {
                updateStat("stat-requirements", data.total_requirements);
            }

            // Update readiness if provided
            if (data.readiness_update) {
                updateReadinessDisplay(data.readiness_update);
            }

            // Render BDD previews if provided
            if (data.bdd_previews && data.bdd_previews.length > 0) {
                renderBddPreviews(data.bdd_previews);
            }

            // Refresh readiness score and complexity
            ns.chatRefreshReadiness();
            ns.chatRefreshComplexity();
        })
        .catch(function (err) {
            removeTypingIndicator(typingId);
            appendSystemMessage("Connection error: " + err.message);
        });
    };

    // -----------------------------------------------------------------------
    // BDD Preview rendering
    // -----------------------------------------------------------------------

    function renderBddPreviews(previews) {
        var section = document.getElementById("bdd-preview-section");
        var list = document.getElementById("bdd-preview-list");
        if (!section || !list) return;

        section.style.display = "block";
        for (var i = 0; i < previews.length; i++) {
            var item = document.createElement("div");
            item.className = "bdd-preview-item";

            var label = document.createElement("div");
            label.className = "bdd-preview-label";
            label.textContent = previews[i].requirement;

            var pre = document.createElement("pre");
            pre.className = "bdd-preview-block";
            pre.textContent = previews[i].gherkin;

            item.appendChild(label);
            item.appendChild(pre);
            list.appendChild(item);
        }
    }

    // -----------------------------------------------------------------------
    // File upload
    // -----------------------------------------------------------------------

    ns.chatUpload = function (files) {
        if (!sessionId) {
            appendSystemMessage("Please wait for session to initialize before uploading.");
            return;
        }
        for (var i = 0; i < files.length; i++) {
            uploadSingleFile(files[i]);
        }
    };

    function uploadSingleFile(file) {
        appendSystemMessage("Uploading " + file.name + "...");

        var formData = new FormData();
        formData.append("session_id", sessionId);
        formData.append("file", file);

        fetch("/api/intake/upload", {
            method: "POST",
            body: formData,
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Upload failed: " + data.error);
                return;
            }
            var msg = "Uploaded " + file.name;
            if (data.requirements_extracted > 0) {
                msg += " — extracted " + data.requirements_extracted + " requirement(s)";
            }
            appendSystemMessage(msg);

            // Update document count
            var docEl = document.getElementById("stat-documents");
            if (docEl) {
                var cur = parseInt(docEl.textContent, 10) || 0;
                docEl.textContent = cur + 1;
            }

            // Refresh readiness
            ns.chatRefreshReadiness();
        })
        .catch(function (err) {
            appendSystemMessage("Upload error: " + err.message);
        });
    }

    // -----------------------------------------------------------------------
    // Readiness
    // -----------------------------------------------------------------------

    ns.chatRefreshReadiness = function () {
        if (!sessionId) return;

        fetch("/api/intake/readiness/" + sessionId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) return;
            updateReadinessDisplay(data);
        })
        .catch(function () { /* silent */ });
    };

    function updateReadinessDisplay(data) {
        // readiness_scorer returns overall_score; process_turn returns overall
        var overall = data.overall_score || data.overall || 0;
        var pct = Math.round(overall * 100);

        // Update gauge arc (circumference = 2 * PI * 50 = 314)
        var arc = document.getElementById("readiness-arc");
        if (arc) {
            var offset = 314 - (314 * overall);
            arc.setAttribute("stroke-dashoffset", offset);
            // Color based on score
            if (overall >= 0.7) arc.setAttribute("stroke", "var(--status-green)");
            else if (overall >= 0.4) arc.setAttribute("stroke", "var(--accent-blue)");
            else arc.setAttribute("stroke", "var(--status-red, #dc3545)");
        }

        // Update percentage text
        var pctEl = document.getElementById("readiness-pct");
        if (pctEl) pctEl.textContent = pct + "%";

        // Update dimension bars — handles both nested (readiness_scorer)
        // and flat (process_turn readiness_update) formats
        var dims = ["completeness", "clarity", "feasibility", "compliance", "testability"];
        var dimData = data.dimensions || data;
        for (var i = 0; i < dims.length; i++) {
            var dim = dims[i];
            var raw = dimData[dim];
            var val = typeof raw === "object" ? (raw.score || 0) : (raw || 0);
            var barFill = document.getElementById("bar-" + dim);
            var valEl = document.getElementById("val-" + dim);
            if (barFill) barFill.style.width = Math.round(val * 100) + "%";
            if (valEl) valEl.textContent = Math.round(val * 100) + "%";
        }

        // Update requirement count
        if (data.total_requirements !== undefined) {
            updateStat("stat-requirements", data.total_requirements);
        } else if (data.requirement_count !== undefined) {
            updateStat("stat-requirements", data.requirement_count);
        }

        // Show/hide Generate Plan button
        var planBtn = document.getElementById("generate-plan-btn");
        var exportBtn = document.getElementById("export-btn");
        if (planBtn) planBtn.style.display = overall >= 0.7 ? "block" : "none";
        if (exportBtn) exportBtn.style.display = overall > 0 ? "block" : "none";
    }

    function startReadinessPolling() {
        if (readinessTimer) clearInterval(readinessTimer);
        readinessTimer = setInterval(function () {
            if (!document.hidden) ns.chatRefreshReadiness();
        }, 10000);
    }

    // -----------------------------------------------------------------------
    // Complexity / Scale-Adaptive Planning
    // -----------------------------------------------------------------------

    var COMPLEXITY_LABELS = {
        quick_flow: "Quick Flow",
        standard: "Standard",
        full_pipeline: "Full Pipeline"
    };

    ns.chatRefreshComplexity = function () {
        if (!sessionId) return;
        fetch("/api/intake/complexity/" + sessionId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error || data.status !== "ok") return;
            updateComplexityDisplay(data);
        })
        .catch(function () { /* silent */ });
    };

    function updateComplexityDisplay(data) {
        var section = document.getElementById("complexity-section");
        var levelEl = document.getElementById("complexity-level");
        var barEl = document.getElementById("complexity-bar");
        var scoreText = document.getElementById("complexity-score-text");
        var recEl = document.getElementById("complexity-recommendation");
        if (!section || !levelEl) return;

        section.style.display = "block";

        var level = data.complexity_level || "standard";
        var score = data.overall_score || 0;
        var label = COMPLEXITY_LABELS[level] || level;
        var cssClass = "level-" + level.replace(/_/g, "-");

        levelEl.innerHTML = '<span class="level-badge ' + cssClass + '">' + label + '</span>';
        if (barEl) {
            barEl.style.width = score + "%";
            barEl.className = "complexity-bar-fill";
            if (level === "standard") barEl.classList.add("bar-standard");
            else if (level === "full_pipeline") barEl.classList.add("bar-full-pipeline");
        }
        if (scoreText) {
            scoreText.textContent = Math.round(score) + "/100";
        }

        // Show recommendation
        var rec = data.recommendation;
        if (rec && recEl) {
            var phases = rec.estimated_phases || 0;
            var skip = rec.skip_tiers || [];
            var html = "<strong>" + phases + " pipeline phases</strong>";
            if (skip.length > 0) {
                html += " &mdash; skip " + skip.join(", ").replace(/_/g, " ");
            }
            recEl.innerHTML = html;
            recEl.style.display = "block";
        }
    }

    // -----------------------------------------------------------------------
    // Elicitation Techniques (BMAD pattern)
    // -----------------------------------------------------------------------

    var activeTechniqueId = null;

    function loadTechniques() {
        fetch("/api/intake/techniques")
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error || !data.techniques) return;
            renderTechniqueChips(data.techniques);
        })
        .catch(function () { /* silent */ });
    }

    function renderTechniqueChips(techniques) {
        var container = document.getElementById("technique-chips");
        if (!container) return;
        container.innerHTML = "";
        for (var i = 0; i < techniques.length; i++) {
            var t = techniques[i];
            var chip = document.createElement("button");
            chip.className = "technique-chip";
            chip.setAttribute("data-technique-id", t.id);
            chip.title = t.short;
            if (t.id === activeTechniqueId) chip.classList.add("active");
            chip.textContent = t.name;
            chip.onclick = (function (techId) {
                return function () { ns.chatActivateTechnique(techId); };
            })(t.id);
            container.appendChild(chip);
        }
    }

    ns.chatActivateTechnique = function (techId) {
        if (!sessionId) return;
        fetch("/api/intake/techniques/" + sessionId + "/activate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ technique_id: techId }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Error: " + data.error);
                return;
            }
            activeTechniqueId = techId;
            // Show active banner
            var banner = document.getElementById("technique-active");
            var nameEl = document.getElementById("technique-active-name");
            if (banner && nameEl && data.technique) {
                nameEl.textContent = data.technique.name;
                banner.style.display = "flex";
            }
            // Update chip states
            var chips = document.querySelectorAll(".technique-chip");
            for (var i = 0; i < chips.length; i++) {
                chips[i].classList.toggle("active", chips[i].getAttribute("data-technique-id") === techId);
            }
            // Show technique explanation + clickable suggested questions
            appendTechniqueActivation(data);
        })
        .catch(function (err) {
            appendSystemMessage("Error: " + err.message);
        });
    };

    ns.chatDeactivateTechnique = function () {
        if (!sessionId) return;
        fetch("/api/intake/techniques/" + sessionId + "/deactivate", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Error: " + data.error);
                return;
            }
            activeTechniqueId = null;
            var banner = document.getElementById("technique-active");
            if (banner) banner.style.display = "none";
            // Remove active state from chips
            var chips = document.querySelectorAll(".technique-chip");
            for (var i = 0; i < chips.length; i++) {
                chips[i].classList.remove("active");
            }
            appendSystemMessage("Technique deactivated. Standard intake mode resumed.");
        })
        .catch(function (err) {
            appendSystemMessage("Error: " + err.message);
        });
    };

    // -----------------------------------------------------------------------
    // Generate plan / Export / Post-export actions
    // -----------------------------------------------------------------------

    ns.chatGeneratePlan = function () {
        if (!sessionId) return;
        appendSystemMessage("Readiness threshold reached! Exporting requirements for plan generation...");
        ns.chatExport();
    };

    ns.chatExport = function () {
        if (!sessionId) return;
        fetch("/api/intake/export/" + sessionId, { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Export error: " + data.error);
                return;
            }
            var count = 0;
            if (data.requirements) count = data.requirements.length;
            else if (data.count !== undefined) count = data.count;
            appendSystemMessage(
                "Exported " + count + " requirements successfully. " +
                "Choose an action below to continue."
            );
            // Show post-export action buttons
            showPostExportActions();
        })
        .catch(function (err) {
            appendSystemMessage("Export error: " + err.message);
        });
    };

    function showPostExportActions() {
        var panel = document.getElementById("post-export-actions");
        if (panel) panel.style.display = "block";
        // Hide the export button since we already exported
        var exportBtn = document.getElementById("export-btn");
        if (exportBtn) exportBtn.style.display = "none";
    }

    ns.chatTriggerBuild = function () {
        if (!sessionId) return;
        appendSystemMessage("Starting build pipeline...");

        // Start the build pipeline (background)
        fetch("/api/intake/build/" + sessionId + "/start", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Error: " + data.error);
                return;
            }
            appendSystemMessage("Build pipeline started. Track progress in the sidebar.");
            // Show and render initial pipeline state
            showBuildPipeline(data.phases || []);
            // Start polling for updates
            startBuildPolling();
        })
        .catch(function (err) {
            appendSystemMessage("Error: " + err.message);
        });
    };

    ns.chatRunSimulation = function () {
        if (!sessionId) return;
        appendSystemMessage("Generating COAs with simulation...");
        fetch("/api/intake/coas/" + sessionId + "/generate", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Error: " + data.error);
                return;
            }
            var count = data.coas ? data.coas.length : 0;
            appendSystemMessage(count + " COAs generated. Select one in the sidebar.");
            if (data.coas) renderCoaCards(data.coas);
        })
        .catch(function (err) {
            appendSystemMessage("Simulation error: " + err.message);
        });
    };

    ns.chatViewRequirements = function () {
        if (!sessionId) return;
        window.open("/api/intake/session/" + sessionId, "_blank");
    };

    // -----------------------------------------------------------------------
    // PRD generation
    // -----------------------------------------------------------------------

    ns.chatGeneratePRD = function () {
        if (!sessionId) return;
        appendSystemMessage("Generating PRD...");
        fetch("/api/intake/prd/" + sessionId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Error generating PRD: " + data.error);
                return;
            }
            var md = data.prd_markdown || "";
            if (!md) {
                appendSystemMessage("PRD generated but empty — add more requirements first.");
                return;
            }
            // Create downloadable file
            var blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
            var url = URL.createObjectURL(blob);
            var a = document.createElement("a");
            a.href = url;
            a.download = "PRD-" + sessionId + ".md";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            var summary = "PRD generated: " + (data.total_requirements || 0) + " requirements";
            if (data.has_coa) summary += ", COA included";
            if (data.has_decomposition) summary += ", SAFe decomposition included";
            summary += ". Downloaded as PRD-" + sessionId + ".md";
            appendSystemMessage(summary);
        })
        .catch(function (err) {
            appendSystemMessage("Error: " + err.message);
        });
    };

    // -----------------------------------------------------------------------
    // PRD validation
    // -----------------------------------------------------------------------

    ns.chatValidatePRD = function () {
        if (!sessionId) return;
        appendSystemMessage("Running PRD quality validation (6 checks)...");
        fetch("/api/intake/prd/" + sessionId + "/validate")
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Error: " + data.error);
                return;
            }
            var overall = (data.overall || "unknown").toUpperCase();
            var score = data.overall_score || 0;
            var icon = overall === "PASS" ? "\u2705" : overall === "WARNING" ? "\u26A0\uFE0F" : "\u274C";
            var lines = [icon + " PRD Quality: " + overall + " (" + score + "%)"];
            lines.push("");
            var checks = data.checks || [];
            for (var i = 0; i < checks.length; i++) {
                var c = checks[i];
                var sev = (c.severity || "").toUpperCase();
                var ci = sev === "PASS" ? "\u2705" : sev === "WARNING" ? "\u26A0\uFE0F" : "\u274C";
                var detail = c.check.replace(/_/g, " ");
                var extra = "";
                if (c.finding_count !== undefined && c.finding_count > 0) {
                    extra = " (" + c.finding_count + " findings)";
                } else if (c.coverage_pct !== undefined) {
                    extra = " (" + c.coverage_pct + "% coverage)";
                } else if (c.average_pct !== undefined) {
                    extra = " (avg " + c.average_pct + "%)";
                } else if (c.measurable_pct !== undefined) {
                    extra = " (" + c.measurable_pct + "% measurable)";
                }
                lines.push(ci + " " + detail + ": " + sev + extra);
            }
            // Show top findings
            var allFindings = [];
            for (var j = 0; j < checks.length; j++) {
                var findings = checks[j].findings || [];
                for (var k = 0; k < findings.length && k < 3; k++) {
                    var f = findings[k];
                    var msg = f.suggestion || f.issue || "";
                    if (f.matched) msg = "\"" + f.matched + "\" \u2192 " + msg;
                    if (f.requirement_id) msg = "[" + f.requirement_id.slice(-6) + "] " + msg;
                    allFindings.push(msg);
                }
            }
            if (allFindings.length > 0) {
                lines.push("");
                lines.push("Top findings:");
                for (var m = 0; m < allFindings.length && m < 8; m++) {
                    lines.push("  \u2022 " + allFindings[m]);
                }
            }
            // SMART weakest dimension
            var smart = null;
            for (var s = 0; s < checks.length; s++) {
                if (checks[s].check === "smart") { smart = checks[s]; break; }
            }
            if (smart && smart.weakest_dimension) {
                lines.push("");
                lines.push("Weakest SMART dimension: " + smart.weakest_dimension.toUpperCase());
            }
            appendSystemMessage(lines.join("\n"));
        })
        .catch(function (err) {
            appendSystemMessage("Validation error: " + err.message);
        });
    };

    // -----------------------------------------------------------------------
    // COA rendering, selection, polling
    // -----------------------------------------------------------------------

    function renderCoaCards(coas) {
        var section = document.getElementById("coa-section");
        var list = document.getElementById("coa-list");
        if (!section || !list) return;

        section.style.display = "block";
        list.innerHTML = "";
        coasLoaded = true;
        if (coaTimer) { clearInterval(coaTimer); coaTimer = null; }

        var hasSelected = false;
        for (var i = 0; i < coas.length; i++) {
            var coa = coas[i];
            if (coa.status === "selected") hasSelected = true;
        }

        for (var j = 0; j < coas.length; j++) {
            var c = coas[j];
            var card = document.createElement("div");
            card.className = "coa-card";
            if (c.status === "selected") card.className += " coa-card-selected";
            else if (c.status === "rejected") card.className += " coa-card-rejected";

            // Header: name + tier badge
            var header = document.createElement("div");
            header.className = "coa-card-header";

            var name = document.createElement("span");
            name.className = "coa-card-name";
            name.textContent = c.coa_name || c.coa_type || "COA";
            header.appendChild(name);

            var tier = (c.boundary_tier || "green").toLowerCase();
            var badge = document.createElement("span");
            badge.className = "coa-tier-badge coa-tier-" + tier;
            badge.textContent = tier.toUpperCase();
            header.appendChild(badge);
            card.appendChild(header);

            // Description
            var desc = document.createElement("div");
            desc.className = "coa-card-desc";
            desc.textContent = c.description || "";
            card.appendChild(desc);

            // Stats row
            var stats = document.createElement("div");
            stats.className = "coa-card-stats";

            // Timeline PIs — handle flat (generate) and nested (DB list) formats
            var timeline = c.timeline;
            if (typeof timeline === "string") { try { timeline = JSON.parse(timeline); } catch (e) { timeline = null; } }
            var pis = c.timeline_pis || (timeline && timeline.timeline_pis) || "?";
            var piSpan = document.createElement("span");
            piSpan.textContent = pis + " PIs";
            stats.appendChild(piSpan);

            // Risk — handle flat (generate) and nested (DB list) formats
            var riskProfile = c.risk_profile;
            if (typeof riskProfile === "string") { try { riskProfile = JSON.parse(riskProfile); } catch (e) { riskProfile = null; } }
            var risk = c.risk_level || (riskProfile && (riskProfile.overall_risk || riskProfile.risk_level)) || "?";
            var riskSpan = document.createElement("span");
            riskSpan.textContent = "Risk: " + risk;
            stats.appendChild(riskSpan);

            card.appendChild(stats);

            // Action button
            var actions = document.createElement("div");
            actions.className = "coa-card-actions";

            if (c.status === "selected") {
                var unsBtn = document.createElement("button");
                unsBtn.className = "coa-select-btn";
                unsBtn.style.cssText = "border-color:var(--status-red,#dc3545);color:var(--status-red,#dc3545);";
                unsBtn.textContent = "Unselect";
                unsBtn.onclick = (function () {
                    return function () { ns.chatUnselectCoa(); };
                })();
                actions.appendChild(unsBtn);

                // Show selected banner
                var banner = document.getElementById("coa-selected-banner");
                var bannerName = document.getElementById("coa-selected-name");
                if (banner && bannerName) {
                    bannerName.textContent = c.coa_name || c.coa_type;
                    banner.style.display = "block";
                }
            } else if (c.status !== "rejected") {
                var btn = document.createElement("button");
                btn.className = "coa-select-btn";
                btn.textContent = "Select";
                btn.setAttribute("data-coa-id", c.id);
                btn.onclick = (function (coaId) {
                    return function () { ns.chatSelectCoa(coaId); };
                })(c.id);
                if (hasSelected) btn.disabled = true;
                actions.appendChild(btn);
            }

            card.appendChild(actions);
            list.appendChild(card);
        }
    }

    ns.chatSelectCoa = function (coaId) {
        if (!sessionId) return;
        fetch("/api/intake/coas/" + sessionId + "/select", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ coa_id: coaId, selected_by: "Dashboard User", rationale: "Selected via chat UI" }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Selection error: " + data.error);
                return;
            }
            appendSystemMessage("COA selected! Architecture and scope locked in for build.");
            ns.chatRefreshCoas();
        })
        .catch(function (err) {
            appendSystemMessage("Selection error: " + err.message);
        });
    };

    ns.chatUnselectCoa = function () {
        if (!sessionId) return;
        fetch("/api/intake/coas/" + sessionId + "/unselect", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Unselect error: " + data.error);
                return;
            }
            appendSystemMessage("COA unselected. You can now choose a different option.");
            // Hide the selected banner
            var banner = document.getElementById("coa-selected-banner");
            if (banner) banner.style.display = "none";
            ns.chatRefreshCoas();
        })
        .catch(function (err) {
            appendSystemMessage("Unselect error: " + err.message);
        });
    };

    ns.chatRefreshCoas = function () {
        if (!sessionId) return;
        fetch("/api/intake/coas/" + sessionId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) return;
            if (data.coas && data.coas.length > 0) {
                renderCoaCards(data.coas);
            }
        })
        .catch(function () { /* silent */ });
    };

    function startCoaPolling() {
        if (coaTimer) return;
        coaTimer = setInterval(function () {
            if (coasLoaded || document.hidden) return;
            ns.chatRefreshCoas();
        }, 15000);
    }

    // -----------------------------------------------------------------------
    // Build Pipeline — rendering, polling, status display
    // -----------------------------------------------------------------------

    var PHASE_ICONS = {
        pending: "&#x25CB;",   // hollow circle
        running: "&#x25CF;",   // solid circle (animated via CSS)
        done: "&#x2713;",      // checkmark
        error: "&#x2717;",     // X mark
        warning: "&#x26A0;"    // warning triangle
    };

    function showBuildPipeline(phases, jobStatus, jobError) {
        var section = document.getElementById("build-pipeline-section");
        if (section) section.style.display = "block";
        renderPipelinePhases(phases, jobStatus || "running", jobError || "");
    }

    function renderPipelinePhases(phases, jobStatus, jobError) {
        var container = document.getElementById("build-pipeline-phases");
        var statusEl = document.getElementById("build-pipeline-status");
        if (!container) return;

        container.innerHTML = "";
        var allDone = true;
        var hasError = false;

        for (var i = 0; i < phases.length; i++) {
            var p = phases[i];
            var row = document.createElement("div");
            row.className = "build-phase build-phase-" + p.status;

            // Connector line (except first)
            if (i > 0) {
                var connector = document.createElement("div");
                connector.className = "build-phase-connector";
                if (p.status === "done" || p.status === "warning") {
                    connector.classList.add("build-phase-connector-done");
                } else if (p.status === "running") {
                    connector.classList.add("build-phase-connector-active");
                }
                container.appendChild(connector);
            }

            // Icon
            var icon = document.createElement("span");
            icon.className = "build-phase-icon";
            if (p.status === "running") icon.className += " build-phase-icon-pulse";
            icon.innerHTML = PHASE_ICONS[p.status] || PHASE_ICONS.pending;
            row.appendChild(icon);

            // Text content
            var text = document.createElement("div");
            text.className = "build-phase-text";

            var name = document.createElement("span");
            name.className = "build-phase-name";
            name.textContent = p.name;
            text.appendChild(name);

            if (p.detail) {
                var detail = document.createElement("span");
                detail.className = "build-phase-detail";
                detail.textContent = p.detail;
                text.appendChild(detail);
            }

            row.appendChild(text);
            container.appendChild(row);

            if (p.status !== "done" && p.status !== "warning") allDone = false;
            if (p.status === "error") hasError = true;
        }

        // Also check overall job status (handles crash before any phase update)
        if (jobStatus === "error") hasError = true;

        // Update overall status
        if (statusEl) {
            if (hasError) {
                var errMsg = "Build encountered an error";
                if (jobError) errMsg += ": " + jobError;
                statusEl.innerHTML = '<span class="build-status-error">' + errMsg.replace(/</g, "&lt;") + '</span>';
                if (buildTimer) { clearInterval(buildTimer); buildTimer = null; }
            } else if (allDone) {
                statusEl.innerHTML = '<span class="build-status-done">Build pipeline complete</span>';
                if (buildTimer) { clearInterval(buildTimer); buildTimer = null; }
                // Show post-build action buttons
                var doneActions = document.getElementById("build-done-actions");
                if (doneActions) doneActions.style.display = "block";
            } else {
                statusEl.innerHTML = '<span class="build-status-running">Building...</span>';
            }
        }
    }

    ns.chatRefreshBuild = function () {
        if (!sessionId) return;
        fetch("/api/intake/build/" + sessionId + "/status")
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.phases || data.phases.length === 0) return;
            showBuildPipeline(data.phases, data.status, data.error);
            if (data.status === "running") startBuildPolling();
        })
        .catch(function () { /* silent */ });
    };

    function startBuildPolling() {
        if (buildTimer) clearInterval(buildTimer);
        var emptyPolls = 0;
        buildTimer = setInterval(function () {
            if (!sessionId || document.hidden) return;
            fetch("/api/intake/build/" + sessionId + "/status")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.phases || data.phases.length === 0) {
                    emptyPolls++;
                    // Server lost state (restart) — stop polling after 3 empty responses
                    if (emptyPolls >= 3) {
                        if (buildTimer) { clearInterval(buildTimer); buildTimer = null; }
                        appendSystemMessage("Build state lost (server restart). Please re-run.");
                    }
                    return;
                }
                emptyPolls = 0;
                renderPipelinePhases(data.phases, data.status, data.error);
                // Stop polling when done or errored
                if (data.status === "done" || data.status === "error") {
                    if (buildTimer) { clearInterval(buildTimer); buildTimer = null; }
                    if (data.status === "done") {
                        appendSystemMessage("Build pipeline complete! Project is ready.");
                    }
                }
            })
            .catch(function () { /* silent */ });
        }, 2000);
    }

    // -----------------------------------------------------------------------
    // Post-build: View Project, Run Tests
    // -----------------------------------------------------------------------

    var testTimer = null;

    ns.chatViewProject = function () {
        if (!sessionId) return;
        fetch("/api/intake/build/" + sessionId + "/project")
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.project_id) {
                window.open("/projects/" + data.project_id, "_blank");
            } else {
                appendSystemMessage("No project found for this session.");
            }
        })
        .catch(function (err) {
            appendSystemMessage("Error: " + err.message);
        });
    };

    ns.chatRunTests = function () {
        if (!sessionId) return;
        appendSystemMessage("Starting test suite...");

        fetch("/api/intake/test/" + sessionId + "/start", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendSystemMessage("Error: " + data.error);
                return;
            }
            appendSystemMessage("Test pipeline started. Track progress in the sidebar.");
            showTestPipeline(data.phases || []);
            startTestPolling();
        })
        .catch(function (err) {
            appendSystemMessage("Error: " + err.message);
        });
    };

    function showTestPipeline(phases, jobStatus, jobError) {
        // Reuse the build pipeline section — update header and content
        var section = document.getElementById("build-pipeline-section");
        if (section) section.style.display = "block";
        // Change title to Test Pipeline
        var header = section ? section.querySelector("h3") : null;
        if (header) header.textContent = "Test Pipeline";
        // Hide post-build buttons during testing
        var doneActions = document.getElementById("build-done-actions");
        if (doneActions) doneActions.style.display = "none";
        renderPipelinePhases(phases, jobStatus || "running", jobError || "");
    }

    function startTestPolling() {
        if (testTimer) clearInterval(testTimer);
        var emptyPolls = 0;
        testTimer = setInterval(function () {
            if (!sessionId || document.hidden) return;
            fetch("/api/intake/test/" + sessionId + "/status")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.phases || data.phases.length === 0) {
                    emptyPolls++;
                    // Server lost state (restart) — stop polling after 3 empty responses
                    if (emptyPolls >= 3) {
                        if (testTimer) { clearInterval(testTimer); testTimer = null; }
                        appendSystemMessage("Test state lost (server restart). Please re-run.");
                    }
                    return;
                }
                emptyPolls = 0;
                renderPipelinePhases(data.phases, data.status, data.error);
                if (data.status === "done" || data.status === "error") {
                    if (testTimer) { clearInterval(testTimer); testTimer = null; }
                    // Restore header
                    var section = document.getElementById("build-pipeline-section");
                    var header = section ? section.querySelector("h3") : null;
                    if (header) header.textContent = "Test Results";
                    // Show actions again
                    var doneActions = document.getElementById("build-done-actions");
                    if (doneActions) doneActions.style.display = "block";
                    if (data.status === "done") {
                        appendSystemMessage("Test pipeline complete! All checks finished.");
                    }
                }
            })
            .catch(function () { /* silent */ });
        }, 2000);
    }

    // -----------------------------------------------------------------------
    // UI helpers
    // -----------------------------------------------------------------------

    function appendBubble(role, text) {
        var container = document.getElementById("chat-messages");
        if (!container) return;
        var div = document.createElement("div");
        div.className = "message-bubble message-" + role;

        var roleDiv = document.createElement("div");
        roleDiv.className = "message-role";
        roleDiv.textContent = role === "customer" ? "You" : "ICDEV Analyst";

        var contentDiv = document.createElement("div");
        contentDiv.className = "message-content";
        contentDiv.textContent = text;

        div.appendChild(roleDiv);
        div.appendChild(contentDiv);
        container.appendChild(div);
        scrollToBottom();
    }

    function appendTechniqueActivation(data) {
        var container = document.getElementById("chat-messages");
        if (!container) return;

        var tech = data.technique || {};
        var qs = data.suggested_questions || [];

        var div = document.createElement("div");
        div.className = "message-bubble message-system technique-activation";

        var html = '<div class="technique-explain">';
        html += '<strong>' + escHtml(tech.name || "Technique") + '</strong>';
        if (tech.description) {
            html += '<p class="technique-desc">' + escHtml(tech.description) + '</p>';
        }
        if (tech.targets && tech.targets.length > 0) {
            html += '<div class="technique-targets">Improves: ';
            for (var t = 0; t < tech.targets.length; t++) {
                html += '<span class="technique-target-tag">' + escHtml(tech.targets[t]) + '</span>';
            }
            html += '</div>';
        }
        html += '</div>';

        if (qs.length > 0) {
            html += '<div class="technique-questions">';
            html += '<div class="technique-questions-label">Try asking:</div>';
            for (var i = 0; i < qs.length; i++) {
                html += '<button class="technique-question-btn" data-q="' + escAttr(qs[i]) + '">' + escHtml(qs[i]) + '</button>';
            }
            html += '</div>';
        }

        div.innerHTML = html;

        // Wire up question buttons to fill input
        var btns = div.querySelectorAll(".technique-question-btn");
        for (var j = 0; j < btns.length; j++) {
            btns[j].addEventListener("click", function () {
                var input = document.getElementById("chat-input");
                if (input) {
                    input.value = this.getAttribute("data-q");
                    input.focus();
                    autoGrow(input);
                    var sendBtn = document.getElementById("chat-send-btn");
                    if (sendBtn) sendBtn.disabled = false;
                }
            });
        }

        container.appendChild(div);
        scrollToBottom();
    }

    function escHtml(s) {
        return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function escAttr(s) {
        return escHtml(s).replace(/'/g, "&#39;");
    }

    function appendSystemMessage(text) {
        var container = document.getElementById("chat-messages");
        if (!container) return;
        var div = document.createElement("div");
        div.className = "message-bubble message-system";
        // Render lines as HTML, wrapping /slash-commands in copyable code blocks
        var html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        html = html.replace(/\n/g, "<br>");
        html = html.replace(/(\/\S+\s+\S+)/g, '<code class="cmd-copy" title="Click to copy" onclick="navigator.clipboard.writeText(this.textContent)">$1</code>');
        div.innerHTML = html;
        container.appendChild(div);
        scrollToBottom();
    }

    function appendTypingIndicator() {
        var container = document.getElementById("chat-messages");
        if (!container) return null;
        var div = document.createElement("div");
        var id = "typing-" + Date.now();
        div.id = id;
        div.className = "message-bubble message-analyst message-typing";
        div.innerHTML = '<div class="message-role">ICDEV Analyst</div><div class="message-content typing-dots"><span>.</span><span>.</span><span>.</span></div>';
        container.appendChild(div);
        scrollToBottom();
        return id;
    }

    function removeTypingIndicator(id) {
        if (!id) return;
        var el = document.getElementById(id);
        if (el) el.remove();
    }

    function scrollToBottom() {
        var container = document.getElementById("chat-messages");
        if (container) container.scrollTop = container.scrollHeight;
    }

    function autoGrow(el) {
        el.style.height = "auto";
        el.style.height = Math.min(el.scrollHeight, 120) + "px";
    }

    function updateStat(id, value) {
        var el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    // -----------------------------------------------------------------------
    // Namespace merge + init
    // -----------------------------------------------------------------------

    ns.chatInit = init;
    ns.chatSend = ns.chatSend || function () {};
    ns.chatUpload = ns.chatUpload || function () {};
    ns.chatRefreshReadiness = ns.chatRefreshReadiness || function () {};
    ns.chatCreateSession = ns.chatCreateSession || function () {};
    ns.chatGeneratePlan = ns.chatGeneratePlan || function () {};
    ns.chatExport = ns.chatExport || function () {};
    ns.chatTriggerBuild = ns.chatTriggerBuild || function () {};
    ns.chatRunSimulation = ns.chatRunSimulation || function () {};
    ns.chatViewRequirements = ns.chatViewRequirements || function () {};
    ns.chatSelectCoa = ns.chatSelectCoa || function () {};
    ns.chatUnselectCoa = ns.chatUnselectCoa || function () {};
    ns.chatRefreshCoas = ns.chatRefreshCoas || function () {};
    ns.chatRefreshBuild = ns.chatRefreshBuild || function () {};
    ns.chatViewProject = ns.chatViewProject || function () {};
    ns.chatRunTests = ns.chatRunTests || function () {};
    ns.chatGeneratePRD = ns.chatGeneratePRD || function () {};
    ns.chatValidatePRD = ns.chatValidatePRD || function () {};
    ns.chatRefreshComplexity = ns.chatRefreshComplexity || function () {};
    ns.chatActivateTechnique = ns.chatActivateTechnique || function () {};
    ns.chatDeactivateTechnique = ns.chatDeactivateTechnique || function () {};
    window.ICDEV = ns;

    // Auto-init when DOM ready
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
