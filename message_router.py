from __future__ import annotations
"""Route incoming WhatsApp messages to checklist or life coach mode."""
from datetime import date
import checklist
import life_coach
import claude_agent
import database
import sheets

LMU_START = date(2026, 8, 17)

CHECKLIST_KEYWORDS = [
    "mark", "complete", "completed", "done", "finished", "push deadline",
    "add task", "remove task", "due this month", "what's still due",
    "whats still due", "checklist", "what have i completed", "what did i complete",
    "deadline", "task", "due", "overdue", "urgent", "what's due", "whats due",
    "what is due", "what's next", "whats next", "coming up", "due soon",
    "this week", "my tasks", "list tasks", "show tasks", "status", "progress",
    "how many tasks", "what's left", "whats left", "remove",
]

_COACHING_SIGNALS = [
    "stressed", "stress", "worried", "worry", "overwhelmed", "anxious", "anxiety",
    "nervous", "scared", "excited", "help me think", "what should i", "should i",
    "how do i", "advice", "feel", "feeling", "don't know", "not sure",
    "thinking about", "considering", "deciding", "decision",
]

_DATA_SIGNALS = [
    "due", "overdue", "urgent", "deadline", "task", "checklist", "mark",
    "complete", "done", "push", "add", "remove", "list", "show", "status",
    "progress", "how many", "what's left", "whats left", "next",
]


def detect_mode(text: str) -> str:
    text_lower = text.lower()

    if life_coach.parse_context_update(text) is not None:
        return "life_coach"

    checklist_score = sum(1 for kw in CHECKLIST_KEYWORDS if kw in text_lower)
    return "checklist" if checklist_score > 0 else "life_coach"


def _is_checklist_coaching(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _COACHING_SIGNALS) and not any(kw in t for kw in _DATA_SIGNALS)


def _checklist_fallback() -> str:
    try:
        upcoming = sheets.get_upcoming_tasks(days=14)
        if upcoming:
            top = upcoming[0]
            return f"Most urgent: {top['Task']} — due {top['Deadline']}. Text \"what's due\" for the full list."
    except Exception:
        pass
    return "Text \"what's due\" to see your task list, or \"status\" for overall progress."


def route_message(text: str) -> str:
    """Main routing function — returns the reply string."""
    print(f"[ROUTER] routing: {text[:60]!r}")
    database.save_message("user", text, mode="incoming")

    # Stress detection — warmth before mode routing
    if life_coach.detect_stress(text):
        response = life_coach.get_stress_response(text)
        database.save_message("assistant", response, mode="life_coach")
        return response

    mode = detect_mode(text)

    # ── Checklist mode ──────────────────────────────────────────────────────
    if mode == "checklist":
        result = checklist.try_checklist_command(text)
        if result:
            database.save_message("assistant", result, mode="checklist")
            return result
        if _is_checklist_coaching(text):
            response = claude_agent.call_claude(text, mode="checklist")
            database.save_message("assistant", response, mode="checklist")
            return response
        result = _checklist_fallback()
        database.save_message("assistant", result, mode="checklist")
        return result

    # ── Life coach mode (default) ───────────────────────────────────────────
    ctx_update = life_coach.parse_context_update(text)
    if ctx_update:
        database.save_message("assistant", ctx_update, mode="life_coach")
        return ctx_update
    response = claude_agent.call_claude(text, mode="life_coach")
    database.save_message("assistant", response, mode="life_coach")
    return response


def build_setup_message() -> str:
    try:
        upcoming = checklist.build_monday_digest()
    except Exception:
        upcoming = "(Could not load checklist — check Google Sheets connection.)"

    days_until_lmu = (LMU_START - date.today()).days

    return (
        f"Hey Ava! I'm your law school coach and checklist manager.\n\n"
        f"✅ Pre-law checklist — task tracking, deadlines, and weekly digests. Text \"what's due\" anytime.\n\n"
        f"📚 Life coach — helping you prep for 1L, manage the WBD transition, and stay balanced before August 17.\n\n"
        f"LMU start: August 17, 2026 ({days_until_lmu} days away).\n\n"
        f"Most urgent right now:\n\n{upcoming}"
    )
