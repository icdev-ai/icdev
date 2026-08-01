// ICDEV delivery-pipeline view — SHARED by /kanban (kanban.html) and the
// Home board (index.html). Single source of truth for the card stage pill
// and the task-modal Lifecycle stepper. Mirrors args/pipeline.yaml.

// ---- Delivery pipeline (Phase 1 visualization; mirrors args/pipeline.yaml) ----
var PIPELINE_STAGES = [
  {key:"implement",    label:"Implement",          tip:"The agent writes the code for this task in an isolated git worktree."},
  {key:"code_quality", label:"Code Quality",       tip:"Static checks: the code compiles, passes ruff lint, and adds no new bandit security findings vs main."},
  {key:"coherence",    label:"Coherence",          tip:"Cross-file consistency: schema, config, manifests and wiring stay intact so the change does not break internal contracts."},
  {key:"conformance",  label:"Conformance Review", tip:"Did we build what was asked? An LLM judge compares the diff against the task's requirements + acceptance criteria and flags scope drift."},
  {key:"unit_tests",   label:"Unit Tests",         tip:"pytest: the task's unit and integration tests pass."},
  {key:"e2e",          label:"E2E Playwright",     tip:"Browser test: the affected pages actually render and work end-to-end (runs only when UI files changed)."},
  {key:"pr",           label:"PR Opened",          tip:"A pull request is opened for the task's branch, awaiting CI."},
  {key:"ci",           label:"CI",                 tip:"GitHub CI checks (Lint, Test, Security Scan, Helm Lint) pass on the PR."},
  {key:"merged",       label:"Merged → main", tip:"The branch is merged and verified present on origin/main. A task only becomes DONE here."}
];
var STATUS_TO_STAGE = {suggested:"implement",backlog:"implement",scheduled:"implement",in_progress:"implement",decomposed:"implement",needs_decomposition:"implement",validating:"code_quality",pr_opened:"pr",changes_requested:"pr",merge_conflict:"pr",ci_failed:"ci",done:"merged",failed:"implement",token_exhausted:"implement"};
var STAGE_STATE_CLASS = {completed:"completed",current:"active",failed:"blocked",pending:"pending",not_run:"skipped"};

function _pipeEsc(x){ return (window.ICDEV && ICDEV.escapeHTML) ? ICDEV.escapeHTML(x) : String(x==null?"":x); }
function _stageForStatus(status){ return STATUS_TO_STAGE[(status||"").trim()] || "implement"; }

function _stagePill(status){
  var key = _stageForStatus(status);
  var def = null;
  for (var i=0;i<PIPELINE_STAGES.length;i++){ if(PIPELINE_STAGES[i].key===key){ def=PIPELINE_STAGES[i]; break; } }
  if (!def) def = PIPELINE_STAGES[0];
  var s = (status||"").trim();
  var color = "#6c6c80";
  if (s==="done") color = "#28a745";
  else if (s==="in_progress"||s==="validating") color = "#4a90d9";
  else if (s==="pr_opened"||s==="changes_requested"||s==="merge_conflict") color = "#f0a020";
  else if (s==="ci_failed"||s==="failed"||s==="token_exhausted") color = "#dc3545";
  return '<span class="badge" style="background:'+color+'20; color:'+color+';" title="'+_pipeEsc(def.tip)+'">'+_pipeEsc(def.label)+'</span>';
}

function renderLifecycle(taskId){
  var el = document.getElementById("task-lifecycle-body");
  if (!el) return;
  el.innerHTML = '<div style="color:var(--text-dim); font-size:12px;">Loading pipeline&hellip;</div>';
  fetch("/api/kanban/tasks/"+encodeURIComponent(taskId)+"/pipeline")
    .then(function(r){return r.json();})
    .then(function(d){ el.innerHTML = _lifecycleHtml(d, taskId); })
    .catch(function(e){ el.innerHTML = '<div style="color:#dc3545; font-size:12px;">Pipeline unavailable.</div>'; });
}

function _lifecycleHtml(d, taskId){
  if (d && d.error){ return '<div style="color:#dc3545; font-size:12px;">'+_pipeEsc(d.error)+'</div>'; }
  var badge = {completed:"✓", current:"●", failed:"✗", pending:"", not_run:"—"};
  var steps = (d.stages||[]).map(function(st){
    var cls = STAGE_STATE_CLASS[st.state] || "pending";
    var detail = st.detail ? '<div class="pipeline-detail">'+_pipeEsc(st.detail)+'</div>' : '';
    return '<div class="pipeline-step '+cls+'" title="'+_pipeEsc(st.tooltip)+'">'
      + '<div class="pipeline-dot">'+(badge[st.state]||"")+'</div>'
      + '<div class="pipeline-label">'+_pipeEsc(st.label)+'</div>'+detail+'</div>';
  }).join("");
  var stepper = '<div class="progress-pipeline">'+steps+'</div>';
  var enforced = (d.enforce_mode === "enforced");
  var modeVal = enforced ? "ENFORCED" : "RECORD-ONLY";
  var modeTip = enforced ? "Gates block a task from being marked done." : "Gates are recorded but do not block done (KANBAN_PIPELINE_ENFORCE off).";
  var mode = '<div style="margin:4px 0 8px; font-size:12px; color:'+(enforced ? "#28a745" : "#6c757d")+';" title="'+_pipeEsc(modeTip)+'">Pipeline mode: <strong>'+modeVal+'</strong></div>';
  var branch = d.branch_state ? '<div style="margin:4px 0 8px; color:#dc3545; font-size:12px;">⚠ '+_pipeEsc(d.branch_state)+'</div>' : '';
  var m = d.meta||{};
  var metaRows = [];
  if (m.branch_name) metaRows.push("branch: "+_pipeEsc(m.branch_name));
  if (m.commit_subject) metaRows.push("commit: "+_pipeEsc(m.commit_subject));
  if (m.files_changed) metaRows.push("files: "+_pipeEsc(m.files_changed)+" (+"+_pipeEsc(m.lines_added||0)+"/-"+_pipeEsc(m.lines_removed||0)+")");
  var metaHtml = metaRows.length ? '<div style="font-size:11px; color:var(--text-dim); margin:6px 0;">'+metaRows.join(" &middot; ")+'</div>' : '';
  var tl = (d.transitions||[]).map(function(t){
    return '<tr><td style="padding:2px 8px; color:var(--text-dim);">'+_pipeEsc(t.at)+'</td>'
      + '<td style="padding:2px 8px;">'+_pipeEsc(t.from)+' → '+_pipeEsc(t.to)+'</td>'
      + '<td style="padding:2px 8px; color:var(--text-dim);">'+_pipeEsc(t.actor)+'</td>'
      + '<td style="padding:2px 8px; color:var(--text-dim);">'+_pipeEsc(t.reason)+'</td></tr>';
  }).join("");
  var timeline = tl ? '<details style="margin-top:8px;"><summary style="cursor:pointer; font-size:12px; color:var(--text-dim);">Transition timeline ('+(d.transitions||[]).length+')</summary><table style="width:100%; font-size:11px; margin-top:6px;">'+tl+'</table></details>' : '';
  var prBtn = '<button class="btn btn-sm" onclick="checkPrCi(\''+_pipeEsc(taskId)+'\')" style="margin-top:8px;">Check PR/CI (live)</button>';
  var prBox = '<div id="task-pr-ci" style="margin-top:8px;"></div>';
  return stepper + mode + branch + metaHtml + prBtn + prBox + timeline;
}

function checkPrCi(taskId){
  var box = document.getElementById("task-pr-ci");
  if (box) box.innerHTML = '<span style="font-size:12px; color:var(--text-dim);">Checking gh&hellip;</span>';
  fetch("/api/kanban/tasks/"+encodeURIComponent(taskId)+"/pipeline?live=1")
    .then(function(r){return r.json();})
    .then(function(d){
      if (!box) return;
      if (!d.pr){ box.innerHTML = '<span style="font-size:12px; color:var(--text-dim);">No PR found (or gh unavailable).</span>'; return; }
      var pr = d.pr;
      var ciColor = pr.ci_conclusion==="passing" ? "#28a745" : (pr.ci_conclusion==="failing" ? "#dc3545" : "#f0a020");
      box.innerHTML = '<div style="font-size:12px;">'
        + (pr.url ? '<a href="'+_pipeEsc(pr.url)+'" target="_blank">PR #'+_pipeEsc(pr.number)+'</a>' : 'PR #'+_pipeEsc(pr.number))
        + ' &middot; state: '+_pipeEsc(pr.state)
        + ' &middot; CI: <span style="color:'+ciColor+';">'+_pipeEsc(pr.ci_conclusion)+'</span>'
        + ' &middot; mergeable: '+_pipeEsc(pr.mergeable)+'</div>';
    })
    .catch(function(e){ if(box) box.innerHTML = '<span style="font-size:12px; color:#dc3545;">gh check failed.</span>'; });
}


// ---- shared task board renderer (moved from index.html + kanban.html for parity; canonical = kanban.html) ----
// kv-viz-02: live counters for in_progress cards
var _kvIntervals = {};
function _clearKvIntervals() {
    Object.keys(_kvIntervals).forEach(function(id) { clearInterval(_kvIntervals[id]); });
    _kvIntervals = {};
}
function _setupKvCounters(boardEl) {
    boardEl.querySelectorAll('[data-kv-started]').forEach(function(sentinel) {
        var taskId = sentinel.getAttribute('data-kv-task-id');
        var startedAt = sentinel.getAttribute('data-kv-started');
        var barEl = document.getElementById('kv-bar-' + taskId);
        var textEl = document.getElementById('kv-tif-' + taskId);
        if (!textEl || !startedAt) return;
        var startMs = new Date(startedAt).getTime();
        function tick() {
            var elapsed = (Date.now() - startMs) / 1000;
            var mins = Math.floor(elapsed / 60);
            var secs = Math.floor(elapsed % 60);
            textEl.textContent = 'Running ' + mins + 'm ' + secs + 's';
            if (barEl) {
                var pct = Math.min(100, (elapsed / 900) * 100);
                barEl.style.width = pct + '%';
                barEl.style.background = pct >= 85 ? '#ef4444' : pct >= 60 ? '#f59e0b' : '#22c55e';
            }
        }
        tick();
        _kvIntervals[taskId] = setInterval(tick, 1000);
    });
}

function renderTaskKanban(tasks) {
    var board = document.getElementById('kanban-board');
    if (!board) return;
    _clearKvIntervals();

    var columns = { suggested: [], backlog: [], scheduled: [], in_progress: [], pr_opened: [], blocked: [], done: [] };
    tasks.forEach(function(t) {
        var s = t.status || 'backlog';
        // 'decomposed' and 'needs_decomposition' are internal scheduler states —
        // decomposed = parent split into children (auto-closes when children done);
        // needs_decomposition = queued for LLM decomposer next cycle.
        // Both belong in Done visually (work is handed off); never bucket as Backlog.
        if (s === 'decomposed' || s === 'needs_decomposition') s = 'done';
        // Lifecycle states (migration 260). 'pr_opened' has its own "Awaiting
        // Merge" column. ci_failed/merge_conflict/changes_requested (transient
        // PR-review states), failed (terminal), token_exhausted (paused) and
        // validating (awaiting integration) group under "Blocked" — previously
        // they fell into Backlog, misleadingly reading as fresh queued work.
        if (s === 'ci_failed' || s === 'merge_conflict' || s === 'changes_requested'
            || s === 'failed' || s === 'token_exhausted' || s === 'validating') s = 'blocked';
        if (columns[s]) columns[s].push(t); else columns.backlog.push(t);
    });

    var priorityColors = { critical: '#ef4444', high: '#f97316', medium: '#3b82f6', low: '#6b7280' };
    var typeIcons = { build: '\u{1F528}', run: '\u25B6', fix: '\u{1F41B}', research: '\u{1F50D}', deploy: '\u{1F680}', test: '\u2705', chore: '\u{1F9F9}' };
    var nextStatus = { suggested: 'backlog', backlog: 'scheduled', scheduled: 'in_progress', in_progress: 'done' };
    var prevStatus = { backlog: 'suggested', scheduled: 'backlog', in_progress: 'scheduled', done: 'in_progress' };
    var executorLabels = {
        claude_cli:     { icon: '\uD83E\uDD16', label: 'Claude',     color: '#7c3aed' },
        ollama_local:   { icon: '\uD83D\uDCBB', label: 'Local',      color: '#0891b2' },
        gitlab:         { icon: '\uD83E\uDD8A', label: 'GitLab',     color: '#e24304' },
        github_actions: { icon: '\u2699\uFE0F', label: 'GH Actions', color: '#2ea043' }
    };
    var llmModeIcons = { claude: '\uD83E\uDDE0', ollama: '\uD83D\uDCBB', none: '\u2699\uFE0F' };
    var llmModeTitles = { claude: 'LLM: Claude', ollama: 'LLM: Ollama (local)', none: 'No LLM' };

    board.querySelectorAll('.kanban-column').forEach(function(colEl) {
        var status = colEl.getAttribute('data-status');
        if (!columns[status]) return;
        var items = columns[status];

        var countEl = colEl.querySelector('.kanban-column-count');
        if (countEl) countEl.textContent = items.length;

        // Show/hide the Promote All button based on suggested card count
        if (status === 'suggested') {
            var promBtn = document.getElementById('btn-promote-all');
            if (promBtn) promBtn.style.display = items.length > 0 ? 'inline-block' : 'none';
        }

        var bodyEl = colEl.querySelector('.kanban-column-body');
        if (!bodyEl) return;

        if (items.length === 0) {
            bodyEl.innerHTML = '<div class="kanban-empty">No tasks</div>';
            return;
        }

        var esc = window.ICDEV && ICDEV.escapeHTML ? ICDEV.escapeHTML : function(s) { return String(s || ''); };
        var cardsHtmlArr = items.map(function(t) {
            var icon = typeIcons[t.task_type] || '';
            var pColor = priorityColors[t.priority] || '#6b7280';
            // Gates get an amber rail instead of a priority rail — a gate has no
            // priority, it is a brake.
            if (t.is_manual_gate) pColor = '#f59e0b';
            var isSuggested = status === 'suggested';
            // A manual-mode gate is a sentinel, not work. Moving it right marks it
            // done, which RELEASES every dependent for auto-dispatch — so it gets no
            // arrows. (The API refuses the move too; this just removes the trap.)
            var isGate = !!t.is_manual_gate;
            var arrows = '';
            if (!isSuggested && !isGate && prevStatus[status]) arrows += '<button class="kanban-move-btn" onclick="event.stopPropagation(); moveTask(\'' + t.id + '\',\'' + prevStatus[status] + '\')" title="Move left">◀</button>';
            if (!isSuggested && !isGate && nextStatus[status]) arrows += '<button class="kanban-move-btn" onclick="event.stopPropagation(); moveTask(\'' + t.id + '\',\'' + nextStatus[status] + '\')" title="Move right">▶</button>';

            var schedLine = t.scheduled_at ? '<div style="font-size:11px; color:var(--text-dim); margin-top:4px;">📅 ' + _formatSchedule(t.scheduled_at) + '</div>' : '';

            // Target date line with overdue warning
            var targetLine = '';
            if (t.target_date) {
                var today = new Date(); today.setHours(0,0,0,0);
                var td = new Date(t.target_date + 'T00:00:00');
                var overdue = td < today && t.status !== 'done';
                targetLine = '<div style="font-size:11px; color:' + (overdue ? '#ef4444' : 'var(--text-dim)') + '; margin-top:3px;" title="Target: ' + esc(t.target_date) + '">'
                    + (overdue ? '🔴' : '🎯') + ' ' + esc(t.target_date) + (overdue ? ' <strong>OVERDUE</strong>' : '') + '</div>';
            }

            // Phantom completion risk badge
            var phantomBadge = '';
            if (t.phantom_ratio && t.phantom_ratio > 0.5) {
                var pr = Math.round(t.phantom_ratio * 100);
                phantomBadge = '<span class="badge" style="background:#f97316; color:#fff; font-size:10px;" title="Phantom completion risk: output/claim ratio ' + pr + '%">⚠️ ' + pr + '% phantom</span>';
            }

            // Change metrics + branch badge (done cards only)
            var changeMetrics = '';
            if (t.status === 'done' && (t.files_changed || t.lines_added || t.lines_removed)) {
                var fc = t.files_changed || 0;
                var la = t.lines_added || 0;
                var lr = t.lines_removed || 0;
                changeMetrics = '<div style="font-size:10px; color:var(--text-dim); margin-top:3px;" title="' + fc + ' files changed">'
                    + fc + ' file' + (fc !== 1 ? 's' : '')
                    + ' <span style="color:#22c55e;">+' + la + '</span>'
                    + ' <span style="color:#ef4444;">-' + lr + '</span>'
                    + '</div>';
            }
            if (t.status === 'done' && t.branch_name) {
                changeMetrics += '<div style="font-size:10px; color:var(--text-dim); margin-top:2px;" title="' + esc(t.commit_summary || '') + '">⑃ ' + esc(t.branch_name) + '</div>';
            }

            // Tag chips
            var tagChips = '';
            if (t.tags && t.tags.length) {
                tagChips = '<div style="display:flex; flex-wrap:wrap; gap:3px; margin-top:4px;">'
                    + t.tags.map(function(tg) {
                        return '<span style="background:' + esc(tg.color) + '33; color:' + esc(tg.color) + '; font-size:10px; padding:1px 5px; border-radius:3px; font-weight:600;">' + esc(tg.name) + '</span>';
                    }).join('')
                    + '</div>';
            }

            // Executor badge
            var execInfo = executorLabels[t.executor_type] || executorLabels['claude_cli'];
            var execBadge = '<span class="badge" style="background:' + execInfo.color + '20;color:' + execInfo.color + ';font-size:10px;" title="Executor: ' + execInfo.label + '">'
                + execInfo.icon + ' ' + execInfo.label + '</span>';

            // Execution ID as clickable link (GitLab pipeline URL) or plain text
            var execIdLine = '';
            if (t.execution_id) {
                var execUrl = t.executor_url || null;
                if (execUrl) {
                    execIdLine = '<div style="font-size:10px; color:var(--text-dim); margin-top:3px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">'
                        + '🔗 <a href="' + esc(execUrl) + '" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color:var(--text-dim);text-decoration:underline;">' + esc(t.execution_id) + '</a></div>';
                } else {
                    execIdLine = '<div style="font-size:10px; color:var(--text-dim); margin-top:3px;">🆔 ' + esc(t.execution_id) + '</div>';
                }
            }

            // LLM mode indicator — inferred from executor_type
            var llmMode = (t.executor_type === 'claude_cli') ? 'claude' : (t.executor_type === 'ollama_local' ? 'ollama' : 'none');
            var llmIcon = llmModeIcons[llmMode] || '';
            var llmTitle = llmModeTitles[llmMode] || '';
            var llmBadge = '<span title="' + llmTitle + '" style="font-size:11px; cursor:default;">' + llmIcon + '</span>';

            // OPT-62: mid-run message injection button (in_progress only)
            var msgBtn = '';
            if (status === 'in_progress') {
                msgBtn = `<button class="kanban-move-btn" onclick="event.stopPropagation(); openMessageModal('${t.id}', ${JSON.stringify(t.title || '').replace(/"/g, '&quot;')})" title="Send message to running task">💬</button>`;
            }

            // kv-viz-02: in_progress visibility elements
            var attemptBadge = '';
            var reaperBar = '';
            var tifLine = '';
            var lastKilledLine = '';
            if (isGate) {
                // Gates sit in_progress forever BY DESIGN. Rendering them with a live
                // "Running 81m" counter and a reaper progress bar made a sentinel look
                // like a hung task — the single most confusing thing on this board.
                // Say what it actually is instead: nothing is running, and here is what
                // it is holding back.
                var holding = t.gate_holding || 0;
                tifLine = '<div style="font-size:11px;color:var(--text-dim);margin-top:4px;">'
                    + 'Not running — held open by design</div>';
                attemptBadge = '<span class="badge" style="background:#f59e0b20;color:#f59e0b;font-size:10px;" '
                    + 'title="Manual-mode gate: a sentinel that blocks its dependents from auto-dispatch. '
                    + 'It is never dispatched, reaped or auto-completed.">🔒 MANUAL GATE</span>';
                if (holding > 0) {
                    tifLine += '<div style="font-size:11px;color:#f59e0b;margin-top:2px;" '
                        + 'title="These tasks stay in Backlog until the gate is released">'
                        + 'Holding ' + holding + ' task' + (holding !== 1 ? 's' : '') + ' in Backlog</div>';
                }
            } else if (status === 'in_progress') {
                var ac = t.attempt_count || 0;
                if (ac > 1) {
                    var abColor = ac >= 4 ? '#ef4444' : '#f59e0b';
                    attemptBadge = '<span class="badge" style="background:' + abColor + '20;color:' + abColor + ';font-size:10px;" title="Dispatched ' + ac + ' times">Attempt ' + ac + '</span>';
                }
                if (t.current_attempt_started_at) {
                    reaperBar = '<div style="margin:4px 0 2px 0;background:var(--bg-code);border-radius:2px;height:3px;overflow:hidden;" title="Reaper kills at 15m with no subprocess heartbeat">'
                        + '<div id="kv-bar-' + esc(t.id) + '" style="height:100%;width:0%;background:#22c55e;"></div></div>';
                    tifLine = '<div id="kv-tif-' + esc(t.id) + '" style="font-size:11px;color:var(--text-dim);margin-top:2px;">Running 0m 0s</div>'
                        + '<span data-kv-started="' + esc(t.current_attempt_started_at) + '" data-kv-task-id="' + esc(t.id) + '" style="display:none;"></span>';
                }
                if (t.last_reaped_reason) {
                    var reason = String(t.last_reaped_reason);
                    var shortReason = reason.length > 80 ? reason.substring(0, 80) + '…' : reason;
                    lastKilledLine = '<div style="font-size:10px;color:var(--text-dim);margin-top:2px;font-style:italic;" title="' + esc(reason) + '">Last killed: ' + esc(shortReason) + '</div>';
                }
            }

            // Dependency badge — show on any status where dep is unmet
            var depLine = '';
            if (t.depends_on_task_id) {
                if (t.is_blocked) {
                    var depLabel = t.depends_on_title ? esc(t.depends_on_title) : esc(t.depends_on_task_id);
                    var depStatus = t.depends_on_status ? ' [' + esc(t.depends_on_status) + ']' : '';
                    depLine = '<div style="font-size:11px; color:#f97316; margin-top:4px;" title="Waiting for: ' + depLabel + depStatus + '">'
                        + '⛔ Blocked by: <span style="font-weight:600;">' + depLabel + '</span>' + depStatus + '</div>';
                } else {
                    // Dep is done — show a subtle unblocked indicator
                    var unblockLabel = t.depends_on_title ? esc(t.depends_on_title) : esc(t.depends_on_task_id);
                    depLine = '<div style="font-size:11px; color:#22c55e; margin-top:4px;" title="Dependency met: ' + unblockLabel + '">'
                        + '✓ After: <span style="opacity:0.8;">' + unblockLabel + '</span></div>';
                }
            }

            // Suggested-column extras: checkbox, oracle badges, inline actions
            var checkBox = '';
            var oracleBadges = '';
            var inlineActions = '';
            if (isSuggested) {
                var checked = _selectedTaskIds.has(t.id) ? ' checked' : '';
                checkBox = `<input type="checkbox" class="suggested-checkbox"${checked} onclick="toggleTaskSelected('${t.id}', event)" style="position:absolute; top:8px; right:8px; cursor:pointer;">`;
                var conf = t.oracle_confidence;
                var val = t.oracle_value;
                var dup = t.dedup_count || 0;
                var lens = t.oracle_lens || '';
                var confLabel = conf ? 'conf ' + Math.round(conf * 100) + '%' : '';
                var valueLabel = val ? ' · val ' + val.toFixed(2) : '';
                var dupLabel = dup > 1 ? ' · ' + dup + '× dup' : '';
                var lensLabel = lens ? ' · ' + esc(lens) : '';
                oracleBadges = '<div style="display:flex; align-items:center; gap:4px; flex-wrap:wrap; margin-bottom:4px;">'
                    + '<span style="font-size:10px; background:#8b5cf620; color:#8b5cf6; border-radius:3px; padding:1px 5px; font-weight:600;">🔮 Oracle</span>'
                    + (confLabel ? '<span style="font-size:10px; color:var(--text-dim);">' + confLabel + valueLabel + dupLabel + lensLabel + '</span>' : '')
                    + '</div>';
                inlineActions = '<div style="display:flex; gap:6px; margin-top:6px;">'
                    + `<button class="btn btn-sm" style="flex:1; font-size:11px; padding:3px 8px; background:#22c55e; color:#fff; border:none; border-radius:4px; cursor:pointer;" onclick="event.stopPropagation(); moveTask('${t.id}', 'backlog')" title="Promote to Backlog">↑ Promote</button>`
                    + `<button class="btn btn-sm" style="flex:1; font-size:11px; padding:3px 8px; background:transparent; color:#6b7280; border:1px solid var(--border); border-radius:4px; cursor:pointer;" onclick="event.stopPropagation(); moveTask('${t.id}', 'done')" title="Dismiss">✕ Dismiss</button>`
                    + '</div>';
            }

            var bottomRow = '';
            if (isSuggested) {
                bottomRow = inlineActions;
            } else {
                // No delete on a gate: removing the sentinel does not release its
                // dependents, it STRANDS them (a task whose parent row is missing can
                // never satisfy its dependency). The API refuses this too.
                var delBtn = isGate ? ''
                    : '<button class="kanban-move-btn" onclick="event.stopPropagation(); deleteTask(\'' + t.id + '\')" title="Delete" style="color:#ef4444;">✕</button>';
                bottomRow = '<div style="display:flex; justify-content:space-between; align-items:center; margin-top:6px;">'
                    + '<div style="display:flex; gap:4px;">' + arrows + msgBtn + '</div>'
                    + delBtn
                    + '</div>';
            }

            var stageBadge = _stagePill(t.status);
            var snapAttr = JSON.stringify(t).replace(/"/g, '&quot;');
            return '<div class="kanban-card" data-task-id="' + esc(t.id) + '" data-status="' + esc(status) + '" data-snapshot="' + snapAttr + '" style="border-left:3px solid ' + pColor + '; cursor:pointer; position:relative;" onclick="openEditTaskModal(' + snapAttr + ')">'
                + checkBox
                + oracleBadges
                + '<div class="kanban-card-title" title="' + esc(t.title) + '">' + icon + ' ' + esc(t.title) + '</div>'
                + reaperBar
                + '<div class="kanban-card-meta">'
                + '<span class="badge badge-info">' + esc(t.task_type) + '</span>'
                + '<span class="badge" style="background:' + pColor + '20; color:' + pColor + ';">' + esc(t.priority) + '</span>'
                + execBadge
                + llmBadge
                + attemptBadge
                + phantomBadge
                + stageBadge
                + '</div>'
                + depLine
                + tifLine
                + lastKilledLine
                + schedLine
                + targetLine
                + changeMetrics
                + tagChips
                + execIdLine
                + bottomRow
                + '</div>';
        });
        var cardsHtml = cardsHtmlArr.join('');

        if (status === 'suggested') {
            var sortOpts = [
                { v: 'value',       l: 'Sort: Value ▼' },
                { v: 'confidence',  l: 'Sort: Confidence ▼' },
                { v: 'priority',    l: 'Sort: Priority ▲' },
                { v: 'created_at',  l: 'Sort: Newest first' }
            ];
            var sortSelect = '<select onchange="onSuggestedSortChange(this.value)" style="font-size:11px; padding:3px 6px; background:var(--bg-code); border:1px solid var(--border); border-radius:4px; color:var(--text-primary); cursor:pointer;">'
                + sortOpts.map(function(o) {
                    return '<option value="' + o.v + '"' + (_suggestedSort === o.v ? ' selected' : '') + '>' + o.l + '</option>';
                }).join('')
                + '</select>';
            var toolbar = '<div class="suggested-toolbar" style="padding:6px 8px; background:var(--bg-code); border-bottom:1px solid var(--border); display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-bottom:6px;">'
                + sortSelect
                + '<select onchange="applySuggestedSelectPreset(this.value); this.value=\'\';" style="font-size:11px; padding:3px 6px; background:var(--bg-code); border:1px solid var(--border); border-radius:4px; color:var(--text-primary); cursor:pointer;">'
                + '<option value="">Select…</option>'
                + '<option value="all">All</option>'
                + '<option value="conf95">Confidence ≥ 95%</option>'
                + '<option value="conf90">Confidence ≥ 90%</option>'
                + '<option value="value10">Value ≥ 1.0</option>'
                + '<option value="value08">Value ≥ 0.8</option>'
                + '<option value="value_lo">Value &lt; 0.6</option>'
                + '<option value="pri_crit">Priority: Critical</option>'
                + '<option value="pri_high">Priority: High</option>'
                + '<option value="pri_med">Priority: Medium</option>'
                + '<option value="pri_low">Priority: Low</option>'
                + '</select>'
                + '<button class="btn btn-sm" onclick="bulkPromoteSuggested()" style="font-size:11px; padding:3px 8px; background:#22c55e; color:#fff; border:none; border-radius:4px; cursor:pointer;">↑ Promote <span data-bulk-count>0</span></button>'
                + '<button class="btn btn-sm" onclick="bulkDismissSuggested()" style="font-size:11px; padding:3px 8px; background:transparent; color:#6b7280; border:1px solid var(--border); border-radius:4px; cursor:pointer;">✕ Dismiss <span data-bulk-count>0</span></button>'
                + '</div>';
            bodyEl.innerHTML = toolbar + cardsHtml;
        } else if (status === 'in_progress') {
            // Group by executor type
            var execGroups = {};
            items.forEach(function(t, i) {
                var et = t.executor_type || 'claude_cli';
                if (!execGroups[et]) execGroups[et] = {cards: [], label: (executorLabels[et] || {icon:'⚙️', label: et})};
                execGroups[et].cards.push(cardsHtmlArr[i]);
            });
            var groupHtml = '';
            Object.keys(execGroups).forEach(function(et) {
                var grp = execGroups[et];
                groupHtml += '<div style="margin-bottom:4px;">'
                    + '<div style="font-size:10px;color:var(--text-dim);padding:4px 0 2px;font-weight:600;border-bottom:1px solid var(--border);margin-bottom:4px;">'
                    + grp.label.icon + ' ' + grp.label.label + ' (' + grp.cards.length + ')</div>'
                    + grp.cards.join('')
                    + '</div>';
            });
            bodyEl.innerHTML = groupHtml || '<div class="kanban-empty">No tasks</div>';
        } else {
            bodyEl.innerHTML = cardsHtml;
        }
    });
    _setupKvCounters(board);
}
