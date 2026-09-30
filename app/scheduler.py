"""
APScheduler setup.
Runs a fetch job every FETCH_INTERVAL_HOURS hours.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_fetch_job() -> None:
    """Wrapper so APScheduler can call the fetcher without import cycles."""
    from app.services.fetcher import run_fetch

    logger.info("APScheduler: starting scheduled fetch")
    try:
        run_fetch(triggered_by="scheduler")
    except Exception:
        logger.exception("APScheduler: fetch job raised an unhandled exception")


def start_scheduler() -> None:
    """Start the background scheduler. Call once at app startup."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _run_fetch_job,
        trigger=IntervalTrigger(hours=settings.FETCH_INTERVAL_HOURS),
        id="fetch_jobs",
        name="Fetch job postings",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started — fetch every %dh", settings.FETCH_INTERVAL_HOURS
    )


def stop_scheduler() -> None:
    """Stop the scheduler gracefully."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
