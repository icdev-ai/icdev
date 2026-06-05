# [TEMPLATE: CUI // SP-CTI]
"""
WebSocket integration via Flask-SocketIO (Phase 30 — D170).

Additive — HTTP polling (D103) remains for backward compat.
Falls back gracefully when flask-socketio is not installed.

Usage:
    from tools.dashboard.websocket import init_socketio, broadcast_activity

    # In create_app():
    socketio = init_socketio(app)

    # When a new event arrives:
    broadcast_activity({...event_dict...})

    # To run with WebSocket support:
    if socketio:
        socketio.run(app, host=HOST, port=PORT)
    else:
        app.run(host=HOST, port=PORT)
"""

_socketio = None


def init_socketio(app):
    """Initialize Flask-SocketIO on the app. Returns socketio instance or None.

    Uses a background thread with a 6-second timeout to guard against the
    python-engineio circular import hang seen on Python 3.14 (engineio/packet.py
    does ``from engineio import json`` which deadlocks the import machinery when
    the package is partially initialised).  Falls back to HTTP polling (D103)
    when the import does not complete in time.
    """
    global _socketio
    import threading

    _result = [None]
    _exc = [None]

    def _do_import():
        try:
            from flask_socketio import SocketIO, join_room  # noqa: PLC0415

            sio = SocketIO(
                app,
                cors_allowed_origins="*",
                async_mode="threading",
                logger=False,
                engineio_logger=False,
            )

            @sio.on("connect")
            def handle_connect():
                pass

            @sio.on("join")
            def handle_join(data):
                room = data.get("room", "activity")
                join_room(room)

            @sio.on("disconnect")
            def handle_disconnect():
                pass

            _result[0] = sio
        except ImportError:
            pass  # not installed
        except Exception as e:
            _exc[0] = e

    t = threading.Thread(target=_do_import, daemon=True, name="socketio-init")
    t.start()
    t.join(timeout=6)

    if _result[0] is not None:
        _socketio = _result[0]
        app.logger.info("Flask-SocketIO initialized (WebSocket enabled)")
        return _socketio

    if t.is_alive():
        app.logger.info(
            "Flask-SocketIO import timed out (python-engineio circular import on Py3.14) "
            "— WebSocket disabled, using HTTP polling (D103)"
        )
    else:
        if _exc[0]:
            app.logger.info("Flask-SocketIO init error (%s) — using HTTP polling", _exc[0])
        else:
            app.logger.info("flask-socketio not installed — WebSocket disabled, using HTTP polling")
    return None


def broadcast_activity(event_data):
    """Broadcast an activity event to all connected WebSocket clients.

    Safe to call even when SocketIO is not available (no-op).
    """
    if _socketio is None:
        return

    try:
        _socketio.emit("activity_event", event_data, room="activity")
    except Exception:
        pass  # Never let WebSocket errors break the main flow


def get_socketio():
    """Return the SocketIO instance (or None if not initialized)."""
    return _socketio
