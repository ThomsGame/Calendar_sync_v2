"""APScheduler configuration for daily automatic syncs."""

from __future__ import annotations

import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


def _daily_sync_job(app) -> None:
    """Job that runs once a day and triggers sync for all active users."""
    with app.app_context():
        from dashboard import get_db
        from dashboard.models import User, UserCredentials
        from dashboard.sync_runner import run_sync_for_user

        db = get_db()
        active_users = (
            db.query(User)
            .filter(User.is_active == True, User.setup_complete == True)
            .join(UserCredentials, User.id == UserCredentials.user_id)
            .all()
        )

        logger.info(f"[SCHEDULER] Daily sync started for {len(active_users)} users.")
        for user in active_users:
            try:
                logger.info(f"[SCHEDULER] Syncing user {user.email} ...")
                run_id = run_sync_for_user(user.id, trigger="scheduled")
                logger.info(f"[SCHEDULER] User {user.email} → run_id={run_id}")
            except Exception as e:
                logger.error(f"[SCHEDULER] User {user.email} sync failed: {e}")

        logger.info("[SCHEDULER] Daily sync complete.")


def start_scheduler(app) -> None:
    """Start the background scheduler (called once from create_app)."""
    global _scheduler

    with _lock:
        if _scheduler is not None:
            return  # Already started (avoid double-start in debug reloader)

        _scheduler = BackgroundScheduler(timezone="Europe/Paris")

        # Run once per day at 07:00 Paris time
        _scheduler.add_job(
            func=_daily_sync_job,
            args=[app],
            trigger=CronTrigger(hour=7, minute=0, timezone="Europe/Paris"),
            id="daily_sync",
            name="Daily calendar sync for all users",
            replace_existing=True,
        )

        _scheduler.start()
        logger.info("[SCHEDULER] Background scheduler started — daily sync at 07:00 Paris time.")


def stop_scheduler() -> None:
    """Gracefully stop the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
