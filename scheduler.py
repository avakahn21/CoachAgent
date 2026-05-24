from __future__ import annotations
"""APScheduler — all proactive scheduled messages."""
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from twilio.rest import Client as TwilioClient

import config
import financial
import checklist
import life_coach
import claude_agent
import database
import sheets

PT = pytz.timezone(config.TIMEZONE)
_scheduler = None


def _send_whatsapp(body: str):
    client = TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    client.messages.create(
        from_=f"whatsapp:{config.TWILIO_WHATSAPP_NUMBER}",
        to="whatsapp:+14155551234",  # Override in prod with actual number from DB
        body=body,
    )


def _get_user_number() -> str | None:
    """Retrieve Ava's WhatsApp number from dynamic_context."""
    try:
        return database.get_context("user_whatsapp_number")
    except Exception:
        return None


def _send(body: str):
    """Send to the stored user number."""
    number = _get_user_number() or config.TWILIO_WHATSAPP_NUMBER
    if number == config.TWILIO_WHATSAPP_NUMBER and not number.startswith("+"):
        return  # No recipient configured yet
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
    msg = checklist.build_monday_digest()
    _send(msg)


def job_sunday_weekly_financial():
    msg = financial.build_weekly_report()
    challenge = financial.get_weekly_challenge()
    _send(msg + "\n\n" + challenge)


def job_first_of_month():
    from datetime import date
    import report
    # Report covers the month that just ended
    today = date.today()
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    report.generate_and_deliver(year, month)


def job_sunday_life_coach():
    from datetime import date
    if date.today() >= life_coach.LMU_START:
        msg = life_coach.build_sunday_checkin()
        _send(msg)


def job_wednesday_balance():
    from datetime import date
    if date.today() >= life_coach.LMU_START:
        msg = life_coach.build_midweek_checkin()
        _send(msg)


def job_threshold_check():
    """Fire immediately if any category hits 75% before the 20th."""
    alerts = financial.check_threshold_alerts()
    for alert in alerts:
        _send(alert)


def job_daily_refresh():
    """Refresh Google Sheets cache daily."""
    import sheets
    sheets.refresh_cache()


def job_plaid_sync():
    """Pull yesterday's Plaid transactions, fire alerts, queue clarifications."""
    import plaid_integration
    import categorization
    if not plaid_integration.has_connected_account():
        return
    try:
        new_count, alerts, needs_clarification = plaid_integration.sync_and_log_transactions(days=2)
        if new_count:
            _send(f"Pulled {new_count} new transaction{'s' if new_count != 1 else ''} from your bank.")
        for alert in alerts:
            _send(alert)
        _queue_and_send_clarifications(needs_clarification)
    except Exception as e:
        print(f"Plaid daily sync error: {e}")


def _queue_and_send_clarifications(needs_clarification: list):
    """Queue low-confidence transactions and send the first clarification message."""
    import categorization
    if not needs_clarification:
        return
    # If there's already an active clarification, queue everything behind it
    if database.get_current_clarification():
        for txn in needs_clarification:
            database.push_clarification_queue(txn)
        return
    # Promote the first to current, queue the rest
    first = needs_clarification[0]
    database.set_current_clarification(first)
    for txn in needs_clarification[1:]:
        database.push_clarification_queue(txn)
    msg = categorization.build_clarification_message(first, first.get("suggested_category", "misc"))
    _send(msg)


def job_deadline_check():
    """Check for overdue tasks and flag them."""
    from datetime import datetime
    overdue = []
    for task in sheets.get_incomplete_tasks():
        raw = str(task.get("Deadline", "")).strip()
        if not raw:
            continue
        deadline = sheets._parse_deadline(raw)
        if deadline and deadline < datetime.utcnow():
            name = task.get("Task", task.get("Task Name", "Unknown"))
            overdue.append(f"• {name} — was due {raw}")
    if overdue:
        msg = "Overdue tasks — these need to be handled:\n\n" + "\n".join(overdue)
        _send(msg)


# ── Scheduler setup ─────────────────────────────────────────────────────────

def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(timezone=PT)

    # Monday 9am PT — checklist digest
    _scheduler.add_job(job_monday_checklist, CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=PT))

    # Sunday 6pm PT — weekly financial report
    _scheduler.add_job(job_sunday_weekly_financial, CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=PT))

    # Sunday 7pm PT — law school life coach (activates after LMU start)
    _scheduler.add_job(job_sunday_life_coach, CronTrigger(day_of_week="sun", hour=19, minute=0, timezone=PT))

    # Wednesday 12pm PT — midweek balance check (activates after LMU start)
    _scheduler.add_job(job_wednesday_balance, CronTrigger(day_of_week="wed", hour=12, minute=0, timezone=PT))

    # 1st of month 8am PT — monthly financial review
    _scheduler.add_job(job_first_of_month, CronTrigger(day=1, hour=8, minute=0, timezone=PT))

    # Every 4 hours — threshold alert check
    _scheduler.add_job(job_threshold_check, CronTrigger(hour="*/4", timezone=PT))

    # Daily 6am — refresh sheets cache and check deadlines
    _scheduler.add_job(job_daily_refresh, CronTrigger(hour=6, minute=0, timezone=PT))
    _scheduler.add_job(job_deadline_check, CronTrigger(hour=7, minute=0, timezone=PT))

    # Daily 8am — pull Plaid transactions (runs after sheets refresh)
    _scheduler.add_job(job_plaid_sync, CronTrigger(hour=8, minute=0, timezone=PT))

    _scheduler.start()
    print("Scheduler started.")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
