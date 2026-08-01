# [TEMPLATE: CUI // SP-CTI]
"""Lazy canvas blueprint loader (cvx-net-02).

Defers each design-canvas blueprint's *module import* until the first request
that targets that canvas, instead of importing every enabled canvas module at
dashboard boot. The heavy canvas (network / ndc) alone imports a large module
and, on a cold database, seeds tables — all on the startup critical path.

Only canvases that declare a non-empty ``url_prefix`` in the component registry
are deferred, because the loader matches an incoming request to a canvas by URL
path prefix. Empty-prefix canvases self-prefix inside their blueprint (which is
unknowable without importing the module) and are cheap, so the caller keeps them
eager.

Design constraints honored:
  * ``url_for`` is preserved. No shared/base template calls ``url_for`` for a
    canvas endpoint; every canvas endpoint is referenced only from that same
    canvas's own templates, which cannot render until the canvas is loaded (its
    blueprint already registered). Cross-canvas ``url_for`` before load never
    occurs.
  * Flask locks blueprint registration after the first request
    (``_check_setup_finished`` raises once ``_got_first_request`` is True). The
    loader briefly clears that flag under a lock — long enough to register one
    blueprint — then restores it. Normal request dispatch never reads the flag,
    so concurrent in-flight requests are unaffected.
  * The canvas access guard is attached as a blueprint ``before_request`` exactly
    as in the eager path. Flask fixes the applicable ``before_request`` list at
    the start of preprocessing — before the blueprint is registered — so on the
    first hit the loader invokes the now-applicable blueprint hooks explicitly.
    Subsequent requests run them through Flask's normal preprocessing.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Iterable


def install_lazy_canvas_loader(
    app: Any,
    components: Iterable[Any],
    url_prefixes: dict[str, str],
    guard_component_access: Callable[[str, str], Callable] | None = None,
) -> set[str]:
    """Register a ``before_request`` hook that imports+registers canvases on first hit.

    Args:
        app: the Flask application.
        components: iterable of registry ``Component`` objects (canvas kind). Each
            must expose ``key``, ``min_il``, and ``get_blueprint()``.
        url_prefixes: mapping of component key -> url_prefix.
        guard_component_access: optional factory ``(key, min_il) -> before_func``.

    Returns:
        The set tracking which canvas keys have been loaded (also stored on
        ``app.extensions['icdev_lazy_canvases']['loaded']``).
    """
    comp_by_key = {c.key: c for c in components}
    prefixes = {
        c.key: ((url_prefixes.get(c.key) or f"/{c.key}").rstrip("/") or f"/{c.key}")
        for c in comp_by_key.values()
    }
    loaded: set[str] = set()
    lock = threading.Lock()

    def _match_prefix(path: str) -> str | None:
        """Return the key of the longest unloaded canvas prefix matching path."""
        best_key, best_len = None, -1
        for key, pfx in prefixes.items():
            if key in loaded or not pfx:
                continue
            if path == pfx or path.startswith(pfx + "/"):
                if len(pfx) > best_len:
                    best_key, best_len = key, len(pfx)
        return best_key

    def _load(key: str) -> None:
        comp = comp_by_key[key]
        bp = comp.get_blueprint()
        if not bp:
            return
        pfx = prefixes[key]
        if guard_component_access is not None:
            try:
                bp.before_request(guard_component_access(key, comp.min_il))
            except Exception as exc:  # noqa: BLE001
                app.logger.warning("Lazy canvas %s guard attach failed: %s", key, exc)
        # Temporarily re-open app setup so register_blueprint is allowed after the
        # first request has been served. Safe: held under `lock`, and normal
        # dispatch never inspects _got_first_request.
        _prev_setup = app._got_first_request
        app._got_first_request = False
        try:
            if not getattr(bp, "url_prefix", None):
                app.register_blueprint(bp, url_prefix=pfx)
            else:
                app.register_blueprint(bp)
        finally:
            app._got_first_request = _prev_setup
        app.logger.info("Canvas %s lazily registered at %s/ on first hit", key.upper(), pfx)

    @app.before_request
    def _lazy_canvas_before_request():  # noqa: ANN202
        from flask import request

        key = _match_prefix(request.path)
        if key is None:
            return None
        with lock:
            if key not in loaded:
                try:
                    _load(key)
                except Exception as exc:  # noqa: BLE001
                    app.logger.warning("Lazy canvas %s load failed: %s", key, exc)
                # Mark loaded regardless so a broken canvas 404s instead of
                # re-importing on every request (retry storm).
                loaded.add(key)
        # Re-match this request against the now-updated url_map so Flask dispatches
        # to the freshly registered view (match_request only sets routing_exception
        # on failure — clear the stale one first).
        try:
            from flask.globals import request_ctx
        except Exception:  # pragma: no cover
            from flask import request_ctx  # type: ignore[attr-defined]
        request.routing_exception = None
        request_ctx.match_request()
        # First-hit only: run the blueprint before_request funcs (e.g. the canvas
        # access guard) that Flask's preprocessing already skipped for this request.
        for _bp_name in reversed(request.blueprints):
            for _func in app.before_request_funcs.get(_bp_name, ()):
                _rv = app.ensure_sync(_func)()
                if _rv is not None:
                    return _rv
        return None

    app.extensions.setdefault("icdev_lazy_canvases", {})["loaded"] = loaded
    return loaded
