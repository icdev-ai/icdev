# Which parent IS this checkout, and may it touch THIS database? (xit-decl-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m icdev.core.context --check          # exit 1 on a DECLARED mismatch
python -m icdev.core.context --check --json
icdev status                                  # first line names the domain, its root and the identity verdict
```

Every ICDEV parent declares itself in `icdev_domain.yaml` at its repo root
(this repo = ICDEV[IT]; ICDEV[FT] is the second parent — docs/programmes/
icdev-domain-split.md). The domain KEY comes from the FILE the core finds,
never from an env var, so two parents on one machine cannot cross-load.
icdev/core/paths.py is the ONE root resolver (ICDEV_PROJECT_ROOT -> the
source checkout holding the calling file -> the nearest icdev_domain.yaml
above CWD, for an installed kernel -> icdev/); icdev/_paths.py and
tools/llm/config_path.py are delegates onto it. `assert_identity()` runs at
dashboard, genesis daemon, migrate.py and kanban/cli.py start-up and REFUSES
when <PREFIX>_PG_DATABASE / the DSN names a database the declaration does
not list. It is fail-closed on a declared mismatch ONLY: an env that names no
database is `unmeasured` and allowed (SQLite, CI, tests). Stand it down with
ICDEV_IDENTITY_GUARD=0, never a shell neutraliser. A wheel or scaffolded
project with no declaration gets the builtin ICDEV[IT] default
(`source: builtin_default`); ICDEV_REQUIRE_DOMAIN=1 refuses to run undeclared.
