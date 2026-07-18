# [TEMPLATE: CUI // SP-CTI]
"""Unit tests for the lazy canvas blueprint loader (cvx-net-02).

Proves the three acceptance criteria without booting the full dashboard:
  1. Deferral — a canvas module is NOT imported at app setup; it appears in
     ``sys.modules`` only after the first request to that canvas.
  2. Post-first-request registration — a canvas hit AFTER Flask has locked setup
     (``_got_first_request`` True) still loads and serves correctly.
  3. ``url_for`` — resolves for a canvas's routes once the canvas is loaded, and
     the canvas access guard runs on the very first hit.

The fake canvases are written to a temp dir on ``sys.path`` so nothing else in
the process imports them, making the ``sys.modules`` assertions meaningful.
"""
from __future__ import annotations

import importlib
import sys
import textwrap

from flask import Flask, url_for

from tools.dashboard.lazy_canvas import install_lazy_canvas_loader


class _Comp:
    """Minimal stand-in for a registry Component the loader consumes."""

    def __init__(self, key: str, module: str, min_il: str = "IL2"):
        self.key = key
        self.module = module
        self.min_il = min_il

    def get_blueprint(self):
        return importlib.import_module(self.module).bp


def _write_fake_canvas(dir_path, modname: str, bp_name: str) -> None:
    src = textwrap.dedent(
        f'''
        from flask import Blueprint, url_for

        bp = Blueprint("{bp_name}", __name__)

        @bp.route("/")
        def index():
            return "index-{bp_name}"

        @bp.route("/detail/<int:i>")
        def detail(i):
            return "detail-%d link=%s" % (i, url_for("{bp_name}.index"))
        '''
    )
    (dir_path / f"{modname}.py").write_text(src, encoding="utf-8")


def test_lazy_canvas_defers_import_and_registers_after_setup_lock(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_fake_canvas(tmp_path, "lazymod_a", "lazy_a")
    _write_fake_canvas(tmp_path, "lazymod_b", "lazy_b")
    for mod in ("lazymod_a", "lazymod_b"):
        sys.modules.pop(mod, None)

    app = Flask(__name__)
    comps = [_Comp("acan", "lazymod_a"), _Comp("bcan", "lazymod_b")]
    loaded = install_lazy_canvas_loader(app, comps, {"acan": "/acan", "bcan": "/bcan"})
    client = app.test_client()

    # (1) Deferral: neither module imported at setup, no canvas rules registered.
    assert "lazymod_a" not in sys.modules
    assert "lazymod_b" not in sys.modules
    assert not any(str(r.rule).startswith("/acan") for r in app.url_map.iter_rules())

    # First request to A loads only A.
    r = client.get("/acan/")
    assert r.status_code == 200
    assert b"index-lazy_a" in r.data
    assert "lazymod_a" in sys.modules
    assert "lazymod_b" not in sys.modules  # B still deferred
    assert app._got_first_request is True  # Flask has now locked setup

    # (2) Hitting B AFTER the first request proves registration works past the
    # setup lock (register_blueprint would otherwise raise).
    r2 = client.get("/bcan/detail/9")
    assert r2.status_code == 200
    assert b"detail-9" in r2.data
    assert "lazymod_b" in sys.modules
    assert loaded == {"acan", "bcan"}

    # (3) url_for resolves for both loaded canvases; and it resolved inside the
    # B view render itself (the link in the body).
    assert b"/bcan/" in r2.data
    with app.test_request_context():
        assert url_for("lazy_a.index") == "/acan/"
        assert url_for("lazy_b.index") == "/bcan/"


def test_lazy_canvas_runs_guard_on_first_and_later_hits(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_fake_canvas(tmp_path, "lazymod_g", "lazy_g")
    sys.modules.pop("lazymod_g", None)

    calls: list[tuple[str, str]] = []

    def guard_factory(key, min_il):
        def _guard():
            calls.append((key, min_il))
            return None

        return _guard

    app = Flask(__name__)
    install_lazy_canvas_loader(
        app, [_Comp("gcan", "lazymod_g")], {"gcan": "/gcan"}, guard_factory
    )
    client = app.test_client()

    # First hit: loader must invoke the blueprint guard explicitly (Flask fixed
    # its preprocess list before the blueprint existed).
    r1 = client.get("/gcan/")
    assert r1.status_code == 200
    assert calls == [("gcan", "IL2")]

    # Later hit: guard runs via Flask's normal preprocessing (not double-run).
    r2 = client.get("/gcan/detail/1")
    assert r2.status_code == 200
    assert calls == [("gcan", "IL2"), ("gcan", "IL2")]


def test_unknown_path_does_not_trigger_any_load(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_fake_canvas(tmp_path, "lazymod_c", "lazy_c")
    sys.modules.pop("lazymod_c", None)

    app = Flask(__name__)

    @app.route("/health")
    def _health():
        return "ok"

    install_lazy_canvas_loader(app, [_Comp("ccan", "lazymod_c")], {"ccan": "/ccan"})
    client = app.test_client()

    assert client.get("/health").status_code == 200
    assert "lazymod_c" not in sys.modules  # a non-canvas path never loads a canvas
    assert client.get("/nope").status_code == 404
    assert "lazymod_c" not in sys.modules
