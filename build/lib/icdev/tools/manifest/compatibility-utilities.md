# Compatibility Utilities (D145)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Compatibility Utilities (D145)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Platform Utils | tools/compat/platform_utils.py | OS detection, temp/home/data dirs, UTF-8 console (D145) | (library) | IS_WINDOWS, IS_LINUX, etc. |
| Datetime Utils | tools/compat/datetime_utils.py | Cross-platform datetime helpers | (library) | UTC-safe datetime funcs |
| DB Utils | tools/compat/db_utils.py | Centralized DB path resolution with env var > explicit > default fallback chain | (library) | get_icdev_db_path(), get_memory_db_path(), get_platform_db_path() |

