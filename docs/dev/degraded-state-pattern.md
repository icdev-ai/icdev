# Degraded-State Pattern — Fail Loud, Not Silent

**Status:** Active convention · **Applies to:** `tools/dashboard/app.py` and every Flask route/API handler in the dashboard.

## The problem

`tools/dashboard/app.py` historically wrapped data reads in bare
`except Exception: pass` (or `return []` / `return {}` / canned zeros). When the
database is unreachable, a table is missing, or a subsystem import fails, the
handler swallows the error and renders a page that looks **healthy but empty** —
zero findings, empty tables, "0" stat cards. An operator cannot tell "there is
genuinely no data" apart from "the data pipeline is down". Outages masquerade as
green. This is the single most dangerous failure mode for an observability
surface.

The fix is **fail loud, not silent**: an outage must (1) be logged and (2) be
visible to the consumer as an explicit error/degraded signal — never a silent
empty payload.

## The pattern

### API / JSON endpoints

Log via the module logger, then return an explicit degraded payload carrying
`error: true` and a `detail`. Keep the existing keys so the front-end does not
crash, but flip an unambiguous flag:

```python
from tools.logging.icdev_logger import get_logger

@app.route("/api/charts/translations")
def api_charts_translations():
    degraded = False
    detail = None
    try:
        ...  # normal reads
    except Exception as exc:
        degraded = True
        detail = str(exc)
        get_logger("icdev.dashboard").warning(
            "api_charts_translations: DB error: %s", exc
        )
    payload = {"status_distribution": status_dist, "language_pair_frequency": lang_pairs}
    if degraded:
        payload["error"] = True
        payload["detail"] = detail
    return jsonify(payload)
```

For a hard subsystem outage where no partial data is meaningful, return HTTP
`503` with the same shape (see the **macro 503** landed example, below).

### Page routes

Set an `error=True` (or `degraded=True`) flag into the template context and log.
The template renders a `role="alert"` banner so the operator sees the failure:

```python
@app.route("/evidence")
def evidence_page():
    stats = {...}          # safe defaults
    degraded = False
    try:
        ...
    except Exception as exc:
        degraded = True
        get_logger("icdev.dashboard").warning("evidence_page: DB error: %s", exc)
    return render_template("evidence.html", stats=stats, degraded=degraded)
```

In the template, add one line at the top of the content block:

```jinja
{% include "includes/_degraded_banner.html" %}
```

The include (`tools/dashboard/templates/includes/_degraded_banner.html`) renders
nothing when `degraded`/`error` is falsy, and a `role="alert"` banner when true.

## Landed reference examples

These already ship on `main` and are the canonical templates to copy:

| Example | File / symbol | Shape |
|---------|---------------|-------|
| **finetune route** | `app.py::finetune_overview_page` | Page: `error = True` in the `except`, logs via `get_logger("icdev.dashboard").warning(...)`, passes `error=error` to `finetune/index.html`. |
| **macro 503** | `app.py::api_macro_intelligence` | API: a data outage returns `{"status": "error", "detail": ..., ...null badges}, 503` and `app.logger.exception(...)` — a data gap must never render as a benign `NEUTRAL`. |
| **monitoring page** | `app.py::monitoring_overview` | Page: logs `.error(...)` and returns an explicit empty context with `health_status="unknown"` (not a silent healthy-looking page). |
| **AISG roi_tracker** | `tools/ai_augmentation/.../roi_tracker` | Returns `state: "error"` rather than an empty success payload. |

## What NOT to convert (leave as best-effort)

Not every `except: pass` is a bug. Leave these silent:

- **Best-effort telemetry / audit inserts** — a failed audit write must not break
  the request path (it is already logged elsewhere / append-only).
- **Optional-feature probes** — a sub-section that degrades to an empty list while
  the rest of the page renders correctly (e.g. per-canvas trend overlays, an
  optional recommendation feed). These are *designed* to degrade per-source.
- **Blocks that already log** — do not double-log.

The rule of thumb: convert the block if a failure there **silently empties the
entire page or the primary payload**. Leave it if the failure only drops one
optional widget while the main content still renders.

## Checklist

1. Does this `except` empty the whole page / primary payload? → convert.
2. Log once via `get_logger("icdev.dashboard")` (or `app.logger`) at `warning`/`exception`.
3. Surface it: `error`/`detail` in the JSON payload, or `degraded=True` context +
   `{% include "includes/_degraded_banner.html" %}` in the page template.
4. Keep safe defaults so the template/JS never crashes on the degraded path.
