# CUI // SP-CTI
"""Services package."""

from .budget_monitor import BudgetMonitor
from .throttle_controller import ThrottleController

__all__ = ["BudgetMonitor", "ThrottleController", "start_budget_services"]


def start_budget_services(poll_interval: int = 60) -> ThrottleController:
    """Instantiate and start the budget monitor and throttling controller as background services."""
    monitor = BudgetMonitor()
    controller = ThrottleController(monitor=monitor, poll_interval=poll_interval)
    controller.start()
    return controller
