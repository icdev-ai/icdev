# Plan: ICDEV Dev Profiles × Cursor AI Integration

## Context
Cursor AI publishes `.cursorrules` and `.cursor/rules/*.mdc` markdown files that define editor AI behavior, coding standards, and best practices. ICDEV already has a robust 5-layer cascade dev profile system (`tools/builder/dev_profile_manager.py`, `/dev-profiles` dashboard page) and static `.cursor/rules/icdev.mdc` files, but the two are not wired together.

## Goal
Run the **Innovation**, **Creative**, and **Research** engines to analyze the opportunity, then implement a bridge that lets users export resolved ICDEV dev profiles as Cursor AI `.cursorrules` or `.mdc` files directly from the `/dev-profiles` dashboard.

## Phase 1 — Run the Three Engines

### Innovation Engine (`tools/innovation/innovation_manager.py --run --json`)
- **DISCOVER**: Scan for how other AI-native IDEs (Cursor, Windsurf, GitHub Copilot Workspace) handle dev profiles.
- **SCORE + TRIAGE**: Rank opportunities by impact vs. effort.
- **GENERATE**: Produce a shortlist of the top 3 integration patterns.
- **Output**: `context/innovation/cursor_devprofile_opportunities.json`

### Creative Engine (`tools/creative/creative_engine.py --run --domain "developer experience" --json`)
- **DISCOVER**: Crawl Cursor AI public docs and `.cursorrules` repos for best-practice patterns.
- **EXTRACT + SCORE**: Identify which ICDEV profile dimensions (style, security, testing, architecture, compliance) map cleanly to Cursor rule categories.
- **GENERATE**: Draft the `.cursorrules` / `.mdc` template spec for each dimension.
- **Output**: `context/creative/cursor_profile_spec.yaml`

### Research Engine (`tools/research/research_engine.py --run --vertical "AI IDE configuration" --json`)
- **SCOPE + LANDSCAPE**: Research Cursor AI's profile format, variable substitution, globs, and rule precedence.
- **REGULATE + COMMUNITY**: Check if Cursor rules can reference external files (e.g., ICDEV tenant config).
- **BUILD_BUY + SYNTHESIZE**: Decide whether to generate static files or maintain a live sync endpoint.
- **Output**: `context/research/cursor_format_dossier.md`

## Phase 2 — Design the Bridge

### Decision Points
1. **Format**: `.cursorrules` (project root, single file) vs. `.cursor/rules/<scope>.mdc` (multi-file, globs)?
   - *Recommendation*: Support both. `.cursorrules` for simple projects; `.mdc` for multi-scope (tenant vs. project) with `globs`.
2. **Sync mode**: One-time export (download) vs. live companion sync?
   - *Recommendation*: Start with one-time export button on `/dev-profiles`, then add a companion sync toggle.
3. **Dimension mapping**: Which `dev_profile` dimensions become Cursor rules?
   - `style` → line length, naming conventions, formatter
   - `security` → SAST commands, secret scanning, compliance level
   - `testing` → test commands, coverage threshold
   - `architecture` → framework, import conventions, layer rules
   - `compliance` → classification markings, audit requirements

### New Artifacts
- `tools/builder/cursor_profile_generator.py` — Core exporter. Accepts a resolved profile JSON and emits `.cursorrules` or `.mdc` string.
- `args/cursor_export_config.yaml` — Declarative mapping from dev_profile dimensions to Cursor rule blocks.
- New API endpoint: `GET /dev-profiles/api/export/cursor/<scope>/<scope_id>`
- Dashboard UI: "Export to Cursor" button with format selector (`.cursorrules` / `.mdc`) on `/dev-profiles`.

## Phase 3 — Implement

### Step 1: Core Exporter (`tools/builder/cursor_profile_generator.py`)
- Import `dev_profile_manager.resolve_profile()`
- Map dimensions via `args/cursor_export_config.yaml`
- Render Jinja2 template (`context/templates/cursorrules.j2` and `cursor_mdc.j2`)
- CLI: `python tools/builder/cursor_profile_generator.py --scope project --scope-id proj-123 --format cursorrules --json`

### Step 2: API Endpoint (`tools/dashboard/app.py`)
- Add `@app.route("/dev-profiles/api/export/cursor/<scope>/<scope_id>")`
- Query param `?format=cursorrules|mdc`
- Returns `text/plain` with downloadable attachment headers, or JSON with `content` + `filename`.

### Step 3: Dashboard UI (`tools/dashboard/templates/dev_profiles.html`)
- Add "Export to Cursor" button next to each resolved profile in the Resolve Cascade section.
- Modal with format choice and copy-to-clipboard.

### Step 4: Config + Templates
- `args/cursor_export_config.yaml` — dimension-to-rule mapping
- `context/templates/cursorrules.j2` — Jinja2 template for `.cursorrules`
- `context/templates/cursor_mdc.j2` — Jinja2 template for `.mdc`

### Step 5: Register and Test
- Add tool to `tools/manifest/builder.md`
- Add CLI to `docs/reference/commands.md`
- Add endpoint to nav/parent link if needed
- Add tests in `tests/test_dev_profile_manager.py` or new `tests/test_cursor_profile_generator.py`
- Run `python tools/dx/companion.py --sync --write --json`
- Run `python tools/workflow/coherence_checker.py --all --fix --gate`

## Acceptance Criteria
1. Running the three engines produces three output artifacts in `context/`.
2. `python tools/builder/cursor_profile_generator.py --scope project --scope-id <id> --format cursorrules` prints a valid `.cursorrules` file.
3. The `/dev-profiles` page has an "Export to Cursor" button that returns downloadable content.
4. The exported Cursor rules reflect the 5-layer resolved profile (not just the project-level raw profile).
5. Coherence checker passes; companion sync succeeds.

## Estimated Effort
- Phase 1 (engines): ~15 min per engine = 45 min
- Phase 2 (design): ~15 min
- Phase 3 (implement): ~60 min
- **Total**: ~2 hours
