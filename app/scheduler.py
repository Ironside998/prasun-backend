"""Two background jobs:
   1. day_ahead_job  - safety-net cron, in case the weather-ingest webhook trigger
                        (see main.py) is ever missed. Runs once/day.
   2. intraday_job   - frequent re-solve for dashboard freshness, using whatever
                        weather/price snapshot was last ingested.
"""
import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .database import SessionLocal
from . import ingestion, runner

# Fallback time-of-day for the day-ahead solve (24h clock, UTC), in case the
# weather webhook trigger doesn't fire. Configurable via env var.
DAY_AHEAD_HOUR = int(os.getenv("DSM_DAY_AHEAD_HOUR", "23"))
DAY_AHEAD_MINUTE = int(os.getenv("DSM_DAY_AHEAD_MINUTE", "30"))

# Cadence for the intraday monitoring solve.
INTRADAY_INTERVAL_MINUTES = int(os.getenv("DSM_INTRADAY_INTERVAL_MINUTES", "15"))

scheduler = BackgroundScheduler()


def _run(run_type: str):
    db = SessionLocal()
    try:
        payload = ingestion.build_dsm_request(db)
    except ValueError as e:
        print(f"[scheduler:{run_type}] Skipped run: {e}")
        db.close()
        return
    try:
        run = runner.execute_and_store(db, payload, run_type=run_type)
        print(f"[scheduler:{run_type}] DSM run {run.id} completed: {run.status}")
    except Exception as e:
        print(f"[scheduler:{run_type}] DSM solve failed: {e}")
    finally:
        db.close()


def day_ahead_job():
    _run("DAY_AHEAD")


def intraday_job():
    _run("INTRADAY")


def start_scheduler():
    scheduler.add_job(
        day_ahead_job,
        CronTrigger(hour=DAY_AHEAD_HOUR, minute=DAY_AHEAD_MINUTE),
        id="dsm_day_ahead_fallback", replace_existing=True,
    )
    scheduler.add_job(
        intraday_job, "interval",
        minutes=INTRADAY_INTERVAL_MINUTES,
        id="dsm_intraday", replace_existing=True,
    )
    scheduler.start()