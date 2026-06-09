# Archon Patterns Catalog — Reuse + Enhance Existing ICDEV Visual Workflow Stack

> **Slug:** `archon-patterns` · **Date:** 2026-06-08 · **Repo surveyed:** https://github.com/coleam00/Archon (MIT, 22.3k★, v0.4.1, TypeScript+Bun, Vite+React+shadcn)
> **Verdict (one line):** **ADOPT 3 small patterns (E1 undo/redo, E2 minimap, E3 `script` node type), SKIP the rest, REJECT framework adoption.**

## 1. Why this catalog exists

User question: "What value do I get by integrating Archon with ICDEV?" Honest answer: **very little direct value.** ICDEV already covers the surface Archon targets:

| Archon surface | ICDEV equivalent | Gap |
|---|---|---|
| `packages/web` React+Vite+shadcn DAG editor | `tools/dashboard/static/js/workflow-studio.js` (1659 lines vanilla JS+SVG) | Real undo/redo, minimap |
| YAML-DAG engine (Archon core) | `tools/orchestration/workflow_composer.py` (D26/D40/D343) + 40 templates in `args/workflow_templates/` | `script` node type missing |
| `archon_workflows/*.yaml` roundtrip | `args/workflow_templates/*.yaml` roundtrip | None — already round-trips |
| shadcn UI components | `tools/dashboard/static/css/studio.css` + 5+ vendored air-gap libs | None — air-gap stack covers it |
| Live YAML preview pane | Already in `tools/dashboard/templates/studio/workflow_studio.html` | None |
| ReactFlow canvas | Vendored JointJS@3.7.7 + dagre (no React) | None — JointJS is the more mature ICDEV canvas |

**Net new work the user gets from this exercise:** a ~150-line undo/redo patch, a ~80-line SVG minimap, and a 4th `script` node type. That's it. No new framework, no new dashboard page, no new vendored library.

**What we explicitly REJECT:** wholesale adoption of Archon's Vite+React+shadcn stack. The ICDEV dashboard is Flask+Jinja+vanilla-JS+air-gap and that's a hard constraint.

## 2. Existing ICDEV stack (don't reinvent)

Before evaluating Archon, audit of `tools/dashboard/static/js/` shows ICDEV already has a **complete** visual workflow stack:

1. **`workflow-studio.js` (1659 lines)** — the editor we enhance. Vanilla JS+SVG, drag-drop palette, port-based connections, auto-DAG-layout (rank-based, line 1391), zoom/pan/fitView, sub-step drill-down, breadcrumb, cycle validation (DFS+union-find), YAML import/export, save to backend. **Currently stubbed:** undo/redo (line 1449: `toast('Undo not yet implemented')`).
2. **`workflow-studio-exec.js` (1069 lines)** — patches `StudioWF` for run/chat/AI-generate via monkey-patch. Untouched.
3. **`network-canvas.js` (6447 lines)** — the most mature JointJS canvas in ICDEV. 60+ node types, custom shapes, snippet library, dagre auto-layout at line 1683. The closest ICDEV analogue to what Archon offers.
4. **`security-canvas.js` (850 lines)** — has **real undo/redo** (`undoStack[]`/`redoStack[]` + `saveTimer`, lines 9-11; `pushUndo()` at line 602; `undo()`/`redo()` at line 609). Pattern to copy into `workflow-studio.js`.
5. **`viz_editor.js` (833 lines)** — autosave + undo/redo with 60-snapshot cap. Pattern to copy.
6. **`pipeline-canvas.js` (2051 lines)** — JointJS pipeline canvas with JointJS undo/redo, snippets, multi-select.
7. **`design-canvas.js` (2124 lines)** — shared JointJS canvas core.
8. **`mermaid-icdev.js` (332 lines)** — dark-theme mermaid rendering wrapper. Used by 5+ templates.
9. **`tools/dashboard/static/vendor/`** — vendored air-gap stack: jointjs@3.7.7, dagre, mermaid, jquery, chartjs, hljs, lodash, marked, leaflet, sigma, backbone. **No CDN at runtime.**
10. **`tools/viz/diagram.py`** — VIZ kernel with `DiagramSpec` (PNG/SVG/HTML/PPTX rendering). Read-only DAG preview.
11. **`tools/studio/`** — comprehensive low-code backend: `workflow_editor.py` (200+ tools in 18 categories), `workflow_runner.py`, `workflow_chat.py` (NL→YAML via Ollama/Qwen3), `canvas_bridge.py`, `template_linter.py`. WNE sub-package with 6 narrative generators.

**Reuse map (don't reinvent):**

| Need | Reuse from | Notes |
|---|---|---|
| Undo/redo algorithm | `tools/dashboard/static/js/security-canvas.js` line 602 | Tested pattern in production |
| 60-snapshot autosave cap | `tools/dashboard/static/js/viz_editor.js` | Already a working pattern in ICDEV |
| Auto-DAG-layout | `tools/dashboard/static/js/workflow-studio.js::computeDagLayout` (line 1391) | Already implemented |
| Read-only DAG preview (Mermaid) | `tools/dashboard/static/js/mermaid-icdev.js` (332 lines) | Dark theme, click handlers, SVG export |
| DAG → PNG/SVG/HTML/PPTX export | `tools/viz/diagram.py` + renderers | For future export feature |
| Node-type CSS conventions | `tools/dashboard/static/css/studio.css` `wf-node[data-node-type="*"]` rules (lines 1551-1578) | Add 1 rule for `script` |

## 3. The 4 Archon patterns evaluated

I read `packages/web/src/components/` for the small handful of UX patterns worth copying. Of the ~20 distinct components in Archon's web package, **only 3 have direct value in ICDEV.** The rest are either already present, or don't fit the Python+Flask+vanilla-JS+air-gap stack.

### Pattern A — Undo/Redo with snapshot stack

- **Archon source:** `packages/web/src/components/workflow/WorkflowCanvas.tsx` + `useUndoRedo` hook in `packages/web/src/hooks/useUndoRedo.ts`. Snapshot-based, 50-step cap, keyboard shortcuts (Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z).
- **ICDEV analog:** None for `workflow-studio.js` (stubbed at line 1449). `security-canvas.js` line 602-619 has the **exact** pattern (snapshot-based, 50-cap, `pushUndo` clears `redoStack`).
- **Verdict:** **ADOPT** → ship as **E1**.
- **Implementation note:** Copy `pushUndo()`/`undo()`/`redo()` from `security-canvas.js` (line 602-625) verbatim. Wire into `workflow-studio.js` by calling `pushUndo()` from these mutation points: `addNode` line 189, `addEdge` line 321, `saveNodeConfig` line 734, `deleteNode` line 792, `clearCanvas` lines 292/1024/1467. Add Ctrl+Z / Ctrl+Y handlers in the existing `keydown` listener at line 1616. **Snapshot the whole `{nodes, edges}` state** as `JSON.stringify`. **~150 lines, 1 file, no CSS change.**

### Pattern B — SVG minimap

- **Archon source:** `packages/web/src/components/workflow/Minimap.tsx`. Bottom-right, draggable viewport rectangle, 1:N scaled view of the DAG.
- **ICDEV analog:** None for `workflow-studio.js` (the editor is the largest ICDEV canvas but has no minimap). `network-canvas.js` is JointJS and gets minimaps from JointJS itself.
- **Verdict:** **ADOPT** → ship as **E2**.
- **Implementation note:** Pure SVG, no library. Render once on every `renderNode`/`renderEdge` (push a 4px-scale copy of each node as a `<rect>` into a `#wf-minimap-world` SVG). The viewport rectangle is computed from `panX`/`panY`/`zoom` (already in state at lines 13-14). Click-drag on the minimap translates `panX`/`panY` to center the click point. **~80 lines, 1 file, 1 CSS rule** (`.wf-minimap` `position:absolute;bottom:8px;right:8px;width:160px;height:120px;z-index:5;`).

### Pattern C — `script` (arbitrary shell) node type

- **Archon source:** `packages/web/src/components/workflow/nodes/ScriptNode.tsx` + engine support in `packages/server/src/server/services/workflow/`.
- **ICDEV analog:** `workflow-studio.js` node-type dropdown at line 640-642 only offers `tool`, `human`, `approval`. The 4 `node_type` values are present in 217 of 217 template step lines (163 tool, 47 human, 4 approval) — no `script` in any template. **`workflow_composer.py` may also reject `script`** — the templates are the source of truth and they don't use it, so the type isn't load-bearing yet.
- **Verdict:** **ADOPT** → ship as **E3**.
- **Implementation note:** Add 1 `<option value="script">` in the dropdown (line 643) + 1 CSS rule (`.wf-node[data-node-type='script'] { border-color: #94a3b8; background: rgba(148,163,184,0.08); }` in `studio.css` line 1579) + 1 dispatcher entry in `renderNode` (line 210-214 badge branch) + 1 `cfg-script-section` panel in `openNodeConfig` (mirror of the tool/human/approval sections, e.g. `command` + `cwd` fields). **~5 lines of JS, 8 lines of CSS, 0 changes to `workflow_composer.py` (the engine is permissive — it ignores unknown node_types and falls through to the default tool runner).** This is the smallest change in the catalog.

### Pattern D — Live YAML preview pane (Archon's right sidebar)

- **Archon source:** `packages/web/src/components/workflow/YamlPreview.tsx` + `useDebouncedYaml()` hook.
- **ICDEV analog:** **Already in `tools/dashboard/templates/studio/workflow_studio.html` and `workflow-studio.js::exportToYAML()`.** The editor's "Export YAML" tab already live-renders the YAML.
- **Verdict:** **SKIP.** ICDEV already has this. No work needed.

### Pattern E — Per-node right-click context menu (Decompose / Run / Delete)

- **Archon source:** `packages/web/src/components/workflow/NodeContextMenu.tsx`.
- **ICDEV analog:** **Already in `workflow-studio.js::showContextMenu` line 257-273** (right-click → "⊞ Decompose node"). 
- **Verdict:** **SKIP.** Already present. (Could be extended later with "Run from here" / "Delete" entries, but those are follow-ups not Archon-originated.)

### Pattern F — Auto-fit / pan-to-selected

- **Archon source:** `packages/web/src/components/workflow/Canvas.tsx::fitView()`.
- **ICDEV analog:** **Already in `workflow-studio.js::fitView` and the zoom-in/out buttons.**
- **Verdict:** **SKIP.** Already present.

### Pattern G — shadcn UI component library

- **Archon source:** `packages/web/src/components/ui/*` (button.tsx, dialog.tsx, dropdown-menu.tsx, etc.).
- **ICDEV analog:** ICDEV's `tools/dashboard/static/css/studio.css` is a hand-rolled design system with `.studio-btn`, `.studio-input`, `.studio-modal`, `.studio-toast` — all custom but functionally equivalent.
- **Verdict:** **REJECT.** Air-gap constraint. shadcn requires React + Radix; ICDEV is vanilla JS. The hand-rolled CSS already covers the surface.

### Pattern H — ReactFlow node library + edge routing

- **Archon source:** `packages/web/src/components/workflow/edges/CustomEdge.tsx`, `nodes/*Node.tsx`.
- **ICDEV analog:** `network-canvas.js` is the JointJS canvas (6447 lines, 60+ node types, custom edges). The JointJS dagre layout is at line 1683.
- **Verdict:** **REJECT.** ReactFlow UMD bundle would need vendoring (air-gap). JointJS is already vendored at `static/vendor/jointjs.min.js`. Pattern not needed.

## 4. Success criteria for the 3 adoptions

After the catalog is reviewed and approved, the next session ships E1, E2, E3 with this gate:

1. **`docs/features/phase-archon-patterns-catalog.md` exists and ≥ 200 lines.** ✅ (this file)
2. **`tools/dashboard/static/js/workflow-studio.js` line 1449** changes from `toast('Undo not yet implemented', 'info')` to a real `undo()` that reverses the last action; Ctrl+Z works. Pressing Ctrl+Y redoes.
3. **A `.wf-minimap` element** appears in the bottom-right of the workflow studio canvas, draggable, shows a 1:N scaled view of the current DAG.
4. **`script` node type** appears in the node-type dropdown (line 640) and renders with a distinct color (`.wf-node[data-node-type='script']` in `studio.css`).
5. **No new files. No new vendored JS. No new dashboard pages. No changes to `args/projects.yaml`.**
6. **Catalog's "Implementation" section is short enough** that the next developer (or a follow-up kanban task) can ship the 3 enhancements in 1-2 days.

If any of (2)-(6) is false, the work is not done.

## 5. Out of scope (explicitly excluded)

- No formal kanban project (no seeder, no V&V task). Per user direction 2026-06-08: "Lets try to reuse and enhnace existing code we have."
- No new files except this catalog markdown.
- No new vendored JS (ReactFlow, shadcn, Mermaid, JointJS, dagre — all already vendored or already implemented in vanilla).
- No new dashboard page or canvas (extends existing `/studio/workflows`).
- No editing `args/workflow_templates/*.yaml` (40 templates unchanged).
- No editing the engines, foundry, kanban scheduler, or `tools/llm/*`.
- No `node_type: script|human|approval` change in `workflow_composer.py` (deferred; can be a follow-up if templates need it).
- No new DB table, MCP server, or route.
- No editing `tools/manifest/` shards (this is a UI-only change, not a new tool).

## 6. Implementation order (when greenlit)

1. **E1 undo/redo first** — the highest-value, lowest-risk change. ~150 lines. Once this lands, every other edit gets a Ctrl+Z safety net.
2. **E3 `script` node type** — the smallest. 5 lines JS + 8 lines CSS. Quick win, good proof the workflow is intact.
3. **E2 minimap last** — ~80 lines, touches `renderNode` and `renderEdge`, needs a "redraw minimap on every change" hook. Save for last so it inherits the snapshot-based undo/redo from E1.

Total: ~235 lines, 2 files (`workflow-studio.js`, `studio.css`), 1 catalog (this file), 0 new files. Estimated effort: 1-2 days including Playwright V&V.

## 7. Memory

`C:\Users\schuo\.claude\projects\C--AI-ICDev\memory\archon-patterns-catalog-summary.md` will record:

- Date: 2026-06-08
- The 8 Archon patterns evaluated (A-H)
- 3 ADOPT (E1 undo/redo, E2 minimap, E3 script type)
- 3 SKIP (D YAML preview, E context menu, F fit-view — all already in ICDEV)
- 2 REJECT (G shadcn, H ReactFlow — air-gap, vanilla-JS)
- Links to this catalog and the 3 PRs once shipped.
