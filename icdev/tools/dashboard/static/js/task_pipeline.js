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
  return stepper + branch + metaHtml + prBtn + prBox + timeline;
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
