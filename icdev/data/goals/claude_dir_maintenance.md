# .claude Directory Maintenance — Per-Phase Governance Checklist

**Standards:** NIST 800-53 AU-2 (Auditable Events), CM-3 (Configuration Change Control), SA-11 (Developer Testing)

---

## Purpose

Every new ICDEV phase adds DB tables, dashboard routes, hooks, and commands. Without explicit governance, the `.claude` directory drifts from the codebase — leaving append-only tables unprotected, routes undocumented, and hooks broken. This goal defines the mandatory checklist that must be completed before any phase is declared done.

---

## Mandatory Per-Phase Checklist

Before declaring any phase complete, verify each item:

### Append-Only Tables (D6 — NIST AU)
- [ ] **New append-only/immutable DB table?** Add the table name to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
- [ ] The table's `CREATE TABLE` statement in `init_icdev_db.py` must include an `-- append-only` or `-- immutable` comment in the 10 preceding lines

### Dashboard Routes
- [ ] **New dashboard page route?** Add to the `Pages:` line in `.claude/commands/start.md`
- [ ] Also update the CLAUDE.md `Dashboard pages` section if the route is user-facing

### E2E Test Specs
- [ ] **New dashboard feature group?** Create an E2E test spec in `.claude/commands/e2e/<name>.md`
- [ ] The spec must verify CUI banners, core functionality, and navigation

### Hook Files
- [ ] **New or renamed hook file?** Update the path reference in `.claude/settings.json` under the `hooks` key
- [ ] All `.claude/hooks/*.py` files must pass `ast.parse()` syntax validation

### Deny Rules
- [ ] **New destructive operation?** Add a deny pattern to `.claude/settings.json` `permissions.deny` list

### Final Validation
- [ ] **Run the governance validator:**
  ```bash
  python tools/testing/claude_dir_validator.py --json
  ```
  Exit code must be `0` (all checks pass). Warnings are acceptable; failures are not.

---

## Validator Details

**Tool:** `tools/testing/claude_dir_validator.py`
**Tests:** `tests/test_claude_dir_validator.py` (50 tests)
**Gate:** `claude_config_alignment` in `args/security_gates.yaml`

### 6 Automated Checks

| Check | Severity | What It Validates |
|-------|----------|-------------------|
| `append-only` | **Blocking** | All append-only tables in `init_icdev_db.py` are in `pre_tool_use.py` APPEND_ONLY_TABLES |
| `hooks-syntax` | **Blocking** | All `.claude/hooks/*.py` files parse without SyntaxError |
| `hooks-refs` | **Blocking** | All hook commands in `settings.json` reference existing files |
| `routes` | Warning | All `@app.route()` page routes in `app.py` are listed in `start.md` |
| `e2e` | Warning | Major dashboard page groups have E2E test specs |
| `settings` | Warning | Required deny patterns (rm -rf, DROP TABLE, etc.) are in settings.json |

### Running Individual Checks
```bash
python tools/testing/claude_dir_validator.py --check append-only --json
python tools/testing/claude_dir_validator.py --check hooks-syntax --json
python tools/testing/claude_dir_validator.py --check routes --json
python tools/testing/claude_dir_validator.py --human   # All checks, terminal output
```

---

## When to Run

1. **After every phase implementation** — before declaring the phase complete
2. **After modifying `.claude/` files** — hooks, settings, commands
3. **After adding DB tables** — especially tables with audit/log semantics
4. **In CI/CD** — as a pre-merge gate check (future integration with test_orchestrator.py)
