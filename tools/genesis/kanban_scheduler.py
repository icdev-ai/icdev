# CUI // SP-CTI
"""Kanban Scheduler — standalone process that runs the kanban reflex on a loop.

Designed to run as a persistent background service (Windows Task Scheduler,
systemd, or nohup). Calls the kanban reflex every INTERVAL seconds to:
1. Poll Telegram for incoming commands
2. Promote due/backlog tasks to in_progress
3. Dispatch tasks to Claude CLI
4. Check for completed tasks and notify

Usage:
    python tools/genesis/kanban_scheduler.py [--interval 60]
    nohup python tools/genesis/kanban_scheduler.py > .tmp/kanban_scheduler.log 2>&1 &
"""

import argparse
import logging
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kanban] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Kanban Scheduler")
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Seconds between kanban reflex cycles (default: 60)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one cycle and exit (for Task Scheduler)",
    )
    args = parser.parse_args()

    # Load .env for Telegram bot token, API keys, etc.
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

    from tools.genesis.reflexes.kanban import run as kanban_run

    dummy_config = {"enabled": True, "risk_tier": "green"}
    dummy_trust = None

    if args.once:
        logger.info("Running single kanban cycle...")
        result = kanban_run(dummy_config, dummy_trust)
        details = result.get("details", {})
        logger.info(
            "Cycle complete: activated=%s, completed=%s, running=%s",
            details.get("tasks_activated", 0),
            len(details.get("completed_this_cycle", [])),
            len(details.get("running", [])),
        )
        return

    logger.info("Kanban scheduler started (interval=%ds)", args.interval)
    logger.info("Press Ctrl+C to stop")

    cycle = 0
    while True:
        cycle += 1
        try:
            result = kanban_run(dummy_config, dummy_trust)
            details = result.get("details", {})
            status = details.get("status", "ok")
            activated = details.get("tasks_activated", 0)
            completed = details.get("completed_this_cycle", [])
            running = details.get("running", [])

            if activated or completed or running:
                logger.info(
                    "Cycle %d: status=%s activated=%s completed=%s running=%s",
                    cycle, status, activated, len(completed), len(running),
                )
            elif cycle % 10 == 0:
                # Heartbeat every 10 cycles
                logger.info("Cycle %d: idle (no due tasks)", cycle)
        except Exception as exc:
            logger.error("Cycle %d error: %s", cycle, exc)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
