# CUI // SP-CTI
"""AI GameDay — Flask-Login integration and session management."""

from __future__ import annotations

from flask_login import LoginManager, UserMixin
from flask_login import login_required  # noqa: F401 — re-exported for blueprint use

from tools.db.storage import get_connection

login_manager = LoginManager()
login_manager.login_view = "login_page"  # redirects to the main dashboard login


class GameDayUser(UserMixin):
    """Lightweight Flask-Login user backed by dashboard_users."""

    def __init__(self, user_id: str, display_name: str, role: str = "player") -> None:
        self.id = user_id
        self.display_name = display_name
        self.role = role


@login_manager.user_loader
def load_user(user_id: str) -> GameDayUser | None:
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT id, display_name, role FROM dashboard_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row:
            return GameDayUser(str(row["id"]), row["display_name"] or "", row.get("role") or "player")
    except Exception:
        pass
    return None
