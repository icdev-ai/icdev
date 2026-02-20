# CUI // SP-CTI
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
        socketio.run(app, host='0.0.0.0', port=5000)
    else:
        app.run(host='0.0.0.0', port=5000)
"""

_socketio = None


def init_socketio(app):
    """Initialize Flask-SocketIO on the app. Returns socketio instance or None."""
    global _socketio

    try:
        from flask_socketio import SocketIO, emit, join_room

        _socketio = SocketIO(
            app,
            cors_allowed_origins="*",
            async_mode="threading",  # Compatible with stdlib threading
            logger=False,
            engineio_logger=False,
        )

        @_socketio.on("connect")
        def handle_connect():
            pass  # Client connected

        @_socketio.on("join")
        def handle_join(data):
            room = data.get("room", "activity")
            join_room(room)

        @_socketio.on("disconnect")
        def handle_disconnect():
            pass  # Client disconnected

        app.logger.info("Flask-SocketIO initialized (WebSocket enabled)")
        return _socketio

    except ImportError:
        app.logger.info(
            "flask-socketio not installed — WebSocket disabled, using HTTP polling"
        )
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
