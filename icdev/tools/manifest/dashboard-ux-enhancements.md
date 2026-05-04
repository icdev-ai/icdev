# Dashboard UX Enhancements (OPT-68, 2026-04-12)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Dashboard UX Enhancements (OPT-68, 2026-04-12)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Debounce Filter JS | tools/dashboard/static/js/debounce_filter.js | Filter-as-you-type helper (react-admin useListFilter pattern, MIT). Binds input to onFilter with 250ms debounce | ICDEV.debounceFilter.bind / bindForm | Live DOM updates |
| Undo Toast JS | tools/dashboard/static/js/undo_toast.js | 5-second Snackbar with Undo callback (react-admin undoable mutation pattern, MIT) | ICDEV.undoToast.show({message, undoCallback, durationMs}) | DOM toast element |
| Filter Presets JS | tools/dashboard/static/js/filter_presets.js | Save/load named filter presets to localStorage, per-page namespace (react-admin saved queries pattern, MIT) | ICDEV.filterPresets.init({key, getCurrent, onApply, selectEl}) | {save, deleteCurrent, applyByName, list, refresh} |

