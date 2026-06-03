from __future__ import annotations
"""APScheduler — proactive scheduled messages (checklist + life coach only)."""
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from twilio.rest import Client as TwilioClient

import config
import checklist
import life_coach
import database
import sheets

PT = pytz.timezone(config.TIMEZONE)
_scheduler = None


def _get_user_number() -> str | None:
    try:
        return database.get_context("user_whatsapp_number")
    except Exception:
        return None


def _send(body: str):
    """Send to the stored user number."""
    number = _get_user_number()
    if not number:
        print("[SCHEDULER] No user number stored — skipping send.")
        return
    try:
        client = TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        to = f"whatsapp:{number}" if not number.startswith("whatsapp:") else number
        client.messages.create(
            from_=f"whatsapp:{config.TWILIO_WHATSAPP_NUMBER}",
            to=to,
            body=body,
        )
        database.save_message("assistant", body, mode="scheduled")
    except Exception as e:
        print(f"Scheduled send error: {e}")


# ── Job functions ───────────────────────────────────────────────────────────

def job_monday_checklist():
    """Monday 9am PT — top urgent tasks for the week."""
    msg = checklist.build_monday_digest()
    _send(msg)


def job_sunday_life_coach():
    """Sunday 7pm PT — week ahead check-in."""
    msg = life_coach.build_sunday_checkin()
    _send(msg)


def job_wednesday_balance():
    """Wednesday 12pm PT — midweek balance check."""
    msg = life_coach.build_midweek_checkin()
    _send(msg)


def job_daily_refresh():
    """Daily 6am — refresh Google Sheets cache and surface overdue tasks."""
    try:
        sheets.refresh_cache()
    except Exception as e:
        print(f"Sheets refresh error: {e}")

    # Surface any newly overdue tasks
    from datetime import datetime
    overdue = []
    try:
        for task in sheets.get_incomplete_tasks():
            raw = str(task.get("Deadline", "")).strip()
            if not raw:
                continue
            deadline = sheets._parse_deadline(raw)
            if deadline and deadline < datetime.utcnow():
                name = task.get("Task", "Unknown")
                overdue.append(f"• {name} — was due {raw}")
    except Exception as e:
        print(f"Overdue check error: {e}")

    if overdue:
        _send("Overdue tasks:\n\n" + "\n".join(overdue))


# ── Scheduler setup ─────────────────────────────────────────────────────────

def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(timezone=PT)

    # Monday 9am PT — checklist digest
    _scheduler.add_job(job_monday_checklist, CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=PT))

    # Sunday 7pm PT — week ahead life coach check-in
    _scheduler.add_job(job_sunday_life_coach, CronTrigger(day_of_week="sun", hour=19, minute=0, timezone=PT))

    # Wednesday 12pm PT — midweek balance check
    _scheduler.add_job(job_wednesday_balance, CronTrigger(day_of_week="wed", hour=12, minute=0, timezone=PT))

    # Daily 6am PT — refresh sheets + overdue check
    _scheduler.add_job(job_daily_refresh, CronTrigger(hour=6, minute=0, timezone=PT))

    _scheduler.start()
    print("Scheduler started — 4 jobs: Monday digest, Sunday check-in, Wednesday balance, daily refresh.")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
