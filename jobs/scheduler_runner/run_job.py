"""Cloud Run Job entrypoint for scheduled daily prompt reruns and weekly crawls."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Literal

from api import probe_history

log = logging.getLogger(__name__)

SchedulerAction = Literal["daily-rerun", "weekly-crawl"]


def _parse_action() -> str:
    for arg in sys.argv[1:]:
        if arg.startswith("--action="):
            return arg.split("=", 1)[1].strip()
    return os.environ.get("SCHEDULER_RUNNER_ACTION", "").strip()


def _run_daily_rerun() -> dict[str, object]:
    return probe_history.scheduled_daily_rerun()


def _run_weekly_crawl() -> dict[str, object]:
    return probe_history.scheduled_weekly_crawl()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[scheduler-runner] %(levelname)s %(message)s")
    action = _parse_action()
    if action not in {"daily-rerun", "weekly-crawl"}:
        raise ValueError(
            "Missing or invalid action. Use --action=daily-rerun or --action=weekly-crawl"
        )

    log.info("Starting scheduler runner action=%s", action)
    if action == "daily-rerun":
        result = _run_daily_rerun()
    else:
        result = _run_weekly_crawl()

    print(json.dumps(result, indent=2))
    status = str(result.get("status") or "error")
    if status != "ok":
        log.error("Scheduler runner failed: %s", result)
        return 1

    log.info("Scheduler runner completed: queued=%s skipped=%s", result.get("queued"), result.get("skipped"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        log.exception("Scheduler runner crashed")
        raise
